"""DffDoc patches on synthetic DFFs; results are re-read by satk.formats (WP-02) and by the vendored rwfury."""

from __future__ import annotations

import struct
from array import array

import pytest

from satk.formats.dff import FLAG_NAMES, decode_geometry, scan_dff
from satk.rw import codecs as C
from satk.rw.chunk import GAME_VERSIONS, STRING, Chunk, parse
from satk.rw.dff import DffDoc, PatchError, first_difference, typed_roundtrip
from satk.rw.vendor import load

SA, VC, III = GAME_VERSIONS["sa"], GAME_VERSIONS["vc"], GAME_VERSIONS["iii"]


def _flags(blob: bytes) -> set[str]:
    info = scan_dff(blob)
    return {n for b, n in FLAG_NAMES.items() if info.flags & b}


def test_parse_and_write_back_unchanged(make_dff):
    blob = make_dff(matfx="envtex", specular="spec", skin=True, instanced=True)
    doc = DffDoc.parse(blob)
    assert doc.to_bytes() == blob
    rebuilt, problems = typed_roundtrip(DffDoc.parse(blob))
    assert problems == [] and rebuilt.to_bytes() == blob
    assert [m.slot for m in doc.materials()] == [0, 1]                 # instanced slot -> same Material chunk
    assert doc.materials()[0].node is doc.materials()[1].node
    assert doc.texture_names() == ["wall"]


def test_rename_textures_everywhere_case_insensitive(make_dff):
    blob = make_dff(tex="Wall", mask="wallA", matfx="envtex", specular="spec")
    doc = DffDoc.parse(blob)
    ch = dict(doc.rename_textures({"WALL": "brick", "walla": "brickA", "EnvTex": "chrome", "spec": "shine"}))
    assert ch == {"texture": 1, "mask": 1, "matfx": 1, "specular": 1}
    out = doc.to_bytes()
    info = scan_dff(out)
    assert {(m.texture, m.mask) for m in info.materials} == {("brick", "brickA")}
    assert b"chrome\0" in out and b"shine\0" in out and b"envtex" not in out
    # everything except the renamed strings (and the sizes on their paths) is byte-identical
    assert first_difference(blob, out).endswith(("String:size", "String:data", "MatEffectsPLG:size",
                                                 "SpecularMat:data"))
    rwf = load()
    if rwf is not None:
        d = rwf.Dff.from_bytes(out)
        m = d.geometries[0].materials[0]
        assert (m.texture_name, m.mask_name, m.specular_texture) == ("brick", "brickA", "shine")
        assert m.mat_fx_data["effect1"]["env_map"]["name"] == "chrome"


def test_rename_keeps_unrelated_garbage_and_is_noop_when_absent(make_dff):
    blob = make_dff(tex="wall")
    doc = DffDoc.parse(blob)
    assert doc.rename_textures({"other": "x"}) == []
    assert doc.to_bytes() == blob                                       # garbage after the NUL untouched
    doc.rename_textures({"wall": "wall"})
    assert doc.to_bytes() == blob


@pytest.mark.parametrize("bad", ["", "a" * 32, "naïve", "dir/x"])
def test_rename_rejects_bad_names(make_dff, bad):
    with pytest.raises(PatchError):
        DffDoc.parse(make_dff()).rename_textures({"wall": bad})


def test_restamp_sa_to_vc_and_back(make_dff):
    blob = make_dff(skin=True, matfx="envtex")
    doc = DffDoc.parse(blob)
    ch = dict(doc.restamp(VC))
    assert ch["geometry_struct"] == 1 and ch["skin"] == 1 and ch["chunks"] > 20
    vc = doc.to_bytes()
    s = parse(vc)
    assert {n.libid for n, _p in s.walk()} == {0x0C02FFFF}
    # the MatFX payload's embedded Texture chunk is re-stamped too
    assert vc.count(struct.pack("<I", 0x1803FFFF)) == 0
    info = scan_dff(vc)
    assert info.rw_version == VC and info.geoms[0].verts == 4
    skin = next(n for n, _p in s.walk() if n.type == 0x116)
    assert skin.data[1] == 0 and skin.data.count(b"\xad\xde\xad\xde") == 3
    back = DffDoc.parse(vc)
    back.restamp(SA)
    sa = back.to_bytes()
    assert len(sa) == len(blob)
    # only the used-bone order differs (the VC skin format has no used-bone list; librw sorts it)
    assert "SkinPLG" in first_difference(blob, sa)
    m = decode_geometry(sa, scan_dff(sa).geoms[0].geom_off)
    assert len(m.positions) == 12


def test_restamp_to_iii_shrinks_the_clump_struct(make_dff):
    doc = DffDoc.parse(make_dff())
    doc.restamp(III)
    out = doc.to_bytes()
    clump_struct = parse(out).chunks[0].kids[0]
    assert len(clump_struct.data) == 4 and clump_struct.libid == 0x0401FFFF
    up = DffDoc.parse(out)
    up.restamp(SA)
    assert len(parse(up.to_bytes()).chunks[0].kids[0].data) == 12   # librw writes lights/cameras from 3.3 on


def test_restamp_keeps_a_short_sa_clump_struct(make_dff):
    blob = make_dff(short_clump=True)
    doc = DffDoc.parse(blob)
    doc.restamp(VC)
    doc2 = DffDoc.parse(doc.to_bytes())
    doc2.restamp(SA)
    assert doc2.to_bytes() == blob


def test_restamp_to_same_version_is_noop(make_dff):
    blob = make_dff(skin=True, matfx="env")
    doc = DffDoc.parse(blob)
    assert doc.restamp(SA) == []
    assert doc.to_bytes() == blob


def test_night_colors_remove_and_add(make_dff):
    doc = DffDoc.parse(make_dff(night=True))
    assert doc.night_colors("remove") == [("night_removed", 1)]
    out = doc.to_bytes()
    assert 0x253F2F9 not in scan_dff(out).plugins
    doc2 = DffDoc.parse(out)
    assert doc2.night_colors("add") == [("night_added", 1)]
    out2 = doc2.to_bytes()
    node = next(n for n, _p in parse(out2).walk() if n.type == 0x253F2F9)
    assert node.data == struct.pack("<I", 1) + bytes(range(16))         # copy of the prelit colours
    ext = [n.type for n in parse(out2).chunks[0].kids[2].kids[1].kids[2].kids]
    assert ext.index(0x253F2F9) == ext.index(0x253F2FD) + 1             # after Breakable, like Rockstar's files
    assert DffDoc.parse(out2).night_colors("add") == []                 # already there


def test_night_colors_add_needs_prelit(make_dff):
    warn: list[str] = []
    doc = DffDoc.parse(make_dff(prelit=False, night=False))
    assert doc.night_colors("add", warn) == []
    assert warn and warn[0].startswith("NO_PRELIT")


def test_recalc_normals_flat_quad(make_dff):
    blob = make_dff(normals=False)
    assert "normals" not in _flags(blob)
    doc = DffDoc.parse(blob)
    assert doc.recalc_normals() == [("normals", 1)]
    out = doc.to_bytes()
    assert "normals" in _flags(out)
    g = doc.geometries()[0].data()
    n = array("f")
    n.frombytes(g.morphs[0].normals)
    assert list(n) == [0.0, 0.0, 1.0] * 4                               # counter-clockwise quad faces +Z
    rwf = load()
    if rwf is not None:
        assert rwf.Dff.from_bytes(out).geometries[0].normals[0] == (0.0, 0.0, 1.0)


def test_recalc_normals_replaces_existing(make_dff):
    doc = DffDoc.parse(make_dff(normals=True))                          # fixture normals point at -Z
    doc.recalc_normals()
    n = array("f")
    n.frombytes(doc.geometries()[0].data().morphs[0].normals)
    assert n[2] == 1.0


def test_material_color_by_row_and_by_geometry_slot(make_dff):
    blob = make_dff(instanced=True)
    doc = DffDoc.parse(blob)
    ch = dict(doc.set_material_color([(None, 0, (255, 0, 0, 255))]))
    assert ch == {"material_color": 1, "modulate_flag": 1}
    out = doc.to_bytes()
    info = scan_dff(out)
    assert [m.rgba for m in info.materials] == [0xFF0000FF, 0xFF0000FF]  # instanced slot shares the material
    assert DffDoc.parse(out).geometries()[0].data().flags & C.GEO_MODULATE
    doc2 = DffDoc.parse(out)
    assert dict(doc2.set_material_color([(0, 1, (255, 0, 0, 255))])) == {}
    with pytest.raises(PatchError):
        doc2.set_material_color([(None, 5, (0, 0, 0, 255))])
    with pytest.raises(PatchError):
        doc2.set_material_color([(3, 0, (0, 0, 0, 255))])


def test_not_a_dff():
    with pytest.raises(PatchError):
        DffDoc.parse(b"COL3" + b"\0" * 60)
    with pytest.raises(PatchError):
        DffDoc.parse(struct.pack("<III", 0x16, 4, 0x1803FFFF) + b"\0\0\0\0")   # a TXD


def test_first_difference_names_the_chunk(make_dff):
    a = make_dff(tex="wall")
    s = parse(a)
    next(n for n, _p in s.walk() if n.type == STRING).data = b"brik\0\xcd\xcd\xcd"
    assert first_difference(a, s.to_bytes()).endswith("Texture/String:data")
    assert first_difference(a, a) is None


def _with_light(blob: bytes) -> bytes:
    """Add one RW light (frame-index Struct + Light) to the clump, like 186 vanilla gta3.img map models."""
    s = parse(blob)
    cl = s.chunks[0]
    lib = cl.libid
    light = Chunk(0x12, lib, kids=[Chunk(0x01, lib, data=struct.pack("<5fI", 1, 1, 1, 1, 0, 0x800001)),
                                   Chunk(0x03, lib, kids=[])])
    at = max(i for i, k in enumerate(cl.kids) if k.type == 0x14) + 1
    cl.kids[at:at] = [Chunk(0x01, lib, data=struct.pack("<i", 0)), light]
    cl.kids[0].data = C.encode_clump(C.ClumpData(1, 1, 0), 0x36003)
    return s.to_bytes()


def test_clump_lights_survive_vc_and_are_dropped_for_iii(make_dff):
    blob = _with_light(make_dff())
    rebuilt, problems = typed_roundtrip(DffDoc.parse(blob))
    assert problems == [] and rebuilt.to_bytes() == blob          # the light's frame Struct is not a clump struct
    vc = DffDoc.parse(blob)
    vc.restamp(VC)
    back = DffDoc.parse(vc.to_bytes())
    back.restamp(SA)
    assert back.to_bytes() == blob
    iii = DffDoc.parse(blob)
    assert dict(iii.restamp(III))["lights_dropped"] == 1
    out = parse(iii.to_bytes())
    assert [k.type for k in out.chunks[0].kids] == [0x01, 0x0E, 0x1A, 0x14, 0x03]
    assert out.chunks[0].kids[0].data == struct.pack("<i", 1)
