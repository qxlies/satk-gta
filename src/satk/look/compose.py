"""Preview sheets: rendered cells -> one small JPEG with labels; textures -> lossless native-scale crops.

* :func:`sheet` - cells (PNG files, one row per state/pass/time, one column per view) tiled under a title
  strip, each cell with a small label; JPEG (or WebP) of at most ``max_px`` and ``max_bytes``
  (:mod:`satk.media.encode`);
* :func:`texture_sheet` - the textures of TXD files at their native scale (crops of at most 256 px), as a
  PNG: lossy previews hide texture defects (block noise, banding), so textures are judged here.

Pillow is imported only inside functions.
"""

from __future__ import annotations

import math
from pathlib import Path

from ..core.errors import SatkError
from ..core.paths import jpath

__all__ = ["MAX_PX", "MAX_BYTES", "cell_size", "layout", "sheet", "texture_sheet"]

MAX_PX = 1024
MAX_BYTES = 300_000
_TITLE_H = 16
_BG = (24, 25, 28, 255)
_FG = (235, 235, 235, 255)
_BADGE = (0, 0, 0, 255)


def cell_size(size: int, cols: int, rows: int = 1, max_px: int = MAX_PX, aspect: float = 1.0) -> int:
    """Cell width that keeps the sheet within ``max_px`` (cells are ``w x w/aspect``, title strip included)."""
    c = min(int(size * aspect), max_px // max(1, cols), int((max_px - _TITLE_H) // max(1, rows) * aspect))
    return max(48, c)


def layout(rows: int, cols: int, size: int, max_px: int = MAX_PX, aspect: float = 1.0) -> tuple[int, int, int | None]:
    """``(w, h, grid)``: the cell size and, when the row/column table would make cells much smaller than a
    plain grid (many states x passes, few views), the number of grid columns to wrap the cells into.
    ``aspect`` = width / height of a cell (a lineup is wide)."""
    cell = cell_size(size, cols, rows, max_px, aspect)
    n = rows * cols
    best, best_k = cell, None
    for k in range(1, n + 1):
        c = cell_size(size, k, math.ceil(n / k), max_px, aspect)
        if c > best * 1.45:
            best, best_k = c, k
    return best, max(32, int(round(best / aspect))), best_k


def _load(path: str) -> tuple[int, int, bytes]:
    from ..media.png import load_rgba

    return load_rgba(path)


def sheet(cells: list, rows: list[str], cols: list[str], *, title: str, out: str | Path, fmt: str = "jpg",
          max_px: int = MAX_PX, max_bytes: int = MAX_BYTES, grid: int | None = None) -> dict:
    """Compose ``cells`` (``[[row, col, png path], ...]``) into one image at ``out``; ``grid`` wraps the
    cells row-major into that many columns (labels name the row and column of each cell)."""
    from ..media import raster
    from ..media.encode import save_image

    if not cells:
        raise SatkError("INTERNAL", "no cells to compose")
    imgs = {(int(r), int(c)): _load(p) for r, c, p in cells}
    cw = max(w for w, _h, _d in imgs.values())
    ch = max(h for _w, h, _d in imgs.values())
    nr = max(r for r, _c in imgs) + 1
    nc = max(c for _r, c in imgs) + 1
    order = sorted(imgs)
    if grid:
        gc = max(1, int(grid))
        pos = {k: divmod(i, gc)[::-1] for i, k in enumerate(order)}  # (col, row) in the grid
        W, H = gc * cw, math.ceil(len(order) / gc) * ch + _TITLE_H
    else:
        pos = {(r, c): (c, r) for r, c in order}
        W, H = nc * cw, nr * ch + _TITLE_H
    p = raster.new_painter(W, H, _BG)
    p.text(4, 4, str(title)[: max(8, W // 6 - 2)], _FG, 1)
    for (r, c) in order:
        w, h, data = imgs[(r, c)]
        gx, gy = pos[(r, c)]
        x, y = gx * cw, _TITLE_H + gy * ch
        p.image(x, y, w, h, data)
        lab = f"{rows[r] if r < len(rows) else r} {cols[c] if c < len(cols) else c}"
        p.badge(x + 2, y + 2, lab.upper(), scale=1, fg=_FG, bg=_BADGE, pad=1)
    info = save_image(out, W, H, p.rgba(), fmt, max_px=max_px, max_bytes=max_bytes)
    info.update(rows=len(rows), cols=len(cols))
    return info


def texture_sheet(txds: list[str], out: str | Path, *, max_w: int = MAX_PX, crop: int = 256,
                  limit: int = 48) -> dict:
    """Own textures of the TXD files at native scale (crops of at most ``crop`` px) into a PNG."""
    from ..media import raster
    from ..media.encode import save_image
    from ..model3d.textures import TxdChain, decode

    items = []
    for t in txds:
        data = Path(t).read_bytes()
        chain = TxdChain([(Path(t).stem.lower(), data)])
        for name in sorted(chain._map):  # noqa: SLF001 - one TXD: its textures in name order
            d = decode(chain._map[name])  # noqa: SLF001
            items.append((f"{Path(t).stem.lower()}/{name}", d.w, d.h, d.rgba))
    if not items:
        raise SatkError("NOT_FOUND", "no textures in the TXD files", data={"txd": [jpath(Path(t)) for t in txds]})
    warn = []
    if len(items) > limit:
        warn.append(f"TRUNCATED: {len(items)} textures, the first {limit} are shown")
        items = items[:limit]
    cell = min(crop, max(16, max(max(w, h) for _n, w, h, _d in items)))
    cols = max(1, min(len(items), max_w // (cell + 2)))
    rows = math.ceil(len(items) / cols)
    W, H = cols * (cell + 2), rows * (cell + 12) + 2
    p = raster.new_painter(W, H, _BG)
    legend = []
    for i, (name, w, h, rgba) in enumerate(items):
        r, c = divmod(i, cols)
        x, y = c * (cell + 2) + 1, r * (cell + 12) + 11
        cw, chh = min(w, cell), min(h, cell)
        if (cw, chh) != (w, h):  # native-scale crop from the top-left corner
            rows_b = [rgba[(yy * w) * 4:(yy * w + cw) * 4] for yy in range(chh)]
            rgba = b"".join(rows_b)
        p.image(x, y, cw, chh, rgba)
        p.text(x, y - 9, str(i + 1), _FG, 1)
        legend.append([i + 1, name, f"{w}x{h}"])
    info = save_image(out, W, H, p.rgba(), "png")
    info["legend"] = legend
    if warn:
        info["warn"] = warn
    return info
