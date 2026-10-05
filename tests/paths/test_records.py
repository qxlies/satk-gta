"""Field layout of path nodes and navis (satk.paths.records, M2-05)."""

from __future__ import annotations

import pytest

from satk.paths import records as R


def test_parse_node_fields(pn):
    raw = pn.node_record(2502.0, -1669.75, 13.125, 7, 15, 6, 2, flood=9, flags=0x1000 | 0x20, width16=24,
                         spawn=5, behaviour=2)
    n = R.parse_node(raw, 15, 6, vehicles=10)
    assert (n.kind, n.x, n.y, n.z) == ("car", 2502.0, -1669.75, 13.125)
    assert (n.base, n.degree, n.width, n.flood, n.spawn, n.behaviour) == (7, 2, 1.5, 9, 5, 2)
    assert (n.rec_area, n.rec_idx) == (15, 6)
    assert R.flag_names(n.flags) == ["switched_off", "not_highway"]


def test_node_kinds(pn):
    car = pn.node_record(0, 0, 0, 0, 1, 0, 0)
    boat = pn.node_record(0, 0, 0, 0, 1, 1, 0, flags=0x80)
    assert R.parse_node(car, 1, 0, vehicles=2).kind == "car"
    assert R.parse_node(boat, 1, 1, vehicles=2).kind == "boat"
    assert R.parse_node(car, 1, 2, vehicles=2).kind == "ped"  # index past the vehicle count


def test_parse_navi_fields(pn):
    raw = pn.navi_record(-2250, -2900.5, (0, 2), (-100, 7), 24, 3, 2, 1 | 4)
    v = R.parse_navi(raw, 1, 0)
    assert (v.x, v.y, v.at_area, v.at_idx) == (-2250.0, -2900.5, 0, 2)
    assert (v.dx, v.dy, v.width) == (-1.0, 0.07, 1.5)
    assert (v.lanes_to, v.lanes_away, v.light, v.bridge) == (3, 2, 1, 1)


@pytest.mark.parametrize("x,y,area", [(-3000, -3000, 0), (-2251, -2999, 0), (-2250, -3000, 1), (2999, 2999, 63),
                                      (-9999, -9999, 0), (9999, 9999, 63), (2502, -1669.75, 15)])
def test_area_of(x, y, area):
    assert R.area_of(x, y) == area


def test_addresses():
    assert R.addr(15, 6) == "15:6"
    assert R.parse_addr(" 15:6 ") == (15, 6)
    assert R.navi_addr((15 << 10) | 17) == (15, 17)
    for bad in ("15", "a:1", "1:-2", "", "1:2:3"):
        with pytest.raises(ValueError):
            R.parse_addr(bad)
