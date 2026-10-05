"""MTA .map reader/writer of satk.mapconv.mta."""

from __future__ import annotations

import pytest

from satk.mapconv.mta import MtaSyntaxError, read_mta, write_mta
from satk.mapconv.scene import Material, MaterialText, MapObject, Removal, Scene

EDITOR_MAP = """<map edf:definitions="editor_main">
    <object id="object (pol_comp_gate) (1)" model="2933" posX="923.9" posY="-1207.1" posZ="17.6" rotX="0"
            rotY="0" rotZ="270" interior="0" dimension="0" alpha="255" scale="1" doublesided="false"
            collisions="true" breakable="true" frozen="false"/>
    <removeWorldObject id="removeWorldObject (ci_watertank01) (1)" model="5838" lodModel="6005" radius="22.79"
            interior="-1" posX="895.25" posY="-1256.92" posZ="31.23" rotX="0" rotY="0" rotZ="0"/>
    <vehicle id="vehicle (1)" model="411" posX="0" posY="0" posZ="3"/>
    <object id="gate" model="980" posX="1" posY="2" posZ="3" scaleX="2" scaleY="2" scaleZ="2" dimension="-1"
            interior="5" alpha="128" doublesided="true" collisions="false" frozen="true"/>
</map>
"""


def test_reads_an_editor_map_without_xmlns():
    sc = read_mta(EDITOR_MAP)
    a, b = sc.objects
    assert (a.model, a.pos, a.rot, a.interior, a.world, a.name, a.label, a.breakable) == (
        2933, (923.9, -1207.1, 17.6), (0.0, 0.0, 270.0), 0, 0, "pol_comp_gate", "object (pol_comp_gate) (1)", True)
    assert (b.scale, b.world, b.interior, b.alpha, b.doublesided, b.collisions, b.frozen, b.name) == (
        2.0, -1, 5, 128, True, False, True, None)
    (r,) = sc.removals
    assert (r.model, r.lod_model, r.radius, r.interior, r.pos, r.name) == (
        5838, 6005, 22.79, -1, (895.25, -1256.92, 31.23), "ci_watertank01")
    assert sc.skipped == {"vehicle": 1} and sc.issues == []
    assert a.line == 2 and r.line == 5


def test_write_read_round_trip_with_extensions():
    o = MapObject(model=19379, pos=(1.5, -2.25, 3.0), rot=(10.0, -20.0, 30.0), interior=-1, world=7, draw=150.0,
                  stream=200.0, scale=1.5, alpha=200, doublesided=True, collisions=False, breakable=False,
                  frozen=True, lod=0, iflags=3, name="wall")
    o.materials.append(Material(0, 1280, "benches_cj", "Metal3_128", 0xFF808080))
    o.texts.append(MaterialText(1, 'Grove <&> "St"', 90, "Arial", 24, 1, 0xFF00FF00, 0, 1))
    sc = Scene(objects=[o, MapObject(model=1, pos=(0.0, 0.0, 0.0))],
               removals=[Removal(1226, (1.0, 2.0, 3.0), 0.25, lod_model=1227, interior=0)])
    text = write_mta(sc)
    assert 'allInteriors="true"' in text and 'interior="0"' in text and "<material " in text
    back = read_mta(text)
    b = back.objects[0]
    for f in ("model", "pos", "rot", "interior", "world", "draw", "stream", "scale", "alpha", "doublesided",
              "collisions", "breakable", "frozen", "lod", "iflags", "name"):
        assert getattr(b, f) == getattr(o, f), f
    assert [(m.slot, m.model, m.txd, m.tex, m.color) for m in b.materials] == [(0, 1280, "benches_cj", "Metal3_128",
                                                                               0xFF808080)]
    assert b.texts[0].text == 'Grove <&> "St"' and b.texts[0].color == 0xFF00FF00
    r = back.removals[0]
    assert (r.model, r.lod_model, r.radius, r.interior, r.pos) == (1226, 1227, 0.25, 0, (1.0, 2.0, 3.0))
    assert back.objects[1].interior == -1 and back.objects[1].world == -1  # defaults: every interior/world
    assert write_mta(back) == text  # stable


def test_drop_materials():
    o = MapObject(model=1, pos=(0.0, 0.0, 0.0), materials=[Material(0, 1, "a", "b")])
    assert "<material" not in write_mta(Scene(objects=[o]), materials=False)


def test_bad_elements_are_reported_not_fatal():
    sc = read_mta('<map><object model="x" posX="1" posY="2" posZ="3"/><object model="1" posY="2" posZ="3"/>'
                  '<object model="2" posX="1" posY="2" posZ="3" rotZ="nan"/><material slot="0"/></map>')
    assert sc.objects == []
    assert [i.code for i in sc.issues] == ["BAD_ELEMENT", "BAD_ELEMENT", "BAD_ELEMENT", "ORPHAN"]


@pytest.mark.parametrize("bad", ["", "<map>", "<map><object></map>", "not xml",
                                 '<!DOCTYPE map [<!ENTITY a "aaaa">]><map>&a;</map>'])
def test_malformed_raises_with_line(bad):
    with pytest.raises(MtaSyntaxError):
        read_mta(bad)


def test_cp1251_without_declaration_and_declared_encodings():
    data = '<map><object id="object (стена) (1)" model="1" posX="0" posY="0" posZ="0"/></map>'.encode("cp1251")
    assert read_mta(data).objects[0].name == "стена"
    decl = '<?xml version="1.0" encoding="windows-1251"?><map><object model="1" posX="0" posY="0" posZ="0"/></map>'
    assert len(read_mta(decl.encode("cp1251")).objects) == 1
    assert len(read_mta(decl).objects) == 1  # already-decoded text: the declaration is ignored
