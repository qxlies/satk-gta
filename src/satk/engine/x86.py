"""Minimal 32-bit x86 length and operand decoder (stdlib only) for the sa-engine site tools.

It is not a disassembler. For one instruction it answers: how many bytes, which opcode, where the
ModRM/displacement/immediate fields are, and whether a memory operand is a plain absolute
address (``ds:[addr]``). That is everything the SecuROM key search and ``engine sites-scan`` need.

The tables cover the one-byte map, the common two-byte (``0F``) map including x87, MMX/SSE and
``Jcc rel32``. Unknown opcodes decode as 1 byte with ``valid = False`` (a linear sweep through
data or junk then resynchronises the way ``dumpbin /disasm`` does).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

__all__ = ["Insn", "decode", "sweep"]

# Prefix bytes: segment overrides, operand/address size, lock, rep.
_PREFIXES = frozenset({0x26, 0x2E, 0x36, 0x3E, 0x64, 0x65, 0x66, 0x67, 0xF0, 0xF2, 0xF3})

# One-byte opcodes: 'm' = ModRM, a number = immediate bytes after the opcode (-4 = imm32 or imm16 with
# the 0x66 prefix, -8 = moffs32/16 address), 'b' = ModRM + imm8, 'd' = ModRM + imm32/16.
_ONE: dict[int, str] = {}


def _fill() -> None:
    for op in range(0x00, 0x40):
        lo = op & 7
        if op == 0x0F:
            continue
        if lo < 4:
            _ONE[op] = "m"
        elif lo == 4:
            _ONE[op] = "i1"
        elif lo == 5:
            _ONE[op] = "iz"
        else:
            _ONE[op] = "-"  # push/pop seg, daa/das/aaa/aas, segment prefixes (handled before)
    for op in range(0x40, 0x60):
        _ONE[op] = "-"
    _ONE.update({0x60: "-", 0x61: "-", 0x62: "m", 0x63: "m", 0x68: "iz", 0x69: "md", 0x6A: "i1", 0x6B: "mb"})
    for op in range(0x6C, 0x70):
        _ONE[op] = "-"
    for op in range(0x70, 0x80):
        _ONE[op] = "i1"
    _ONE.update({0x80: "mb", 0x81: "md", 0x82: "mb", 0x83: "mb"})
    for op in range(0x84, 0x90):
        _ONE[op] = "m"
    for op in range(0x90, 0xA0):
        _ONE[op] = "-"
    _ONE[0x9A] = "far"
    _ONE.update({0xA0: "ma", 0xA1: "ma", 0xA2: "ma", 0xA3: "ma"})
    for op in range(0xA4, 0xA8):
        _ONE[op] = "-"
    _ONE.update({0xA8: "i1", 0xA9: "iz"})
    for op in range(0xAA, 0xB0):
        _ONE[op] = "-"
    for op in range(0xB0, 0xB8):
        _ONE[op] = "i1"
    for op in range(0xB8, 0xC0):
        _ONE[op] = "iz"
    _ONE.update({0xC0: "mb", 0xC1: "mb", 0xC2: "i2", 0xC3: "-", 0xC4: "m", 0xC5: "m", 0xC6: "mb", 0xC7: "md",
                 0xC8: "i3", 0xC9: "-", 0xCA: "i2", 0xCB: "-", 0xCC: "-", 0xCD: "i1", 0xCE: "-", 0xCF: "-"})
    for op in range(0xD0, 0xD4):
        _ONE[op] = "m"
    _ONE.update({0xD4: "i1", 0xD5: "i1", 0xD6: "-", 0xD7: "-"})
    for op in range(0xD8, 0xE0):
        _ONE[op] = "m"
    for op in range(0xE0, 0xE4):
        _ONE[op] = "i1"
    _ONE.update({0xE4: "i1", 0xE5: "i1", 0xE6: "i1", 0xE7: "i1", 0xE8: "iz", 0xE9: "iz", 0xEA: "far", 0xEB: "i1"})
    for op in range(0xEC, 0xF0):
        _ONE[op] = "-"
    _ONE.update({0xF1: "-", 0xF4: "-", 0xF5: "-", 0xF6: "grp3b", 0xF7: "grp3z"})
    for op in range(0xF8, 0xFE):
        _ONE[op] = "-"
    _ONE.update({0xFE: "m", 0xFF: "m"})


_fill()

# Two-byte opcodes (after 0F). Default for the not listed ones: invalid.
_TWO: dict[int, str] = {}


def _fill2() -> None:
    for op in (0x00, 0x01, 0x02, 0x03, 0x0D, 0x18, 0x19, 0x1A, 0x1B, 0x1C, 0x1D, 0x1E, 0x1F):
        _TWO[op] = "m"
    for op in (0x05, 0x06, 0x07, 0x08, 0x09, 0x0B, 0x0E, 0x30, 0x31, 0x32, 0x33, 0x34, 0x35, 0x77, 0xA0, 0xA1,
               0xA2, 0xA8, 0xA9, 0xAA):
        _TWO[op] = "-"
    for op in range(0x10, 0x18):
        _TWO[op] = "m"
    for op in range(0x20, 0x24):
        _TWO[op] = "m"
    for op in range(0x28, 0x30):
        _TWO[op] = "m"
    _TWO[0x38] = "m"
    _TWO[0x3A] = "mb"
    for op in range(0x40, 0x80):
        _TWO[op] = "m"
    for op in (0x70, 0x71, 0x72, 0x73):
        _TWO[op] = "mb"
    for op in range(0x78, 0x7C):
        _TWO[op] = "-"
    for op in range(0x80, 0x90):
        _TWO[op] = "iz"
    for op in range(0x90, 0xA0):
        _TWO[op] = "m"
    for op in (0xA3, 0xA5, 0xAB, 0xAD, 0xAE, 0xAF):
        _TWO[op] = "m"
    for op in (0xA4, 0xAC):
        _TWO[op] = "mb"
    for op in range(0xB0, 0xC0):
        _TWO[op] = "m"
    _TWO[0xB9] = "-"
    _TWO[0xBA] = "mb"
    for op in (0xC0, 0xC1, 0xC3, 0xC7):
        _TWO[op] = "m"
    for op in (0xC2, 0xC4, 0xC5, 0xC6):
        _TWO[op] = "mb"
    for op in range(0xC8, 0xD0):
        _TWO[op] = "-"
    for op in range(0xD0, 0x100):
        _TWO[op] = "m"
    _TWO[0xFF] = "-"


_fill2()


@dataclass(frozen=True, slots=True)
class Insn:
    """One decoded instruction.

    ``mem`` is ``"abs"`` for a plain ``ds:[disp32]`` operand (ModRM ``00/101`` or ``A0..A3``),
    ``"disp"`` for a displacement32 combined with a base or index register, ``"reg"`` for a
    register-only ModRM, ``"none"`` when the instruction has no ModRM. ``disp_off``/``imm_off`` are
    byte offsets from the instruction start (``-1`` when absent), ``disp_len``/``imm_len`` sizes.
    """

    length: int
    opcode: int  # one byte, or 0x0F00 | second byte
    valid: bool
    mem: str
    reg: int  # ModRM reg field, -1 without ModRM
    mod: int  # ModRM mod field, -1 without ModRM
    rm: int
    has_sib: bool
    sib_base: int
    sib_index: int
    disp_off: int
    disp_len: int
    imm_off: int
    imm_len: int
    prefixes: int  # number of prefix bytes

    @property
    def op(self) -> int:
        return self.opcode


_BAD = Insn(1, -1, False, "none", -1, -1, -1, False, -1, -1, -1, 0, -1, 0, 0)


def decode(buf: bytes | bytearray | memoryview, pos: int = 0) -> Insn:
    """Decode the instruction at ``buf[pos:]``; an unknown or truncated one gives ``_BAD`` (1 byte)."""
    n = len(buf)
    i = pos
    opsz16 = False
    while i < n and buf[i] in _PREFIXES and i - pos < 14:
        if buf[i] == 0x66:
            opsz16 = True
        i += 1
    npre = i - pos
    if i >= n:
        return _BAD
    op = buf[i]
    i += 1
    two = False
    if op == 0x0F:
        if i >= n:
            return _BAD
        op2 = buf[i]
        i += 1
        form = _TWO.get(op2)
        two = True
        full = 0x0F00 | op2
        if form is None:
            return _BAD
        if op2 == 0x38 or op2 == 0x3A:
            # three-byte maps: third opcode byte, then ModRM (+ imm8 for 0F 3A)
            if i >= n:
                return _BAD
            i += 1
    else:
        full = op
        form = _ONE.get(op)
        if form is None:
            return _BAD
    zsz = 2 if opsz16 else 4

    mem = "none"
    reg = mod = rm = -1
    has_sib = False
    sib_base = sib_index = -1
    disp_off = -1
    disp_len = 0
    imm_off = -1
    imm_len = 0

    needs_modrm = form in ("m", "mb", "md", "ma", "grp3b", "grp3z") or (form == "mb" and two)
    if form == "ma":  # A0..A3: moffs32 (no ModRM)
        asz = 2 if any(b == 0x67 for b in buf[pos:pos + npre]) else 4
        if i + asz > n:
            return _BAD
        disp_off, disp_len = i - pos, asz
        i += asz
        return Insn(i - pos, full, True, "abs", -1, -1, -1, False, -1, -1, disp_off, disp_len, -1, 0, npre)
    if needs_modrm:
        if i >= n:
            return _BAD
        b = buf[i]
        i += 1
        mod, reg, rm = b >> 6, (b >> 3) & 7, b & 7
        if mod == 3:
            mem = "reg"
        else:
            mem = "disp"
            if rm == 4:
                if i >= n:
                    return _BAD
                sib = buf[i]
                i += 1
                has_sib = True
                sib_base = sib & 7
                sib_index = (sib >> 3) & 7
                if mod == 0 and sib_base == 5:
                    disp_off, disp_len = i - pos, 4
                    i += 4
                    # absolute with SIB and no index is also a plain address; with an index it is a table
                    mem = "abs" if sib_index == 4 else "disp"
            if mod == 0 and rm == 5:
                disp_off, disp_len = i - pos, 4
                i += 4
                mem = "abs"
            elif mod == 1:
                disp_off, disp_len = i - pos, 1
                i += 1
            elif mod == 2:
                disp_off, disp_len = i - pos, 4
                i += 4
        if i > n:
            return _BAD

    imm = 0
    if form == "i1":
        imm = 1
    elif form == "iz":
        imm = zsz
    elif form == "i2":
        imm = 2
    elif form == "i3":
        imm = 3
    elif form == "far":
        imm = 6
    elif form == "mb":
        imm = 1
    elif form == "md":
        imm = zsz
    elif form == "grp3b":
        imm = 1 if reg in (0, 1) else 0
    elif form == "grp3z":
        imm = zsz if reg in (0, 1) else 0
    if imm:
        imm_off, imm_len = i - pos, imm
        i += imm
    if i > n:
        return _BAD
    return Insn(i - pos, full, True, mem, reg, mod, rm, has_sib, sib_base, sib_index, disp_off, disp_len,
                imm_off, imm_len, npre)


def is_stop(buf: bytes | bytearray | memoryview, pos: int, ins: Insn) -> bool:
    """True for the mnemonics that end the key search window: popfd, xchg, ret, jmp, call."""
    op = ins.opcode
    if op in (0x9D, 0xC3, 0xC2, 0xCB, 0xCA, 0xE8, 0xE9, 0xEB, 0xEA, 0x9A, 0x86, 0x87):
        return True
    if 0x91 <= op <= 0x97:  # xchg eax,reg (0x90 is nop)
        return True
    if op == 0xFF and ins.reg in (2, 3, 4, 5):
        return True
    return False


def abs_operand(buf: bytes | bytearray | memoryview, pos: int, ins: Insn) -> int | None:
    """The absolute ``ds:[addr]`` operand of the instruction at ``pos``, or ``None``."""
    if ins.mem != "abs" or ins.disp_len != 4:
        return None
    return struct.unpack_from("<I", buf, pos + ins.disp_off)[0]


def sweep(buf: bytes | bytearray | memoryview, base_va: int, start_va: int, end_va: int):
    """Linear sweep of ``buf`` (mapped at ``base_va``) from ``start_va`` to ``end_va``.

    Yields ``(va, offset, Insn)``. The caller keeps the instruction list when it needs look-around.
    """
    pos = start_va - base_va
    stop = min(end_va - base_va, len(buf))
    while pos < stop:
        ins = decode(buf, pos)
        yield base_va + pos, pos, ins
        pos += ins.length
