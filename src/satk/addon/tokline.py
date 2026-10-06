"""Token-preserving text lines: change one field of a data line and keep every other byte.

GTA data files are hand-aligned with tabs and spaces (``handling.cfg``, ``weapon.dat``) or commas
(``*.ide``, ``carcols.dat``). :class:`TokLine` splits a line into tokens and the separators between
them, so a line that is written back unchanged is byte-identical and a patched line only differs in
the patched tokens. :func:`fmt_number` formats a new value in the style of the value it replaces
(``1400.0`` -> ``1500.0``, ``0.70`` -> ``0.80``, ``C04000`` -> ``C04001``).

Stdlib only.
"""

from __future__ import annotations

import math
import re
import struct
from dataclasses import dataclass

__all__ = ["TokLine", "fmt_number", "f32", "decimals", "parse_typed", "split_eol"]

_SPACE_TOK = re.compile(r"\S+")
_COMMA_TOK = re.compile(r"[^\s,]+")
_PLAIN_DEC = re.compile(r"^[+-]?(\d*)(?:\.(\d*))?$")
_FLOAT = re.compile(r"^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$")
_INT = re.compile(r"^[+-]?\d+$")
_HEX = re.compile(r"^(?:0[xX])?[0-9A-Fa-f]+$")


def split_eol(raw: str) -> tuple[str, str]:
    """``"abc\\r\\n"`` -> ``("abc", "\\r\\n")``."""
    body = raw.rstrip("\r\n")
    return body, raw[len(body):]


@dataclass
class TokLine:
    """A line as ``parts = [sep0, tok0, sep1, tok1, ..., sepN]`` plus a trailing comment and end of line.

    ``commas=True`` treats commas as separators (IDE, carcols, carmods, cargrp); the comment starts at
    the first of ``comment`` characters.
    """

    parts: list[str]
    tail: str = ""
    eol: str = ""

    @classmethod
    def parse(cls, raw: str, *, commas: bool = False, comment: str = "") -> "TokLine":
        body, eol = split_eol(raw)
        cut = len(body)
        for ch in comment:
            i = body.find(ch)
            if 0 <= i < cut:
                cut = i
        text, tail = body[:cut], body[cut:]
        rx = _COMMA_TOK if commas else _SPACE_TOK
        parts: list[str] = []
        pos = 0
        for m in rx.finditer(text):
            parts.append(text[pos:m.start()])
            parts.append(m.group(0))
            pos = m.end()
        parts.append(text[pos:])
        return cls(parts, tail, eol)

    @property
    def toks(self) -> list[str]:
        return self.parts[1::2]

    def __len__(self) -> int:
        return len(self.parts) // 2

    def get(self, i: int) -> str:
        return self.parts[2 * i + 1]

    def set(self, i: int, text: str) -> None:
        if not text or any(c.isspace() for c in text):
            raise ValueError(f"token {text!r} is empty or has spaces")
        self.parts[2 * i + 1] = text

    def insert_after(self, i: int, sep: str, text: str) -> None:
        """Insert ``sep`` + ``text`` right after token ``i`` (the separator before the next token is kept)."""
        at = 2 * i + 2
        self.parts[at:at] = [sep, text]

    def text(self, *, eol: bool = True) -> str:
        return "".join(self.parts) + self.tail + (self.eol if eol else "")

    def copy(self) -> "TokLine":
        return TokLine(list(self.parts), self.tail, self.eol)


def f32(x: float) -> float:
    """``x`` as the engine stores it (32-bit float)."""
    try:
        return struct.unpack("<f", struct.pack("<f", float(x)))[0]
    except OverflowError:
        return math.copysign(math.inf, x)


def decimals(text: str) -> int | None:
    """Digits after the decimal point of a plain decimal (``"0.70"`` -> 2, ``"5"`` -> 0); ``None`` otherwise."""
    m = _PLAIN_DEC.match(text.strip())
    if not m or not (m.group(1) or m.group(2)):
        return None
    return len(m.group(2) or "") if "." in text else 0


def parse_typed(text: str, typ: str):
    """Parse a value by type code: ``f`` float, ``i`` int, ``x`` hex int, ``c`` one character, ``s`` word."""
    t = text.strip()
    if typ == "f":
        if not _FLOAT.match(t):
            raise ValueError(f"not a number: {text!r}")
        return float(t)
    if typ == "i":
        if _INT.match(t):
            return int(t)
        if _FLOAT.match(t) and float(t).is_integer():
            return int(float(t))
        raise ValueError(f"not an integer: {text!r}")
    if typ == "x":
        if not _HEX.match(t):
            raise ValueError(f"not a hex number: {text!r}")
        return int(t, 16)
    if typ == "c":
        if len(t) != 1:
            raise ValueError(f"expected one character, got {text!r}")
        return t.upper()
    if not t or any(c.isspace() for c in t):
        raise ValueError(f"expected one word, got {text!r}")
    return t


def fmt_number(new, typ: str, old: str | None = None, given: str | None = None) -> str:
    """Text for ``new`` in the style of the token ``old`` it replaces.

    Floats keep at least the decimals of ``old`` and of the user's ``given`` text (``1500`` replacing
    ``1400.0`` -> ``1500.0``); hex keeps the letter case of ``old``; ints and chars are plain.
    """
    if old is not None:
        try:
            same = parse_typed(old, typ) == new if typ != "f" else f32(parse_typed(old, typ)) == f32(float(new))
        except ValueError:
            same = False
        if same:
            return old                     # an unchanged value keeps its text ('08.0', '0431')
    if typ == "x":
        s = format(int(new), "X")
        if old and any(c in "abcdef" for c in old) and not any(c in "ABCDEF" for c in old):
            s = s.lower()
        if old and len(old) > len(s) and old.startswith("0"):
            s = s.zfill(len(old))
        return s
    if typ == "i":
        return str(int(new))
    if typ == "c" or typ == "s":
        return str(new)
    v = float(new)
    d_old = decimals(old) if old else None
    d_given = decimals(given) if given else None
    if given and d_given is None:          # exponent notation from the user: keep it as typed
        return given.strip()
    if d_old is None and d_given is None:
        r = repr(v)
        return r if "e" not in r else format(v, ".9g")
    d = max(d_old or 0, d_given or 0)
    if d == 0 and old and "." in old:
        d = 1
    s = f"{v:.{d}f}"
    if float(s) != v:                       # value needs more digits than the old style has
        s = repr(v)
    return s
