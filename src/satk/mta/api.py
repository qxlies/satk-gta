"""The MTA:SA scripting reference used by ``satk mta lint``.

Sources, in the order ``ref="auto"`` tries them:

1. the ``mta_*`` tables of the knowledge base (``satk kb build`` reads the MTA source tree: functions with their
   side and argument lists, events, OOP classes, enums; read-only here);
2. the MTA source tree itself (``paths.engine``, else ``<src>/mtasa-neon`` or ``<src>/mtasa-blue``) parsed with the
   knowledge base parser and cached as ``work/cache/mta/ref-<revision>.json``;
3. nothing: the Lua checks still run, the API checks are skipped (a warning says why).

The deprecated/renamed functions come from ``CResourceChecker.Data.h`` of the same tree (the list the MTA server
uses for its own upgrade warnings) and the version from ``Shared/sdk/version.h``. A JSON file written by
:meth:`Reference.to_json` (or by a test) can stand in for all of it (``ref=<file>``).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from ..core.errors import SatkError

__all__ = ["SideRef", "Reference", "load_reference", "lua_facts", "mta_tree", "reference_from_tree",
           "parse_deprecated", "parse_version", "scan_element_types", "REF_VERSION"]

REF_VERSION = 1
_CHECKER = "Server/mods/deathmatch/logic/CResourceChecker.Data.h"
_VERSION_H = "Shared/sdk/version.h"
_DEP_LIST = re.compile(r"SDeprecatedItem\s+(client|server)DeprecatedList\[\]\s*=\s*\{(.*?)\};", re.S)
_DEP_ITEM = re.compile(r'\{\s*(true|false)\s*,\s*"([^"]*)"\s*,\s*"([^"]*)"(?:\s*,\s*"([^"]*)")?\s*\}')
_COMMENT = re.compile(r"//[^\n]*")
_TYPE_NAME = re.compile(r'SetTypeName\(\s*"([a-z0-9-]+)"\s*\)')
_LOGIC = ("Client/mods/deathmatch/logic", "Server/mods/deathmatch/logic")


@dataclass(slots=True)
class SideRef:
    """One side of a function: argument lists (``(name, type, optional)``), exactness, origin, enum tables."""

    variants: list[list[tuple[str, str, bool]]] = field(default_factory=list)
    exact: bool = False          # ArgumentParser (exact C++ signature) vs argument reader (approximate)
    origin: str = "mta"          # mta | neon (only in the Neon fork)
    enums: tuple[str, ...] = ()  # Lua-facing enum table names the function accepts
    ret: str | None = None
    approx: bool = False         # argument reader with branches: argument lists are approximate


class Reference:
    """Functions, events, classes, enums and deprecations of MTA:SA, per side (``client`` / ``server``)."""

    def __init__(self) -> None:
        self.funcs: dict[str, dict[str, SideRef]] = {}
        self.events: dict[str, dict[str, str]] = {}            # name -> {side: params}
        self.classes: dict[str, set[str]] = {}                  # OOP class -> sides
        self.enums: dict[str, list[str]] = {}                   # Lua enum table name -> accepted strings
        self.deprecated: dict[str, dict[str, tuple[bool, str, str]]] = {}   # side -> name -> (removed, how, ver)
        self.element_types: dict[str, str] = {}                 # element type -> origin (mta | neon)
        self.source = "none"
        self.where: str | None = None
        self.version: str | None = None

    # -- queries ------------------------------------------------------------------------

    def __bool__(self) -> bool:
        return bool(self.funcs)

    def sides(self, name: str) -> set[str]:
        return set(self.funcs.get(name, ()))

    def side(self, name: str, side: str) -> SideRef | None:
        return self.funcs.get(name, {}).get(side)

    def event_sides(self, name: str) -> set[str]:
        return set(self.events.get(name, ()))

    def deprecation(self, name: str, side: str) -> tuple[bool, str, str] | None:
        return self.deprecated.get(side, {}).get(name)

    def describe(self) -> dict:
        out = {"source": self.source, "functions": len(self.funcs), "events": len(self.events)}
        if self.where:
            out["from"] = self.where
        if self.version:
            out["mta"] = self.version
        return out

    # -- (de)serialization --------------------------------------------------------------

    def to_json(self) -> dict:
        return {
            "version": REF_VERSION, "source": self.source, "where": self.where, "mta": self.version,
            "funcs": {n: {s: {"v": [[list(a) for a in v] for v in r.variants], "x": int(r.exact), "o": r.origin,
                              "e": list(r.enums), "r": r.ret, "a": int(r.approx)}
                          for s, r in sorted(sides.items())} for n, sides in sorted(self.funcs.items())},
            "events": {n: dict(sorted(v.items())) for n, v in sorted(self.events.items())},
            "classes": {n: sorted(v) for n, v in sorted(self.classes.items())},
            "enums": dict(sorted(self.enums.items())),
            "deprecated": {s: {n: list(v) for n, v in sorted(d.items())} for s, d in sorted(self.deprecated.items())},
            "element_types": dict(sorted(self.element_types.items())),
        }

    @classmethod
    def from_json(cls, d: dict, source: str = "file") -> "Reference":
        r = cls()
        if not isinstance(d, dict) or not isinstance(d.get("funcs", {}), dict):
            raise SatkError("BAD_PARAMS", "not an satk MTA reference (expected {\"funcs\": {...}, ...})")
        for name, sides in d.get("funcs", {}).items():
            out = {}
            for s, v in sides.items():
                if s == "shared":
                    for s2 in ("client", "server"):
                        out[s2] = _side_from(v)
                else:
                    out[s] = _side_from(v)
            r.funcs[name] = out
        for name, sides in d.get("events", {}).items():
            r.events[name] = ({s: "" for s in sides} if isinstance(sides, list) else dict(sides))
        r.classes = {n: set(v) for n, v in d.get("classes", {}).items()}
        r.enums = {n: list(v) for n, v in d.get("enums", {}).items()}
        for s, items in d.get("deprecated", {}).items():
            r.deprecated[s] = {n: (bool(v[0]), str(v[1]), str(v[2]) if len(v) > 2 and v[2] else "")
                               for n, v in items.items()}
        r.element_types = {str(k): str(v) for k, v in d.get("element_types", {}).items()}
        r.source = d.get("source") or source
        r.where = d.get("where")
        r.version = d.get("mta")
        return r


def _side_from(v) -> SideRef:
    if isinstance(v, list):          # a bare list of variants
        v = {"v": v}
    variants = [[(str(a[0]), str(a[1]), bool(a[2]) if len(a) > 2 else False) for a in var] for var in v.get("v", [])]
    return SideRef(variants=variants, exact=bool(v.get("x", 0)), origin=v.get("o", "mta"),
                   enums=tuple(v.get("e", ())), ret=v.get("r"), approx=bool(v.get("a", 0)))


# --------------------------------------------------------------------------- facts


@lru_cache(maxsize=1)
def lua_facts() -> dict:
    """``data/mta/lua.json``: the Lua VM of MTA (libraries, disabled functions, globals, rule data)."""
    from ..core import resources

    return resources.read_json("mta", "lua.json")


# --------------------------------------------------------------------------- MTA tree


def mta_tree() -> Path | None:
    """The first MTA source tree on disk: ``paths.engine``, ``<src>/mtasa-neon``, ``<src>/mtasa-blue``."""
    from ..core.paths import cfg

    c = cfg()
    cands: list[Path] = []
    eng = c.paths.get("engine")
    if eng is not None:
        cands.append(Path(eng))
    src = c.paths.get("src")
    if src is not None:
        cands += [Path(src) / "mtasa-neon", Path(src) / "mtasa-blue"]
    for p in cands:
        if (p / _CHECKER).is_file():
            return p
    return None


def _read(p: Path) -> str | None:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def parse_deprecated(text: str) -> dict[str, dict[str, tuple[bool, str, str]]]:
    """``CResourceChecker.Data.h`` -> ``{side: {old name: (removed, replacement or note, min version)}}``."""
    text = _COMMENT.sub("", text)
    out: dict[str, dict[str, tuple[bool, str, str]]] = {}
    for m in _DEP_LIST.finditer(text):
        side = m.group(1)
        items = out.setdefault(side, {})
        for it in _DEP_ITEM.finditer(m.group(2)):
            items[it.group(2)] = (it.group(1) == "true", it.group(3), it.group(4) or "")
    return out


def parse_version(text: str) -> str | None:
    vals = {}
    for k in ("MAJOR", "MINOR", "MAINTENANCE"):
        m = re.search(rf"#define\s+MTASA_VERSION_{k}\s+(\d+)", text)
        if not m:
            return None
        vals[k] = m.group(1)
    return f"{vals['MAJOR']}.{vals['MINOR']}.{vals['MAINTENANCE']}"


def _head(tree: Path) -> str:
    """The checked-out commit of a git tree (or the mtime of the checker data), for cache keys."""
    try:
        head = (tree / ".git" / "HEAD").read_text(encoding="utf-8").strip()
        if head.startswith("ref: "):
            ref = head[5:]
            p = tree / ".git" / ref
            if p.is_file():
                return p.read_text(encoding="utf-8").strip()
            for line in (tree / ".git" / "packed-refs").read_text(encoding="utf-8").splitlines():
                if line.endswith(" " + ref):
                    return line.split(" ", 1)[0]
        return head
    except OSError:
        try:
            return str(int((tree / _CHECKER).stat().st_mtime))
        except OSError:
            return "0"


def scan_element_types(tree: Path) -> list[str]:
    """Element type names a tree creates (``SetTypeName("...")`` in the deathmatch logic sources)."""
    out: set[str] = set()
    for sub in _LOGIC:
        base = tree / sub
        if not base.is_dir():
            continue
        for p in [*base.glob("*.cpp"), *(base / "luadefs").glob("*.cpp")]:
            t = _read(p)
            if t and "SetTypeName" in t:
                out.update(_TYPE_NAME.findall(t))
    return sorted(out)


def _element_types(tree: Path) -> list[str]:
    from ..core.paths import atomic_write, work

    key = hashlib.sha1(f"{tree.as_posix()}|{_head(tree)}".encode("utf-8")).hexdigest()[:16]
    cache = work("cache", "mta", f"types-{key}.json")
    try:
        return list(json.loads(cache.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    types = scan_element_types(tree)
    try:
        atomic_write(cache, json.dumps(types))
    except (OSError, SatkError):
        pass
    return types


def _tree_extras(r: Reference, tree: Path | None) -> None:
    if tree is None:
        return
    t = _read(tree / _CHECKER)
    if t:
        r.deprecated = parse_deprecated(t)
    v = _read(tree / _VERSION_H)
    if v:
        r.version = parse_version(v)
    r.element_types = {x: "mta" for x in _element_types(tree)}
    from ..core.paths import cfg

    src = cfg().paths.get("src")
    neon = Path(src) / "mtasa-neon" if src is not None else None
    if neon is not None and neon.resolve() != tree.resolve() and (neon / _LOGIC[0]).is_dir():
        for x in _element_types(neon):
            r.element_types.setdefault(x, "neon")


# --------------------------------------------------------------------------- loaders


def _from_kb(path: Path | None = None) -> Reference:
    from ..kb.query import connect

    con = connect(path)
    try:
        if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='mta_func'").fetchone() is None:
            raise SatkError("NOT_READY", "the knowledge base has no MTA tables (built by an older satk)",
                            hint="satk kb build")
        r = Reference()
        for row in con.execute("SELECT name, side, origin, ret, variants, parser, enums, flags FROM mta_func "
                               "ORDER BY name, side, origin"):
            name, side, origin, ret, variants, parser, enums, flags = tuple(row)
            if side not in ("client", "server"):
                continue
            sides = r.funcs.setdefault(name, {})
            if side in sides:
                continue
            vs = [[(str(a[0]), str(a[1]), bool(a[2])) for a in v] for v in json.loads(variants or "[]")]
            fl = json.loads(flags) if flags else {}
            sides[side] = SideRef(variants=vs, exact=parser == "argparser", origin=origin or "mta",
                                  enums=(), ret=ret, approx=bool(fl.get("approx") or fl.get("unresolved")))
            sides[side].enums = tuple(_enum_names(con, json.loads(enums) if enums else [], side))
        for name, side, params in con.execute("SELECT name, side, params FROM mta_event"):
            if side in ("client", "server"):
                r.events.setdefault(name, {})[side] = params or ""
        for name, side in con.execute("SELECT name, side FROM mta_class"):
            r.classes.setdefault(name, set()).add(side)
        for name, vals in con.execute("SELECT name, vals FROM mta_enum ORDER BY side"):
            r.enums.setdefault(name, json.loads(vals))
        src = con.execute("SELECT repo, rev FROM source WHERE key = 'mta-lua'").fetchone()
        r.source = "kb"
        r.where = "kb" + (f" ({src[0]} {str(src[1])[:10]})" if src and src[0] else "")
        return r
    finally:
        con.close()


def _enum_names(con, ctypes: list[str], side: str) -> list[str]:
    out = []
    for ct in ctypes:
        short = ct.rsplit("::", 1)[-1] if not ct.endswith("::Enum") else ct
        row = con.execute("SELECT name FROM mta_enum WHERE ctype = ? OR ctype = ? OR ctype LIKE ? "
                          "ORDER BY side = ? DESC LIMIT 1", (ct, short, f"%::{short}", side)).fetchone()
        if row is not None and row[0] not in out:
            out.append(row[0])
    return out


def reference_from_tree(tree: Path, cache: bool = True) -> Reference:
    """Parse the Lua definitions of an MTA tree (about 5 s); ``cache`` keeps it in work/cache/mta/."""
    from ..core.paths import atomic_write, jpath, work
    from ..kb import scriptapi as S
    from ..re.gitsrc import DirTree, GitTree

    t = None
    if (tree / ".git").exists():
        try:
            t = GitTree(tree, "HEAD")
        except SatkError:
            t = None
    if t is None:
        t = DirTree(tree)
    rev = getattr(t, "rev", None) or hashlib.sha1(jpath(tree).encode("utf-8")).hexdigest()
    cache_file = work("cache", "mta", f"ref-{REF_VERSION}-{str(rev)[:16]}.json") if cache else None
    if cache_file is not None and cache_file.is_file():
        try:
            return Reference.from_json(json.loads(cache_file.read_text(encoding="utf-8")), "source")
        except (OSError, ValueError, SatkError):
            pass
    files = dict(t.read_many(t.list(S.MTA_PREFIXES, (".cpp", ".h", ".hpp", ".inl"))))
    if not files:
        raise SatkError("NOT_READY", f"no MTA Lua definitions in {jpath(tree)}", hint="satk kb build")
    api = S.parse_mta(files)
    r = Reference()
    enum_by_ctype = {}
    for e in api.enums:
        r.enums.setdefault(e.name, list(e.values))
        enum_by_ctype.setdefault(e.ctype, e.name)
        enum_by_ctype.setdefault(e.ctype.rsplit("::", 1)[-1], e.name)
    for f in api.funcs:
        if f.side not in ("client", "server"):
            continue
        sides = r.funcs.setdefault(f.name, {})
        if f.side in sides:
            continue
        en = [enum_by_ctype[c] for c in (f.enums or []) if c in enum_by_ctype]
        sides[f.side] = SideRef(variants=[[(a.name, a.type, bool(a.opt)) for a in v] for v in f.variants],
                                exact=f.parser == "argparser", origin="mta", enums=tuple(dict.fromkeys(en)),
                                ret=f.ret, approx=bool(f.flags.get("approx") or f.flags.get("unresolved")))
    for e in api.events:
        r.events.setdefault(e.name, {})[e.side] = e.params or ""
    for c in api.classes:
        r.classes.setdefault(c.name, set()).add(c.side)
    r.source = "source"
    r.where = f"source ({jpath(tree)})"
    _tree_extras(r, tree)
    if cache_file is not None:
        atomic_write(cache_file, json.dumps(r.to_json(), ensure_ascii=False, separators=(",", ":")))
    return r


def _kb_key() -> tuple:
    from ..kb.query import kb_path

    p = kb_path()
    try:
        st = p.stat()
        return (str(p), st.st_size, int(st.st_mtime))
    except OSError:
        return (str(p), 0, 0)


@lru_cache(maxsize=8)
def _cached(spec: str, key: tuple) -> tuple[Reference, tuple[str, ...]]:
    warn: list[str] = []
    if spec == "none":
        return Reference(), ()
    if spec not in ("auto", "kb", "source"):
        p = Path(spec)
        if not p.is_file():
            raise SatkError("NOT_FOUND", f"no reference file {spec!r}",
                            hint="ref is auto, kb, source, none or a JSON file written by satk")
        try:
            return Reference.from_json(json.loads(p.read_text(encoding="utf-8")), "file"), ()
        except ValueError as e:
            raise SatkError("BAD_PARAMS", f"{p.name}: invalid JSON: {e}") from None
    tree = mta_tree()
    if spec in ("auto", "kb"):
        try:
            r = _from_kb()
            _tree_extras(r, tree)
            if tree is None:
                warn.append("NOT_READY: no MTA source tree on disk: deprecated functions are not checked")
            return r, tuple(warn)
        except SatkError as e:
            if spec == "kb":
                raise
            why = e.msg
    else:
        why = "ref=source"
    if tree is None:
        if spec == "source":
            raise SatkError("NOT_READY", "no MTA source tree (paths.engine, <src>/mtasa-neon or <src>/mtasa-blue)",
                            hint="satk engine setup, or satk kb build where a tree exists")
        return Reference(), (f"NOT_READY: no MTA reference ({why}; no MTA source tree): only Lua checks ran; "
                             "build it with: satk kb build",)
    r = reference_from_tree(tree)
    if spec == "auto":
        warn.append(f"INDEX_MISSING: knowledge base unavailable ({why}); read the MTA tree directly "
                    "(satk kb build makes it faster)")
    return r, tuple(warn)


def load_reference(spec: str = "auto") -> tuple[Reference, list[str]]:
    """``(reference, warnings)`` for ``spec``: auto | kb | source | none | a JSON file."""
    key = _kb_key() if spec in ("auto", "kb") else (spec,)
    r, w = _cached(spec, key)
    return r, list(w)
