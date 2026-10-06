"""Animation verification must detect lost data as well as numerical differences (no Blender needed)."""

from __future__ import annotations

from copy import deepcopy
import json
import math

import pytest

from satk.anim.blender import compare
from satk.anim.ifp import Anim, Seq

from .conftest import walk_ifp


@pytest.mark.parametrize("change", ["missing_sequence", "extra_sequence", "missing_keys", "renamed", "tag"])
def test_compare_reports_structural_loss(change):
    source = walk_ifp().anims[0]
    exported = deepcopy(source)
    if change == "missing_sequence":
        exported.seqs.pop()
    elif change == "extra_sequence":
        exported.seqs.append(deepcopy(exported.seqs[0]))
    elif change == "missing_keys":
        exported.seqs[0].keys.pop()
    elif change == "renamed":
        exported.name = "another_action"
    else:
        exported.seqs[0].tag = 5
    result = compare(source, exported)
    assert result["exact"] is False
    assert result["within_tolerance"] is False
    assert result.get("problems"), result


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
@pytest.mark.parametrize("component", [0, 4, 5])
def test_compare_rejects_nonfinite_keys(value, component):
    source = Anim("a", [Seq("Root", 0, True, False, [(0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0)])])
    exported = deepcopy(source)
    key = list(exported.seqs[0].keys[0])
    key[component] = value
    exported.seqs[0].keys[0] = tuple(key)
    result = compare(source, exported)
    assert result["exact"] is False
    assert result["within_tolerance"] is False
    assert result.get("problems"), result


def test_compare_distinguishes_rotation_from_stored_quaternion_sign():
    source = walk_ifp().anims[0]
    exported = deepcopy(source)
    assert compare(source, exported)["exact"] is True
    exported.seqs[0].keys = [tuple(-v for v in k[:4]) + k[4:] for k in exported.seqs[0].keys]
    result = compare(source, exported)
    assert result["exact"] is False and not result.get("problems")
    assert result["rot_deg"] == 0.0 and result["trans_m"] == result["time_s"] == 0.0
    assert result["within_tolerance"] is True


@pytest.mark.parametrize("kind", ["rotation", "time", "translation", "zero_rotation", "scale"])
def test_compare_uses_unrounded_errors_and_checks_all_channels(kind):
    key = (0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0)
    source = Anim("a", [Seq("Root", 0, True, False, [key], scale=True)])
    exported = deepcopy(source)
    changed = list(key)
    if kind == "rotation":
        angle = math.radians(0.050004) / 2
        changed[0], changed[3] = math.sin(angle), math.cos(angle)
    elif kind == "time":
        changed[4] = 0.001004
    elif kind == "translation":
        changed[5] = 0.001004
    elif kind == "zero_rotation":
        changed[:4] = [0.0] * 4
    else:
        changed[8] = 2.0
    exported.seqs[0].keys = [tuple(changed)]
    result = compare(source, exported)
    assert result["exact"] is result["within_tolerance"] is False, result


@pytest.mark.parametrize("change", ["empty_actions", "missing_anim", "extra_anim", "missing_sequence"])
def test_verification_rejects_incomplete_blender_output(change, satk_home, ifp_files, tmp_path, monkeypatch, run_cli):
    from satk.anim import blender, ops
    from satk.anim.ifp import read_ifp
    from satk.anim.jsonio import ifp_to_json

    source = read_ifp(ifp_files["walk"].read_bytes())
    doc = ifp_to_json(source)
    rows = [[a.name, a.name, 4, 4, 0, 4] for a in source.anims]
    if change == "empty_actions":
        rows.clear()
    elif change == "missing_anim":
        doc["anims"].pop()
    elif change == "extra_anim":
        doc["anims"].append(deepcopy(doc["anims"][0]))
    else:
        doc["anims"][0]["bones"].pop()
    exported = tmp_path / "roundtrip.json"
    exported.write_text(json.dumps(doc), encoding="utf-8")
    monkeypatch.setattr(ops, "_skin_plan", lambda *args: {})
    monkeypatch.setattr(blender, "run_anim_job", lambda *args, **kw: {
        "stats": {"actions": rows}, "files": {"roundtrip": str(exported)}})
    result = run_cli(["anim", "to-blender", str(ifp_files["walk"]), "--all", "--verify"])
    assert result.code == 0, result.out + result.err
    assert result.json["verified"] is False and result.json["problems"]
    assert all(len(row) == len(result.json["cols"]) for row in result.json["rows"])


def test_success_response_does_not_hide_blender_process_failure(satk_home, monkeypatch, tmp_path):
    from satk.anim.blender import run_anim_job
    from satk.blender import runner
    from satk.core.errors import SatkError

    monkeypatch.setattr(runner, "blender_exe", lambda: tmp_path / "blender.exe")
    monkeypatch.setattr(runner, "ensure_dragonff", lambda: {})
    monkeypatch.setattr(runner, "new_job", lambda cmd: ("test", tmp_path))
    monkeypatch.setattr(runner, "blender_env", lambda job: {})
    (tmp_path / "response.json").write_text('{"ok":true,"stats":{}}', encoding="utf-8")
    monkeypatch.setattr(runner, "run_blender", lambda *args, **kw: (1, 0.1))
    with pytest.raises(SatkError, match="exited with code 1"):
        run_anim_job("from_blender", {})
