"""Build ``work/re/symdb.sqlite`` from all sources (SPEC §4.9; owner WP-09).

``build_db(out, Sources(...))`` takes explicit inputs; tests pass synthetic trees.
:func:`default_sources` wires it to the configured workspace (read-only git access to ``src\\``,
``engine\\mtasa`` as trunk when present). The DB is written to a temporary file and moved into
place with ``os.replace`` (atomic; readers never see a half-built DB).
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from ..core.errors import SatkError
from ..core.paths import ensure_writable
from .gitsrc import DirTree, GitTree, SourceTree
from .pe import PeImage
from .resolve import AddrMap, SectionInfo
from .sources import FuncSym, GlobalSym
from .sources.exe import classify_thunk, read_vtable, scan_exe
from .sources.ghidra import load_functions
from .sources.gtarev import scan_gtarev
from .sources.hooks import load_hooks
from .sources.limits import LIMITS_PATH, load_limits_toml, merge_limits
from .sources.mta import scan_mta
from .sources.pluginsdk import scan_pluginsdk

__all__ = ["SCHEMA_VERSION", "Sources", "build_db", "default_sources", "schema_sql"]

SCHEMA_VERSION = 1
ORIGIN_RANK = {"hooks_json": 0, "plugin_sdk": 1, "vtable": 2, "call_target": 3, "data_ptr": 4, "padding": 5}


def schema_sql() -> str:
    return (Path(__file__).with_name("schema.sql")).read_text(encoding="utf-8")


@dataclass
class Sources:
    """Inputs of one build. ``None`` skips a source (recorded in ``stats.skipped``)."""

    exe: Path
    gtarev: SourceTree
    pluginsdk: SourceTree | None = None
    upstream: SourceTree | None = None
    trunk: SourceTree | None = None
    neon: SourceTree | None = None
    limits: SourceTree | None = None                              # tree with docs/limits.toml (our fork)
    repo_names: dict[str, str] = field(default_factory=dict)   # kind -> 'src/gta-reversed' etc.
    skipped: dict[str, str] = field(default_factory=dict)
    ghidra_functions: Path | None = None  # optional exported JSONL, no Ghidra dependency


def _workspace_rel(p: Path, workspace: Path) -> str:
    try:
        return Path(os.path.relpath(p, workspace)).as_posix()
    except ValueError:
        return p.as_posix()


def default_sources(*, exe: str | Path | None = None, trunk: bool = True,
                    ghidra_functions: str | Path | None = None) -> Sources:
    """Sources from the configuration (``paths.game``/``paths.src``/``paths.engine``)."""
    from ..core.paths import cfg

    c = cfg()
    ws = c.paths.workspace
    src = c.paths.src
    exe_p = Path(exe) if exe else c.paths.game / "gta_sa.exe"
    gr = c.paths.get("game_root")
    if not exe and not exe_p.is_file() and gr is not None and (gr / "gta_sa.exe").is_file():
        exe_p = gr / "gta_sa.exe"  # no clean copy: the user's game executable
    if not exe_p.is_file():
        raise SatkError("NOT_FOUND", f"gta_sa.exe not found: {exe_p}", hint="satk game verify (or --exe <gta_sa.exe>)")
    names: dict[str, str] = {"exe": _workspace_rel(exe_p, ws)}
    skipped: dict[str, str] = {}

    def git(repo: Path, ref: str, kind: str) -> GitTree | None:
        if not (repo / ".git").exists():
            skipped[kind] = f"not a git repository: {repo.as_posix()}"
            return None
        try:
            t = GitTree(repo, ref)
        except SatkError as e:
            skipped[kind] = e.msg
            return None
        names[kind] = _workspace_rel(repo, ws)
        return t

    gtarev_repo = src / "gta-reversed"
    if (gtarev_repo / ".git").exists():
        gtarev: SourceTree = GitTree(gtarev_repo, "origin/master")
        names["gta-reversed"] = _workspace_rel(gtarev_repo, ws)
    elif gtarev_repo.is_dir():
        gtarev = DirTree(gtarev_repo)
        names["gta-reversed"] = _workspace_rel(gtarev_repo, ws)
    else:
        raise SatkError("NOT_FOUND", f"gta-reversed clone not found: {gtarev_repo}")
    neon_repo = src / "mtasa-neon"
    srcs = Sources(
        exe=exe_p,
        gtarev=gtarev,
        pluginsdk=git(src / "plugin-sdk-sa", "HEAD", "plugin-sdk"),
        upstream=git(neon_repo, "refs/remotes/upstream/master", "mta-upstream"),
        neon=git(neon_repo, "HEAD", "neon"),
        repo_names=names,
        skipped=skipped,
        ghidra_functions=Path(ghidra_functions) if ghidra_functions else None,
    )
    eng = c.paths.get("engine")
    if trunk and eng is not None and (eng / ".git").exists():
        t = git(eng, "HEAD", "mta-trunk")
        srcs.limits = t
        if t is not None and srcs.upstream is not None and t.rev == srcs.upstream.rev:
            skipped["mta-trunk"] = f"trunk HEAD == upstream ({t.rev[:9]}): patches not scanned twice"
            t = None
        srcs.trunk = t
    elif trunk:
        skipped["mta-trunk"] = "engine/mtasa not set up yet (satk engine setup)"
    return srcs


# --------------------------------------------------------------------------- build


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_db(out: str | Path, srcs: Sources, *, progress: Callable[[str], None] | None = None) -> dict:
    """Build the symbol DB at ``out``; returns ``stats`` (also stored in ``meta.stats``)."""
    from .. import __version__

    t0 = time.perf_counter()
    timings: dict[str, float] = {}

    def step(name: str, t: float) -> None:
        timings[name] = round(time.perf_counter() - t, 2)
        if progress:
            progress(name)

    out = ensure_writable(out)   # never inside protected roots (src, game copies)
    # -- inputs ----------------------------------------------------------------------------
    t = time.perf_counter()
    img = PeImage.open(srcs.exe)
    ex = scan_exe(img)
    step("exe", t)
    t = time.perf_counter()
    gh = load_functions(srcs.ghidra_functions, img) if srcs.ghidra_functions is not None else None
    if gh:
        step("ghidra", t)
    t = time.perf_counter()
    hooks, hooks_raw = load_hooks(srcs.gtarev)
    gr = scan_gtarev(srcs.gtarev)
    step("gta_reversed", t)
    t = time.perf_counter()
    ps = scan_pluginsdk(srcs.pluginsdk) if srcs.pluginsdk else None
    step("plugin_sdk", t)
    mta: dict[str, tuple[SourceTree, object]] = {}
    for origin, tree in (("upstream", srcs.upstream), ("trunk", srcs.trunk), ("neon", srcs.neon)):
        if tree is not None:
            t = time.perf_counter()
            mta[origin] = (tree, scan_mta(tree, manifests=(origin == "neon")))
            step(f"mta_{origin}", t)

    t = time.perf_counter()
    text = ex.text
    code_lo, code_hi = text.va, text.va + max(text.vsize, text.raw_size)
    rwc = img.section("_rwcseg")

    def in_code(a: int) -> bool:
        return code_lo <= a < code_hi or (rwc is not None and rwc.contains(a))

    # -- named functions -------------------------------------------------------------------
    funcs: dict[int, dict] = {}
    aliases: set[tuple[int, str, str]] = set()
    for f in hooks:
        funcs[f.addr] = _frow(f)
    ps_new = ps_alias = ps_outside = 0
    ps_new_how: dict[str, int] = {}
    ps_proto = 0   # names the report-10 prototype could see: addrof/META + free-function wrappers
    if ps:
        for f in ps.funcs:
            if not in_code(f.addr):
                ps_outside += 1
                continue
            cur = funcs.get(f.addr)
            if cur is None:
                funcs[f.addr] = _frow(f)
                ps_new += 1
                key = f"{f.how or '?'}{'' if f.cls is None or f.how != 'wrapper' else '_method'}"
                ps_new_how[key] = ps_new_how.get(key, 0) + 1
                if f.how in ("addrof", "meta") or (f.how == "wrapper" and f.cls is None):
                    ps_proto += 1
            elif cur["qual"] != f.qual and not cur["qual"].endswith("::" + f.qual):
                aliases.add((f.addr, f.qual, "plugin_sdk"))   # a different spelling, not just a shorter one
                ps_alias += 1
    gr_new = gr_interior = 0
    for f in gr.funcs:
        if f.addr not in funcs and in_code(f.addr):
            # Source-only compiler entries are 16-aligned; unaligned installs are call-site
            # hooks. EXE/Ghidra starts are handled separately and may be unaligned.
            if f.addr % 16 or (gh and f.addr not in gh.ends and gh.containing(f.addr) is not None):
                gr_interior += 1
                continue
            funcs[f.addr] = _frow(f)
            gr_new += 1
    named = set(funcs)

    # -- vtables -----------------------------------------------------------------------------
    vt_rows = []
    slot_rows = []
    vt_starts: set[int] = set()
    for v in gr.vtables:
        slots = read_vtable(img, v.addr, v.slots)
        vt_rows.append((v.addr, v.cls, v.slots, v.src_file, v.src_line))
        for i, target in enumerate(slots):
            slot_rows.append((v.addr, i, target))
            if in_code(target):
                vt_starts.add(target)

    # -- unnamed starts --------------------------------------------------------------------
    def add_unnamed(addrs: set[int], origin: str) -> int:
        n = 0
        for a in addrs:
            if a not in funcs and in_code(a):
                funcs[a] = {"addr": a, "origin": origin}
                n += 1
        return n

    n_vt = add_unnamed(vt_starts, "vtable")
    n_e8 = add_unnamed(ex.e8_starts, "call_target")
    n_dp = add_unnamed(ex.data_ptr_starts, "data_ptr")
    n_pad = add_unnamed(ex.padding_starts, "padding")
    n_thunk = add_unnamed(set(ex.hoodlum_jumps), "thunk_scan")
    gh_new = 0
    if gh:
        for a in sorted(set(gh.ends) | {owner for _lo, _hi, owner, _body in gh.ranges}):
            if a not in funcs:
                funcs[a] = {"addr": a, "origin": "ghidra"}
                gh_new += 1

    # -- thunks ------------------------------------------------------------------------------
    thunks: list[tuple[int, int, str]] = []
    for a in sorted(funcs):
        th = classify_thunk(img, a)
        if th is None:
            continue
        kind, target = th
        thunks.append((a, target, kind))
        if kind == "hoodlum":
            funcs[a]["hoodlum_body"] = target

    # -- ends ----------------------------------------------------------------------------------
    sections = [SectionInfo(s.name, s.va, s.va + max(s.vsize, s.raw_size), s.flags) for s in img.sections]
    amap = AddrMap(sections, {a: (a in named) for a in funcs}, thunks,
                   tentative={a for a, row in funcs.items() if row["origin"] in ("gta_reversed", "thunk_scan")},
                   ghidra=set(gh.ends) if gh else None, ends=gh.ends if gh else None,
                   exact_ranges=gh.ranges if gh else None)
    for a, row in funcs.items():
        row["end_addr"] = amap.end_of(a)
        row["bounds"] = "ghidra" if gh and a in gh.ends else "next_start"
    step("merge", t)

    # -- patches ---------------------------------------------------------------------------
    t = time.perf_counter()
    patch_rows = []
    patch_stats: dict[str, dict] = {}
    for origin, (tree, scan) in mta.items():
        st: dict[str, int] = {}
        for p in scan.patches:  # type: ignore[attr-defined]
            loc = amap.locate(p.addr)
            fa = loc.start if loc.kind == "code" else None
            fo = loc.off if fa is not None else None
            patch_rows.append((origin, p.addr, p.len, p.kind, p.symbol, p.src_file, p.src_line, fa, fo, p.note))
            st[p.kind] = st.get(p.kind, 0) + 1
        patch_stats[origin] = {"total": len(scan.patches), "by_kind": dict(sorted(st.items())),  # type: ignore
                               "scan": scan.stats}  # type: ignore[attr-defined]
    step("patches", t)

    # -- globals --------------------------------------------------------------------------
    globs: dict[int, GlobalSym] = {g.addr: g for g in gr.globals}
    ps_glob_new = 0
    if ps:
        for g in ps.globals:
            if g.addr not in globs:
                if g.byte_size is None and g.type:
                    sz = gr.consts.type_size(g.type)
                    g.byte_size = sz
                    if g.elem_type:
                        g.elem_size = gr.consts.type_size(g.elem_type)
                globs[g.addr] = g
                ps_glob_new += 1

    # -- limits (gta-reversed + our fork's docs/limits.toml) ---------------------------------
    toml = load_limits_toml(srcs.limits)
    lim_src = f"{srcs.repo_names.get('mta-trunk', 'engine/mtasa')}:{LIMITS_PATH}"
    limit_rows, limit_merge = merge_limits(gr.limits, toml, lim_src)

    # -- write -------------------------------------------------------------------------------
    t = time.perf_counter()
    revs: list[tuple[str, str, str]] = [("exe", srcs.repo_names.get("exe", srcs.exe.name), img.sha256),
                                        ("gta-reversed", srcs.repo_names.get("gta-reversed", srcs.gtarev.label),
                                         srcs.gtarev.rev)]
    if srcs.pluginsdk:
        revs.append(("plugin-sdk", srcs.repo_names.get("plugin-sdk", srcs.pluginsdk.label), srcs.pluginsdk.rev))
    if gh:
        revs.append(("ghidra", srcs.ghidra_functions.as_posix(), gh.sha256))
    kind_of = {"upstream": "mta-upstream", "trunk": "mta-trunk", "neon": "neon"}
    for origin, (tree, _scan) in mta.items():
        revs.append((kind_of[origin], srcs.repo_names.get(kind_of[origin], tree.label), tree.rev))
    if srcs.limits is not None and "trunk" not in mta and toml is not None:
        revs.append(("mta-trunk", srcs.repo_names.get("mta-trunk", srcs.limits.label), srcs.limits.rev))

    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f".{out.name}.{os.getpid()}.tmp")
    if tmp.exists():
        tmp.unlink()
    con = sqlite3.connect(tmp)
    try:
        con.executescript(schema_sql())
        scanned = _now()
        rev_ids: dict[str, int] = {}
        for i, (kind, repo, rev) in enumerate(revs, 1):
            con.execute("INSERT INTO source_rev(id,kind,repo,rev,scanned_at) VALUES(?,?,?,?,?)",
                        (i, kind, repo, rev, scanned))
            rev_ids[kind] = i
        con.executemany("INSERT INTO section VALUES(?,?,?,?,?)",
                        [(s.name, s.va, s.va + max(s.vsize, s.raw_size), s.raw_off, s.flags) for s in img.sections])
        frows = []
        origin_sources = {"hooks_json": "gta-reversed", "gta_reversed": "gta-reversed",
                          "plugin_sdk": "plugin-sdk", "ghidra": "ghidra"}
        for a in sorted(funcs):
            r = funcs[a]
            origin = r["origin"]
            rid = rev_ids[origin_sources.get(origin, "exe")]
            frows.append((a, r.get("end_addr"), r["bounds"], r.get("name"), r.get("cls"), r.get("qual"), origin, rid,
                          r.get("src_file"), r.get("src_line"), _b(r.get("reversed")), r.get("hook_state"),
                          _b(r.get("locked")), r.get("category"), r.get("hoodlum_body")))
        con.executemany("INSERT INTO func VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", frows)
        if gh:
            con.executemany("INSERT INTO func_range VALUES(?,?,?,?)", gh.ranges)
        con.executemany("INSERT OR IGNORE INTO func_alias VALUES(?,?,?)", sorted(aliases))
        con.executemany("INSERT INTO global VALUES(?,?,?,?,?,?,?,?,?,?)",
                        [(g.addr, g.name, g.type, g.elem_type, g.array_len, g.elem_size, g.byte_size, g.origin,
                          g.src_file, g.src_line) for g in (globs[a] for a in sorted(globs))])
        con.executemany("INSERT INTO vtable VALUES(?,?,?,?,?)", vt_rows)
        con.executemany("INSERT INTO vtable_slot VALUES(?,?,?)", slot_rows)
        con.executemany("INSERT OR IGNORE INTO struct_size VALUES(?,?,?,?)",
                        [(s.name, s.size, s.src_file, s.src_line) for s in gr.sizes])
        con.executemany("INSERT INTO thunk VALUES(?,?,?)", thunks)
        con.executemany(
            "INSERT INTO patch(rev_id,origin,addr,len,kind,symbol,src_file,src_line,func_addr,func_off,note) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            [(rev_ids[kind_of[o]], o, a, ln, k, sym, sf, sl, fa, fo, note)
             for (o, a, ln, k, sym, sf, sl, fa, fo, note) in patch_rows])
        if ps:
            con.executemany("INSERT OR IGNORE INTO callsite VALUES(?,?,?,?,?)",
                            [(c.site, c.callee, c.caller, c.kind, c.origin) for c in ps.calls])
        con.executemany("INSERT OR IGNORE INTO limit_def(name,kind,vanilla,global_addr,trunk,neon,source) "
                        "VALUES(?,?,?,?,?,?,?)", limit_rows)
        fts = [(f"fn:0x{a:x}", "fn", r["qual"]) for a, r in sorted(funcs.items()) if r.get("qual")]
        fts += [(f"fn:0x{a:x}", "fn", q) for a, q, _o in sorted(aliases)]
        fts += [(f"g:0x{g.addr:x}", "g", g.name) for g in globs.values()]
        fts += [(f"vt:0x{v[0]:x}", "vt", v[1]) for v in vt_rows]
        fts += [(None, "struct", s.name) for s in gr.sizes]
        syms = sorted({(o, sym) for (o, _a, _l, _k, sym, *_r) in patch_rows if sym})
        fts += [(f"patch:{o}/{sym.lower()}", "patch", sym) for o, sym in syms]
        con.executemany("INSERT INTO sym_fts(sid,kind,qual) VALUES(?,?,?)", fts)

        hook_thunks = {"hoodlum": 0, "seh_push_jmp": 0, "other_jmp": 0}
        all_thunks = dict(hook_thunks)
        hook_addrs = {f.addr for f in hooks}
        for a, _t, k in thunks:
            all_thunks[k] += 1
            if a in hook_addrs:
                hook_thunks[k] += 1
        n_named = len(named)
        stats = {
            "hooks_json": hooks_raw,
            "reversed": sum(1 for f in hooks if f.reversed),
            "named_starts": n_named,
            "named_starts_by_origin": {"hooks_json": len(hooks), "plugin_sdk": ps_new, "gta_reversed": gr_new},
            "named_starts_plugin_sdk_by_method": dict(sorted(ps_new_how.items())),
            "named_starts_proto_method": len(hooks) + ps_proto,
            "plugin_sdk_aliases": ps_alias,
            "plugin_sdk_outside_code": ps_outside,
            "starts_total": len(funcs),
            "starts_by_origin": {"vtable": n_vt, "call_target": n_e8, "data_ptr": n_dp, "padding": n_pad},
            "thunk_scan_starts": n_thunk,
            "source_hook_interior_sites": gr_interior,
            "ghidra": {"functions": len(gh.ends), "ranges": len(gh.ranges), "new_starts": gh_new} if gh else None,
            "e8_targets": ex.stats["e8_targets"],
            "e8_starts": ex.stats["e8_starts"],
            "vtable": len(vt_rows),
            "vtable_slots": len(slot_rows),
            "struct_size": len(gr.sizes),
            "global": len(globs),
            "global_by_origin": {"gta_reversed": len(gr.globals), "plugin_sdk": ps_glob_new},
            "global_arrays": sum(1 for g in globs.values() if g.array_len),
            "limit_def": {k: sum(1 for x in limit_rows if x[1] == k)
                          for k in ("array", "pool", "store", "id_range", "streaming", "world")},
            "limit_def_gta_reversed": {k: sum(1 for x in gr.limits if x.kind == k)
                                       for k in ("array", "pool", "store", "id_range")},
            "limits_toml": (limit_merge | {"rev": srcs.limits.rev[:12]}) if toml is not None and srcs.limits else None,
            "thunk": all_thunks,
            "thunk_hooks_json": hook_thunks,
            "callsite": len(ps.calls) if ps else 0,
            "patch": {o: v["by_kind"] | {"total": v["total"]} for o, v in patch_stats.items()},
            "patch_scan": {o: v["scan"] for o, v in patch_stats.items()},
            # The SPEC measured MemPut call sites, including unresolved addresses, excluding MemPutFast.
            "neon_memput_calls_nonfast": sum(patch_stats.get("neon", {}).get("scan", {}).get("calls", {}).get("MemPut", [])),
            "gta_reversed_scan": gr.stats,
            "plugin_sdk_scan": ps.stats if ps else None,
            "skipped": dict(srcs.skipped),
        }
        stats["seconds"] = round(time.perf_counter() - t0, 2)
        stats["timings"] = timings
        meta = {
            "schema_version": str(SCHEMA_VERSION),
            "satk_version": __version__,
            "built_at": scanned,
            "exe_path": srcs.exe.as_posix(),
            "exe_sha256": img.sha256,
            "image_base": f"0x{img.image_base:x}",
            "stats": json.dumps(stats, sort_keys=True),
        }
        con.executemany("INSERT INTO meta VALUES(?,?)", sorted(meta.items()))
        con.commit()
    except BaseException:
        con.close()
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    con.close()
    try:
        os.replace(tmp, out)
    except PermissionError as e:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise SatkError("BUSY", f"cannot replace {out.as_posix()} (open in another process?)",
                        hint="stop the process holding the DB (e.g. the MCP server) and retry") from e
    step("write", t)
    stats["seconds"] = round(time.perf_counter() - t0, 2)
    return stats


def _b(v) -> int | None:
    return None if v is None else int(bool(v))


def _frow(f: FuncSym) -> dict:
    return {"addr": f.addr, "name": f.name, "cls": f.cls, "qual": f.qual, "origin": f.origin,
            "src_file": f.src_file, "src_line": f.src_line, "reversed": f.reversed, "hook_state": f.hook_state,
            "locked": f.locked, "category": f.category}
