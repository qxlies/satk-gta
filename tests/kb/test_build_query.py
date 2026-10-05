"""satk.kb end to end on a synthetic world: build, then every operation through the CLI."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from kb_synth import make_inputs, make_world  # noqa: E402


@pytest.fixture
def built(tmp_path, satk_home):
    from satk.kb.build import build_kb
    from satk.kb.query import kb_path

    world = make_world(tmp_path / "w")
    out = kb_path()
    stats = build_kb(out, make_inputs(world))
    return out, stats, world


def _j(res):
    assert res.code == 0, res.out + res.err
    return json.loads(res.out)


def test_build_stats_and_tables(built):
    out, st, _w = built
    assert out.is_file() and not list(out.parent.glob("*.tmp-*"))
    c = st["counts"]
    assert c["opcode"] == 4 and c["fact"] == 40 and c["sym"] > 20 and c["chunk"] > 5 and c["struct"] >= 5
    assert st["gta-reversed"]["hooks"] == 4 and st["gta-reversed"]["handlers"] == 1
    assert st["mta-neon"]["changed_files"] == 1                  # only Common.h differs from upstream
    assert st["cleo-ai"]["with_handler"] == 1
    with sqlite3.connect(out) as con:
        assert con.execute("PRAGMA user_version").fetchone()[0] == 1
        keys = [r[0] for r in con.execute("SELECT key FROM source ORDER BY id")]
        assert keys == ["plugin-sdk", "gta-reversed", "mta-upstream", "mta-neon", "cleo-ai", "research", "facts"]
        # neon keeps only its own MAX_BUILDINGS
        rows = con.execute("SELECT s.sig, src.key FROM sym s JOIN file f ON f.id=s.file_id JOIN source src "
                           "ON src.id=f.source_id WHERE s.name='MAX_BUILDINGS' ORDER BY src.id").fetchall()
        assert rows == [("13000", "mta-upstream"), ("32000", "mta-neon")]


def test_search_finds_function_with_file_line(built, run_cli):
    env = _j(run_cli(["kb", "search", "CStreaming RequestModel", "--json"]))
    first = dict(zip(env["cols"], env["rows"][0]))
    assert first["kind"] == "func" and first["name"] == "CStreaming::RequestModel"
    assert first["loc"] == "source/game_sa/Streaming.cpp:11" and first["src"] == "gta-reversed"
    assert first["info"].startswith("0x4087E0 void CStreaming::RequestModel(int32 modelId")
    kinds = {r[0] for r in env["rows"]}
    assert {"func", "define", "code"} <= kinds


def test_search_address_lists_symbols_and_references(built, run_cli):
    env = _j(run_cli(["kb", "search", "0x8A5A80", "--json"]))
    rows = [dict(zip(env["cols"], r)) for r in env["rows"]]
    assert any(r["kind"] == "global" and r["name"] == "CStreaming::ms_memoryAvailable" for r in rows)
    assert any(r["kind"] == "define" and r["src"] == "mta-upstream" for r in rows)
    assert any(r["kind"] == "ref" for r in rows)
    assert any(r["kind"] == "fact" and r["name"] == "streaming.memory" for r in rows)
    env = _j(run_cli(["kb", "search", "gta_sa.exe+0x1B8E64", "--json"]))   # 0x5B8E64 in a comment
    assert any(r[0] == "ref" and "Streaming.cpp" in r[2] for r in env["rows"])


def test_search_filters_russian_and_paging(built, run_cli):
    env = _j(run_cli(["kb", "search", "память стриминга", "--json"]))
    assert any(r[0] == "doc" and r[3] == "research" for r in env["rows"])
    env = _j(run_cli(["kb", "search", "health", "--kind", "opcode", "--json"]))
    assert [r[1] for r in env["rows"]] == ["0223 SET_CHAR_HEALTH"]
    env = _j(run_cli(["kb", "search", "RequestModel", "--source", "plugin-sdk", "--json"]))
    assert env["rows"] and all(r[3] == "plugin-sdk" for r in env["rows"])
    p1 = _j(run_cli(["kb", "search", "CStreaming", "--limit", "2", "--json"]))
    assert p1["n"] == 2 and p1["next"]
    p2 = _j(run_cli(["kb", "search", "CStreaming", "--limit", "2", "--cursor", p1["next"], "--json"]))
    assert p2["rows"] and p2["rows"][0] != p1["rows"][0]
    bad = run_cli(["kb", "search", "x", "--source", "nope", "--json"])
    assert bad.code != 0


def test_sym_stages_and_kinds(built, run_cli):
    env = _j(run_cli(["kb", "sym", "CStreaming::RequestModel", "--json"]))
    assert env["match"] == "exact"
    rows = [dict(zip(env["cols"], r)) for r in env["rows"]]
    assert rows[0]["src"] == "gta-reversed" and rows[0]["addr"] == "0x4087E0"
    assert "decl source/game_sa/Streaming.h:" in rows[0]["info"]
    assert any(r["src"] == "plugin-sdk" for r in rows)
    env = _j(run_cli(["kb", "sym", "RequestModelStream", "--json"]))
    assert env["match"] == "member" and "not reversed" in env["rows"][0][6] and "locked" in env["rows"][0][6]
    env = _j(run_cli(["kb", "sym", "MAX_", "--kind", "const", "--json"]))
    assert env["match"] == "prefix" and {r[1] for r in env["rows"]} >= {"MAX_BUILDINGS", "MAX_IMG_FILES"}
    env = _j(run_cli(["kb", "sym", "RESOURCE_ID_COL", "--json"]))
    assert env["rows"][0][3] == "20001"                         # implicit enum value
    env = _j(run_cli(["kb", "sym", "ms_pPedPool", "--kind", "limit", "--json"]))
    assert env["rows"][0][3] == "140"
    env = _j(run_cli(["kb", "sym", "0x4087E0", "--json"]))
    assert env["match"] == "address" and len(env["rows"]) >= 3
    nf = run_cli(["kb", "sym", "NoSuchThing", "--json"])
    assert nf.code == 1 and json.loads(nf.out)["error"]["code"] == "NOT_FOUND"


def test_struct_fields_offsets_and_at(built, run_cli):
    env = _j(run_cli(["kb", "struct", "CPed", "--json"]))
    assert env["size"] == "0x50" and env["calc"] == "0x50" and env["layout"] == "ok" and env["bases"] == "CPlaceable"
    assert env["sizes"]["plugin-sdk"] == "0x50" and env["sizes"]["mta-upstream"] == "0x20 (CPedSAInterface)"
    rows = {r[1]: r for r in env["fields"]["rows"]}
    assert rows["m_fHealth"][0] == "0x1C" and rows["m_fHealth"][4] == "plugin-sdk"     # anchored by plugin-sdk
    assert rows["field_14"][4] == "plugin-sdk"
    bits = [r for r in env["fields"]["rows"] if r[2].startswith("bits:")]
    assert bits and bits[0][0] == "0x18" and bits[0][1].startswith("bIsStanding, bWasStanding, knock")
    env = _j(run_cli(["kb", "struct", "CPed", "--bits", "--match", "knock", "--json"]))
    assert env["fields"]["rows"] == [["0x18.2", "knock", "uint8:2", 1, "calc"]]
    env = _j(run_cli(["kb", "struct", "CPed", "--at", "0x10", "--json"]))
    assert env["fields"]["rows"][0][1] == "m_matrix" and "<CPlaceable>" in env["fields"]["rows"][0][4]
    env = _j(run_cli(["kb", "struct", "CPed", "--at", "0x2C", "--json"]))
    names = [r[1] for r in env["fields"]["rows"]]
    assert names == ["m_aPoints", "m_aPoints[0].z"]             # descends into CVector[2]: 0x2C = [0]+8
    env = _j(run_cli(["kb", "struct", "CPed", "--at", "0x34", "--json"]))
    assert env["fields"]["rows"][1][:2] == ["0x34", "m_aPoints[1].y"]
    env = _j(run_cli(["kb", "struct", "CPed", "--inherited", "--json"]))
    assert env["fields"]["rows"][0][1] == "m_pos"
    env = _j(run_cli(["kb", "struct", "CPed", "--source", "mta-upstream", "--json"]))
    assert env["name"] == "CPedSAInterface" and env["layout"] == "ok"
    rows = {r[1]: r for r in env["fields"]["rows"]}
    assert rows["fHealth"][0] == "0x1C" and rows["fHealth"][4] == "assert"
    nf = run_cli(["kb", "struct", "CNope", "--json"])
    assert json.loads(nf.out)["error"]["code"] == "NOT_FOUND"


def test_opcode_lookup(built, run_cli):
    env = _j(run_cli(["kb", "opcode", "0A8C", "--json"]))
    assert env["name"] == "WRITE_MEMORY" and env["cls"] == "Memory.Write" and len(env["input"]) == 4
    assert env["descr"] == "Writes the value at the memory address" and env["loc"] == "reference/ext-CLEO.md:5"
    env = _j(run_cli(["kb", "opcode", "set_char_health", "--json"]))
    assert env["op"] == "0223" and env["handler"].startswith("SetCharHealth (source/game_sa/Scripts/Commands/")
    assert env["details"] == "Health above the maximum is clamped."
    env = _j(run_cli(["kb", "opcode", "0A8D", "--json"]))
    assert env["total"] == 2                                      # one id, two extensions -> table
    env = _j(run_cli(["kb", "opcode", "0A8D", "--ext", "CLEO", "--json"]))
    assert env["name"] == "READ_MEMORY"
    env = _j(run_cli(["kb", "opcode", "memory", "--json"]))
    assert env["total"] == 3


def test_facts(built, run_cli):
    env = _j(run_cli(["kb", "fact", "--json"]))
    assert env["total"] == 40
    env = _j(run_cli(["kb", "fact", "pools.ped", "--json"]))
    assert env["confidence"] == "exe" and env["checks"]["cols"] == ["check", "expected", "got", "ok"]
    checks = {r[0]: r for r in env["checks"]["rows"]}
    assert checks["global ms_pPedPool"][3] is True and checks["limit ms_pPedPool"][3] is True
    assert checks["u32 0x550ff2"][3] is None                      # synthetic exe is not 1.0 US
    env = _j(run_cli(["kb", "fact", "pools", "--json"]))
    assert env["total"] == 11 and all(r[0].startswith("pools.") for r in env["rows"])
    env = _j(run_cli(["kb", "fact", "TaskAllocator", "--json"]))
    assert [r[0] for r in env["rows"]] == ["pools.small"]
    env = _j(run_cli(["kb", "fact", "--status", "mismatch", "--json"]))
    assert all(r[4] == "mismatch" for r in env["rows"])


def test_status_doctor_and_not_ready(satk_home, run_cli, tmp_path):
    res = run_cli(["kb", "search", "x", "--json"])
    assert res.code == 3 and json.loads(res.out)["error"]["code"] == "NOT_READY"
    from satk.kb.ops import _doctor, _status

    assert _status(False)["built"] is False and _doctor()["status"] == "warn"
    from satk.kb.build import build_kb
    from satk.kb.query import kb_path

    build_kb(kb_path(), make_inputs(make_world(tmp_path / "w2")))
    st = _status(True)
    assert st["built"] is True and st["counts"]["fact"] == 40 and st["stale"] == []
    assert _doctor()["msg"].startswith("kb: ")


def test_rebuild_replaces_file_and_skipped_sources(satk_home, tmp_path):
    from satk.kb.build import build_kb
    from satk.kb.query import kb_path

    world = make_world(tmp_path / "w3")
    out = kb_path()
    build_kb(out, make_inputs(world))
    st = build_kb(out, make_inputs(world, neon=None, research=None, cleo=None))
    with sqlite3.connect(out) as con:
        assert con.execute("SELECT COUNT(*) FROM opcode").fetchone()[0] == 0
        assert "mta-neon" not in {r[0] for r in con.execute("SELECT key FROM source")}
    assert "mta-neon" not in st


def test_build_needs_some_source(satk_home):
    from satk.core.errors import SatkError
    from satk.kb.build import Inputs, build_kb
    from satk.kb.query import kb_path

    with pytest.raises(SatkError) as e:
        build_kb(kb_path(), Inputs(skipped={"gta-reversed": "not found"}))
    assert e.value.code == "NOT_FOUND"
    assert not kb_path().exists()


def test_build_refuses_protected_output(satk_home, tmp_path):
    from satk.core.config import build as build_config
    from satk.core.errors import SatkError
    from satk.kb.build import build_kb

    src = build_config().paths.src
    with pytest.raises(SatkError) as e:
        build_kb(src / "kb.sqlite", make_inputs(make_world(tmp_path / "w4")))
    assert e.value.code in ("PROTECTED_PATH", "READ_ONLY")


def test_scanner_error_keeps_build_going(satk_home, tmp_path, monkeypatch):
    from satk.kb import cxx
    from satk.kb.build import build_kb
    from satk.kb.query import kb_path

    real = cxx.scan_file

    def flaky(path, text, **kw):
        if path.endswith("Pools.cpp"):
            raise IndexError("synthetic")
        return real(path, text, **kw)

    monkeypatch.setattr(cxx, "scan_file", flaky)
    st = build_kb(kb_path(), make_inputs(make_world(tmp_path / "w5")))
    assert st["scan_errors_total"] == 1 and "Pools.cpp: IndexError" in st["scan_errors"][0]
    assert st["counts"]["opcode"] == 4
