"""Note operations, the ``note`` SID provider and the ``notes`` status section (SPEC §4.5, WP-06 acc. 5)."""

from __future__ import annotations

import json

import pytest

from satk.core.errors import SatkError
from satk.core.ids import Sid, provider_for
from satk.core.registry import get_op, invoke, op_by_mcp, status_providers


def test_acceptance_flow_cli(satk_home, run_cli, tmp_path):
    """satk note add model:411 "Красная спортивная машина" --tags машина -> find 'машин' -> export (1 line)."""
    r = run_cli(["note", "add", "model:411", "Красная спортивная машина", "--tags", "машина"])
    assert r.code == 0, r.out
    env = r.json
    assert env["ok"] and env["note"].startswith("note:") and env["sid"] == "model:411" and env["tags"] == ["машина"]
    # what asset_find(query="машин", kind="note") does (WP-03 dispatches non-index kinds to the provider)
    found = provider_for("note").find("машин", "note", 20, None, "vanilla")
    assert found["cols"] == ["id", "kind", "name", "info"]
    assert [row[2] for row in found["rows"]] == ["model:411"] and found["rows"][0][1] == "note"
    out = tmp_path / "notes.jsonl"
    r = run_cli(["note", "export", "--out", str(out)])
    assert r.code == 0 and r.json["notes"] == 1
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["text"] == "Красная спортивная машина"


def test_note_list_cli_positional_sid_and_query(satk_home, run_cli):
    run_cli(["note", "add", "model:411", "Красная спортивная машина"])
    run_cli(["note", "add", "tex:bistro/vent_64", "vent grille"])
    by_sid = run_cli(["note", "list", "model:411"]).json
    assert by_sid["total"] == 1 and by_sid["rows"][0][1] == "model:411"
    by_q = run_cli(["note", "list", "--query", "vent"]).json
    assert by_q["total"] == 1 and by_q["rows"][0][1] == "tex:bistro/vent_64"
    assert run_cli(["note", "list"]).json["total"] == 2
    table = run_cli(["note", "list", "--table"], tty=True)
    assert "Красная спортивная машина" in table.out


def test_mcp_note_tool(satk_home):
    spec = op_by_mcp("note")
    added = invoke(spec, {"action": "add", "id": "model:411", "text": "Красная спортивная машина", "tags": ["машина"]})
    assert added["ok"] and added["note"].startswith("note:")
    dup = invoke(spec, {"action": "add", "id": "model:411", "text": "Красная спортивная машина"})
    assert dup["note"] == added["note"] and dup["warn"][0].startswith("EXISTS")
    listed = invoke(spec, {"action": "list", "query": "машин"})
    assert listed["total"] == 1 and listed["cols"] == ["id", "sid", "text", "tags", "author", "date"]
    assert invoke(spec, {"action": "list", "id": "model:411"})["total"] == 1
    bad = invoke(spec, {"action": "add", "id": "model:411"})
    assert bad["ok"] is False and bad["error"]["code"] == "BAD_PARAMS"
    bad_id = invoke(spec, {"action": "add", "id": "nonsense", "text": "x"})
    assert bad_id["error"]["code"] == "BAD_ID"


def test_paging_cursor(satk_home):
    spec = op_by_mcp("note")
    for i in range(5):
        invoke(spec, {"action": "add", "id": "model:1", "text": f"t{i}"})
    p1 = invoke(spec, {"action": "list", "limit": 2})
    p2 = invoke(spec, {"action": "list", "limit": 2, "cursor": p1["next"]})
    p3 = invoke(spec, {"action": "list", "limit": 2, "cursor": p2["next"]})
    assert [r[2] for r in p1["rows"] + p2["rows"] + p3["rows"]] == [f"t{i}" for i in range(5)]
    assert p3["next"] is None and p1["total"] == 5
    assert invoke(spec, {"action": "list", "cursor": "zz"})["error"]["code"] == "BAD_PARAMS"


def test_note_rm_and_import(satk_home, run_cli, tmp_path):
    nid = run_cli(["note", "add", "model:1", "x"]).json["note"]
    assert run_cli(["note", "rm", nid]).json["removed"] == nid
    r = run_cli(["note", "rm", nid])
    assert r.code == 1 and r.json["error"]["code"] == "NOT_FOUND"
    f = tmp_path / "n.jsonl"
    f.write_text('{"sid":"model:2","text":"y","author":"human:qx"}\n', encoding="utf-8")
    assert run_cli(["note", "import", "--file", str(f)]).json["added"] == 1


def test_provider_get_refs(satk_home):
    spec = get_op("note.add")
    nid = invoke(spec, {"sid": "fn:0x53BF09", "text": "CGame::Process hook site", "confidence": 0.9})["note"]
    p = provider_for("note")
    got = p.get(Sid.parse(nid), None, "vanilla")
    assert got["id"] == nid and got["sid"] == "fn:0x53bf09" and got["links"] == {"target": "fn:0x53bf09"}
    assert got["confidence"] == 0.9
    assert p.get(Sid.parse(nid), ["text"], "vanilla") == {"ok": True, "id": nid, "text": "CGame::Process hook site"}
    assert p.refs(Sid.parse(nid), None, 50, None, "vanilla")["rels"] == {"target": 1}
    assert p.refs(Sid.parse(nid), "target", 50, None, "vanilla")["rows"] == [["fn:0x53bf09", "target"]]
    with pytest.raises(SatkError) as e:
        p.get(Sid.parse("note:999"), None, "vanilla")
    assert e.value.code == "NOT_FOUND"


def test_bookmark_capture_provider_ready_for_viewer(satk_home):
    from satk.notes import db
    from satk.notes.provider import BookmarkCaptureProvider

    db.bookmark_save("grove_center", {"pos": [1, 2, 3]}, note="Гроув-стрит")
    p = BookmarkCaptureProvider()
    assert p.get(Sid.parse("bm:grove_center"), None, "vanilla")["pose"] == {"pos": [1, 2, 3]}
    rows = p.find("grove", "bm", 20, None, "vanilla")["rows"]
    assert rows == [["bm:grove_center", "bm", "grove_center", "Гроув-стрит"]]
    cap = db.capture_add("D:/ws/work/out/captures/x.png", "mock", {"pos": [0, 0, 0]}, w=4, h=4)
    assert p.get(Sid.parse(cap["id"]), None, "vanilla")["target"] == "mock"


def test_status_section(satk_home):
    fn = status_providers()["notes"]
    assert fn(False)["exists"] is False
    invoke("note.add", {"sid": "model:1", "text": "x"})
    sec = fn(False)
    assert sec["notes"] == 1 and sec["db"].endswith("/work/notes.sqlite")
