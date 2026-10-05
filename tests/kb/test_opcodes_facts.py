"""satk.kb.opcodes (cleo-ai reference) and satk.kb.facts (curated facts, check machinery)."""

from __future__ import annotations

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from kb_synth import CLEO_CHAR, CLEO_EXT, CLEO_INDEX, make_pe  # noqa: E402

from satk.kb.facts import CONFIDENCE, FACTS  # noqa: E402
from satk.kb.opcodes import parse_index, parse_reference, parse_sections  # noqa: E402

CHECK_KINDS = {"func", "global", "size", "limit", "const", "u8", "u16", "u32", "f32", "section"}


def test_parse_index_rows():
    rows = parse_index(CLEO_INDEX)
    assert [(o.op, o.name, o.ext, o.nparams) for o in rows][:2] == [("0223", "SET_CHAR_HEALTH", "default", 2),
                                                                    ("0A8C", "WRITE_MEMORY", "CLEO", 4)]
    assert rows[1].flags == "static" and rows[1].descr == "Writes the value at the memory address"


def test_parse_sections_details_and_params():
    ops = {o.name: o for o in parse_sections(CLEO_EXT, "reference/ext-CLEO.md")}
    rd = ops["READ_MEMORY"]
    assert (rd.cls, rd.member) == ("Memory", "Read")
    assert rd.input == ["address: int", "size: int", "vp: bool"]
    assert rd.output == ["result: any (variable)"]
    ch = parse_sections(CLEO_CHAR, "reference/opcodes-by-class/Char.md")[0]
    assert ch.details == "Health above the maximum is clamped."
    assert ch.line == 3


def test_parse_reference_merges_and_keeps_duplicate_ids():
    ops = parse_reference({"reference/opcode-index.md": CLEO_INDEX, "reference/ext-CLEO.md": CLEO_EXT,
                           "reference/opcodes-by-class/Char.md": CLEO_CHAR})
    by = {(o.op, o.name): o for o in ops}
    assert len([o for o in ops if o.op == "0A8D"]) == 2          # same id in two extensions
    w = by[("0A8C", "WRITE_MEMORY")]
    assert w.member == "Write" and len(w.input) == 4 and w.path == "reference/ext-CLEO.md"
    assert by[("0A8D", "READ_MEMORY_WITH_OFFSET")].input == []    # no detail section: index data only


def test_facts_are_well_formed():
    keys = [f["key"] for f in FACTS]
    assert len(FACTS) == 40 and len(set(keys)) == 40
    for f in FACTS:
        assert f["conf"] in CONFIDENCE, f["key"]
        assert f["title"] and f["value"], f["key"]
        assert f.get("checks"), f"{f['key']}: every fact has at least one machine check"
        for chk in f["checks"]:
            assert chk[0] in CHECK_KINDS and len(chk) in (3, 4), (f["key"], chk)
            if chk[0] in ("u8", "u16", "u32", "f32"):
                assert isinstance(chk[1], int) and 0x401000 <= chk[1] < 0x1600000


def test_check_machinery(tmp_path):
    """Exe and KB checks evaluate against a synthetic PE and fake KB maps."""
    import sqlite3

    from satk.kb.build import Inputs, _Build
    from satk.re.pe import PeImage

    exe = make_pe(tmp_path / "gta_sa.exe", {0x401010: struct.pack("<I", 140) + struct.pack("<f", 1.2) + b"\x6e"})
    img = PeImage.open(exe)
    b = _Build(sqlite3.connect(":memory:"), Inputs(), None)
    b.sids["gta-reversed"] = 1
    b.gr_funcs = {"Game::Idle": [0x53E920]}
    b.gr_globals = {"ScriptsArray": [0xA8B430]}
    b.limits = {"CPools::ms_pPedPool": [140]}
    b.struct_sizes = {"gta-reversed": {"CPed": 0x79C}}
    b.consts = {"mta-neon": {"MAX_COL_MODELS": 30000}}
    ok = [("u32", 0x401010, 140), ("f32", 0x401014, 1.2), ("u8", 0x401018, 110), ("section", ".text", 0x401000),
          ("func", "Idle", 0x53E920), ("global", "CTheScripts::ScriptsArray", 0xA8B430),
          ("limit", "ms_pPedPool", 140), ("size", "CPed", 0x79C), ("const", "MAX_COL_MODELS", 30000, "mta-neon")]
    for chk in ok:
        r = b._check(chk, img, None)
        assert r[3] is True, r
    assert b._check(("u32", 0x401010, 141), img, None)[3] is False
    assert b._check(("func", "Missing", 0x401000), img, None)[3] is False
    assert b._check(("size", "CPed", 1, "plugin-sdk"), img, None)[3] is None       # source not built
    assert b._check(("u32", 0x401010, 140), None, "no gta_sa.exe")[2:] == ["no gta_sa.exe", None]
    r = b._check(("u32", 0x401010, 140), img, None)
    assert r[1] == "140" and r[2] == "140"                                          # counts are decimal
    assert b._check(("global", "X", 0x8A5A80), img, None)[1] == "0x8A5A80"          # addresses are hex
