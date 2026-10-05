"""Manifests of the game copy: the audited install and the clean stock 1.0 US set (SPEC §4.1).

Two data files live in ``<repo>/data/manifests`` (hashes and paths only, no game bytes); they are
read through :mod:`satk.core.resources`, so an installed package finds its packaged copy:

* ``install-2026-10-04.json`` — the read-only audit of our original install (``<workspace>\\GTA San Andreas``)
  (549 files: ``{relpath: {size, sha256, mtime}}``, relpaths with backslashes, as written by
  the install audit, research report 13);
* ``stock-1.0us-hoodlum.json`` — the 416 files of the clean dev copy ``gta-sa-clean``
  (414 copied verbatim + ``gta_sa.exe`` restored to stock + ``vorbisFile.dll`` taken from
  ``vorbisHooked.dll``), generated from the first one by :func:`build_stock` (``python -m
  satk.game.manifests build``; a test checks the committed file is reproducible).

Relative paths are compared like Windows does: separators ``\\`` and ``/`` are equivalent and
case is ignored (:func:`key`). Paths are always built with ``pathlib`` from split components:
string literals with backslash escapes caused the ``models\\x360btns.txd`` -> ``models60btns.txd``
bug of the first prototype (SPEC §0.2 V2); ``tests/game`` scans this package for them.
"""

from __future__ import annotations

import fnmatch
import json
import os
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from typing import Any, Iterable, Iterator

from satk.core import resources
from satk.core.errors import SatkError

__all__ = [
    "DATA_DIR",
    "INSTALL_MANIFEST",
    "STOCK_MANIFEST",
    "CLEAN_MANIFEST_NAME",
    "STOCK_DIRS",
    "STOCK_ROOT_FILES",
    "EXCLUDE_PATHS",
    "EXCLUDE_GLOBS",
    "RESTORE",
    "PROTECT_IMGS",
    "FAST_GLOBS",
    "Entry",
    "Manifest",
    "norm_rel",
    "key",
    "to_path",
    "classify",
    "is_fast",
    "load_install",
    "load_stock",
    "load_manifest",
    "read_sha256_file",
    "format_sha256_file",
    "build_stock",
    "dump_stock",
]

#: ``manifests`` in the data root: ``<repo>/data/manifests`` in a checkout, ``satk/_data/manifests``
#: in an installed package (:func:`satk.core.resources.data_path`).
DATA_DIR: Path = resources.data_path("manifests")
INSTALL_MANIFEST = "install-2026-10-04.json"
STOCK_MANIFEST = "stock-1.0us-hoodlum.json"
#: Hash list written into the root of a clean copy (``<sha256>  <relpath>`` lines).
CLEAN_MANIFEST_NAME = "MANIFEST.sha256"
STOCK_FORMAT = "satk.stock-manifest/1"

# --------------------------------------------------------------------------- selection rules
# SPEC §4.1 "Исключения при clone". Paths use "/" and are matched case-insensitively.

#: Top-level directories copied into the clean copy.
STOCK_DIRS: tuple[str, ...] = ("anim", "audio", "data", "models", "movies", "text", "ReadMe")
#: Root files copied verbatim (gta_sa.exe and vorbisFile.dll are restored, see RESTORE).
STOCK_ROOT_FILES: tuple[str, ...] = ("eax.dll", "ogg.dll", "vorbis.dll", "stream.ini")
#: Additions found inside the stock directories (SA-MP, GInput, LanguageLoader).
EXCLUDE_PATHS: tuple[str, ...] = (
    "data/colorcycle.dat",
    "models/ps3btns.txd",
    "models/x360btns.txd",
    "models/sixaxis.txd",
    "text/languages.ini",
)
EXCLUDE_GLOBS: tuple[str, ...] = ("*.two",)
#: Files whose clean content is produced from another source: ``dst -> (src, transform)``.
RESTORE: dict[str, tuple[str, str | None]] = {
    "gta_sa.exe": ("gta_sa.exe", "exe:stock"),  # revert the 12-byte no-intro patch (satk.game.exe)
    "vorbisFile.dll": ("vorbisHooked.dll", None),  # the ASI loader took its name; original kept aside
}
#: IMG archives made read-only by ``satk game protect`` (Ariane and the game open them "rb", V7).
PROTECT_IMGS: tuple[str, ...] = (
    "models/gta3.img",
    "models/gta_int.img",
    "models/player.img",
    "models/cutscene.img",
    "anim/anim.img",
    "anim/cuts.img",
    "data/paths/carrec.img",
    "data/script/script.img",
)
#: Files checked by size+mtime in the default (non ``--deep``) verify: big, never edited.
FAST_GLOBS: tuple[str, ...] = ("*.img", "audio/*")


def norm_rel(rel: str | os.PathLike) -> str:
    """Canonical relative path: ``/`` separators, no ``.`` parts, original case.

    ``"models\\\\gta3.img"``, ``"models/gta3.img"`` and ``"./models/gta3.img"`` all become
    ``"models/gta3.img"``. Absolute paths and ``..`` are rejected (``BAD_PARAMS``).
    """
    p = PureWindowsPath(os.fspath(rel))
    if p.drive or p.root:
        raise SatkError("BAD_PARAMS", f"expected a relative path, got {os.fspath(rel)!r}")
    parts = [x for x in p.parts if x not in ("", ".")]
    if any(x == ".." for x in parts):
        raise SatkError("BAD_PARAMS", f"'..' is not allowed in a game-relative path: {os.fspath(rel)!r}")
    return "/".join(parts)


def key(rel: str | os.PathLike) -> str:
    """Comparison key of a relative path (Windows semantics: separator- and case-insensitive)."""
    return norm_rel(rel).casefold()


def to_path(root: str | os.PathLike, rel: str) -> Path:
    """``root / rel`` built from components (never by string concatenation)."""
    return Path(root).joinpath(*norm_rel(rel).split("/"))


def _match(k: str, pattern: str) -> bool:
    """Case-insensitive glob on a casefolded key; ``*`` crosses directories (fnmatch)."""
    return fnmatch.fnmatchcase(k, pattern.casefold())


_STOCK_DIR_KEYS = frozenset(d.casefold() for d in STOCK_DIRS)
_ROOT_FILE_KEYS = frozenset(f.casefold() for f in STOCK_ROOT_FILES)
_EXCLUDE_KEYS = frozenset(key(p) for p in EXCLUDE_PATHS)
_RESTORE_KEYS = frozenset(key(p) for p in RESTORE)


def classify(rel: str | os.PathLike) -> str:
    """Role of an install file in the clean copy.

    Returns ``"stock"`` (copied verbatim), ``"restore"`` (stock path whose clean content is
    produced from another source, :data:`RESTORE`), ``"exclude"`` (an addition inside a stock
    directory, e.g. ``models/x360btns.txd``) or ``"nonstock"`` (everything else: SA-MP, ASI,
    CLEO, modloader, logs...). Stock candidates = ``stock`` + ``restore`` (416 for 1.0 US).
    """
    k = key(rel)
    if k in _RESTORE_KEYS:
        return "restore"
    if k in _EXCLUDE_KEYS or any(_match(k, g) for g in EXCLUDE_GLOBS):
        return "exclude"
    head, sep, _ = k.partition("/")
    if sep and head in _STOCK_DIR_KEYS:
        return "stock"
    if not sep and k in _ROOT_FILE_KEYS:
        return "stock"
    return "nonstock"


def is_fast(rel: str) -> bool:
    """True for files the default verify checks by size+mtime (IMG archives, ``audio/``)."""
    k = key(rel)
    return any(_match(k, g) for g in FAST_GLOBS)


# --------------------------------------------------------------------------- manifest objects


@dataclass(frozen=True, slots=True)
class Entry:
    """Expected state of one file.

    ``src``/``via`` describe how ``clone`` produces it when it is not a verbatim copy;
    ``alt`` lists other accepted SHA-256 values (``gta_sa.exe`` may be the MTA-canonical
    variant, which differs from stock only by the PE checksum field).
    """

    path: str
    size: int
    sha256: str
    mtime: int | None = None
    src: str | None = None
    via: str | None = None
    alt: tuple[str, ...] = ()

    @property
    def source(self) -> str:
        """Relative path of the source file in the install."""
        return self.src or self.path

    def accepts(self, sha256: str) -> bool:
        return sha256 == self.sha256 or sha256 in self.alt

    def as_json(self) -> dict[str, Any]:
        d: dict[str, Any] = {"size": self.size, "sha256": self.sha256}
        if self.mtime is not None:
            d["mtime"] = self.mtime
        if self.src is not None:
            d["from"] = self.src
        if self.via is not None:
            d["via"] = self.via
        if self.alt:
            d["alt"] = list(self.alt)
        return d


@dataclass
class Manifest:
    """A set of expected files keyed by :func:`key`; iteration is sorted by path."""

    name: str
    entries: dict[str, Entry]
    meta: dict[str, Any] = field(default_factory=dict)
    path: Path | None = None

    @classmethod
    def from_entries(cls, name: str, entries: Iterable[Entry], **kw) -> "Manifest":
        d: dict[str, Entry] = {}
        for e in entries:
            k = key(e.path)
            if k in d:
                raise SatkError("BAD_PARAMS", f"manifest {name}: duplicate path {e.path!r}")
            d[k] = e
        return cls(name=name, entries=d, **kw)

    def __len__(self) -> int:
        return len(self.entries)

    def __iter__(self) -> Iterator[Entry]:
        return iter(sorted(self.entries.values(), key=lambda e: key(e.path)))

    def __contains__(self, rel: object) -> bool:
        return isinstance(rel, (str, os.PathLike)) and key(rel) in self.entries

    def get(self, rel: str) -> Entry | None:
        return self.entries.get(key(rel))

    @property
    def total_bytes(self) -> int:
        return sum(e.size for e in self.entries.values())

    def subset(self, pred) -> "Manifest":
        """Entries for which ``pred(entry)`` is true (same name/meta)."""
        return Manifest(self.name, {k: e for k, e in self.entries.items() if pred(e)}, dict(self.meta), self.path)


def _read_json(path: Path) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        raise SatkError("NOT_FOUND", f"manifest not found: {path}",
                        hint=resources.HINT if Path(path).parent == DATA_DIR else "check the --manifest path") from None
    except (OSError, json.JSONDecodeError) as e:
        raise SatkError("BAD_PARAMS", f"cannot read manifest {path}: {e}") from None


def _entry_from_json(rel: str, v: dict) -> Entry:
    try:
        return Entry(
            path=norm_rel(rel),
            size=int(v["size"]),
            sha256=str(v["sha256"]).lower(),
            mtime=int(v["mtime"]) if v.get("mtime") is not None else None,
            src=norm_rel(v["from"]) if v.get("from") else None,
            via=v.get("via"),
            alt=tuple(str(a).lower() for a in v.get("alt", ())),
        )
    except (KeyError, TypeError, ValueError) as e:
        raise SatkError("BAD_PARAMS", f"bad manifest entry {rel!r}: {e}") from None


def load_manifest(path: str | os.PathLike) -> Manifest:
    """Load either format: a stock manifest (``{"format": ..., "entries": {...}}``) or a flat
    install manifest (``{relpath: {size, sha256, mtime}}``)."""
    p = Path(path)
    data = _read_json(p)
    if not isinstance(data, dict):
        raise SatkError("BAD_PARAMS", f"manifest {p} is not a JSON object")
    if "entries" in data and isinstance(data.get("entries"), dict):
        meta = {k: v for k, v in data.items() if k != "entries"}
        entries = [_entry_from_json(rel, v) for rel, v in data["entries"].items()]
        return Manifest.from_entries(str(data.get("id") or p.stem), entries, meta=meta, path=p)
    entries = [_entry_from_json(rel, v) for rel, v in data.items()]
    return Manifest.from_entries(p.stem, entries, meta={"format": "install-audit"}, path=p)


_cache: dict[tuple[str, int], Manifest] = {}


def _cached(path: Path) -> Manifest:
    try:
        mt = path.stat().st_mtime_ns
    except OSError:
        mt = -1
    k = (str(path), mt)
    m = _cache.get(k)
    if m is None:
        m = load_manifest(path)
        _cache[k] = m
    return m


def load_install(path: str | os.PathLike | None = None) -> Manifest:
    """The audited install manifest (549 files); only development checkouts have the default one."""
    p = Path(path) if path else DATA_DIR / INSTALL_MANIFEST
    if not path and not p.is_file():
        raise SatkError("NOT_FOUND", f"no install audit manifest in this copy of satk ({INSTALL_MANIFEST})",
                        hint="it describes the maintainers' own install and is not published: pass --manifest <file> "
                             "with an audit of your install, or check a clean copy with --against stock")
    return _cached(p)


def load_stock(path: str | os.PathLike | None = None) -> Manifest:
    """The clean stock manifest (416 files), or an alternative one given by ``path``."""
    return _cached(Path(path) if path else DATA_DIR / STOCK_MANIFEST)


# --------------------------------------------------------------------------- MANIFEST.sha256


def read_sha256_file(path: str | os.PathLike) -> dict[str, tuple[str, str]]:
    """Parse ``<sha256>  <relpath>`` lines: ``{key: (relpath, sha256)}``."""
    out: dict[str, tuple[str, str]] = {}
    with open(path, "r", encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.rstrip("\r\n")
            if not line.strip() or line.startswith("#"):
                continue
            sha, sep, rel = line.partition("  ")
            if not sep or len(sha) != 64:
                raise SatkError("BAD_PARAMS", f"{path}:{n}: expected '<sha256>  <path>'")
            rel = rel.lstrip("*")
            out[key(rel)] = (norm_rel(rel), sha.lower())
    return out


def format_sha256_file(items: Iterable[tuple[str, str]]) -> str:
    """``MANIFEST.sha256`` text for ``(relpath, sha256)`` pairs.

    Same layout as the original clean copy: Windows separators, sorted by the lower-cased
    Windows path, ``\\n`` line ends, trailing newline.
    """
    rows = sorted(((str(PureWindowsPath(norm_rel(rel))), sha) for rel, sha in items), key=lambda r: r[0].lower())
    return "".join(f"{sha}  {rel}\n" for rel, sha in rows)


# --------------------------------------------------------------------------- generation


def build_stock(install: Manifest, *, exe_sha256: dict[str, str] | None = None,
                vorbisfile_sha256: str | None = None, source: str | None = None) -> dict[str, Any]:
    """Generate the stock manifest (JSON object) from an install manifest.

    Args:
        install: the audited install (``load_install()``).
        exe_sha256: accepted ``gta_sa.exe`` hashes by variant; the first is the target
            (default: ``hoodlum-stock`` then ``mta-canonical`` from :mod:`satk.game.exe`).
        vorbisfile_sha256: expected hash of the original ``vorbisFile.dll`` (checked against
            the install's ``vorbisHooked.dll``; default: the MTA reference hash).
        source: name recorded as ``generated_from``.
    """
    from . import exe as _exe

    exe_sha = dict(exe_sha256 or {"hoodlum-stock": _exe.STOCK_SHA256, "mta-canonical": _exe.MTA_SHA256})
    vf_sha = (vorbisfile_sha256 or _exe.VORBISFILE_STOCK_SHA256).lower()
    entries: list[Entry] = []
    skipped = 0
    for e in install:
        role = classify(e.path)
        if role == "stock":
            entries.append(Entry(e.path, e.size, e.sha256, e.mtime))
        elif role != "restore":
            skipped += 1
    for dst, (src, via) in RESTORE.items():
        se = install.get(src)
        if se is None:
            raise SatkError("NOT_FOUND", f"install manifest has no {src!r} (needed for {dst})")
        if via == "exe:stock":
            target, *alts = list(exe_sha.values())
            entries.append(Entry(norm_rel(dst), se.size, target, None, se.path if key(src) != key(dst) else None,
                                 via, tuple(alts)))
        else:
            if se.sha256 != vf_sha:
                raise SatkError("REVISION", f"{src} in the install manifest is {se.sha256}, expected {vf_sha}")
            entries.append(Entry(norm_rel(dst), se.size, se.sha256, se.mtime, se.path, via))
    m = Manifest.from_entries("stock", entries)
    body = {
        "format": STOCK_FORMAT,
        "id": "stock-1.0us-hoodlum",
        "title": "GTA San Andreas 1.0 US (HOODLUM) - clean dev copy (gta-sa-clean)",
        "generated_from": source or (install.path.name if install.path else install.name),
        "files": len(m),
        "bytes": m.total_bytes,
        "skip_nonstock": skipped,
        "rules": {
            "dirs": list(STOCK_DIRS),
            "root_files": list(STOCK_ROOT_FILES),
            "exclude": list(EXCLUDE_GLOBS) + list(EXCLUDE_PATHS),
            "restore": {d: {"from": s, **({"via": v} if v else {})} for d, (s, v) in RESTORE.items()},
            "protect": list(PROTECT_IMGS),
            "fast_check": list(FAST_GLOBS),
        },
        "targets": {"gta_sa.exe": exe_sha, "vorbisFile.dll": {"stock": vf_sha}},
        "entries": {e.path: e.as_json() for e in m},
    }
    return body


def dump_stock(body: dict[str, Any]) -> str:
    """Deterministic, diff-friendly JSON text: one entry per line, LF line ends."""
    head = {k: v for k, v in body.items() if k != "entries"}
    lines = ["{"]
    for k, v in head.items():
        lines.append(f" {json.dumps(k)}: {json.dumps(v, ensure_ascii=False)},")
    lines.append(' "entries": {')
    items = list(body["entries"].items())
    for i, (rel, v) in enumerate(items):
        comma = "," if i + 1 < len(items) else ""
        lines.append(f"  {json.dumps(rel, ensure_ascii=False)}: {json.dumps(v, separators=(', ', ': '))}{comma}")
    lines.append(" }")
    lines.append("}")
    return "\n".join(lines) + "\n"


def _main(argv: list[str] | None = None) -> int:
    """``python -m satk.game.manifests build [--check]``: (re)generate the stock manifest."""
    import argparse

    ap = argparse.ArgumentParser(prog="python -m satk.game.manifests")
    ap.add_argument("cmd", choices=["build"])
    ap.add_argument("--check", action="store_true", help="only compare with the committed file")
    a = ap.parse_args(argv)
    install = load_install()
    text = dump_stock(build_stock(install, source=INSTALL_MANIFEST))
    out = DATA_DIR / STOCK_MANIFEST
    cur = out.read_text(encoding="utf-8") if out.is_file() else None
    if a.check:
        print("up to date" if cur == text else "DIFFERS")
        return 0 if cur == text else 1
    if cur != text:
        from satk.core.paths import atomic_write

        atomic_write(out, text)
    print(f"{out}: {len(json.loads(text)['entries'])} entries")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
