"""The mock world (rendering, streaming, picking, dev methods), geometry helpers and the PNG codec."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path

import pytest

from satk.core.errors import SatkError
from satk.saap import geom as G
from satk.saap import png as P
from satk.saap.mock import ENTITIES, MockWorld, mini_lua

# --------------------------------------------------------------------------- geometry


@pytest.mark.parametrize("yaw,pitch", [(0, 0), (90, -30), (-135, 45), (180, 10)])
def test_ypr_roundtrip(yaw, pitch):
    d = G.dir_from_ypr(yaw, pitch)
    y, p = G.ypr_from_dir(d)
    assert math.isclose(((y - yaw + 180) % 360) - 180, 0, abs_tol=1e-9) and math.isclose(p, pitch, abs_tol=1e-9)


def test_yaw_convention():
    assert G.dir_from_ypr(0, 0) == pytest.approx((0, 1, 0))      # north = +Y
    assert G.dir_from_ypr(90, 0) == pytest.approx((-1, 0, 0))    # CCW from above -> west


def test_parse_pose_errors():
    for bad in ({}, {"pos": [0, 0, 0]}, {"pos": [0, 0], "look": [1, 1, 1]}, {"pos": [0, 0, 0], "look": [0, 0, 0]},
                {"pos": [0, 0, 0], "look": [1, 0, 0], "ypr": [0, 0, 0]}, {"pos": [0, 0, 0], "look": [1, 0, 0], "fov_h_deg": 0}):
        with pytest.raises(SatkError) as e:
            G.parse_pose(bad)
        assert e.value.code == "BAD_PARAMS"


def test_camera_projection_and_rays():
    cam = G.Camera.from_pose({"pos": [0, 0, 10], "look": [0, 50, 0]}, 800, 600, 70)
    x, y, z = cam.project((0, 50, 0))
    assert (x, y) == pytest.approx((400, 300)) and z > 0
    o, d = cam.ray(399.5, 299.5)  # pixel-centre convention: ray() adds 0.5
    assert d == pytest.approx(cam.f, abs=1e-9)
    # horizontal FOV: the left/right image edges are 35 degrees off-axis
    _, dl = cam.ray(-0.5, 299.5)
    assert math.degrees(math.acos(G.dot(dl, cam.f))) == pytest.approx(35.0, abs=1e-6)
    assert cam.project((0, -50, 0)) is None
    assert cam.project_box((-1, 49, -1), (1, 51, 1)) is not None


def test_frame_sphere_and_ray_aabb():
    p = G.frame_sphere((10, 20, 5), 4.0)
    assert G.dist(p["pos"], (10, 20, 5)) == pytest.approx(2.5 * 4 + 5, abs=1e-3)
    assert p["pos"][2] > 5  # above, 30 degrees
    t, n = G.ray_aabb((0, 0, 10), (0, 0, -1), (-1, -1, 0), (1, 1, 2))
    assert t == pytest.approx(8) and n == (0, 0, 1)
    assert G.ray_aabb((5, 5, 10), (0, 0, -1), (-1, -1, 0), (1, 1, 2)) is None


def test_png_roundtrip():
    px = bytes(range(256)) * 3
    data = P.encode(16, 16, px)
    w, h, ch, out = P.decode(data)
    assert (w, h, ch) == (16, 16, 3) and out == px
    assert P.encode(16, 16, px) == data  # deterministic


# --------------------------------------------------------------------------- mock world


def _cap(world: MockWorld, tmp: Path, name: str, **kw) -> dict:
    return world.handle("capture", {"path_prefix": str(tmp / name), **kw})


def test_capture_deterministic_with_layers(satk_home):
    work = satk_home / "work"
    w1, w2 = MockWorld(), MockWorld()
    a = _cap(w1, work, "a", w=320, h=240, layers=["color", "ids", "depth"])
    b = _cap(w2, work, "b", w=320, h=240, layers=["color", "ids", "depth"])
    assert a["sha256"] == b["sha256"] == hashlib.sha256(Path(a["files"]["color"]).read_bytes()).hexdigest()
    assert P.size(a["files"]["ids"]) == (320, 240)
    assert Path(a["files"]["depth"]).stat().st_size == 320 * 240 * 4
    refs = {e["entity"]["ref"] for e in a["ids_legend"]}
    assert "m1" in refs and len(refs) >= 4 and a["settled"] is True and a["pending"] == 0
    assert sum(e["px"] for e in a["ids_legend"]) <= 320 * 240


def test_settle_matters(satk_home):
    w = MockWorld()
    r = w._m_capture({"path_prefix": str(satk_home / "work" / "s0"), "settle": {"max_frames": 0}})
    assert r["settled"] is False and r["pending"] > 0  # nothing streamed in yet
    s = w.handle("world.settle", {"max_frames": 120, "quiet_frames": 2})
    assert s["settled"] is True and s["pending"] == 0 and s["frames"] >= 2


def test_stateless_capture_restores_camera(satk_home):
    w = MockWorld()
    w.handle("camera.set", {"pose": {"pos": [2400, -1650, 80], "look": [2450, -1660, 13]}})
    rev = w.handle("camera.get", {})["rev"]
    _cap(w, satk_home / "work", "st", pose={"pos": [2489.3, -1720, 60], "look": [2489.3, -1668.5, 12.3]})
    g = w.handle("camera.get", {})
    assert g["pose"]["pos"] == [2400, -1650, 80] and g["rev"] == rev + 2


def test_pick_and_raycast():
    w = MockWorld()
    w.handle("world.settle", {})
    h = w.handle("pick", {"points": [[400, 300], [5, 5], [900, 900]]})["hits"]
    assert h[0]["hit"] and h[0]["entity"]["model_id"] == 17613 and h[0]["entity"]["src"] == {"kind": "model_pos"}
    assert h[1]["hit"] and "entity" not in h[1]  # ground
    assert h[2]["hit"] is False  # outside the window
    r = w.handle("raycast", {"from": [2489.3, -1668.5, 80], "to": [2489.3, -1668.5, -20]})
    assert r["hit"] and r["entity"]["ref"] == "m1" and r["pos"][2] == pytest.approx(12.9) and r["normal"] == [0, 0, 1]
    assert w.handle("raycast", {"from": [0, 0, 100], "to": [0, 0, 50]}) == {"hit": False}


def test_entities_env_view():
    w = MockWorld()
    q = w.handle("entity.query", {"center": [2489.3, -1668.5, 12.3], "r": 200, "limit": 2})
    assert len(q["items"]) == 2 and q["next"] == "2" and q["items"][0]["ref"] == "m1"
    assert all(not e["lod"] for e in q["items"])
    q2 = w.handle("entity.query", {"center": [2489.3, -1668.5, 12.3], "r": 1, "include_lod": True})
    assert {e["ref"] for e in q2["items"]} == {"m1", "m7"}
    insp = w.handle("entity.inspect", {"ref": "m1"})
    assert insp["lod"]["parent"]["model_id"] == 17858 and insp["model"]["name"] == "lae2_roads89"
    with pytest.raises(SatkError) as e:
        w.handle("entity.inspect", {"ref": "zz"})
    assert e.value.code == "NOT_FOUND"
    assert w.handle("env.set", {"time": "21:30", "weather": 8})["weather_b"] == 8
    with pytest.raises(SatkError):
        w.handle("env.set", {"weather": 99})
    a = w.handle("view.set", {"overlays": ["col"], "hide": ["m2"]})["applied"]
    assert a["overlays"] == ["col"] and a["hide"] == ["m2"]
    with pytest.raises(SatkError) as e:
        w.handle("view.set", {"overlays": ["bogus"]})
    assert e.value.code == "UNSUPPORTED"


def test_dev_methods(satk_home):
    w = MockWorld()
    assert w.handle("mem.read", {"addr": "0x400000", "len": 2})["hex"] == "4d5a"
    with pytest.raises(SatkError):
        w.handle("mem.read", {"addr": 16, "len": 2})
    assert w.handle("console.exec", {"line": "echo hi"}) == {"accepted": True, "output": ["hi"]}
    assert w.handle("console.exec", {"line": "frobnicate"})["accepted"] is False
    log = w.handle("log.poll", {"since": 0})
    assert log["items"][0]["msg"] == "mock endpoint started" and log["next_seq"] >= 1
    assert any(it["msg"] == "> echo hi" for it in log["items"])
    assert w.handle("log.poll", {"since": log["next_seq"]})["items"] == []
    assert w.handle("log.poll", {"since": 0, "streams": ["server"]})["items"] == []
    files = w.handle("asset.render", {"model": "infernus", "views": [45, 225], "size": 64,
                                      "path_prefix": str(satk_home / "work" / "inf")})["files"]
    assert len(files) == 2 and files[0].endswith("inf_45.png") and P.size(files[0]) == (64, 64)


@pytest.mark.parametrize("code,values,prints,err", [
    ("return 1 + 2", [3], [], False),
    ("print('hi') return 1 + 2", [3], ["hi"], False),
    ("print(1, true, nil)\nreturn 2 * 3, 'x'", [6, "x"], ["1\ttrue\tnil"], False),
    ("return 7 / 2", [3.5], [], False),
    ("os.exit()", [], [], True),
    ("return 1 / 0", [], [], True),
])
def test_mini_lua(code, values, prints, err):
    r = mini_lua(code)
    assert r["values"] == values and r["prints"] == prints and ("error" in r) == err


def test_entity_table_is_consistent():
    refs = [e.ref for e in ENTITIES]
    assert len(refs) == len(set(refs))
    m1 = next(e for e in ENTITIES if e.ref == "m1")
    assert m1.model_id == 17613 and m1.pos == (2489.3, -1668.5, 12.3) and m1.src == {"kind": "model_pos"}
