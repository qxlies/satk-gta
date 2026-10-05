"""Deterministic PNG encoding (Pillow when installed, stdlib ``zlib`` otherwise) and PNG reading.

Same pixels -> same bytes: no timestamps, no text chunks, fixed compression level.
"""

from __future__ import annotations

import struct
import zlib

__all__ = ["encode_png", "png_size", "read_png_rgb"]

_SIG = b"\x89PNG\r\n\x1a\n"


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def encode_png(w: int, h: int, pixels: bytes, channels: int = 4, *, level: int = 6) -> bytes:
    """PNG bytes of ``w x h`` pixels (``channels`` 3 = RGB, 4 = RGBA, row 0 = top)."""
    if channels not in (3, 4):
        raise ValueError("channels must be 3 or 4")
    if len(pixels) != w * h * channels:
        raise ValueError(f"expected {w * h * channels} bytes, got {len(pixels)}")
    try:
        from PIL import Image
    except ImportError:
        Image = None
    if Image is not None:
        import io

        im = Image.frombytes("RGBA" if channels == 4 else "RGB", (w, h), bytes(pixels))
        buf = io.BytesIO()
        im.save(buf, "PNG", compress_level=level)
        return buf.getvalue()
    stride = w * channels
    raw = b"".join(b"\0" + pixels[y * stride:(y + 1) * stride] for y in range(h))
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6 if channels == 4 else 2, 0, 0, 0)
    return _SIG + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", zlib.compress(raw, level)) + _chunk(b"IEND", b"")


def png_size(data: bytes) -> tuple[int, int]:
    """``(w, h)`` from the IHDR of PNG bytes (``ValueError`` if not a PNG)."""
    if data[:8] != _SIG or data[12:16] != b"IHDR":
        raise ValueError("not a PNG")
    return struct.unpack(">II", data[16:24])


def read_png_rgb(path, bg: tuple[int, int, int] = (0, 0, 0), size: int | None = None) -> tuple[int, int, bytes]:
    """``(w, h, rgb bytes)`` of a PNG file composited over ``bg`` (optionally resized to ``size``²).

    Needs Pillow; used to tile renders of the viewer/Blender backends.
    """
    from ..core.errors import require_module

    Image = require_module("PIL.Image", pip="Pillow", purpose="reading PNG renders")
    with Image.open(path) as im:
        im = im.convert("RGBA")
        if size is not None and im.size != (size, size):
            im = im.resize((size, size), Image.LANCZOS)
        base = Image.new("RGBA", im.size, (*bg, 255))
        base.alpha_composite(im)
        rgb = base.convert("RGB")
        return rgb.width, rgb.height, rgb.tobytes()
