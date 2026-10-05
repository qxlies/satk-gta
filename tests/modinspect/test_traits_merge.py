"""Ported Mod Loader rules: line trimming, record keys and equality, the dominance merge."""

from __future__ import annotations

import pytest

from satk.modinspect.merge import dominant, merge, plan_stores
from satk.modinspect.traits import (Rec, Store, cells_equal, feq, recs_equal, trait_for, trim_config_line)

from .conftest import CARCOLS, HANDLING, TEST_IDE, VEHICLES_IDE, anim_line, boat_line, car_line


@pytest.mark.parametrize("raw,want", [
    ("  a, b,c  ", "a  b c"),
    ("x 1 ; comment", "x 1"),
    ("x 1 # comment", "x 1"),
    (";the end", ""),
    ("\tTESTCAR\t1.0\r", "TESTCAR\t1.0".replace("\t", " ")),
])
def test_trim_config_line(raw, want):
    assert trim_config_line(raw) == want


def test_float_equality_is_float32_relative():
    assert feq(0.1, 0.10000000000000001)
    assert feq(1400.0, 1400.00001)          # same float32
    assert not feq(1400.0, 1400.5)
    assert cells_equal((1, 2.0, "A"), (1, 2, "A"))
    assert not cells_equal((1, 2.0, "A"), (1, 2.0, "a"))


def test_handling_keys_bundle_and_quirks():
    tr = trait_for("handling.cfg")
    st = tr.parse(HANDLING + car_line("AFTEREND") + "\n")
    # ';the end' is only a comment for Mod Loader: the line after it is read
    assert ("car", "AFTEREND") in st.recs
    assert tr.parse(HANDLING + car_line("AFTEREND") + "\n", engine=True).recs.get(("car", "AFTEREND")) is None
    assert {k[0] for k in st.recs} == {"car", "boat", "anim"}
    pre = tr.premerge(st.recs)
    assert set(pre) == {("veh", "TESTCAR"), ("veh", "FASTCAR"), ("veh", "TESTBOAT"), ("veh", "AFTEREND"), ("anim", 0)}
    assert pre[("veh", "TESTBOAT")].parts[0] is not None and pre[("veh", "TESTCAR")].parts == (None, None, None)
    # ids are case-sensitive; extra trailing tokens are ignored; short lines are dropped
    st2 = tr.parse("\n".join([car_line("testcar"), car_line("TESTCAR") + " extra tokens", "TESTCAR 1 2 3"]))
    assert set(st2.recs) == {("car", "testcar"), ("car", "TESTCAR")} and st2.failed == [3]
    # the first line of a key wins inside a merged store, the last one when the game reads the file
    two = car_line("X", 100.0) + "\n" + car_line("X", 200.0) + "\n"
    assert tr.parse(two).recs[("car", "X")].cells[1] == 100.0
    assert tr.parse(two, engine=True).recs[("car", "X")].cells[1] == 200.0


def test_handling_fixtok_and_anim():
    tr = trait_for("handling.cfg")
    plane = "$ RCX " + " ".join(["0.5"] * 13) + " 0.1s " + " ".join(["0.9"] * 7)
    st = tr.parse(plane + "\n" + anim_line(3) + "\n")
    assert ("plane", "RCX") in st.recs and st.recs[("plane", "RCX")].cells[15] == "0.1s"
    assert ("anim", 3) in st.recs
    assert tr.readme_rec(trim_config_line(car_line("NEWONE")), 1, frozenset()) is not None
    assert tr.readme_rec("Thanks to all 1 2 3", 1, frozenset()) is None


def test_carcols_keys_and_dot_quirk():
    tr = trait_for("carcols.dat")
    st = tr.parse(CARCOLS)
    assert st.recs[("col", 2)].cells == (42, 119, 161)
    assert st.recs[("car", "testcar")].cells == ("testcar", (1, 2, 2, 1))
    assert tr.readme_rec("testcar  3 3  0 0", 1, frozenset({"testcar"})) is not None
    assert tr.readme_rec("testcar  3 3  0 0", 1, frozenset()) is None       # unknown model: not a carcols line
    assert tr.readme_rec("testcar 3 3 0 0", 1, frozenset({"testcar"})) is None  # needs the ', ' spacing


def test_ide_keys():
    tr = trait_for("data/vehicles.ide")
    st = tr.parse(VEHICLES_IDE + TEST_IDE + "txdp\nchild, parent\nend\n2dfx\n1000, 1.0, 2.0, 3.0, 0, 1 2 3\nend\n")
    assert ("id", 411) not in st.recs and ("id", 401) in st.recs and ("id", 1000) in st.recs
    assert ("txdp", "child") in st.recs
    assert any(k[0] == "2dfx" and k[1] == 1000 for k in st.recs)
    line = trim_config_line("402, testboat, testboat, boat, TESTBOAT, TESTBOA, null, fancy, 10, 0, 0")
    assert tr.readme_rec(line, 1, frozenset()) is None          # 'fancy' is not a vehicle class of the pattern
    line = trim_config_line("499, newcar, newcar, car, NEWCAR, NEWCAR, null, normal, 10, 0, 0")
    assert tr.readme_rec(line, 1, frozenset()).key == ("id", 499)


def test_gtadat_and_object_eof():
    g = trait_for("gta.dat").parse("IDE DATA\\MAPS\\X.IDE\nCOLFILE 0 MODELS\\COLL\\W.COL\nEXIT\n")
    assert set(g.recs) == {("IDE", "data/maps/x.ide"), ("COLFILE", "models/coll/w.col")}
    o = trait_for("object.dat").parse("a, 1,1,1,1,1,1,1, 1,1, 1, 1,1\n* end\nb, 1,1,1,1,1,1,1, 1,1, 1, 1,1\n")
    assert list(o.recs) == [("obj", "a")]


def _st(label, recs, default=False):
    return Store(label, label, default, {r.key: r for r in recs})


def _r(key, v):
    return Rec(key, "", (v,), 1, str(v))


def test_dominance_rules():
    k = ("k",)
    d = _st("game", [_r(k, 1)], default=True)
    a, b, c = _st("a", [_r(k, 2)]), _st("b", [_r(k, 2)]), _st("c", [_r(k, 3)])
    assert dominant([d, a], k).rec.cells == (2,) and dominant([d, a], k).rule == "custom"
    o = dominant([d, a, b, c], k)                     # 2 twice, 3 once: the least common wins
    assert o.rec.cells == (3,) and o.rule == "less-common"
    o = dominant([d, a, c], k)                        # tie: the first store wins
    assert o.rec.cells == (2,) and o.rule == "tie-first" and o.winner == 1
    o = dominant([d, a, _st("e", [])], k)             # in the game and missing in one mod: removed
    assert o.rec is None and o.rule == "removed" and o.missing == [2]
    o = dominant([d, _st("same", [_r(k, 1)])], k)     # only the game's value exists
    assert o.rec.cells == (1,) and o.rule == "default"
    n = ("new",)
    o = dominant([d, _st("x", [_r(n, 5)]), _st("y", [])], n)   # not in the game: never removed
    assert o.rec.cells == (5,)


def test_plan_modes_and_readme_copy_quirk():
    tr = trait_for("handling.cfg")
    default = tr.parse(HANDLING, is_default=True)
    m1 = tr.parse(HANDLING.replace("FASTCAR 1200.0", "FASTCAR 1300.0"), label="m1", origin="m1")
    assert plan_stores(tr, default, [], []).mode == "default"
    assert plan_stores(tr, default, [m1], []).mode == "override"
    p = plan_stores(tr, default, [m1, m1], [])
    assert p.mode == "merge" and len(p.stores) == 4 and p.stores[-1].label == "readme"
    # without a game file the readme store copies the first mod: its values count twice
    k = ("k",)
    x, y = _st("x", [_r(k, 1)]), _st("y", [_r(k, 2)])
    p = plan_stores(trait_for("unknown.dat"), None, [x, y], [])
    out = {o.key: o for o in merge(p.stores, trait_for("unknown.dat"))}
    assert out[k].rec.cells == (2,) and out[k].rule == "less-common"


def test_recs_equal_with_parts():
    tr = trait_for("handling.cfg")
    a = tr.premerge(tr.parse(car_line("B") + "\n" + boat_line("B", 0.5)).recs)[("veh", "B")]
    b = tr.premerge(tr.parse(car_line("B") + "\n" + boat_line("B", 0.7)).recs)[("veh", "B")]
    assert not recs_equal(a, b) and recs_equal(a, a)
