"""Address extraction from text and MTA crash dumps (SPEC §4.9.3 step 1; WP-09)."""

from __future__ import annotations

from pathlib import Path

from satk.re.crash import parse_text

SAMPLE = Path(__file__).with_name("data") / "crash_sample.txt"


def test_crash_sample():
    items = parse_text(SAMPLE.read_text(encoding="utf-8"))
    gta = [hex(i.addr) for i in items if i.is_gta]
    other = [(i.module, i.off) for i in items if not i.is_gta]
    # Offset line, EIP (duplicate of it), Reason (IDA value), stack frames; registers ignored
    assert gta == ["0x53bf09", "0x53e4fa", "0x407260", "0x1566830", "0x6f4040"]
    assert other == [("core.dll", 0x4A1B2), ("multiplayer_sa.dll", 0x12F00), ("game_sa.dll", 0xC1234),
                     ("netc.dll", 0x1234)]


def test_bare_tokens_and_modplus():
    items = parse_text("0x53BF09 53e4fa, gta_sa.exe+0x7260 core.dll+0x10")
    assert [(i.module, i.addr) for i in items] == [("gta_sa.exe", 0x53BF09), ("gta_sa.exe", 0x53E4FA),
                                                    ("gta_sa.exe", 0x407260), ("core.dll", None)]
    assert parse_text("0x53BF09 0x53bf09") == parse_text("0x53BF09")       # deduplicated


def test_free_text_hex_and_registers():
    text = "crashed near 0x0053BF09 while ESI=0x00C8D4C0; value 0x12 is not an address\nEAX=0x00401000 EBX=0"
    assert [hex(i.addr) for i in parse_text(text)] == ["0x53bf09"]
    assert parse_text("nothing to see") == []


def test_module_offset_without_module_line():
    items = parse_text("Module Offset: 0x00001000\n")
    assert items[0].module == "?" and items[0].off == 0x1000 and items[0].addr is None


def test_module_bases_are_metadata_not_code_addresses():
    text = """Reason: exception at gta_sa.exe+0x0013BF09
=== Registry-Resolved Module Info ===
Resolved Module Name: gta_sa.exe
Resolved Module Base: 0x00400000
Resolved Module Offset: 0x0013E4FA
Resolved IDA Address: 0x0053E4FA
Module Base: 0x00400000
Module: core.dll
Module Base = 0x10000000
Offset = 0x00001000
"""
    assert [(item.module, item.addr, item.off) for item in parse_text(text)] == [
        ("gta_sa.exe", 0x53BF09, 0x13BF09), ("gta_sa.exe", 0x53E4FA, 0x13E4FA), ("core.dll", None, 0x1000),
    ]
