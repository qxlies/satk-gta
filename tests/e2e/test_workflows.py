"""The agent workflows of docs/agent/workflows.md really work, within their documented call budgets.

Runs ``satk.docs.workflows`` (the executable form of S1-S8, also behind ``satk dev workflow-cost``) against
the workspace's built indexes and symbol DB: read-only queries, PNGs into ``work/out`` (regenerable). A
missing index/symdb is a skip with the build command, never an implicit build. S2 here uses the mock
target (the live Ariane variant is ``test_scenario.py::test_agent_scenario_ariane``); S5 needs Blender
(marker ``blender``); S6 needs ``target=game`` (WP-13/16) and is expected to skip.
"""

from __future__ import annotations

import pytest

from satk.docs import workflows as W

pytestmark = [pytest.mark.e2e, pytest.mark.game]


def _run(wid: str, **kw) -> dict:
    r = W.run(wid, **kw)
    if r["status"] == "skip":
        pytest.skip(r["reason"])
    assert r["status"] == "ok", r
    assert r["calls"] <= r["budget"], (r["calls"], r["budget"], r["steps"])
    return r


def test_s1_texture_usage():
    a = _run("S1")["answer"]
    assert a["textures"] == 257 and a["models"] == 470  # golden A.1 (ws_rooftarmac1)
    assert a["pixel_variants"] == 3


def test_s2_viewer_mock():
    r = _run("S2", target="mock")
    a = r["answer"]
    assert a["settled"] is True and a["marks"] >= 1 and "inst:lae2_stream0#4" in a["legend_sids"]
    assert r["images"] == 1  # one Read of the marks image, nothing else


def test_s3_crash_address():
    a = _run("S3")["answer"]
    assert (a["fn"], a["off"], a["src"]) == ("CGame::Process", "0x29", "game_sa/Game.cpp:78")  # golden A.4
    assert a["confidence"] == "high" and "HOOKPOS_CStreaming_Update_Caller" in a["patch_at_addr"]
    assert a["src_lines"] > 0


def test_s4_profile_diff():
    a = _run("S4")["answer"]
    assert a["summary"]["txd"]["added"] == 3  # A.2: ps3btns, sixaxis, x360btns
    assert a["textures"] > 0


@pytest.mark.blender
@pytest.mark.slow
def test_s5_blender_round_trip():
    a = _run("S5")["answer"]
    assert a["blend"].endswith(".blend") and a["render"].endswith(".png")
    assert a["export_files"]


def test_s6_needs_the_game_target():
    r = W.run("S6")
    assert r["status"] in ("skip", "ok")  # ok once WP-13/16 make target=game live


def test_s7_one_sql():
    r = _run("S7")
    assert r["calls"] == 1
    a = r["answer"]
    assert a["lod_models_without_col"] > 500 and a["hd_models_without_col"] <= 5


def test_s8_samp_overrides():
    a = _run("S8")["answer"]
    assert a["model_300"] == "lapdna" and a["model_300_layer"] == "samp"  # A.3
    assert a["summary"]["added"] > 1000 and a["summary"]["overridden"] >= 1
