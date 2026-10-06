"""Queries over ``work/kb/kb.sqlite`` (owner M2-02): search, sym, struct, opcode, fact, status.

Every call opens the file read-only and closes it again (no handle stays open, so a rebuild can
replace the file at any time). Results are satk envelopes; code is shown as single lines
(``info``), never written anywhere.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from contextlib import closing
from pathlib import Path

from ..core.envelope import obj, table
from ..core.errors import SatkError

__all__ = ["kb_path", "connect", "fts_query", "search", "sym", "struct", "opcode", "fact", "status", "parse_addr",
           "enum_members", "func_location", "source_rev", "class_bases", "SOURCE_ORDER"]

SOURCE_ORDER = ("gta-reversed", "plugin-sdk", "mta-upstream", "mta-neon", "cleo-ai", "research", "facts")
KIND_ORDER = ("func", "global", "struct", "vtable", "limit", "const", "enum", "define", "hookpos")
_SRC_RANK = {k: i for i, k in enumerate(SOURCE_ORDER)}
_KIND_RANK = {k: i for i, k in enumerate(KIND_ORDER)}
_TOKEN = re.compile(r"[A-Za-z0-9_Ѐ-ӿ]+")
_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z0-9])|[A-Z]?[a-z]+|[A-Z]+|\d+")
_ADDR = re.compile(r"^\s*(?:(?:gta_sa\.exe|gta_sa)\s*\+\s*)?0[xX]([0-9A-Fa-f]{1,8})\s*$")


def kb_path() -> Path:
    """``<work>/kb/kb.sqlite`` (not created)."""
    from ..core.paths import cfg

    return Path(os.path.abspath(cfg().paths.work)) / "kb" / "kb.sqlite"


def connect(path: Path | None = None) -> sqlite3.Connection:
    """Read-only connection; ``NOT_READY`` if the KB is missing or from another schema version."""
    from .build import SCHEMA_VERSION

    p = path or kb_path()
    if not p.is_file():
        raise SatkError("NOT_READY", f"knowledge base not built ({p.as_posix()})", hint="satk kb build")
    con = sqlite3.connect(p.absolute().as_uri() + "?mode=ro", uri=True, check_same_thread=False)
    con.row_factory = sqlite3.Row
    v = con.execute("PRAGMA user_version").fetchone()[0]
    if v != SCHEMA_VERSION:
        con.close()
        raise SatkError("NOT_READY", f"knowledge base schema {v} != {SCHEMA_VERSION}", hint="satk kb build")
    return con


def parse_addr(s: str) -> int | None:
    """``0x5B8E64`` / ``gta_sa.exe+0x1B8E64`` -> int, else ``None``."""
    m = _ADDR.match(str(s))
    if not m:
        return None
    v = int(m.group(1), 16)
    return v + 0x400000 if "+" in s else v


def _offset(cursor: str | None) -> int:
    if not cursor:
        return 0
    if cursor.startswith("o:") and cursor[2:].isdigit():
        return int(cursor[2:])
    raise SatkError("BAD_PARAMS", f"bad cursor {cursor!r}")


def _hx(v: int | None) -> str | None:
    return None if v is None else f"0x{v:X}"


def _loc(path: str | None, line: int | None) -> str | None:
    if not path:
        return None
    return f"{path}:{line}" if line else path


def _cut(s: str | None, n: int = 160) -> str | None:
    if s is None:
        return None
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= n else s[:n - 3] + "..."


# --------------------------------------------------------------------------- FTS


def fts_query(text: str) -> str | None:
    """User text -> FTS5 query: every token must match (prefix); camelCase tokens may also match
    as a phrase of their words (``InfoForModel`` -> ``"info for model"``)."""
    terms = []
    for tok in _TOKEN.findall(text):
        t = tok.lower()
        parts = [p.lower() for p in _CAMEL.findall(tok)] if re.search(r"[A-Za-z]", tok) else []
        if len(parts) > 1:
            terms.append(f'("{t}"* OR "{" ".join(parts)}")')
        else:
            terms.append(f'"{t}"*')
    return " AND ".join(terms) if terms else None


def _tokens(text: str) -> list[tuple[str, list[str]]]:
    """``[(token, camelCase parts)]`` of a query, lower case."""
    out = []
    for tok in _TOKEN.findall(text):
        parts = [p.lower() for p in _CAMEL.findall(tok)] if re.search(r"[A-Za-z]", tok) else []
        out.append((tok.lower(), parts if len(parts) > 1 else []))
    return out


def _best_line(body: str, toks: list[tuple[str, list[str]]]) -> tuple[int, str]:
    """Index and text of the body line that matches the query best (whole tokens count double)."""
    best, best_i, best_s = -1, 0, ""
    full = 2 * len(toks)
    for i, ln in enumerate(body.splitlines()):
        low = ln.lower()
        score = 0
        for t, parts in toks:
            if t in low:
                score += 2
            elif parts and all(p in low for p in parts):
                score += 1
        if score > best:
            best, best_i, best_s = score, i, ln
            if score == full:
                break
    return best_i, best_s


def _src_filter(source: str | None) -> tuple[str, list]:
    if not source:
        return "", []
    if source not in SOURCE_ORDER:
        raise SatkError("BAD_PARAMS", f"unknown source {source!r}", did_you_mean=list(SOURCE_ORDER))
    return " AND src.key = ?", [source]


# --------------------------------------------------------------------------- search


def _sym_row(r: sqlite3.Row) -> list:
    info = " ".join(x for x in (_hx(r["addr"]), r["sig"]) if x)
    if r["doc"] and len(info) < 100:
        info = f"{info} // {r['doc']}" if info else r["doc"]
    return [r["kind"], r["name"], _loc(r["path"], r["line"]), r["src"], _cut(info)]


_SYM_SELECT = ("SELECT s.id, s.kind, s.name, s.owner, s.addr, s.sig, s.doc, s.line, s.flags, f.path AS path, "
               "src.key AS src FROM sym s LEFT JOIN file f ON f.id = s.file_id LEFT JOIN source src ON src.id = f.source_id")


def search(q: str, *, source: str | None = None, kind: str | None = None, limit: int = 20,
           cursor: str | None = None, path: Path | None = None) -> dict:
    """Full-text search over symbols, facts, opcodes and source/doc chunks (see :func:`fts_query`)."""
    off = _offset(cursor)
    want = off + limit + 1          # one extra row tells whether there is a next page
    with closing(connect(path)) as con:
        addr = parse_addr(q)
        if addr is not None:
            return _search_addr(con, addr, source, limit, off)
        fq = fts_query(q)
        if not fq:
            raise SatkError("BAD_PARAMS", "empty query", hint='satk kb search "CStreaming RequestModel"')
        toks = _tokens(q)
        sf, sp = _src_filter(source)
        norm_q = re.sub(r"\W+", "", q).lower()
        exact: list[list] = []
        syms: list[list] = []
        facts: list[list] = []
        ops: list[list] = []
        code: list[list] = []
        try:
            if kind in (None, "sym"):
                rows = con.execute(
                    f"{_SYM_SELECT} JOIN sym_fts ON sym_fts.rowid = s.id WHERE sym_fts MATCH ?{sf} "
                    "ORDER BY bm25(sym_fts, 10.0, 4.0, 1.0, 0.5) LIMIT ?", [fq, *sp, want * 3 + 50]).fetchall()
                def is_exact(name: str) -> bool:   # "CStreaming RequestModel" or just "RequestModel"
                    return norm_q in (re.sub(r"\W+", "", name).lower(),
                                      re.sub(r"\W+", "", name.rsplit("::", 1)[-1]).lower())

                ranked = sorted(enumerate(rows), key=lambda ir: (
                    not is_exact(ir[1]["name"]), ir[0] // 10, _KIND_RANK.get(ir[1]["kind"], 99),
                    _SRC_RANK.get(ir[1]["src"], 99), ir[0]))
                for _i, r in ranked:
                    (exact if is_exact(r["name"]) else syms).append(_sym_row(r))
            if kind in (None, "fact") and source in (None, "facts"):
                for r in con.execute("SELECT f.key, f.title, f.value, f.status FROM fact_fts JOIN fact f ON f.key = fact_fts.key "
                                     "WHERE fact_fts MATCH ? ORDER BY bm25(fact_fts) LIMIT ?", (fq, want)):
                    facts.append(["fact", r["key"], None, "facts", _cut(f"{r['title']}: {r['value']} [{r['status']}]")])
                for f in _asset_match(q)[:want]:
                    facts.append(["fact", f["key"], None, "facts", _cut(f"{f['title']}: {f['value']} [{_ASSET_STATUS}]")])
            if kind in (None, "opcode") and source in (None, "cleo-ai"):
                for r in con.execute("SELECT o.op, o.name, o.ext, o.descr, o.line, f.path FROM opcode_fts "
                                     "JOIN opcode o ON o.id = opcode_fts.rowid LEFT JOIN file f ON f.id = o.file_id "
                                     "WHERE opcode_fts MATCH ? ORDER BY bm25(opcode_fts, 10.0, 4.0, 2.0, 1.0) LIMIT ?",
                                     (fq, want)):
                    ops.append(["opcode", f"{r['op']} {r['name']}", _loc(r["path"], r["line"]), "cleo-ai",
                                _cut(f"[{r['ext']}] {r['descr']}")])
            if kind in (None, "code"):
                for r in con.execute("SELECT c.rowid, c.heading, c.body, c.line, f.path, src.key AS src FROM chunk c "
                                     "LEFT JOIN file f ON f.id = c.file_id LEFT JOIN source src ON src.id = f.source_id "
                                     f"WHERE chunk MATCH ?{sf} ORDER BY bm25(chunk, 5.0, 1.0) LIMIT ?",
                                     [fq, *sp, want]):
                    i, ln = _best_line(r["body"], toks)
                    k = "doc" if (r["path"] or "").endswith(".md") else "code"
                    code.append([k, r["heading"] or (r["path"] or "").rsplit("/", 1)[-1], _loc(r["path"], r["line"] + i),
                                 r["src"], _cut(ln.strip(), 200)])
        except sqlite3.OperationalError as e:
            raise SatkError("BAD_PARAMS", f"bad search query: {e}", hint="use plain words") from None
        head = exact + facts + ops + syms
        if kind is None and code:
            keep_code = min(len(code), max(want // 3, want - len(head)))
        else:
            keep_code = len(code)
        merged = head[:max(0, want - keep_code)] + code[:keep_code]
        total = len(head) + len(code)
        rows = merged[off:off + limit]
        nxt = f"o:{off + limit}" if len(merged) > off + limit else None
        return table(["kind", "name", "loc", "src", "info"], rows, total=total, next=nxt)


def _search_addr(con: sqlite3.Connection, addr: int, source: str | None, limit: int, off: int) -> dict:
    sf, sp = _src_filter(source)
    rows: list[list] = []
    for r in con.execute(f"{_SYM_SELECT} WHERE s.addr = ?{sf} ORDER BY s.kind, s.name", [addr, *sp]):
        rows.append(_sym_row(r))
    rows.sort(key=lambda x: (_KIND_RANK.get(x[0], 99), _SRC_RANK.get(x[3], 99)))
    if source in (None, "facts"):
        for r in con.execute("SELECT key, title, value, status FROM fact WHERE addrs LIKE ? ORDER BY rowid",
                             (f'%"0x{addr:X}"%',)):
            rows.append(["fact", r["key"], None, "facts", _cut(f"{r['title']}: {r['value']} [{r['status']}]")])
    for r in con.execute("SELECT a.line, c.body, c.line AS cline, c.heading, f.path, src.key AS src FROM addr_ref a "
                         "JOIN chunk c ON c.rowid = a.chunk_id LEFT JOIN file f ON f.id = c.file_id "
                         f"LEFT JOIN source src ON src.id = f.source_id WHERE a.addr = ?{sf} "
                         "ORDER BY src.id, f.path, a.line LIMIT 500", [addr, *sp]):
        lines = r["body"].splitlines()
        k = r["line"] - r["cline"]
        text = lines[k] if 0 <= k < len(lines) else ""
        rows.append(["ref", r["heading"] or (r["path"] or "").rsplit("/", 1)[-1], _loc(r["path"], r["line"]), r["src"],
                     _cut(text.strip(), 200)])
    total = len(rows)
    nxt = f"o:{off + limit}" if total > off + limit else None
    return table(["kind", "name", "loc", "src", "info"], rows[off:off + limit], total=total, next=nxt)


# --------------------------------------------------------------------------- sym

_KINDS = ("func", "global", "struct", "vtable", "limit", "const", "enum", "define", "hookpos")


def _enum_rows(con: sqlite3.Connection, owner: str, source: str | None) -> tuple[str | None, list[sqlite3.Row]]:
    """``(owner as stored, member rows in declaration order)`` of an enum type; exact name, then ``::name``."""
    sf, sp = _src_filter(source)
    for cond, arg in (("s.owner = ? COLLATE NOCASE", owner), ("s.owner LIKE ? ESCAPE '\\'",
                                                               "%::" + owner.replace("_", "\\_"))):
        rows = con.execute(f"{_SYM_SELECT} WHERE s.kind = 'enum' AND {cond}{sf}", [arg, *sp]).fetchall()
        if rows:
            best = min({r["src"] for r in rows}, key=lambda k: _SRC_RANK.get(k, 99))
            rows = [r for r in rows if r["src"] == best]
            first = min((r["path"] or "", r["line"] or 0) for r in rows)
            rows = [r for r in rows if (r["path"] or "") == first[0]]   # one declaration (the first file)
            rows.sort(key=lambda r: r["line"] or 0)
            return rows[0]["owner"], rows
    return None, []


def enum_members(owner: str, *, source: str | None = None, path: Path | None = None) -> list[tuple[str, int | None]]:
    """Members of an enum type as ``[(name, value)]`` in declaration order (gta-reversed first);
    ``[]`` when the type is unknown. ``NOT_READY`` when the KB is not built."""
    with closing(connect(path)) as con:
        _own, rows = _enum_rows(con, owner, source)
    out = []
    for r in rows:
        try:
            v: int | None = int(str(r["sig"]), 0)
        except (TypeError, ValueError):
            v = None
        out.append((r["name"], v))
    return out


def func_location(name_or_addr: str | int, *, path: Path | None = None) -> dict | None:
    """Where a function lives in the indexed sources: ``{name, addr, sig, loc, path, line, src, hook,
    reversed}`` (gta-reversed first, then plugin-sdk); ``None`` if no source declares it."""
    with closing(connect(path)) as con:
        if isinstance(name_or_addr, int):
            cond, args = "s.addr = ?", [name_or_addr]
        else:
            a = parse_addr(name_or_addr)
            cond, args = ("s.addr = ?", [a]) if a is not None else ("s.name = ? COLLATE NOCASE", [name_or_addr.strip()])
        where = f"{_SYM_SELECT} WHERE s.kind = 'func' AND src.key IN ('gta-reversed', 'plugin-sdk') AND "
        rows = con.execute(where + cond, args).fetchall()
        if not rows and cond.startswith("s.name"):   # unqualified: Class::Name with exactly one class
            esc = str(args[0]).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            rows = con.execute(where + "s.name LIKE ? ESCAPE '\\'", [f"%::{esc}"]).fetchall()
            if len({r["name"].lower() for r in rows}) != 1:
                rows = []
    if not rows:
        return None
    r = sorted(rows, key=lambda r: (_SRC_RANK.get(r["src"], 99), r["path"] or "", r["line"] or 0))[0]
    flags = json.loads(r["flags"]) if r["flags"] else {}
    return {"name": r["name"], "addr": r["addr"], "sig": r["sig"], "loc": _loc(r["path"], r["line"]),
            "path": r["path"], "line": r["line"], "src": r["src"], "hook": flags.get("hook"),
            "reversed": flags.get("reversed")}


def source_rev(key: str, *, path: Path | None = None) -> dict | None:
    """``{"repo", "ref", "rev"}`` the KB read source ``key`` from (``repo`` as stored: workspace-relative or
    absolute); ``None`` when the source is not in the KB."""
    with closing(connect(path)) as con:
        r = con.execute("SELECT repo, ref, rev FROM source WHERE key = ?", (key,)).fetchone()
    if r is None or not r["repo"] or not r["rev"]:
        return None
    return {"repo": r["repo"], "ref": r["ref"], "rev": r["rev"]}


def sym(name: str, *, kind: str | None = None, source: str | None = None, limit: int = 20,
        cursor: str | None = None, path: Path | None = None) -> dict:
    """Symbols by name (exact, ``::member``, prefix, substring) or by address.

    An enum type name (``eCarPiece``) lists the members of that enum in declaration order.
    """
    off = _offset(cursor)
    if kind is not None and kind not in _KINDS:
        raise SatkError("BAD_PARAMS", f"unknown kind {kind!r}", did_you_mean=list(_KINDS))
    with closing(connect(path)) as con:
        if kind in (None, "enum") and parse_addr(name) is None:
            owner, members = _enum_rows(con, name.strip(), source)
            if members and not con.execute("SELECT 1 FROM sym WHERE name = ? COLLATE NOCASE AND kind != 'enum' "
                                           "LIMIT 1", (name.strip(),)).fetchone():
                page = members[off:off + limit]
                env = table(["name", "value", "loc", "src"],
                            [[r["name"], r["sig"], _loc(r["path"], r["line"]), r["src"]] for r in page],
                            total=len(members), next=f"o:{off + limit}" if len(members) > off + limit else None)
                env.update({"enum": owner, "match": "enum type"})
                return env
        sf, sp = _src_filter(source)
        kf, kp = (" AND s.kind = ?", [kind]) if kind else ("", [])
        addr = parse_addr(name)
        stages: list[tuple[str, str, list]] = []
        if addr is not None:
            stages.append(("address", "s.addr = ?", [addr]))
        else:
            n = name.strip()
            esc = n.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            stages += [("exact", "s.name = ? COLLATE NOCASE", [n]),
                       ("member", "s.name LIKE ? ESCAPE '\\'", [f"%::{esc}"]),
                       ("prefix", "s.name LIKE ? ESCAPE '\\'", [f"{esc}%"]),
                       ("substring", "s.name LIKE ? ESCAPE '\\'", [f"%{esc}%"])]
        rows: list[sqlite3.Row] = []
        match = None
        for label, cond, params in stages:
            rows = con.execute(f"{_SYM_SELECT} WHERE {cond}{kf}{sf} LIMIT 5000", [*params, *kp, *sp]).fetchall()
            if rows:
                match = label
                break
        rows = sorted(rows, key=lambda r: (_KIND_RANK.get(r["kind"], 99), _SRC_RANK.get(r["src"], 99),
                                           r["name"].lower(), r["path"] or "", r["line"] or 0))
        out = []
        for r in rows[off:off + limit]:
            flags = json.loads(r["flags"]) if r["flags"] else {}
            info = []
            if r["doc"]:
                info.append(r["doc"])
            if flags.get("reversed") is False:
                info.append("not reversed")
            if flags.get("locked"):
                info.append("locked")
            if flags.get("decl"):
                info.append(f"decl {flags['decl']}")
            if flags.get("hook") is False:
                info.append("no hook")
            out.append([r["kind"], r["name"], _hx(r["addr"]), _cut(r["sig"], 200), _loc(r["path"], r["line"]), r["src"],
                        _cut("; ".join(info)) or None])
        total = len(rows)
        env = table(["kind", "name", "addr", "value", "loc", "src", "info"], out, total=total,
                    next=f"o:{off + limit}" if total > off + limit else None)
        if match:
            env["match"] = match
        if not rows:
            sug = [r[0] for r in con.execute("SELECT DISTINCT s.name FROM sym_fts JOIN sym s ON s.id = sym_fts.rowid "
                                             "WHERE sym_fts MATCH ? LIMIT 5", (fts_query(name) or '""',))] \
                if fts_query(name) else []
            raise SatkError("NOT_FOUND", f"no symbol {name!r}", hint=f'satk kb search "{name}"', did_you_mean=sug)
        return env


# --------------------------------------------------------------------------- struct


def _pick_struct(con: sqlite3.Connection, name: str, source: str | None) -> sqlite3.Row | None:
    names = [name]
    if not name.endswith("SAInterface"):
        names.append(name + "SAInterface")
    sf, sp = _src_filter(source)
    rows = con.execute("SELECT st.*, f.path AS path, src.key AS src FROM struct st LEFT JOIN file f ON f.id = st.file_id "
                       "LEFT JOIN source src ON src.id = st.source_id "
                       f"WHERE (st.name = ? COLLATE NOCASE OR st.name LIKE ? ESCAPE '\\' OR st.name = ? COLLATE NOCASE){sf}",
                       [names[0], "%::" + names[0].replace("_", "\\_"), names[-1], *sp]).fetchall()
    if not rows:
        return None
    verdict = {"ok": 0, "unverified": 1, "partial": 2, "mismatch": 3}

    def key(r):
        exact = r["name"].lower() == name.lower() or r["name"].lower().endswith("::" + name.lower())
        return (not exact, _SRC_RANK.get(r["src"], 99), verdict.get(r["layout"], 9), -(r["nfields"] or 0), r["id"])

    return sorted(rows, key=key)[0]


def class_bases(name: str, *, depth: int = 8, path: Path | None = None) -> list[str]:
    """Base classes of ``name``, nearest first (``CAutomobile`` -> ``CVehicle, CPhysical, CEntity, CPlaceable``).

    First base only per level (single-inheritance chain of the engine classes); ``[]`` without a hit.
    """
    out: list[str] = []
    with closing(connect(path)) as con:
        cur = name
        while len(out) < depth:
            st = _pick_struct(con, cur, None)
            if st is None or not st["bases"]:
                break
            nxt = st["bases"].split(",")[0].strip()
            if not nxt or nxt in out or nxt == name:
                break
            out.append(nxt)
            cur = nxt
    return out


def _fields(con: sqlite3.Connection, sid: int) -> list[sqlite3.Row]:
    return con.execute("SELECT * FROM field WHERE struct_id = ? ORDER BY idx", (sid,)).fetchall()


def _flatten(con: sqlite3.Connection, st: sqlite3.Row, depth: int = 0) -> list[dict]:
    """Fields of ``st`` with the fields of its bases first (same source)."""
    out: list[dict] = []
    if depth < 8 and st["bases"]:
        for b in [x.strip() for x in st["bases"].split(",") if x.strip()]:
            bs = _pick_struct(con, b, _src_key(con, st["source_id"]))
            if bs is not None:
                for f in _flatten(con, bs, depth + 1):
                    f.setdefault("from", bs["name"])
                    out.append(f)
    for f in _fields(con, st["id"]):
        out.append(dict(f))
    return out


def _src_key(con: sqlite3.Connection, sid: int) -> str | None:
    r = con.execute("SELECT key FROM source WHERE id = ?", (sid,)).fetchone()
    return r[0] if r else None


def _field_rows(fields: list[dict], bits: bool) -> list[list]:
    rows: list[list] = []
    i = 0
    while i < len(fields):
        f = fields[i]
        if f.get("bits") is not None and not bits:
            j = i
            names = []
            while j < len(fields) and fields[j].get("bits") is not None and fields[j]["off"] == f["off"]:
                names.append(fields[j]["name"])
                j += 1
            nbits = sum(fields[k]["bits"] or 0 for k in range(i, j))
            via = f["src"]
            rows.append([_hx(f["off"]), _cut(", ".join(names), 120), f"bits:{nbits}", f["size"], via])
            i = j
            continue
        t = f["type"] + (f":{f['bits']}" if f.get("bits") is not None else "")
        off = _hx(f["off"]) + (f".{f['bit']}" if f.get("bits") is not None and f.get("bit") else "") if f["off"] is not None else "?"
        via = f["src"] + (f" ({f['note']})" if f.get("note") else "")
        if f.get("from"):
            via += f" <{f['from']}>"
        rows.append([off, f["name"], t, f["size"], via])
        i += 1
    return rows


def struct(name: str, *, source: str | None = None, at: str | None = None, match: str | None = None,
           inherited: bool = False, bits: bool = False, limit: int = 100, path: Path | None = None) -> dict:
    """Size and fields of a class/struct, by source; ``at`` finds the field at an offset."""
    with closing(connect(path)) as con:
        st = _pick_struct(con, name, source)
        if st is None:
            sug = [r[0] for r in con.execute("SELECT DISTINCT name FROM struct WHERE name LIKE ? LIMIT 8",
                                             (f"%{name}%",))]
            raise SatkError("NOT_FOUND", f"no struct {name!r}" + (f" in {source}" if source else ""),
                            hint=f"satk kb sym {name} --kind struct", did_you_mean=sug)
        sizes = {}
        for r in con.execute("SELECT DISTINCT src.key AS k, s.sig, s.name FROM sym s JOIN file f ON f.id = s.file_id "
                             "JOIN source src ON src.id = f.source_id WHERE s.kind = 'struct' AND "
                             "(s.name = ? COLLATE NOCASE OR s.name = ? COLLATE NOCASE)",
                             (st["name"].rsplit("::", 1)[-1], st["name"].rsplit("::", 1)[-1].removesuffix("SAInterface")
                              + "SAInterface")):
            sizes.setdefault(r["k"], r["sig"] if r["name"] == st["name"].rsplit("::", 1)[-1] else f"{r['sig']} ({r['name']})")
        warn = []
        if at is not None:
            try:
                want = int(str(at), 0)
            except ValueError:
                raise SatkError("BAD_PARAMS", f"bad offset {at!r}", hint="--at 0x540") from None
            flds = _flatten(con, st)
            hits = [f for f in flds if f["off"] is not None and f["size"] and f["off"] <= want < f["off"] + f["size"]]
            hits = _descend(con, hits, want, st["source_id"])
            rows = _field_rows(hits, True)
        else:
            flds = _flatten(con, st) if inherited else [dict(f) for f in _fields(con, st["id"])]
            if match:
                flds = [f for f in flds if match.lower() in f["name"].lower()]
            rows = _field_rows(flds, bits)
        total = len(rows)
        shown = rows[:limit]
        if total > limit:
            warn.append(f"TRUNCATED: {total} fields, showing {limit} (--limit, --match, --at)")
        if st["layout"] == "mismatch":
            warn.append(f"LAYOUT: computed size 0x{st['calc'] or 0:X} != asserted 0x{st['size'] or 0:X}; "
                        "offsets marked calc may be off")
        elif st["layout"] == "partial":
            warn.append("LAYOUT: some member types have unknown size; offsets after them come from asserts only")
        env = obj(None, name=st["name"], kind=st["kind"], src=st["src"], loc=_loc(st["path"], st["line"]),
                  size=_hx(st["size"]), calc=_hx(st["calc"]), layout=st["layout"], bases=st["bases"],
                  vptr=bool(st["vptr"]) or None, sizes=sizes or None,
                  fields={"cols": ["off", "name", "type", "size", "via"], "rows": shown, "total": total})
        if warn:
            env["warn"] = warn
        return env


def _descend(con: sqlite3.Connection, hits: list[dict], want: int, source_id: int, depth: int = 0) -> list[dict]:
    """Expand a hit whose type is a struct (or an array of structs) into the inner field at ``want``."""
    if depth > 4:
        return hits
    out = []
    src = _src_key(con, source_id)
    for f in hits:
        out.append(f)
        t = (f["type"] or "").strip()
        if not t or t.endswith(("*", "&")) or f.get("bits") is not None:
            continue
        m = re.match(r"^(?:std::)?array<(.+),\s*[^,<>]+>$", t)
        is_array = bool(m) or t.endswith("]")
        elem = m.group(1).strip() if m else re.sub(r"\[.*$", "", t).strip()
        if elem.endswith(("*", "&")):
            continue
        inner = _pick_struct(con, elem, src)
        if inner is None or not (inner["size"] or inner["calc"]):
            continue
        esize = inner["size"] or inner["calc"]
        idx, rel = divmod(want - f["off"], esize) if is_array else (0, want - f["off"])
        base = f["off"] + idx * esize
        prefix = f"{f['name']}[{idx}]" if is_array else f["name"]
        sub = [g for g in _flatten(con, inner) if g["off"] is not None and g["size"] and g["off"] <= rel < g["off"] + g["size"]]
        for g in _descend(con, sub, rel, inner["source_id"], depth + 1):
            g = dict(g)
            g["name"] = f"{prefix}.{g['name']}"
            g["off"] = base + g["off"]
            out.append(g)
    return out


# --------------------------------------------------------------------------- opcode


def _op_obj(con: sqlite3.Connection, r: sqlite3.Row) -> dict:
    hfile = None
    if r["handler_file_id"]:
        x = con.execute("SELECT path FROM file WHERE id = ?", (r["handler_file_id"],)).fetchone()
        hfile = x[0] if x else None
    details = r["details"]
    return obj(None, op=r["op"], name=r["name"], ext=r["ext"],
               cls=".".join(x for x in (r["class"], r["member"]) if x) or None, flags=r["flags"] or None,
               params=r["nparams"], descr=r["descr"],
               input=json.loads(r["input"]) if r["input"] else None,
               output=json.loads(r["output"]) if r["output"] else None,
               details=_cut(details, 700) if details else None,
               loc=_loc(r["path"], r["line"]),
               handler=f"{r['handler']} ({_loc(hfile, r['handler_line'])})" if r["handler"] else None)


def opcode(q: str, *, ext: str | None = None, limit: int = 20, path: Path | None = None) -> dict:
    """Opcode by id (``0A8C``), by name (``WRITE_MEMORY``) or by words (``write memory``)."""
    with closing(connect(path)) as con:
        sel = ("SELECT o.*, f.path AS path FROM opcode o LEFT JOIN file f ON f.id = o.file_id")
        ef, ep = (" AND o.ext = ? COLLATE NOCASE", [ext]) if ext else ("", [])
        qs = q.strip()
        rows: list[sqlite3.Row] = []
        if re.fullmatch(r"(?:0[xX])?[0-9A-Fa-f]{1,4}", qs) and not re.fullmatch(r"[A-Za-z]+", qs):
            op = qs[2:] if qs.lower().startswith("0x") else qs
            rows = con.execute(f"{sel} WHERE o.op = ?{ef} ORDER BY o.id", [op.upper().zfill(4), *ep]).fetchall()
        if not rows:
            rows = con.execute(f"{sel} WHERE o.name = ? COLLATE NOCASE{ef} ORDER BY o.id",
                               [qs.replace(" ", "_"), *ep]).fetchall()
        if len(rows) == 1:
            return _op_obj(con, rows[0])
        if not rows:
            fq = fts_query(qs)
            if fq:
                rows = con.execute(f"{sel} JOIN opcode_fts ON opcode_fts.rowid = o.id WHERE opcode_fts MATCH ?{ef} "
                                   "ORDER BY bm25(opcode_fts, 10.0, 4.0, 2.0, 1.0) LIMIT ?", [fq, *ep, 200]).fetchall()
        if not rows:
            raise SatkError("NOT_FOUND", f"no opcode {q!r}", hint='satk kb opcode "write memory"')
        out = [[r["op"], r["name"], r["ext"], ".".join(x for x in (r["class"], r["member"]) if x) or None,
                r["nparams"], _cut(r["descr"], 120)] for r in rows[:limit]]
        return table(["op", "name", "ext", "class", "params", "descr"], out, total=len(rows))


# --------------------------------------------------------------------------- fact


_ASSET_STATUS = "tested"


def _asset_facts() -> list[dict]:
    from .facts import ASSET_FACTS

    return ASSET_FACTS


def _asset_text(f: dict) -> str:
    return " ".join([f["key"], f["title"], f["value"], f.get("note") or "", *f.get("refs", [])]).lower()


def _asset_match(q: str) -> list[dict]:
    """Authoring facts whose key/title/value/refs hold every word of ``q`` (key prefix first)."""
    q = q.strip()
    facts = _asset_facts()
    pre = [f for f in facts if f["key"].lower() == q.lower() or f["key"].lower().startswith(q.lower().rstrip(".") + ".")]
    if pre:
        return pre
    words = [t.lower() for t in _TOKEN.findall(q)]
    return [f for f in facts if words and all(w in _asset_text(f) for w in words)]


def _asset_obj(f: dict) -> dict:
    from .facts import CONFIDENCE

    return obj(None, key=f["key"], title=f["title"], value=f["value"], refs=f.get("refs") or None,
               sources=f.get("sources") or None, confidence=CONFIDENCE.get(f["conf"], f["conf"]),
               status=_ASSET_STATUS, note=f.get("note"), verify=f.get("verify") or None,
               checks={"cols": ["check"], "rows": [[" ".join(str(x) for x in c)] for c in f["checks"]]}
               if f.get("checks") else None)


def _asset_rows(facts: list[dict]) -> list[list]:
    from .facts import CONFIDENCE

    return [[f["key"], f["title"], _cut(f["value"], 140), CONFIDENCE.get(f["conf"], f["conf"]), _ASSET_STATUS]
            for f in facts]


def fact(key: str | None = None, *, status_filter: str | None = None, limit: int = 50,
         path: Path | None = None) -> dict:
    """One fact (exact key) or a table of facts (topic prefix, words, or all).

    Engine facts come from the built KB. Authoring facts (``asset.*``, :data:`satk.kb.facts.ASSET_FACTS`,
    status ``tested``) are served from the package: ``asset`` lists them, words match them too, and the
    full list names how many there are (``asset_facts``).
    """
    if key and key.strip().lower().startswith("asset"):
        hits = _asset_match(key)
        if len(hits) == 1 and hits[0]["key"].lower() == key.strip().lower():
            return _asset_obj(hits[0])
        if hits and status_filter in (None, _ASSET_STATUS):
            return table(["key", "title", "value", "confidence", "status"], _asset_rows(hits)[:limit], total=len(hits))
    if status_filter == _ASSET_STATUS:
        hits = _asset_match(key) if key else _asset_facts()
        return table(["key", "title", "value", "confidence", "status"], _asset_rows(hits)[:limit], total=len(hits))
    try:
        return _db_fact(key, status_filter=status_filter, limit=limit, path=path)
    except SatkError as e:
        if e.code != "NOT_FOUND" or not key:
            raise
        hits = _asset_match(key)
        if not hits:
            raise
        return table(["key", "title", "value", "confidence", "status"], _asset_rows(hits)[:limit], total=len(hits))


def _code_facts() -> dict[str, dict]:
    """Engine facts of this package by key: their text wins over a KB built before a wording change
    (the facts were translated to English; ``status``/``checks`` still come from the build)."""
    from .facts import FACTS

    return {f["key"]: f for f in FACTS}


def _db_fact(key: str | None, *, status_filter: str | None, limit: int, path: Path | None) -> dict:
    with closing(connect(path)) as con:
        if key:
            r = con.execute("SELECT * FROM fact WHERE key = ? COLLATE NOCASE", (key,)).fetchone()
            if r is not None:
                checks = json.loads(r["checks"]) if r["checks"] else []
                cur = _code_facts().get(r["key"]) or {}
                return obj(None, key=r["key"], title=cur.get("title") or r["title"], value=cur.get("value") or r["value"],
                           addrs=json.loads(r["addrs"]) if r["addrs"] else None,
                           refs=json.loads(r["refs"]) if r["refs"] else None,
                           sources=json.loads(r["sources"]) if r["sources"] else None,
                           confidence=r["confidence"], status=r["status"], note=cur.get("note") or r["note"],
                           checks={"cols": ["check", "expected", "got", "ok"], "rows": checks} if checks else None)
        where, params = [], []
        if key:
            rows = con.execute("SELECT key FROM fact WHERE key LIKE ? OR topic = ? COLLATE NOCASE",
                               (key.rstrip(".") + "%", key.rstrip("."))).fetchall()
            keys = [x[0] for x in rows]
            if not keys:
                fq = fts_query(key)
                keys = [x[0] for x in con.execute("SELECT key FROM fact_fts WHERE fact_fts MATCH ? ORDER BY bm25(fact_fts)",
                                                  (fq,))] if fq else []
            if not keys:
                raise SatkError("NOT_FOUND", f"no fact {key!r}", hint="satk kb fact (lists all keys)")
            where.append(f"key IN ({','.join('?' * len(keys))})")
            params += keys
        if status_filter:
            where.append("status = ?")
            params.append(status_filter)
        sql = "SELECT key, title, value, confidence, status FROM fact" + (" WHERE " + " AND ".join(where) if where else "") \
            + " ORDER BY rowid"
        rows = con.execute(sql, params).fetchall()
        code = _code_facts()
        out = [[r["key"], (code.get(r["key"]) or {}).get("title") or r["title"],
                _cut((code.get(r["key"]) or {}).get("value") or r["value"], 140), r["confidence"], r["status"]]
               for r in rows[:limit]]
        env = table(["key", "title", "value", "confidence", "status"], out, total=len(rows))
        if not key and not status_filter:
            env["asset_facts"] = len(_asset_facts())
            env["hint"] = "authoring facts (asset.*): satk kb fact asset"
        return env


# --------------------------------------------------------------------------- status


def status(*, deep: bool = False, path: Path | None = None) -> dict:
    """Built?, counts, sources with revisions (``deep``: sources whose revision moved since the build)."""
    p = path or kb_path()
    if not p.is_file():
        return {"built": False, "path": p.as_posix(), "hint": "satk kb build"}
    with closing(connect(p)) as con:
        meta = {r[0]: r[1] for r in con.execute("SELECT key, value FROM meta")}
        stats = json.loads(meta.get("stats") or "{}")
        srcs = [dict(r) for r in con.execute("SELECT key, repo, ref, rev, files FROM source ORDER BY id")]
    out = {"built": True, "path": p.as_posix(), "built_at": meta.get("built_at"), "bytes": p.stat().st_size,
           "counts": stats.get("counts"), "facts": {k: v for k, v in (stats.get("facts") or {}).items() if k != "facts"},
           "sources": {s["key"]: (s["rev"] or "")[:9] or None for s in srcs}}
    if deep:
        out["stale"] = _stale(srcs)
    return out


def _stale(srcs: list[dict]) -> list[str]:
    from ..core.paths import cfg
    from ..re.gitsrc import GitTree

    out = []
    ws = cfg().paths.workspace
    for s in srcs:
        if not s.get("ref") or not s.get("repo"):
            continue
        repo = Path(s["repo"])
        if not repo.is_absolute():
            repo = ws / repo
        try:
            cur = GitTree(repo, s["ref"]).rev
        except SatkError:
            continue
        if cur != s["rev"]:
            out.append(f"{s['key']}: {(s['rev'] or '')[:9]} -> {cur[:9]}")
    return out
