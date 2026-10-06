"""Sanity checks of 2dEffect entries (satk.fx2d.check)."""

from __future__ import annotations

import pytest

from satk.fx2d.check import Resources, check_items, fxp_names, load_resources, txd_names
from satk.fx2d.schema import decode_entry

from .conftest import FXP, make_txd, raw_entries

RES = Resources(fxp={"vent", "fire"}, txd={"coronastar", "shad_exp"})


def _codes(entries, res=RES, spheres=None) -> list[tuple[str, str]]:
    items = [(f"e{i}", 0, e) for i, e in enumerate(entries)]
    return [(i.sev, i.code) for i in check_items(items, res, spheres)]


def _light(**kw):
    e = {"type": "light", "pos": [0, 0, 1], "color": [255, 255, 255, 200], "corona": "coronastar",
         "shadow": "shad_exp", "corona_size": 1.0, "far_clip": 100.0, "range": 10.0, "shadow_size": 4.0,
         "shadow_mult": 40, "shadow_z": 0, "flash": "default", "reflection": False, "flare": 0,
         "flags": ["at_night"], "look_dir": [0, 0, 100]}
    e.update(kw)
    return e


def test_synthetic_entries_of_every_type_are_clean():
    entries = [decode_entry(p, t, d, keep=False) for p, t, d in raw_entries()]
    assert _codes(entries) == []


@pytest.mark.parametrize("kw,code,sev", [
    ({"corona": "coronamoon"}, "TEX_MISSING", "warn"),
    ({"shadow": "lamp_shad_64"}, "TEX_MISSING", "warn"),
    ({"corona": ""}, "NO_TEXTURE", "warn"),
    ({"shadow": "", "shadow_size": 3.0}, "NO_TEXTURE", "warn"),
    ({"corona": "x" * 24}, "NAME_TOO_LONG", "error"),
    ({"flags": ["fog1"]}, "NEVER_SHOWN", "warn"),
    ({"corona_size": 60.0}, "RANGE", "warn"),
    ({"far_clip": 0.0}, "RANGE", "warn"),
    ({"flash": 20}, "BAD_ENUM", "warn"),
    ({"flare": 7}, "BAD_ENUM", "warn"),
    ({"flags": ["at_day", "check_direction"], "look_dir": [0, 0, 0]}, "LOOK_DIR_ZERO", "warn"),
    ({"range": "0x7FC00000"}, "NOT_FINITE", "error"),
])
def test_light_checks(kw, code, sev):
    assert (sev, code) in _codes([_light(**kw)])


def test_light_without_corona_flag_and_wet_flash_are_fine():
    assert _codes([_light(corona="", flags=["at_night", "without_corona"])]) == []
    assert _codes([_light(flags=[], flash="random_when_wet")]) == []


def test_other_types():
    p = [0, 0, 0]
    cases = [
        ({"type": "particle", "pos": p, "name": "smoke_huge"}, "PARTICLE_MISSING"),
        ({"type": "particle", "pos": p, "name": ""}, "EMPTY_NAME"),
        ({"type": "attractor", "pos": p, "atype": "scripted", "queue_dir": [0, 1, 0], "use_dir": [0, 1, 0],
          "fwd_dir": [0, 1, 0], "script": "none", "probability": 75}, "BAD_ENUM"),
        ({"type": "attractor", "pos": p, "atype": "seat", "queue_dir": [0, 2, 0], "use_dir": [0, 1, 0],
          "fwd_dir": [0, 1, 0], "script": "none", "probability": 75}, "DIR_NOT_UNIT"),
        ({"type": "attractor", "pos": p, "atype": "trigger_script", "queue_dir": [0, 1, 0], "use_dir": [0, 1, 0],
          "fwd_dir": [0, 1, 0], "script": "none", "probability": 75}, "SCRIPT_NONE"),
        ({"type": "attractor", "pos": p, "atype": "seat", "queue_dir": [0, 1, 0], "use_dir": [0, 1, 0],
          "fwd_dir": [0, 1, 0], "script": "none", "probability": 175}, "RANGE"),
        ({"type": "enex", "pos": p, "name": "", "radius": [2, 2], "time_on": 0, "time_off": 24}, "EMPTY_NAME"),
        ({"type": "enex", "pos": p, "name": "A", "radius": [0, 2], "time_on": 0, "time_off": 24}, "RANGE"),
        ({"type": "enex", "pos": p, "name": "A", "radius": [2, 2], "time_on": 30, "time_off": 24}, "RANGE"),
        ({"type": "roadsign", "pos": p, "size": [4, 2], "lines": 1, "chars": 4, "text": ["EXIT", "X", "", ""]},
         "TEXT_IGNORED"),
        ({"type": "roadsign", "pos": p, "size": [4, 2], "lines": 1, "chars": 4, "text": ["EXITS", "", "", ""]},
         "TEXT_TOO_LONG"),
        ({"type": "cover_point", "pos": p, "dir": [0.5, 0], "usage": "low_cover"}, "DIR_NOT_UNIT"),
        ({"type": "cover_point", "pos": p, "dir": [1, 0], "usage": 9}, "BAD_ENUM"),
        ({"type": "escalator", "pos": p, "up": 3}, "BAD_ENUM"),
        ({"type": "sun_glare", "pos": p}, "TYPE_NOT_READ"),
        ({"type": "interior", "pos": p, "data": "00"}, "TYPE_NOT_READ"),
        ({"type": "light", "pos": p, "data": "00" * 10}, "BAD_SIZE"),
        ({"type": "particle", "pos": ["0x7F800000", 0, 0], "name": "fire"}, "NOT_FINITE"),
    ]
    for e, code in cases:
        assert code in [c for _s, c in _codes([e])], (e, _codes([e]))


def test_missing_names_suggest_close_ones():
    issues = check_items([("e0", 0, {"type": "particle", "pos": [0, 0, 0], "name": "fyre"}),
                          ("e1", 0, _light(corona="coronstar"))], RES)
    assert "did you mean fire?" in issues[0].msg and "did you mean coronastar?" in issues[1].msg


def test_duplicates_distance_and_missing_resources():
    a = {"type": "particle", "pos": [0, 0, 1], "name": "fire"}
    assert ("warn", "DUPLICATE") in _codes([a, dict(a)])
    red, blue = _light(color=[255, 0, 0, 200]), _light(color=[0, 0, 255, 200])   # police lights: one spot, two colours
    assert _codes([red, blue]) == []
    far = dict(a, pos=[500, 0, 0])
    assert ("warn", "FAR_FROM_MODEL") in _codes([far], spheres={0: (0, 0, 0, 1)})
    sign = {"type": "roadsign", "pos": [1600, -2000, 20], "size": [4, 2], "lines": 1, "chars": 16,
            "text": ["A", "", "", ""]}
    assert _codes([sign], spheres={0: (0, 0, 0, 1)}) == []          # road signs use world coordinates
    assert _codes([dict(a, name="nothing_like_it")], res=Resources()) == []


def test_resource_readers(tmp_path):
    assert fxp_names(FXP) == {"vent", "fire"}                        # emitter NAME: lines are not systems
    assert txd_names(make_txd(["CoronaStar", "shad_exp"])) == {"coronastar", "shad_exp"}
    (tmp_path / "e.fxp").write_text(FXP, encoding="latin-1")
    res = load_resources(tmp_path / "e.fxp", None, lambda p: p.read_bytes())
    assert res.fxp == {"vent", "fire"} and res.txd is None
