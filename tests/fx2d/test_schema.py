"""One 2dEffect entry <-> JSON (satk.fx2d.schema): exact both ways, readable fields, helpful errors."""

from __future__ import annotations

import json
import struct

import pytest

from satk.fx2d.schema import Fx2dError, decode_entry, encode_entry, f32_json, field_names, summary

from .conftest import light, pos, raw_entries, roadsign


def _rt(p, t, d, keep=True):
    e = json.loads(json.dumps(decode_entry(p, t, d, keep=keep)))     # through real JSON text
    return e, encode_entry(e)


@pytest.mark.parametrize("i", range(len(raw_entries())))
def test_every_type_round_trips_bit_exact(i):
    p, t, d = raw_entries()[i]
    e, back = _rt(p, t, d)
    assert back == (p, t, d)
    assert "data" not in e                                    # decoded into fields, not kept raw


def test_light_fields_are_readable():
    e = decode_entry(pos(0.5, 0, 3.2), 0, light())
    assert e["type"] == "light" and e["pos"] == [0.5, 0.0, 3.2]
    assert e["color"] == [255, 200, 100, 200] and e["corona"] == "coronastar" and e["shadow"] == "shad_exp"
    assert e["far_clip"] == 100.0 and e["range"] == 12.0 and e["corona_size"] == 2.5 and e["shadow_size"] == 8.0
    assert e["flash"] == "default" and e["reflection"] is True and e["shadow_mult"] == 40
    assert e["flags"] == ["fog1", "at_night", "update_height_above_ground"]
    assert e["look_dir"] == [0, 0, 100] and "bytes" not in e
    assert set(e["keep"]) == {"corona", "shadow", "pad"} and e["keep"]["pad"] == "6768"
    assert summary(e).startswith("coronastar rgba 255,200,100,200 size 2.5 far 100 range 12")


def test_short_light_and_short_enex_keep_their_size():
    e, back = _rt(pos(0, 0, 1), 0, light(size=76))
    assert e["bytes"] == 76 and "look_dir" not in e and len(back[2]) == 76
    bad = dict(e, look_dir=[0, 0, 100])
    with pytest.raises(Fx2dError, match="needs"):
        encode_entry(bad)
    p, t, d = raw_entries()[5]
    e = decode_entry(p, t, d)
    assert e["bytes"] == 40 and "time_on" not in e and e["name"] == "SFHSM2"
    with pytest.raises(Fx2dError, match="bit 7"):
        encode_entry(dict(e, flags=["burglary_access"]))


def test_floats_are_shortest_float32_and_nan_survives():
    assert f32_json(struct.pack("<f", 0.1)) == 0.1
    assert f32_json(struct.pack("<f", 12.000001)) == 12.000001
    assert str(f32_json(struct.pack("<f", -0.0))) == "-0.0"
    nan = struct.pack("<I", 0x7FC00001)
    assert f32_json(nan) == "0x7FC00001"
    p = pos(0, 0, 0)
    e, back = _rt(p, 8, struct.pack("<i", 1))
    e["pos"][2] = "0x7FC00001"
    assert encode_entry(e)[0][8:] == nan


def test_without_keep_strings_and_padding_become_zero():
    p, t, d = raw_entries()[0]
    e = decode_entry(p, t, d, keep=False)
    assert "keep" not in e
    _p, _t, clean = encode_entry(e)
    assert clean != d and clean[25:49] == b"coronastar".ljust(24, b"\0") and clean[78:80] == b"\0\0"
    # the same entry with keep, but a renamed corona: the stale tail does not fit and is dropped
    e = decode_entry(p, t, d)
    e["corona"] = "coronamoon2"
    assert encode_entry(e)[2][25:49] == b"coronamoon2".ljust(24, b"\0")


def test_roadsign_text_layout_and_world_position():
    e = decode_entry(pos(1600.5, -2050.25, 28.0), 7, roadsign())
    assert e["text"] == ["Las_Brujas", "Ghost_Town_<", "", ""]
    assert (e["lines"], e["chars"], e["color"]) == (2, 16, 3) and "flags_extra" not in e
    assert e["keep"] == {"pad": "5249"} and e["rot"] == [0.0, 0.0, 90.0]
    assert summary(e) == "Las Brujas | Ghost Town <"
    e2 = {"type": "roadsign", "pos": [0, 0, 0], "size": [4, 2], "text": ["EXIT"], "lines": 1, "chars": 8}
    _p, _t, d = encode_entry(e2)
    assert d[22:38] == b"EXIT____________" and d[38:86] == b"_" * 48
    assert struct.unpack_from("<H", d, 20)[0] == 1 | 3 << 2
    with pytest.raises(Fx2dError, match="chars must be"):
        encode_entry(dict(e2, chars=5))
    with pytest.raises(Fx2dError, match="lines must be"):
        encode_entry(dict(e2, lines=[1]))


def test_hand_written_entries_take_documented_defaults():
    _p, t, d = encode_entry({"type": "light", "pos": [0, 0, 3]})
    e = decode_entry(_p, t, d)
    assert t == 0 and len(d) == 80 and e["corona"] == "coronastar" and e["color"] == [249, 145, 34, 200]
    assert e["flags"] == ["fog1", "at_night", "update_height_above_ground"] and "keep" not in e
    _p, t, d = encode_entry({"type": "particle", "pos": [0, 0, 1], "name": "fire"})
    assert t == 1 and d == b"fire".ljust(24, b"\0")
    _p, t, d = encode_entry({"type": 9, "pos": [0, 0, 0], "dir": [1, 0], "usage": 1})
    assert decode_entry(_p, t, d)["usage"] == "wall_to_left"
    with pytest.raises(Fx2dError, match="missing 'name'"):
        encode_entry({"type": "particle", "pos": [0, 0, 1]})


def test_unknown_types_and_sizes_stay_raw():
    p = pos(1, 2, 3)
    for t, d in ((5, bytes(range(36))), (42, b"\x01\x02"), (0, b"\0" * 10), (4, b"")):
        e, back = _rt(p, t, d)
        assert back == (p, t, d)
        if t == 4:
            assert set(e) == {"type", "pos"} and e["type"] == "sun_glare"
        else:
            assert e["data"] == d.hex()
    with pytest.raises(Fx2dError, match="raw entry"):
        encode_entry({"type": "interior", "pos": [0, 0, 0], "data": "00", "color": 1})
    with pytest.raises(Fx2dError, match="no known layout"):
        encode_entry({"type": "interior", "pos": [0, 0, 0]})


@pytest.mark.parametrize("entry,match,suggest", [
    ({"type": "lihgt", "pos": [0, 0, 0]}, "unknown type", "light"),
    ({"type": "light", "pos": [0, 0, 0], "colour": [1, 2, 3, 4]}, "unknown key 'colour'", "color"),
    ({"type": "light", "pos": [0, 0, 0], "flash": "radnom"}, "unknown value", "random"),
    ({"type": "light", "pos": [0, 0, 0], "flags": ["at_nite"]}, "unknown flag", "at_night"),
    ({"type": "light", "pos": [0, 0, 0], "color": [1, 2, 3, 300]}, "out of range", None),
    ({"type": "light", "pos": [0, 0, 0], "corona": "x" * 25}, "longer than 24", None),
    ({"type": "light", "pos": [0, 0]}, "3 numbers", None),
    ({"type": "light"}, "missing 'pos'", None),
    ({"type": "light", "pos": [0, 0, 0], "bytes": 77}, "data bytes", None),
    ({"type": "light", "pos": [0, 0, 0], "range": "far"}, "expected a number", None),
    ({"type": "light", "pos": [0, 0, 0], "range": 1e39}, "float32", None),
    ({"type": "attractor", "pos": [0, 0, 0]}, "missing 'atype'", None),
    ({"type": "enex", "pos": [0, 0, 0], "name": "ВХОД", "exit": [0, 0, 0]}, "latin-1", None),
])
def test_errors_name_the_json_path_and_suggest(entry, match, suggest):
    with pytest.raises(Fx2dError, match=match) as ei:
        encode_entry(entry, "effects[3]")
    assert str(ei.value).startswith("effects[3]")
    if suggest:
        assert suggest in ei.value.did_you_mean


def test_field_names_cover_the_documented_keys():
    assert field_names(0)[:3] == ["color", "corona", "shadow"]
    assert "lines" in field_names(7) and "size" in field_names(7)
    assert field_names(5) == []
