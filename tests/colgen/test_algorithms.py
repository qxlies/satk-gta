"""Geometry algorithms of satk.colgen: outer hull, QEM decimation, voxels, nearest triangles, welding."""

from __future__ import annotations

from collections import Counter

import pytest

np = pytest.importorskip("numpy")

from satk.colgen.decimate import cluster, decimate  # noqa: E402
from satk.colgen.geom import weld  # noqa: E402
from satk.colgen.hull import outer_hull  # noqa: E402
from satk.colgen.nearest import nearest_tri, outside_distance, winding  # noqa: E402
from satk.colgen.voxel import boxes, sample_triangles, spheres, surface, voxelize  # noqa: E402


def _uv_sphere(n_lat: int = 20, n_lon: int = 40):
    V = [(0.0, 0.0, 1.0)]
    for i in range(1, n_lat):
        th = np.pi * i / n_lat
        for j in range(n_lon):
            ph = 2 * np.pi * j / n_lon
            V.append((np.sin(th) * np.cos(ph), np.sin(th) * np.sin(ph), np.cos(th)))
    V.append((0.0, 0.0, -1.0))
    F = [(0, 1 + j, 1 + (j + 1) % n_lon) for j in range(n_lon)]
    for i in range(n_lat - 2):
        for j in range(n_lon):
            a, b = 1 + i * n_lon + j, 1 + i * n_lon + (j + 1) % n_lon
            F += [(a, a + n_lon, b + n_lon), (a, b + n_lon, b)]
    last, base = len(V) - 1, 1 + (n_lat - 2) * n_lon
    F += [(last, base + (j + 1) % n_lon, base + j) for j in range(n_lon)]
    return np.asarray(V), np.asarray(F)


def _edge_uses(T) -> Counter:
    c: Counter = Counter()
    for a, b, d in np.asarray(T).tolist():
        for x, y in ((a, b), (b, d), (d, a)):
            c[(min(x, y), max(x, y))] += 1
    return Counter(c.values())


def _volume(P, T) -> float:
    a, b, c = P[T[:, 0]], P[T[:, 1]], P[T[:, 2]]
    return float((np.einsum("ij,ij->i", a, np.cross(b, c)) / 6).sum())


def test_uv_sphere_fixture_is_closed_and_outward():
    V, F = _uv_sphere()
    assert set(_edge_uses(F)) == {2}
    assert _volume(V, F) > 3.5


# ----------------------------------------------------------------------------- hull
def test_hull_of_a_box_is_exact_12_triangles():
    P = np.array([[x, y, z] for x in (0, 1) for y in (0, 2) for z in (0, 3)], float)
    h = outer_hull(P, 64)
    assert len(h.T) == 12 and h.exact and h.max_dev == 0.0
    assert np.allclose(h.P.min(axis=0), 0) and np.allclose(h.P.max(axis=0), (1, 2, 3))
    assert _volume(h.P, h.T) == pytest.approx(6.0)                   # counter-clockwise from outside


@pytest.mark.parametrize("budget", [16, 32, 64, 200])
def test_hull_respects_budget_and_contains_every_point(budget):
    V, _F = _uv_sphere()
    h = outer_hull(V, budget)
    assert 12 <= len(h.T) <= budget
    assert outside_distance(V, h.P, h.T) < 1e-6
    assert set(_edge_uses(h.T)) == {2}                               # closed
    assert _volume(h.P, h.T) >= 4 * np.pi / 3 * 0.9                  # contains the sphere


def test_hull_more_faces_get_closer():
    V, _F = _uv_sphere()
    devs = [outer_hull(V, b).max_dev for b in (24, 64, 200)]
    assert devs[0] > devs[1] > devs[2]


def test_hull_of_a_flat_set_gets_a_minimum_thickness():
    rng = np.random.default_rng(3)
    P = rng.uniform(size=(50, 3))
    P[:, 2] = 0.25
    h = outer_hull(P, 64, min_size=0.02)
    assert h.P[:, 2].min() == pytest.approx(0.24) and h.P[:, 2].max() == pytest.approx(0.26)
    assert outside_distance(P, h.P, h.T) < 1e-6


# ----------------------------------------------------------------------------- decimate
def test_decimate_keeps_a_closed_mesh_closed_and_reaches_the_target():
    V, F = _uv_sphere(30, 60)
    P2, T2, src = decimate(V, F, 200, closed_pairs=True)
    assert len(T2) <= 200
    assert set(_edge_uses(T2)) == {2}
    assert _volume(P2, T2) == pytest.approx(4 * np.pi / 3, rel=0.1)
    assert len(src) == len(T2) and src.max() < len(F)


def test_decimate_keeps_attributes_and_borders():
    n = 30
    xs, ys = np.meshgrid(np.linspace(0, 10, n), np.linspace(0, 10, n))
    P = np.stack([xs.ravel(), ys.ravel(), np.zeros(n * n)], axis=1)
    T = []
    for i in range(n - 1):
        for j in range(n - 1):
            a = i * n + j
            T += [(a, a + 1, a + n + 1), (a, a + n + 1, a + n)]
    T = np.asarray(T)
    attr = (P[T].mean(axis=1)[:, 0] > 5).astype(np.int64)
    P2, T2, src = decimate(P, T, 60, attr=attr)
    assert len(T2) <= 60
    # the outline survives: same bounding rectangle, no face flipped
    assert np.allclose(P2.min(axis=0)[:2], 0) and np.allclose(P2.max(axis=0)[:2], 10)
    n2 = np.cross(P2[T2[:, 1]] - P2[T2[:, 0]], P2[T2[:, 2]] - P2[T2[:, 0]])
    assert (n2[:, 2] > 0).all()
    # each output face inherits its source face's attribute; the material border stays near x = 5
    assert set(attr[src]) == {0, 1}
    cx = P2[T2].mean(axis=1)[:, 0]
    assert (cx[attr[src] == 0] < 5.5).all() and (cx[attr[src] == 1] > 4.5).all()


def test_decimate_below_target_is_a_copy():
    V, F = _uv_sphere(6, 8)
    P2, T2, src = decimate(V, F, 10_000)
    assert len(T2) == len(F) and (src == np.arange(len(F))).all()


def test_cluster_pre_reduction():
    V, F = _uv_sphere(40, 80)
    P2, T2, src = cluster(V, F, 800)
    assert 800 < len(T2) < len(F)
    assert len(src) == len(T2)


# ----------------------------------------------------------------------------- voxels
def _cube(lo=0.0, hi=1.0):
    v = [(x, y, z) for x in (lo, hi) for y in (lo, hi) for z in (lo, hi)]
    P = np.asarray(v)
    q = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    T = []
    for a, b, c, d in q:
        T += [(a, b, c), (a, c, d)]
    return P, np.asarray(T)


def test_voxelize_fills_a_closed_shell():
    P, T = _cube()
    vx = voxelize(P, T, cells=10)
    inner = vx.centres(vx.solid)
    assert vx.solid.sum() > vx.surf.sum()                          # interior filled
    assert ((inner > -0.2) & (inner < 1.2)).all()


def test_boxes_contain_every_sample_and_hug_the_mesh():
    P, T = _cube()
    vx = voxelize(P, T, cells=12)
    bx = boxes(vx, 4)
    assert len(bx) == 1
    assert np.allclose(bx[0, :3], 0, atol=1e-9) and np.allclose(bx[0, 3:], 1, atol=1e-9)
    # an L shape needs more than one box; all samples stay covered
    P2, T2 = _cube(0.0, 1.0)
    P3 = P2 + (1.0, 0.0, 0.0)
    P4 = P2 + (0.0, 1.0, 0.0)
    PL = np.concatenate([P2, P3, P4])
    TL = np.concatenate([T2, T2 + 8, T2 + 16])
    vx = voxelize(PL, TL, cells=16)
    bx = boxes(vx, 3)
    assert 2 <= len(bx) <= 3
    S = sample_triangles(PL, TL, 0.05)
    inside = ((S[:, None, :] >= bx[None, :, :3] - 1e-6) & (S[:, None, :] <= bx[None, :, 3:] + 1e-6)).all(axis=2)
    assert inside.any(axis=1).all()


def test_spheres_cover_a_box_and_mirror_pairs():
    P, T = _cube(-1.0, 1.0)
    P = P * (1.0, 2.0, 0.5)
    vx = voxelize(P, T, cells=20)
    sp = spheres(vx, 12, mirror_x=True)
    assert 1 <= len(sp) <= 12 and (sp[:, 3] > 0).all()
    xs = sorted(round(float(x), 2) for x in sp[:, 0] if abs(x) > vx.cell / 2)
    assert xs == sorted(-x for x in xs)                             # left/right pairs


def test_surface_is_closed_and_outward():
    V, F = _uv_sphere()
    vx = voxelize(V, F, cells=16)
    SP, ST = surface(vx, smooth=0)
    assert set(_edge_uses(ST)) <= {2, 4}                            # closed (4 = edge-touching cells)
    assert _volume(SP, ST) > 0
    SP2, _ = surface(vx, smooth=4)
    assert _volume(SP2, ST) > 0


# ----------------------------------------------------------------------------- nearest / weld
def test_nearest_and_winding():
    P, T = _cube()
    q = np.array([[0.5, 0.5, 2.0], [0.5, 0.5, 0.5], [-1.0, 0.5, 0.5]])
    _i, d = nearest_tri(q, P, T)
    assert d == pytest.approx([1.0, 0.5, 1.0])
    w = winding(q, P, T)
    assert w[1] == pytest.approx(1.0, abs=1e-6) and abs(w[0]) < 1e-6
    assert outside_distance(q, P, T) == pytest.approx(1.0)


def test_weld_snaps_merges_and_drops_degenerates():
    P = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0.001, 0, 0], [1.0001, 0, 0], [0, 1, 0]], float)
    T = np.array([[0, 1, 2], [3, 4, 5], [0, 3, 1]])                 # duplicate after snapping, degenerate
    P2, T2, src = weld(P, T)
    assert len(T2) == 1 and list(src) == [0]
    assert len(P2) == 3


def test_nearest_is_exact_against_brute_force_with_needles():
    from satk.colgen.nearest import point_tri_dist

    rng = np.random.default_rng(7)
    P = rng.uniform(-5, 5, size=(300, 3))
    T = rng.integers(0, 300, size=(400, 3))
    T = T[(T[:, 0] != T[:, 1]) & (T[:, 1] != T[:, 2]) & (T[:, 0] != T[:, 2])]
    q = rng.uniform(-6, 6, size=(250, 3))
    _i, d = nearest_tri(q, P, T, k=4)
    brute = point_tri_dist(q[:, None, :], P[T[:, 0]][None], P[T[:, 1]][None], P[T[:, 2]][None]).min(axis=1)
    assert np.allclose(d, brute, atol=2e-4)
