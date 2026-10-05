"""satk.rw.chunk and satk.rw.codecs on synthetic data (no game files)."""

from __future__ import annotations

import struct

import pytest

from satk.rw import codecs as C
from satk.rw.chunk import (CLUMP, EXTENSION, GAME_VERSIONS, MATERIAL, STRING, STRUCT, pack_libid, parse, parse_chunk,
                           rw_version)


@pytest.mark.parametrize("version,libid", [(0x36003, 0x1803FFFF), (0x33002, 0x0C02FFFF), (0x31001, 0x0401FFFF),
                                           (0x34003, 0x1003FFFF), (0x31000, 0x310), (0x30800, 0x308)])
def test_pack_libid_matches_librw(version, libid):
    assert pack_libid(version) == libid
    assert rw_version(libid) == version


def test_game_versions():
    assert {k: pack_libid(v) for k, v in GAME_VERSIONS.items()} == {"iii": 0x0401FFFF, "vc": 0x0C02FFFF,
                                                                   "sa": 0x1803FFFF}


def test_tree_round_trip_is_identity_and_keeps_padding(make_dff):
    blob = make_dff(matfx="envtex", specular="spec", skin=True, instanced=True)
    padded = blob + b"\0" * 300                      # IMG sector padding
    s = parse(padded)
    assert s.to_bytes() == padded
    assert s.to_bytes(with_tail=False) == blob
    assert s.tail == b"\0" * 300
    assert [c.type for c in s.chunks] == [CLUMP]


def test_sizes_are_recomputed_after_an_edit(make_dff):
    blob = make_dff(tex="wall")
    s = parse(blob)
    strings = [n for n, _p in s.walk() if n.type == STRING]
    strings[0].data = C.make_string("a_much_longer_texture_name")
    out = s.to_bytes()
    assert len(out) == len(blob) + len(strings[0].data) - 8
    again = parse(out)                                # sizes on the path to the root are consistent
    assert again.to_bytes() == out
    from satk.formats.dff import scan_dff
    assert {m.texture for m in scan_dff(out).materials} == {"a_much_longer_texture_name"}


def test_container_that_does_not_split_stays_opaque():
    bad_ext = struct.pack("<III", EXTENSION, 20, 0x1803FFFF) + struct.pack("<III", 0x50E, 99, 0x1803FFFF) + b"x" * 8
    blob = struct.pack("<III", CLUMP, len(bad_ext), 0x1803FFFF) + bad_ext
    s = parse(blob)
    ext = s.chunks[0].kids[0]
    assert ext.opaque and ext.data is not None
    assert s.to_bytes() == blob


def test_container_tail_bytes_are_kept():
    inner = struct.pack("<III", STRUCT, 4, 0x1803FFFF) + b"abcd" + b"\x01\x02"      # 2 stray bytes
    blob = struct.pack("<III", MATERIAL, len(inner), 0x1803FFFF) + inner
    c = parse_chunk(blob)
    assert c.kids is not None and c.tail == b"\x01\x02"
    assert c.to_bytes() == blob


def test_parse_rejects_non_rw():
    with pytest.raises(ValueError):
        parse(b"COL3" + b"\0" * 40)


@pytest.mark.parametrize("version", [0x36003, 0x33002, 0x31001])
def test_geometry_codec_round_trip_both_layouts(version, make_dff):
    s = parse(make_dff(version=version, normals=True))
    st = next(n for n, p in s.walk() if n.type == STRUCT and p and p[-1] == 0x0F)
    g = C.decode_geometry(st.data, version)
    assert (g.surf is not None) == (version < 0x34000)
    assert g.num_verts == 4 and g.num_tris == 2 and g.num_uv == 1 and g.morphs[0].has_normals
    assert C.encode_geometry(g, version) == st.data
    other = 0x33002 if version >= 0x34000 else 0x36003
    assert len(C.encode_geometry(g, other)) == len(st.data) + (12 if other < 0x34000 else -12)


def test_material_clump_skin_night_matfx_specular_codecs(make_dff):
    blob = make_dff(matfx="envtex", specular="spec", skin=True)
    s = parse(blob)
    seen = set()
    for n, p in s.walk():
        if n.data is None:
            continue
        if n.type == STRUCT and p[-1] == MATERIAL:
            m = C.decode_material(n.data, 0x36003)
            assert m.unused == 0x1C2B24 and m.color == b"\xff" * 4
            assert C.encode_material(m, 0x36003) == n.data
            seen.add("material")
        elif n.type == STRUCT and p[-1] == CLUMP:
            assert C.encode_clump(C.decode_clump(n.data, 0x36003), 0x36003) == n.data
            seen.add("clump")
        elif n.type == 0x116:
            sk = C.decode_skin(n.data, 4)
            assert not sk.old_format and sk.used == bytes((2, 0))
            assert C.encode_skin(sk) == n.data
            old = C.skin_to_old(sk)
            enc = C.encode_skin(old)
            assert enc[1] == 0 and enc.count(b"\xad\xde\xad\xde") == 3          # 0xDEADDEAD before each matrix
            back = C.skin_to_new(C.decode_skin(enc, 4), 4)
            assert back.used == bytes((0, 2)) and back.max_weights == 2         # librw: sorted used bones
            seen.add("skin")
        elif n.type == 0x253F2F9:
            nd = C.decode_night(n.data, 4)
            assert nd.magic == 0x2E5EFF70 and C.encode_night(nd) == n.data
            seen.add("night")
        elif n.type == 0x120:
            fx = C.decode_matfx(n.data)
            assert [t.type for t in fx.textures()] == [0x06]
            assert C.encode_matfx(fx) == n.data
            seen.add("matfx")
        elif n.type == 0x253F2F6:
            sp = C.decode_specular(n.data)
            assert sp.texture == "spec" and C.encode_specular(sp) == n.data
            seen.add("specular")
    assert seen == {"material", "clump", "skin", "night", "matfx", "specular"}


def test_short_clump_struct_is_kept():
    c = C.decode_clump(struct.pack("<i", 3), 0x36003)
    assert c.short and C.encode_clump(c, 0x36003) == struct.pack("<i", 3)
    assert C.encode_clump(C.ClumpData(3, 0, 0), 0x36003) == struct.pack("<iii", 3, 0, 0)
    assert C.encode_clump(C.ClumpData(3, 0, 0), 0x31001) == struct.pack("<i", 3)


def test_strings_keep_garbage_and_make_string_is_canonical():
    d = C.decode_string(b"wall\0\xcd\xcd\xcd")
    assert d.text == "wall" and C.encode_string(d) == b"wall\0\xcd\xcd\xcd"
    assert C.make_string("abc") == b"abc\0" and C.make_string("abcd") == b"abcd\0\0\0\0"


def test_truncated_payloads_raise_codec_errors():
    with pytest.raises(C.CodecError):
        C.decode_geometry(b"\0" * 8, 0x36003)
    with pytest.raises(C.CodecError):
        C.decode_skin(bytes((2, 1, 1, 0)), 10)
    with pytest.raises(C.CodecError):
        C.decode_matfx(struct.pack("<II", 2, 9))
