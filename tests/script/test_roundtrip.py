"""Disassembler <-> assembler round trips on synthetic scripts (no game files)."""

from __future__ import annotations

import struct
import sys
from pathlib import Path

import pytest

from satk.core.errors import SatkError  # noqa: E402
from satk.script.asm import assemble
from satk.script.disasm import decode_program, disassemble, parse_main_header, sb_footer
from satk.script.scm import fmt_float

sys.path.insert(0, str(Path(__file__).resolve().parent))

from script_synth import CLEO_TEXT, MAIN_TEXT, synth_db  # noqa: E402


@pytest.fixture(scope="module")
def db():
    return synth_db()


def _round(text: str, kind: str, db) -> tuple[bytes, str]:
    data = assemble(text, db).data
    d = disassemble(data, kind, db)
    again = assemble(d.text, db).data
    assert again == data, "disassembled text does not assemble to the same bytes"
    assert disassemble(again, kind, db).text == d.text, "the text form is not a fixpoint"
    return data, d.text


def test_cleo_every_parameter_form_round_trips(db):
    data, text = _round(CLEO_TEXT, "cleo", db)
    assert text.splitlines()[1] == "{$CLEO .cs}"
    for want in ("0111: test_any 5:i32 5:i16 300:i32 -1:i16", "0111: test_any 0x7FC00000:f 1.0e-08 $12 &13",
                 "$40(&41,8t9) 'ABC' 'A\\x00B\\x01'", "05B6: save_string_to_debug_file r'DEBUG\\x00\\x12garbage'",
                 "8039: not is_int_lvar_equal_to_number 0@ 3", "0AB1: cleo_call @SYNTH_541 2 0@ 1.5 5@",
                 "  90 90 C3 00*12", "0111: test_any 1.5 -0.0 0.1 3.4028235e+38"):
        assert want in text, want
    # variadic commands end with 0x00; labels are negative offsets from the start
    assert data.endswith(bytes.fromhex("b20a" "0401" "030000" "00"))


def test_main_scm_headers_missions_and_objects(db):
    data, text = _round(MAIN_TEXT, "main", db)
    h = parse_main_header(data)
    assert h.globals_size == 64 and h.global_data[8:12] == b"\x01\x02\x03\x04"
    assert [o.rstrip(b"\0") for o in h.objects] == [b"", b"MYOBJ", b"odd\x00\x07"]
    assert len(h.missions) == 3 and h.missions[0] == h.missions[2] and h.exclusive == 1 and h.mission_locals == 40
    assert h.externals == [(b"EXT1".ljust(20, b"\0"), 5000, 100)] and h.seg6_threads == 7
    assert h.main_size == h.missions[0] and h.largest_mission == max(h.missions[1] - h.missions[0], len(data) - h.missions[1])
    assert "0213: create_pickup #MYOBJ 3 1.0 2.0 3.0 $5" in text
    assert "0002: goto @MAIN_294" in text            # a mission jumps into the main code (absolute)
    prog = decode_program(data, "main", db)
    assert [s.kind for s in prog.sections] == ["main", "mission", "mission"]
    mis = prog.sections[2]
    goto = mis.items[-1]
    assert goto.args[0].v == -2                       # relative to the mission start


def test_main_header_overrides_are_kept(db):
    data = bytearray(assemble(MAIN_TEXT, db).data)
    # a file whose stored sizes disagree with the layout still round-trips (DEFINE MAIN_SIZE / LARGEST_MISSION_SIZE)
    h = parse_main_header(bytes(data))
    off = 8 + h.globals_size + 12 + 24 * len(h.objects) + 8
    struct.pack_into("<II", data, off, 12345, 777)
    d = disassemble(bytes(data), "main", db)
    assert "DEFINE MAIN_SIZE 12345" in d.text and "DEFINE LARGEST_MISSION_SIZE 777" in d.text
    assert assemble(d.text, db).data == bytes(data)


def test_not_a_main_scm(db):
    with pytest.raises(SatkError) as e:
        disassemble(b"\x01\x00\x04\x00", "main", db)
    assert e.value.code == "UNSUPPORTED"


def test_code_after_a_jump_over_data_and_sanny_footer(db):
    blob = bytes(range(0x90, 0xA0)) * 3
    code = assemble("{$CLEO}\n0001: wait 0\n0A93: terminate_this_custom_script\n", db).data
    # goto over a machine-code blob (like CLEO scripts that carry x86 code), then a Sanny Builder footer
    goto = b"\x02\x00\x01" + struct.pack("<i", -(7 + len(blob)))
    body = goto + blob + code
    footer = b"VAR\x00names" + struct.pack("<I", len(body)) + b"__SBFTR\x00"
    data = body + footer
    assert sb_footer(data) == len(body)
    d = disassemble(data, "cleo", db, name="my mod")
    assert assemble(d.text, db).data == data
    prog = d.program
    assert [it.size for s in prog.sections for it in s.items if not hasattr(it, "opw")] == [len(blob), len(footer)]
    assert any("Sanny Builder footer" in m for _o, m in prog.issues)
    assert ":MY_MOD_" in d.text                          # labels named after the file when there is no 03A4


def test_zero_padding_is_one_hex_run(db):
    code = assemble("{$EXTERNAL}\n0001: wait 0\n004E: terminate_this_script\n", db).data
    data = code + bytes(2048 - len(code))
    d = disassemble(data, "external", db)
    assert f"  00*{2048 - len(code)}" in d.text
    assert assemble(d.text, db).data == data


def test_float_text_is_shortest_and_exact():
    for bits in (0x00000000, 0x80000000, 0x3F800000, 0x3DCCCCCD, 0x7F7FFFFF, 0x00000001, 0x447A0000, 0xC2C80000,
                 0x3EAAAAAB, 0x4B189680, 0x7FC00000, 0xFF800000):
        s = fmt_float(bits)
        if s.endswith(":f"):
            assert int(s[:-2], 16) == bits
            continue
        assert ("." in s) and struct.unpack("<I", struct.pack("<f", float(s)))[0] == bits, (hex(bits), s)
    assert fmt_float(0x3DCCCCCD) == "0.1" and fmt_float(0x447A0000) == "1000.0"


def test_annotate_hints_are_comments(db):
    data = assemble(CLEO_TEXT, db).data
    d = disassemble(data, "cleo", db, annotate=True)
    assert "0001: wait {time} 0" in d.text
    assert assemble(d.text, db).data == data


def test_extension_preference(db):
    data = assemble("{$CLEO}\n0B20: read_clipboard_data 0 4\n", db).data
    assert "read_clipboard_data" in disassemble(data, "cleo", db).text
    samp = db.preferring(("SAMPFUNCS",))
    text = "{$CLEO}\n{$USE SAMPFUNCS}\n0B20: samp_get_player_char_by_id 1 0@\n"
    data2 = assemble(text, db).data
    out = disassemble(data2, "cleo", samp).text
    assert "{$USE SAMPFUNCS}" in out and "samp_get_player_char_by_id 1 0@" in out
    assert assemble(out, db).data == data2
