"""Pure geometry of the studio form methods (stdlib only; imported by the Blender methods and the mock).

Everything here is plain Python on tuples, so it is unit-tested without Blender and the mock world counts
exactly what Blender builds.

**Section shape** (``mesh.loft`` sections and ``mesh.sweep`` profiles): a symmetric rounded section in the
plane of the section, ``a`` across (x for a loft along y) and ``b`` up::

    {"w": 0.9,            # half width (m) at the widest line
     "h": 1.2,            # total height (m)
     "z": 0.3,            # bottom (m): the section spans b = z .. z + h
     "exp": 3,            # superellipse exponent: 2 = ellipse, 3-4 = soft box, 6+ = nearly square
     "exp_top": 2.6,      #   or separately above / below the widest line
     "exp_bottom": 4,
     "mid": 0.45,         # height of the widest line (belt) as a share of h (default 0.5)
     "crown": 0.06,       # the top centre rises this much (m) over the shoulders: a crowned roof
     "tumble": 0.78,      # top half-width / widest half-width: the greenhouse leans in
     "shoulder": 0.05,    # the side steps in this much (m) at the widest line: a beltline ledge under the glass
     "flat_bottom": true} # a flat floor with a rounded corner instead of a round bottom

The half profile runs from the top centre (a = 0) down the +a side to the bottom centre (a = 0). Its points
are placed by *turn* spacing: half by arc length, half by how much the outline turns, so corners get the
vertices and long flat runs get few (vanilla SA: "density where the outline turns"). The widest line and both
centres are always vertices, so a loft keeps a belt line and a clean mirror seam.

:func:`pchip` is the monotone cubic (Fritsch-Carlson) used to interpolate shape parameters and points between
sections without overshoot. :func:`rounded_box` and :func:`capsule` build the ``mesh.primitive`` kinds of the
same names as ``(verts, faces)``; :func:`sweep_path` resamples a smooth path for ``mesh.sweep``.
"""

from __future__ import annotations

import math
from typing import Callable, Iterable, Sequence

from ..core.errors import SatkError

__all__ = ["SHAPE_KEYS", "check_shape", "shape_half", "shape_center", "lerp_shapes", "pchip", "pchip_many",
           "resample", "half_to_loop", "rounded_box", "capsule", "merge_mesh", "sweep_path", "polyline_length",
           "TURN_WEIGHT"]

SHAPE_KEYS = frozenset({"w", "h", "z", "exp", "exp_top", "exp_bottom", "mid", "crown", "tumble", "shoulder",
                        "flat_bottom"})
#: Share of the points placed by turning (the rest by arc length) with ``spacing: turn``.
TURN_WEIGHT = 0.5
_DENSE = 128
_EPS = 1e-9

Pt = tuple[float, float]


def _bad(where: str, msg: str, hint: str | None = None) -> SatkError:
    return SatkError("BAD_PARAMS", f"{where}: {msg}", hint=hint)


def _isnum(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v))


# --------------------------------------------------------------------------- section shapes


def check_shape(d, where: str) -> dict:
    """A validated shape with every key filled: ``w h z et eb mid crown tumble shoulder flat`` (``flat`` 0..1)."""
    if not isinstance(d, dict):
        raise _bad(where, "'shape' must be an object such as {\"w\": 0.9, \"h\": 1.2, \"z\": 0.3, \"exp\": 3}")
    extra = set(d) - SHAPE_KEYS
    if extra:
        raise _bad(where, f"shape: unknown key(s) {sorted(extra)}", hint=f"keys: {', '.join(sorted(SHAPE_KEYS))}")

    def num(key: str, default=None, lo=None, hi=None, *, required=False) -> float:
        v = d.get(key, default)
        if v is None:
            if required:
                raise _bad(where, f"shape.{key} is required")
            return None  # type: ignore[return-value]
        if not _isnum(v) or (lo is not None and v < lo) or (hi is not None and v > hi):
            rng = f" ({lo if lo is not None else '-inf'}..{hi if hi is not None else 'inf'})"
            raise _bad(where, f"shape.{key} must be a number{rng}, got {v!r}")
        return float(v)

    w = num("w", lo=1e-4, required=True)
    h = num("h", lo=1e-4, required=True)
    z = num("z", 0.0)
    e = num("exp", 2.0, 0.5, 20.0)
    et = num("exp_top", e, 0.5, 20.0)
    eb = num("exp_bottom", e, 0.5, 20.0)
    mid = num("mid", 0.5, 0.05, 0.95)
    crown = num("crown", 0.0, 0.0)
    if crown > (1.0 - mid) * h * 0.9:
        raise _bad(where, f"shape.crown {crown:g} is more than the height above the widest line allows "
                   f"({(1.0 - mid) * h * 0.9:.3g})", hint="a roof crown is a few cm: 0.03-0.10")
    tumble = num("tumble", 1.0, 0.05, 1.5)
    shoulder = num("shoulder", 0.0, 0.0, w * 0.5)
    fb = d.get("flat_bottom", False)
    if isinstance(fb, bool):
        flat = 1.0 if fb else 0.0
    elif _isnum(fb) and 0 <= fb <= 1:
        flat = float(fb)
    else:
        raise _bad(where, f"shape.flat_bottom must be true or false, got {fb!r}")
    return {"w": w, "h": h, "z": z, "et": et, "eb": eb, "mid": mid, "crown": crown, "tumble": tumble,
            "shoulder": shoulder, "flat": flat}


_LERP_KEYS = ("w", "h", "z", "et", "eb", "mid", "crown", "tumble", "shoulder", "flat")


def lerp_shapes(a: dict, b: dict, t: float) -> dict:
    return {k: a[k] + (b[k] - a[k]) * t for k in _LERP_KEYS}


def shape_center(s: dict) -> float:
    """``b`` of the widest line (the centre for part angles)."""
    return s["z"] + s["mid"] * s["h"]


def _se(c: float, e: float) -> float:
    """``|c| ** (2 / e)`` with the sign of ``c`` (superellipse coordinate)."""
    return math.copysign(abs(c) ** (2.0 / e), c)


def _upper(s: dict, n: int) -> list[Pt]:
    """Dense upper quadrant from the widest point (w, zm) to the top centre (0, top); a ``shoulder`` first steps in
    along a slightly rising ledge, then the quadrant starts from the ledge's inner edge."""
    w0, h, z, e = s["w"], s["h"], s["z"], s["et"]
    zm0 = z + s["mid"] * h
    sh = min(s.get("shoulder", 0.0), w0 * 0.5)
    rise = min(sh * 0.3, (z + h - zm0) * 0.3)
    ledge = [(w0 - sh * k / 8, zm0 + rise * k / 8) for k in range(8)] if sh > 1e-6 else []
    w, zm = w0 - sh, zm0 + rise
    hu = z + h - zm
    crown = min(s["crown"], hu * 0.9)
    tumble = s["tumble"]
    out = list(ledge)
    for i in range(n + 1):
        t = (math.pi / 2) * i / n
        x = w * _se(math.cos(t), e)
        b = zm + (hu - crown) * _se(math.sin(t), e)
        u = x / w if w > _EPS else 0.0
        b += crown * (1.0 - u * u)
        v = (b - zm) / hu if hu > _EPS else 0.0
        x *= 1.0 - (1.0 - tumble) * v
        out.append((x, b))
    out[-1] = (0.0, z + h)
    return out


def _lower(s: dict, n: int) -> list[Pt]:
    """Dense lower quadrant from the widest point (w, zm) to the bottom centre (0, z); ``flat`` turns the bottom of
    the quadrant into a straight floor with a rounded corner of radius about min(w, lower height)."""
    w, h, z, e = s["w"], s["h"], s["z"], s["eb"]
    zm = z + s["mid"] * h
    hl = zm - z
    a = w + s["flat"] * (min(w, hl) - w)       # x radius of the corner arc
    cx = w - a                                  # its centre
    out = []
    for i in range(n + 1):
        t = (math.pi / 2) * i / n
        out.append((cx + a * _se(math.cos(t), e), zm - hl * _se(math.sin(t), e)))
    if cx > 1e-6:                               # the straight floor to the centre line
        m = max(2, int(n * cx / max(w, _EPS)))
        out += [(cx * (1.0 - k / m), z) for k in range(1, m + 1)]
    out[-1] = (0.0, z)
    return out


def _dist(p: Pt, q: Pt) -> float:
    return math.hypot(q[0] - p[0], q[1] - p[1])


def polyline_length(pts: Sequence[Sequence[float]], closed: bool = False) -> float:
    seq = list(pts) + ([pts[0]] if closed and pts else [])
    return sum(math.dist(seq[i], seq[i + 1]) for i in range(len(seq) - 1))


def resample(pts: Sequence[Sequence[float]], n: int, *, closed: bool = False, turn: float = 0.0) -> list[tuple]:
    """``n`` points along a polyline (``n`` segments' worth for a loop, else ``n`` points from end to end).

    ``turn`` (0..1) is the share placed by turning angle (corners) instead of arc length. Works on 2D or 3D
    points. The first point is kept; an open polyline keeps its last point too.
    """
    seq = [tuple(float(x) for x in p) for p in pts]
    if closed:
        seq = seq + [seq[0]]
    m = len(seq)
    if m < 2:
        return [seq[0]] * n
    lens = [math.dist(seq[i], seq[i + 1]) for i in range(m - 1)]
    total = sum(lens)
    if total < 1e-12:
        return [seq[0]] * n
    # turning at each inner vertex (radians), split half to each neighbouring segment
    turns = [0.0] * (m - 1)
    if turn > 0:
        def ang(i: int) -> float:
            a, b, c = seq[i - 1], seq[i], seq[i + 1]
            u = [b[k] - a[k] for k in range(len(a))]
            v = [c[k] - b[k] for k in range(len(a))]
            lu, lv = math.sqrt(sum(x * x for x in u)), math.sqrt(sum(x * x for x in v))
            if lu < 1e-12 or lv < 1e-12:
                return 0.0
            cs = max(-1.0, min(1.0, sum(x * y for x, y in zip(u, v)) / (lu * lv)))
            return math.acos(cs)

        idx = list(range(1, m - 1))
        if closed and m > 3:
            idx.append(0)   # the closing corner (seq[0] == seq[-1])
        for i in idx:
            if i == 0:
                a, b, c = seq[-2], seq[0], seq[1]
                u = [b[k] - a[k] for k in range(len(a))]
                v = [c[k] - b[k] for k in range(len(a))]
                lu, lv = math.sqrt(sum(x * x for x in u)), math.sqrt(sum(x * x for x in v))
                th = 0.0 if lu < 1e-12 or lv < 1e-12 else math.acos(
                    max(-1.0, min(1.0, sum(x * y for x, y in zip(u, v)) / (lu * lv))))
                turns[-1] += th / 2
                turns[0] += th / 2
            else:
                th = ang(i)
                turns[i - 1] += th / 2
                turns[i] += th / 2
    tt = sum(turns)
    tw = turn if tt > 1e-6 else 0.0
    wts = [(1.0 - tw) * lens[i] / total + (tw * turns[i] / tt if tw else 0.0) for i in range(m - 1)]
    cum = [0.0]
    for x in wts:
        cum.append(cum[-1] + x)
    W = cum[-1]
    targets = [W * k / n for k in range(n)] if closed else [W * k / (n - 1) for k in range(n)]
    out, i = [], 0
    for t in targets:
        while i < m - 2 and cum[i + 1] < t - 1e-12:
            i += 1
        span = cum[i + 1] - cum[i]
        f = 0.0 if span < 1e-15 else min(1.0, max(0.0, (t - cum[i]) / span))
        a, b = seq[i], seq[i + 1]
        out.append(tuple(a[k] + (b[k] - a[k]) * f for k in range(len(a))))
    if not closed:
        out[-1] = seq[-1]
    return out


def shape_half(s: dict, n_half: int, *, turn: float = TURN_WEIGHT) -> list[Pt]:
    """The half profile of a checked shape: ``n_half + 1`` points from the top centre to the bottom centre."""
    if n_half < 2:
        raise _bad("shape", f"a shape section needs at least 4 samples, got {2 * n_half}")
    n_lo = n_half // 2
    n_up = n_half - n_lo
    up = resample(_upper(s, _DENSE), n_up + 1, turn=turn)          # side -> top
    lo = resample(_lower(s, _DENSE), n_lo + 1, turn=turn)          # side -> bottom
    half = [tuple(p) for p in reversed(up)] + [tuple(p) for p in lo[1:]]
    half[0] = (0.0, half[0][1])
    half[-1] = (0.0, half[-1][1])
    return [(float(a), float(b)) for a, b in half]


def half_to_loop(half: Sequence[Pt]) -> list[Pt]:
    """A closed loop from a half profile (top centre ... bottom centre): the half, then its mirror back up."""
    return list(half) + [(-a, b) for a, b in reversed(half[1:-1])]


# --------------------------------------------------------------------------- monotone interpolation


def _slopes(x: Sequence[float], y: Sequence[float]) -> list[float]:
    n = len(x)
    h = [x[i + 1] - x[i] for i in range(n - 1)]
    d = [(y[i + 1] - y[i]) / h[i] for i in range(n - 1)]
    if n == 2:
        return [d[0], d[0]]
    m = [0.0] * n
    for i in range(1, n - 1):
        if d[i - 1] * d[i] <= 0:
            m[i] = 0.0
        else:
            w1, w2 = 2 * h[i] + h[i - 1], h[i] + 2 * h[i - 1]
            m[i] = (w1 + w2) / (w1 / d[i - 1] + w2 / d[i])

    def end(h0, h1, d0, d1):
        v = ((2 * h0 + h1) * d0 - h0 * d1) / (h0 + h1)
        if v * d0 <= 0:
            return 0.0
        if d0 * d1 <= 0 and abs(v) > 3 * abs(d0):
            return 3 * d0
        return v

    m[0] = end(h[0], h[1], d[0], d[1])
    m[-1] = end(h[-1], h[-2], d[-1], d[-2])
    return m


def pchip(x: Sequence[float], y: Sequence[float]) -> Callable[[float], float]:
    """Monotone cubic interpolant through ``(x, y)`` (``x`` strictly increasing or decreasing); clamps outside."""
    xs, ys = [float(v) for v in x], [float(v) for v in y]
    if len(xs) != len(ys) or len(xs) < 2:
        raise ValueError("pchip needs two or more points")
    if xs[0] > xs[-1]:
        xs, ys = xs[::-1], ys[::-1]
    if any(xs[i + 1] <= xs[i] for i in range(len(xs) - 1)):
        raise ValueError("pchip: x must be strictly monotonic")
    m = _slopes(xs, ys)

    def f(t: float) -> float:
        if t <= xs[0]:
            return ys[0]
        if t >= xs[-1]:
            return ys[-1]
        lo, hi = 0, len(xs) - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if xs[mid] <= t:
                lo = mid
            else:
                hi = mid
        h = xs[hi] - xs[lo]
        s = (t - xs[lo]) / h
        h00 = (1 + 2 * s) * (1 - s) ** 2
        h10 = s * (1 - s) ** 2
        h01 = s * s * (3 - 2 * s)
        h11 = s * s * (s - 1)
        return h00 * ys[lo] + h10 * h * m[lo] + h01 * ys[hi] + h11 * h * m[hi]

    return f


def pchip_many(x: Sequence[float], rows: Sequence[Sequence[float]]) -> Callable[[float], list[float]]:
    """``pchip`` per column of ``rows`` (one row per ``x``)."""
    cols = list(zip(*rows))
    fs = [pchip(x, c) for c in cols]
    return lambda t: [f(t) for f in fs]


# --------------------------------------------------------------------------- closed primitives


def merge_mesh(verts: list, faces: list, eps: float = 1e-7) -> tuple[list, list]:
    """Merge coincident vertices; faces lose repeated corners, faces with fewer than 3 distinct corners go."""
    key: dict = {}
    remap: list[int] = []
    out_v: list = []
    q = 1.0 / eps
    for v in verts:
        k = tuple(int(round(c * q)) for c in v)
        j = key.get(k)
        if j is None:
            j = key[k] = len(out_v)
            out_v.append(tuple(float(c) for c in v))
        remap.append(j)
    out_f = []
    for f in faces:
        g = []
        for i in f:
            j = remap[i]
            if not g or g[-1] != j:
                g.append(j)
        while len(g) > 1 and g[0] == g[-1]:
            g.pop()
        if len(set(g)) >= 3 and len(set(g)) == len(g):
            out_f.append(tuple(g))
    return out_v, out_f


def rounded_box(size: Sequence[float], radius: float, segments: int) -> tuple[list, list]:
    """A box ``size`` (x, y, z) centred on the origin with every edge and corner rounded by ``radius`` in
    ``segments`` steps per quarter (1 = a chamfer). Rings run along y; faces are quads with triangles at the 8
    corner tips. Returns merged ``(verts, faces)``."""
    hx, hy, hz = (float(s) / 2 for s in size)
    r = min(float(radius), hx, hy, hz)
    n = int(segments)
    ix, iy, iz = hx - r, hy - r, hz - r
    verts: list = []
    rings: list[list[int]] = []
    for s, js in ((-1, range(n, -1, -1)), (1, range(0, n + 1))):
        for j in js:
            th = (math.pi / 2) * j / n
            y = s * (iy + r * math.sin(th))
            c = r * math.cos(th)
            ring = []
            for qx, qz, base in ((1, 1, 0.0), (-1, 1, 90.0), (-1, -1, 180.0), (1, -1, 270.0)):
                for k in range(n + 1):
                    ph = math.radians(base + 90.0 * k / n)
                    ring.append(len(verts))
                    verts.append((qx * ix + c * math.cos(ph), y, qz * iz + c * math.sin(ph)))
            rings.append(ring)
    faces: list = []
    for r0, r1 in zip(rings, rings[1:]):
        m = len(r0)
        for k in range(m):
            k1 = (k + 1) % m
            faces.append((r0[k], r0[k1], r1[k1], r1[k]))
    faces.append(tuple(reversed(rings[0])))
    faces.append(tuple(rings[-1]))
    return merge_mesh(verts, faces)


def capsule(radius: float, depth: float, segments: int, rings: int) -> tuple[list, list]:
    """A capsule along z: total length ``depth`` (>= 2 radius), ``segments`` around, ``rings`` latitude steps per
    hemisphere. Returns merged ``(verts, faces)`` (quads, triangle fans at the poles)."""
    r, n, m = float(radius), int(segments), int(rings)
    c = max(0.0, float(depth) / 2 - r)
    verts: list = [(0.0, 0.0, -c - r)]
    circles: list[list[int]] = []
    lats = [(-c, -(math.pi / 2) * j / m) for j in range(m - 1, -1, -1)] + \
           [(c, (math.pi / 2) * j / m) for j in range(0, m)]
    for zc, la in lats:
        ring = []
        for k in range(n):
            ph = 2 * math.pi * k / n
            ring.append(len(verts))
            verts.append((r * math.cos(la) * math.cos(ph), r * math.cos(la) * math.sin(ph), zc + r * math.sin(la)))
        circles.append(ring)
    top = len(verts)
    verts.append((0.0, 0.0, c + r))
    faces: list = []
    first = circles[0]
    for k in range(n):
        faces.append((0, first[(k + 1) % n], first[k]))
    for r0, r1 in zip(circles, circles[1:]):
        for k in range(n):
            k1 = (k + 1) % n
            faces.append((r0[k], r0[k1], r1[k1], r1[k]))
    last = circles[-1]
    for k in range(n):
        faces.append((top, last[k], last[(k + 1) % n]))
    return merge_mesh(verts, faces)


def tri_count(faces: Iterable[Sequence[int]]) -> int:
    return sum(len(f) - 2 for f in faces)


# --------------------------------------------------------------------------- sweep paths


def _catmull(p0, p1, p2, p3, t: float, alpha: float = 0.5) -> tuple:
    """A point of the centripetal Catmull-Rom segment p1 -> p2."""
    def tj(ti, a, b):
        d = math.dist(a, b)
        return ti + max(d, 1e-9) ** alpha

    t0 = 0.0
    t1 = tj(t0, p0, p1)
    t2 = tj(t1, p1, p2)
    t3 = tj(t2, p2, p3)
    tt = t1 + (t2 - t1) * t

    def lerp(a, b, ta, tb):
        if tb - ta < 1e-12:
            return a
        return tuple(a[k] * (tb - tt) / (tb - ta) + b[k] * (tt - ta) / (tb - ta) for k in range(len(a)))

    a1 = lerp(p0, p1, t0, t1)
    a2 = lerp(p1, p2, t1, t2)
    a3 = lerp(p2, p3, t2, t3)
    b1 = lerp(a1, a2, t0, t2)
    b2 = lerp(a2, a3, t1, t3)
    return lerp(b1, b2, t1, t2)


def sweep_path(points: Sequence[Sequence[float]], samples: int | None, *, closed: bool = False,
               smooth: bool = True) -> list[tuple]:
    """Stations of a sweep: the path ``points`` as given (``samples`` None), or ``samples`` stations spaced
    evenly along a centripetal Catmull-Rom curve through them (``smooth``) or along the polyline."""
    pts = [tuple(float(c) for c in p) for p in points]
    if samples is None:
        return pts
    if smooth and len(pts) >= 3:
        ext = ([pts[-1]] + pts + [pts[0], pts[1]]) if closed else \
            ([tuple(2 * a - b for a, b in zip(pts[0], pts[1]))] + pts + [tuple(2 * a - b for a, b in zip(pts[-1], pts[-2]))])
        dense = []
        segs = len(pts) if closed else len(pts) - 1
        per = 24
        for i in range(segs):
            p0, p1, p2, p3 = ext[i], ext[i + 1], ext[i + 2], ext[i + 3]
            for k in range(per):
                dense.append(_catmull(p0, p1, p2, p3, k / per))
        if not closed:
            dense.append(pts[-1])
        return resample(dense, samples, closed=closed)
    return resample(pts, samples, closed=closed)
