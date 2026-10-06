"""``texture.new``: a flat, gradient or banded base image of any size, so no helper script is needed.

An agent that models a prop needs a base image for its own texture: the paint colour, a lighter lid, a darker foot.
:func:`new_image` paints it from a few numbers: a base colour, an optional two-colour gradient and rectangles in
UV space (the same ``[u0, v0, u1, v1]`` rectangles ``uv.fit`` and ``kit.uv_region`` use: ``v`` runs up, as in
Blender). The PNG then goes to ``kit.material_preset image=`` (own texture) and through ``texture finish`` for the
SA look.

Example::

    from satk.texmod.newimg import new_image
    new_image("bin_base", size=[256, 128], color="#7a7c7c", rects=["0:0:1:0.6=#5c7a68"])
    # -> {"file": ".../work/out/texmod/new/bin_base.png", "size": "256x128", ...}

numpy is imported inside the functions only (SPEC section 2.3).
"""

from __future__ import annotations

import re
from pathlib import Path

from ..core.errors import SatkError
from ..core.paths import atomic_write, ensure_writable, jpath, work

__all__ = ["parse_color", "parse_size", "parse_rect", "new_image", "MAX_SIDE"]

#: Largest side ``texture.new`` makes (D3D9 cards of the SA era take 2048; vanilla maps stop at 512).
MAX_SIDE = 4096
_HEX = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")


def parse_color(text: str | list | tuple) -> tuple[int, int, int, int | None]:
    """``(r, g, b, a)`` of ``#rrggbb``, ``#rgb``, ``#rrggbbaa`` or ``r,g,b[,a]`` (0-255); ``a`` is ``None`` when absent."""
    if isinstance(text, (list, tuple)):
        parts = [str(v) for v in text]
    else:
        s = str(text).strip()
        m = _HEX.match(s)
        if m:
            h = m.group(1)
            if len(h) == 3:
                h = "".join(c * 2 for c in h)
            vals = [int(h[i:i + 2], 16) for i in range(0, len(h), 2)]
            return vals[0], vals[1], vals[2], vals[3] if len(vals) == 4 else None
        parts = [p for p in re.split(r"[,\s]+", s) if p]
    try:
        vals = [int(float(p)) for p in parts]
    except ValueError:
        vals = []
    if len(vals) not in (3, 4) or any(not 0 <= v <= 255 for v in vals):
        raise SatkError("BAD_PARAMS", f"colour {text!r}: use #rrggbb or r,g,b (0-255)",
                        hint="satk texture new bin --color #7a7c7c")
    return vals[0], vals[1], vals[2], vals[3] if len(vals) == 4 else None


def parse_size(size) -> tuple[int, int]:
    """``(w, h)`` of ``256``, ``[256, 128]``, ``"256x128"`` or ``"256,128"``; each side 4..4096, a multiple of 4."""
    if size is None or size == "":
        w = h = 256
    elif isinstance(size, (int, float)) and not isinstance(size, bool):
        w = h = int(size)
    else:
        parts = [int(p) for p in re.split(r"[x,\s]+", str(size).strip().lower()) if p] \
            if not isinstance(size, (list, tuple)) else [int(p) for p in size]
        if len(parts) == 1:
            w = h = parts[0]
        elif len(parts) == 2:
            w, h = parts
        else:
            raise SatkError("BAD_PARAMS", f"size {size!r}: one number or W H")
    if not (4 <= w <= MAX_SIDE and 4 <= h <= MAX_SIDE) or w % 4 or h % 4:
        raise SatkError("BAD_PARAMS", f"size {w}x{h}: each side 4..{MAX_SIDE} and a multiple of 4 (DXT blocks)",
                        hint="powers of two are best: 64 128 256; non-square 2:1 is fine (256x128)")
    return w, h


def parse_rect(text: str) -> tuple[float, float, float, float, tuple[int, int, int, int | None]]:
    """``(u0, v0, u1, v1, colour)`` of ``"u0:v0:u1:v1=#hex"`` (UV space, ``v`` up, 0..1)."""
    try:
        box, col = str(text).rsplit("=", 1)
        u0, v0, u1, v1 = (float(x) for x in re.split(r"[:\s,]+", box.strip()) if x != "")
    except ValueError:
        raise SatkError("BAD_PARAMS", f"rect {text!r}: use u0:v0:u1:v1=#rrggbb (UV space, v up)",
                        hint="--rect 0:0:1:0.6=#5c7a68") from None
    if not (0.0 <= u0 < u1 <= 1.0 and 0.0 <= v0 < v1 <= 1.0):
        raise SatkError("BAD_PARAMS", f"rect {text!r}: need 0 <= u0 < u1 <= 1 and 0 <= v0 < v1 <= 1")
    return u0, v0, u1, v1, parse_color(col)


def _name(name: str) -> str:
    n = str(name or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9_\-]{1,31}", n):
        raise SatkError("BAD_PARAMS", f"name {name!r}: 1-31 characters a-z 0-9 _ - (a texture name)")
    return n


def new_image(name: str, size=None, color: str = "#808080", color2: str | None = None, gradient: str = "none",
              rects: list[str] | None = None, alpha: int = 255, out: str | None = None) -> dict:
    """Paint ``<name>.png`` (see the module docstring); returns the envelope fields."""
    import numpy as np

    from ..core.envelope import obj
    from ..media import png as _png

    nm = _name(name)
    w, h = parse_size(size)
    r, g, b, a0 = parse_color(color)
    if not 0 <= int(alpha) <= 255:
        raise SatkError("BAD_PARAMS", f"alpha must be 0..255, got {alpha}")
    base_a = int(alpha) if a0 is None else a0
    img = np.empty((h, w, 4), dtype=np.float64)
    img[..., 0], img[..., 1], img[..., 2], img[..., 3] = r, g, b, base_a
    if gradient not in ("none", "u", "v"):
        raise SatkError("BAD_PARAMS", f"gradient must be none, u or v, got {gradient!r}")
    if gradient != "none":
        if not color2:
            raise SatkError("BAD_PARAMS", "a gradient needs --color2 (the colour at the far end)",
                            hint="--gradient v --color #6a6c6c --color2 #8a8c8c: v goes from the bottom (color) to the top")
        r2, g2, b2, a2 = parse_color(color2)
        end = np.array([r2, g2, b2, base_a if a2 is None else a2], dtype=np.float64)
        start = np.array([r, g, b, base_a], dtype=np.float64)
        if gradient == "v":  # v = 0 at the bottom row of the PNG, 1 at the top
            t = ((h - 1 - np.arange(h)) + 0.5) / h
            img[:] = start + (end - start) * t[:, None, None]
        else:
            t = (np.arange(w) + 0.5) / w
            img[:] = start + (end - start) * t[None, :, None]
    rows = []
    for text in rects or []:
        u0, v0, u1, v1, (cr, cg, cb, ca) = parse_rect(text)
        x0, x1 = int(round(u0 * w)), int(round(u1 * w))
        y0, y1 = int(round((1.0 - v1) * h)), int(round((1.0 - v0) * h))   # PNG row 0 is the top (v = 1)
        x1, y1 = max(x1, x0 + 1), max(y1, y0 + 1)
        img[y0:y1, x0:x1] = (cr, cg, cb, base_a if ca is None else ca)
        rows.append([text, f"{x0},{y0}..{x1},{y1}"])
    data = np.clip(np.rint(img), 0, 255).astype(np.uint8)
    opaque = bool((data[..., 3] == 255).all())
    d = work("out", "texmod", "new") if not out else ensure_writable(Path(out).absolute())
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{nm}.png"
    atomic_write(path, _png.encode(w, h, data.tobytes(), opaque=opaque))
    warn = []
    if (w & (w - 1)) or (h & (h - 1)):
        warn.append(f"NOT_POT: {w}x{h} is not a power of two; texture pack --pot resizes it")
    res = {"file": jpath(path), "size": f"{w}x{h}", "alpha": not opaque}
    if rows:
        res["rects"] = {"cols": ["rect", "pixels x0,y0..x1,y1"], "rows": rows}
    return obj(None, **res, warn=warn)
