"""Background map of the catalog (owner M2-09): the +-3000 m world drawn from placement footprints.

There is no water layer in the index yet, so the map is inferred from exterior placements:

* **land** = footprints (>= 40 m) of HD terrain tiles that rise above sea level (``maxz > 1.5``);
  everything else is water (the sea floor tiles stay below 0);
* **relief** = the highest tile top per pixel (lighter = higher), softened by a box blur;
* **lights** = density of HD object centres smaller than 160 m (buildings, props, vegetation).

Coverage uses a 2D difference array and blurs use summed-area tables (numpy), so 45 000 footprints take
about a second. North is up; pixel ``(0, 0)`` is world ``(-3000, 3000)``. numpy and Pillow are imported
lazily; without numpy there is no map image (the page then draws a plain grid).
"""

from __future__ import annotations

import io
from typing import Sequence

__all__ = ["MAP_PX", "render_world"]

#: Side of the map image, px (6000 m / 1536 px = 3.9 m per pixel).
MAP_PX = 1536
_WATER = (14, 32, 52)
_LOW = (40, 46, 46)
_HIGH = (92, 96, 86)
_LIGHT = (255, 176, 80)
_TILE_MIN = 40.0     # m: smaller footprints are objects, not terrain
_OBJ_MAX = 160.0     # m: bigger footprints are terrain, not "lights"
_SEA = 1.5           # m: a tile whose top is below this is sea floor
_TOP = 250.0         # m: relief colour saturates here


def _px_rect(np, a, px: int, world: Sequence[float]):
    x0w, y0w, x1w, y1w = world
    sx, sy = px / (x1w - x0w), px / (y1w - y0w)
    c0 = np.clip(np.floor((a[:, 0] - x0w) * sx), 0, px).astype(np.int64)
    c1 = np.clip(np.ceil((a[:, 2] - x0w) * sx), 0, px).astype(np.int64)
    r0 = np.clip(np.floor((y1w - a[:, 3]) * sy), 0, px).astype(np.int64)
    r1 = np.clip(np.ceil((y1w - a[:, 1]) * sy), 0, px).astype(np.int64)
    return c0, np.maximum(c1, np.minimum(c0 + 1, px)), r0, np.maximum(r1, np.minimum(r0 + 1, px))


def _inside(np, a, world: Sequence[float]):
    x0w, y0w, x1w, y1w = world
    return a[(a[:, 2] >= x0w) & (a[:, 0] <= x1w) & (a[:, 3] >= y0w) & (a[:, 1] <= y1w)]


def _coverage(np, a, px: int, world: Sequence[float]):
    """How many rectangles ``a[:, 0:4]`` (minx, miny, maxx, maxy) cover each pixel."""
    a = _inside(np, a, world)
    c0, c1, r0, r1 = _px_rect(np, a, px, world)
    d = np.zeros((px + 1, px + 1), dtype=np.int64)
    np.add.at(d, (r0, c0), 1)
    np.add.at(d, (r0, c1), -1)
    np.add.at(d, (r1, c0), -1)
    np.add.at(d, (r1, c1), 1)
    return d.cumsum(axis=0).cumsum(axis=1)[:px, :px]


def _blur(np, img, k: int):
    """Box blur of radius ``k`` (summed-area table, edges padded with zeros)."""
    n = 2 * k + 1
    s = np.pad(np.pad(img, k).cumsum(axis=0).cumsum(axis=1), ((1, 0), (1, 0)))
    return (s[n:, n:] - s[:-n, n:] - s[n:, :-n] + s[:-n, :-n]) / float(n * n)


def render_world(footprints: Sequence[tuple], ext: str, world: Sequence[float], px: int = MAP_PX) -> bytes | None:
    """Map image bytes (``webp``/``png``) from ``[(minx, miny, maxx, maxy, minz, maxz, is_lod)]``.

    Returns ``None`` without numpy.
    """
    try:
        import numpy as np
    except ImportError:
        return None
    img = np.empty((px, px, 3), dtype=np.float64)
    img[:] = _WATER
    if footprints:
        a = np.asarray([f[:6] for f in footprints], dtype=np.float64).reshape(-1, 6)
        hd = ~np.asarray([bool(f[6]) for f in footprints])
        size = np.maximum(a[:, 2] - a[:, 0], a[:, 3] - a[:, 1])
        tiles = _inside(np, a[hd & (size >= _TILE_MIN) & (a[:, 5] > _SEA)], world)
        if len(tiles):
            land = _coverage(np, tiles, px, world) > 0
            height = np.zeros((px, px), dtype=np.float64)
            c0, c1, r0, r1 = _px_rect(np, tiles, px, world)
            for i in np.argsort(tiles[:, 5], kind="stable"):  # painter: the highest top wins
                height[r0[i]:r1[i], c0[i]:c1[i]] = tiles[i, 5]
            h = np.clip(_blur(np, np.clip(height, 0.0, _TOP), 4) / _TOP, 0.0, 1.0)[..., None]
            relief = np.asarray(_LOW, dtype=np.float64) * (1.0 - h) + np.asarray(_HIGH, dtype=np.float64) * h
            img = np.where(land[..., None], relief, img)
        objs = a[hd & (size < _OBJ_MAX)]
        if len(objs):
            x0w, y0w, x1w, y1w = world
            cx = (objs[:, 0] + objs[:, 2]) / 2.0
            cy = (objs[:, 1] + objs[:, 3]) / 2.0
            ok = (cx >= x0w) & (cx < x1w) & (cy > y0w) & (cy <= y1w)
            col = ((cx[ok] - x0w) * px / (x1w - x0w)).astype(np.int64).clip(0, px - 1)
            row = ((y1w - cy[ok]) * px / (y1w - y0w)).astype(np.int64).clip(0, px - 1)
            dens = np.zeros((px, px), dtype=np.float64)
            np.add.at(dens, (row, col), 1.0)
            t = np.clip(np.log1p(_blur(np, dens, 2) * 25.0) / np.log1p(12.0), 0.0, 1.0)[..., None] * 0.85
            img = img * (1.0 - t) + np.asarray(_LIGHT, dtype=np.float64) * t
    rgb = np.ascontiguousarray(np.clip(np.rint(img), 0, 255).astype(np.uint8))
    if ext == "webp":
        from PIL import Image

        buf = io.BytesIO()
        Image.fromarray(rgb, "RGB").save(buf, "WEBP", quality=82, method=4)
        return buf.getvalue()
    from ..media import png as _png

    rgba = np.concatenate([rgb, np.full((px, px, 1), 255, np.uint8)], axis=2)
    return _png.encode(px, px, rgba.tobytes())
