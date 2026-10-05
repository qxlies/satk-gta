"""Literal-only Pawn reader/writer of satk.mapconv.pawn (M2-04 acceptance 1: Pawn -> map -> Pawn)."""

from __future__ import annotations

from pathlib import Path

import pytest

from satk.mapconv.mta import read_mta, write_mta
from satk.mapconv.pawn import read_pawn, tokenize, write_pawn
from satk.mapconv.rot import same_rotation

DATA = Path(__file__).parent / "data"


def _codes(sc):
    return [i.code for i in sc.issues]


def test_tokenizer_comments_strings_numbers():
    toks = tokenize('/* a\nb */ x = F(0x1F, -2.5e1, \'A\', "q\\"\\n\\65;", 1_000) // tail\n')
    kinds = [(t.kind, t.value) for t in toks]
    assert ("num", 31) in kinds and ("num", 25.0) in kinds and ("num", 65) in kinds and ("num", 1000) in kinds
    assert ("str", 'q"\nA') in kinds
    assert toks[0].line == 2  # the block comment had a newline


def test_unterminated_string_is_a_syntax_issue():
    sc = read_pawn('CreateObject(1, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0);\nx = "oops\n')
    assert sc.objects == [] and _codes(sc) == ["SYNTAX"] and sc.issues[0].where == "line 2"


def test_texture_studio_export():
    src = """
RemoveBuildingForPlayer(playerid, 1226, 1529.000, -1680.000, 13.000, 0.250);
new tmpobjid;
tmpobjid = CreateDynamicObject(19379, 1520.123, -1690.0, 12.5, 0.0, 90.0, 0.0, -1, -1, -1, 300.00, 300.00); // note
SetDynamicObjectMaterial(tmpobjid, 0, 10101, "2notherbuildsfe", "Bow_Abpave_Gen", 0x0);
SetDynamicObjectMaterialText(tmpobjid, 0, "Text", 90, "Arial", 24, 1, 0xFFFFFFFF, 0x0, 1);
"""
    sc = read_pawn(src)
    assert sc.issues == []
    (o,) = sc.objects
    assert (o.model, o.pos, o.rot, o.world, o.interior, o.stream, o.draw) == (
        19379, (1520.123, -1690.0, 12.5), (0.0, 90.0, 0.0), -1, -1, 300.0, 300.0)
    assert o.func == "CreateDynamicObject" and o.line == 4
    (m,) = o.materials
    assert (m.slot, m.model, m.txd, m.tex, m.color) == (0, 10101, "2notherbuildsfe", "Bow_Abpave_Gen", 0)
    (t,) = o.texts
    assert (t.slot, t.text, t.size, t.font, t.font_size, t.bold, t.color, t.back, t.align) == (
        0, "Text", 90, "Arial", 24, 1, 0xFFFFFFFF, 0, 1)
    (r,) = sc.removals
    assert (r.model, r.pos, r.radius, r.lod_model) == (1226, (1529.0, -1680.0, 13.0), 0.25, None)


def test_material_text_argument_order_differs():
    sc = read_pawn("""
o = CreateObject(1, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0);
SetObjectMaterialText(o, "native", 2, OBJECT_MATERIAL_SIZE_512x512, "Tahoma", 30, 0, -1, 0xFF000000,
                      OBJECT_MATERIAL_TEXT_ALIGN_RIGHT);
d = CreateDynamicObject(2, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0);
SetDynamicObjectMaterialText(d, 3, "streamer");
""")
    t1, t2 = sc.objects[0].texts[0], sc.objects[1].texts[0]
    assert (t1.slot, t1.text, t1.size, t1.font, t1.color, t1.back, t1.align) == (
        2, "native", 140, "Tahoma", 0xFFFFFFFF, 0xFF000000, 2)
    assert (t2.slot, t2.text, t2.size, t2.font, t2.font_size, t2.bold) == (3, "streamer", 90, "Arial", 24, 1)


def test_handles_arrays_and_player_objects():
    sc = read_pawn("""
new objs[4];
objs[2] = CreateObject(10, 1.0, 2.0, 3.0, 0.0, 0.0, 0.0, 99.0);
SetObjectMaterial(objs[2], 1, -1, "none", "none", 0xFFFF0000);
p = CreatePlayerObject(playerid, 11, 1.0, 2.0, 3.0, 0.0, 0.0, 0.0);
SetPlayerObjectMaterial(playerid, p, 0, 1, "a", "b");
SetObjectMaterial(missing, 0, 1, "a", "b", 0);
""")
    assert [o.model for o in sc.objects] == [10, 11]
    assert sc.objects[0].draw == 99.0 and sc.objects[0].materials[0].color == 0xFFFF0000
    assert sc.objects[1].materials[0].tex == "b"
    assert "PLAYER_OBJECT" in _codes(sc) and "UNKNOWN_HANDLE" in _codes(sc)


def test_not_literal_calls_are_skipped_with_their_line():
    sc = read_pawn("""CreateObject(1, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0);
CreateObject(2, x + 1.0, 0.0, 0.0, 0.0, 0.0, 0.0);
CreateObject(3, GetX(), 0.0, 0.0, 0.0, 0.0, 0.0);
CreateObject(4, 0.0, 0.0);
""")
    assert [o.model for o in sc.objects] == [1]
    assert [(i.code, i.where) for i in sc.issues] == [("NOT_LITERAL", "line 2"), ("NOT_LITERAL", "line 3"),
                                                     ("NOT_LITERAL", "line 4")]


def test_declarations_and_defines_are_not_calls():
    sc = read_pawn("""
native CreateObject(modelid, Float:x, Float:y, Float:z, Float:rX, Float:rY, Float:rZ, Float:dd = 0.0);
#define CreateObject(%0) CreateDynamicObject(%0)
stock CreateDynamicObject(a, b) { return 0; }
CreateObject(5, Float:1.0, 2.0, 3.0, 0.0, 0.0, STREAMER_TAG_AREA:-90.0);
""")
    assert [(o.model, o.rot[2]) for o in sc.objects] == [(5, -90.0)]
    assert sc.issues == []


def test_dynamic_object_ex_and_constants():
    sc = read_pawn("CreateDynamicObjectEx(7, 1.0, 2.0, 3.0, 0.0, 0.0, 0.0, STREAMER_OBJECT_SD, 50.0, {4}, {2});\n"
                   "CreateDynamicObjectEx(8, 1.0, 2.0, 3.0, 0.0, 0.0, 0.0, 99.0, 0.0, {1, 2});\n"
                   "CreateDynamicObject(9, 1.0, 2.0, 3.0, 0.0, 0.0, 0.0, 5, 6, -1, STREAMER_OBJECT_SD, "
                   "STREAMER_OBJECT_DD, 3);\n")
    a, b, c = sc.objects
    assert (a.world, a.interior, a.stream, a.draw) == (4, 2, None, 50.0)
    assert (b.world, b.stream) == (1, 99.0)
    assert (c.world, c.interior, c.stream, c.draw) == (5, 6, None, 0.0)
    assert _codes(sc) == ["EX_ARRAYS", "STREAMER_FILTER"]


def test_custom_models_and_slot_replacement():
    sc = read_pawn("""
AddSimpleModel(-1, 19379, -2000, "wall.dff", "wall.txd");
AddSimpleModelTimed(0, 1337, -2001, "a.dff", "a.txd", 6, 20);
AddCharModel(7, 20001, "skin.dff", "skin.txd");
o = CreateObject(-2000, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0);
SetObjectMaterial(o, 0, 1, "a", "b", 0);
SetObjectMaterial(o, 0, 2, "c", "d", 0);
""")
    assert [(m.kind, m.id, m.time_on) for m in sc.models] == [("simple", -2000, None), ("timed", -2001, 6),
                                                             ("char", 20001, None)]
    assert [m.model for m in sc.objects[0].materials] == [2]
    assert "SLOT_REPLACED" in _codes(sc) and "CUSTOM_MODELS" in _codes(sc)


def test_cp1251_source():
    data = 'o = CreateObject(1, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0);\nSetObjectMaterialText(o, "Привет");\n'.encode("cp1251")
    sc = read_pawn(data)
    assert sc.encoding == "cp1251" and sc.objects[0].texts[0].text == "Привет"


def _key(o, nd=2):
    return (o.model, tuple(round(c, nd) for c in o.pos), o.world, o.interior, round(o.draw, nd),
            None if o.stream is None else round(o.stream, nd),
            [(m.slot, m.model, m.txd, m.tex, m.color) for m in o.materials],
            [(t.slot, t.text, t.size, t.font, t.font_size, t.bold, t.color, t.back, t.align) for t in o.texts])


def test_pawn_map_pawn_round_trip_is_lossless():
    """Acceptance 1: Pawn -> MTA .map -> Pawn keeps every object, removal, material to 0.01."""
    src = read_pawn((DATA / "grove_sample.pwn").read_bytes())
    assert src.issues == [] and len(src.objects) == 5 and len(src.removals) == 1
    mta = write_mta(src)
    mid = read_mta(mta)
    back = read_pawn(write_pawn(mid))
    assert back.issues == []
    assert [_key(o) for o in back.objects] == [_key(o) for o in src.objects]
    for a, b in zip(src.objects, back.objects):
        assert all(abs(x - y) <= 0.01 for x, y in zip(a.rot, b.rot)) and same_rotation(a.rot, b.rot, 0.01)
    assert [(r.model, r.pos, r.radius) for r in back.removals] == [(r.model, r.pos, r.radius) for r in src.removals]


def test_writer_functions_and_reread():
    sc = read_pawn((DATA / "grove_sample.pwn").read_bytes())
    for func in ("CreateObject", "CreateDynamicObject", "CreateDynamicObjectEx"):
        text = write_pawn(sc, func=func)
        again = read_pawn(text)
        assert again.issues == [], (func, again.issues)
        assert [(o.model, o.pos, o.rot, o.draw) for o in again.objects] == [
            (o.model, o.pos, o.rot, o.draw) for o in sc.objects]
        assert {o.func for o in again.objects} == {func}
    assert "tmpobjid = CreateDynamicObject(19379, 2495.0, -1675.0, 12.4, 0.0, 90.0, 0.0, -1, -1, -1, 300.0, 300.0);" \
        in write_pawn(sc)


def test_writer_escapes_strings():
    sc = read_pawn('o = CreateObject(1, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0);\n'
                   'SetObjectMaterialText(o, "a \\"b\\" \\\\ c\\nd");\n')
    text = sc.objects[0].texts[0].text
    assert text == 'a "b" \\ c\nd'
    assert read_pawn(write_pawn(sc)).objects[0].texts[0].text == text


@pytest.mark.parametrize("bad", ["", "CreateObject(", "CreateObject(1, 2", "))))", "{" * 1000])
def test_garbage_never_crashes(bad):
    sc = read_pawn(bad)
    assert sc.objects == []
