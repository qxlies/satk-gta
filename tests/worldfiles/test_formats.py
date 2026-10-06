"""satk.worldfiles document models and writers on synthetic data (no game files)."""

from __future__ import annotations

import json
import struct

import pytest

from satk.formats.gxt import key_hash as formats_key_hash
from satk.formats.gxt import parse_gxt
from satk.worldfiles import charset as CS
from satk.worldfiles.fxt import parse_fxt, write_fxt
from satk.worldfiles.gxt import (GxtDoc, GxtError, GxtTable, doc_from_json, doc_to_json, key_hash, parse_key,
                                 read_gxt, write_gxt)

from .conftest import INFO_ZON, MAP_ZON, WATER1_DAT, WATER_DAT, gxt_bytes, popcycle_text, timecyc_text

# --------------------------------------------------------------------------- charset


def test_gta_charset_round_trips_every_byte():
    raw = bytes(range(1, 256))
    text = CS.decode(raw, "gta")
    assert CS.encode(text, "gta") == raw
    assert CS.decode(b"\x96\x9e\xad\xaf", "gta") == "ßéÑ¿"
    assert CS.encode("Größe", "gta") == b"Gr\xa8\x96e"


def test_charset_errors_and_folding():
    with pytest.raises(ValueError, match="no glyph"):
        CS.encode("Привет", "gta")
    assert CS.encode("Привет", "cp1251") == "Привет".encode("cp1251")
    folded: list[str] = []
    assert CS.encode("it’s …", "gta", folded) == b"it's ..."
    assert folded == ["’", "…"]
    with pytest.raises(ValueError):
        CS.encode("a\0b", "gta")


# --------------------------------------------------------------------------- GXT


def test_key_hash_matches_the_formats_reader():
    for k in ("CRED001", "fem_ok", "INT1_AA", "X"):
        assert key_hash(k) == formats_key_hash(k)
    assert parse_key("0x00abcdef") == (0xABCDEF, None)
    assert parse_key("cred001") == (key_hash("CRED001"), "CRED001")
    with pytest.raises(ValueError):
        parse_key("two words")


def test_gxt_layout_matches_the_engine_reader():
    data = gxt_bytes()
    g = parse_gxt(data)
    assert list(g.tables) == ["MAIN", "INTRO1"]
    assert g.text("BIGCITY") == "Big City" and g.text("INT1_AB", "INTRO1") == "~z~Bye."
    assert g.tables["MAIN"][0xABCDEF] == "hashed"
    # header, TABL, MAIN without a name, tables 4-byte aligned, TKEY sorted by hash
    assert data[:4] == struct.pack("<HH", 4, 8) and data[4:8] == b"TABL"
    tabl = [struct.unpack_from("<8sI", data, 12 + 12 * i) for i in range(2)]
    assert tabl[0][1] == 12 + 24 and data[tabl[0][1]:tabl[0][1] + 4] == b"TKEY"
    assert tabl[1][1] % 4 == 0 and data[tabl[1][1]:tabl[1][1] + 8] == b"INTRO1\0\0"
    n = struct.unpack_from("<I", data, tabl[0][1] + 4)[0] // 8
    hashes = [struct.unpack_from("<II", data, tabl[0][1] + 8 + 8 * k)[1] for k in range(n)]
    assert hashes == sorted(hashes)


def test_gxt_round_trip_keeps_string_order_and_bytes():
    data = gxt_bytes()
    doc = read_gxt(data, {key_hash("TOWN"): "TOWN"})
    assert doc.tables[0].entries[0] == ("TOWN", "Town")
    assert doc.tables[0].entries[1][0] == f"0x{key_hash('PARK'):08X}"     # unknown name -> hash key
    assert write_gxt(doc) == data
    again = doc_from_json(json.loads(json.dumps(doc_to_json(doc))))
    assert write_gxt(again) == data
    assert doc.find("park")[0][2] == "Park"


def test_gxt_16_bit_and_latin1():
    doc = GxtDoc(bits=16, charset="utf-16", tables=[GxtTable("MAIN", [("JP", "日本語")])])
    data = write_gxt(doc)
    assert parse_gxt(data).text("JP") == "日本語"
    assert write_gxt(read_gxt(data)) == data
    lat = GxtDoc(charset="latin1", tables=[GxtTable("MAIN", [("A", "é")])])
    assert parse_gxt(write_gxt(lat)).text("A") == "é"


@pytest.mark.parametrize("tables, msg", [
    ([GxtTable("INTRO1", [])], "first table must be MAIN"),
    ([GxtTable("MAIN", [("A", "1"), ("a", "2")])], "appears twice"),
    ([GxtTable("MAIN", []), GxtTable("TOOLONGNAME", [])], "bad table name"),
    ([GxtTable("MAIN", [("A", "Привет")])], "no glyph"),
])
def test_gxt_writer_refuses_bad_documents(tables, msg):
    with pytest.raises(GxtError, match=msg):
        write_gxt(GxtDoc(tables=tables))


def test_gxt_reader_rejects_other_games():
    with pytest.raises(GxtError, match="only GTA SA"):
        read_gxt(b"TABL" + b"\0" * 20)


def test_doc_from_plain_object():
    doc = doc_from_json({"HELLO": "Hi", "BYE": "Bye"})
    assert [t.name for t in doc.tables] == ["MAIN"] and parse_gxt(write_gxt(doc)).text("bye") == "Bye"


# --------------------------------------------------------------------------- FXT


def test_fxt_write_and_parse():
    warn: list[str] = []
    data = write_fxt([("MYCAR", "Super GT"), ("LONGKEYNAME", "Café")], "gta", "title", warn)
    assert data == b"# title\r\nMYCAR Super GT\r\nLONGKEYNAME Caf\x9e\r\n"
    assert any(w.startswith("KEY_LENGTH") for w in warn)
    assert parse_fxt(data.decode("latin-1")) == [("MYCAR", "Super GT"), ("LONGKEYNAME", "Caf\x9e")]
    with pytest.raises(ValueError, match="twice"):
        write_fxt([("A", "1"), ("a", "2")])
    with pytest.raises(ValueError, match="line break"):
        write_fxt([("A", "x\ny")])


# --------------------------------------------------------------------------- zones


def test_zon_round_trip_keeps_odd_lines():
    from satk.worldfiles.zon import doc_from_json, doc_to_json, parse_zon_doc, write_zon

    errs: list = []
    doc = parse_zon_doc(INFO_ZON, errs)
    assert errs == [] and len(doc.zones) == 5
    assert write_zon(doc) == INFO_ZON.encode("latin-1")
    obj = doc_to_json(doc, "info.zon")
    assert [z.get("raw") for z in obj["zones"]][1].startswith("TOWN2\t,")
    assert sum(1 for z in obj["zones"] if "raw" in z) == 1          # only the line canonical form cannot rebuild
    assert write_zon(doc_from_json(json.loads(json.dumps(obj)))) == INFO_ZON.encode("latin-1")
    obj["zones"][1]["max"][0] = 310.0                                # a changed zone is written canonically
    out = write_zon(doc_from_json(obj)).decode("latin-1")
    assert "TOWN2, 0, 100.0, -100.0, -4.57764e-005, 310.0, 100.0, 200.0, 1, TOWN" in out
    assert write_zon(parse_zon_doc(MAP_ZON)) == MAP_ZON.encode("latin-1")


def test_zone_rules_point_lookup_and_relations():
    from satk.worldfiles.zon import Zone, limits, parse_zon_doc, relations, zone_errors, zones_at

    doc = parse_zon_doc(INFO_ZON)
    z = {x.name: x for x in doc.zones}
    hits, shown = zones_at(doc.zones, 0, 0)
    assert [h.name for h in hits] == ["PARK", "TOWN1", "BIG"] and shown.name == "PARK"
    assert zones_at(doc.zones, 5000, 0) == ([], None)                # -> SAN_AND
    assert relations(z["PARK"], z["TOWN1"]) == "inside" and relations(z["TOWN1"], z["PARK"]) == "contains"
    assert relations(z["EDGE"], z["TOWN2"]) == "partial" and relations(z["TOWN1"], z["EDGE"]) is None
    assert relations(z["TOWN1"], z["TOWN2"]) is None                  # touching edges share no volume
    bad = Zone("WAYTOOLONG", 2, (0, 0, 0), (1, 1, 1), 9, "x y")
    errs = " ".join(zone_errors(bad))
    assert "name" in errs and "label" in errs and "type 2" in errs and "level 9" in errs
    assert limits(380, 39, 380) == [] and len(limits(381, 40, 381)) == 3


# --------------------------------------------------------------------------- water


def test_water_round_trip_counts_and_rules():
    from satk.worldfiles.water import (counts, doc_from_json, doc_to_json, limit_warnings, parse_water_doc,
                                       poly_issues, rect_poly, write_water)

    doc = parse_water_doc(WATER_DAT)
    assert len(doc.polys) == 4 and write_water(doc) == WATER_DAT.encode("latin-1")
    obj = json.loads(json.dumps(doc_to_json(doc)))
    assert not any("raw" in p for p in obj["polys"])                 # the stock format rebuilds every line
    assert write_water(doc_from_json(obj)) == WATER_DAT.encode("latin-1")
    assert write_water(parse_water_doc(WATER1_DAT)) == WATER1_DAT.encode("latin-1")
    c = counts(doc.polys)
    assert c == {"quads": 3, "tris": 1, "verts": 12 - 2 + 3}          # two quads share an edge (2 vertices)
    assert limit_warnings(c) == [] and limit_warnings({"quads": 302, "tris": 0, "verts": 0})
    q = rect_poly(10, 20, 0, 0, 5.0)
    assert [v[:2] for v in q.verts] == [[0, 0], [10, 0], [0, 20], [10, 20]] and poly_issues(q) == []
    q.verts[1], q.verts[2] = q.verts[2], q.verts[1]
    assert "order" in poly_issues(q)[0]
    assert "outside" in poly_issues(rect_poly(2900, 0, 3100, 10, 0))[0]


# --------------------------------------------------------------------------- timecyc


def test_timecyc_patch_changes_only_tokens_and_reverts():
    from satk.worldfiles.timecyc import TimecycFile, select_rows, slot_index

    text = timecyc_text()
    tf = TimecycFile(text)
    assert len(tf.rows) == 184 and tf.render() == text.encode("latin-1")
    r = select_rows(tf.rows, "sunny_la", "12")[0]
    old = tf.flat(r)
    s = {n: i for i, n in slot_index("sky_top")}
    far = slot_index("far_clip")[0][0]
    tf.set(r, {s["sky_top.r"]: 1, s["sky_top.g"]: 2, s["sky_top.b"]: 3, far: 1234.5})
    out = tf.render().decode("latin-1")
    changed = [a for a, b in zip(out.splitlines(), text.splitlines()) if a != b]
    assert len(changed) == 1 and "\t1 2 3\t" in changed[0] and "1234.50 100.00" in changed[0]
    back = TimecycFile(out)
    r2 = select_rows(back.rows, "SUNNY_LA", "12")[0]
    assert back.flat(r2)[far] == 1234.5 and r2.values["sky_top"] == [1, 2, 3]
    back.set(r2, {s["sky_top.r"]: old[s["sky_top.r"]], s["sky_top.g"]: old[s["sky_top.g"]],
                  s["sky_top.b"]: old[s["sky_top.b"]], far: old[far]})
    assert back.render() == text.encode("latin-1")


def test_timecyc_short_line_is_rewritten_with_engine_values():
    from satk.worldfiles.timecyc import TimecycFile, select_rows, slot_index

    tf = TimecycFile(timecyc_text())
    r = select_rows(tf.rows, "RAINY_COUNTRYSIDE", "20")[0]
    assert r.nread < 51
    prev = select_rows(tf.rows, "RAINY_COUNTRYSIDE", "19")[0]
    fog = slot_index("fog_start")[0][0]
    tf.set(r, {fog: 7.0})
    assert tf.rewritten == [r.line]
    back = TimecycFile(tf.render().decode("latin-1"))
    r2 = select_rows(back.rows, "RAINY_COUNTRYSIDE", "20")[0]
    assert r2.nread == 51 and back.flat(r2)[fog] == 7.0
    # every other value is what the engine used before (carried over from 7 PM where the line was short)
    exp = tf.flat(r)
    exp[fog] = 7.0
    assert back.flat(r2)[:51] == exp[:51] and back.flat(r2)[30] == tf.flat(prev)[30]


def test_timecyc_selection_errors():
    from satk.worldfiles.timecyc import TimecycFile, select_rows, slot_index

    tf = TimecycFile(timecyc_text())
    assert len(select_rows(tf.rows, "*_LA", "all")) == 5 * 8
    assert len(select_rows(tf.rows, "1", "0,22")) == 2
    with pytest.raises(LookupError, match="not a time point"):
        select_rows(tf.rows, "SUNNY_LA", "13")
    with pytest.raises(LookupError):
        select_rows(tf.rows, "SUNNY_MARS", None)
    assert slot_index("postfx1.a") == [(40, "postfx1.a")]
    with pytest.raises(KeyError):
        slot_index("far_clip.r")


# --------------------------------------------------------------------------- popcycle


def test_popcycle_select_patch_and_revert():
    from satk.worldfiles.popcycle import FIELDS, PopcycleFile

    text = popcycle_text()
    pf = PopcycleFile(text)
    assert len(pf.rows) == 480 and pf.errors == [] and pf.render() == text.encode("latin-1")
    r = pf.select("gangland", "weekend", "21")[0]
    assert (r.zone, r.day, r.slot, r.hours) == (7, 1, 10, "20-22")
    assert r.as_dict()["max_peds"] == 8 and r.as_dict()["casual_rich"] == 50
    pf.set(r, {0: 25, FIELDS.index("aircrew_runway"): 9})
    out = pf.render().decode("latin-1")
    back = PopcycleFile(out)
    assert back.rows[pf.rows.index(r)].values[0] == 25 and back.rows[pf.rows.index(r)].values[23] == 9
    assert sum(1 for a, b in zip(out.splitlines(), text.splitlines()) if a != b) == 1
    with pytest.raises(LookupError):
        pf.select("NOWHERE", None, None)
    with pytest.raises(LookupError):
        pf.select(None, "holiday", None)
