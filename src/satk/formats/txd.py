"""Texture dictionaries (SPEC §4.2 ``satk.formats.txd``). Stdlib only.

``TexDictionary 0x16`` = ``Struct`` (``u16 count``, ``u16 deviceId``) + N x ``TextureNative 0x15``
+ ``Extension``. ``TextureNative.Struct`` (platform 8 = D3D8, 9 = D3D9)::

    u32 platform; u8 filter; u8 addressing (U<<4 | V); u16 pad; char name[32]; char mask[32];
    u32 rasterFormat; u32 d3dFormat (D3D9) | hasAlpha (D3D8);
    u16 w, h; u8 depth, levels, rasterType, flags   (D3D9: 1 alpha, 2 cube, 8 compressed; D3D8: DXTn)
    [palette 256x4 (PAL8) | 32x4 (PAL4)]; per level: u32 size + data

Pitfalls handled (SPEC §4.2 #4-#7): ``deviceId`` is unreliable (the texture ``platform`` decides),
platforms other than 8/9 are kept with ``unsupported=True``; DXT1 + raster 565 is opaque
(``alpha=False``); RW 0x34003 (``outro.txd``, D3D8 PAL8); 79 % of textures have one level.

Offsets in :class:`TexInfo` (``mip0_off``, ``pal_off``) are relative to the start of the TXD, i.e. to
``buf[base]`` (SPEC 4.2: "relative to the start of buf + base"). Every function takes the same ``base``:
``parse_txd(arc, base=o)`` and ``texture_hash(arc, t, base=o)`` agree, and the same ``TexInfo`` also
works on the blob alone (``texture_hash(arc[o:], t)``) or on the whole archive (``base=o``).

Example::

    t = parse_txd(blob)
    for tex in t.textures:
        print(tex.name, tex.d3dfmt, tex.w, tex.h, texture_hash(blob, tex).hex())
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass

from .rw import FormatError, iter_children, read_chunk, rw_version

__all__ = [
    "TexInfo",
    "Txd",
    "parse_txd",
    "texture_hash",
    "mip0_bytes",
    "palette_bytes",
    "D3DFMT_NAMES",
    "RASTER_NAMES",
    "HASH_SIZE",
]

#: blake2b digest size of :func:`texture_hash` (96 bit -> 24 hex chars, SID ``pix:``).
HASH_SIZE = 12

#: D3DFORMAT enum values used by RW D3D9 natives (FOURCC formats are decoded from ASCII).
D3DFMT_NAMES: dict[int, str] = {
    20: "R8G8B8", 21: "A8R8G8B8", 22: "X8R8G8B8", 23: "R5G6B5", 24: "X1R5G5B5", 25: "A1R5G5B5",
    26: "A4R4G4B4", 28: "A8", 41: "PAL8", 50: "L8", 51: "A8L8",
}
#: rasterFormat & 0xF00 -> d3dfmt name (D3D8 natives and the fallback for D3D9).
RASTER_NAMES: dict[int, str] = {
    0x100: "A1R5G5B5", 0x200: "R5G6B5", 0x300: "A4R4G4B4", 0x400: "L8", 0x500: "A8R8G8B8",
    0x600: "X8R8G8B8", 0xA00: "X1R5G5B5",
}
RASTER_PAL8 = 0x2000
RASTER_PAL4 = 0x4000
RASTER_565 = 0x200

_TN = struct.Struct("<IBBH32s32sII")  # platform, filter, addr, pad, name, mask, raster, d3dfmt/alpha
_DIM = struct.Struct("<HHBBBB")


@dataclass(frozen=True, slots=True)
class TexInfo:
    """Header of one texture (no pixels). ``alpha`` is the *effective* alpha."""

    idx: int
    name: str
    mask: str
    platform: int
    filter: int
    uaddr: int
    vaddr: int
    raster_fmt: int
    d3dfmt: str
    w: int
    h: int
    depth: int
    levels: int
    alpha: bool
    mip0_off: int
    mip0_size: int
    pal_off: int | None
    pal_size: int
    unsupported: bool


@dataclass(frozen=True, slots=True)
class Txd:
    """A parsed TXD. ``count`` is the header count (may differ from ``len(textures)`` in broken files)."""

    count: int
    device_id: int
    rw_version: int
    textures: list[TexInfo]


def _cstr(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("latin-1")


def _fourcc(v: int) -> str:
    if v in D3DFMT_NAMES:
        return D3DFMT_NAMES[v]
    b = struct.pack("<I", v)
    if all(48 <= c < 91 or c == 32 for c in b):  # 'DXT1', 'DXT3', 'DXT5', ...
        return b.decode("ascii").strip()
    return f"0x{v:X}"


def parse_txd(buf, base: int = 0) -> Txd:
    """Parse the TXD whose ``TexDictionary`` chunk starts at ``buf[base]``.

    ``TexInfo`` offsets are relative to ``buf[base]`` (pass the same ``base`` to :func:`texture_hash`,
    :func:`mip0_bytes`, :func:`palette_bytes`). Raises ``FormatError`` on anything malformed (never
    ``IndexError``/``struct.error``); ``FormatError.offset`` is a position in ``buf``.
    """
    top = read_chunk(buf, base)
    if top.type != 0x16:
        raise FormatError("txd", base, f"not a TXD (chunk type 0x{top.type:X})")
    count: int | None = None
    device = 0
    textures: list[TexInfo] = []
    for ch in iter_children(buf, top.data_off, top.end):
        if ch.type == 0x01 and count is None:
            if ch.size < 4:
                raise FormatError("txd", ch.data_off, "TexDictionary struct shorter than 4 bytes")
            count, device = struct.unpack_from("<HH", buf, ch.data_off)
        elif ch.type == 0x15:
            textures.append(_parse_native(buf, ch.data_off, ch.end, len(textures), base))
    return Txd(len(textures) if count is None else count, device, rw_version(top.libid), textures)


def _parse_native(buf, start: int, end: int, idx: int, base: int) -> TexInfo:
    kids = iter_children(buf, start, end)
    st = next(kids, None)
    if st is None or st.type != 0x01:
        raise FormatError("txd", start, "TextureNative without a leading Struct")
    o = st.data_off
    if st.size < 4:
        raise FormatError("txd", o, "TextureNative struct too short")
    (platform,) = struct.unpack_from("<I", buf, o)
    if platform not in (8, 9):
        return _unsupported(buf, st, kids, idx, platform, base)
    if st.size < 88:
        raise FormatError("txd", o, f"D3D TextureNative struct is {st.size} bytes, need >= 88")
    platform, filt, addr, _pad, name, mask, raster, fmt_or_alpha = _TN.unpack_from(buf, o)
    w, h, depth, levels, _rtype, flags = _DIM.unpack_from(buf, o + 80)
    pal = 8 if raster & RASTER_PAL8 else (4 if raster & RASTER_PAL4 else 0)
    if platform == 9:
        compressed = bool(flags & 8)
        d3dfmt = _fourcc(fmt_or_alpha)
        if pal:
            d3dfmt = f"PAL{pal}"
        elif not compressed and d3dfmt.startswith("0x"):
            d3dfmt = RASTER_NAMES.get(raster & 0xF00, d3dfmt)
        alpha = bool(flags & 1)
    else:  # D3D8: flags = DXTn number, alpha flag in the format slot
        if flags:
            d3dfmt = f"DXT{flags}"
        elif pal:
            d3dfmt = f"PAL{pal}"
        else:
            d3dfmt = RASTER_NAMES.get(raster & 0xF00, f"0x{raster & 0xF00:X}")
        alpha = bool(fmt_or_alpha)
    if d3dfmt == "DXT1" and (raster & 0xF00) == RASTER_565:
        alpha = False  # index 3 is black, not transparent; blending is off in the game
    p = o + 88
    send = st.end
    pal_off: int | None = None
    pal_size = 0
    if pal:
        pal_size = (256 if pal == 8 else 32) * 4
        if p + pal_size > send:
            raise FormatError("txd", p, f"palette of {pal_size} bytes overruns the texture struct")
        pal_off = p
        p += pal_size
    mip0_off = p
    mip0_size = 0
    for lvl in range(levels):
        if p + 4 > send:
            raise FormatError("txd", p, f"mip level {lvl} size field overruns the texture struct")
        (sz,) = struct.unpack_from("<I", buf, p)
        p += 4
        if p + sz > send:
            raise FormatError("txd", p, f"mip level {lvl} data ({sz} bytes) overruns the texture struct")
        if lvl == 0:
            mip0_off, mip0_size = p, sz
        p += sz
    # Unused children (including Extensions) must still fit in the TextureNative.
    for _ in kids:
        pass
    return TexInfo(
        idx=idx, name=_cstr(name), mask=_cstr(mask), platform=platform, filter=filt,
        uaddr=addr >> 4, vaddr=addr & 0xF, raster_fmt=raster, d3dfmt=d3dfmt, w=w, h=h, depth=depth,
        levels=levels, alpha=alpha, mip0_off=mip0_off - base, mip0_size=mip0_size,
        pal_off=None if pal_off is None else pal_off - base,
        pal_size=pal_size, unsupported=False,
    )


def _unsupported(buf, st, kids, idx: int, platform: int, base: int) -> TexInfo:
    """PS2/Xbox/other natives: keep the row, read names from String chunks when present."""
    names: list[str] = []
    for ch in kids:
        if ch.type == 0x02 and len(names) < 2:
            names.append(_cstr(bytes(buf[ch.data_off:ch.end])))
    filt = buf[st.data_off + 4] if st.size >= 6 else 0
    addr = buf[st.data_off + 5] if st.size >= 6 else 0
    return TexInfo(
        idx=idx, name=names[0] if names else "", mask=names[1] if len(names) > 1 else "",
        platform=platform, filter=filt, uaddr=addr >> 4, vaddr=addr & 0xF, raster_fmt=0,
        d3dfmt=f"PLATFORM_0x{platform:X}", w=0, h=0, depth=0, levels=0, alpha=False,
        mip0_off=st.end - base, mip0_size=0, pal_off=None, pal_size=0, unsupported=True,
    )


def mip0_bytes(buf, t: TexInfo, base: int = 0) -> bytes:
    """Raw mip0 data of ``t``; ``base`` = position of the TXD in ``buf`` (as for :func:`parse_txd`)."""
    a = base + t.mip0_off
    if a < 0 or a + t.mip0_size > len(buf):
        raise FormatError("txd", max(a, 0), "mip0 outside buffer")
    return bytes(buf[a:a + t.mip0_size])


def palette_bytes(buf, t: TexInfo, base: int = 0) -> bytes | None:
    """Raw palette (BGRA/RGBA as stored) or ``None``; ``base`` as for :func:`mip0_bytes`."""
    if t.pal_off is None:
        return None
    a = base + t.pal_off
    if a < 0 or a + t.pal_size > len(buf):
        raise FormatError("txd", max(a, 0), "palette outside buffer")
    return bytes(buf[a:a + t.pal_size])


def texture_hash(buf, t: TexInfo, base: int = 0) -> bytes:
    """blake2b (12 bytes) of mip0, then the palette when there is one. Key of ``image``/``pix:``.

    ``base`` = position of the TXD in ``buf``, the same value given to :func:`parse_txd` (``t``'s offsets
    are TXD-relative). A ``TexInfo`` from ``parse_txd(blob)`` hashes the same against the whole archive
    with ``base`` = the blob's offset in it.
    """
    h = hashlib.blake2b(digest_size=HASH_SIZE)
    a = base + t.mip0_off
    if a < 0 or a + t.mip0_size > len(buf):
        raise FormatError("txd", max(a, 0), "mip0 outside buffer")
    h.update(memoryview(buf)[a:a + t.mip0_size])
    pal = palette_bytes(buf, t, base)
    if pal:
        h.update(pal)
    return h.digest()
