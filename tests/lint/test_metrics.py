"""Lint shading metrics: the canonical K1 numbers (``satk.style.metrics``) and the pure-Python fallback agree."""

from __future__ import annotations

import math
from array import array
from types import SimpleNamespace

import pytest

from satk.lint import metrics as LM


def _tube(sides: int, smooth: bool):
    """Open tube split per face (3 vertices per triangle); normals radial (smooth) or per face (flat)."""
    pos, tris, nrm = array("f"), array("I"), array("f")
    ring = [(math.cos(2 * math.pi * i / sides), math.sin(2 * math.pi * i / sides)) for i in range(sides)]
    for i in range(sides):
        (x0, y0), (x1, y1) = ring[i], ring[(i + 1) % sides]
        quad = [(x0, y0, 0.0), (x1, y1, 0.0), (x0, y0, 2.0), (x1, y1, 2.0)]
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        ml = math.hypot(mx, my)
        for a, b, c in ((0, 1, 2), (2, 1, 3)):
            for k in (a, b, c):
                tris.append(len(pos) // 3)
                pos.extend(quad[k])
                x, y, _z = quad[k]
                nrm.extend((x, y, 0.0) if smooth else (mx / ml, my / ml, 0.0))
    return SimpleNamespace(positions=pos, tris=tris, normals=nrm, frame=0)


@pytest.fixture
def no_k1(monkeypatch):
    monkeypatch.setattr(LM, "_K1", [None])


def test_k1_and_fallback_agree_on_a_round_tube(no_k1):
    pytest.importorskip("numpy")
    m = _tube(12, smooth=True)
    local = LM.model_shading([m], {0: "chassis"})
    LM._K1.clear()                                         # resolve K1 again
    k1 = LM.model_shading([m], {0: "chassis"})
    assert local["source"] == "local" and k1["source"] == "k1"
    assert k1["shade.normal_bend"] == pytest.approx(local["shade.normal_bend"], abs=0.01)
    assert k1["shade.flat_share"] == pytest.approx(local["shade.flat_share"], abs=0.001)
    assert 14.9 < k1["shade.normal_bend"] < 15.1            # radial corners, 30 deg sides: 15 deg
    assert "geo.median_dihedral" in k1


def test_flat_tube_is_flat_and_damage_parts_are_skipped(no_k1):
    flat, rnd = _tube(12, smooth=False), _tube(12, smooth=True)
    rnd.frame = 1
    got = LM.model_shading([flat, rnd], {0: "chassis", 1: "bonnet_dam"})
    assert got["shade.normal_bend"] == 0 and got["shade.flat_share"] == 1
    assert LM.model_shading([rnd], {1: "door_lf_vlo"}) is None


def test_mesh_metrics_takes_per_vertex_normals(no_k1):
    m = _tube(8, smooth=False)
    got = LM.mesh_metrics(m.positions, m.tris, normals=m.normals)
    assert got["geo.tris"] == 16 and got["shade.normal_bend"] == 0 and got["dff.verts_per_tri"] == 3
    assert LM.geometry_bend(m.positions, m.tris, _tube(8, smooth=True).normals) > 5
