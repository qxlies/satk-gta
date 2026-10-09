"""Pillow-drawn icons in the style of the generated set: the fallback that never needs the API.

Badge (rounded square, 12 % corner radius) in the badge colour with a thin ring, an emblem in the off-white colour
plus the accent of the icon: layered hills (1 to 4) with a dashed sight line, a tiny skyline on the last preset, or
a padlock (rectangle + arc). Status dots are flat circles with a 1 px darker ring. Everything is drawn 8x or more
oversampled on a transparent canvas and shrunk with Lanczos, so it is already transparent and passes the
validation of :mod:`satk.imagegen.keying`.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from ..core.errors import require_module
from .keying import KeyParams, parse_color, png_bytes, shrink, validate_image
from .provenance import sha256_bytes, utc_now, write_sidecar
from .spec import Icon, Spec, Status

__all__ = ["draw_icon", "draw_status", "write_set"]

BADGE_FILL = 0.926   # badge side as a fraction of the canvas (80 % badge + 4 % padding on each side, trimmed)
RADIUS = 0.12


def _lerp(a, b, t: float) -> tuple[int, int, int]:
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))  # type: ignore[return-value]


def _ss(size: int) -> int:
    return max(8, 384 // max(size, 1))


def _hill_points(cx: float, w: float, h: float, base: float, n: int = 48) -> list[tuple[float, float]]:
    pts = [(cx - w / 2, base)]
    for i in range(n + 1):
        x = cx - w / 2 + w * i / n
        bump = (0.5 + 0.5 * math.cos(math.pi * (x - cx) / (w / 2))) ** 0.85
        pts.append((x, base - h * bump))
    pts.append((cx + w / 2, base))
    return pts


def _draw_hills(d, U, L, icon: Icon, accent, badge, emblem) -> None:
    base = 0.72
    n = max(1, min(icon.hills, 4))
    hills = []
    for k in range(n):  # back (far, small) to front (near, large)
        t = 1.0 if n == 1 else k / (n - 1)
        if n == 1:
            hills.append((0.52, 0.62, 0.23, 1.0))
        else:
            hills.append((0.74 - 0.30 * t, 0.30 + 0.32 * t, 0.12 + 0.12 * t, t))
    for cx, w, h, t in hills:
        col = _lerp(badge, accent, 0.48 + 0.52 * t)
        d.polygon([U(x, y) for x, y in _hill_points(cx, w, h, base)], fill=col + (255,))
    if icon.skyline:
        cx, w, h, _t = hills[0]  # the far hill: a tiny city on its right slope
        for dx, bh in ((0.00, 0.050), (0.026, 0.080), (0.052, 0.060), (0.078, 0.040)):
            x = cx + dx - 0.02
            xx = (x - cx) / (w / 2)
            top = base - h * (0.5 + 0.5 * math.cos(math.pi * xx)) ** 0.85 if abs(xx) <= 1 else base
            d.rectangle([U(x - 0.010, top - bh), U(x + 0.010, top + 0.012)], fill=emblem + (255,))
    # ground bar, viewer dot and the dashed sight line (above the hills, falling slightly toward the horizon)
    d.rounded_rectangle([U(0.17, 0.72), U(0.83, 0.765)], radius=L(0.02), fill=emblem + (255,))
    ex, ey = 0.21, 0.36
    d.ellipse([U(ex - 0.05, ey - 0.05), U(ex + 0.05, ey + 0.05)], fill=emblem + (255,))
    x0 = ex + 0.085
    x_end = x0 + max(0.05, min(1.0, icon.sight)) * (0.82 - x0)
    x, dash, gap = x0, 0.055, 0.04
    y0, y1 = ey, 0.40
    span = max(x_end - x0, 1e-6)
    while x < x_end - 0.01:
        xa, xb = x, min(x + dash, x_end)
        ya = y0 + (y1 - y0) * (xa - x0) / span
        yb = y0 + (y1 - y0) * (xb - x0) / span
        d.line([U(xa, ya), U(xb, yb)], fill=emblem + (255,), width=max(1, int(L(0.028))))
        x += dash + gap


def _draw_lock(d, U, L, accent, badge) -> None:
    acc = accent + (255,)
    lw = max(1, int(L(0.07)))
    cx, cy, r = 0.5, 0.41, 0.145
    d.arc([U(cx - r, cy - r), U(cx + r, cy + r)], 180, 360, fill=acc, width=lw)
    d.line([U(cx - r + 0.035, cy), U(cx - r + 0.035, 0.50)], fill=acc, width=lw)
    d.line([U(cx + r - 0.035, cy), U(cx + r - 0.035, 0.50)], fill=acc, width=lw)
    d.rounded_rectangle([U(0.29, 0.47), U(0.71, 0.77)], radius=L(0.045), fill=acc)
    d.ellipse([U(0.5 - 0.045, 0.575), U(0.5 + 0.045, 0.665)], fill=badge + (255,))
    d.rectangle([U(0.5 - 0.019, 0.63), U(0.5 + 0.019, 0.71)], fill=badge + (255,))


def draw_icon(icon: Icon, spec: Spec, size: int, *, ss: int | None = None):
    """The icon as a Pillow RGBA image of ``size`` x ``size`` (transparent outside the badge)."""
    Image = require_module("PIL.Image", pip="Pillow", purpose="imagegen placeholder")
    ImageDraw = require_module("PIL.ImageDraw", pip="Pillow", purpose="imagegen placeholder")
    s = ss or _ss(size)
    C = size * s
    badge, ring = parse_color(spec.badge_color), parse_color(spec.ring_color)
    emblem, accent = parse_color(spec.emblem_color), parse_color(icon.accent)
    im = Image.new("RGBA", (C, C), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    side = C * BADGE_FILL
    off = (C - side) / 2

    def U(ux: float, uy: float) -> tuple[float, float]:
        return (off + ux * side, off + uy * side)

    def L(v: float) -> float:
        return v * side

    d.rounded_rectangle([U(0, 0), U(1, 1)], radius=RADIUS * side, fill=badge + (255,))
    ins = 0.045
    d.rounded_rectangle([U(ins, ins), U(1 - ins, 1 - ins)], radius=(RADIUS - 0.03) * side, outline=ring + (255,),
                        width=max(1, int(round(0.022 * side))))
    layer = Image.new("RGBA", (C, C), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    if icon.kind == "lock":
        _draw_lock(ld, U, L, accent, badge)
    else:
        _draw_hills(ld, U, L, icon, accent, badge, emblem)
    im.alpha_composite(layer)
    return im


def draw_status(st: Status, *, ss: int | None = None):
    """A flat circle of the status colour with a 1 px darker ring, ``st.size`` px square, transparent corners."""
    Image = require_module("PIL.Image", pip="Pillow", purpose="imagegen placeholder")
    ImageDraw = require_module("PIL.ImageDraw", pip="Pillow", purpose="imagegen placeholder")
    s = ss or _ss(st.size)
    C = st.size * s
    col = parse_color(st.color)
    dark = _lerp(col, (0, 0, 0), 0.4)
    im = Image.new("RGBA", (C, C), (0, 0, 0, 0))
    ImageDraw.Draw(im).ellipse([0, 0, C - 1, C - 1], fill=col + (255,), outline=dark + (255,), width=s)
    return im


def _finish(big, size: int, sharpen: bool):
    params = KeyParams(unsharp_max=48 if sharpen else 0)
    return shrink(big, size, params)


def write_set(spec: Spec, out: Path, *, only: list[str] | None = None) -> list[dict[str, Any]]:
    """Draw every icon and status dot of ``spec`` into ``out`` with sidecars. Returns one row per file.

    Row: ``file, id, size, sha256, valid, errors, path``. Files are overwritten (same bytes every run).
    """
    from ..core.paths import atomic_write

    out.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    jobs: list[tuple[str, int, Any, list[dict]]] = []
    for icon in spec.icons:
        if only and icon.name not in only and icon.id not in only:
            continue
        for size in icon.sizes:
            big = draw_icon(icon, spec, size)
            im, steps = _finish(big, size, sharpen=True)
            jobs.append((f"{icon.id}_{size}.png", size, im, [{"step": "draw", "shape": icon.kind, "hills": icon.hills,
                                                              "oversample": big.size[0] // size}] + steps))
    for st in spec.status:
        if only and st.name not in only and st.id not in only:
            continue
        big = draw_status(st)
        im, steps = _finish(big, st.size, sharpen=False)
        jobs.append((f"{st.id}_{st.size}.png", st.size, im, [{"step": "draw", "shape": "circle", "ring_px": 1,
                                                              "oversample": big.size[0] // st.size}] + steps))
    now = utc_now()
    for name, size, im, steps in jobs:
        data = png_bytes(im)
        path = atomic_write(out / name, data)
        v = validate_image(im)
        digest = sha256_bytes(data)
        write_sidecar(path, {"tool": "satk imagegen.placeholder", "generator": "placeholder", "spec": spec.name,
                             "prompt_id": None, "model": None, "size": size, "sha256": digest,
                             "sha256_raw": None, "steps": steps, "validation": v, "time_utc": now})
        rows.append({"file": name, "id": name.rsplit("_", 1)[0], "size": size, "sha256": digest, "valid": v["ok"],
                     "errors": v["errors"], "path": path, "steps": steps, "generator": "placeholder",
                     "model": None, "prompt_id": None, "time_utc": now})
    return rows
