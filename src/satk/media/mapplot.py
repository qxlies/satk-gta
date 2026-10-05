"""Top-down map of an area from the index (SPEC §4.4 ``map_image``). Owner WP-04.

What is drawn (north up, world +Y = image up, ``m_per_px = span / px``):

* layer ``inst`` -- world AABB footprints of HD placements: outdoor orange, interiors (``area != 0``) violet;
  big footprints (ground, roads) are drawn fainter than small ones;
* layer ``lod``  -- LOD placements, faint blue, underneath;
* layer ``zone`` -- zone rectangles (``map.zon``/``info.zon``) with their names;
* always: a coordinate grid with world values on the edges, a scale bar, a north mark and the numbers
  1..N of the ``labels`` largest objects (footprint inside the view; labels kept apart). Their SIDs and
  names come back in the legend ``[n, sid, name]``.

Files go to ``work/out/maps/``; the name carries a key of the request and the index ``content_hash``,
and a ``.json`` sidecar keeps the legend, so a repeated request returns the existing file at once.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
from typing import Sequence

from ..core.errors import SatkError
from ..core.paths import ensure_writable, jpath, work
from . import png as _png
from .font import text_size
from .raster import new_painter

__all__ = ["MAP_LAYERS", "MAP_VERSION", "map_image", "render_map", "nice_step", "world_to_px"]

MAP_LAYERS = ("inst", "lod", "zone")
#: Bump when the drawing changes (part of the cache key).
MAP_VERSION = 3
_BG = (22, 24, 28, 255)
_GRID = (46, 50, 58, 255)
_GRID_TEXT = (125, 130, 142, 255)
_HD_RGB = (255, 172, 72)
_INT_RGB = (206, 112, 236)
_LOD_FILL, _LOD_LINE = (70, 110, 180, 30), (92, 140, 214, 140)
#: (max footprint m², fill alpha, outline alpha): big ground pieces stay faint so buildings read well
_ALPHA_BY_SIZE = ((300.0, 72, 255), (3000.0, 40, 220), (float("inf"), 14, 130))
#: objects whose footprint exceeds this share of the view get numbers only after all others
_BIG_SHARE = 0.15
_ZONE_LINE, _ZONE_TEXT = (92, 206, 120, 220), (120, 226, 146, 255)
_BADGE_BG, _BADGE_FG, _BADGE_BORDER = (0, 0, 0, 255), (255, 255, 255, 255), (255, 205, 40, 255)
_WHITE = (240, 240, 240, 255)
_LABEL_GAP = (16, 10)  # min |dx|, |dy| in px between label centres, per unit of badge scale


def nice_step(span: float, lines: float = 5.0) -> float:
    """A 1/2/5 x 10^k step giving about ``lines`` intervals over ``span``."""
    raw = max(span / lines, 1e-9)
    e = math.floor(math.log10(raw))
    for m in (1, 2, 5, 10):
        if m * 10 ** e >= raw:
            return float(m * 10 ** e)
    return float(10 ** (e + 1))  # pragma: no cover


def world_to_px(wx: float, wy: float, view: Sequence[float], mpp: float) -> tuple[float, float]:
    """World ``(x, y)`` -> image ``(px, py)`` (float) for ``view = (x0, y0, x1, y1)``."""
    return (wx - view[0]) / mpp, (view[3] - wy) / mpp


def _num(v: float) -> str:
    return f"{v:.2f}".rstrip("0").rstrip(".").replace("-", "m") or "0"


def _fmt_coord(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else f"{v:g}"


def _check(x: float, y: float, span: float, px: int, layers: Sequence[str], area: int, labels: int) -> tuple:
    try:
        x, y, span = float(x), float(y), float(span)
        px, area, labels = int(px), int(area), int(labels)
    except (TypeError, ValueError):
        raise SatkError("BAD_PARAMS", "x, y, span must be numbers; px, area, labels integers") from None
    if not all(math.isfinite(v) for v in (x, y, span)):
        raise SatkError("BAD_PARAMS", "x, y, span must be finite")
    if not 10 <= span <= 8000:
        raise SatkError("BAD_PARAMS", f"span must be 10..8000 m, got {span:g}")
    if not 64 <= px <= 2048:
        raise SatkError("BAD_PARAMS", f"px must be 64..2048, got {px}")
    if not -1 <= area <= 255:
        raise SatkError("BAD_PARAMS", f"area must be 0..255 or -1 (all), got {area}")
    if not 0 <= labels <= 99:
        raise SatkError("BAD_PARAMS", f"labels must be 0..99, got {labels}")
    ls = []
    for la in layers or ("inst",):
        la = str(la).strip().lower()
        if la not in MAP_LAYERS:
            raise SatkError("BAD_PARAMS", f"layer must be one of {', '.join(MAP_LAYERS)}, got {la!r}",
                            data={"layers": list(MAP_LAYERS)})
        if la not in ls:
            ls.append(la)
    return x, y, span, px, tuple(sorted(ls, key=MAP_LAYERS.index)), area, labels


def _index_stamp(db) -> str:
    stamp = ""
    try:
        stamp = str(db.meta().get("content_hash") or "")
    except Exception:  # noqa: BLE001 - meta is informational here
        pass
    if stamp:
        return stamp  # rebuilding an unchanged index must reuse the same map
    p = getattr(db, "path", None)
    try:
        st = os.stat(p) if p is not None and str(p) != ":memory:" else None
        if st is not None:
            stamp += f":{st.st_size}:{st.st_mtime_ns}"
    except OSError:
        pass
    return stamp


def _prune_request(side: Path, request_key: str) -> None:
    """Remove older generated pairs for this exact request after its replacement is written.

    Other views/options and legacy sidecars without a request key are left alone. Never follow
    a cache-file symlink out of this directory, or remove a newer concurrent render.
    """
    base = side.parent.resolve()
    try:
        newest = side.stat().st_mtime_ns
        for old in side.parent.glob(side.stem.rsplit("_", 1)[0] + "_*.json"):
            png = old.with_suffix(".png")
            try:
                if old == side or old.resolve().parent != base or png.resolve().parent != base:
                    continue
                if old.stat().st_mtime_ns > newest or (png.exists() and png.stat().st_mtime_ns > newest):
                    continue
                meta = json.loads(old.read_text(encoding="utf-8"))
                if not isinstance(meta, dict) or meta.get("request_key") != request_key:
                    continue
                ensure_writable(png).unlink(missing_ok=True)
                ensure_writable(old).unlink(missing_ok=True)
            except (OSError, ValueError):
                continue  # cleanup must not prevent use of the newly rendered map
    except OSError:
        pass


def _zones(db, view: Sequence[float]) -> list[list]:
    env = db.query("SELECT name, label, minx, miny, maxx, maxy FROM zone "
                   "WHERE maxx >= ? AND minx <= ? AND maxy >= ? AND miny <= ? "
                   "ORDER BY (maxx - minx) * (maxy - miny) DESC, lower(name), id LIMIT 300",
                   [view[0], view[2], view[1], view[3]], limit=300)
    return [list(r) for r in env["rows"] if None not in r[2:6]]


def render_map(x: float, y: float, span: float = 300, px: int = 768, layers: Sequence[str] = ("inst",),
               area: int = 0, labels: int = 20, profile: str = "vanilla") -> dict:
    """Render (or reuse) a map; returns ``{file, legend, m_per_px, view, counts[, zones]}``."""
    from ..index.api import open_index

    x, y, span, px, layers, area, labels = _check(x, y, span, px, layers, area, labels)
    db = open_index(profile)
    half = span / 2.0
    view = (x - half, y - half, x + half, y + half)
    mpp = span / px
    request = {"x": x, "y": y, "span": span, "px": px,
               "layers": layers, "area": area, "labels": labels, "profile": profile}
    request_key = hashlib.blake2b(json.dumps(request, separators=(",", ":"), sort_keys=True).encode(),
                                  digest_size=12).hexdigest()
    key_src = json.dumps({**request, "v": MAP_VERSION, "png": _png.backend(),
                          "index": _index_stamp(db)}, separators=(",", ":"), sort_keys=True)
    key = hashlib.blake2b(key_src.encode("utf-8"), digest_size=6).hexdigest()
    path = work("out", "maps", f"map_{_num(x)}_{_num(y)}_{_num(span)}m_{px}_{key}.png")
    side = path.with_suffix(".json")
    if path.is_file() and side.is_file():
        try:
            meta = json.loads(side.read_text(encoding="utf-8"))
            if meta.pop("key", None) == key:
                meta.pop("request_key", None)
                return {"file": jpath(path), **meta}
        except (OSError, ValueError):
            pass

    rows = []
    if "inst" in layers or "lod" in layers:
        lod = "all" if ("inst" in layers and "lod" in layers) else ("hd" if "inst" in layers else "lod")
        rows = db.insts(box=(view[0], view[1], view[2], view[3]), area=None if area < 0 else area, lod=lod,
                        match="aabb")
    zones = _zones(db, view) if "zone" in layers else []

    p = new_painter(px, px, _BG)
    # ---- grid
    step = nice_step(span, 6.0)
    grid_labels = []
    left_gutter = 0
    for k in range(math.ceil(view[0] / step), math.floor(view[2] / step) + 1):
        gx = k * step
        sx = int(round((gx - view[0]) / mpp))
        p.line_v(sx, 0, px - 1, _GRID)
        grid_labels.append((sx + 3, 3, _fmt_coord(gx)))
    for k in range(math.ceil(view[1] / step), math.floor(view[3] / step) + 1):
        gy = k * step
        sy = int(round((view[3] - gy) / mpp))
        p.line_h(0, px - 1, sy, _GRID)
        label = _fmt_coord(gy)
        grid_labels.append((3, sy + 3, label))
        left_gutter = max(left_gutter, text_size(label)[0] + 7)

    # ---- footprints
    def rect_of(a) -> tuple[int, int, int, int]:
        x0 = math.floor((a[0] - view[0]) / mpp)
        x1 = math.ceil((a[3] - view[0]) / mpp)
        y0 = math.floor((view[3] - a[4]) / mpp)
        y1 = math.ceil((view[3] - a[1]) / mpp)
        if x1 - x0 < 3:
            c = (x0 + x1) // 2
            x0, x1 = c - 1, c + 2
        if y1 - y0 < 3:
            c = (y0 + y1) // 2
            y0, y1 = c - 1, c + 2
        return x0, y0, x1, y1

    def clipped(r) -> tuple[int, int, int, int]:
        return max(0, r[0]), max(0, r[1]), min(px, r[2]), min(px, r[3])

    items = []
    for r in rows:
        rr = rect_of(r.aabb)
        c = clipped(rr)
        vis = max(0, c[2] - c[0]) * max(0, c[3] - c[1])
        items.append((r, rr, c, vis))
    lods = sorted((it for it in items if it[0].is_lod), key=lambda it: (-it[3], it[0].sid))
    hds = sorted((it for it in items if not it[0].is_lod), key=lambda it: (-it[3], it[0].sid))
    for r, rr, _c, _v in lods:
        p.rect(*rr, fill=_LOD_FILL, outline=_LOD_LINE)
    for r, rr, _c, _v in hds:
        a = r.aabb
        m2 = max(0.0, a[3] - a[0]) * max(0.0, a[4] - a[1])
        fa, la = next((f, o) for lim, f, o in _ALPHA_BY_SIZE if m2 <= lim)
        rgb = _INT_RGB if r.area else _HD_RGB
        p.rect(*rr, fill=(*rgb, fa), outline=(*rgb, la))

    # ---- zones
    here: list[str] = []
    for name, _label, zx0, zy0, zx1, zy1 in zones:
        if zx0 <= x <= zx1 and zy0 <= y <= zy1:
            here.append(str(name))
        r = rect_of((zx0, zy0, 0.0, zx1, zy1, 0.0))
        p.rect(*r, outline=_ZONE_LINE, width=2)
        c = clipped(r)
        text = str(name).upper()
        tx, ty = max(c[0] + 4, left_gutter), max(c[1] + 4, 14)
        tw, th = text_size(text)
        if tx + tw < c[2] and ty + th < c[3]:
            p.text(tx, ty, text, _ZONE_TEXT, 1)

    # ---- numbered labels: the largest visible objects, kept apart
    legend: list[list] = []
    if labels:
        big = _BIG_SHARE * span * span

        def is_big(r) -> int:
            a = r.aabb
            return int((a[3] - a[0]) * (a[4] - a[1]) > big)

        pool = sorted(hds if hds else lods, key=lambda it: (is_big(it[0]), -it[3], it[0].sid))
        scale = 2 if px >= 512 else 1
        gx_, gy_ = _LABEL_GAP[0] * scale, _LABEL_GAP[1] * scale
        centres: list[tuple[float, float]] = []
        for r, _rr, c, vis in pool:
            if vis <= 0:
                continue
            cx, cy = (c[0] + c[2]) / 2.0, (c[1] + c[3]) / 2.0
            if any(abs(cx - ox) < gx_ and abs(cy - oy) < gy_ for ox, oy in centres):
                continue
            centres.append((cx, cy))
            legend.append([len(legend) + 1, r.sid, r.name])
            if len(legend) >= labels:
                break
        for (n, _sid, _name), (cx, cy) in zip(legend, centres):
            p.badge(int(cx), int(cy), str(n), scale=scale, fg=_BADGE_FG, bg=_BADGE_BG, border=_BADGE_BORDER,
                    pad=1, center=True)

    # Coordinates stay readable over footprints and zone outlines as well as zone names.
    for tx, ty, label in grid_labels:
        tw, th = text_size(label)
        p.rect(tx - 1, ty - 1, tx + tw + 1, ty + th + 1, fill=_BG)
        p.text(tx, ty, label, _GRID_TEXT, 1)

    # ---- scale bar (bottom left) and north mark (top right)
    bar_m = nice_step(span, 6.0)
    bar_px = max(1, int(round(bar_m / mpp)))
    bx, by = 12, px - 14
    p.rect(bx - 4, by - 18, bx + bar_px + 6, by + 8, fill=(0, 0, 0, 150))
    p.rect(bx, by, bx + bar_px, by + 4, fill=_WHITE)
    p.rect(bx, by - 4, bx + 2, by + 4, fill=_WHITE)
    p.rect(bx + bar_px - 2, by - 4, bx + bar_px, by + 4, fill=_WHITE)
    p.text(bx, by - 14, f"{_fmt_coord(bar_m)} m", _WHITE, 1)
    nx = px - 26  # below the row of grid labels along the top edge
    p.rect(nx - 4, 16, nx + 18, 50, fill=(0, 0, 0, 150))
    p.text(nx + 2, 19, "^", _WHITE, 2)
    p.text(nx + 2, 34, "N", _WHITE, 2)

    _png.write_file(path, p.png())
    n_lod = sum(1 for it in items if it[0].is_lod)
    out = {"file": jpath(path), "legend": legend, "m_per_px": round(mpp, 4),
           "view": [round(v, 2) for v in view],
           "counts": {"inst": len(items) - n_lod, "lod": n_lod, "zone": len(zones)}}
    if "zone" in layers:
        out["zones"] = here
    side_data = dict(out, key=key, request_key=request_key)
    side_data.pop("file")
    _png.write_file(side, json.dumps(side_data, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    _prune_request(side, request_key)
    return out


def map_image(x: float, y: float, span: float = 300, px: int = 768, layers: Sequence[str] = ("inst",),
              area: int = 0, labels: int = 20, profile: str = "vanilla") -> tuple[Path, list[list], float]:
    """Top-down map around ``(x, y)``: ``(png path, legend [[n, sid, name]], m_per_px)`` (SPEC §4.4).

    Args:
        x, y: world centre.
        span: width = height of the view in metres.
        px: image side in pixels.
        layers: any of ``inst``, ``lod``, ``zone``.
        area: interior to show (0 = outdoors, -1 = all).
        labels: how many of the largest objects get numbers (0..99).
        profile: index profile.
    """
    r = render_map(x, y, span, px, layers, area, labels, profile)
    return Path(r["file"]), r["legend"], r["m_per_px"]
