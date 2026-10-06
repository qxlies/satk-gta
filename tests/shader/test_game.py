"""``satk.shader`` against the vanilla index (marker ``game``; read-only).

* ``--kind road`` holds the well-known asphalt textures of all three cities and none of the signs, vehicle bodies or
  wheels that contain "road" in their names; its exact plan resolves to exactly the kind;
* the other kinds find their anchor textures (``waterclear256``, ``vehiclegrunge256``, CJ's clothes ...);
* a model and an area selection give that model's / place's textures; every kind's plan is exact.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.game


def _skip(reason: str):
    if os.environ.get("SATK_TEST_NO_SKIP") == "1":
        pytest.fail(reason, pytrace=False)
    pytest.skip(reason)


@pytest.fixture(scope="module")
def world():
    from satk.core.errors import SatkError
    from satk.index.api import open_index
    from satk.shader.names import load_world

    try:
        db = open_index("vanilla")
        return db, load_world(db)
    except SatkError as e:
        _skip(f"no vanilla index: {e.msg}")


def test_kind_road_is_sane(world):
    from satk.shader.names import kind_names
    from satk.shader.wild import cover, resolve

    db, w = world
    road = kind_names(w, "road")
    assert 150 < len(road) < 600
    assert {"sf_road5", "tar_1line256hv", "vegasroad1_256", "crossing_law", "roadnew4_256", "tar_freewyleft",
            "cos_hiwaymid_256", "sf_junction5", "dt_road"} <= road
    assert not road & {"cj_road_sign1", "roadsign01_128", "broadway92body256a", "wheel_offroad64", "ws_roadpost",
                       "carparkdoor1_256", "gun_target1"}
    plan = cover(road, w.names)
    assert plan.exact and resolve(plan.ops(), w.names) == road
    assert len(plan.picks) < len(road) / 5


def test_other_kinds_find_their_anchors(world):
    from satk.shader.names import kind_names, kinds
    from satk.shader.wild import cover, resolve

    _db, w = world
    anchors = {"water": {"waterclear256"}, "vehicle_paint": {"vehiclegrunge256"}, "vehicle": {"vehiclegeneric256"},
               "tree": {"newtreeleaves128", "sm_pinetreebit"}, "glass": {"cj_frame_glass"},
               "grass": {"desgreengrass"}, "pavement": {"sf_pave6"}, "sand": {"pavebsand256"}}
    for k in kinds():
        got = kind_names(w, k)
        assert got, k
        assert anchors.get(k, set()) <= got, (k, anchors.get(k, set()) - got)
        if len(got) <= 600:
            plan = cover(got, w.names)
            assert plan.exact and resolve(plan.ops(), w.names) == got, k


def test_model_and_area_selections(world, run_cli):
    from satk.shader.names import select

    db, w = world
    inf = select(db, w, model="model:411")
    assert {"vehiclegrunge256", "vehiclegeneric256"} <= inf.names and any(n.startswith("infernus") for n in inf.names)
    grove = select(db, w, kind="road", area=[2495, -1666, 150])
    assert grove.names and grove.names <= select(db, w, kind="road").names
    env = run_cli(["shader", "textures", "--kind", "road", "--area", "2495", "-1666", "150", "--json"]).json
    assert env["ok"] and env["spill"]["placements_outside"] > 0


def test_textures_cli_on_vanilla(world, run_cli):
    env = run_cli(["shader", "textures", "--kind", "water", "--json"]).json
    assert env["ok"] and "waterclear256" in {r[0] for r in env["rows"]}
    assert env["plan"]["exact"]


def test_generated_wet_roads_glue_hits_exactly_the_road_kind(world, tmp_path):
    from satk.shader import check as C
    from satk.shader import fx
    from satk.shader.names import kind_names
    from satk.shader.templates import render
    from satk.shader.wild import cover

    _db, w = world
    road = kind_names(w, "road")
    plan = cover(road, w.names)
    for fname, text in render("wet_roads", "wr", apply=plan.apply, remove=plan.remove, selection="kind road").items():
        (tmp_path / fname).write_text(text, encoding="utf-8")
    eff = fx.load(tmp_path / "wet_roads.fx")
    issues, info = C.lua_checks(eff, C.lua_refs([tmp_path / "client.lua"]), universe=w.names)
    assert [i for i in issues if i.sev != "info"] == []
    assert info["textures"] == len(road)
