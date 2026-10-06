"""satk.ingame.checks without a game: verdicts from canned numbers, the job protocol, side-by-side frames."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from satk.core import paths
from satk.core.errors import SatkError
from satk.ingame import checks as CH
from satk.saap import png as P

COMPS = ["chassis", "wheel_lf_dummy", "wheel_rf_dummy", "wheel_lb_dummy", "wheel_rb_dummy", "door_lf_dummy",
         "bump_front_dummy"]


def V(kind, check, mod, ref=None, spec=None, state="done", notes=None):
    lanes = {"mod": mod} if ref is None else {"mod": mod, "ref": ref}
    job = {"state": state, "result": {"lanes": lanes, "notes": notes or []}}
    return CH.verdict(kind, check, job, spec or {})


def test_components():
    ex = {"exhaust": [0.5, -2.4, -0.3], "light_front_main": [0.7, 2.2, 0.1]}
    bb = [-1, -2.6, -1, 1, 2.6, 1]
    ok = V("vehicle", "components", {"components": COMPS, "dummies": ex, "bbox": bb},
           {"components": COMPS, "dummies": ex})
    assert ok[0] == "pass" and ok[1].startswith("7 frames")
    warn = V("vehicle", "components", {"components": COMPS[:-1], "dummies": ex, "bbox": bb},
             {"components": COMPS, "dummies": ex})
    assert warn[0] == "warn" and "bump_front_dummy" in warn[4]
    fail = V("vehicle", "components", {"components": COMPS[1:], "dummies": ex, "bbox": bb},
             {"components": COMPS, "dummies": ex})
    assert fail[0] == "fail" and "chassis" in fail[4]
    no_ex = V("vehicle", "components", {"components": COMPS, "dummies": {}, "bbox": bb},
              {"components": COMPS, "dummies": ex})
    assert no_ex[0] == "warn" and "no exhaust dummy" in no_ex[4]
    outside = V("vehicle", "components", {"components": COMPS, "dummies": {"exhaust": [0, -5, 0]}, "bbox": bb})
    assert outside[0] == "warn" and "outside the body" in outside[4]


def test_rest_and_speed():
    ref = {"wheels": 4, "ride_m": 0.9, "settle_s": 1.0}
    assert V("vehicle", "rest", {"wheels": 4, "ride_m": 0.95, "settle_s": 1.2, "gap_m": 0.1}, ref)[0] == "pass"
    v = V("vehicle", "rest", {"wheels": 2, "ride_m": 0.9, "settle_s": 1}, ref)
    assert v[0] == "fail" and "only 2 wheels" in v[4]
    v = V("vehicle", "rest", {"wheels": 4, "ride_m": 1.2, "settle_s": 4.0}, ref)
    assert v[0] == "warn" and "higher" in v[4] and "bouncing" in v[4]
    r = {"top_kmh": 200.0, "t100_s": 6.0, "plateau_s": 20}
    assert V("vehicle", "speed", {"top_kmh": 198.0, "t100_s": 6.2, "plateau_s": 20}, r)[0] == "pass"
    v = V("vehicle", "speed", {"top_kmh": 160.0, "t100_s": 6.2, "plateau_s": 20}, r)
    assert v[0] == "warn" and "-20% top speed" in v[4]
    spec = {"handling": {"maxVelocity": 260.0}, "expect": {"max_vel": 260.0, "vanilla_max_vel": 200.0}}
    v = V("vehicle", "speed", {"top_kmh": 240.0, "t100_s": 5.0, "plateau_s": 20}, r, spec)
    assert v[0] == "info" and v[3].startswith("maxVelocity 260 km/h (mod handling)")
    assert V("vehicle", "speed", {"top_kmh": 150.0, "t100_s": 9, "plateau_s": 20}, r, spec)[0] == "warn"
    v = V("vehicle", "speed", {"top_kmh": 90.0, "lost_s": 3.5, "dist_m": 100}, r)
    assert v[0] == "fail" and "left its lane" in v[4] and "still accelerating" in v[1]


def test_crash_damage_lights():
    ref = {"penetration_m": 0.02, "panels_damaged": 2, "doors_damaged": 0}
    assert V("vehicle", "crash", {"passed": True, "speed_kmh": 50}, ref)[0] == "fail"
    assert V("vehicle", "crash", {"penetration_m": 0.6, "panels_damaged": 2}, ref)[0] == "warn"
    v = V("vehicle", "crash", {"penetration_m": 0.0, "panels_damaged": 0, "doors_damaged": 0}, ref)
    assert v[0] == "warn" and "no damage reaction" in v[4]
    assert V("vehicle", "crash", {"penetration_m": 0.05, "panels_damaged": 1}, ref)[0] == "pass"
    parts = {p: True for p in ("bonnet_dummy", "door_lf_dummy", "door_rf_dummy", "bump_front_dummy", "bump_rear_dummy")}
    v = V("vehicle", "damage", {"parts": dict(parts, bonnet_dummy=False)}, {"parts": parts})
    assert v[0] == "warn" and "bonnet_dummy" in v[4]
    assert V("vehicle", "damage", {"parts": parts})[0] == "pass"
    d = {"light_front_main": [0.7, 2.2, 0.1], "light_rear_main": [0.7, -2.4, 0.1]}
    assert V("vehicle", "lights", {"dummies": d}, {"dummies": d})[0] == "pass"
    v = V("vehicle", "lights", {"dummies": {"light_front_main": [0.7, -2.2, 0.1]}}, {"dummies": d})
    assert v[0] == "warn" and "behind the centre" in v[4] and "no rear light" in v[4]


def test_objects_peds_weapons_and_errors():
    assert V("object", "collision_ped", {"passed": True}, {"blocked": True})[0] == "fail"
    assert V("object", "collision_ped", {"blocked": True, "height": 0.2})[0] == "info"
    assert V("object", "collision_car", {"blocked": True, "gap_m": 0.1}, {"blocked": True})[0] == "pass"
    assert V("object", "lod", {"lod_distance": 50}, {"lod_distance": 300})[0] == "warn"
    assert V("object", "lod", {"lod_distance": 300}, {"lod_distance": 300})[0] == "info"
    segs = {"spine": {"len": 0.15, "dev": 0.0}, "r_forearm": {"len": 0.25, "dev": 0.0}}
    ok = {"played": ["WALK_civi", "run_civi", "IDLE_chat", "XPRESSscratch", "handsup", "FightA_1"], "anims": 6,
          "segments": segs}
    assert V("ped", "anims", ok, ok)[0] == "pass"
    v = V("ped", "anims", dict(ok, played=ok["played"][:5]), ok)
    assert v[0] == "fail" and "FightA_1" in v[4]
    v = V("ped", "anims", dict(ok, segments={"spine": {"len": 0.15, "dev": 0.2}}), ok)
    assert v[0] == "fail" and "spine changes length by 20%" in v[4]
    v = V("ped", "anims", dict(ok, segments={"spine": {"len": 0.3, "dev": 0.0}}), ok)
    assert v[0] == "warn" and "spine x2.00" in v[4]
    assert V("ped", "walk", {"moved_m": 0.2})[0] == "fail"
    assert V("weapon", "held", {"weapon": 23, "muzzle_hand_aim_m": 0.3}, None, {"weapon": 24})[0] == "fail"
    v = V("weapon", "held", {"weapon": 24, "muzzle_hand_aim_m": 0.6}, {"muzzle_hand_aim_m": 0.3}, {"weapon": 24})
    assert v[0] == "warn"
    assert V("weapon", "fire", {"fired": 0})[0] == "fail" and V("weapon", "fire", {"fired": 4})[0] == "pass"
    assert CH.verdict("vehicle", "rest", {"state": "error", "error": "boom"}, {})[:1] == ("error",)
    assert CH.verdict("vehicle", "nope", {"state": "done", "result": {"lanes": {}}}, {})[0] == "error"
    v = V("weapon", "fire", {"fired": 4}, notes=["2 handling properties were rejected"])
    assert "rejected" in v[4]


class FakeBridge:
    """Plays a job: running -> shot -> running -> done (or never ends)."""

    def __init__(self, states, fail_capture=False):
        self.states = list(states)
        self.calls = []
        self.fail_capture = fail_capture

    def client(self, cmd, args=None):
        self.calls.append((cmd, args))
        if cmd == "check.start":
            return {"id": "j1"}
        if cmd == "job":
            return self.states.pop(0) if len(self.states) > 1 else self.states[0]
        return {"state": "running"}

    def capture(self, pose, prefix, *, w, h, settle=None):
        self.calls.append(("capture", {"pose": pose, "prefix": str(prefix), "settle": settle}))
        if self.fail_capture:
            raise SatkError("NOT_READY", "minimized")
        path = P.write(str(prefix) + ".png", w, h, bytes(w * h * 3))
        return {"files": {"color": path.as_posix()}}


def test_run_job_takes_frames_and_resumes(satk_home, monkeypatch):
    monkeypatch.setattr(CH.time, "sleep", lambda s: None)
    out = paths.work("out", "ingame", "t")
    pose = {"pos": [0, 0, 5], "look": [0, 10, 0], "fov_h_deg": 60}
    b = FakeBridge([{"state": "running"}, {"state": "shot", "shot": {"name": "front-both", "pose": pose, "settle": 20}},
                    {"state": "done", "result": {"lanes": {"mod": {"fired": 1}}}}])
    r = CH.run_job(b, check="fire", key="gun", kind="weapon", reference=True, out_dir=out, w=32, h=16, timeout=30)
    assert r["state"] == "done" and r["shots"][0]["name"] == "front-both"
    assert Path(r["shots"][0]["file"]).name == "fire-front-both.png"
    cap = next(a for c, a in b.calls if c == "capture")
    assert cap["pose"] == pose and cap["settle"] == 20
    resume = next(a for c, a in b.calls if c == "job.resume")
    assert resume == {"id": "j1", "file": r["shots"][0]["file"]}
    b2 = FakeBridge([{"state": "shot", "shot": {"name": "x-mod"}}, {"state": "error", "failure": "boom"}],
                    fail_capture=True)
    r2 = CH.run_job(b2, check="held", key="gun", kind="weapon", reference=True, out_dir=out, w=32, h=16, timeout=30)
    assert r2["state"] == "error" and r2["error"] == "boom" and r2["shots"][0]["file"] is None
    assert r2["warn"][0].startswith("NOT_READY: frame x-mod")


def test_run_job_times_out_and_cancels(satk_home, monkeypatch):
    t = iter(range(0, 10000, 10))
    monkeypatch.setattr(CH.time, "monotonic", lambda: float(next(t)))
    monkeypatch.setattr(CH.time, "sleep", lambda s: None)
    b = FakeBridge([{"state": "running"}])
    r = CH.run_job(b, check="speed", key="car", kind="vehicle", reference=False, out_dir=paths.work("out"), w=32, h=16,
                   timeout=50)
    assert r["state"] == "timeout" and ("job.cancel", {"id": "j1"}) in b.calls
    assert ("check.start", {"check": "speed", "key": "car", "kind": "vehicle", "reference": False}) in b.calls


@pytest.mark.parametrize("pillow", [True, False])
def test_side_by_side(satk_home, monkeypatch, pillow):
    if not pillow:
        monkeypatch.setitem(sys.modules, "PIL", None)
    elif pytest.importorskip("PIL") is None:  # pragma: no cover
        return
    out = paths.work("out", "sbs")
    a = P.write(out / "a.png", 4, 2, bytes([255, 0, 0] * 8))
    b = P.write(out / "b.png", 3, 3, bytes([0, 0, 255] * 9))
    r = CH.side_by_side(a, b, out / "ab.png", gap=2)
    w, h, ch, px = P.decode(Path(r).read_bytes())
    assert (w, h) == (9, 3) and px[0:3] == b"\xff\x00\x00" and px[(6 * ch):(6 * ch) + 3] == b"\x00\x00\xff"


def test_run_suite_writes_a_report(satk_home, monkeypatch):
    monkeypatch.setattr(CH.time, "sleep", lambda s: None)
    out = paths.work("out", "ingame", "gun", "weapon")
    res_mod = {"lanes": {"mod": {"fired": 3}, "ref": {"fired": 3}}, "notes": []}
    b = FakeBridge([{"state": "shot", "shot": {"name": "flash-mod"}}, {"state": "shot", "shot": {"name": "flash-ref"}},
                    {"state": "done", "result": res_mod}])
    r = CH.run_suite(b, {"key": "gun", "weapon": 24}, kind="weapon", checks=["fire"], reference=True, out_dir=out,
                     w=8, h=8, timeout=60)
    assert r["rows"][0][:2] == ["fire", "pass"] and r["rows"][0][6].endswith("fire-flash-pair.png")
    assert r["counts"] == {"pass": 1} and Path(r["report"]).is_file()
