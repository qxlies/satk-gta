"""form.dense_flat: fine triangles on flat or gently curved surface, on synthetic meshes (no game data).

The limits were calibrated on all 14,790 vanilla models (data/style/form.json "dense_flat": 0 findings, the
highest share 0.261); these tests pin the rule: a subdivided smooth barrel is found, a vanilla-like barrel with 16
segments and no rings on the straight part is not, rich detail that turns the outline is never counted, big
triangles on flat ground are the SA way, seams and double-sided cards never count, and the verdict is a defect for
map models and advice for vehicles.
"""

from __future__ import annotations

import math

import pytest

np = pytest.importorskip("numpy")

from satk.style import check as CK  # noqa: E402
from satk.style import form as F  # noqa: E402


# ----------------------------------------------------------------------------- builders
def lathe(profile, segments, *, name="body", uv_seams=False, centre=(0.0, 0.0)):
    """A closed surface of revolution around Z: ``profile`` = [(radius, z)] from bottom to top, capped by a centre
    vertex at each end; outward winding. ``uv_seams``: every face gets its own UVs (a seam on every edge)."""
    cx, cy = centre
    P = []
    for r, z in profile:
        for k in range(segments):
            a = 2 * math.pi * k / segments
            P.append((cx + r * math.cos(a), cy + r * math.sin(a), z))
    bot, top = len(P), len(P) + 1
    P += [(cx, cy, profile[0][1]), (cx, cy, profile[-1][1])]
    T = []
    for i in range(len(profile) - 1):
        for k in range(segments):
            a, b = i * segments + k, i * segments + (k + 1) % segments
            c, d = a + segments, b + segments
            T += [(a, b, d), (a, d, c)]
    last = (len(profile) - 1) * segments
    for k in range(segments):
        T.append((bot, (k + 1) % segments, k))
        T.append((top, last + k, last + (k + 1) % segments))
    P, T = np.array(P, dtype=np.float64), np.array(T, dtype=np.int64)
    uv = None
    if uv_seams:
        uv = np.tile(np.array([[0.0, 0.0], [1.0, 0.0], [0.0, 1.0]]), (len(T), 1, 1)) \
            + np.arange(len(T))[:, None, None] * 0.001
    return {"name": name, "pos": P, "tris": T, "normals": None, "uv": uv, "mat": np.zeros(len(T), np.int64),
            "tex": ["paint"], "alpha": [255], "role": "hd"}


def subdivided_barrel(segments=32, rings=24, radius=0.27, height=0.9, bulge=0.02, **kw):
    """The bin of the first atelier run, simplified: a soft barrel with many rings and segments."""
    prof = [(radius + bulge * math.sin(math.pi * i / rings), height * i / rings) for i in range(rings + 1)]
    return lathe(prof, segments, **kw)


def grid_box(size, cells, *, name="box", double_sided=False):
    """A closed box whose faces are ``cells`` x ``cells`` flat grids (a subdivided cube). ``double_sided``: one flat
    grid plus its back faces on the same vertices (a card)."""
    n = cells
    P, T, idx = [], [], {}

    def v(p):
        key = tuple(round(c, 9) for c in p)
        if key not in idx:
            idx[key] = len(P)
            P.append(key)
        return idx[key]

    h = size / 2
    faces = [((0, 1, 2), 1), ((0, 1, 2), -1), ((1, 2, 0), 1), ((1, 2, 0), -1), ((2, 0, 1), 1), ((2, 0, 1), -1)]
    if double_sided:
        faces = [((0, 1, 2), 1)]
    for (ua, va, na), sgn in faces:
        for i in range(n):
            for j in range(n):
                q = []
                for di, dj in ((0, 0), (1, 0), (1, 1), (0, 1)):
                    p = [0.0, 0.0, 0.0]
                    p[ua] = -h + size * (i + di) / n
                    p[va] = -h + size * (j + dj) / n
                    p[na] = sgn * h if not double_sided else 0.0
                    q.append(v(p))
                a, b, c, d = q
                T += [(a, b, c), (a, c, d)] if sgn > 0 else [(a, c, b), (a, d, c)]
    T = np.array(T, dtype=np.int64)
    if double_sided:
        T = np.concatenate([T, T[:, [0, 2, 1]]])
    return {"name": name, "pos": np.array(P, dtype=np.float64), "tris": T, "normals": None, "uv": None,
            "mat": np.zeros(len(T), np.int64), "tex": ["paint"], "alpha": [255], "role": "hd"}


def _run(parts):
    return F.analyse(parts if isinstance(parts, list) else [parts], body=None, checks=("dense_flat",))


# ----------------------------------------------------------------------------- the rule
def test_subdivided_smooth_barrel_is_found_with_locations():
    r = _run(subdivided_barrel())
    assert r["counts"]["dense_share"] >= F.limits()["dense_share"]
    (d,) = r["dense_flat"]
    assert d["part"] == "body" and d["wasted"] >= 1000 and d["share"] == pytest.approx(r["counts"]["dense_share"])
    reg = d["regions"][0]
    assert reg["tris"] > 1000 and reg["edge_mm"] < 100 and reg["fold_deg"] < F.limits()["dense_fold_deg"]
    assert abs(reg["at"][2] - 0.45) < 0.1 and reg["size"][2] > 0.6       # the barrel side, not the caps


def test_vanilla_like_barrel_is_clean():
    # 16 segments around (22.5 deg folds), rings only where the outline turns: foot, bead, rim
    prof = [(0.25, 0.0), (0.27, 0.05), (0.27, 0.45), (0.285, 0.47), (0.27, 0.49), (0.27, 0.85), (0.24, 0.9)]
    r = _run(lathe(prof, 16))
    assert "dense_flat" not in r and r["counts"]["dense_wasted"] == 0


def test_straight_rings_are_wasted_even_when_the_circle_is_coarse():
    # 16 segments but 30 rings on a straight barrel: the rings add nothing to the outline
    r = _run(subdivided_barrel(segments=16, rings=30, bulge=0.0))
    assert r.get("dense_flat") and r["counts"]["dense_share"] > 0.8


def test_detail_that_turns_the_outline_is_never_counted():
    # many small 8-sided bolts (45 deg folds) on a lean body: rich detail, no waste
    bolts = [lathe([(0.01, 0.0), (0.012, 0.004), (0.01, 0.008)], 8, name="bolts", centre=(x * 0.03, y * 0.03))
             for x in range(10) for y in range(10)]
    parts = [lathe([(0.25, 0.0), (0.27, 0.05), (0.27, 0.85), (0.24, 0.9)], 16)] + bolts
    r = _run(parts)
    # only the centre vertices of the flat bolt caps could go (2 triangles each); the bolt sides are never counted
    assert "dense_flat" not in r and r["counts"]["dense_wasted"] == 2 * 2 * len(bolts)


def test_long_triangles_on_flat_ground_are_the_sa_way():
    # a 20 m subdivided block with 2 m cells: flat and redundant, but not fine
    r = _run(grid_box(20.0, 10))
    assert "dense_flat" not in r and r["counts"]["dense_wasted"] == 0


def test_a_fine_flat_grid_is_found():
    r = _run(grid_box(0.6, 12))
    assert r.get("dense_flat") and r["dense_flat"][0]["regions"][0]["fold_deg"] == 0.0


def test_seams_and_double_sided_cards_never_count():
    assert _run(subdivided_barrel(uv_seams=True))["counts"]["dense_wasted"] == 0
    assert _run(grid_box(0.6, 12, double_sided=True))["counts"]["dense_wasted"] == 0


def test_small_models_and_small_shares_are_quiet():
    lim = F.limits()
    tiny = _run(subdivided_barrel(segments=12, rings=4, radius=0.05, height=0.1, bulge=0.002))
    assert "dense_flat" not in tiny and tiny["counts"]["dense_wasted"] < lim["dense_min_tris"]
    # the same waste diluted by a lean part five times its size stays under the share
    big = lathe([(3.0, 0.0), (3.2, 0.5), (3.2, 6.0), (3.0, 6.5)], 1600, name="hull")
    r = _run([subdivided_barrel(segments=16, rings=8), big])
    assert r["counts"]["dense_share"] < lim["dense_share"] and "dense_flat" not in r


def test_opt_in_and_limits_match_the_data_file():
    from satk.core import resources

    assert "dense_flat" not in F.analyse([subdivided_barrel()], body=None)       # not in the default checks
    data = resources.read_json("style", "form.json")
    for k in ("dense_fold_deg", "dense_fine_mm", "dense_share", "dense_min_tris", "dense_regions"):
        assert data["limits"][k] == F.DEFAULTS[k], k
    assert data["dense_flat"]["vanilla_max_share"] < F.DEFAULTS["dense_share"]


# ----------------------------------------------------------------------------- asset.check rows
def test_map_models_get_a_defect_vehicles_get_advice():
    res = _run(subdivided_barrel())
    prop = CK.form_rows(res, False, CK.class_rules("prop"))
    car = CK.form_rows(res, True, CK.class_rules("car.sedan"))
    (pr,), (cr,) = [r for r in prop if r[0] == "form.dense_flat"], [r for r in car if r[0] == "form.dense_flat"]
    assert pr[6] == "defect" and cr[6] == "warn" and pr[9] == cr[9] == "form"
    assert "move density to where the outline turns" in pr[7] and "normals" in pr[7]
    assert "does not block" in cr[7] and "placed many times" in pr[7]
    assert CK.strict_policy("car.sedan")["form.dense_flat"].get("advice") is True
    assert "dense_flat" in CK.class_rules("prop")["checks"] and "dense_flat" in CK.class_rules("ped")["checks"]
