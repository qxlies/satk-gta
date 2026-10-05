"""Overlays on captured frames (SPEC §4.10.5): numbered marks, a labelled grid, comparisons.

* marks (Set-of-Mark): top-N entities by visible pixels of the ID buffer (exact) or, without
  an ID buffer, by the projected area of index AABBs (``approx: true``); a numbered circle at
  each visible centre -> ``*_marks.png``;
* grid: columns A…H × rows 1…6 with labels -> ``*_grid.png``; :func:`cell_center` maps ``"D3"``
  to a pixel;
* compare: side by side + difference heat map -> ``*_cmp.png`` and SSIM (8×8 windows, luma).

Pillow and numpy are imported inside the functions (``DEPENDENCY`` when missing).
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any, Sequence

from ..core.errors import SatkError, require_module

__all__ = ["GRID_COLS", "GRID_ROWS", "cell_center", "cell_of", "marks_from_ids", "marks_from_boxes",
           "draw_marks", "draw_grid", "compare", "ssim", "image_stats"]

GRID_COLS = "ABCDEFGH"
GRID_ROWS = 6


def _pil():
    return require_module("PIL.Image", pip="Pillow", purpose="viewer overlays")


def _font(size: int):
    ImageFont = require_module("PIL.ImageFont", pip="Pillow")
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # pragma: no cover - very old Pillow
        return ImageFont.load_default()


def cell_center(cell: str, w: int, h: int) -> tuple[float, float]:
    """Pixel centre of grid cell ``"D3"`` (column letter A…H, row 1…6) in a ``w×h`` image."""
    m = re.match(r"^\s*([A-Ha-h])\s*([1-6])\s*$", str(cell))
    if not m:
        raise SatkError("BAD_PARAMS", f"bad grid cell {cell!r} (columns A-H, rows 1-6, e.g. D3)")
    c = GRID_COLS.index(m.group(1).upper())
    r = int(m.group(2)) - 1
    return ((c + 0.5) * w / len(GRID_COLS), (r + 0.5) * h / GRID_ROWS)


def cell_of(x: float, y: float, w: int, h: int) -> str:
    c = min(len(GRID_COLS) - 1, max(0, int(x * len(GRID_COLS) / w)))
    r = min(GRID_ROWS - 1, max(0, int(y * GRID_ROWS / h)))
    return f"{GRID_COLS[c]}{r + 1}"


def _load_rgb(path: str | Path):
    Image = _pil()
    try:
        with Image.open(path) as im:
            return im.convert("RGB")
    except OSError as e:
        raise SatkError("NOT_FOUND", f"cannot read image {path}: {e}") from None


def _visible_center(mask) -> tuple[int, int]:
    """Visible pixel nearest the median coordinates of a non-empty entity mask."""
    np = require_module("numpy", purpose="viewer marks")
    ys, xs = np.nonzero(mask)
    # The medians can fall in a hole or between disconnected spans. Select a pixel
    # pair from the mask so the anchor always belongs to this entity.
    k = int(np.argmin((ys - np.median(ys)) ** 2 + (xs - np.median(xs)) ** 2))
    return int(xs[k]), int(ys[k])


def marks_from_ids(ids_png: str | Path, legend: list[dict], n: int) -> list[dict]:
    """Top-``n`` legend entries by visible pixels with a visible centre point.

    Returns ``[{"id", "entity", "px", "share", "x", "y"}]`` sorted by ``px`` (largest first).
    """
    np = require_module("numpy", purpose="viewer marks")
    img = np.asarray(_load_rgb(ids_png), dtype=np.uint32)
    h, w = img.shape[:2]
    idmap = (img[..., 0] << 16) | (img[..., 1] << 8) | img[..., 2]
    counts = np.bincount(idmap.ravel())
    by_id = {int(e["id"]): e for e in legend}
    order = [i for i in np.argsort(-counts, kind="stable") if i != 0 and counts[i] > 0 and int(i) in by_id]
    out = []
    for i in order[:n]:
        cx, cy = _visible_center(idmap == i)
        out.append({"id": int(i), "entity": by_id[int(i)]["entity"], "px": int(counts[i]),
                    "share": round(float(counts[i]) / (w * h), 4), "x": cx, "y": cy})
    return out


def marks_from_boxes(boxes: list[dict], w: int, h: int, n: int) -> list[dict]:
    """Approximate marks from screen rectangles ``[{"sid", "name", "rect": (x0, y0, x1, y1), "depth"}]``.

    Nearer boxes occlude farther ones (painter's order on rectangles); the visible share is the
    unoccluded rectangle area, the centre is a visible pixel near the median coordinates.
    """
    np = require_module("numpy", purpose="viewer marks")
    owner = np.zeros((h, w), dtype=np.int32)
    order = sorted(range(len(boxes)), key=lambda i: -float(boxes[i].get("depth", 0)))
    for i in order:
        x0, y0, x1, y1 = boxes[i]["rect"]
        owner[max(0, int(y0)):min(h, int(math.ceil(y1))), max(0, int(x0)):min(w, int(math.ceil(x1)))] = i + 1
    counts = np.bincount(owner.ravel(), minlength=len(boxes) + 1)
    ranked = [i for i in np.argsort(-counts[1:], kind="stable") if counts[i + 1] > 0][:n]
    out = []
    for i in ranked:
        cx, cy = _visible_center(owner == i + 1)
        b = boxes[i]
        out.append({**b, "px": int(counts[i + 1]), "share": round(float(counts[i + 1]) / (w * h), 4), "x": cx, "y": cy})
    return out


def draw_marks(src: str | Path, marks: Sequence[dict], out: str | Path) -> Path:
    """Draw numbered circles (``marks[i]["n"]`` at ``x``, ``y``) onto ``src`` -> ``out``."""
    ImageDraw = require_module("PIL.ImageDraw", pip="Pillow")
    im = _load_rgb(src)
    w, h = im.size
    d = ImageDraw.Draw(im)
    r = max(9, int(min(w, h) / 40))
    font = _font(int(r * 1.3))
    for m in marks:
        x, y, n = float(m["x"]), float(m["y"]), str(m["n"])
        x = min(max(x, r + 1), w - r - 2)
        y = min(max(y, r + 1), h - r - 2)
        d.ellipse((x - r, y - r, x + r, y + r), fill=(255, 255, 255), outline=(0, 0, 0), width=max(2, r // 5))
        d.text((x, y), n, fill=(200, 0, 0), font=font, anchor="mm")
    return _save(im, out)


def draw_grid(src: str | Path, out: str | Path) -> Path:
    """Draw the A…H × 1…6 grid with a label in every cell -> ``out``."""
    ImageDraw = require_module("PIL.ImageDraw", pip="Pillow")
    Image = _pil()
    im = _load_rgb(src).convert("RGBA")
    w, h = im.size
    layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    cw, ch = w / len(GRID_COLS), h / GRID_ROWS
    lw = max(1, int(min(w, h) / 400))
    for c in range(1, len(GRID_COLS)):
        d.line(((c * cw, 0), (c * cw, h)), fill=(255, 255, 0, 200), width=lw)
    for r in range(1, GRID_ROWS):
        d.line(((0, r * ch), (w, r * ch)), fill=(255, 255, 0, 200), width=lw)
    font = _font(max(10, int(ch / 6)))
    for c in range(len(GRID_COLS)):
        for r in range(GRID_ROWS):
            label = f"{GRID_COLS[c]}{r + 1}"
            x, y = c * cw + 3, r * ch + 2
            bbox = d.textbbox((x, y), label, font=font)
            d.rectangle((bbox[0] - 2, bbox[1] - 1, bbox[2] + 2, bbox[3] + 1), fill=(0, 0, 0, 150))
            d.text((x, y), label, fill=(255, 255, 0, 255), font=font)
    return _save(Image.alpha_composite(im, layer).convert("RGB"), out)


def _gray(im):
    np = require_module("numpy")
    a = np.asarray(im, dtype=np.float64)
    return 0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]


def ssim(a, b, win: int = 8) -> float:
    """Mean SSIM of two equally sized luma arrays (non-overlapping ``win×win`` blocks)."""
    np = require_module("numpy")
    h, w = a.shape
    hh, ww = h - h % win, w - w % win
    if hh == 0 or ww == 0:
        return 1.0 if np.array_equal(a, b) else 0.0
    A = a[:hh, :ww].reshape(hh // win, win, ww // win, win).transpose(0, 2, 1, 3).reshape(-1, win * win)
    B = b[:hh, :ww].reshape(hh // win, win, ww // win, win).transpose(0, 2, 1, 3).reshape(-1, win * win)
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    ma, mb = A.mean(1), B.mean(1)
    va, vb = A.var(1), B.var(1)
    cov = ((A - ma[:, None]) * (B - mb[:, None])).mean(1)
    s = ((2 * ma * mb + c1) * (2 * cov + c2)) / ((ma ** 2 + mb ** 2 + c1) * (va + vb + c2))
    return float(s.mean())


def compare(a_path: str | Path, b_path: str | Path, out: str | Path) -> dict:
    """Side-by-side + heat map (``out``), SSIM and mean absolute difference (0–255)."""
    np = require_module("numpy", purpose="viewer compare")
    Image = _pil()
    a = _load_rgb(a_path)
    b = _load_rgb(b_path)
    resized = False
    if b.size != a.size:
        b = b.resize(a.size, Image.Resampling.BILINEAR)
        resized = True
    ga, gb = _gray(a), _gray(b)
    s = ssim(ga, gb)
    diff = np.abs(np.asarray(a, dtype=np.int16) - np.asarray(b, dtype=np.int16)).max(axis=2).astype(np.float64)
    mad = float(np.abs(ga - gb).mean())
    norm = np.clip(diff / max(1.0, float(diff.max())), 0, 1)
    heat = np.zeros(diff.shape + (3,), dtype=np.uint8)
    heat[..., 0] = (255 * np.clip(norm * 2, 0, 1)).astype(np.uint8)
    heat[..., 1] = (255 * np.clip(norm * 2 - 1, 0, 1)).astype(np.uint8)
    heat[..., 2] = (64 * (1 - norm)).astype(np.uint8)
    w, h = a.size
    canvas = Image.new("RGB", (w * 3, h), (0, 0, 0))
    canvas.paste(a, (0, 0))
    canvas.paste(b, (w, 0))
    canvas.paste(Image.fromarray(heat, "RGB"), (2 * w, 0))
    p = _save(canvas, out)
    return {"file": p, "ssim": round(s, 4), "mad": round(mad, 3), "resized": resized,
            "changed_share": round(float((diff > 8).mean()), 4)}


def image_stats(path: str | Path) -> dict:
    """``{w, h, luma_std, black_share, mean}`` of an image (live-capture sanity checks)."""
    np = require_module("numpy")
    im = _load_rgb(path)
    a = np.asarray(im, dtype=np.int32)
    g = _gray(im)
    black = float((a.max(axis=2) == 0).mean())
    return {"w": im.size[0], "h": im.size[1], "luma_std": round(float(g.std()), 2), "mean": round(float(g.mean()), 2),
            "black_share": round(black, 4)}


def _save(im: Any, out: str | Path) -> Path:
    from ..core import paths

    p = paths.ensure_writable(out)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.tmp.png")
    im.save(tmp, format="PNG", optimize=False)
    import os

    os.replace(tmp, p)
    return p
