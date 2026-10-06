"""satk-testdrive in a simulated MTA: model loading, spawning, hot reload and every check suite.

The real resource scripts run in the fork's Lua 5.1 against ``mta_sim.lua``; the Python runner of
:mod:`satk.ingame.checks` drives the jobs through :class:`tdsim.SimBridge`, takes the frames and
gives the verdicts. Toy physics: the numbers are not the game's, the code paths are.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from satk.core import paths
from satk.ingame import checks as CH
from satk.ingame import spots as SP

pytestmark = pytest.mark.engine

VANILLA = """
Sim.components.vanilla = {"chassis", "wheel_lf_dummy", "wheel_rf_dummy", "wheel_lb_dummy", "wheel_rb_dummy",
  "bonnet_dummy", "boot_dummy", "door_lf_dummy", "door_rf_dummy", "door_lr_dummy", "door_rr_dummy",
  "bump_front_dummy", "bump_rear_dummy", "windscreen_dummy", "wing_lf_dummy", "wing_rf_dummy", "exhaust"}
Sim.dummies.vanilla = {light_front_main = {0.7, 2.2, 0.1}, light_rear_main = {0.7, -2.4, 0.1},
  exhaust = {0.5, -2.5, -0.3}, engine = {0, 1.8, 0.2}}
"""


def mdl(key: str, kind: str, mode: str, base: int, files=("dff", "txd"), **kw) -> dict:
    return {"key": key, "kind": kind, "mode": mode, "base": base, "label": key, "ref": True,
            "files": {k: f"m/{key}.{k}" for k in files}, **kw}


def manifest(*models, rev: str = "r1") -> dict:
    return {"rev": rev, "version": 1, "label": "test", "defaults": {"spot": "grove", "time": "12:00", "weather": 0},
            "models": list(models), "spots": SP.SPOTS, "geom": SP.GEOM}


def mod_path(key: str, ext: str = "dff") -> str:
    return f":satk-testdrive-mod/m/{key}.{ext}"


@pytest.fixture
def bridge_for(make_sim, satk_home, monkeypatch):
    from tdsim import SimBridge

    monkeypatch.setattr(CH.time, "sleep", lambda s: None)

    def make(m: dict, knobs: str = ""):
        sim = make_sim(m, VANILLA + knobs)
        return sim, SimBridge(sim)

    return make


def _suite(bridge, spec: dict, kind: str, **kw) -> dict:
    out = paths.work("out", "ingame", spec["key"], kind)
    return CH.run_suite(bridge, spec, kind=kind, checks=kw.pop("checks", CH.SUITES[kind]), reference=kw.pop("ref", True),
                        out_dir=out, w=64, h=36, timeout=kw.pop("timeout", 600))


def _rows(res: dict) -> dict[str, list]:
    return {r[0]: r for r in res["rows"]}


def test_models_load_and_the_client_reports(bridge_for):
    sim, b = bridge_for(manifest(mdl("car", "vehicle", "new", 426), mdl("gun", "weapon", "replace", 348, weapon=24)))
    st = b.client("status")
    models = {m["key"]: m for m in st["models"]}
    assert st["rev"] == st["applied"] == "r1" and st["content"] is True
    assert models["car"]["ok"] and models["car"]["id"] > 20000 and models["car"]["mode"] == "new"
    assert models["gun"]["ok"] and models["gun"]["id"] == 348
    assert sim.eval("Sim.replaced")[str(models["car"]["id"])] == mod_path("car")
    srv = b.server("status")
    assert srv["rev"] == "r1" and srv["content"] == "running"
    p = srv["players"][0]
    assert p["ready"] and p["rev"] == "r1" and p["models"]["car"]["ok"] and p["request_model"] is True


def test_a_missing_file_is_reported(bridge_for):
    sim, b = bridge_for(manifest(mdl("car", "vehicle", "new", 426)), f'Sim.failLoad["{mod_path("car", "txd")}"] = true')
    m = {x["key"]: x for x in b.client("status")["models"]}
    assert m["car"]["ok"] is False and "TXD" in m["car"]["err"]
    assert b.server("status")["players"][0]["models"]["car"]["ok"] is False


def test_spawn_drive_props_and_model_swap(bridge_for):
    sim, b = bridge_for(manifest(mdl("car", "vehicle", "new", 426, handling={"maxVelocity": 250.0})))
    r = b.server("spawn", {"model": "mod", "kind": "vehicle", "spot": "wall", "drive": True, "camera": "chase"})
    assert r["spot"] == "wall" and r["driving"] is True and r["model"] == 426 and r["element"] == "satk.td.1"
    sim.pump(40)
    veh = sim.eval('(function() local v = Sim.client.getElementByID("satk.td.1") return {model = v.model, '
                   'key = v.data["satk.td.key"], h = v.handling.maxVelocity, driver = v.driver == Sim.player} end)()')
    car_id = {m["key"]: m for m in b.client("status")["models"]}["car"]["id"]
    assert veh == {"model": car_id, "key": "car", "h": 250.0, "driver": True}
    walls = sim.eval('(function() local n = 0 for _, e in ipairs(Sim.els) do if e.alive and e._type == "object" and '
                     'e.model == 8650 then n = n + 1 end end return n end)()')
    assert walls == 1
    assert b.server("clear")["removed"] == 1
    assert b.server("spawn", {"model": 411, "kind": "vehicle", "spot": "runway"})["model"] == 411
    with pytest.raises(Exception) as e:
        b.server("spawn", {"model": "nope"})
    assert "unknown model" in str(e.value)
    pose = b.client("pose", {"camera": "side", "target": "auto"})
    assert len(pose["pos"]) == 3 and pose["fov_h_deg"] == 60


def test_hot_reload_applies_the_new_rev(bridge_for):
    sim, b = bridge_for(manifest(mdl("car", "vehicle", "replace", 426)))
    sim.set_manifest(manifest(mdl("car", "vehicle", "replace", 426), rev="r2"))
    r = b.server("restart")
    assert r["queued"] == "restart" and r["rev"] == "r1"
    sim.pump(20)
    p = b.server("status")["players"][0]
    assert p["rev"] == "r2" and p["total_ms"] >= 0 and p["models"]["car"]["ok"]
    assert b.client("status")["applied"] == "r2"


def test_vehicle_suite_new_mode_side_by_side(bridge_for):
    knobs = f"""Sim.components["{mod_path('car')}"] = {{"chassis", "wheel_lf_dummy", "wheel_rf_dummy", "wheel_lb_dummy",
      "wheel_rb_dummy", "bonnet_dummy", "door_lf_dummy", "door_rf_dummy", "bump_rear_dummy", "exhaust"}}"""
    sim, b = bridge_for(manifest(mdl("car", "vehicle", "new", 426)), knobs)
    spec = sim.td("manifest")["models"][0]
    res = _suite(b, spec, "vehicle")
    rows = _rows(res)
    assert list(rows) == CH.SUITES["vehicle"]
    assert all(r[1] != "error" for r in res["rows"]), res["rows"]
    assert rows["components"][1] == "warn" and "bump_front_dummy" in rows["components"][5]
    assert rows["rest"][1] == "pass" and rows["speed"][1] == "pass" and rows["crash"][1] == "pass"
    assert rows["damage"][1] == "warn" and "boot_dummy" in rows["damage"][5]
    assert rows["components"][6].endswith("components-exhaust-both.png")
    report = (Path(res["report"])).read_text(encoding="utf-8")
    assert '"ref_id": 426' in report and '"passes": 1' in report
    names = [Path(c["file"]).name for c in b.captures]
    assert "speed-run-both.png" in names and "crash-impact-both.png" in names and len(names) >= 11
    st = b.client("status")
    assert st.get("job") is None
    assert sim.eval("Sim.controls") is True  # the player got the controls back
    assert sim.eval("Sim.camera.target == Sim.player")


def test_vehicle_replace_mode_runs_the_reference_in_a_second_pass(bridge_for):
    knobs = f'Sim.vmax["{mod_path("car")}"] = 140'
    sim, b = bridge_for(manifest(mdl("car", "vehicle", "replace", 426)), knobs)
    spec = sim.td("manifest")["models"][0]
    res = _suite(b, spec, "vehicle", checks=["speed", "damage"])
    rows = _rows(res)
    assert rows["speed"][1] == "warn" and "vanilla handling" in rows["speed"][5]
    assert rows["damage"][1] == "pass"
    assert rows["speed"][6].endswith("speed-run-pair.png") and Path(rows["speed"][6]).is_file()
    names = {Path(c["file"]).name for c in b.captures}
    assert {"speed-run-mod.png", "speed-run-ref.png", "damage-front-mod.png", "damage-front-ref.png"} <= names
    assert sim.eval("Sim.replaced[426]") == mod_path("car")  # the replacement is back after the reference pass


def test_vehicle_without_wheels_on_the_ground_fails(bridge_for):
    sim, b = bridge_for(manifest(mdl("car", "vehicle", "new", 426)), f'Sim.noWheels["{mod_path("car")}"] = true')
    spec = sim.td("manifest")["models"][0]
    rows = _rows(_suite(b, spec, "vehicle", checks=["rest"]))
    assert rows["rest"][1] == "fail" and "only 0 wheels" in rows["rest"][5]


def test_object_suite_detects_a_missing_collision(bridge_for):
    sim, b = bridge_for(manifest(mdl("box", "object", "new", 1337, files=("dff", "txd", "col"))),
                        f'Sim.softCol["{mod_path("box")}"] = true')
    spec = sim.td("manifest")["models"][0]
    res = _suite(b, spec, "object")
    rows = _rows(res)
    assert rows["collision_ped"][1] == "fail" and "(vanilla blocks)" in rows["collision_ped"][5]
    assert rows["collision_car"][1] == "fail"
    assert rows["lod"][1] == "info" and rows["night"][1] == "info"


def test_object_suite_passes_with_collision(bridge_for):
    sim, b = bridge_for(manifest(mdl("box", "object", "new", 1337, files=("dff", "txd", "col"))))
    spec = sim.td("manifest")["models"][0]
    rows = _rows(_suite(b, spec, "object", checks=["collision_ped", "collision_car"]))
    assert rows["collision_ped"][1] == "pass" and rows["collision_car"][1] == "pass"


def test_ped_suite_finds_stretched_bones(bridge_for):
    sim, b = bridge_for(manifest(mdl("guy", "ped", "new", 7)), f'Sim.stretch["{mod_path("guy")}"] = true')
    spec = sim.td("manifest")["models"][0]
    rows = _rows(_suite(b, spec, "ped"))
    assert rows["anims"][1] == "fail" and "r_upper_arm" in rows["anims"][5]
    assert rows["walk"][1] == "pass"


def test_ped_suite_passes_for_a_sound_skeleton(bridge_for):
    sim, b = bridge_for(manifest(mdl("guy", "ped", "new", 7)))
    spec = sim.td("manifest")["models"][0]
    rows = _rows(_suite(b, spec, "ped", checks=["anims"]))
    assert rows["anims"][1] == "pass", rows["anims"]


def test_weapon_suite(bridge_for):
    sim, b = bridge_for(manifest(mdl("deagle", "weapon", "replace", 348, weapon=24)))
    spec = sim.td("manifest")["models"][0]
    rows = _rows(_suite(b, spec, "weapon"))
    assert rows["held"][1] == "pass" and rows["fire"][1] == "pass" and "shots fired" in rows["fire"][2]


def test_job_errors_and_busy(bridge_for):
    sim, b = bridge_for(manifest(mdl("car", "vehicle", "new", 426)))
    bad = b.client("check.start", {"check": "nope", "key": "car"})
    info = b.client("job", {"id": bad["id"]})
    assert info["state"] == "error" and "no check nope" in info["failure"]
    first = b.client("check.start", {"check": "speed", "key": "car"})
    with pytest.raises(Exception) as e:
        b.client("check.start", {"check": "rest", "key": "car"})
    assert "already running" in str(e.value)
    assert b.client("job.cancel", {"id": first["id"]})["state"] == "cancelled"
    sim.pump(3)
    assert b.client("job", {"id": first["id"]})["state"] == "cancelled"
    assert b.client("status").get("job") is None
    assert sim.eval("Sim.controls") is True


def test_check_operation_end_to_end(bridge_for, monkeypatch):
    from satk.ingame import ops as O

    sim, b = bridge_for(manifest(mdl("car", "vehicle", "new", 426)))
    monkeypatch.setattr(O, "_bridge", lambda need_client=False: b)
    env = O.ingame_check(only=["rest", "lights"], width=64, height=36)
    assert env["cols"] == ["check", "verdict", "value", "ref", "expect", "note", "frame"]
    assert [r[:2] for r in env["rows"]] == [["rest", "pass"], ["lights", "pass"]]
    assert env["model"] == "car (vehicle, new, base 426)" and env["counts"] == {"pass": 2}
    assert Path(env["report"]).is_file() and Path(env["rows"][1][6]).name == "lights-front-pair.png"
    b.client("check.start", {"check": "speed", "key": "car"})
    with pytest.raises(Exception) as e:
        O.ingame_check()
    assert getattr(e.value, "code", "") == "BUSY"


def test_visual_checks_of_a_new_model_with_a_txd_run_the_reference_alone(bridge_for):
    sim, b = bridge_for(manifest(mdl("car", "vehicle", "new", 426), mdl("bare", "vehicle", "new", 411, files=("dff",))))
    models = {m["key"]: m for m in b.client("status")["models"]}
    assert models["car"]["shared_txd"] is True and "shared_txd" not in models["bare"]
    m = sim.td("manifest")["models"]
    _suite(b, m[0], "vehicle", checks=["dirt", "rest"])
    names = {Path(c["file"]).name for c in b.captures}
    assert {"dirt-dirty-mod.png", "dirt-dirty-ref.png", "rest-wheels-both.png"} <= names
    car_id = models["car"]["id"]
    assert sim.eval(f"Sim.replaced[{car_id}]") == mod_path("car")  # loaded again after the reference pass
    b.captures.clear()
    _suite(b, m[1], "vehicle", checks=["dirt"])
    assert [Path(c["file"]).name for c in b.captures] == ["dirt-dirty-both.png"]


def test_mod_wheel_size_is_undone_for_the_reference_pass(bridge_for):
    sim, b = bridge_for(manifest(mdl("car", "vehicle", "replace", 426, wheels=[0.9, 0.85])))
    spec = sim.td("manifest")["models"][0]
    _suite(b, spec, "vehicle", checks=["rest"])
    log = sim.eval("Sim.wheelLog")
    assert log[0] == [426, "front_axle", 0.9] and [426, "front_axle", 0.7] in log  # vanilla size for the reference
    assert sim.eval('Sim.wheelSize["426front_axle"]') == 0.9  # the mod's size is back afterwards
