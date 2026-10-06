"""A small, strict YAML subset for recipe files (stdlib only; satk has no YAML dependency).

Supported: block mappings and sequences by indentation (spaces only), ``- key: value`` items, flow
collections (``[a, b]``, ``{k: v}``, nested, may span lines), plain / single-quoted / double-quoted scalars,
``|`` and ``>`` block scalars (with ``-``/``+`` chomping), ``#`` comments and a leading ``---``.
Plain scalars resolve like YAML 1.2 core: ``null ~`` -> ``None``, ``true false`` -> bool, integers
(``0x`` hex too), floats; everything else is a string (``yes``/``no`` stay strings).

Not supported (an error names the line): anchors and aliases, tags, several documents, complex keys,
plain scalars continued on the next line (quote them or use ``|``). Duplicate keys and tabs in the
indentation are errors.

Example::

    from satk.batch.yamlite import loads
    loads("steps:\\n  - id: a\\n    args: {limit: 5}\\n")   # {'steps': [{'id': 'a', 'args': {'limit': 5}}]}
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..core.errors import SatkError

__all__ = ["loads", "YamlError"]

_HINT = ("recipes use a YAML subset: mappings, lists, plain/quoted scalars, [flow] {flow}, | and > blocks; "
         "no anchors, tags or multi-line plain scalars (or write the recipe as JSON)")
_INT = re.compile(r"^[-+]?(?:0|[1-9][0-9_]*)$")
_HEX = re.compile(r"^0x[0-9a-fA-F_]+$")
_OCT = re.compile(r"^0o[0-7_]+$")
_FLOAT = re.compile(r"^[-+]?(?:\d[\d_]*\.\d*|\.\d+|\d[\d_]*)(?:[eE][-+]?\d+)?$")
_ESC = {"0": "\0", "a": "\a", "b": "\b", "t": "\t", "\t": "\t", "n": "\n", "v": "\v", "f": "\f", "r": "\r",
        "e": "\x1b", " ": " ", '"': '"', "/": "/", "\\": "\\", "N": "\x85", "_": "\xa0"}


class YamlError(SatkError):
    """``BAD_PARAMS`` with the source and line of the problem."""

    def __init__(self, source: str, line: int, msg: str):
        super().__init__("BAD_PARAMS", f"{source}:{line}: {msg}", hint=_HINT, data={"line": line})


@dataclass
class _Line:
    no: int        # 1-based line number
    indent: int    # leading spaces
    text: str      # content without the indentation and the trailing comment
    raw: str       # the whole physical line (block scalars)


def _strip_comment(s: str) -> str:
    """Drop a ``#`` comment (at the start or after whitespace) outside quotes."""
    quote = ""
    i = 0
    while i < len(s):
        ch = s[i]
        if quote:
            if ch == quote:
                if quote == "'" and i + 1 < len(s) and s[i + 1] == "'":
                    i += 2
                    continue
                quote = ""
            elif ch == "\\" and quote == '"':
                i += 2
                continue
        elif ch in "'\"" and (i == 0 or s[i - 1] in " \t[{,:-"):
            quote = ch
        elif ch == "#" and (i == 0 or s[i - 1] in " \t"):
            return s[:i].rstrip()
        i += 1
    return s.rstrip()


class _Parser:
    def __init__(self, text: str, source: str):
        self.src = source
        self.raw = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        self.lines: list[_Line] = []
        for no, raw in enumerate(self.raw, 1):
            body = raw.lstrip(" ")
            if body.startswith("\t") or (body and raw[: len(raw) - len(body)].count("\t")):
                raise YamlError(source, no, "tab in the indentation (use spaces)")
            text_ = _strip_comment(body)
            if not text_:
                continue
            self.lines.append(_Line(no, len(raw) - len(body), text_, raw))

    def err(self, line: int, msg: str) -> YamlError:
        return YamlError(self.src, line, msg)

    # ------------------------------------------------------------------ document

    def document(self) -> Any:
        lines = self.lines
        if lines and lines[0].text == "---" and lines[0].indent == 0:
            lines.pop(0)
        if lines and lines[-1].text in ("...",) and lines[-1].indent == 0:
            lines.pop()
        for ln in lines:
            if ln.indent == 0 and ln.text in ("---", "..."):
                raise self.err(ln.no, "several documents in one file are not supported")
        if not lines:
            return None
        value, i = self.node(0, lines[0].indent)
        if i < len(lines):
            raise self.err(lines[i].no, "unexpected indentation or content after the document")
        return value

    def node(self, i: int, indent: int) -> tuple[Any, int]:
        ln = self.lines[i]
        if ln.indent != indent:
            raise self.err(ln.no, f"bad indentation ({ln.indent} spaces, expected {indent})")
        if ln.text == "-" or ln.text.startswith("- "):
            return self.sequence(i, indent)
        if ln.text == "?" or ln.text.startswith("? "):
            raise self.err(ln.no, "complex keys ('? key') are not supported")
        if self.split_key(ln) is not None:
            return self.mapping(i, indent)
        value, j = self.inline(i, ln.text, ln.no)
        return value, j

    # ------------------------------------------------------------------ collections

    def split_key(self, ln: _Line) -> tuple[str, str] | None:
        """``(key, rest)`` when the line is ``key: rest`` (rest may be empty), else ``None``."""
        t = ln.text
        if t[0] in "[{" or t.startswith(("- ", "|", ">")) or t == "-":
            return None
        if t[0] in "'\"":
            q = t[0]
            j = 1
            while j < len(t):
                if t[j] == q:
                    if q == "'" and j + 1 < len(t) and t[j + 1] == "'":
                        j += 2
                        continue
                    break
                if t[j] == "\\" and q == '"':
                    j += 1
                j += 1
            else:
                return None
            rest = t[j + 1:]
            m = re.match(r"\s*:(?:\s+|$)", rest)
            if not m:
                return None
            key = self.scalar(t[: j + 1], ln.no)
            return str(key), rest[m.end():].strip()
        m = re.match(r"^([^\s#'\"\[\]{},:][^#]*?|[^\s#'\"\[\]{},]):(?:\s+|$)", t)
        if not m:
            return None
        key = m.group(1).rstrip()
        if key in ("?",) or key.startswith(("&", "*", "!")):
            raise self.err(ln.no, f"unsupported key syntax {key!r} (anchors, aliases, tags, complex keys)")
        return key, t[m.end():].strip()

    def mapping(self, i: int, indent: int) -> tuple[dict, int]:
        out: dict[str, Any] = {}
        lines = self.lines
        while i < len(lines):
            ln = lines[i]
            if ln.indent < indent:
                break
            if ln.indent > indent:
                raise self.err(ln.no, "bad indentation inside a mapping")
            kv = self.split_key(ln)
            if kv is None:
                if ln.text == "-" or ln.text.startswith("- "):
                    raise self.err(ln.no, "a list item where a 'key: value' line was expected")
                raise self.err(ln.no, f"expected 'key: value', got {ln.text[:40]!r}")
            key, rest = kv
            if key in out:
                raise self.err(ln.no, f"duplicate key {key!r}")
            if rest == "":
                j = i + 1
                if j < len(lines) and lines[j].indent > indent:
                    out[key], i = self.node(j, lines[j].indent)
                elif j < len(lines) and lines[j].indent == indent and (lines[j].text == "-"
                                                                       or lines[j].text.startswith("- ")):
                    out[key], i = self.sequence(j, indent)  # "key:\n- a" (same indentation)
                else:
                    out[key], i = None, j
            elif rest[0] in "|>":
                out[key], i = self.block_scalar(i, rest, indent)
            else:
                out[key], i = self.inline(i, rest, ln.no, owner_indent=indent)
        return out, i

    def sequence(self, i: int, indent: int) -> tuple[list, int]:
        out: list[Any] = []
        lines = self.lines
        while i < len(lines):
            ln = lines[i]
            if ln.indent < indent:
                break
            if ln.indent > indent:
                raise self.err(ln.no, "bad indentation inside a list")
            if not (ln.text == "-" or ln.text.startswith("- ")):
                break  # a mapping key at the same indentation ends a "key:\n- a" list
            rest = ln.text[1:].lstrip(" ")
            if rest == "":
                j = i + 1
                if j < len(lines) and lines[j].indent > indent:
                    v, i = self.node(j, lines[j].indent)
                else:
                    v, i = None, j
                out.append(v)
                continue
            child = ln.indent + (len(ln.text) - len(rest))
            virt = _Line(ln.no, child, rest, ln.raw)
            if rest == "-" or rest.startswith("- ") or self.split_key(virt) is not None:
                # "- key: v" (+ continuation lines at the key's column) or "- - nested"
                at, saved = i, lines[i]
                lines[at] = virt
                try:
                    v, i = self.node(at, child)
                finally:
                    lines[at] = saved
                out.append(v)
            elif rest[0] in "|>":
                v, i = self.block_scalar(i, rest, indent)
                out.append(v)
            else:
                v, i = self.inline(i, rest, ln.no, owner_indent=indent)
                out.append(v)
        return out, i

    # ------------------------------------------------------------------ scalars

    def block_scalar(self, i: int, head: str, indent: int) -> tuple[str, int]:
        ln = self.lines[i]
        m = re.fullmatch(r"([|>])([-+]?)(\d?)([-+]?)", head)
        if not m:
            raise self.err(ln.no, f"bad block scalar header {head!r}")
        style, chomp = m.group(1), (m.group(2) or m.group(4))
        start_raw = ln.no  # raw lines after the header
        body: list[str] = []
        block_indent: int | None = int(m.group(3)) + indent if m.group(3) else None
        k = start_raw
        while k < len(self.raw):
            raw = self.raw[k]
            stripped = raw.lstrip(" ")
            ind = len(raw) - len(stripped)
            if stripped == "":
                body.append("")
                k += 1
                continue
            if block_indent is None:
                if ind <= indent:
                    break
                block_indent = ind
            if ind < block_indent:
                break
            body.append(raw[block_indent:])
            k += 1
        # skip the structural lines the block consumed
        j = i + 1
        while j < len(self.lines) and self.lines[j].no <= k:
            j += 1
        while body and body[-1] == "" and chomp != "+":
            body.pop()
        if style == "|":
            text = "\n".join(body)
        else:
            parts: list[str] = []
            for b in body:
                if b == "":
                    parts.append("\n")
                elif parts and not parts[-1].endswith("\n"):
                    parts.append(" " + b)
                else:
                    parts.append(b)
            text = "".join(parts)
        if chomp != "-" and body:
            text += "\n"
        return text, j

    def inline(self, i: int, text: str, no: int, owner_indent: int | None = None) -> tuple[Any, int]:
        """A scalar or flow collection that starts on line ``i`` (a flow value may continue on later lines)."""
        j = i + 1
        if text[0] in "[{":
            buf = text
            while True:
                try:
                    value, pos = _Flow(buf, self, no).parse()
                except _Incomplete:
                    if j >= len(self.lines):
                        raise self.err(no, "unclosed flow collection") from None
                    buf += " " + self.lines[j].text
                    j += 1
                    continue
                if buf[pos:].strip():
                    raise self.err(no, f"unexpected text after a flow collection: {buf[pos:].strip()[:30]!r}")
                return value, j
        if text.startswith(("&", "*", "!")):
            raise self.err(no, "anchors, aliases and tags are not supported")
        value = self.scalar(text, no)
        if j < len(self.lines) and owner_indent is not None and self.lines[j].indent > owner_indent:
            nxt = self.lines[j]
            if not (nxt.text.startswith("- ") or nxt.text == "-" or self.split_key(nxt) is not None):
                raise self.err(nxt.no, "a plain scalar continues on the next line: quote it or use '|'")
            raise self.err(nxt.no, "bad indentation: a value already ends the line above")
        return value, j

    def scalar(self, t: str, no: int) -> Any:
        t = t.strip()
        if not t:
            return None
        if t[0] == '"':
            if len(t) < 2 or not t.endswith('"') or _unescaped_quote(t[1:-1]):
                raise self.err(no, f"bad double-quoted string {t[:40]!r}")
            return _unescape(t[1:-1], self, no)
        if t[0] == "'":
            inner = t[1:-1]
            if len(t) < 2 or not t.endswith("'") or "'" in inner.replace("''", ""):
                raise self.err(no, f"bad single-quoted string {t[:40]!r}")
            return inner.replace("''", "'")
        return _plain(t)


def _unescaped_quote(s: str) -> bool:
    i = 0
    while i < len(s):
        if s[i] == "\\":
            i += 2
            continue
        if s[i] == '"':
            return True
        i += 1
    return False


def _unescape(s: str, p: _Parser, no: int) -> str:
    out: list[str] = []
    i = 0
    while i < len(s):
        ch = s[i]
        if ch != "\\":
            out.append(ch)
            i += 1
            continue
        if i + 1 >= len(s):
            raise p.err(no, "a backslash ends a double-quoted string")
        e = s[i + 1]
        if e in _ESC:
            out.append(_ESC[e])
            i += 2
        elif e in "xuU":
            n = {"x": 2, "u": 4, "U": 8}[e]
            hexs = s[i + 2: i + 2 + n]
            if len(hexs) != n or not all(c in "0123456789abcdefABCDEF" for c in hexs):
                raise p.err(no, f"bad escape \\{e}{hexs}")
            out.append(chr(int(hexs, 16)))
            i += 2 + n
        else:
            raise p.err(no, f"unknown escape \\{e} (write \\\\ for a backslash, or use single quotes)")
    return "".join(out)


def _plain(t: str) -> Any:
    low = t.lower()
    if low in ("null", "~"):
        return None
    if low == "true":
        return True
    if low == "false":
        return False
    if _INT.match(t):
        return int(t.replace("_", ""))
    if _HEX.match(t):
        return int(t[2:].replace("_", ""), 16)
    if _OCT.match(t):
        return int(t[2:].replace("_", ""), 8)
    if _FLOAT.match(t) and any(c.isdigit() for c in t):
        return float(t.replace("_", ""))
    if low in (".inf", "+.inf"):
        return float("inf")
    if low == "-.inf":
        return float("-inf")
    return t


class _Incomplete(Exception):
    pass


class _Flow:
    """Flow collections: ``[a, "b", {k: v}]`` and ``{k: v, l: [1, 2]}``."""

    def __init__(self, s: str, p: _Parser, no: int):
        self.s, self.p, self.no, self.i = s, p, no, 0

    def parse(self) -> tuple[Any, int]:
        v = self.value(in_map=False)
        return v, self.i

    def ws(self) -> None:
        while self.i < len(self.s) and self.s[self.i] in " \t":
            self.i += 1

    def peek(self) -> str:
        self.ws()
        if self.i >= len(self.s):
            raise _Incomplete
        return self.s[self.i]

    def value(self, in_map: bool, key: bool = False) -> Any:
        c = self.peek()
        if c == "[":
            return self.seq()
        if c == "{":
            return self.map()
        if c in "'\"":
            return self.quoted()
        if c in "&*!":
            raise self.p.err(self.no, "anchors, aliases and tags are not supported")
        start = self.i
        stop = ",]}" + (":" if key else "")
        while self.i < len(self.s):
            ch = self.s[self.i]
            if ch == "$" and self.s.startswith("${", self.i):  # a recipe reference is one token: {k: ${v}}
                end = self.s.find("}", self.i)
                if end < 0:
                    raise _Incomplete
                self.i = end + 1
                continue
            if ch in stop and not (ch == ":" and self.i + 1 < len(self.s) and self.s[self.i + 1] not in " ,]}"):
                break
            if ch == "#" and self.i > start and self.s[self.i - 1] == " ":
                break
            self.i += 1
        return _plain(self.s[start:self.i].strip())

    def quoted(self) -> Any:
        q = self.s[self.i]
        j = self.i + 1
        while j < len(self.s):
            if self.s[j] == q:
                if q == "'" and j + 1 < len(self.s) and self.s[j + 1] == "'":
                    j += 2
                    continue
                break
            if self.s[j] == "\\" and q == '"':
                j += 1
            j += 1
        else:
            raise _Incomplete
        tok = self.s[self.i: j + 1]
        self.i = j + 1
        return self.p.scalar(tok, self.no)

    def seq(self) -> list:
        self.i += 1
        out: list = []
        while True:
            if self.peek() == "]":
                self.i += 1
                return out
            out.append(self.value(in_map=False))
            c = self.peek()
            if c == ",":
                self.i += 1
            elif c != "]":
                raise self.p.err(self.no, f"expected ',' or ']' in a flow list, got {c!r}")

    def map(self) -> dict:
        self.i += 1
        out: dict = {}
        while True:
            if self.peek() == "}":
                self.i += 1
                return out
            k = self.value(in_map=True, key=True)
            if self.peek() != ":":
                raise self.p.err(self.no, f"expected ':' after the key {k!r} in a flow mapping")
            self.i += 1
            if self.peek() in ",}":
                v = None
            else:
                v = self.value(in_map=True)
            ks = "" if k is None else (k if isinstance(k, str) else str(k).lower() if isinstance(k, bool) else str(k))
            if ks in out:
                raise self.p.err(self.no, f"duplicate key {ks!r}")
            out[ks] = v
            c = self.peek()
            if c == ",":
                self.i += 1
            elif c != "}":
                raise self.p.err(self.no, f"expected ',' or '}}' in a flow mapping, got {c!r}")


def loads(text: str, *, source: str = "<recipe>") -> Any:
    """Parse ``text`` (the YAML subset above); ``BAD_PARAMS`` with ``source:line`` on errors."""
    if text.startswith("﻿"):
        text = text[1:]
    return _Parser(text, source).document()
