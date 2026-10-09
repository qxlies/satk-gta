"""Silhouettes of a true-view reference photo and of a model (``look.silhouette``, ``blender preview --ref``).

numpy only (imported by satk and inside Blender; no Pillow here). A photo's object is found against its border
colour: the median colour of the image border is the background, pixels further than ``tol`` from it are the
object, rows and columns with fewer than ``min_share`` object pixels are noise. The comparison aligns the two
silhouettes by their bounding boxes (length to length, ground to ground) and reports the overlap (intersection over
union) and, at stations along the length, the top line of each in metres: information for a critic, never a gate.
Only true elevations (side, front, rear from far away) can be compared this way; a three-quarter photo cannot.

Example::

    import numpy as np
    from satk.look import silhouette as S
    box = S.photo_box(rgb)                     # (x0, y0, x1, y1) of the object in an (h, w, 3) uint8 photo
    rep = S.compare(model_mask, S.photo_mask(rgb), length_m=4.6, height_m=1.5, stations=20)
"""

from __future__ import annotations

__all__ = ["photo_mask", "mask_box", "photo_box", "compare", "VIEWS"]

#: Views a photo can be compared in (true elevations only).
VIEWS = ("side", "front", "rear")


def _np():
    import numpy as np

    return np


def photo_mask(rgb, tol: float = 40.0):
    """Boolean ``(h, w)`` mask of the object of an ``(h, w, 3|4)`` photo (0..255) against its border colour."""
    np = _np()
    a = np.asarray(rgb, dtype=np.float32)[..., :3]
    h, w = a.shape[:2]
    b = max(1, min(h, w) // 50)
    border = np.concatenate([a[:b].reshape(-1, 3), a[-b:].reshape(-1, 3), a[:, :b].reshape(-1, 3),
                             a[:, -b:].reshape(-1, 3)])
    bg = np.median(border, axis=0)
    d = np.sqrt(((a - bg) ** 2).sum(axis=-1))
    return d > float(tol)


def mask_box(mask, min_share: float = 0.02) -> tuple[int, int, int, int] | None:
    """``(x0, y0, x1, y1)`` (x1, y1 exclusive) of the rows and columns that hold at least ``min_share`` of the mask's
    pixels of that row/column direction; ``None`` for an empty mask."""
    np = _np()
    m = np.asarray(mask, dtype=bool)
    if not m.any():
        return None
    h, w = m.shape
    cols = np.flatnonzero(m.sum(axis=0) >= max(1, min_share * h))
    rows = np.flatnonzero(m.sum(axis=1) >= max(1, min_share * w))
    if not len(cols) or not len(rows):
        return None
    return int(cols[0]), int(rows[0]), int(cols[-1]) + 1, int(rows[-1]) + 1


def photo_box(rgb, tol: float = 40.0) -> tuple[int, int, int, int] | None:
    """Bounding box of the object of a photo (see :func:`photo_mask`), or ``None``."""
    return mask_box(photo_mask(rgb, tol))


def _crop_resize(mask, box, w: int, h: int):
    """The ``box`` of ``mask`` resampled (nearest) to ``(h, w)``."""
    np = _np()
    x0, y0, x1, y1 = box
    m = np.asarray(mask, dtype=bool)[y0:y1, x0:x1]
    ys = np.minimum((np.arange(h) + 0.5) * m.shape[0] / h, m.shape[0] - 1).astype(int)
    xs = np.minimum((np.arange(w) + 0.5) * m.shape[1] / w, m.shape[1] - 1).astype(int)
    return m[ys][:, xs]


def compare(model_mask, photo, *, length_m: float, height_m: float, stations: int = 20,
            photo_box_px=None, flip: bool = False) -> dict:
    """Overlap and top-line deviation of a model silhouette (``(h, w)`` bool, image rows top-down) and a photo
    silhouette (bool mask, same orientation; ``flip`` mirrors it left-right) aligned by their bounding boxes.

    Returns ``{"iou", "stations": [[t, model_top_m, photo_top_m, dev_m], ...], "worst": rows, "box_px"}``: ``t`` = 0
    at the left edge of the image .. 1 at the right edge; heights above the lowest point in metres (``height_m`` =
    the model's height)."""
    np = _np()
    mm = np.asarray(model_mask, dtype=bool)
    pm = np.asarray(photo, dtype=bool)
    if flip:
        pm = pm[:, ::-1]
    mb = mask_box(mm, 0.0)
    pb = tuple(photo_box_px) if photo_box_px is not None else mask_box(pm)
    if mb is None or pb is None:
        raise ValueError("an empty silhouette: no object found")
    if flip and photo_box_px is not None:
        w = pm.shape[1]
        pb = (w - pb[2], pb[1], w - pb[0], pb[3])
    W, H = mb[2] - mb[0], mb[3] - mb[1]
    M = mm[mb[1]:mb[3], mb[0]:mb[2]]
    P = _crop_resize(pm, pb, W, H)
    inter = float((M & P).sum())
    union = float((M | P).sum()) or 1.0
    rows = []
    for k in range(stations):
        c = min(W - 1, int((k + 0.5) * W / stations))
        mcol, pcol = np.flatnonzero(M[:, c]), np.flatnonzero(P[:, c])
        mt = (H - mcol[0]) / H * height_m if len(mcol) else 0.0
        pt = (H - pcol[0]) / H * height_m if len(pcol) else 0.0
        rows.append([round((k + 0.5) / stations, 3), round(mt, 3), round(pt, 3), round(pt - mt, 3)])
    worst = sorted(rows, key=lambda r: -abs(r[3]))[:3]
    return {"iou": round(inter / union, 3), "stations": rows, "worst": worst,
            "box_px": [int(v) for v in pb], "m_per_px": round(length_m / max(1, W), 5)}
