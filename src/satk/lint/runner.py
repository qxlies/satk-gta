"""Lint runner: target -> items -> per-file checks -> set checks -> link checks -> :class:`Report`.

Targets (:func:`collect`):

* a file (``.dff .txd .col .ide``), an IMG archive (every DFF/TXD/COL entry), ``<img>/<entry>``;
* a directory, walked recursively (IMG archives inside are expanded). A *game root* (``data/gta.dat``
  inside) is linted like the game loads it: only the IDE files named by ``data/default.dat`` and
  ``data/gta.dat``, the COLFILE buffer rule only for COLFILE files, no index fallback and no orphan
  findings for loose files (the exe loads those by name);
* a SID: ``model:`` (DFF, TXD chain and COL of the model, linked through the index), ``dff:``/``txd:``
  (the blob, plus its model definition from the index), ``col:`` (one collision model), ``file:``/``ide:``
  (a file under the profile root).

Relative paths are tried against the current directory first, then the profile root (any case).
Labels in the findings are paths relative to the profile root when the target is inside it, else relative
to the target's parent directory, with forward slashes (``models/gta3.img/infernus.dff``).
"""

from __future__ import annotations

import os
from contextlib import ExitStack
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable

from ..core.errors import SatkError
from ..core.ids import Sid, is_sid
from ..core.paths import jpath, open_ro, profile_root
from ..formats.dat import parse_dat, read_text, resolve_ci
from ..formats.dff import find_embedded_col
from ..formats.img import ImgArchive
from ..formats.rw import FormatError
from .col import check_col
from .dff import ModelDef, check_dff
from .ide import check_ide, check_ide_set
from .link import Catalog, IndexView, check_links
from .rules import SEV_RANK, SEVERITIES, Collector, Finding, Rules
from .txd import check_txd

__all__ = ["Item", "Report", "lint", "collect", "KINDS"]

#: File kinds the linter reads.
KINDS: tuple[str, ...] = ("dff", "txd", "col", "ide")
_INDEX_SID_KINDS = ("model", "dff", "txd", "col")
_ORDER = {"ide": 0, "txd": 1, "dff": 2, "col": 3}
#: Above this many DFF/COL items the index model names are loaded in bulk instead of one query per name.
_PRELOAD_AT = 200


@dataclass
class Item:
    """One thing to lint. ``read`` returns its bytes (IMG entries are read on demand)."""

    label: str
    kind: str
    read: Callable[[], bytes]
    loose: bool = False
    archive: str | None = None       # lower-case IMG file name for entries
    colfile: bool = False            # loose .col loaded by COLFILE (32 KB buffer rule)
    col_idx: int | None = None       # col: SID -> only this model
    embedded: bool = False           # col: SID of a vehicle's embedded collision
    name: str = ""                   # file name (for file.name_len)

    @property
    def stem(self) -> str:
        return self.label.rsplit("/", 1)[-1].split("#", 1)[0].rsplit(".", 1)[0].lower()


@dataclass
class Target:
    items: list[Item] = field(default_factory=list)
    root: Path | None = None
    game_root: bool = False
    defs: list[ModelDef] = field(default_factory=list)
    skipped: int = 0
    stack: ExitStack = field(default_factory=ExitStack)


@dataclass
class Report:
    """Result of one lint run; ``findings`` sorted by severity (fatal first), file, rule."""

    findings: list[Finding]
    summary: dict[str, int]
    by_rule: dict[str, int]
    files: int
    root: str | None
    preset: str
    rules_source: str
    skipped: int = 0
    warn: list[str] = field(default_factory=list)

    def at_least(self, sev: str) -> list[Finding]:
        r = SEV_RANK[sev]
        return [f for f in self.findings if SEV_RANK[f.sev] >= r]


# --------------------------------------------------------------------------- targets


def _label(p: Path, base: Path | None) -> str:
    if base is not None:
        try:
            rel = os.path.relpath(p, base)
            if not rel.startswith(".."):
                return rel.replace("\\", "/")
        except ValueError:
            pass
    return jpath(p)


def _root_of(profile: str) -> Path | None:
    try:
        return Path(os.path.abspath(profile_root(profile)))
    except SatkError:
        return None


def _within(p: Path, root: Path | None) -> bool:
    if root is None:
        return False
    try:
        return os.path.commonpath([os.path.normcase(os.path.abspath(p)), os.path.normcase(root)]) == \
            os.path.normcase(root)
    except ValueError:
        return False


def _resolve(t: str, profile: str, root_only: bool = False) -> Path:
    p = Path(t)
    if p.is_absolute() and not root_only:
        if p.exists():
            return p
        raise SatkError("NOT_FOUND", f"no such file or directory: {jpath(p)}")
    if p.exists() and not root_only:
        return Path(os.path.abspath(p))
    root = _root_of(profile)
    hit = resolve_ci(root, t) if root is not None else None
    if hit is None:
        raise SatkError("NOT_FOUND", f"{t!r} is neither a path from here nor under the {profile} root",
                        hint="give an absolute path, a path under the game root or a SID (model:411)")
    return hit


def _reader(path: Path) -> Callable[[], bytes]:
    def read() -> bytes:
        with open_ro(path) as f:
            return f.read()
    return read


def _img_items(tg: Target, path: Path, label: str, collector: Collector) -> None:
    try:
        arc = tg.stack.enter_context(ImgArchive.open(path))
    except (FormatError, OSError) as e:
        collector.add("img.parse", label, err=str(e))
        return
    name = path.name.lower()
    for e in arc.entries:
        if e.ext in ("dff", "txd", "col"):
            tg.items.append(Item(f"{label}/{e.name}", e.ext, (lambda a=arc, en=e: a.read(en)), archive=name,
                                 name=e.name))
        else:
            tg.skipped += 1


def _dat_refs(root: Path) -> tuple[list[Path], set[str]]:
    """IDE files and COLFILE paths named by ``data/default.dat`` + ``data/gta.dat`` of a game root."""
    ides: list[Path] = []
    colfiles: set[str] = set()
    for dat in ("data/default.dat", "data/gta.dat"):
        p = resolve_ci(root, dat)
        if p is None:
            continue
        for ln in parse_dat(read_text(p)):
            hit = resolve_ci(root, ln.path) if ln.key in ("IDE", "COLFILE") else None
            if hit is None:
                continue
            if ln.key == "IDE" and hit not in ides:
                ides.append(hit)
            elif ln.key == "COLFILE":
                colfiles.add(os.path.normcase(os.path.abspath(hit)))
    return ides, colfiles


def _walk(tg: Target, d: Path, base: Path | None, collector: Collector) -> None:
    game = resolve_ci(d, "data/gta.dat") is not None
    tg.game_root = tg.game_root or game
    ides: list[Path] = []
    colfiles: set[str] = set()
    if game:
        ides, colfiles = _dat_refs(d)
        for p in ides:
            tg.items.append(Item(_label(p, base), "ide", _reader(p), loose=True, name=p.name))
    files: list[Path] = []
    for cur, dirs, names in os.walk(d):
        dirs.sort(key=str.lower)
        for n in sorted(names, key=str.lower):
            files.append(Path(cur) / n)
    for p in files:
        ext = p.suffix.lower().lstrip(".")
        if ext == "img":
            _img_items(tg, p, _label(p, base), collector)
        elif ext in KINDS:
            if ext == "ide" and game:
                if p not in ides:
                    tg.skipped += 1
                continue
            colfile = game and ext == "col" and os.path.normcase(os.path.abspath(p)) in colfiles
            if game and ext == "col" and not colfile:
                tg.skipped += 1                     # a loose .col no COLFILE names is never loaded
                continue
            tg.items.append(Item(_label(p, base), ext, _reader(p), loose=True, colfile=colfile, name=p.name))


def _blob_label(ref, root: Path | None) -> str:
    base = _label(Path(ref.path), root)
    return f"{base}/{ref.name}" if Path(ref.path).suffix.lower() == ".img" else base


def _sid_items(tg: Target, sid: str, db, ix: IndexView) -> None:
    kind, _, key = sid.partition(":")
    root = Path(db.root) if getattr(db, "root", None) else None
    tg.root = root

    def blob_item(ref, k: str) -> Item:
        return Item(_blob_label(ref, root), k, (lambda r=ref: db.read_blob(r)), name=ref.name)

    def add_def(d: ModelDef | None) -> None:
        if d is not None:
            tg.defs.append(d)

    if kind == "model":
        mf = db.model_files(sid)
        d = _index_def(ix, mf.name)
        if d is not None:
            d = replace(d, chain=tuple(Path(r.name).stem.lower() for r in mf.txd_chain))
        add_def(d)
        if mf.dff is not None:
            tg.items.append(blob_item(mf.dff, "dff"))
        for r in mf.txd_chain:
            tg.items.append(blob_item(r, "txd"))
        if mf.col is not None and mf.col.via != "embedded":
            it = blob_item(mf.col.blob, "col")
            it.col_idx = mf.col.idx
            tg.items.append(it)
        return
    if kind in ("dff", "txd"):
        ref = db.blob_ref(sid)
        tg.items.append(blob_item(ref, kind))
        if kind == "dff":
            add_def(_index_def(ix, Path(ref.name).stem))
        return
    # col:
    env = db.get(sid)
    fsid = env.get("file")
    if not fsid:
        raise SatkError("NOT_FOUND", f"{sid}: the index names no file for it")
    ref = db.blob_ref(fsid)
    if env.get("via") == "embedded":
        def read_emb(r=ref) -> bytes:
            data = db.read_blob(r)
            span = find_embedded_col(data)
            if span is None:
                raise SatkError("NOT_FOUND", f"{sid}: no embedded collision in {r.name}")
            return data[span[0]:span[0] + span[1]]
        tg.items.append(Item(_blob_label(ref, root) + "#col", "col", read_emb, embedded=True, name=ref.name))
    else:
        it = blob_item(ref, "col")
        it.col_idx = int(env.get("idx", 0))
        tg.items.append(it)
    add_def(_index_def(ix, env.get("name") or key))


def collect(target: str, profile: str, ix: IndexView, collector: Collector) -> Target:
    """Items of ``target`` (see the module docstring). The caller closes ``Target.stack`` (open IMGs)."""
    tg = Target()
    try:
        return _collect(tg, target, profile, ix, collector)
    except BaseException:
        tg.stack.close()
        raise


def _collect(tg: Target, target: str, profile: str, ix: IndexView, collector: Collector) -> Target:
    t = target.strip()
    kind = t.split(":", 1)[0].lower() if is_sid(t) else ""
    root_only = False
    if kind in ("file", "ide"):
        t, root_only = t.split(":", 1)[1], True
    elif kind in _INDEX_SID_KINDS:
        if ix.db is None:
            raise SatkError("INDEX_MISSING", f"{target}: a SID target needs the {profile} index",
                            hint=f"satk index build --profile {profile}")
        _sid_items(tg, str(Sid.parse(t)), ix.db, ix)
        return tg
    norm = t.replace("\\", "/")
    cut = norm.lower().find(".img/")
    if cut > 0:
        arc_path = _resolve(norm[:cut + 4], profile, root_only)
        entry = norm[cut + 5:]
        root = _root_of(profile)
        tg.root = root if _within(arc_path, root) else arc_path.parent
        try:
            arc = tg.stack.enter_context(ImgArchive.open(arc_path))
        except (FormatError, OSError) as e:
            raise SatkError("UNSUPPORTED", f"cannot open {jpath(arc_path)}: {e}") from None
        e = arc.find(entry)
        if e is None:
            raise SatkError("NOT_FOUND", f"no entry {entry!r} in {jpath(arc_path)}",
                            hint=f"satk formats ls {jpath(arc_path)} --name {entry.rsplit('.', 1)[0]}")
        if e.ext not in ("dff", "txd", "col"):
            raise SatkError("UNSUPPORTED", f"{e.name}: the linter reads {', '.join(KINDS)}")
        tg.items.append(Item(f"{_label(arc_path, tg.root)}/{e.name}", e.ext, (lambda a=arc, en=e: a.read(en)),
                             archive=arc_path.name.lower(), name=e.name))
        return tg
    p = _resolve(t, profile, root_only)
    root = _root_of(profile)
    base = root if _within(p, root) else p.parent
    tg.root = base
    if p.is_dir():
        _walk(tg, p, base, collector)
        if not tg.items and not collector.findings:
            raise SatkError("NOT_FOUND", f"no {', '.join(KINDS)} or img files under {jpath(p)}")
        return tg
    ext = p.suffix.lower().lstrip(".")
    if ext == "img":
        _img_items(tg, p, _label(p, base), collector)
        return tg
    if ext not in KINDS:
        raise SatkError("UNSUPPORTED", f"{p.name}: the linter reads {', '.join(KINDS)}, img and directories")
    tg.items.append(Item(_label(p, base), ext, _reader(p), loose=True, name=p.name))
    return tg


# --------------------------------------------------------------------------- run


def _open_index(profile: str, use: bool) -> tuple[IndexView, str | None]:
    if not use:
        return IndexView(None), None
    from ..index.api import open_index

    try:
        return IndexView(open_index(profile)), None
    except SatkError as e:
        return IndexView(None), f"{e.code}: no {profile} index, links outside the target are not checked"


def lint(target: str, *, profile: str = "vanilla", preset: str = "game", config: str | None = None,
         only: list[str] | None = None, use_index: bool = True) -> Report:
    """Lint ``target`` and return every finding (all severities).

    Args:
        target: file, directory, IMG, ``<img>/<entry>`` or SID (see the module docstring).
        profile: profile whose root resolves relative paths and whose index answers names outside the target.
        preset: rule preset (``game`` = defaults calibrated on vanilla, ``strict`` = for new content).
        config: JSON file with rule overrides ``{"rules": {id: {...}}}``.
        only: run only the rules matching these patterns.
        use_index: look names up in the profile index (SID targets always need it).
    """
    rules = Rules.load(preset=preset, config=config, only=only)
    c = Collector(rules)
    t0 = target.strip()
    sid_target = is_sid(t0) and t0.split(":", 1)[0].lower() in _INDEX_SID_KINDS
    ix, ix_warn = _open_index(profile, use_index or sid_target)
    warn: list[str] = []
    tg = collect(target, profile, ix, c)
    with tg.stack:
        if tg.game_root and not sid_target:
            ix = IndexView(None)                    # a game root is self-contained
        elif ix_warn:
            warn.append(ix_warn)
        cat = Catalog(defs=list(tg.defs))
        if ix.available and sum(1 for it in tg.items if it.kind in ("dff", "col")) > _PRELOAD_AT:
            ix.preload()
        nmax = int(rules.param("file.name_len", "max", 23))
        by_name: dict[str, ModelDef] | None = None
        for it in sorted(tg.items, key=lambda x: _ORDER[x.kind]):  # stable: IDE first, then TXD, DFF, COL
            if it.name and len(it.name) > nmax and not it.embedded:
                c.add("file.name_len", it.label, name=it.name, n=len(it.name))
            try:
                data = it.read()
            except OSError as e:
                c.add(f"{it.kind}.parse", it.label, err=f"cannot read: {e}")
                continue
            if it.kind == "ide":
                defs, txdp = check_ide(c, it.label, data.decode("latin-1"))
                cat.defs.extend(defs)
                for child, parent in txdp:
                    if child.lower() != parent.lower():
                        cat.txdp[child.lower()] = parent.lower()
            elif it.kind == "txd":
                f = check_txd(c, it.label, data, archive=it.archive, loose=it.loose)
                if f is not None:
                    cat.add_txd(f)
            elif it.kind == "dff":
                if by_name is None:                 # every IDE is read by now
                    by_name = {d.name.lower(): d for d in cat.defs}
                d = by_name.get(it.stem)
                if d is None:
                    d = _index_def(ix, it.stem)
                    if d is not None and all(x.name.lower() != d.name.lower() for x in cat.index_defs):
                        cat.index_defs.append(d)
                f = check_dff(c, it.label, data, d, archive=it.archive, loose=it.loose,
                              guess=not (by_name or ix.available))
                if f is not None:
                    cat.add_dff(f)
            else:
                for f in check_col(c, it.label, data, colfile=it.colfile, only_idx=it.col_idx, embedded=it.embedded):
                    cat.add_col(c, f)
        check_ide_set(c, cat.defs)
        check_links(c, cat, ix, skip_loose_orphans=tg.game_root)
    findings = sorted(c.findings, key=Finding.sort_key)
    summary = {s: 0 for s in reversed(SEVERITIES)}
    for f in findings:
        summary[f.sev] += 1
    by_rule = dict(sorted(c.fired.items(), key=lambda kv: (-SEV_RANK[rules[kv[0]].sev], -kv[1], kv[0])))
    return Report(findings, summary, by_rule, len(tg.items), jpath(tg.root) if tg.root else None, preset,
                  rules.source, tg.skipped, warn)


def _index_def(ix: IndexView, stem: str) -> ModelDef | None:
    """The definition of model ``stem`` in the index (``None`` without an index or such a model)."""
    env = ix.model(stem)
    if not env or not str(env.get("id", "")).startswith("model:"):
        return None
    try:
        mid = int(str(env["id"]).split(":", 1)[1].split("@", 1)[0])
    except ValueError:
        return None
    return ModelDef(mid, env.get("name") or stem, env.get("txd"), env.get("sec") or "objs", env.get("draw"), "index")
