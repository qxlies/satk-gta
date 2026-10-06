"""Token-preserving lines, handling.cfg / weapon.dat records and the field tables (synthetic text)."""

from __future__ import annotations

import pytest

from satk.addon import fields as F
from satk.addon import handling as H
from satk.addon import weapon as W
from satk.addon.add import readme_carmods_ok, readme_weapon_ok
from satk.addon.game import parse_cargrp, parse_carmods, parse_pedstats
from satk.addon.tokline import TokLine, decimals, fmt_number, parse_typed
from satk.core.errors import SatkError
from satk.formats.handling import HANDLING_FLAGS, KINDS, MODEL_FLAGS, parse_handling

from .conftest import CARGRP, CARMODS, HANDLING, PEDSTATS, WEAPON


@pytest.mark.parametrize("raw", [
    "411, \tinfernus, \tinfernus, \tcar,\t\tINFERNUS,\tINFERNU, \tnull,\texecutive, \t5, \t0,\t0,\t\t-1, 0.7, 0.7,\t\t0\r\n",
    "camper, 1,31,1,0, 1,31,1,0 \n",
    "  linerun, packer\t# POPCYCLE_GROUP_WORKERS\r\n",
    "",
    "#only a comment",
])
def test_tokline_roundtrip_with_commas(raw):
    tl = TokLine.parse(raw, commas=True, comment="#")
    assert tl.text() == raw


def test_tokline_set_and_insert_keep_the_rest():
    tl = TokLine.parse("testcar, nto_b_l,\tnto_b_s  # c\r\n", commas=True, comment="#")
    assert tl.toks == ["testcar", "nto_b_l", "nto_b_s"]
    tl.set(0, "newcar")
    tl.insert_after(len(tl) - 1, ", ", "x")
    assert tl.text() == "newcar, nto_b_l,\tnto_b_s, x  # c\r\n"
    with pytest.raises(ValueError):
        tl.set(1, "has space")


@pytest.mark.parametrize("new,typ,old,given,want", [
    (1500.0, "f", "1400.0", "1500", "1500.0"),
    (0.8, "f", "0.70", "0.8", "0.80"),
    (0.85, "f", "0.7", "0.85", "0.85"),
    (9.0, "f", "08.0", "9", "9.0"),
    (8.0, "f", "08.0", "8", "08.0"),                   # an unchanged value keeps its text
    (0x431, "x", "0431", "431", "0431"),
    (0x432, "x", "0431", "432", "0432"),
    (5.0, "f", "5", "5", "5"),
    (1e-5, "f", "-0.00006", "1e-5", "1e-5"),
    (0xC04001, "x", "C04000", "C04001", "C04001"),
    (0xC008, "x", "c008", "c008", "c008"),
    (30, "i", "25", "30", "30"),
    ("R", "c", "4", "r", "R"),
])
def test_fmt_number_keeps_the_style(new, typ, old, given, want):
    assert fmt_number(new, typ, old, given) == want


def test_parse_typed_and_decimals():
    assert parse_typed("1.0", "i") == 1 and parse_typed("ff", "x") == 255 and parse_typed("r", "c") == "R"
    assert decimals("0.70") == 2 and decimals("5") == 0 and decimals("1e5") is None
    for bad, typ in (("abc", "f"), ("1.5", "i"), ("xyz", "x"), ("ab", "c")):
        with pytest.raises(ValueError):
            parse_typed(bad, typ)


def test_handling_parse_matches_formats_and_renders_back():
    hf = H.parse(HANDLING)
    assert hf.render() == HANDLING                     # every byte, CRLF kept
    ref = parse_handling(HANDLING)
    assert len(hf.recs) == len(ref) == 7
    for r in ref:
        mine = hf.get(r.name, r.kind)
        assert mine is not None and mine.values == r.values
        assert mine.text == HANDLING.splitlines()[mine.line - 1]
    assert hf.get("tplane", "flying").values["wind_mult"] == 0.1      # the '0.1s' quirk
    assert [r.kind for r in hf.kinds_of("TBIKE")] == ["car", "bike"]


def test_handling_patch_changes_only_the_tokens():
    hf = H.parse(HANDLING)
    rec = hf.get("TESTCAR")
    new = rec.patched({"mass": (1500.0, "1500"), "drive": ("R", "r"), "model_flags": (0x40002005, "40002005")},
                      new_id="NEWCAR")
    assert new.text == car_text("NEWCAR", "1500.0").replace(" 4 P ", " R P ").replace("40002004", "40002005")
    assert rec.text == car_text("TESTCAR", "1400.0")            # the original is untouched
    out = hf.render({rec.line: new.text})
    assert out.replace(new.text, rec.text) == HANDLING
    boat = hf.get("TBOAT", "boat").patched({}, new_id="X")
    assert boat.text.startswith("%\tX\t\t0.50")


def car_text(hid: str, mass: str) -> str:
    from .conftest import car

    return car(hid, mass)


def test_check_value_and_mta_calls():
    f = F.find_field("handling", "fTractionBias", "car")[1]
    assert H.check_value(f, "0.4") == (0.4, None)
    assert H.check_value(f, "1.5")[1].startswith("OUT_OF_RANGE")
    with pytest.raises(SatkError) as e:
        H.check_value(F.find_field("handling", "nDriveType")[1], "X")
    assert e.value.code == "BAD_PARAMS"
    with pytest.raises(SatkError):
        H.check_value(F.find_field("handling", "fMass")[1], "heavy")
    hf = H.parse(HANDLING)
    rec = hf.get("TESTCAR")
    calls, warn = H.mta_calls(rec, {"mass": 1500.0, "com_z": -0.3, "drive": "R", "abs": 1, "value": 5,
                                    "handling_flags": 0xC04001}, [(400, "testcar")])
    assert calls == ['setModelHandling(400, "mass", 1500) -- testcar',
                     'setModelHandling(400, "centerOfMass", {0, 0, -0.3}) -- testcar',
                     'setModelHandling(400, "driveType", "rwd") -- testcar',
                     'setModelHandling(400, "ABS", true) -- testcar',
                     'setModelHandling(400, "handlingFlags", 0xC04001) -- testcar']
    assert any("monetary" in w for w in warn)
    _c, w2 = H.mta_calls(hf.get("TBIKE", "bike"), {"max_lean": 40.0}, [(401, "tbike")])
    assert w2 and w2[0].startswith("MTA_UNSUPPORTED")


def test_field_tables_match_the_parsers():
    t = F.table("handling")
    for kind, specs in KINDS.items():
        assert [f["key"] for f in t[kind]] == [n for n, _ in specs], kind
        assert [f["type"] for f in t[kind]] == [ty for _, ty in specs], kind
        assert all(f.get("desc") and f.get("unit") for f in t[kind])
    assert {b["bit"]: b["name"] for b in t["model_flags"]} == {i: n for i, n in enumerate(MODEL_FLAGS)}
    assert {b["bit"]: b["name"] for b in t["handling_flags"]} == {i: n for i, n in enumerate(HANDLING_FLAGS) if n}
    w = F.table("weapon")
    assert len([f for f in w["gun"] if not f.get("optional")]) == 25 and len(w["melee"]) == 11 and len(w["aim"]) == 9
    assert w["types"]["PISTOL"] == 22 and w["types"]["TEC9"] == 32 and len(w["types"]) == 47
    # every accepted name resolves back to its own field
    for kind in F.kinds("handling"):
        for f in t[kind]:
            for name in [f["key"], f["name"], *f.get("aliases", ())]:
                assert F.find_field("handling", name, kind)[1] is f


def test_find_field_names_and_errors():
    assert F.find_field("handling", "fMass")[1]["key"] == "mass"
    assert F.find_field("handling", "TRACTIONMULTIPLIER")[1]["key"] == "traction_mult"
    assert F.find_field("handling", "vecCentreOfMass.z")[1]["key"] == "com_z"
    assert F.find_field("handling", "B", "boat")[1]["key"] == "thrust_y"
    assert F.find_field("weapon", "maximum_clip_ammo", "gun")[1]["key"] == "ammo_clip"
    with pytest.raises(SatkError) as e:
        F.find_field("handling", "fMas")
    assert e.value.code == "NOT_FOUND" and "fMass" in e.value.did_you_mean
    assert F.flag_names("handling", "model_flags", 0x40002004) == ["IS_LOW", "DOUBLE_EXHAUST", "FORCE_GROUND_CLEARANCE"]


def test_weapon_dat_lines():
    wf = W.parse(WEAPON)
    assert wf.render() == WEAPON and not wf.errors and wf.end == 12
    assert [r.skill for r in wf.by_type("pistol")] == [0, 1, 2, 3]
    melee = wf.by_type("BRASSKNUCKLE")[0]
    assert melee.kind == "melee" and melee.values["model1"] == 331 and melee.values["flags"] == 1
    rocket = wf.by_type("ROCKET")[0]
    assert rocket.values["lifespan"] == 800.0 and rocket.values["spread"] == 1.0
    pistol = wf.by_type("PISTOL")[0]
    assert "speed" not in pistol.values and pistol.values["flags"] == 0x3033
    new = pistol.patched({"damage": (30, "30"), "accuracy": (0.8, "0.8")})
    assert new.text == pistol.text.replace("\t 25\t", "\t 30\t").replace("0.75 1.0", "0.80 1.0")
    with pytest.raises(SatkError):
        pistol.patched({"speed": (1.0, "1")})
    assert wf.aim("COLT45").values["aim_x"] == 0.2
    assert W.type_id("pistol") == 22 and W.type_id("NOPE") is None
    for r in wf.recs.values():
        assert readme_weapon_ok(r.text) == (r.kind != "aim"), r.text
    calls, warn = W.mta_calls(wf.by_type("PISTOL")[2], {"damage": 30, "anim_loop_end": 12, "slot": 3})
    assert calls == ['setWeaponProperty(22, "pro", "damage", 30) -- PISTOL damage',
                     'setWeaponProperty(22, "pro", "anim_loop_stop", 0.4) -- PISTOL 12 frames']
    assert warn and "weaponSlot" in warn[0]
    assert W.mta_calls(wf.by_type("PISTOL")[3], {"damage": 1})[1][0].startswith("MTA_UNSUPPORTED")


def test_small_data_files():
    mods = parse_carmods(CARMODS)
    assert mods == {"testcar": (6, ["nto_b_l", "nto_b_s", "nto_b_tw"])}
    assert readme_carmods_ok("testcar, nto_b_l, nto_b_s") and not readme_carmods_ok("testcar, banana")
    groups = parse_cargrp(CARGRP)
    assert [(g.idx, g.models, g.label) for g in groups] == [
        (0, ["testcar", "tbike"], "POPCYCLE_GROUP_WORKERS"), (1, ["tbike"], "POPCYCLE_GROUP_BUSINESS"),
        (2, ["tboat", "tplane"], "Boats")]
    stats = parse_pedstats(PEDSTATS)
    assert [s.name for s in stats] == ["STAT_PLAYER", "STAT_SENSIBLE_GUY"] and stats[1].values[0] == "17.0"
