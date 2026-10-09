"""The pure geometry of the form methods (satk.studio.shapes): section shapes, monotone interpolation, resampling,
the soft primitives and sweep paths. No Blender: these are the numbers mesh.loft / mesh.primitive / mesh.sweep
build from, and the mock world counts with them."""

from __future__ import annotations

import math

import pytest

from satk.core.errors import SatkError
from satk.studio import shapes as S
from satk.studio.mock import primitive_counts, primitive_dims

CAR = {"w": 0.98, "h": 1.2, "z": 0.22, "exp_top": 4, "exp_bottom": 3, "mid": 0.58, "crown": 0.06, "tumble": 0.74,
       "flat_bottom": True}


def test_check_shape_defaults_and_errors():
    s = S.check_shape({"w": 1, "h": 2}, "t")
    assert s == {"w": 1.0, "h": 2.0, "z": 0.0, "et": 2.0, "eb": 2.0, "mid": 0.5, "crown": 0.0, "tumble": 1.0,
                 "shoulder": 0.0, "flat": 0.0}
    assert S.check_shape({"w": 1, "h": 2, "exp": 3, "exp_top": 5}, "t")["eb"] == 3.0
    for bad, word in (({"h": 1}, "w"), ({"w": 1, "h": 1, "exp": 0.1}, "exp"), ({"w": 1, "h": 1, "tumble": 0}, "tumble"),
                      ({"w": 1, "h": 1, "crown": 0.6}, "crown"), ({"w": 1, "h": 1, "cornr": 1}, "unknown"),
                      ({"w": 1, "h": 1, "flat_bottom": "yes"}, "flat_bottom"), ("box", "shape")):
        with pytest.raises(SatkError) as ei:
            S.check_shape(bad, "mesh.loft section 2")
        assert ei.value.code == "BAD_PARAMS" and word in ei.value.msg and "section 2" in ei.value.msg


def test_shape_half_runs_top_to_bottom_with_fixed_landmarks():
    s = S.check_shape(CAR, "t")
    h = S.shape_half(s, 16)
    assert len(h) == 17
    assert h[0] == (0.0, pytest.approx(1.42)) and h[-1] == (0.0, pytest.approx(0.22))
    assert all(a >= 0 for a, _ in h)
    belt = S.shape_center(s)
    widest = max(h, key=lambda q: q[0])
    assert widest == (pytest.approx(0.98), pytest.approx(belt))      # the belt line is a vertex
    assert h.index(widest) == 8                                        # upper half gets n - n // 2 segments


def test_shape_crown_tumble_flat_bottom():
    s = S.check_shape(CAR, "t")
    h = S.shape_half(s, 24)
    top = [q for q in h if q[1] > 1.2]
    # crowned roof: the centre is the highest point, the roof edge sits lower
    assert h[0][1] == max(b for _, b in h)
    shoulder = max(top, key=lambda q: q[0])
    assert 0.02 < h[0][1] - shoulder[1] < 0.2
    # tumblehome: the roof is narrower than the belt by about the tumble ratio
    assert shoulder[0] < 0.98 * 0.8
    # flat floor: the lowest points share the floor height out to the corner; a round bottom has one floor point
    floor = [q for q in h if abs(q[1] - 0.22) < 1e-6]
    assert len(floor) >= 2 and max(a for a, _ in floor) > 0.2
    round_ = S.shape_half(S.check_shape(dict(CAR, flat_bottom=False), "t"), 24)
    assert len([q for q in round_ if abs(q[1] - 0.22) < 1e-6]) == 1
    # a barrel side: the lower side tucks in a few cm below the belt, not a vertical wall
    side = [a for a, b in h if 0.4 < b < 0.9]
    assert 0.02 < 0.98 - min(side) < 0.2


def test_shoulder_makes_a_beltline_ledge():
    plain = S.shape_half(S.check_shape(CAR, "t"), 16)
    ledge = S.shape_half(S.check_shape(dict(CAR, shoulder=0.05), "t"), 16)
    belt = S.shape_center(S.check_shape(CAR, "t"))
    # just above the belt the ledge version is already 5 cm in, the plain one still at the full width
    above = lambda h: [a for a, b in h if belt + 0.005 < b < belt + 0.03]   # noqa: E731
    assert above(ledge) and max(above(ledge)) < 0.98 - 0.04
    assert max(a for a, _ in ledge) == pytest.approx(0.98)                 # the belt itself stays the widest
    assert len(ledge) == len(plain)


def test_turn_spacing_puts_points_in_corners():
    boxy = S.check_shape({"w": 1, "h": 1, "exp": 8}, "t")
    even = S.shape_half(boxy, 16, turn=0.0)
    turn = S.shape_half(boxy, 16, turn=S.TURN_WEIGHT)

    def corner(pts):    # points in the 45-degree corner zone of the upper quadrant
        return sum(1 for a, b in pts if a > 0.6 and b > 0.5 + 0.6 * 0.5)

    assert corner(turn) > corner(even)


def test_half_to_loop_is_symmetric():
    h = S.shape_half(S.check_shape({"w": 0.5, "h": 0.4, "exp": 3}, "t"), 6)
    loop = S.half_to_loop(h)
    assert len(loop) == 12
    for k in range(1, 6):
        assert loop[12 - k] == (-h[k][0], h[k][1])


def test_pchip_is_monotone_and_exact_at_knots():
    f = S.pchip([0, 1, 2, 3], [0, 1, 1, 0])
    assert [f(x) for x in (0, 1, 2, 3)] == [0, 1, 1, 0]
    vals = [f(i / 20) for i in range(61)]
    assert max(vals) <= 1.0 + 1e-12 and min(vals) >= -1e-12        # no overshoot on the plateau
    g = S.pchip([3, 2, 1], [9, 4, 1])                               # decreasing x works too
    assert g(2.5) == pytest.approx(S.pchip([1, 2, 3], [1, 4, 9])(2.5))
    assert S.pchip([0, 1], [2, 4])(0.25) == pytest.approx(2.5)      # two points = a straight line
    with pytest.raises(ValueError):
        S.pchip([0, 1, 1], [0, 1, 2])
    rows = S.pchip_many([0, 1, 2], [[0, 10], [1, 20], [2, 30]])(1.5)
    assert rows == [pytest.approx(1.5), pytest.approx(25)]


def test_resample_keeps_ends_and_spacing():
    pts = [(0, 0), (1, 0), (1, 1)]
    r = S.resample(pts, 5)
    assert r[0] == (0.0, 0.0) and r[-1] == (1.0, 1.0) and r[2] == (pytest.approx(1.0), pytest.approx(0.0))
    loop = S.resample([(0, 0), (1, 0), (1, 1), (0, 1)], 8, closed=True)
    assert len(loop) == 8 and loop[0] == (0.0, 0.0)
    gaps = [math.dist(loop[i], loop[(i + 1) % 8]) for i in range(8)]
    assert max(gaps) - min(gaps) < 1e-9
    assert S.resample([(0, 0, 0), (0, 0, 2)], 3)[1] == (0.0, 0.0, 1.0)


def test_rounded_box_counts_and_bounds():
    v, f = S.rounded_box([1, 2, 0.5], 0.1, 1)            # a chamfered box: 6 + 12 quads, 8 corner triangles
    assert (len(v), len(f), S.tri_count(f)) == (24, 26, 44)
    v, f = S.rounded_box([1, 2, 0.5], 0.1, 3)
    lo = [min(p[i] for p in v) for i in range(3)]
    hi = [max(p[i] for p in v) for i in range(3)]
    assert [round(b - a, 6) for a, b in zip(lo, hi)] == [1.0, 2.0, 0.5]
    # every edge is shared by exactly two faces: a closed, welded shell
    edges: dict = {}
    for face in f:
        for i in range(len(face)):
            e = tuple(sorted((face[i], face[(i + 1) % len(face)])))
            edges[e] = edges.get(e, 0) + 1
    assert set(edges.values()) == {2}
    # radius = half the smallest side: coincident rings merge, still closed
    v, f = S.rounded_box([1, 1, 0.4], 0.2, 2)
    edges = {}
    for face in f:
        for i in range(len(face)):
            e = tuple(sorted((face[i], face[(i + 1) % len(face)])))
            edges[e] = edges.get(e, 0) + 1
    assert set(edges.values()) == {2}


def test_capsule_counts():
    v, f = S.capsule(0.2, 1.0, 8, 3)
    assert len(v) == 2 * 3 * 8 + 2 and S.tri_count(f) == 4 * 3 * 8
    zs = [p[2] for p in v]
    assert min(zs) == pytest.approx(-0.5) and max(zs) == pytest.approx(0.5)
    v2, _ = S.capsule(0.2, 0.4, 8, 3)                    # depth = 2 r: a sphere, the equators merge
    assert len(v2) == len(v) - 8


def test_mock_counts_the_soft_kinds():
    assert primitive_counts("rounded_box", {"size": [1, 2, 0.5], "radius": 0.1, "segments": 1}) == (24, 44)
    assert primitive_counts("capsule", {"radius": 0.2, "depth": 1, "segments": 8, "rings": 3}) == (50, 96)
    assert primitive_dims("capsule", {"radius": 0.2, "depth": 1}) == [0.4, 0.4, 1.0]
    assert primitive_dims("rounded_box", {"size": [1, 2, 0.5]}) == [1.0, 2.0, 0.5]
    for kind, p, word in (("rounded_box", {"size": 1}, "segments"), ("capsule", {"segments": 8}, "rings"),
                          ("rounded_box", {"size": 1, "segments": 2, "radius": 0.7}, "radius"),
                          ("capsule", {"radius": 0.5, "depth": 0.5, "segments": 8, "rings": 2}, "depth")):
        with pytest.raises(SatkError) as ei:
            primitive_counts(kind, p)
        assert ei.value.code == "BAD_PARAMS" and word in ei.value.msg


def test_sweep_path_resamples_a_smooth_curve():
    pts = [(0, 0, 0), (1, 0, 0), (2, 1, 0), (2, 2, 0)]
    raw = S.sweep_path(pts, None)
    assert raw == [tuple(float(c) for c in p) for p in pts]
    st = S.sweep_path(pts, 9)
    assert len(st) == 9 and st[0] == (0.0, 0.0, 0.0) and st[-1] == (2.0, 2.0, 0.0)
    gaps = [math.dist(st[i], st[i + 1]) for i in range(8)]
    assert max(gaps) / min(gaps) < 1.15                 # even stations along the curve
    loop = S.sweep_path([(1, 0, 0), (0, 1, 0), (-1, 0, 0), (0, -1, 0)], 16, closed=True)
    assert len(loop) == 16 and all(0.85 < math.hypot(x, y) < 1.15 for x, y, _ in loop)
