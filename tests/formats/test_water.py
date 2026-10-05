"""satk.formats.water: water.dat / water1.dat polygons (synthetic text, no game files)."""

from __future__ import annotations

import pytest

from satk.formats.rw import FormatError
from satk.formats.water import FLAG_SHALLOW, FLAG_VISIBLE, VERTEX_FIELDS, WaterVertex, parse_water

V = "{x} {y} 0.0 0.0 0.0 0.051 0.102"


def quad(x0: float, y0: float, x1: float, y1: float, flags: str = " 1") -> str:
    return "   ".join(V.format(x=x, y=y) for x, y in ((x0, y0), (x1, y0), (x0, y1), (x1, y1))) + flags


def test_quads_triangles_flags():
    text = "processed\n" + quad(-10, -20, 30, 40) + "\n\n" + \
           "   ".join(V.format(x=x, y=y) for x, y in ((0, 0), (8, 0), (0, 8))) + "   3\n" + quad(1, 2, 3, 4, "") + "\n"
    polys = parse_water(text)
    assert [(p.idx, p.line, p.kind, p.flags) for p in polys] == [(0, 2, "quad", 1), (1, 4, "tri", 3), (2, 5, "quad", None)]
    q = polys[0]
    assert q.verts[1] == WaterVertex(30.0, -20.0, 0.0, 0.0, 0.0, 0.051, 0.102)
    assert q.verts[1].as_list() == [30.0, -20.0, 0.0, 0.0, 0.0, 0.051, 0.102] and len(VERTEX_FIELDS) == 7
    assert q.bbox == (-10.0, -20.0, 0.0, 30.0, 40.0, 0.0)
    assert polys[1].flags & FLAG_VISIBLE and polys[1].flags & FLAG_SHALLOW


def test_commas_count_as_spaces():
    text = ",".join(V.format(x=x, y=y).replace(" ", ",") for x, y in ((0, 0), (1, 0), (0, 1), (1, 1))) + ",1"
    p = parse_water(text)[0]
    assert p.kind == "quad" and p.flags == 1 and p.verts[3].x == 1.0


@pytest.mark.parametrize("line,msg", [
    ("   ".join(V.format(x=x, y=y) for x, y in ((0, 0), (1, 0))) + " 1", "2 vertices"),
    (quad(0, 0, 1, 1, " 1 7"), "extra fields"),
    ("hello world 1 2 3 4 5 6", "no vertex"),
    ("1 2 3", "no vertex"),
    (quad(0, 0, 1, 1, " x"), "could not convert"),
    ("1 2 nan 4 5 6 7  1 2 3 4 5 6 7  1 2 3 4 5 6 7 1", "no vertex"),
])
def test_malformed_lines(line, msg):
    errs: list = []
    assert parse_water("processed\n" + line + "\n" + quad(0, 0, 1, 1), errors=errs) and len(errs) == 1
    assert errs[0][0] == 2 and msg in errs[0][1]
    with pytest.raises(FormatError) as e:
        parse_water("processed\n" + line, strict=True)
    assert e.value.kind == "water" and e.value.offset == 2


def test_empty_and_header_only():
    assert parse_water("") == [] and parse_water("processed\r\n\r\n") == []
    errs: list = []
    assert parse_water("processed extra\n", errors=errs) == [] and errs


@pytest.mark.game
def test_vanilla_water(clean_root):
    from satk.formats.dat import read_text, resolve_ci

    errs: list = []
    w = parse_water(read_text(resolve_ci(clean_root, "data/water.dat")), errors=errs)
    assert (len(w), sum(p.kind == "quad" for p in w), sum(p.kind == "tri" for p in w), errs) == (307, 301, 6, [])
    assert sum(1 for p in w if p.flags & FLAG_VISIBLE) == 305 and sum(1 for p in w if p.flags & FLAG_SHALLOW) == 21
    w1 = parse_water(read_text(resolve_ci(clean_root, "data/water1.dat")))
    assert len(w1) == 267 and {p.flags for p in w1} == {None}
