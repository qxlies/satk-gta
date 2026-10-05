"""Real-data acceptance of satk.kb (plan M2-02): build from the read-only clones under ``src\\``.

Builds into a temp directory (the shared ``work/kb/kb.sqlite`` is not touched) and checks the package
acceptance: ``kb search "CStreaming RequestModel"`` -> function with file:line; ``kb struct CPed`` ->
size and fields; ``kb opcode 0A8C`` -> description; build <= 2 min; ``src\\`` stays clean.
Marked ``game`` and ``slow``.
"""

from __future__ import annotations

import os
import time

import pytest

pytestmark = [pytest.mark.game, pytest.mark.slow]


def _skip(reason: str):
    if os.environ.get("SATK_TEST_NO_SKIP") == "1":
        pytest.fail(reason, pytrace=False)
    pytest.skip(reason)


def _git_status(tree) -> str | None:
    from satk.re.gitsrc import GitTree, run_git

    if not isinstance(tree, GitTree):
        return None
    return run_git(tree.repo, "status", "--porcelain", "--untracked-files=no").decode("utf-8", "replace")


@pytest.fixture(scope="module")
def real(tmp_path_factory):
    from satk.kb.build import build_kb, default_inputs

    inp = default_inputs()
    missing = [k for k in ("gtarev", "pluginsdk", "upstream", "cleo") if getattr(inp, k) is None]
    if missing:
        _skip(f"donor clones missing: {missing} ({inp.skipped})")
    trees = [inp.gtarev, inp.pluginsdk, inp.upstream, inp.cleo]
    before = [_git_status(t) for t in trees]
    out = tmp_path_factory.mktemp("kb") / "kb.sqlite"
    t0 = time.perf_counter()
    stats = build_kb(out, inp)
    elapsed = time.perf_counter() - t0
    after = [_git_status(t) for t in trees]
    return out, stats, elapsed, before, after, inp


def test_build_time_and_clean_sources(real):
    out, stats, elapsed, before, after, _inp = real
    assert elapsed <= 120, f"kb build took {elapsed:.0f} s"
    assert before == after, "a donor clone changed while building the KB"
    c = stats["counts"]
    assert c["sym"] > 50_000 and c["opcode"] >= 3_700 and c["chunk"] > 20_000 and c["fact"] == 40
    assert stats["gta-reversed"]["hooks"] >= 8_000


def test_search_finds_request_model_with_file_line(real):
    from satk.kb.query import search

    env = search("CStreaming RequestModel", path=real[0])
    first = dict(zip(env["cols"], env["rows"][0]))
    assert first["kind"] == "func" and first["name"] == "CStreaming::RequestModel" and first["src"] == "gta-reversed"
    path, _, line = first["loc"].rpartition(":")
    assert path == "source/game_sa/Streaming.cpp" and int(line) > 1
    assert first["info"].startswith("0x4087E0 void CStreaming::RequestModel(")


def test_struct_cped_size_and_fields(real):
    from satk.kb.query import struct

    env = struct("CPed", path=real[0])
    assert env["size"] == "0x79C" and env["calc"] == "0x79C" and env["layout"] == "ok" and env["bases"] == "CPhysical"
    assert env["sizes"]["plugin-sdk"] == "0x79C"
    rows = {r[1]: r for r in env["fields"]["rows"]}
    assert rows["m_fHealth"][0] == "0x540" and rows["m_pVehicle"][0] == "0x58C"
    assert struct("CPed", at="0x540", path=real[0])["fields"]["rows"][0][1] == "m_fHealth"


def test_opcode_0a8c(real):
    from satk.kb.query import opcode

    env = opcode("0A8C", path=real[0])
    assert env["name"] == "WRITE_MEMORY" and env["descr"] and len(env["input"]) == 4
    assert env["handler"].startswith("WriteMemory (source/game_sa/Scripts/Commands/CLEO/")


def test_facts_verified(real):
    _out, stats, *_rest, inp = real
    f = stats["facts"]
    assert f["mismatch"] == 0, "a curated fact no longer matches the sources: satk kb fact --status mismatch"
    if inp.exe is not None and "exe" not in f:
        assert f["verified"] == 40


def test_layout_quality(real):
    st = real[1]["gta-reversed"]["structs"]
    assert st["ok"] >= 700 and st["mismatch"] <= 25
    assert real[1]["plugin-sdk"]["offsets"] >= 4_000
