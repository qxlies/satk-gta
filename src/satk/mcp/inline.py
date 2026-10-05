"""Inline images for MCP results (SPEC §3.5): only on ``inline=true``, longest side <= 1024 px.

Pillow is imported lazily. Without Pillow a PNG is embedded as is if it is already small
enough (size read from the IHDR chunk), otherwise it is skipped with a warning.
"""

from __future__ import annotations

import base64
import io
import struct
from pathlib import Path

__all__ = ["MAX_SIDE", "MAX_BYTES", "encode_image", "png_size"]

MAX_SIDE = 1024
#: Above this many PNG bytes a resized image is re-encoded as JPEG (pixels, not bytes, cost tokens).
MAX_BYTES = 1_500_000
_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


def png_size(data: bytes) -> tuple[int, int] | None:
    """(w, h) from a PNG header, ``None`` if not a PNG."""
    if len(data) >= 24 and data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
        w, h = struct.unpack(">II", data[16:24])
        return int(w), int(h)
    return None


def encode_image(path: str | Path, max_side: int = MAX_SIDE) -> tuple[str, str, tuple[int, int]]:
    """``(base64, mime, (w, h))`` of ``path`` scaled to ``max_side``; ``ValueError`` if impossible."""
    p = Path(path)
    raw = p.read_bytes()
    try:
        from PIL import Image  # lazy: Pillow is optional
    except ImportError:
        size = png_size(raw)
        if size and max(size) <= max_side:
            return base64.b64encode(raw).decode("ascii"), "image/png", size
        raise ValueError("Pillow is not installed and the image needs resizing") from None
    with Image.open(io.BytesIO(raw)) as im:
        im.load()
        w, h = im.size
        mime = _MIME.get(p.suffix.lower())
        if max(w, h) <= max_side and mime in ("image/png", "image/jpeg") and len(raw) <= MAX_BYTES:
            return base64.b64encode(raw).decode("ascii"), mime, (w, h)
        if max(w, h) > max_side:
            k = max_side / max(w, h)
            im = im.resize((max(1, round(w * k)), max(1, round(h * k))), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        has_alpha = im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info)
        im.save(buf, format="PNG", optimize=False)
        if buf.tell() > MAX_BYTES and not has_alpha:
            buf = io.BytesIO()
            im.convert("RGB").save(buf, format="JPEG", quality=85)
            return base64.b64encode(buf.getvalue()).decode("ascii"), "image/jpeg", im.size
        return base64.b64encode(buf.getvalue()).decode("ascii"), "image/png", im.size
