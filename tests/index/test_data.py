"""Schema v3 (M2-10): data files in the index (water, timecyc, carcols, handling, ped.dat, object.dat),
the SID kinds ``water:``, ``tcyc:``, ``handling:``, the views ``v_vehicle``/``v_ped`` and the v2 -> v3 rebuild.

Synthetic game of ``tests/index/conftest.py`` (no game files)."""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from satk.core.errors import SatkError
from satk.index import api
from satk.index.api import IndexDB, LocalSid, as_sid, parse_sid
from satk.index.build import build
from satk.index.ddl import SCHEMA_VERSION
from satk.index.golden import compute_metrics, verify
from satk.index.layers import HashCache

DATS = ["data/default.dat", "data/gta.dat"]


def _build(root: Path, out: Path, dats=DATS, **kw) -> dict:
    return build("vanilla", jobs=1, out=out, root=root, dat_files=dats, img_order="engine",
                 vanilla_manifest=root / "MANIFEST.sha256", hashcache=HashCache(out.parent / "hc.sqlite"), **kw)


@pytest.fixture
def built(game_root: Path, tmp_path: Path):
    out = tmp_path / "idx" / "vanilla.sqlite"
    rep = _build(game_root, out)
    db = IndexDB("vanilla", out)
    yield rep, db, game_root
    db.close()


def test_report_counts_and_no_warnings(built):
    rep, db, _root = built
    c = rep["counts"]
    assert (c["water_quad"], c["timecyc"], c["handling"], c["car_color"], c["object_data"]) == (3, 184, 1, 2, 2)
    assert "errors" not in rep and not [w for w in rep.get("warn", []) if "data/" in w]
    stats = json.loads(db.meta()["stats"])
    assert "data_missing" not in stats and stats["handling_anim_groups"] == 1 and stats["ped_rel"] == 3
    sig = {r[0] for r in json.loads(db.meta()["sources_sig"])}
    assert {"data/water.dat", "data/timecyc.dat", "data/handling.cfg", "data/object.dat"} <= sig


def test_metrics(built):
    _rep, db, _root = built
    m = compute_metrics(db._conn())
    assert (m["water.polys"], m["water.quads"], m["water.tris"], m["water.visible"], m["water.shallow"],
            m["water1.polys"]) == (2, 1, 1, 2, 1, 1)
    assert (m["tcyc.rows"], m["tcyc.weathers"], m["tcyc.hours"], m["tcyc.short_lines"]) == (184, 23, 8, 0)
    assert (m["handling.car"], m["handling.bike"], m["handling.cars_linked"], m["handling.unused"]) == (1, 0, 1, 0)
    assert (m["carcol.palette"], m["carcol.models"], m["carcol.sets"], m["carcol.car4"]) == (3, 1, 2, 0)
    assert m["carcol.out_of_palette"] == 0 and m["carcol.unmatched"] == 0
    assert (m["ped_rel.rows"], m["ped_rel.pedtypes"]) == (3, 2)
    assert (m["object.rows"], m["object.loaded"], m["object.models"]) == (2, 1, 1)
    assert (m["view.v_vehicle"], m["view.v_ped"]) == (1, 0)


def test_model_shows_handling_colors_physics(built):
    _rep, db, _root = built
    o = db.get("model:400")
    assert o["links"]["handling"] == "handling:testcar"
    assert o["handling"] == {"mass": 1500.0, "max_vel": 200.0, "accel": 28.0, "gears": 5, "drive": "R", "engine": "P",
                             "brake": 11.0, "steer_lock": 30.0, "value": 35000}
    assert o["colors"] == [[1, 2], [2, 1]] and o["color_rgb"] == {"1": "#f5f5f5", "2": "#2a77a1"}
    assert db.get("model:1000")["physics"] == {"mass": 20.0, "turn_mass": 20.0, "elasticity": 0.03, "dmg_mult": 2.5,
                                               "dmg_effect": "smash", "col_response": "smallbox",
                                               "fx": "explosion_crate", "line": 2}
    lod = db.get("model:1001")["physics"]  # only listed after the '*' terminator: the engine never reads it
    assert lod["loaded"] is False and lod["dmg_effect"] == "none"
    assert "handling" not in db.get("model:1000") and "colors" not in db.get("model:1000")


def test_get_handling_and_refs(built):
    _rep, db, _root = built
    h = db.get("handling:TESTCAR")
    assert h["id"] == "handling:testcar" and h["name"] == "TESTCAR" and h["kinds"] == ["car"]
    assert h["car"]["mass"] == 1500.0 and h["car"]["model_flags"] == "0x2800" and h["car"]["handling_flags"] == "0x10200000"
    assert h["model_flags"] == ["CONVERTIBLE", "DOUBLE_EXHAUST"]
    assert h["handling_flags"] == ["OFFROAD_ABILITY2", "SWINGING_CHASSIS"]
    assert h["models"] == ["model:400"] and h["file"] == "file:data/handling.cfg" and h["line"] == 2
    assert db.refs("handling:testcar") == {"ok": True, "id": "handling:testcar", "rels": {"models": 1}}
    assert db.refs("handling:testcar", rel="models")["rows"] == [["model:400", "testcar", "car executive"]]
    with pytest.raises(SatkError) as e:
        db.get("handling:testca")
    assert e.value.code == "NOT_FOUND" and e.value.did_you_mean == ["handling:testcar"]
    with pytest.raises(SatkError) as e:
        db.get("handling:ignored")  # after ';the end'
    assert e.value.code == "NOT_FOUND"


def test_get_tcyc(built):
    _rep, db, _root = built
    t = db.get("tcyc:w3/12")
    assert t["id"] == "tcyc:w3/12" and t["weather"] == 3 and t["hour"] == 12 and t["far_clip"] == 500.0
    assert t["amb"] == "#030303" and t["sky_top"] == "#4475d2" and t["water"] == "#5aaaaaf0" and "nread" not in t
    assert db.get("tcyc:3/12")["id"] == "tcyc:w3/12"  # weather by number
    w = db.get("tcyc:first_w")
    assert w["weather"] == 0 and len(w["hours"]) == 8 and w["hours"][4] == "tcyc:first_w/12"
    assert w["far_clip"] == [100.0, 200.0, 300.0, 400.0, 500.0, 600.0, 700.0, 800.0]
    r = db.refs("tcyc:w3/12", rel="hours")
    assert r["total"] == 8 and r["rows"][0] == ["tcyc:w3/0", "0", "far_clip 100 fog_start 10"]
    for bad, code in (("tcyc:w3/13", "NOT_FOUND"), ("tcyc:nope", "NOT_FOUND"), ("tcyc:w3/x", "NOT_FOUND")):
        with pytest.raises(SatkError) as e:
            db.get(bad)
        assert e.value.code == code


def test_get_water(built):
    _rep, db, _root = built
    q = db.get("water:0")
    assert q["kind"] == "quad" and q["flags"] == 1 and q["visible"] is True and "shallow" not in q
    assert q["verts"][3] == [140.0, 240.0, 0.5] and q["bbox"] == [100.0, 200.0, 0.0, 140.0, 240.0, 0.5]
    assert q["waves"][0] == [0.0, 0.0, 0.05, 0.1] and q["file"] == "file:data/water.dat" and q["line"] == 2
    t = db.get("water:1")
    assert t["kind"] == "tri" and t["shallow"] is True
    w1 = db.get("water:water1/0")
    assert w1["config"] == 1 and "flags" not in w1 and w1["file"] == "file:data/water1.dat"
    assert db.refs("water:0") == {"ok": True, "id": "water:0"}
    for bad, code in (("water:7", "NOT_FOUND"), ("water:x/1", "BAD_ID"), ("water:abc", "BAD_ID")):
        with pytest.raises(SatkError) as e:
            db.get(bad)
        assert e.value.code == code
    rows = db._conn().execute("SELECT id FROM water_rtree WHERE minx <= 5 AND maxx >= 5 AND miny <= 5 "
                              "AND maxy >= 5").fetchall()
    assert sorted(r[0] for r in rows) == [2, 3]  # the triangle and the water1.dat quad


def test_near_water(built):
    _rep, db, _root = built
    n = db.near(5, 5, r=1, kinds=("water",))
    assert n["rows"] == [["water:1", None, "tri", [5.0, 5.0, 1.5], None, None, None, 0.0]]  # water1.dat: not default
    n = db.near(120, 260, r=30, kinds=("inst", "water"), lod="all", area=None)
    assert [r[0] for r in n["rows"]][:1] == ["water:0"] and n["rows"][0][2] == "quad" and n["rows"][0][7] == 20.0
    n = db.near(None, None, box=(90, 190, 160, 260), match="center", kinds=("water",))
    assert [r[0] for r in n["rows"]] == ["water:0"]


def test_ped_rel_and_views(built):
    _rep, db, _root = built
    rows = db.query("SELECT pedtype, rel, other FROM ped_rel ORDER BY line, other")["rows"]
    assert rows == [["CIVMALE", "respect", "CIVMALE"], ["CIVMALE", "hate", "COP"], ["COP", "respect", "COP"]]
    v = db.query("SELECT sid, type, class, handling, mass, max_vel, drive, colors FROM v_vehicle")["rows"]
    assert v == [["model:400", "car", "executive", "TESTCAR", 1500.0, 200.0, "R", 2]]


def test_find_data_kinds(built):
    _rep, db, _root = built
    f = db.find("testcar")
    assert [r[0] for r in f["rows"]] == ["model:400", "dff:testcar", "txd:testcar", "col:testcar", "handling:testcar"]
    assert f["rows"][4][3] == "car 1500 kg"
    t = db.find("w12", kind="tcyc")
    assert t["total"] == 1 and t["rows"][0][:3] == ["tcyc:w12", "tcyc", "W12"] and t["rows"][0][3] == "weather 12, 8 hours"
    assert db.find("first", kind="tcyc")["rows"][0][0] == "tcyc:first_w"


def test_peds_definitions(game_root: Path, tmp_path: Path):
    """``v_ped`` exposes the peds IDE fields; the model shows its ped type's acquaintances."""
    dat = game_root / "data" / "default.dat"
    dat.write_text(dat.read_text(encoding="latin-1") + "IDE DATA\\PEDS.IDE\n", encoding="latin-1")
    (game_root / "data" / "peds.ide").write_text(
        "peds\n7, male01, male01, CIVMALE, STAT_SENSIBLE_GUY, man, 0, 0, man, 1, 4, PED_TYPE_GEN, "
        "VOICE_GEN_MALE01, VOICE_GEN_MALE01\nend\n", encoding="latin-1")
    out = tmp_path / "idx" / "vanilla.sqlite"
    _build(game_root, out)
    with IndexDB("vanilla", out) as db:
        assert db.query("SELECT sid, pedtype, stat, voice1 FROM v_ped")["rows"] == [
            ["model:7", "CIVMALE", "STAT_SENSIBLE_GUY", "VOICE_GEN_MALE01"]]
        assert db.get("model:7")["pedtype_rel"] == {"respect": ["CIVMALE"], "hate": ["COP"]}


def test_samp_two_files_win(game_root: Path, tmp_path: Path, data_builders):
    """A profile loaded through SA-MP '.two' DAT files reads HANDLING.two/timecyc.two like the SA-MP client."""
    for name in ("default", "gta"):
        shutil.copy(game_root / "data" / f"{name}.dat", game_root / "data" / f"{name}.two")
    text = data_builders.files["data/handling.cfg"].replace("TESTCAR 1500.0", "TESTCAR 1600.0")
    (game_root / "data" / "HANDLING.two").write_text(text, encoding="latin-1")
    out = tmp_path / "idx" / "samp.sqlite"
    _build(game_root, out, dats=["data/default.two", "data/gta.two"])
    with IndexDB("samp", out) as db:
        h = db.get("handling:testcar")
        assert h["car"]["mass"] == 1600.0 and h["file"] == "file:data/handling.two"
        assert db.get("tcyc:first_w")["file"] == "file:data/timecyc.dat"  # no timecyc.two -> the .dat
    out2 = tmp_path / "idx2" / "vanilla.sqlite"
    _build(game_root, out2)  # the stock DAT files keep reading handling.cfg
    with IndexDB("vanilla", out2) as db:
        assert db.get("handling:testcar")["car"]["mass"] == 1500.0


def test_missing_data_files_are_not_warnings(game_root: Path, tmp_path: Path):
    for rel in ("water.dat", "water1.dat", "timecyc.dat", "carcols.dat", "handling.cfg", "ped.dat", "object.dat"):
        (game_root / "data" / rel).unlink()
    out = tmp_path / "idx" / "vanilla.sqlite"
    rep = _build(game_root, out)
    assert rep["ok"] and "errors" not in rep and not [w for w in rep.get("warn", []) if "data/" in w]
    with IndexDB("vanilla", out) as db:
        stats = json.loads(db.meta()["stats"])
        assert len(stats["data_missing"]) == 7 and stats["timecyc"] == 0
        o = db.get("model:400")
        assert "handling" not in o and "colors" not in o and "handling" not in o["links"]
        with pytest.raises(SatkError) as e:
            db.get("water:0")
        assert e.value.code == "NOT_FOUND"


def test_data_quirks_go_to_notes(game_root: Path, tmp_path: Path, data_builders):
    (game_root / "data" / "timecyc.dat").write_text(data_builders.timecyc_text(short=(16, 6)), encoding="latin-1")
    (game_root / "data" / "carcols.dat").write_text(
        "col\n0,0,0\n77.93,96\nend\ncar\ntestcar, 1,1\ntestcar, 0,1, 1,0\nend\n", encoding="latin-1")
    out = tmp_path / "idx" / "vanilla.sqlite"
    rep = _build(game_root, out)
    assert rep["ok"] and "errors" not in rep and rep["notes"] >= 3
    with IndexDB("vanilla", out) as db:
        notes = json.loads(db.meta()["notes"])
        assert any(n.startswith("data/timecyc.dat:") and "20 of 51" in n for n in notes)
        assert any(n.startswith("data/carcols.dat:3:") for n in notes)
        assert any("listed twice" in n for n in notes)
        t = db.get("tcyc:w16/20")
        assert t["nread"] == 20 and t["far_clip"] == 600.0  # repeated from 7 PM, like the engine
        assert db.get("model:400")["colors"] == [[0, 1], [1, 0]]  # the later line wins
        assert db.query("SELECT rgb FROM carcol WHERE idx = 1")["rows"] == [[0x4D5D60]]


def test_v2_index_needs_rebuild(built, tmp_path: Path):
    rep, db, root = built
    path = Path(db.path)
    db.close()
    c = sqlite3.connect(path)
    c.execute("PRAGMA user_version = 2")
    c.commit()
    c.close()
    with pytest.raises(SatkError) as e:
        IndexDB("vanilla", path)
    assert e.value.code == "INDEX_MISSING" and "schema v2" in str(e.value) and f"v{SCHEMA_VERSION}" in str(e.value)
    assert e.value.hint.startswith("satk index build")
    again = _build(root, path)  # the rebuild replaces the v2 file
    assert again["user_version"] == SCHEMA_VERSION == 3 and again["content_hash"] == rep["content_hash"]
    with IndexDB("vanilla", path) as db2:
        assert db2.get("handling:testcar")["models"] == ["model:400"]


def test_golden_examples_with_data_kinds(built):
    _rep, db, _root = built
    golden = {"metrics": {"water.polys": 2, "tcyc.rows": 184, "handling.car": 1},
              "examples": [{"get": "model:400", "expect": {"handling.mass": 1500.0, "colors": [[1, 2], [2, 1]]}},
                           {"get": "handling:testcar", "expect": {"car.drive": "R"}},
                           {"refs": "handling:testcar", "rel": "models", "contains": "model:400"},
                           {"get": "tcyc:w3/12", "expect": {"far_clip": 500.0}},
                           {"get": "water:0", "expect": {"kind": "quad"}},
                           {"find": "testcar", "kind": "handling", "total": 1}]}
    r = verify(db, golden)
    assert r["ok"] and r["checked"] == 9, r["failed"]


def test_parse_sid_local_kinds():
    for text, kind, key in (("water:12", "water", "12"), ("WATER:Water1/3", "water", "water1/3"),
                            ("tcyc:EXTRASUNNY_LA/12", "tcyc", "extrasunny_la/12"), (" handling:Infernus ", "handling", "infernus")):
        s = parse_sid(text)
        if "water" in api.LOCAL_KINDS:  # until satk.core.ids lists the data kinds
            assert isinstance(s, LocalSid)
        assert (s.kind, s.key, str(s)) == (kind, key, f"{kind}:{key}")
        assert as_sid(s) is s
    assert parse_sid("model:411").kind == "model" and set(api.DATA_KINDS) == {"water", "tcyc", "handling"}
    for bad in ("handling:", "water:1@vanilla"):
        with pytest.raises(SatkError) as e:
            parse_sid(bad)
        assert e.value.code == "BAD_ID"
    with pytest.raises(SatkError) as e:
        as_sid("handling:x", ("model",))
    assert e.value.code == "BAD_ID"


@pytest.fixture
def ws(synth_ws: Path, run_cli):
    api.clear_cache()
    r = run_cli(["index", "build", "--jobs", "1", "--json"])
    assert r.code == 0, r.out
    yield synth_ws
    api.clear_cache()


def test_cli_asset_get_refs_data_kinds(ws: Path, run_cli):
    r = run_cli(["asset", "get", "model:400", "--fields", "handling,colors"])
    assert r.code == 0 and r.json["handling"]["mass"] == 1500.0 and r.json["colors"] == [[1, 2], [2, 1]]
    r = run_cli(["asset", "get", "handling:testcar", "--fields", "models"])
    assert r.code == 0 and r.json == {"ok": True, "id": "handling:testcar", "models": ["model:400"]}
    r = run_cli(["asset", "refs", "tcyc:first_w", "--rel", "hours", "--limit", "2"])
    assert r.code == 0 and r.json["total"] == 8 and [x[0] for x in r.json["rows"]] == ["tcyc:first_w/0", "tcyc:first_w/5"]
    r = run_cli(["asset", "get", "water:water1/0"])
    assert r.code == 0 and r.json["config"] == 1
    r = run_cli(["asset", "get", "water:99"])
    assert r.code == 1 and r.json["error"]["code"] == "NOT_FOUND"
    r = run_cli(["index", "query", "SELECT count(*) FROM v_vehicle"])
    assert r.json["rows"] == [[1]]
