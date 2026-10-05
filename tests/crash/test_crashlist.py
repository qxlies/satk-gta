"""The vendored CrashInfo list (M3 C1): provenance, parser, matching, lookup."""

from __future__ import annotations

import hashlib

import pytest

from satk.core import resources
from satk.crash import crashlist as C

SYNTH = """05/10/26 (DD/MM/YY)

Error: 0x00401000
Problem: First problem.
Solution: First solution
that continues on a second line.

Error: 0x00402000, 0x00402010 (in the "Backtrace" at the end of the log you will have 0x00403000)
About: Common text.
Problem 1: If you have a "0x00404000" in the second line of Backtrace, it was mod A.
Solution 1: Remove mod A.
Problem 2: Otherwise it is mod B.
Solution2: Remove mod B.

Error: 0x*
Backtrace: 0x00405000
Problem: Only the backtrace tells.

Error: (in the "Backtrace" at the end of the log there will be "0x00406000" and "Some.asi")
Problem: Backtrace only.
0x00407000 in "Backtrace": a note about another address.


SCRLOG


By commands:

Last command: [0001] WAIT
Problem: Not informative.

Last Command: (Some Bitwise): [0B10] or [ 0B11] | (Some .ini): [0AF0 ]
Problem: Incomplete CLEO.

By scripts:

script nebo
Mod: Skybox
About: Bad script.

Others

Error: std.stream.dll or STDSTR ~ 1.DLL
About: Missing model.

Other problems:

Error: Freeze (usually flying)
Solution: Run as administrator.
"""


def test_vendored_file_matches_its_provenance():
    src = C.source_info()
    raw = resources.read_bytes("crashlist", src["file"])
    assert hashlib.sha256(raw).hexdigest() == src["sha256"]
    assert src["license"] == "MIT" and src["revision"] and src["list_date"] == "2026-10-02"
    notice = resources.read_text("notices", "crashinfo.txt")
    assert "MIT License" in notice and src["revision"] in notice and src["sha256"] in notice
    assert "Permission is hereby granted, free of charge" in notice


def test_real_list_parses_completely():
    entries, meta = C.load()
    s = meta["sections"]
    assert meta["date"] == "2026-10-02"
    assert s["offsets"] + s["modules"] + s["other"] == C.source_info()["entries_error"] == 335
    assert s["commands"] == 11 and s["scripts"] == 12
    assert [e.n for e in entries] == list(range(1, len(entries) + 1))
    e = next(e for e in entries if 0x00456809 in e.addrs)
    assert e.title().startswith("Creation of a pickup using a model") and "EAX" in e.solution()
    # every Error entry yields something to match on, or is a plain description in "Other problems"
    assert all(e.addrs or e.backtrace or e.modules or e.section == "other" for e in entries
               if e.section in ("offsets", "modules", "other"))
    assert next(e for e in entries if e.script == "nebo").texts("mod") == ["Skybox"]
    assert next(e for e in entries if e.commands == ("038B",)).title() == "Loading a model"


def test_parser_edge_cases():
    entries, meta = C.parse(SYNTH)
    assert meta["date"] == "2026-10-05"
    by_head = {e.head: e for e in entries}
    e1 = by_head["0x00401000"]
    assert e1.solution() == "First solution that continues on a second line."
    e2 = by_head["0x00402000, 0x00402010"]
    assert e2.addrs == (0x402000, 0x402010) and e2.backtrace == (0x403000,)
    assert e2.variants() == [1, 2] and e2.solution(2) == "Remove mod B." and e2.title() == "Common text."
    assert e2.variant_for({0x404000}) == 1 and e2.variant_for({0x999999}) is None
    assert by_head["backtrace 0x00405000"].addrs == () and by_head["backtrace 0x00405000"].section == "offsets"
    e4 = by_head["backtrace 0x00406000, 0x00407000"]
    assert e4.backtrace == (0x406000, 0x407000) and e4.modules == ()
    cmds = [e for e in entries if e.section == "commands"]
    assert [e.commands for e in cmds] == [("0001",), ("0B10", "0B11", "0AF0")]
    scr = next(e for e in entries if e.section == "scripts")
    assert scr.script == "nebo" and scr.title() == "Skybox"
    mod = next(e for e in entries if e.section == "modules")
    assert mod.modules == ("std.stream.dll", "stdstr~1.dll")
    other = next(e for e in entries if e.section == "other")
    assert other.head == "Freeze (usually flying)" and other.solution() == "Run as administrator."


def test_match_ranks_ip_stack_module_command_script():
    m = C.match(ip=0x00456809)
    assert m[0].entry.addrs[0] == 0x00456809 and m[0].how == "ip" and m[0].score == 100
    # 0x005334F0 has variants keyed by the second backtrace line
    e = next(x for x in C.load()[0] if 0x005334F0 in x.addrs)
    v = e.variant_for({0x005279B6})
    assert v is not None and "car camera" in e.title(v)
    mv = C.match(ip=0x005334F0, stack=[0x005279B6])
    assert mv[0].entry is e and mv[0].variant == v
    # backtrace-only entries match through the stack
    ms = C.match(ip=0x12345678, stack=[0x0040A000, 0x006E2BB7])
    assert ms and ms[0].how == "stack" and ms[0].entry.head == "backtrace 0x006E2BB7"
    assert C.match(module="CLEO.asi")[0].entry.head == "CLEO.asi"
    assert C.match(module="gta_sa.exe") == []
    assert C.match(commands=["038B"])[0].entry.commands == ("038B",)
    assert C.match(scripts=["NEBO"])[0].entry.script == "nebo"
    assert C.match(ip=0x00401008) == []                       # an address that is not in the list


@pytest.mark.parametrize("query, kind, head", [
    ("0x00456809", "auto", "0x00456809"), ("00456809", "auto", "0x00456809"),
    ("gta_sa.exe+0x56809", "auto", "0x00456809"), ("[038B]", "auto", "[038B]"), ("038b", "auto", "[038B]"),
    ("script:nebo", "auto", "script nebo"), ("CLEO.asi", "auto", "CLEO.asi"), ("std.stream.dll", "auto", None),
    ("nebo", "script", "script nebo"),
])
def test_lookup(query, kind, head):
    hits = C.lookup(query, kind=kind)
    assert hits, query
    if head:
        assert hits[0][0].head == head


def test_lookup_words_and_misses():
    hits = C.lookup("pickup model")
    assert any(e.addrs and e.addrs[0] == 0x00456809 for e, _how in hits) and {h for _e, h in hits} == {"text"}
    assert C.lookup("0x00401008") == [] and C.lookup("zzzz qqqq") == []
    assert len(C.lookup(None)) == len(C.load()[0])
