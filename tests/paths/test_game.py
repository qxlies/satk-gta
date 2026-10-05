"""satk.paths on the real vanilla game (gta-sa-clean, read-only; M2-05 acceptance).

Numbers from research report 09 (gta-flow on the stock 1.0 US corpus): 64 regions, 68 237 nodes
(30 587 vehicle, 37 650 pedestrian), 31 466 navis, 143 622 links; the unchanged network compiles
byte for byte; moving node 15:6 by -2 on X changes region 15 only. Everything is written to a private
``SATK_PATHS_WORK`` under the test's temp directory, never the shared ``work``.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from satk.core import config as _config
from satk.core.registry import invoke

pytestmark = pytest.mark.game


@pytest.fixture(scope="module")
def private_work(tmp_path_factory):
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("SATK_PATHS_WORK", str(tmp_path_factory.mktemp("paths-work")))
        _config.reset()
        if not _config.load().profile("vanilla").configured:
            pytest.skip("no vanilla game root")
        yield
    _config.reset()


@pytest.fixture(scope="module")
def imported(private_work):
    env = invoke("paths.import", {"profile": "vanilla", "force": True})
    assert env["ok"], env
    return env


def test_vanilla_numbers_match_report_09(imported):
    env = imported
    assert env["areas"] == 64
    assert (env["nodes"], env["vehicle"], env["ped"]) == (68237, 30587, 37650)
    assert (env["navis"], env["links"]) == (31466, 143622)
    assert env["car"] + env["boat"] == env["vehicle"] and env["boat"] > 0
    assert env["sources"] == {"models/gta3.img": 64}
    assert env["check"] == "valid: 0 errors, 4 warnings"  # the 4 pre-existing exceptional navis
    assert "warn" not in env


def test_near_grove_street(imported):
    env = invoke("paths.near", {"x": 2495, "y": -1687})
    assert env["ok"] and env["n"] > 0 and env["total"] >= env["n"]
    kinds = {r[1] for r in env["rows"]}
    assert "ped" in kinds and "car" in kinds
    assert all(r[3] <= 30 for r in env["rows"]) and all(r[6] for r in env["rows"])
    assert "warn" not in env
    node = invoke("paths.node", {"node": "15:6"})
    assert node["pos"] == [2502.0, -1669.75, 13.0] and node["kind"] == "car"


def test_unchanged_network_compiles_byte_identical(imported):
    env = invoke("paths.compile", {"write": "all", "name": "roundtrip"})
    assert env["ok"], env
    assert (env["areas"], env["identical"], env["byte_identical"], env["written"]) == (64, 64, True, 64)
    assert env["oracle"] == "valid: 0 errors, 4 warnings"
    from satk.paths.db import PathsDB, db_path

    with PathsDB(db_path("vanilla")) as db:
        src = {r["area"]: r["sha256"] for r in db.area_rows()}
    out = Path(env["out"])
    got = {int(p.stem[5:]): hashlib.sha256(p.read_bytes()).hexdigest() for p in out.glob("nodes*.dat")}
    assert got == src


def test_edit_moves_one_node_in_one_region(imported):
    env = invoke("paths.compile", {"edits": {"15:6": [2500, -1669.75, 13]}, "name": "moved"})
    assert env["ok"], env
    assert env["changed"] == [15] and env["identical"] == 63 and env["files"] == ["nodes15.dat"]
    assert env["oracle"].startswith("valid: 0 errors")


def test_export_grove_overlay(imported):
    env = invoke("paths.export", {"area": [2400, -1750, 2600, -1600], "kind": "car"})
    assert env["ok"] and env["nodes"] > 20 and env["segments"] >= env["nodes"] - 1 and env["navis"] > 0
