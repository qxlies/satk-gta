"""Nearest triangle queries in numpy (``satk.colgen.nearest``).

:func:`closest_on_tris` is the vectorised closest-point-on-triangle test (Ericson, *Real-Time Collision
Detection* 5.1.5); :func:`nearest_tri` answers "which triangle is closest to each query point" by
pre-selecting the ``k`` triangles with the nearest centroids (chunked, bounded memory) and testing them
exactly.
"""

from __future__ import annotations

from ._np import np

__all__ = ["closest_on_tris", "nearest_tri", "point_tri_dist", "winding", "outside_distance"]

_CHUNK_CELLS = 2_000_000          # query x triangle cells per centroid-distance chunk
_TOL = 1e-4                       # answers within 0.1 mm of the true distance are exact enough


def closest_on_tris(p: np.ndarray, a: np.ndarray, b: np.ndarray, c: np.ndarray) -> np.ndarray:
    """Closest points on triangles ``(a, b, c)`` to points ``p`` (all ``(..., 3)``, broadcast)."""
    p, a, b, c = np.broadcast_arrays(p, a, b, c)
    ab, ac, ap = b - a, c - a, p - a
    d1 = (ab * ap).sum(-1)
    d2 = (ac * ap).sum(-1)
    bp = p - b
    d3 = (ab * bp).sum(-1)
    d4 = (ac * bp).sum(-1)
    cp = p - c
    d5 = (ab * cp).sum(-1)
    d6 = (ac * cp).sum(-1)
    va = d3 * d6 - d5 * d4
    vb = d5 * d2 - d1 * d6
    vc = d1 * d4 - d3 * d2
    with np.errstate(divide="ignore", invalid="ignore"):
        den = va + vb + vc
        v = np.where(den != 0, vb / den, 0.0)
        w = np.where(den != 0, vc / den, 0.0)
        out = a + ab * v[..., None] + ac * w[..., None]                  # inside the face
        # edge regions
        t_ab = np.where(d1 - d3 != 0, d1 / (d1 - d3), 0.0)
        e_ab = (vc <= 0) & (d1 >= 0) & (d3 <= 0)
        out = np.where(e_ab[..., None], a + ab * t_ab[..., None], out)
        t_ac = np.where(d2 - d6 != 0, d2 / (d2 - d6), 0.0)
        e_ac = (vb <= 0) & (d2 >= 0) & (d6 <= 0)
        out = np.where(e_ac[..., None], a + ac * t_ac[..., None], out)
        den_bc = (d4 - d3) + (d5 - d6)
        t_bc = np.where(den_bc != 0, (d4 - d3) / den_bc, 0.0)
        e_bc = (va <= 0) & ((d4 - d3) >= 0) & ((d5 - d6) >= 0)
        out = np.where(e_bc[..., None], b + (c - b) * t_bc[..., None], out)
    # vertex regions
    out = np.where(((d1 <= 0) & (d2 <= 0))[..., None], a, out)
    out = np.where(((d3 >= 0) & (d4 <= d3))[..., None], b, out)
    out = np.where(((d6 >= 0) & (d5 <= d6))[..., None], c, out)
    return out


def point_tri_dist(p: np.ndarray, a: np.ndarray, b: np.ndarray, c: np.ndarray) -> np.ndarray:
    q = closest_on_tris(p, a, b, c)
    return np.sqrt(((np.broadcast_to(p, q.shape) - q) ** 2).sum(-1))


def nearest_tri(q: np.ndarray, P: np.ndarray, T: np.ndarray, *, k: int = 16) -> tuple[np.ndarray, np.ndarray]:
    """``(index, distance)`` of the nearest triangle of ``(P, T)`` for every point of ``q`` (exact to 0.1 mm).

    Each triangle gets a bounding sphere; ``|q - centre| - radius`` is a lower bound of the distance. The
    ``k`` triangles with the smallest bounds are tested exactly; a query whose next bound is still below
    its best distance is then tested against every triangle (rare: long thin triangles).
    """
    nq, nt = len(q), len(T)
    if nq == 0 or nt == 0:
        return np.zeros(nq, dtype=np.int64), np.full(nq, np.inf)
    A, B, C = P[T[:, 0]], P[T[:, 1]], P[T[:, 2]]
    cen = (A + B + C) / 3.0
    rad = np.sqrt(np.maximum(np.maximum(((A - cen) ** 2).sum(1), ((B - cen) ** 2).sum(1)), ((C - cen) ** 2).sum(1)))
    kk = min(k, nt)
    best_i = np.zeros(nq, dtype=np.int64)
    best_d = np.full(nq, np.inf)
    recheck = np.zeros(nq, dtype=bool)
    step = max(1, _CHUNK_CELLS // max(nt, 1))
    rows_all = np.arange(nq)
    for s in range(0, nq, step):
        qq = q[s:s + step]
        lb = np.sqrt(((qq[:, None, :] - cen[None, :, :]) ** 2).sum(-1)) - rad[None, :]
        if kk < nt:
            part = np.argpartition(lb, kk, axis=1)
            cand = part[:, :kk]
            nxt = lb[np.arange(len(qq)), part[:, kk]]
        else:
            cand = np.broadcast_to(np.arange(nt), (len(qq), nt))
            nxt = np.full(len(qq), np.inf)
        dist = point_tri_dist(qq[:, None, :], A[cand], B[cand], C[cand])
        j = dist.argmin(axis=1)
        rows = np.arange(len(qq))
        best_i[s:s + step] = cand[rows, j]
        best_d[s:s + step] = dist[rows, j]
        bd = dist[rows, j]
        recheck[s:s + step] = (nxt < bd - _TOL) & (bd > _TOL)       # a closer triangle is still possible
    redo = rows_all[recheck]                         # exact against every triangle whose bound may still win
    m = max(1, _CHUNK_CELLS // nt)
    for s in range(0, len(redo), m):
        ids = redo[s:s + m]
        lb = np.sqrt(((q[ids][:, None, :] - cen[None, :, :]) ** 2).sum(-1)) - rad[None, :]
        rr, tt = np.nonzero(lb < best_d[ids][:, None])
        if not len(rr):
            continue
        d = point_tri_dist(q[ids][rr], A[tt], B[tt], C[tt])
        order = np.lexsort((d, rr))
        rr, tt, d = rr[order], tt[order], d[order]
        first = np.ones(len(rr), dtype=bool)
        first[1:] = rr[1:] != rr[:-1]
        rows_u, tmin, dmin = rr[first], tt[first], d[first]
        better = dmin < best_d[ids[rows_u]]
        best_d[ids[rows_u[better]]] = dmin[better]
        best_i[ids[rows_u[better]]] = tmin[better]
    return best_i, best_d


def winding(q: np.ndarray, P: np.ndarray, T: np.ndarray) -> np.ndarray:
    """Generalised winding number of a triangle mesh (counter-clockwise = outward) at points ``q``:
    ~1 inside a closed mesh, ~0 outside (Jacobson et al. 2013, solid angles of Van Oosterom-Strackee)."""
    out = np.zeros(len(q))
    if not len(q) or not len(T):
        return out
    A, B, C = P[T[:, 0]], P[T[:, 1]], P[T[:, 2]]
    step = max(1, _CHUNK_CELLS // max(len(T), 1))
    for s in range(0, len(q), step):
        qq = q[s:s + step, None, :]
        a, b, c = A[None] - qq, B[None] - qq, C[None] - qq
        la, lb, lc = (np.linalg.norm(x, axis=2) for x in (a, b, c))
        num = (a * np.cross(b, c)).sum(axis=2)
        den = la * lb * lc + (a * b).sum(axis=2) * lc + (b * c).sum(axis=2) * la + (c * a).sum(axis=2) * lb
        out[s:s + step] = (2.0 * np.arctan2(num, den)).sum(axis=1) / (4.0 * np.pi)
    return out


def outside_distance(q: np.ndarray, P: np.ndarray, T: np.ndarray) -> float:
    """How far the farthest point of ``q`` lies outside the closed mesh ``(P, T)`` (0 = all inside or on it)."""
    if not len(q):
        return 0.0
    if not len(T):
        return float("inf")
    w = winding(q, P, T)
    out = w < 0.5
    if not out.any():
        return 0.0
    _i, d = nearest_tri(q[out], P, T)
    return float(d.max())
