"""Exact ranges are optional, half-open in the DB, and may be discontiguous."""

from __future__ import annotations

import json
import sqlite3

import pytest

import re_synth as C
from satk.core.errors import SatkError
from satk.re import api
from satk.re.build import build_db
from satk.re.db import SymDb
from satk.re.pe import PeImage
from satk.re.resolve import AddrMap, SectionInfo
from satk.re.sources.ghidra import load_functions


def test_explicit_end_is_not_extended_to_next_start():
    amap = AddrMap([SectionInfo(".text", C.TEXT, C.RDATA, C.EXEC)],
                   {C.F_BAR: True, C.F_BAZ: True}, [], ghidra={C.F_BAR}, ends={C.F_BAR: C.F_BAR + 10})
    assert amap.locate(C.F_BAR + 9).confidence == "exact"
    assert amap.locate(C.F_BAR + 10).start is None
    assert amap.locate(C.F_BAR + 0x20).start is None


@pytest.fixture
def bounds_file(world, tmp_path):
    rows = [
        {"entry": hex(C.F_BAR), "end": hex(C.F_BAR + 0x22),
         "ranges": [[hex(C.F_BAR), hex(C.F_BAR + 9)], [hex(C.F_BAR + 0x20), hex(C.F_BAR + 0x22)]]},
        {"entry": hex(C.F_BAZ), "end": hex(C.F_BAZ + 4), "ranges": [[hex(C.F_BAZ), hex(C.F_BAZ + 4)]]},
        {"entry": hex(C.H_BAZ), "end": hex(C.H_BAZ + 6), "ranges": [[hex(C.H_BAZ), hex(C.H_BAZ + 6)]],
         "canonical_entry": hex(C.F_BAZ)},
    ]
    path = tmp_path / "functions.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    (tmp_path / "summary.json").write_text(json.dumps({"executable_sha256": PeImage.open(world.exe).sha256}))
    return path


def test_build_uses_exact_ranges_and_thunk_owner(world, satk_home, bounds_file):
    from re_synth import make_sources

    sources = make_sources(world)
    sources.ghidra_functions = bounds_file
    out = satk_home / "work/re/exact.sqlite"
    stats = build_db(out, sources)
    db = SymDb(out)
    try:
        for address in (C.F_BAR, C.F_BAR + 9, C.F_BAR + 0x20, C.F_BAR + 0x22):
            result = api.addr_detail(db, address)
            assert result["start"] == hex(C.F_BAR) and result["confidence"] == "exact"
        for address in (C.F_BAR + 10, C.F_BAR + 0x23, C.H_BAZ + 7, C.HOOD + 0x1FF):
            result = api.addr_detail(db, address)
            assert result["confidence"] == "none" and "fn" not in result
        result = api.addr_detail(db, C.H_BAZ + 6)
        assert result["start"] == hex(C.F_BAZ) and result["off"] == "0x6"
        assert result["confidence"] == "exact" and result["in_hoodlum"] is True
        assert db.func(C.F_BAR)["bounds"] == "ghidra" and db.amap.end_of(C.F_BAZ) == C.F_BAZ + 5
        assert stats["ghidra"]["functions"] == 3 and stats["ghidra"]["ranges"] == 4
        assert any(source["kind"] == "ghidra" for source in db.sources())
        patches = api.patches(db, "CFoo::Bar", None, "all", None, 100, 0)
        assert "0x401010" not in {row[0] for row in patches["rows"]}  # a hole in the body
        assert "0x401020" in {row[0] for row in patches["rows"]}  # detached range
        assert patches["total"] == api.addr_detail(db, C.F_BAR)["patches"]["total"]
        assert patches["total"] == api.fn_refs(db, C.F_BAR, None, 50, 0)["rels"]["patches"]
    finally:
        db.close()


def test_bad_ghidra_input_does_not_replace_database(world, satk_home, bounds_file):
    from re_synth import make_sources

    sources = make_sources(world)
    out = satk_home / "work/re/existing.sqlite"
    build_db(out, sources)
    original = out.read_bytes()
    sources.ghidra_functions = bounds_file
    bounds_file.with_name("summary.json").write_text(json.dumps({"executable_sha256": "0" * 64}))
    with pytest.raises(SatkError, match="SHA-256") as error:
        build_db(out, sources)
    assert error.value.code == "BAD_PARAMS" and out.read_bytes() == original


@pytest.mark.parametrize("row", [
    {"entry": hex(C.F_BAR), "ranges": [{hex(C.F_BAR): 0, hex(C.F_BAR + 9): 0}]},
    {"entry": hex(C.F_BAR), "end": hex(C.F_BAR + 9), "canonical_entry": 0},
    {"entry": hex(C.F_BAR), "ranges": [[hex(C.F_BAR), hex(C.F_BAR + 9)],
                                       [hex(C.F_BAR + 5), hex(C.F_BAR + 12)]]},
    {"entry": hex(C.F_BAR), "ranges": [[hex(C.F_BAR), hex(C.F_BAR + 9), hex(C.F_BAR + 10)]]},
    {"entry": hex(C.F_BAR), "end": hex(C.RDATA)},
    {"entry": hex(C.F_BAR), "ranges": [[hex(C.F_BAR + 1), hex(C.F_BAR + 9)]]},
], ids=["object-pair", "zero-owner", "overlap", "three-endpoints", "non-code-end", "entry-outside"])
def test_rejects_invalid_ghidra_ranges(world, tmp_path, row):
    path = tmp_path / "invalid-functions.jsonl"
    path.write_text(json.dumps(row), encoding="utf-8")
    with pytest.raises(SatkError) as error:
        load_functions(path, PeImage.open(world.exe))
    assert error.value.code == "BAD_PARAMS"


def test_detached_block_before_entry_has_signed_offset(world, satk_home, bounds_file):
    from re_synth import make_sources

    # CFoo::Baz owns a block preceding its entry, even though another named entry is nearer.
    rows = [
        {"entry": hex(C.F_BAZ), "ranges": [[hex(C.F_BAR + 0x20), hex(C.F_BAR + 0x22)],
                                           [hex(C.F_BAZ), hex(C.F_BAZ + 4)]]},
    ]
    bounds_file.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    sources = make_sources(world)
    sources.ghidra_functions = bounds_file
    out = satk_home / "work/re/detached.sqlite"
    build_db(out, sources)
    db = SymDb(out)
    try:
        result = api.addr_detail(db, C.F_BAR + 0x20)
        assert (result["fn"], result["off"], result["confidence"]) == ("CFoo::Baz", "-0x20", "exact")
        patches = api.patches(db, "CFoo::Baz", None, "neon", None, 100, 0)
        assert any(row[0] == hex(C.F_BAR + 0x20) and row[-1] == "CFoo::Baz-0x20" for row in patches["rows"])
    finally:
        db.close()


def test_exact_body_does_not_promote_an_interior_hook_site(world, satk_home, bounds_file):
    from re_synth import make_sources

    site = C.F_BAR + 0x20  # aligned, but a detached block of CFoo::Bar rather than a new function
    (world.gtarev / "source/game_sa/Interior.cpp").write_text(
        f"void COther::InjectHooks() {{ RH_ScopedClass(COther); RH_ScopedInstall(Update, {site}); }}")
    sources = make_sources(world)
    sources.ghidra_functions = bounds_file
    out = satk_home / "work/re/interior.sqlite"
    stats = build_db(out, sources)
    db = SymDb(out)
    try:
        assert db.func(site) is None and stats["source_hook_interior_sites"] == 1
        result = api.addr_detail(db, site)
        assert (result["fn"], result["off"], result["confidence"]) == ("CFoo::Bar", "0x20", "exact")
    finally:
        db.close()


def test_old_v1_database_without_range_table_is_readable(built):
    out, _stats = built
    with sqlite3.connect(out) as con:
        con.execute("DROP TABLE func_range")
        con.execute("UPDATE func SET bounds='ghidra', end_addr=? WHERE addr=?", (C.F_BAR + 10, C.F_BAR))
    con.close()
    db = SymDb(out)
    try:
        assert api.addr_detail(db, C.F_BAZ)["fn"] == "CFoo::Baz"
        assert api.addr_detail(db, C.F_BAR + 9)["confidence"] == "exact"
        assert api.addr_detail(db, C.F_BAR + 10)["confidence"] == "none"
    finally:
        db.close()
