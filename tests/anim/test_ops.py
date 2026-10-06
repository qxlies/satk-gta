"""CLI of satk.anim (list, extract, write, merge, check, skeleton, roundtrip) in an isolated workspace."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from satk.anim.ifp import read_ifp, write_ifp
from satk.core.registry import all_ops

from .conftest import img_bytes, skinned_dff, walk_ifp

OPS = ("anim.list", "anim.extract", "anim.write", "anim.merge", "anim.check", "anim.skeleton", "anim.roundtrip",
       "anim.mta", "anim.to_blender", "anim.from_blender")


def test_operations_are_cli_only():
    ops = {o.name: o for o in all_ops()}
    for name in OPS:
        assert ops[name].mcp_name is None and ops[name].module == "satk.anim.ops", name
        assert len(ops[name].summary) <= 300
    assert ops["anim.to_blender"].group == "blender" and ops["anim.list"].group == "formats"


def _ok(r):
    assert r.code == 0, r.out + r.err
    return r.json


def test_list_animations_bones_and_img(satk_home, run_cli, ifp_files, tmp_path):
    env = _ok(run_cli(["anim", "list", str(ifp_files["walk"])]))
    assert env["format"] == "ANP3" and env["pack"] == "test" and env["anims"] == 2 and env["compressed"] == "yes"
    assert env["rows"] == [["walk", 4, 5, 0.133, "move 1.50m"], ["idle", 4, 5, 0.133, "fixed"]]
    env = _ok(run_cli(["anim", "list", str(ifp_files["walk"]), "--anim", "WALK", "--limit", "2"]))
    assert env["anim"] == "walk" and env["total"] == 4 and env["next"] == "2"
    assert env["rows"][0] == ["Root", 0, "rot+trans", 5, 0.133, "0.00,1.50,0.00"]
    assert _ok(run_cli(["anim", "list", str(ifp_files["walk"]), "--name", "i*"]))["rows"][0][0] == "idle"
    img = tmp_path / "anims.img"
    img.write_bytes(img_bytes({"walk.ifp": ifp_files["walk"].read_bytes(), "cut.ifp": ifp_files["anpk"].read_bytes(),
                               "x.txt": b"hello"}))
    env = _ok(run_cli(["anim", "list", str(img)]))
    assert env["ifps"] == 2 and [r[1:] for r in env["rows"]] == [["ANP3", "test", 2], ["ANPK", "cut", 1]]
    env = _ok(run_cli(["anim", "list", f"{img}/cut.ifp"]))
    assert env["format"] == "ANPK" and env["rows"][0][0] == "door_open"


def test_list_errors_have_hints(satk_home, run_cli, ifp_files, tmp_path):
    r = run_cli(["anim", "list", str(ifp_files["walk"]), "--anim", "wlak"])
    assert r.code != 0 and r.json["error"]["code"] == "NOT_FOUND" and "walk" in r.json["error"]["did_you_mean"]
    r = run_cli(["anim", "list", str(tmp_path / "nope.ifp")])
    assert r.json["error"]["code"] == "NOT_FOUND" and "hint" in r.json["error"]
    bad = tmp_path / "bad.ifp"
    bad.write_bytes(b"RIFF" + b"\0" * 40)
    assert run_cli(["anim", "list", str(bad)]).json["error"]["code"] == "UNSUPPORTED"


def test_extract_write_round_trip(satk_home, run_cli, ifp_files):
    out_root = satk_home / "work" / "out" / "anim"
    env = _ok(run_cli(["anim", "extract", str(ifp_files["walk"]), "--all"]))
    assert env["dir"].endswith("/out/anim/test") and env["extracted"] == 2
    files = sorted(p.name for p in (out_root / "test").iterdir())
    assert files == ["idle.json", "walk.json"]
    doc = json.loads((out_root / "test" / "walk.json").read_text(encoding="utf-8"))
    assert doc["anims"][0]["index"] == 0 and doc["source"].endswith("walk.ifp")
    env = _ok(run_cli(["anim", "write", "test", "--out", "again.ifp"]))      # the folder under <work>/out/anim
    assert env["verified"] is True and env["anims"] == 2 and env["id"].endswith("/out/anim/again.ifp")
    assert (out_root / "again.ifp").read_bytes() == ifp_files["walk"].read_bytes()
    env = _ok(run_cli(["anim", "extract", str(ifp_files["walk"]), "idle", "--out", "idle.json"]))
    assert env["path"].endswith("/idle.json")
    env = _ok(run_cli(["anim", "extract", str(ifp_files["walk"]), "w*", "--out", "only_walk.ifp"]))
    assert env["verified"] and env["anims"] == 1
    assert [a.name for a in read_ifp((out_root / "only_walk.ifp").read_bytes()).anims] == ["walk"]
    r = run_cli(["anim", "extract", str(ifp_files["walk"])])
    assert r.json["error"]["code"] == "BAD_PARAMS" and "walk" in r.json["error"]["msg"]


def test_write_conversions(satk_home, run_cli, ifp_files):
    out_root = satk_home / "work" / "out" / "anim"
    env = _ok(run_cli(["anim", "write", str(ifp_files["anpk"]), "--format", "ANP3", "--compress", "yes",
                       "--out", "cut3.ifp"]))
    assert env["format"] == "ANP3" and env["compressed"] == "yes"
    a = read_ifp((out_root / "cut3.ifp").read_bytes()).anims[0]
    assert a.flags == 1 and a.seqs[0].keys[1][5:] == (1024, 2048, 3584)
    env = _ok(run_cli(["anim", "write", str(ifp_files["walk"]), "--format", "ANPK", "--out", "walk_k.ifp"]))
    assert env["format"] == "ANPK"
    env = _ok(run_cli(["anim", "write", str(ifp_files["float"]), "--compress", "keep", "--pack", "renamed"]))
    assert env["id"].endswith("/out/anim/renamed.ifp") and env["pack"] == "renamed" and env["compressed"] == "no"
    r = run_cli(["anim", "write", str(ifp_files["walk"]), "--format", "ANPK", "--compress", "yes"])
    assert r.json["error"]["code"] == "BAD_PARAMS"


def test_write_json_errors(satk_home, run_cli, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"anims": [{"name": "a", "bones": [{"name": "b", "keys": [[0, 1]]}]}]}), encoding="utf-8")
    r = run_cli(["anim", "write", str(bad)])
    assert r.json["error"]["code"] == "BAD_PARAMS" and "$.anims[0].bones[0].keys[0]" in r.json["error"]["msg"]
    broken = tmp_path / "broken.json"
    broken.write_text("{", encoding="utf-8")
    assert run_cli(["anim", "write", str(broken)]).json["error"]["code"] == "BAD_PARAMS"


def test_merge_added_skipped_replaced(satk_home, run_cli, ifp_files, tmp_path):
    other = walk_ifp("other", travel=2.0)
    other.anims[1].name = "dance"
    add = tmp_path / "add.ifp"
    add.write_bytes(write_ifp(other))
    env = _ok(run_cli(["anim", "merge", str(ifp_files["walk"]), str(add)]))
    assert [r[:2] for r in env["rows"]] == [["walk", "skipped"], ["dance", "added"]]
    assert env["added"] == 1 and env["skipped"] == 1 and env["anims"] == 3 and env["pack"] == "test"
    assert any(w.startswith("SKIPPED") for w in env["warn"])
    env = _ok(run_cli(["anim", "merge", str(ifp_files["walk"]), str(add), "--replace", "--anim", "walk",
                       "--out", "replaced.ifp"]))
    assert env["replaced"] == 1 and env["anims"] == 2
    merged = read_ifp((satk_home / "work" / "out" / "anim" / "replaced.ifp").read_bytes())
    assert [a.name for a in merged.anims] == ["walk", "idle"]
    assert merged.anims[0].seqs[0].keys[-1][6] == 2048                 # the added walk travels 2 m
    env = _ok(run_cli(["anim", "merge", str(ifp_files["walk"]), str(ifp_files["anpk"])]))
    k = read_ifp(Path(env["path"]).read_bytes())
    assert k.format == "ANP3" and k.anims[-1].name == "door_open" and not k.anims[-1].compressed


def test_check_and_skeleton(satk_home, run_cli, ifp_files, tmp_path):
    env = _ok(run_cli(["anim", "check", str(ifp_files["walk"]), "--sev", "info"]))
    assert env["skeleton"] == "sa-ped" and env["summary"] == {"info": 1} and env["rows"][0][2] == "root_motion"
    env = _ok(run_cli(["anim", "check", str(ifp_files["anpk"])]))
    assert env["summary"] == {"warn": 1} and env["rows"][0][2] == "unbound_name"
    r = run_cli(["anim", "check", str(ifp_files["anpk"]), "--fail-on", "warn"])
    assert r.code != 0 and r.json["error"]["code"] == "CHECK_FAILED" and r.json["error"]["data"]["rows"]
    dff = tmp_path / "toy.dff"
    dff.write_bytes(skinned_dff())
    env = _ok(run_cli(["anim", "check", str(ifp_files["walk"]), "--skin", str(dff)]))
    assert env["summary"] == {"warn": 2, "info": 1}                    # the toy skeleton has no bone 22
    assert {r[2] for r in env["rows"]} == {"unknown_bone"}
    env = _ok(run_cli(["anim", "skeleton"]))
    assert env["bones"] == 32 and env["rows"][1] == [1, 1, " Pelvis", "Pelvis", "Root"]
    env = _ok(run_cli(["anim", "skeleton", str(dff)]))
    assert env["skinned"] is True and [r[1] for r in env["rows"]] == [0, 1, 2, 5, 41]


def test_roundtrip_folder(satk_home, run_cli, ifp_files):
    env = _ok(run_cli(["anim", "roundtrip", str(ifp_files["walk"].parent), "--via-json"]))
    assert env["files"] == 3 and env["identical"] == 3 and env["rows"] == [] and env["formats"] == {"ANP3": 2,
                                                                                                    "ANPK": 1}


def test_blender_ops_validate_before_starting_blender(satk_home, run_cli, ifp_files):
    r = run_cli(["anim", "to-blender", str(ifp_files["walk"])])
    assert r.json["error"]["code"] == "BAD_PARAMS" and "--all" in r.json["error"]["msg"]
    r = run_cli(["anim", "to-blender", str(ifp_files["walk"]), "walk", "--size", "9"])
    assert r.json["error"]["code"] == "BAD_PARAMS"
    r = run_cli(["anim", "from-blender", "nothing.blend"])
    assert r.json["error"]["code"] == "NOT_FOUND"


@pytest.mark.parametrize("change", ["nul", "unicode", "flags", "float_range"])
def test_invalid_output_never_replaces_existing_file(change, satk_home, run_cli, tmp_path):
    from satk.anim.jsonio import ifp_to_json

    doc = ifp_to_json(walk_ifp())
    if change == "nul":
        doc["anims"][0]["name"] = "walk\0hidden"
    elif change == "unicode":
        doc["anims"][0]["bones"][0]["name"] = "\u2603"
    elif change == "flags":
        doc["anims"][0]["flags"] = 1 << 32
    else:
        doc["anims"][0]["compressed"] = False
        doc["anims"][0]["bones"][0]["keys"][0][1] = 1e99
    source = tmp_path / "edit.json"
    source.write_text(json.dumps(doc), encoding="utf-8")
    output = tmp_path / "keep.ifp"
    output.write_bytes(b"existing output")
    result = run_cli(["anim", "write", str(source), "--out", str(output)])
    assert result.code != 0 and result.json["error"]["code"] == "BAD_PARAMS", result.out
    assert output.read_bytes() == b"existing output"


@pytest.mark.parametrize("args", [["--fps", "0"], ["--fps", "241"], ["--format", "ANPK", "--compress", "yes"]])
def test_invalid_export_options_are_checked_before_opening_blender(args, satk_home, run_cli):
    result = run_cli(["anim", "from-blender", "nothing.blend", *args])
    assert result.json["error"]["code"] == "BAD_PARAMS"
