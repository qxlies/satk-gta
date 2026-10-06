"""Assembler: satk script text -> SCM bytes (stdlib only).

Two passes: the parser turns every line into an instruction, a label, a ``hex`` block or a
directive (with all errors collected and numbered by line), then the layout assigns offsets and the
encoder resolves ``@labels`` (absolute in ``main.scm``'s main code, negative offsets from the section
start in missions, CLEO and streamed scripts). The text form is the one :mod:`.disasm` writes:

* ``{$CLEO .cs}`` / ``{$EXTERNAL}`` / ``{$MAIN}`` - file kind (default ``{$CLEO .cs}``, with a warning);
  ``{$MISSION}`` starts a mission in ``{$MAIN}``; ``{$USE SAMPFUNCS}`` prefers an extension's opcodes;
* ``0001: wait 0`` or ``wait 0`` (name lookup in the opcode db), ``8019: not ...`` / ``not ...``;
* ``:LABEL`` and ``@LABEL``; ``hex 00 01 FF*16 end``; ``DEFINE ...`` header lines of ``main.scm``;
* comments ``// ...``, ``/* ... */`` and ``{ ... }`` (so Sanny Builder's ``{name}`` hints are fine).
"""

from __future__ import annotations

import difflib
import re
import struct
from dataclasses import dataclass, field

from ..core.errors import SatkError
from .disasm import Blob, MainHeader, Program, Section
from .opdb import Command, OpcodeDB
from .scm import (ARRAYS, INT_TYPES, RAW8, RAW128, T_FLOAT, T_GARR, T_GARR_S, T_GARR_V, T_GVAR, T_GVAR_S,
                  T_GVAR_V, T_INT8, T_INT16, T_INT32, T_LARR, T_LARR_S, T_LARR_V, T_LVAR, T_LVAR_S, T_LVAR_V,
                  T_STR8, T_STR16, T_STRV, VARS, Arg, Instr, encode_instr, min_int_type)

__all__ = ["AsmResult", "AsmError", "assemble", "parse_text", "unescape", "MAX_ERRORS"]

MAX_ERRORS = 200
_OPCODE = re.compile(r"^([0-9A-Fa-f]{4}):$")
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_LABEL = re.compile(r"^[A-Za-z0-9_]+$")
_INT = re.compile(r"^([-+]?)(0[xX][0-9A-Fa-f]+|\d+)(?::i(8|16|32))?$")
_FLOAT = re.compile(r"^[-+]?(?:\d+\.\d*|\.\d+|\d+(?=[eE]))(?:[eE][-+]?\d+)?$")
_FBITS = re.compile(r"^0[xX]([0-9A-Fa-f]{1,8}):f$")
_GV = re.compile(r"^([sv]?)([$&])(\d+)$")
_LV = re.compile(r"^(\d+)@([sv]?)$")
_ARR = re.compile(r"^([^()]+)\(\s*([^,()]+?)\s*,\s*(\d+)\s*([ifsv]|t\d+)\s*\)$")
_HEXTOK = re.compile(r"^([0-9A-Fa-f]{2})\*(\d+)$")
_INT_BITS = {"8": T_INT8, "16": T_INT16, "32": T_INT32}
_INT_RANGE = {T_INT8: (-128, 127), T_INT16: (-32768, 32767), T_INT32: (-2 ** 31, 2 ** 31 - 1)}
_KINDS = {"CLEO": "cleo", "EXTERNAL": "external", "MAIN": "main"}


@dataclass
class AsmError:
    line: int
    msg: str
    did_you_mean: list[str] = field(default_factory=list)

    def text(self) -> str:
        return f"line {self.line}: {self.msg}" if self.line else self.msg


@dataclass
class AsmResult:
    data: bytes
    program: Program
    kind: str
    ext: str
    warnings: list[str]
    lines: dict[int, int] = field(default_factory=dict)      # offset -> source line
    errors: list[AsmError] = field(default_factory=list)


# --------------------------------------------------------------------------- lexing


_SPECIAL = re.compile(r"['\"/{]")
_TOK_SPECIAL = re.compile(r"['\"({]")


def _strip_line(line: str, mode: str) -> tuple[str, str]:
    """One line without comments; ``mode`` is "" / "block" / "brace" (a comment open from the line before)."""
    buf: list[str] = []
    i, n = 0, len(line)
    while i < n:
        c = line[i]
        if mode == "block":
            j = line.find("*/", i)
            if j < 0:
                return "".join(buf), mode
            mode = ""
            i = j + 2
            continue
        if mode == "brace":
            j = line.find("}", i)
            if j < 0:
                return "".join(buf), mode
            mode = ""
            i = j + 1
            continue
        if c in "'\"":
            j = i + 1
            while j < n and line[j] != c:
                j += 2 if line[j] == "\\" else 1
            buf.append(line[i:j + 1])
            i = j + 1
            continue
        if c == "/" and line.startswith("//", i):
            break
        if c == "/" and line.startswith("/*", i):
            mode = "block"
            buf.append(" ")
            i += 2
            continue
        if c == "{":
            if line.startswith("{$", i):
                j = line.find("}", i)
                j = n - 1 if j < 0 else j
                buf.append(" " + line[i:j + 1] + " ")
                i = j + 1
                continue
            mode = "brace"
            buf.append(" ")
            i += 1
            continue
        k = i + 1
        m = _SPECIAL.search(line, k)
        k = m.start() if m else n
        buf.append(line[i:k])
        i = k
    return "".join(buf), mode


def _strip_comments(text: str) -> list[tuple[int, str]]:
    """``(line number, text)`` without comments, keeping string literals and ``{$...}`` directives.

    A comment that spans lines ends the line it starts on; the text after it belongs to its own line.
    """
    out: list[tuple[int, str]] = []
    mode = ""
    search = _SPECIAL.search
    for ln, line in enumerate(text.split("\n"), 1):
        if not mode and search(line) is None:
            out.append((ln, line))
            continue
        s, mode = _strip_line(line, mode)
        out.append((ln, s))
    return out


def _tokens(s: str) -> list[str]:
    if _TOK_SPECIAL.search(s) is None:
        return s.split()
    out: list[str] = []
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c.isspace():
            i += 1
            continue
        if s.startswith("{$", i):
            k = s.find("}", i)
            k = n - 1 if k < 0 else k
            out.append(s[i:k + 1])
            i = k + 1
            continue
        j = i
        if c in "vVrR" and i + 1 < n and s[i + 1] == "'":
            j = i + 1
        if s[j] in "'\"":
            q = s[j]
            k = j + 1
            while k < n and s[k] != q:
                k += 2 if s[k] == "\\" else 1
            out.append(s[i:k + 1])
            i = k + 1
            continue
        k = i
        depth = 0
        while k < n and (depth or not s[k].isspace()):
            if s[k] == "(":
                depth += 1
            elif s[k] == ")":
                depth = max(0, depth - 1)
            k += 1
        out.append(s[i:k])
        i = k
    return out


def unescape(body: str) -> bytes:
    """Text between quotes -> bytes (``\\\\ \\' \\" \\xHH \\n \\r \\t \\0``; other characters latin-1)."""
    out = bytearray()
    i, n = 0, len(body)
    while i < n:
        c = body[i]
        if c == "\\" and i + 1 < n:
            d = body[i + 1]
            if d in "\\'\"":
                out.append(ord(d))
                i += 2
            elif d in "xX" and i + 3 < n + 1 and re.fullmatch(r"[0-9A-Fa-f]{2}", body[i + 2:i + 4] or ""):
                out.append(int(body[i + 2:i + 4], 16))
                i += 4
            elif d in "nrt0":
                out.append({"n": 10, "r": 13, "t": 9, "0": 0}[d])
                i += 2
            else:
                raise ValueError(f"unknown escape \\{d}")
            continue
        o = ord(c)
        if o > 255:
            raise ValueError(f"character {c!r} is not in latin-1: write it as \\xHH")
        out.append(o)
        i += 1
    return bytes(out)


# --------------------------------------------------------------------------- parse


@dataclass
class _Ref:
    """A parameter resolved after parsing: ``@label`` or ``#model``."""

    kind: str          # label | model
    name: str
    tok: str


@dataclass
class _Ins:
    line: int
    opw: int
    cmd: Command
    args: list           # Arg | _Ref
    sec: int = 0
    off: int = 0
    size: int = 0


@dataclass
class _Parsed:
    kind: str | None = None
    ext: str = ".cs"
    use: tuple[str, ...] = ()
    items: list = field(default_factory=list)      # (sec, item) item: _Ins | Blob | ("label", name, line)
    nsec: int = 1
    defines: list[tuple[int, list[str]]] = field(default_factory=list)
    errors: list[AsmError] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    cache: dict = field(default_factory=dict)          # (token, param type) -> parsed parameter
    checks: dict = field(default_factory=dict)         # (id(command), index, type) -> (error, note)


def _var_off(sigil: str, num: str) -> int:
    v = int(num)
    return v * 4 if sigil == "$" else v


def _parse_var(tok: str) -> Arg | None:
    m = _GV.match(tok)
    if m:
        off = _var_off(m.group(2), m.group(3))
        if off > 0xFFFF:
            raise ValueError(f"global variable {tok} is beyond offset 65535")
        return Arg({"": T_GVAR, "s": T_GVAR_S, "v": T_GVAR_V}[m.group(1)], off)
    m = _LV.match(tok)
    if m:
        idx = int(m.group(1))
        if idx > 0xFFFF:
            raise ValueError(f"local variable {tok} is out of range")
        return Arg({"": T_LVAR, "s": T_LVAR_S, "v": T_LVAR_V}[m.group(2)], idx)
    return None


def _parse_param(tok: str, ptype: str) -> Arg | _Ref:
    """One parameter token -> Arg (or a label/model reference). ``ValueError`` with a message."""
    if tok.startswith("@"):
        if not _LABEL.match(tok[1:]):
            raise ValueError(f"bad label reference {tok!r}")
        return _Ref("label", tok[1:], tok)
    if tok.startswith("#"):
        if not _LABEL.match(tok[1:]):
            raise ValueError(f"bad model name {tok!r}")
        return _Ref("model", tok[1:], tok)
    if tok[0] in "tTfF":
        low = tok.lower()
        if low in ("true", "false"):
            return Arg(T_INT8, 1 if low == "true" else 0)
    m = _INT.match(tok)
    if m:
        num = m.group(2)
        v = (int(num[2:], 16) if num[1:2] in ("x", "X") else int(num, 10)) * (-1 if m.group(1) == "-" else 1)
        t = _INT_BITS[m.group(3)] if m.group(3) else min_int_type(v)
        lo, hi = _INT_RANGE[t]
        if not lo <= v <= hi:
            if t == T_INT32 and not m.group(3) and 0 <= v <= 0xFFFFFFFF:
                v -= 1 << 32           # 0xFFFFFFFF-style unsigned constants
            else:
                raise ValueError(f"{tok} does not fit int{ {T_INT8: 8, T_INT16: 16, T_INT32: 32}[t] }")
        return Arg(t, v)
    m = _FBITS.match(tok)
    if m:
        return Arg(T_FLOAT, int(m.group(1), 16))
    if _FLOAT.match(tok):
        f = float(tok)
        try:
            raw = struct.pack("<f", f)
        except OverflowError:
            raise ValueError(f"{tok} does not fit a 32-bit float") from None
        return Arg(T_FLOAT, int.from_bytes(raw, "little"))
    if tok[:1] in "'\"" or (tok[:1] in "vVrR" and tok[1:2] == "'"):
        pre = tok[0].lower() if tok[0] not in "'\"" else ""
        body = tok[len(pre):]
        if len(body) < 2 or body[-1] != body[0]:
            raise ValueError(f"unterminated string {tok}")
        b = unescape(body[1:-1])
        if body[0] == '"':
            if len(b) > 255:
                raise ValueError(f"string of {len(b)} bytes: a \"...\" string holds at most 255")
            return Arg(T_STRV, b)
        if pre == "v":
            t, n = T_STR16, 16
        elif pre == "r":
            t, n = (RAW128, 128) if ptype == "string128" else (RAW8, 8)
            first = b[0] if b else 0
            if first <= T_LARR_V:
                raise ValueError(f"an untyped string cannot start with byte 0x{first:02X} "
                                 "(the game would read a parameter type)")
        else:
            t, n = T_STR8, 8
        if len(b) > n:
            raise ValueError(f"string of {len(b)} bytes does not fit {n} ('...' = 8, v'...' = 16; "
                             "use \"...\" for longer text)")
        return Arg(t, b)
    var = _parse_var(tok)
    if var is not None:
        return var
    m = _ARR.match(tok)
    if m:
        base = _parse_var(m.group(1).strip())
        idx = _parse_var(m.group(2).strip())
        if base is None or idx is None or idx.t not in (T_GVAR, T_LVAR):
            raise ValueError(f"bad array {tok!r}: write $12(0@,10i) or 0@(1@,4f)")
        size = int(m.group(3))
        if size > 255:
            raise ValueError(f"array size {size} > 255")
        et = m.group(4)
        etn = "ifsv".index(et) if len(et) == 1 else int(et[1:])
        if etn > 0x7F:
            raise ValueError(f"bad array element type {et}")
        t = {T_GVAR: T_GARR, T_LVAR: T_LARR, T_GVAR_S: T_GARR_S, T_LVAR_S: T_LARR_S,
             T_GVAR_V: T_GARR_V, T_LVAR_V: T_LARR_V}[base.t]
        return Arg(t, (base.v, idx.v, size, etn | (0x80 if idx.t == T_GVAR else 0)))
    if tok.startswith("$") and _IDENT.match(tok[1:]):
        raise ValueError(f"named variable {tok}: write global variables as $N (N = byte offset / 4)")
    raise ValueError(f"cannot read parameter {tok!r}")


def _arg_class(a) -> str:
    """lit-int, lit-float, lit-str, var-num, var-str, label, model."""
    if isinstance(a, _Ref):
        return a.kind
    t = a.t
    if t in INT_TYPES:
        return "lit-int"
    if t == T_FLOAT:
        return "lit-float"
    if t in (T_STR8, T_STR16, T_STRV, RAW8, RAW128):
        return "lit-str"
    if t in (T_GVAR, T_LVAR, T_GARR, T_LARR):
        return "var-num"
    return "var-str"


def _check_param(cmd: Command, i: int, a, warn: list[str], line: int, cache: dict) -> str | None:
    """Error text when ``a`` cannot be parameter ``i`` of ``cmd`` (source mismatches only warn).

    ``cache`` lives for one parse, so ``id(cmd)`` is stable inside it.
    """
    key = (id(cmd), i, a.kind if isinstance(a, _Ref) else a.t)
    hit = cache.get(key)
    if hit is None:
        hit = cache[key] = _check_kind(cmd, i, a)
    err, note = hit
    if note:
        warn.append(f"SOURCE: line {line}: {note}")
    return err


def _check_kind(cmd: Command, i: int, a) -> tuple[str | None, str | None]:
    p = cmd.params[i]
    ac = _arg_class(a)
    what = f"parameter {i + 1} ({p.describe()}) of {cmd.name.lower()}"
    lit = ac.startswith("lit") or ac in ("label", "model")
    if p.source in ("var", "gvar", "lvar") and lit:
        return f"{what} must be a variable, got a {ac.replace('lit-', '')} literal", None
    k = p.kind
    if k == "float" and ac in ("lit-int", "model", "label"):
        return f"{what} is a float: write it with a decimal point (5.0)", None
    if k in ("int", "model", "label") and ac == "lit-float":
        return f"{what} is an integer, got a float", None
    if k in ("int", "model", "label", "float") and ac == "lit-str":
        return f"{what} is a number, got a string", None
    if k == "string" and ac in ("lit-int", "lit-float", "label", "model"):
        return f"{what} is a string: write 'TEXT' (8 bytes) or \"text\"", None
    if k != "string" and not isinstance(a, _Ref) and a.t in (RAW8, RAW128):
        return f"{what}: an untyped r'...' string fits only a string parameter", None
    if not isinstance(a, _Ref) and a.t in VARS | ARRAYS:
        glob = a.t in (T_GVAR, T_GVAR_S, T_GVAR_V, T_GARR, T_GARR_S, T_GARR_V)
        if p.source == "gvar" and not glob:
            return None, f"{what} expects a global variable"
        if p.source == "lvar" and glob:
            return None, f"{what} expects a local variable"
        if p.source == "lit":
            return None, f"{what} expects a literal"
    return None, None


def _parse_instr(toks: list[str], line: int, db: OpcodeDB, ps: _Parsed) -> _Ins | None:
    i = 0
    opw = None
    m = _OPCODE.match(toks[0])
    if m:
        opw = int(m.group(1), 16)
        i = 1
    neg = False
    if i < len(toks) and toks[i].lower() == "not":
        neg = True
        i += 1
    name = None
    if i < len(toks) and _IDENT.match(toks[i]) and toks[i].lower() not in ("true", "false"):
        name = toks[i]
        i += 1
    if opw is None:
        if name is None:
            ps.errors.append(AsmError(line, f"expected a command, got {toks[0]!r}",
                                      [] if not _IDENT.match(toks[0]) else db.suggest(toks[0])))
            return None
        cmd = db.find(name)
        if cmd is None:
            ps.errors.append(AsmError(line, f"unknown command {name!r} (opcode db {db.describe()})",
                                      db.suggest(name)))
            return None
        opw = cmd.op | (0x8000 if neg else 0)
    else:
        cmd = db.get(opw)
        if cmd is None:
            ps.errors.append(AsmError(line, f"unknown opcode {opw & 0x7FFF:04X} (opcode db {db.describe()})",
                                      db.suggest(name) if name else []))
            return None
        if name is not None and name.upper() != cmd.name:
            other = db.find(name)
            hint = [f"{other.op:04X}: {other.name.lower()}"] if other else db.suggest(name)
            ps.errors.append(AsmError(line, f"opcode {opw & 0x7FFF:04X} is {cmd.name.lower()}, not {name!r}", hint))
            return None
        if neg:
            opw |= 0x8000
    args = []
    bad = False
    nfix = cmd.nfixed
    cache = ps.cache
    for k, tok in enumerate(toks[i:]):
        ptype = cmd.params[k].type if k < nfix else ""
        hit = cache.get((tok, ptype))
        if hit is not None:
            args.append(Arg(hit[0], hit[1]) if isinstance(hit, tuple) else hit)
            continue
        try:
            a = _parse_param(tok, ptype)
            if k >= nfix and not isinstance(a, _Ref) and a.t in (RAW8, RAW128):
                raise ValueError(f"an untyped string {tok} cannot be a variable argument (use '...' or \"...\")")
            args.append(a)
            cache[(tok, ptype)] = a if isinstance(a, _Ref) else (a.t, a.v)
        except ValueError as e:
            ps.errors.append(AsmError(line, f"{cmd.name.lower()}: {e}"))
            bad = True
    if bad:
        return None
    nf = cmd.nfixed
    if len(args) < nf or (not cmd.variadic and len(args) > nf):
        need = f"at least {nf}" if cmd.variadic else str(nf)
        ps.errors.append(AsmError(line, f"{cmd.name.lower()} takes {need} parameters, got {len(args)}: "
                                        f"{cmd.signature()}"))
        return None
    for k in range(nf):
        err = _check_param(cmd, k, args[k], ps.warnings, line, ps.checks)
        if err:
            ps.errors.append(AsmError(line, err, [cmd.signature()]))
            bad = True
    if bad:
        return None
    return _Ins(line, opw, cmd, args, ps.nsec - 1)


def _hex_bytes(toks: list[str]) -> bytes:
    out = bytearray()
    for t in toks:
        m = _HEXTOK.match(t)
        if m:
            out += bytes([int(m.group(1), 16)]) * int(m.group(2))
        elif re.fullmatch(r"(?:[0-9A-Fa-f]{2})+", t):
            out += bytes.fromhex(t)
        else:
            raise ValueError(f"bad hex byte {t!r}")
    return bytes(out)


def parse_text(text: str, db: OpcodeDB) -> tuple[_Parsed, OpcodeDB]:
    """Parse every line (errors collected in the result). Returns the parse and the db after ``{$USE}``."""
    ps = _Parsed()
    hex_toks: list[str] | None = None
    hex_line = 0
    seen_code = False
    for line, s in _strip_comments(text.lstrip("\ufeff")):
        if len(ps.errors) >= MAX_ERRORS:
            break
        toks = _tokens(s)
        if not toks:
            continue
        if hex_toks is not None:
            if toks[-1].lower() == "end":
                hex_toks += toks[:-1]
                try:
                    ps.items.append((ps.nsec - 1, Blob(0, _hex_bytes(hex_toks), line=hex_line)))
                except ValueError as e:
                    ps.errors.append(AsmError(hex_line, f"hex block: {e}"))
                hex_toks = None
            else:
                hex_toks += toks
            continue
        t0 = toks[0]
        if t0.startswith("{$"):
            body = t0[2:-1].strip().split()
            key = body[0].upper() if body else ""
            if len(toks) > 1:
                ps.errors.append(AsmError(line, f"text after the directive {t0}"))
            if key in _KINDS:
                if ps.kind is not None:
                    ps.errors.append(AsmError(line, f"a second file kind directive {t0}"))
                elif seen_code:
                    ps.errors.append(AsmError(line, f"{t0} must come before the code"))
                ps.kind = _KINDS[key]
                if key == "CLEO":
                    ext = body[1] if len(body) > 1 else ".cs"
                    ps.ext = ext if ext.startswith(".") else "." + ext
            elif key == "MISSION":
                if ps.kind != "main":
                    ps.errors.append(AsmError(line, "{$MISSION} is only valid in a {$MAIN} file"))
                ps.nsec += 1
                ps.items.append((ps.nsec - 1, ("mission", line)))
                seen_code = True
            elif key == "USE":
                ps.use = tuple(body[1:])
                known = set(db.exts())
                for e in ps.use:
                    if e not in known:
                        ps.errors.append(AsmError(line, f"unknown extension {e!r} in {{$USE}}", sorted(known)))
                db = db.preferring(ps.use)
            else:
                ps.warnings.append(f"DIRECTIVE: line {line}: {t0} ignored")
            continue
        if t0.upper() == "DEFINE":
            ps.defines.append((line, toks[1:]))
            continue
        if t0.lower() == "hex":
            seen_code = True
            rest = toks[1:]
            if rest and rest[-1].lower() == "end":
                try:
                    ps.items.append((ps.nsec - 1, Blob(0, _hex_bytes(rest[:-1]), line=line)))
                except ValueError as e:
                    ps.errors.append(AsmError(line, f"hex block: {e}"))
            else:
                hex_toks, hex_line = list(rest), line
            continue
        if t0.startswith(":"):
            name = t0[1:]
            if not _LABEL.match(name):
                ps.errors.append(AsmError(line, f"bad label {t0!r} (letters, digits, _)"))
            else:
                ps.items.append((ps.nsec - 1, ("label", name, line)))
            seen_code = True
            toks = toks[1:]
            if not toks:
                continue
        seen_code = True
        ins = _parse_instr(toks, line, db, ps)
        if ins is not None:
            ps.items.append((ps.nsec - 1, ins))
    if hex_toks is not None:
        ps.errors.append(AsmError(hex_line, "hex block without 'end'"))
    return ps, db


# --------------------------------------------------------------------------- header (main.scm)


def _name_bytes(tok: str, n: int) -> bytes:
    if tok == "(noname)":
        return b""
    if tok[:1] in "'\"":
        b = unescape(tok[1:-1])
    elif _LABEL.match(tok):
        b = tok.encode("latin-1")
    else:
        raise ValueError(f"bad name {tok!r}")
    if len(b) > n:
        raise ValueError(f"name {tok} longer than {n} bytes")
    return b


def _int_tok(tok: str) -> int:
    return int(tok, 0)


def _build_header(ps: _Parsed) -> tuple[MainHeader, dict]:
    """Header fields from DEFINE lines; returns ``(header, extras)`` (mission labels, overrides)."""
    h = MainHeader()
    extra: dict = {"missions": [], "main_size": None, "largest": None, "globals": None, "g6": None,
                   "objects_n": None, "missions_n": None, "scripts_n": None}
    gbytes: list[tuple[int, bytes, int]] = []
    for line, args in ps.defines:
        if not args:
            ps.errors.append(AsmError(line, "empty DEFINE"))
            continue
        key = args[0].upper()
        a = args[1:]
        try:
            if key == "GLOBALS_SIZE":
                extra["globals"] = _int_tok(a[0])
            elif key == "TARGET_GAME":
                h.game = _int_tok(a[0])
            elif key == "SEGMENT_IDS":
                h.seg_ids = tuple(_int_tok(x) for x in a)
                if len(h.seg_ids) != 5:
                    raise ValueError("SEGMENT_IDS takes 5 numbers")
            elif key == "GLOBAL_BYTES":
                gbytes.append((_int_tok(a[0]), bytes.fromhex(a[1]), line))
            elif key == "OBJECTS":
                extra["objects_n"] = (_int_tok(a[0]), line)
            elif key == "OBJECT":
                h.objects.append(_name_bytes(a[0], 24).ljust(24, b"\0"))
            elif key == "MISSIONS":
                extra["missions_n"] = (_int_tok(a[0]), line)
            elif key == "MISSION":
                idx = _int_tok(a[0])
                if len(a) != 3 or a[1].upper() != "AT" or not a[2].startswith("@"):
                    raise ValueError("write DEFINE MISSION <n> AT @LABEL")
                if idx != len(extra["missions"]):
                    raise ValueError(f"mission {idx} out of order (expected {len(extra['missions'])})")
                extra["missions"].append((a[2][1:], line))
            elif key == "EXCLUSIVE_MISSIONS":
                h.exclusive = _int_tok(a[0])
            elif key == "MISSION_LOCALS":
                h.mission_locals = _int_tok(a[0])
            elif key == "EXTERNAL_SCRIPTS":
                extra["scripts_n"] = (_int_tok(a[0]), line)
            elif key == "LARGEST_EXTERNAL_SIZE":
                h.largest_external = _int_tok(a[0])
            elif key == "SCRIPT":
                if len(a) != 5 or a[1].upper() != "OFFSET" or a[3].upper() != "SIZE":
                    raise ValueError("write DEFINE SCRIPT <name> OFFSET <n> SIZE <n>")
                h.externals.append((_name_bytes(a[0], 20).ljust(20, b"\0"), _int_tok(a[2]), _int_tok(a[4])))
            elif key == "UNKNOWN_EMPTY_SEGMENT":
                h.seg5 = _int_tok(a[0])
            elif key == "GLOBAL_VARS_SIZE":
                extra["g6"] = _int_tok(a[0])
            elif key == "UNKNOWN_THREADS_MEMORY":
                h.seg6_threads = _int_tok(a[0])
            elif key == "MAIN_SIZE":
                extra["main_size"] = _int_tok(a[0])
            elif key == "LARGEST_MISSION_SIZE":
                extra["largest"] = _int_tok(a[0])
            else:
                ps.errors.append(AsmError(line, f"unknown DEFINE {args[0]}", [
                    "GLOBALS_SIZE", "OBJECTS", "OBJECT", "MISSIONS", "MISSION", "EXCLUSIVE_MISSIONS", "MISSION_LOCALS",
                    "EXTERNAL_SCRIPTS", "LARGEST_EXTERNAL_SIZE", "SCRIPT", "UNKNOWN_EMPTY_SEGMENT",
                    "UNKNOWN_THREADS_MEMORY"]))
        except (ValueError, IndexError) as e:
            ps.errors.append(AsmError(line, f"DEFINE {args[0]}: {e or 'missing value'}"))
    for key, items, what in (("objects_n", h.objects, "OBJECT"), ("missions_n", extra["missions"], "MISSION"),
                             ("scripts_n", h.externals, "SCRIPT")):
        if extra[key] is not None and extra[key][0] != len(items):
            ps.errors.append(AsmError(extra[key][1], f"{extra[key][0]} declared, {len(items)} DEFINE {what} lines"))
    if extra["globals"] is None:
        ps.errors.append(AsmError(0, "{$MAIN} needs DEFINE GLOBALS_SIZE <bytes>"))
        extra["globals"] = 0
    h.globals_size = extra["globals"]
    data = bytearray(h.globals_size)
    for off, b, line in gbytes:
        if off + len(b) > len(data):
            ps.errors.append(AsmError(line, "GLOBAL_BYTES beyond GLOBALS_SIZE"))
            continue
        data[off:off + len(b)] = b
    h.global_data = bytes(data)
    h.seg6_globals = extra["g6"] if extra["g6"] is not None else h.globals_size
    h.missions = [0] * len(extra["missions"])      # offsets come after the layout; the size counts now
    return h, extra


def _encode_header(h: MainHeader) -> bytes:
    out = bytearray()

    def seg(nxt: int, ident: int) -> None:
        out.extend(b"\x02\x00\x01" + struct.pack("<i", nxt))
        out.append(ident & 0xFF)

    t1 = 8 + h.globals_size
    seg(t1, h.game)
    out += h.global_data
    t2 = t1 + 12 + 24 * len(h.objects)
    seg(t2, h.seg_ids[0])
    out += struct.pack("<I", len(h.objects)) + b"".join(h.objects)
    t3 = t2 + 24 + 4 * len(h.missions)
    seg(t3, h.seg_ids[1])
    out += struct.pack("<IIHHI", h.main_size, h.largest_mission, len(h.missions), h.exclusive, h.mission_locals)
    out += struct.pack(f"<{len(h.missions)}I", *h.missions)
    t4 = t3 + 16 + 28 * len(h.externals)
    seg(t4, h.seg_ids[2])
    out += struct.pack("<II", h.largest_external, len(h.externals))
    for name, off, size in h.externals:
        out += struct.pack("<20sII", name, off, size)
    t5 = t4 + 12
    seg(t5, h.seg_ids[3])
    out += struct.pack("<I", h.seg5)
    t6 = t5 + 16
    seg(t6, h.seg_ids[4])
    out += struct.pack("<II", h.seg6_globals, h.seg6_threads)
    return bytes(out)


# --------------------------------------------------------------------------- models


def _resolve_models(ps: _Parsed, objects: list[bytes], profile: str | None) -> dict[str, int]:
    names = sorted({a.name.upper() for _, it in ps.items if isinstance(it, _Ins)
                    for a in it.args if isinstance(a, _Ref) and a.kind == "model"})
    if not names:
        return {}
    out: dict[str, int] = {}
    for i, raw in enumerate(objects):
        s = raw.split(b"\0", 1)[0].decode("latin-1").upper()
        if i and s and s not in out:
            out[s] = -i
    rest = [n for n in names if n not in out]
    if rest:
        why = None
        try:
            from ..index.api import open_index

            db = open_index(profile or "vanilla")
            for k in range(0, len(rest), 400):
                part = rest[k:k + 400]
                env = db.query(f"SELECT id, upper(name) FROM v_model WHERE upper(name) IN ({','.join('?' * len(part))})",
                               part, limit=500)
                for mid, nm in env["rows"]:
                    out.setdefault(str(nm), int(mid))
        except SatkError as e:
            why = f"{e.code}: {e.msg}"
        for _, it in ps.items:
            if isinstance(it, _Ins):
                for a in it.args:
                    if isinstance(a, _Ref) and a.kind == "model" and a.name.upper() not in out:
                        ps.errors.append(AsmError(it.line, f"unknown model {a.tok}" + (f" (no index: {why})" if why else ""),
                                                  ["satk asset find " + a.name.lower() + " --kind model"]))
    return out


# --------------------------------------------------------------------------- assemble


def assemble(text: str, db: OpcodeDB, *, kind: str | None = None, profile: str | None = None,
             source: str = "", raise_errors: bool = True) -> AsmResult:
    """Assemble ``text``; ``SatkError(BAD_PARAMS)`` with every error (line numbers) in ``data.errors``.

    ``raise_errors=False`` returns the errors in :attr:`AsmResult.errors` instead (``data`` is then empty).
    """
    ps, db = parse_text(text, db)
    if kind is not None:
        if ps.kind is not None and ps.kind != kind:
            ps.warnings.append(f"KIND: the text says {ps.kind}, assembling as {kind}")
        ps.kind = kind
    if ps.kind is None:
        ps.kind = "cleo"
        ps.warnings.append("KIND: no {$CLEO}/{$EXTERNAL}/{$MAIN} directive: assembling a CLEO .cs script")
    if ps.kind != "main" and ps.defines:
        ps.errors.append(AsmError(ps.defines[0][0], "DEFINE lines are only valid in a {$MAIN} file"))
    header, extra = (_build_header(ps) if ps.kind == "main" else (None, {}))
    models = _resolve_models(ps, header.objects if header else [], profile)

    # sections and layout
    base = header.size() if header is not None else 0
    secs: list[Section] = []
    if ps.kind == "main":
        secs.append(Section("main", base, base, 0, False, prefix="MAIN"))
    else:
        secs.append(Section("script", 0, 0, 0, True))
    labels: dict[str, tuple[int, int, int]] = {}       # name -> (offset, section idx, line)
    pos = base
    cur = 0
    order: list = []
    for sec_i, it in ps.items:
        if isinstance(it, tuple) and it[0] == "mission":
            secs[cur].end = pos
            secs.append(Section("mission", pos, pos, pos, True, index=len(secs) - 1))
            cur = len(secs) - 1
            continue
        if isinstance(it, tuple) and it[0] == "label":
            _, name, line = it
            if name in labels:
                ps.errors.append(AsmError(line, f"label :{name} defined twice (first on line {labels[name][2]})"))
            labels[name] = (pos, cur, line)
            continue
        if isinstance(it, Blob):
            it.off = pos
            pos += len(it.data)
        else:
            for k, a in enumerate(it.args):
                if isinstance(a, _Ref) and a.kind == "model":
                    v = models.get(a.name.upper(), 0)
                    it.args[k] = Arg(min_int_type(v), v)
            it.off, it.sec = pos, cur
            it.size = 2 + sum(_arg_size(a) for a in it.args) + (1 if it.cmd.variadic else 0)
            pos += it.size
        order.append((cur, it))
    secs[cur].end = pos
    if ps.kind == "main":
        nm = [s for s in secs if s.kind == "mission"]
        h = header
        mis = []
        for name, line in extra["missions"]:
            if name not in labels:
                ps.errors.append(AsmError(line, f"DEFINE MISSION: no label :{name}"))
                mis.append(0)
            else:
                mis.append(labels[name][0])
        h.missions = mis
        h.main_size = extra["main_size"] if extra["main_size"] is not None else (nm[0].start if nm else pos)
        h.largest_mission = extra["largest"] if extra["largest"] is not None else \
            max((s.end - s.start for s in nm), default=0)
        h.code_start = base
    # encode
    out = bytearray(_encode_header(header) if header is not None else b"")
    prog = Program(ps.kind, secs, header, ps.ext, 0, db=db)
    lines: dict[int, int] = {}
    for sec_i, it in order:
        sec = secs[sec_i]
        if isinstance(it, Blob):
            sec.items.append(it)
            lines[it.off] = it.line
            out += it.data
            continue
        args = []
        for a in it.args:
            if isinstance(a, _Ref):
                tgt = labels.get(a.name)
                if tgt is None:
                    known = sorted(labels)
                    ps.errors.append(AsmError(it.line, f"no label :{a.name}",
                                              difflib.get_close_matches(a.name, known, n=3, cutoff=0.6)))
                    v = 0
                else:
                    v = _label_value(sec, secs[tgt[1]], tgt[0], a.name, it.line, ps)
                args.append(Arg(T_INT32, v, a.name))
            else:
                args.append(a)
        ins = Instr(it.off, it.opw, it.cmd, args, it.size, it.line)
        sec.items.append(ins)
        lines[it.off] = it.line
        b = encode_instr(ins)
        if len(b) != it.size:  # pragma: no cover - layout and encoder agree by construction
            raise SatkError("INTERNAL", f"line {it.line}: size {len(b)} != {it.size}")
        out += b
    if ps.errors:
        if raise_errors:
            _raise(ps.errors, source)
        return AsmResult(b"", prog, ps.kind, ps.ext, ps.warnings, lines, ps.errors)
    for name, (off, si, _line) in labels.items():
        prog.labels.setdefault(off, name)
    prog.size = len(out)
    for s in secs:
        if not s.prefix:
            s.prefix = "SCRIPT" if s.kind == "script" else f"MISSION{s.index}"
    return AsmResult(bytes(out), prog, ps.kind, ps.ext if ps.kind == "cleo" else ".scm", ps.warnings, lines)


def _arg_size(a) -> int:
    if isinstance(a, _Ref):
        return 5
    t = a.t
    if t in INT_TYPES:
        return 1 + {T_INT8: 1, T_INT16: 2, T_INT32: 4}[t]
    if t == T_FLOAT:
        return 5
    if t in VARS:
        return 3
    if t in ARRAYS:
        return 7
    if t == T_STR8:
        return 9
    if t == T_STR16:
        return 17
    if t == T_STRV:
        return 2 + len(a.v)
    if t == RAW8:
        return 8
    if t == RAW128:
        return 128
    raise ValueError(t)  # pragma: no cover


def _label_value(sec: Section, tsec: Section, off: int, name: str, line: int, ps: _Parsed) -> int:
    if sec.rel and tsec is sec:
        v = -(off - sec.base)
        if v == 0:
            ps.warnings.append(f"JUMP_ZERO: line {line}: @{name} is the first byte of the script, its offset encodes "
                               "as 0 (= absolute offset 0); put a command before the label")
        return v
    if tsec.kind == "main":
        return off
    ps.errors.append(AsmError(line, f"@{name} is in another {tsec.kind} section: jumps reach only their own "
                                    "section or the main code"))
    return 0


def _raise(errors: list[AsmError], source: str) -> None:
    first = errors[0]
    dym = []
    for e in errors:
        dym += [d for d in e.did_you_mean if d not in dym]
    raise SatkError("BAD_PARAMS", f"{len(errors)} error(s){' in ' + source if source else ''}; {first.text()}",
                    hint="fix the lines listed in error.data.errors (satk script check shows them with warnings)",
                    did_you_mean=dym[:8],
                    data={"errors": [e.text() for e in errors[:20]], "total": len(errors)})
