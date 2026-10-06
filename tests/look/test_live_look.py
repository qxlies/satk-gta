"""The game look with the real Blender 5.1 and gta-sa-clean (markers blender, game, slow).

The work directory is a temp dir (``SATK_PATHS_WORK``); the game is only read. Golden numbers (numbers only)
are in ``tests/golden/look_vanilla.json``; they will be recalibrated against in-game captures.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from satk.core import config
from satk.core.registry import get_op

pytestmark = [pytest.mark.blender, pytest.mark.game, pytest.mark.slow]

GOLDEN = json.loads((Path(__file__).resolve().parents[1] / "golden" / "look_vanilla.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def live_work(tmp_path_factory):
    work = tmp_path_factory.mktemp("work")
    old = os.environ.get("SATK_PATHS_WORK")
    os.environ["SATK_PATHS_WORK"] = str(work)
    config.reset()
    from satk.blender import gamedata, runner

    gamedata.clear_cache()
    try:
        runner.blender_exe()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"no Blender: {e}")
    yield work
    if old is None:
        os.environ.pop("SATK_PATHS_WORK", None)
    else:
        os.environ["SATK_PATHS_WORK"] = old
    config.reset()


@pytest.fixture(scope="module")
def measured(live_work):
    from satk.blender import resolve, runner

    runner.ensure_dragonff()
    out = live_work / "golden"
    out.mkdir()
    req = {"satk_src": str(runner.satk_src()), "addon_parent": str(runner.addon_dir().parent),
           "dragonff": str(runner.dragonff_root()), "out": str(out),
           "car": resolve.plan_model("model:426")["model"],
           "prelit": {sid: resolve.plan_model(sid)["model"] for sid in GOLDEN["prelit"]}}
    rp = out / "request.json"
    rp.write_text(json.dumps(req), encoding="utf-8")
    script = runner.addon_dir() / "look" / "golden.py"
    code, _s = runner.run_blender(["-b", "--factory-startup", "--python-exit-code", "1", "--python", str(script),
                                   "--", str(rp)], log=out / "blender.log", timeout=600, env=runner.blender_env(out))
    assert code == 0, runner.log_tail(out / "blender.log", 20)
    return json.loads((out / "measure.json").read_text(encoding="utf-8"))


def test_glass_is_see_through(measured):
    g = GOLDEN["premier"]
    assert measured["glass_px"] >= g["glass_px_min"]
    lo, hi = g["glass_lum"]
    assert lo <= measured["glass_lum"] <= hi                         # the interior shows, not black
    assert abs(measured["glass_lum"] - measured["glass_lum_opaque"]) >= g["glass_vs_opaque_min"]


def test_lamps_white_and_dirt(measured):
    g = GOLDEN["premier"]
    assert measured["lamp_materials"] >= g["lamp_materials_min"] and measured["lamp_white"] == measured["lamp_materials"]
    assert measured["dirt_px"] >= g["dirt_px_min"]
    assert measured["dirt2_minus_raw"] >= g["dirt2_lighter_min"]   # level 2 is lighter than the raw texture


def test_prelit_luminance_band(measured):
    for sid, (lo, hi) in GOLDEN["prelit"].items():
        assert lo <= measured["prelit"][sid] <= hi, (sid, measured["prelit"][sid])


def test_preview_sheet_of_vanilla_and_lineup(live_work):
    r = get_op("blender.preview").call({"subject": "model:426", "states": ["ok", "dam"]})
    assert r["files"]["sheet"].endswith(".jpg") and r["sheet"]["bytes"] <= GOLDEN["sheet_bytes_max"]
    assert max(r["sheet"]["w"], r["sheet"]["h"]) <= 1024
    st = r["stats"]["e0"]
    lo, hi = GOLDEN["premier"]["normal_bend"]
    assert lo <= st["shade.normal_bend"] <= hi                        # K1 equals asset.check
    assert r["legend"]["rows"] == ["ok/game", "dam/game"] and len(r["legend"]["cols"]) == 4
    r = get_op("blender.preview").call({"subject": "model:426", "lineup": "model:405,model:560",
                                        "passes": ["game", "clay"]})
    assert [e[1] for e in r["legend"]["entries"]] == ["premier", "sentinel", "sultan"]
    assert r["sheet"]["bytes"] <= GOLDEN["sheet_bytes_max"]


def test_no_thumbnails_or_pycache(live_work):
    gpl = Path(__file__).resolve().parents[2] / "blender" / "satk_blender"
    assert not list(gpl.rglob("__pycache__")), "Blender wrote bytecode into the add-on"
