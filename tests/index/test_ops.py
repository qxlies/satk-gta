"""CLI/MCP operations of satk.index on an isolated workspace with a synthetic game (no game files)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from satk.core import registry
from satk.index import api


@pytest.fixture
def ws(synth_ws: Path, run_cli):
    api.clear_cache()
    r = run_cli(["index", "build", "--jobs", "1", "--json"])
    assert r.code == 0, r.out
    yield synth_ws
    api.clear_cache()


def test_build_output(ws: Path, run_cli):
    r = run_cli(["index", "build", "--jobs", "1"])
    env = r.json
    assert env["ok"] and env["path"].endswith("/work/index/vanilla.sqlite") and env["user_version"] == 3
    assert (ws / "work" / "index" / "vanilla.sqlite").is_file()
    assert (ws / "work" / "index" / "hashcache.sqlite").is_file()


def test_asset_get_refs_find(ws: Path, run_cli):
    r = run_cli(["asset", "get", "model:400"])
    assert r.code == 0 and r.json["links"]["txd_chain"] == ["txd:testcar", "txd:vehicle"]
    r = run_cli(["asset", "get", "model:400", "--fields", "name,sec"])
    assert set(r.json) == {"ok", "id", "name", "sec"}
    r = run_cli(["asset", "refs", "model:1000"])
    assert r.json["rels"]["inst"] == 3
    r = run_cli(["asset", "refs", "model:1000", "--rel", "inst"])
    assert [x[0] for x in r.json["rows"]] == ["inst:orphan_stream0#0", "inst:test#1", "inst:test_stream0#0"]
    r = run_cli(["asset", "find", "box1", "--kind", "model"])
    assert r.json["total"] == 1 and r.json["rows"][0][3] == "objs test.ide"
    r = run_cli(["asset", "find", "стол"])
    assert r.code == 0 and r.json["ok"] and r.json["total"] == 0
    r = run_cli(["asset", "get", "model:nope"])
    assert r.code == 1 and r.json["error"]["code"] == "NOT_FOUND"
    r = run_cli(["asset", "get", "garbage"])
    assert r.code == 1 and r.json["error"]["code"] == "BAD_ID"


def test_world_near_cli(ws: Path, run_cli):
    r = run_cli(["world", "near", "100", "200", "--r", "10", "--lod", "all"])
    assert r.code == 0 and [x[0] for x in r.json["rows"]] == ["inst:test#0", "inst:test#1"]
    r = run_cli(["world", "near", "--box", "90,190,160,260", "--match", "center", "--lod", "all", "--area", "any",
                 "--limit", "500"])
    assert r.json["total"] == 3
    r = run_cli(["world", "near", "100", "200", "5", "--r", "50", "--kinds", "inst,zone", "--lod", "all"])
    assert {x[0].split(":")[0] for x in r.json["rows"]} == {"inst", "zone"}
    r = run_cli(["world", "near", "--area", "x", "--box", "0,0,1,1"])
    assert r.code == 2 and r.json["error"]["code"] == "BAD_PARAMS"


def test_index_query(ws: Path, run_cli):
    r = run_cli(["index", "query", "SELECT name FROM v_model WHERE id = ?", "--params", "400"])
    assert r.json["rows"] == [["testcar"]]
    r = run_cli(["index", "query", "DELETE FROM model"])
    assert r.code == 1 and r.json["error"]["code"] == "READ_ONLY"
    r = run_cli(["index", "query", "SELECT 1", "--db", "re"])
    assert r.code == 3 and r.json["error"]["code"] == "NOT_READY"


def test_list_options_before_positionals(ws: Path, run_cli):
    result = run_cli(["asset", "get", "--fields", "name,sec", "model:400", "--json"])
    assert result.code == 0 and result.json == {"ok": True, "id": "model:400", "name": "testcar", "sec": "cars"}
    result = run_cli(["index", "query", "--params", "1000", "SELECT count(*) FROM inst WHERE model_id = ?"])
    assert result.code == 0 and result.json["rows"] == [[3]]


@pytest.mark.parametrize("quoted,value", [
    ("'00123'", "00123"), ("'1e3'", "1e3"), ("'-42'", "-42"), ("''", ""),
    ("'O''Brien'", "O'Brien"), ("'''00123'''", "'00123'"), ("' 00123 '", " 00123 "),
])
def test_query_can_preserve_text_parameters(ws: Path, run_cli, quoted, value):
    result = run_cli(["index", "query", "SELECT ?, typeof(?)", "--params", quoted, quoted])
    assert result.code == 0 and result.json["rows"] == [[value, "text"]]


def test_query_mixes_quoted_text_and_numeric_parameters(ws: Path, run_cli):
    params = ["'00123'", "00123", "1e3", "-42", "O'Brien", "'unterminated"]
    sql = "SELECT ?, ?, ?, ?, ?, ?"
    result = run_cli(["index", "query", sql, "--params", *params])
    assert result.code == 0 and result.json["rows"] == [["00123", 123, 1000.0, -42, "O'Brien", "'unterminated"]]
    mcp = registry.invoke(registry.op_by_mcp("index_query"), {"sql": sql, "params": params})
    assert mcp == result.json


def test_verify_includes_spec_metrics_by_default(ws: Path, run_cli, tmp_path: Path):
    golden = tmp_path / "metrics.json"
    golden.write_text(json.dumps({"metrics": {"unresolved_texrefs_main": 1, "unresolved_texrefs_all": 1}}))
    brief = run_cli(["index", "verify", "--golden", str(golden), "--json"])
    assert brief.code == 0 and brief.json["ok"] and brief.json["checked"] == 2
    assert brief.json["unresolved_texrefs_main"] == brief.json["unresolved_texrefs_all"] == 1
    assert "metrics" not in brief.json
    full = run_cli(["index", "verify", "--golden", str(golden), "--metrics", "--json"])
    assert full.code == 0
    for suffix in ("main", "all"):
        assert full.json["metrics"][f"unresolved_texrefs_{suffix}"] == full.json["metrics"][f"texref.unresolved_{suffix}"] == 1


def test_status_verify_hash_bench_diff(ws: Path, run_cli, tmp_path: Path):
    r = run_cli(["index", "status"])
    rows = {x[0]: x for x in r.json["rows"]}
    assert rows["vanilla"][1] is True and rows["vanilla"][2] is True
    g = tmp_path / "g.json"
    g.write_text(json.dumps({"metrics": {"blobs": 12}, "examples": [{"get": "model:300", "expect": {"sec": "hier"}}]}),
                 encoding="utf-8")
    r = run_cli(["index", "verify", "--golden", str(g)])
    assert r.json == {"ok": True, "profile": "vanilla", "checked": 2, "failed": [],
                      "unresolved_texrefs_main": 1, "unresolved_texrefs_all": 1}
    h1 = run_cli(["index", "hash", "--recompute"]).json
    assert h1["match"] is True
    run_cli(["index", "build", "--jobs", "1"])
    h2 = run_cli(["index", "hash"]).json
    assert h1["content_hash"] == h2["content_hash"]
    b = run_cli(["index", "bench", "--n", "5"]).json
    assert b["ok"] and [x[0] for x in b["rows"]] == ["get", "find", "refs", "near_r100"]
    d = run_cli(["index", "diff", "vanilla", "vanilla", "--kind", "txd"]).json
    assert d["total"] == 0 and d["summary"]["txd"] == {"added": 0, "changed": 0, "removed": 0}


def test_missing_index_is_not_ready(synth_ws: Path, run_cli):
    api.clear_cache()
    r = run_cli(["asset", "get", "model:400"])
    assert r.code == 3 and r.json["error"]["code"] == "INDEX_MISSING"


def test_registration_mcp_names():
    registry.discover()
    names = {s.name: s.mcp_name for s in registry.all_ops()}
    for op, mcp in (("asset.find", "asset_find"), ("asset.get", "asset_get"), ("asset.refs", "asset_refs"),
                    ("world.near", "world_near"), ("index.query", "index_query"), ("index.diff", "index_diff")):
        assert names[op] == mcp
    for cli_only in ("index.build", "index.status", "index.verify", "index.hash", "index.bench"):
        assert names[cli_only] is None
    assert "satk.index.ops" not in registry.import_errors()
    for s in registry.all_ops():
        if s.name.split(".")[0] in ("asset", "world", "index"):
            assert len(s.description) <= registry.MAX_SUMMARY


def test_status_provider_and_doctor(ws: Path):
    st = registry.status_providers()["index"](False)
    assert st["vanilla"]["built"] and st["vanilla"]["fresh"] and st["vanilla"]["counts"]["model"] == 4
    d = registry.doctor_checks()["index_fresh"]()
    assert d["status"] == "ok"


def test_doctor_warns_without_index(synth_ws: Path):
    d = registry.doctor_checks()["index_fresh"]()
    assert d["status"] == "warn" and d["fix"] == "satk index build"


def test_mcp_style_call(ws: Path):
    spec = registry.op_by_mcp("world_near")
    env = registry.invoke(spec, {"x": 100, "y": 200, "r": 10, "lod": "all", "area": 0})
    assert env["ok"] and env["total"] == 2
    env = registry.invoke(registry.op_by_mcp("asset_find"), {"query": "testcar", "kind": "model"})
    assert env["rows"][0][0] == "model:400"
    env = registry.invoke(registry.op_by_mcp("index_query"), {"sql": "SELECT count(*) FROM inst", "params": []})
    assert env["rows"] == [[4]]


def test_game_profile_without_clean_copy(satk_home: Path, make_game_fn, run_cli):
    """PORTABILITY §4: only the user's game folder -> profile game; vanilla is its alias (one index)."""
    make_game_fn(satk_home / "GTA San Andreas")  # paths.game_root defaults to paths.installed
    api.clear_cache()
    try:
        r = run_cli(["index", "build", "--jobs", "1"])
        assert r.code == 0, r.out
        j = r.json
        assert j["profile"] == "game" and j["path"].endswith("/work/index/game.sqlite")
        assert any(w.startswith("NO_CLEAN_COPY") for w in j["warn"])
        assert not (satk_home / "work" / "index" / "vanilla.sqlite").exists()
        r = run_cli(["asset", "find", "box1", "--kind", "model"])  # default profile vanilla -> game index
        assert r.code == 0 and r.json["total"] == 1
        st = {row[0]: row[-1] for row in run_cli(["index", "status"]).json["rows"]}
        assert st["game"] == "built" and st["vanilla"] == "alias of game"
        assert st["installed"] == "built" or st["installed"] == "not built"  # installed = the same folder here
        d = registry.doctor_checks()["index_fresh"]()
        assert d["status"] == "ok" and d["msg"].startswith("game index")
    finally:
        api.clear_cache()


def test_unconfigured_profiles_are_not_errors(synth_ws: Path, run_cli):
    st = {row[0]: row[-1] for row in run_cli(["index", "status"]).json["rows"]}
    assert st["vanilla"] == "not built" and st["installed"] == "not configured" and st["game"] == "not configured"
