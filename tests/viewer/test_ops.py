"""``satk view …`` and ``satk saap …`` through the CLI (WP-07 acceptance 1, 2, 4, 5 at test level)."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import threading
from pathlib import Path

import pytest

from satk.core.ids import Sid, provider_for
from satk.core.registry import all_ops, get_op


def test_ops_registered():
    names = {o.name for o in all_ops()}
    for n in ("view.control", "view.start", "view.stop", "view.status", "view.goto", "view.capture", "view.pick",
              "view.set", "view.bookmark", "view.replay", "view.conformance", "view.mock", "saap.validate",
              "saap.call", "saap.cpp_selftest"):
        assert n in names, n
    mcp = {o.mcp_name for o in all_ops() if o.name.startswith("view.") and o.mcp_name}
    assert mcp == {"view_control", "view_goto", "view_capture", "view_pick", "view_set", "view_bookmark"}
    cap = get_op("view.capture").input_schema()
    assert cap["properties"]["marks"]["default"] == 0 and cap["properties"]["width"]["default"] == 960
    assert cap["properties"]["target"]["enum"] == ["ariane", "game", "mock"]


def test_saap_validate_cli(run_cli, repo_root, tmp_path):
    r = run_cli(["saap", "validate", str(repo_root / "proto" / "conformance" / "*.jsonl"),
                 str(repo_root / "proto" / "SAAP-v1.md")])
    assert r.code == 0, r.out
    d = r.json
    assert d["valid"] is True and d["schemas"] == 21 and len(d["rows"]) >= 12
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"id":"x","caps":["camera"],"steps":[{"call":"camera.set","params":{}}]}\n', encoding="utf-8")
    r = run_cli(["saap", "validate", str(bad)])
    assert r.code == 2 and r.json["error"]["code"] == "BAD_PARAMS"
    env = tmp_path / "req.json"
    env.write_text('{"saap":1,"id":"r","method":"ping"}', encoding="utf-8")
    assert run_cli(["saap", "validate", str(env)]).code == 0


def test_pick_mock_resolves_nearest(run_cli, satk_home):
    """Acceptance 4: the mock reports model 17613 at the inst:lae2_stream0#4 position with src model_pos."""
    r = run_cli(["view", "pick", "400", "300", "--target", "mock", "--json"])
    assert r.code == 0, r.out
    d = r.json
    row = dict(zip(d["cols"], d["rows"][0]))
    assert row["id"] == "inst:lae2_stream0#4" and row["link"] == "nearest" and row["model"] == "model:17613"
    assert any(w.startswith("INDEX_MISSING") for w in d["warn"])


def test_pick_cells_and_ground(run_cli, satk_home):
    d = run_cli(["view", "pick", "--cells", "A1,D4", "--target", "mock"]).json
    rows = [dict(zip(d["cols"], r)) for r in d["rows"]]
    assert rows[0]["link"] in ("ground", "none") and rows[1]["px"] == 350.0
    r = run_cli(["view", "pick", "--target", "mock"])
    assert r.code == 2


def test_capture_marks_grid_replay(run_cli, satk_home):
    """Acceptance 5: marks + grid + 4 legend rows + sidecar; replay gives the same PNG hash."""
    r = run_cli(["view", "capture", "--target", "mock", "--marks", "4", "--grid", "--json"])
    assert r.code == 0, r.out
    d = r.json
    assert d["id"].startswith("cap:") and len(d["legend"]) == 4 and d["settled"] is True
    for k in ("file", "marks_file", "grid_file", "sidecar", "ids_file"):
        assert Path(d[k]).is_file(), k
    assert d["marks_file"].endswith("_marks.png") and d["grid_file"].endswith("_grid.png")
    assert d["legend"][0][1] == "inst:lae2_stream0#4"
    assert hashlib.sha256(Path(d["file"]).read_bytes()).hexdigest() == d["sha256"]
    sc = json.loads(Path(d["sidecar"]).read_text(encoding="utf-8"))
    assert sc["target"] == "mock" and sc["request"]["marks"] == 4 and sc["legend"] == d["legend"]
    rp = run_cli(["view", "replay", d["sidecar"], "--json"])
    assert rp.code == 0, rp.out
    assert rp.json["same"] is True and rp.json["sha256"] == d["sha256"]
    # cap: provider
    got = provider_for("cap").get(Sid.parse(d["id"]), None, "vanilla")
    assert got["file"] == d["file"] and got["legend"] == d["legend"]
    refs = provider_for("cap").refs(Sid.parse(d["id"]), None, 10, None, "vanilla")
    assert refs["rows"][0][0] == "inst:lae2_stream0#4"


def test_capture_compare_and_pose(run_cli, satk_home):
    a = run_cli(["view", "capture", "--target", "mock", "--width", "320", "--height", "180"]).json
    b = run_cli(["view", "capture", "--target", "mock", "--width", "320", "--height", "180",
                 "--pos", "2489.3,-1730,70", "--look", "2489.3,-1668.5,12.3", "--fov", "60",
                 "--compare-to", a["id"]]).json
    assert b["w"] == 320 and b["pose"]["fov_h_deg"] == 60.0
    assert 0 < b["diff"]["ssim"] < 1 and Path(b["diff"]["file"]).is_file()
    r = run_cli(["view", "capture", "--target", "mock", "--layers", "color,normals"])
    assert r.code == 1 and r.json["error"]["code"] == "UNSUPPORTED"
    r = run_cli(["view", "capture", "--target", "mock", "--fov", "60"])
    assert r.code == 2


def test_goto_set_bookmarks_json_store(run_cli, satk_home):
    g = run_cli(["view", "goto", "--id", "inst:lae2_stream0#4", "--target", "mock", "--json"]).json
    assert g["framed"] == "inst:lae2_stream0#4" and g["pose"]["look"][0] == pytest.approx(2489.3, abs=0.2)
    assert run_cli(["view", "goto", "--target", "mock"]).code == 2
    s = run_cli(["view", "set", "--time", "21:30", "--weather", "8", "--overlays", "col", "--target", "mock"]).json
    assert s["env"]["time"] == "21:30" and s["view"]["overlays"] == ["col"]
    h = run_cli(["view", "set", "--hide", "inst:lae2_stream0#4", "--target", "mock"]).json
    assert h["view"]["hide"] == ["m1"]
    assert run_cli(["view", "set", "--target", "mock"]).code == 2
    pose = '{"pos":[1,2,3],"look":[1,5,3]}'
    assert run_cli(["view", "bookmark", "save", "grove", "--pose", pose, "--note", "тест"]).code == 0
    lst = run_cli(["view", "bookmark", "list"]).json
    assert lst["store"] == "json" and lst["rows"][0][0] == "bm:grove" and lst["rows"][0][4] == "тест"
    assert provider_for("bm").get(Sid.parse("bm:grove"), None, "vanilla")["pose"]["pos"] == [1, 2, 3]
    assert provider_for("bm").find("gro", None, 10, None, "vanilla")["rows"][0][0] == "bm:grove"
    g = run_cli(["view", "goto", "--bm", "grove", "--target", "mock"]).json
    assert g["pose"]["pos"] == [1, 2, 3] and g["bm"] == "bm:grove"
    assert run_cli(["view", "bookmark", "save", "Bad Name!", "--pose", pose]).code == 2
    assert run_cli(["view", "bookmark", "rm", "grove"]).code == 0
    assert run_cli(["view", "bookmark", "rm", "grove"]).code == 1


def test_bookmarks_use_notes_sqlite(run_cli, satk_home):
    """When WP-06's notes.sqlite has the bookmark table, bookmarks go there (JSON entries migrate)."""
    pose = '{"pos":[1,2,3],"look":[1,5,3]}'
    assert run_cli(["view", "bookmark", "save", "early", "--pose", pose]).code == 0
    db = satk_home / "work" / "notes.sqlite"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE bookmark(name TEXT PRIMARY KEY, pose TEXT NOT NULL, env TEXT, note TEXT, "
                "created_at TEXT NOT NULL) WITHOUT ROWID")
    con.commit()
    con.close()
    assert run_cli(["view", "bookmark", "save", "late", "--pose", pose, "--target", "mock"]).code == 0
    lst = run_cli(["view", "bookmark", "list"]).json
    assert lst["store"] == "notes.sqlite" and [r[0] for r in lst["rows"]] == ["bm:early", "bm:late"]
    con = sqlite3.connect(db)
    assert con.execute("SELECT count(*) FROM bookmark").fetchone()[0] == 2
    con.close()


def test_bookmark_save_current_camera(run_cli, satk_home):
    d = run_cli(["view", "bookmark", "save", "cur", "--target", "mock", "--json"]).json
    assert d["pose"]["pos"] == [2489.3, -1720.0, 60.0] and d["env"]["time"] == "12:00"


def test_status_and_conformance_inprocess(run_cli, satk_home):
    st = run_cli(["view", "status", "--target", "mock"]).json
    assert st["up"] is False
    c = run_cli(["view", "conformance", "--target", "mock", "--json"])
    assert c.code == 0 and c.json["fail"] == 0 and c.json["driver"] == "backend"
    r = run_cli(["view", "status", "--target", "ariane"])
    assert r.json["up"] is False


def test_conformance_against_mock_endpoint(run_cli, satk_home):
    """Acceptance 2 at test level: a mock endpoint (discovery files) -> 100 % PASS incl. negative cases."""
    from satk.saap.mock import serve

    ready = threading.Event()
    t = threading.Thread(target=serve, kwargs={"port": 0, "token": secrets.token_hex(32),
                                                "on_ready": lambda s: ready.set()}, daemon=True)
    t.start()
    assert ready.wait(10)
    try:
        r = run_cli(["view", "conformance", "--target", "mock", "--show-all", "--json"])
        d = r.json
        assert r.code == 0 and d["driver"] == "saap" and d["fail"] == 0 and d["skip"] == 0 and d["percent"] == 100.0
        assert d["pass"] == d["total"] >= 45
        res = {row[0]: row[2] for row in d["rows"]}
        for neg in ("auth.bad_token", "proto.oversize", "core.unknown_method", "camera.revision",
                    "capture.unsupported_layer"):
            assert res[neg] == "pass"
        call = run_cli(["saap", "call", "mock", "env.set", '{"time":"06:15"}', "--json"]).json
        assert call["result"]["time"] == "06:15"
        assert run_cli(["view", "pick", "400", "300", "--target", "mock"]).json["warn"][0].startswith("INDEX_MISSING")
        st = run_cli(["view", "status", "--target", "mock"]).json
        assert st["up"] is True and st["proto"] == "saap/1" and st["window"] == [800, 600]
        from satk.core.registry import status_providers

        assert status_providers()["viewer"](False)["mock"]["up"] is True
    finally:
        run_cli(["saap", "call", "mock", "quit"])
        t.join(10)


def test_doctor_check(satk_home):
    from satk.core.registry import doctor_checks

    r = doctor_checks()["viewer"]()
    assert r["status"] == "warn" and "build.ps1" in r["fix"]  # no ariane.exe under the temp workspace
