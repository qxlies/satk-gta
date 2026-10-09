"""satk.engine.x86: length and operand decoding of the instructions the site tools care about."""

from __future__ import annotations

import pytest

from satk.engine.x86 import abs_operand, decode, is_stop, sweep

CASES = [
    # bytes, length, mem kind, disp_off, imm_off
    ("68 8C 00 00 00", 5, "none", -1, 1),  # push imm32
    ("6A 40", 2, "none", -1, 1),  # push imm8
    ("8B 0D F8 48 B7 00", 6, "abs", 2, -1),  # mov ecx,[abs]
    ("A1 5C EF C3 00", 5, "abs", 1, -1),  # mov eax,[moffs]
    ("89 34 85 F8 48 B7 00", 7, "disp", 3, -1),  # mov [eax*4+disp32],esi
    ("89 98 48 8E C0 00", 6, "disp", 2, -1),  # mov [eax+disp32],ebx
    ("81 FE 58 EF C3 00", 6, "reg", -1, 2),  # cmp esi,imm32
    ("3D 8E EF C3 00", 5, "none", -1, 1),  # cmp eax,imm32
    ("C7 05 84 39 C0 00 48 3A C0 00", 10, "abs", 2, 6),  # mov [abs],imm32
    ("8D 81 48 3A C0 00", 6, "disp", 2, -1),  # lea eax,[ecx+disp32]
    ("D8 1D D8 8F 85 00", 6, "abs", 2, -1),  # fcomp dword ptr [abs]
    ("0F B6 4E FC", 4, "disp", 3, -1),  # movzx ecx,byte [esi-4]
    ("0F 84 10 00 00 00", 6, "none", -1, 2),  # je rel32
    ("66 83 F9 40", 4, "reg", -1, 3),  # cmp cx,40h
    ("66 B8 34 12", 4, "none", -1, 2),  # mov ax,imm16 (operand-size prefix)
    ("C7 44 24 58 40 00 00 00", 8, "disp", 3, 4),  # mov dword [esp+58h],40h
    ("E8 00 00 00 00", 5, "none", -1, 1),  # call rel32
    ("F7 C1 00 00 00 80", 6, "reg", -1, 2),  # test ecx,imm32 (group 3 with immediate)
    ("F7 D9", 2, "reg", -1, -1),  # neg ecx (group 3 without immediate)
    ("33 05 F0 10 40 00", 6, "abs", 2, -1),  # xor eax,[abs]
    ("FF 15 00 80 85 00", 6, "abs", 2, -1),  # call [abs]
    ("8B 04 25 00 10 40 00", 7, "abs", 3, -1),  # mov eax,[disp32] through SIB without base/index
]


@pytest.mark.parametrize("hexstr,length,mem,disp_off,imm_off", CASES)
def test_decode_lengths(hexstr, length, mem, disp_off, imm_off):
    raw = bytes.fromhex(hexstr.replace(" ", ""))
    ins = decode(raw + b"\xcc" * 8, 0)
    assert ins.valid and ins.length == length
    assert ins.mem == mem
    assert ins.disp_off == disp_off
    assert ins.imm_off == imm_off


def test_abs_operand_and_stops():
    raw = bytes.fromhex("8B0DF848B700" "C3" "9D" "E9" "00000000" "FF2500800000" "87D8" "90")
    out = []
    for va, pos, ins in sweep(raw, 0x1000, 0x1000, 0x1000 + len(raw)):
        out.append((va, ins.opcode, abs_operand(raw, pos, ins), is_stop(raw, pos, ins)))
    assert out[0] == (0x1000, 0x8B, 0xB748F8, False)
    assert out[1][3] is True  # ret
    assert out[2][3] is True  # popfd
    assert out[3][3] is True  # jmp rel32
    assert out[4][3] is True and out[4][2] == 0x8000  # jmp [abs]
    assert out[5][3] is True  # xchg
    assert out[6][3] is False  # nop is not xchg


def test_unknown_and_truncated_are_one_byte():
    assert decode(b"\x0f\x04", 0).length == 1 and not decode(b"\x0f\x04", 0).valid
    assert decode(b"\x8b", 0).length == 1  # truncated ModRM
    assert decode(b"\x81\xfe\x00", 0).length == 1  # truncated imm32
