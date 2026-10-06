"""Assembler: syntax, validation against the opcode db, line-numbered errors (synthetic db)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from satk.core.errors import SatkError  # noqa: E402
from satk.script.asm import assemble, unescape  # noqa: E402
from satk.script.opdb import load_db  # noqa: E402

from script_synth import synth_db  # noqa: E402


@pytest.fixture(scope="module")
def db():
    return synth_db()


def _err(text: str, db) -> SatkError:
    with pytest.raises(SatkError) as e:
        assemble(text, db, source="t.txt")
    assert e.value.code == "BAD_PARAMS"
    return e.value


def test_names_and_opcodes_assemble_the_same(db):
    a = assemble("{$CLEO .cs}\nwait 0\nnot is_int_lvar_equal_to_number 0@ 1\n", db).data
    b = assemble("{$CLEO .cs}\n0001: wait 0\n8039: is_int_lvar_equal_to_number 0@ 1\n", db).data
    c = assemble("{$CLEO .cs}\n0001: WAIT 0\n0039: not is_int_lvar_equal_to_number 0@ 1\n", db).data
    assert a == b == c == bytes.fromhex("0100" "0400" "3980" "030000" "0401")


def test_comments_and_sanny_hints(db):
    text = ("{$CLEO .cs} // kind\n/* block\n comment */ 0001: wait {time} 5 // five\n"
            "{ a brace\n comment } 0001: wait 1\n0001: wait 2 /* inline */\n")
    assert assemble(text, db).data == bytes.fromhex("0100" "0405" "0100" "0401" "0100" "0402")


def test_errors_carry_line_numbers_and_suggestions(db):
    e = _err("{$CLEO}\n0001: wait 0\nwiat 0\n", db)
    assert e.data["errors"][0].startswith("line 3: unknown command 'wiat'") and "wait" in e.did_you_mean
    e = _err("{$CLEO}\n\n0001: wait\n", db)
    assert "line 3: wait takes 1 parameters, got 0" in e.data["errors"][0] and "0001 WAIT(time: int)" in e.msg
    e = _err("{$CLEO}\n0007: set_lvar_float 0@ 5\n", db)
    assert "is a float: write it with a decimal point" in e.data["errors"][0]
    e = _err("{$CLEO}\n0001: wait 1.5\n", db)
    assert "is an integer, got a float" in e.data["errors"][0]
    e = _err("{$CLEO}\n00A0: get_char_coordinates $3 1@ 2@ 7\n", db)
    assert "must be a variable" in e.data["errors"][0]
    e = _err("{$CLEO}\n03A4: script_name 5\n", db)
    assert "is a string" in e.data["errors"][0]
    e = _err("{$CLEO}\n0001: script_name 'X'\n", db)
    assert "opcode 0001 is wait, not 'script_name'" in e.data["errors"][0] and "03A4: script_name" in e.did_you_mean
    e = _err("{$CLEO}\n0FFF: wait 0\n", db)
    assert "unknown opcode 0FFF" in e.data["errors"][0]
    e = _err("{$CLEO}\n0002: goto @NOPE\n:NOPE2\n", db)
    assert "no label :NOPE" in e.data["errors"][0] and "NOPE2" in e.did_you_mean
    e = _err("{$CLEO}\n:A\n:A\n0001: wait 0\n", db)
    assert "defined twice" in e.data["errors"][0]
    e = _err("{$CLEO}\nhex 00 01\n", db)
    assert "hex block without 'end'" in e.data["errors"][0]
    e = _err("{$CLEO}\n0111: test_any 'TOO LONG TEXT' 1 2 3\n", db)
    assert "does not fit 8" in e.data["errors"][0]
    e = _err("{$CLEO}\n0111: test_any r'X' 1 2 3\n", db)
    assert "fits only a string parameter" in e.data["errors"][0]
    e = _err("{$CLEO}\n0001: wait 200:i8\n", db)
    assert "does not fit int8" in e.data["errors"][0]
    e = _err("{$CLEO}\n0001: wait $PLAYER\n", db)
    assert "named variable" in e.data["errors"][0]
    e = _err("{$CLEO}\nDEFINE OBJECTS 1\n", db)
    assert "only valid in a {$MAIN} file" in e.data["errors"][0]


def test_many_errors_are_counted(db):
    e = _err("{$CLEO}\n" + "wiat 0\n" * 30, db)
    assert e.data["total"] == 30 and len(e.data["errors"]) == 20


def test_kind_default_and_jump_zero_warnings(db):
    r = assemble(":START\n0001: wait 0\n0002: goto @START\n", db)
    assert r.kind == "cleo" and r.ext == ".cs"
    assert any(w.startswith("KIND:") for w in r.warnings) and any(w.startswith("JUMP_ZERO:") for w in r.warnings)
    assert r.data.endswith(b"\x02\x00\x01\x00\x00\x00\x00")


def test_cleo_extension_and_external(db):
    assert assemble("{$CLEO .cm}\n0001: wait 0\n", db).ext == ".cm"
    r = assemble("{$EXTERNAL}\n:L\n0001: wait 0\n0002: goto @L\n", db)
    assert r.kind == "external" and r.ext == ".scm"


def test_unescape():
    assert unescape(r"a\x00\'\"\\\n") == b"a\x00'\"\\\n"
    with pytest.raises(ValueError):
        unescape("Ж")


def test_core_db_assembles_basic_cleo():
    db = load_db("core")
    r = assemble("{$CLEO .cs}\n03A4: script_name 'X'\n:L\nwait 0\nprint_help_string \"hi\"\n0002: goto @L\n", db)
    assert r.data.startswith(b"\xa4\x03\x09X")
    assert db.find("terminate_this_custom_script").op == 0x0A93
