"""The form methods in the real Blender 5.1 (markers blender, slow): mesh.loft shape sections (smooth steps, half,
parts), mesh.sweep, mesh.transform falloff, the near/loop/grow selectors, mesh.relax, mesh.deform, the soft
primitives, mesh.attach (snap, weld, bridge), mesh.flare and the python reply cap. Synthetic geometry only."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from satk.core import config
from satk.core.errors import SatkError
from satk.studio import api
from satk.studio import launcher as L
from satk.studio.mock import primitive_counts

pytestmark = [pytest.mark.blender, pytest.mark.slow]

NAME = "form"

SEDAN = [
    {"at": -2.3, "shape": {"w": 0.85, "h": 0.6, "z": 0.3, "exp": 3, "mid": 0.8}},
    {"at": -1.2, "shape": {"w": 0.95, "h": 0.75, "z": 0.25, "exp_top": 4, "exp_bottom": 3, "mid": 0.9,
                           "flat_bottom": True}},
    {"at": 0.0, "shape": {"w": 0.95, "h": 1.2, "z": 0.25, "exp_top": 4, "exp_bottom": 3, "mid": 0.55, "crown": 0.06,
                          "tumble": 0.75, "flat_bottom": True}},
    {"at": 1.2, "shape": {"w": 0.95, "h": 0.75, "z": 0.25, "exp_top": 4, "exp_bottom": 3, "mid": 0.9,
                          "flat_bottom": True}},
    {"at": 2.3, "shape": {"w": 0.85, "h": 0.6, "z": 0.3, "exp": 3, "mid": 0.8}},
]


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    from satk.blender import runner
    from satk.docs.cleanup import remove_tree

    dff = runner.dragonff_root()            # the workspace's DragonFF copy (kit.blank_split needs it)
    work = tmp_path_factory.mktemp("form") / "work"
    work.mkdir()
    old = os.environ.get("SATK_PATHS_WORK")
    old_dff = os.environ.get("SATK_DRAGONFF")
    if (dff / "dragonff").is_dir():
        os.environ["SATK_DRAGONFF"] = str(dff)
    os.environ["SATK_PATHS_WORK"] = str(work)
    config.reset()
    L.start(NAME, owner_pid=os.getpid(), idle=600)
    try:
        yield work
    finally:
        api.close_all()
        try:
            L.stop(NAME)
        finally:
            for k, v in (("SATK_PATHS_WORK", old), ("SATK_DRAGONFF", old_dff)):
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            config.reset()
            remove_tree(work.parent)


def _c(method: str, params: dict | None = None, **kw) -> dict:
    r = api.call(method, params, session=NAME, **kw)
    limit = 5300 if method == "python" else 1700
    assert len(json.dumps(r, separators=(",", ":")).encode()) <= limit, method
    return r


def _py(code: str, **args) -> dict:
    return _c("python", {"code": code, "args": args}, stats="none", checkpoint=False)["result"].get("value") or {}


def _obj(r: dict, name: str) -> dict:
    return r["stats"]["objects"][name]


def test_loft_shape_sections_half_steps_and_parts(live):
    _c("scene.clear")
    r = _c("mesh.loft", {"name": "body", "samples": 24, "half": True, "steps": 2, "sections": SEDAN,
                         "parts": [{"name": "bonnet", "at": [1.25, 2.4], "angle": [0, 60]},
                                   {"name": "door_f", "at": [-0.1, 1.1], "angle": [55, 130]}], "model": "car"})
    res = r["result"]
    assert res["rings"] == 5 + 4 * 2 and res["mirror"] == "mirror"
    assert res["parts"]["bonnet"] > 0 and res["parts"]["door_f"] > 0 and res["parts"]["chassis"] > 0
    body = _obj(r, "body")
    assert body["geo.pieces"] == 1 and body["geo.open_edges"] == 0       # welded, closed after the mirror
    assert body["dims"] == [1.9, 4.6, 1.2] and "shade.flat_share" not in body  # step stats: no shading numbers
    full = _c("scene.stats", {"objects": "body"})["result"]["objects"]["body"]   # an explicit query has them
    assert full["shade.flat_share"] < 0.1                                # smooth-shaded sides
    v = _py("import json\no = bpy.data.objects['body']; me = o.data\n"
            "xs = [v.co.x for v in me.vertices]\n"
            "result.update(minx=min(xs), smooth=sum(p.use_smooth for p in me.polygons), faces=len(me.polygons),\n"
            "  attr=me.attributes['satk_part'].domain, names=json.loads(o['satk_part_names']),\n"
            "  blank=json.loads(o['satk_blank'])['parts'], uv=[u.name for u in me.uv_layers])")
    assert v["minx"] >= -1e-6 and v["attr"] == "FACE" and v["names"] == ["chassis", "bonnet", "door_f"]
    assert v["smooth"] >= v["faces"] - 2 and v["uv"] == ["UVMap"]
    assert {"name": "door_f", "slot": "door_{s}f_ok", "sided": True} in v["blank"]
    # the cut the kit makes along the attribute: doors per side
    if not os.environ.get("SATK_DRAGONFF"):
        pytest.skip("no DragonFF copy in the workspace (satk blender doctor)")
    split = _c("kit.blank_split", {"object": "body"})["result"]
    names = {row[0] for row in split["parts"]}
    assert {"car_bonnet", "car_door_f_l", "car_door_f_r", "car_chassis"} <= names


def test_loft_interp_smooth_vs_linear_and_errors(live):
    _c("scene.clear")
    secs = [{"at": 0, "shape": {"w": 0.5, "h": 0.4}}, {"at": 1, "shape": {"w": 1.0, "h": 0.4}},
            {"at": 2, "shape": {"w": 1.0, "h": 0.4}}]
    _c("mesh.loft", {"name": "s", "samples": 8, "steps": 1, "sections": secs})
    _c("mesh.loft", {"name": "l", "samples": 8, "steps": 1, "interp": "linear", "sections": secs})
    v = _py("result.update({n: max(v.co.x for v in bpy.data.objects[n].data.vertices if abs(v.co.y - 0.5) < 1e-6)"
            " for n in ('s', 'l')})")
    assert v["l"] == pytest.approx(0.75, abs=1e-4)
    assert 0.75 < v["s"] < 1.0                    # the monotone spline rises faster into the plateau, no overshoot
    # point sections keep working (the old mirror form): 3 rings of 12 points, quads + 2 capped ends
    r = _c("mesh.loft", {"name": "p", "samples": 12, "mirror": True, "sections": [
        {"at": y, "points": [[0, 0.5], [0.4, 0.4], [0.5, 0], [0.4, -0.3], [0, -0.35]]} for y in (-1, 0, 1)]})
    assert r["result"]["faces"] == 2 * 12 + 2 and _obj(r, "p")["geo.open_edges"] == 0
    for bad, word in (({"samples": 7, "sections": secs}, "even"),
                      ({"samples": 8, "steps": 1, "sections": [secs[0], secs[2], secs[1]]}, "one way"),
                      ({"samples": 8, "sections": [{"at": 0, "shape": {"w": 1}}, secs[1]]}, "shape.h"),
                      ({"samples": 8, "sections": [{"at": 0}, secs[1]]}, "points' or 'shape")):
        with pytest.raises(SatkError) as ei:
            _c("mesh.loft", dict(bad, name="x"))
        assert ei.value.code == "BAD_PARAMS" and word in ei.value.msg, ei.value.msg


def test_sweep_bumper_half_and_closed_loop(live):
    _c("scene.clear")
    r = _c("mesh.sweep", {"name": "bump", "path": [[0, 2.5, 0.4], [0.6, 2.45, 0.4], [0.95, 2.2, 0.4],
                                                   [1.0, 1.9, 0.4]], "samples": 10,
                          "profile": {"w": 0.08, "h": 0.2, "exp": 3}, "profile_samples": 8, "half": True,
                          "scale": [1, 1, 0.8]})
    assert r["result"]["stations"] == 10 and r["result"]["mirror"] == "mirror"
    b = _obj(r, "bump")
    assert b["geo.pieces"] == 1 and b["geo.open_edges"] == 0             # the halves merge on x = 0
    assert b["dims"][2] == pytest.approx(0.2, abs=0.01)
    r = _c("mesh.sweep", {"name": "ring", "path": [[1, 0, 0], [0, 1, 0], [-1, 0, 0], [0, -1, 0]], "closed": True,
                          "samples": 24, "profile": [[0.05, 0], [0, 0.05], [-0.05, 0], [0, -0.05]]})
    ring = _obj(r, "ring")
    assert ring["geo.open_edges"] == 0 and ring["tris"] == 24 * 4 * 2
    with pytest.raises(SatkError) as ei:
        _c("mesh.sweep", {"name": "x", "path": [[0.3, 0, 0], [1, 0, 0]], "profile": [[0, 0], [0, 1], [1, 0]],
                          "half": True})
    assert ei.value.code == "BAD_PARAMS" and "x = 0" in ei.value.msg


def test_soft_primitives_match_the_mock(live):
    _c("scene.clear")
    for p in ({"kind": "rounded_box", "size": [0.4, 1.2, 0.3], "radius": 0.06, "segments": 3, "name": "rb"},
              {"kind": "capsule", "radius": 0.05, "depth": 0.6, "segments": 10, "rings": 3, "name": "cap"}):
        r = _c("mesh.primitive", p)
        o = _obj(r, p["name"])
        assert (o["verts"], o["tris"]) == primitive_counts(p["kind"], p)
        assert o["geo.open_edges"] == 0
        full = _c("scene.stats", {"objects": p["name"]})["result"]["objects"][p["name"]]
        assert full["shade.flat_share"] == 0.0
    info = _c("mesh.info", {"object": "rb"})["result"]
    assert info["smooth_faces"] == info["faces"] and info["bbox"] == [[-0.2, -0.6, -0.15], [0.2, 0.6, 0.15]]


def test_transform_falloff_and_selectors(live):
    _c("scene.clear")
    _c("mesh.primitive", {"kind": "grid", "x_segments": 20, "y_segments": 20, "size": [2, 2], "name": "g"})
    r = _c("mesh.transform", {"object": "g", "select": {"near": {"point": [0, 0, 0], "radius": 0.08}},
                              "translate": [0, 0, 0.2], "falloff": {"radius": 0.5, "curve": "smooth"}})
    assert r["result"]["verts"] == 9 and r["result"]["soft"] > 20        # 4 faces around the point
    v = _py("me = bpy.data.objects['g'].data\n"
            "z = {(round(v.co.x, 2), round(v.co.y, 2)): v.co.z for v in me.vertices}\n"
            "result.update(c=z[(0.0, 0.0)], mid=z[(0.3, 0.0)], far=z[(0.8, 0.0)])")
    assert v["c"] == pytest.approx(0.2) and 0.01 < v["mid"] < 0.19 and v["far"] == 0.0
    # connected falloff stops at a separate piece (a cube floating 30 cm above the selection)
    _c("mesh.primitive", {"kind": "cube", "size": 0.1, "name": "c", "location": [0.6, 0.6, 0.3]})
    _c("scene.transform", {"object": "c", "apply": True, "apply_location": True})
    _c("scene.join", {"objects": ["g", "c"]})
    r = _c("mesh.transform", {"object": "g", "select": {"near": {"point": [0.6, 0.6, 0], "radius": 0.08}},
                              "translate": [0, 0, 0.1], "falloff": {"radius": 1.2, "connected": True}})
    v = _py("me = bpy.data.objects['g'].data\nresult['cube'] = max(v.co.z for v in me.vertices)")
    assert v["cube"] == pytest.approx(0.35)
    # loop / ring / grow on a lofted tube (rings of 8 around y)
    _c("scene.clear")
    _c("mesh.loft", {"name": "t", "samples": 8, "cap": False, "sections": [
        {"at": y, "shape": {"w": 0.5, "h": 1.0, "z": -0.5}} for y in (0, 1, 2, 3)]})
    r = _c("mesh.transform", {"object": "t", "select": {"loop": {"point": [0.5, 1.0, 0.0], "dir": "z"}},
                              "scale": [1.5, 1, 1.5]})
    assert r["result"]["verts"] == 8                                     # one section ring, not its faces
    r = _c("mesh.group", {"object": "t", "name": "strip", "select": {"loop": {"point": [0.5, 1.5, 0.0],
                                                                              "ring": True, "dir": "z"}}})
    assert r["result"]["verts"] == 8
    r = _c("mesh.group", {"object": "t", "name": "lane", "select": {"loop": {"point": [0.5, 1.5, 0.0], "dir": "y"},
                                                                     "grow": 1}})
    assert r["result"]["verts"] > 8
    with pytest.raises(SatkError) as ei:
        _c("mesh.transform", {"object": "t", "select": {"near": {"point": [0, 0]}}, "translate": [0, 0, 1]})
    assert ei.value.code == "BAD_PARAMS"


def test_relax_and_deform(live):
    _c("scene.clear")
    _c("mesh.primitive", {"kind": "grid", "x_segments": 10, "y_segments": 10, "size": [2, 2], "name": "n"})
    _py("import random\nrandom.seed(4)\nme = bpy.data.objects['n'].data\n"
        "for v in me.vertices:\n    v.co.z += random.uniform(-0.05, 0.05)\nme.update()")
    rough = "me = bpy.data.objects['n'].data\nzs = [v.co.z for v in me.vertices if abs(v.co.x) < 0.95 and abs(v.co.y) < 0.95]\n" \
            "m = sum(zs) / len(zs)\nresult['dev'] = (sum((z - m) ** 2 for z in zs) / len(zs)) ** 0.5\n" \
            "result['edge'] = sorted(round(v.co.z, 6) for v in me.vertices if abs(v.co.x) > 0.99)[:3]"
    before = _py(rough)
    r = _c("mesh.relax", {"object": "n", "iterations": 10})
    after = _py(rough)
    assert r["result"]["verts"] == 81 and after["dev"] < before["dev"] * 0.5
    assert after["edge"] == before["edge"]                                # the open border stays
    _c("mesh.primitive", {"kind": "rounded_box", "size": [0.4, 2.0, 0.4], "radius": 0.05, "segments": 2, "name": "b"})
    _c("mesh.loopcut", {"object": "b", "axis": "y", "count": 8})
    r = _c("mesh.deform", {"object": "b", "kind": "bend", "params": {"axis": "z", "angle": 90}, "apply": True})
    assert r["result"]["applied"] and _obj(r, "b")["dims"][1] < 1.8
    r = _c("mesh.deform", {"object": "b", "kind": "taper", "params": {"axis": "z", "factor": 0.5,
                                                                      "origin": [0, 0, 0]}})
    assert r["result"]["modifier"] and r["result"]["helpers"] == ["b.deform_origin"]
    assert "deform_taper" in _c("modifier.list", {"object": "b"})["result"]["stack"][0]["name"]
    _c("mesh.primitive", {"kind": "cube", "size": 1, "name": "lat"})
    _c("mesh.subdivide", {"object": "lat", "cuts": 3})
    r = _c("mesh.deform", {"object": "lat", "kind": "lattice", "apply": True, "params": {
        "resolution": [3, 3, 3], "moves": [{"point": [1, 1, 2], "offset": [0, 0, 0.3]}]}})
    assert r["result"]["applied"] and _obj(r, "lat")["dims"][2] > 1.05
    assert "lat.deform_lattice" not in {o["name"] for o in _c("scene.info")["result"]["objects"]}
    with pytest.raises(SatkError) as ei:
        _c("mesh.deform", {"object": "lat", "kind": "melt"})
    assert ei.value.code == "BAD_PARAMS" and ei.value.did_you_mean is not None


def test_attach_snap_weld_bridge(live):
    _c("scene.clear")
    _c("mesh.primitive", {"kind": "grid", "x_segments": 4, "y_segments": 4, "size": [2, 2], "name": "door"})
    _c("mesh.primitive", {"kind": "rounded_box", "size": [0.1, 0.1, 0.3], "radius": 0.02, "segments": 2,
                          "name": "stalk", "location": [0, 0, 0.17]})
    r = _c("mesh.attach", {"object": "stalk", "to": "door", "select": {"where": ["z<-0.1"]}, "rigid": True,
                           "max_dist": 0.1})
    assert r["result"]["verts"] > 0 and 15 < r["result"]["gap_mm"] < 45   # the gap it closed (2-4 cm)
    v = _py("o = bpy.data.objects['stalk']\nresult['zmin'] = min((o.matrix_world @ v.co).z for v in o.data.vertices)")
    assert v["zmin"] == pytest.approx(0.0, abs=1e-4)                     # touches the door now
    with pytest.raises(SatkError) as ei:
        _c("mesh.attach", {"object": "stalk", "to": "door", "max_dist": 0.0001, "mode": "weld"})
    assert ei.value.code == "NOT_FOUND"
    # weld: two open half tubes meeting at y = 0 become one welded shell
    secs = lambda a, b: [{"at": a, "shape": {"w": 0.3, "h": 0.6, "z": -0.3}},  # noqa: E731
                         {"at": b, "shape": {"w": 0.3, "h": 0.6, "z": -0.3}}]
    _c("mesh.loft", {"name": "ta", "samples": 8, "cap": False, "sections": secs(-1, 0)})
    _c("mesh.loft", {"name": "tb", "samples": 8, "cap": False, "sections": secs(0.004, 1)})
    r = _c("mesh.attach", {"object": "tb", "to": "ta", "mode": "weld", "max_dist": 0.01})
    assert r["result"]["merged"] == 8 and r["result"]["joined"] == "tb"
    assert _obj(r, "ta")["geo.pieces"] == 1 and _obj(r, "ta")["verts"] == 24
    # weld_boundaries (what kit.fill calls after a join): two halves cut from one shell become one piece again
    _c("mesh.loft", {"name": "wa", "samples": 8, "cap": False, "sections": secs(3, 4)})
    _c("mesh.loft", {"name": "wb", "samples": 8, "cap": False, "sections": secs(4, 5)})
    _c("scene.join", {"objects": ["wa", "wb"]})
    v = _py("from satk_blender.studio.methods.attach import weld_boundaries\n"
            "result['merged'] = weld_boundaries(bpy.data.objects['wa'], 1e-4)")
    assert v["merged"] == 8
    assert _c("scene.stats", {"objects": "wa"})["result"]["objects"]["wa"]["geo.pieces"] == 1
    # bridge: a gap of 10 cm filled between two rings of different sizes
    _c("mesh.loft", {"name": "tc", "samples": 12, "cap": False, "sections": [
        {"at": 1.1, "shape": {"w": 0.25, "h": 0.5, "z": -0.25}}, {"at": 2, "shape": {"w": 0.25, "h": 0.5, "z": -0.25}}]})
    r = _c("mesh.attach", {"object": "tc", "to": "ta", "mode": "bridge", "max_dist": 0.2})
    assert r["result"]["faces"] >= 12 and _obj(r, "ta")["geo.pieces"] == 1


def test_flare_cuts_a_welded_arch_with_liner(live):
    _c("scene.clear")
    _c("mesh.loft", {"name": "body", "samples": 24, "half": True, "steps": 2, "sections": SEDAN})
    r = _c("mesh.flare", {"object": "body", "arch": {"center": [0.9, 1.4, 0.34], "radius": 0.42}, "segments": 6,
                          "width": 0.06, "out": 0.03, "liner_material": "liner"})
    assert r["result"]["rims"] == 1 and r["result"]["band_verts"] > 6
    body = _obj(r, "body")
    assert body["geo.pieces"] == 1 and body["geo.open_edges"] == 0      # the liner closes the arch
    assert 1.9 < body["dims"][0] < 1.97                                 # the band stands up to 3 cm proud
    info = _c("mesh.info", {"object": "body"})["result"]
    assert info["groups"]["arch_liner"] > 0 and info["groups"]["arch_flare"] > 0 and "liner" in info["materials"]
    r = _c("mesh.flare", {"object": "body", "arch": {"center": [0.9, -1.4, 0.34], "radius": 0.42}, "segments": 6,
                          "out": 0})                                     # a plain sedan cut
    assert _obj(r, "body")["geo.open_edges"] == 0
    with pytest.raises(SatkError) as ei:
        _c("mesh.flare", {"object": "body", "arch": {"center": [0.9, 9.0, 0.3], "radius": 0.4}, "segments": 6})
    assert ei.value.code == "NOT_FOUND"
    with pytest.raises(SatkError) as ei:
        _c("mesh.flare", {"object": "body", "arch": {"center": [0.9, 1.4, 0.3], "radius": 0.4}})
    assert ei.value.code == "BAD_PARAMS" and "segments" in ei.value.msg


def test_python_reply_cap_and_out(live):
    big = _c("python", {"code": "result['rows'] = [{'i': i, 'name': 'x' * 30} for i in range(400)]"},
             stats="none", checkpoint=False)["result"]
    assert big["value_chars"] > 4000 and big["out"].endswith(".json") and big["value"].endswith("...")
    data = json.loads(Path(big["out"]).read_text(encoding="utf-8"))
    assert len(data["rows"]) == 400
    mid = _c("python", {"code": "result['s'] = 'y' * 3500"}, stats="none", checkpoint=False)["result"]
    assert len(mid["value"]["s"]) == 3500                                # inline up to 4,000 characters
    out = str(live / "tmp" / "dump.json")
    r = _c("python", {"code": "result['a'] = 1\nresult['b'] = [1, 2]", "out": out}, stats="none",
           checkpoint=False)["result"]
    assert r["out"] == out.replace("\\", "/") and r["keys"] == ["a", "b"] and "value" not in r
    with pytest.raises(SatkError) as ei:
        _c("python", {"code": "result['a'] = 1", "out": str(live.parent.parent / "outside.json")})
    assert ei.value.code == "PROTECTED_PATH"


def test_sweep_along_a_curve_object_cast_and_flare_by_selection(live):
    _c("scene.clear")
    _c("mesh.curve", {"name": "rail", "kind": "smooth", "points": [[0, 0, 0], [0.5, 0.5, 0.1], [1, 0.4, 0.2]]})
    r = _c("mesh.sweep", {"name": "pipe", "path": "rail", "samples": 9, "profile": {"w": 0.03, "h": 0.06},
                          "profile_samples": 8})
    assert r["result"]["stations"] == 9 and _obj(r, "pipe")["geo.open_edges"] == 0
    _c("mesh.primitive", {"kind": "rounded_box", "size": 1, "radius": 0.2, "segments": 3, "name": "ball"})
    r = _c("mesh.deform", {"object": "ball", "kind": "cast", "params": {"type": "sphere", "factor": 1.0},
                           "apply": True})
    v = _py("import math\nme = bpy.data.objects['ball'].data\nrs = [v.co.length for v in me.vertices]\n"
            "result.update(lo=min(rs), hi=max(rs))")
    assert v["hi"] - v["lo"] < 0.02                                     # cast to a sphere: all radii equal
    # a full (not half) body: flare both sides at once; an opening given by a selection
    _c("mesh.loft", {"name": "full", "samples": 24, "steps": 1, "sections": SEDAN})
    r = _c("mesh.flare", {"object": "full", "arch": {"center": [0.9, 1.4, 0.34], "radius": 0.42}, "segments": 6,
                          "out": 0, "both": True})
    assert r["result"]["rims"] == 2 and _obj(r, "full")["geo.open_edges"] == 0
    r = _c("mesh.flare", {"object": "full", "select": {"near": {"point": [0.95, -0.2, 0.6], "radius": 0.12}},
                          "out": 0.01, "width": 0.04, "depth": 0.1})
    assert r["result"]["rims"] == 1 and _obj(r, "full")["geo.open_edges"] == 0


def test_smart_unwrap_keeps_unselected_faces(live):
    """uv.unwrap smart with a selection runs in face select mode: the top of a cube whose four vertices are all
    selected (by the side faces) keeps its UVs (in vertex mode Blender would flush it into the unwrap)."""
    _c("scene.clear")
    _c("mesh.primitive", {"kind": "cube", "size": 1, "name": "uvbox"})
    _c("uv.unwrap", {"object": "uvbox", "method": "planar", "axis": "z", "size": 10})
    top = ("me = bpy.data.objects['uvbox'].data\nuv = me.uv_layers.active.data\n"
           "f = [p for p in me.polygons if p.normal.z > 0.9][0]\n"
           "result['uv'] = sorted([round(uv[i].uv.x, 4), round(uv[i].uv.y, 4)] for i in f.loop_indices)\n"
           "result['mode'] = list(bpy.context.scene.tool_settings.mesh_select_mode)")
    before = _py(top)
    r = _c("uv.unwrap", {"object": "uvbox", "method": "smart", "select": {"side": "+z", "invert": True}})
    assert r["result"]["faces"] == 5
    after = _py(top)
    assert after["uv"] == before["uv"] and after["mode"] == before["mode"]
