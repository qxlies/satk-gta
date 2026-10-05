"""TXD writer (satk.texmod.txdwrite): SA PC D3D9 layout, parsed back by satk.formats.txd (M2-03)."""

from __future__ import annotations

import struct

import pytest

from satk.formats.rw import iter_children, read_chunk
from satk.formats.txd import parse_txd, texture_hash
from satk.texmod.txdwrite import (LIBID_SA, NativeSpec, check_name, mip_filter, native_chunk, rewrite_txd,
                                  txd_chunk)


def _raw_struct(native: bytes) -> tuple:
    """(platform, filter, addr, raster, d3dfmt, w, h, depth, levels, rtype, flags, level sizes) of a native."""
    top = read_chunk(native, 0)
    st = next(iter_children(native, top.data_off, top.end))
    o = st.data_off
    plat, filt, addr = struct.unpack_from("<IBB", native, o)
    raster, d3d, w, h, depth, levels, rtype, flags = struct.unpack_from("<IIHHBBBB", native, o + 72)
    p, sizes = o + 88, []
    for _ in range(levels):
        (sz,) = struct.unpack_from("<I", native, p)
        sizes.append(sz)
        p += 4 + sz
    assert p == st.end
    return plat, filt, addr, raster, d3d, w, h, depth, levels, rtype, flags, sizes


@pytest.mark.parametrize("fmt,alpha,raster,flags,depth", [
    ("DXT1", False, 0x200, 8, 16), ("DXT1", True, 0x100, 9, 16), ("DXT3", True, 0x300, 9, 16),
    ("DXT5", True, 0x300, 9, 16), ("A8R8G8B8", True, 0x500, 1, 32), ("X8R8G8B8", False, 0x600, 0, 32),
    ("R5G6B5", False, 0x200, 0, 16), ("A1R5G5B5", True, 0x100, 1, 16), ("A4R4G4B4", True, 0x300, 1, 16)])
def test_native_layout_matches_vanilla_conventions(tm, fmt, alpha, raster, flags, depth):
    img = tm.image("binary_alpha" if alpha else "smooth", 16, 8)
    one = tm.native("tex_a", img, fmt, mips=1, alpha=alpha)
    plat, filt, addr, r, d3d, w, h, dep, levels, rtype, fl, sizes = _raw_struct(one)
    assert (plat, filt, addr, r, w, h, dep, levels, rtype, fl) == (9, 6, 0x11, raster, 16, 8, depth, 1, 4, flags)
    if fmt.startswith("DXT"):
        assert struct.pack("<I", d3d) == fmt.encode()
    many = tm.native("tex_a", img, fmt, mips=5, alpha=alpha)
    *_, levels, _rt, _fl, sizes = _raw_struct(many)
    assert levels == 5 and _raw_struct(many)[3] == raster | 0x8000
    if fmt == "DXT1":
        assert sizes == [64, 16, 8, 8, 8]                            # 16x8, 8x4, 4x2, 2x1, 1x1: one block minimum
    assert read_chunk(one, 0).libid == LIBID_SA


def test_txd_parses_back(tm):
    data = tm.sample_txd()
    t = parse_txd(data)
    assert (t.count, t.device_id, t.rw_version, len(t.textures)) == (3, 2, 0x36003, 3)
    rows = [(x.name, x.d3dfmt, x.w, x.h, x.levels, x.alpha) for x in t.textures]
    assert rows == [("wall", "X8R8G8B8", 64, 64, 1, False), ("Floor_Tiles", "DXT1", 32, 32, 6, False),
                    ("glass", "A8R8G8B8", 16, 16, 1, True)]
    top = read_chunk(data, 0)
    kids = [c.type for c in iter_children(data, top.data_off, top.end)]
    assert kids == [0x01, 0x15, 0x15, 0x15, 0x03] and top.end == len(data)


def test_names_are_checked():
    assert check_name("a" * 31).endswith(b"\0")
    for bad in ("a" * 32, "текстура", "tab\there"):
        with pytest.raises(ValueError):
            check_name(bad)
    with pytest.raises(ValueError):
        native_chunk(NativeSpec("x", "PAL8", 4, 4, (b"\0" * 16,)))
    with pytest.raises(ValueError):
        native_chunk(NativeSpec("x", "DXT1", 4, 4, ()))


def test_mip_filter():
    assert mip_filter(2, 5) == 6 and mip_filter(1, 5) == 3 and mip_filter(6, 5) == 6
    assert mip_filter(2, 1) == 2


def test_rewrite_keeps_other_textures_byte_for_byte(tm):
    data = tm.sample_txd()
    old = parse_txd(data)
    new_native = tm.native("Floor_Tiles", tm.image("shapes", 64, 64), "DXT5", mips=3)
    added = tm.native("extra", tm.image("smooth", 8, 8), "DXT1")
    out = rewrite_txd(data, {1: new_native}, [added])
    t = parse_txd(out)
    assert t.count == 4 and [x.name for x in t.textures] == ["wall", "Floor_Tiles", "glass", "extra"]
    assert (t.textures[1].d3dfmt, t.textures[1].w, t.textures[1].levels) == ("DXT5", 64, 3)
    for i in (0, 2):
        assert texture_hash(out, t.textures[i]) == texture_hash(data, old.textures[i])
    assert rewrite_txd(data, {}) == data                              # nothing to do -> identical bytes
    with pytest.raises(ValueError):
        rewrite_txd(data, {7: new_native})


def test_rewrite_keeps_unknown_chunks_and_libid(tm):
    data = bytearray(tm.sample_txd())
    struct.pack_into("<I", data, 8, 0x1C020065)                       # another RW stamp on the dictionary
    extra = struct.pack("<III", 0x99, 4, LIBID_SA) + b"abcd"
    body = bytes(data[12:]) + extra
    data = struct.pack("<III", 0x16, len(body), 0x1C020065) + body
    out = rewrite_txd(data, {0: tm.native("wall", tm.image("smooth", 8, 8), "DXT1")})
    assert read_chunk(out, 0).libid == 0x1C020065 and out.endswith(extra)


def test_txd_chunk_device_and_empty():
    t = parse_txd(txd_chunk([], device_id=1))
    assert (t.count, t.device_id, t.textures) == (0, 1, [])
