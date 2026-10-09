"""The LOD outline check (``lod.silhouette``): a LOD that keeps the HD outline covers it; slabs left of a decimated
box do not (numpy only, synthetic boxes)."""

from __future__ import annotations

import pytest

np = pytest.importorskip("numpy")

from satk.style import lodcheck as LC  # noqa: E402


def _box(lo, hi):
    x0, y0, z0 = lo
    x1, y1, z1 = hi
    P = np.array([[x, y, z] for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)], dtype=float)
    T = np.array([[0, 1, 3], [0, 3, 2], [4, 6, 7], [4, 7, 5], [0, 4, 5], [0, 5, 1], [2, 3, 7], [2, 7, 6],
                  [0, 2, 6], [0, 6, 4], [1, 5, 7], [1, 7, 3]])
    return {"pos": P, "tris": T}


def test_a_lod_with_the_hd_outline_covers_it_and_slabs_do_not():
    hd = [_box((-6, -5, 0), (6, 5, 5)), _box((-6.3, -5.3, 5), (6.3, 5.3, 5.3))]
    same = LC.compare(hd, [_box((-6, -5, 0), (6.3, 5.3, 5.3))])
    assert same["coverage"] >= 0.95 and same["spill"] < 0.1
    slabs = [_box((-6, -5, 1.9), (6, 5, 2.0)), _box((-6.3, -5.3, 5.2), (6.3, 5.3, 5.3))]
    r = LC.compare(hd, slabs)
    assert r["coverage"] < 0.3 and r["worst"] in ("side", "front")
    flat = [_box((-50, -50, 0), (50, 50, 0.5))]            # a flat road: the thin side views are left out
    assert set(LC.compare(flat, flat)["views"]) == {"top"}
