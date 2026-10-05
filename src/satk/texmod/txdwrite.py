"""TXD writer: SA PC D3D9 texture natives and texture dictionaries (owner M2-03). Stdlib only.

Layout (the reader is :mod:`satk.formats.txd`; conventions checked against all vanilla SA TXDs)::

    TexDictionary 0x16 { Struct 0x01 (u16 count, u16 deviceId = 2) ; TextureNative 0x15 ... ; Extension 0x03 }
    TextureNative 0x15 { Struct 0x01 (below) ; Extension 0x03 (empty) }
    u32 platform = 9 ; u8 filter ; u8 addressing ; u16 0 ; char name[32] ; char mask[32]
    u32 rasterFormat ; u32 d3dFormat (FOURCC 'DXTn' or D3DFORMAT) ; u16 w, h ; u8 depth, levels,
    rasterType = 4, flags (1 = alpha, 8 = compressed) ; per level: u32 size + data

Raster format per texture format (vanilla: DXT1 0x200 / 0x100 with alpha, DXT3 0x300, X8R8G8B8 0x600,
A8R8G8B8 0x500; depth 16 for DXT); ``| 0x8000`` (rwRASTERFORMATMIPMAP) when there is more than one level.
Every chunk is stamped with the library ID of SA PC, ``0x1803FFFF`` (RW 3.6.0.3), unless the TXD being
edited uses another one. Sizes of the DXT levels below 4x4 are one block (some vanilla files write 0 there).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from ..formats.rw import FormatError, iter_children, read_chunk

__all__ = ["LIBID_SA", "DEVICE_D3D9", "FILTER_LINEAR", "FILTER_TRILINEAR", "ADDR_WRAP", "FMT_LAYOUT", "MAX_NAME",
           "NativeSpec", "native_chunk", "txd_chunk", "chunk", "rewrite_txd", "check_name", "mip_filter"]

LIBID_SA = 0x1803FFFF
DEVICE_D3D9 = 2
FILTER_LINEAR = 2          # rwFILTERLINEAR
FILTER_TRILINEAR = 6       # rwFILTERLINEARMIPLINEAR (the vanilla default)
ADDR_WRAP = 0x11           # U = V = rwTEXTUREADDRESSWRAP
#: Longest texture/mask name (32-byte field with a terminating zero).
MAX_NAME = 31
#: format -> (rasterFormat, d3dFormat, depth, compressed) for the alpha-less variant (DXT1 alpha: 0x100).
FMT_LAYOUT: dict[str, tuple[int, int, int, bool]] = {
    "DXT1": (0x200, int.from_bytes(b"DXT1", "little"), 16, True),
    "DXT3": (0x300, int.from_bytes(b"DXT3", "little"), 16, True),
    "DXT5": (0x300, int.from_bytes(b"DXT5", "little"), 16, True),
    "A8R8G8B8": (0x500, 21, 32, False),
    "X8R8G8B8": (0x600, 22, 32, False),
    "R5G6B5": (0x200, 23, 16, False),
    "A1R5G5B5": (0x100, 25, 16, False),
    "A4R4G4B4": (0x300, 26, 16, False),
}
_RASTER_MIPMAP = 0x8000
_RASTER_TEXTURE = 4
_HEAD = struct.Struct("<IBBH32s32sIIHHBBBB")
#: filter without mip filtering -> the same filter with mips (NEAREST -> MIPNEAREST, LINEAR -> LINEARMIPLINEAR)
_MIP_FILTER = {1: 3, 2: 6}


def check_name(name: str, what: str = "texture name") -> bytes:
    """``name`` as the 32-byte field; ``ValueError`` if it is not ASCII or longer than 31 characters."""
    try:
        raw = name.encode("ascii")
    except UnicodeEncodeError:
        raise ValueError(f"{what} {name!r} is not ASCII") from None
    if len(raw) > MAX_NAME:
        raise ValueError(f"{what} {name!r} is {len(raw)} characters, the TXD field holds {MAX_NAME}")
    if any(c < 32 or c == 127 for c in raw):
        raise ValueError(f"{what} {name!r} has control characters")
    return raw.ljust(32, b"\0")


def mip_filter(filt: int, levels: int) -> int:
    """Filter mode to store: a texture with mips needs a mip filter, or the game never samples them."""
    return _MIP_FILTER.get(filt, filt) if levels > 1 else filt


def chunk(ctype: int, payload: bytes, libid: int = LIBID_SA) -> bytes:
    return struct.pack("<III", ctype, len(payload), libid) + payload


@dataclass(frozen=True)
class NativeSpec:
    """One texture to write. ``levels`` = encoded mip levels, largest first (``w x h`` = level 0)."""

    name: str
    fmt: str
    w: int
    h: int
    levels: tuple[bytes, ...]
    alpha: bool = False
    mask: str = ""
    filter: int = FILTER_TRILINEAR
    addressing: int = ADDR_WRAP


def native_chunk(spec: NativeSpec, libid: int = LIBID_SA) -> bytes:
    """The ``TextureNative`` chunk of ``spec`` (platform 9, D3D9)."""
    if spec.fmt not in FMT_LAYOUT:
        raise ValueError(f"cannot write format {spec.fmt!r}; expected one of {tuple(FMT_LAYOUT)}")
    if not spec.levels or len(spec.levels) > 255:
        raise ValueError(f"texture {spec.name!r}: {len(spec.levels)} levels (1..255)")
    if not (0 < spec.w <= 0xFFFF and 0 < spec.h <= 0xFFFF):
        raise ValueError(f"texture {spec.name!r}: bad size {spec.w}x{spec.h}")
    raster, d3dfmt, depth, compressed = FMT_LAYOUT[spec.fmt]
    alpha = spec.alpha or spec.fmt in ("DXT3", "DXT5", "A8R8G8B8", "A1R5G5B5", "A4R4G4B4")
    if spec.fmt == "DXT1" and alpha:
        raster = 0x100
    if len(spec.levels) > 1:
        raster |= _RASTER_MIPMAP
    flags = (1 if alpha else 0) | (8 if compressed else 0)
    head = _HEAD.pack(9, spec.filter & 0xFF, spec.addressing & 0xFF, 0, check_name(spec.name),
                      check_name(spec.mask, "mask name"), raster, d3dfmt, spec.w, spec.h, depth,
                      len(spec.levels), _RASTER_TEXTURE, flags)
    body = b"".join(struct.pack("<I", len(lv)) + lv for lv in spec.levels)
    return chunk(0x15, chunk(0x01, head + body, libid) + chunk(0x03, b"", libid), libid)


def txd_chunk(natives: list[bytes], device_id: int = DEVICE_D3D9, libid: int = LIBID_SA) -> bytes:
    """A whole TXD from ready ``TextureNative`` chunks."""
    if len(natives) > 0xFFFF:
        raise ValueError(f"{len(natives)} textures do not fit a TXD (max 65535)")
    st = chunk(0x01, struct.pack("<HH", len(natives), device_id), libid)
    return chunk(0x16, st + b"".join(natives) + chunk(0x03, b"", libid), libid)


def rewrite_txd(buf: bytes, replace: dict[int, bytes], append: list[bytes] = ()) -> bytes:
    """Copy of the TXD in ``buf`` with the ``TextureNative`` chunks number ``k`` (0-based, file order)
    swapped for ``replace[k]`` and ``append`` added after the last one. Everything else (other textures,
    the dictionary extension, unknown chunks) is copied byte for byte; the header count is updated."""
    top = read_chunk(buf, 0)
    if top.type != 0x16:
        raise FormatError("txd", 0, f"not a TXD (chunk type 0x{top.type:X})")
    parts: list[bytes] = []
    k = 0
    count_at: int | None = None
    natives_end = None
    for ch in iter_children(buf, top.data_off, top.end):
        raw = bytes(buf[ch.data_off - 12:ch.end])
        if ch.type == 0x15:
            raw = replace.get(k, raw)
            k += 1
            parts.append(raw)
            natives_end = len(parts)
            continue
        if ch.type == 0x01 and count_at is None:
            if ch.size < 4:
                raise FormatError("txd", ch.data_off, "TexDictionary struct shorter than 4 bytes")
            count_at = len(parts)
        parts.append(raw)
    missing = sorted(set(replace) - set(range(k)))
    if missing:
        raise ValueError(f"no TextureNative number {missing[0]} (the TXD has {k})")
    if append:
        at = natives_end if natives_end is not None else (count_at + 1 if count_at is not None else 0)
        parts[at:at] = list(append)
    total = k + len(append)
    if count_at is not None:
        st = bytearray(parts[count_at])
        if total > 0xFFFF:
            raise ValueError(f"{total} textures do not fit a TXD (max 65535)")
        struct.pack_into("<H", st, 12, total)
        parts[count_at] = bytes(st)
    payload = b"".join(parts)
    return struct.pack("<III", 0x16, len(payload), top.libid) + payload
