"""The satk-bench resource: scripts compile with the server's Lua 5.1, the pure helpers agree with the Python
statistics, and the stage runner works in a simulated client with and without the sa-engine functions."""

from __future__ import annotations

import json
import random
import xml.etree.ElementTree as ET

import pytest

from satk.ingame import bench as B
from satk.ingame import bench_scenes as BS
from satk.ingame import resource as R

pytestmark = pytest.mark.engine

SCRIPTS = ("stats.lua", "client.lua")


@pytest.fixture
def make_bench(lua_dll):
    from benchsim import BenchSim

    sims = []

    def make(setup: str = ""):
        s = BenchSim(lua_dll, setup)
        sims.append(s)
        return s

    yield make
    for s in sims:
        s.close()


def test_meta_lists_the_scripts_and_the_export():
    meta = ET.parse(R.bench_source() / "meta.xml").getroot()
    assert [(s.get("src"), s.get("type")) for s in meta.findall("script")] == [("stats.lua", "client"), ("client.lua", "client")]
    assert [(e.get("function"), e.get("type")) for e in meta.findall("export")] == [("bench", "client")]
    for name in SCRIPTS:
        head = (R.bench_source() / name).read_text(encoding="utf-8").splitlines()[:6]
        assert "-- SPDX-License-Identifier: MIT" in head, name


@pytest.mark.parametrize("name", SCRIPTS)
def test_scripts_compile_with_the_servers_lua(lua_dll, name):
    from lua51 import Lua

    L = Lua(lua_dll)
    try:
        assert L.compile((R.bench_source() / name).read_text(encoding="utf-8"), name) is None
    finally:
        L.close()


def test_lua_frame_stats_equal_the_python_ones(make_bench):
    sim = make_bench()
    rnd = random.Random(5)
    frames = [round(rnd.uniform(5.0, 9.0) + (40.0 if rnd.random() < 0.01 else 0.0), 3) for _ in range(1500)]
    sim.run(f"F = {R.lua(frames)}")
    lua = sim.eval("SB.frameStats(F, 12345)")
    py = B.frame_stats(frames, 12345)
    for key in ("n", "avg_ms", "max_ms", "p50_ms", "p95_ms", "p99_ms", "p999_ms", "fps_avg", "low1_fps", "q"):
        assert lua[key] == pytest.approx(py[key], abs=0.0011), key
    assert lua["fps_wall"] == pytest.approx(py["fps_wall"], abs=0.01) and lua["integer_share"] == pytest.approx(py["integer_share"], abs=0.001)


def test_lua_path_clean_and_flatmax_helpers(make_bench):
    sim = make_bench()
    pts = BS.make_path([(0, 0), (100, 0), (100, 100)], 50.0)
    sim.run(f"P = {R.lua(pts)}")
    assert sim.eval("{SB.pathAt(P, 0)}") == [0, 0, 50]
    assert sim.eval("{SB.pathAt(P, 0.5)}") == [100, 0, 50]
    assert sim.eval("{SB.pathAt(P, 0.75)}") == [100, 50, 50]
    assert sim.eval("{SB.pathAt(P, 2)}") == [100, 100, 50]
    assert sim.eval("{SB.parseTime('18:30')}") == [18, 30] and sim.eval("{SB.parseTime('25:00')}") == []
    out = sim.eval("(function() local d = {} SB.flatMax({a = {b = 3, c = 1}, d = 2}, d) "
                   "SB.flatMax({a = {b = 1, c = 5}, d = 9}, d) return d end)()")
    assert out == {"a.b": 3, "a.c": 5, "d": 9}
    assert sim.eval("SB.clean({x = 1/3, f = print, s = 'ok', n = 0/0})") == {"x": 0.333, "s": "ok"}
    a = sim.eval("(function() local r = SB.lcg(7) return {r(), r(), r()} end)()")
    b = sim.eval("(function() local r = SB.lcg(7) return {r(), r(), r()} end)()")
    assert a == b and all(0 <= v < 1 for v in a)


def _run_stage(sim, stage: dict, *, slice_ms: float = 16.0, warmup=None, sample=None, index: int = 1) -> dict:
    assert sim.bench("begin", {"run": "t", "scene": "T"})["ok"]
    assert sim.bench("stage", {"stage": stage, "index": index, "warmup": warmup, "sample": sample})["ok"]
    for _ in range(100000):
        st = sim.bench("status")
        if not st["busy"]:
            break
        sim.pump(60, slice_ms)
    assert st["state"] == "running"
    return sim.bench("result", {"index": index})


def _stage(**kw) -> dict:
    d = {"id": "t", "title": "t", "env": {"time": "12:00", "weather": 1, "freeze": True},
         "camera": {"kind": "fixed", "pos": [0, 0, 10], "look": [10, 0, 10], "fov": 70}, "warmup": 1, "sample": 3}
    d.update(kw)
    return d


def test_hello_reports_features_without_the_sae_functions(make_bench):
    sim = make_bench()
    h = sim.bench("hello")
    assert h["version"] == 1 and h["sae"] is False
    f = h["features"]
    assert f["getProcessMemoryStats"] and f["dxGetStatus"] and f["engineStreamingGetUsedMemory"]
    assert not f["getEngineStats"] and not f["getEngineLimits"] and not f["getDrawDistanceInfo"]


def test_stage_without_sae_functions_still_measures_frames_and_va(make_bench):
    sim = make_bench()
    res = _run_stage(sim, _stage(), slice_ms=10.0)
    f = res["frames"]
    assert f["n"] > 250 and f["p50_ms"] == 10 and f["fps_avg"] == pytest.approx(100, abs=0.1)
    assert res["va"]["start_mib"] == pytest.approx(1500, abs=0.2) and res["va"]["growth_mib"] == 0
    assert res["clock"]["note"].startswith("getEngineStats") and "ratio" not in res["clock"]
    assert res["limits_max"] in ({}, []) and res["features"]["getEngineStats"] is False
    assert len(res["series"]) >= 3 and res["series"][0][2] == 10
    # the environment was frozen at the requested time and restored by finish
    assert sim.eval("Sim.weather") == 1 and sim.eval("Sim.minute") == 60000000
    assert sim.bench("finish")["ok"]
    assert sim.eval("Sim.weather") == 0 and sim.eval("Sim.minute") == 1000 and sim.eval("Sim.camera") is None
    assert sim.eval("Sim.hour") == 9 and sim.eval("Sim.min") == 30
    assert sim.eval("localPlayer.frozen") is False and sim.eval("localPlayer.col") is True
    assert sim.eval("{localPlayer.x, localPlayer.y, localPlayer.z}") == [10, 20, 30]


def test_stage_with_the_sae_functions_records_counters_and_the_clock(make_bench):
    sim = make_bench("Sim.enableSae(); Sim.timerRatio = 0.864")
    assert sim.bench("hello")["sae"] is True and sim.bench("hello")["profile"] == "sae"
    res = _run_stage(sim, _stage(), slice_ms=10.0)
    assert res["clock"]["ratio"] == pytest.approx(0.864, abs=0.002) and res["clock"]["timer_mode"] == "real"
    assert res["clock"]["frame_counter_hz"] == pytest.approx(30.0, abs=0.5)
    assert res["limits_max"]["counters.visibleEntities"] >= 200 and res["limits_max"]["capacity.lodList"] == 4000
    assert res["finish"]["draw"]["preset"] == "classic" and res["finish"]["engine"]["vaGuard"] == "ok"
    assert res["features"]["getEngineStats"] is True
    assert res["counts"]["streamed_end"]["ped"] == 0


def test_spawn_walk_and_cleanup(make_bench):
    sim = make_bench()
    peds = [{"model": 7, "x": i, "y": 0, "z": 13, "h": 0} for i in range(20)]
    cars = [{"model": 426, "x": 10 * i, "y": 5, "z": 13, "h": 270} for i in range(8)]
    res = _run_stage(sim, _stage(peds=peds, vehicles=cars, walk=True, center=[10, 0, 13], radius=5, turn_ms=500, seed=3),
                     slice_ms=20.0)
    assert res["counts"]["spawned"] == {"vehicles": 8, "peds": 20, "failed": 0}
    assert res["counts"]["streamed_max"] == {"vehicle": 8, "ped": 20, "object": 0}
    assert sim.eval("Sim.count('ped')") == 0 and sim.eval("Sim.count('vehicle')") == 0     # destroyed at the end
    assert sim.eval("localPlayer.frozen") is False
    # peds walked: control states were set and the rotation changed from the spawn heading
    sim2 = make_bench()
    sim2.bench("begin", {})
    sim2.bench("stage", {"stage": _stage(peds=peds[:3], walk=True, center=[0, 0, 13], radius=1, turn_ms=200, seed=3)})
    sim2.pump(100, 20.0)
    assert sim2.eval("Sim.els[1].ctl.forwards") is True and sim2.eval("Sim.els[1].rot") != 0
    assert sim2.bench("abort")["aborted"] is True and sim2.eval("Sim.count('ped')") == 0


def test_path_camera_follows_and_player_follows_the_camera(make_bench):
    sim = make_bench()
    stage = _stage(camera={"kind": "path", "points": BS.make_path([(0, 0), (900, 0)], 100.0), "agl": 150.0, "ahead": 0.02,
                           "drop": 30.0})
    sim.bench("begin", {})
    sim.bench("stage", {"stage": stage, "warmup": 0.2, "sample": 10})
    sim.pump(20, 16.0)                      # past the warm-up
    cam0 = sim.eval("Sim.camera")
    sim.pump(300, 16.0)                     # ~4.8 s of the 10 s sample
    cam1 = sim.eval("Sim.camera")
    assert cam1[0] > cam0[0] + 300 and cam1[2] >= 150 + 29                       # terrain following: ground 30 + agl 150
    assert abs(sim.eval("localPlayer.x") - cam1[0]) < 80 and sim.eval("localPlayer.frozen") is True
    sim.bench("abort")


def test_timelapse_scales_the_game_clock_to_the_sample(make_bench):
    sim = make_bench()
    stage = _stage(env={"time": "18:00", "weather": 1, "freeze": True, "timelapse": {"from": "18:00", "to": "19:30"}},
                   warmup=0.5, sample=90)
    sim.bench("begin", {})
    sim.bench("stage", {"stage": stage})
    assert sim.eval("Sim.hour") == 18 and sim.eval("Sim.minute") == 60000000          # held during the warm-up
    sim.pump(60, 16.0)
    assert sim.eval("Sim.minute") == 1000                                              # 90 s / 90 game minutes
    assert sim.eval("{Sim.hour, Sim.min}") == [18, 0]
    sim.bench("abort")


def test_weapon_probe_counts_the_shots_of_its_own_ped(make_bench):
    sim = make_bench("Sim.enableSae(); Sim.timerRatio = 0.9")
    probe = {"kind": "weapon", "weapon": 31, "slot": 5, "ammo": 99999, "skin": 7, "pos": [1600, -2593, 13.6], "h": 270,
             "target": [1660, -2593, 14.8]}
    res = _run_stage(sim, _stage(probe=probe, warmup=1, sample=5), slice_ms=16.0)
    p = res["probe"]
    assert p["kind"] == "weapon" and 40 <= p["shots"] <= 50 and p["shots_per_s"] == pytest.approx(9.0, abs=1.0)
    assert p["shots_per_game_s"] == pytest.approx(p["shots_per_s"] / 0.9, rel=0.03)
    assert sim.eval("Sim.count('ped')") == 0


def test_loader_probe_stops_at_the_va_yellow_line_and_releases(make_bench):
    sim = make_bench("Sim.va = 3000 * 1048576")
    probe = {"kind": "loader", "tex_side": 1024, "tex_mib": 4, "tex_per_step": 1, "dff_mib": 2, "dff_per_step": 1,
             "dff_path": "gen/probe.dff", "step_ms": 50, "yellow_mib": 3072, "max_steps": 800, "hold_ms": 500,
             "release_ms": 500}
    res = _run_stage(sim, _stage(probe=probe, warmup=0.2, sample=60), slice_ms=16.0)
    p = res["probe"]
    assert p["stop_reason"] == "yellow" and p["steps"] == 12 and p["loaded_mib"] == 72       # 12 x 6 MiB: 3000 + 72 >= 3072
    assert p["textures"] == 12 and p["dffs"] == 12 and p["tex_fail"] == 0
    assert res["va"]["peak_mib"] >= 3072 and p["points"][0][:2] == [1, 6]
    assert p["va_after_release_mib"] == pytest.approx(3000, abs=0.2) and p["va_at_release_mib"] == pytest.approx(3000, abs=0.2)
    assert sim.eval("Sim.count('texture')") == 0 and sim.eval("Sim.count('dff')") == 0
    assert res["frames"]["n"] < 60 * 60                                                    # ended early, not at the cap


def test_loader_probe_stops_on_failures_and_on_the_guard(make_bench):
    sim = make_bench("Sim.dffOk = false; Sim.maxTex = 3")
    probe = {"kind": "loader", "tex_per_step": 1, "dff_per_step": 1, "step_ms": 20, "yellow_mib": 3072, "hold_ms": 100,
             "release_ms": 100}
    res = _run_stage(sim, _stage(probe=probe, warmup=0.1, sample=30))
    assert res["probe"]["stop_reason"] == "load_failed" and res["probe"]["dff_fail"] >= 1
    assert any("engineLoadDFF" in w or "dff" in w.lower() for w in res.get("warn", [])) or res["probe"]["dff_fail"]
    sim2 = make_bench("Sim.enableSae(); Sim.guard = 'yellow'")
    res2 = _run_stage(sim2, _stage(probe=probe, warmup=0.1, sample=30))
    assert res2["probe"]["stop_reason"] == "guard:yellow" and res2["probe"]["steps"] == 1


def test_busy_unknown_and_error_paths(make_bench):
    sim = make_bench()
    sim.bench("begin", {})
    assert sim.bench("stage", {"stage": _stage()})["ok"]
    assert "running" in sim.bench("stage", {"stage": _stage()})["error"]
    assert "running" in sim.bench("begin", {})["error"]
    assert sim.bench("stage", {"stage": 5})["error"]
    assert "unknown" in sim.bench("nope")["error"]
    sim.bench("abort")
    assert sim.bench("stage", {"stage": {"title": "no id"}})["error"]
    assert sim.bench("result", {"index": 9})["error"]
    # a Lua error inside the frame step ends the stage with state=error instead of spamming the console
    sim.bench("begin", {})
    sim.bench("stage", {"stage": _stage(camera={"kind": "fixed"})})
    sim.pump(5)
    st = sim.bench("status")
    assert st["state"] == "error" and st["busy"] is False and "attempt to index" in st["err"]


def test_every_scene_runs_in_the_simulated_client_and_through_run_scene(make_bench):
    """The whole python side (stage plan, console lines, polling, result document) against the real Lua."""
    from benchsim import SimClient

    for sid in BS.scene_ids():
        sim = make_bench("Sim.enableSae(); Sim.va = 3040 * 1048576")
        console, fps = [], []
        doc = B.run_scene(SimClient(sim, fps=120.0, seconds=2.0), lambda line: console.append(line) or True, scene=sid,
                          label="t", run="r", duration=3.0, warmup=0.5, poll_s=0, sleep=lambda s: None,
                          server_fps=lambda n: fps.append(n) or True)
        assert doc["schema"] == B.SCHEMA and doc["scene"] == sid and len(doc["stages"]) == len(BS.scene(sid)["stages"])
        assert all(s["frames"]["n"] > 10 for s in doc["stages"]), sid
        json.dumps(doc)                                                    # serialisable
        if sid == "S1":
            assert console == [] and fps == [60, 144, 165, 240, None]
            assert [s["fps_limit"]["requested"] for s in doc["stages"]] == [60, 144, 165, 240]
            assert [s["fps_limit"]["lua"] for s in doc["stages"]] == [True, False, False, False]    # setFPSLimit caps at 100
            assert doc["summary"]["clock_ratio_min"] == pytest.approx(1.0, abs=0.01)
        if sid == "S5":
            assert doc["stages"][0]["counts"]["spawned"]["vehicles"] == 64
        if sid == "S6":
            assert doc["stages"][0]["counts"]["spawned"]["peds"] == 110
        if sid == "S8":
            assert doc["summary"]["shots_per_s"] > 5
        if sid == "S9":
            assert doc["stages"][0]["probe"]["stop_reason"] in ("yellow", "max_steps")
        if sid == "S4":
            assert doc["stages"][0]["timelapse"]["minutes"] == 90
