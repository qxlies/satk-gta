"""Chroma key of the generated badge: magenta background -> alpha, despill, trim, resize, validation.

Steps, in this order (the same list goes into every provenance record):

1. ``key``     RGB distance to the key colour (#FF00FF): ``<= hard`` (90) fully transparent, ``hard..soft``
               (90..130) a linear alpha ramp, above ``soft`` opaque. Ramp pixels are decontaminated
               (``F = (C - (1-a) K) / a``) and blended toward the badge colour by ``1 - a``.
2. ``despill`` remove any magenta tint that is left: ``min(r, b) > g`` is pulled down to ``g``.
3. ``trim``    crop to the bounding box of the badge (alpha > 50 %), centre it on a square canvas.
4. ``pad``     4 % of the side on every edge (transparent).
5. ``resize``  Lanczos to the shipped size (premultiplied alpha, no dark fringes).
6. ``sharpen`` unsharp mask at sizes <= 48 px (alpha below 4 is then snapped to 0: resampling ringing).

Validation (:func:`validate_image`): the four corner pixels have alpha 0; no pixel with alpha >= 16 has a hue within
15 degrees of magenta (300) and saturation > 0.5; the badge (alpha >= 128) covers at least 50 % of the canvas.
Pillow and numpy are imported inside functions.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from ..core.errors import SatkError, require_module

__all__ = ["KeyParams", "parse_color", "load_image", "png_bytes", "key_image", "shrink", "validate_image",
           "ALPHA_VISIBLE", "ALPHA_FLOOR", "MAGENTA_HUE", "HUE_WINDOW", "SAT_MIN", "COVERAGE_MIN"]

ALPHA_VISIBLE = 16
ALPHA_FLOOR = 4
MAGENTA_HUE = 300.0
HUE_WINDOW = 15.0
SAT_MIN = 0.5
COVERAGE_MIN = 0.5


def parse_color(s: str | tuple | list) -> tuple[int, int, int]:
    """``#RRGGBB`` (or an ``(r, g, b)`` sequence) -> tuple of ints."""
    if isinstance(s, (tuple, list)) and len(s) >= 3:
        return int(s[0]), int(s[1]), int(s[2])
    t = str(s).strip().lstrip("#")
    if len(t) != 6 or any(c not in "0123456789abcdefABCDEF" for c in t):
        raise SatkError("BAD_PARAMS", f"expected a #RRGGBB colour, got {s!r}")
    return int(t[0:2], 16), int(t[2:4], 16), int(t[4:6], 16)


@dataclass(frozen=True)
class KeyParams:
    """Parameters of :func:`key_image` / :func:`shrink` (they are recorded in the provenance)."""

    key_color: str = "#FF00FF"
    hard: float = 90.0
    soft: float = 130.0
    badge_color: str = "#1F2428"
    pad: float = 0.04
    unsharp_max: int = 48

    def check(self) -> "KeyParams":
        if not (0 <= self.hard < self.soft <= 442):
            raise SatkError("BAD_PARAMS", f"need 0 <= hard < soft <= 442, got hard={self.hard} soft={self.soft}")
        if not (0 <= self.pad <= 0.3):
            raise SatkError("BAD_PARAMS", f"pad must be 0..0.3 of the side, got {self.pad}")
        parse_color(self.key_color), parse_color(self.badge_color)
        return self

    def asdict(self) -> dict:
        return asdict(self)


def _pil():
    return require_module("PIL.Image", pip="Pillow", purpose="imagegen")


def _np():
    return require_module("numpy", purpose="imagegen keying")


def load_image(source: str | Path | bytes):
    """A Pillow RGBA image from a file path or from encoded bytes."""
    Image = _pil()
    try:
        im = Image.open(io.BytesIO(source) if isinstance(source, (bytes, bytearray)) else str(source))
        im.load()
    except (OSError, ValueError) as e:
        raise SatkError("BAD_PARAMS", f"not a readable image: {e}") from None
    return im.convert("RGBA")


def png_bytes(im) -> bytes:
    """Deterministic PNG bytes of a Pillow image."""
    buf = io.BytesIO()
    im.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def _despill(rgb, np):
    """``min(r, b) > g`` -> pull r and b down to g (float arrays, in place on a copy)."""
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    spill = np.maximum(np.minimum(r, b) - g, 0.0)
    out = rgb.copy()
    out[..., 0] = r - spill
    out[..., 2] = b - spill
    return out


def key_image(im, params: KeyParams | None = None) -> tuple[Any, list[dict]]:
    """Key, despill, trim and pad ``im`` (RGBA, any size). Returns ``(master RGBA image, steps)``.

    Raises ``BAD_PARAMS`` when nothing opaque is left (the image has no badge).
    """
    p = (params or KeyParams()).check()
    Image, np = _pil(), _np()
    arr = np.asarray(im.convert("RGBA"), dtype=np.float32)
    rgb, a_in = arr[..., :3], arr[..., 3] / 255.0
    key = np.array(parse_color(p.key_color), dtype=np.float32)
    badge = np.array(parse_color(p.badge_color), dtype=np.float32)
    dist = np.sqrt(((rgb - key) ** 2).sum(axis=-1))
    a = np.clip((dist - p.hard) / (p.soft - p.hard), 0.0, 1.0)
    ramp = (a > 0.0) & (a < 1.0)
    out = rgb.copy()
    if ramp.any():
        aa = a[ramp][:, None]
        fg = np.clip((rgb[ramp] - (1.0 - aa) * key) / np.maximum(aa, 1e-3), 0.0, 255.0)
        out[ramp] = fg * aa + badge * (1.0 - aa)
    out = _despill(out, np)
    alpha = a * a_in
    steps = [{"step": "key", "key_color": p.key_color, "hard": p.hard, "soft": p.soft,
              "ramp_pixels": int(ramp.sum()), "transparent_pixels": int((alpha <= 0.0).sum())},
             {"step": "despill", "toward": p.badge_color}]

    # trim: bounding box of the badge, ignoring stray specks (rows/columns with almost no opaque pixels)
    solid = alpha > 0.5
    if not solid.any():
        raise SatkError("BAD_PARAMS", "no badge found: the whole image is background after keying",
                        hint="check that the background really is the key colour (#FF00FF)")
    rows, cols = solid.sum(axis=1), solid.sum(axis=0)
    ry = np.nonzero(rows >= max(2, 0.02 * rows.max()))[0]
    cx = np.nonzero(cols >= max(2, 0.02 * cols.max()))[0]
    y0, y1, x0, x1 = int(ry[0]), int(ry[-1]) + 1, int(cx[0]), int(cx[-1]) + 1
    rgba = np.dstack([np.clip(out, 0, 255), alpha * 255.0]).astype(np.float32)[y0:y1, x0:x1]
    h, w = rgba.shape[:2]
    side = max(h, w)
    pad = int(round(side * p.pad))
    canvas = np.zeros((side + 2 * pad, side + 2 * pad, 4), dtype=np.float32)
    oy, ox = pad + (side - h) // 2, pad + (side - w) // 2
    canvas[oy:oy + h, ox:ox + w] = rgba
    canvas[canvas[..., 3] <= 0.0, :3] = badge  # invisible pixels carry the badge colour (no halo when scaled)
    master = Image.fromarray(np.clip(np.rint(canvas), 0, 255).astype(np.uint8), "RGBA")
    steps += [{"step": "trim", "bbox": [x0, y0, x1, y1], "from": list(im.size)},
              {"step": "pad", "fraction": p.pad, "pixels": pad, "master": list(master.size)}]
    return master, steps


def shrink(master, size: int, params: KeyParams | None = None) -> tuple[Any, list[dict]]:
    """Lanczos resize of a keyed master to ``size`` x ``size``; unsharp mask when ``size <= unsharp_max``."""
    p = (params or KeyParams()).check()
    Image, np = _pil(), _np()
    ImageFilter = require_module("PIL.ImageFilter", pip="Pillow", purpose="imagegen")
    size = int(size)
    if size < 8 or size > 4096:
        raise SatkError("BAD_PARAMS", f"size must be 8..4096, got {size}")
    im = master if master.size == (size, size) else master.resize((size, size), Image.LANCZOS)
    steps: list[dict] = [{"step": "resize", "to": size, "filter": "lanczos"}]
    if size <= p.unsharp_max:
        radius, percent = (0.6, 90) if size <= 32 else (0.8, 70)
        arr = np.asarray(im, dtype=np.uint8).copy()
        badge = np.array(parse_color(p.badge_color), dtype=np.uint8)
        arr[arr[..., 3] == 0, :3] = badge
        rgb = Image.fromarray(arr[..., :3], "RGB").filter(ImageFilter.UnsharpMask(radius=radius, percent=percent,
                                                                                    threshold=1))
        arr[..., :3] = np.asarray(rgb, dtype=np.uint8)
        im = Image.fromarray(arr, "RGBA")
        steps.append({"step": "sharpen", "radius": radius, "percent": percent, "threshold": 1})
    # a last despill: resampling can mix red and blue neighbours into a faint magenta
    arr = np.asarray(im, dtype=np.float32).copy()
    arr[..., :3] = _despill(arr[..., :3], np)
    faint = arr[..., 3] < ALPHA_FLOOR  # Lanczos ringing leaves alpha 1..3 around the badge: snap it to 0
    arr[faint, 3] = 0.0
    im = Image.fromarray(np.clip(np.rint(arr), 0, 255).astype(np.uint8), "RGBA")
    return im, steps


def validate_image(im) -> dict:
    """Section-7 validation of a finished icon. ``{"ok", "corners", "magenta_pixels", "coverage", "errors"}``."""
    np = _np()
    arr = np.asarray(im.convert("RGBA"), dtype=np.float32)
    h, w = arr.shape[:2]
    alpha = arr[..., 3]
    corners = [int(alpha[0, 0]), int(alpha[0, w - 1]), int(alpha[h - 1, 0]), int(alpha[h - 1, w - 1])]
    rgb = arr[..., :3] / 255.0
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    mx, mn = rgb.max(axis=-1), rgb.min(axis=-1)
    delta = mx - mn
    sat = np.where(mx > 0, delta / np.maximum(mx, 1e-9), 0.0)
    d = np.maximum(delta, 1e-9)
    hue = np.where(mx == r, ((g - b) / d) % 6.0, np.where(mx == g, (b - r) / d + 2.0, (r - g) / d + 4.0)) * 60.0
    hue = np.where(delta <= 0, 0.0, hue)
    vis = alpha >= ALPHA_VISIBLE
    mag = vis & (np.abs(hue - MAGENTA_HUE) <= HUE_WINDOW) & (sat > SAT_MIN)
    coverage = float((alpha >= 128).sum()) / float(h * w)
    errors = []
    if any(c != 0 for c in corners):
        errors.append(f"corners not fully transparent (alpha {corners})")
    if int(mag.sum()):
        errors.append(f"{int(mag.sum())} magenta pixels left")
    if coverage < COVERAGE_MIN:
        errors.append(f"badge covers {coverage:.0%} of the canvas (< {COVERAGE_MIN:.0%})")
    return {"ok": not errors, "corners": corners, "magenta_pixels": int(mag.sum()),
            "coverage": round(coverage, 3), "errors": errors}
