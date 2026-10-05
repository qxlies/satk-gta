"""Real-data acceptance of the sa-re symbol DB against tests/golden/re.json (SPEC §5.3 WP-09).

Builds the DB from gta-sa-clean + the read-only clones under ``src\\`` into a temp directory
(the shared ``work/re/symdb.sqlite`` is not touched). Marked ``game`` and ``slow``.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from satk.core.config import build as build_config
from satk.re import api
from satk.re.db import SymDb

pytestmark = [pytest.mark.game, pytest.mark.slow]

GOLDEN = json.loads((Path(__file__).resolve().parents[1] / "golden" / "re.json").read_text(encoding="utf-8"))
STARTS = json.loads((Path(__file__).with_name("data") / "function_starts.json").read_text(encoding="utf-8"))
SAMPLE = Path(__file__).with_name("data") / "crash_sample.txt"


@pytest.fixture(scope="module")
def real(tmp_path_factory):
    from satk.re.build import build_db, default_sources

    srcs = default_sources()
    out = tmp_path_factory.mktemp("symdb") / "symdb.sqlite"
    stats = build_db(out, srcs)
    db = SymDb(out)
    yield db, stats
    db.close()


def _get(d: dict, dotted: str):
    for part in dotted.split("."):
        d = d[part]
    return d


def _measured(db: SymDb, stats: dict) -> dict:
    neon_calls = (stats["patch_scan"].get("neon") or {}).get("calls", {}).get("MemPut", [0, 0])
    return {
        **stats,
        "patch_neon": stats["patch"].get("neon"),
        "patch_upstream": stats["patch"].get("upstream"),
        "neon_hookpos": (stats["patch"].get("neon") or {}).get("hookpos"),
        "neon_memput_calls_nonfast": sum(neon_calls),
        "upstream_patches": (stats["patch"].get("upstream") or {}).get("total", 0),
        "limit_arrays_with_len": db.count("limit_def", "WHERE kind='array' AND vanilla IS NOT NULL"),
        "build_seconds": stats["seconds"],
    }


def test_spec_ranges(real):
    db, stats = real
    m = _measured(db, stats)
    bad = []
    for key, (lo, hi) in GOLDEN["spec"].items():
        v = _get(m, key)
        if v is None or v < lo or (hi is not None and v > hi):
            bad.append(f"{key}={v} not in [{lo}, {hi}]")
    assert bad == []


def test_exact_when_sources_unchanged(real):
    db, stats = real
    revs = {s["kind"]: s["rev"] for s in db.sources()}
    if db.meta.get("exe_sha256") != GOLDEN["exe_sha256"] or any(revs.get(k) != v for k, v in GOLDEN["revs"].items()):
        pytest.skip(f"source revisions changed since the golden run: {revs}")
    m = _measured(db, stats)
    expected = GOLDEN["exact"] | STARTS["exact"]
    diff = {k: (m.get(k), v) for k, v in expected.items() if m.get(k) != v}
    assert diff == {}


def test_control_addresses(real):
    db, _ = real
    c = GOLDEN["control"]
    d = api.resolve_text(db, "0x53bf09")
    assert {k: d[k] for k in ("fn", "off", "src", "confidence")} == {k: c["0x53bf09"][k] for k in
                                                                   ("fn", "off", "src", "confidence")}
    sym, origin, src = c["0x53bf09"]["patch"]
    assert [sym, origin, src] in [[r[2], r[3], r[4]] for r in d["patches"]["rows"]]
    d = api.resolve_text(db, "gta_sa.exe+0x13E4FA")
    assert (d["fn"], d["off"]) == ("Game::Render2dStuff", "0x2ca")
    d = api.resolve_text(db, "0x407260")
    assert d["fn"] == "CWorld::GetSector" and d["thunk"] == c["0x407260"]["thunk"]
    d = api.resolve_text(db, "0x1566830")
    assert d["fn"] == "CWorld::GetSector" and d["in_hoodlum"] is True and d["confidence"] == "high"
    d = api.resolve_text(db, "0x6f4040")
    assert (d["fn"], d["src"], d["reversed"]) == ("CDoor::Process", "game_sa/Door.cpp:32", True)
    assert api.resolve_text(db, "0x41b1d0")["confidence"] != "high"
    assert api.find(db, "CPed::Update")["rows"][0][2] == "CPed::Update"
    s = api.src(db, "CDoor::Process", 30)
    assert s["file"] == "game_sa/Door.cpp" and s["first"] <= 32 <= s["first"] + len(s["lines"]) - 1
    g = api.global_detail(db, "0xc8d4c0")
    assert (g["name"], g["type"]) == ("gGameState", "int32")


def test_crash_sample_resolves(real):
    db, _ = real
    t = api.resolve_text(db, SAMPLE.read_text(encoding="utf-8"))
    for row in t["rows"]:
        if row[1] == "gta_sa.exe":
            assert row[2] and row[8] in ("high", "medium", "exact"), row
        else:
            assert row[0].startswith(row[1] + "+0x") and row[2] is None, row


@pytest.mark.parametrize("address,name,entry", [
    (0x43B0F0, "CPedToPlayerConversations::Update", 0x43B0F0),
    (0x43C190, "CConversationForPed::Update", 0x43C190),
    (0x15668B0, "CConversationForPed::Update", 0x43C190),
    (0x43AC40, "CConversationForPed::IsPlayerInPositionForConversation", 0x43AC40),
    (0x50E690, "CIdleCam::IdleCamGeneralProcess", 0x50E690),
])
def test_source_only_function_regressions(real, address, name, entry):
    db, _stats = real
    result = api.addr_detail(db, address)
    assert result["fn"] == name and result["start"] == hex(entry)
    assert result["confidence"] == "medium" and result["bounds"] == "next_start"


def test_inferred_hoodlum_tail_is_uncertain(real):
    db, _stats = real
    result = api.addr_detail(db, 0x1576FFF)
    assert result["in_hoodlum"] is True and result["confidence"] == "low"


def test_source_callsite_hooks_do_not_split_winmain_without_ghidra(real):
    db, stats = real
    assert stats["source_hook_interior_sites"] == 2
    assert db.func(0x7487CF) is None and db.func(0x7F6781) is None
    for address in (0x7487CF, 0x748A93):
        result = api.addr_detail(db, address)
        assert (result["fn"], result["start"], result["confidence"]) == ("Win::NOTSA_WinMain", "0x748710", "high")
    patches = api.patches(db, "Win::NOTSA_WinMain", None, "all", None, 500, 0)
    assert any(row[0] == "0x748a93" and row[2] == "HOOKPOS_WinLoop" for row in patches["rows"])


@pytest.fixture(scope="module")
def real_ghidra(tmp_path_factory):
    from satk.re.build import build_db, default_sources
    from satk.re.sources.ghidra import load_functions
    from satk.re.pe import PeImage

    export = build_config().paths.work / "re/ghidra/export/functions.jsonl"
    if not export.is_file():
        pytest.skip("optional Ghidra functions.jsonl is not available")
    sources = default_sources(ghidra_functions=export)
    out = tmp_path_factory.mktemp("symdb-ghidra") / "symdb.sqlite"
    stats = build_db(out, sources)
    db = SymDb(out)
    yield db, stats, load_functions(export, PeImage.open(sources.exe))
    db.close()


def test_every_ghidra_range_has_exact_canonical_owner(real_ghidra):
    db, stats, bounds = real_ghidra
    assert stats["ghidra"]["functions"] == len(bounds.ends) == 23254
    for lo, hi, entry, body in bounds.ranges:
        for address in (lo, hi - 1):
            result = db.amap.locate(address)
            assert (result.start, result.off, result.confidence) == (entry, address - body, "exact")


def test_ghidra_judges_reported_ambiguous_addresses(real_ghidra):
    db, stats, _bounds = real_ghidra
    assert stats["source_hook_interior_sites"] == 2
    moved = api.addr_detail(db, 0x15668B0)
    assert (moved["fn"], moved["start"], moved["confidence"]) == ("CConversationForPed::Update", "0x43c190", "exact")
    assert api.addr_detail(db, 0x1576FFF)["confidence"] == "none"
    assert api.addr_detail(db, 0x1566B00)["confidence"] == "none"  # hole between body ranges
    # The export disagrees with this part of the report: 7487CF is a call inside WinMain.
    call = api.addr_detail(db, 0x7487CF)
    assert (call["fn"], call["start"], call["confidence"]) == ("Win::NOTSA_WinMain", "0x748710", "exact")
    # The export has no function at 50E690. Keep the source-declared name with uncertainty.
    idle = api.addr_detail(db, 0x50E690)
    assert idle["fn"] == "CIdleCam::IdleCamGeneralProcess" and idle["confidence"] == "medium"


def test_donor_clones_stay_clean(real):
    cfg = build_config()
    for name in ("gta-reversed", "plugin-sdk-sa", "mtasa-neon"):
        repo = cfg.paths.src / name
        out = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, text=True,
                             env={**__import__("os").environ, "GIT_NO_LAZY_FETCH": "1", "GIT_OPTIONAL_LOCKS": "0"})
        assert out.returncode == 0 and out.stdout.strip() == "", name
    repo_root = Path(__file__).resolve().parents[2]
    files = subprocess.run(["git", "-C", str(repo_root), "ls-files"], capture_output=True, text=True).stdout
    assert "gta-reversed" not in files.lower()
