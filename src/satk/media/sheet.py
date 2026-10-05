"""Contact sheets: many textures in ONE numbered PNG + a JSON legend (SPEC §4.4, §3.5). Owner WP-04.

Layout: up to 8x8 cells of ``cell`` px (default 128) with a 4 px gap and margin; every cell has its
number 1..N in the top-left corner (white on black, built-in bitmap font). Textures are scaled to fit
the cell (small ones are enlarged by an integer factor with nearest neighbour, so their pixels stay
crisp); transparent textures sit on a grey checkerboard. Captions under the cells only with
``labels=True``: the agent reads names from the legend ``[n, sid, "WxH FMT"]``.

Sheets are written to ``work/out/sheets/<slug>-<key>.png``; ``key`` hashes the cell contents and the
layout, so the same request returns the existing file without re-rendering.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Sequence

from ..core.errors import SatkError
from ..core.paths import work
from . import png as _png
from .raster import fit_size, new_painter, resize_rgba
from .texture import Reader, decode_ref, describe, resolve_refs

__all__ = ["MAX_COLS", "MAX_CELLS", "GAP", "contact_sheet", "render_sheet", "auto_cols", "sheet_geometry",
           "upscale_nearest", "SHEET_VERSION"]

MAX_COLS = 8
MAX_CELLS = MAX_COLS * MAX_COLS
GAP = 4
#: Bump when the drawing changes (part of the cache key).
SHEET_VERSION = 2
_BG = (30, 31, 34, 255)
_CELL_BG = (52, 53, 58, 255)
_CHECK = ((92, 92, 96, 255), (136, 136, 140, 255))
_BADGE_BG = (0, 0, 0, 255)
_BADGE_FG = (255, 255, 255, 255)
_BADGE_BORDER = (255, 205, 40, 255)
_CAPTION = (225, 225, 225, 255)


def auto_cols(n: int) -> int:
    """Columns for ``n`` cells: ``ceil(sqrt(n))``, at most 8 (16 -> 4, 15 -> 4, 64 -> 8)."""
    return max(1, min(MAX_COLS, math.ceil(math.sqrt(max(1, n)))))


def sheet_geometry(n: int, cols: int, cell: int, labels: bool) -> dict:
    """Pixel layout of a sheet: size and the top-left corner of every cell."""
    cols = max(1, min(int(cols), n))
    rows = math.ceil(n / cols)
    cap = 10 if labels else 0
    w = 2 * GAP + cols * cell + (cols - 1) * GAP
    h = 2 * GAP + rows * (cell + cap) + (rows - 1) * GAP
    cells = [(GAP + (i % cols) * (cell + GAP), GAP + (i // cols) * (cell + cap + GAP)) for i in range(n)]
    return {"w": w, "h": h, "cols": cols, "rows": rows, "caption": cap, "cells": cells}


def upscale_nearest(w: int, h: int, rgba: bytes, k: int) -> bytes:
    """Integer nearest-neighbour enlargement (stdlib, C-speed slicing)."""
    if k <= 1:
        return bytes(rgba)
    out_rows = []
    for y in range(h):
        row = rgba[y * w * 4:(y + 1) * w * 4]
        wide = b"".join(row[x * 4:x * 4 + 4] * k for x in range(w))
        out_rows.append(wide * k)
    return b"".join(out_rows)


def _fit_cell(w: int, h: int, rgba: bytes, cell: int) -> tuple[int, int, bytes]:
    if max(w, h) > cell:
        nw, nh = fit_size(w, h, cell)
        return nw, nh, resize_rgba(w, h, rgba, nw, nh)
    k = cell // max(w, h)
    if k >= 2:
        return w * k, h * k, upscale_nearest(w, h, rgba, k)
    return w, h, rgba


def _slug(s: str) -> str:
    t = re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")
    return (t or "sheet")[:40]


def _checker(p, x0: int, y0: int, w: int, h: int, sq: int = 8) -> None:
    for yy in range(0, h, sq):
        for xx in range(0, w, sq):
            c = _CHECK[((xx // sq) + (yy // sq)) & 1]
            p.rect(x0 + xx, y0 + yy, x0 + min(w, xx + sq), y0 + min(h, yy + sq), fill=c)


def render_sheet(refs: Sequence, *, cols: int | None = None, cell: int = 128, labels: bool = False, start: int = 1,
                 name: str = "sheet", reader: Reader | None = None) -> tuple[Path, list[list]]:
    """Render ``refs`` (≤64 :class:`~satk.index.api.TexRef`) into one sheet; numbers start at ``start``.

    Returns ``(path, legend)``; legend rows are ``[n, sid, "WxH FMT"]``.
    """
    n = len(refs)
    if n == 0:
        raise SatkError("NOT_FOUND", "no textures to put on a sheet")
    if n > MAX_CELLS:
        raise SatkError("BAD_PARAMS", f"a sheet holds at most {MAX_CELLS} textures, got {n}",
                        hint="split the list (texture_image pages automatically)")
    cell = int(cell)
    if cell < 16 or cell > 512:
        raise SatkError("BAD_PARAMS", f"cell must be 16..512 px, got {cell}")
    c = auto_cols(n) if not cols else max(1, min(MAX_COLS, int(cols)))
    legend = [[start + i, str(r.sid), describe(r)] for i, r in enumerate(refs)]
    key_src = json.dumps({"v": SHEET_VERSION, "png": _png.backend(), "cols": c, "cell": cell, "labels": bool(labels),
                          "cells": [[str(r.pix), row[1], row[2], r.platform, bool(r.alpha)]
                                    for r, row in zip(refs, legend)], "start": start},
                         separators=(",", ":"), sort_keys=True)
    key = hashlib.blake2b(key_src.encode("utf-8"), digest_size=8).hexdigest()
    path = work("out", "sheets", f"{_slug(name)}-{key}.png")
    if path.is_file():
        return path, legend

    g = sheet_geometry(n, c, cell, labels)
    p = new_painter(g["w"], g["h"], _BG)
    scale = 2 if cell >= 96 else 1
    own = reader is None
    rd = reader or Reader()
    errors = []
    try:
        for i, (ref, (cx, cy)) in enumerate(zip(refs, g["cells"])):
            p.rect(cx, cy, cx + cell, cy + cell, fill=_CELL_BG)
            try:
                w, h, rgba = decode_ref(ref, rd)
            except SatkError as e:
                legend[i][2] += f" ({e.code})"
                errors.append([i, e.code])
                p.rect(cx + 8, cy + 8, cx + cell - 8, cy + cell - 8, outline=(200, 60, 60, 255), width=2)
                w = 0
            if w:
                tw, th, tile = _fit_cell(w, h, rgba, cell)
                tx, ty = cx + (cell - tw) // 2, cy + (cell - th) // 2
                if not _png.is_opaque(tile):
                    _checker(p, tx, ty, tw, th)
                p.image(tx, ty, tw, th, tile)
            p.badge(cx, cy, str(start + i), scale=scale, fg=_BADGE_FG, bg=_BADGE_BG, border=_BADGE_BORDER)
            if labels:
                text = str(ref.sid).rsplit("/", 1)[-1].split(":", 1)[-1].split("@", 1)[0].upper()
                maxc = max(1, (cell + 1) // 6)
                if len(text) > maxc:
                    text = text[:maxc - 1] + "~"
                p.text(cx, cy + cell + 2, text, _CAPTION, 1)
    finally:
        if own:
            rd.close()
    if errors:
        # A failed decode must never populate the successful sheet's cache entry. Retry it on
        # every request; identical failures still have identical pixels, paths and legends.
        failure = hashlib.blake2b(json.dumps(errors, separators=(",", ":")).encode(), digest_size=6).hexdigest()
        path = path.with_name(f"{path.stem}-error-{failure}.png")
    if not path.is_file():
        _png.write_file(path, p.png())
    return path, legend


def contact_sheet(sids: list[str], cols: int = 4, cell: int = 128, labels: bool = False,
                  profile: str = "vanilla") -> tuple[Path, list[list]]:
    """One contact sheet for ``sids`` (tex/pix/txd/model SIDs expanded to ≤64 textures).

    Args:
        sids: SIDs; ``txd:``/``model:`` expand to all their textures.
        cols: columns (0 = automatic, ``ceil(sqrt(n))``, at most 8).
        cell: cell size in px.
        labels: print texture names under the cells.
        profile: index profile.

    Returns:
        ``(path, legend)`` with legend rows ``[n, sid, "WxH FMT"]``.
    """
    from ..index.api import open_index

    if not sids:
        raise SatkError("BAD_PARAMS", "no SIDs given")
    refs, _warn = resolve_refs(open_index(profile), sids)
    name = str(sids[0]) if len(sids) == 1 else f"{sids[0]}_{len(refs)}"
    return render_sheet(refs, cols=cols, cell=cell, labels=labels, name=name)
