"""End-to-end slice with the real Blender 5.1 (markers blender, slow): an asset project, a car body blocked out
to gate G2 and a street prop, in about 30 studio calls, then the journal replayed into a second session.

Everything runs in a private ``SATK_PATHS_WORK``; the reference photo is synthetic. No game data is read
(the target size is given, not taken from a vanilla model).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from satk.core import config
from satk.core.registry import get_op
from satk.studio import api
from satk.studio import launcher as L
from satk.studio import replay

pytestmark = [pytest.mark.blender, pytest.mark.slow]

PROJECT = "slicecar"
TARGET = [2.3, 5.5, 1.5]  # width (x), length (y), height (z) in metres

#: Half profiles [x, z] of the body from the top centre down to the bottom centre, rear to front (y).
SECTIONS = [
    (-2.75, [[0, 0.15], [0.95, 0.12], [1.0, -0.2], [0.95, -0.45], [0, -0.45]]),
    (-2.3, [[0, 0.25], [1.05, 0.22], [1.12, -0.1], [1.1, -0.45], [0, -0.45]]),
    (-1.4, [[0, 0.75], [0.85, 0.7], [1.12, 0.2], [1.15, -0.2], [1.12, -0.45], [0, -0.45]]),
    (-0.6, [[0, 0.82], [0.85, 0.78], [1.12, 0.25], [1.16, -0.2], [1.12, -0.45], [0, -0.45]]),
    (0.4, [[0, 0.82], [0.85, 0.78], [1.12, 0.25], [1.16, -0.2], [1.12, -0.45], [0, -0.45]]),
    (1.1, [[0, 0.3], [1.0, 0.27], [1.14, 0.1], [1.15, -0.2], [1.12, -0.45], [0, -0.45]]),
    (2.3, [[0, 0.18], [1.0, 0.15], [1.1, 0.0], [1.1, -0.3], [1.05, -0.45], [0, -0.45]]),
    (2.75, [[0, 0.05], [0.9, 0.03], [0.98, -0.2], [0.92, -0.45], [0, -0.45]]),
]
WHEEL = [[0, -0.11], [0.3, -0.11], [0.34, -0.08], [0.34, 0.08], [0.3, 0.11], [0, 0.11]]


@pytest.fixture(scope="module")
def work(tmp_path_factory):
    from satk.docs.cleanup import remove_tree

    w = tmp_path_factory.mktemp("slice") / "work"
    w.mkdir()
    old = os.environ.get("SATK_PATHS_WORK")
    os.environ["SATK_PATHS_WORK"] = str(w)
    config.reset()
    try:
        yield w
    finally:
        api.close_all()
        for name in (PROJECT, "slicereplay"):
            try:
                L.stop(name)
            except Exception:  # noqa: BLE001
                pass
        if old is None:
            os.environ.pop("SATK_PATHS_WORK", None)
        else:
            os.environ["SATK_PATHS_WORK"] = old
        config.reset()
        remove_tree(w.parent)


def _photo(path: Path) -> Path:
    from PIL import Image, ImageDraw

    im = Image.new("RGB", (1400, 560), (196, 205, 214))
    d = ImageDraw.Draw(im)
    d.polygon([(110, 300), (380, 250), (560, 120), (980, 120), (1120, 250), (1290, 290), (1290, 420), (110, 420)],
              fill=(70, 86, 120))
    for cx in (330, 1080):
        d.ellipse([cx - 95, 330, cx + 95, 520], fill=(25, 25, 25))
    im.save(path, "PNG")
    return path


def test_slice_car_and_prop_to_g2_then_replay(work):
    calls: list[tuple[str, float, int]] = []

    def step(method: str, params: dict | None = None, **kw) -> dict:
        t = time.perf_counter()
        r = api.call(method, params, session=PROJECT, **kw)
        size = len(json.dumps(r, separators=(",", ":")).encode())
        calls.append((method, time.perf_counter() - t, size))
        assert size <= 1700, (method, size)  # 1.5 KB reply + the client's session field
        return r

    t0 = time.perf_counter()
    # the session mechanics only: no inventory (a project with one closes a gate only with its items built)
    init = get_op("asset.init").call({"dir": PROJECT, "kind": "automobile", "intent": "add", "dims": TARGET,
                                      "detail": "none"})
    assert init["dims"] == TARGET and init["tier"] == "sa_plus"
    st = L.start(None, project=PROJECT, owner_pid=os.getpid(), idle=600)
    assert st["up"] and st["name"] == PROJECT and st["project"].endswith(f"assets/{PROJECT}")
    ref = get_op("ref.import").call({"photo": str(_photo(work / "side.png")), "project": PROJECT})
    assert Path(ref["image"]).is_file() and ref["size"] == [1400, 560]

    # -- the car body: loft -> half -> mirror -> belt line -> glass -> subdivision + weighted normals
    step("ref.plane", {"view": "left", "image": ref["image"], "points": [[330, 425], [1080, 425]],
                       "distance": 3.0, "origin_px": [705, 330]})
    r = step("mesh.loft", {"name": "chassis", "samples": 20, "mirror": True,
                           "sections": [{"at": y, "points": pts} for y, pts in SECTIONS]})
    assert r["result"]["tris"] > 200
    step("mesh.bisect", {"object": "chassis", "axis": "x", "keep": "+"})
    step("modifier.add", {"object": "chassis", "type": "MIRROR", "settings": {"axis": "x", "clip": True}})
    step("mesh.loopcut", {"object": "chassis", "axis": "z", "at": [0.3]})
    step("mesh.group", {"object": "chassis", "name": "side_glass",
                        "select": {"side": "+x", "within": 55, "where": ["z>0.32"]}})
    step("material.create", {"name": "paint1", "color": [60, 255, 0], "object": "chassis"})
    step("material.create", {"name": "glass", "color": [255, 255, 255], "alpha": 128, "object": "chassis",
                             "select": {"group": "side_glass"}})
    step("mesh.mark", {"object": "chassis", "kind": "sharp", "by": "material"})
    step("modifier.add", {"object": "chassis", "type": "SUBSURF", "settings": {"levels": 1}})
    step("modifier.add", {"object": "chassis", "type": "WEIGHTED_NORMAL", "settings": {"keep_sharp": True}})
    step("shade.basic", {"objects": ["chassis"], "mode": "smooth"})
    # -- wheels: one lathe, three copies, dummies
    step("mesh.lathe", {"name": "wheel_rf", "profile": WHEEL, "segments": 16, "axis": "x",
                        "location": [0.92, 1.62, -0.42]})
    step("batch", {"steps": [
        {"method": "scene.duplicate", "params": {"object": "wheel_rf", "name": "wheel_rb", "offset": [0, -3.24, 0]}},
        {"method": "scene.duplicate", "params": {"object": "wheel_rf", "name": "wheel_lf", "mirror": "x"}},
        {"method": "scene.duplicate", "params": {"object": "wheel_rb", "name": "wheel_lb", "mirror": "x"}},
        {"method": "scene.empty", "params": {"name": "wheel_rf_dummy", "location": [0.92, 1.62, -0.42]}},
        {"method": "scene.empty", "params": {"name": "wheel_lf_dummy", "location": [-0.92, 1.62, -0.42]}},
    ]})
    step("uv.unwrap", {"object": "chassis", "method": "smart"})
    step("camera.views", {"fit": ["chassis", "wheel_*"]})
    sheet = step("scene.info", {"limit": 0}, snapshot={"views": ["3q", "front", "left", "top"], "size": 512})
    side = step("scene.info", {"limit": 0}, snapshot="ref_cam_left")
    for s in (sheet, side):
        p = Path(s["snapshot"])
        assert p.is_file() and p.stat().st_size <= 80_000 and str(p).replace("\\", "/").startswith(
            str(work / "assets" / PROJECT).replace("\\", "/"))
    g1 = get_op("asset.status").call({"dir": PROJECT, "record": {"gate": "G1", "state": "done"}})
    assert g1["gate_checkpoint"].endswith("-G1.blend") and "G1+" in g1["gates"]

    # -- G2: shape and shading numbers of the evaluated body
    m = step("scene.stats", {"objects": "chassis"})["result"]
    body = m["objects"]["chassis"]
    assert 800 <= body["tris"] <= 4000, body
    assert body["shade.flat_share"] <= 0.3 and 3.0 <= body["shade.normal_bend"] <= 25.0, body
    assert body["geo.open_edges"] == 0, body
    dims = m["scene"]["dims"]
    assert all(abs(d / t - 1) <= 0.12 for d, t in zip(dims, TARGET)), dims
    get_op("asset.status").call({"dir": PROJECT, "record": {"gate": "G2", "state": "done"}})

    # -- a street prop beside it: lamp post (lathe + curve arm), bench (array of slats), sign (solidify)
    step("scene.collection", {"name": "prop"})
    step("mesh.lathe", {"name": "lamp_post", "profile": [[0, 0], [0.14, 0], [0.12, 0.25], [0.06, 0.35], [0.05, 4.2],
                                                         [0, 4.25]], "segments": 12, "location": [5, 0, -0.7],
                        "collection": "prop"})
    step("mesh.curve", {"name": "lamp_arm", "points": [[5, 0, 3.4], [5, 0.4, 3.9], [5, 1.1, 4.0]], "kind": "smooth",
                        "bevel_depth": 0.035, "bevel_resolution": 1, "resolution": 6, "collection": "prop"})
    step("mesh.convert", {"object": "lamp_arm"})
    step("mesh.primitive", {"kind": "cube", "size": [1.6, 0.12, 0.04], "name": "bench_slat",
                            "location": [6.5, 0, -0.25], "collection": "prop"})
    step("modifier.add", {"object": "bench_slat", "type": "ARRAY",
                          "settings": {"count": 4, "relative": [0, 1.25, 0]}})
    step("modifier.add", {"object": "bench_slat", "type": "BEVEL", "settings": {"width": 0.01, "segments": 1}})
    step("mesh.primitive", {"kind": "plane", "size": [0.6, 0.4], "name": "sign", "location": [5, -0.3, 2.2],
                            "rotation": [90, 0, 0], "collection": "prop"})
    r = step("modifier.add", {"object": "sign", "type": "SOLIDIFY", "settings": {"thickness": 0.02}},
             snapshot={"view": "3q", "objects": ["lamp_post", "lamp_arm", "bench_slat", "sign"]})
    assert r["stats"]["objects"]["sign"]["tris"] == 12 and Path(r["snapshot"]).is_file()
    slats = step("scene.stats", {"objects": "bench_slat"})["result"]["objects"]["bench_slat"]
    assert slats["tris"] > 4 * 12 and slats["dims"][1] > 0.5
    total = time.perf_counter() - t0
    n_calls = len(calls)
    assert 28 <= n_calls <= 36, n_calls
    assert total <= 15.0, f"{n_calls} calls + start took {total:.1f} s"
    card = get_op("asset.status").call({"dir": PROJECT})
    blob = json.dumps({k: v for k, v in card.items() if k != "ok"}, separators=(",", ":")).encode()
    assert len(blob) <= 1024 and card["gates"].startswith("G0- G1+ G2+"), card
    final_file = step("scene.stats", {"objects": "*", "precise": True, "file": True})["result"]["file"]
    final = json.loads(Path(final_file).read_text(encoding="utf-8"))

    # -- replay the project journal into a fresh session: same triangles, box and shading
    L.stop(PROJECT)
    L.start("slicereplay", owner_pid=os.getpid(), idle=600)
    t = time.perf_counter()
    rep = replay.run(PROJECT, session="slicereplay")
    replay_s = time.perf_counter() - t
    meas = json.loads(Path(rep["stats_file"]).read_text(encoding="utf-8"))
    assert meas["scene"]["tris"] == final["scene"]["tris"]
    for a, b in zip(sum(meas["scene"]["bbox"], []), sum(final["scene"]["bbox"], [])):
        assert abs(a - b) <= 1e-5, (meas["scene"]["bbox"], final["scene"]["bbox"])
    for name, row in final["objects"].items():
        got = meas["objects"][name]
        assert got["tris"] == row["tris"], name
        for k in ("shade.normal_bend", "shade.flat_share"):
            if k in row:
                assert abs(got[k] - row[k]) <= 0.01, (name, k, got[k], row[k])
    assert replay_s < 30
    print(f"\nslice: {n_calls} calls, {total:.1f} s with start; median call "
          f"{sorted(c[1] for c in calls)[n_calls // 2] * 1000:.0f} ms; largest reply {max(c[2] for c in calls)} B; "
          f"replay {rep['steps']} steps in {replay_s:.1f} s")
