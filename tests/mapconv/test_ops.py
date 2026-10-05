"""``satk map convert`` / ``satk map validate`` through the CLI (isolated workspace, FakeIndexDB)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from satk.mapconv.scene import Material, MapObject, Removal, Scene, scene_from_json, scene_to_json

DATA = Path(__file__).parent / "data"
SAMPLE = DATA / "grove_sample.pwn"


@pytest.fixture
def fake_index(satk_home):
    from satk.index.api import FakeIndexDB, override_index

    db = FakeIndexDB()
    with override_index(db):
        yield db


def test_convert_pawn_to_mta_default_output(satk_home, fake_index, run_cli):
    r = run_cli(["map", "convert", str(SAMPLE), "--to", "mta"])
    assert r.code == 0, r.err
    env = r.json
    out = Path(env["file"])
    assert out == satk_home / "work" / "out" / "mapconv" / "grove_sample.map" and out.is_file()
    assert (env["from"], env["to"], env["objects"], env["removals"], env["materials"], env["texts"]) == (
        "pawn", "mta", 5, 1, 1, 1)
    rep = json.loads(Path(env["report"]).read_text(encoding="utf-8"))
    assert rep["materials"]["rows"] == [[2, 0, 1280, "benches_cj", "Metal3_128", "0xFF808080"]]
    assert rep["texts"]["rows"][0][:3] == [2, 1, "Grove Street"]
    text = out.read_text(encoding="utf-8")
    assert text.startswith('<map edf:definitions="editor_main">') and 'model="19379"' in text


def test_convert_names_from_index_and_inferred_format(satk_home, fake_index, run_cli, tmp_path):
    src = tmp_path / "x.pwn"
    src.write_text("CreateObject(17613, 2489.3, -1668.5, 12.3, 0.0, 0.0, 0.0);\n", encoding="utf-8")
    r = run_cli(["map", "convert", str(src), "--out", "sub/y.ipl"])
    assert r.code == 0, r.err
    out = Path(r.json["file"])
    assert out == satk_home / "work" / "out" / "mapconv" / "sub" / "y.ipl"
    lines = out.read_text(encoding="latin-1").splitlines()
    assert lines[1:] == ["inst", "17613, lae2_roads89, 0, 2489.3, -1668.5, 12.3, 0, 0, 0, 1, -1", "end"]
    assert r.json["to"] == "ipl" and "INTERIOR_ALL" in [i[1] for i in r.json["issues"]]


def test_convert_errors(satk_home, fake_index, run_cli, tmp_path):
    r = run_cli(["map", "convert", str(SAMPLE)])
    assert r.code == 2 and r.json["error"]["code"] == "BAD_PARAMS"
    r = run_cli(["map", "convert", str(tmp_path / "missing.pwn"), "--to", "mta"])
    assert r.code == 1 and r.json["error"]["code"] == "NOT_FOUND"
    r = run_cli(["map", "convert", str(SAMPLE), "--out", "x.weird"])
    assert r.code == 2
    bad = tmp_path / "bad.map"
    bad.write_text("<map><object", encoding="utf-8")
    r = run_cli(["map", "convert", str(bad), "--to", "pawn"])
    assert r.code == 2 and "not a valid .map" in r.json["error"]["msg"]
    r = run_cli(["map", "convert", str(SAMPLE), "--out", "../../escape.map"])
    assert r.code == 2


def test_convert_refuses_to_overwrite_outside_work_without_force(satk_home, fake_index, run_cli, tmp_path):
    dst = tmp_path / "out.map"
    dst.write_text("old", encoding="utf-8")
    r = run_cli(["map", "convert", str(SAMPLE), "--out", str(dst)])
    assert r.code == 1 and r.json["error"]["code"] == "EXISTS"
    r = run_cli(["map", "convert", str(SAMPLE), "--out", str(dst), "--force"])
    assert r.code == 0 and dst.read_text(encoding="utf-8").startswith("<map")
    r = run_cli(["map", "convert", str(dst), "--out", str(dst), "--force"])
    assert r.code == 2  # output == input


def test_convert_without_index_still_works(satk_home, run_cli):
    r = run_cli(["map", "convert", str(SAMPLE), "--to", "pawn"])
    assert r.code == 0, r.err
    assert any(w.startswith("NO_NAMES") for w in r.json.get("warn", []))


def test_pawn_to_ipl_reports_losses(satk_home, fake_index, run_cli):
    r = run_cli(["map", "convert", str(SAMPLE), "--to", "ipl"])
    codes = {i[1] for i in r.json["issues"]}
    assert {"LOST_MATERIALS", "LOST_REMOVALS", "LOST_STREAMER", "TILT_LOST"} <= codes


def test_json_round_trip(satk_home, fake_index, run_cli):
    r = run_cli(["map", "convert", str(SAMPLE), "--to", "json"])
    assert r.code == 0
    p = Path(r.json["file"])
    sc = scene_from_json(p.read_text(encoding="utf-8"))
    assert len(sc.objects) == 5 and sc.objects[2].materials[0].tex == "Metal3_128"
    r2 = run_cli(["map", "convert", str(p), "--to", "json", "--out", "again.json"])
    assert r2.code == 0
    assert Path(r2.json["file"]).read_text(encoding="utf-8") == p.read_text(encoding="utf-8")


def test_scene_json_validation():
    sc = Scene(objects=[MapObject(1, (1.0, 2.0, 3.0), materials=[Material(0, 1, "a", "b")])],
               removals=[Removal(5, (0.0, 0.0, 0.0), 1.0)])
    text = scene_to_json(sc)
    assert '"line"' not in text and '"scale"' not in text  # defaults and provenance are omitted
    back = scene_from_json(text)
    assert back.objects[0].pos == (1.0, 2.0, 3.0) and back.removals[0].radius == 1.0
    for bad in ('{"format": "x"}', '{"format": "satk-map", "version": 2}',
                '{"format": "satk-map", "version": 1, "objects": [{"model": 1}]}',
                '{"format": "satk-map", "version": 1, "objects": [{"model": "a", "pos": [0, 0, 0]}]}',
                '{"format": "satk-map", "version": 1, "objects": [{"model": 1, "pos": [0, 0, 0], "zz": 1}]}',
                '{"format": "satk-map", "version": 1, "objects": {"a": 1}}', "[1,"):
        with pytest.raises(ValueError):
            scene_from_json(bad)


# ---------------------------------------------------------------------------- validate

VALIDATE_SRC = """
CreateObject(17613, 2489.3, -1668.5, 12.3, 0.0, 0.0, 0.0);
CreateObject(17613, 2489.3, -1668.5, 12.3, 0.0, 0.0, 0.0);
o = CreateObject(411, 5000.0, 0.0, 3000.0, 0.0, 0.0, 0.0);
SetObjectMaterial(o, 16, 999, "bistro", "vent_64", 0);
SetObjectMaterial(o, 1, 411, "bistro", "nope", 0);
CreateObject(19379, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0);
CreateObject(-1500, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0);
RemoveBuildingForPlayer(playerid, 17613, 2489.3, -1668.5, 12.3, 1.0);
RemoveBuildingForPlayer(playerid, 411, 0.0, 0.0, 0.0, 1.0);
RemoveBuildingForPlayer(playerid, 411, 0.0, 0.0, 0.0, 1.0);
"""


def test_validate_against_the_index(satk_home, fake_index, run_cli, tmp_path):
    src = tmp_path / "v.pwn"
    src.write_text(VALIDATE_SRC, encoding="utf-8")
    r = run_cli(["map", "validate", str(src), "--limit", "50"])
    assert r.code == 0, r.err
    env = r.json
    rows = {(row[1], row[2].split(" (")[0]) for row in env["rows"]}
    assert ("DUP_OBJECT", "obj 1") in rows
    assert ("OUT_OF_MAP", "obj 2") in rows and ("Z_RANGE", "obj 2") in rows
    assert ("MAT_SLOT", "obj 2") in rows and ("MAT_MODEL_UNKNOWN", "obj 2") in rows
    assert ("MAT_TEX_UNKNOWN", "obj 2") in rows  # bistro/nope; bistro/vent_64 exists
    assert sum(1 for row in env["rows"] if row[1] == "MAT_TEX_UNKNOWN") == 1
    assert ("MODEL_UNKNOWN", "obj 3") in rows and ("MODEL_UNKNOWN", "obj 4") in rows
    samp = next(row for row in env["rows"] if row[1] == "MODEL_UNKNOWN" and "19379" in row[3])
    assert "--profile samp" in samp[3]
    assert ("REMOVE_NO_LOD", "rm 0") in rows
    assert "17858" in next(row[3] for row in env["rows"] if row[1] == "REMOVE_NO_LOD")
    assert ("REMOVE_NOTHING", "rm 1") in rows and ("REMOVE_DUP", "rm 2") in rows
    assert env["errors"] == 3 and env["objects"] == 5 and env["removals"] == 3
    assert [row[0] for row in env["rows"]] == sorted((row[0] for row in env["rows"]),
                                                     key=["error", "warn", "info"].index)
    r = run_cli(["map", "validate", str(src), "--strict"])
    assert r.code == 1 and r.json["error"]["code"] == "CHECK_FAILED"
    page = run_cli(["map", "validate", str(src), "--limit", "2"]).json
    assert page["n"] == 2 and page["next"] == "o2" and page["total"] == len(env["rows"])
    nxt = run_cli(["map", "validate", str(src), "--limit", "2", "--cursor", "o2"]).json
    assert nxt["rows"] == env["rows"][2:4]


def test_validate_lod_removed_too(satk_home, fake_index, run_cli, tmp_path):
    src = tmp_path / "ok.pwn"
    src.write_text("RemoveBuildingForPlayer(playerid, 17613, 2489.3, -1668.5, 12.3, 1.0);\n"
                   "RemoveBuildingForPlayer(playerid, 17858, 2489.3, -1668.5, 12.3, 1.0);\n", encoding="utf-8")
    env = run_cli(["map", "validate", str(src)]).json
    assert env["rows"] == [] and env["errors"] == 0
    m = tmp_path / "ok.map"
    m.write_text('<map><removeWorldObject model="17613" lodModel="17858" radius="1" posX="2489.3" posY="-1668.5" '
                 'posZ="12.3"/></map>', encoding="utf-8")
    assert run_cli(["map", "validate", str(m)]).json["rows"] == []


def test_validate_without_index(satk_home, run_cli):
    env = run_cli(["map", "validate", str(SAMPLE)]).json
    assert env["ok"] and any(w.startswith("NO_INDEX") for w in env["warn"])
