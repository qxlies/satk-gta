"""satk.formats.dff on synthetic models (SPEC §4.2 pitfalls #3, #4, #8; milestone F2)."""

from __future__ import annotations

import random
import struct
from array import array

import pytest

from satk.formats.col import iter_col
from satk.formats.dff import (FLAG_NAMES, FX_NAMES, count_strip_triangles, decode_geometries, decode_geometry,
                              find_embedded_col, scan_dff, strip_to_triangles)
from satk.formats.rw import FormatError

FLAG = {v: k for k, v in FLAG_NAMES.items()}
FX = {v: k for k, v in FX_NAMES.items()}


def naive_strip(ix):
    """Reference strip expansion (report-14 prototype): alternate winding, drop degenerates."""
    out = []
    for i in range(len(ix) - 2):
        a, b, c = ix[i], ix[i + 1], ix[i + 2]
        if a == b or b == c or a == c:
            continue
        out += [a, b, c] if i % 2 == 0 else [b, a, c]
    return out


# ----------------------------------------------------------------------------- strips
def test_strip_expansion_matches_reference_on_random_strips():
    rng = random.Random(7)
    for _ in range(300):
        n = rng.randrange(0, 40)
        ix = [rng.randrange(0, 6) for _ in range(n)]
        want = naive_strip(ix)
        assert list(strip_to_triangles(ix)) == want
        assert count_strip_triangles(array("I", ix)) == len(want) // 3


def test_strip_joint_and_winding():
    # two quads joined by a degenerate pair (3,3,4,4): 2 + 2 triangles, 4 degenerate windows dropped
    ix = [0, 1, 2, 3, 3, 4, 4, 5, 6, 7]
    t = list(strip_to_triangles(ix))
    assert t == naive_strip(ix)
    assert t[:6] == [0, 1, 2, 2, 1, 3]                    # odd triangle swaps its first two vertices
    assert len(t) // 3 == count_strip_triangles(array("I", ix)) == 4


def test_strip_values_beyond_one_byte():
    """The big-int equality masks compare whole 4-byte indices (equal low bytes are not equal indices)."""
    ix = [0x10000, 0x20000, 0x10000 | 1, 0x30000, 0x30000, 0x7FFFFFFF, 0x7FFFFF00, 0xFFFFFFFF, 0x7FFFFF00]
    assert list(strip_to_triangles(ix)) == naive_strip(ix)
    assert count_strip_triangles(ix) == len(naive_strip(ix)) // 3


def test_geometry_with_many_strips_matches_per_strip_reference(b):
    """All strips of a geometry are expanded in one pass (odd starts padded): same triangles and material
    ids as expanding every strip on its own, also for strips of 0..2 indices."""
    rng = random.Random(11)
    for _ in range(40):
        nv = rng.randrange(1, 9)
        pos = [(float(i), 0.0, 0.0) for i in range(nv)]
        strips = [(rng.randrange(0, 3), [rng.randrange(0, nv) for _ in range(rng.randrange(0, 14))])
                  for _ in range(rng.randrange(1, 6))]
        g = b.geometry(pos, strips=strips, mats=[b.material()] * 3)
        blob = b.clump([g])
        (m,) = decode_geometries(blob)
        want_t, want_m = [], []
        for mat, ix in strips:
            t = naive_strip(ix)
            want_t += t
            want_m += [mat] * (len(t) // 3)
        assert list(m.tris) == want_t and list(m.mat_ids) == want_m, strips
        assert scan_dff(blob).tris == len(want_t) // 3


# ----------------------------------------------------------------------------- scan
def test_scan_quad(b):
    blob = b.quad_dff(night=True)
    i = scan_dff(blob)
    assert (i.rw_version, i.clumps, i.atomics, i.verts, i.tris) == (0x36003, 1, 1, 4, 2)
    assert [(f.idx, f.parent, f.name, f.atomic) for f in i.frames] == [(0, -1, "root", True)]
    (g,) = i.geoms
    assert (g.idx, g.verts, g.tris, g.uv_sets, g.strip, g.frame) == (0, 4, 2, 1, True, 0)
    assert g.rw_flags & 0xFFFF == 0x02 | 0x04 | 0x08 | 0x20 and (g.rw_flags >> 16) & 0xFF == 1
    assert blob[g.geom_off:g.geom_off + 4] == b"\x0f\0\0\0"
    (m,) = i.materials
    assert (m.geom, m.idx, m.rgba, m.texture, m.mask, m.fx, m.color_slot) == (0, 0, 0xFF0000FF, "brick", "brickm", 0, None)
    assert i.flags == FLAG["prelit"] | FLAG["night"]
    assert {0x50E, 0x253F2F9, 0x253F2FE, 0x253F2FD} <= i.plugins
    assert i.bbox == (0.0, 0.0, 0.0, 1.0, 1.0, 2.0)
    assert i.bsphere == (0.0, 0.0, 0.0, 1.0)            # single geometry: its own sphere


def test_light_struct_is_not_an_atomic(b):
    """Regression: the report-14 prototype added each Light's Struct (a frame index) to the atomic count."""
    i = scan_dff(b.clump([b.geometry([(0, 0, 0)] * 3, lists=[(0, [0, 1, 2])])], lights=[5, 9]))
    assert i.atomics == 1 and i.clumps == 1


def test_multi_clump_global_indices(b):
    """player.img entries hold 3 clumps: all are read, frame/geometry indices are global."""
    g = b.geometry([(0, 0, 0), (1, 0, 0), (0, 1, 0)], lists=[(0, [0, 1, 2])], mats=[b.material(tex="skin")])
    one = b.clump([g, g], frames=[(-1, "root", (0, 0, 0)), (0, "child", (0, 0, 0))], atomics=[(1, 0), (0, 1)])
    blob = one * 3 + b"\0" * 300                            # sector padding after the RW data
    i = scan_dff(blob)
    assert (i.clumps, i.atomics, len(i.frames), len(i.geoms), len(i.materials)) == (3, 6, 6, 6, 6)
    assert [f.parent for f in i.frames] == [-1, 0, -1, 2, -1, 4]
    assert [g.frame for g in i.geoms] == [1, 0, 3, 2, 5, 4]
    assert [m.geom for m in i.materials] == [0, 1, 2, 3, 4, 5]
    assert i.flags & FLAG["multi_clump"]
    meshes = decode_geometries(blob)
    assert [m.frame for m in meshes] == [1, 0, 3, 2, 5, 4]
    assert decode_geometry(blob, i.geoms[3].geom_off).frame == 2


def test_uvanim_dict_before_clump_and_old_rw_version(b):
    """UVAnimDict 0x2B precedes the clump; geometry of RW < 0x34000 has 12 extra struct bytes."""
    old = 0x0C02FFFF                                       # RW 0x33002
    pos = [(0, 0, 0), (2, 0, 0), (0, 3, 0)]
    g = b.geometry(pos, lists=[(0, [0, 1, 2])], libid=old, prelit=True, uv_sets=1)
    blob = b.chunk(0x2B, b.chunk(0x01, b"\0" * 4)) + b.clump([g], libid=old)
    i = scan_dff(blob)
    assert i.rw_version == 0x33002 and i.flags & FLAG["uvanim"]
    m = decode_geometry(blob, i.geoms[0].geom_off)
    assert list(m.positions) == [0, 0, 0, 2, 0, 0, 0, 3, 0]
    assert list(m.prelit[:4]) == [0, 10, 20, 255] and list(m.uv[0][:4]) == [0.0, -0.0, 0.5, -0.25]


def test_materials_fx_color_slots_and_refs(b):
    matfx_env = b.chunk(0x120, struct.pack("<II", 2, 2) + b"\0" * 12)
    mats = [b.material((60, 255, 0, 255), "body", ext=matfx_env + b.chunk(0x253F2FC, b"\0" * 24)),
            b.material((255, 0, 175, 255), "body2", ext=b.chunk(0x253F2F6, b"\0" * 28)),
            b.material((0, 0, 0, 255), "", ext=b.chunk(0x135, b"\0" * 8))]
    g = b.geometry([(0, 0, 0)] * 3, lists=[(0, [0, 1, 2])], mats=mats, mat_refs=[-1, -1, -1, 0])
    i = scan_dff(b.clump([g]))
    assert [m.idx for m in i.materials] == [0, 1, 2, 3]
    assert [m.color_slot for m in i.materials] == [1, 2, None, 1]
    assert i.materials[0].fx == FX["env"] | FX["reflection"]
    assert i.materials[1].fx == FX["specular"] and i.materials[2].fx == FX["uvanim"]
    assert i.materials[2].texture is None                  # empty texture name -> None
    assert i.materials[3] == i.materials[0].__class__(0, 3, *[getattr(i.materials[0], k) for k in
                                                              ("rgba", "texture", "mask", "fx", "color_slot")])
    for f in ("matfx", "reflection", "specular", "uvanim"):
        assert i.flags & FLAG[f], f


def test_effects_skin_hanim_breakable_embedded_col(b):
    col = b.col_v23("car_col", 3, faces=[(0, 1, 2, 63)], verts=[(0, 0, 0), (128, 0, 0), (0, 128, 0)])
    fx = b.fx_entries(b.fx_light(), struct.pack("<3fII", 0, 0, 0, 1, 24) + b"smoke".ljust(24, b"\0"),
                      struct.pack("<3fII", 0, 0, 0, 99, 3) + b"abc")
    g = b.geometry([(0, 0, 0)] * 3, lists=[(0, [0, 1, 2])], fx2d=fx, skin=True, breakable=1, normals=True)
    blob = b.clump([g], col=col, hanim=True)
    i = scan_dff(blob)
    for f in ("2dfx", "skin", "hanim", "breakable", "embedded_col", "normals"):
        assert i.flags & FLAG[f], f
    light, part, unk = i.effects
    assert (light.type_name, light.pos) == ("light", (1.0, 2.0, 3.0))
    assert light.data["rgba"] == 0xFF8000C8 and light.data["corona"] == "coronastar" and light.data["range"] == 18.0
    assert light.data["flags"] == 0x41 | (1 << 8)
    assert (part.type_name, part.data) == ("particle", {"name": "smoke"})
    assert (unk.type, unk.type_name, unk.data) == (99, "type99", {"size": 3})
    off, size = find_embedded_col(blob)
    (cm,) = iter_col(blob[off:off + size])
    assert (cm.name, cm.version, cm.faces, cm.verts, cm.surfaces) == ("car_col", 3, 1, 3, {63: 1})
    assert find_embedded_col(b.quad_dff()) is None


def test_bbox_uses_child_frame_transforms(b):
    """Root frame = identity (the entity matrix replaces it); child frames are applied."""
    g = b.geometry([(0, 0, 0), (1, 1, 1), (0, 1, 0)], lists=[(0, [0, 1, 2])])
    blob = b.clump([g, g], frames=[(-1, "root", (100, 100, 100)), (0, "wheel", (5, 0, 0))],
                   atomics=[(0, 0), (1, 1)])
    i = scan_dff(blob)
    assert i.bbox == (0.0, 0.0, 0.0, 6.0, 1.0, 1.0)
    cx, cy, cz, r = i.bsphere
    assert (cx, cy, cz) == (3.0, 0.5, 0.5) and r == pytest.approx((3 ** 2 + 0.5 ** 2 + 0.5 ** 2) ** 0.5)


# ----------------------------------------------------------------------------- decode
def test_decode_strip_arrays(b):
    blob = b.quad_dff(night=True, normals=True)
    (m,) = decode_geometries(blob)
    assert list(m.tris) == [0, 1, 2, 2, 1, 3] and list(m.mat_ids) == [0, 0]
    assert list(m.positions) == [0, 0, 0, 1, 0, 0, 0, 1, 0, 1, 1, 2]
    assert list(m.normals) == [0, 0, 1] * 4
    assert len(m.uv) == 1 and list(m.uv[0]) == [0.0, -0.0, 0.5, -0.25, 1.0, -0.5, 1.5, -0.75]
    assert list(m.prelit) == [0, 10, 20, 255, 1, 10, 20, 255, 2, 10, 20, 255, 3, 10, 20, 255]
    assert list(m.night[:8]) == [1, 2, 3, 0, 1, 2, 3, 1]
    assert m.frame == 0
    assert m.positions.typecode == "f" and m.tris.typecode == "I" and m.mat_ids.typecode == "H"


def test_decode_list_meshes_two_uv_sets_and_struct_fallback(b):
    pos = [(0, 0, 0), (1, 0, 0), (0, 1, 0), (1, 1, 0)]
    g = b.geometry(pos, lists=[(1, [0, 1, 2]), (0, [2, 1, 3, 3, 3])], uv_sets=2, prelit=False,
                   mats=[b.material(), b.material()])
    (m,) = decode_geometries(b.clump([g]))
    assert list(m.tris) == [0, 1, 2, 2, 1, 3] and list(m.mat_ids) == [1, 0]   # lists keep their triangles
    assert len(m.uv) == 2 and m.prelit is None and m.night is None and m.normals is None
    nobin = b.geometry(pos, tris=[(0, 1, 2, 1), (3, 2, 1, 0)], mats=[b.material(), b.material()])
    blob = b.clump([nobin])
    (m2,) = decode_geometries(blob)
    assert list(m2.tris) == [0, 1, 2, 3, 2, 1] and list(m2.mat_ids) == [1, 0]
    assert scan_dff(blob).geoms[0].tris == 2


def test_decode_rejects_bad_indices_and_offsets(b):
    g = b.geometry([(0, 0, 0)] * 3, strips=[(0, [0, 1, 7])])
    blob = b.clump([g])
    with pytest.raises(FormatError, match="vertex count"):
        decode_geometries(blob)
    with pytest.raises(FormatError, match="no Geometry"):
        decode_geometry(b.quad_dff(), 12)


@pytest.mark.parametrize("blob,match", [
    (b"", "no Clump"),
    (b"\x10\0\0\0\xff\0\0\0\xff\xff\x03\x18", "overruns"),
    (b"\x16\0\0\0\0\0\0\0\xff\xff\x03\x18", "no Clump"),
])
def test_scan_errors(blob, match):
    with pytest.raises(FormatError, match=match):
        scan_dff(blob)


def test_atomic_references_checked(b):
    g = b.geometry([(0, 0, 0)] * 3, lists=[(0, [0, 1, 2])])
    with pytest.raises(FormatError, match="atomic references"):
        scan_dff(b.clump([g], atomics=[(0, 3)]))
    with pytest.raises(FormatError, match="parent"):
        scan_dff(b.clump([g], frames=[(5, "x", (0, 0, 0))]))
