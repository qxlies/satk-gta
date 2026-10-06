"""Build a symdb from the synthetic world and query it (SPEC §4.9.2/§4.9.3; WP-09)."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys

import pytest

import re_synth as C
from satk.core.errors import SatkError
from satk.core.ids import Sid
from satk.re import api
from satk.re.db import open_db, parse_addr
from satk.re.export import export
from satk.re.provider import ReProvider


def test_build_tables_and_stats(built):
    out, st = built
    con = sqlite3.connect(out)
    assert con.execute("PRAGMA user_version").fetchone()[0] == 1
    assert st["hooks_json"] == 3 and st["reversed"] == 2
    assert st["named_starts"] == 6                      # 3 hooks + RwFoo, CFoo::Qux, CFoo::CFoo
    assert st["named_starts_by_origin"] == {"hooks_json": 3, "plugin_sdk": 3, "gta_reversed": 0}
    origin = dict(con.execute("SELECT addr, origin FROM func"))
    assert origin[C.F_PAD] == "vtable" and origin[C.F_CALLEE] == "call_target"
    assert origin[C.F_DATA] == "data_ptr" and origin[C.F_LOW] == "padding"
    assert st["thunk"] == {"hoodlum": 1, "seh_push_jmp": 1, "other_jmp": 0}
    assert st["thunk_hooks_json"] == {"hoodlum": 1, "seh_push_jmp": 1, "other_jmp": 0}
    assert con.execute("SELECT hoodlum_body FROM func WHERE addr=?", (C.F_BAZ,)).fetchone()[0] == C.H_BAZ
    assert con.execute("SELECT count(*) FROM vtable_slot").fetchone()[0] == 2
    assert dict(con.execute("SELECT name, size FROM struct_size")) == {"CFoo": 8, "CBar": 20}
    # globals: gta-reversed wins, plugin-sdk adds the rest
    g = {r[0]: r[1:] for r in con.execute("SELECT addr, name, origin, byte_size FROM global")}
    assert g[C.G_STATE] == ("gState", "gta_reversed", 4) and g[0x404040][:2] == ("CFoo::ms_count", "plugin_sdk")
    assert g[0x404040][2] == 4                           # size from the shared type table
    # limits merged with docs/limits.toml of the trunk tree
    lim = {r[0]: r[1:] for r in con.execute("SELECT name, kind, vanilla, trunk, neon FROM limit_def")}
    assert lim["CPools::ms_pThingPool"] == ("pool", 80, 160, 320) and lim["world.bounds"][0] == "world"
    # patches: three origins; func attribution at build time
    pt = {(r[0], r[1]): r[2:] for r in con.execute(
        "SELECT origin, addr, func_addr, func_off FROM patch WHERE kind IN ('hookpos','memcpy')")}
    assert pt[("neon", 0x401008)] == (C.F_BAR, 8)
    assert pt[("neon", 0x4012C5)] == (C.F_OPEN, 0)      # moved body -> owner of the thunk
    assert pt[("neon", 0x405012)] == (C.F_BAZ, 2)       # .HOODLUM body -> owner of the thunk
    assert con.execute("SELECT count(*) FROM patch WHERE origin='trunk' AND addr=0x401011").fetchone()[0] == 1
    assert con.execute("SELECT count(*) FROM callsite").fetchone()[0] == 1
    kinds = {r[0] for r in con.execute("SELECT kind FROM source_rev")}
    assert kinds == {"exe", "gta-reversed", "plugin-sdk", "mta-upstream", "mta-trunk", "neon"}
    meta = dict(con.execute("SELECT key, value FROM meta"))
    assert meta["schema_version"] == "1" and meta["image_base"] == "0x400000"
    assert json.loads(meta["stats"])["global"] == st["global"]
    assert con.execute("SELECT count(*) FROM sym_fts WHERE sym_fts MATCH '\"Foo::B\"'").fetchone()[0] >= 2


def test_rebuild_is_deterministic(built, world, satk_home):
    out, _ = built
    from re_synth import make_sources
    from satk.re import db as dbm
    from satk.re.build import build_db

    def dump(p):
        con = sqlite3.connect(p)
        res = {t: con.execute(f"SELECT * FROM {t} ORDER BY 1, 2").fetchall()
               for t in ("func", "global", "vtable", "thunk", "struct_size", "limit_def", "callsite")}
        con.close()
        return res

    first = dump(out)
    dbm.reset_cache()
    out2 = satk_home / "work" / "re" / "again.sqlite"
    build_db(out2, make_sources(world))
    assert dump(out2) == first


def test_locate_confidence_cases(built):
    db = open_db()
    m = db.amap
    loc = m.locate(C.F_BAR + 8)
    assert (loc.start, loc.off, loc.confidence) == (C.F_BAR, 8, "high")
    loc = m.locate(C.F_PAD + 2)                     # unnamed start after the named CDoor::Open
    assert loc.start == C.F_PAD and loc.confidence == "medium" and loc.alt[0][0] < C.F_PAD
    loc = m.locate(C.H_BAZ + 4)                      # .HOODLUM body
    assert (loc.start, loc.off, loc.in_hoodlum, loc.confidence) == (C.F_BAZ, 4, True, "low")
    loc = m.locate(C.MOVED + 3)                      # moved body inside .text
    assert loc.start == C.F_OPEN and loc.via_thunk == (C.F_OPEN, C.MOVED, "seh_push_jmp")
    assert loc.confidence == "medium" and loc.alt[0][0] == C.F_LOW
    assert m.locate(C.G_STATE).kind == "data" and m.locate(0x300000).kind == "outside"


def test_addr_detail_object(built):
    db = open_db()
    d = api.addr_detail(db, C.F_BAR + 8)
    assert d["fn"] == "CFoo::Bar" and d["off"] == "0x8" and d["src"] == "game_sa/Foo.cpp:7" and d["reversed"] is True
    assert d["confidence"] == "high" and d["id"] == "fn:0x401000"
    rows = d["patches"]["rows"]
    assert rows[0][0] == "0x401008" and rows[0][-1] is True          # covering patches first
    # upstream rows repeated by trunk are dropped; the one trunk changed (0x401010 -> 0x401011) stays
    assert [r[0] for r in rows if r[3] == "upstream"] == ["0x401010"]
    assert {r[3] for r in rows} == {"neon", "trunk", "upstream"}
    h = api.addr_detail(db, C.H_BAZ + 2)
    assert h["fn"] == "CFoo::Baz" and h["in_hoodlum"] is True and h["thunk"] == {"kind": "hoodlum", "target": "0x405010"}
    assert any(r[1] == "memcpy" and r[-1] for r in h["patches"]["rows"])
    p = api.addr_detail(db, C.F_PAD)
    assert p["fn"] == "sub_401180" and p["confidence"] == "medium" and p["vtables"] == ["CFoo[1]"]
    g = api.addr_detail(db, C.G_ARR + 6)
    assert g["g"] == "CFoo::ms_things" and g["g_off"] == "0x6" and g["id"] == "g:0x404020"
    o = api.addr_detail(db, 0x300000)
    assert o["confidence"] == "none"


def test_resolve_text_shapes(built):
    db = open_db()
    one = api.resolve_text(db, "gta_sa.exe+0x1008")
    assert one["ok"] and one["id"] == "fn:0x401000" and one["fn"] == "CFoo::Bar"
    many = api.resolve_text(db, "0x401008 0x405014 core.dll+0x10")
    assert many["cols"] == api.ADDR_COLS and many["n"] == 3
    r = {row[0]: row for row in many["rows"]}
    assert r["0x405014"][2] == "CFoo::Baz" and r["0x405014"][6] == "hoodlum->0x405010"
    assert r["core.dll+0x10"][1] == "core.dll" and r["core.dll+0x10"][8] == "none"
    assert many["summary"] == {"gta_sa": 2, "other_modules": 1}
    with pytest.raises(SatkError):
        api.resolve_text(db, "nothing here")


def test_find_ordering(built):
    db = open_db()
    t = api.find(db, "CFoo::Bar")
    assert t["rows"][0][:3] == ["fn:0x401000", "fn", "CFoo::Bar"]
    t = api.find(db, "Bar")                                   # ::member exact before substring
    assert t["rows"][0][2] == "CFoo::Bar"
    t = api.find(db, "cfoo::b")                               # prefix, case-insensitive
    assert {r[2] for r in t["rows"]} >= {"CFoo::Bar", "CFoo::Baz"}
    assert api.find(db, "gState", "global")["rows"][0][:3] == ["g:0x404010", "g", "gState"]
    assert api.find(db, "CFoo", "vtable")["rows"][0][3] == "2 slots"
    assert api.find(db, "CBar", "struct")["rows"][0] == [None, "struct", "CBar", "size 0x14"]
    page = api.find(db, "o", None, limit=2)
    assert page["n"] == 2 and page["next"] == "o:2"
    with pytest.raises(SatkError):
        api.find(db, "x", "nope")


@pytest.mark.parametrize("query,limit", [("Process", 1), ("Process", 7), ("Pr", 7)])
def test_find_kind_filter_totals_and_pagination(built, query, limit):
    out, _ = built
    names = ["Process", "CExact::Process", "ProcessGlobal"]
    names += [f"CObjects::ms_ProcessBuffer{i:02}" for i in range(33)]
    with sqlite3.connect(out) as con:
        # Short function names used to consume the FTS limit before the global filter.
        con.executemany("INSERT INTO sym_fts(sid,kind,qual) VALUES(?,?,?)",
                        [(f"fn:0x{0x600000 + i:x}", "fn", f"C::Process{i}") for i in range(80)])
        con.executemany("INSERT INTO global(addr,name,origin) VALUES(?,?,?)",
                        [(0x700000 + i, name, "manual") for i, name in enumerate(names)])
        con.executemany("INSERT INTO sym_fts(sid,kind,qual) VALUES(?,?,?)",
                        [(f"g:0x{0x700000 + i:x}", "g", name) for i, name in enumerate(names)])
    db = open_db()
    found = []
    for offset in range(0, len(names), limit):
        page = api.find(db, query, kind="global", limit=limit, offset=offset)
        assert page["total"] == 36
        assert page["n"] == min(limit, 36 - offset)
        assert all(row[1] == "g" for row in page["rows"])
        assert page["next"] == (f"o:{offset + limit}" if offset + limit < 36 else None)
        found.extend(row[2] for row in page["rows"])
    assert len(found) == len(set(found)) == 36
    assert set(found) == set(names)
    assert found[:3] == (names[:3] if query == "Process" else [names[0], names[2], names[1]])
    beyond = api.find(db, query, kind="global", offset=36)
    assert beyond["n"] == 0 and beyond["total"] == 36 and beyond["next"] is None


def test_find_prefix_total_is_not_capped_at_200(built):
    out, _ = built
    with sqlite3.connect(out) as con:
        con.executemany("INSERT INTO global(addr,name,origin) VALUES(?,?,?)",
                        [(0x700000 + i, f"Process{i:03}", "manual") for i in range(205)])
        con.executemany("INSERT INTO sym_fts(sid,kind,qual) VALUES(?,?,?)",
                        [(f"g:0x{0x700000 + i:x}", "g", f"Process{i:03}") for i in range(205)])
    db = open_db()
    first = api.find(db, "Process", kind="global", limit=10)
    assert first["total"] == 205 and first["next"] == "o:10"
    last = api.find(db, "Process", kind="global", limit=10, offset=200)
    assert last["total"] == 205 and last["n"] == 5 and last["next"] is None
    assert [row[2] for row in last["rows"]] == [f"Process{i:03}" for i in range(200, 205)]


def test_patches_cover_full_intervals_and_filter_origin(built):
    out, _ = built
    start = 0x8D44F8  # CPoolsSA.cpp's 128-byte memset from review #2
    lengths = [None, 0, -1, 1, 64, 128, 4096]
    with sqlite3.connect(out) as con:
        rev = con.execute("SELECT id FROM source_rev LIMIT 1").fetchone()[0]
        con.executemany("INSERT INTO patch(rev_id,origin,addr,len,kind,src_file,src_line) VALUES(?,?,?,?,?,?,?)",
                        [(rev, origin, start, length, "memset", "CPoolsSA.cpp", i + 1)
                         for origin in ("upstream", "trunk", "neon") for i, length in enumerate(lengths)])
    db = open_db()
    for offset in (-1, 0, 1, 63, 64, 127, 128, 4095, 4096):
        expected = {(origin, i + 1) for origin in ("upstream", "trunk", "neon")
                    for i, length in enumerate(lengths) if 0 <= offset < max(length or 1, 1)}
        rows = db.patches_covering(start + offset)
        assert {(row["origin"], row["src_line"]) for row in rows} == expected
        rows = db.patches_covering(start + offset, origin="neon")
        assert {(row["origin"], row["src_line"]) for row in rows} == {p for p in expected if p[0] == "neon"}


def test_src_reads_tree(built):
    db = open_db()
    s = api.src(db, "CFoo::Bar", context=3)
    assert s["file"] == "game_sa/Foo.cpp" and s["line"] == 7 and s["def_line"] == 12
    assert s["lines"][0].startswith(f"{s['first']}: ") and "int32 CFoo::Bar" in s["lines"][3]
    assert "RH_ScopedInstall(Bar" in s["install"]               # install line outside the window
    wide = api.src(db, "CFoo::Bar", context=30)
    assert wide["first"] == 4 and "install" not in wide and any("RH_ScopedInstall(Bar" in x for x in wide["lines"])
    s2 = api.src(db, "0x401052")                               # address inside CDoor::Open's thunk
    assert s2["fn"] == "CDoor::Open" and s2["def_line"] == 6
    s3 = api.src(db, "RwFoo")                                  # plugin-sdk wrapper source
    assert s3["repo"] == "plugin-sdk" and s3["file"].endswith("RenderWare.cpp")
    with pytest.raises(SatkError) as e:
        api.src(db, "CFoo::Nope")
    assert e.value.code == "NOT_FOUND"
    with pytest.raises(SatkError):
        api.src(db, "sub_401180")


def test_patches_and_limits(built):
    db = open_db()
    t = api.patches(db, "CFoo::Bar", None, "all", None, 50, 0)
    assert t["fn"] == "CFoo::Bar" and t["total"] == 12 and t["raw_total"] == 16 and t["cols"] == api.PATCH_COLS
    raw = api.patches(db, "CFoo::Bar", None, "all", None, 50, 0, dedupe=False)
    assert raw["total"] == raw["raw_total"] == 16
    assert api.patches(db, "CFoo::Bar", None, "neon", "hookpos", 50, 0)["total"] == 1
    t = api.patches(db, "CFoo::Baz", None, "neon", None, 50, 0)  # thunk entry + .HOODLUM body
    assert [r[1] for r in t["rows"]] == ["memcpy"]
    t = api.patches(db, None, "0x401000-0x401010", "upstream", None, 2, 0)
    assert t["n"] == 2 and t["next"] == "o:2"
    with pytest.raises(SatkError):
        api.patches(db, None, "bad", "all", None, 5, 0)
    lim = api.limits(db, None, None, 500, 0)
    assert lim["by_kind"]["array"] == 2 and lim["arrays_with_len"] == 2
    assert api.limits(db, "pool", None, 10, 0)["rows"][0][:4] == ["CPools::ms_pThingPool", "pool", 80, "0x404060"]
    assert api.limits(db, None, "things", 10, 0)["total"] == 1


def test_provider(built):
    p = ReProvider()
    g = p.get(Sid.parse("g:0x404010"), None, "vanilla")
    assert g["name"] == "gState" and g["type"] == "int32" and g["id"] == "g:0x404010"
    assert g["patches"]["rows"][0][1] == "memput"
    assert p.get(Sid.parse("g:gstate"), ["name"], "vanilla") == {"ok": True, "id": "g:0x404010", "name": "gState"}
    f = p.get(Sid.parse("fn:cfoo::bar"), None, "vanilla")
    assert f["fn"] == "CFoo::Bar" and f["id"] == "fn:0x401000"
    assert p.get(Sid.parse("fn:0x401008"), None, "vanilla")["off"] == "0x8"
    v = p.get(Sid.parse("vt:cfoo"), None, "vanilla")
    assert v["table"]["rows"] == [[0, "0x401000", "CFoo::Bar"], [1, "0x401180", "sub_401180"]]
    pt = p.get(Sid.parse("patch:neon/hookpos_fooentry"), None, "vanilla")
    assert pt["symbol"] == "HOOKPOS_FooEntry" and pt["func"] == "CFoo::Bar+0x8"
    assert p.find("gState", None, 5, None, "vanilla")["rows"][0][0] == "g:0x404010"
    assert p.find("x", "model", 5, None, "vanilla")["rows"] == []
    rels = p.refs(Sid.parse("fn:0x401300"), None, 10, None, "vanilla")
    assert rels["rels"]["callers"] == 1
    callers = p.refs(Sid.parse("fn:cfoo::qux"), "callers", 10, None, "vanilla")
    assert callers["rows"] == [["0x401003", "CFoo::Bar", "call"]]
    assert p.refs(Sid.parse("fn:cfoo::bar"), "callees", 10, None, "vanilla")["total"] == 1
    assert p.refs(Sid.parse("g:0x404010"), None, 10, None, "vanilla")["rels"] == {"patches": 3}
    assert p.refs(Sid.parse("vt:0x403000"), "slots", 1, None, "vanilla")["next"] == "o:1"
    with pytest.raises(SatkError):
        p.get(Sid.parse("g:nosuch"), None, "vanilla")


def test_export_formats(built, satk_home):
    db = open_db()
    j = export(db, "json")
    data = json.loads(open(j["files"][0], encoding="utf-8").read())
    assert data["format"] == "satk-symbols/1" and j["counts"]["functions"] == 6
    x = export(db, "x32dbg")
    dd = json.loads(open(x["files"][0], encoding="utf-8").read())
    assert {"module": "gta_sa.exe", "address": "0x1000", "manual": True, "text": "CFoo::Bar"} in dd["labels"]
    assert any(c["text"].startswith("MTA ") for c in dd["comments"])
    gh = export(db, "ghidra")
    assert [p.rsplit("/", 1)[-1] for p in gh["files"]] == ["symbols.json", "ApplySaSymbols.py"]
    compile(open(gh["files"][1], encoding="utf-8").read(), "ApplySaSymbols.py", "exec")
    guarded = satk_home / "src" / "x"  # src\ of the isolated workspace is protected
    with pytest.raises(SatkError) as e:
        export(db, "json", out=str(guarded))
    assert e.value.code == "PROTECTED_PATH"
    assert not guarded.exists()


def test_parse_addr():
    assert parse_addr("0x53BF09") == 0x53BF09 and parse_addr("gta_sa.exe+0x13BF09") == 0x53BF09
    assert parse_addr("53bf09") == 0x53BF09
    with pytest.raises(SatkError):
        parse_addr("core.dll+0x10")
    with pytest.raises(SatkError):
        parse_addr("zzz")


def test_missing_db_is_not_ready(satk_home):
    from satk.re import db as dbm

    dbm.reset_cache()
    with pytest.raises(SatkError) as e:
        open_db()
    assert e.value.code == "NOT_READY" and e.value.hint == "satk re build"
    assert open_db(required=False) is None


def test_corrupt_db_is_not_ready(tmp_path):
    path = tmp_path / "corrupt.sqlite"
    original = b"not a SQLite database"
    path.write_bytes(original)
    with pytest.raises(SatkError) as error:
        open_db(path)
    assert error.value.code == "NOT_READY" and error.value.hint == "satk re build"
    assert path.read_bytes() == original


def test_cached_reader_does_not_block_replacement(built, tmp_path):
    out, _ = built
    reader = open_db()
    before = reader.func(C.F_BAR)["qual"]
    replacement = tmp_path / "replacement.sqlite"
    replacement.write_bytes(out.read_bytes())
    with sqlite3.connect(replacement) as con:
        con.execute("UPDATE func SET qual='CFoo::Replacement' WHERE addr=?", (C.F_BAR,))
    con.close()
    # A second process cannot close our handles; this is the long-lived MCP server case.
    result = subprocess.run([sys.executable, "-c", "import os, sys; os.replace(sys.argv[1], sys.argv[2])",
                             str(replacement), str(out)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    os.utime(out, ns=(out.stat().st_atime_ns, out.stat().st_mtime_ns + 1_000_000))
    refreshed = open_db()
    assert refreshed.func(C.F_BAR)["qual"] == "CFoo::Replacement"
    assert reader.func(C.F_BAR)["qual"] == before  # an in-flight call keeps a consistent snapshot
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        refreshed.con.execute("DELETE FROM func")


@pytest.mark.parametrize("start", [C.F_BAR, C.F_BAZ, C.F_OPEN])
def test_function_patch_counts_and_pages_agree(built, start):
    db = open_db()
    detail = api.addr_detail(db, start)
    summary = api.fn_refs(db, start, None, 50, 0)
    patches = api.patches(db, hex(start), None, "all", None, 500, 0)
    total = detail.get("patches", {}).get("total", 0)
    assert total > 0
    assert summary["rels"]["patches"] == patches["total"] == total
    rows = []
    for offset in range(total):
        page = api.fn_refs(db, start, "patches", 1, offset)
        assert page["total"] == total and page["n"] == 1
        assert page["next"] == (f"o:{offset + 1}" if offset + 1 < total else None)
        rows.extend(page["rows"])
    assert rows == patches["rows"]
    if (thunk := db.thunk(start)) is not None:
        body = api.addr_detail(db, thunk["target"])
        assert body["patches"]["total"] == total


def test_build_adds_source_only_entries_and_unlisted_thunks(world, satk_home):
    from re_synth import make_sources
    from satk.re.build import build_db
    from satk.re.db import SymDb
    from satk.re.pe import PeImage

    source_entry = C.F_BAR + 0x20
    anonymous_entry = C.F_BAR + 0x2B
    source_body, anonymous_body = C.HOOD + 0x30, C.HOOD + 0x50
    path = world.gtarev / "source/game_sa/More.cpp"
    path.write_text(f"void CMore::InjectHooks() {{ RH_ScopedClass(CMore); RH_ScopedInstall(Update, {source_entry}); }}")
    image = PeImage.open(world.exe)
    code = C.Code(C.TEXT, 0x2000)
    code.buf[:] = image.raw(image.section(".text"))
    code.jmp(source_entry, source_body)
    code.jmp(anonymous_entry, anonymous_body)
    hood = C.Code(C.HOOD, 0x200)
    hood.buf[:] = image.raw(image.section(".HOODLUM"))
    hood.put(source_body, b"\x33\xC0\xC3")
    hood.put(anonymous_body, b"\x33\xC0\xC3")
    sections = [(s.name, s.va, bytes(code.buf) if s.name == ".text" else bytes(hood.buf) if s.name == ".HOODLUM"
                 else image.raw(s), s.flags) for s in image.sections]
    world.exe.write_bytes(C.make_pe(sections))
    out = satk_home / "work/re/expanded.sqlite"
    build_db(out, make_sources(world))
    db = SymDb(out)
    try:
        candidate = api.addr_detail(db, source_entry)
        assert candidate["fn"] == "CMore::Update" and candidate["confidence"] == "medium"
        assert candidate["alt"][0]["fn"] == "CFoo::Bar"
        assert api.addr_detail(db, source_body)["start"] == hex(source_entry)
        assert api.addr_detail(db, anonymous_entry)["start"] == hex(anonymous_entry)
        assert api.addr_detail(db, anonymous_body)["start"] == hex(anonymous_entry)
        assert api.addr_detail(db, C.H_BAZ + 4)["confidence"] == "high"  # bounded, curated named owner
        assert api.addr_detail(db, source_body + 4)["confidence"] == "medium"  # tentative owner
        assert api.addr_detail(db, anonymous_body + 4)["confidence"] == "low"  # last unbounded body
        assert api.src(db, "CMore::Update")["file"] == "game_sa/More.cpp"
    finally:
        db.close()


def test_unbounded_hoodlum_tail_is_not_high_confidence(built):
    result = api.addr_detail(open_db(), C.HOOD + 0x1FF)
    assert result["confidence"] not in ("high", "exact")


def test_unaligned_source_hook_does_not_split_named_function(world, satk_home):
    from re_synth import make_sources
    from satk.re.build import build_db
    from satk.re.db import SymDb

    site = C.F_BAR + 3  # E8 call inside CFoo::Bar, not a compiler-aligned function entry
    (world.gtarev / "source/game_sa/Interior.cpp").write_text(
        f"void COther::InjectHooks() {{ RH_ScopedClass(COther); RH_ScopedInstall(Update, {site}); }}")
    out = satk_home / "work/re/interior.sqlite"
    stats = build_db(out, make_sources(world))
    db = SymDb(out)
    try:
        assert db.func(site) is None and stats["source_hook_interior_sites"] == 1
        assert db.amap.end_of(C.F_BAR) == C.F_BAZ
        for address in (site, C.F_BAR + 0x20):
            result = api.addr_detail(db, address)
            assert (result["fn"], result["start"], result["confidence"]) == ("CFoo::Bar", hex(C.F_BAR), "high")
        patches = api.patches(db, "CFoo::Bar", None, "neon", None, 100, 0)
        assert any(row[0] == hex(C.F_BAR + 0x20) and row[-1] == "CFoo::Bar+0x20" for row in patches["rows"])
    finally:
        db.close()
