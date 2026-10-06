"""Solid voxels of a render mesh and what is built from them (``satk.colgen.voxel``).

* :func:`voxelize` marks the cells touched by the triangles (dense barycentric samples, spacing <= half a
  cell), floods the outside from the padded border and returns the solid = everything not reached (a
  closed shell becomes a filled body; an open sheet stays one cell thick);
* :func:`boxes` decomposes the solid into axis-aligned boxes (greedy maximal boxes, then cheapest pair
  merges down to the budget), finally shrunk onto the surface samples each box holds - every sample stays
  inside a box, so the boxes contain the render mesh;
* :func:`spheres` covers the solid with inscribed spheres (greedy maximum coverage, mirrored pairs for
  left/right symmetric models such as vehicles);
* :func:`surface` returns the closed boundary of the solid as triangles (two per exposed cell face,
  counter-clockwise seen from outside), smoothed with Taubin's lambda/mu filter.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ._np import np

__all__ = ["Voxels", "voxelize", "boxes", "spheres", "surface", "sample_triangles"]

_MAX_SAMPLES = 4_000_000


@dataclass
class Voxels:
    solid: np.ndarray          # (nx, ny, nz) bool
    surf: np.ndarray           # cells touched by triangles
    origin: np.ndarray         # world position of cell (0, 0, 0)'s minimum corner
    cell: float
    samples: np.ndarray        # (k, 3) surface sample points (render vertices included)
    outside: np.ndarray | None = None   # cells reached from the border (the complement of solid)
    sample_cells: np.ndarray | None = None   # (k, 3) cell of each sample

    def outer_samples(self) -> np.ndarray:
        """Samples in cells next to the outside: the outer skin (a car's seats are not part of it)."""
        if self.outside is None or self.sample_cells is None:
            return self.samples
        outer = self.surf & _dilate6(self.outside)
        c = self.sample_cells
        return self.samples[outer[c[:, 0], c[:, 1], c[:, 2]]]

    def centres(self, mask: np.ndarray) -> np.ndarray:
        return self.origin + (np.argwhere(mask) + 0.5) * self.cell


def sample_triangles(P: np.ndarray, T: np.ndarray, spacing: float) -> np.ndarray:
    """Points on every triangle with at most ``spacing`` between neighbours, plus the vertices."""
    a, b, c = P[T[:, 0]], P[T[:, 1]], P[T[:, 2]]
    longest = np.maximum(np.maximum(np.linalg.norm(b - a, axis=1), np.linalg.norm(c - b, axis=1)),
                         np.linalg.norm(a - c, axis=1))
    n = np.maximum(1, np.ceil(longest / spacing)).astype(np.int64)
    total = int(((n + 1) * (n + 2) // 2).sum())
    if total > _MAX_SAMPLES:                        # keep memory bounded: coarser spacing
        f = math.sqrt(total / _MAX_SAMPLES)
        n = np.maximum(1, np.ceil(longest / (spacing * f))).astype(np.int64)
    out = [P]
    for k in np.unique(n):
        sel = np.nonzero(n == k)[0]
        i, j = np.meshgrid(np.arange(k + 1), np.arange(k + 1), indexing="ij")
        keep = (i + j) <= k
        u = (i[keep] / k)[None, :, None]
        v = (j[keep] / k)[None, :, None]
        pts = a[sel, None, :] + u * (b[sel] - a[sel])[:, None, :] + v * (c[sel] - a[sel])[:, None, :]
        out.append(pts.reshape(-1, 3))
    return np.concatenate(out)


def _dilate6(m: np.ndarray) -> np.ndarray:
    d = m.copy()
    d[1:] |= m[:-1]
    d[:-1] |= m[1:]
    d[:, 1:] |= m[:, :-1]
    d[:, :-1] |= m[:, 1:]
    d[:, :, 1:] |= m[:, :, :-1]
    d[:, :, :-1] |= m[:, :, 1:]
    return d


def voxelize(P: np.ndarray, T: np.ndarray, *, cells: int = 40, cell: float | None = None) -> Voxels:
    """Solid voxels of ``(P, T)`` with about ``cells`` cells along the longest axis (or a fixed ``cell``)."""
    lo, hi = P.min(axis=0), P.max(axis=0)
    ext = float((hi - lo).max())
    size = cell or max(ext / max(cells, 2), 0.01)
    origin = lo - size
    dims = np.maximum(np.ceil((hi - lo) / size).astype(np.int64) + 3, 3)
    S = sample_triangles(P, T, size * 0.5)
    idx = np.clip(np.floor((S - origin) / size).astype(np.int64), 0, dims - 1)
    surf = np.zeros(tuple(dims), dtype=bool)
    surf[idx[:, 0], idx[:, 1], idx[:, 2]] = True
    ext_m = np.zeros_like(surf)
    ext_m[0, :, :] = ext_m[-1, :, :] = True
    ext_m[:, 0, :] = ext_m[:, -1, :] = True
    ext_m[:, :, 0] = ext_m[:, :, -1] = True
    ext_m &= ~surf
    free = ~surf
    while True:
        nxt = _dilate6(ext_m) & free
        if (nxt == ext_m).all():
            break
        ext_m = nxt
    return Voxels(~ext_m, surf, origin, size, S, ext_m, idx)


# ----------------------------------------------------------------------------- boxes
def _greedy_boxes(solid: np.ndarray) -> list[list[int]]:
    """Maximal boxes in scan order: ``[x0, y0, z0, x1, y1, z1]`` (inclusive cell ranges)."""
    left = solid.copy()
    out = []
    nx, ny, nz = left.shape
    for x, y, z in np.argwhere(solid):
        if not left[x, y, z]:
            continue
        x1 = x
        while x1 + 1 < nx and left[x1 + 1, y, z]:
            x1 += 1
        y1 = y
        while y1 + 1 < ny and left[x:x1 + 1, y1 + 1, z].all():
            y1 += 1
        z1 = z
        while z1 + 1 < nz and left[x:x1 + 1, y:y1 + 1, z1 + 1].all():
            z1 += 1
        left[x:x1 + 1, y:y1 + 1, z:z1 + 1] = False
        out.append([int(x), int(y), int(z), int(x1), int(y1), int(z1)])
    return out


def _pair_down(bx: np.ndarray) -> np.ndarray:
    """Halve a long box list cheaply: merge neighbours in Z-order (Morton) of their centres."""
    c = (bx[:, :3] + bx[:, 3:]) / 2
    q = np.clip(((c - c.min(axis=0)) / max(float(np.ptp(c, axis=0).max()), 1e-9) * 1023).astype(np.int64), 0, 1023)
    code = np.zeros(len(bx), dtype=np.int64)
    for bit in range(10):
        for ax in range(3):
            code |= ((q[:, ax] >> bit) & 1) << (3 * bit + ax)
    o = bx[np.argsort(code, kind="stable")]
    n2 = len(o) // 2
    a, b = o[0:2 * n2:2], o[1:2 * n2:2]
    merged = np.concatenate([np.minimum(a[:, :3], b[:, :3]), np.maximum(a[:, 3:], b[:, 3:])], axis=1)
    return np.concatenate([merged, o[2 * n2:]]) if len(o) % 2 else merged


def _merge_to(bx: np.ndarray, budget: int) -> np.ndarray:
    """Merge the pair whose bounding box adds the least volume until ``len <= budget``."""
    bx = bx.astype(np.float64)
    while len(bx) > max(budget, 400):
        bx = _pair_down(bx)
    n = len(bx)
    if n <= budget:
        return bx
    lo, hi = bx[:, :3].copy(), bx[:, 3:].copy()
    vol = np.prod(hi - lo, axis=1)

    def row(i: int) -> np.ndarray:
        u = np.prod(np.maximum(hi, hi[i]) - np.minimum(lo, lo[i]), axis=1)
        r = u - vol - vol[i]
        r[i] = np.inf
        return r

    cost = np.stack([row(i) for i in range(n)])
    alive = n
    while alive > budget:
        i, j = np.unravel_index(int(np.argmin(cost)), cost.shape)
        if i > j:
            i, j = j, i
        lo[i] = np.minimum(lo[i], lo[j])
        hi[i] = np.maximum(hi[i], hi[j])
        vol[i] = float(np.prod(hi[i] - lo[i]))
        cost[j, :] = np.inf
        cost[:, j] = np.inf
        lo[j] = np.inf                                      # dead slot: never the cheapest
        hi[j] = -np.inf
        r = row(i)
        dead = np.isinf(cost[:, i]) & (np.arange(n) != i)
        r[dead] = np.inf
        cost[i, :] = r
        cost[:, i] = r
        alive -= 1
    keep = np.isfinite(lo).all(axis=1)
    return np.concatenate([lo[keep], hi[keep]], axis=1)


def boxes(vx: Voxels, budget: int) -> np.ndarray:
    """``(k, 6)`` boxes ``min3, max3`` in model space covering the solid and every surface sample.

    Voxel boxes overshoot the surface by up to a cell; a side with no solid cell beyond it (an outer side)
    is moved in to the farthest surface sample inside the box, so the result hugs the mesh while every
    sample stays covered.
    """
    raw = _greedy_boxes(vx.solid)
    if not raw:
        return np.zeros((0, 6))
    cb = np.asarray(raw, dtype=np.float64)
    cells = _merge_to(np.concatenate([cb[:, :3], cb[:, 3:] + 1], axis=1), max(1, budget))
    S = vx.samples
    solid = vx.solid
    dims = np.asarray(solid.shape)
    out = []
    for b in cells:
        lo_c, hi_c = b[:3].astype(np.int64), b[3:].astype(np.int64)        # cells lo_c .. hi_c - 1
        lo = vx.origin + lo_c * vx.cell
        hi = vx.origin + hi_c * vx.cell
        inside = ((S >= lo - 1e-9) & (S <= hi + 1e-9)).all(axis=1)
        if inside.any():
            smin, smax = S[inside].min(axis=0), S[inside].max(axis=0)
            for ax in range(3):
                sl = [slice(lo_c[k], hi_c[k]) for k in range(3)]
                if lo_c[ax] > 0:
                    sl[ax] = slice(lo_c[ax] - 1, lo_c[ax])
                    if not solid[tuple(sl)].any():
                        lo[ax] = max(lo[ax], smin[ax])
                sl = [slice(lo_c[k], hi_c[k]) for k in range(3)]
                if hi_c[ax] < dims[ax]:
                    sl[ax] = slice(hi_c[ax], hi_c[ax] + 1)
                    if not solid[tuple(sl)].any():
                        hi[ax] = min(hi[ax], smax[ax])
        out.append(np.concatenate([lo, hi]))
    return np.asarray(out).reshape(-1, 6)


# ----------------------------------------------------------------------------- spheres
def spheres(vx: Voxels, budget: int, *, coverage: float = 0.97, mirror_x: bool = False,
            min_radius: float | None = None) -> np.ndarray:
    """``(k, 4)`` spheres ``x, y, z, r`` covering the solid (see the module doc)."""
    solid = vx.solid
    C = vx.centres(solid)
    if not len(C):
        return np.zeros((0, 4))
    edge = vx.centres(_dilate6(solid) & ~solid)  # outside cells next to the solid
    r_min = min_radius if min_radius is not None else vx.cell * 0.5
    # depth of each solid cell (distance to the outside cells, chunked brute force) ranks the candidates
    dist = np.empty(len(C))
    step = max(1, 2_000_000 // max(len(edge), 1))
    for s in range(0, len(C), step):
        d2 = ((C[s:s + step, None, :] - edge[None, :, :]) ** 2).sum(axis=2)
        dist[s:s + step] = np.sqrt(d2.min(axis=1))
    order = np.argsort(-dist, kind="stable")
    if len(order) > 1500:                        # the deepest cells and an even spread (ends and corners)
        spread = np.arange(0, len(C), int(math.ceil(len(C) / 750)))
        cand = np.unique(np.concatenate([order[:750], spread]))
    else:
        cand = order
    # radius: reach the boundary of the solid (outside cell centres lie half a cell beyond it); what is
    # inside was decided by the flood fill, so interior geometry (a car's seats) does not shrink spheres
    rad = np.maximum(dist - 0.5 * vx.cell, r_min)
    pts_idx = np.arange(len(C))
    if len(C) > 6000:                            # coverage measured on a regular subsample
        pts_idx = pts_idx[:: int(math.ceil(len(C) / 6000))]
    Pc = C[pts_idx]
    cov = ((C[cand, None, :] - Pc[None, :, :]) ** 2).sum(axis=2) <= (rad[cand, None] + 1e-9) ** 2
    covered = np.zeros(len(Pc), dtype=bool)
    out: list[tuple[float, float, float, float]] = []
    while len(out) < budget and covered.mean() < coverage:
        gain = (cov & ~covered).sum(axis=1)
        k = int(np.argmax(gain))
        if gain[k] == 0:
            break
        x, y, z = (float(v) for v in C[cand[k]])
        paired = mirror_x and abs(x) > vx.cell / 2
        if paired and len(out) + 2 > budget:
            cov[k] = False                                  # no room for its mirror: try a centre one
            continue
        r = float(rad[cand[k]])
        out.append((x, y, z, r))
        covered |= cov[k]
        cov[k] = False
        if paired:
            # the exact mirror image; its radius is limited by the depth at the mirrored place
            j = int(np.argmin(((C - (-x, y, z)) ** 2).sum(axis=1)))
            rm = min(r, float(max(dist[j] - 0.5 * vx.cell, r_min)))
            out.append((-x, y, z, rm))
            covered |= ((Pc - (-x, y, z)) ** 2).sum(axis=1) <= rm * rm + 1e-9
    return np.asarray(out, dtype=np.float64).reshape(-1, 4)


# ----------------------------------------------------------------------------- closed surface
_FACES = (  # (axis, direction, corner offsets of the quad, counter-clockwise from outside)
    (0, -1, ((0, 0, 0), (0, 0, 1), (0, 1, 1), (0, 1, 0))),
    (0, 1, ((1, 0, 0), (1, 1, 0), (1, 1, 1), (1, 0, 1))),
    (1, -1, ((0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1))),
    (1, 1, ((0, 1, 0), (0, 1, 1), (1, 1, 1), (1, 1, 0))),
    (2, -1, ((0, 0, 0), (0, 1, 0), (1, 1, 0), (1, 0, 0))),
    (2, 1, ((0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1))),
)


def surface(vx: Voxels, *, smooth: int = 4) -> tuple[np.ndarray, np.ndarray]:
    """Closed boundary of the solid: ``(P, T)``, counter-clockwise seen from outside."""
    solid = vx.solid
    pad = np.pad(solid, 1)
    quads = []
    for axis, dirn, corners in _FACES:
        nb = np.roll(pad, -dirn, axis=axis)[1:-1, 1:-1, 1:-1]
        cells = np.argwhere(solid & ~nb)
        if not len(cells):
            continue
        q = np.stack([cells + np.asarray(c) for c in corners], axis=1)       # (k, 4, 3) grid corners
        quads.append(q)
    if not quads:
        return np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int64)
    Q = np.concatenate(quads)
    corners, inv = np.unique(Q.reshape(-1, 3), axis=0, return_inverse=True)
    inv = inv.reshape(-1, 4)
    T = np.concatenate([inv[:, [0, 1, 2]], inv[:, [0, 2, 3]]])
    P = vx.origin + corners.astype(np.float64) * vx.cell
    if smooth:
        P = _taubin(P, T, smooth)
    return P, T


def _taubin(P: np.ndarray, T: np.ndarray, it: int, lam: float = 0.5, mu: float = -0.53) -> np.ndarray:
    e = np.concatenate([T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]])
    e = np.unique(np.sort(e, axis=1), axis=0)
    deg = np.bincount(e.ravel(), minlength=len(P)).astype(np.float64)
    deg = np.maximum(deg, 1)
    for _ in range(it):
        for f in (lam, mu):
            acc = np.zeros_like(P)
            np.add.at(acc, e[:, 0], P[e[:, 1]])
            np.add.at(acc, e[:, 1], P[e[:, 0]])
            P = P + f * (acc / deg[:, None] - P)
    return P
