"""Pixel/transform checks in real headless Blender, using synthetic scenes and TXDs."""

import json
import math
import os
from pathlib import Path

import pytest

from satk.core import config

pytestmark = pytest.mark.blender


@pytest.fixture(scope="module")
def observations(tmp_path_factory):
    from satk.blender import runner

    out = tmp_path_factory.mktemp("blender_media")
    old = os.environ.get("SATK_PATHS_WORK")
    os.environ["SATK_PATHS_WORK"] = str(out)
    config.reset()
    try:
        runner.ensure_dragonff()
        log = out / "blender.log"
        script = Path(__file__).with_name("media_probe.py")
        code, _ = runner.run_blender(
            ["-b", "--factory-startup", "--python-exit-code", "1", "--python", str(script),
             "--", str(out), str(runner.dragonff_root())],
            log=log, timeout=180, env=runner.blender_env(out))
        assert code == 0, log.read_text(encoding="utf-8", errors="replace")[-5000:]
        return json.loads((out / "observations.json").read_text(encoding="utf-8"))
    finally:
        if old is None:
            os.environ.pop("SATK_PATHS_WORK", None)
        else:
            os.environ["SATK_PATHS_WORK"] = old
        config.reset()


def _assert_edit(row):
    assert row["pos"] == pytest.approx([10, 20, 30], abs=1e-4)
    assert row["q"] == pytest.approx([0, 0, math.sqrt(0.5), math.sqrt(0.5)], abs=1e-6)
    assert row["rot_zxy_deg"] == pytest.approx([0, 0, 90], abs=1e-4)


def test_edited_legacy_placement_removes_prototype_transform(observations):
    from satk.blender import packaging

    row, = observations["placements"]
    _assert_edit(row)
    model = {"name": "test", "id": 1000, "sec": "objs", "insts": [row], "files": {}}
    fields = packaging.ipl_text([model]).splitlines()[2].split(", ")
    assert list(map(float, fields[3:6])) == [10, 20, 30]
    assert list(map(float, fields[6:10])) == pytest.approx([0, 0, -math.sqrt(0.5), math.sqrt(0.5)], abs=1e-6)
    lua = packaging.client_lua("edited", [model])
    assert "10, 20, 30, 0, 0, 90" in lua


def test_area_import_has_editable_root_for_all_parts(observations):
    result = observations["area_roots"]
    assert result["parts"] == 2 and result["root"]
    assert result["matrices_match"]
    row, = result["placements"]
    _assert_edit(row)


def test_single_part_mesh_transform_edits_are_exported(observations):
    row, = observations["area_roots"]["mesh_edit"]
    assert row["pos"] == pytest.approx([40, 50, 60], abs=1e-4)
    assert row["q"] == pytest.approx([0, 0, math.sin(0.3), math.cos(0.3)], abs=1e-6)


def test_inconsistent_part_edits_are_not_silently_discarded(observations):
    assert observations["area_roots"]["partial_edit_error"] == "BAD_PARAMS"


def test_dxt3_blender_pixels_use_four_colour_bc2(observations):
    r = observations["texture_pixels"]
    assert r["packed"]
    assert r["max_error"] == 0, r


@pytest.mark.parametrize("case,expected_names", [
    ("truncated_native", {"intact_first", "intact_last"}),
    ("truncated_tail", {"intact_first"}),
    ("short_mip", {"intact_first", "intact_last"}),
    ("unsupported_format", {"intact_first", "intact_last"}),
])
def test_undecodable_texture_preserves_other_txd_images(observations, case, expected_names):
    r = observations["texture_failures"][case]
    assert "error" not in r, r
    assert set(r["images"]) == expected_names
    for image in r["images"].values():
        assert image["packed"]
        assert image["pixels"] == [[33, 17, 9, 255]] * 4
    assert r["missing"] == ["broken"]


def test_untagged_occluder_keeps_depth_and_texture_alpha(observations):
    r = observations["object_ids"]
    assert r["tagged"] and all(row[0] != "inst:target#0" for row in r["tagged"])
    assert not r["hidden"]
    assert not r["untagged"]
    assert any(row[0] == "inst:target#0" for row in r["clear"])


def test_blender_orbit_azimuth_matches_saap(observations):
    el = math.radians(25)
    for d, az in zip(observations["orbits"], [45, 135, 225, 315]):
        a = math.radians(az)
        assert d == pytest.approx([-math.sin(a) * math.cos(el), math.cos(a) * math.cos(el), math.sin(el)], abs=1e-6)


def test_workbench_request_renders_fractional_alpha_correctly(observations):
    r = observations["alpha_render"]
    assert r["unloaded"] == 2
    assert r["engine"].startswith("BLENDER_EEVEE"), r
    assert len(r["warnings"]) == 1 and r["warnings"][0].startswith("EEVEE_ALPHA:"), r
    assert r["max_error"] <= 2 / 255, r
