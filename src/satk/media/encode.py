"""Small, lossy images for agents: JPEG or WebP with a pixel and a byte limit (Pillow).

Images an agent opens cost context tokens; previews therefore go out as JPEG (or WebP) of at most
``max_px`` on the long side and, when ``max_bytes`` is given, at most that many bytes (the quality is
stepped down, then the image is shrunk). PNG stays the format of lossless outputs (textures, crops).

Example::

    from satk.media.encode import save_image
    info = save_image(path, w, h, rgba, fmt="jpg", max_px=1024, max_bytes=300_000)
    # {"path": ..., "w": 1024, "h": 512, "bytes": 182344, "quality": 85, "format": "jpg"}

Pillow is imported only inside functions (SPEC §2.3); equal pixels and settings give equal bytes.
"""

from __future__ import annotations

import io
import os
from pathlib import Path

from ..core.errors import SatkError, require_module
from ..core.paths import atomic_write, ensure_writable, jpath

__all__ = ["FORMATS", "fit", "encode_image", "save_image", "convert"]

FORMATS = ("jpg", "webp", "png")
_QUALITIES = (85, 78, 70, 62, 55, 48)


def _fmt(fmt: str) -> str:
    f = str(fmt or "jpg").lower().lstrip(".")
    f = {"jpeg": "jpg"}.get(f, f)
    if f not in FORMATS:
        raise SatkError("BAD_PARAMS", f"image format must be one of {'|'.join(FORMATS)}, got {fmt!r}")
    return f


def fit(w: int, h: int, max_px: int | None) -> tuple[int, int]:
    """``(w, h)`` scaled down (never up) so that the long side is at most ``max_px``."""
    if not max_px or max(w, h) <= max_px:
        return int(w), int(h)
    k = float(max_px) / max(w, h)
    return max(1, int(round(w * k))), max(1, int(round(h * k)))


def _pil_image(w: int, h: int, rgba: bytes, bg=(255, 255, 255)):
    Image = require_module("PIL.Image", purpose="JPEG/WebP images", pip="Pillow")
    im = Image.frombytes("RGBA", (int(w), int(h)), bytes(rgba))
    if im.getextrema()[3][0] < 255:  # flatten transparency (JPEG has no alpha)
        base = Image.new("RGBA", im.size, (*bg, 255))
        im = Image.alpha_composite(base, im)
    return im.convert("RGB")


def encode_image(w: int, h: int, rgba: bytes, fmt: str = "jpg", *, quality: int = 85, max_px: int | None = None,
                 max_bytes: int | None = None) -> tuple[bytes, dict]:
    """Encode RGBA pixels; returns ``(data, {"w", "h", "bytes", "quality", "format"})``.

    ``max_px`` limits the long side (LANCZOS downscale); ``max_bytes`` steps the quality down
    (85 ... 48) and then halves the size until the data fits (lossy formats only).
    """
    f = _fmt(fmt)
    if len(rgba) != int(w) * int(h) * 4:
        raise SatkError("BAD_PARAMS", f"expected {int(w) * int(h) * 4} RGBA bytes for {w}x{h}, got {len(rgba)}")
    Image = require_module("PIL.Image", purpose="JPEG/WebP images", pip="Pillow")
    im = _pil_image(w, h, rgba)
    nw, nh = fit(im.width, im.height, max_px)
    if (nw, nh) != im.size:
        im = im.resize((nw, nh), Image.LANCZOS)
    if f == "png":
        buf = io.BytesIO()
        im.save(buf, format="PNG", optimize=False, compress_level=6)
        data = buf.getvalue()
        return data, {"w": im.width, "h": im.height, "bytes": len(data), "format": f}
    qs = [int(quality)] + [q for q in _QUALITIES if q < int(quality)]
    while True:
        for q in qs:
            buf = io.BytesIO()
            if f == "jpg":
                im.save(buf, format="JPEG", quality=q, optimize=True, progressive=False, subsampling=2)
            else:
                im.save(buf, format="WEBP", quality=q, method=4)
            data = buf.getvalue()
            if not max_bytes or len(data) <= max_bytes:
                return data, {"w": im.width, "h": im.height, "bytes": len(data), "quality": q, "format": f}
        if min(im.size) <= 64:
            return data, {"w": im.width, "h": im.height, "bytes": len(data), "quality": qs[-1], "format": f}
        im = im.resize((max(1, im.width * 3 // 4), max(1, im.height * 3 // 4)), Image.LANCZOS)


def save_image(path: str | os.PathLike, w: int, h: int, rgba: bytes, fmt: str | None = None, **kw) -> dict:
    """:func:`encode_image` into ``path`` (atomic; under the work directory). ``fmt`` default: the suffix."""
    p = Path(path)
    f = _fmt(fmt or p.suffix or "jpg")
    data, info = encode_image(w, h, rgba, f, **kw)
    ensure_writable(p)
    atomic_write(p, data)
    info["path"] = jpath(p)
    return info


def convert(src: str | os.PathLike, dst: str | os.PathLike, fmt: str | None = None, **kw) -> dict:
    """Read any image Pillow reads (PNG, JPEG, ...) and :func:`save_image` it."""
    Image = require_module("PIL.Image", purpose="JPEG/WebP images", pip="Pillow")
    try:
        with Image.open(src) as im:
            im = im.convert("RGBA")
            w, h, rgba = im.width, im.height, im.tobytes()
    except OSError as e:
        raise SatkError("NOT_FOUND", f"cannot read image {jpath(Path(src))}: {e}") from None
    return save_image(dst, w, h, rgba, fmt, **kw)
