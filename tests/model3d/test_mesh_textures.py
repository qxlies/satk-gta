"""satk.model3d.mesh and satk.model3d.textures on synthetic DFF/TXD data."""

from __future__ import annotations

import math

import pytest

from satk.formats.rw import FormatError
from satk.model3d import textures as TX
from satk.model3d.mesh import (IDENTITY, build_scene, mat_apply, mat_det, mat_mul, rot_x, rot_z, smooth_normals)


def _close(a, b, eps=1e-6):
    return all(abs(x - y) <= eps for x, y in zip(a, b))


def test_matrix_helpers():
    m = mat_mul(rot_z(90), rot_x(90))
    assert _close(mat_apply(rot_z(90), 1, 0, 0), (0, 1, 0))
    assert _close(mat_apply(rot_x(90), 0, 1, 0), (0, 0, 1))
    assert _close(mat_apply(m, 0, 1, 0), mat_apply(rot_z(90), 0, 0, 1))
    assert mat_det(IDENTITY) == 1.0 and abs(mat_det(m) - 1.0) < 1e-9


def test_box_scene_parts_and_counts(m):
    sc = build_scene(m.box_dff(), name="box")
    assert sc.tris == 12 and sc.verts == 24
    assert [p.name for p in sc.parts] == ["box"] and sc.parts[0].matrix == IDENTITY
    assert sc.preview_parts == sc.parts and sc.root == IDENTITY
    assert sc.texture_names() == ["boxtex"]


def test_frame_hierarchy_model_matrices(m):
    pos, tris, uv, nrm = m.box((1, 1, 1))
    g = m.geometry(pos, tris, uv=uv)
    rot90 = (0.0, 1.0, 0.0, -1.0, 0.0, 0.0, 0.0, 0.0, 1.0)          # 90 deg about Z
    dff = m.clump([g, g], [(-1, "root", (5.0, 5.0, 5.0)), (0, "a", (1.0, 0.0, 0.0), rot90), (1, "b", (2.0, 0.0, 0.0))],
                  [(1, 0), (2, 1)])
    sc = build_scene(dff, name="h")
    a, b = sc.parts
    assert sc.frames[0].model == IDENTITY                              # clump root ignored, like the engine
    assert _close(mat_apply(a.matrix, 0, 0, 0), (1, 0, 0))
    assert _close(mat_apply(b.matrix, 0, 0, 0), (1, 2, 0))             # child offset rotated by the parent
    assert _close(mat_apply(b.matrix, 1, 0, 0), (1, 3, 0))


def test_orphan_geometry_kept(m):
    pos, tris, uv, _n = m.box()
    g = m.geometry(pos, tris, uv=uv)
    sc = build_scene(m.clump([g, g], [(-1, "only", (0, 0, 0))], [(0, 0)]), name="o")
    assert [(p.name, p.kind) for p in sc.parts] == [("only", "atomic"), ("o_geom1", "orphan")]
    assert sc.tris == 24


def test_vehicle_preview_rules(m):
    sc = build_scene(m.car_dff(), name="infernus", sec="cars")
    assert [p.name for p in sc.parts] == ["chassis", "chassis_vlo", "bonnet_ok", "bonnet_dam", "wheel"]
    hidden = {p.name for p in sc.preview_parts if p.hidden}
    assert hidden == {"chassis_vlo", "bonnet_dam"}
    copies = [p for p in sc.preview_parts if p.kind == "copy"]
    assert sorted(p.name for p in copies) == ["wheel@wheel_lb_dummy", "wheel@wheel_lf_dummy", "wheel@wheel_rb_dummy"]
    by = {p.name: p for p in copies}
    assert _close(mat_apply(by["wheel@wheel_lf_dummy"].matrix, 0, 0, 0), (-1.1, 1.3, 0.35), 1e-5)
    # left wheels are turned around (rim outwards), the right one keeps the template orientation
    assert _close(mat_apply(by["wheel@wheel_lf_dummy"].matrix, 1, 0, 0), (-2.1, 1.3, 0.35), 1e-5)
    assert _close(mat_apply(by["wheel@wheel_rb_dummy"].matrix, 1, 0, 0), (2.1, -1.3, 0.35), 1e-5)
    # the same frames without sec='cars' are still recognised as a vehicle (frame layout)
    assert any(p.kind == "copy" for p in build_scene(m.car_dff(), name="x").preview_parts)


def test_ped_is_turned_upright(m):
    pos, tris, uv, _n = m.box((0.5, 1.8, 0.3), (0.0, 0.9, 0.0))       # "standing" along +Y like the bind pose
    g = m.geometry(pos, tris, uv=uv, skin=True)
    sc = build_scene(m.clump([g], [(-1, None, (0, 0, 0)), (0, "Root", (0, 0, 0)), (1, " Pelvis", (0, 0, 0))],
                             [(2, 0)]), name="ped", sec="peds")
    assert sc.root != IDENTITY and sc.notes
    top = mat_apply(sc.root, 0.0, 1.8, 0.0)
    assert _close(top, (0.0, 0.0, 1.8))                                 # +Y (up in the DFF) -> +Z
    assert _close(mat_apply(sc.root, 0.0, 0.0, 1.0), (0.0, 1.0, 0.0))   # DFF front (+Z) -> +Y (like vehicles)


def test_malformed_dff_raises_formaterror(m):
    good = m.box_dff()
    with pytest.raises(FormatError):
        build_scene(good[:40])
    with pytest.raises(FormatError):
        build_scene(b"\x01\x02\x03" * 10)


def test_smooth_normals_unit_and_outward(m):
    from satk.formats.dff import decode_geometries

    mesh = decode_geometries(m.box_dff(normals=False))[0]
    assert mesh.normals is None
    n = smooth_normals(mesh)
    assert len(n) == len(mesh.positions)
    for i in range(0, len(n), 3):
        assert abs(math.sqrt(n[i] ** 2 + n[i + 1] ** 2 + n[i + 2] ** 2) - 1.0) < 1e-5


# ----------------------------------------------------------------------------- textures
def test_txd_chain_first_wins_case_insensitive(m):
    a = m.solid_txd({"Shared": (255, 0, 0, 255), "onlya": (1, 2, 3, 255)})
    b = m.solid_txd({"shared": (0, 255, 0, 255), "onlyb": (4, 5, 6, 255)})
    ch = TX.TxdChain([("a", a), ("b", b)])
    assert len(ch) == 3
    assert ch.find("SHARED").txd == "a" and ch.find("onlyb").txd == "b"
    assert ch.find(None) is None and ch.find("nope") is None
    d = TX.decode(ch.find("shared"))
    assert (d.w, d.h, d.rgba[:4], d.alpha) == (4, 4, bytes((255, 0, 0, 255)), False)


def test_txd_chain_skips_broken_txd(m):
    ch = TX.TxdChain([("bad", b"\x16\x00\x00\x00garbage"), ("ok", m.solid_txd({"t": (1, 1, 1, 255)}))])
    assert ch.find("t") is not None and ch.errors and ch.errors[0].startswith("bad:")


def test_decode_cache_hits(m):
    TX.cache_clear()
    h = TX.TxdChain([("a", m.solid_txd({"t": (9, 9, 9, 255)}))]).find("t")
    TX.decode(h)
    TX.decode(h)
    st = TX.cache_stats()
    assert st["misses"] == 1 and st["hits"] == 1 and st["entries"] == 1


def test_resolve_materials_colours_lights_missing(m):
    from satk.formats.dff import Material

    mats = [[Material(0, 0, 0x3CFF00FF, "paint", None, 0, 1), Material(0, 1, 0xFFAF00FF, None, None, 0, None),
             Material(0, 2, 0xFFFFFFFF, "lost", None, 0, None), Material(0, 3, 0x80808080, None, None, 0, None)]]
    ch = TX.TxdChain([("v", m.solid_txd({"paint": (1, 1, 1, 255)}))])
    out = TX.resolve_materials(mats, ch, {1: (10, 20, 30)})
    paint, light, lost, plain = out[0]
    assert paint.rgba == (10, 20, 30, 255) and paint.slot == 1 and paint.tex is not None
    assert light.rgba[:3] == (255, 175, 0)          # a lamp key off vehiclelights128 stays (engine rule)
    assert lost.missing and lost.tex is None and lost.tex_name == "lost"
    assert plain.rgba == (128, 128, 128, 128) and not plain.missing
    raw = TX.resolve_materials(mats, ch, None)[0]                      # not a vehicle: colours untouched
    assert raw[0].rgba == (60, 255, 0, 255) and raw[0].slot is None


def test_lamp_rule_white_on_vehiclelights128(m):
    from satk.formats.dff import Material

    mats = [[Material(0, 0, 0xFFAF00FF, "vehiclelights128", None, 0, None),
             Material(0, 1, 0xB9FF00FF, "vehiclelights128", None, 0, None),
             Material(0, 2, 0xFF3C00FF, "generic", None, 0, None)]]
    ch = TX.TxdChain([("v", m.solid_txd({"vehiclelights128": (9, 9, 9, 255), "generic": (9, 9, 9, 255)}))])
    head, tail, other = TX.resolve_materials(mats, ch, {1: (10, 20, 30)})[0]
    assert head.rgba[:3] == (255, 255, 255) and tail.rgba[:3] == (255, 255, 255)   # every lamp material white
    assert other.rgba[:3] == (255, 60, 0)                                          # key colour elsewhere kept
    assert TX.resolve_materials(mats, ch, None)[0][0].rgba[:3] == (255, 175, 0)   # not a vehicle: untouched


def test_parse_carcols_and_vehicle_colours(tmp_path):
    text = ("# comment\ncol\n0,0,0\t# black\n245,245,245 # white\n132,4,16\nend\ncar\n"
            "infernus, 2,1, 0,0\nbad\nend\ncar4\ncamper, 1,2,0,1, 3,3,3,3\nend\n")
    pal, cars = TX.parse_carcols(text)
    assert pal == [(0, 0, 0), (245, 245, 245), (132, 4, 16)]
    assert cars == {"infernus": [2, 1], "camper": [1, 2, 0, 1]}
    root = tmp_path / "game"
    (root / "data").mkdir(parents=True)
    (root / "data" / "carcols.dat").write_text(text, encoding="latin-1")
    c = TX.vehicle_colours("INFERNUS", root)
    assert c[1] == (132, 4, 16) and c[2] == (245, 245, 245) and c[3] == TX.DEFAULT_CAR_COLOURS[3]
    assert TX.vehicle_colours("camper", root)[4] == (245, 245, 245)
    assert TX.vehicle_colours("unknown", root) == TX.DEFAULT_CAR_COLOURS
    assert TX.vehicle_colours("infernus", None) == TX.DEFAULT_CAR_COLOURS
