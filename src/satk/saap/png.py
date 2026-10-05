"""Minimal deterministic PNG writer/reader (stdlib ``zlib``), used by the mock endpoint.

Only 8-bit truecolor (RGB/RGBA) and grayscale, filter 0, no ancillary chunks, so equal pixels
always give equal bytes (the replay test relies on it).
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

__all__ = ["encode", "write", "size", "decode"]

_SIG = b"\x89PNG\r\n\x1a\n"
_CT = {1: 0, 3: 2, 4: 6}  # channels -> PNG colour type


def _chunk(tag: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


def encode(w: int, h: int, pixels: bytes | bytearray, channels: int = 3, level: int = 6) -> bytes:
    """PNG bytes for ``w×h`` pixels, rows top-down, ``channels`` bytes per pixel (1, 3, 4)."""
    if channels not in _CT:
        raise ValueError("channels must be 1, 3 or 4")
    stride = w * channels
    if len(pixels) != stride * h:
        raise ValueError(f"expected {stride * h} bytes, got {len(pixels)}")
    raw = bytearray((stride + 1) * h)
    mv = memoryview(pixels)
    for y in range(h):
        o = y * (stride + 1)
        raw[o] = 0
        raw[o + 1:o + 1 + stride] = mv[y * stride:(y + 1) * stride]
    ihdr = struct.pack(">IIBBBBB", w, h, 8, _CT[channels], 0, 0, 0)
    return _SIG + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", zlib.compress(bytes(raw), level)) + _chunk(b"IEND", b"")


def write(path: str | Path, w: int, h: int, pixels: bytes | bytearray, channels: int = 3) -> Path:
    """Encode and write atomically (via ``satk.core.paths.atomic_write``)."""
    from ..core.paths import atomic_write

    return atomic_write(path, encode(w, h, pixels, channels))


def size(path: str | Path) -> tuple[int, int] | None:
    """``(w, h)`` from the IHDR chunk, or ``None`` if the file is not a PNG."""
    try:
        with open(path, "rb") as f:
            head = f.read(24)
    except OSError:
        return None
    if len(head) < 24 or head[:8] != _SIG or head[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", head[16:24])


def decode(data: bytes) -> tuple[int, int, int, bytes]:
    """Decode a non-interlaced 8-bit gray/RGB/RGBA PNG -> ``(w, h, channels, pixels)``."""
    if data[:8] != _SIG:
        raise ValueError("not a PNG")
    pos = 8
    w = h = ct = 0
    idat = bytearray()
    while pos < len(data):
        (n,) = struct.unpack(">I", data[pos:pos + 4])
        tag = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + n]
        pos += 12 + n
        if tag == b"IHDR":
            w, h, depth, ct, _c, _f, interlace = struct.unpack(">IIBBBBB", body)
            if depth != 8 or interlace != 0 or ct not in (0, 2, 6):
                raise ValueError("only 8-bit non-interlaced gray/RGB/RGBA PNGs are supported")
        elif tag == b"IDAT":
            idat += body
        elif tag == b"IEND":
            break
    ch = {0: 1, 2: 3, 6: 4}[ct]
    raw = zlib.decompress(bytes(idat))
    stride = w * ch
    out = bytearray(stride * h)
    prev = bytearray(stride)
    for y in range(h):
        ft = raw[y * (stride + 1)]
        line = bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for i in range(stride):
            a = line[i - ch] if i >= ch else 0
            b = prev[i]
            c = prev[i - ch] if i >= ch else 0
            if ft == 1:
                line[i] = (line[i] + a) & 0xFF
            elif ft == 2:
                line[i] = (line[i] + b) & 0xFF
            elif ft == 3:
                line[i] = (line[i] + ((a + b) >> 1)) & 0xFF
            elif ft == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 0xFF
        out[y * stride:(y + 1) * stride] = line
        prev = line
    return w, h, ch, bytes(out)
