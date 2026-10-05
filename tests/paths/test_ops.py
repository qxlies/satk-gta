"""``satk paths near|node|export|compile`` on the synthetic network (M2-05)."""

from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path

import pytest

from satk.core.registry import get_op, invoke
from satk.paths import db as D


def test_ops_are_cli_only():
    for name in ("paths.import", "paths.near", "paths.node", "paths.export", "paths.compile"):
        spec = get_op(name)
        assert spec.mcp_name is None and spec.group == "world", name


def test_near_auto_imports_and_sorts(game, run_cli):
    res = run_cli(["paths", "near", "-2281", "-2899", "--r", "25"])
    assert res.code == 0, res.err
    env = res.json
    assert env["cols"] == ["node", "kind", "pos", "d", "width", "flood", "links"]
    assert [r[0] for r in env["rows"]] == ["0:1", "0:5", "0:0", "0:2", "0:4"]  # 0:2/0:4 tie: by address
    assert env["rows"][0] == ["0:1", "car", [-2280.0, -2900.0, 10.0], 1.41, 1.0, 1, ["0:0", "0:2"]]
    assert env["total"] == 5
    assert any(w.startswith("IMPORTED: 2 regions, 8 nodes") for w in env["warn"])
    again = run_cli(["paths", "near", "-2281", "-2899", "--r", "25"]).json
    assert "warn" not in again


def test_near_filters(game):
    cars = invoke("paths.near", {"x": -2281, "y": -2899, "r": 100, "kind": "car", "limit": 2})
    assert [r[0] for r in cars["rows"]] == ["0:1", "0:0"] and cars["total"] == 5 and cars["n"] == 2
    veh = invoke("paths.near", {"x": -2290, "y": -2950, "r": 5, "kind": "vehicle"})
    assert [r[:2] for r in veh["rows"]] == [["0:3", "boat"]]
    peds = invoke("paths.near", {"x": -2281, "y": -2899, "r": 100, "kind": "ped"})
    assert {r[0] for r in peds["rows"]} == {"0:4", "0:5"}
    three_d = invoke("paths.near", {"x": -2290, "y": -2950, "z": 30, "r": 25})
    assert three_d["total"] == 0 and three_d["warn"][0].startswith("EMPTY: no path node within 25")
    assert invoke("paths.near", {"x": 0, "y": 0, "r": 0})["error"]["code"] == "BAD_PARAMS"


def test_node_detail(game):
    env = invoke("paths.node", {"node": "0:2"})
    assert env["id"] == "0:2" and env["kind"] == "car" and env["pos"] == [-2260.0, -2900.0, 10.5]
    assert env["spawn"] == 7 and env["behaviour"] == 2 and env["flags"] == ["not_highway"]
    assert env["links"] == [{"to": "0:1", "dist": 20, "navi": "0:1", "lanes": [1, 1], "inter": 1},
                            {"to": "1:0", "dist": 20, "navi": "1:0", "lanes": [2, 0]}]
    assert env["navis"] == ["1:0"] and env["region"] == 0
    back = invoke("paths.node", {"node": "1:0"})
    assert back["links"][0] == {"to": "0:2", "dist": 20, "navi": "1:0", "lanes": [0, 2]}  # one way east
    assert invoke("paths.node", {"node": "0:99"})["error"]["code"] == "NOT_FOUND"
    assert invoke("paths.node", {"node": "zero"})["error"]["code"] == "BAD_PARAMS"


def test_export_overlay(game, run_cli):
    res = run_cli(["paths", "export", "--area=-2300,-2905,-2255,-2885"])
    assert res.code == 0, res.err
    env = res.json
    path = Path(env["path"])
    assert path.name == "all_-2300_-2905_-2255_-2885.json" and path.parent.name == "vanilla"
    assert path.parent.parent.name == "paths" and path.parent.parent.parent.name == "out"
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["format"] == "satk.paths.overlay/1" and doc["box"] == [-2300, -2905, -2255, -2885]
    assert [n["id"] for n in doc["nodes"]] == ["0:0", "0:1", "0:2", "0:4", "0:5"]
    segs = {(s["a"], s["b"]): s for s in doc["segments"]}
    assert set(segs) == {("0:0", "0:1"), ("0:1", "0:2"), ("0:2", "1:0"), ("0:4", "0:5")}
    cross = segs[("0:2", "1:0")]
    assert cross["pb"] == [-2240.0, -2900.0, 10.0] and cross["lanes"] == [2, 0] and cross["navi"] == "1:0"
    assert "navi" not in segs[("0:4", "0:5")]
    assert [v["id"] for v in doc["navis"]] == ["0:0", "0:1", "1:0"]
    assert (env["nodes"], env["segments"], env["navis"]) == (5, 4, 3)
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    run_cli(["paths", "export", "--area=-2300,-2905,-2255,-2885"])
    assert hashlib.sha256(path.read_bytes()).hexdigest() == sha  # deterministic


def test_export_kinds_and_whole_map(game):
    cars = invoke("paths.export", {"kind": "car", "name": "cars", "navis": False})
    doc = json.loads(Path(cars["path"]).read_text(encoding="utf-8"))
    assert Path(cars["path"]).name == "cars.json" and doc["box"] is None
    assert {n["kind"] for n in doc["nodes"]} == {"car"} and len(doc["nodes"]) == 5
    assert len(doc["segments"]) == 4 and doc["navis"] == []
    peds = invoke("paths.export", {"kind": "ped"})
    assert Path(peds["path"]).name == "ped_map.json" and peds["segments"] == 1


@pytest.mark.parametrize("args,code", [({"area": [1, 2, 3]}, "BAD_PARAMS"), ({"area": [5, 0, 1, 1]}, "BAD_PARAMS"),
                                       ({"name": "../evil"}, "BAD_PARAMS"), ({"kind": "plane"}, "BAD_PARAMS")])
def test_export_rejects(game, args, code):
    assert invoke("paths.export", args)["error"]["code"] == code


def test_compile_round_trip_is_byte_identical(game, pn):
    env = invoke("paths.compile", {})
    assert env["ok"], env
    assert (env["areas"], env["identical"], env["byte_identical"]) == (2, 2, True)
    assert "changed" not in env and env["written"] == 0 and env["oracle"] == "valid: 0 errors, 0 warnings"
    out = Path(env["out"])
    assert out.as_posix().endswith("/out/paths/vanilla/compiled")
    man = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert man["identical"] == 2 and man["changed"] == [] and man["oracle"]["valid"] is True
    assert all(r["sha256"] == r["source_sha256"] for r in man["regions"])
    full = invoke("paths.compile", {"write": "all", "name": "rt"})
    files = sorted(Path(full["out"]).glob("nodes*.dat"))
    assert [f.name for f in files] == ["nodes0.dat", "nodes1.dat"] and full["written"] == 2
    net = pn.network()
    assert {int(f.stem[5:]): f.read_bytes() for f in files} == net  # padding included


def test_compile_edit_changes_only_its_region(game):
    env = invoke("paths.compile", {"edits": {"1:1": [-2221.5, -2900, 11]}, "name": "moved"})
    assert env["ok"], env
    assert env["changed"] == [1] and env["identical"] == 1 and env["byte_identical"] is False
    assert env["files"] == ["nodes1.dat"]
    assert env["edited"] == [{"node": "1:1", "from": [-2220.0, -2900.0, 10.0], "pos": [-2221.5, -2900, 11]}]
    data = (Path(env["out"]) / "nodes1.dat").read_bytes()
    assert struct.unpack_from("<3h", data, 20 + 28 + 8) == (round(-2221.5 * 8), -2900 * 8, 88)
    assert env["oracle"].startswith("valid")


def test_compile_flag_edits(game):
    env = invoke("paths.compile", {"edits": {"0:0": {"width": 2, "spawn": 3, "behaviour": 2}}, "name": "flags"})
    assert env["changed"] == [0], env
    data = (Path(env["out"]) / "nodes0.dat").read_bytes()
    rec = data[20:48]
    assert rec[22] == 32 and rec[26] == 3 | (2 << 4)


def test_compile_overwrites_previous_output(game):
    first = invoke("paths.compile", {"edits": {"1:1": [-2221.5, -2900, 11]}, "name": "same"})
    assert first["files"] == ["nodes1.dat"]
    second = invoke("paths.compile", {"name": "same"})
    assert second["written"] == 0
    assert sorted(p.name for p in Path(second["out"]).iterdir()) == ["manifest.json"]


@pytest.mark.parametrize("edits,code", [
    ({"0:99": [0, 0, 0]}, "NOT_FOUND"), ({"x": [0, 0]}, "BAD_PARAMS"), ({"0:0": {"foo": 1}}, "BAD_PARAMS"),
    ({"0:0": [1]}, "BAD_PARAMS"), ({"0:0": {"spawn": 16}}, "BAD_PARAMS"), ({"0:0": {"width": -1}}, "BAD_PARAMS"),
    ({"0:0": {"pos": [1, "a", 2]}}, "BAD_PARAMS"), ({"0:0": [0, 0, 99999]}, "BAD_PARAMS")])
def test_compile_rejects_bad_edits(game, edits, code):
    env = invoke("paths.compile", {"edits": edits})
    assert env["ok"] is False and env["error"]["code"] == code, env


def test_compile_cli_edits_from_file(game, run_cli, tmp_path):
    f = tmp_path / "edits.json"
    f.write_text(json.dumps({"1:1": [-2221.5, -2900, 11]}), encoding="utf-8")
    res = run_cli(["paths", "compile", "--edits", f"@{f}", "--name", "fromfile"])
    assert res.code == 0, res.err
    assert res.json["changed"] == [1]
    assert invoke("paths.compile", {"write": "some"})["error"]["code"] == "BAD_PARAMS"
    assert invoke("paths.compile", {"name": "a/b"})["error"]["code"] == "BAD_PARAMS"


def test_database_is_separate_from_the_index(game):
    invoke("paths.import", {})
    p = D.db_path("vanilla")
    assert p.name == "paths-vanilla.sqlite" and not (p.parent / "vanilla.sqlite").exists()
