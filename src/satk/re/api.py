"""Query functions behind ``satk re ...`` and the ``fn``/``g``/``vt``/``patch`` SID provider (WP-09).

All functions take an open :class:`~satk.re.db.SymDb` and return envelope payloads (§3.3).
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from ..core.envelope import obj, table
from ..core.errors import SatkError
from .crash import CrashItem, parse_text
from .db import SymDb, hx, parse_addr

__all__ = ["fn_label", "addr_detail", "addr_rows", "resolve_text", "find", "src", "patches", "limits",
           "resolve_fn", "global_detail", "vtable_detail", "patch_detail", "fn_refs", "ADDR_COLS", "PATCH_COLS"]

ADDR_COLS = ["addr", "module", "fn", "off", "src", "reversed", "thunk", "patches", "confidence"]
PATCH_COLS = ["addr", "kind", "symbol", "origin", "src", "func"]
_ADDR_LIKE = re.compile(r"^(?:0x[0-9a-fA-F]+|[0-9a-fA-F]{5,8}|[\w.\-]+\.exe\+(?:0x)?[0-9a-fA-F]+)$")


# --------------------------------------------------------------------------- helpers


def fn_label(row: sqlite3.Row | None, addr: int | None) -> str | None:
    if addr is None:
        return None
    if row is not None and row["qual"]:
        return row["qual"]
    return f"sub_{addr:x}"


def _src(row: sqlite3.Row | None) -> str | None:
    if row is None or not row["src_file"]:
        return None
    return f"{row['src_file']}:{row['src_line']}" if row["src_line"] is not None else row["src_file"]


def _bool(v) -> bool | None:
    return None if v is None else bool(v)


def _with_offset(name: str, offset: int | None) -> str:
    return f"{name}{'-' if offset < 0 else '+'}0x{abs(offset):x}" if offset else name


def _fn_at(db: SymDb, a: int | None) -> str | None:
    """``CGame::Process+0x29`` for a code address (empty for data/outside)."""
    if a is None:
        return None
    loc = db.amap.locate(a)
    if loc.start is None:
        return None
    name = fn_label(db.func(loc.start), loc.start)
    return _with_offset(name, loc.off)


def _patch_rows(db: SymDb, rows: list[sqlite3.Row], hit_at: int | None = None) -> list[list]:
    out = []
    for r in rows:
        func = None
        if r["func_addr"] is not None:
            name = fn_label(db.func(r["func_addr"]), r["func_addr"])
            func = _with_offset(name, r["func_off"])
        row = [hx(r["addr"]), r["kind"], r["symbol"], r["origin"], f"{r['src_file']}:{r['src_line']}", func]
        if hit_at is not None:
            row.append(r["addr"] <= hit_at < r["addr"] + max(r["len"] or 1, 1))
        out.append(row)
    return out


def _dedupe_upstream(rows: list[sqlite3.Row]) -> list[sqlite3.Row]:
    """Drop upstream rows that our trunk repeats (same addr/kind/symbol): trunk = upstream + our work."""
    trunk = {(r["addr"], r["kind"], r["symbol"]) for r in rows if r["origin"] == "trunk"}
    if not trunk:
        return rows
    return [r for r in rows if not (r["origin"] == "upstream" and (r["addr"], r["kind"], r["symbol"]) in trunk)]


def _function_ranges(db: SymDb, start: int) -> list[tuple[int, int]]:
    """Every body of a function, shared by get/refs/patches (including thunk entries)."""
    exact = db.amap.exact_function_ranges(start)
    if exact:
        return exact
    ranges = [(start, db.amap.end_of(start))]
    th = db.thunk(start)
    if th is not None:
        loc = db.amap.locate(th["target"])
        if loc.end is not None:
            ranges.append((th["target"], loc.end))
    return ranges


def _patch_selection(db: SymDb, ranges: list[tuple[int, int]], origin: str = "all",
                     kind: str | None = None, *, dedupe: bool = True) -> tuple[list[sqlite3.Row], int]:
    where = " OR ".join("(addr >= ? AND addr < ?)" for _ in ranges)
    args: list = [value for bounds in ranges for value in bounds]
    sql = f"SELECT * FROM patch WHERE ({where})"
    if origin != "all":
        sql += " AND origin = ?"
        args.append(origin)
    if kind:
        sql += " AND kind = ?"
        args.append(kind)
    rows = list(db.con.execute(sql + " ORDER BY addr, origin, kind, src_file, src_line", args))
    raw_total = len(rows)
    return (_dedupe_upstream(rows) if dedupe and origin == "all" else rows), raw_total


def _patch_block(db: SymDb, ranges: list[tuple[int, int]], at: int, limit: int) -> dict | None:
    rows, raw_total = _patch_selection(db, ranges)
    if not rows:
        return None
    # patches covering the address first, then by address; origins: trunk, neon, upstream
    rank = {"satk": 0, "trunk": 1, "neon": 2, "upstream": 3}
    rows.sort(key=lambda r: (not (r["addr"] <= at < r["addr"] + max(r["len"] or 1, 1)), r["addr"],
                             rank.get(r["origin"], 9), r["kind"], r["src_file"], r["src_line"]))
    shown = rows[:limit]
    return {"cols": PATCH_COLS[:5] + ["hit"], "rows": [r[:5] + [r[6]] for r in _patch_rows(db, shown, at)],
            "total": len(rows), "raw_total": raw_total}


# --------------------------------------------------------------------------- re addr


def addr_detail(db: SymDb, a: int, *, patch_limit: int = 20) -> dict:
    """Everything known about one gta_sa.exe address (object payload, without ``ok``)."""
    loc = db.amap.locate(a)
    d: dict = {"addr": hx(a), "module": "gta_sa.exe", "section": loc.section}
    if loc.in_hoodlum:
        d["in_hoodlum"] = True
    if loc.kind == "outside":
        d.update(confidence="none", note="address outside the gta_sa.exe image")
        return d
    if loc.kind == "data":
        return _data_detail(db, a, d)
    if loc.start is None:
        d.update(confidence="none", note="address is not in a known function range")
        return d
    f = db.func(loc.start)
    d["id"] = f"fn:0x{loc.start:x}"
    d["fn"] = fn_label(f, loc.start)
    d["start"] = hx(loc.start)
    d["off"] = hx(loc.off)
    if loc.via_thunk:
        entry, target, kind = loc.via_thunk
        d["body"] = hx(target)
        d["via_thunk"] = {"entry": hx(entry), "kind": kind, "target": hx(target)}
    d["end"] = hx(loc.end)
    d["confidence"] = loc.confidence
    d["bounds"] = "ghidra" if loc.confidence == "exact" else "next_start"
    if loc.confidence == "exact":
        d["ranges"] = [[hx(lo), hx(hi)] for lo, hi in db.amap.exact_function_ranges(loc.start)]
    if f is not None:
        d["origin"] = f["origin"]
        d["src"] = _src(f) if f["origin"] in ("hooks_json", "gta_reversed") else None
        if f["origin"] == "plugin_sdk":
            d["sdk"] = _src(f)
        d["reversed"] = _bool(f["reversed"])
        d["state"] = f["hook_state"]
        d["locked"] = _bool(f["locked"])
    th = db.thunk(loc.start)
    if th is not None:
        d["thunk"] = {"kind": th["kind"], "target": hx(th["target"])}
    if loc.in_hoodlum:
        d["in_hoodlum"] = True
    if loc.alt:
        d["alt"] = [{"fn": fn_label(db.func(s), s), "start": hx(s), "off": hx(o),
                     "origin": (db.func(s) or {"origin": None})["origin"]} for s, o in loc.alt]
    aka = db.aliases(loc.start)
    if aka:
        d["aka"] = aka
    vts = db.vtable_slots_for(loc.start)
    if vts:
        d["vtables"] = vts
    pb = _patch_block(db, _function_ranges(db, loc.start), a, patch_limit)
    if pb and patch_limit > 0:
        d["patches"] = pb
    elif pb:
        d["n_patches"] = pb["total"]
    return d


def _data_detail(db: SymDb, a: int, d: dict) -> dict:
    d["kind"] = "data"
    g = db.global_at(a)
    if g is not None:
        r, off = g
        d.update(id=f"g:0x{r['addr']:x}", g=r["name"], g_off=hx(off) if off else None, type=r["type"],
                 src=_src(r), origin=r["origin"], confidence="high")
    else:
        v = db.vtable_at(a)
        if v is not None:
            r, off = v
            d.update(id=f"vt:0x{r['addr']:x}", vtable=r["cls"], slot=off // 4, src=_src(r), confidence="high")
        else:
            d["confidence"] = "none"
    hits = db.patches_covering(a)
    if hits:
        d["patches"] = {"cols": PATCH_COLS[:5], "rows": [r[:5] for r in _patch_rows(db, hits)], "total": len(hits)}
    return d


def _row_for(db: SymDb, item: CrashItem) -> list:
    if item.addr is None:
        return [f"{item.module}+0x{(item.off or 0):x}", item.module, None, None, None, None, None, None, "none"]
    d = addr_detail(db, item.addr, patch_limit=0)
    loc_thunk = d.get("thunk") or d.get("via_thunk")
    thunk = f"{loc_thunk['kind']}->{loc_thunk['target']}" if loc_thunk else None
    fn = d.get("fn") or d.get("g") or d.get("vtable")
    pcount = d.get("n_patches", 0) if d.get("id", "").startswith("fn:") else None
    return [hx(item.addr), "gta_sa.exe", fn, d.get("off") or d.get("g_off"), d.get("src"), d.get("reversed"),
            thunk, pcount, d.get("confidence")]


def addr_rows(db: SymDb, items: list[CrashItem]) -> list[list]:
    return [_row_for(db, it) for it in items]


def resolve_text(db: SymDb, text: str, *, limit: int = 50) -> dict:
    """``re_addr``: one gta_sa.exe address -> object; several (or a crash log) -> table."""
    items = parse_text(text)
    if not items:
        raise SatkError("BAD_PARAMS", "no addresses found in the input",
                        hint="satk re addr 0x53BF09 | gta_sa.exe+0x13BF09 | --text-file crash.txt")
    if len(items) == 1 and items[0].addr is not None:
        d = addr_detail(db, items[0].addr)
        ident = d.pop("id", None)
        return obj(ident, **d)
    rows = addr_rows(db, items[:limit])
    gta = sum(1 for i in items if i.addr is not None)
    env = table(ADDR_COLS, rows, total=len(items))
    env["summary"] = {"gta_sa": gta, "other_modules": len(items) - gta}
    return env


# --------------------------------------------------------------------------- find


_KIND_MAP = {"func": "fn", "global": "g", "vtable": "vt", "struct": "struct", "patch": "patch",
             "fn": "fn", "g": "g", "vt": "vt"}


def _info_fn(db: SymDb, addr: int) -> str:
    f = db.func(addr)
    if f is None:
        return ""
    parts = []
    s = _src(f)
    if s:
        parts.append(s)
    if f["reversed"] is not None:
        parts.append("reversed" if f["reversed"] else "not reversed")
    if f["origin"] != "hooks_json":
        parts.append(f["origin"])
    th = db.thunk(addr)
    if th is not None:
        parts.append(f"thunk {th['kind']}")
    return " ".join(parts)


def _row_from_hit(db: SymDb, kind: str, sid: str | None, name: str) -> list:
    if kind == "fn" and sid:
        return [sid, "fn", name, _info_fn(db, int(sid[3:], 16))]
    if kind == "g" and sid:
        r = db.con.execute("SELECT type, array_len, src_file, src_line FROM global WHERE addr=?",
                           (int(sid[2:], 16),)).fetchone()
        info = (r["type"] or "") if r else ""
        return [sid, "g", name, info]
    if kind == "vt" and sid:
        r = db.con.execute("SELECT slots FROM vtable WHERE addr=?", (int(sid[3:], 16),)).fetchone()
        return [sid, "vt", name, f"{r['slots']} slots" if r else ""]
    if kind == "struct":
        r = db.con.execute("SELECT size FROM struct_size WHERE name=?", (name,)).fetchone()
        return [None, "struct", name, f"size 0x{r['size']:x}" if r else ""]
    if kind == "patch":
        return [sid, "patch", name, ""]
    return [sid, kind, name, ""]


def find(db: SymDb, name: str, kind: str | None = None, limit: int = 10, offset: int = 0) -> dict:
    """``re_find``: exact name, then ``::member`` exact, then prefix, then trigram/substring."""
    q = name.strip()
    if not q:
        raise SatkError("BAD_PARAMS", "empty name")
    k = _KIND_MAP.get(kind or "", None) if kind else None
    if kind and k is None:
        raise SatkError("BAD_PARAMS", f"unknown kind {kind!r}", did_you_mean=["func", "global", "vtable", "struct"])
    kinds = [k] if k else ["fn", "g", "vt", "struct", "patch"]
    seen: set[tuple] = set()
    hits: list[tuple[int, str, str | None, str]] = []   # (tier, kind, sid, name)
    like_esc = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    def add(tier: int, kd: str, sid: str | None, nm: str) -> None:
        key = (kd, sid, nm)
        if key not in seen:
            seen.add(key)
            hits.append((tier, kd, sid, nm))

    # Collect all matching tiers so total and next do not depend on page size or offset.
    for tier, (op, arg) in enumerate([("=", q), ("suffix", "%::" + like_esc), ("prefix", like_esc + "%")]):
        for kd in kinds:
            for sid, nm in _lookup(db, kd, op, arg):
                add(tier, kd, sid, nm)
    kind_sql = " AND kind IN (" + ",".join("?" for _ in kinds) + ")"
    rows = None
    if len(q) >= 3:
        fts_q = '"' + q.replace('"', '""') + '"'
        try:
            rows = list(db.con.execute("SELECT sid, kind, qual FROM sym_fts WHERE sym_fts MATCH ?" + kind_sql,
                                       [fts_q, *kinds]))
        except sqlite3.OperationalError:
            pass  # same substring search below if FTS is unavailable
    if rows is None:
        rows = list(db.con.execute("SELECT sid, kind, qual FROM sym_fts WHERE qual LIKE ? ESCAPE '\\'" + kind_sql,
                                   ["%" + like_esc + "%", *kinds]))
    for r in rows:
        add(3, r["kind"], r["sid"], r["qual"])
    # exact case-sensitive first, then by tier, then shorter names
    hits.sort(key=lambda h: (h[0], 0 if h[3] == q else 1, len(h[3]), h[3], h[1], h[2] or ""))
    page = hits[offset:offset + limit]
    rows_out = [_row_from_hit(db, kd, sid, nm) for _t, kd, sid, nm in page]
    nxt = f"o:{offset + limit}" if len(hits) > offset + limit else None
    if not hits and kinds[0] == "fn":
        extra = _kb_find_rows(db, q)
        if extra:
            env = table(["id", "kind", "name", "info"], extra, total=len(extra))
            env["note"] = "no symbol DB match; answered from the knowledge base (re src works for these)"
            return env
    env = table(["id", "kind", "name", "info"], rows_out, total=len(hits), next=nxt)
    if not hits and kinds[0] == "fn" and offset == 0:
        similar = _similar_funcs(db, q if "::" in q else "::" + q)
        if similar:
            env["did_you_mean"] = similar
            env["hint"] = f"satk re src {similar[0]}"
    return env


def _kb_find_rows(db: SymDb, q: str) -> list[list]:
    """``re find`` rows from the knowledge base for a function the symbol DB does not know (not hooked by
    gta-reversed), or for a member declared in a base class."""
    hit = _kb_location(q)
    if hit is not None:
        a = hit.get("addr")
        rel = str(hit.get("path") or "")
        rel = rel[len("source/"):] if rel.startswith("source/") else rel
        why = "not reversed" if hit.get("hook") is False else "not in the symbol DB"
        return [[f"fn:0x{a:x}" if a is not None else None, "func", hit["name"],
                 f"kb: {why} at {rel}:{hit.get('line') or 1}"]]
    base = inherited_name(q, lambda n: bool(db.funcs_by_name(n)))
    if base is not None:
        rows = db.funcs_by_name(base)
        return [[f"fn:0x{rows[0]['addr']:x}", "func", rows[0]["qual"], f"inherited: {q} is served by {base}"]]
    return []


def _lookup(db: SymDb, kd: str, op: str, arg: str) -> list[tuple[str | None, str]]:
    if op == "=":
        cond, args = "= ? COLLATE NOCASE", (arg,)
    else:
        cond, args = "LIKE ? ESCAPE '\\'", (arg,)
    if kd == "fn":
        rows = [(f"fn:0x{r[0]:x}", r[1]) for r in db.con.execute(
            f"SELECT addr, qual FROM func WHERE qual {cond} ORDER BY length(qual), addr", args)]
        rows += [(f"fn:0x{r[0]:x}", r[1]) for r in db.con.execute(
            f"SELECT addr, qual FROM func_alias WHERE qual {cond} ORDER BY length(qual), addr", args)]
        return rows
    if kd == "g":
        return [(f"g:0x{r[0]:x}", r[1]) for r in db.con.execute(
            f"SELECT addr, name FROM global WHERE name {cond} ORDER BY length(name), addr", args)]
    if kd == "vt":
        return [(f"vt:0x{r[0]:x}", r[1]) for r in db.con.execute(
            f"SELECT addr, cls FROM vtable WHERE cls {cond} ORDER BY length(cls), addr", args)]
    if kd == "struct":
        return [(None, r[0]) for r in db.con.execute(
            f"SELECT name FROM struct_size WHERE name {cond} ORDER BY length(name)", args)]
    if kd == "patch":
        return [(f"patch:{r[0]}/{r[1].lower()}", r[1]) for r in db.con.execute(
            f"SELECT DISTINCT origin, symbol FROM patch WHERE symbol {cond} ORDER BY length(symbol)", args)]
    return []


# --------------------------------------------------------------------------- functions by name/addr


def resolve_fn(db: SymDb, fn: str) -> tuple[int, sqlite3.Row | None, list[str]]:
    """``(start, func row, other candidates)`` for a name or an address inside a function."""
    s = fn.strip()
    if s.lower().startswith("fn:"):
        s = s[3:]
    m = re.fullmatch(r"sub_([0-9a-fA-F]{5,8})", s)
    if m:
        s = "0x" + m.group(1)
    if _ADDR_LIKE.match(s) and not re.fullmatch(r"[A-Za-z_]\w*", s):
        a = parse_addr(s)
        loc = db.amap.locate(a)
        if loc.start is None:
            raise SatkError("NOT_FOUND", f"no function contains {hx(a)}", hint=f"satk re addr {hx(a)}")
        return loc.start, db.func(loc.start), []
    rows = db.funcs_by_name(s)
    if not rows:
        cands = [r[2] for r in find(db, s, "func", limit=5)["rows"]]
        member = s.rpartition("::")[2]
        if member != s and len(cands) < 5:     # Class::Member unknown: the member in other classes
            cands += [r[2] for r in find(db, member, "func", limit=5)["rows"] if r[2] not in cands][:5 - len(cands)]
        if len(cands) < 5:                     # a guessed name: functions sharing most of its words
            cands += [n for n in _similar_funcs(db, s) if n not in cands][:5 - len(cands)]
        raise SatkError("NOT_FOUND", f"no function {s!r}",
                        hint=f"satk re src {cands[0]}" if cands else f"satk kb search {member}",
                        did_you_mean=cands)
    exact = [r for r in rows if r["qual"] == s] or rows
    others = [f"{r['qual']}@0x{r['addr']:x}" for r in rows if r is not exact[0]]
    return exact[0]["addr"], exact[0], others


_WORD = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")


def _words(name: str) -> set[str]:
    """Lower-case CamelCase words of a name, plural ``s`` dropped (``SetEditableMaterials`` -> set, editable,
    material)."""
    out = set()
    for w in _WORD.findall(name):
        w = w.lower()
        if len(w) > 3 and w.endswith("s"):
            w = w[:-1]
        if len(w) > 1:
            out.add(w)
    return out


def _similar_funcs(db: SymDb, name: str, limit: int = 5) -> list[str]:
    """Functions whose member name shares most CamelCase words with ``name`` (``FindEditableMaterialList`` ->
    ``SetEditableMaterials``), the same class first; at least two shared words."""
    cls, _, member = name.rpartition("::")
    want = _words(member)
    if len(want) < 2:
        return []
    keys = sorted(want, key=lambda w: (-len(w), w))[:3]       # letters and digits only: nothing to escape
    sql = "SELECT DISTINCT qual FROM func WHERE " + " OR ".join("qual LIKE ?" for _ in keys) + " LIMIT 5000"
    found: list[tuple[str, str, set[str]]] = []
    df: dict[str, int] = {}                                    # how many candidates carry each word
    for (qual,) in db.con.execute(sql, [f"%{k}%" for k in keys]):
        c, _, m = str(qual).rpartition("::")
        shared = want & _words(m)
        for w in shared:
            df[w] = df.get(w, 0) + 1
        if len(shared) >= 2:
            found.append((str(qual), c, shared))
    scored = {q: (bool(cls) and c.lower() != cls.lower(), -round(sum(1.0 / df[w] for w in sh), 9), len(q), q)
              for q, c, sh in found}                           # rare shared words weigh more
    return sorted(scored, key=scored.__getitem__)[:limit]


# --------------------------------------------------------------------------- re src


def _tree_for(db: SymDb, kind: str):
    from ..core.paths import cfg
    from .gitsrc import DirTree, GitTree

    r = db.con.execute("SELECT repo, rev FROM source_rev WHERE kind=?", (kind,)).fetchone()
    if r is None:
        raise SatkError("NOT_FOUND", f"source {kind} is not in the symbol DB")
    repo = Path(r["repo"])
    if not repo.is_absolute():
        repo = cfg().paths.workspace / repo
    if r["rev"].startswith("dir:"):
        return DirTree(repo)
    return GitTree(repo, r["rev"])


def _kb_location(key: str | int) -> dict | None:
    """The knowledge base's location of a function (name or start address); ``None`` without a hit or a KB."""
    try:
        from ..kb.query import func_location

        return func_location(key)
    except (SatkError, OSError, ImportError):
        return None


def _kb_bases(cls: str) -> list[str]:
    """Base classes of ``cls`` from the knowledge base, nearest first; ``[]`` without a KB."""
    try:
        from ..kb.query import class_bases

        return class_bases(cls)
    except (SatkError, OSError, ImportError, sqlite3.Error):
        return []


def inherited_name(name: str, known) -> str | None:
    """``CVehicle::ApplySpringCollision`` -> ``CPhysical::ApplySpringCollision`` when the member is declared in
    a base class: the first base (nearest first) for which ``known(qualified)`` is true."""
    cls, sep, member = name.rpartition("::")
    if not sep or not cls or not member:
        return None
    for base in _kb_bases(cls):
        cand = f"{base}::{member}"
        if known(cand):
            return cand
    return None


def _kb_tree(kind: str):
    """Source tree at the revision the knowledge base read ``kind`` from (no symbol DB needed)."""
    from ..core.paths import cfg
    from ..kb.query import source_rev
    from .gitsrc import DirTree, GitTree

    r = source_rev(kind)
    if r is None:
        raise SatkError("NOT_FOUND", f"source {kind} is not in the knowledge base")
    repo = Path(r["repo"])
    if not repo.is_absolute():
        repo = cfg().paths.workspace / repo
    return DirTree(repo) if r["rev"].startswith("dir:") else GitTree(repo, r["rev"])


def _kb_src(db: SymDb | None, hit: dict, context: int, others: list[str] | None = None) -> dict:
    """``re_src`` answer from a knowledge-base location: file:line, status and the lines when readable.

    The source tree comes from the symbol DB when there is one, else from the knowledge base's revision.
    """
    path, line = hit["path"] or "", int(hit["line"] or 1)
    kind = hit["src"]
    hooked = hit.get("hook")
    if hooked is False:
        status = "not reversed (no install line in gta-reversed; the body may be a stub)"
    else:
        status = "not in the symbol DB"
    rel = path[len("source/"):] if kind == "gta-reversed" and path.startswith("source/") else path
    addr = hit.get("addr")
    out: dict = {"fn": hit["name"], "addr": hx(addr) if addr is not None else None, "file": rel, "line": line,
                 "def_line": line, "repo": kind, "status": status, "via": "kb",
                 "note": f"{status} at {rel}:{line}"}
    try:
        tree = _tree_for(db, kind) if db is not None else _kb_tree(kind)
        text = tree.read(path)
    except SatkError as e:
        text, tree = None, None
        out["lines_unavailable"] = e.msg
    if text is not None and tree is not None:
        lines = text.splitlines()
        cls, _, base = str(hit["name"]).rpartition("::")
        def_line = None
        if addr is not None:
            def_line = _find_def(lines, addr, cls or None, base) or (_find_def(lines, addr, None, base) if cls else None)
        if def_line and def_line != line:
            out["def_line"] = def_line
            if 0 < line <= len(lines):
                out["install"] = f"{line}: {lines[line - 1]}"
            line = def_line
            out["note"] = f"{status} at {rel}:{def_line}"
        lo, hi = max(1, line - 3), min(len(lines), line + context)
        out.update(rev=tree.rev[:12], first=lo, lines=[f"{n}: {lines[n - 1]}" for n in range(lo, hi + 1)])
    elif tree is not None:
        out["lines_unavailable"] = f"{path} not readable at {tree.rev[:12]} ({kind})"
    if others:
        out["others"] = others[:5]
    return obj(f"fn:0x{addr:x}" if addr is not None else None, **out)


def src(db: SymDb, fn: str, context: int = 30) -> dict:
    """``re_src``: gta-reversed source around the function (install line and definition).

    Functions without a source line in the symbol DB (not hooked by gta-reversed) fall back to the
    knowledge base's location (``via: kb``) instead of a dead end.
    """
    if context < 0 or context > 400:
        raise SatkError("BAD_PARAMS", f"context must be in 0..400 (got {context})",
                        hint="--context 400 is the maximum; the answer names file and line for reading further")
    try:
        start, f, others = resolve_fn(db, fn)
    except SatkError as e:
        if e.code != "NOT_FOUND":
            raise
        name = fn.strip()[3:] if fn.strip().lower().startswith("fn:") else fn.strip()
        hit = _kb_location(name)
        if hit is not None:
            return _kb_src(db, hit, context)
        base = inherited_name(name, lambda q: bool(db.funcs_by_name(q)) or _kb_location(q) is not None)
        if base is None:
            raise
        env = src(db, base, context)
        env["inherited"] = f"{name} is not declared in {name.rpartition('::')[0]}; {base} (a base class) serves it"
        return env
    if f is None or not f["src_file"]:
        hit = _kb_location(start) or (_kb_location(f["qual"]) if f is not None and f["qual"] else None)
        if hit is not None:
            return _kb_src(db, hit, context, others)
        raise SatkError("NOT_FOUND", f"no source location for {fn_label(f, start)}",
                        hint=f"only gta-reversed and plugin-sdk functions have sources; satk kb search "
                             f"{fn_label(f, start)} (needs satk kb build)")
    if f["origin"] in ("hooks_json", "gta_reversed"):
        tree, rel, kind = _tree_for(db, "gta-reversed"), "source/" + f["src_file"], "gta-reversed"
    else:
        tree, rel, kind = _tree_for(db, "plugin-sdk"), f["src_file"], "plugin-sdk"
    text = tree.read(rel)
    if text is None:
        raise SatkError("NOT_FOUND", f"{rel} not readable at {tree.rev[:12]} ({kind})",
                        data={"missing_blobs": getattr(tree, "missing", [])[:3]})
    lines = text.splitlines()
    line = f["src_line"] or 1
    def_line = _find_def(lines, start, f["cls"], f["name"])
    if def_line and abs(def_line - line) <= context:
        lo, hi = min(line, def_line) - 3, max(line, def_line) + context
    elif def_line:
        lo, hi = def_line - 3, def_line + context
    else:
        lo, hi = line - context, line + context
    lo = max(1, lo)
    hi = min(len(lines), hi)
    shown = [f"{n}: {lines[n - 1]}" for n in range(lo, hi + 1)]
    out = {"fn": fn_label(f, start), "addr": hx(start), "file": f["src_file"], "line": line,
           "def_line": def_line, "repo": kind, "rev": tree.rev[:12], "first": lo, "lines": shown}
    if def_line and not (lo <= line <= hi):
        out["install"] = f"{line}: {lines[line - 1]}" if 0 < line <= len(lines) else None
    if others:
        out["others"] = others[:5]
    return obj(f"fn:0x{start:x}", **out)


def _find_def(lines: list[str], addr: int, cls: str | None, name: str | None) -> int | None:
    """Definition line: the line after ``// 0xADDR`` or ``Class::Name(`` not ending with ``;``."""
    pat_addr = re.compile(r"//\s*0x0*%x\b" % addr, re.I)
    base = (name or "").split("-")[0]
    pat_def = None
    if base:
        q = (re.escape(cls) + r"\s*::\s*" if cls else r"\b") + re.escape(base) + r"\s*\("
        pat_def = re.compile(q)
    for i, ln in enumerate(lines):
        if pat_addr.search(ln) and "Install" not in ln:
            for j in range(i, min(i + 4, len(lines))):
                if "(" in lines[j] and not lines[j].lstrip().startswith("//"):
                    return j + 1
    if pat_def:
        for i, ln in enumerate(lines):
            s = ln.strip()
            if pat_def.search(ln) and not s.startswith(("//", "RH_", "return")) and not s.endswith(";"):
                return i + 1
    return None


# --------------------------------------------------------------------------- re patches


def patches(db: SymDb, fn: str | None, rng: str | None, origin: str, kind: str | None, limit: int,
            offset: int, *, dedupe: bool = True) -> dict:
    lo, hi = 0, 1 << 32
    extra: dict = {}
    if fn and rng:
        raise SatkError("BAD_PARAMS", "give fn or range, not both")
    if fn:
        start, f, _others = resolve_fn(db, fn)
        extra["fn"] = fn_label(f, start)
        ranges = _function_ranges(db, start)
    elif rng:
        m = re.fullmatch(r"\s*((?:0x)?[0-9a-fA-F]+)\s*(?:-|\.\.)\s*((?:0x)?[0-9a-fA-F]+)\s*", rng)
        if not m:
            raise SatkError("BAD_PARAMS", f"range must look like 0x53BF00-0x53C000, got {rng!r}")
        lo, hi = int(m.group(1), 16), int(m.group(2), 16) + 1
        ranges = [(lo, hi)]
    else:
        ranges = [(lo, hi)]
    rows, raw_total = _patch_selection(db, ranges, origin, kind, dedupe=dedupe)
    total = len(rows)
    env = table(PATCH_COLS, _patch_rows(db, rows[offset:offset + limit]), total=total,
                next=f"o:{offset + limit}" if total > offset + limit else None)
    env["raw_total"] = raw_total
    env.update(extra)
    return env


# --------------------------------------------------------------------------- re limits


def limits(db: SymDb, kind: str | None, q: str | None, limit: int, offset: int, *, summary: bool = False) -> dict:
    where, args = [], []
    if kind:
        where.append("l.kind = ?")
        args.append(kind)
    if q:
        where.append("l.name LIKE ? ESCAPE '\\'")
        args.append("%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    w = (" WHERE " + " AND ".join(where)) if where else ""
    total = int(db.con.execute(f"SELECT count(*) FROM limit_def l{w}", args).fetchone()[0])
    if summary:
        by = {r[0]: r[1] for r in db.con.execute(f"SELECT l.kind, count(*) FROM limit_def l{w} GROUP BY l.kind", args)}
        return obj(None, total=total, by_kind=by, arrays_with_len=int(db.con.execute(
            "SELECT count(*) FROM limit_def WHERE kind='array' AND vanilla IS NOT NULL").fetchone()[0]),
            hint="rows: satk re limits --kind pool (or --match <name>); stock pools and stores: satk kb fact pools")
    rows = db.con.execute(
        f"SELECT l.*, g.type AS gtype, g.elem_size AS esz FROM limit_def l LEFT JOIN global g ON g.addr = l.global_addr"
        f"{w} ORDER BY l.kind, l.name LIMIT ? OFFSET ?", args + [limit, offset])
    out = [[r["name"], r["kind"], r["vanilla"], hx(r["global_addr"]), r["gtype"], r["esz"], r["trunk"], r["neon"],
            r["source"]] for r in rows]
    by_kind = {r[0]: r[1] for r in db.con.execute("SELECT kind, count(*) FROM limit_def GROUP BY kind")}
    env = table(["name", "kind", "vanilla", "addr", "type", "elem_size", "trunk", "neon", "src"], out, total=total,
                next=f"o:{offset + limit}" if total > offset + limit else None)
    env["by_kind"] = by_kind
    env["arrays_with_len"] = int(db.con.execute(
        "SELECT count(*) FROM limit_def WHERE kind='array' AND vanilla IS NOT NULL").fetchone()[0])
    return env


# --------------------------------------------------------------------------- SID objects


def global_detail(db: SymDb, key: str) -> dict:
    if key.startswith("0x"):
        g = db.global_at(int(key, 16))
        if g is None:
            raise SatkError("NOT_FOUND", f"no global at {key}", hint=f"satk re addr {key}")
        r, off = g
    else:
        r = db.con.execute("SELECT * FROM global WHERE name = ? COLLATE NOCASE ORDER BY length(name) LIMIT 1",
                           (key,)).fetchone()
        if r is None:
            r = db.con.execute("SELECT * FROM global WHERE name LIKE ? ESCAPE '\\' COLLATE NOCASE "
                               "ORDER BY length(name) LIMIT 1", ("%::" + key.replace("_", "\\_"),)).fetchone()
        if r is None:
            raise SatkError("NOT_FOUND", f"no global {key!r}", hint=f"satk re find {key} --kind global")
        off = 0
    size = r["byte_size"] or 1
    hits = db.patches_in(r["addr"], r["addr"] + size)
    lim = db.con.execute("SELECT kind, vanilla FROM limit_def WHERE global_addr = ?", (r["addr"],)).fetchone()
    return obj(f"g:0x{r['addr']:x}", name=r["name"], addr=hx(r["addr"]), off=hx(off) if off else None, type=r["type"],
               elem_type=r["elem_type"], array_len=r["array_len"], elem_size=r["elem_size"], byte_size=r["byte_size"],
               src=_src(r), origin=r["origin"],
               limit={"kind": lim["kind"], "vanilla": lim["vanilla"]} if lim else None,
               patches={"cols": PATCH_COLS, "rows": _patch_rows(db, hits[:20]), "total": len(hits)} if hits else None)


def vtable_detail(db: SymDb, key: str, slot_limit: int = 64) -> dict:
    if key.startswith("0x"):
        v = db.vtable_at(int(key, 16))
        if v is None:
            raise SatkError("NOT_FOUND", f"no vtable at {key}")
        r = v[0]
    else:
        r = db.con.execute("SELECT * FROM vtable WHERE cls = ? COLLATE NOCASE", (key,)).fetchone()
        if r is None:
            raise SatkError("NOT_FOUND", f"no vtable for class {key!r}", hint=f"satk re find {key} --kind vtable")
    slots = []
    for s in db.con.execute("SELECT slot, target FROM vtable_slot WHERE vt=? ORDER BY slot LIMIT ?",
                            (r["addr"], slot_limit)):
        slots.append([s["slot"], hx(s["target"]), fn_label(db.func(s["target"]), s["target"])])
    return obj(f"vt:0x{r['addr']:x}", cls=r["cls"], addr=hx(r["addr"]), slots=r["slots"], src=_src(r),
               table={"cols": ["slot", "target", "fn"], "rows": slots})


def patch_detail(db: SymDb, key: str) -> dict:
    origin, _, sym = key.partition("/")
    rows = list(db.con.execute(
        "SELECT * FROM patch WHERE origin = ? AND symbol = ? COLLATE NOCASE ORDER BY addr, src_file, src_line",
        (origin, sym)))
    if not rows:
        raise SatkError("NOT_FOUND", f"no patch {key!r}", hint=f"satk re find {sym} --kind patch")
    return obj(f"patch:{origin}/{rows[0]['symbol'].lower()}", symbol=rows[0]["symbol"], origin=origin,
               addr=hx(rows[0]["addr"]), func=_fn_at(db, rows[0]["addr"]),
               sites={"cols": PATCH_COLS, "rows": _patch_rows(db, rows[:50]), "total": len(rows)})


def fn_refs(db: SymDb, start: int, rel: str | None, limit: int, offset: int) -> dict:
    rels = {
        "patches": lambda: len(_patch_selection(db, _function_ranges(db, start))[0]),
        "callers": lambda: db.count("callsite", "WHERE callee = ?", (start,)),
        "callees": lambda: db.count("callsite", "WHERE caller = ?", (start,)),
        "vtables": lambda: db.count("vtable_slot", "WHERE target = ?", (start,)),
    }
    if rel is None:
        return obj(f"fn:0x{start:x}", rels={k: f() for k, f in rels.items()})
    if rel == "patches":
        env = patches(db, hx(start), None, "all", None, limit, offset)
        env.pop("fn", None)
        return env
    if rel in ("callers", "callees"):
        col, other = ("callee", "caller") if rel == "callers" else ("caller", "callee")
        rows = list(db.con.execute(f"SELECT site, {other} AS fn, kind FROM callsite WHERE {col} = ? "
                                   f"ORDER BY site LIMIT ? OFFSET ?", (start, limit, offset)))
        total = rels[rel]()
        return table(["site", "fn", "kind"],
                     [[hx(r["site"]), _fn_at(db, r["fn"]) if r["fn"] else None, r["kind"]] for r in rows],
                     total=total, next=f"o:{offset + limit}" if total > offset + limit else None)
    if rel == "vtables":
        rows = list(db.con.execute("SELECT v.addr, v.cls, s.slot FROM vtable_slot s JOIN vtable v ON v.addr = s.vt "
                                   "WHERE s.target = ? ORDER BY v.cls LIMIT ? OFFSET ?", (start, limit, offset)))
        return table(["id", "cls", "slot"], [[f"vt:0x{r[0]:x}", r[1], r[2]] for r in rows], total=rels[rel]())
    raise SatkError("BAD_PARAMS", f"unknown rel {rel!r}", did_you_mean=list(rels))
