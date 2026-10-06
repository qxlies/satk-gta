"""The generic studio methods in the real Blender 5.1 (markers blender, slow): every method family once, face
selectors, modifiers, snapshots (looks, sheet, ghost, reference composite), checkpoint timing on a ~5 MB
scene, two sessions in two work folders, no thumbnails and no bytecode. Synthetic geometry only; the
vanilla ghost test also needs the game copy (marker game)."""

from __future__ import annotations

import json
import os
import statistics
import time
from pathlib import Path

import pytest

from satk.core import config
from satk.core.errors import SatkError
from satk.saap import client as C
from satk.studio import api
from satk.studio import launcher as L

pytestmark = [pytest.mark.blender, pytest.mark.slow]

NAME = "methods"


def _thumbnails_of(files) -> list[str]:
    """Freedesktop thumbnails (Blender writes them on save unless previews are off) of ``files``.

    The name is the MD5 of the file URI; other programs and agents may add thumbnails of their own files
    at the same time, so only ours are looked for."""
    import hashlib

    root = Path.home() / ".thumbnails"
    hits = []
    for f in files:
        s = str(Path(f).absolute()).replace("\\", "/")
        for uri in {f"file:///{s}", f"file:///{s[0].upper()}{s[1:]}", f"file:///{s[0].lower()}{s[1:]}"}:
            h = hashlib.md5(uri.encode("utf-8")).hexdigest() + ".png"
            hits += [str(root / d / h) for d in ("normal", "large") if (root / d / h).is_file()]
    return hits


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    from satk.docs.cleanup import remove_tree

    work = tmp_path_factory.mktemp("methods") / "work"
    work.mkdir()
    old = os.environ.get("SATK_PATHS_WORK")
    os.environ["SATK_PATHS_WORK"] = str(work)
    config.reset()
    L.start(NAME, owner_pid=os.getpid(), idle=600)
    try:
        yield work, None
    finally:
        api.close_all()
        try:
            L.stop(NAME)
        finally:
            if old is None:
                os.environ.pop("SATK_PATHS_WORK", None)
            else:
                os.environ["SATK_PATHS_WORK"] = old
            config.reset()
            remove_tree(work.parent)


def _c(method: str, params: dict | None = None, **kw) -> dict:
    r = api.call(method, params, session=NAME, **kw)
    assert len(json.dumps(r, separators=(",", ":")).encode()) <= 1700, method
    return r


def test_every_method_family(live):
    work, _ = live
    _c("scene.clear")
    names = {m["name"] for m in api.methods(session=NAME)["methods"]}
    for want in ("scene.info", "scene.stats", "scene.duplicate", "mesh.loft", "mesh.lathe", "mesh.extrude",
                 "mesh.inset", "mesh.loopcut", "mesh.bevel", "mesh.merge", "mesh.dissolve", "mesh.mark",
                 "mesh.bridge", "modifier.add", "material.create", "uv.unwrap", "uv.texel", "camera.add",
                 "ref.plane", "io.ghost", "io.import", "shade.basic", "python", "session.restore"):
        assert want in names, want
    help_ = api.methods("mesh.loft", session=NAME)
    assert "mirror" in help_["help"] and "select" not in help_.get("errors", [])
    # box -> cuts -> extrude a group -> transform by the group -> inset -> bevel -> mark sharp -> dissolve
    _c("mesh.primitive", {"kind": "cube", "size": [2, 4, 1], "name": "box"})
    r = _c("mesh.loopcut", {"object": "box", "axis": "y", "at": [-1, 1]})
    assert r["result"]["tris"] == 28
    r = _c("mesh.extrude", {"object": "box", "select": {"side": "+z", "where": ["y>-1", "y<1"]}, "distance": 0.5,
                            "save_group": "top"})
    assert r["stats"]["objects"]["box"]["dims"] == [2.0, 4.0, 1.5]
    assert _c("mesh.info", {"object": "box"})["result"]["groups"] == {"top": 4}
    r = _c("mesh.transform", {"object": "box", "select": {"group": "top"}, "scale": [0.5, 0.5, 1]})
    assert r["result"]["verts"] == 4
    _c("mesh.inset", {"object": "box", "select": {"side": "-z"}, "thickness": 0.2, "save_group": "floor"})
    r = _c("mesh.bevel", {"object": "box", "width": 0.05, "segments": 2, "angle": 60})
    assert r["result"]["edges"] > 8 and r["stats"]["objects"]["box"]["geo.open_edges"] == 0
    r = _c("mesh.mark", {"object": "box", "kind": "sharp", "by": "angle", "angle": 40})
    _c("mesh.dissolve", {"object": "box"})
    with pytest.raises(SatkError) as ei:
        _c("mesh.extrude", {"object": "box", "select": {"side": "+q"}, "distance": 1})
    assert ei.value.code == "BAD_PARAMS"
    with pytest.raises(SatkError) as ei:
        _c("mesh.extrude", {"object": "box", "select": {"group": "nope"}, "distance": 1})
    assert ei.value.code == "NOT_FOUND" and ei.value.data["groups"] == ["top", "floor"]
    # symmetric halves, bridge, merge
    _c("mesh.primitive", {"kind": "grid", "x_segments": 4, "y_segments": 4, "size": [2, 2], "name": "sheet"})
    r = _c("mesh.bisect", {"object": "sheet", "axis": "x", "keep": "+"})
    assert r["stats"]["objects"]["sheet"]["dims"][0] == 1.0
    r = _c("mesh.symmetrize", {"object": "sheet", "source": "+x"})
    assert r["stats"]["objects"]["sheet"]["dims"][0] == 2.0
    _c("mesh.primitive", {"kind": "circle", "segments": 8, "cap": "none", "name": "ring_a"})
    _c("mesh.primitive", {"kind": "circle", "segments": 8, "cap": "none", "name": "ring_b", "location": [0, 0, 1]})
    _c("scene.transform", {"object": "ring_b", "apply": True, "apply_location": True})
    r = _c("mesh.bridge", {"object": "ring_a", "with": ["ring_b"], "cuts": 2})
    assert r["result"]["joined"] == ["ring_b"] and r["stats"]["objects"]["ring_a"]["tris"] == 48
    assert "ring_b" not in {o["name"] for o in _c("scene.info")["result"]["objects"]}
    r = _c("mesh.merge", {"object": "ring_a", "dist": 0.001})
    assert r["result"]["removed"] == 0
    # modifiers: whitelist, settings, order, apply, smooth by angle
    for t, s in (("MIRROR", {"axis": "x"}), ("SUBSURF", {"levels": 1}), ("BEVEL", {"width": 0.02}),
                 ("SOLIDIFY", {"thickness": 0.01}), ("WEIGHTED_NORMAL", {"keep_sharp": True}),
                 ("TRIANGULATE", {"quad": "beauty"}), ("SMOOTH_BY_ANGLE", {"angle": 40})):
        _c("modifier.add", {"object": "sheet", "type": t, "settings": s})
    stack = _c("modifier.list", {"object": "sheet"})["result"]["stack"]
    assert [m["type"] for m in stack] == ["MIRROR", "SUBSURF", "BEVEL", "SOLIDIFY", "WEIGHTED_NORMAL", "TRIANGULATE",
                                          "SMOOTH_BY_ANGLE"]
    _c("modifier.set", {"object": "sheet", "name": "subsurf", "settings": {"levels": 2}, "index": 0})
    with pytest.raises(SatkError) as ei:
        _c("modifier.add", {"object": "sheet", "type": "BOOLEAN"})
    assert ei.value.code == "BAD_PARAMS"
    with pytest.raises(SatkError) as ei:
        _c("modifier.add", {"object": "sheet", "type": "BEVEL", "settings": {"widht": 1}})
    assert ei.value.code == "BAD_PARAMS" and "width" in ei.value.data["allowed"]
    _c("modifier.remove", {"object": "sheet", "name": "triangulate"})
    before = _c("scene.stats", {"objects": "sheet"})["result"]["objects"]["sheet"]["tris"]
    r = _c("modifier.apply", {"object": "sheet", "all": True})
    assert r["stats"]["objects"]["sheet"]["tris"] == before and len(r["result"]["applied"]) == 6
    # array along a curve target, shrinkwrap, decimate (planar), curve deform, weld
    _c("mesh.curve", {"name": "rail", "points": [[0, 0, 0], [0, 3, 0.5], [0, 6, 0]], "kind": "smooth"})
    _c("mesh.primitive", {"kind": "cube", "size": [0.2, 0.5, 0.2], "name": "link"})
    _c("modifier.add", {"object": "link", "type": "ARRAY", "settings": {"curve": "rail", "merge": True}})
    r = _c("modifier.add", {"object": "link", "type": "CURVE", "settings": {"curve": "rail", "axis": "y"}})
    assert r["stats"]["objects"]["link"]["tris"] > 12 * 5
    _c("modifier.add", {"object": "link", "type": "WELD", "settings": {"threshold": 0.001}})
    _c("modifier.add", {"object": "link", "type": "DECIMATE", "settings": {"kind": "planar", "angle": 1}})
    _c("modifier.add", {"object": "ring_a", "type": "SHRINKWRAP", "settings": {"target": "box", "offset": 0.01}})
    # material, UV, cameras, shading, objects
    r = _c("material.create", {"name": "trim", "color": "#303030", "object": "box", "select": {"group": "floor"}})
    assert r["result"]["faces"] >= 1 and r["result"]["color"] == [48, 48, 48, 255]
    _c("material.create", {"name": "paint1", "color": [60, 255, 0]})
    _c("material.assign", {"object": "box", "material": "paint1", "select": {"group": "top"}})
    mats = {m["name"]: m for m in _c("material.list", {"object": "box"})["result"]["materials"]}
    assert set(mats) == {"trim", "paint1"}
    _c("mesh.mark", {"object": "box", "kind": "seam", "by": "material"})
    r = _c("uv.unwrap", {"object": "box", "method": "smart"})
    assert r["result"]["layer"] == "UVMap"
    _c("uv.layers", {"object": "box", "add": "UV2"})
    _c("uv.unwrap", {"object": "box", "method": "planar", "axis": "z", "size": 4, "layer": "UV2"})
    r = _c("uv.fit", {"object": "box", "rect": [0.5, 0.5, 1.0, 1.0], "layer": "UV2"})
    assert r["result"]["layer"] == "UV2"
    tex = _c("uv.texel", {"object": "box", "px": 256})["result"]["px_per_m"]
    assert 0 < tex["p10"] <= tex["p50"] <= tex["p90"]
    _c("shade.basic", {"objects": ["box"], "mode": "angle", "angle": 35})
    _c("camera.add", {"name": "cam_box", "view": "front", "fit": ["box"]})
    assert {c["name"] for c in _c("camera.list")["result"]["cameras"]} == {"cam_box"}
    _c("scene.empty", {"name": "door_lf_dummy", "location": [-1, 1, 0.5], "display": "cube"})
    _c("scene.parent", {"objects": ["link"], "parent": "door_lf_dummy"})
    _c("scene.duplicate", {"object": "door_lf_dummy", "name": "door_rf_dummy", "mirror": "x"})
    o = _c("scene.object", {"object": "door_rf_dummy"})["result"]
    assert o["location"] == [1.0, 1.0, 0.5] and o["scale"] == [1.0, 1.0, 1.0]
    _c("scene.rename", {"object": "link", "name": "chain"})
    _c("scene.origin", {"object": "box", "to": "bottom"})
    assert _c("scene.object", {"object": "box"})["result"]["location"][2] == -0.5
    _c("scene.props", {"objects": ["box"], "set": {"satk_part": "chassis"}})
    _c("scene.visible", {"objects": ["ring_a"], "hide": True})
    _c("scene.join", {"objects": ["sheet", "chain"], "into": "sheet"})
    _c("scene.delete", {"objects": ["door_*"]})
    info = _c("scene.info")["result"]
    assert {o["name"] for o in info["objects"]} == {"box", "sheet", "ring_a", "rail", "cam_box"}


def test_snapshots_looks_sheet_and_references(live):
    work, _ = live
    from PIL import Image

    out = {}
    for look in ("clay", "raw", "wire"):
        t = time.perf_counter()
        r = _c("scene.info", {"limit": 0}, snapshot={"view": "3q", "look": look})
        out[look] = (time.perf_counter() - t, Path(r["snapshot"]))
    for look, (secs, p) in out.items():
        im = Image.open(p)
        assert im.format == "JPEG" and im.size == (512, 512) and p.stat().st_size <= 40_000, look
        assert secs <= 1.0, (look, secs)
    r = _c("scene.info", {"limit": 0}, snapshot={"views": ["3q", "front", "left", "top"], "size": 512})
    assert Image.open(r["snapshot"]).size == (512, 512) and r["snapshot"].endswith("-sheet.jpg")
    g = _c("scene.info", {"limit": 0}, snapshot={"view": "front", "look": "game"})   # the look plug-in's game look
    assert Image.open(g["snapshot"]).size == (512, 512) and not g.get("warn")
    bad = _c("scene.info", {"limit": 0}, snapshot={"view": "diagonal"})  # a failed snapshot is a warning
    assert "snapshot" not in bad and any(w.startswith("BAD_PARAMS: snapshot: unknown view") for w in bad["warn"])
    # a red/blue reference photo behind the box, seen through the model from the matched camera
    photo = work / "ref.png"
    im = Image.new("RGB", (800, 400), (220, 30, 30))
    im.paste((30, 30, 220), (400, 0, 800, 400))
    im.save(photo)
    r = _c("ref.plane", {"view": "left", "image": str(photo), "width": 8.0, "alpha": 0.5})
    assert r["result"]["size_m"] == [8.0, 4.0] and r["result"]["camera"] == "ref_cam_left"
    assert "ref_left" not in (r.get("stats") or {}).get("objects", {}) or \
        "tris" not in r["stats"]["objects"]["ref_left"]
    shot = Image.open(_c("scene.info", {"limit": 0}, snapshot="ref_cam_left")["snapshot"]).convert("RGB")
    w, h = shot.size
    left, right = shot.getpixel((5, h // 2)), shot.getpixel((w - 5, h // 2))
    # camera on -X looks along +X: image right = -Y; the photo keeps its left half red, right half blue
    assert left[0] > 150 > left[2] and right[2] > 150 > right[0], (left, right)
    assert [x["name"] for x in _c("ref.list")["result"]["refs"]] == ["ref_left"]
    stats = _c("scene.stats")["result"]
    assert "ref_left" not in stats.get("objects", {})


def test_checkpoint_and_restore_of_a_5mb_scene(live):
    _c("mesh.primitive", {"kind": "grid", "x_segments": 260, "y_segments": 260, "name": "heavy"}, stats="none")
    t = time.perf_counter()
    r = _c("session.checkpoint", {"tag": "heavy"}, stats="none")
    ck_s = time.perf_counter() - t
    size = Path(r["result"]["checkpoint"]).stat().st_size
    _c("scene.delete", {"objects": ["heavy"]}, stats="none")
    t = time.perf_counter()
    r = _c("session.restore", {"ref": "heavy"}, stats="none")
    rs_s = time.perf_counter() - t
    assert "heavy" in r["changed"]
    print(f"\ncheckpoint {size / 1e6:.1f} MB in {ck_s * 1000:.0f} ms, restore in {rs_s * 1000:.0f} ms")
    assert size >= 4_000_000 and ck_s <= 0.100 and rs_s <= 0.200
    _c("scene.delete", {"objects": ["heavy"]}, stats="none")


def test_warm_step_latency_with_stats(live):
    lat = []
    for i in range(15):
        t = time.perf_counter()
        _c("mesh.primitive", {"kind": "uv_sphere", "segments": 24, "rings": 12, "name": f"s{i}", "location": [i, 5, 0]})
        lat.append(time.perf_counter() - t)
    assert statistics.median(lat) <= 0.150
    _c("scene.delete", {"objects": ["s*"]}, stats="none")


@pytest.mark.game
def test_vanilla_ghost_overlay_and_lineup(live, clean_root):
    from satk.blender import runner

    work, _ = live
    runner.ensure_dragonff()  # extracted into this test's work folder
    r = _c("io.ghost", {"sid": "model:426"})
    dims = r["result"]["dims"]
    assert abs(dims[0] - 2.58) < 0.05 and abs(dims[1] - 5.5) < 0.05, dims  # premier, full-detail parts only
    assert "objects" not in (r.get("stats") or {})  # ghosts are never counted
    for mode in ("overlay", "lineup", "hide"):
        p = _c("scene.info", {"limit": 0}, snapshot={"view": "left", "ghost": mode})["snapshot"]
        assert Path(p).is_file()
    _c("scene.delete", {"objects": ["*"]}, stats="none")


def test_two_sessions_in_two_work_folders(live, tmp_path):
    from satk.docs.cleanup import remove_tree

    work, _ = live
    other = tmp_path / "work2"
    other.mkdir()
    os.environ["SATK_PATHS_WORK"] = str(other)
    config.reset()
    try:
        st = L.start("second", owner_pid=os.getpid(), idle=300)
        assert st["up"] and (other / "run" / "endpoints" / "blender-second.json").is_file()
        r = api.call("mesh.primitive", {"kind": "cube", "name": "only_here"}, session="second")
        assert r["stats"]["scene"]["objects"] == 1
        with pytest.raises(SatkError):
            api.call("scene.info", session=NAME)  # the first session is not in this work folder
    finally:
        api.close_all()
        L.stop("second")
        os.environ["SATK_PATHS_WORK"] = str(work)
        config.reset()
    assert "only_here" not in {o["name"] for o in _c("scene.info")["result"]["objects"]}
    remove_tree(other)


def test_no_thumbnails_no_bytecode_and_clean_stop(live):
    work, _ = live
    _c("scene.info", {"limit": 0}, save=str(work / "out" / "a.blend"))
    st = L.status(NAME)
    pid = st["pid"]
    assert L.stop(NAME)["stopped"] and not C.pid_alive(pid) and C.pid_alive(os.getpid())
    log = (work / "studio" / NAME / "blender.log").read_text(encoding="utf-8", errors="replace")
    assert "stopped (quit)" in log
    saved = [work / "out" / "a.blend", *work.rglob("*.blend")]
    assert len(saved) > 3 and _thumbnails_of(saved) == []
    pyc = list(Path(L.main_script()).parents[1].rglob("__pycache__"))
    assert pyc == [], pyc
