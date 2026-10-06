"""Quadric edge-collapse decimation (Garland-Heckbert) for collision meshes (``satk.colgen.decimate``).

:func:`decimate` reduces a triangle mesh to a face budget:

* every vertex carries the area-weighted quadric of its faces' planes; an edge collapses to the point that
  minimises the summed quadric (or the best of its ends / middle when that point is unstable or far away);
* open borders and borders between faces of different attributes (surface materials) get strong
  perpendicular quadrics, so outlines and material regions keep their shape;
* a collapse is refused when it flips a face, makes a needle, or breaks the link condition (it would turn
  a manifold edge into a non-manifold one);
* faces only change their vertices: each output face keeps the attribute of the input face it came from.

Collapsing vertices never changes the parity of edge uses, so a closed input (no border edges) stays
closed (``closed_pairs=True`` also drops coincident face pairs in pairs).

Pure Python in the loop (numpy for set-up); ~20k faces/s on a desktop.
"""

from __future__ import annotations

import heapq
import math

from ._np import np

__all__ = ["decimate", "cluster"]


def _plane_quadrics(P: np.ndarray, T: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    a, b, c = P[T[:, 0]], P[T[:, 1]], P[T[:, 2]]
    n = np.cross(b - a, c - a)
    ln = np.linalg.norm(n, axis=1)
    area = 0.5 * ln
    with np.errstate(invalid="ignore", divide="ignore"):
        n = np.where(ln[:, None] > 1e-15, n / ln[:, None], 0.0)
    d = -(n * a).sum(axis=1)
    return n, d, area


def _q(nx: float, ny: float, nz: float, d: float, w: float) -> tuple:
    return (w * nx * nx, w * nx * ny, w * nx * nz, w * nx * d, w * ny * ny, w * ny * nz, w * ny * d,
            w * nz * nz, w * nz * d, w * d * d)


def _qadd(a, b):
    return tuple(x + y for x, y in zip(a, b))


def _qerr(q, x: float, y: float, z: float) -> float:
    return (q[0] * x * x + 2 * q[1] * x * y + 2 * q[2] * x * z + 2 * q[3] * x + q[4] * y * y + 2 * q[5] * y * z
            + 2 * q[6] * y + q[7] * z * z + 2 * q[8] * z + q[9])


def decimate(P: np.ndarray, T: np.ndarray, target: int, *, attr: np.ndarray | None = None,
             border_weight: float = 100.0, closed_pairs: bool = False) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Decimate ``(P, T)`` to at most ``target`` faces (more when no further collapse is allowed).

    Returns ``(P2, T2, src)``: ``src[i]`` = input face index of output face ``i``.
    """
    nv, nf = len(P), len(T)
    if nf <= target:
        return P.copy(), T.copy(), np.arange(nf)
    if attr is None:
        attr = np.zeros(nf, dtype=np.int64)
    nrm, dd, area = _plane_quadrics(P, T)
    # vertex quadrics (numpy accumulate, then python tuples)
    Q = np.zeros((nv, 10))
    qf = np.stack([nrm[:, 0] ** 2, nrm[:, 0] * nrm[:, 1], nrm[:, 0] * nrm[:, 2], nrm[:, 0] * dd,
                   nrm[:, 1] ** 2, nrm[:, 1] * nrm[:, 2], nrm[:, 1] * dd, nrm[:, 2] ** 2, nrm[:, 2] * dd,
                   dd * dd], axis=1) * area[:, None]
    for k in range(3):
        np.add.at(Q, T[:, k], qf)
    # border / material-border edges: perpendicular planes through the edge
    e = np.concatenate([T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]])
    ef = np.concatenate([np.arange(nf)] * 3)
    key = np.sort(e, axis=1)
    order = np.lexsort((key[:, 1], key[:, 0]))
    key, e, ef = key[order], e[order], ef[order]
    same_next = np.zeros(len(key), dtype=bool)
    same_next[:-1] = (key[1:] == key[:-1]).all(axis=1)
    start = np.ones(len(key), dtype=bool)
    start[1:] = ~same_next[:-1]
    run_id = np.cumsum(start) - 1
    run_len = np.bincount(run_id)
    first_attr = attr[ef[start]]
    attr_differs = np.zeros(len(run_len), dtype=bool)
    np.logical_or.at(attr_differs, run_id, attr[ef] != first_attr[run_id])
    border_run = (run_len[run_id] == 1) | attr_differs[run_id]
    for i in np.nonzero(border_run)[0]:
        a, b = e[i]
        f = ef[i]
        pa, pb = P[a], P[b]
        ed = pb - pa
        ln = float(np.linalg.norm(ed))
        if ln < 1e-12:
            continue
        pn = np.cross(ed, nrm[f])
        pl = float(np.linalg.norm(pn))
        if pl < 1e-12:
            continue
        pn /= pl
        q = np.array(_q(pn[0], pn[1], pn[2], -float(pn @ pa), border_weight * ln * ln))
        Q[a] += q
        Q[b] += q
    pos = [tuple(map(float, p)) for p in P]
    quad = [tuple(map(float, q)) for q in Q]
    faces = [list(map(int, t)) for t in T]
    alive = [True] * nf
    vfaces: list[set] = [set() for _ in range(nv)]
    for i, t in enumerate(faces):
        for v in t:
            vfaces[v].add(i)
    ver = [0] * nv
    scale = float(np.linalg.norm(P.max(axis=0) - P.min(axis=0))) or 1.0

    def neighbours(v: int) -> set:
        out = set()
        for f in vfaces[v]:
            out.update(faces[f])
        out.discard(v)
        return out

    def best(a: int, b: int) -> tuple[float, tuple]:
        q = _qadd(quad[a], quad[b])
        pa, pb = pos[a], pos[b]
        cand = [pa, pb, ((pa[0] + pb[0]) / 2, (pa[1] + pb[1]) / 2, (pa[2] + pb[2]) / 2)]
        a11, a12, a13, a22, a23, a33 = q[0], q[1], q[2], q[4], q[5], q[7]
        det = a11 * (a22 * a33 - a23 * a23) - a12 * (a12 * a33 - a23 * a13) + a13 * (a12 * a23 - a22 * a13)
        if abs(det) > 1e-12 * max(1.0, abs(a11 * a22 * a33)):
            b1, b2, b3 = -q[3], -q[6], -q[8]
            x = (b1 * (a22 * a33 - a23 * a23) - a12 * (b2 * a33 - a23 * b3) + a13 * (b2 * a23 - a22 * b3)) / det
            y = (a11 * (b2 * a33 - a23 * b3) - b1 * (a12 * a33 - a23 * a13) + a13 * (a12 * b3 - b2 * a13)) / det
            z = (a11 * (a22 * b3 - b2 * a23) - a12 * (a12 * b3 - b2 * a13) + b1 * (a12 * a23 - a22 * a13)) / det
            mid = cand[2]
            el = math.dist(pa, pb)
            if math.dist((x, y, z), mid) <= max(el, 1e-6) * 1.5:
                cand.append((x, y, z))
        errs = [(_qerr(q, *c), k) for k, c in enumerate(cand)]
        err, k = min(errs)
        return max(0.0, err), cand[k]

    heap: list = []

    def push(a: int, b: int) -> None:
        if a > b:
            a, b = b, a
        err, _p = best(a, b)
        heapq.heappush(heap, (err, a, b, ver[a], ver[b]))

    seen = set()
    for t in faces:
        for k in range(3):
            a, b = t[k], t[(k + 1) % 3]
            kk = (a, b) if a < b else (b, a)
            if kk not in seen:
                seen.add(kk)
                push(*kk)
    nalive = nf

    def normal(p0, p1, p2):
        ux, uy, uz = p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2]
        vx, vy, vz = p2[0] - p0[0], p2[1] - p0[1], p2[2] - p0[2]
        return uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx

    while heap and nalive > target:
        err, a, b, va, vb = heapq.heappop(heap)
        if va != ver[a] or vb != ver[b] or not vfaces[a] or not vfaces[b]:
            continue
        shared = vfaces[a] & vfaces[b]
        if not shared:
            continue
        # link condition: common neighbours == the opposite vertices of the shared faces
        opp = set()
        for f in shared:
            opp.update(faces[f])
        opp.discard(a)
        opp.discard(b)
        if (neighbours(a) & neighbours(b)) - opp:
            continue                                            # re-queued when a neighbour collapses
        _e, p = best(a, b)
        ok = True
        for v in (a, b):
            for f in vfaces[v]:
                if f in shared:
                    continue
                t = faces[f]
                old = [pos[x] for x in t]
                new = [p if x in (a, b) else pos[x] for x in t]
                n0 = normal(*old)
                n1 = normal(*new)
                l0 = math.sqrt(n0[0] ** 2 + n0[1] ** 2 + n0[2] ** 2)
                l1 = math.sqrt(n1[0] ** 2 + n1[1] ** 2 + n1[2] ** 2)
                if l1 <= 1e-12 * scale * scale or l0 <= 0:
                    ok = False
                    break
                if (n0[0] * n1[0] + n0[1] * n1[1] + n0[2] * n1[2]) < 0.2 * l0 * l1:
                    ok = False
                    break
                # needle guard: longest edge^2 / (2 * area) must stay bounded
                e2 = max(math.dist(new[0], new[1]), math.dist(new[1], new[2]), math.dist(new[2], new[0])) ** 2
                if e2 > 400.0 * l1 and e2 > 4 * (max(math.dist(old[0], old[1]), math.dist(old[1], old[2]),
                                                     math.dist(old[2], old[0])) ** 2):
                    ok = False
                    break
            if not ok:
                break
        if not ok:
            continue
        # collapse b into a
        for f in shared:
            alive[f] = False
            nalive -= 1
            for x in faces[f]:
                vfaces[x].discard(f)
        for f in list(vfaces[b]):
            t = faces[f]
            faces[f] = [a if x == b else x for x in t]
            vfaces[a].add(f)
        vfaces[b] = set()
        pos[a] = p
        quad[a] = _qadd(quad[a], quad[b])
        ver[a] += 1
        ver[b] += 1
        for n in neighbours(a):
            push(a, n)
    keep = [i for i in range(nf) if alive[i]]
    if closed_pairs:
        groups: dict = {}
        for i in keep:
            groups.setdefault(tuple(sorted(faces[i])), []).append(i)
        drop = set()
        for _k, ids in groups.items():
            if len(ids) >= 2:
                drop.update(ids[: len(ids) - len(ids) % 2])
        keep = [i for i in keep if i not in drop]
    used = sorted({v for i in keep for v in faces[i]})
    remap = {v: k for k, v in enumerate(used)}
    P2 = np.asarray([pos[v] for v in used], dtype=np.float64).reshape(-1, 3)
    T2 = np.asarray([[remap[v] for v in faces[i]] for i in keep], dtype=np.int64).reshape(-1, 3)
    return P2, T2, np.asarray(keep, dtype=np.int64)


def cluster(P: np.ndarray, T: np.ndarray, target: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Fast pre-reduction by vertex clustering on a grid: the largest cell size whose result still has
    more than ``target`` faces (binary search). Returns ``(P2, T2, src)`` like :func:`decimate`."""
    lo = P.min(axis=0)
    ext = float((P.max(axis=0) - lo).max()) or 1.0

    def run(cell: float):
        q = np.floor((P - lo) / cell).astype(np.int64)
        _u, inv = np.unique(q, axis=0, return_inverse=True)
        inv = inv.reshape(-1)
        T2 = inv[T]
        ok = (T2[:, 0] != T2[:, 1]) & (T2[:, 1] != T2[:, 2]) & (T2[:, 0] != T2[:, 2])
        idx = np.nonzero(ok)[0]
        key = np.sort(T2[idx], axis=1)
        _k, first = np.unique(key, axis=0, return_index=True)
        idx = idx[np.sort(first)]
        cnt = np.bincount(inv, minlength=int(inv.max()) + 1).astype(np.float64)
        acc = np.zeros((len(cnt), 3))
        np.add.at(acc, inv, P)
        centre = acc / np.maximum(cnt, 1)[:, None]
        return centre, T2[idx], idx

    lo_c, hi_c = ext / 4096.0, ext / 4.0
    best = run(lo_c)
    for _ in range(18):
        mid = math.sqrt(lo_c * hi_c)
        r = run(mid)
        if len(r[1]) > target:
            best, lo_c = r, mid
        else:
            hi_c = mid
    C, T2, idx = best
    used = np.unique(T2)
    remap = np.full(len(C), -1, dtype=np.int64)
    remap[used] = np.arange(len(used))
    return C[used], remap[T2], idx

