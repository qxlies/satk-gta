"""Outer convex hull with a face budget (``satk.colgen.hull``).

The hull is built from *support planes* of the point set: for a direction ``n`` the plane
``n . x = max(n . p)`` touches the points and has all of them behind it, so any intersection of such
half-spaces contains every point (the render mesh is always inside, exactly - no inner simplification).

Candidate directions are the face normals of the render mesh (an exact hull of a low-poly prop is made of
them), a Fibonacci sphere and the six axes. Starting from the bounding box, the candidate whose plane
cuts the deepest remaining corner is clipped off next (greedy Hausdorff), until the corners are within
``tol`` of the true hull or the next cut would exceed the triangle budget. ``max_dev`` reports how far
the result may stick out of the exact hull.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ._np import np

__all__ = ["HullResult", "outer_hull", "fibonacci_dirs", "Polytope"]

_EPS = 1e-7


def fibonacci_dirs(n: int) -> np.ndarray:
    """``n`` nearly uniform unit directions (deterministic)."""
    i = np.arange(n, dtype=np.float64) + 0.5
    phi = np.arccos(1.0 - 2.0 * i / n)
    theta = math.pi * (1.0 + 5.0 ** 0.5) * i
    return np.stack([np.cos(theta) * np.sin(phi), np.sin(theta) * np.sin(phi), np.cos(phi)], axis=1)


class Polytope:
    """A convex polytope as planar faces (vertex loops, counter-clockwise seen from outside)."""

    def __init__(self, lo: np.ndarray, hi: np.ndarray):
        x0, y0, z0 = (float(v) for v in lo)
        x1, y1, z1 = (float(v) for v in hi)
        self.v: list[tuple[float, float, float]] = [
            (x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
            (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
        # (normal, loop) with loops counter-clockwise around the outward normal
        self.faces: list[tuple[tuple[float, float, float], list[int]]] = [
            ((0.0, 0.0, -1.0), [0, 3, 2, 1]), ((0.0, 0.0, 1.0), [4, 5, 6, 7]),
            ((0.0, -1.0, 0.0), [0, 1, 5, 4]), ((0.0, 1.0, 0.0), [2, 3, 7, 6]),
            ((-1.0, 0.0, 0.0), [0, 4, 7, 3]), ((1.0, 0.0, 0.0), [1, 2, 6, 5])]

    def vertices(self) -> np.ndarray:
        used = sorted({i for _n, loop in self.faces for i in loop})
        return np.asarray([self.v[i] for i in used], dtype=np.float64)

    def triangles(self) -> int:
        return sum(len(loop) - 2 for _n, loop in self.faces)

    def copy(self) -> "Polytope":
        p = Polytope.__new__(Polytope)
        p.v = list(self.v)
        p.faces = [(n, list(loop)) for n, loop in self.faces]
        return p

    def clip(self, n: tuple[float, float, float], d: float) -> bool:
        """Keep the part with ``n . x <= d``; ``False`` when nothing was cut."""
        nx, ny, nz = n
        side = {}
        for _fn, loop in self.faces:
            for i in loop:
                if i not in side:
                    x, y, z = self.v[i]
                    side[i] = nx * x + ny * y + nz * z - d
        scale = max(1e-9, max(abs(c) for p in self.v for c in p))
        eps = 1e-9 * scale
        if all(s <= eps for s in side.values()):
            return False
        cut: dict[tuple[int, int], int] = {}

        def point_on(a: int, b: int) -> int:
            key = (a, b) if a < b else (b, a)
            hit = cut.get(key)
            if hit is None:
                sa, sb = side[a], side[b]
                t = sa / (sa - sb)
                pa, pb = self.v[a], self.v[b]
                self.v.append((pa[0] + t * (pb[0] - pa[0]), pa[1] + t * (pb[1] - pa[1]), pa[2] + t * (pb[2] - pa[2])))
                hit = len(self.v) - 1
                cut[key] = hit
            return hit

        faces = []
        cap: list[int] = []
        for fn, loop in self.faces:
            out: list[int] = []
            k = len(loop)
            for j in range(k):
                a, b = loop[j], loop[(j + 1) % k]
                sa, sb = side[a], side[b]
                if sa <= eps:
                    out.append(a)
                    if abs(sa) <= eps:
                        cap.append(a)
                if (sa < -eps and sb > eps) or (sa > eps and sb < -eps):
                    c = point_on(a, b)
                    out.append(c)
                    cap.append(c)
            # drop repeated neighbours
            clean = [x for j, x in enumerate(out) if x != out[j - 1]] if len(out) > 1 else out
            if len(clean) >= 3:
                faces.append((fn, clean))
        cap = list(dict.fromkeys(cap))
        if len(cap) >= 3:
            pts = np.asarray([self.v[i] for i in cap])
            c = pts.mean(axis=0)
            nvec = np.asarray(n, dtype=np.float64)
            u = pts[0] - c
            u = u - nvec * (u @ nvec)
            if np.linalg.norm(u) < 1e-12:
                u = np.cross(nvec, [1.0, 0.0, 0.0] if abs(nvec[0]) < 0.9 else [0.0, 1.0, 0.0])
            u /= np.linalg.norm(u)
            w = np.cross(nvec, u)
            ang = np.arctan2((pts - c) @ w, (pts - c) @ u)
            order = [cap[i] for i in np.argsort(ang, kind="stable")]
            faces.append((n, order))
        self.faces = faces
        return True


@dataclass
class HullResult:
    P: np.ndarray            # (n, 3) vertices
    T: np.ndarray            # (m, 3) triangles, counter-clockwise seen from outside
    planes: int
    max_dev: float           # how far a corner may stick out of the exact hull (metres)
    exact: bool


def _candidates(P: np.ndarray, normals: np.ndarray | None, extra: int) -> np.ndarray:
    dirs = [np.eye(3), -np.eye(3), fibonacci_dirs(extra)]
    if normals is not None and len(normals):
        ln = np.linalg.norm(normals, axis=1)
        nn = normals[ln > 1e-12] / ln[ln > 1e-12, None]
        dirs.append(nn)
    D = np.concatenate(dirs)
    q = np.round(D * 2000.0).astype(np.int64)                 # merge directions within ~0.03 deg
    _u, first = np.unique(q, axis=0, return_index=True)
    return D[np.sort(first)]


def outer_hull(P: np.ndarray, max_faces: int, *, normals: np.ndarray | None = None, tol: float = 0.004,
               extra_dirs: int = 256, min_size: float = 0.02) -> HullResult:
    """Support-plane hull of ``P`` with at most ``max_faces`` triangles (see the module doc).

    Args:
        P: points (n, 3).
        max_faces: triangle budget (>= 12: the bounding box).
        normals: render face normals (candidate directions; exact hulls of low-poly props).
        tol: stop when no corner sticks out of the exact hull by more than this (metres).
        extra_dirs: Fibonacci directions added to the candidates.
        min_size: flat point sets get this minimum thickness.
    """
    flat = P.max(axis=0) - P.min(axis=0) < min_size
    if flat.any():                                            # thicken flat sets along their flat axes
        off = np.where(flat, min_size / 2, 0.0)
        P = np.concatenate([P - off, P + off])
    lo, hi = P.min(axis=0), P.max(axis=0)
    D = _candidates(P, normals, extra_dirs)
    S = (P @ D.T).max(axis=0)                                 # support distance per direction
    poly = Polytope(lo, hi)
    used = 0
    max_dev = 0.0
    budget = max(12, int(max_faces))
    while True:
        V = poly.vertices()
        viol = (V @ D.T).max(axis=0) - S
        j = int(np.argmax(viol))
        max_dev = float(max(0.0, viol[j]))
        if max_dev <= tol:
            break
        trial = poly.copy()
        if not trial.clip(tuple(float(x) for x in D[j]), float(S[j])):
            S[j] = np.inf                                    # numerically nothing to cut
            continue
        if trial.triangles() > budget:
            break
        poly = trial
        used += 1
    Pv, Tv = _triangulate(poly)
    return HullResult(Pv, Tv, used + 6, max_dev, max_dev <= tol)


def _triangulate(poly: Polytope) -> tuple[np.ndarray, np.ndarray]:
    used = sorted({i for _n, loop in poly.faces for i in loop})
    remap = {old: k for k, old in enumerate(used)}
    P = np.asarray([poly.v[i] for i in used], dtype=np.float64)
    tris = []
    for _n, loop in poly.faces:
        ids = [remap[i] for i in loop]
        for k in range(1, len(ids) - 1):
            tris.append((ids[0], ids[k], ids[k + 1]))
    return P, np.asarray(tris, dtype=np.int64).reshape(-1, 3)
