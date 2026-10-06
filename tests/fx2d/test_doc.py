"""DFF <-> fx2d document (satk.fx2d.doc) on synthetic DFFs."""

from __future__ import annotations

import json

import pytest

from satk.fx2d.doc import Item, apply_doc, frame_names, normalize, parse_dff, read_doc, spheres
from satk.fx2d.schema import FORMAT, Fx2dError
from satk.rw.chunk import FX2D

from .conftest import build_dff, raw_entries


def _rt(data: bytes, **kw) -> bytes:
    doc = json.loads(json.dumps(read_doc(data, **kw)))
    return apply_doc(data, normalize(doc))[0]


def test_document_shape_and_exact_round_trip():
    data = build_dff(raw_entries(), None, raw_entries()[:2], names=("lamp", "body", "lod"))
    doc = read_doc(data, source="x.dff")
    assert doc["format"] == FORMAT and doc["source"] == "x.dff" and doc["geometry_count"] == 3
    got = [(g["geometry"], g["frame"], len(g["effects"])) for g in doc["geometries"]]
    assert got == [(0, "lamp", 10), (2, "lod", 2)]
    assert _rt(data) == data


def test_img_padding_is_dropped_unless_asked():
    data = build_dff(raw_entries(), pad=500)
    new, rep = apply_doc(data, normalize(read_doc(data)))
    assert new == data[:-500] and rep.geometries == [0] and rep.entries == 10
    assert apply_doc(data, normalize(read_doc(data)), with_tail=True)[0] == data


def test_add_replace_and_remove():
    data = build_dff(raw_entries()[:3])
    lamp = {"type": "light", "pos": [0, 0, 5]}
    new, rep = apply_doc(data, [Item(0, [lamp])], mode="add")
    assert len(read_doc(new)["geometries"][0]["effects"]) == 4 and rep.created == []
    new, rep = apply_doc(data, [Item(0, [lamp])])
    effs = read_doc(new)["geometries"][0]["effects"]
    assert len(effs) == 1 and effs[0]["pos"] == [0.0, 0.0, 5.0]
    new, rep = apply_doc(data, [Item(0, [])])
    assert rep.removed == [0] and read_doc(new)["geometries"] == []
    assert parse_dff(new).stream.find_all(FX2D) == []


def test_new_chunk_goes_last_in_the_extension_and_extension_is_created():
    data = build_dff(None, None)
    new, rep = apply_doc(data, [Item(None, [{"type": "particle", "pos": [0, 0, 1], "name": "fire"}])], geometry=1)
    assert rep.created == [1]
    g = parse_dff(new).geometries()[1]
    from satk.rw.chunk import EXTENSION

    assert g.node.child(EXTENSION).kids[-1].type == FX2D
    doc = read_doc(new)
    assert doc["geometries"][0]["geometry"] == 1 and doc["geometries"][0]["effects"][0]["name"] == "fire"
    bare = build_dff(None, ext=False)
    new, rep = apply_doc(bare, [Item(0, [{"type": "trigger_point", "pos": [0, 0, 0], "id": 2}])])
    assert rep.created == [0] and read_doc(new)["geometries"][0]["effects"][0]["id"] == 2


def test_errors_and_warnings():
    data = build_dff(raw_entries()[:1], names=("lamp",))
    with pytest.raises(Fx2dError, match="geometry 4 does not exist"):
        apply_doc(data, [Item(4, [])])
    with pytest.raises(Fx2dError, match="listed twice"):
        apply_doc(data, [Item(0, []), Item(0, [])])
    with pytest.raises(Fx2dError, match="mode"):
        apply_doc(data, [], mode="merge")
    with pytest.raises(Fx2dError, match="not a readable DFF"):
        read_doc(b"COL3" + b"\0" * 40)
    _new, rep = apply_doc(data, [Item(0, [], frame="tree")])
    assert rep.warn and rep.warn[0].startswith("FRAME_MISMATCH")


def test_a_geometry_without_struct_is_a_clean_error():
    from satk.rw.chunk import GEOMETRY, STRUCT, parse

    s = parse(build_dff(raw_entries()[:1]))
    g = s.find_all(GEOMETRY)[0]
    g.kids = [k for k in g.kids if k.type != STRUCT]
    with pytest.raises(Fx2dError, match="not a readable DFF"):
        read_doc(s.to_bytes())


def test_normalize_accepts_three_shapes():
    one = {"type": "light", "pos": [0, 0, 1]}
    assert normalize([one])[0].geometry is None
    it = normalize({"effects": [one], "geometry": 2})[0]
    assert it.geometry == 2 and it.effects == [one]
    it = normalize({"format": FORMAT, "geometries": [{"geometry": 1, "effects": [one], "frame": "x"}]})[0]
    assert (it.geometry, it.frame, it.where) == (1, "x", "geometries[0].effects")
    for bad, msg in (({"geometrys": []}, "unknown top-level key"), ({"format": "other/2", "effects": []}, "format"),
                     ({"geometries": [{"effects": []}]}, "needs 'geometry'"), ("x", "expected a JSON object"),
                     ({}, "no 'geometries'"), ({"effects": [], "geometries": []}, "either")):
        with pytest.raises(Fx2dError, match=msg):
            normalize(bad)


def test_frames_and_spheres():
    data = build_dff(None, raw_entries()[:1], names=("a", "b"))
    assert frame_names(parse_dff(data)) == {0: "a", 1: "b"}
    sp = spheres(data)
    assert set(sp) == {0, 1} and sp[0][3] == pytest.approx(0.8)
