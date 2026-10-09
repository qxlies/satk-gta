"""Contact sheets of arbitrary PNG files (a grid, numbered cells) built from the ``satk.media`` sheet code.

Same look as the texture sheets: dark background, numbered cells (white on black, built-in bitmap font), transparent
images on a grey checkerboard, small images enlarged by an integer factor with nearest neighbour. Cells are filled
row by row; ``None`` marks a failed candidate (empty cell with a red frame).
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from ..core.errors import SatkError
from ..media import png as _png
from ..media.raster import new_painter
from ..media.sheet import (MAX_CELLS, _BADGE_BG, _BADGE_BORDER, _BADGE_FG, _BG, _CELL_BG, _checker, _fit_cell,
                           sheet_geometry)

__all__ = ["render_png_sheet"]


def render_png_sheet(files: Sequence[str | Path | None], out: str | Path, *, cols: int, cell: int = 160,
                     upscale_small: bool = True, start: int = 1) -> Path:
    """Write one sheet of ``files`` (``cols`` per row) to ``out``; numbers start at ``start``."""
    from ..core.paths import atomic_write

    n = len(files)
    if n == 0:
        raise SatkError("NOT_FOUND", "no images to put on a sheet")
    if n > MAX_CELLS:
        raise SatkError("BAD_PARAMS", f"a sheet holds at most {MAX_CELLS} cells, got {n}")
    if not 16 <= cell <= 512:
        raise SatkError("BAD_PARAMS", f"cell must be 16..512 px, got {cell}")
    g = sheet_geometry(n, max(1, cols), cell, False)
    p = new_painter(g["w"], g["h"], _BG)
    scale = 2 if cell >= 96 else 1
    for i, (f, (cx, cy)) in enumerate(zip(files, g["cells"])):
        p.rect(cx, cy, cx + cell, cy + cell, fill=_CELL_BG)
        w = 0
        if f is not None:
            try:
                w, h, rgba = _png.load_rgba(f)
            except (OSError, ValueError, SatkError):
                w = 0
        if w:
            if not upscale_small and max(w, h) < cell:
                tw, th, tile = w, h, rgba
            else:
                tw, th, tile = _fit_cell(w, h, rgba, cell)
            tx, ty = cx + (cell - tw) // 2, cy + (cell - th) // 2
            if not _png.is_opaque(tile):
                _checker(p, tx, ty, tw, th)
            p.image(tx, ty, tw, th, tile)
        else:
            p.rect(cx + 8, cy + 8, cx + cell - 8, cy + cell - 8, outline=(200, 60, 60, 255), width=2)
        p.badge(cx, cy, str(start + i), scale=scale, fg=_BADGE_FG, bg=_BADGE_BG, border=_BADGE_BORDER)
    return atomic_write(out, p.png())
