"""satk.formats.carcols: carcols.dat palette and per-model colour variations (synthetic text)."""

from __future__ import annotations

import pytest

from satk.formats.carcols import MAX_VARIATIONS, CarColors, parse_carcols
from satk.formats.rw import FormatError

TEXT = """
# header comment
#street, 9,73
col
0,0,0\t\t\t\t# 0 black\t\t\t\tblack
245,245,245\t\t\t# 1 white\t\t\t\twhite
42,119,161\t\t\t# 2 police car blue\t\t\tblue
# a comment between colours
77.93,96\t\t\t# 3 malachite poly\t\t\tblue
300,-1,7
end
car
admiral, 1,1, 2,2, 3,3
alpha,   1,0, 2,0   # inline comment 9,9
long, 1,1, 2,2, 3,3, 4,4, 0,0, 1,2, 2,1, 3,1, 1,3
odd, 1,2, 3
end
car4
camper, 1,31,1,0, 1,31,1,0
end
"""


def test_palette():
    errs: list = []
    cc = parse_carcols(TEXT, errors=errs)
    assert [p.idx for p in cc.palette] == [0, 1, 2, 3, 4]
    assert cc.palette[1].rgb == 0xF5F5F5 and cc.palette[1].hex == "#f5f5f5"
    assert (cc.palette[2].name, cc.palette[2].radio) == ("police car blue", "blue")
    assert cc.palette[3].rgb == (77 << 16) | (93 << 8) | 96 and cc.palette[3].line == 9  # "77.93,96" (vanilla 98)
    assert cc.palette[4].rgb == 0xFF0007 and cc.palette[4].name is None  # clamped to 0..255
    assert [e[0] for e in errs] == [9, 10] and "outside 0..255" in errs[1][1]


def test_cars_and_car4():
    cc = parse_carcols(TEXT)
    assert cc.cars["admiral"] == CarColors("admiral", 13, ((1, 1), (2, 2), (3, 3)), False)
    assert cc.cars["alpha"].sets == ((1, 0), (2, 0))  # the inline comment is not read
    assert len(cc.cars["long"].sets) == MAX_VARIATIONS == 8  # the engine keeps 8 variations
    assert cc.cars["odd"].sets == ((1, 2),)  # an unpaired colour is dropped
    assert cc.cars["camper"].four and cc.cars["camper"].sets == ((1, 31, 1, 0), (1, 31, 1, 0))
    assert cc.duplicates == 0


def test_duplicates_later_wins_and_errors():
    cc = parse_carcols("car\nfoo, 1,1\nFOO, 2,2\nend\ncar4\nfoo, 1,2,3,4\nend\n")
    assert cc.duplicates == 2 and cc.cars["foo"].sets == ((1, 2, 3, 4),) and cc.cars["foo"].four
    errs: list = []
    cc = parse_carcols("car\nlonely\nbad, x, 1\nend\ncol\n1 2\nend\n", errors=errs)
    assert cc.cars == {} and cc.palette == () and [e[0] for e in errs] == [2, 3, 6]
    with pytest.raises(FormatError) as e:
        parse_carcols("col\n1 2\nend\n", strict=True)
    assert e.value.kind == "carcols" and e.value.offset == 2
    with pytest.raises(FormatError):
        parse_carcols("col\n77.93,96\nend\n", strict=True)  # repaired lines are errors in strict mode


def test_lines_outside_sections_are_ignored():
    cc = parse_carcols("infernus, 1, 1\nend\nnothing here\n")
    assert cc.cars == {} and cc.palette == ()


@pytest.mark.game
def test_vanilla_carcols(clean_root):
    from satk.formats.dat import read_text, resolve_ci

    errs: list = []
    cc = parse_carcols(read_text(resolve_ci(clean_root, "data/carcols.dat")), errors=errs)
    assert (len(cc.palette), len(cc.cars), sum(len(c.sets) for c in cc.cars.values())) == (127, 199, 1157)
    assert sorted(n for n, c in cc.cars.items() if c.four) == ["camper", "cement", "squalo"]
    assert [e[0] for e in errs] == [127] and cc.palette[98].rgb == 0x4D5D60  # '77.93,96'
    assert cc.cars["infernus"].sets[0] == (12, 1) and (117, 227) in cc.cars["moonbeam"].sets
