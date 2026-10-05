"""satk.formats.txd on synthetic TXDs (pitfalls #4-#7)."""

from __future__ import annotations

import hashlib
import struct

import pytest

from satk.formats.rw import FormatError
from satk.formats.txd import mip0_bytes, palette_bytes, parse_txd, texture_hash


def test_d3d9_formats_and_effective_alpha(b):
    natives = [
        b.native("brick", fmt="DXT1", raster=0x200, alpha=1),            # DXT1 + 565 + alpha flag -> opaque
        b.native("fence", fmt="DXT1", raster=0x100, alpha=1, mask="fencem"),  # DXT1 + 1555 -> 1-bit alpha
        b.native("glass", fmt="DXT3", raster=0x300, alpha=1, levels=[b"\0" * 16]),
        b.native("vent_64", fmt=22, raster=0x600, w=1, h=1, levels=[b"\1\2\3\4"], flags=0),
        b.native("argb", fmt=21, raster=0x500, w=1, h=1, levels=[b"\1\2\3\4"], flags=1),
        b.native("mips", fmt="DXT1", raster=0x200, w=8, h=8, levels=[b"a" * 32, b"b" * 8, b"c" * 8, b"d" * 8]),
    ]
    t = parse_txd(b.txd(natives, device=6))  # deviceId 6 (PS2) is ignored: the texture platform decides
    assert t.count == 6 and t.device_id == 6 and t.rw_version == 0x36003
    got = [(x.name, x.d3dfmt, x.alpha, x.platform, x.unsupported) for x in t.textures]
    assert got == [("brick", "DXT1", False, 9, False), ("fence", "DXT1", True, 9, False),
                   ("glass", "DXT3", True, 9, False), ("vent_64", "X8R8G8B8", False, 9, False),
                   ("argb", "A8R8G8B8", True, 9, False), ("mips", "DXT1", False, 9, False)]
    fence = t.textures[1]
    assert fence.mask == "fencem" and fence.filter == 6 and (fence.uaddr, fence.vaddr) == (1, 1)
    mips = t.textures[5]
    assert mips.levels == 4 and mips.mip0_size == 32 and (mips.w, mips.h) == (8, 8)
    assert [x.idx for x in t.textures] == list(range(6))


def test_mip0_offsets_and_hash(b):
    data = b.txd([b.native("a", levels=[b"ABCDEFGH"]), b.native("b", levels=[b"ABCDEFGH"]),
                  b.native("c", levels=[b"12345678"])])
    t = parse_txd(data)
    a, bb, c = t.textures
    assert mip0_bytes(data, a) == b"ABCDEFGH"
    assert texture_hash(data, a) == texture_hash(data, bb) != texture_hash(data, c)
    assert texture_hash(data, a) == hashlib.blake2b(b"ABCDEFGH", digest_size=12).digest()
    assert len(texture_hash(data, a)) == 12
    # base = position of the TXD in buf, the same value for every function; TexInfo offsets are TXD-relative
    prefix = b"\xEE" * 100
    arch = prefix + data
    t2 = parse_txd(arch, base=100)
    assert t2.textures == t.textures                       # position-independent TexInfo
    for x in t2.textures:
        assert texture_hash(arch, x, 100) == texture_hash(data, x)
        assert mip0_bytes(arch, x, 100) == mip0_bytes(data, x)
        assert palette_bytes(arch, x, 100) == palette_bytes(data, x)
    assert mip0_bytes(arch, t2.textures[0], base=100) == b"ABCDEFGH"
    with pytest.raises(FormatError, match="mip0 outside buffer"):
        texture_hash(data, a, base=len(data))


def test_d3d8_pal8_with_palette_in_hash(b):
    pal = bytes(range(256)) * 4
    pix = bytes(16)
    nat = b.native("outro", platform=8, fmt=0, raster=0x2000 | 0x600, w=4, h=4, depth=8,
                   levels=[pix], flags=0, alpha=0, palette=pal)
    data = b.txd([nat])
    (t,) = parse_txd(data).textures
    assert (t.platform, t.d3dfmt, t.pal_size, t.alpha) == (8, "PAL8", 1024, False)
    assert palette_bytes(data, t) == pal and mip0_bytes(data, t) == pix
    h = hashlib.blake2b(digest_size=12)
    h.update(pix)
    h.update(pal)
    assert texture_hash(data, t) == h.digest()
    arch = b"\0" * 7 + data                                # same base everywhere, palette included
    (t7,) = parse_txd(arch, base=7).textures
    assert t7 == t and t7.pal_off is not None
    assert texture_hash(arch, t7, 7) == h.digest() and palette_bytes(arch, t7, base=7) == pal


def test_d3d8_dxt_and_raster_names(b):
    data = b.txd([b.native("w", platform=8, raster=0x200, flags=1, alpha=0),
                  b.native("m", platform=8, raster=0x500, flags=0, alpha=1, w=1, h=1, levels=[b"1234"]),
                  b.native("x", platform=8, raster=0x300, flags=3, alpha=1, levels=[b"\0" * 16])])
    assert [(x.d3dfmt, x.alpha) for x in parse_txd(data).textures] == [("DXT1", False), ("A8R8G8B8", True),
                                                                      ("DXT3", True)]


def test_unsupported_platform_kept(b):
    ps2 = b.chunk(0x15, b.chunk(0x01, struct.pack("<I", 0x00325350) + b"\x02\x11\0\0")
                  + b.chunk(0x02, b"ps2tex\0\0") + b.chunk(0x02, b"mask\0\0\0\0") + b.chunk(0x01, b"\0" * 64))
    t = parse_txd(b.txd([ps2, b.native("ok")]))
    p, ok = t.textures
    assert p.unsupported and p.platform == 0x00325350 and p.name == "ps2tex" and p.mask == "mask"
    assert p.mip0_size == 0 and p.d3dfmt.startswith("PLATFORM_")
    assert not ok.unsupported and ok.idx == 1
    data = b.txd([ps2, b.native("ok")])
    assert parse_txd(b"\1" * 5 + data, base=5).textures == t.textures


@pytest.mark.parametrize("platform", [8, 9, 0x00325350])
@pytest.mark.parametrize("base", [0, 17])
@pytest.mark.parametrize("preceding_extension", [False, True])
def test_native_trailing_child_overrun(b, platform, base, preceding_extension):
    native = b.native("a", platform=platform)
    if preceding_extension:
        native = b.chunk(0x15, native[12:] + b.chunk(0x03, b""))
    bad = bytearray(native)
    struct.pack_into("<I", bad, len(bad) - 8, 0xFFFFFFFF)
    data = b"\xEE" * base + b.txd([bytes(bad)])
    # TXD header + dictionary Struct + native's final Extension header.
    offset = base + 12 + 16 + len(native) - 12
    with pytest.raises(FormatError, match="overruns parent") as ei:
        parse_txd(data, base=base)
    assert ei.value.offset == offset
    assert f"0x{offset:x}" in str(ei.value)


@pytest.mark.parametrize("platform", [8, 9, 0x00325350])
def test_native_trailing_padding_is_allowed(b, platform):
    native = b.native("a", platform=platform)
    data = b.txd([b.chunk(0x15, native[12:] + b"\xFF" * 11)])
    assert len(parse_txd(data).textures) == 1


def test_empty_txd(b):
    t = parse_txd(b.txd([], device=6))
    assert t.count == 0 and t.textures == []


@pytest.mark.parametrize("mutate", ["not_txd", "short_struct", "mip_overrun", "palette_overrun", "no_struct"])
def test_malformed_raise_format_error(b, mutate):
    if mutate == "not_txd":
        data = b.chunk(0x10, b"")
    elif mutate == "short_struct":
        data = b.txd([b.chunk(0x15, b.chunk(0x01, struct.pack("<I", 9) + b"\0" * 20))])
    elif mutate == "mip_overrun":
        nat = bytearray(b.native("a", levels=[b"12345678"]))
        size_pos = 12 + 12 + 88  # TextureNative hdr + Struct hdr + fixed fields -> level-0 size field
        struct.pack_into("<I", nat, size_pos, 9999)
        data = b.txd([bytes(nat)])
    elif mutate == "palette_overrun":
        data = b.txd([b.native("p", raster=0x2000, fmt=41, flags=0, levels=[], palette=b"\0" * 10)])
    else:
        data = b.txd([b.chunk(0x15, b.chunk(0x02, b"name"))])
    with pytest.raises(FormatError) as ei:
        parse_txd(data)
    assert ei.value.kind in ("txd", "rw")
