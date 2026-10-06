"""SA SCM binary: parameter types, instruction decoding and encoding (stdlib only).

An instruction is a little-endian ``u16`` (bit 15 = NOT flag of a condition, bits 0-14 = command)
followed by its parameters. Every parameter starts with a type byte (SA):

==== ====================== =================================================================
type text form              payload
==== ====================== =================================================================
0x00 (implicit)             end of the variable part (``arguments``)
0x01 ``5:i32``/``@label``   int32
0x02 ``$12``                global number variable: u16 byte offset (``$N`` = offset/4, ``&N`` = raw)
0x03 ``0@``                 local number variable: u16 index
0x04 ``5``                  int8
0x05 ``300``                int16
0x06 ``1.5``                float32
0x07 ``$12(0@,10i)``        global number array: u16 base, u16 index var, u8 size, u8 type|0x80 if global index
0x08 ``0@(1@,10f)``         local number array (same payload)
0x09 ``'TEXT'``             8-byte string
0x0A ``s$12`` / 0x0B ``0@s`` global / local 8-byte string variable
0x0C ``s$12(0@,4s)`` / 0x0D local 8-byte string arrays
0x0E ``"text"``             length-prefixed string (u8 length)
0x0F ``v'TEXT'``            16-byte string
0x10 ``v$12`` / 0x11 ``0@v`` global / local 16-byte string variable
0x12 ``v$12(0@,4v)`` / 0x13 local 16-byte string arrays
==== ====================== =================================================================

A byte above 0x13 where a string is expected starts an untyped 8-byte string (``r'TEXT'``): the
engine reads it whole (``CRunningScript::ReadTextLabelFromScript`` default branch).

Integers are written in the smallest type that holds them; any other width is kept explicitly
(``5:i32``) so the text assembles back to the same bytes.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field
from decimal import Decimal

from .opdb import Command, OpcodeDB

__all__ = ["Arg", "Instr", "DecodeError", "decode_at", "encode_instr", "encode_arg", "min_int_type",
           "fmt_float", "esc_bytes", "INT_TYPES", "STRING_LITERALS", "ARRAYS", "VARS", "RAW8", "RAW128"]

T_END, T_INT32, T_GVAR, T_LVAR, T_INT8, T_INT16, T_FLOAT = 0, 1, 2, 3, 4, 5, 6
T_GARR, T_LARR, T_STR8, T_GVAR_S, T_LVAR_S, T_GARR_S, T_LARR_S = 7, 8, 9, 10, 11, 12, 13
T_STRV, T_STR16, T_GVAR_V, T_LVAR_V, T_GARR_V, T_LARR_V = 14, 15, 16, 17, 18, 19
#: Pseudo types of untyped strings (no type byte): 8 bytes, or 128 for ``string128`` (05B6).
RAW8 = -1
RAW128 = -2

INT_TYPES = {T_INT8: 1, T_INT16: 2, T_INT32: 4}
VARS = {T_GVAR, T_LVAR, T_GVAR_S, T_LVAR_S, T_GVAR_V, T_LVAR_V}
ARRAYS = {T_GARR, T_LARR, T_GARR_S, T_LARR_S, T_GARR_V, T_LARR_V}
STRING_LITERALS = {T_STR8: 8, T_STR16: 16, RAW8: 8, RAW128: 128}
_I8, _I16, _I32, _U16, _F = struct.Struct("<b"), struct.Struct("<h"), struct.Struct("<i"), struct.Struct("<H"), \
    struct.Struct("<f")


class DecodeError(ValueError):
    """Bytes at an offset do not decode as an instruction."""

    def __init__(self, off: int, msg: str):
        super().__init__(f"0x{off:X}: {msg}")
        self.off = off
        self.msg = msg


@dataclass(slots=True)
class Arg:
    """One parameter. ``v``: int (ints, vars), float bits (``T_FLOAT``), bytes (strings) or
    ``(base, index, size, flags)`` (arrays). ``label`` is set by the disassembler/assembler."""

    t: int
    v: object
    label: str | None = None

    def is_int(self) -> bool:
        return self.t in INT_TYPES


@dataclass(slots=True)
class Instr:
    off: int
    opw: int                       # raw u16 including the NOT bit
    cmd: Command | None
    args: list[Arg]
    size: int
    line: int = 0                  # source line (assembler)
    extra: dict = field(default_factory=dict)

    @property
    def op(self) -> int:
        return self.opw & 0x7FFF

    @property
    def negated(self) -> bool:
        return bool(self.opw & 0x8000)


# --------------------------------------------------------------------------- decoding


def _read_arg(buf: bytes, q: int, end: int, kind: str, ptype: str = "") -> tuple[Arg, int]:
    if q >= end:
        raise DecodeError(q, "parameter runs past the end")
    t = buf[q]
    if t == T_INT8:
        if q + 2 > end:
            raise DecodeError(q, "truncated int8")
        return Arg(t, _I8.unpack_from(buf, q + 1)[0]), q + 2
    if t == T_INT16:
        if q + 3 > end:
            raise DecodeError(q, "truncated int16")
        return Arg(t, _I16.unpack_from(buf, q + 1)[0]), q + 3
    if t in (T_INT32, T_FLOAT):
        if q + 5 > end:
            raise DecodeError(q, "truncated 4-byte value")
        if t == T_INT32:
            return Arg(t, _I32.unpack_from(buf, q + 1)[0]), q + 5
        return Arg(t, int.from_bytes(buf[q + 1:q + 5], "little")), q + 5
    if t in VARS:
        if q + 3 > end:
            raise DecodeError(q, "truncated variable")
        return Arg(t, _U16.unpack_from(buf, q + 1)[0]), q + 3
    if t in ARRAYS:
        if q + 7 > end:
            raise DecodeError(q, "truncated array")
        base, idx = struct.unpack_from("<HH", buf, q + 1)
        return Arg(t, (base, idx, buf[q + 5], buf[q + 6])), q + 7
    if t == T_STR8 or t == T_STR16:
        n = 8 if t == T_STR8 else 16
        if q + 1 + n > end:
            raise DecodeError(q, "truncated string")
        return Arg(t, bytes(buf[q + 1:q + 1 + n])), q + 1 + n
    if t == T_STRV:
        if q + 2 > end:
            raise DecodeError(q, "truncated string length")
        n = buf[q + 1]
        if q + 2 + n > end:
            raise DecodeError(q, "string runs past the end")
        return Arg(t, bytes(buf[q + 2:q + 2 + n])), q + 2 + n
    if t > T_LARR_V and kind == "string":
        n, rt = (128, RAW128) if ptype == "string128" else (8, RAW8)
        if q + n > end:
            raise DecodeError(q, "truncated untyped string")
        return Arg(rt, bytes(buf[q:q + n])), q + n
    if t == T_END:
        raise DecodeError(q, "end-of-arguments byte where a parameter is expected")
    raise DecodeError(q, f"unknown parameter type 0x{t:02X}")


def decode_at(buf: bytes, p: int, end: int, db: OpcodeDB) -> Instr:
    """Decode the instruction at ``p`` (must end at or before ``end``); ``DecodeError`` otherwise."""
    if p + 2 > end:
        raise DecodeError(p, "truncated opcode")
    w = buf[p] | (buf[p + 1] << 8)
    cmd = db.get(w)
    if cmd is None:
        raise DecodeError(p, f"unknown opcode {w & 0x7FFF:04X}")
    q = p + 2
    args: list[Arg] = []
    params = cmd.params
    nfix = cmd.nfixed
    for i in range(nfix):
        a, q = _read_arg(buf, q, end, params[i].kind, params[i].type)
        args.append(a)
    if cmd.variadic:
        while True:
            if q >= end:
                raise DecodeError(q, "arguments are not terminated")
            if buf[q] == T_END:
                q += 1
                break
            a, q = _read_arg(buf, q, end, "any")
            args.append(a)
    return Instr(p, w, cmd, args, q - p)


# --------------------------------------------------------------------------- encoding


def min_int_type(v: int) -> int:
    if -128 <= v <= 127:
        return T_INT8
    if -32768 <= v <= 32767:
        return T_INT16
    return T_INT32


def encode_arg(a: Arg, out: bytearray) -> None:
    t, v = a.t, a.v
    if t == T_INT8:
        out.append(T_INT8)
        out += _I8.pack(v)
    elif t == T_INT16:
        out.append(T_INT16)
        out += _I16.pack(v)
    elif t == T_INT32:
        out.append(T_INT32)
        out += _I32.pack(v)
    elif t == T_FLOAT:
        out.append(T_FLOAT)
        out += int(v).to_bytes(4, "little")
    elif t in VARS:
        out.append(t)
        out += _U16.pack(v)
    elif t in ARRAYS:
        base, idx, size, flags = v
        out.append(t)
        out += struct.pack("<HHBB", base, idx, size, flags)
    elif t == T_STR8 or t == T_STR16:
        n = 8 if t == T_STR8 else 16
        out.append(t)
        out += bytes(v).ljust(n, b"\0")
    elif t == T_STRV:
        out.append(t)
        out.append(len(v))
        out += bytes(v)
    elif t == RAW8 or t == RAW128:
        out += bytes(v).ljust(8 if t == RAW8 else 128, b"\0")
    else:  # pragma: no cover - the parser never builds other types
        raise ValueError(f"cannot encode parameter type {t}")


def encode_instr(ins: Instr) -> bytes:
    out = bytearray(ins.opw.to_bytes(2, "little"))
    cmd = ins.cmd
    for a in ins.args:
        encode_arg(a, out)
    if cmd is not None and cmd.variadic:
        out.append(T_END)
    return bytes(out)


# --------------------------------------------------------------------------- text helpers


def fmt_float(bits: int) -> str:
    """Shortest decimal text that reads back to the same float32 bits (``0x7FC00000:f`` for NaN/inf)."""
    raw = int(bits).to_bytes(4, "little")
    f = _F.unpack(raw)[0]
    if not math.isfinite(f):
        return f"0x{bits:08X}:f"
    s = ""
    for prec in range(1, 10):
        s = f"{f:.{prec}g}"
        try:
            if _F.pack(float(s)) == raw:
                break
        except OverflowError:       # rounding up past FLT_MAX
            continue
    d = Decimal(s)
    exp = d.adjusted()
    if -7 <= exp <= 15:
        s = format(d, "f")
        if "." not in s:
            s += ".0"
    elif "." not in s.split("e")[0]:
        mant, _, e = s.partition("e")
        s = f"{mant}.0e{e}"
    return s


def esc_bytes(b: bytes, quote: str) -> str:
    """Bytes -> quoted text: printable ASCII as is, ``\\\\``, the quote and others as ``\\xHH``."""
    out = []
    for ch in b:
        c = chr(ch)
        if c == "\\" or c == quote:
            out.append("\\" + c)
        elif 0x20 <= ch < 0x7F:
            out.append(c)
        else:
            out.append(f"\\x{ch:02X}")
    return quote + "".join(out) + quote
