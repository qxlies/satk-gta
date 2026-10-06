"""CLI of satk.fx2d (dump/apply/copy/check/roundtrip) in an isolated workspace (synthetic files only)."""

from __future__ import annotations

import json
from pathlib import Path

from satk.core.registry import all_ops

from .conftest import build_dff, raw_entries


def test_operations_are_registered_cli_only():
    ops = {o.name: o for o in all_ops()}
    for name in ("fx2d.dump", "fx2d.apply", "fx2d.copy", "fx2d.check", "fx2d.roundtrip"):
        assert name in ops and ops[name].mcp_name is None and ops[name].group == "formats"
    assert ops["fx2d.roundtrip"].long_running


def test_dump_then_apply_unchanged_is_identical(satk_home, run_cli, tmp_path):
    src = tmp_path / "lamp.dff"
    src.write_bytes(build_dff(raw_entries(), names=("lamp",)))
    r = run_cli(["fx2d", "dump", str(src)])
    assert r.code == 0, r.out + r.err
    env = r.json
    out = Path(env["file"])
    assert out == satk_home / "work" / "out" / "fx2d" / "lamp.json"
    assert env["entries"] == 10 and env["total"] == 10 and env["counts"]["light"] == 2
    assert env["rows"][0] == ["g0#0", "light", [0.5, 0.0, 3.2], env["rows"][0][3]]
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["geometries"][0]["frame"] == "lamp"
    env = run_cli(["fx2d", "dump", str(src), "--filter", "light", "--limit", "1", "--full"]).json
    assert env["total"] == 2 and env["n"] == 1 and len(env["effects"]) == 1
    assert env["effects"][0]["fx"] == "g0#0" and env["effects"][0]["corona"] == "coronastar"
    assert "keep" not in env["effects"][0] and "effects" not in run_cli(["fx2d", "dump", str(src)]).json
    r = run_cli(["fx2d", "apply", str(src), "lamp.json"])                # the JSON is found in <work>/out/fx2d
    assert r.code == 0, r.out + r.err
    env = r.json
    assert env["identical"] is True and env["geometries"] == [0] and env["entries"] == 10
    assert Path(env["file"]).read_bytes() == src.read_bytes()
    assert "check" in env and "RESOURCE" not in json.dumps(env)        # no game root: checks without names


def test_apply_inline_json_add_and_out_name(satk_home, run_cli, tmp_path):
    src = tmp_path / "box.dff"
    src.write_bytes(build_dff(None))
    spec = json.dumps({"effects": [{"type": "light", "pos": [0, 0, 3]}, {"type": "particle", "pos": [0, 0, 1],
                                                                         "name": "fire"}]})
    env = run_cli(["fx2d", "apply", str(src), spec, "--out", "lit"]).json
    assert env["created"] == [0] and env["identical"] is False and env["file"].endswith("/out/fx2d/lit.dff")
    env = run_cli(["fx2d", "apply", "lit.dff", "[{\"type\": \"trigger_point\", \"pos\": [0, 0, 0], \"id\": 1}]",
                   "--mode", "add", "--out", "lit2"]).json
    assert env["entries"] == 3
    d = run_cli(["fx2d", "dump", "lit2.dff"]).json
    assert [r[1] for r in d["rows"]] == ["light", "particle", "trigger_point"]


def test_copy_with_filter_and_offset_redumps_equal(satk_home, run_cli, tmp_path):
    lamp = tmp_path / "lamp.dff"
    lamp.write_bytes(build_dff(raw_entries()))
    box = tmp_path / "box.dff"
    box.write_bytes(build_dff(None, None))
    env = run_cli(["fx2d", "copy", str(lamp), str(box), "--filter", "light", "--to-geometry", "1"]).json
    assert env["copied"] == 2 and env["types"] == {"light": 2} and env["created"] == [1]
    src_doc = json.loads(Path(run_cli(["fx2d", "dump", str(lamp), "--out", "a"]).json["file"]).read_text("utf-8"))
    dst_doc = json.loads(Path(run_cli(["fx2d", "dump", "box.dff", "--out", "b"]).json["file"]).read_text("utf-8"))
    lights = [e for e in src_doc["geometries"][0]["effects"] if e["type"] == "light"]
    assert dst_doc["geometries"][0]["geometry"] == 1 and dst_doc["geometries"][0]["effects"] == lights
    env = run_cli(["fx2d", "copy", str(lamp), str(box), "--filter", "particle", "--offset", "1,2,3",
                   "--out", "shifted"]).json
    moved = run_cli(["fx2d", "dump", "shifted.dff"]).json["rows"]
    assert moved == [["g0#0", "particle", [1.0, 2.0, 5.0], "vent"]]
    r = run_cli(["fx2d", "copy", str(box), str(lamp)])
    assert r.json["error"]["code"] == "NOT_FOUND"
    r = run_cli(["fx2d", "copy", str(lamp), str(box), "--filter", "lamp"])
    assert r.json["error"]["code"] == "BAD_PARAMS" and "light" in r.json["error"]["did_you_mean"]


def test_check_json_with_explicit_resources_and_strict(satk_home, run_cli, tmp_path, resources):
    fxp, txd = resources
    spec = tmp_path / "fx.json"
    spec.write_text(json.dumps({"effects": [
        {"type": "particle", "pos": [0, 0, 1], "name": "smoke_huge"},
        {"type": "light", "pos": [0, 0, 2], "corona": "coronamoon"},
        {"type": "light", "pos": [0, 0, 3], "colour": [1, 2, 3, 4]},
        {"type": "sun_glare", "pos": [0, 0, 0]}]}), encoding="utf-8")
    env = run_cli(["fx2d", "check", str(spec), "--fxp", str(fxp), "--txd", str(txd)]).json
    codes = {r[2] for r in env["rows"]}
    assert {"PARTICLE_MISSING", "TEX_MISSING", "INVALID", "TYPE_NOT_READ"} <= codes
    assert env["errors"] == 2 and env["rows"][0][1] == "error" and env["entries"] == 4
    assert set(env["resources"]) == {"effects.fxp", "particle.txd"} and "warn" not in env
    r = run_cli(["fx2d", "check", str(spec), "--fxp", str(fxp), "--txd", str(txd), "--strict"])
    assert r.code != 0 and r.json["error"]["code"] == "CHECK_FAILED"
    env = run_cli(["fx2d", "check", str(spec)]).json
    assert any(w.startswith("RESOURCE_MISSING") for w in env["warn"])


def test_check_dff_and_errors(satk_home, run_cli, tmp_path, resources):
    fxp, txd = resources
    src = tmp_path / "ok.dff"
    src.write_bytes(build_dff(raw_entries()))
    env = run_cli(["fx2d", "check", str(src), "--fxp", str(fxp), "--txd", str(txd)]).json
    assert env["errors"] == 0 and env["warnings"] == 0 and env["entries"] == 10
    r = run_cli(["fx2d", "dump", str(tmp_path / "missing.dff")])
    assert r.json["error"]["code"] == "NOT_FOUND"
    bad = tmp_path / "bad.dff"
    bad.write_bytes(b"COL3" + b"\0" * 40)
    assert run_cli(["fx2d", "dump", str(bad)]).json["error"]["code"] == "UNSUPPORTED"
    r = run_cli(["fx2d", "apply", str(src), "{\"effects\": [{\"type\": \"light\"}]}"])
    assert r.json["error"]["code"] == "BAD_PARAMS" and "effects[0]" in r.json["error"]["msg"]
    r = run_cli(["fx2d", "apply", str(src), "{\"effects\": [}"])
    assert r.json["error"]["code"] == "BAD_PARAMS" and "invalid JSON" in r.json["error"]["msg"]
    r = run_cli(["fx2d", "dump", str(tmp_path)])
    assert r.json["error"]["code"] == "BAD_PARAMS"


def test_empty_block_and_no_block_dump_to_valid_json(satk_home, run_cli, tmp_path):
    src = tmp_path / "empty.dff"
    src.write_bytes(build_dff([], None))                                # geometry 0: a block with 0 entries
    env = run_cli(["fx2d", "dump", str(src)]).json
    doc = json.loads(Path(env["file"]).read_text(encoding="utf-8"))
    assert doc["geometries"] == [{"geometry": 0, "frame": "part0", "effects": []}]
    assert env["warn"][0].startswith("NO_2DFX")
    assert run_cli(["fx2d", "apply", str(src), "empty.json"]).json["identical"] is True
    none = tmp_path / "none.dff"
    none.write_bytes(build_dff(None))
    env = run_cli(["fx2d", "dump", str(none)]).json
    assert json.loads(Path(env["file"]).read_text(encoding="utf-8"))["geometries"] == []


def test_roundtrip_folder(satk_home, run_cli, tmp_path):
    d = tmp_path / "mod"
    d.mkdir()
    (d / "a.dff").write_bytes(build_dff(raw_entries(), pad=100))
    (d / "b.dff").write_bytes(build_dff(None))
    (d / "c.dff").write_bytes(build_dff(raw_entries()[:2], raw_entries()[6:8]))
    env = run_cli(["fx2d", "roundtrip", str(d)]).json
    assert (env["files"], env["with_2dfx"], env["exact"], env["differ"]) == (3, 2, 2, 0)
    assert env["entries"]["light"] == 4 and env["total_entries"] == 14 and env["raw"] == 0
    env = run_cli(["fx2d", "roundtrip", str(d), "--no-keep"]).json
    assert env["exact"] == 0 and env["differ"] == 2 and env["with_keep"] == 0
    assert [Path(x).name for x in env["examples"]["differ"]] == ["a.dff", "c.dff"]
