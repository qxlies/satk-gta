"""satk.anim.jsonio: the satk-anim/1 JSON form is bit-exact for compressed and float data; errors name the path."""

from __future__ import annotations

import json

import pytest

from satk.anim.ifp import read_ifp, write_ifp
from satk.anim.jsonio import JsonError, anim_to_json, dumps, ifp_to_json, json_to_ifp

from .conftest import anp3_bytes, anpk_bytes, walk_ifp


def _via_json(ifp):
    return json_to_ifp(json.loads(dumps(ifp_to_json(ifp))))


@pytest.mark.parametrize("blob", [
    write_ifp(walk_ifp()),
    anp3_bytes(b"flt\0junk", [("spin", [("Root", 0, 2, [(0.0, 0.0, 0.70710677, 0.70710677, 0.5, 0.1, 2.0, -0.3)]),
                                         ("x", 5, 1, [(0.123456789, 0.5, 0.5, 0.5, 0.0333333)])])]),
    anp3_bytes("old", [("a", [("Root", -1, 1, [(0.0, 0.0, 0.0, 1.0, 0.0)])])], anp2=True),
    anpk_bytes("cut", [("door", [("DOOR", -1, b"KRT0", [(0.1, 0.2, 0.3, 0.9, 1.0, 2.0, 3.0, 0.5)]),
                                 ("e", 3, b"KR00", [])]),
                       ("s", [("box", -1, b"KRTS", [(0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0, 2.0, 2.0, 2.0, 1e-7)])])]),
], ids=["anp3-compressed", "anp3-float", "anp2", "anpk"])
def test_json_round_trip_bit_exact(blob):
    ifp = read_ifp(blob)
    assert write_ifp(_via_json(ifp)) == blob


def test_json_shape_and_values():
    doc = ifp_to_json(walk_ifp(), source="x.ifp")
    assert doc["satk"] == "anim/1" and doc["format"] == "ANP3" and doc["pack"] == "test" and doc["source"] == "x.ifp"
    walk = doc["anims"][0]
    assert walk["name"] == "walk" and walk["index"] == 0 and walk["compressed"] is True and walk["duration"] == 0.1333
    root = walk["bones"][0]
    assert root["name"] == "Root" and root["tag"] == 0 and root["trans"] is True
    assert root["keys"][1] == [0.033333, 0.0, 0.0, 0.707031, 0.707031, 0.0, 0.375, -0.068359]
    assert "trans" not in walk["bones"][1] and len(walk["bones"][1]["keys"][0]) == 5
    text = dumps(doc)
    assert json.loads(text) == doc and text.count("\n") > 20         # one key per line


def test_single_anim_object_list_of_docs_and_order():
    w = walk_ifp()
    a = anim_to_json(w.anims[1], index=1)
    b = anim_to_json(w.anims[0], index=0)
    ifp = json_to_ifp([{"bones": a["bones"], "name": "idle", "index": 1}, {"satk": "anim/1", "pack": "mine",
                                                                          "anims": [b]}])
    assert [x.name for x in ifp.anims] == ["walk", "idle"] and ifp.pack == "mine" and ifp.format == "ANP3"
    assert json_to_ifp(a, pack="solo").pack == "solo"


def test_authoring_defaults():
    """A hand-written animation: compressed by default, trans from the key width, tag -1 by default."""
    doc = {"anims": [{"name": "nod", "bones": [
        {"name": "Head", "keys": [[0, 0, 0, 0, 1], [0.5, 0.2588, 0, 0, 0.9659], [1.0, 0, 0, 0, 1]]},
        {"name": "Root", "tag": 0, "keys": [[0, 0, 0, 0, 1, 0, 0, 0], [1.0, 0, 0, 0, 1, 0, 1.25, 0]]}]}]}
    ifp = json_to_ifp(doc)
    a = ifp.anims[0]
    assert a.flags == 1 and a.compressed and a.seqs[0].tag == -1 and not a.seqs[0].trans and a.seqs[1].trans
    assert a.seqs[0].keys[1] == (1060, 0, 0, 3956, 30)
    assert a.seqs[1].keys[1][5:] == (0, 1280, 0)
    assert read_ifp(write_ifp(ifp)).anims[0].duration == 1.0
    ifp2 = json_to_ifp({"anims": [{"name": "nod", "compressed": False, "bones": doc["anims"][0]["bones"]}]})
    assert not ifp2.anims[0].compressed and ifp2.anims[0].flags == 0


@pytest.mark.parametrize("doc,where,text", [
    ({"anims": [{"name": "a", "bones": [{"name": "b", "keys": [[0, 0, 0, 1]]}]}]}, "$.anims[0].bones[0].keys[0]",
     "expected 5 numbers"),
    ({"anims": [{"name": "a", "bones": [{"name": "b", "keys": [[0, 0, 0, 0, "x"]]}]}]}, "$.anims[0].bones[0].keys[0]",
     "expected a number"),
    ({"anims": [{"name": "a", "bones": [{"tag": 1, "keys": []}]}]}, "$.anims[0].bones[0].name", "missing bone name"),
    ({"anims": [{"name": "a", "bones": [{"name": "b", "tag": "x"}]}]}, "$.anims[0].bones[0].tag", "integer"),
    ({"anims": [{"name": "a", "bones": [{"name": "Root", "keys": [[0, 0, 0, 0, 1, 40, 0, 0]]}]}]},
     "$.anims[0].bones[0]", "translation"),
    ({"anims": "x"}, "$.anims", "missing list"),
    ({"format": "ANPX", "anims": []}, "$.format", "unknown format"),
])
def test_json_errors_name_the_path(doc, where, text):
    with pytest.raises(JsonError) as ei:
        json_to_ifp(doc)
    assert ei.value.where == where and text in str(ei.value)


def test_decreasing_times_are_kept():
    """Vanilla cutscene files have a few keys earlier than the previous one: JSON must carry them (check warns)."""
    blob = anpk_bytes("cut", [("a", [("b", -1, b"KR00", [(0.0, 0.0, 0.0, 1.0, 1.0), (0.0, 0.0, 0.0, 1.0, 0.9)])])])
    assert write_ifp(_via_json(read_ifp(blob))) == blob


@pytest.mark.parametrize("ftype", [1, 2, 3, 4])
def test_empty_sequences_keep_their_frame_type(ftype):
    blob = anp3_bytes("empty", [("a", [("Root", 0, 3, [(0, 0, 0, 4096, 0)]), ("Head", 5, ftype, [])]),
                                ("empty", [("Root", 0, ftype, [])])])
    assert write_ifp(_via_json(read_ifp(blob))) == blob


@pytest.mark.parametrize("blob", [
    anp3_bytes(b"pack\0padding", [(b"walk\0padding", [(b"Root\0padding", 0, 3, [(0, 0, 0, 4096, 0)])])]),
    anpk_bytes("pack\0" + "p" * 18, [("walk\0" + "p" * 18,
                                     [("Root\0padding", 0, b"KR00", [(0, 0, 0, 1, 0)])])]),
], ids=["ANP3", "ANPK-24-byte-name-payload"])
def test_editing_names_does_not_restore_old_names_from_byte_metadata(blob):
    assert write_ifp(_via_json(read_ifp(blob))) == blob
    doc = ifp_to_json(read_ifp(blob))
    doc["pack"] = "edited"
    doc["anims"][0]["name"] = "renamed"
    doc["anims"][0]["bones"][0]["name"] = "other"
    back = read_ifp(write_ifp(json_to_ifp(doc)))
    assert back.pack == "edited" and back.anims[0].name == "renamed" and back.anims[0].seqs[0].name == "other"


@pytest.mark.parametrize("value", [True, -1, 1 << 32, "not-a-number", {}])
def test_flags_errors_are_reported_as_json_errors(value):
    doc = ifp_to_json(walk_ifp())
    doc["anims"][0]["flags"] = value
    with pytest.raises(JsonError, match=r"\$\.anims\[0\]\.flags"):
        json_to_ifp(doc)


@pytest.mark.parametrize("key", ["trans", "scale", "compressed"])
def test_boolean_fields_are_not_truthy_strings(key):
    doc = ifp_to_json(walk_ifp())
    doc["anims"][0]["bones"][0][key] = "false"
    with pytest.raises(JsonError, match="expected true or false"):
        json_to_ifp(doc)


def test_unknown_schema_and_out_of_range_bone_tag():
    with pytest.raises(JsonError, match="expected 'anim/1'"):
        json_to_ifp({"satk": "anim/99", "anims": []})
    doc = ifp_to_json(walk_ifp())
    doc["anims"][0]["bones"][0]["tag"] = 1 << 31
    with pytest.raises(JsonError, match="integer bone id"):
        json_to_ifp(doc)
