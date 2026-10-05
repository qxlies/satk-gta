"""GLB and OBJ writers: structure, consistency and determinism (synthetic models)."""

from __future__ import annotations

import struct

import pytest

from satk.model3d.gltf import ZUP_TO_YUP, build_glb, parse_glb
from satk.model3d.mesh import build_scene
from satk.model3d.obj import build_obj
from satk.model3d.textures import TxdChain, resolve_materials

@pytest.fixture
def car(m):
    sc = build_scene(m.car_dff(), name="infernus", sec="cars")
    ch = TxdChain([("infernus", m.solid_txd({"tyre": (30, 30, 30, 255)})),
                   ("vehicle", m.solid_txd({"paint": (200, 200, 200, 255)}))])
    return sc, resolve_materials(sc.materials, ch, {1: (200, 10, 10)})


def test_glb_car_structure(car, validate_glb):
    sc, mats = car
    glb, stats = build_glb(sc, mats)
    doc = validate_glb(glb)
    assert stats["tris"] == sc.tris == 60 and stats["meshes"] == 5 and stats["textures"] == 2
    assert stats["tex_missing"] == 0
    root = doc["nodes"][0]
    assert root["name"] == "satk_root" and root["matrix"][4:7] == [0.0, 0.0, -1.0]   # +Y(gta) -> -Z(gltf)
    names = {n["name"]: n for n in doc["nodes"]}
    assert names["chassis_vlo"]["extras"] == {"satk_state": "vlo"}
    assert names["bonnet_dam"]["extras"] == {"satk_state": "dam"}
    assert "wheel@wheel_lf_dummy" not in names                      # preview-only copies are not exported
    assert names["wheel_rf_dummy"]["matrix"][12:15] == [1.1, 1.3, 0.35]
    paint = next(x for x in doc["materials"] if x.get("extras", {}).get("satk_color_slot") == 1)
    assert paint["pbrMetallicRoughness"]["baseColorFactor"][:3] == [round(200 / 255, 7), round(10 / 255, 7), round(10 / 255, 7)]
    assert paint["extras"]["satk_key_rgba"] == "3cff00ff"
    glass = [x for x in doc["materials"] if x.get("alphaMode") == "BLEND"]
    assert glass and glass[0]["pbrMetallicRoughness"]["baseColorFactor"][3] == round(120 / 255, 7)


def test_glb_prelit_colors_and_missing_texture(m, validate_glb):
    sc = build_scene(m.box_dff(tex="nowhere", prelit=(10, 20, 30, 255)), name="b")
    mats = resolve_materials(sc.materials, TxdChain([]), None)
    glb, stats = build_glb(sc, mats)
    doc = validate_glb(glb)
    prim = doc["meshes"][0]["primitives"][0]
    assert "COLOR_0" in prim["attributes"] and doc["accessors"][prim["attributes"]["COLOR_0"]]["normalized"]
    assert "NORMAL" in prim["attributes"] and "TEXCOORD_0" in prim["attributes"]
    assert stats["tex_missing"] == 1 and doc["materials"][0]["extras"]["satk_missing_texture"] == "nowhere"
    assert "textures" not in doc


def test_glb_recomputes_normals_and_is_deterministic(m):
    sc = build_scene(m.box_dff(normals=False), name="b")
    mats = resolve_materials(sc.materials, TxdChain([("t", m.solid_txd({"boxtex": (1, 2, 3, 255)}))]), None)
    a, _ = build_glb(sc, mats)
    b, _ = build_glb(build_scene(m.box_dff(normals=False), name="b"), mats)
    assert a == b
    doc, binary = parse_glb(a)
    acc = doc["accessors"][doc["meshes"][0]["primitives"][0]["attributes"]["NORMAL"]]
    v = doc["bufferViews"][acc["bufferView"]]
    vals = struct.unpack_from("<%df" % (acc["count"] * 3), binary, v["byteOffset"])
    for i in range(0, len(vals), 3):
        assert abs(sum(x * x for x in vals[i:i + 3]) - 1.0) < 1e-4


def test_glb_alpha_texture_is_mask(m, validate_glb):
    sc = build_scene(m.box_dff(tex="leaf"), name="tree")
    txd = m.solid_txd({"leaf": (0, 200, 0, 255)}, alpha={"leaf": [0, 255] * 8})
    doc = validate_glb(build_glb(sc, resolve_materials(sc.materials, TxdChain([("t", txd)]), None))[0])
    assert doc["materials"][0]["alphaMode"] == "MASK" and doc["materials"][0]["alphaCutoff"] == 0.5


def test_obj_car(car):
    sc, mats = car
    text, mtl, pngs, stats = build_obj(sc, mats, "infernus")
    faces = [ln for ln in text.splitlines() if ln.startswith("f ")]
    assert len(faces) == stats["tris"] == 60
    assert text.splitlines()[2] == "mtllib infernus.mtl"
    objs = [ln[2:] for ln in text.splitlines() if ln.startswith("o ")]
    assert objs == ["chassis", "chassis_vlo", "bonnet_ok", "bonnet_dam", "wheel"]
    maps = [ln.split(" ", 1)[1] for ln in mtl.splitlines() if ln.startswith("map_Kd")]
    assert sorted(maps) == sorted(pngs) == ["paint.png", "tyre.png"]
    nv = sum(1 for ln in text.splitlines() if ln.startswith("v "))
    nvt = sum(1 for ln in text.splitlines() if ln.startswith("vt "))
    nvn = sum(1 for ln in text.splitlines() if ln.startswith("vn "))
    assert nv == nvt == nvn == sc.verts
    for ln in faces:
        for corner in ln.split()[1:]:
            v, vt, vn = (int(x) for x in corner.split("/"))
            assert 1 <= v <= nv and 1 <= vt <= nvt and 1 <= vn <= nvn


def test_obj_is_y_up_and_flips_v(m):
    sc = build_scene(m.box_dff(size=(2.0, 4.0, 1.0)), name="b")
    text, _mtl, _p, _s = build_obj(sc, resolve_materials(sc.materials, None, None), "b")
    vs = [tuple(float(x) for x in ln.split()[1:]) for ln in text.splitlines() if ln.startswith("v ")]
    ys = [v[1] for v in vs]
    zs = [v[2] for v in vs]
    assert (min(ys), max(ys)) == (0.0, 1.0)            # GTA z (0..1) -> OBJ y
    assert (min(zs), max(zs)) == (-2.0, 2.0)           # GTA y (-2..2) -> OBJ -z
    vts = [tuple(float(x) for x in ln.split()[1:]) for ln in text.splitlines() if ln.startswith("vt ")]
    assert vts[0] == (0.0, 0.0)                        # (u=0, v=1) -> (0, 1-1)
    assert ZUP_TO_YUP[3:6] == (0.0, 0.0, -1.0)
