"""Numbers of the game look (satk.look.gamelook): dirt, keys, time of day, colour filter."""

from __future__ import annotations

import pytest

from satk.look import gamelook as G


def test_dirt_formula_decoded_from_the_exe():
    # level i: c * i / 16 + 255 * (16 - i) / 16 (InitialiseDirtTexture 0x5D5BC0)
    assert G.dirt_rgb((120, 100, 80), 0) == (255, 255, 255)
    assert G.dirt_rgb((120, 100, 80), 16) == (120, 100, 80)
    assert G.dirt_rgb((120, 100, 80), 2) == (238, 236, 233)
    assert G.dirt_rgb((0, 0, 0), 8) == (128, 128, 128)
    assert G.DIRT_DEFAULT == 2 and G.DIRT_SPAWN_MAX == 14 and G.dirt_t(99) == 1.0 and G.dirt_t(-3) == 0.0


def test_lamp_paint_and_gunflash_keys():
    assert [G.lamp_index(k) for k in ((255, 175, 0), (0, 255, 200), (185, 255, 0), (255, 60, 0))] == [0, 1, 2, 3]
    assert G.lamp_index((255, 175, 0, 9)) == 0 and G.lamp_index((1, 2, 3)) is None
    assert G.paint_slot((60, 255, 0)) == 1 and G.paint_slot((255, 0, 255)) == 4 and G.paint_slot((0, 0, 0)) is None
    assert G.is_gunflash("gunflash") and G.is_gunflash("GunFlash.001") and not G.is_gunflash("m4")
    assert len(G.PAINT_DEFAULT) == 4


@pytest.mark.parametrize("t,want", [("12:00", (12, 0)), ("21:30", (21, 30)), (21.5, (21, 30)), (None, (12, 0)),
                                    ("24", (0, 0))])
def test_parse_time(t, want):
    assert G.parse_time(t) == want


@pytest.mark.parametrize("bad", ["25:00", "12:75", "noon", "1:2:3"])
def test_parse_time_rejects(bad):
    with pytest.raises(ValueError):
        G.parse_time(bad)


def test_day_night_balance_curve():
    assert G.day_night_balance(3) == 1.0 and G.day_night_balance(12) == 0.0
    assert G.day_night_balance(6, 30) == pytest.approx(0.5) and G.day_night_balance(20, 30) == pytest.approx(0.5)
    assert G.day_night_balance(23) == 1.0


def test_builtin_env_interpolates_and_grades():
    e = G.default_env("12:00")
    assert e["source"] == "builtin" and e["balance"] == 0.0 and e["weather"] == G.DEFAULT_WEATHER
    assert e["amb_obj"] == [round(v / 255, 4) for v in (210, 194, 182)]
    f = G.grade_factors(e)
    assert f == (1.9062, 1.7617, 1.4219)               # warm noon filter of EXTRASUNNY_LA
    mid = G.default_env("09:30")                      # between 07:00 and 12:00
    lo, hi = G.default_env("07:00"), G.default_env("12:00")
    for k in ("amb", "amb_obj", "dir"):
        for a, b, c in zip(lo[k], mid[k], hi[k]):
            assert min(a, c) - 1e-6 <= b <= max(a, c) + 1e-6
    assert G.default_env("23:00")["balance"] == 1.0


def test_env_at_falls_back_without_an_index(satk_home):
    e = G.env_at("12:00", profile="vanilla")
    assert e["source"] == "builtin" and e["time"] == "12:00"
    with pytest.raises(ValueError):
        G.env_at("12:00", weather="NOPE_LA")


def test_display_curve_and_lit_mult():
    assert G.display_curve(0.0) < 0.02 and G.display_curve(1.0) == 1.0
    assert G.display_curve(0.5) > 0.5                    # the default gamma brightens mid tones
    vals = [G.display_curve(i / 10) for i in range(11)]
    assert vals == sorted(vals)
    assert G.lit_mult(0.0) == G.LIT_MULT and G.lit_mult(1.0) == pytest.approx(G.LIT_MULT * G.LIT_NIGHT)
    assert abs(sum(v * v for v in G.LIGHT_DIR) - 1.0) < 1e-9 and G.LIGHT_DIR[2] > 0
