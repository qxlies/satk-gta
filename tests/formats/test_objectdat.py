"""satk.formats.objectdat: object.dat lines and the engine's '*' terminator (synthetic text, no game files)."""

from __future__ import annotations

import pytest

from satk.formats.objectdat import COL_RESPONSES, DMG_EFFECTS, FIELDS, N_REQUIRED, parse_object_dat
from satk.formats.rw import FormatError

TEXT = """;
; Title : Object.dat
;Name  Mass  TurnMass ...
# DONT USE THESE NUMBERS FOR ANY OTHER OBJECTS
crate_a,\t\t25.0,\t\t25.0\t\t0.98,\t\t0.04,\t\t50.0,\t\t0.0,  \t2.0,\t20,\t2,\t1,\t0,\t2,\t0.0, 0.0, 0.0,\t\tsome_fx
fence_b\t\t\t99999.0,        99999.0         0.99,           0.1,            50.0,           0.0,    1.0,    0,      4,      1,      0,      0,
glass_box  100.0 200.0 0.99 0.1 50.0 0.0 1.0 200 0 0 0 0 0.0 0.0 0.0 none 2.0 0.1 0.2 0.3 0.5 1 1

*********more objects*************
late_obj                 1200.0,           2400.0            0.99,       0.05,       50.0,       0.0,        1.0,  0,    0,    1,    0,    0,    0.0, 0.0, 0.0,          none
* ;end of file
"""


def test_constants():
    assert len(FIELDS) == 23 and N_REQUIRED == 12
    assert DMG_EFFECTS[202] == "breakable_remove" and COL_RESPONSES[9] == "poolball"


def test_lines_and_terminator():
    objs = parse_object_dat(TEXT)
    assert [(o.name, o.line, o.loaded) for o in objs] == [
        ("crate_a", 5, True), ("fence_b", 6, True), ("glass_box", 7, True), ("late_obj", 10, False)]
    v = objs[0].values
    assert (v["mass"], v["air_res"], v["dmg_effect"], v["col_response"], v["cam_avoid"], v["fx_type"]) == (
        25.0, 0.98, 20, 2, 1, 2)
    assert (v["fx_x"], v["fx_name"]) == (0.0, "some_fx") and "smash_mult" not in v
    assert len(objs[1].values) == 12 and "fx_name" not in objs[1].values  # 13 sscanf fields: enough
    b = objs[2].values
    assert (b["smash_mult"], b["break_vz"], b["break_rand"], b["gun_break"], b["sparks"]) == (2.0, 0.3, 0.5, 1, 1)


@pytest.mark.parametrize("line,msg", [
    ("short 1.0 2.0 3.0", "3 readable values"),
    ("bad 1.0 x 3.0 4 5 6 7 8 9 10 11 12", "1 readable values"),
])
def test_malformed_lines(line, msg):
    errs: list = []
    assert [o.name for o in parse_object_dat(line + "\n" + TEXT, errors=errs)][0] == "crate_a"
    assert errs == [(1, f"object: {msg}, need 12")]
    with pytest.raises(FormatError) as e:
        parse_object_dat(line, strict=True)
    assert e.value.kind == "object" and e.value.offset == 1


def test_values_after_a_bad_one_are_not_read():
    o = parse_object_dat("x 1 2 3 4 5 6 7 8 9 10 11 12 0.0 zz 0.0 name")[0]
    assert o.values["fx_x"] == 0.0 and "fx_y" not in o.values and "fx_name" not in o.values


@pytest.mark.game
def test_vanilla_object_dat(clean_root):
    from satk.formats.dat import read_text, resolve_ci

    errs: list = []
    objs = parse_object_dat(read_text(resolve_ci(clean_root, "data/object.dat")), errors=errs)
    assert (len(objs), sum(o.loaded for o in objs), errs) == (995, 994, [])
    assert [o.name for o in objs if not o.loaded] == ["flowera"]
