"""Framing a zone: centre of the zone box, ground height from its placements, capped radius."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from satk.viewer.resolve import IndexAccess


class _Idx(IndexAccess):
    def __init__(self, zone: dict, inst_z: list[float]):
        self._zone, self._z = zone, inst_z

    def get(self, sid, fields=None):
        return self._zone

    def insts(self, **kw):
        assert kw["box"] == (self._zone["min"][0], self._zone["min"][1], self._zone["max"][0], self._zone["max"][1])
        return [SimpleNamespace(pos=(0.0, 0.0, z), sid=f"inst:x#{i}") for i, z in enumerate(self._z)]


def test_zone_centre_ground_and_radius():
    zone = {"id": "zone:gan1", "min": [2222.56, -1722.33, -89.08], "max": [2632.83, -1628.53, 110.92]}
    (x, y, z), r, o = _Idx(zone, [12.0, 13.0, 30.0, 13.5])._locate_zone(SimpleNamespace(kind="zone"))
    assert (x, y) == pytest.approx((2427.695, -1675.43))
    assert z == 13.5                       # median placement height, not the zone box centre (10.9)
    assert r == pytest.approx(0.35 * 420.86, rel=1e-3) and o is zone


def test_huge_zone_radius_is_capped_and_empty_zone_falls_back():
    zone = {"id": "zone:la", "min": [44.0, -2892.0, -242.0], "max": [2997.0, -768.0, 900.0]}
    (_x, _y, z), r, _o = _Idx(zone, [])._locate_zone(SimpleNamespace(kind="zone"))
    assert r == IndexAccess.ZONE_MAX_RADIUS and z == -232.0
