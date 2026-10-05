"""satk.formats.timecyc: timecyc.dat with the engine's sscanf semantics (synthetic text, no game files)."""

from __future__ import annotations

import pytest

from satk.formats.rw import FormatError
from satk.formats.timecyc import GROUPS, HOURS, N_REQUIRED, SLOTS, WEATHERS, parse_timecyc

#: 51 values: ambient = weather, far clip = 100 * (hour index + 1).
LINE = ("{a} {a} {a}\t210 194 182\t255 255 255\t68 117 210\t36 117 199\t255 255 255\t255 255 255\t1.10 1.00 0.00 "
        "236 0 190\t{far:.2f} 10.00 0.00 44 34 23\t145 164 183\t90 170 170 240\t255 66 66 48\t255 166 129 60\t25 180 0")


def make(weathers: int = 23, hours: int = 8, names: bool = True, short: tuple[int, int] | None = None,
         extra: str = "") -> str:
    out = []
    for w in range(weathers):
        if names:
            out.append(f"//////////// NAME_{w}")
        out.append("//Amb Amb_Obj Dir Sky top Sky bot")
        for h in range(hours):
            out.append(f"//hour {h}")
            line = LINE.format(a=w, far=100.0 * (h + 1)) + extra
            if short == (w, h):
                line = "255\t" + line.split("\t", 1)[1]
            out.append(line)
        out.append("//")
    return "\r\n".join(out) + "\r\n"


def test_slots_and_constants():
    assert len(SLOTS) == 52 and N_REQUIRED == 51 and len(WEATHERS) == 23 and HOURS == (0, 5, 6, 7, 12, 19, 20, 22)
    assert [g for g, _t, _k in GROUPS][:3] == ["amb", "amb_obj", "dir"] and GROUPS[-1][0] == "dir_mult"


def test_full_file():
    rows = parse_timecyc(make())
    assert len(rows) == 184 and {r.nread for r in rows} == {51}
    r = rows[3 * 8 + 4]
    assert (r.weather, r.weather_name, r.hour) == (3, "NAME_3", 12)
    v = r.values
    assert v["amb"] == [3, 3, 3] and v["sky_top"] == [68, 117, 210] and v["far_clip"] == 500.0
    assert v["sun_size"] == 1.1 and v["shadow"] == 236 and v["low_clouds"] == [44, 34, 23]
    assert v["water"] == [90.0, 170.0, 170.0, 240.0] and v["postfx1"] == [255.0, 66.0, 66.0, 48.0]
    assert (v["cloud_alpha"], v["high_light_min"], v["water_fog_alpha"], v["dir_mult"]) == (25.0, 180, 0, None)
    assert r.line == 3 * (8 * 2 + 3) + 4 * 2 + 4


def test_weather_names_fall_back_to_eweathertype():
    rows = parse_timecyc(make(names=False))
    assert rows[0].weather_name == "EXTRASUNNY_LA" and rows[-1].weather_name == "EXTRACOLOURS_2"


def test_short_line_repeats_previous_values_like_the_engine():
    errs: list = []
    rows = parse_timecyc(make(short=(16, 6)), errors=errs)
    r, prev = rows[16 * 8 + 6], rows[16 * 8 + 5]
    assert r.nread == 20 and len(errs) == 1 and "20 of 51" in errs[0][1] and errs[0][0] == r.line
    # values shifted by two slots: '255 210 194' becomes the ambient colour ...
    assert r.values["amb"] == [255, 210, 194] and r.values["amb_obj"] == [182, 255, 255]
    # ... '1.10' is read as 1 by %d, then '.10' stops sscanf: the rest is the previous line's
    assert r.values["sun_corona"] == [255, 1, prev.values["sun_corona"][2]]
    assert r.values["far_clip"] == prev.values["far_clip"] == 600.0 and r.values["water"] == prev.values["water"]
    with pytest.raises(FormatError) as e:
        parse_timecyc(make(short=(0, 0)), strict=True)
    assert e.value.kind == "timecyc"


def test_optional_dir_mult():
    rows = parse_timecyc(make(extra=" 0.75"))
    assert {r.nread for r in rows} == {52} and rows[0].values["dir_mult"] == 0.75


def test_24_hour_file_and_wrong_count():
    rows = parse_timecyc(make(hours=24))
    assert len(rows) == 552 and [r.hour for r in rows[:24]] == list(range(24))
    errs: list = []
    rows = parse_timecyc(make(weathers=2), errors=errs)
    assert len(rows) == 16 and "16 data lines" in errs[0][1]
    with pytest.raises(FormatError):
        parse_timecyc(make(weathers=2), strict=True)
    assert parse_timecyc("", errors=[]) == []


@pytest.mark.game
def test_vanilla_timecyc(clean_root):
    from satk.formats.dat import read_text, resolve_ci

    errs: list = []
    rows = parse_timecyc(read_text(resolve_ci(clean_root, "data/timecyc.dat")), errors=errs)
    assert len(rows) == 184 and [r.weather_name for r in rows[::8]] == list(WEATHERS)
    assert [(e[0], r.nread) for e in errs for r in rows if r.line == e[0]] == [(320, 20)]
    noon = rows[4]
    assert (noon.weather_name, noon.hour, noon.values["far_clip"], noon.values["sky_top"]) == (
        "EXTRASUNNY_LA", 12, 800.0, [68, 117, 210])
