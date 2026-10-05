"""notes.sqlite: schema, notes with Cyrillic FTS, bookmarks, captures, export/import (SPEC §4.5)."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest

from satk.core.errors import SatkError
from satk.core.ids import Sid
from satk.notes import db


def test_schema_v1_applied(satk_home):
    con = db.connect()
    try:
        assert con.execute("PRAGMA user_version").fetchone()[0] == 1
        names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type IN ('table','trigger')")}
        assert {"note", "note_fts", "bookmark", "capture", "note_ai", "note_ad"} <= names
        assert con.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    finally:
        con.close()
    assert db.db_file() == satk_home / "work" / "notes.sqlite"


def test_schema_file_is_spec_ddl():
    sql = db.schema_sql()
    assert "PRAGMA user_version = 1;" in sql
    assert "tokenize = 'unicode61 remove_diacritics 2'" in sql
    con = sqlite3.connect(":memory:")
    con.executescript(sql)  # V12: compiles as-is
    con.close()


def test_readers_never_create_the_file(satk_home):
    assert db.list_notes() == ([], 0)
    assert db.counts() is None
    assert db.get_note(1) is None
    assert db.bookmark_list() == ([], 0)
    assert db.count_for("model:411") == 0
    assert not db.db_file().exists()


def test_add_dedupe_get_remove(satk_home):
    n, created = db.add_note("MODEL:411", "  Красная спортивная машина ", tags=["машина", "#авто машина"])
    assert created and n.sid == "model:411" and n.text == "Красная спортивная машина"
    assert n.tags == "машина авто" and n.author == "claude" and n.lang == "ru"
    assert n.note_sid == f"note:{n.id}" and n.created_at.endswith("Z")
    again, created2 = db.add_note("model:411", "Красная спортивная машина", tags=["другое"])
    assert not created2 and again.id == n.id
    assert db.get_note(f"note:{n.id}") == n
    assert db.count_for("model:411") == 1
    assert db.counts() == {"notes": 1, "bookmarks": 0, "captures": 0}
    removed = db.remove_note(n.id)
    assert removed is not None and removed.id == n.id
    assert db.remove_note(n.id) is None
    assert db.list_notes(q="машин") == ([], 0)  # FTS row deleted by the trigger


def test_author_env_and_explicit(satk_home, monkeypatch):
    monkeypatch.setenv("SATK_AUTHOR", "human:qx")
    n, _ = db.add_note("model:1", "a")
    assert n.author == "human:qx"
    m, _ = db.add_note("model:1", "a", author="claude")
    assert m.author == "claude" and m.id != n.id  # UNIQUE(sid, author, text)


@pytest.mark.parametrize("q", ["машин", "МАШИН", "спорт", "Машина", "крас маш"])
def test_fts_cyrillic_prefixes(satk_home, q):
    db.add_note("model:411", "Красная спортивная машина", tags=["машина"])
    db.add_note("model:596", "Полицейская машина ЛС")
    db.add_note("tex:bistro/vent_64", "Vent grille, 64x64")
    notes, total = db.list_notes(q=q)
    assert "model:411" in [x.sid for x in notes]
    assert total == len(notes)


def test_fts_ranking_ascii_and_injection_safe(satk_home):
    db.add_note("tex:bistro/vent_64", "Vent grille texture", tags=["vent"])
    db.add_note("model:411", "Infernus; has a vent on the hood")
    notes, total = db.list_notes(q="vent")
    assert total == 2 and notes[0].sid == "tex:bistro/vent_64"  # bm25: text + tag hit ranks first
    for nasty in ('vent" OR 1=1 --', "NEAR(vent", "vent*", "-vent", "^vent:"):
        notes, _ = db.list_notes(q=nasty)
        assert all(isinstance(x, db.Note) for x in notes)
    assert db.list_notes(q="грузовик") == ([], 0)
    with pytest.raises(SatkError) as e:
        db.list_notes(q="  ;; ")
    assert e.value.code == "BAD_PARAMS"


def test_query_that_is_a_sid_filters_by_sid(satk_home):
    db.add_note("model:411", "one")
    db.add_note("model:4110", "two")
    notes, total = db.list_notes(q="model:411")
    assert total == 1 and notes[0].text == "one"


def test_filters_and_paging(satk_home):
    for i in range(7):
        db.add_note("model:411", f"note {i}", tags=["a"] if i % 2 else ["b"], author="claude" if i < 5 else "human:x")
    page1, total = db.list_notes("model:411", limit=3)
    page2, _ = db.list_notes("model:411", limit=3, offset=3)
    assert total == 7 and [n.text for n in page1 + page2] == [f"note {i}" for i in range(6)]
    assert db.list_notes(author="human:x")[1] == 2
    assert db.list_notes(tag="a")[1] == 3
    assert db.list_notes(tag="#b")[1] == 4


@pytest.mark.parametrize("kwargs,code", [
    ({"sid": "nonsense", "text": "x"}, "BAD_ID"),
    ({"sid": "model:1", "text": "   "}, "BAD_PARAMS"),
    ({"sid": "model:1", "text": "x" * (db.MAX_TEXT + 1)}, "BAD_PARAMS"),
    ({"sid": "model:1", "text": "x", "lang": "russian"}, "BAD_PARAMS"),
    ({"sid": "model:1", "text": "x", "confidence": 1.5}, "BAD_PARAMS"),
    ({"sid": "model:1", "text": "x", "author": "bad\nname"}, "BAD_PARAMS"),
])
def test_bad_inputs(satk_home, kwargs, code):
    sid = kwargs.pop("sid")
    text = kwargs.pop("text")
    with pytest.raises(SatkError) as e:
        db.add_note(sid, text, **kwargs)
    assert e.value.code == code


def test_write_guard_refuses_protected_roots(satk_home):
    # the roots of the isolated workspace are protected like those of a real one
    for p in (satk_home / "src" / "notes.sqlite", satk_home / "GTA San Andreas" / "notes.sqlite",
              satk_home / "gta-sa-clean" / "notes.sqlite"):
        with pytest.raises(SatkError) as e:
            db.add_note("model:1", "x", path=str(p))
        assert e.value.code == "PROTECTED_PATH"
        assert not p.exists()
    db.add_note("model:1", "x")
    with pytest.raises(SatkError) as e:
        db.export_jsonl(str(satk_home / "src" / "notes.jsonl"))
    assert e.value.code == "PROTECTED_PATH"
    assert not (satk_home / "src" / "notes.jsonl").exists()


def test_export_import_roundtrip(satk_home, tmp_path):
    db.add_note("tex:bistro/vent_64", "Vent", tags=["b", "a"], confidence=0.5, evidence="cap:20261004-153201-ab12")
    db.add_note("model:411", "Красная спортивная машина", tags=["машина"])
    db.bookmark_save("grove_center", {"pos": [2495.0, -1687.0, 30.0], "look": [2495, -1670, 13]}, note="Гроув")
    out = tmp_path / "notes.jsonl"
    res = db.export_jsonl(out)
    assert res["notes"] == 2 and res["bookmarks"] == 1
    lines = out.read_text(encoding="utf-8").splitlines()
    recs = [json.loads(x) for x in lines]
    assert [r["sid"] for r in recs] == ["model:411", "tex:bistro/vent_64"]  # sorted
    assert "id" not in recs[0] and recs[0]["text"] == "Красная спортивная машина"  # UTF-8, no \u escapes
    assert "\\u" not in lines[0]
    again = tmp_path / "again.jsonl"
    db.export_jsonl(again)
    assert again.read_bytes().replace(b"\r\n", b"\n") == out.read_bytes().replace(b"\r\n", b"\n")
    # a fresh database gets everything back; a second import adds nothing
    other = tmp_path / "other.sqlite"
    r1 = db.import_jsonl(out, path=other)
    assert (r1["added"], r1["skipped"], r1["bookmarks_added"]) == (2, 0, 1)
    r2 = db.import_jsonl(out, path=other)
    assert (r2["added"], r2["skipped"], r2["bookmarks_added"]) == (0, 2, 0)
    notes, _ = db.list_notes(q="машин", path=other)
    assert notes[0].sid == "model:411" and notes[0].tags == "машина"
    assert db.bookmark_get("grove_center", path=other)["note"] == "Гроув"


def test_import_reports_bad_lines(satk_home, tmp_path):
    f = tmp_path / "notes.jsonl"
    f.write_text('{"sid":"model:1","text":"ok"}\nnot json\n{"sid":"zzz","text":"x"}\n\n', encoding="utf-8")
    r = db.import_jsonl(f)
    assert r["added"] == 1 and len(r["warn"]) == 2
    with pytest.raises(SatkError) as e:
        db.import_jsonl(tmp_path / "missing.jsonl")
    assert e.value.code == "NOT_FOUND"


def test_default_export_path_is_repo_data(repo_root):
    assert db.default_export_path() == repo_root / "data" / "notes" / "notes.jsonl"


def test_bookmarks(satk_home):
    pose = {"pos": [1, 2, 3], "look": [4, 5, 6], "fov": 70}
    b = db.bookmark_save("BM:Grove_Center", pose, env={"time": "12:00", "weather": 0})
    assert b["id"] == "bm:grove_center" and b["pose"] == pose and b["env"]["weather"] == 0
    with pytest.raises(SatkError) as e:
        db.bookmark_save("grove_center", pose, replace=False)
    assert e.value.code == "EXISTS"
    db.bookmark_save("grove_center", {"pos": [9, 9, 9]})
    assert db.bookmark_get("grove_center")["pose"] == {"pos": [9, 9, 9]}
    db.bookmark_save("ls_airport", pose)
    rows, total = db.bookmark_list()
    assert total == 2 and [r["name"] for r in rows] == ["grove_center", "ls_airport"]
    assert db.bookmark_list("air")[1] == 1
    assert db.bookmark_rm("ls_airport") and not db.bookmark_rm("ls_airport")
    for bad in ("", "has space", "x" * 65, "Ünï"):
        with pytest.raises(SatkError):
            db.bookmark_save(bad, pose)


def test_captures(satk_home, tmp_path):
    cid = db.new_capture_id()
    assert Sid.parse(cid).kind == "cap"
    png = tmp_path / "frame.png"
    c = db.capture_add(png, "ariane", {"pos": [1, 2, 3]}, w=960, h=540, backend_build="abc")
    assert c["id"].startswith("cap:") and c["path"] == str(png).replace("\\", "/") and c["w"] == 960
    c2 = db.capture_add(png, "mock", {"pos": [0, 0, 0]}, id="20261004-153201-ab12")
    assert c2["id"] == "cap:20261004-153201-ab12"
    assert db.capture_get("cap:20261004-153201-ab12")["target"] == "mock"
    rows, total = db.capture_list()
    assert total == 2
    assert db.capture_list("ariane")[1] == 1


def test_newer_schema_is_refused(satk_home):
    f = db.db_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(f)
    con.execute("PRAGMA user_version = 2")
    con.close()
    with pytest.raises(SatkError) as e:
        db.add_note("model:1", "x")
    assert e.value.code == "UNSUPPORTED"


def test_existing_protected_database_cannot_be_written(satk_home, monkeypatch, run_cli):
    from satk.core import config

    root = satk_home / "src"
    root.mkdir()
    path = root / "notes.sqlite"
    con = sqlite3.connect(path)
    con.executescript(db.schema_sql())
    con.close()
    before = path.read_bytes()
    monkeypatch.setenv("SATK_PATHS_WORK", str(root))
    config.reset()
    r = run_cli(["note", "add", "model:411", "forbidden insertion"])
    assert r.code == 1 and r.json["error"]["code"] == "PROTECTED_PATH"
    with pytest.raises(SatkError) as exc:
        db.connect(path)
    assert exc.value.code == "PROTECTED_PATH"
    assert path.read_bytes() == before
    assert list(root.iterdir()) == [path]


@pytest.mark.parametrize("version", [0, 2])
def test_readers_refuse_unknown_schema_without_modifying_it(satk_home, version):
    path = satk_home / "work" / "uninitialized.sqlite"
    path.touch()
    if version:
        con = sqlite3.connect(path)
        con.execute(f"PRAGMA user_version = {version}")
        con.close()
    before = path.read_bytes(), path.stat().st_mtime_ns
    with pytest.raises(SatkError) as exc:
        db.connect(path, create=False)
    assert exc.value.code == "UNSUPPORTED"
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    assert list(path.parent.iterdir()) == [path]


@pytest.mark.parametrize("extended", [False, pytest.param(True, marks=pytest.mark.skipif(
    os.name != "nt", reason="Windows extended paths"))])
def test_reader_connection_is_read_only_and_uri_escaped(satk_home, extended):
    root = satk_home / "src"
    root.mkdir()
    path = root / "notes #100%.sqlite"
    con = sqlite3.connect(path)
    con.executescript(db.schema_sql())
    con.close()
    before = path.read_bytes()
    reader = db.connect("\\\\?\\" + str(path) if extended else path, create=False)
    try:
        assert reader.execute("SELECT count(*) FROM note").fetchone()[0] == 0
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            reader.execute("CREATE TABLE forbidden(id INTEGER)")
    finally:
        reader.close()
    assert path.read_bytes() == before
    assert list(root.iterdir()) == [path]
