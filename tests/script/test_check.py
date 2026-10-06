"""Static checks (synthetic programs, binary and text)."""

from __future__ import annotations

import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from satk.script.asm import assemble  # noqa: E402
from satk.script.check import check_program  # noqa: E402
from satk.script.disasm import decode_program  # noqa: E402

from script_synth import CLEO_TEXT, MAIN_TEXT, synth_db  # noqa: E402


@pytest.fixture(scope="module")
def db():
    return synth_db()


def _codes(text: str, db) -> dict[str, str]:
    r = assemble(text, db)
    return {f[3]: f[0] for f in check_program(r.program, r.lines)}


def _bin_codes(data: bytes, db, kind: str = "cleo") -> dict[str, str]:
    return {f[3]: f[0] for f in check_program(decode_program(data, kind, db))}


def test_clean_scripts_have_no_warnings(db):
    good = "{$CLEO}\n03A4: script_name 'OK'\n:L\n0001: wait 0\n0002: goto @L\n"
    assert _codes(good, db) == {}
    assert "UNDECODED" not in _codes(CLEO_TEXT, db)        # text input: hex blocks are the author's choice
    assert {c for c, s in _codes(MAIN_TEXT, db).items() if s != "info"} == {"LOOP_NO_WAIT"}  # mission 1 spins


def test_loops_without_wait(db):
    spin = "{$CLEO}\n03A4: script_name 'X'\n:L\n0256: is_player_playing $2\n0002: goto @L\n"
    assert _codes(spin, db) == {"LOOP_NO_WAIT": "warn"}
    key = "{$CLEO}\n03A4: script_name 'X'\n:L\n00D6: if 0\n0AB0: is_key_pressed 116\n004D: goto_if_false @L\n" \
          "0A93: terminate_this_custom_script\n"
    assert _codes(key, db) == {"LOOP_NO_WAIT": "warn"}
    counter = ("{$CLEO}\n03A4: script_name 'X'\n0006: set_lvar_int 0@ 0\n:L\n000A: add_val_to_int_lvar 0@ 1\n"
               "00D6: if 0\n0039: is_int_lvar_equal_to_number 0@ 10\n004D: goto_if_false @L\n"
               "0A93: terminate_this_custom_script\n")
    assert _codes(counter, db) == {}
    call = "{$CLEO}\n03A4: script_name 'X'\n:L\n0050: gosub @S\n0002: goto @L\n:S\n0001: wait 0\n0051: return\n"
    assert _codes(call, db) == {}


def test_missing_terminate_and_falling_into_data(db):
    assert _codes("{$CLEO}\n03A4: script_name 'X'\n0001: wait 0\n", db) == {"NO_TERMINATE": "warn"}
    assert _codes("{$CLEO}\n0001: wait 0\nhex 00 00 end\n", db) == {"FALLS_INTO_DATA": "warn"}


def test_if_counts_names_and_not(db):
    text = ("{$CLEO}\n03A4: script_name 'LONGNAME'\n:L\n0001: wait 0\n00D6: if 1\n0256: is_player_playing $2\n"
            "004D: goto_if_false @L\n8001: not wait 0\n0002: goto @L\n")
    got = _codes(text, db)
    assert got == {"SCRIPT_NAME": "warn", "IF_COUNT": "warn", "NOT_CONDITION": "info"}


def test_bad_jumps_in_binary(db):
    head = assemble("{$CLEO}\n03A4: script_name 'X'\n0001: wait 0\n", db).data
    goto = lambda v: b"\x02\x00\x01" + struct.pack("<i", v)  # noqa: E731
    assert _bin_codes(head + goto(-5000), db) == {"JUMP_OUTSIDE": "error"}
    assert _bin_codes(head + goto(-3), db)["JUMP_MISALIGNED"] == "error"
    assert _bin_codes(head + goto(1234), db) == {"JUMP_ABSOLUTE": "error"}
    assert _bin_codes(head + goto(0), db) == {"JUMP_ZERO": "warn"}


def test_undecoded_bytes_are_reported(db):
    data = assemble("{$CLEO}\n0001: wait 0\n0A93: terminate_this_custom_script\n", db).data + b"\xff\xee\xdd"
    assert _bin_codes(data, db) == {"UNDECODED": "warn"}
