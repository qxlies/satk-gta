"""Read side of the scripting API references: ``satk kb mta`` and ``satk kb native``.

Answers come from the ``mta_*`` and ``pawn_sym`` tables of ``work/kb/kb.sqlite`` (see
:mod:`.scriptapi_build`). A name gives one object (signature, side, OOP names, accepted enum strings,
implementation ``file:line``); words give a table; a typo gives ``NOT_FOUND`` with ``did_you_mean``.
"""

from __future__ import annotations

import difflib
import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path

from ..core.envelope import obj, table
from ..core.errors import SatkError
from .query import connect
from .scriptapi_build import BUILTIN_PATH

__all__ = ["mta", "native", "overview"]

_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z0-9])|[A-Z]?[a-z]+|[A-Z]+|\d+")
_SIDE_ORDER = {"client": 0, "server": 1}
_HINT_BUILD = "satk kb build"


def _open(path: Path | None, table_name: str) -> sqlite3.Connection:
    con = connect(path)
    if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table_name,)).fetchone() is None:
        con.close()
        raise SatkError("NOT_READY", "the knowledge base has no scripting API tables (built by an older satk)",
                        hint=_HINT_BUILD)
    return con


def _words(text: str) -> list[str]:
    out = []
    for tok in re.findall(r"[A-Za-z0-9_]+", text):
        parts = [p.lower() for p in _CAMEL.findall(tok)] or [tok.lower()]
        out += [p for p in (parts if len(parts) > 1 else [tok.lower()]) if p]
    return out


def _cut(s: str | None, n: int = 300) -> str | None:
    if s is None:
        return None
    return s if len(s) <= n else s[:n - 3] + "..."


def _suggest(q: str, names: list[str], n: int = 5) -> list[str]:
    low = {}
    for x in names:
        low.setdefault(x.lower(), x)
    ql = q.lower()
    hits = difflib.get_close_matches(ql, list(low), n=n, cutoff=0.72)
    if len(hits) < n:
        hits += [k for k in sorted(low, key=len) if ql in k and k not in hits][:n - len(hits)]
    return [low[h] for h in hits]


def _render(name: str, variant: list, ret: str | None) -> str:
    parts = []
    for a_name, a_type, a_opt, a_def in variant:
        if a_type == "...":
            parts.append("...")
            continue
        s = f"{a_type} {a_name}".strip() + (f" = {a_def}" if a_def is not None else "")
        parts.append(f"[{s}]" if a_opt else s)
    return f"{ret + ' ' if ret else ''}{name}({', '.join(parts)})"


def _loc(con: sqlite3.Connection, file_id: int | None, line: int | None) -> str | None:
    if not file_id:
        return None
    r = con.execute("SELECT path FROM file WHERE id = ?", (file_id,)).fetchone()
    if r is None:
        return None
    return f"{r[0]}:{line}" if line else r[0]


def _repo(con: sqlite3.Connection, key: str) -> str | None:
    r = con.execute("SELECT repo FROM source WHERE key = ?", (key,)).fetchone()
    return r[0] if r else None


# --------------------------------------------------------------------------- MTA


def _oop_names(con: sqlite3.Connection, name: str) -> list[str]:
    static = {r[0] for r in con.execute("SELECT DISTINCT name FROM mta_class WHERE static = 1")}
    out: list[str] = []
    for r in con.execute("SELECT cls, member, kind, func, setter FROM mta_oop WHERE func = ? OR setter = ? "
                         "ORDER BY cls, kind, member", (name, name)):
        if r["kind"] == "method":
            s = f"{r['cls']}.{r['member']}" if r["cls"] in static else f"{r['cls']}:{r['member']}"
        else:
            how = "/".join(x for x, f in (("get", r["func"]), ("set", r["setter"])) if f == name)
            s = f"{r['cls']}.{r['member']} ({how})"
        if s not in out:
            out.append(s)
    return out


def _enum_values(con: sqlite3.Connection, ctypes: list[str], side: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for ct in ctypes:
        short = ct.rsplit("::", 1)[-1] if not ct.endswith("::Enum") else ct
        r = con.execute("SELECT name, vals FROM mta_enum WHERE (ctype = ? OR ctype = ? OR ctype LIKE ?) "
                        "ORDER BY side = ? DESC LIMIT 1", (ct, short, f"%::{short}", side)).fetchone()
        if r is None:
            continue
        vals = json.loads(r["vals"])
        s = "|".join(vals)
        out[r["name"]] = _cut(s, 240) + (f" ({len(vals)} values)" if len(s) > 240 else "")
    return out


def _flags_text(flags: dict, origin: str, parser: str) -> list[str]:
    notes = []
    if origin == "neon":
        notes.append("Neon fork only (not in MTA upstream)")
    if flags.get("compat"):
        notes.append("deprecated alias from the compatibility table")
    if flags.get("restricted"):
        notes.append("ACL-restricted by default")
    if flags.get("debug"):
        notes.append("debug builds only")
    if parser == "argreader" and flags.get("approx"):
        notes.append("approximate arguments (CScriptArgReader with branches): check doc and the source")
    elif parser == "raw":
        notes.append("arguments are read with raw lua_* calls: see the source")
    if flags.get("overloads"):
        notes.append(f"{flags['overloads']} overloads")
    return notes


_PARSERS = {"argparser": "ArgumentParser", "argreader": "CScriptArgReader", "raw": "raw lua_* calls"}


def _impl_text(impl: str | None, parser: str) -> str | None:
    if not impl:
        return None
    m = re.fullmatch(r"ArgumentParser(Warn)?\s*<(.*)>", impl.strip(), re.S)
    if m:
        parts = [x.strip() for x in m.group(2).split(",")]
        impl = ", ".join(parts[1:] if m.group(1) else parts)
    return f"{impl} ({_PARSERS.get(parser, parser)})"


def _func_obj(con: sqlite3.Connection, rows: list[sqlite3.Row], via: str | None = None) -> dict:
    rows = sorted(rows, key=lambda r: _SIDE_ORDER.get(r["side"], 9))
    per: list[dict] = []
    for r in rows:
        variants = json.loads(r["variants"]) if r["variants"] else []
        sigs = [_render(r["name"], v, r["ret"]) for v in variants] or [f"{r['ret'] + ' ' if r['ret'] else ''}"
                                                                         f"{r['name']}(?)"]
        flags = json.loads(r["flags"]) if r["flags"] else {}
        ctypes = json.loads(r["enums"]) if r["enums"] else []
        src = "mta-lua-neon" if r["origin"] == "neon" else "mta-lua"
        per.append({"side": r["side"], "sig": sigs[0] if len(sigs) == 1 else sigs, "doc": r["doc"],
                    "enums": _enum_values(con, ctypes, r["side"]),
                    "impl": _impl_text(r["impl"], r["parser"]),
                    "loc": _loc(con, r["impl_file_id"], r["impl_line"]) or _loc(con, r["file_id"], r["line"]),
                    "registered": _loc(con, r["file_id"], r["line"]),
                    "notes": _flags_text(flags, r["origin"], r["parser"]), "src": src})
    for p in per:                       # the registration line only when the implementation was not found
        if p["loc"] != p["registered"]:
            p.pop("registered")
    first = per[0]
    up = {r["side"] for r in rows if r["origin"] != "neon"} or {r["side"] for r in rows}
    side = next(iter(up)) if len(up) == 1 else "shared"
    out = {"name": rows[0]["name"], "side": side, **{k: v for k, v in first.items() if k != "side"}}
    out.pop("registered", None)
    if len(per) > 1:
        other = per[1]
        diff = {k: v for k, v in other.items() if k not in ("side", "src", "registered") and v != first.get(k) and v}
        if diff:
            out[other["side"]] = diff
    out["oop"] = _oop_names(con, rows[0]["name"])
    if via:
        out["via"] = via
    out["repo"] = _repo(con, first["src"])
    return obj(None, **out)


def _event_obj(con: sqlite3.Connection, rows: list[sqlite3.Row]) -> dict:
    rows = sorted(rows, key=lambda r: _SIDE_ORDER.get(r["side"], 9))
    r = rows[0]
    return obj(None, name=r["name"], kind="event", side=r["side"] if len(rows) == 1 else "shared",
               params=r["params"] or "(none)", loc=_loc(con, r["file_id"], r["line"]),
               notes=["Neon fork only (not in MTA upstream)"] if r["origin"] == "neon" else None,
               hint=f'addEventHandler("{r["name"]}", root, function({r["params"]}) ... end)')


def _class_obj(con: sqlite3.Connection, name: str, limit: int) -> dict:
    cls = con.execute("SELECT * FROM mta_class WHERE name = ? COLLATE NOCASE ORDER BY side", (name,)).fetchall()
    cname = cls[0]["name"]
    members = con.execute("SELECT member, kind, group_concat(DISTINCT side) AS sides, func, setter, impl FROM mta_oop "
                          "WHERE cls = ? GROUP BY member, kind ORDER BY kind, member", (cname,)).fetchall()
    rows = []
    for m in members:
        target = m["func"] or ""
        if m["kind"] == "var":
            target = " / ".join(x for x in (f"get {m['func']}" if m["func"] else "",
                                            f"set {m['setter']}" if m["setter"] else "") if x)
        if not target and m["impl"]:
            target = f"(OOP only: {m['impl']})"
        sides = m["sides"].split(",")
        rows.append([m["member"], m["kind"], "shared" if len(sides) > 1 else sides[0], target])
    sides = sorted({c["side"] for c in cls})
    return obj(None, name=cname, kind="class", side=sides[0] if len(sides) == 1 else "shared",
               parent=cls[0]["parent"], static=bool(cls[0]["static"]) or None,
               loc=_loc(con, cls[0]["file_id"], cls[0]["line"]),
               members={"cols": ["member", "kind", "side", "function"], "rows": rows[:limit], "total": len(rows)},
               warn=[f"TRUNCATED: {len(rows)} members, showing {limit} (--limit)"] if len(rows) > limit else None)


def overview(path: Path | None = None) -> dict:
    """Counts of the MTA and Pawn tables."""
    with closing(_open(path, "mta_func")) as con:
        def one(sql: str, *a):
            return con.execute(sql, a).fetchone()[0]

        names = {r[0]: r[1] for r in con.execute(
            "SELECT name, group_concat(DISTINCT side) FROM mta_func WHERE origin = 'mta' GROUP BY name")}
        sides = [v.split(",") for v in names.values()]
        out = {
            "functions": {"total": len(names), "client": sum(1 for s in sides if s == ["client"]),
                          "server": sum(1 for s in sides if s == ["server"]),
                          "shared": sum(1 for s in sides if len(s) > 1),
                          "neon_only": one("SELECT COUNT(DISTINCT name) FROM mta_func WHERE origin = 'neon'")},
            "events": one("SELECT COUNT(DISTINCT name) FROM mta_event"),
            "classes": one("SELECT COUNT(DISTINCT name) FROM mta_class"),
            "enums": one("SELECT COUNT(DISTINCT ctype) FROM mta_enum"),
            "natives": one("SELECT COUNT(*) FROM pawn_sym WHERE kind = 'native'"),
            "callbacks": one("SELECT COUNT(*) FROM pawn_sym WHERE kind = 'callback'"),
            "pawn_consts": one("SELECT COUNT(*) FROM pawn_sym WHERE kind = 'const'"),
            "sources": {r[0]: r[1] for r in con.execute(
                "SELECT key, repo FROM source WHERE key IN ('mta-lua', 'mta-lua-neon', 'pawn')")},
        }
        return obj(None, **out, hint='satk kb mta engineRequestModel | "vehicle handling" | Vehicle | --event '
                                     'onClientRender; satk kb native SetObjectMaterial')


def _search_mta(con: sqlite3.Connection, q: str, *, event: bool, side: str | None, limit: int) -> list[list]:
    words = _words(q)
    if not words:
        return []
    out: list[tuple] = []
    if not event:
        for r in con.execute("SELECT name, group_concat(DISTINCT side) AS sides, min(origin) AS origin, "
                             "max(variants) AS variants, max(ret) AS ret FROM mta_func GROUP BY name"):
            low = r["name"].lower()
            if all(w in low for w in words):
                sides = sorted(r["sides"].split(","))
                sd = "shared" if len(sides) > 1 else sides[0]
                if side and sd not in (side, "shared"):
                    continue
                v = json.loads(r["variants"])[0] if r["variants"] else []
                out.append((not low.startswith(words[0]), len(low), "function", r["name"], sd,
                            _cut(_render(r["name"], v, r["ret"]), 200)))
    for r in con.execute("SELECT name, group_concat(DISTINCT side) AS sides, max(params) AS params FROM mta_event "
                         "GROUP BY name"):
        low = r["name"].lower()
        if all(w in low for w in words):
            sides = sorted(r["sides"].split(","))
            sd = "shared" if len(sides) > 1 else sides[0]
            if side and sd not in (side, "shared"):
                continue
            out.append((not low.startswith(("on" + words[0], words[0])), len(low), "event", r["name"], sd,
                        r["params"] or "(none)"))
    out.sort()
    return [list(x[2:]) for x in out]


def mta(query: str | None, *, event: bool = False, side: str | None = None, limit: int = 20,
        path: Path | None = None) -> dict:
    """MTA Lua function / OOP member / class / event / enum by name, or a table for words."""
    if side is not None and side not in ("client", "server", "shared"):
        raise SatkError("BAD_PARAMS", f"unknown side {side!r}", did_you_mean=["client", "server", "shared"])
    q = (query or "").strip()
    q = re.sub(r"\s*\(\s*\)?\s*$", "", q)
    if not q and not event:
        return overview(path)
    with closing(_open(path, "mta_func")) as con:
        con.row_factory = sqlite3.Row
        if not q:
            rows = con.execute("SELECT name, group_concat(DISTINCT side) AS sides, max(params) AS params FROM mta_event "
                               "GROUP BY name ORDER BY name").fetchall()
            data = [[r["name"], "shared" if "," in r["sides"] else r["sides"], r["params"] or "(none)"] for r in rows
                    if not side or side in r["sides"] or side == "shared" and "," in r["sides"]]
            return table(["event", "side", "params"], data[:limit], total=len(data),
                         warn=[f"TRUNCATED: {len(data)} events, showing {limit} (a name or words narrow it)"]
                         if len(data) > limit else ())
        if not event:
            rows = con.execute("SELECT * FROM mta_func WHERE name = ? COLLATE NOCASE", (q,)).fetchall()
            if side and rows:
                rows = [r for r in rows if side == "shared" or r["side"] == side] or rows
            if rows:
                return _func_obj(con, rows)
            m = re.fullmatch(r"(\w+)\s*[:.]\s*(\w+)", q)
            if m:
                o = con.execute("SELECT * FROM mta_oop WHERE cls = ? COLLATE NOCASE AND member = ? COLLATE NOCASE "
                                "ORDER BY kind = 'method' DESC, side", (m.group(1), m.group(2))).fetchall()
                if o:
                    fn = o[0]["func"] or o[0]["setter"]
                    rows = con.execute("SELECT * FROM mta_func WHERE name = ?", (fn,)).fetchall() if fn else []
                    if rows:
                        return _func_obj(con, rows, via=f"{o[0]['cls']}.{o[0]['member']} ({o[0]['kind']})")
                    return obj(None, name=f"{o[0]['cls']}.{o[0]['member']}", kind=f"OOP {o[0]['kind']}",
                               side=o[0]["side"], impl=o[0]["impl"], loc=_loc(con, o[0]["file_id"], o[0]["line"]),
                               notes=["OOP-only implementation: no global function of its own"])
            if con.execute("SELECT 1 FROM mta_class WHERE name = ? COLLATE NOCASE", (q,)).fetchone():
                return _class_obj(con, q, limit)
        ev = con.execute("SELECT * FROM mta_event WHERE name = ? COLLATE NOCASE", (q,)).fetchall()
        if ev:
            return _event_obj(con, ev)
        if not event:
            en = con.execute("SELECT * FROM mta_enum WHERE name = ? COLLATE NOCASE OR ctype = ? OR ctype LIKE ? "
                             "ORDER BY side", (q, q, f"%::{q}")).fetchall()
            if en:
                vals = json.loads(en[0]["vals"])
                return obj(None, name=en[0]["name"], kind="enum", ctype=en[0]["ctype"],
                           side=en[0]["side"] if len({e["side"] for e in en}) == 1 else "shared",
                           values=vals, loc=_loc(con, en[0]["file_id"], en[0]["line"]))
        rows = _search_mta(con, q, event=event, side=side, limit=limit)
        if rows:
            return table(["kind", "name", "side", "sig"], rows[:limit], total=len(rows))
        names = [r[0] for r in con.execute("SELECT DISTINCT name FROM mta_event")]
        if not event:
            names += [r[0] for r in con.execute("SELECT DISTINCT name FROM mta_func")]
            names += [r[0] for r in con.execute("SELECT DISTINCT name FROM mta_class")]
        what = "event" if event else "MTA function, class or event"
        raise SatkError("NOT_FOUND", f"no {what} {q!r}", did_you_mean=_suggest(q, names),
                        hint='satk kb mta "words of the name" (all must match), e.g. satk kb mta "vehicle handling"')


# --------------------------------------------------------------------------- Pawn

_INC_FIRST = re.compile(r"^(?:open\.mp|omp_\w+|a_(?!npc)\w+)$")


def _inc_rank(inc: str | None) -> int:
    """Stock SA-MP/open.mp includes first, ``a_npc`` (NPC scripts) after them, then plugins and libraries."""
    i = inc or ""
    return 0 if _INC_FIRST.match(i) else (1 if i == "a_npc" else 2)


def _pawn_obj(con: sqlite3.Connection, rows: list[sqlite3.Row]) -> dict:
    rows = sorted(rows, key=lambda r: (_inc_rank(r["inc"]), r["kind"] != "native", r["inc"] or ""))
    r = rows[0]
    params = json.loads(r["params"]) if r["params"] else []
    rendered = []
    for name, tag, ref, const, arr, default in params:
        s = ("const " if const else "") + ("&" if ref else "") + (f"{tag}:" if tag else "") + name + (arr or "")
        rendered.append(s + (f" = {default}" if default is not None else ""))
    if r["kind"] == "const":
        sig = f"#define {r['name']} {r['value']}"
    else:
        sig = ("native " if r["kind"] == "native" else "forward ") + (f"{r['tag']}:" if r["tag"] else "") + \
            f"{r['name']}({', '.join(rendered)})" + (f" = {r['alias']}" if r["alias"] else "")
    src = con.execute("SELECT src.key, f.path FROM file f JOIN source src ON src.id = f.source_id WHERE f.id = ?",
                      (r["file_id"],)).fetchone()
    notes = []
    if r["deprecated"]:
        notes.append("#pragma deprecated")
    builtin = bool(src and src[1] == BUILTIN_PATH)
    if builtin:
        notes.append("built-in: one of the map natives satk's map converter reads; set kb.pawn_include in "
                     "satk.toml to a pawno/include or qawno/include folder and rebuild for the full API")
    also = [x["inc"] for x in rows[1:] if x["inc"] != r["inc"]]
    return obj(None, name=r["name"], kind=r["kind"], sig=sig, ret=r["tag"] or None,
               params=len(params) if r["kind"] != "const" else None,
               value=r["value"], inc=r["inc"], loc=None if builtin else _loc(con, r["file_id"], r["line"]),
               also=also or None, src=src[0] if src else None, notes=notes or None)


def native(query: str | None, *, kind: str | None = None, inc: str | None = None, limit: int = 20,
           path: Path | None = None) -> dict:
    """SA-MP/open.mp native, callback or constant by name, or a table for words."""
    if kind is not None and kind not in ("native", "callback", "const"):
        raise SatkError("BAD_PARAMS", f"unknown kind {kind!r}", did_you_mean=["native", "callback", "const"])
    q = re.sub(r"\s*\(\s*\)?\s*$", "", (query or "").strip())
    with closing(_open(path, "pawn_sym")) as con:
        con.row_factory = sqlite3.Row
        total = con.execute("SELECT COUNT(*) FROM pawn_sym").fetchone()[0]
        if total == 0:
            raise SatkError("NOT_READY", "no SA-MP/open.mp natives in the knowledge base",
                            hint="set kb.pawn_include in satk.toml to a pawno/include or qawno/include folder "
                                 "(or SATK_KB_PAWN_INCLUDE), then satk kb build")
        kf, kp = (" AND kind = ?", [kind]) if kind else ("", [])
        incf, incp = (" AND inc = ? COLLATE NOCASE", [inc.removesuffix(".inc")]) if inc else ("", [])
        if not q:
            rows = con.execute(f"SELECT inc, COUNT(*) AS n, SUM(kind = 'native') AS natives, "
                               f"SUM(kind = 'callback') AS callbacks FROM pawn_sym WHERE 1{kf}{incf} GROUP BY inc "
                               "ORDER BY natives DESC, inc", [*kp, *incp]).fetchall()
            data = [[r["inc"], r["natives"], r["callbacks"], r["n"] - r["natives"] - r["callbacks"]] for r in rows]
            return table(["inc", "natives", "callbacks", "consts"], data[:limit], total=len(data))
        rows = con.execute(f"SELECT * FROM pawn_sym WHERE name = ? COLLATE NOCASE{kf}{incf}", [q, *kp, *incp]).fetchall()
        if rows:
            return _pawn_obj(con, rows)
        words = _words(q)
        out: list[tuple] = []
        if words:
            for r in con.execute(f"SELECT kind, name, inc, tag, params, value, alias FROM pawn_sym WHERE 1{kf}{incf}",
                                 [*kp, *incp]):
                low = r["name"].lower()
                if all(w in low for w in words):
                    if r["kind"] == "const":
                        sig = f"#define {r['name']} {r['value']}"
                    else:
                        ps = json.loads(r["params"]) if r["params"] else []
                        sig = (f"{r['tag']}:" if r["tag"] else "") + r["name"] + "(" + ", ".join(
                            ("&" if p[2] else "") + (f"{p[1]}:" if p[1] else "") + p[0] + (p[4] or "") +
                            (f" = {p[5]}" if p[5] is not None else "") for p in ps) + ")"
                    out.append((r["kind"] == "const", not low.startswith(words[0]), len(low), r["kind"], r["name"],
                                r["inc"], _cut(sig, 200)))
        out.sort()
        dedup: list[list] = []
        seen: set[tuple[str, str]] = set()
        for x in out:
            if (x[3], x[4]) in seen:
                continue
            seen.add((x[3], x[4]))
            dedup.append(list(x[3:]))
        if dedup:
            return table(["kind", "name", "inc", "sig"], dedup[:limit], total=len(dedup))
        names = [r[0] for r in con.execute(f"SELECT DISTINCT name FROM pawn_sym WHERE kind != 'const'{kf}", kp)]
        raise SatkError("NOT_FOUND", f"no native, callback or constant {q!r}", did_you_mean=_suggest(q, names),
                        hint='satk kb native "words of the name", e.g. satk kb native "object material"')
