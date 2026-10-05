"""Tiny C++ helpers for scanning donor sources with regular expressions (owner WP-09).

Not a parser: just enough to read ``StaticRef<T>(0x...)``, ``VALIDATE_SIZE``, ``constexpr``/enum
constants, MTA ``#define HOOKPOS_*`` and ``MemPut<T>(addr, ...)`` calls robustly:

* :func:`blank` — comments, string and char literals (optionally preprocessor lines) replaced by
  spaces with newlines kept, so offsets and line numbers stay valid;
* :class:`LineIndex` — offset -> 1-based line;
* :func:`match_close`, :func:`split_top` — balanced ``<>``/``()`` handling with nesting;
* :class:`ConstEval` — lazy, cycle-safe evaluation of integer constant expressions with
  ``sizeof``, casts, enums, ``#define``s and type aliases;
* :class:`ScopeMap` — namespace/class/function scope names for an offset (to qualify globals).
"""

from __future__ import annotations

import bisect
import re
from typing import Callable, Iterable

__all__ = ["blank", "LineIndex", "match_close", "split_top", "ConstEval", "ScopeMap", "norm_type",
           "array_info", "iter_calls", "PRIMITIVE_SIZES"]

# --------------------------------------------------------------------------- blanking

_LIT = re.compile(
    r"//[^\n]*"
    r"|/\*.*?\*/"
    r"|(?:u8|[uUL])?R\"([^()\\\s]{0,16})\(.*?\)\1\""
    r"|\"(?:\\.|[^\"\\\n])*\""
    r"|(?<![0-9A-Za-z_])'(?:\\.|[^'\\\n]){1,8}'",
    re.S,
)
_PREPROC = re.compile(r"^[ \t]*#(?:[^\n\\]|\\.|\\\n)*", re.M)


def _spaces(s: str) -> str:
    return "".join("\n" if c == "\n" else " " for c in s)


def blank(text: str, *, strings: bool = True, preproc: bool = False) -> str:
    """Replace comments (and string/char literals, preprocessor lines) with spaces.

    Offsets and newlines are preserved, so :class:`LineIndex` built on the original text
    stays valid for the result.
    """
    def sub(m: re.Match) -> str:
        s = m.group(0)
        if not strings and s[:1] in ("\"", "'", "R", "u", "U", "L"):
            return s
        return _spaces(s)

    out = _LIT.sub(sub, text)
    if preproc:
        out = _PREPROC.sub(lambda m: _spaces(m.group(0)), out)
    return out


class LineIndex:
    """Offset -> 1-based line number."""

    def __init__(self, text: str):
        self._nl = [m.start() for m in re.finditer("\n", text)]

    def line(self, pos: int) -> int:
        return bisect.bisect_left(self._nl, pos) + 1


# --------------------------------------------------------------------------- brackets

_OPEN = {"(": ")", "[": "]", "{": "}", "<": ">"}


def match_close(s: str, i: int, *, angle: bool | None = None) -> int:
    """Index just after the bracket matching ``s[i]`` (``(``, ``[``, ``{`` or ``<``), or -1.

    ``<``/``>`` are tracked only when ``s[i] == '<'`` (template arguments) unless ``angle``
    says otherwise; ``->`` is never a closer. A ``;`` at depth 0 aborts.
    """
    opener = s[i]
    track_angle = (opener == "<") if angle is None else angle
    stack = [opener]
    j = i + 1
    n = len(s)
    while j < n:
        c = s[j]
        if c in "([{" or (c == "<" and track_angle):
            stack.append(c)
        elif c in ")]}" or (c == ">" and track_angle and s[j - 1] != "-"):
            top = stack[-1]
            if _OPEN.get(top) != c:
                if c == ">":  # a stray '>' (comparison) inside parentheses: ignore
                    j += 1
                    continue
                return -1
            stack.pop()
            if not stack:
                return j + 1
        elif c == ";" and len(stack) == 1 and opener != "{":
            return -1
        j += 1
    return -1


def split_top(s: str, sep: str = ",", *, angle: bool = True) -> list[str]:
    """Split at ``sep`` outside of any brackets (``<>`` included when ``angle``)."""
    out: list[str] = []
    depth = 0
    cur: list[str] = []
    for k, c in enumerate(s):
        if c in "([{" or (angle and c == "<"):
            depth += 1
        elif c in ")]}" or (angle and c == ">" and (k == 0 or s[k - 1] != "-")):
            depth = max(0, depth - 1)
        if c == sep and depth == 0:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(c)
    out.append("".join(cur))
    return [x.strip() for x in out]


# --------------------------------------------------------------------------- types

#: Sizes on the 32-bit target (gta_sa.exe, MSVC).
PRIMITIVE_SIZES: dict[str, int] = {"void": 0}
for _n, _names in {
    1: ["bool", "char", "int8", "uint8", "int8_t", "uint8_t", "uchar", "byte", "BYTE", "CHAR", "UCHAR",
        "BOOLEAN", "RwUInt8", "RwInt8", "RwChar", "signed char", "unsigned char", "char8_t", "std::byte"],
    2: ["short", "int16", "uint16", "int16_t", "uint16_t", "ushort", "WORD", "SHORT", "USHORT", "wchar_t",
        "char16_t", "RwUInt16", "RwInt16", "unsigned short", "signed short", "short int", "unsigned short int"],
    4: ["int", "int32", "uint32", "int32_t", "uint32_t", "uint", "unsigned", "signed", "long", "ulong", "float",
        "DWORD", "LONG", "ULONG", "UINT", "INT", "BOOL", "FLOAT", "size_t", "ptrdiff_t", "intptr_t",
        "uintptr_t", "uintptr", "intptr", "RwInt32", "RwUInt32", "RwReal", "RwBool", "RwFixed", "char32_t",
        "unsigned int", "signed int", "unsigned long", "signed long", "long int", "unsigned long int",
        "HANDLE", "HWND", "LPVOID", "PVOID", "LPSTR", "LPCSTR", "float32"],
    8: ["int64", "uint64", "int64_t", "uint64_t", "double", "long long", "unsigned long long",
        "signed long long", "__int64", "unsigned __int64", "LARGE_INTEGER", "float64", "RwUInt64", "RwInt64",
        "long double"],
}.items():
    for _t in _names:
        PRIMITIVE_SIZES[_t] = _n
        if not _t.startswith("std::") and " " not in _t and _t.endswith("_t"):
            PRIMITIVE_SIZES["std::" + _t] = _n

_QUAL = re.compile(r"\b(?:const|volatile|struct|class|enum|union|typename|mutable)\b")


def norm_type(t: str) -> str:
    """Canonical spelling: no cv/struct keywords, single spaces, ``T *`` -> ``T*``."""
    t = _QUAL.sub(" ", t)
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"\s*([*&<>,\[\]])\s*", r"\1", t)
    t = t.replace(",", ", ")
    return t


# --------------------------------------------------------------------------- constants

_NUM = re.compile(r"(0[xX][0-9a-fA-F']+|0[bB][01']+|(?:\d[\d']*\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)([uUlLfF]*)")
_TOK = re.compile(
    r"\s*(?:(?P<num>0[xX][0-9a-fA-F']+[uUlL]*|0[bB][01']+[uUlL]*|(?:\d[\d']*\.?\d*|\.\d+)(?:[eE][+-]?\d+)?[uUlLfF]*)"
    r"|(?P<id>(?:::)?[A-Za-z_]\w*(?:\s*::\s*~?[A-Za-z_]\w*)*)"
    r"|(?P<op><<|>>|<=|>=|==|!=|&&|\|\||[-+*/%&|^~!()<>?:,]))"
)


class _Unresolved(Exception):
    pass


class ConstEval:
    """Lazy constant-expression evaluator over collected definitions.

    Definitions are raw expression strings (``define``); values are computed on demand with
    a recursion guard, so definition order across files does not matter. Identifiers are
    looked up exactly, then by their last ``::`` component.
    """

    def __init__(self) -> None:
        self.raw: dict[str, str] = {}
        self.values: dict[str, int | float] = {}
        self.sizes: dict[str, int] = {}          # struct/class sizes (VALIDATE_SIZE)
        self.aliases: dict[str, str] = {}        # using X = T; typedef T X;
        self.enum_sizes: dict[str, int] = {}     # enum name -> underlying size
        self._busy: set[str] = set()
        self._fail: set[str] = set()

    # -- definitions -------------------------------------------------------------------

    def define(self, name: str, expr: str, *, override: bool = False) -> None:
        name = name.replace(" ", "")
        if override or name not in self.raw:
            self.raw[name] = expr
        short = name.rsplit("::", 1)[-1]
        if short != name and short not in self.raw:
            self.raw[short] = expr

    def set_value(self, name: str, value: int | float) -> None:
        self.values[name.replace(" ", "")] = value

    def alias(self, name: str, target: str) -> None:
        self.aliases.setdefault(norm_type(name), norm_type(target))

    # -- lookup ------------------------------------------------------------------------

    def value(self, name: str) -> int | float | None:
        name = name.replace(" ", "")
        if name.startswith("::"):
            name = name[2:]
        for key in (name, name.rsplit("::", 1)[-1]):
            if key in self.values:
                return self.values[key]
            if key in self._fail:
                continue
            if key in self.raw:
                if key in self._busy:
                    return None
                self._busy.add(key)
                try:
                    v = self.eval(self.raw[key])
                finally:
                    self._busy.discard(key)
                if v is None:
                    self._fail.add(key)
                    continue
                self.values[key] = v
                return v
        return None

    # -- sizes -------------------------------------------------------------------------

    def type_size(self, t: str, _depth: int = 0) -> int | None:
        """``sizeof(t)`` on the 32-bit target, or ``None`` if unknown."""
        if _depth > 12:
            return None
        t = norm_type(t)
        if not t:
            return None
        if t.endswith("*") or t.endswith("&"):
            return 4
        info = array_info(t, self)
        if info is not None:
            elem, n = info
            es = self.type_size(elem, _depth + 1)
            return es * n if es is not None and n is not None else None
        if t in PRIMITIVE_SIZES:
            return PRIMITIVE_SIZES[t]
        m = re.match(r"^(?:notsa::)?WEnum([US])(8|16|32|64)<", t)
        if m:
            return int(m.group(2)) // 8
        if t.startswith("std::") and t[5:] in PRIMITIVE_SIZES:
            return PRIMITIVE_SIZES[t[5:]]
        for key in (t, t.rsplit("::", 1)[-1]):
            if key in self.sizes:
                return self.sizes[key]
            if key in self.enum_sizes:
                return self.enum_sizes[key]
            if key in self.aliases and self.aliases[key] != t:
                return self.type_size(self.aliases[key], _depth + 1)
        m = re.fullmatch(r"(?:std::)?(?:pair)<(.+)>", t)
        if m:
            parts = split_top(m.group(1))
            if len(parts) == 2:
                a, b = (self.type_size(x, _depth + 1) for x in parts)
                if a is not None and b is not None:
                    al = min(4, max(a if a < 4 else 4, b if b < 4 else 4))
                    return ((a + al - 1) // al) * al + ((b + al - 1) // al) * al
        return None

    # -- evaluation --------------------------------------------------------------------

    def eval(self, expr: str) -> int | float | None:
        """Value of a C++ constant expression or ``None``."""
        try:
            toks = self._tokens(expr)
            if not toks:
                return None
            p = _Parser(toks, self)
            v = p.ternary()
            if p.i != len(toks):
                return None
            return v
        except (_Unresolved, ZeroDivisionError, OverflowError, ValueError, IndexError, RecursionError):
            return None

    def _tokens(self, expr: str) -> list[tuple[str, object]]:
        s = expr.strip()
        # casts and wrappers that do not change the value
        s = re.sub(r"\b(?:static_cast|reinterpret_cast|const_cast|narrow_cast|notsa::narrow_cast)\s*<[^<>()]*(?:<[^<>()]*>[^<>()]*)*>\s*", "", s)
        s = re.sub(r"\(\s*(?:const\s+)?(?:unsigned\s+|signed\s+)?[A-Za-z_][\w:]*\s*\**\s*\)\s*(?=[\w(.\-~+])",
                   lambda m: "" if _is_type_cast(m.group(0), self) else m.group(0), s)
        out: list[tuple[str, object]] = []
        i = 0
        while i < len(s):
            if s[i].isspace():
                i += 1
                continue
            if s.startswith("sizeof", i) and not (i + 6 < len(s) and (s[i + 6].isalnum() or s[i + 6] == "_")):
                j = i + 6
                while j < len(s) and s[j].isspace():
                    j += 1
                if j < len(s) and s[j] == "(":
                    k = match_close(s, j, angle=False)
                    if k < 0:
                        raise _Unresolved("sizeof")
                    size = self.type_size(s[j + 1:k - 1])
                    if size is None:
                        raise _Unresolved("sizeof")
                    out.append(("num", size))
                    i = k
                    continue
                raise _Unresolved("sizeof")
            m = _TOK.match(s, i)
            if not m or m.end() == i:
                raise _Unresolved(s[i:i + 8])
            if m.group("num") is not None:
                out.append(("num", _parse_num(m.group("num"))))
            elif m.group("id") is not None:
                ident = re.sub(r"\s+", "", m.group("id"))
                if ident in ("true", "false"):
                    out.append(("num", 1 if ident == "true" else 0))
                else:
                    out.append(("id", ident))
            else:
                out.append(("op", m.group("op")))
            i = m.end()
        return out


def _is_type_cast(text: str, ev: ConstEval) -> bool:
    """``(T)`` followed by an operand: a cast if ``T`` is a type, not a parenthesized constant."""
    inner = text.strip()[1:].split(")")[0].strip()
    t = norm_type(inner)
    if t.endswith("*"):
        return True
    base = t.strip()
    short = base.rsplit("::", 1)[-1]
    if any(k in ev.values or k in ev.raw for k in (base, short)):
        return False                       # "(KIND_TOTAL) + 1" is arithmetic, not a cast
    return (base in PRIMITIVE_SIZES or base in ev.aliases or base in ev.sizes or base in ev.enum_sizes
            or base.startswith("std::") or re.match(r"^e[A-Z]\w*$", short) is not None)


def _parse_num(tok: str) -> int | float:
    t = tok.replace("'", "")
    t = t.rstrip("uUlL") if not t.lower().startswith("0x") else re.sub(r"[uUlL]+$", "", t)
    if t.lower().startswith("0x"):
        return int(t, 16)
    if t.lower().startswith("0b"):
        return int(t[2:], 2)
    if re.fullmatch(r"\d+", t):
        if len(t) > 1 and t.startswith("0"):
            try:
                return int(t, 8)
            except ValueError:
                return int(t)
        return int(t)
    return float(t.rstrip("fF"))


_BIN_PREC = [("||",), ("&&",), ("|",), ("^",), ("&",), ("==", "!="), ("<", "<=", ">", ">="),
             ("<<", ">>"), ("+", "-"), ("*", "/", "%")]


class _Parser:
    def __init__(self, toks: list[tuple[str, object]], ev: ConstEval):
        self.t = toks
        self.i = 0
        self.ev = ev

    def peek(self) -> tuple[str, object] | None:
        return self.t[self.i] if self.i < len(self.t) else None

    def take_op(self, *ops: str) -> str | None:
        p = self.peek()
        if p and p[0] == "op" and p[1] in ops:
            self.i += 1
            return str(p[1])
        return None

    def ternary(self):
        c = self.binary(0)
        if self.take_op("?"):
            a = self.ternary()
            if not self.take_op(":"):
                raise _Unresolved("?:")
            b = self.ternary()
            return a if c else b
        return c

    def binary(self, level: int):
        if level >= len(_BIN_PREC):
            return self.unary()
        lhs = self.binary(level + 1)
        while True:
            op = self.take_op(*_BIN_PREC[level])
            if op is None:
                return lhs
            rhs = self.binary(level + 1)
            lhs = _apply(op, lhs, rhs)

    def unary(self):
        op = self.take_op("-", "+", "~", "!")
        if op:
            v = self.unary()
            return {"-": lambda x: -x, "+": lambda x: x, "~": lambda x: ~int(x), "!": lambda x: int(not x)}[op](v)
        return self.primary()

    def primary(self):
        p = self.peek()
        if p is None:
            raise _Unresolved("eof")
        if p[0] == "num":
            self.i += 1
            return p[1]
        if p[0] == "id":
            self.i += 1
            nxt = self.peek()
            if nxt and nxt[0] == "op" and nxt[1] == "(":
                fname = str(p[1])
                if fname in ("std::max", "std::min", "max", "min"):
                    self.i += 1
                    args = [self.ternary()]
                    while self.take_op(","):
                        args.append(self.ternary())
                    if not self.take_op(")"):
                        raise _Unresolved(")")
                    return max(args) if fname.endswith("max") else min(args)
                raise _Unresolved(fname)  # other function-style calls are unsupported
            v = self.ev.value(str(p[1]))
            if v is None:
                raise _Unresolved(str(p[1]))
            return v
        if self.take_op("("):
            v = self.ternary()
            if not self.take_op(")"):
                raise _Unresolved(")")
            return v
        raise _Unresolved(str(p[1]))


def _apply(op: str, a, b):
    if op == "/":
        if isinstance(a, int) and isinstance(b, int):
            q = abs(a) // abs(b)
            return q if (a >= 0) == (b >= 0) else -q
        return a / b
    if op == "%":
        return int(a) % int(b) if a >= 0 else -(abs(int(a)) % abs(int(b)))
    return {
        "+": lambda: a + b, "-": lambda: a - b, "*": lambda: a * b,
        "<<": lambda: int(a) << int(b), ">>": lambda: int(a) >> int(b),
        "&": lambda: int(a) & int(b), "|": lambda: int(a) | int(b), "^": lambda: int(a) ^ int(b),
        "==": lambda: int(a == b), "!=": lambda: int(a != b), "<": lambda: int(a < b),
        "<=": lambda: int(a <= b), ">": lambda: int(a > b), ">=": lambda: int(a >= b),
        "&&": lambda: int(bool(a) and bool(b)), "||": lambda: int(bool(a) or bool(b)),
    }[op]()


# --------------------------------------------------------------------------- arrays

_STDARRAY = re.compile(r"^(?:std::)?array<(.+)>$")
_MDARRAY = re.compile(r"^(?:notsa::)?mdarray<(.+)>$")
_CARRAY = re.compile(r"^(.+?)\[([^\[\]]+)\]((?:\[[^\[\]]+\])*)$")


def array_info(t: str, ev: ConstEval | None = None) -> tuple[str, int | None] | None:
    """``(elem_type, length)`` for ``std::array<E,N>``, ``E[N]...``, ``notsa::mdarray<E,A,B>``.

    ``None`` if ``t`` is not an array type; length ``None`` if it cannot be evaluated.
    """
    t = norm_type(t)
    m = _STDARRAY.match(t)
    if m:
        parts = split_top(m.group(1))
        if len(parts) == 2:
            return norm_type(parts[0]), _int(ev.eval(parts[1]) if ev else None)
        return None
    m = _MDARRAY.match(t)
    if m:
        parts = split_top(m.group(1))
        if len(parts) >= 2:
            n: int | None = 1
            for d in parts[1:]:
                v = _int(ev.eval(d) if ev else None)
                n = None if v is None or n is None else n * v
            return norm_type(parts[0]), n
        return None
    m = _CARRAY.match(t)
    if m and "<" not in m.group(2):
        elem = norm_type(m.group(1) + m.group(3))
        return elem, _int(ev.eval(m.group(2)) if ev else None)
    return None


def _int(v) -> int | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, float):
        return int(v) if v.is_integer() else None
    return int(v)


# --------------------------------------------------------------------------- scopes

_HEAD_NS = re.compile(r"\bnamespace\s*((?:[A-Za-z_]\w*\s*(?:::\s*)?)*)$")
_HEAD_CLS = re.compile(
    r"\b(class|struct|union)\s+(?:alignas\s*\([^)]*\)\s*|__declspec\s*\([^)]*\)\s*|\[\[[^\]]*\]\]\s*)*"
    r"(?:[A-Z][A-Z0-9_]+\s+)*([A-Za-z_]\w*(?:\s*::\s*[A-Za-z_]\w*)*)\s*(?:final\s*)?(?::(?!:)[^{;]*)?$", re.S)
_HEAD_ENUM = re.compile(r"\benum\s+(?:class\s+|struct\s+)?([A-Za-z_]\w*)?\s*(?::\s*([\w:\s]+))?$", re.S)
_HEAD_FN = re.compile(
    r"((?:[A-Za-z_]\w*\s*::\s*)*(?:operator\s*(?:new|delete)(?:\s*\[\s*\])?|operator\s*(?:\(\s*\)|[^\s\w(]+)"
    r"|~?[A-Za-z_]\w*)(?:\s*<[^;{}()]*>)?)\s*\((?:[^()]|\([^()]*\))*\)"
    r"(?:\s*(?:const|noexcept|override|final|volatile|&|&&|mutable|constexpr|throw\s*\([^)]*\)))*"
    r"(?:\s*->\s*[^{;]+)?\s*(?::[^{;]*)?$", re.S)
_CTRL = {"if", "for", "while", "switch", "catch", "return", "sizeof", "decltype", "alignof", "do", "else", "try"}


class ScopeMap:
    """Scope labels of a (blanked, preprocessor-free) C++ text.

    ``chain(pos)`` -> list of ``(kind, name)`` from outermost to innermost, kinds: ``ns``,
    ``class``, ``enum``, ``fn``, ``block``. ``qualifier(pos)`` joins namespace/class/function
    names with ``::`` (anonymous namespaces and plain blocks are skipped).
    """

    def __init__(self, text: str):
        self.text = text
        self.events: list[tuple[int, int, tuple[str, str | None]]] = []  # (pos, +1/-1, label)
        stack: list[tuple[str, str | None]] = []
        for m in re.finditer(r"[{}]", text):
            pos = m.start()
            if m.group() == "{":
                label = self._classify(pos)
                stack.append(label)
                self.events.append((pos, 1, label))
            else:
                if stack:
                    stack.pop()
                self.events.append((pos, -1, ("", None)))
        self._pos = [e[0] for e in self.events]

    def _classify(self, pos: int) -> tuple[str, str | None]:
        t = self.text
        k = pos - 1
        while k >= 0 and t[k] not in ";{}":
            k -= 1
        # compact whitespace (blanked comments are long space runs) and bound the length: the
        # regexes below backtrack, so they must only ever see a short head
        head = re.sub(r"\s+", " ", t[max(k + 1, pos - 4000):pos]).strip()[-400:]
        if not head:
            return ("block", None)
        tail = head[-1]
        if tail in "=,([" or head.endswith(("return", "=")):
            return ("block", None)
        m = _HEAD_NS.search(head)
        if m:
            name = re.sub(r"\s+", "", m.group(1)) or None
            return ("ns", name)
        if re.search(r"\benum\b", head):
            m = _HEAD_ENUM.search(head)
            return ("enum", m.group(1) if m else None)
        m = _HEAD_CLS.search(head)
        if m and "(" not in head[m.start():].split(":", 1)[0]:
            return ("class", re.sub(r"\s+", "", m.group(2)))
        m = _HEAD_FN.search(head)
        if m:
            name = re.sub(r"(^|::)operator(?=\S)", r"\1operator ", re.sub(r"\s+", "", m.group(1)))
            name = re.sub(r"<[^()]*>$", "", name)
            if name.split("::")[-1] not in _CTRL and not head.rstrip().endswith("]"):
                return ("fn", name)
        return ("block", None)

    def chain(self, pos: int) -> list[tuple[str, str | None]]:
        """Scopes enclosing ``pos`` (outermost first)."""
        i = bisect.bisect_left(self._pos, pos)
        stack: list[tuple[str, str | None]] = []
        for p, d, label in self.events[:i]:
            if d > 0:
                stack.append(label)
            elif stack:
                stack.pop()
        return stack

    def qualifier(self, pos: int, *, functions: bool = True) -> str:
        parts: list[str] = []
        for kind, name in self.chain(pos):
            if not name:
                continue
            if kind in ("ns", "class") or (functions and kind == "fn"):
                if kind == "fn" and "::" in name:
                    parts = [name]  # out-of-class member definition: already qualified
                else:
                    parts.append(name)
        return "::".join(parts)

    def qualifiers(self, positions: Iterable[int], *, functions: bool = True) -> list[str]:
        """``qualifier`` for many sorted positions in one sweep."""
        out: list[str] = []
        stack: list[tuple[str, str | None]] = []
        ev = iter(self.events)
        cur = next(ev, None)
        for pos in positions:
            while cur is not None and cur[0] < pos:
                if cur[1] > 0:
                    stack.append(cur[2])
                elif stack:
                    stack.pop()
                cur = next(ev, None)
            parts: list[str] = []
            for kind, name in stack:
                if not name:
                    continue
                if kind in ("ns", "class") or (functions and kind == "fn"):
                    if kind == "fn" and "::" in name:
                        parts = [name]
                    else:
                        parts.append(name)
            out.append("::".join(parts))
        return out


def iter_calls(text: str, names_re: str) -> Iterable[tuple[re.Match, int, int]]:
    """``(match, args_start, args_end)`` for calls ``NAME<...>(args)`` in blanked text."""
    pat = re.compile(r"\b(" + names_re + r")\s*(<)?")
    for m in pat.finditer(text):
        j = m.end()
        if m.group(2):
            k = match_close(text, j - 1)
            if k < 0:
                continue
            j = k
        while j < len(text) and text[j] in " \t\r\n":
            j += 1
        if j >= len(text) or text[j] != "(":
            continue
        k = match_close(text, j, angle=False)
        if k < 0:
            continue
        yield m, j + 1, k - 1


def scan(text: str, pattern: re.Pattern, fn: Callable[[re.Match], object]) -> list:
    """Apply ``fn`` to every match, dropping ``None`` results."""
    out = []
    for m in pattern.finditer(text):
        r = fn(m)
        if r is not None:
            out.append(r)
    return out
