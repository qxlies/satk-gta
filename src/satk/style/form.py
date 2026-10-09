"""Composition checks of satk.style: defects with locations, never numbers to chase.

The SA look needs one welded shell with details that touch it, soft corners and closed wheel arches. This
module finds the opposite, as defects an agent can act on:

* ``floating``     - groups of pieces (welded connected components) that touch nothing grounded: the body
                     shell is the ground; a gap above ``touch_mm`` separates (name, gap mm, centre);
* ``intersect``    - pieces that cross another piece and sit deep behind its surface (a box jammed through
                     the body, a flare ring sunk into the side);
* ``loose_share``  - share of the body part's triangles outside its largest welded piece (vanilla chassis
                     16-38 %);
* ``hard_corners`` - 60-100 deg folds whose normals are split without a material or UV seam (the boxy look;
                     vanilla hardens seams and designed creases, not corners);
* ``see_through``  - wheel arches without a liner: rays from the side through the arch above the tyre reach
                     the far half of the car or nothing;
* ``dense_flat``   - fine triangles on flat or gently curved surface: vertices a coarser mesh would not need
                     (folds under 8 deg, edges under 10 cm), when they are a third of the model or more (the
                     uniformly dense, subdivided-smooth look; vanilla puts density where the outline turns and
                     lets the normals make it soft).

Allowed exceptions come from vanilla (``data/style/form.json``): flat cards (plates, gauges, steering card),
glass panes behind their frame and small heads on stalks may hover a little.

numpy only, and nothing from satk at module level (the limits file is read lazily), so the module also runs in
Blender's Python: the studio step stats use it for their ``form`` block.

Input parts are dicts::

    {"name": "chassis", "pos": (V, 3) model-space metres, "tris": (T, 3),
     "normals": (T, 3, 3) corner normals | (V, 3) vertex normals | None,
     "uv": (T, 3, 2) corner UVs of set 0 | (V, 2) | None, "mat": (T,) material index | None,
     "tex": [texture name per material] | None, "alpha": [alpha per material] | None,
     "role": "hd" (default) | "wheel" (skipped) | "context" (a neighbour only, never reported)}

Example::

    from satk.style.form import analyse
    r = analyse(parts, body="chassis", wheels=[{"name": "wheel_lf_dummy", "center": (-0.9, 1.4, 0.1),
                                               "radius": 0.36}])
    r["floating"]      # [{"part": "roof_rail", "pieces": 3, "tris": 36, "gap_mm": 44.0, "at": [...]}]
"""

from __future__ import annotations

__all__ = ["DEFAULTS", "limits", "analyse", "pieces_of", "hard_corners", "see_through", "loose_share",
           "nearest_surface", "ray_x", "piece_table", "arch_outline", "cover_2d", "dense_flat"]

#: Built-in limits; ``data/style/form.json`` overrides them (see :func:`limits`).
DEFAULTS: dict = {
    "weld_m": 1e-4,
    "touch_mm": 6.0,             # pieces closer than this touch
    "gap_search_mm": 300.0,      # how far the gap of a floating group is searched
    "card_max_tris": 12,         # a flat card (plate, gauge, steering card) ...
    "card_thick_mm": 3.0,        # ... no thicker than this ...
    "card_gap_mm": 25.0,         # ... may hover up to this far
    "glass_gap_mm": 25.0,        # glass panes may sit this far behind their frame
    "head_max_tris": 24,         # a small head (mirror head on a stalk, a lamp bezel) ...
    "head_gap_mm": 20.0,         # ... may hover up to this far from what it belongs to
    "tiny_max_tris": 8,          # a tiny bit (interior mirror, badge) ...
    "tiny_gap_mm": 40.0,         # ... may hover up to this far
    "panel_parts": "^(door|bonnet|boot|bump|windscreen|plate|wing|ug_)",
    "panel_gap_mm": 30.0,        # an opening panel may stand off the body by its shut line
    "moving_parts": "^(misc_|gear_|bogie_|transmission|extra|moving_|static_|rotor|tail|elevator|aileron|rudder|"
                    "flap|airbrake|hook|ladder|turret|fork|arm|bucket|scoop|hatch|wiper)",
    "moving_any": "(flap|rudder|aileron|elevator|rotor|moving_|_prop)",   # ... also anywhere in the name
    "moving_gap_mm": 60.0,       # a part the game animates may stand off by this much
    "world_mm": None,            # map models: a group on the terrain has its lowest point this close to the
                                 # model's foot (None: anywhere; authored models: data/style/form.json "authored")
    "mount_mm": 50.0,            # ... a group on a wall reaches the model's box side ...
    "mount_main": False,         # ... and (authored models) the model's main group stands against that side too
    "deep_mm": 30.0,             # intersect: depth behind the crossed surface ...
    "deep_share": 0.9,           # ... for at least this share of the piece's vertices ...
    "deep_min_tris": 12,         # ... of a piece with at least this many triangles
    "deep_radius_mm": 150.0,     # surfaces farther than this do not count for the depth
    "loose_share": 0.6,          # body loose share above this is a defect (vanilla road cars up to 0.57) ...
    "loose_min_tris": 200,       # ... for a body of at least this many triangles
    "fold_deg": [60.0, 100.0],   # hard_corners: fold range
    "hard_deg": 1.0,             # corner normals differing more than this = hard
    "corner_min_folds": 60,      # at least this many folds before a verdict
    "corner_share": 0.2,         # model: crease share above this ...
    "corner_soft": 0.08,         # ... and soft share below this is a defect (vanilla soft >= 0.098)
    "step_corner_share": 0.25,   # studio step (the changed objects): crease share above this ...
    "step_corner_soft": 0.03,    # ... and soft share below this (vanilla chassis soft >= 0.047)
    "corner_examples": 4,
    "arch_angles": [25.0, 155.0, 9],      # see_through: angles over the top (deg, from the front), count
    "arch_radii": [1.04, 1.1],            # x wheel radius (the opening between tyre and arch rim)
    "arch_share": 0.34,                   # share of see-through rays per arch above this is a defect
    "outline_angles": [20.0, 160.0, 15],  # arch outline: side rays from the wheel centre
    "outline_max": 2.0,                   # x wheel radius: farther = no arch there
    "outline_min_rays": 6,                # fewer hits = an open wheel (no arch)
    "dense_fold_deg": 8.0,       # dense_flat: a vertex is redundant when its faces stay within this fold ...
    "dense_fine_mm": 100.0,      # ... and its edges are shorter than this (fine triangles) on average
    "dense_share": 0.33,         # model: wasted triangles (2 per redundant vertex) above this share ...
    "dense_min_tris": 100,       # ... and at least this many is a finding (vanilla, 14,790 models: at most 0.261)
    "dense_regions": 3,          # regions reported per part
}

_LIMITS: list = []


def limits(override: dict | None = None) -> dict:
    """:data:`DEFAULTS` updated by ``data/style/form.json`` (``limits``) and ``override``."""
    if not _LIMITS:
        lim = dict(DEFAULTS)
        try:
            from ..core import resources  # stdlib only; absent when this file is loaded on its own

            lim.update(resources.read_json("style", "form.json").get("limits", {}))
        except Exception:  # noqa: BLE001 - the built-in defaults equal the data file
            pass
        _LIMITS.append(lim)
    out = dict(_LIMITS[0])
    out.update(override or {})
    return out


def _np():
    import numpy as np

    return np


def _r(x, nd: int = 2) -> float:
    return float(round(float(x), nd)) + 0.0


# ----------------------------------------------------------------------------- parts and pieces
def _arr(np, a, width, dtype):
    return np.asarray(a, dtype=dtype).reshape(-1, width)


def _prep(np, part: dict, weld: float) -> dict | None:
    P = _arr(np, part["pos"], 3, np.float64)
    T = _arr(np, part["tris"], 3, np.int64)
    if not len(T) or not len(P):
        return None
    ok = np.isfinite(P).all(axis=1)
    if not ok.all():
        P = np.where(ok[:, None], P, 0.0)
        T = T[ok[T].all(axis=1)]
        if not len(T):
            return None
    key = np.round(P / weld).astype(np.int64)
    _u, wid = np.unique(key, axis=0, return_inverse=True)
    wid = np.asarray(wid).reshape(-1)
    W = wid[T]
    keep = (W[:, 0] != W[:, 1]) & (W[:, 1] != W[:, 2]) & (W[:, 0] != W[:, 2])
    A, B, C = P[T[:, 0]], P[T[:, 1]], P[T[:, 2]]
    cr = np.cross(B - A, C - A)
    area2 = np.linalg.norm(cr, axis=1)
    keep &= area2 > 1e-12
    out = {"name": str(part.get("name") or "part"), "role": part.get("role", "hd"), "P": P, "T": T, "W": W,
           "nw": int(wid.max()) + 1, "keep": keep, "fn": cr / np.maximum(area2, 1e-30)[:, None],
           "area": area2 / 2.0}
    nt = len(T)
    M = part.get("mat")
    out["M"] = np.asarray(M, dtype=np.int64).reshape(-1)[:nt] if M is not None else np.zeros(nt, np.int64)
    if len(out["M"]) != nt:
        out["M"] = np.zeros(nt, np.int64)
    N = part.get("normals")
    if N is not None:
        N = np.asarray(N, dtype=np.float64)
        if N.ndim == 3 and N.shape[0] == nt:
            out["CN"] = N
        else:
            N = N.reshape(-1, 3)
            if len(N) == len(P):
                out["CN"] = N[T]
    U = part.get("uv")
    if U is not None:
        U = np.asarray(U, dtype=np.float64)
        if U.ndim == 3 and U.shape[0] == nt:
            out["UV"] = U
        else:
            U = U.reshape(-1, 2)
            if len(U) == len(P):
                out["UV"] = U[T]
    tex = [str(t or "").lower() for t in (part.get("tex") or [])]
    alpha = [int(a) for a in (part.get("alpha") or [])]
    out["tex"], out["alpha"] = tex, alpha
    out["keys"] = [str(k) for k in (part.get("keys") or [])]
    out["talpha"] = [bool(a) for a in (part.get("talpha") or [])]
    return out


def _labels(np, W, nw: int):
    """Connected component label per triangle (same algorithm as satk.style.metrics)."""
    from .metrics import _components

    return _components(W, nw, np)


def pieces_of(parts: list[dict], weld: float | None = None) -> tuple[list[dict], list[dict]]:
    """``(prepared parts, pieces)``. A piece = ``{part, label, tri (indices), vid, lo, hi, tris, area, centre,
    planar_mm, glass, textures}``; parts with ``role == "wheel"`` are left out."""
    np = _np()
    weld = weld or limits()["weld_m"]
    preps, pieces = [], []
    for part in parts:
        if part.get("role") == "wheel":
            continue
        pp = _prep(np, part, weld)
        if pp is None:
            continue
        pi = len(preps)
        preps.append(pp)
        T, W, keep = pp["T"], pp["W"], pp["keep"]
        if not keep.any():
            continue
        lab = _labels(np, W, pp["nw"])
        lab[~keep] = -1
        pp["lab"] = lab
        first = len(pieces)
        for k in np.unique(lab[keep]):
            tri = np.flatnonzero(lab == k)
            vid = np.unique(T[tri].reshape(-1))
            V = pp["P"][vid]
            lo, hi = V.min(axis=0), V.max(axis=0)
            mats = np.unique(pp["M"][tri])
            texs = sorted({pp["tex"][m] for m in mats if 0 <= m < len(pp["tex"])} - {""})
            glass = bool(pp["alpha"]) and all(0 <= m < len(pp["alpha"]) and pp["alpha"][m] < 255 for m in mats)
            alpha = all((0 <= m < len(pp["alpha"]) and pp["alpha"][m] < 255)
                        or (0 <= m < len(pp["talpha"]) and pp["talpha"][m]) for m in mats)
            pieces.append({"part": pi, "label": int(k), "tri": tri, "vid": vid, "lo": lo, "hi": hi,
                           "tris": int(len(tri)), "area": float(pp["area"][tri].sum()), "centre": (lo + hi) / 2,
                           "planar_mm": _thickness(np, V) * 1000.0, "glass": glass, "textures": texs,
                           "alpha": alpha, "main": False})
        if len(pieces) > first:
            big = max(range(first, len(pieces)), key=lambda i: (pieces[i]["tris"], pieces[i]["area"]))
            pieces[big]["main"] = True
    return preps, pieces


def _thickness(np, V) -> float:
    """Smallest extent of the points along their principal axes (0 for a flat card)."""
    if len(V) < 4:
        return 0.0
    X = V - V.mean(axis=0)
    try:
        _u, _s, vt = np.linalg.svd(X, full_matrices=False)
    except np.linalg.LinAlgError:
        return 0.0
    d = X @ vt[-1]
    return float(d.max() - d.min())


# ----------------------------------------------------------------------------- geometry kernels
def _closest(np, Q, A, B, C):
    """Closest points on triangles ``A, B, C`` (K, 3) to points ``Q`` (K, 3), pairwise (Ericson). Returns
    ``(distance (K,), interior (K,) bool)``: interior = the closest point lies inside the face."""
    ab, ac, aq = B - A, C - A, Q - A
    d1, d2 = (ab * aq).sum(1), (ac * aq).sum(1)
    bq = Q - B
    d3, d4 = (ab * bq).sum(1), (ac * bq).sum(1)
    cq = Q - C
    d5, d6 = (ab * cq).sum(1), (ac * cq).sum(1)
    va, vb, vc = d3 * d6 - d5 * d4, d5 * d2 - d1 * d6, d1 * d4 - d3 * d2
    den = va + vb + vc
    den = np.where(np.abs(den) < 1e-30, 1e-30, den)
    v, w = vb / den, vc / den
    R = A + ab * v[:, None] + ac * w[:, None]
    inner = np.ones(len(Q), dtype=bool)

    def put(m, X):
        R[m] = X[m]
        inner[m] = False

    with np.errstate(divide="ignore", invalid="ignore"):
        m = (va <= 0) & ((d4 - d3) >= 0) & ((d5 - d6) >= 0)
        t = (d4 - d3) / np.where(m, (d4 - d3) + (d5 - d6), 1.0)
        put(m, B + (C - B) * t[:, None])
        m = (vb <= 0) & (d2 >= 0) & (d6 <= 0)
        t = d2 / np.where(m, d2 - d6, 1.0)
        put(m, A + ac * t[:, None])
        put((d6 >= 0) & (d5 <= d6), C)
        m = (vc <= 0) & (d1 >= 0) & (d3 <= 0)
        t = d1 / np.where(m, d1 - d3, 1.0)
        put(m, A + ab * t[:, None])
        put((d3 >= 0) & (d4 <= d3), B)
        put((d1 <= 0) & (d2 <= 0), A)
    return np.linalg.norm(R - Q, axis=1), inner


def _seg_hits(np, S0, S1, A, B, C):
    """Pairwise segment-triangle intersection (Moller-Trumbore): ``(hit bool (K,), point (K, 3))``."""
    d = S1 - S0
    e1, e2 = B - A, C - A
    p = np.cross(d, e2)
    det = (e1 * p).sum(1)
    ok = np.abs(det) > 1e-14
    inv = 1.0 / np.where(ok, det, 1.0)
    s = S0 - A
    u = (s * p).sum(1) * inv
    q = np.cross(s, e1)
    v = (d * q).sum(1) * inv
    t = (e2 * q).sum(1) * inv
    eps = 1e-9
    hit = ok & (u >= -eps) & (v >= -eps) & (u + v <= 1 + eps) & (t > 1e-6) & (t < 1 - 1e-6)
    return hit, S0 + d * t[:, None]


class _Grid:
    """Uniform grid of triangle boxes (expanded by ``pad``): candidate triangles for points and boxes."""

    def __init__(self, np, lo, hi, pad: float, cell: float | None = None, max_entries: int = 3_000_000):
        self.np = np
        lo = lo - pad
        hi = hi + pad
        self.origin = lo.min(axis=0) if len(lo) else np.zeros(3)
        ext = (hi - lo).max(axis=1) if len(lo) else np.zeros(1)
        h = cell or max(float(np.median(ext)) if len(ext) else 0.1, 0.02)
        while True:
            c0 = np.floor((lo - self.origin) / h).astype(np.int64)
            c1 = np.floor((hi - self.origin) / h).astype(np.int64)
            span = c1 - c0 + 1
            n = span.prod(axis=1)
            if n.sum() <= max_entries:
                break
            h *= 1.6
        self.h = h
        self.dims = (c1.max(axis=0) + 1) if len(c1) else np.ones(3, np.int64)
        tot = int(n.sum())
        tri = np.repeat(np.arange(len(lo)), n)
        off = np.arange(tot) - np.repeat(np.cumsum(n) - n, n)
        sy, sz = span[tri, 1], span[tri, 2]
        dz = off % sz
        dy = (off // sz) % sy
        dx = off // (sy * sz)
        keys = self._key(c0[tri, 0] + dx, c0[tri, 1] + dy, c0[tri, 2] + dz)
        order = np.argsort(keys, kind="stable")
        keys, self.tri = keys[order], tri[order]
        self.ukeys, self.start, self.count = np.unique(keys, return_index=True, return_counts=True)

    def _key(self, x, y, z):
        ny, nz = int(self.dims[1]) + 1, int(self.dims[2]) + 1
        return (x * ny + y) * nz + z

    def _cells(self, X):
        c = self.np.floor((X - self.origin) / self.h).astype(self.np.int64)
        bad = (c < 0).any(axis=1) | (c > self.dims).any(axis=1)
        return self._key(c[:, 0], c[:, 1], c[:, 2]), bad

    def pairs(self, X):
        """``(query index, triangle index)`` candidate pairs of points ``X`` (N, 3)."""
        np = self.np
        k, bad = self._cells(X)
        pos = np.searchsorted(self.ukeys, k)
        pos = np.minimum(pos, len(self.ukeys) - 1)
        found = (self.ukeys[pos] == k) & ~bad if len(self.ukeys) else np.zeros(len(X), bool)
        q = np.flatnonzero(found)
        cnt = self.count[pos[q]]
        st = self.start[pos[q]]
        qi = np.repeat(q, cnt)
        off = np.arange(int(cnt.sum())) - np.repeat(np.cumsum(cnt) - cnt, cnt)
        return qi, self.tri[np.repeat(st, cnt) + off]

    def box_pairs(self, lo, hi):
        """``(box index, triangle index)`` candidate pairs of boxes ``lo``..``hi`` (B, 3); may repeat a pair."""
        np = self.np
        c0 = np.clip(np.floor((lo - self.origin) / self.h).astype(np.int64), 0, self.dims)
        c1 = np.clip(np.floor((hi - self.origin) / self.h).astype(np.int64), 0, self.dims)
        span = np.maximum(c1 - c0 + 1, 1)
        n = span.prod(axis=1)
        box = np.repeat(np.arange(len(lo)), n)
        off = np.arange(int(n.sum())) - np.repeat(np.cumsum(n) - n, n)
        sy, sz = span[box, 1], span[box, 2]
        k = self._key(c0[box, 0] + off // (sy * sz), c0[box, 1] + (off // sz) % sy, c0[box, 2] + off % sz)
        if not len(self.ukeys) or not len(k):
            return np.zeros(0, np.int64), np.zeros(0, np.int64)
        pos = np.minimum(np.searchsorted(self.ukeys, k), len(self.ukeys) - 1)
        f = self.ukeys[pos] == k
        box, pos = box[f], pos[f]
        cnt, st = self.count[pos], self.start[pos]
        bi = np.repeat(box, cnt)
        o2 = np.arange(int(cnt.sum())) - np.repeat(np.cumsum(cnt) - cnt, cnt)
        return bi, self.tri[np.repeat(st, cnt) + o2]


# ----------------------------------------------------------------------------- the scene of one analysis
class _Scene:
    def __init__(self, np, preps: list[dict], pieces: list[dict]):
        self.np = np
        self.preps, self.pieces = preps, pieces
        # global triangle table over kept triangles of every piece
        A, B, C, owner, part, ltri = [], [], [], [], [], []
        vP, vowner = [], []
        for i, pc in enumerate(pieces):
            pp = preps[pc["part"]]
            T = pp["T"][pc["tri"]]
            A.append(pp["P"][T[:, 0]])
            B.append(pp["P"][T[:, 1]])
            C.append(pp["P"][T[:, 2]])
            owner.append(np.full(len(T), i, np.int64))
            ltri.append(pc["tri"])
            al = np.asarray(pp["alpha"] or [255], dtype=np.int64)
            mt = np.clip(pp["M"][pc["tri"]], 0, len(al) - 1)
            part.append(al[mt] < 255)
            vP.append(pp["P"][pc["vid"]])
            vowner.append(np.full(len(pc["vid"]), i, np.int64))
        cat = (lambda xs, w: np.concatenate(xs) if xs else np.zeros((0, w)))
        self.A, self.B, self.C = cat(A, 3), cat(B, 3), cat(C, 3)
        self.owner = np.concatenate(owner) if owner else np.zeros(0, np.int64)
        self.ltri = np.concatenate(ltri) if ltri else np.zeros(0, np.int64)
        self.tglass = np.concatenate(part) if part else np.zeros(0, bool)
        self.V = cat(vP, 3)
        self.vowner = np.concatenate(vowner) if vowner else np.zeros(0, np.int64)
        fn = np.cross(self.B - self.A, self.C - self.A)
        self.fn = fn / np.maximum(np.linalg.norm(fn, axis=1), 1e-30)[:, None]
        self.tlo = np.minimum(np.minimum(self.A, self.B), self.C)
        self.thi = np.maximum(np.maximum(self.A, self.B), self.C)

    def grid(self, pad: float) -> _Grid:
        return _Grid(self.np, self.tlo, self.thi, pad)

    def edges(self):
        """Unique edges per piece as segment endpoints ``(S0, S1, owner)``."""
        np = self.np
        S0, S1, own = [], [], []
        for i, pc in enumerate(self.pieces):
            pp = self.preps[pc["part"]]
            T, W = pp["T"][pc["tri"]], pp["W"][pc["tri"]]
            e = np.concatenate([np.stack([W[:, a], W[:, b], T[:, a], T[:, b]], axis=1)
                                for a, b in ((0, 1), (1, 2), (2, 0))])
            k = np.minimum(e[:, 0], e[:, 1]) * (int(pp["nw"]) + 1) + np.maximum(e[:, 0], e[:, 1])
            _u, first = np.unique(k, return_index=True)
            e = e[first]
            S0.append(pp["P"][e[:, 2]])
            S1.append(pp["P"][e[:, 3]])
            own.append(np.full(len(e), i, np.int64))
        if not S0:
            return np.zeros((0, 3)), np.zeros((0, 3)), np.zeros(0, np.int64)
        return np.concatenate(S0), np.concatenate(S1), np.concatenate(own)


def _chunked(np, n: int, size: int = 400_000):
    for a in range(0, n, size):
        yield a, min(n, a + size)


def _contacts(sc: _Scene, tol: float, focus: set | None):
    """Touching piece pairs ``{(i, j)}`` (vertex within ``tol`` of a face, or crossing faces) and the crossing
    pairs with their hit points. ``focus``: piece ids whose contacts are needed (None = all)."""
    np = sc.np
    touch: set = set()
    if not len(sc.A):
        return touch, {}, set()
    g = sc.grid(tol)
    sel = np.arange(len(sc.V)) if focus is None else np.flatnonzero(np.isin(sc.vowner, list(focus)))
    qi, ti = g.pairs(sc.V[sel])
    qi = sel[qi]
    m = sc.vowner[qi] != sc.owner[ti]
    qi, ti = qi[m], ti[m]
    for a, b in _chunked(np, len(qi)):
        q, t = qi[a:b], ti[a:b]
        lo, hi = sc.tlo[t] - tol, sc.thi[t] + tol
        inside = ((sc.V[q] >= lo) & (sc.V[q] <= hi)).all(axis=1)
        q, t = q[inside], t[inside]
        if not len(q):
            continue
        d, _in = _closest(np, sc.V[q], sc.A[t], sc.B[t], sc.C[t])
        near = d <= tol
        for i, j in set(zip(sc.vowner[q[near]].tolist(), sc.owner[t[near]].tolist())):
            touch.add((min(i, j), max(i, j)))
    vtouch = set(touch)
    # crossing faces: edges of a piece through triangles of another
    S0, S1, own = sc.edges()
    cross: dict = {}
    if len(S0):
        es = np.arange(len(S0)) if focus is None else np.flatnonzero(np.isin(own, list(focus)))
        qi, ti = g.box_pairs(np.minimum(S0[es], S1[es]), np.maximum(S0[es], S1[es]))
        if len(qi):
            nt = len(sc.A) + 1
            pair = np.unique(qi.astype(np.int64) * nt + ti)
            qi, ti = es[pair // nt], pair % nt
            m = own[qi] != sc.owner[ti]
            qi, ti = qi[m], ti[m]
            elo = np.minimum(S0[qi], S1[qi])
            ehi = np.maximum(S0[qi], S1[qi])
            ov = ((elo <= sc.thi[ti]) & (ehi >= sc.tlo[ti])).all(axis=1)
            qi, ti = qi[ov], ti[ov]
            for a, b in _chunked(np, len(qi)):
                q, t = qi[a:b], ti[a:b]
                hit, X = _seg_hits(np, S0[q], S1[q], sc.A[t], sc.B[t], sc.C[t])
                for i, j, x in zip(own[q[hit]].tolist(), sc.owner[t[hit]].tolist(), X[hit]):
                    key = (min(i, j), max(i, j))
                    touch.add(key)
                    cross.setdefault(key, []).append(x)
    return touch, cross, vtouch


def _union(n: int, pairs) -> list[int]:
    par = list(range(n))

    def find(x):
        while par[x] != x:
            par[x] = par[par[x]]
            x = par[x]
        return x
    for i, j in pairs:
        ri, rj = find(i), find(j)
        if ri != rj:
            par[max(ri, rj)] = min(ri, rj)
    return [find(i) for i in range(n)]


def nearest_surface(np, Q, A, B, C, fn=None, chunk: int = 300_000):
    """Distance from each point ``Q`` (N, 3) to the nearest of the triangles ``A, B, C`` (M, 3) (brute force,
    chunked); with ``fn`` also the signed distance (by the nearest face's normal) and its triangle index."""
    n, mtri = len(Q), len(A)
    best = np.full(n, np.inf)
    idx = np.full(n, -1, np.int64)
    if not n or not mtri:
        return best, idx
    step = max(1, chunk // max(mtri, 1))
    for a in range(0, n, step):
        q = Q[a:a + step]
        qq = np.repeat(q, mtri, axis=0)
        d, _in = _closest(np, qq, np.tile(A, (len(q), 1)), np.tile(B, (len(q), 1)), np.tile(C, (len(q), 1)))
        d = d.reshape(len(q), mtri)
        k = d.argmin(axis=1)
        best[a:a + step] = d[np.arange(len(q)), k]
        idx[a:a + step] = k
    return best, idx


def _gap(sc: _Scene, members: list[int], others: list[int], search: float) -> float | None:
    """Smallest distance (m) between the pieces ``members`` and ``others`` within ``search`` (None: farther)."""
    np = sc.np
    if not others:
        return None
    lo = np.min([sc.pieces[i]["lo"] for i in members], axis=0) - search
    hi = np.max([sc.pieces[i]["hi"] for i in members], axis=0) + search
    tm = np.isin(sc.owner, others) & ((sc.thi >= lo) & (sc.tlo <= hi)).all(axis=1)
    vm = np.isin(sc.vowner, members)
    best = None
    if tm.any() and vm.any():
        d, _ = nearest_surface(np, sc.V[vm], sc.A[tm], sc.B[tm], sc.C[tm])
        best = float(d.min())
    # the other direction: vertices of the neighbours to the faces of the group
    om = np.isin(sc.vowner, others) & ((sc.V >= lo) & (sc.V <= hi)).all(axis=1)
    gm = np.isin(sc.owner, members)
    if om.any() and gm.any():
        d, _ = nearest_surface(np, sc.V[om], sc.A[gm], sc.B[gm], sc.C[gm])
        best = float(d.min()) if best is None else min(best, float(d.min()))
    return best if best is not None and best <= search else None


# ----------------------------------------------------------------------------- checks
def _axis_hits(np, A, B, C, axis: int, p):
    """Coordinates along ``axis`` where the line through ``p`` parallel to it meets triangles ``A, B, C``."""
    a, b = [k for k in range(3) if k != axis]
    ya, za, yb, zb, yc, zc = A[:, a], A[:, b], B[:, a], B[:, b], C[:, a], C[:, b]
    y, z = float(p[a]), float(p[b])
    den = (zb - zc) * (ya - yc) + (yc - yb) * (za - zc)
    ok = np.abs(den) > 1e-14
    den = np.where(ok, den, 1.0)
    l0 = ((zb - zc) * (y - yc) + (yc - yb) * (z - zc)) / den
    l1 = ((zc - za) * (y - yc) + (ya - yc) * (z - zc)) / den
    l2 = 1.0 - l0 - l1
    hit = ok & (l0 >= -1e-9) & (l1 >= -1e-9) & (l2 >= -1e-9)
    return (l0 * A[:, axis] + l1 * B[:, axis] + l2 * C[:, axis])[hit]


def _hidden(sc: "_Scene", members: list[int]) -> bool:
    """True when opaque geometry of other pieces covers the group from all six axis directions (engine bay,
    cabin and underbody fill that nobody sees from outside)."""
    np = sc.np
    other = ~np.isin(sc.owner, members) & ~sc.tglass
    if not other.any():
        return False
    lo = np.min([sc.pieces[i]["lo"] for i in members], axis=0)
    hi = np.max([sc.pieces[i]["hi"] for i in members], axis=0)
    c = (lo + hi) / 2
    A, B, C = sc.A[other], sc.B[other], sc.C[other]
    for axis in range(3):
        x = _axis_hits(np, A, B, C, axis, c)
        if not (x > hi[axis]).any() or not (x < lo[axis]).any():
            return False
    return True


def _exempt(pc: dict, gap_mm: float | None, lim: dict) -> str | None:
    """The vanilla exception a hovering piece falls under, or None (hidden groups are tested separately)."""
    g = float("inf") if gap_mm is None else gap_mm
    if pc["tris"] <= lim["card_max_tris"] and pc["planar_mm"] <= lim["card_thick_mm"] \
            and (g <= lim["card_gap_mm"] or lim.get("card_free")):
        return "card"
    if pc["glass"] and g <= lim["glass_gap_mm"]:
        return "glass"
    if pc["tris"] <= lim["head_max_tris"] and g <= lim["head_gap_mm"]:
        return "head"
    if pc["tris"] <= lim["tiny_max_tris"] and g <= lim["tiny_gap_mm"]:
        return "tiny"
    if pc.get("alpha") and (g <= float(lim.get("alpha_gap_mm", 0.0)) or lim.get("alpha_free")):
        return "alpha"
    return None


def _anchor(sc: _Scene, body: str | None) -> list[int]:
    if not sc.pieces:
        return []
    names = [sc.preps[pc["part"]]["name"].lower() for pc in sc.pieces]
    cand = [i for i, n in enumerate(names) if body and n == body.lower()]
    pool = cand or list(range(len(sc.pieces)))
    pool = [i for i in pool if sc.preps[sc.pieces[i]["part"]]["role"] != "context"] or pool
    return [max(pool, key=lambda i: (sc.pieces[i]["area"], sc.pieces[i]["tris"]))]


def _depths(sc: _Scene, i: int, j: int, radius: float) -> tuple[float, float]:
    """``(depth m, share)`` of piece ``i`` behind the surface of piece ``j`` (vertices within ``radius``)."""
    np = sc.np
    pi, pj = sc.pieces[i], sc.pieces[j]
    lo, hi = pi["lo"] - radius, pi["hi"] + radius
    tm = (sc.owner == j) & ((sc.thi >= lo) & (sc.tlo <= hi)).all(axis=1)
    V = sc.V[sc.vowner == i]
    if not tm.any() or not len(V):
        return 0.0, 0.0
    A, B, C, fn = sc.A[tm], sc.B[tm], sc.C[tm], sc.fn[tm]
    d, k = nearest_surface(np, V, A, B, C)
    # sign by the nearest face: behind = against its normal
    proj = ((V - A[k]) * fn[k]).sum(axis=1)
    near = d <= radius
    behind = near & (proj < 0)
    if not behind.any():
        return 0.0, 0.0
    return float(d[behind].max()), float(behind.sum() / len(V))


def loose_share(prep: dict) -> tuple[float, int] | None:
    """``(share of triangles outside the largest piece, pieces)`` of one prepared part."""
    np = _np()
    lab = prep.get("lab")
    if lab is None:
        return None
    lab = lab[lab >= 0]
    if not len(lab):
        return None
    _u, cnt = np.unique(lab, return_counts=True)
    return 1.0 - float(cnt.max()) / float(len(lab)), int(len(cnt))


def hard_corners(prep: dict, lim: dict | None = None) -> dict | None:
    """Folds of 60-100 deg in one prepared part: ``{folds, creases, soft, seams, share, at}`` (creases =
    hard without a material/UV seam; ``at`` = midpoints of the longest creases); None without normals."""
    np = _np()
    lim = lim or limits()
    CN = prep.get("CN")
    if CN is None:
        return None
    T, W, keep = prep["T"], prep["W"], prep["keep"]
    nt = len(T)
    f = np.repeat(np.arange(nt), 3)
    c0 = np.arange(3 * nt)
    c1 = 3 * (c0 // 3) + (c0 % 3 + 1) % 3
    Wf = W.reshape(-1)
    w0, w1 = Wf[c0], Wf[c1]
    good = keep[f] & (w0 != w1)
    c0, c1, w0, w1, f = c0[good], c1[good], w0[good], w1[good], f[good]
    sw = w0 > w1
    clo, chi = np.where(sw, c1, c0), np.where(sw, c0, c1)
    ek = np.minimum(w0, w1) * prep["nw"] + np.maximum(w0, w1)
    o = np.argsort(ek, kind="stable")
    ek, clo, chi, f = ek[o], clo[o], chi[o], f[o]
    if not len(ek):
        return None
    st = np.flatnonzero(np.r_[True, ek[1:] != ek[:-1]])
    cnt = np.diff(np.r_[st, len(ek)])
    s2 = st[cnt == 2]
    fa, fb = f[s2], f[s2 + 1]
    fn = prep["fn"]
    dih = np.degrees(np.arccos(np.clip((fn[fa] * fn[fb]).sum(1), -1.0, 1.0)))
    lo_d, hi_d = lim["fold_deg"]
    fold = (dih >= lo_d) & (dih <= hi_d)
    if not fold.any():
        return {"folds": 0, "creases": 0, "soft": 0, "seams": 0, "share": 0.0, "at": []}
    s2, fa, fb = s2[fold], fa[fold], fb[fold]
    cn = CN.reshape(-1, 3)
    cn = cn / np.maximum(np.linalg.norm(cn, axis=1), 1e-30)[:, None]
    l0, h0, l1, h1 = clo[s2], chi[s2], clo[s2 + 1], chi[s2 + 1]
    lim_cos = np.cos(np.radians(lim["hard_deg"]))
    hard = ((cn[l0] * cn[l1]).sum(1) < lim_cos) | ((cn[h0] * cn[h1]).sum(1) < lim_cos)
    seam = prep["M"][fa] != prep["M"][fb]
    if "UV" in prep:
        uv = prep["UV"].reshape(-1, 2)
        seam |= (np.abs(uv[l0] - uv[l1]).max(axis=1) > 1e-4) | (np.abs(uv[h0] - uv[h1]).max(axis=1) > 1e-4)
    crease = hard & ~seam
    Pf = prep["P"][prep["T"].reshape(-1)]
    out = {"folds": int(len(s2)), "creases": int(crease.sum()), "soft": int((~hard).sum()),
           "seams": int((hard & seam).sum()), "share": _r(crease.mean(), 3)}
    if crease.any():
        a, b = Pf[l0[crease]], Pf[h0[crease]]
        ln = np.linalg.norm(b - a, axis=1)
        top = np.argsort(-ln, kind="stable")[: int(lim["corner_examples"])]
        out["at"] = [[_r(x) for x in (a[k] + b[k]) / 2] for k in top]
    else:
        out["at"] = []
    return out


def _per_vertex(np, vid, vals, nw: int, how: str):
    """Reduce ``vals`` (n,) or (n, d) per welded vertex ``vid`` (n,) with ``min``/``max``/``sum``; vertices without a
    value get +inf / -inf / 0."""
    vals = np.asarray(vals, dtype=np.float64)
    shape = (nw,) + vals.shape[1:]
    fill = {"min": np.inf, "max": -np.inf, "sum": 0.0}[how]
    out = np.full(shape, fill)
    if not len(vid):
        return out
    o = np.argsort(vid, kind="stable")
    v, x = vid[o], vals[o]
    st = np.flatnonzero(np.r_[True, v[1:] != v[:-1]])
    red = {"min": np.minimum, "max": np.maximum, "sum": np.add}[how]
    out[v[st]] = red.reduceat(x, st, axis=0)
    return out


def dense_flat(prep: dict, lim: dict | None = None) -> dict | None:
    """Fine triangles on flat or gently curved surface in one prepared part (``form.dense_flat``).

    A welded vertex is *redundant* when a coarser mesh would not need it: it can collapse into one of its
    neighbours with every face around it staying within ``dense_fold_deg`` of that neighbour (the distance of the
    neighbour to each face plane is below tan(``dense_fold_deg``) x the edge length: a flat grid, the rings of a
    straight or gently bulged barrel, the inner loops of a subdivided surface), and its edges are shorter than
    ``dense_fine_mm`` on average (fine triangles; long triangles on flat areas are the SA way). Vertices on open
    or non-manifold edges, on a material, UV or normal seam, or between faces turned against each other
    (double-sided cards) are never redundant. Removing one redundant vertex saves two triangles, so
    ``wasted`` = 2 x redundant vertices (an estimate).

    Returns ``{"tris", "wasted", "verts", "regions": [{"tris", "wasted", "area_m2", "at", "size", "edge_mm",
    "fold_deg"}]}`` (regions = connected patches of the triangles around redundant vertices, largest first), or
    None without triangles."""
    np = _np()
    lim = lim or limits()
    T, W, keep = prep["T"], prep["W"], prep["keep"]
    ti = np.flatnonzero(keep)
    if not len(ti):
        return None
    nw = int(prep["nw"])
    P, fn, area = prep["P"], prep["fn"], prep["area"]
    Pw = np.zeros((nw, 3))
    Pw[W.reshape(-1)] = P[T.reshape(-1)]
    Wk = W[ti]
    nt = len(ti)
    out = {"tris": int(nt), "wasted": 0, "verts": 0, "regions": []}
    # open and non-manifold edges
    e0, e1 = Wk.reshape(-1), Wk[:, [1, 2, 0]].reshape(-1)
    lo, hi = np.minimum(e0, e1), np.maximum(e0, e1)
    _uk, inv, cnt = np.unique(lo * nw + hi, return_inverse=True, return_counts=True)
    bad_e = cnt[np.asarray(inv).reshape(-1)] != 2
    bad = np.zeros(nw, dtype=bool)
    bad[lo[bad_e]] = True
    bad[hi[bad_e]] = True
    # corners: (welded vertex, triangle, corner)
    cv = Wk.reshape(-1)
    ct = np.repeat(ti, 3)
    ck = np.tile(np.arange(3), nt)
    seam = np.zeros(nw, dtype=bool)

    def _split(vals, tol):
        return (_per_vertex(np, cv, vals, nw, "max") - _per_vertex(np, cv, vals, nw, "min")).reshape(nw, -1)\
            .max(axis=1) > tol

    seam |= _split(prep["M"][ct], 0.5)
    if "UV" in prep:
        seam |= _split(prep["UV"][ct, ck], 1e-4)
    if "CN" in prep:
        cn = prep["CN"][ct, ck]
        cn = cn / np.maximum(np.linalg.norm(cn, axis=1), 1e-30)[:, None]
        seam |= _split(cn, 2.0 * np.sin(np.radians(lim["hard_deg"]) / 2.0))
    # faces turned against each other around a vertex (double-sided cards, sharp folds): not a surface to thin
    ca = area[ct]
    mn = _per_vertex(np, cv, fn[ct] * ca[:, None], nw, "sum")
    asum = _per_vertex(np, cv, ca, nw, "sum")
    mlen = np.linalg.norm(mn, axis=1)
    mh = mn / np.maximum(mlen, 1e-30)[:, None]
    cosmin = _per_vertex(np, cv, (fn[ct] * mh[cv]).sum(axis=1), nw, "min")
    folded = (mlen < 0.5 * asum) | (cosmin < np.cos(np.radians(80.0)))
    # collapse candidates v -> u along every edge, the error = max distance of u to the planes around v
    pv = np.concatenate([Wk[:, 0], Wk[:, 1], Wk[:, 2], Wk[:, 0], Wk[:, 1], Wk[:, 2]])
    pu = np.concatenate([Wk[:, 1], Wk[:, 2], Wk[:, 0], Wk[:, 2], Wk[:, 0], Wk[:, 1]])
    pk = np.unique(pv * nw + pu)
    pv, pu = pk // nw, pk % nw
    o = np.argsort(cv, kind="stable")
    sv, sf = cv[o], ct[o]
    starts = np.searchsorted(sv, np.arange(nw))
    deg = np.searchsorted(sv, np.arange(nw), side="right") - starts
    reps = deg[pv]
    if not reps.sum():
        return out
    pidx = np.repeat(np.arange(len(pv)), reps)
    off = np.arange(len(pidx)) - np.repeat(np.cumsum(reps) - reps, reps)
    f = sf[np.repeat(starts[pv], reps) + off]
    dist = np.abs((fn[f] * Pw[pu[pidx]]).sum(axis=1) - (fn[f] * P[T[f, 0]]).sum(axis=1))
    pst = np.flatnonzero(np.r_[True, pidx[1:] != pidx[:-1]])
    dmax = np.zeros(len(pv))
    dmax[pidx[pst]] = np.maximum.reduceat(dist, pst)
    L = np.linalg.norm(Pw[pu] - Pw[pv], axis=1)
    ratio = dmax / np.maximum(L, 1e-12)
    best = _per_vertex(np, pv, ratio, nw, "min")
    elen = _per_vertex(np, pv, L, nw, "sum") / np.maximum(_per_vertex(np, pv, np.ones(len(pv)), nw, "sum"), 1.0)
    used = deg > 0
    red = used & ~bad & ~seam & ~folded & (best < np.tan(np.radians(float(lim["dense_fold_deg"])))) \
        & (elen < float(lim["dense_fine_mm"]) / 1000.0)
    nred = int(red.sum())
    out["verts"] = nred
    out["wasted"] = 2 * nred
    if not nred:
        return out
    rs = red[Wk].any(axis=1)
    Wr = Wk[rs]
    lab = _labels(np, Wr, nw)
    ar = area[ti][rs]
    C3 = (P[T[ti[rs], 0]] + P[T[ti[rs], 1]] + P[T[ti[rs], 2]]) / 3.0
    vlab = np.full(nw, -1, dtype=np.int64)
    vlab[Wr.reshape(-1)] = np.repeat(lab, 3)
    fold = np.degrees(np.arctan(np.where(np.isfinite(best), best, 0.0)))
    regions = []
    for k in np.unique(lab):
        sel = lab == k
        rv = red & (vlab == k)
        lo3, hi3 = C3[sel].min(axis=0), C3[sel].max(axis=0)
        a = float(ar[sel].sum())
        regions.append({"tris": int(sel.sum()), "wasted": 2 * int(rv.sum()), "area_m2": _r(a, 3),
                        "at": [_r(x) for x in (C3[sel] * ar[sel][:, None]).sum(axis=0) / max(a, 1e-30)],
                        "size": [_r(x) for x in hi3 - lo3], "edge_mm": _r(float(np.median(elen[rv])) * 1000.0, 0),
                        "fold_deg": _r(float(np.median(fold[rv])), 1)})
    regions.sort(key=lambda r: (-r["wasted"], -r["tris"], r["at"]))
    out["regions"] = regions[: int(lim["dense_regions"])]
    return out


def ray_x(np, A, B, C, y: float, z: float):
    """Hits of the line ``(*, y, z)`` along X with triangles ``A, B, C``: ``(x of the hit, face normal x)``."""
    ya, za, yb, zb, yc, zc = A[:, 1], A[:, 2], B[:, 1], B[:, 2], C[:, 1], C[:, 2]
    den = (zb - zc) * (ya - yc) + (yc - yb) * (za - zc)
    ok = np.abs(den) > 1e-14
    den = np.where(ok, den, 1.0)
    l0 = ((zb - zc) * (y - yc) + (yc - yb) * (z - zc)) / den
    l1 = ((zc - za) * (y - yc) + (ya - yc) * (z - zc)) / den
    l2 = 1.0 - l0 - l1
    hit = ok & (l0 >= -1e-9) & (l1 >= -1e-9) & (l2 >= -1e-9)
    x = l0 * A[:, 0] + l1 * B[:, 0] + l2 * C[:, 0]
    nx = np.cross(B - A, C - A)[:, 0]
    return x[hit], nx[hit]


def cover_2d(np, Q, A, B, C, a: int, b: int, chunk: int = 2_000_000):
    """For 2D points ``Q`` (S, 2) in the plane of axes ``a, b``: covered by any projected triangle."""
    out = np.zeros(len(Q), dtype=bool)
    if not len(A):
        return out
    ya, za, yb, zb, yc, zc = A[:, a], A[:, b], B[:, a], B[:, b], C[:, a], C[:, b]
    den = (zb - zc) * (ya - yc) + (yc - yb) * (za - zc)
    ok = np.abs(den) > 1e-14
    ya, za, yb, zb, yc, zc, den = ya[ok], za[ok], yb[ok], zb[ok], yc[ok], zc[ok], den[ok]
    step = max(1, chunk // max(len(ya), 1))
    for s in range(0, len(Q), step):
        y = Q[s:s + step, 0:1]
        z = Q[s:s + step, 1:2]
        l0 = ((zb - zc) * (y - yc) + (yc - yb) * (z - zc)) / den
        l1 = ((zc - za) * (y - yc) + (ya - yc) * (z - zc)) / den
        l2 = 1.0 - l0 - l1
        out[s:s + step] = ((l0 >= -1e-9) & (l1 >= -1e-9) & (l2 >= -1e-9)).any(axis=1)
    return out


def arch_outline(np, A, B, C, wheel: dict, lim: dict | None = None) -> dict | None:
    """Side outline of the body around one wheel, from the triangles outboard of the tyre's mid-plane projected
    on the side (YZ) plane: ``{angles (deg), dist (m, nan = nothing within arch_max x R), open, offset_m,
    ratio}``. ``open`` = fewer than ``arch_min_rays`` hits (no arch: a kart, a tractor, a bike)."""
    lim = lim or limits()
    cx, cy, cz = (float(v) for v in wheel["center"])
    R = float(wheel["radius"])
    side = 1.0 if cx >= 0 else -1.0
    if not len(A):
        return None
    cen = (A + B + C) / 3
    m = (np.abs(cen[:, 1] - cy) <= 2.5 * R) & (cen[:, 2] <= cz + 2.5 * R) & (cen[:, 0] * side >= abs(cx))
    if not m.any():
        return None
    A, B, C = A[m], B[m], C[m]
    a0, a1, n = lim["outline_angles"]
    angs = np.radians(np.linspace(a0, a1, int(n)))
    rs = np.arange(0.9, lim["outline_max"] + 1e-9, 0.01) * R
    Q = np.stack([(cy + np.cos(angs)[:, None] * rs[None, :]).reshape(-1),
                  (cz + np.sin(angs)[:, None] * rs[None, :]).reshape(-1)], axis=1)
    cov = cover_2d(np, Q, A, B, C, 1, 2).reshape(len(angs), len(rs))
    dist = np.full(len(angs), np.nan)
    for i in range(len(angs)):
        k = np.flatnonzero(cov[i])
        if len(k):
            dist[i] = rs[k[0]]
    hit = np.isfinite(dist)
    out = {"angles": np.degrees(angs), "dist": dist, "open": bool(hit.sum() < lim["outline_min_rays"])}
    if out["open"]:
        return out
    dys = []
    for i in range(len(angs) // 2):          # d(t) - d(180 - t) = 2 dy cos t for an arch centred dy ahead
        j = len(angs) - 1 - i
        c = np.cos(angs[i])
        if hit[i] and hit[j] and abs(c) > 0.2:
            dys.append((dist[i] - dist[j]) / (2 * c))
    out["offset_m"] = float(np.median(dys)) if dys else 0.0
    out["ratio"] = float(np.nanmedian(dist) / R)
    return out


def _body_tris(np, preps: list[dict]):
    tris = [(p["P"][p["T"][p["keep"]]]) for p in preps if p["role"] != "wheel"]
    if not tris:
        z = np.zeros((0, 3))
        return z, z, z
    X = np.concatenate(tris)
    return X[:, 0], X[:, 1], X[:, 2]


def see_through(preps: list[dict], wheels: list[dict], lim: dict | None = None, stats: dict | None = None
                ) -> list[dict]:
    """Arches you can look through: per arched wheel the share of side rays above the tyre that find no surface
    facing them in the near half of the car (``[{wheel, share, rays, at}]`` above ``arch_share``). ``stats``
    gets ``arches`` (wheels with an arch around them)."""
    np = _np()
    lim = lim or limits()
    A, B, C = _body_tris(np, preps)
    if not len(A) or not wheels:
        return []
    a0, a1, na = lim["arch_angles"]
    out = []
    for w in wheels:
        cx, cy, cz = (float(v) for v in w["center"])
        R = float(w["radius"])
        if R <= 0.05 or abs(cx) < 0.05:
            continue
        ol = arch_outline(np, A, B, C, w, lim)
        if ol is None or ol["open"]:
            continue                                   # no arch around this wheel: nothing to close
        if stats is not None:
            stats["arches"] = stats.get("arches", 0) + 1
        side = 1.0 if cx > 0 else -1.0
        bad = n = 0
        for ang in np.linspace(np.radians(a0), np.radians(a1), int(na)):
            for k in lim["arch_radii"]:
                y = cy + np.cos(ang) * R * float(k)
                z = cz + np.sin(ang) * R * float(k)
                xs, nx = ray_x(np, A, B, C, y, z)
                n += 1
                # from the outside towards the centre: near-half hits between the outside and x = 0
                near = (xs * side) > 0.0
                if not near.any():
                    bad += 1
                    continue
                first = np.argmax(xs * side * near - (~near) * 1e9)
                if nx[first] * side <= 0:          # the first face seen from outside faces away: culled
                    bad += 1
        share = bad / n if n else 0.0
        if share > lim["arch_share"]:
            out.append({"wheel": w.get("name", ""), "share": _r(share, 2), "rays": n,
                        "at": [_r(cx), _r(cy), _r(cz + R * 1.1)]})
    return out


def piece_table(preps: list[dict], pieces: list[dict]) -> list[list]:
    """``[part, label, tris, centre, planar_mm, glass, textures]`` rows (debugging and tests)."""
    return [[preps[p["part"]]["name"], p["label"], p["tris"], [_r(x) for x in p["centre"]], _r(p["planar_mm"], 1),
             p["glass"], p["textures"]] for p in pieces]


def _kind_of(name: str, lim: dict, movable: bool) -> str:
    """``panel`` (opening panels and plates: a shut-line gap is fine), ``moving`` (frames the game animates:
    gear, rotors, extras), else ``part``."""
    import re

    n = name.lower()
    if re.match(lim["panel_parts"], n):
        return "panel"
    if movable and (re.match(lim["moving_parts"], n) or re.search(lim.get("moving_any") or "(?!x)x", n)):
        return "moving"
    return "part"


def _on_world(sc: "_Scene", members: list[int], world_m: float | None = None) -> bool:
    """True when the group stands on the world ground (map models: the terrain is another model): its lowest point
    is within ``world_m`` of the model's lowest point and nothing of the model lies below it (straight down from its
    lowest points). A detail floating beside the body, high above the model's foot, is not on the ground."""
    np = sc.np
    vm = np.isin(sc.vowner, members)
    V = sc.V[vm]
    if not len(V):
        return False
    if world_m is not None and len(sc.V) and float(V[:, 2].min()) > float(sc.V[:, 2].min()) + float(world_m):
        return False
    tm = ~np.isin(sc.owner, members)
    if not tm.any():
        return True
    lo = V.min(axis=0)
    hi = V.max(axis=0)
    # only faces below the group's top and inside its footprint can be under it
    tm &= (sc.tlo[:, 2] < hi[2]) & (sc.thi[:, 0] >= lo[0]) & (sc.tlo[:, 0] <= hi[0]) \
        & (sc.thi[:, 1] >= lo[1]) & (sc.tlo[:, 1] <= hi[1])
    if not tm.any():
        return True
    A, B, C = sc.A[tm], sc.B[tm], sc.C[tm]
    order = np.argsort(V[:, 2], kind="stable")[:4]
    pts = list(V[order]) + [np.array([(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, lo[2]])]
    for p in pts:
        z = _axis_hits(np, A, B, C, 2, p)
        if (z < p[2] - 0.01).any():
            return False
    return True


def _floating(sc: "_Scene", preps, pieces, judged, touch, body, lim, movable: bool, counts: dict,
              ground_mode: str = "anchor") -> list[dict]:
    np = sc.np
    roots = _union(len(pieces), touch)
    ground = {roots[i] for i in _anchor(sc, body)}
    groups: dict = {}
    for i in range(len(pieces)):
        if roots[i] not in ground:
            groups.setdefault(roots[i], []).append(i)
    grounded = [i for i in range(len(pieces)) if roots[i] in ground]
    search = lim["gap_search_mm"] / 1000.0
    allowed = 0
    pending = dict(groups)
    gaps: dict = {}
    on_world = 0
    if ground_mode == "world":
        # map models: a group with nothing of the model under it stands on the terrain (another model); with
        # ``mount_dirs`` a group whose side reaches the model's outer box on such a side hangs on a wall or a
        # ceiling of another model (wall cabinets, wall signs, ceiling lamps)
        dirs = [d for d in (lim.get("mount_dirs") or [])]
        edge = float(lim.get("mount_mm", 50.0)) / 1000.0
        mlo = np.min([pc["lo"] for pc in pieces], axis=0)
        mhi = np.max([pc["hi"] for pc in pieces], axis=0)
        ax = {"x": 0, "y": 1, "z": 2}
        glo = np.min([pieces[i]["lo"] for i in grounded], axis=0) if grounded else mlo
        ghi = np.max([pieces[i]["hi"] for i in grounded], axis=0) if grounded else mhi
        wm = lim.get("world_mm")
        world_m = None if wm is None else float(wm) / 1000.0

        def reaches(lo, hi, d) -> bool:
            k = ax[d[1]]
            return (d[0] == "+" and hi[k] >= mhi[k] - edge) or (d[0] == "-" and lo[k] <= mlo[k] + edge)

        def mounted(mem):
            # the side is a wall (or a ceiling) only when the model's main group stands against it too: a part
            # sticking out beside the body makes that side of the box itself
            lo = np.min([pieces[i]["lo"] for i in mem], axis=0)
            hi = np.max([pieces[i]["hi"] for i in mem], axis=0)
            return any(reaches(lo, hi, d) and (not lim.get("mount_main") or reaches(glo, ghi, d)) for d in dirs)

        for key in [k for k, mem in sorted(pending.items())
                    if _on_world(sc, mem, world_m) or (dirs and mounted(mem))]:
            mem = pending.pop(key)
            grounded += mem
            on_world += len(mem)
    free = lim.get("free_parts")
    if free:
        import re

        rx = re.compile(free)
        for key in [k for k, mem in sorted(pending.items())
                    if all(rx.match(preps[pieces[i]["part"]]["name"].lower()) for i in mem)]:
            allowed += len(pending.pop(key))
    # iterative grounding: an opening panel at its shut line or a part the game animates becomes ground for what
    # touches it; cards, heads, tiny bits, glass and hidden fill may hover but carry nothing
    for _round in range(8):
        anchors = []
        for key, mem in sorted(pending.items()):
            g = _gap(sc, mem, grounded, search)
            gmm = None if g is None else g * 1000.0
            gaps[key] = gmm
            gv = float("inf") if gmm is None else gmm
            kinds = {_kind_of(preps[pieces[i]["part"]]["name"], lim, movable) for i in mem}
            mains = any(pieces[i]["main"] for i in mem)
            if mains and kinds == {"panel"} and gv <= lim["panel_gap_mm"]:
                anchors.append(key)
            elif mains and "moving" in kinds and kinds <= {"moving", "panel"} and gv <= lim["moving_gap_mm"]:
                anchors.append(key)
        if not anchors:
            break
        for key in anchors:
            mem = pending.pop(key)
            allowed += len(mem)
            grounded += mem
    for key, mem in sorted(pending.items()):
        if key not in gaps:
            g = _gap(sc, mem, grounded, search)
            gaps[key] = None if g is None else g * 1000.0
    for key in [k for k, mem in sorted(pending.items())
                if all(_exempt(pieces[i], gaps.get(k), lim) for i in mem) or _hidden(sc, mem)]:
        allowed += len(pending.pop(key))
    if ground_mode == "loose" and pending:
        # separate components (two exhaust pipes, the blocks of a LOD): a cluster of groups farther than
        # loose_near_mm from the grounded pieces stands on its own: its largest group carries the rest of it, and
        # only near misses float
        near = float(lim.get("loose_near_mm", 60.0)) / 1000.0
        keys = sorted(pending)
        far = [k for k in keys if gaps.get(k) is None or gaps[k] / 1000.0 > near]
        if len(far) <= 60:
            idx = {k: n for n, k in enumerate(far)}
            links = [(idx[a], idx[b]) for n, a in enumerate(far) for b in far[n + 1:]
                     if (lambda g: g is not None and g <= near)(_gap(sc, pending[a], pending[b], near))]
            root = _union(len(far), links)
            clusters: dict = {}
            for k in far:
                clusters.setdefault(root[idx[k]], []).append(k)
        else:
            clusters = {n: [k] for n, k in enumerate(far)}
        for ks in clusters.values():
            base = max(ks, key=lambda k: (sum(pieces[i]["tris"] for i in pending[k]),
                                          sum(pieces[i]["area"] for i in pending[k])))
            mem = pending.pop(base)
            grounded += mem
            allowed += len(mem)
        for key, mem in sorted(pending.items()):
            g = _gap(sc, mem, grounded, search)
            gaps[key] = None if g is None else g * 1000.0
        for key in [k for k, mem in sorted(pending.items()) if gaps.get(k) is None or gaps[k] / 1000.0 > near]:
            allowed += len(pending.pop(key))
    jset = set(judged)
    flo, nf, tf = [], 0, 0
    for key, mem in sorted(pending.items()):
        if not any(i in jset for i in mem):
            continue
        gmm = gaps.get(key)
        big = max(mem, key=lambda i: (pieces[i]["tris"], pieces[i]["area"]))
        lo = np.min([pieces[i]["lo"] for i in mem], axis=0)
        hi = np.max([pieces[i]["hi"] for i in mem], axis=0)
        tris = int(sum(pieces[i]["tris"] for i in mem))
        nf += len(mem)
        tf += tris
        parts_in = sorted({preps[pieces[i]["part"]]["name"] for i in mem})
        row = {"part": preps[pieces[big]["part"]]["name"], "pieces": len(mem), "tris": tris,
               "gap_mm": None if gmm is None else _r(gmm, 1), "at": [_r(x) for x in (lo + hi) / 2],
               "size": [_r(x) for x in hi - lo]}
        if len(parts_in) > 1:
            row["parts"] = parts_in
        flo.append(row)
    flo.sort(key=lambda r: (-r["tris"], r["part"]))
    counts.update(floating_pieces=nf, floating_tris=tf, hover_allowed=allowed)
    if on_world:
        counts["on_world"] = on_world
    return flo


def analyse(parts: list[dict], *, body: str | None = "chassis", wheels: list[dict] | None = None,
            focus: list[str] | None = None, checks: tuple = ("floating", "intersect", "loose_share",
                                                             "hard_corners", "see_through"),
            movable: bool = True, step: bool = False, limits_override: dict | None = None,
            ground: str = "anchor") -> dict:
    """Composition defects of a set of parts (see the module docstring).

    Args:
        parts: part dicts (model space).
        body: name of the body part (its largest piece is the ground; ``loose_share`` is measured on it);
            ``None`` or a missing part: the largest piece of all is the ground.
        wheels: ``[{name, center, radius}]`` for ``see_through`` (cars; arches only, open wheels are skipped).
        focus: part names whose pieces are judged (studio step: the changed objects); the rest is context.
        checks: which checks to run.
        movable: parts the game animates (``moving_parts``: gear, rotors, extras, misc) may hover by
            ``moving_gap_mm`` (False for bikes: their forks and bars must touch).
        step: the studio step variant of ``hard_corners`` (only the judged objects; stricter limits).
        limits_override: thresholds on top of :func:`limits`.
        ground: what counts as ground besides the body: ``anchor`` (only what touches the body: vehicles,
            peds, weapons), ``world`` (also groups with nothing of the model under them: map models stand on the
            terrain), ``loose`` (also separate components farther than ``loose_near_mm``: upgrades, pickups).

    Returns ``{"floating": [...], "intersect": [...], "loose_share": {...}?, "hard_corners": [...],
    "see_through": [...], "dense_flat": [{part, tris, wasted, share, regions}], "counts": {...}}``: defect lists
    only when found; ``counts`` always (pieces, floating_pieces, floating_tris, hover_allowed, apart, loose_share,
    folds, creases, crease_share, soft_share, dense_wasted, dense_share). ``dense_flat`` is opt-in (``checks``).
    """
    np = _np()
    lim = limits(limits_override)
    preps, pieces = pieces_of(parts, lim["weld_m"])
    out: dict = {}
    counts = {"pieces": len(pieces)}
    fset = {f.lower() for f in focus} if focus is not None else None
    judged = [i for i, pc in enumerate(pieces) if preps[pc["part"]]["role"] != "context"
              and (fset is None or preps[pc["part"]]["name"].lower() in fset)]
    sc = _Scene(np, preps, pieces) if pieces else None
    need_contacts = sc is not None and ("floating" in checks or "intersect" in checks)
    touch, cross, vtouch = _contacts(sc, lim["touch_mm"] / 1000.0, None) if need_contacts else (set(), {}, set())
    if need_contacts:
        near = {i for pair in vtouch for i in pair}
        counts["apart"] = sum(1 for i in judged if i not in near)
    if "floating" in checks and sc is not None:
        flo = _floating(sc, preps, pieces, judged, touch, body, lim, movable, counts, ground)
        if flo:
            out["floating"] = flo
    if "intersect" in checks and sc is not None and cross:
        rad = lim["deep_radius_mm"] / 1000.0
        rows = []
        jset = set(judged)
        for (i, j), pts in sorted(cross.items()):
            for a, b in ((i, j), (j, i)):
                if a not in jset or preps[pieces[a]["part"]]["role"] == "context":
                    continue
                if pieces[a]["area"] > pieces[b]["area"] or pieces[a]["tris"] < lim["deep_min_tris"]:
                    continue                                      # the smaller piece is the one inside
                depth, share = _depths(sc, a, b, rad)
                if depth * 1000.0 >= lim["deep_mm"] and share >= lim["deep_share"]:
                    c = np.mean(pts, axis=0)
                    rows.append({"part": preps[pieces[a]["part"]]["name"], "into": preps[pieces[b]["part"]]["name"],
                                 "tris": pieces[a]["tris"], "depth_mm": _r(depth * 1000.0, 1),
                                 "share": _r(share, 2), "at": [_r(x) for x in c]})
        rows.sort(key=lambda r: (-r["depth_mm"], r["part"]))
        if rows:
            out["intersect"] = rows
    if "loose_share" in checks and body:
        bp = next((p for p in preps if p["name"].lower() == body.lower() and p["role"] != "context"
                   and (fset is None or p["name"].lower() in fset)), None)
        if bp is not None:
            ls = loose_share(bp)
            if ls is not None:
                counts["loose_share"] = _r(ls[0], 3)
                if ls[0] > lim["loose_share"] and int(bp["keep"].sum()) >= lim["loose_min_tris"]:
                    out["loose_share"] = {"part": bp["name"], "share": _r(ls[0], 3), "pieces": ls[1],
                                          "tris": int(bp["keep"].sum())}
    if "hard_corners" in checks:
        rows, folds, creases, soft = [], 0, 0, 0
        for p in preps:
            if p["role"] != "hd" or (fset is not None and p["name"].lower() not in fset):
                continue
            hc = hard_corners(p, lim)
            if not hc or not hc["folds"]:
                continue
            folds += hc["folds"]
            creases += hc["creases"]
            soft += hc["soft"]
            if hc["creases"]:
                rows.append({"part": p["name"], **hc})
        share = creases / folds if folds else 0.0
        sshare = soft / folds if folds else 0.0
        counts.update(folds=folds, creases=creases, crease_share=_r(share, 3), soft_share=_r(sshare, 3))
        key = "step_" if step else ""
        if folds >= lim["corner_min_folds"] and share > lim[f"{key}corner_share"] \
                and sshare < lim[f"{key}corner_soft"]:
            # the parts that make the model boxy: their own crease share is high, or they hold many of the creases
            rows = [r for r in rows if r["share"] > lim[f"{key}corner_share"] or r["creases"] >= 0.25 * creases]
            rows.sort(key=lambda r: (-r["creases"], r["part"]))
            out["hard_corners"] = rows[:8]
    if "see_through" in checks and wheels:
        st = see_through(preps, wheels, lim, counts)
        if st:
            out["see_through"] = st
    if "dense_flat" in checks:
        rows, tris, wasted = [], 0, 0
        for p in preps:
            if p["role"] != "hd" or (fset is not None and p["name"].lower() not in fset):
                continue
            d = dense_flat(p, lim)
            if not d:
                continue
            tris += d["tris"]
            wasted += d["wasted"]
            if d["wasted"]:
                rows.append({"part": p["name"], "tris": d["tris"], "wasted": d["wasted"],
                             "share": _r(d["wasted"] / max(d["tris"], 1), 3), "regions": d["regions"]})
        share = wasted / tris if tris else 0.0
        counts.update(dense_wasted=wasted, dense_share=_r(share, 3))
        if wasted >= int(lim["dense_min_tris"]) and share >= float(lim["dense_share"]):
            # the parts that hold the waste: at least a tenth of it each, most first
            rows = [r for r in rows if r["wasted"] >= 0.1 * wasted]
            rows.sort(key=lambda r: (-r["wasted"], r["part"]))
            out["dense_flat"] = rows[:4]
    out["counts"] = counts
    return out
