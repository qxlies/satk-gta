"""Template plans from the vanilla game (read-only; marker ``game``; skipped without the vanilla index)."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from satk.kit.plan import template_plan

pytestmark = pytest.mark.game

#: Words that would mean vanilla geometry leaked into a plan.
GEOMETRY_KEYS = {"positions", "vertices", "verts", "faces", "normals", "uv", "uvs", "pixels", "tris_list"}


@pytest.fixture
def vanilla(clean_root):
    from satk.core.errors import SatkError
    from satk.index.api import open_index

    from satk.index.api import clear_cache

    try:
        db = open_index("vanilla")
        db.query("SELECT 1 FROM model LIMIT 1", [], limit=1)
    except SatkError as e:
        pytest.skip(f"vanilla index unavailable: {e}")
    yield db
    clear_cache()


def _walk(x, path=""):
    if isinstance(x, dict):
        for k, v in x.items():
            yield path + "/" + str(k), k, v
            yield from _walk(v, path + "/" + str(k))
    elif isinstance(x, list):
        for i, v in enumerate(x):
            yield from _walk(v, f"{path}[{i}]")


def _no_geometry(plan: dict) -> None:
    for where, k, v in _walk(plan):
        assert str(k).lower() not in GEOMETRY_KEYS, where
        if isinstance(v, list) and v and all(isinstance(e, (int, float)) for e in v):
            assert len(v) <= 12, f"{where}: a number list of {len(v)} (only RW matrices may hold 12)"


def test_premier_plan_frames_slots_presets(vanilla):
    p = template_plan(like="model:426", name="kittest")
    names = [f["name"] for f in p["frames"]]
    env = vanilla.query("SELECT f.name FROM model m JOIN model_link ml ON ml.id = m.id JOIN dff_frame f "
                        "ON f.dff_id = ml.dff_id WHERE m.id = 426 AND m.active = 1 ORDER BY f.idx", [], limit=200)
    vanilla_names = [(r[0] or "").strip().lower() for r in env["rows"]]
    assert p["counts"]["frames"] == 51 and len(names) == 51
    assert [n.lower() for n in names[1:]] == vanilla_names[1:] and names[0] == "kittest"   # vanilla order
    assert p["counts"]["slots"] == 22 and p["counts"]["presets"] >= 8
    assert {f["name"] for f in p["frames"] if f["role"] == "dummy"} >= {"wheel_lf_dummy", "door_lf_dummy",
                                                                       "ped_frontseat", "headlights", "exhaust"}
    slots = {f["name"]: f["slot"] for f in p["frames"] if f.get("part")}
    assert slots["chassis"] == "hd" and slots["door_lf_ok"] == "ok" and slots["chassis_vlo"] == "vlo"
    assert p["col"]["name"] == "kittest_col" and p["col"]["embedded"] and len(p["col"]["spheres"]) == 20
    assert p["col"]["spheres"][0]["surface"][0] == 63                       # CAR
    assert p["anchors"]["wheel_scale"] == 0.7 and p["ide"]["type"] == "car" and p["ide"]["handling"] == "PREMIER"
    assert {t["name"] for t in p["txd"]["own"]} == {"kittest92interior128", "kittest92wheel64"}
    _no_geometry(p)
    assert len(json.dumps(p)) < 40_000


def test_dims_scale_positions_and_keep_rotations(vanilla):
    a = template_plan(like="model:426")
    b = template_plan(like="model:426", dims=[a["dims"]["like"]["L"] * 1.1, a["dims"]["like"]["W"],
                                             a["dims"]["like"]["H"]])
    fa = {f["name"]: f for f in a["frames"]}
    fb = {f["name"]: f for f in b["frames"]}
    assert b["dims"]["target"]["L"] == pytest.approx(a["dims"]["like"]["L"] * 1.1, abs=1e-3)
    # wheel_lf_dummy hangs on the root: its y moves by 10 %, x and z stay, the rotation is the same
    wa, wb = fa["wheel_lf_dummy"]["matrix"], fb["wheel_lf_dummy"]["matrix"]
    assert wb[10] == pytest.approx(wa[10] * 1.1, abs=1e-3) and wb[9] == pytest.approx(wa[9], abs=1e-4)
    assert wb[:9] == wa[:9]
    assert b["anchors"]["wheel_scale"] == a["anchors"]["wheel_scale"]       # wheels keep their size


@pytest.mark.parametrize("kind,checks", [
    ("automobile", {"slots": 22}), ("bike", {"min_slots": 6}), ("boat", {"min_slots": 3}),
    ("heli", {"frame": "moving_rotor"}), ("plane", {"frame": "rudder"}), ("prop", {"slots": 1}),
    ("building", {"lod": True}), ("weapon", {"frame": "gunflash"}), ("ped", {"bones": 31}),
    ("pickup", {"slots": 1}), ("vehicle_upgrade", {"slots": 1}), ("interior_shell", {"min_slots": 1}),
    ("train", {"frame": "bogie_front"}), ("trailer", {"frame": "hookup"}), ("mtruck", {"min_slots": 5}),
    ("quad", {"frame": "handlebars"}), ("bmx", {"frame": "chainset"}), ("breakable", {"slots": 1}),
    ("animated_object", {"min_slots": 1}), ("interior_prop", {"slots": 1}),
])
def test_every_kind_has_a_plan(vanilla, kind, checks):
    t0 = time.perf_counter()
    p = template_plan(kind=kind, name="kittest")
    assert time.perf_counter() - t0 < 2.0
    assert p["kind"] == kind and p["frames"][0]["name"] == "kittest" and p["frames"][0]["role"] == "root"
    c = p["counts"]
    if "slots" in checks:
        assert c["slots"] == checks["slots"], c
    if "min_slots" in checks:
        assert c["slots"] >= checks["min_slots"], c
    if "frame" in checks:
        assert checks["frame"] in {f["name"].lower() for f in p["frames"]}
    if "bones" in checks:
        assert c["bones"] == checks["bones"] and p["anchors"]["ped_height"] == 1.84
        assert {f.get("bone_id") for f in p["frames"]} >= {0, 1, 2, 5, 24, 34}
    if checks.get("lod"):
        assert p["lod"]["name"] == "lodtest" and p["lod"]["like_name"].lower().startswith("lod")
        assert 0.1 < p["lod"]["ratio_tris"][1] < 0.3
    for f in p["frames"]:
        assert f["parent"] < f["i"], f                                   # parents before children
    _no_geometry(p)


def test_a_prop_can_ask_for_a_lod_slot(vanilla):
    """``--lod`` gives a prop the LOD name of a building (``lod`` + the name without its first 3 characters)."""
    from satk.core.errors import SatkError

    plain = template_plan(kind="prop", name="sa_bin1")
    assert not plain["lod"].get("name")
    p = template_plan(kind="prop", name="sa_bin1", lod=True)
    assert p["lod"]["name"] == "lodbin1" and p["kind"] == "prop" and p["counts"]["slots"] == 1
    assert template_plan(kind="building", name="kitbuilding")["lod"]["name"] == "lodbuilding"
    with pytest.raises(SatkError) as e:
        template_plan(kind="automobile", name="mycar", lod=True)
    assert e.value.code == "BAD_PARAMS" and "map models" in e.value.msg
    _no_geometry(p)


def test_template_cli_takes_lod(vanilla, run_cli):
    r = run_cli(["kit", "template", "--kind", "prop", "--name", "sa_bin1", "--lod", "--plan-only", "--json"])
    assert r.code == 0, r.out + r.err
    plan = json.loads(Path(r.json["plan"]).read_text(encoding="utf-8"))
    assert plan["lod"]["name"] == "lodbin1"
