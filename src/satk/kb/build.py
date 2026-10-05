"""Build ``work/kb/kb.sqlite`` (owner M2-02): sources -> symbols, structs, opcodes, facts, chunks.

``build_kb(out, Inputs(...))`` takes explicit source trees (tests pass synthetic
:class:`~satk.re.gitsrc.DirTree` s); :func:`default_inputs` wires the configured workspace:

========== ======================================================= =============== ===========
key        tree                                                    license         policy
========== ======================================================= =============== ===========
gta-reversed ``src/gta-reversed`` @ ``origin/master``              none            local-only
plugin-sdk ``src/plugin-sdk-sa`` @ ``HEAD``                        Zlib            mirror
mta-upstream ``src/mtasa-neon`` @ ``upstream/master``              GPL-3.0         mirror
mta-neon   ``src/mtasa-neon`` @ ``HEAD`` (only files that differ)  GPL-3.0         mirror
cleo-ai    ``src/cleo-ai`` @ ``HEAD`` (opcode reference)           none            local-only
research   ``docs/research/*.md`` of the workspace                 ours            ours
facts      :mod:`satk.kb.facts`                                    ours            ours
========== ======================================================= =============== ===========

Git trees are read with ``git cat-file`` (no checkout, no lazy fetch: the clones stay clean).
The DB is written to a temporary file next to ``out`` and moved into place atomically.
"""

from __future__ import annotations

import bisect
import json
import os
import re
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from ..core.errors import SatkError
from ..core.paths import ensure_writable
from ..re.cpp import ConstEval, norm_type, PRIMITIVE_SIZES
from ..re.gitsrc import DirTree, GitTree, SourceTree
from . import cxx
from .cxx import ClassDef, FileScan, camel_words
from .layout import Layouts

__all__ = ["SCHEMA_VERSION", "Inputs", "default_inputs", "build_kb", "schema_sql", "SOURCES"]

SCHEMA_VERSION = 1
CPP_EXTS = (".h", ".hpp", ".cpp", ".inl")
GR_PREFIX = "source"
PS_PREFIX = "plugin_sa/game_sa"
MTA_PREFIXES = ("Client/game_sa", "Client/multiplayer_sa", "Client/sdk/game")
MTA_LAYOUT_PREFIXES = ("Client/game_sa", "Client/multiplayer_sa")
CLEO_DOCS = ("reference/syntax-guide.md", "reference/sdk-api.md", "reference/enums.md")

#: key -> (title, license, policy)
SOURCES: dict[str, tuple[str, str, str]] = {
    "gta-reversed": ("gta-reversed: reimplementation of gta_sa.exe 1.0 US", "none", "local-only"),
    "plugin-sdk": ("plugin-sdk (plugin_sa/game_sa)", "Zlib", "mirror"),
    "mta-upstream": ("MTA:SA upstream Client/game_sa, multiplayer_sa, sdk/game", "GPL-3.0", "mirror"),
    "mta-neon": ("MTA:SA Neon fork (files that differ from upstream)", "GPL-3.0", "mirror"),
    "cleo-ai": ("cleo-ai opcode reference (Sanny Builder Library data)", "none", "local-only"),
    "research": ("satk research reports (docs/research)", "ours", "ours"),
    "facts": ("curated engine facts (report 24 §6)", "ours", "ours"),
}


def schema_sql() -> str:
    return Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")


@dataclass
class Inputs:
    """Trees of one build; ``None`` skips a source (recorded in ``skipped``)."""

    gtarev: SourceTree | None = None
    pluginsdk: SourceTree | None = None
    upstream: SourceTree | None = None
    neon: SourceTree | None = None
    cleo: SourceTree | None = None
    research: SourceTree | None = None
    exe: Path | None = None
    repos: dict[str, str] = field(default_factory=dict)     # key -> display path of the repository
    refs: dict[str, str] = field(default_factory=dict)      # key -> git ref
    skipped: dict[str, str] = field(default_factory=dict)


def _ws_rel(p: Path, ws: Path) -> str:
    try:
        return Path(os.path.relpath(p, ws)).as_posix()
    except ValueError:
        return p.as_posix()


def default_inputs(*, research: bool = True) -> Inputs:
    """Trees from the configuration (``paths.src``, ``paths.workspace``, ``paths.game``)."""
    from ..core.paths import cfg

    c = cfg()
    ws = c.paths.workspace
    src = c.paths.src
    inp = Inputs()

    def git(key: str, repo: Path, ref: str) -> SourceTree | None:
        if (repo / ".git").exists():
            try:
                t: SourceTree = GitTree(repo, ref)
            except SatkError as e:
                inp.skipped[key] = e.msg
                return None
            inp.refs[key] = ref
        elif repo.is_dir() and key != "mta-upstream":
            t = DirTree(repo)
        else:
            inp.skipped[key] = f"not found: {repo.as_posix()}"
            return None
        inp.repos[key] = _ws_rel(repo, ws)
        return t

    inp.gtarev = git("gta-reversed", src / "gta-reversed", "origin/master")
    if inp.gtarev is None and "gta-reversed" in inp.skipped and (src / "gta-reversed" / ".git").exists():
        inp.skipped.pop("gta-reversed")
        inp.gtarev = git("gta-reversed", src / "gta-reversed", "HEAD")
    inp.pluginsdk = git("plugin-sdk", src / "plugin-sdk-sa", "HEAD")
    inp.upstream = git("mta-upstream", src / "mtasa-neon", "refs/remotes/upstream/master")
    inp.neon = git("mta-neon", src / "mtasa-neon", "HEAD")
    inp.cleo = git("cleo-ai", src / "cleo-ai", "HEAD")
    rdir = ws / "docs" / "research"
    if research and rdir.is_dir():
        inp.research = DirTree(rdir, label="research")
        inp.repos["research"] = _ws_rel(rdir, ws)
    elif research:
        inp.skipped["research"] = f"not found: {rdir.as_posix()}"
    exe = c.paths.game / "gta_sa.exe"
    gr = c.paths.get("game_root")
    if not exe.is_file() and gr is not None and (gr / "gta_sa.exe").is_file():
        exe = gr / "gta_sa.exe"
    inp.exe = exe if exe.is_file() else None
    if inp.exe is None:
        inp.skipped["exe"] = "gta_sa.exe not found: exe checks of facts are skipped"
    return inp


# --------------------------------------------------------------------------- writer


class _Writer:
    def __init__(self, con: sqlite3.Connection):
        self.con = con
        self.files: dict[tuple[int, str], int] = {}
        self.n_sym = 0
        self.sym_rows: list[tuple] = []
        self.fts_rows: list[tuple] = []
        self.chunk_rows: list[tuple] = []
        self.addr_rows: list[tuple] = []
        self.chunk_id = 0

    def source(self, key: str, repo: str | None, ref: str | None, rev: str | None) -> int:
        title, lic, policy = SOURCES[key]
        return self.con.execute("INSERT INTO source(key,title,license,policy,repo,ref,rev,files) VALUES(?,?,?,?,?,?,?,0)",
                                (key, title, lic, policy, repo, ref, rev)).lastrowid

    def file(self, sid: int, path: str | None) -> int | None:
        if not path:
            return None
        k = (sid, path)
        fid = self.files.get(k)
        if fid is None:
            fid = self.con.execute("INSERT INTO file(source_id,path) VALUES(?,?)", k).lastrowid
            self.files[k] = fid
        return fid

    def sym(self, kind: str, name: str, *, owner: str | None = None, addr: int | None = None, sig: str | None = None,
            doc: str | None = None, sid: int, path: str | None = None, line: int | None = None,
            flags: dict | None = None) -> None:
        self.n_sym += 1
        fid = self.file(sid, path)
        fl = json.dumps(flags, separators=(",", ":"), sort_keys=True) if flags else None
        self.sym_rows.append((self.n_sym, kind, name, owner, addr, sig, doc, fid, line, fl))
        self.fts_rows.append((self.n_sym, name, camel_words(name) + (" " + owner.lower() if owner else ""),
                              sig or "", doc or ""))
        if len(self.sym_rows) >= 20000:
            self.flush()

    def chunk(self, sid: int, path: str, text: str, *, markdown: bool = False, headings=None,
              max_lines: int = 80) -> int:
        lines = text.splitlines()
        fid = self.file(sid, path)
        n = 0
        for a, z in cxx.chunks(text, markdown=markdown, max_lines=max_lines):
            body = "\n".join(lines[a - 1:z])
            if len(body) > 24000:
                body = body[:24000]
            self.chunk_id += 1
            n += 1
            head = headings(a, z) if headings else ""
            self.chunk_rows.append((self.chunk_id, head or "", body, fid, a))
            if "0x" in body or "0X" in body:
                for k, ln in enumerate(lines[a - 1:z]):
                    if "0x" in ln or "0X" in ln:
                        for ad in cxx.exe_addrs(ln):
                            self.addr_rows.append((ad, self.chunk_id, a + k))
        if len(self.chunk_rows) >= 4000:
            self.flush()
        return n

    def flush(self) -> None:
        c = self.con
        if self.sym_rows:
            c.executemany("INSERT INTO sym(id,kind,name,owner,addr,sig,doc,file_id,line,flags) VALUES(?,?,?,?,?,?,?,?,?,?)",
                          self.sym_rows)
            c.executemany("INSERT INTO sym_fts(rowid,name,words,sig,doc) VALUES(?,?,?,?,?)", self.fts_rows)
            self.sym_rows, self.fts_rows = [], []
        if self.chunk_rows:
            c.executemany("INSERT INTO chunk(rowid,heading,body,file_id,line) VALUES(?,?,?,?,?)", self.chunk_rows)
            self.chunk_rows = []
        if self.addr_rows:
            c.executemany("INSERT INTO addr_ref(addr,chunk_id,line) VALUES(?,?,?)", self.addr_rows)
            self.addr_rows = []


# --------------------------------------------------------------------------- helpers


def _hexs(v) -> str:
    if isinstance(v, bool):
        return str(int(v))
    if isinstance(v, int):
        return f"0x{v:X}" if abs(v) >= 0x100 else str(v)
    if isinstance(v, float):
        return repr(round(v, 6))
    return str(v)


def _dec(v) -> str:
    if isinstance(v, bool):
        return str(int(v))
    if isinstance(v, float):
        return repr(round(v, 6))
    return str(v)


def _headings(fs: FileScan):
    """``(first, last) -> heading`` from the function definitions and classes of one file."""
    starts = sorted((f.line, f.name) for f in fs.funcs)
    lines = [s for s, _n in starts]
    classes = sorted(((c.line, c.end_line, c.name) for c in fs.classes), key=lambda x: (x[0], -x[1]))

    def heading(a: int, z: int) -> str:
        i = bisect.bisect_left(lines, a)
        names = []
        while i < len(lines) and lines[i] <= z and len(names) < 3:
            names.append(starts[i][1])
            i += 1
        if names:
            return " ".join(names)
        best = ""
        for s, e, n in classes:
            if s <= a <= e:
                best = n
        return best

    return heading


def _ev_from_scans(scans: list[FileScan], sizes_out: dict[str, int], enum_sizes: dict[str, int],
                   seed: ConstEval | None = None) -> ConstEval:
    """A :class:`ConstEval` from the constants, enums, aliases and size asserts of ``scans``."""
    ev = seed if seed is not None else ConstEval()
    enums: dict[str, list[tuple[str, str]]] = {}
    for fs in scans:
        for c in fs.consts:
            if c.kind == "const":
                ev.define(c.name, c.expr)
            else:
                enums.setdefault(c.owner or "", []).append((c.name, c.expr))
        for a, t in fs.aliases:
            ev.alias(a, t)
        for ename, under in fs.enum_types:
            if ename:
                size = PRIMITIVE_SIZES.get(norm_type(under)) if under else 4
                ev.enum_sizes.setdefault(ename, size or 4)
                enum_sizes.setdefault(ename, size or 4)
    for _round in range(3):
        for ename, members in enums.items():
            prev: int | None = -1
            for member, expr in members:
                key = f"{ename}::{member}" if ename else member
                if key in ev.values:
                    prev = ev.values[key] if isinstance(ev.values[key], int) else None
                    continue
                v = ev.eval(expr) if expr else (prev + 1 if isinstance(prev, int) else None)
                if isinstance(v, bool):
                    v = int(v)
                if isinstance(v, int):
                    ev.values[key] = v
                    if member not in ev.values and member not in ev.raw:
                        ev.values[member] = v
                prev = v if isinstance(v, int) else None
    pending = [(a.type, a.expr) for fs in scans for a in fs.asserts if a.kind == "size"]
    for _round in range(5):
        left = []
        for t, expr in pending:
            v = ev.eval(expr)
            if isinstance(v, int) and v > 0:
                sizes_out.setdefault(t, v)
                ev.sizes.setdefault(t, v)
                ev.sizes.setdefault(t.rsplit("::", 1)[-1], v)
            else:
                left.append((t, expr))
        if len(left) == len(pending):
            break
        pending = left
    return ev


def _eval_sizes(pending: list[tuple[str, str]], ev: ConstEval) -> dict[str, int]:
    """Evaluate size asserts to a fixpoint (sizes may use other sizes)."""
    out: dict[str, int] = {}
    for _round in range(5):
        left = []
        for t, expr in pending:
            v = ev.eval(expr)
            if isinstance(v, int) and v > 0:
                out.setdefault(t, v)
                ev.sizes.setdefault(t, v)
            else:
                left.append((t, expr))
        if len(left) == len(pending):
            break
        pending = left
    return out


def _const_value(ev: ConstEval, c) -> object | None:
    if c.kind == "enum":
        key = f"{c.owner}::{c.name}" if c.owner else c.name
        v = ev.values.get(key)
        if v is None:
            v = ev.value(c.name)
        return v
    return ev.value(c.name)


def _changed(upstream: SourceTree | None, neon: SourceTree, prefixes, exts) -> list[str]:
    """Paths of ``neon`` (under ``prefixes``) that are new or differ from ``upstream``."""
    paths = neon.list(prefixes, exts)
    if upstream is None:
        return paths
    if isinstance(upstream, GitTree) and isinstance(neon, GitTree):
        a, b = upstream._ls(), neon._ls()
        return [p for p in paths if a.get(p) != b.get(p)]
    up = dict(upstream.read_many(paths))
    return [p for p, t in neon.read_many(paths) if up.get(p) != t]


def _lookup(table: dict[str, list], name: str) -> list:
    """Values for ``name``: exact, else any entry whose last ``::`` part equals the last part of ``name``."""
    if name in table:
        return table[name]
    short = name.rsplit("::", 1)[-1]
    out: list = []
    for k, v in table.items():
        if k.rsplit("::", 1)[-1] == short:
            out.extend(v)
    return out


_REG_HANDLER = re.compile(r"REGISTER_COMMAND_HANDLER\s*\(\s*COMMAND_([A-Z0-9_]+)\s*,\s*([A-Za-z_][\w:]*)")


# --------------------------------------------------------------------------- sources


class _Build:
    def __init__(self, con: sqlite3.Connection, inp: Inputs, progress: Callable[[str], None] | None):
        self.w = _Writer(con)
        self.con = con
        self.inp = inp
        self.progress = progress
        self.stats: dict = {"skipped": dict(inp.skipped)}
        self.timings: dict[str, float] = {}
        self.ps_offsets: dict[tuple[str, str], tuple[int, str]] = {}
        self.handlers: dict[str, tuple[str, str, int, int | None]] = {}   # opcode name -> (fn, path, line, def line)
        self.sids: dict[str, int] = {}
        self.gr_funcs: dict[str, list[int]] = {}
        self.gr_globals: dict[str, list[int]] = {}
        self.limits: dict[str, list[int]] = {}
        self.struct_sizes: dict[str, dict[str, int]] = {}
        self.consts: dict[str, dict[str, object]] = {}

    def scan(self, path: str, text: str, **kw) -> FileScan:
        """:func:`cxx.scan_file`; a file the scanner cannot handle is counted and kept as text only."""
        try:
            return cxx.scan_file(path, text, **kw)
        except (ValueError, IndexError, KeyError, RecursionError) as e:
            errs = self.stats.setdefault("scan_errors", [])
            if len(errs) < 20:
                errs.append(f"{path}: {type(e).__name__}: {e}"[:200])
            self.stats["scan_errors_total"] = self.stats.get("scan_errors_total", 0) + 1
            return FileScan(path)

    def step(self, name: str, t0: float) -> None:
        self.timings[name] = round(time.perf_counter() - t0, 2)
        if self.progress:
            self.progress(name)

    def add_source(self, key: str, tree: SourceTree | None) -> int:
        rev = getattr(tree, "rev", None) if tree is not None else None
        sid = self.w.source(key, self.inp.repos.get(key), self.inp.refs.get(key), rev)
        self.sids[key] = sid
        return sid

    # -- shared: classes -> struct/field rows -----------------------------------------

    def write_structs(self, key: str, sid: int, scans: list[FileScan], ev: ConstEval, sizes: dict[str, int],
                      enum_sizes: dict[str, int], offsets: dict[tuple[str, str], tuple[int, str]],
                      prefixes: tuple[str, ...] | None = None,
                      xoffsets: dict[tuple[str, str], tuple[int, str]] | None = None) -> dict:
        classes: list[ClassDef] = []
        size_lines: dict[str, tuple[str, int]] = {}
        for fs in scans:
            classes.extend(fs.classes)
            for a in fs.asserts:
                if a.kind == "size":
                    size_lines.setdefault(a.type, (fs.path, a.line))
        lay = Layouts(classes, ev, sizes=sizes, offsets=offsets, xoffsets=xoffsets, enum_sizes=enum_sizes)
        st = {"classes": 0, "fields": 0, "ok": 0, "mismatch": 0, "unverified": 0, "partial": 0}
        rows_s = []
        rows_f = []
        sid_next = self.con.execute("SELECT COALESCE(MAX(id),0) FROM struct").fetchone()[0]
        for c in classes:
            if c.template or (prefixes and not c.path.startswith(prefixes)):
                continue
            if not c.members and not c.bases and lay.asserted(c.name) is None:
                continue
            res = lay.layout(c)
            if res is None:
                continue
            asserted = lay.asserted(c.name) if c.short == c.name.rsplit("::", 1)[-1] else None
            if res.unknown:
                verdict = "partial"
            elif asserted is None:
                verdict = "unverified"
            elif res.size == asserted:
                verdict = "ok"
            else:
                verdict = "mismatch"
            st[verdict] += 1
            st["classes"] += 1
            sid_next += 1
            sl = size_lines.get(c.name) or size_lines.get(c.short)
            rows_s.append((sid_next, c.name, c.kind, sid, self.w.file(sid, c.path), c.line,
                           ", ".join(c.bases) or None, asserted, res.size, int(res.vptr), len(res.fields), verdict,
                           sl[1] if sl and sl[0] == c.path else None))
            for i, f in enumerate(res.fields):
                rows_f.append((sid_next, i, f.name, f.type, f.off, f.size, f.bit, f.bits, f.src, f.line, f.note))
            st["fields"] += len(res.fields)
        self.con.executemany("INSERT INTO struct(id,name,kind,source_id,file_id,line,bases,size,calc,vptr,nfields,layout,"
                             "size_line) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", rows_s)
        self.con.executemany("INSERT INTO field(struct_id,idx,name,type,off,size,bit,bits,src,line,note) "
                             "VALUES(?,?,?,?,?,?,?,?,?,?,?)", rows_f)
        self.struct_sizes[key] = {k: v for k, v in sizes.items()}
        return st

    # -- plugin-sdk -------------------------------------------------------------------

    def pluginsdk(self) -> None:
        tree = self.inp.pluginsdk
        if tree is None:
            return
        t0 = time.perf_counter()
        from ..re.sources.pluginsdk import scan_pluginsdk

        sid = self.add_source("plugin-sdk", tree)
        scans: list[FileScan] = []
        nfiles = nchunks = 0
        for path, text in tree.files((PS_PREFIX,), CPP_EXTS):
            nfiles += 1
            fs = self.scan(path, text, funcs=False)
            scans.append(fs)
            nchunks += self.w.chunk(sid, path, text, headings=_headings(fs))
        sizes: dict[str, int] = {}
        enum_sizes: dict[str, int] = {}
        ev = _ev_from_scans(scans, sizes, enum_sizes)
        offsets: dict[tuple[str, str], tuple[int, str]] = {}
        for fs in scans:
            for a in fs.asserts:
                if a.kind == "offset" and a.member and "." not in a.member and "[" not in a.member:
                    v = ev.eval(a.expr)
                    if isinstance(v, int):
                        offsets.setdefault((a.type.rsplit("::", 1)[-1], a.member), (v, "assert"))
        self.ps_offsets = {k: (v[0], "plugin-sdk") for k, v in offsets.items()}
        st = self.write_structs("plugin-sdk", sid, scans, ev, sizes, enum_sizes, offsets)
        # methods declared in classes: signatures for the plugin-sdk function names
        decls: dict[str, tuple[str, str, int]] = {}
        for fs in scans:
            for c in fs.classes:
                for m in c.methods:
                    decls.setdefault(f"{c.name}::{m.name}", (m.sig, fs.path, m.line))
        ps = scan_pluginsdk(tree)
        for f in ps.funcs:
            d = decls.get(f.qual)
            self.w.sym("func", f.qual, owner=f.cls, addr=f.addr, sig=d[0] if d else None, sid=sid,
                       path=f.src_file, line=f.src_line, flags={"decl": f"{d[1]}:{d[2]}"} if d else None)
        for g in ps.globals:
            t = re.sub(r"#\s*\w+(?:\s+[\w.\"<>]+)?\s*", "", g.type or "").strip() or None
            if t and g.array_len and "[" not in t:
                t += f"[{g.array_len}]"
            self.w.sym("global", g.name, addr=g.addr, sig=t, sid=sid, path=g.src_file, line=g.src_line)
        for name, size in sorted(sizes.items()):
            pl = next(((fs.path, a.line) for fs in scans for a in fs.asserts if a.kind == "size" and a.type == name),
                      (None, None))
            self.w.sym("struct", name, sig=f"0x{size:X}", sid=sid, path=pl[0], line=pl[1])
        self.w.flush()
        self.stats["plugin-sdk"] = {"files": nfiles, "chunks": nchunks, "funcs": len(ps.funcs), "globals": len(ps.globals),
                                    "sizes": len(sizes), "offsets": len(offsets), "structs": st}
        self.con.execute("UPDATE source SET files=? WHERE id=?", (nfiles, sid))
        self.step("plugin-sdk", t0)

    # -- gta-reversed -----------------------------------------------------------------

    def gtarev(self) -> None:
        tree = self.inp.gtarev
        if tree is None:
            return
        t0 = time.perf_counter()
        from ..re.sources.gtarev import scan_gtarev
        from ..re.sources.hooks import load_hooks

        sid = self.add_source("gta-reversed", tree)
        scan = scan_gtarev(tree)
        ev = scan.consts
        try:
            hooks, _raw = load_hooks(tree)
        except SatkError:
            hooks = []
            self.stats["skipped"]["gta-reversed:hooks.json"] = "docs/hooks.json not found"
        self.step("gta-reversed:scan", t0)
        t1 = time.perf_counter()
        scans: list[FileScan] = []
        nfiles = nchunks = 0
        for path, text in tree.files((GR_PREFIX,), CPP_EXTS):
            nfiles += 1
            fs = self.scan(path, text)
            scans.append(fs)
            nchunks += self.w.chunk(sid, path, text, headings=_headings(fs))
            if "REGISTER_COMMAND_HANDLER" in text:
                defs = {f.name.rsplit("::", 1)[-1]: f.line for f in fs.funcs}
                for m in _REG_HANDLER.finditer(text):
                    fn = m.group(2)
                    dl = defs.get(fn)
                    if dl is None:
                        dm = re.search(rf"^[^\n;{{}}]*\b{re.escape(fn)}\s*\([^;{{}}]*\)\s*\{{", text, re.M)
                        dl = text.count("\n", 0, dm.start()) + 1 if dm else None
                    self.handlers.setdefault(m.group(1), (fn, path, text.count("\n", 0, m.start()) + 1, dl))
        for path, text in tree.files(("docs",), (".md",)):
            nchunks += self.w.chunk(sid, path, text, markdown=True)
        self.step("gta-reversed:files", t1)
        t2 = time.perf_counter()
        # -- functions: hooks.json joined with definitions and declarations
        defs_by_name: dict[str, list[tuple[cxx.FuncDef, str]]] = {}
        for fs in scans:
            for f in fs.funcs:
                defs_by_name.setdefault(f.name, []).append((f, fs.path))
        decls: dict[str, tuple[str, str, int]] = {}
        for fs in scans:
            for c in fs.classes:
                for m in c.methods:
                    decls.setdefault(f"{c.name}::{m.name}", (m.sig, fs.path, m.line))
        used: set[int] = set()
        nf = 0
        for h in hooks:
            base = re.sub(r"-[^:]*$", "", h.name)
            qual = f"{h.cls}::{base}" if h.cls else base
            cands = defs_by_name.get(qual, [])
            pick = next(((f, p) for f, p in cands if f.addr == h.addr and id(f) not in used), None)
            if pick is None:  # an overload without its own address comment
                pick = next(((f, p) for f, p in cands if f.addr is None and id(f) not in used), None)
            install = f"{GR_PREFIX}/{h.src_file}:{h.src_line}" if h.src_file else None
            flags: dict = {"reversed": h.reversed}
            if h.locked:
                flags["locked"] = True
            d = decls.get(qual)
            if pick is not None:
                f, p = pick
                used.add(id(f))
                flags["install"] = install
                if d:
                    flags["decl"] = f"{d[1]}:{d[2]}"
                self.w.sym("func", h.qual, owner=h.cls, addr=h.addr, sig=f.sig, doc=f.doc, sid=sid, path=p,
                           line=f.line, flags=flags)
            elif d:
                flags["install"] = install
                self.w.sym("func", h.qual, owner=h.cls, addr=h.addr, sig=d[0], sid=sid, path=d[1], line=d[2],
                           flags=flags)
            else:
                p, ln = (f"{GR_PREFIX}/{h.src_file}", h.src_line) if h.src_file else (None, None)
                self.w.sym("func", h.qual, owner=h.cls, addr=h.addr, sid=sid, path=p, line=ln, flags=flags)
            nf += 1
            self.gr_funcs.setdefault(h.qual, []).append(h.addr)
            if qual != h.qual:
                self.gr_funcs.setdefault(qual, []).append(h.addr)
        hook_addrs = {h.addr for h in hooks}
        extra = 0
        for name, lst in sorted(defs_by_name.items()):
            for f, p in lst:
                if id(f) in used:
                    continue
                owner = name.rsplit("::", 1)[0] if "::" in name else None
                flags = {"hook": False} if f.addr is not None and f.addr not in hook_addrs else None
                self.w.sym("func", name, owner=owner, addr=f.addr, sig=f.sig, doc=f.doc, sid=sid, path=p, line=f.line,
                           flags=flags)
                extra += 1
                if f.addr is not None:
                    self.gr_funcs.setdefault(name, []).append(f.addr)
        # -- globals, vtables, limits, sizes
        for g in scan.globals:
            t = g.type
            self.w.sym("global", g.name, owner=g.name.rsplit("::", 1)[0] if "::" in g.name else None, addr=g.addr,
                       sig=t, sid=sid, path=f"{GR_PREFIX}/{g.src_file}" if g.src_file else None, line=g.src_line,
                       flags={"bytes": g.byte_size} if g.byte_size else None)
            self.gr_globals.setdefault(g.name, []).append(g.addr)
        for v in scan.vtables:
            self.w.sym("vtable", v.cls, addr=v.addr, sig=f"{v.slots} slots", sid=sid,
                       path=f"{GR_PREFIX}/{v.src_file}" if v.src_file else None, line=v.src_line)
        for lim in scan.limits:
            loc = (lim.source or "").split(":", 1)[1] if (lim.source or "").startswith("gta-reversed:") else None
            p, ln = None, None
            if loc and ":" in loc:
                p, _, l2 = loc.rpartition(":")
                p, ln = f"{GR_PREFIX}/{p}", int(l2) if l2.isdigit() else None
            self.w.sym("limit", lim.name, addr=lim.global_addr, sig=str(lim.vanilla), sid=sid, path=p, line=ln,
                       flags={"kind": lim.kind})
            self.limits.setdefault(lim.name, []).append(lim.vanilla)
        sizes = {s.name: s.size for s in scan.sizes}
        for s in scan.sizes:
            self.w.sym("struct", s.name, sig=f"0x{s.size:X}", sid=sid,
                       path=f"{GR_PREFIX}/{s.src_file}" if s.src_file else None, line=s.src_line)
        # -- constants and enum members
        nconst = 0
        consts: dict[str, object] = {}
        for fs in scans:
            for c in fs.consts:
                v = _const_value(ev, c)
                if v is None and (not c.expr or len(c.expr) > 80):
                    continue
                self.w.sym(c.kind, c.name, owner=c.owner, sig=_dec(v) if v is not None else c.expr[:80], sid=sid,
                           path=fs.path, line=c.line)
                nconst += 1
                if v is not None:
                    consts.setdefault(c.name, v)
        self.consts["gta-reversed"] = consts
        self.w.flush()
        self.step("gta-reversed:symbols", t2)
        t3 = time.perf_counter()
        enum_sizes = dict(ev.enum_sizes)
        qsizes = _eval_sizes([(a.type, a.expr) for fs in scans for a in fs.asserts if a.kind == "size"], ev)
        for name, v in sizes.items():           # what satk.re evaluated but we could not
            qsizes.setdefault(name, v)
        own: dict[tuple[str, str], tuple[int, str]] = {}
        for fs in scans:
            for a in fs.asserts:
                if a.kind == "offset" and a.member and "." not in a.member:
                    v = ev.eval(a.expr)
                    if isinstance(v, int):
                        own.setdefault((a.type.rsplit("::", 1)[-1], a.member), (v, "assert"))
        st = self.write_structs("gta-reversed", sid, scans, ev, qsizes, enum_sizes, own, xoffsets=self.ps_offsets)
        self.step("gta-reversed:structs", t3)
        self.stats["gta-reversed"] = {"files": nfiles, "chunks": nchunks, "hooks": len(hooks), "funcs": nf + extra,
                                      "funcs_not_in_hooks": extra, "globals": len(scan.globals),
                                      "vtables": len(scan.vtables), "limits": len(scan.limits), "sizes": len(sizes),
                                      "consts": nconst, "handlers": len(self.handlers), "structs": st}
        self.con.execute("UPDATE source SET files=? WHERE id=?", (nfiles, sid))

    # -- MTA --------------------------------------------------------------------------

    def mta(self, key: str, tree: SourceTree | None, only: list[str] | None = None) -> None:
        if tree is None:
            return
        t0 = time.perf_counter()
        sid = self.add_source(key, tree)
        paths = only if only is not None else tree.list(MTA_PREFIXES, CPP_EXTS)
        scans: list[FileScan] = []
        nfiles = nchunks = ndef = nfn = 0
        for path, text in tree.read_many(paths):
            nfiles += 1
            fs = self.scan(path, text, defines=True)
            scans.append(fs)
            nchunks += self.w.chunk(sid, path, text, headings=_headings(fs), max_lines=60)
            for d in fs.defines:
                kind = "hookpos" if d.name.startswith(("HOOKPOS", "RETURN", "HOOKSIZE", "HOOKCHECK")) else "define"
                self.w.sym(kind, d.name, owner=path.rsplit("/", 1)[-1], addr=d.addr, doc=d.comment, sid=sid,
                           path=path, line=d.line)
                ndef += 1
            for f in fs.funcs:
                self.w.sym("func", f.name, owner=f.name.rsplit("::", 1)[0], sig=f.sig, doc=f.doc, sid=sid, path=path,
                           line=f.line)
                nfn += 1
        sizes: dict[str, int] = {}
        enum_sizes: dict[str, int] = {}
        ev = _ev_from_scans(scans, sizes, enum_sizes)
        consts: dict[str, object] = {}
        nconst = 0
        for fs in scans:
            for c in fs.consts:
                if c.kind != "const" or not re.match(r"^(?:MAX|NUM|TOTAL|MIN|DEFAULT)_[A-Z0-9_]+$", c.name):
                    continue
                v = ev.value(c.name)
                if v is None:
                    continue
                self.w.sym("const", c.name, owner=c.owner, sig=_dec(v), sid=sid, path=fs.path, line=c.line)
                consts.setdefault(c.name, v)
                nconst += 1
        for fs in scans:
            for a in fs.asserts:
                if a.kind == "size" and a.type in sizes:
                    self.w.sym("struct", a.type, sig=f"0x{sizes[a.type]:X}", sid=sid, path=fs.path, line=a.line)
        offsets: dict[tuple[str, str], tuple[int, str]] = {}
        for fs in scans:
            for a in fs.asserts:
                if a.kind == "offset" and a.member:
                    v = ev.eval(a.expr)
                    if isinstance(v, int):
                        offsets.setdefault((a.type.rsplit("::", 1)[-1], a.member), (v, "assert"))
        st = self.write_structs(key, sid, scans, ev, sizes, enum_sizes, offsets, prefixes=MTA_LAYOUT_PREFIXES)
        self.consts[key] = consts
        self.w.flush()
        self.stats[key] = {"files": nfiles, "chunks": nchunks, "defines": ndef, "funcs": nfn, "consts": nconst,
                           "sizes": len(sizes), "structs": st}
        self.con.execute("UPDATE source SET files=? WHERE id=?", (nfiles, sid))
        self.step(key, t0)

    # -- cleo-ai ----------------------------------------------------------------------

    def cleo(self) -> None:
        tree = self.inp.cleo
        if tree is None:
            return
        t0 = time.perf_counter()
        from .opcodes import DETAIL_PREFIXES, INDEX_PATH, parse_reference

        sid = self.add_source("cleo-ai", tree)
        paths = [p for p in tree.list(("reference",), (".md",))
                 if p == INDEX_PATH or p.startswith(DETAIL_PREFIXES) or p in CLEO_DOCS]
        files = dict(tree.read_many(paths))
        ops = parse_reference(files)
        rows, fts = [], []
        gr_sid = self.sids.get("gta-reversed")
        nh = 0
        for i, o in enumerate(ops, 1):
            h = self.handlers.get(o.name.upper())
            hfid = self.w.file(gr_sid, h[1]) if h and gr_sid else None
            if h:
                nh += 1
            rows.append((i, o.op, o.name, o.ext, o.cls, o.member, o.nparams, o.flags, o.descr,
                         json.dumps(o.input, ensure_ascii=False) if o.input else None,
                         json.dumps(o.output, ensure_ascii=False) if o.output else None,
                         o.details[:4000] or None, self.w.file(sid, o.path), o.line,
                         h[0] if h else None, hfid, (h[3] or h[2]) if h else None))
            fts.append((i, o.name, camel_words(o.name.replace("_", " ")) + " " + o.op.lower(), f"{o.cls} {o.member} {o.ext}",
                        f"{o.descr} {o.details[:2000]}"))
        self.con.executemany("INSERT INTO opcode(id,op,name,ext,class,member,nparams,flags,descr,input,output,details,"
                             "file_id,line,handler,handler_file_id,handler_line) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                             rows)
        self.con.executemany("INSERT INTO opcode_fts(rowid,name,words,class,descr) VALUES(?,?,?,?,?)", fts)
        nchunks = 0
        for p in CLEO_DOCS:
            if p in files:
                nchunks += self.w.chunk(sid, p, files[p], markdown=True)
        self.w.flush()
        self.stats["cleo-ai"] = {"opcodes": len(ops), "with_handler": nh, "chunks": nchunks}
        self.con.execute("UPDATE source SET files=? WHERE id=?", (len(files), sid))
        self.step("cleo-ai", t0)

    # -- research ---------------------------------------------------------------------

    def research(self) -> None:
        tree = self.inp.research
        if tree is None:
            return
        t0 = time.perf_counter()
        sid = self.add_source("research", tree)
        n = nchunks = 0
        for path, text in tree.files((), (".md",)):
            n += 1
            nchunks += self.w.chunk(sid, path, text, markdown=True, max_lines=50)
        self.w.flush()
        self.stats["research"] = {"files": n, "chunks": nchunks}
        self.con.execute("UPDATE source SET files=? WHERE id=?", (n, sid))
        self.step("research", t0)

    # -- facts ------------------------------------------------------------------------

    def facts(self) -> None:
        t0 = time.perf_counter()
        from .facts import CONFIDENCE, FACTS, topic

        sid = self.add_source("facts", None)
        img = None
        exe_note = None
        if self.inp.exe is not None:
            try:
                from ..core.detect import identify_exe
                from ..re.pe import PeImage

                info = identify_exe(self.inp.exe)
                if info.get("layout") == "1.0us":
                    img = PeImage.open(self.inp.exe)
                else:
                    exe_note = f"exe is {info.get('variant')}, not the 1.0 US layout"
            except (OSError, ValueError, SatkError) as e:
                exe_note = f"exe unreadable: {e}"
        else:
            exe_note = "no gta_sa.exe"
        counts = {"verified": 0, "mismatch": 0, "unchecked": 0}
        rows, fts = [], []
        for f in FACTS:
            results = [self._check(chk, img, exe_note) for chk in f.get("checks", [])]
            if any(r[3] is False for r in results):
                status = "mismatch"
            elif results and all(r[3] is True for r in results):
                status = "verified"
            else:
                status = "unchecked"
            counts[status] += 1
            addrs = [f"0x{a:X}" for a in f.get("addrs", [])]
            rows.append((f["key"], topic(f["key"]), f["title"], f["value"], json.dumps(addrs) if addrs else None,
                         json.dumps(f.get("refs", []), ensure_ascii=False), json.dumps(f.get("sources", []), ensure_ascii=False),
                         CONFIDENCE.get(f["conf"], f["conf"]), status,
                         json.dumps(results, ensure_ascii=False, separators=(",", ":")) if results else None, f.get("note")))
            fts.append((f["key"], f["title"], f["value"] + " " + " ".join(addrs), f.get("note") or ""))
        self.con.executemany("INSERT INTO fact(key,topic,title,value,addrs,refs,sources,confidence,status,checks,note) "
                             "VALUES(?,?,?,?,?,?,?,?,?,?,?)", rows)
        self.con.executemany("INSERT INTO fact_fts(key,title,value,note) VALUES(?,?,?,?)", fts)
        self.stats["facts"] = {"facts": len(FACTS), **counts, **({"exe": exe_note} if exe_note else {})}
        self.con.execute("UPDATE source SET files=? WHERE id=?", (0, sid))
        self.step("facts", t0)

    def _check(self, chk: tuple, img, exe_note: str | None) -> list:
        """``[text, expected, got, ok]``; ``ok`` is ``None`` when the check could not run."""
        kind, name, want = chk[0], chk[1], chk[2]
        extra = chk[3] if len(chk) > 3 else None
        label = f"{kind} {name if isinstance(name, str) else hex(name)}" + (f" @{extra}" if isinstance(extra, str) else "")
        addr_like = kind in ("func", "global", "section", "size") or (isinstance(want, int) and 0x400000 <= want < 0x1600000)
        fmt = _hexs if addr_like else _dec
        exp = fmt(want)
        try:
            if kind in ("func", "global", "limit"):
                if "gta-reversed" not in self.sids:
                    return [label, exp, None, None]
                table = {"func": self.gr_funcs, "global": self.gr_globals, "limit": self.limits}[kind]
                got = _lookup(table, name)
                return [label, exp, fmt(got[0]) if got else None, want in got if got else False]
            if kind == "size":
                src = extra or "gta-reversed"
                if src not in self.struct_sizes:
                    return [label, exp, None, None]
                got = self.struct_sizes[src].get(name)
                return [label, exp, fmt(got) if got is not None else None, got == want]
            if kind == "const":
                src = extra or "gta-reversed"
                if src not in self.consts:
                    return [label, exp, None, None]
                got = self.consts[src].get(name)
                return [label, exp, fmt(got) if got is not None else None, got == want]
            if kind in ("u8", "u16", "u32", "f32", "section"):
                if img is None:
                    return [label, exp, exe_note, None]
                import struct as _st

                if kind == "section":
                    s = img.section(name, extra if isinstance(extra, int) else 0)
                    got = s.va if s is not None else None
                    return [label, exp, fmt(got) if got is not None else None, got == want]
                pack, n = {"u8": ("<B", 1), "u16": ("<H", 2), "u32": ("<I", 4), "f32": ("<f", 4)}[kind]
                b = img.read(name, n)
                if len(b) != n:
                    return [label, exp, "unbacked", False]
                got = _st.unpack(pack, b)[0]
                ok = abs(got - want) < 1e-5 if kind == "f32" else got == want
                return [label, exp, fmt(round(got, 6) if kind == "f32" else got), ok]
        except Exception as e:  # noqa: BLE001 - a broken check must not break the build
            return [label, exp, f"error: {e}", None]
        return [label, exp, "unknown check", None]


# --------------------------------------------------------------------------- build


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _replace(tmp: Path, out: Path) -> None:
    last: OSError | None = None
    for k in range(20):
        try:
            os.replace(tmp, out)
            return
        except PermissionError as e:   # a reader has the old file open (Windows)
            last = e
            time.sleep(0.25 * (k + 1))
    raise SatkError("BUSY", f"cannot replace {out}: {last}", hint="close programs reading the KB and retry")


def build_kb(out: str | Path, inp: Inputs, *, progress: Callable[[str], None] | None = None) -> dict:
    """Build the KB at ``out``; returns ``stats`` (also stored in ``meta.stats``)."""
    from .. import __version__

    t0 = time.perf_counter()
    out = ensure_writable(out)
    if inp.gtarev is None and inp.pluginsdk is None and inp.upstream is None and inp.cleo is None:
        raise SatkError("NOT_FOUND", "no knowledge sources found (gta-reversed, plugin-sdk-sa, mtasa-neon, cleo-ai)",
                        hint="clone them under paths.src (satk config show)", data={"skipped": inp.skipped})
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f"{out.name}.tmp-{os.getpid()}")
    if tmp.exists():
        tmp.unlink()
    con = sqlite3.connect(tmp)
    try:
        con.execute("PRAGMA journal_mode=OFF")
        con.execute("PRAGMA synchronous=OFF")
        con.executescript(schema_sql())
        b = _Build(con, inp, progress)
        b.pluginsdk()            # first: its VALIDATE_OFFSETs anchor gta-reversed layouts
        b.gtarev()
        b.mta("mta-upstream", inp.upstream)
        if inp.neon is not None:
            only = _changed(inp.upstream, inp.neon, MTA_PREFIXES, CPP_EXTS)
            b.mta("mta-neon", inp.neon, only=only)
            b.stats["mta-neon"]["changed_files"] = len(only)
        b.cleo()
        b.research()
        b.facts()
        b.w.flush()
        t = time.perf_counter()
        con.execute("INSERT INTO chunk(chunk) VALUES('optimize')")
        con.execute("INSERT INTO sym_fts(sym_fts) VALUES('optimize')")
        b.step("fts-optimize", t)
        counts = {tbl: con.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
                  for tbl in ("sym", "struct", "field", "opcode", "fact", "chunk", "addr_ref", "file")}
        stats = {**b.stats, "counts": counts, "timings": b.timings, "seconds": round(time.perf_counter() - t0, 1)}
        meta = {"schema_version": str(SCHEMA_VERSION), "built_at": _now(), "satk_version": __version__,
                "stats": json.dumps(stats, ensure_ascii=False, sort_keys=True)}
        if inp.exe is not None:
            meta["exe"] = inp.exe.name
        con.executemany("INSERT INTO meta(key,value) VALUES(?,?)", sorted(meta.items()))
        con.commit()
    except BaseException:
        con.close()
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    con.close()
    stats["bytes"] = tmp.stat().st_size
    _replace(tmp, out)
    stats["seconds"] = round(time.perf_counter() - t0, 1)
    return stats
