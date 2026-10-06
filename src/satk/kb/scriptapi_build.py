"""Store the scripting API references into ``work/kb/kb.sqlite`` at ``satk kb build`` time.

Tables (created here, next to the KB schema; an older KB without them answers ``NOT_READY``):

=========== ===================================================================================
mta_func    one row per Lua function and side: signature variants, return, implementation, flags
mta_class   OOP classes (parent, static)
mta_oop     class members: method -> global function, variable -> getter/setter
mta_event   built-in events with their parameter names
mta_enum    string tables of C++ enums (the values a string argument accepts)
pawn_sym    SA-MP/open.mp natives, callbacks (``forward``) and numeric ``#define`` constants
=========== ===================================================================================

Sources (:func:`default_inputs`): the MTA tree is ``paths.engine`` at ``HEAD`` when it holds the Lua
definitions, else ``<src>/mtasa-neon`` at ``upstream/master``, else ``<src>/mtasa-blue``; the Neon fork
(``<src>/mtasa-neon`` at ``HEAD``) adds only what upstream does not have. Pawn include folders come from
``kb.pawn_include`` in ``satk.toml`` (a path or a list), ``SATK_KB_PAWN_INCLUDE`` (paths separated by
``os.pathsep``) and ``.inc`` files of the clones under ``paths.src`` (``pawno/include``, ``qawno/include``,
``include``). Every tree is read-only (git without checkout or fetch). Function names, types and lines
also go into ``sym`` (kinds ``lua``, ``lua-event``, ``native``, ``callback``), so ``satk kb search`` finds them.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..core.errors import SatkError
from ..re.gitsrc import DirTree, GitTree, SourceTree
from . import scriptapi as S

__all__ = ["DDL", "TABLES", "ScriptApiInputs", "default_inputs", "build_scriptapi", "PAWN_DIRS"]

DDL = """
CREATE TABLE IF NOT EXISTS mta_func(
  id INTEGER PRIMARY KEY, name TEXT, side TEXT, origin TEXT, ret TEXT,
  variants TEXT,          -- JSON [[[name, type, opt, default], ...], ...] (one list per overload)
  doc TEXT,               -- the ``bool name ( ... )`` comment of the implementation
  impl TEXT, parser TEXT, -- argparser | argreader | raw | unknown
  flags TEXT, enums TEXT, file_id INTEGER, line INTEGER, impl_file_id INTEGER, impl_line INTEGER);
CREATE INDEX IF NOT EXISTS mta_func_name ON mta_func(name COLLATE NOCASE);
CREATE TABLE IF NOT EXISTS mta_class(name TEXT, side TEXT, origin TEXT, parent TEXT, static INTEGER,
  file_id INTEGER, line INTEGER);
CREATE TABLE IF NOT EXISTS mta_oop(cls TEXT, member TEXT, kind TEXT, side TEXT, origin TEXT, func TEXT,
  setter TEXT, impl TEXT, file_id INTEGER, line INTEGER);
CREATE INDEX IF NOT EXISTS mta_oop_func ON mta_oop(func);
CREATE INDEX IF NOT EXISTS mta_oop_cls ON mta_oop(cls COLLATE NOCASE);
CREATE TABLE IF NOT EXISTS mta_event(name TEXT, side TEXT, origin TEXT, params TEXT, file_id INTEGER, line INTEGER);
CREATE TABLE IF NOT EXISTS mta_enum(name TEXT, ctype TEXT, side TEXT, origin TEXT, vals TEXT, file_id INTEGER,
  line INTEGER);
CREATE TABLE IF NOT EXISTS pawn_sym(id INTEGER PRIMARY KEY, kind TEXT, name TEXT, tag TEXT, params TEXT,
  alias TEXT, value TEXT, deprecated INTEGER, inc TEXT, file_id INTEGER, line INTEGER);
CREATE INDEX IF NOT EXISTS pawn_sym_name ON pawn_sym(name COLLATE NOCASE);
"""
TABLES = ("mta_func", "mta_class", "mta_oop", "mta_event", "mta_enum", "pawn_sym")
#: Include folders searched inside every clone under ``paths.src``.
PAWN_DIRS = ("pawno/include", "qawno/include", "include")
_MTA_CANDIDATES = (("engine", None, "HEAD"), ("src", "mtasa-neon", "refs/remotes/upstream/master"),
                   ("src", "mtasa-blue", "HEAD"), ("src", "mtasa-blue", "refs/remotes/origin/master"))
_LUADEFS = "Client/mods/deathmatch/logic/luadefs"


@dataclass
class ScriptApiInputs:
    """Trees of one build; ``None``/empty skips a part (the reason goes into ``skipped``)."""

    mta: SourceTree | None = None
    neon: SourceTree | None = None
    pawn: list[tuple[str, SourceTree]] = field(default_factory=list)   # (label, tree of .inc files)
    repos: dict[str, str] = field(default_factory=dict)
    refs: dict[str, str] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)


def _rel(p: Path, ws: Path) -> str:
    try:
        r = Path(os.path.relpath(p, ws))
        return p.as_posix() if r.as_posix().startswith("..") else r.as_posix()
    except ValueError:
        return p.as_posix()


def _git(repo: Path, ref: str) -> GitTree | None:
    if not (repo / ".git").exists():
        return None
    try:
        t = GitTree(repo, ref)
    except SatkError:
        return None
    return t if t.list((_LUADEFS,), (".cpp",)) else None


def _pawn_roots(cfg_value, env_value: str | None) -> list[Path]:
    out: list[Path] = []
    vals: list[str] = []
    if isinstance(cfg_value, str) and cfg_value.strip():
        vals.append(cfg_value)
    elif isinstance(cfg_value, (list, tuple)):
        vals += [str(x) for x in cfg_value if str(x).strip()]
    if env_value:
        vals += [x for x in env_value.split(os.pathsep) if x.strip()]
    for v in vals:
        p = Path(os.path.expandvars(os.path.expanduser(v.strip())))
        if p not in out:
            out.append(p)
    return out


def default_inputs() -> ScriptApiInputs:
    """Trees from the configuration (``paths.engine``, ``paths.src``, ``kb.pawn_include``)."""
    from ..core.paths import cfg

    c = cfg()
    ws = c.paths.workspace
    inp = ScriptApiInputs()
    for base, sub, ref in _MTA_CANDIDATES:
        root = c.paths.get(base)
        if root is None:
            continue
        repo = Path(root) / sub if sub else Path(root)
        t = _git(repo, ref)
        if t is not None:
            inp.mta = t
            inp.repos["mta-lua"] = _rel(repo, ws)
            inp.refs["mta-lua"] = ref
            break
    if inp.mta is None:
        inp.skipped["mta-lua"] = "no MTA:SA source tree with Client/mods/deathmatch/logic/luadefs (paths.engine, " \
                                 "<src>/mtasa-neon or <src>/mtasa-blue)"
    neon_repo = c.paths.src / "mtasa-neon"
    nt = _git(neon_repo, "HEAD")
    if nt is not None and (inp.mta is None or getattr(inp.mta, "rev", None) != nt.rev):
        inp.neon = nt
        inp.repos["mta-lua-neon"] = _rel(neon_repo, ws)
        inp.refs["mta-lua-neon"] = "HEAD"
    conf = c.get("kb.pawn_include")     # SATK_KB_PAWN_INCLUDE overrides a file value (config rules), else adds it
    roots = _pawn_roots(conf, None if conf is not None else os.environ.get("SATK_KB_PAWN_INCLUDE"))
    missing = [r.as_posix() for r in roots if not r.is_dir()]
    for r in roots:
        if r.is_dir():
            inp.pawn.append((_rel(r, ws), DirTree(r)))
    src = c.paths.src
    if src.is_dir():
        for d in sorted(p for p in src.iterdir() if p.is_dir()):
            if (d / ".git").exists():
                try:
                    t = GitTree(d, "HEAD")
                except SatkError:
                    continue
                if any(p.lower().endswith(".inc") for p in t._ls()):
                    inp.pawn.append((_rel(d, ws), t))
                continue
            for sub in PAWN_DIRS:
                inc = d / sub
                if inc.is_dir() and any(inc.glob("*.inc")):
                    inp.pawn.append((_rel(inc, ws), DirTree(inc)))
                    break
    if missing:
        inp.skipped["pawn:missing"] = f"kb.pawn_include folders not found: {', '.join(missing)}"
    return inp


# --------------------------------------------------------------------------- build


def _j(v) -> str | None:
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"), sort_keys=True) if v else None


def _read_mta(tree: SourceTree) -> dict[str, str]:
    paths = tree.list(S.MTA_PREFIXES, (".cpp", ".h", ".hpp", ".inl"))
    return dict(tree.read_many(paths))


def _func_sig(f: S.MtaFunc) -> str:
    v = f.variants[0] if f.variants else []
    return S.render_sig(f.name, v, f.ret)


def _store_mta(b, key: str, tree: SourceTree, api: S.MtaApi, origin: str, skip: set[tuple[str, str]] | None) -> dict:
    """Write ``api`` rows; ``skip`` = (name, side) already stored from the primary tree (Neon diff)."""
    sid = b.add_source(key, tree)
    w = b.w
    con = b.con
    n_f = n_e = n_o = 0
    rows = []
    for f in api.funcs:
        if skip is not None and (f.name, f.side) in skip:
            continue
        variants = [[[a.name, a.type, int(a.opt), a.default] for a in v] for v in f.variants]
        flags = dict(f.flags)
        rows.append((f.name, f.side, origin, f.ret, _j(variants), f.doc_sig, f.impl, f.parser, _j(flags),
                     _j(f.enums), w.file(sid, f.path), f.line, w.file(sid, f.impl_path), f.impl_line))
        w.sym("lua", f.name, owner=f.side, sig=_func_sig(f), doc=f.doc_sig, sid=sid, path=f.impl_path or f.path,
              line=f.impl_line or f.line)
        n_f += 1
    con.executemany("INSERT INTO mta_func(name,side,origin,ret,variants,doc,impl,parser,flags,enums,file_id,line,"
                    "impl_file_id,impl_line) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    known_funcs = {(f.name, f.side) for f in api.funcs if skip is None or (f.name, f.side) not in skip}
    oop_rows = []
    for o in api.oop:
        if skip is not None and not ((o.func, o.side) in known_funcs or (o.setter, o.side) in known_funcs):
            continue
        oop_rows.append((o.cls, o.member, o.kind, o.side, origin, o.func, o.setter, o.impl, w.file(sid, o.path),
                         o.line))
        n_o += 1
    con.executemany("INSERT INTO mta_oop(cls,member,kind,side,origin,func,setter,impl,file_id,line) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?)", oop_rows)
    if skip is None:
        con.executemany("INSERT INTO mta_class(name,side,origin,parent,static,file_id,line) VALUES(?,?,?,?,?,?,?)",
                        [(c.name, c.side, origin, c.parent, int(c.static), w.file(sid, c.path), c.line)
                         for c in api.classes])
        con.executemany("INSERT INTO mta_enum(name,ctype,side,origin,vals,file_id,line) VALUES(?,?,?,?,?,?,?)",
                        [(e.name, e.ctype, e.side, origin, json.dumps(e.values, ensure_ascii=False),
                          w.file(sid, e.path), e.line) for e in api.enums])
    ev_rows = []
    have_ev = None if skip is None else {(n, s) for n, s in con.execute("SELECT name, side FROM mta_event")}
    for e in api.events:
        if have_ev is not None and (e.name, e.side) in have_ev:
            continue
        ev_rows.append((e.name, e.side, origin, e.params, w.file(sid, e.path), e.line))
        w.sym("lua-event", e.name, owner=e.side, sig=e.params or None, sid=sid, path=e.path, line=e.line)
        n_e += 1
    con.executemany("INSERT INTO mta_event(name,side,origin,params,file_id,line) VALUES(?,?,?,?,?,?)", ev_rows)
    w.flush()
    con.execute("UPDATE source SET files=? WHERE id=?", (len({f.path for f in api.funcs}), sid))
    out = {"functions": n_f, "events": n_e, "oop": n_o}
    if skip is None:
        out.update({k: api.stats[k] for k in ("client", "server", "argparser", "argreader", "doc_sig", "classes",
                                               "enums", "unresolved")})
        out["names"] = len({f.name for f in api.funcs})
    return out


def _store_pawn(b, roots: list[tuple[str, SourceTree]]) -> dict:
    sid = b.add_source("pawn", None)
    w = b.w
    rows = []
    files = 0
    used: list[str] = []
    seen: set[tuple[str, str, str]] = set()
    for label, tree in roots:
        for path, text in tree.files((), (".inc",)):
            if "native" not in text and "forward" not in text:
                continue
            syms = S.parse_pawn(text, path)
            if not any(s.kind in ("native", "callback") for s in syms):
                continue          # not Pawn (a C++ or shader .inc)
            files += 1
            if label not in used:
                used.append(label)
            full = f"{label}/{path}"
            inc = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
            for s in syms:
                k = (s.kind, s.name, inc)
                if k in seen:
                    continue
                seen.add(k)
                params = [[p.name, p.tag, int(p.ref), int(p.const), p.array, p.default] for p in s.params]
                rows.append((s.kind, s.name, s.tag or None, json.dumps(params) if params else None, s.alias,
                             s.value, int(s.deprecated), inc, w.file(sid, full), s.line))
                w.sym({"native": "native", "callback": "callback"}.get(s.kind, "const"), s.name, owner=inc,
                      sig=S.render_pawn(s), sid=sid, path=full, line=s.line)
    b.con.executemany("INSERT INTO pawn_sym(kind,name,tag,params,alias,value,deprecated,inc,file_id,line) "
                      "VALUES(?,?,?,?,?,?,?,?,?,?)", rows)
    w.flush()
    b.con.execute("UPDATE source SET files=?, repo=? WHERE id=?", (files, "; ".join(used) or None, sid))
    return {"files": files, "natives": sum(1 for r in rows if r[0] == "native"),
            "callbacks": sum(1 for r in rows if r[0] == "callback"), "consts": sum(1 for r in rows if r[0] == "const")}


#: Display path of the built-in natives (no file on disk).
BUILTIN_PATH = "satk.mapconv.pawn.FUNCS"
_TAGS = {"float": ("Float", None), "str": ("", "[]"), "arr": ("", "[]"), "int": ("", None), "ref": ("", None)}


def _store_builtin(b) -> dict:
    """Without any include file: the map natives :mod:`satk.mapconv.pawn` reads (names and defaults are satk's)."""
    from ..mapconv.pawn import CONSTANTS, FUNCS

    streamer_default = CONSTANTS.get("STREAMER_OBJECT_SD")
    r = b.con.execute("SELECT id FROM source WHERE key = 'pawn'").fetchone()
    sid = r[0] if r else b.add_source("pawn", None)
    rows = []
    for name in sorted(FUNCS):
        params = []
        for p in FUNCS[name][1]:
            tag, arr = _TAGS.get(p.type, ("", None))
            d = p.default
            if d is streamer_default:
                default = "STREAMER_OBJECT_SD"
            elif type(d) is object or d is None:     # required / no default
                default = None
            elif isinstance(d, tuple):
                default = "{" + ", ".join(str(x) for x in d) + "}"
            elif isinstance(d, str):
                default = f'"{d}"'
            elif isinstance(d, int) and d > 0xFFFF:
                default = f"0x{d:08X}"
            else:
                default = str(d)
            params.append([p.name, tag, 0, 0, arr, default])
        rows.append(("native", name, None, json.dumps(params), None, None, 0, "satk-mapconv",
                     b.w.file(sid, BUILTIN_PATH), None))
        b.w.sym("native", name, owner="satk-mapconv", sig=f"{name}(...)", sid=sid, path=BUILTIN_PATH)
    b.con.executemany("INSERT INTO pawn_sym(kind,name,tag,params,alias,value,deprecated,inc,file_id,line) "
                      "VALUES(?,?,?,?,?,?,?,?,?,?)", rows)
    b.w.flush()
    return {"builtin": len(rows)}


def build_scriptapi(b, inp: ScriptApiInputs) -> dict:
    """Hook of :func:`satk.kb.build.build_kb`: parse the trees of ``inp`` and fill the tables (``b`` = the build)."""
    t0 = time.perf_counter()
    b.con.executescript(DDL)
    for k, v in inp.skipped.items():
        b.stats["skipped"][k] = v
    b.inp.repos.update(inp.repos)
    b.inp.refs.update(inp.refs)
    st: dict = {}
    primary: set[tuple[str, str]] = set()
    if inp.mta is not None:
        api = S.parse_mta(_read_mta(inp.mta))
        st["mta-lua"] = _store_mta(b, "mta-lua", inp.mta, api, "mta", None)
        primary = {(f.name, f.side) for f in api.funcs}
        b.step("mta-lua", t0)
    if inp.neon is not None:
        t1 = time.perf_counter()
        api = S.parse_mta(_read_mta(inp.neon))
        st["mta-lua-neon"] = _store_mta(b, "mta-lua-neon", inp.neon, api, "neon", primary)
        b.step("mta-lua-neon", t1)
    if inp.pawn:
        t2 = time.perf_counter()
        st["pawn"] = _store_pawn(b, inp.pawn)
        b.step("pawn", t2)
    if not st.get("pawn", {}).get("files"):
        b.stats["skipped"]["pawn"] = "no SA-MP/open.mp include files (.inc with natives) found: set kb.pawn_include " \
                                     "in satk.toml to a pawno/include or qawno/include folder (only the built-in " \
                                     "map natives are listed)"
        st.setdefault("pawn", {}).update(_store_builtin(b))
    st["counts"] = {t: b.con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}
    return st
