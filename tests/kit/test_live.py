"""The kit with the real Blender 5.1 (markers blender, slow; the template/round-trip tests also game).

Plans are built first from the vanilla index of the current configuration (names and numbers only); then the
work directory is redirected to a temp dir (``SATK_PATHS_WORK``): DragonFF is extracted there from the
read-only clone, and the studio session ``kitlive`` (owner = this process) runs there. Reads the game only.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from satk.core import config
from satk.core.errors import SatkError
from satk.core.registry import get_op

pytestmark = [pytest.mark.blender, pytest.mark.slow]

NAME = "kitlive"
KINDS = ("bike", "boat", "heli", "plane", "prop", "building", "weapon")


def _plans() -> dict:
    """Plans of premier and one exemplar per kind (``{}`` without a vanilla index)."""
    from satk.kit.plan import template_plan

    try:
        out = {"premier": template_plan(like="model:426", name="kitcar", ghost=True)}
        for k in KINDS:
            out[k] = template_plan(kind=k, name=f"kit{k}"[:17])
    except SatkError:
        return {}
    return out


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    from satk.blender import runner
    from satk.docs.cleanup import remove_tree
    from satk.studio import api
    from satk.studio import launcher as L

    try:
        runner.blender_exe()
    except SatkError as e:
        pytest.skip(str(e))
    from satk.blender import gamedata
    from satk.index import api as index_api

    plans = _plans()
    index_api.clear_cache()   # the plans read the configured index: later tests must not see its handle
    work = tmp_path_factory.mktemp("kit") / "work"
    work.mkdir()
    pdir = work / "plans"
    pdir.mkdir()
    paths = {}
    for k, p in plans.items():
        f = pdir / f"{k}.json"
        f.write_text(json.dumps(p), encoding="utf-8")
        paths[k] = str(f)
    old = os.environ.get("SATK_PATHS_WORK")
    os.environ["SATK_PATHS_WORK"] = str(work)
    config.reset()
    try:
        runner.ensure_dragonff()
    except SatkError as e:
        os.environ.pop("SATK_PATHS_WORK", None) if old is None else os.environ.__setitem__("SATK_PATHS_WORK", old)
        config.reset()
        pytest.skip(str(e))
    L.start(NAME, owner_pid=os.getpid(), idle=600)
    try:
        yield {"work": work, "plans": plans, "paths": paths}
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
            index_api.clear_cache()
            gamedata.clear_cache()
            remove_tree(work.parent)


def _call(method: str, params: dict | None = None, **kw) -> dict:
    from satk.studio import api

    return api.call(method, params, session=NAME, stats="none", **kw)


def _py(code: str, args: dict | None = None) -> dict:
    return (_call("python", {"code": code, "args": args or {}}).get("result") or {}).get("value") or {}


def _need(live, key: str) -> str:
    if key not in live["paths"]:
        pytest.skip("no vanilla index for the template plans")
    return live["paths"][key]


_SPHERE = """
import bmesh
bm = bmesh.new()
bmesh.ops.create_uvsphere(bm, u_segments=16, v_segments=8, radius=1.0, calc_uvs=False)
me = bpy.data.meshes.new(args['name'])
bm.to_mesh(me)
bm.free()
for p in me.polygons:
    p.use_smooth = False
o = bpy.data.objects.new(args['name'], me)
bpy.context.scene.collection.objects.link(o)
if args.get('wn'):
    m = o.modifiers.new('wn', 'WEIGHTED_NORMAL')
    m.keep_sharp = True
    o.dff.export_split_normals = True
result['tris'] = len(me.polygons)
"""


def test_shade_reaches_the_dff(live):
    """sa_shade on a flat 16x8 sphere: the re-read DFF is smooth; WN on flat faces stays flat (the order rule)."""
    _call("scene.clear")
    _py(_SPHERE, {"name": "ball"})
    r = _call("kit.shade", {"objects": ["ball"]})["result"]
    row = dict(zip(r["cols"], r["rows"][0]))
    assert row["flat_before"] == pytest.approx(1.0) and row["bend_before"] == pytest.approx(0.0, abs=1e-6)
    assert row["bend_dff"] >= 13.0 and row["flat_dff"] == 0.0 and row["verts_per_tri"] <= 0.6
    _py(_SPHERE, {"name": "ball_wn", "wn": True})
    m = _py("from satk_blender.kit.shade import dff_shade_metrics\n"
            "result.update(dff_shade_metrics(bpy.data.objects['ball_wn'], ctx.out_dir))")
    assert m["shade.normal_bend"] < 0.5 and m["shade.flat_share"] > 0.9 and m["dff.verts_per_tri"] > 1.5


def test_template_premier_scaffold(live):
    path = _need(live, "premier")
    _call("scene.clear")
    t0 = time.perf_counter()
    r = _call("kit.template", {"plan": path})
    dt = time.perf_counter() - t0
    res = r["result"]
    assert res["frames"] == 51 and res["slots"] == 22 and res["materials"] >= 8 and res["col"] >= 20
    assert res["ghost"] > 0 and dt < 1.5, dt
    v = _py("result['kit'] = sum(len(o.data.vertices) for o in bpy.data.objects if o.type == 'MESH' "
            "and not o.get('satk_ghost'))\n"
            "result['ghost'] = sum(len(o.data.vertices) for o in bpy.data.objects if o.type == 'MESH' and o.get('satk_ghost'))\n"
            "c = bpy.data.collections['kitcar.dff']\n"
            "result['order'] = [o.name for o in sorted(c.objects, key=lambda o: o.dff.frame_index)][:4]\n"
            "result['paint'] = list(bpy.data.materials['kitcar.paint1'].node_tree.nodes['Principled BSDF'].inputs['Base Color'].default_value)")
    assert v["kit"] == 0 and v["ghost"] > 1000          # no vanilla vertex outside the ghost
    assert v["order"][0] == "kitcar"
    assert [round(x * 255) for x in v["paint"]] == [60, 255, 0, 255]


def test_kinds_scaffold(live):
    for k in KINDS:
        path = _need(live, k)
        r = _call("kit.template", {"plan": path, "replace": True})["result"]
        plan = live["plans"][k]
        assert r["frames"] == plan["counts"]["frames"] and r["slots"] == plan["counts"]["slots"], k
        if k == "building":
            assert r["lod"] == "lodbuilding"
        if k == "weapon":
            names = _py("result['n'] = [o.name for o in bpy.data.collections['kitweapon.dff'].objects]")["n"]
            assert "gunflash" in names


_BODY = """
import bmesh
bm = bmesh.new()
bmesh.ops.create_cube(bm, size=1.0)
bmesh.ops.subdivide_edges(bm, edges=bm.edges[:], cuts=5, use_grid_fill=True)
for v in bm.verts:
    v.co.x *= 2.2; v.co.y *= 5.6; v.co.z *= 1.1
me = bpy.data.meshes.new('body')
bm.to_mesh(me)
bm.free()
o = bpy.data.objects.new('body', me)
bpy.context.scene.collection.objects.link(o)
result['tris'] = sum(len(p.vertices) - 2 for p in me.polygons)
"""


def test_generators_and_export(live):
    _need(live, "premier")
    _call("scene.clear")
    _call("kit.template", {"plan": live["paths"]["premier"], "replace": True})
    _py(_BODY)
    _call("kit.fill", {"slot": "chassis", "objects": ["body"]})
    _call("kit.material_preset", {"object": "chassis", "role": "paint1", "slot": 0})
    for part, loc in (("door_lf_ok", [-1.1, 0.5, 0.1]), ("door_rf_ok", [1.1, 0.5, 0.1]), ("bonnet_ok", [0, 1.9, 0.5]),
                      ("bump_front_ok", [0, 2.9, -0.3])):
        _call("mesh.primitive", {"kind": "uv_sphere", "segments": 12, "rings": 6, "radius": 0.45, "name": "tmp",
                                 "location": loc})
        _call("kit.fill", {"slot": part, "objects": ["tmp"]})
    sh = _call("kit.shade", {"model": "kitcar"})["result"]
    assert sh["objects"] >= 5
    w = _call("kit.wheel", {})["result"]
    assert 16 <= w["sides"] <= 24 and w["diameter"] == 0.7 and 240 <= w["tris"] <= 400
    uv = _py("o = bpy.data.objects['wheel']\nme = o.data\n"
             "tyre = [i for i, m in enumerate(me.materials) if m and m.get('satk_role') == 'tyre']\n"
             "us = [l.uv for p in me.polygons if p.material_index in tyre for l in (me.uv_layers[0].data[i] for i in p.loop_indices)]\n"
             "result['u'] = [min(u.x for u in us), max(u.x for u in us)]\n"
             "result['tex'] = me.materials[tyre[0]].get('satk_texture')")
    assert uv["tex"] == "vehicletyres128" and 0.0 <= uv["u"][0] and uv["u"][1] <= 0.5
    vlo = _call("kit.vlo", {})["result"]
    assert vlo["tris"] <= 130
    dam = _call("kit.damage", {})["result"]
    assert dam["parts"] == 4 and 0.8 <= dam["ratio"][0] <= dam["ratio"][1] <= 1.1 and dam["p90_m_min"] >= 0.12
    col = _call("kit.col", {})["result"]
    assert col["shadow_faces"] >= 12
    _call("kit.uv_region", {"object": "chassis", "region": "grunge.paint", "project": "x", "faces": "role:paint1"})
    res = get_op("kit.export").call({"session": NAME, "col": "auto"})
    assert res["frames"] == 51 and res["atomics"] >= 9 and res["bsphere"]["fixed"] == res["atomics"]
    assert all(r[3] == "ok" for r in res["diff"]["rows"]), res["diff"]
    dff = Path(res["files"]["dff"]).read_bytes()
    from satk.formats.col import iter_col
    from satk.formats.dff import find_embedded_col
    from satk.formats.txd import parse_txd

    off, size = find_embedded_col(dff)
    assert [m.name for m in iter_col(dff[off:off + size])] == ["kitcar_col"]
    txd = parse_txd(Path(res["files"]["txd"]).read_bytes())
    assert {t.name.lower() for t in txd.textures} == {"kitcar92interior128", "kitcar92wheel64"}
    assert all(t.levels == 1 and t.d3dfmt in ("DXT1", "DXT3") for t in txd.textures)
    lint = res.get("lint") or {}
    assert (lint.get("summary") or {}).get("error", 0) == 0 and "dff.bsphere" not in (lint.get("by_rule") or {})


def test_building_lod(live):
    _need(live, "building")
    _call("scene.clear")
    _call("kit.template", {"plan": live["paths"]["building"], "replace": True})
    _py(_BODY.replace("'body'", "'hd'"))
    _call("kit.fill", {"slot": "kitbuilding", "objects": ["hd"]})
    r = _call("kit.lod", {"model": "kitbuilding"})["result"]
    assert 0.15 <= r["ratio"] <= 0.3 and r["object"] == "lodbuilding"


def test_game_ready_class_prop(live, tmp_path):
    obj = live["work"] / "cube.obj"
    obj.write_text("v -0.5 -0.5 0\nv 0.5 -0.5 0\nv 0.5 0.5 0\nv -0.5 0.5 0\nv -0.5 -0.5 1\nv 0.5 -0.5 1\nv 0.5 0.5 1\n"
                   "v -0.5 0.5 1\nf 1 4 3 2\nf 5 6 7 8\nf 1 2 6 5\nf 2 3 7 6\nf 3 4 8 7\nf 4 1 5 8\n", encoding="utf-8")
    r = get_op("blender.game_ready").call({"src": str(obj), "name": "kitcube", "asset_class": "prop", "uv": "box",
                                          "draw": 500})
    st = r["stats"]
    assert 205 <= st["texel_px_m"] <= 250 and st["texel_target_px_m"] == 228.0
    assert st["col_light"][0] > 0 and any(w.startswith("DRAW_CLAMPED") for w in r["warn"])
    ide = Path(r["files"]["ide"]).read_text(encoding="utf-8")
    assert "kitcube, kitcube, 300, 0" in ide
    from satk.model3d.mesh import build_scene

    sc = build_scene(Path(r["files"]["dff"]).read_bytes(), name="kitcube")
    night = sc.meshes[0].night
    rs = sum(night[0::4])
    bs = sum(night[2::4])
    assert night is not None and rs > bs              # warm nights (R > B), never blue
    from satk.rw import col as RC

    recs, _tail = RC.split_models(Path(r["files"]["col"]).read_bytes())
    m = RC.decode_model(recs[0])
    assert m.faces and all((f[4] & 15) > 0 for f in m.faces)   # face light: day nibble never 0


@pytest.mark.game
def test_reexport_premier_roundtrip(live, clean_root):
    imp = get_op("blender.import_model").call({"id": "model:426"})
    blend = imp["files"]["blend"]
    r = _call("kit.adopt", {}, blend=blend)["result"]
    assert r["frames"] == 51 and r["slots"] == 22 and r["col"] >= 20
    res = get_op("kit.export").call({"session": NAME, "name": "premier", "col": "kit"})
    assert res["bsphere"]["fixed"] == 22 and res["bsphere"]["max_move_m"] > 1.0
    dff = Path(res["files"]["dff"]).read_bytes()
    from satk.formats.col import iter_col
    from satk.formats.dff import find_embedded_col
    from satk.formats.txd import parse_txd

    off, size = find_embedded_col(dff)
    assert [m.name for m in iter_col(dff[off:off + size])] == ["premier_col"]
    txd_b = Path(res["files"]["txd"]).read_bytes()
    txd = parse_txd(txd_b)
    assert len(txd_b) < 50_000 and len(txd.textures) == 2
    assert all(t.levels == 1 and t.d3dfmt.startswith("DXT") for t in txd.textures)
    lint = get_op("asset.lint").call({"target": res["files"]["dff"], "preset": "strict"})
    assert lint["summary"]["error"] == 0 and "dff.bsphere" not in (lint.get("by_rule") or {})
    # the plain blender.export of the same scene: spheres fixed and the COL named premier_col as well
    ex = get_op("blender.export").call({"blend": blend, "objects": ["premier"], "target": "modloader"})
    d = Path(ex["out"])
    lint2 = get_op("asset.lint").call({"target": str(d / "premier.dff"), "preset": "strict"})
    assert "dff.bsphere" not in (lint2.get("by_rule") or {})
    assert not (d / "premier.ide").exists()            # no empty IDE fragment for cars
    b = (d / "premier.dff").read_bytes()
    off, size = find_embedded_col(b)
    assert [m.name for m in iter_col(b[off:off + size])] == ["premier_col"]


def test_uv_region_bake_and_own_texture(live):
    """Atlas UVs land in the region (Blender v flipped); Cycles bakes AO + edge mask; an own texture from a PNG."""
    from satk.kit import atlas

    _call("scene.clear")
    _call("mesh.primitive", {"kind": "cube", "size": [1.0, 2.0, 0.5], "name": "panel"})
    r = _call("kit.uv_region", {"object": "panel", "region": "generic.glass_core", "project": "auto", "margin": 0.0})
    assert r["result"]["faces"] == 6 and r["result"]["texture"] == "vehiclegeneric256"
    box = _py("me = bpy.data.objects['panel'].data\nuv = [d.uv for d in me.uv_layers[0].data]\n"
              "result['b'] = [min(u.x for u in uv), min(u.y for u in uv), max(u.x for u in uv), max(u.y for u in uv)]")["b"]
    u0, v0, u1, v1 = atlas.to_blender(atlas.region("generic.glass_core")["rect"])
    assert u0 - 1e-5 <= box[0] and box[2] <= u1 + 1e-5 and v0 - 1e-5 <= box[1] and box[3] <= v1 + 1e-5
    _call("mesh.primitive", {"kind": "cube", "size": [1.0, 1.0, 1.0], "name": "crate"})
    b = _call("kit.bake", {"object": "crate", "size": 64, "samples": 4})["result"]
    from satk.media.png import png_size

    assert set(b["files"]) == {"ao", "edge"} and all(png_size(f) == (64, 64) for f in b["files"].values())
    png = b["files"]["edge"]
    m = _call("kit.material_preset", {"object": "crate", "role": "map", "model": "kitcrate", "image": png})["result"]
    assert m["material"] == "kitcrate.map" and m["texture"] == "kitcrate_tex"
    own = _py("img = bpy.data.images['kitcrate_tex']\nresult['s'] = list(img.size)\nresult['shared'] = bool(img.get('satk_shared'))")
    assert own == {"s": [64, 64], "shared": False}
