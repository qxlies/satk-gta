"""TXD surgery for ``satk.txdopt``: native chunk spans, mip levels, a rebuild that drops or replaces textures, and
the lossless DXT3/DXT5 -> DXT1 transcode of fully opaque textures.

:func:`rebuild` copies every chunk it does not touch byte for byte (the dictionary extension, unknown chunks),
so a rebuild without changes returns the input bytes. The texture count in the dictionary struct is updated.

Transcode: a DXT3/DXT5 block ends with an 8-byte colour block that the decoder always reads in the 4-colour
mode. DXT1 uses the 4-colour mode only when ``c0 > c1``, so blocks with ``c0 < c1`` swap the endpoints and flip
the low index bit (``idx ^ 1``: 0<->1, 2<->3 give the same colours, including the integer rounding), and blocks
with ``c0 == c1`` (one colour) get all indices 0. The decoded RGB is identical; alpha must be 255 everywhere,
which the caller checks first. numpy inside the functions only.
"""

from __future__ import annotations

import struct

from ..formats.rw import FormatError, iter_children, read_chunk
from ..formats.txd import TexInfo

__all__ = ["native_spans", "level_list", "rebuild", "dxt_to_dxt1"]


def native_spans(buf: bytes) -> list[tuple[int, int]]:
    """``(start, end)`` of every ``TextureNative`` chunk (header included), in file order."""
    top = read_chunk(buf, 0)
    if top.type != 0x16:
        raise FormatError("txd", 0, f"not a TXD (chunk type 0x{top.type:X})")
    return [(ch.data_off - 12, ch.end) for ch in iter_children(buf, top.data_off, top.end) if ch.type == 0x15]


def level_list(buf: bytes, t: TexInfo) -> list[bytes]:
    """The stored mip levels of ``t`` (level 0 first), as :func:`satk.formats.txd.parse_txd` validated them."""
    out = []
    p = t.mip0_off - 4
    for _ in range(t.levels):
        (n,) = struct.unpack_from("<I", buf, p)
        out.append(bytes(buf[p + 4:p + 4 + n]))
        p += 4 + n
    return out


def rebuild(buf: bytes, natives: dict[int, bytes | None], append: list[bytes] = ()) -> bytes:
    """Copy of the TXD with ``TextureNative`` number ``k`` replaced by ``natives[k]`` or dropped (``None``);
    ``append`` goes after the last native. Untouched chunks are copied byte for byte."""
    top = read_chunk(buf, 0)
    if top.type != 0x16:
        raise FormatError("txd", 0, f"not a TXD (chunk type 0x{top.type:X})")
    parts: list[bytes] = []
    k = 0
    kept = 0
    count_at: int | None = None
    last_native: int | None = None
    for ch in iter_children(buf, top.data_off, top.end):
        raw = bytes(buf[ch.data_off - 12:ch.end])
        if ch.type == 0x15:
            new = natives[k] if k in natives else raw
            k += 1
            if new is None:
                continue
            parts.append(new)
            kept += 1
            last_native = len(parts)
            continue
        if ch.type == 0x01 and count_at is None:
            if ch.size < 4:
                raise FormatError("txd", ch.data_off, "TexDictionary struct shorter than 4 bytes")
            count_at = len(parts)
        parts.append(raw)
    missing = sorted(set(natives) - set(range(k)))
    if missing:
        raise ValueError(f"no TextureNative number {missing[0]} (the TXD has {k})")
    if append:
        at = last_native if last_native is not None else (count_at + 1 if count_at is not None else 0)
        parts[at:at] = list(append)
    total = kept + len(append)
    if count_at is not None:
        if total > 0xFFFF:
            raise ValueError(f"{total} textures do not fit a TXD (max 65535)")
        st = bytearray(parts[count_at])
        struct.pack_into("<H", st, 12, total)
        parts[count_at] = bytes(st)
    payload = b"".join(parts)
    return struct.pack("<III", 0x16, len(payload), top.libid) + payload


def dxt_to_dxt1(level: bytes) -> bytes:
    """One DXT3/DXT5 level -> the DXT1 level that decodes to the same RGB (see the module docstring)."""
    from ..core.errors import require_module

    np = require_module("numpy", purpose="DXT transcoding (satk texture optimize)")
    if not level:
        return b""
    if len(level) % 16:
        raise ValueError(f"DXT3/DXT5 level of {len(level)} bytes is not whole 16-byte blocks")
    blk = np.frombuffer(level, dtype=np.uint8).reshape(-1, 16)[:, 8:].copy()
    c = blk[:, 0:4].view("<u2")
    c0 = c[:, 0].copy()
    c1 = c[:, 1].copy()
    bits = blk[:, 4:8].view("<u4")[:, 0].copy()
    swap = c0 < c1
    same = c0 == c1
    out = np.empty((blk.shape[0], 2), dtype="<u4")
    lo = np.where(swap, c1, c0).astype(np.uint32)
    hi = np.where(swap, c0, c1).astype(np.uint32)
    out[:, 0] = lo | (hi << np.uint32(16))
    nb = np.where(swap, bits ^ np.uint32(0x55555555), bits)
    out[:, 1] = np.where(same, np.uint32(0), nb)
    return out.astype("<u4").tobytes()

