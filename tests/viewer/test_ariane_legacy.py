"""The ARIANE_IPC/1 adapter against a fake bridge (no real viewer needed)."""

from __future__ import annotations

import math

import pytest

from satk.core.errors import SatkError
from satk.saap import conformance as CF
from satk.saap import schema as S
from satk.viewer.backends.ariane_legacy import (ArianeLegacyBackend, ariane_fov, entity_from_instance, hfov_of,
                                                map_error)


@pytest.mark.parametrize("aspect", [4 / 3, 16 / 9, 21 / 9, 1.0])
@pytest.mark.parametrize("h", [30.0, 70.0, 100.0])
def test_fov_conversion_roundtrip(aspect, h):
    f = ariane_fov(h, aspect)
    assert hfov_of(f, aspect) == pytest.approx(h, abs=1e-9)


def test_fov_is_identity_at_4_3_and_matches_librw():
    assert ariane_fov(70, 4 / 3) == pytest.approx(70)
    # librw Camera::setFOV: viewWindow.x = tan(2*atan(tan(F/4) * ar/(4/3))) is tan(hfov/2)
    F = 70.0
    ar = 16 / 9
    vw = math.tan(2 * math.atan(math.tan(math.radians(F) / 4) * ar / (4 / 3)))
    assert math.degrees(2 * math.atan(vw)) == pytest.approx(hfov_of(F, ar))


@pytest.mark.parametrize("inst,src,lod", [
    ({"instance_id": 1, "model_id": 17613, "position": [1, 2, 3], "ipl": "LAE2_STREAM0", "ipl_kind": "binary", "ipl_index": 4},
     {"kind": "ipl_bin", "ipl": "lae2_stream0", "idx": 4}, None),
    ({"instance_id": 2, "model_id": 17858, "position": [1, 2, 3], "ipl": "lae2", "ipl_kind": "text", "ipl_index": 198,
      "ipl_file": "DATA\\MAPS\\LA\\LAE2.IPL", "is_lod": True},
     {"kind": "ipl_text", "file": "data/maps/la/lae2.ipl", "idx": 198}, True),
    ({"instance_id": 3, "model_id": 1, "position": [1, 2, 3], "ipl_kind": "none", "ipl_index": -1},
     {"kind": "model_pos"}, None),
])
def test_entity_from_instance(inst, src, lod):
    e = entity_from_instance(inst)
    assert e["src"] == src and e["ref"] == f"i{inst['instance_id']}@1.00,2.00"
    assert e.get("lod") == lod
    assert S.validate({"x": e}, {"properties": {"x": {"$ref": "common.json#/$defs/EntityRef"}}}) == []


def test_quaternion_is_conjugated():
    e = entity_from_instance({"instance_id": 1, "model_id": 1, "position": [0, 0, 0], "q_file": [0.0, 0.0, 0.7071, 0.7071]})
    assert e["rot"]["q_world"] == [0.0, 0.0, -0.7071, 0.7071]


@pytest.mark.parametrize("msg,code", [("authentication failed", "AUTH"), ("unknown command", "UNKNOWN_METHOD"),
                                      ("camera revision changed", "REVISION"), ("unknown model: x", "NOT_FOUND"),
                                      ("invalid camera field", "BAD_PARAMS"), ("could not capture", "INTERNAL")])
def test_map_error(msg, code):
    assert map_error(msg) == code


def test_hello_and_status(ariane_backend, fake_ariane):
    h = ariane_backend.call("hello", {})
    assert h["impl"] == "ariane-legacy" and h["viewport"] == {"w": 1280, "h": 720}
    assert "world.settle" in h["caps"] and "capture.size" not in h["caps"] and "capture.ids" not in h["caps"]
    assert h["game"]["root"] == "D:/G/clean" and S.validate_result("hello", h) == []
    st = ariane_backend.call("status", {})
    assert st["frame"] == 1234 and st["fps"] == 144.0 and S.validate_result("status", st) == []


def test_no_settle_build_drops_capability(make_fake_ariane, satk_home):
    fa = make_fake_ariane(settle=False, quit_cmd=False)
    b = ArianeLegacyBackend("ariane", "ariane", fa.port, fa.token, timeout=5)
    assert "world.settle" not in b.caps
    with pytest.raises(SatkError) as e:
        b.call("world.settle", {})
    assert e.value.code == "UNSUPPORTED"
    r = b.call("capture", {"path_prefix": str(satk_home / "work" / "ns")})
    assert r["settled"] is False
    assert [c[0] for c in fa.log].count("capture") == 2  # warm-up capture without patch V0-6


def test_camera_set_converts_fov(ariane_backend, fake_ariane):
    r = ariane_backend.call("camera.set", {"pose": {"pos": [0, 0, 50], "look": [0, 40, 0]}, "fov_h_deg": 70})
    assert fake_ariane.cam["fov"] == pytest.approx(ariane_fov(70, 16 / 9))
    assert r["pose"]["fov_h_deg"] == pytest.approx(70, abs=1e-3) and r["rev"] == fake_ariane.cam_rev
    with pytest.raises(SatkError) as e:
        ariane_backend.call("camera.set", {"pose": {"pos": [0, 0, 50], "look": [0, 40, 0]}, "expect_rev": 0})
    assert e.value.code == "REVISION"


def test_stateless_capture_restores_camera(ariane_backend, fake_ariane, satk_home):
    ariane_backend.call("camera.set", {"pose": {"pos": [10, 10, 80], "look": [10, 60, 0]}})
    before = dict(fake_ariane.cam)
    r = ariane_backend.call("capture", {"path_prefix": str(satk_home / "work" / "out" / "c1"),
                                        "pose": {"pos": [2495, -1720, 60], "look": [2495, -1670, 15], "fov_h_deg": 70}})
    assert r["w"] == 32 and r["settled"] is True and S.validate_result("capture", r) == []
    assert r["pose"]["pos"] == [2495, -1720, 60] and r["pose"]["fov_h_deg"] == pytest.approx(70, abs=1e-3)
    assert fake_ariane.cam["position"] == before["position"]  # restored by the adapter (Ariane said restored:false)
    cp = [c for c in fake_ariane.log if c[0] == "capture_pose"][0]
    assert cp[-2:] == ["120", "2"]  # settle_frames / quiet_frames
    with pytest.raises(SatkError) as e:
        ariane_backend.call("capture", {"path_prefix": str(satk_home / "work" / "c2"), "w": 320, "h": 240})
    assert e.value.code == "UNSUPPORTED"
    with pytest.raises(SatkError) as e:
        ariane_backend.call("capture", {"path_prefix": str(satk_home / "work" / "c3"), "layers": ["color", "ids"]})
    assert e.value.code == "UNSUPPORTED"


def _screen_to_world_dir(cam: dict, W: int, H: int, px: float, py: float) -> list[float]:
    """Ray direction exactly as agentbridge.cpp ``screen_to_world`` + librw ``Camera::setFOV`` build it."""
    def norm(v):
        n = math.sqrt(sum(c * c for c in v))
        return [c / n for c in v]

    def cross(a, b):
        return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]

    ar = W / H
    vwx = math.tan(2 * math.atan(math.tan(math.radians(cam["fov"]) / 4) * ar / (4 / 3)))
    vwy = vwx / ar
    f = norm([t - p for t, p in zip(cam["target"], cam["position"])])
    r = norm(cross(f, [0, 0, 1]))
    u = norm(cross(r, f))
    nx, ny = (px + 0.5) / W * 2 - 1, (py + 0.5) / H * 2 - 1
    return norm([f[i] + r[i] * vwx * nx - u[i] * vwy * ny for i in range(3)])


def test_pick_window_space_is_a_raycast_along_the_screen_to_world_ray(ariane_backend, fake_ariane):
    """Window picks: same rays as Ariane's screen_to_world, but raycast_segment (no LOD parents)."""
    fake_ariane.cam = {"position": [2440.0, -1640.0, 45.0], "target": [2495.0, -1690.0, 15.0], "fov": 70.0}
    pts = [[640, 360], [1100, 300], [0, 0], [1279, 719]]
    h = ariane_backend.call("pick", {"points": pts + [[5000, 5]]})["hits"]
    assert h[0]["entity"]["src"] == {"kind": "ipl_bin", "ipl": "lae2_stream0", "idx": 4}
    assert h[0]["entity"]["lod"] is False and h[0]["dist"] == 42.0  # HD, not the LOD screen_to_world reports
    assert h[-1]["hit"] is False
    assert "screen_to_world" not in [c[0] for c in fake_ariane.log]
    segs = [c for c in fake_ariane.log if c[0] == "raycast_segment"][-len(pts):]
    for (px, py), seg in zip(pts, segs):
        start, end = [float(x) for x in seg[1:4]], [float(x) for x in seg[4:7]]
        d = [(e - s) / math.dist(start, end) for s, e in zip(start, end)]
        assert start == pytest.approx(fake_ariane.cam["position"])
        assert d == pytest.approx(_screen_to_world_dir(fake_ariane.cam, 1280, 720, px, py), abs=1e-6)


def test_pick_window_space_falls_back_to_screen_to_world(make_fake_ariane):
    fa = make_fake_ariane(raycast=False)
    b = ArianeLegacyBackend("ariane", "ariane", fa.port, fa.token, timeout=5)
    h = b.call("pick", {"points": [[640, 360], [10, 10]]})["hits"]
    assert h[0]["entity"]["lod"] is True and h[0]["entity"]["model_id"] == 17858  # what the old bridge reports
    assert "entity" not in h[1] and h[1]["normal"] == [0, 0, 1]
    assert [c[0] for c in fa.log].count("screen_to_world") == 2


def test_pick_table_flags_lod_rows(make_fake_ariane, monkeypatch):
    from satk.viewer import api

    fa = make_fake_ariane(raycast=False)
    monkeypatch.setattr(api, "get_backend",
                        lambda target: ArianeLegacyBackend("ariane", "ariane", fa.port, fa.token, timeout=5))
    out = api.pick([[640, 360], [10, 10]], target="ariane")
    assert out["lod_rows"] == [0] and any(w.startswith("LOD: row(s) 0 ") for w in out["warn"])
    assert out["rows"][0][2] == "inst:lae2#198" and out["rows"][0][-1] == "exact"


def test_pick_window_and_capture_space(ariane_backend, fake_ariane):
    n_cam = fake_ariane.cam_rev
    r = ariane_backend.call("pick", {"points": [[320, 240]], "space": "capture", "w": 640, "h": 480,
                                     "pose": {"pos": [2495, -1720, 60], "look": [2495, -1670, 15], "fov_h_deg": 70}})
    assert r["hits"][0]["entity"]["model_id"] == 17613 and r["hits"][0]["dist"] == 42.0
    assert fake_ariane.cam_rev == n_cam  # capture-space picks never move the camera
    seg = [c for c in fake_ariane.log if c[0] == "raycast_segment"][-1]
    start, end = [float(x) for x in seg[1:4]], [float(x) for x in seg[4:7]]
    assert start == pytest.approx([2495, -1720, 60]) and math.dist(start, end) == pytest.approx(3000.0)
    with pytest.raises(SatkError) as e:
        ariane_backend.call("pick", {"points": [[1, 1]], "mode": "visible"})
    assert e.value.code == "UNSUPPORTED"


def test_entities_env_assets(ariane_backend, fake_ariane, satk_home):
    q = ariane_backend.call("entity.query", {"center": [2489.3, -1668.5, 12.3], "r": 50})
    assert [e["model_id"] for e in q["items"]] == [17613, 1280] and q["total"] == 2
    q = ariane_backend.call("entity.query", {"center": [2489.3, -1668.5, 12.3], "r": 50, "limit": 1})
    assert len(q["items"]) == 1 and q["next"] == "1"
    q = ariane_backend.call("entity.query", {"center": [2489.3, -1668.5, 12.3], "r": 50, "model": 1280})
    assert [e["model_id"] for e in q["items"]] == [1280]
    insp = ariane_backend.call("entity.inspect", {"ref": "i101@2489.30,-1668.50"})
    assert insp["model"] == {"id": 17613, "name": "lae2_roads89", "draw": 150.0}
    assert insp["lod"]["parent"]["src"] == {"kind": "ipl_text", "file": "lae2.ipl", "idx": 198}
    for bad in ("i999@0,0", "nonsense"):
        with pytest.raises(SatkError) as e:
            ariane_backend.call("entity.inspect", {"ref": bad})
        assert e.value.code == "NOT_FOUND"
    env = ariane_backend.call("env.set", {"time": "21:30", "weather": 8})
    assert env["time"] == "21:30" and env["weather_b"] == 8 and fake_ariane.env[:4] == [21, 30, 8, 8]
    files = ariane_backend.call("asset.render", {"model": 17613, "views": [45, 90], "size": 64,
                                                 "path_prefix": str(satk_home / "work" / "a")})["files"]
    assert len(files) == 2 and files[1].endswith("a_90.png")
    pv = [c for c in fake_ariane.log if c[0] == "asset_preview"][0]
    assert float(pv[3]) == pytest.approx(math.radians(45))
    with pytest.raises(SatkError) as e:
        ariane_backend.call("asset.render", {"model": "zzz", "path_prefix": str(satk_home / "work" / "b")})
    assert e.value.code == "NOT_FOUND"


def test_auth_failure(fake_ariane):
    b = ArianeLegacyBackend("ariane", "ariane", fake_ariane.port, "w" * 64, timeout=5)
    with pytest.raises(SatkError) as e:
        b.call("ping", {})
    assert e.value.code == "AUTH"


def test_conformance_through_adapter(ariane_backend):
    rep = CF.run(CF.BackendDriver(ariane_backend))
    bad = [r for r in rep["results"] if r[2] == "fail"]
    assert bad == [], bad
    assert rep["pass"] >= 25


def test_quit(ariane_backend, fake_ariane):
    assert ariane_backend.call("quit", {}) == {}
    assert fake_ariane.stopped.is_set()
