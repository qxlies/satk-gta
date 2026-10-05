"""satk.describe binding and the import/status/clear operations on a fake index with real DFF bytes (M2-13).

Notes go to the isolated ``satk_home`` workspace (``work/notes.sqlite``); no game files are read.
"""

from __future__ import annotations

import json

import pytest

from satk.describe.bind import bind
from satk.describe.pack import load_pack
from satk.notes import db as notes


def _imp(run_cli, pack, *extra):
    r = run_cli(["describe", "import", "--pack", str(pack), *extra])
    assert r.code == 0, r.out + r.err
    return r.json


def _imported():
    found, _ = notes.list_notes(author="import:gta-scout", limit=1000)
    return {n.sid: n for n in found}


def test_bind_by_hash_only(fake_world, pack_file):
    b = bind(load_pack(pack_file), fake_world("vanilla"))
    got = {(d.sid, d.entry.name, d.by_name) for d in b.descs}
    # infernus: hash + name; roads: same bytes, other publisher name (hash only); lod: other bytes -> unbound
    assert got == {("model:411", "infernus", True), ("model:17613", "roads_published_name", False)}
    assert b.stats == {"models_in_profile": 3, "dff_hashed": 3, "entries_bound": 2, "entries_unmatched": 1,
                       "bound": 2, "models": 2, "hash_only": 1}
    assert b.warn == []


def test_import_writes_attributed_notes_and_is_idempotent(fake_world, pack_file, run_cli):
    first = _imp(run_cli, pack_file)
    assert (first["bound"], first["notes"], first["added"], first["unchanged"]) == (2, 2, 2, 0)
    assert first["profile"] == "vanilla" and first["pack"]["version"] == "sa-2026-10-03"
    assert first["sample"][0] == ["model:411", "Red sports car parked next to a park bench."]
    n = _imported()
    car = n["model:411"]
    assert (car.author, car.lang, car.text) == ("import:gta-scout", "en", "Red sports car parked next to a park bench.")
    assert car.tag_list[:2] == ["desc", "gta-scout"] and {"car", "red"} <= set(car.tag_list)
    assert car.confidence == 0.9 and car.created_at == "2026-10-03T00:00:00Z"
    assert car.evidence.startswith("gta-scout:sa-2026-10-03#") and "gta3.img/infernus.dff sha256:" in car.evidence
    assert "published as roads_published_name" in n["model:17613"].evidence
    ids = {s: x.id for s, x in n.items()}

    again = _imp(run_cli, pack_file)
    assert (again["added"], again["unchanged"], again["replaced"], again["pruned"]) == (0, 2, 0, 0)
    assert {s: x.id for s, x in _imported().items()} == ids  # nothing rewritten


def test_found_by_asset_find_and_note_list(fake_world, pack_file, run_cli):
    _imp(run_cli, pack_file)
    r = run_cli(["asset", "find", "bench", "--kind", "note"])
    assert r.code == 0 and [row[2] for row in r.json["rows"]] == ["model:411"]
    r = run_cli(["note", "list", "model:17613"])
    assert r.json["total"] == 1 and r.json["rows"][0][4] == "import:gta-scout"
    assert run_cli(["note", "list", "--tag", "desc"]).json["total"] == 2


def test_dry_run_writes_nothing(fake_world, pack_file, run_cli, satk_home):
    env = _imp(run_cli, pack_file, "--dry-run")
    assert env["dry_run"] is True and env["added"] == 2
    assert not (satk_home / "work" / "notes.sqlite").exists()


def test_changed_text_replaces_only_that_note_and_keeps_others(fake_world, pack_file, run_cli, scout, tmp_path):
    _imp(run_cli, pack_file)
    notes.add_note("model:411", "My own note", author="human:me", tags=["desc"])
    v2 = scout.write_pack(tmp_path / "v2" / "sa-2026-11-01.json", [
        scout.entry("infernus", "EN: Red sports car, corrected text.", data=scout.INFERNUS, tags=("car", "red")),
    ], version="sa-2026-11-01")
    env = _imp(run_cli, v2)
    # roads is not in v2: kept (not bound any more) until --prune; infernus text changed: replaced
    assert (env["added"], env["replaced"], env["unchanged"], env["kept_unbound"]) == (1, 1, 0, 1)
    n = _imported()
    assert n["model:411"].text == "Red sports car, corrected text."
    assert n["model:411"].created_at == "2026-11-01T00:00:00Z" and "model:17613" in n
    pruned = _imp(run_cli, v2, "--prune")
    assert (pruned["pruned"], pruned["unchanged"], pruned["added"]) == (1, 1, 0)
    assert set(_imported()) == {"model:411"}
    mine = notes.list_notes(author="human:me")[0]
    assert [x.text for x in mine] == ["My own note"]  # other authors are never touched


def test_text_full_and_tag_change_replace(fake_world, pack_file, run_cli):
    _imp(run_cli, pack_file)
    env = _imp(run_cli, pack_file, "--text", "full")
    assert (env["replaced"], env["added"], env["unchanged"]) == (1, 1, 1)  # only infernus has FR/EN
    car = _imported()["model:411"]
    assert car.text.startswith("FR: Banc rouge.") and car.lang == "mul"


def test_clear_removes_only_imported(fake_world, pack_file, run_cli):
    _imp(run_cli, pack_file)
    notes.add_note("model:411", "Keep me", author="claude", tags=["desc"])
    dry = run_cli(["describe", "clear", "--dry-run"]).json
    assert dry["would_remove"] == 2 and dry["removed"] == 0
    env = run_cli(["describe", "clear"]).json
    assert env["removed"] == 2 and _imported() == {}
    assert [x.text for x in notes.list_notes()[0]] == ["Keep me"]


def test_status(fake_world, pack_file, run_cli):
    s = run_cli(["describe", "status", "--pack", str(pack_file)]).json
    assert s["ok"] and s["pack"]["models_with_dff"] == 3 and s["imported"] == 0
    _imp(run_cli, pack_file)
    s = run_cli(["describe", "status", "--pack", str(pack_file)]).json
    assert (s["imported"], s["models"]) == (2, 2)
    missing = run_cli(["describe", "status"]).json  # no clone in the isolated workspace
    assert missing["ok"] and "pack" not in missing and missing["warn"][0].startswith("NOT_FOUND")


def test_import_without_pack_or_index(satk_home, run_cli, pack_file):
    r = run_cli(["describe", "import"])
    assert r.code == 1 and r.json["error"]["code"] == "NOT_FOUND" and "gta-scout" in r.json["error"]["hint"]
    r = run_cli(["describe", "import", "--pack", str(pack_file)])
    assert r.code == 3 and r.json["error"]["code"] == "INDEX_MISSING"


def test_samp_override_gets_name_sid(fake_world, run_cli, scout, tmp_path):
    pack = scout.write_pack(tmp_path / "s" / "sa-2026-10-03.json", [
        scout.entry("lapdna", "Police officer skin in dark uniform.", data=scout.LAPDNA, archive="SAMP.img"),
        scout.entry("infernus", "EN: Red car.", data=scout.INFERNUS),
    ])
    env = _imp(run_cli, pack, "--profile", "samp")
    assert env["profile"] == "samp" and env["bound"] == 2
    # model 300 is cutobj01 in vanilla and lapdna (SA-MP layer) in samp: the note names the model
    assert set(_imported()) == {"model:lapdna", "model:411"}
    vanilla = _imp(run_cli, pack, "--profile", "vanilla")
    assert vanilla["bound"] == 1 and vanilla["kept_unbound"] == 1


def test_nothing_bound_warns(fake_world, run_cli, scout, tmp_path):
    pack = scout.write_pack(tmp_path / "n" / "sa-2026-10-03.json",
                            [scout.entry("infernus", "EN: Red car.", data=scout.dff_bytes(5, 3))])
    env = _imp(run_cli, pack)
    assert env["bound"] == 0 and env["entries_unmatched"] == 1
    assert any(w.startswith("NOTHING_BOUND") for w in env["warn"])


def test_unreadable_blob_is_a_warning(fake_world, pack_file, run_cli, scout):
    db = fake_world("vanilla")
    img = db.root / "models" / "gta3.img"
    off = db.query("SELECT abs_off FROM blob WHERE name = 'infernus.dff'")["rows"][0][0]
    data = img.read_bytes()
    img.write_bytes(data[: off + len(scout.INFERNUS) + 100])  # stale index: the archive got shorter
    env = _imp(run_cli, pack_file)
    assert any(w.startswith("BLOB_UNREADABLE") for w in env["warn"])
    assert env["bound"] == 1  # infernus sits in the first half and is still bound


def test_bad_digest_needs_no_verify(fake_world, run_cli, scout, tmp_path):
    pack = scout.write_pack(tmp_path / "d" / "sa-2026-10-03.json",
                            [scout.entry("infernus", "EN: Red car.", data=scout.INFERNUS)], digest_ok=False)
    r = run_cli(["describe", "import", "--pack", str(pack)])
    assert r.json["error"]["code"] == "UNSUPPORTED"
    assert _imp(run_cli, pack, "--no-verify")["added"] == 1


def test_export_is_deterministic(fake_world, pack_file, run_cli, tmp_path):
    _imp(run_cli, pack_file)
    a = tmp_path / "a.jsonl"
    run_cli(["note", "export", "--out", str(a)])
    run_cli(["describe", "clear"])
    _imp(run_cli, pack_file)
    b = tmp_path / "b.jsonl"
    run_cli(["note", "export", "--out", str(b)])
    assert a.read_bytes() == b.read_bytes()
    recs = [json.loads(x) for x in a.read_text(encoding="utf-8").splitlines()]
    assert [r["sid"] for r in recs] == ["model:17613", "model:411"]


@pytest.mark.parametrize("name", ["describe.import", "describe.status", "describe.clear"])
def test_ops_are_cli_only(name):
    from satk.core.registry import get_op

    spec = get_op(name)
    assert spec.mcp_name is None and spec.group == "note" and len(spec.summary) <= 300


def test_agents_reach_import_but_need_consent_for_clear(fake_world, pack_file, run_cli):
    """Through the generic MCP access (satk_op; its CLI twin `satk mcp op`)."""
    found = run_cli(["mcp", "ops", "gta-scout"]).json
    assert {"describe.import", "describe.status"} <= {row[0] for row in found["rows"]}
    r = run_cli(["mcp", "op", "describe import", "--args", json.dumps({"pack": str(pack_file)})])
    assert r.code == 0 and r.json["added"] == 2, r.out
    r = run_cli(["mcp", "op", "describe.clear"])
    assert r.json["error"]["code"] == "CONSENT_REQUIRED" and len(_imported()) == 2
