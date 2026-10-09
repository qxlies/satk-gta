"""Does a map model's LOD keep its HD model's silhouette? (``asset.check`` row ``lod.silhouette``)

The HD model and its LOD are rasterised from the side, from the front and from above (orthographic, one grid
over both models). ``coverage`` = the share of the HD outline the LOD covers, ``spill`` = the LOD outside the HD
outline as a share of the HD outline. A LOD that lost its walls (slabs left of a decimated box) covers little; a
LOD made of boxes spills a little. numpy only.
"""

from __future__ import annotations

__all__ = ["VIEWS", "silhouette", "compare"]

#: Projections: the axis each one drops (side = drop x, front = drop y, top = drop z).
VIEWS = {"side": 0, "front": 1, "top": 2}


def _tris(parts: list[dict]):
    import numpy as np

    out = [p["pos"][p["tris"]] for p in parts if len(p["tris"])]
    return np.concatenate(out) if out else np.zeros((0, 3, 3))


def silhouette(T, drop: int, lo, hi, px: float):
    """Boolean mask of triangles ``T`` (``(m, 3, 3)``) projected along axis ``drop`` over the box, square pixels of
    ``px`` metres."""
    import numpy as np

    keep = [a for a in range(3) if a != drop]
    lo2, hi2 = np.asarray(lo)[keep], np.asarray(hi)[keep]
    nx, ny = (max(1, int(np.ceil((hi2[i] - lo2[i]) / px)) + 1) for i in (0, 1))
    mask = np.zeros((nx, ny), dtype=bool)
    if not len(T):
        return mask
    P = (T[:, :, keep] - lo2) / px                           # pixel units
    cx = np.arange(max(nx, ny)) + 0.5
    for tri in P:
        x0, y0 = np.floor(tri.min(axis=0)).astype(int)
        x1, y1 = np.ceil(tri.max(axis=0)).astype(int)
        x0, y0, x1, y1 = max(x0, 0), max(y0, 0), min(x1, nx), min(y1, ny)
        if x1 <= x0 or y1 <= y0:
            continue
        X, Y = np.meshgrid(cx[x0:x1], cx[y0:y1], indexing="ij")
        (ax, ay), (bx, by), (qx, qy) = tri
        d = (by - qy) * (ax - qx) + (qx - bx) * (ay - qy)
        if abs(d) < 1e-12:
            # an edge-on triangle: its line still covers pixels (a wall seen side-on)
            for t in np.linspace(0.0, 1.0, 8):
                for (sx, sy), (ex, ey) in (((ax, ay), (bx, by)), ((bx, by), (qx, qy)), ((qx, qy), (ax, ay))):
                    ix, iy = int(sx + (ex - sx) * t), int(sy + (ey - sy) * t)
                    if 0 <= ix < nx and 0 <= iy < ny:
                        mask[ix, iy] = True
            continue
        l1 = ((by - qy) * (X - qx) + (qx - bx) * (Y - qy)) / d
        l2 = ((qy - ay) * (X - qx) + (ax - qx) * (Y - qy)) / d
        inside = (l1 >= -1e-6) & (l2 >= -1e-6) & (l1 + l2 <= 1 + 1e-6)
        mask[x0:x1, y0:y1] |= inside
    return mask


def compare(hd_parts: list[dict], lod_parts: list[dict], n: int = 96, min_px: int = 60) -> dict:
    """``{"coverage", "spill", "views": {view: [coverage, spill]}, "worst"}`` of a LOD against its HD model (``n``
    pixels along the longest side; a view where the HD outline has fewer than ``min_px`` pixels - a flat road seen
    from the side - is left out)."""
    import numpy as np

    A, B = _tris(hd_parts), _tris(lod_parts)
    if not len(A) or not len(B):
        return {"coverage": 0.0 if len(A) else 1.0, "spill": 0.0, "views": {}, "worst": None}
    allp = np.concatenate([A.reshape(-1, 3), B.reshape(-1, 3)])
    lo, hi = allp.min(axis=0), allp.max(axis=0)
    px = float(max(hi - lo)) / n or 1.0
    views: dict = {}
    for name, drop in VIEWS.items():
        ma, mb = silhouette(A, drop, lo, hi, px), silhouette(B, drop, lo, hi, px)
        na = int(ma.sum())
        if na < min_px:
            continue
        views[name] = [round(float((ma & mb).sum()) / na, 3), round(float((mb & ~ma).sum()) / na, 3)]
    if not views:
        return {"coverage": 1.0, "spill": 0.0, "views": {}, "worst": None}
    worst = min(views, key=lambda k: views[k][0])
    return {"coverage": views[worst][0], "spill": max(v[1] for v in views.values()), "views": views, "worst": worst}
