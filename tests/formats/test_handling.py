"""satk.formats.handling: handling.cfg records of every type (synthetic text, no game files)."""

from __future__ import annotations

import pytest

from satk.formats.handling import (
    BIKE_FIELDS, BOAT_FIELDS, CAR_FIELDS, FLYING_FIELDS, HANDLING_FLAGS, MODEL_FLAGS, flag_names, parse_handling,
)
from satk.formats.rw import FormatError

CAR = ("SPORTCAR     1450.0    2700.0   1.6    0.0 0.1 -0.2  72  0.75 0.8  0.52 \t5 230.0 31.0 12.0 4 P \t10.5  0.52 0 "
       "32.0  \t1.1  0.18  0.0   0.24 -0.11 0.5  0.3\t\t0.35 0.70 90000 \t40002004\t\tC04000\t\t1  1\t1")
BIKE_STD = "MOTO 410.0 210.0 4.0 0.0 0.07 -0.08 100 1.7 0.9 0.47 5 185.0 58.0 5.0 R P 14.0 0.5 0 34.0 " \
           "0.80 0.14 0.0 0.16 -0.15 0.5 0.0 0.0 0.14 9000 1002000 2 1 1 4"
BIKE = "!\tMOTO\t0.24\t0.11\t0.31\t0.12\t54.0\t37.0\t0.94\t0.61\t0.52\t0.11\t31.0\t-34.0\t-0.011\t0.71\t0.62"
BOAT = "%\tSKIFF\t0.66\t0.52\t0.48\t7.5\t0.72\t-0.45\t3.1\t0.71\t0.997\t0.998\t0.84\t0.97\t0.95\t3.5"
FLY = ("$\tRCPLANE\t0.26\t0.61\t-0.003\t0.06\t0.11\t0.008\t5.5\t0.008\t5.5\t0.41\t0.009\t0.21\t1.1\t0.1s\t0.988\t0.870"
       "\t0.871\t0.997\t0.0\t0.0\t5.5")


def test_field_tables():
    assert (len(CAR_FIELDS), len(BIKE_FIELDS), len(BOAT_FIELDS), len(FLYING_FIELDS)) == (35, 15, 14, 21)
    assert len(MODEL_FLAGS) == 32 and len(HANDLING_FLAGS) == 32


def test_all_record_types():
    text = "\n".join(["; comment", CAR, BIKE_STD, BIKE, BOAT, FLY, "^\t0\t0\t0", "^\t1\t1\t0", ";the end", "LATE 1 2"])
    h = parse_handling(text)
    assert len(h) == 5 and h.anim_groups == 2 and h.duplicates == 0
    assert [(r.name, r.kind, r.line) for r in h] == [("SPORTCAR", "car", 2), ("MOTO", "car", 3), ("MOTO", "bike", 4),
                                                       ("SKIFF", "boat", 5), ("RCPLANE", "flying", 6)]
    v = h.get("sportcar").values
    assert (v["mass"], v["turn_mass"], v["submerged"], v["gears"], v["max_vel"], v["drive"], v["engine"]) == (
        1450.0, 2700.0, 72, 5, 230.0, "4", "P")
    assert (v["value"], v["model_flags"], v["handling_flags"], v["anim_group"]) == (90000, 0x40002004, 0xC04000, 1)
    assert h.get("MOTO", "bike").values["max_lean"] == 54.0 and h.get("moto", "bike").values["stoppie_stab_mult"] == 0.62
    assert h.get("skiff", "boat").values["look_lr_behind_cam_height"] == 3.5
    f = h.get("rcplane", "flying").values
    assert f["wind_mult"] == 0.1 and f["move_res"] == 0.988 and f["speed_res_z"] == 5.5  # '0.1s': %f%*c
    assert h.get("late") is None and h.get("sportcar", "bike") is None


def test_flag_names():
    assert flag_names(0x40002004, MODEL_FLAGS) == ["IS_LOW", "DOUBLE_EXHAUST", "FORCE_GROUND_CLEARANCE"]
    assert flag_names(0xC04000, HANDLING_FLAGS) == ["WHEEL_R_WIDE", "HALOGEN_LIGHTS", "PROC_REARWHEEL_1ST"]
    assert flag_names(1 << 27 | 1, HANDLING_FLAGS) == ["1G_BOOST", "bit27"] and flag_names(0, MODEL_FLAGS) == []


def test_duplicates_replace():
    h = parse_handling(CAR + "\n" + CAR.replace("1450.0", "1500.0", 1))
    assert len(h) == 1 and h.duplicates == 1 and h.get("SPORTCAR").values["mass"] == 1500.0
    assert h.get("SPORTCAR").line == 2


@pytest.mark.parametrize("line,msg", [
    ("SHORT 1.0 2.0", "need 35"),
    (CAR.replace("4 P", "4x P"), "one character"),
    (CAR.replace("40002004", "4000200g"), "invalid literal"),
    (BOAT.replace("0.66", "0.66s"), "not a number"),
    ("!\tBIKE\t0.1", "need 15"),
])
def test_malformed_lines(line, msg):
    errs: list = []
    h = parse_handling("; x\n" + line + "\n" + CAR, errors=errs)
    assert len(h) == 1 and len(errs) == 1 and errs[0][0] == 2 and msg in errs[0][1]
    with pytest.raises(FormatError) as e:
        parse_handling(line, strict=True)
    assert e.value.kind == "handling" and e.value.offset == 1


@pytest.mark.game
def test_vanilla_handling(clean_root):
    from collections import Counter

    from satk.formats.dat import read_text, resolve_ci

    errs: list = []
    h = parse_handling(read_text(resolve_ci(clean_root, "data/handling.cfg")), errors=errs)
    assert Counter(r.kind for r in h) == {"car": 210, "flying": 24, "bike": 13, "boat": 12}
    assert (h.anim_groups, h.duplicates, errs) == (30, 0, [])
    assert h.get("INFERNUS").values["max_vel"] == 240.0 and h.get("RCRAIDER", "flying").values["wind_mult"] == 0.1
