"""Discovery and layer attribution of a load profile (SPEC §4.3.1, §4.3.2 steps 1-2). Owner: WP-03.

``discover(profile)`` walks the profile root READ-ONLY and returns a :class:`Discovery`:

* every file the index looks at as a :class:`SourceSpec` (``source`` table): the DAT files of
  the profile, IMG archives in registration order (``formats.layout.archives``), IDE/IPL/ZON and
  COL/TXD/DFF lines of the DAT files, loose assets under ``models/`` and ``anim/``, and all other
  files of the root (kind ``other`` or by extension, ``load_ref = NULL`` = present, not loaded);
* the layer of each file:

  1. ``vanilla`` if ``(relpath, sha256)`` matches ``gta-sa-clean/MANIFEST.sha256``;
  2. else the first matching ``[layers].rules`` glob (``SAMP/**`` -> ``samp``,
     ``modloader/*/**`` -> ``modloader:{1}``);
  3. else ``[layers].fallback`` (``modded``).

SHA-256 values come from ``work/index/hashcache.sqlite`` keyed by ``(root, relpath, size,
mtime_ns)``. A miss is seeded from the manifests in ``tools/data/manifests`` (size + mtime
seconds equal, like ``satk game verify``'s fast mode) or hashed (``open_ro``). Only files whose
relpath appears in the vanilla manifest need a hash at all.

Stdlib only.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..core.config import REPO_ROOT
from ..core.errors import SatkError
from ..core.paths import cfg, ensure_writable, jpath, open_ro
from ..formats.dat import canon_relpath, parse_dat, read_text, resolve_ci
from ..formats.layout import ASSUMPTIONS, archives, loose_assets
from ..formats.rw import FormatError

__all__ = [
    "SourceSpec", "LayerSpec", "Discovery", "discover", "layer_kind", "layer_priority", "HashCache",
    "read_vanilla_manifest", "MANIFEST_NAME", "EXT_KIND", "DAT_KEY_KIND",
]

MANIFEST_NAME = "MANIFEST.sha256"
#: File extension -> ``source.kind``.
EXT_KIND = {"img": "img", "ide": "ide", "ipl": "ipl", "zon": "zon", "txd": "txd", "dff": "dff", "col": "col",
            "ifp": "ifp"}
#: DAT directive -> kind of the file it loads.
DAT_KEY_KIND = {"IDE": "ide", "IPL": "ipl", "COLFILE": "col", "TEXDICTION": "txd", "MODELFILE": "dff",
                "HIERFILE": "dff"}
_LAYER_KINDS = ("vanilla", "samp", "modded", "modloader", "mta", "project")
_LAYER_PRIORITY = {"vanilla": 0, "samp": 10, "modded": 20, "modloader": 30, "mta": 40, "project": 50}
#: mtime tolerance (seconds) when seeding the hash cache from a manifest.
_MTIME_SLACK = 2


def layer_kind(name: str) -> str:
    """``modloader:foo`` -> ``modloader``; unknown prefixes count as ``modded``."""
    k = name.split(":", 1)[0]
    return k if k in _LAYER_KINDS else "modded"


def layer_priority(name: str) -> int:
    """Larger = applied later (wins IDE overrides): vanilla 0 < samp 10 < modded 20 < modloader 30 ..."""
    return _LAYER_PRIORITY[layer_kind(name)]


# --------------------------------------------------------------------------- records


@dataclass
class SourceSpec:
    """One physical file of the profile root (a future ``source`` row)."""

    relpath: str               # canonical: lower-case, forward slashes
    path: Path                 # real path on disk (for reading, open_ro)
    kind: str                  # img dat ide ipl zon txd dff col ifp other
    size: int
    mtime_ns: int
    sha256: str | None = None
    layer: str = "modded"
    load_ref: str | None = None
    load_order: int | None = None   # IMG registration order (0 = first = wins)
    ns: str | None = None           # IMG namespace main|player|anim|cuts; 'loose' for loose blobs
    loose: bool = False             # a loose asset (becomes a blob)
    dat_order: int | None = None    # order of the loading directive (IDE/IPL/COLFILE/...) across the DAT files


@dataclass(frozen=True)
class LayerSpec:
    name: str
    kind: str
    priority: int


@dataclass
class Discovery:
    """Everything step 1-2 of the pipeline found for one profile."""

    profile: str
    root: Path
    dat_files: list[str]
    img_order: str
    sources: list[SourceSpec]
    layers: list[LayerSpec]
    assumptions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    hash_stats: dict[str, int] = field(default_factory=dict)

    def by_relpath(self) -> dict[str, SourceSpec]:
        return {s.relpath: s for s in self.sources}

    @property
    def archives(self) -> list[SourceSpec]:
        """Registered IMG archives in registration order."""
        return sorted((s for s in self.sources if s.kind == "img" and s.load_order is not None),
                      key=lambda s: s.load_order)

    def loaded(self, kind: str) -> list[SourceSpec]:
        """Sources of ``kind`` loaded through a DAT directive, in load order."""
        return sorted((s for s in self.sources if s.kind == kind and s.dat_order is not None),
                      key=lambda s: s.dat_order)


# --------------------------------------------------------------------------- hash cache


class HashCache:
    """``work/index/hashcache.sqlite``: SHA-256 per ``(root, relpath, size, mtime_ns)``.

    Seeds (first fill) are manifests mapping relpath -> ``{size, mtime (s), sha256}`` for a
    given root; they are trusted only when size and mtime agree.
    """

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path is not None else Path(os.path.abspath(cfg().paths.work)) / "index" / "hashcache.sqlite"
        self._conn: sqlite3.Connection | None = None
        self._seeds: dict[str, dict[str, tuple[int, int, str]]] = {}
        self.stats = {"cached": 0, "seeded": 0, "hashed": 0, "hashed_bytes": 0}

    def _db(self) -> sqlite3.Connection:
        if self._conn is None:
            ensure_writable(self.path)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            c = sqlite3.connect(self.path, timeout=10)
            c.execute("CREATE TABLE IF NOT EXISTS hc(root TEXT NOT NULL, relpath TEXT NOT NULL, size INTEGER NOT NULL,"
                      " mtime_ns INTEGER NOT NULL, sha256 TEXT NOT NULL, PRIMARY KEY(root, relpath))")
            self._conn = c
        return self._conn

    @staticmethod
    def _rootkey(root: Path) -> str:
        return os.path.normcase(os.path.abspath(root)).replace("\\", "/")

    def add_seed(self, root: Path, entries: dict[str, tuple[int, int, str]]) -> None:
        """``entries``: canonical relpath -> (size, mtime seconds, sha256)."""
        self._seeds.setdefault(self._rootkey(root), {}).update(entries)

    def sha256(self, root: Path, relpath: str, path: Path, size: int, mtime_ns: int) -> str:
        rk = self._rootkey(root)
        db = self._db()
        row = db.execute("SELECT size, mtime_ns, sha256 FROM hc WHERE root=? AND relpath=?", (rk, relpath)).fetchone()
        if row and row[0] == size and row[1] == mtime_ns:
            self.stats["cached"] += 1
            return row[2]
        seed = self._seeds.get(rk, {}).get(relpath)
        if seed and seed[0] == size and abs(int(mtime_ns // 1_000_000_000) - seed[1]) <= _MTIME_SLACK:
            sha = seed[2]
            self.stats["seeded"] += 1
        else:
            sha = _sha256_file(path)
            self.stats["hashed"] += 1
            self.stats["hashed_bytes"] += size
        db.execute("INSERT OR REPLACE INTO hc VALUES (?,?,?,?,?)", (rk, relpath, size, mtime_ns, sha))
        return sha

    def close(self) -> None:
        if self._conn is not None:
            self._conn.commit()
            self._conn.close()
            self._conn = None


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open_ro(path) as f:
        while True:
            b = f.read(1 << 20)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _manifest_seeds() -> list[tuple[Path, dict[str, tuple[int, int, str]]]]:
    """(root, {relpath: (size, mtime_s, sha256)}) from ``tools/data/manifests``."""
    out: list[tuple[Path, dict[str, tuple[int, int, str]]]] = []
    mdir = REPO_ROOT / "data" / "manifests"
    c = cfg()
    try:
        d = json.loads((mdir / "stock-1.0us-hoodlum.json").read_text(encoding="utf-8"))
        ents = d.get("entries") or {}
        out.append((c.paths.game, {canon_relpath(k): (int(v["size"]), int(v.get("mtime") or -999), v["sha256"])
                                   for k, v in ents.items() if isinstance(v, dict) and "sha256" in v}))
    except (OSError, ValueError, KeyError, TypeError):
        pass
    try:
        d = json.loads((mdir / "install-2026-10-04.json").read_text(encoding="utf-8"))
        out.append((c.paths.installed, {canon_relpath(k): (int(v["size"]), int(v.get("mtime") or -999), v["sha256"])
                                        for k, v in d.items() if isinstance(v, dict) and "sha256" in v}))
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return out


def read_vanilla_manifest(path: Path) -> dict[str, str]:
    """``MANIFEST.sha256`` (``<sha>  <relpath>``) -> {canonical relpath: sha256}."""
    out: dict[str, str] = {}
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) == 2 and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            out[canon_relpath(parts[1].lstrip("*"))] = parts[0].lower()
    return out


# --------------------------------------------------------------------------- layer rules


def _glob_regex(glob: str) -> re.Pattern:
    """``SAMP/**`` / ``modloader/*/**`` -> regex on canonical relpaths; ``*`` groups are captured."""
    g = canon_relpath(glob)
    out = []
    i = 0
    while i < len(g):
        if g.startswith("**", i):
            out.append(".*")
            i += 2
        elif g[i] == "*":
            out.append("([^/]*)")
            i += 1
        elif g[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(g[i]))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def _rules() -> tuple[list[tuple[re.Pattern, str]], str]:
    c = cfg()
    rules = []
    for r in c.get("layers.rules", []) or []:
        if isinstance(r, dict) and r.get("glob") and r.get("layer"):
            rules.append((_glob_regex(str(r["glob"])), str(r["layer"])))
    return rules, str(c.get("layers.fallback", "modded") or "modded")


def _rule_layer(relpath: str, rules, fallback: str) -> str:
    for rx, layer in rules:
        m = rx.match(relpath)
        if m:
            name = layer
            for n, g in enumerate(m.groups(), 1):
                name = name.replace("{%d}" % n, g)
            return name.lower()
    return fallback


# --------------------------------------------------------------------------- discovery


def _stat(p: Path) -> tuple[int, int]:
    st = p.stat()
    return st.st_size, st.st_mtime_ns


def discover(profile: str = "vanilla", *, root: Path | None = None, dat_files: list[str] | None = None,
             img_order: str | None = None, vanilla_manifest: Path | None = None,
             hashcache: HashCache | None = None) -> Discovery:
    """Steps 1-2 of ``index build``: sources of ``profile`` with their layers and load references.

    Args:
        profile: configured profile name (root/dat/img_order come from ``satk.toml``).
        root, dat_files, img_order: overrides (tests build synthetic roots).
        vanilla_manifest: ``MANIFEST.sha256`` (default ``[layers].vanilla_manifest``).
        hashcache: hash cache (default ``work/index/hashcache.sqlite``).

    Raises:
        SatkError: ``NOT_FOUND`` if the root or a DAT file is missing.
    """
    c = cfg()
    if root is None or dat_files is None or img_order is None:
        p = c.profile(profile)
        root = root if root is not None else p.root
        dat_files = list(dat_files if dat_files is not None else p.dat)
        img_order = img_order or p.img_order
    root = Path(os.path.abspath(root))
    if not root.is_dir():
        raise SatkError("NOT_FOUND", f"profile {profile!r}: game root not found: {jpath(root)}",
                        hint="check [profiles] / [paths] in satk.toml (satk config show)")
    warnings: list[str] = []

    # ---- every file of the root
    files: dict[str, SourceSpec] = {}
    for dp, dn, fn in os.walk(root):
        if canon_relpath(os.path.relpath(dp, root)) == "modloader":
            # .data/.profiles contain modloader itself, not loadable user mods.
            dn[:] = [name for name in dn if not name.startswith(".")]
        dn.sort()
        for f in sorted(fn):
            full = Path(dp) / f
            rel = canon_relpath(os.path.relpath(full, root))
            if rel == MANIFEST_NAME.lower():
                continue
            try:
                size, mt = _stat(full)
            except OSError:
                continue
            ext = rel.rsplit(".", 1)[-1] if "." in rel.rsplit("/", 1)[-1] else ""
            files[rel] = SourceSpec(rel, full, EXT_KIND.get(ext, "other"), size, mt)

    def get(dos_or_rel: str) -> SourceSpec | None:
        p = resolve_ci(root, dos_or_rel)
        if p is None:
            return None
        return files.get(canon_relpath(os.path.relpath(p, root)))

    # ---- DAT files
    dat_rel: list[str] = []
    dat_lines: list[tuple[str, object]] = []
    for d in dat_files:
        s = get(d)
        if s is None:
            raise SatkError("NOT_FOUND", f"profile {profile!r}: DAT file {d} not found under {jpath(root)}",
                            hint="satk config show")
        s.kind = "dat"
        s.load_ref = "exe:CFileLoader::LoadLevel"
        dat_rel.append(s.relpath)
        dat_lines += [(s.relpath, ln) for ln in parse_dat(read_text(s.path))]

    # ---- IMG archives (registration order)
    try:
        specs = archives(root, dat_files, img_order)
    except FormatError as e:
        raise SatkError("NOT_FOUND", f"profile {profile!r}: {e}") from None
    for a in specs:
        s = files.get(a.relpath) or get(a.relpath)
        if s is None:
            warnings.append(f"MISSING: archive {a.relpath} ({a.load_ref}) not found")
            continue
        s.kind = "img"
        s.load_ref, s.load_order, s.ns = a.load_ref, a.order, a.ns

    # ---- DAT directives: IDE / IPL / ZON / COLFILE / TEXDICTION / MODELFILE
    order = 0
    for dat, ln in dat_lines:
        kind = DAT_KEY_KIND.get(ln.key)  # type: ignore[attr-defined]
        if kind is None:
            continue
        s = get(ln.path)  # type: ignore[attr-defined]
        if s is None:
            warnings.append(f"MISSING: {ln.key} {ln.path} ({dat}:{ln.line}) not found")  # type: ignore[attr-defined]
            continue
        if s.dat_order is not None:
            continue  # listed twice: the first directive loads it
        if kind == "ipl" and s.relpath.endswith(".zon"):
            kind = "zon"
        s.kind = kind
        s.load_ref = f"{dat}:{ln.line}"  # type: ignore[attr-defined]
        s.dat_order = order
        order += 1

    # ---- loose assets (models/**, anim/**): blobs of namespace 'loose'
    vman_path = Path(vanilla_manifest) if vanilla_manifest is not None else Path(
        str(c.get("layers.vanilla_manifest") or (c.paths.game / MANIFEST_NAME)))
    vman = read_vanilla_manifest(vman_path)
    if not vman:
        warnings.append(f"NO_MANIFEST: {jpath(vman_path)} missing or empty: no file can be attributed to 'vanilla'")
    for rel in loose_assets(root):
        s = files.get(rel)
        if s is None:
            continue
        s.loose = True
        s.ns = "loose"
        s.kind = EXT_KIND.get(rel.rsplit(".", 1)[-1], s.kind)
        if s.load_ref is None and rel in vman:
            s.load_ref = "exe:hardcoded"  # fonts/hud/particle txd, ped.ifp, coll/*.col ... loaded by name

    # ---- layers
    hc = hashcache or HashCache()
    own_hc = hashcache is None
    for r, seeds in _manifest_seeds():
        hc.add_seed(r, seeds)
    rules, fallback = _rules()
    try:
        for s in files.values():
            want = vman.get(s.relpath)
            if want is not None:
                s.sha256 = hc.sha256(root, s.relpath, s.path, s.size, s.mtime_ns)
                if s.sha256 == want:
                    s.layer = "vanilla"
                    continue
            s.layer = _rule_layer(s.relpath, rules, fallback)
    finally:
        if own_hc:
            hc.close()
        elif hc._conn is not None:
            hc._conn.commit()
    names = sorted({s.layer for s in files.values()} | {"vanilla"},
                   key=lambda n: (layer_priority(n), n))
    layers = [LayerSpec(n, layer_kind(n), layer_priority(n)) for n in names]
    return Discovery(profile=profile, root=root, dat_files=dat_rel, img_order=img_order,
                     sources=sorted(files.values(), key=lambda s: s.relpath), layers=layers,
                     assumptions=list(ASSUMPTIONS.get(img_order, [])), warnings=warnings,
                     hash_stats=dict(hc.stats))


def stat_signature(root: Path, relpaths: list[str]) -> dict[str, tuple[int, int] | None]:
    """Current ``(size, mtime_ns)`` of files (``None`` if gone) - used by freshness checks."""
    out: dict[str, tuple[int, int] | None] = {}
    for rel in relpaths:
        p = Path(root).joinpath(*rel.split("/"))
        try:
            st = os.stat(p)
            out[rel] = (st.st_size, st.st_mtime_ns)
        except OSError:
            q = resolve_ci(Path(root), rel)
            if q is None:
                out[rel] = None
                continue
            st = os.stat(q)
            out[rel] = (st.st_size, st.st_mtime_ns)
    return out


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
