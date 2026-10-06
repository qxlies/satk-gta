"""Lua 5.1 lexer and parser with scope resolution (MTA:SA runs Lua 5.1.5). Stdlib only, no I/O.

satk's own implementation of the Lua 5.1 grammar. Syntax errors use the wording and the token rules of the
reference compiler (``luaL_loadbuffer`` of Lua 5.1.5 with ``LUA_COMPAT_LSTR=1`` and ``LUA_COMPAT_VARARG``,
the configuration MTA:SA ships), so a message here reads like the ``Loading script failed: ...`` line of an MTA
log: ``'end' expected (to close 'function' at line 3) near '<eof>'``. The limits of that compiler are checked
too: 200 syntax levels, 200 active local variables and 60 upvalues per function.

Names are resolved while parsing, the way the compiler does it: every :class:`Name` knows its
:class:`LocalVar` (a local or an upvalue) or is a global (``var is None``). :func:`parse` returns a
:class:`Chunk` with the tree, every global name, every function and every local; :func:`walk` iterates a tree
without recursion.
"""

from __future__ import annotations

import bisect
import re
import sys

__all__ = ["KEYWORDS", "LuaSyntaxError", "Source", "Chunk", "LocalVar", "parse", "tokenize", "walk",
           "Node", "Nil", "TrueE", "FalseE", "Num", "Str", "Vararg", "Func", "Table", "BinOp", "UnOp", "Name",
           "Index", "Call", "MCall", "Paren", "Local", "Assign", "CallStat", "Do", "While", "Repeat", "If",
           "NumFor", "GenFor", "FuncStat", "LocalFunc", "Return", "Break", "MAX_LEVELS", "MAX_VARS",
           "MAX_UPVALUES"]

KEYWORDS = frozenset("and break do else elseif end false for function if in local nil not or repeat return "
                     "then true until while".split())
MAX_LEVELS = 200        # LUAI_MAXCCALLS
MAX_VARS = 200          # LUAI_MAXVARS
MAX_UPVALUES = 60       # LUAI_MAXUPVALUES

_NL = re.compile(r"\r\n|\n\r|\n|\r")
_TOK = re.compile(r"""
 (?P<ws>[ \t\f\v\r\n]+)
|(?P<name>[A-Za-z_][A-Za-z0-9_]*)
|(?P<num>(?:[0-9]|\.[0-9])[0-9.]*(?:[Ee][+-]?)?[A-Za-z0-9_]*)
|(?P<cmt>--)
|(?P<str>["'])
|(?P<lb>\[=*\[|\[=+)
|(?P<op>\.\.\.|\.\.|==|~=|<=|>=|[-+*/%^\#<>=(){}\[\];:,.])
|(?P<other>[\s\S])
""", re.X)
_LONG_OPEN = re.compile(r"\[(=*)\[")
_TO_EOL = re.compile(r"[^\r\n]*")
_STR_BODY = {'"': re.compile(r'[^"\\\r\n]*'), "'": re.compile(r"[^'\\\r\n]*")}
_DEC = re.compile(r"(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z")
_HEX = re.compile(r"0[xX](?:[0-9A-Fa-f]+\.?[0-9A-Fa-f]*|\.[0-9A-Fa-f]+)(?:[pP][+-]?[0-9]+)?\Z")
_ESC = {"a": "\a", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v"}
#: Escapes Lua 5.2+ understands; Lua 5.1 keeps the letter and drops the backslash.
_ESC52 = {"x": "\\xXX (hex byte)", "z": "\\z (skip whitespace)", "u": "\\u{XXX} (UTF-8 code point)"}
_TOKEN_NAMES = {"name": "<name>", "string": "<string>", "number": "<number>", "<eof>": "<eof>"}


class LuaSyntaxError(Exception):
    """A compile error: ``msg`` (+ ``near`` token text) at ``line``:``col`` (1-based)."""

    def __init__(self, msg: str, pos: int, line: int, col: int, near: str | None = None):
        super().__init__(msg)
        self.msg = msg
        self.pos = pos
        self.line = line
        self.col = col
        self.near = near

    @property
    def text(self) -> str:
        return f"{self.msg} near '{self.near}'" if self.near is not None else self.msg

    def __str__(self) -> str:  # pragma: no cover - debugging aid
        return f"{self.line}:{self.col}: {self.text}"


class Source:
    """Text plus line starts (Lua counts ``\\r\\n``, ``\\n\\r``, ``\\n`` and ``\\r`` as one newline)."""

    __slots__ = ("text", "starts")

    def __init__(self, text: str):
        self.text = text
        self.starts = [0] + [m.end() for m in _NL.finditer(text)]

    def line(self, pos: int) -> int:
        return bisect.bisect_right(self.starts, pos)

    def linecol(self, pos: int) -> tuple[int, int]:
        i = bisect.bisect_right(self.starts, pos)
        return i, pos - self.starts[i - 1] + 1

    def line_text(self, line: int) -> str:
        if line < 1 or line > len(self.starts):
            return ""
        a = self.starts[line - 1]
        b = self.starts[line] if line < len(self.starts) else len(self.text)
        return self.text[a:b].rstrip("\r\n")


# --------------------------------------------------------------------------- lexer


def tokenize(src: Source) -> tuple[list[tuple], list[tuple], list[tuple]]:
    """``(tokens, notes, comments)`` of a chunk.

    A token is ``(type, value, start, end)``: type ``name``/``number``/``string``/``<eof>``, a keyword or the
    operator text (an unknown character is its own type, the parser rejects it). A lexer error ends the list
    with an ``("<error>", LuaSyntaxError, pos, pos)`` token: the reference lexer is lazy, so the parser raises it
    only when it reaches that token (an earlier grammar error wins). ``notes`` are lexer warnings
    ``(code, pos, msg)``; ``comments`` are ``(pos, text)`` of short comments (for ``satk:ignore``).
    """
    text = src.text
    n = len(text)
    toks: list[tuple] = []
    notes: list[tuple] = []
    comments: list[tuple] = []
    append = toks.append
    match = _TOK.match
    p = 0

    def err(msg: str, pos: int, near: str | None, line_pos: int | None = None) -> LuaSyntaxError:
        ln, col = src.linecol(pos)
        if line_pos is not None:
            ln2 = src.line(line_pos)
            if ln2 != ln:
                ln, col = ln2, 1
        return LuaSyntaxError(msg, pos, ln, col, near)

    def long_body(start: int, level: int, is_comment: bool) -> tuple[str, int]:
        """Content and end of a long bracket opened at ``start`` (the first ``[``)."""
        body = start + level + 2
        close = "]" + "=" * level + "]"
        j = text.find(close, body)
        if level == 0:
            nest = text.find("[[", body, j if j >= 0 else n)
            if nest >= 0:
                raise err("nesting of [[...]] is deprecated", nest, "[")
        if j < 0:
            raise err("unfinished long comment" if is_comment else "unfinished long string", n, "<eof>")
        content = text[body:j]
        m = _NL.match(content)
        if m:
            content = content[m.end():]
        if "\r" in content:
            content = _NL.sub("\n", content)
        return content, j + len(close)

    try:
        while True:
            m = match(text, p)
            if m is None:
                append(("<eof>", None, n, n))
                break
            kind = m.lastgroup
            s = p
            p = m.end()
            if kind == "ws":
                continue
            if kind == "name":
                v = m.group()
                append((v if v in KEYWORDS else "name", v, s, p))
            elif kind == "op":
                v = m.group()
                append((v, v, s, p))
            elif kind == "num":
                v = m.group()
                if not (_DEC.match(v) or _HEX.match(v)):
                    raise err("malformed number", s, v)
                append(("number", v, s, p))
            elif kind == "str":
                q = m.group()
                buf: list[str] = []
                body = _STR_BODY[q]
                i = p
                while True:
                    bm = body.match(text, i)
                    buf.append(bm.group())
                    i = bm.end()
                    if i >= n:
                        raise err("unfinished string", n, "<eof>")
                    c = text[i]
                    if c == q:
                        i += 1
                        break
                    if c in "\r\n":
                        raise err("unfinished string", s, q + "".join(buf), i)
                    i += 1                          # backslash
                    if i >= n:
                        continue                    # unfinished string at the next round
                    c = text[i]
                    if c in _ESC:
                        buf.append(_ESC[c])
                        i += 1
                    elif c in "\r\n":
                        nm = _NL.match(text, i)
                        buf.append("\n")
                        i = nm.end()
                    elif "0" <= c <= "9":
                        j = i
                        while j < n and j - i < 3 and "0" <= text[j] <= "9":
                            j += 1
                        code = int(text[i:j])
                        if code > 255:
                            raise err("escape sequence too large", s, q + "".join(buf), j)
                        buf.append(chr(code))
                        i = j
                    else:
                        if c in _ESC52:
                            notes.append(("ESCAPE_52", i - 1, f"'\\{c}' is a Lua 5.2+ escape {_ESC52[c]}; Lua 5.1 "
                                                              f"(MTA) reads it as the plain letter '{c}'"))
                        buf.append(c)
                        i += 1
                p = i
                append(("string", "".join(buf), s, p))
            elif kind == "cmt":
                lm = _LONG_OPEN.match(text, p)
                if lm:
                    _content, p = long_body(p, len(lm.group(1)), True)
                else:
                    em = _TO_EOL.match(text, p)
                    c = em.group()
                    if "satk:" in c:
                        comments.append((s, c))
                    p = em.end()
            elif kind == "lb":
                v = m.group()
                if not v.endswith("[") or len(v) < 2:
                    raise err("invalid long string delimiter", s, v)
                content, p = long_body(s, len(v) - 2, False)
                append(("string", content, s, p))
            else:  # other: a character the grammar has no use for; the parser reports it
                v = m.group()
                append((v, v, s, p))
    except LuaSyntaxError as e:
        append(("<error>", e, e.pos, e.pos))
    return toks, notes, comments


# --------------------------------------------------------------------------- tree


class Node:
    __slots__ = ("pos",)
    fields: tuple[str, ...] = ()

    def __init__(self, pos: int):
        self.pos = pos


class Nil(Node):
    __slots__ = ()


class TrueE(Node):
    __slots__ = ()


class FalseE(Node):
    __slots__ = ()


class Vararg(Node):
    __slots__ = ()


class Num(Node):
    __slots__ = ("text",)

    def __init__(self, pos, text):
        self.pos, self.text = pos, text


class Str(Node):
    __slots__ = ("value",)

    def __init__(self, pos, value):
        self.pos, self.value = pos, value


class Name(Node):
    """``var`` is the :class:`LocalVar` (local or upvalue) or ``None`` (global); ``ctx`` ``r``/``w``."""

    __slots__ = ("name", "var", "ctx", "func")

    def __init__(self, pos, name, var, func):
        self.pos, self.name, self.var, self.ctx, self.func = pos, name, var, "r", func


class Index(Node):
    __slots__ = ("obj", "key", "dot")
    fields = ("obj", "key")

    def __init__(self, pos, obj, key, dot):
        self.pos, self.obj, self.key, self.dot = pos, obj, key, dot


class Call(Node):
    __slots__ = ("fn", "args", "end")
    fields = ("fn", "args")

    def __init__(self, pos, fn, args, end):
        self.pos, self.fn, self.args, self.end = pos, fn, args, end


class MCall(Node):
    __slots__ = ("obj", "name", "args", "end")
    fields = ("obj", "args")

    def __init__(self, pos, obj, name, args, end):
        self.pos, self.obj, self.name, self.args, self.end = pos, obj, name, args, end


class Paren(Node):
    __slots__ = ("expr",)
    fields = ("expr",)

    def __init__(self, pos, expr):
        self.pos, self.expr = pos, expr


class BinOp(Node):
    __slots__ = ("op", "left", "right")
    fields = ("left", "right")

    def __init__(self, pos, op, left, right):
        self.pos, self.op, self.left, self.right = pos, op, left, right


class UnOp(Node):
    __slots__ = ("op", "operand")
    fields = ("operand",)

    def __init__(self, pos, op, operand):
        self.pos, self.op, self.operand = pos, op, operand


class Table(Node):
    """``items`` = ``[(key or None, value), ...]`` (a ``Name =`` key is a :class:`Str`)."""

    __slots__ = ("items",)

    def __init__(self, pos, items):
        self.pos, self.items = pos, items


class Func(Node):
    """A function body; ``line`` = where it is defined (0 = the main chunk); ``name`` = how it was declared."""

    __slots__ = ("line", "params", "is_vararg", "body", "end", "name", "parent", "upvalues", "method")
    fields = ("body",)

    def __init__(self, pos, line, parent):
        self.pos, self.line, self.parent = pos, line, parent
        self.params: list = []
        self.is_vararg = False
        self.body: list = []
        self.end = pos
        self.name: str | None = None
        self.upvalues: dict = {}
        self.method = False


class Local(Node):
    __slots__ = ("vars", "exprs")
    fields = ("exprs",)

    def __init__(self, pos, vars, exprs):  # noqa: A002
        self.pos, self.vars, self.exprs = pos, vars, exprs


class Assign(Node):
    __slots__ = ("targets", "exprs")
    fields = ("targets", "exprs")

    def __init__(self, pos, targets, exprs):
        self.pos, self.targets, self.exprs = pos, targets, exprs


class CallStat(Node):
    __slots__ = ("call",)
    fields = ("call",)

    def __init__(self, pos, call):
        self.pos, self.call = pos, call


class Do(Node):
    __slots__ = ("body",)
    fields = ("body",)

    def __init__(self, pos, body):
        self.pos, self.body = pos, body


class While(Node):
    __slots__ = ("cond", "body")
    fields = ("cond", "body")

    def __init__(self, pos, cond, body):
        self.pos, self.cond, self.body = pos, cond, body


class Repeat(Node):
    __slots__ = ("body", "cond")
    fields = ("body", "cond")

    def __init__(self, pos, body, cond):
        self.pos, self.body, self.cond = pos, body, cond


class If(Node):
    """``tests`` = ``[(cond, body), ...]`` (if + elseifs); ``orelse`` = body or ``None``."""

    __slots__ = ("tests", "orelse")

    def __init__(self, pos, tests, orelse):
        self.pos, self.tests, self.orelse = pos, tests, orelse


class NumFor(Node):
    __slots__ = ("var", "start", "stop", "step", "body")
    fields = ("start", "stop", "step", "body")

    def __init__(self, pos, var, start, stop, step, body):
        self.pos, self.var, self.start, self.stop, self.step, self.body = pos, var, start, stop, step, body


class GenFor(Node):
    __slots__ = ("vars", "exprs", "body")
    fields = ("exprs", "body")

    def __init__(self, pos, vars, exprs, body):  # noqa: A002
        self.pos, self.vars, self.exprs, self.body = pos, vars, exprs, body


class FuncStat(Node):
    """``function a.b:c() end``: ``target`` = the Name/Index expression (``a.b.c``), ``func`` = the body."""

    __slots__ = ("target", "func")
    fields = ("target", "func")

    def __init__(self, pos, target, func):
        self.pos, self.target, self.func = pos, target, func


class LocalFunc(Node):
    __slots__ = ("var", "func")
    fields = ("func",)

    def __init__(self, pos, var, func):
        self.pos, self.var, self.func = pos, var, func


class Return(Node):
    __slots__ = ("exprs",)
    fields = ("exprs",)

    def __init__(self, pos, exprs):
        self.pos, self.exprs = pos, exprs


class Break(Node):
    __slots__ = ()


class LocalVar:
    """A local variable; ``kind`` local|param|for|function|self|arg; ``reads``/``writes`` count uses."""

    __slots__ = ("name", "pos", "kind", "func", "reads", "writes", "captured")

    def __init__(self, name, pos, kind, func):
        self.name, self.pos, self.kind, self.func = name, pos, kind, func
        self.reads = 0
        self.writes = 0
        self.captured = False


def walk(node) -> "Iterator[Node]":  # noqa: F821 - typing only
    """Every node under ``node`` (a node or a list of nodes), parents before children, without recursion."""
    stack = [node]
    pop = stack.pop
    push = stack.append
    while stack:
        x = pop()
        if x is None:
            continue
        if isinstance(x, list):
            for y in reversed(x):
                push(y)
            continue
        if isinstance(x, tuple):
            for y in reversed(x):
                if y is not None:
                    push(y)
            continue
        if not isinstance(x, Node):
            continue
        yield x
        t = type(x)
        if t is If:
            if x.orelse is not None:
                push(x.orelse)
            for c, b in reversed(x.tests):
                push(b)
                push(c)
            continue
        if t is Table:
            for k, v in reversed(x.items):
                push(v)
                if k is not None:
                    push(k)
            continue
        if t is Call or t is MCall:
            push(x.args)
            if t is Call:
                push(x.fn)
            else:
                push(x.obj)
            continue
        for f in reversed(t.fields):
            push(getattr(x, f))


# --------------------------------------------------------------------------- parser


class Chunk:
    """Result of :func:`parse`: ``body``, the main :class:`Func`, global names, functions, locals, notes."""

    __slots__ = ("src", "main", "names", "funcs", "locals", "notes", "comments")

    def __init__(self, src, main, names, funcs, locals_, notes, comments):
        self.src, self.main, self.names, self.funcs = src, main, names, funcs
        self.locals, self.notes, self.comments = locals_, notes, comments

    @property
    def body(self) -> list:
        return self.main.body


class _FS:
    """Compile-time state of one function (``FuncState``)."""

    __slots__ = ("parent", "actvar", "blocks", "node", "is_vararg")

    def __init__(self, parent, node, is_vararg):
        self.parent, self.node, self.is_vararg = parent, node, is_vararg
        self.actvar: list[LocalVar] = []
        self.blocks: list[tuple[int, bool]] = []


_BINPRI = {"+": (6, 6), "-": (6, 6), "*": (7, 7), "/": (7, 7), "%": (7, 7), "^": (10, 9), "..": (5, 4),
           "==": (3, 3), "~=": (3, 3), "<": (3, 3), "<=": (3, 3), ">": (3, 3), ">=": (3, 3),
           "and": (2, 2), "or": (1, 1)}
_UNARY = frozenset(("not", "-", "#"))
_UNARY_PRI = 8
_BLOCK_FOLLOW = frozenset(("else", "elseif", "end", "<eof>", "until"))


class _Parser:
    def __init__(self, src: Source, toks: list[tuple]):
        self.src = src
        self.toks = toks
        self.i = 0
        self.t = toks[0]
        self.prev_end = 0
        self.prev: tuple = toks[0]
        self.prev_la: int | None = None
        self.la_line: int | None = None     # line of a token read ahead (constructor keys)
        self.level = 0
        self.fs: _FS | None = None
        self.names: list[Name] = []
        self.funcs: list[Func] = []
        self.locals: list[LocalVar] = []

    # -- tokens -------------------------------------------------------------------------

    def next(self) -> None:
        self.prev = self.t
        self.prev_la = self.la_line
        self.la_line = None
        self.prev_end = self.t[3]
        self.i += 1
        self.t = self.toks[self.i]
        if self.t[0] == "<error>":
            raise self.t[1]

    def linenumber(self) -> int:
        """``ls->linenumber``: where the reference lexer is (after the current or the read-ahead token)."""
        return self.la_line if self.la_line is not None else self.tok_line(self.t)

    def tok_line(self, t: tuple) -> int:
        """Line the reference lexer is on after reading ``t`` (the line of its last character)."""
        return self.src.line(t[3] - 1 if t[3] > t[2] else t[2])

    def near(self, t: tuple) -> str:
        k = t[0]
        if k in ("name", "number", "string"):
            s = self.src.text[t[2]:t[3]]
            if k == "string":                               # the reference prints its buffer
                if s[:1] in ("'", '"'):
                    return s[0] + t[1] + s[0]
                lvl = s.index("[", 1) - 1
                return "[" + "=" * lvl + "[" + t[1] + "]" + "=" * lvl + "]"
            return s
        if k == "<eof>":
            return "<eof>"
        if len(k) == 1 and (ord(k) < 32 or ord(k) == 127):     # the reference prints "char(N)"
            return f"char({ord(k)})"
        return k

    def error(self, msg: str, near: bool = True) -> LuaSyntaxError:
        t = self.t
        ln = self.linenumber()
        sl, col = self.src.linecol(t[2])
        if sl != ln:
            col = 1
        return LuaSyntaxError(msg, t[2], ln, col, self.near(t) if near and t[0] != chr(0) else None)

    def error_expected(self, what: str) -> LuaSyntaxError:
        return self.error(f"'{_TOKEN_NAMES.get(what, what)}' expected")

    def check(self, what: str) -> None:
        if self.t[0] != what:
            raise self.error_expected(what)

    def checknext(self, what: str) -> None:
        if self.t[0] != what:
            raise self.error_expected(what)
        self.next()

    def testnext(self, what: str) -> bool:
        if self.t[0] == what:
            self.next()
            return True
        return False

    def check_match(self, what: str, who: str, where: int) -> None:
        if self.t[0] == what:
            self.next()
            return
        if where == self.linenumber():
            raise self.error_expected(what)
        raise self.error(f"'{what}' expected (to close '{who}' at line {where})")

    def checkname(self) -> str:
        if self.t[0] != "name":
            raise self.error_expected("name")
        v = self.t[1]
        self.next()
        return v

    def _lookahead(self) -> str:
        t = self.toks[self.i + 1]
        if t[0] == "<error>":
            raise t[1]
        self.la_line = self.tok_line(t)
        return t[0]

    def enter(self) -> None:
        self.level += 1
        if self.level > MAX_LEVELS:
            raise self.error("chunk has too many syntax levels", near=False)

    # -- scopes -------------------------------------------------------------------------

    def errorlimit(self, fs: _FS, limit: int, what: str) -> LuaSyntaxError:
        line = fs.node.line
        msg = (f"main function has more than {limit} {what}" if line == 0
               else f"function at line {line} has more than {limit} {what}")
        return self.error(msg, near=False)

    def new_localvar(self, name: str, n: int, pos: int, kind: str) -> LocalVar:
        fs = self.fs
        if len(fs.actvar) + n + 1 > MAX_VARS:
            raise self.errorlimit(fs, MAX_VARS, "local variables")
        return LocalVar(name, pos, kind, fs.node)

    def adjust(self, vs: list[LocalVar]) -> None:
        self.fs.actvar.extend(vs)
        self.locals.extend(v for v in vs if not v.name.startswith("("))

    def hidden(self, names: tuple[str, ...], pos: int) -> list[LocalVar]:
        return [self.new_localvar(nm, i, pos, "hidden") for i, nm in enumerate(names)]

    def enterblock(self, breakable: bool) -> None:
        self.fs.blocks.append((len(self.fs.actvar), breakable))

    def leaveblock(self) -> None:
        n, _ = self.fs.blocks.pop()
        del self.fs.actvar[n:]

    def block(self) -> list:
        self.enterblock(False)
        body = self.chunk()
        self.leaveblock()
        return body

    def singlevar(self, name: str, pos: int) -> Name:
        fs = self.fs
        var = None
        for v in reversed(fs.actvar):
            if v.name == name:
                var = v
                break
        if var is None:
            chain = []
            f = fs.parent
            prev = fs
            while f is not None:
                chain.append(prev)
                for v in reversed(f.actvar):
                    if v.name == name:
                        var = v
                        break
                if var is not None:
                    break
                prev = f
                f = f.parent
            if var is not None:
                var.captured = True
                for g in chain:                         # every function between the use and the declaration
                    ups = g.node.upvalues
                    if id(var) not in ups:
                        if len(ups) + 1 > MAX_UPVALUES:
                            raise self.errorlimit(g, MAX_UPVALUES, "upvalues")
                        ups[id(var)] = var
        nd = Name(pos, name, var, fs.node)
        if var is None:
            self.names.append(nd)
        else:
            var.reads += 1
        return nd

    def mark_write(self, e) -> None:
        if isinstance(e, Name):
            e.ctx = "w"
            if e.var is not None:
                e.var.reads -= 1
                e.var.writes += 1

    # -- statements ---------------------------------------------------------------------

    def chunk(self) -> list:
        body: list = []
        self.enter()
        islast = False
        while not islast and self.t[0] not in _BLOCK_FOLLOW:
            st, islast = self.statement()
            body.append(st)
            self.testnext(";")
        self.level -= 1
        return body

    def statement(self) -> tuple[Node, bool]:
        t = self.t
        k = t[0]
        line = self.linenumber()
        pos = t[2]
        if k == "if":
            return self.ifstat(line, pos), False
        if k == "while":
            self.next()
            cond = self.expr()
            self.enterblock(True)
            self.checknext("do")
            body = self.block()
            self.check_match("end", "while", line)
            self.leaveblock()
            return While(pos, cond, body), False
        if k == "do":
            self.next()
            body = self.block()
            self.check_match("end", "do", line)
            return Do(pos, body), False
        if k == "for":
            return self.forstat(line, pos), False
        if k == "repeat":
            self.enterblock(True)
            self.enterblock(False)
            self.next()
            body = self.chunk()
            self.check_match("until", "repeat", line)
            cond = self.expr()
            self.leaveblock()
            self.leaveblock()
            return Repeat(pos, body, cond), False
        if k == "function":
            self.next()
            npos = self.t[2]
            target = self.singlevar(self.checkname(), npos)
            fname = target.name
            method = False
            while self.t[0] == "." or self.t[0] == ":":
                method = self.t[0] == ":"
                self.next()
                kpos = self.t[2]
                key = self.checkname()
                target = Index(kpos, target, Str(kpos, key), True)
                fname += (":" if method else ".") + key
                if method:
                    break
            if isinstance(target, Name):        # `function a.b()` only reads `a`
                self.mark_write(target)
                if target.var is None:
                    target.ctx = "f"
            fn = self.body(method, line, pos, fname)
            fn.method = method
            return FuncStat(pos, target, fn), False
        if k == "local":
            self.next()
            if self.testnext("function"):
                npos = self.t[2]
                name = self.checkname()
                v = self.new_localvar(name, 0, npos, "function")
                self.adjust([v])
                fn = self.body(False, self.tok_line(self.t), pos, name)
                return LocalFunc(pos, v, fn), False
            vs = []
            while True:
                npos = self.t[2]
                vs.append(self.new_localvar(self.checkname(), len(vs), npos, "local"))
                if not self.testnext(","):
                    break
            exprs = self.explist() if self.testnext("=") else []
            self.adjust(vs)
            return Local(pos, vs, exprs), False
        if k == "return":
            self.next()
            exprs = [] if self.t[0] in _BLOCK_FOLLOW or self.t[0] == ";" else self.explist()
            return Return(pos, exprs), True
        if k == "break":
            self.next()
            if not any(b for _n, b in self.fs.blocks):
                raise self.error("no loop to break")
            return Break(pos), True
        return self.exprstat(pos), False

    def ifstat(self, line: int, pos: int) -> If:
        tests = []
        orelse = None
        self.next()
        cond = self.expr()
        self.checknext("then")
        tests.append((cond, self.block()))
        while self.t[0] == "elseif":
            self.next()
            cond = self.expr()
            self.checknext("then")
            tests.append((cond, self.block()))
        if self.t[0] == "else":
            self.next()
            orelse = self.block()
        self.check_match("end", "if", line)
        return If(pos, tests, orelse)

    def forstat(self, line: int, pos: int) -> Node:
        self.enterblock(True)
        self.next()
        npos = self.t[2]
        varname = self.checkname()
        k = self.t[0]
        if k == "=":
            hidden = self.hidden(("(for index)", "(for limit)", "(for step)"), npos)
            var = self.new_localvar(varname, 3, npos, "for")
            self.next()
            start = self.expr()
            self.checknext(",")
            stop = self.expr()
            step = self.expr() if self.testnext(",") else None
            body = self.forbody(hidden, [var])
            node: Node = NumFor(pos, var, start, stop, step, body)
        elif k == "," or k == "in":
            hidden = self.hidden(("(for generator)", "(for state)", "(for control)"), npos)
            vs = [self.new_localvar(varname, 3, npos, "for")]
            while self.testnext(","):
                p2 = self.t[2]
                vs.append(self.new_localvar(self.checkname(), 3 + len(vs), p2, "for"))
            self.checknext("in")
            exprs = self.explist()
            body = self.forbody(hidden, vs)
            node = GenFor(pos, vs, exprs, body)
        else:
            raise self.error("'=' or 'in' expected")
        self.check_match("end", "for", line)
        self.leaveblock()
        return node

    def forbody(self, hidden: list[LocalVar], vs: list[LocalVar]) -> list:
        self.fs.actvar.extend(hidden)
        self.checknext("do")
        self.enterblock(False)
        self.adjust(vs)
        body = self.block()
        self.leaveblock()
        return body

    def exprstat(self, pos: int) -> Node:
        e = self.primaryexp()
        if isinstance(e, (Call, MCall)):
            return CallStat(pos, e)
        targets = [e]
        while True:
            if not isinstance(targets[-1], (Name, Index)):
                raise self.error("syntax error")
            if self.t[0] != ",":
                break
            self.next()
            targets.append(self.primaryexp())
        self.checknext("=")
        exprs = self.explist()
        for t in targets:
            self.mark_write(t)
        return Assign(pos, targets, exprs)

    # -- functions ----------------------------------------------------------------------

    def body(self, needself: bool, line: int, pos: int, name: str | None) -> Func:
        node = Func(pos, line, self.fs.node if self.fs else None)
        node.name = name
        self.funcs.append(node)
        fs = _FS(self.fs, node, False)
        self.fs = fs
        self.checknext("(")
        params: list[LocalVar] = []
        if needself:
            params.append(self.new_localvar("self", 0, pos, "self"))
            self.adjust(params[-1:])
        pl: list[LocalVar] = []
        if self.t[0] != ")":
            while True:
                k = self.t[0]
                if k == "name":
                    p = self.t[2]
                    pl.append(self.new_localvar(self.checkname(), len(pl), p, "param"))
                elif k == "...":
                    p = self.t[2]
                    self.next()
                    pl.append(self.new_localvar("arg", len(pl), p, "arg"))   # LUA_COMPAT_VARARG
                    fs.is_vararg = True
                else:
                    raise self.error("<name> or '...' expected")
                if fs.is_vararg or not self.testnext(","):
                    break
        self.adjust(pl)
        node.params = params + pl
        node.is_vararg = fs.is_vararg
        self.checknext(")")
        node.body = self.chunk()
        node.end = self.t[2]
        self.check_match("end", "function", line)
        self.fs = fs.parent
        return node

    # -- expressions --------------------------------------------------------------------

    def explist(self) -> list:
        out = [self.expr()]
        while self.testnext(","):
            out.append(self.expr())
        return out

    def expr(self):
        return self.subexpr(0)

    def subexpr(self, limit: int):
        self.enter()
        t = self.t
        if t[0] in _UNARY:
            self.next()
            e = UnOp(t[2], t[0], self.subexpr(_UNARY_PRI))
        else:
            e = self.simpleexp()
        pri = _BINPRI.get(self.t[0])
        while pri is not None and pri[0] > limit:
            op = self.t
            self.next()
            e = BinOp(op[2], op[0], e, self.subexpr(pri[1]))
            pri = _BINPRI.get(self.t[0])
        self.level -= 1
        return e

    def simpleexp(self):
        t = self.t
        k = t[0]
        if k == "number":
            self.next()
            return Num(t[2], t[1])
        if k == "string":
            self.next()
            return Str(t[2], t[1])
        if k == "nil":
            self.next()
            return Nil(t[2])
        if k == "true" or k == "false":
            self.next()
            return TrueE(t[2]) if k == "true" else FalseE(t[2])
        if k == "...":
            if not self.fs.is_vararg:
                raise self.error("cannot use '...' outside a vararg function")
            self.next()
            return Vararg(t[2])
        if k == "{":
            return self.constructor()
        if k == "function":
            self.next()
            return self.body(False, self.tok_line(self.t), t[2], None)
        return self.primaryexp()

    def primaryexp(self):
        e = self.prefixexp()
        while True:
            t = self.t
            k = t[0]
            if k == ".":
                self.next()
                p = self.t[2]
                e = Index(t[2], e, Str(p, self.checkname()), True)
            elif k == "[":
                self.next()
                key = self.expr()
                self.checknext("]")
                e = Index(t[2], e, key, False)
            elif k == ":":
                self.next()
                name = self.checkname()
                args = self.funcargs()
                e = MCall(t[2], e, name, args, self.prev_end)
            elif k == "(" or k == "string" or k == "{":
                args = self.funcargs()
                e = Call(t[2], e, args, self.prev_end)
            else:
                return e

    def prefixexp(self):
        t = self.t
        if t[0] == "(":
            line = self.linenumber()
            self.next()
            e = self.expr()
            self.check_match(")", "(", line)
            return Paren(t[2], e)
        if t[0] == "name":
            self.next()
            return self.singlevar(t[1], t[2])
        raise self.error("unexpected symbol")

    def funcargs(self) -> list:
        t = self.t
        k = t[0]
        if k == "(":
            line = self.linenumber()
            lastline = self.prev_la if self.prev_la is not None else self.tok_line(self.prev)
            if line != lastline:
                raise self.error("ambiguous syntax (function call x new statement)")
            self.next()
            args = [] if self.t[0] == ")" else self.explist()
            self.check_match(")", "(", line)
            return args
        if k == "{":
            return [self.constructor()]
        if k == "string":
            self.next()
            return [Str(t[2], t[1])]
        raise self.error("function arguments expected")

    def constructor(self) -> Table:
        t = self.t
        line = self.linenumber()
        self.checknext("{")
        items: list = []
        while True:
            k = self.t[0]
            if k == "}":
                break
            if k == "name" and self._lookahead() == "=":
                kp = self.t[2]
                key = Str(kp, self.checkname())
                self.checknext("=")
                items.append((key, self.expr()))
            elif k == "[":
                self.next()
                key = self.expr()
                self.checknext("]")
                self.checknext("=")
                items.append((key, self.expr()))
            else:
                items.append((None, self.expr()))
            if not (self.testnext(",") or self.testnext(";")):
                break
        self.check_match("}", "{", line)
        return Table(t[2], items)


def parse(text: str | Source) -> Chunk:
    """Parse a Lua 5.1 chunk; raises :class:`LuaSyntaxError` on the first error (as the compiler does)."""
    src = text if isinstance(text, Source) else Source(text)
    toks, notes, comments = tokenize(src)
    if toks[0][0] == "<error>":
        raise toks[0][1]
    p = _Parser(src, toks)
    main = Func(0, 0, None)
    main.is_vararg = True
    p.fs = _FS(None, main, True)
    old = sys.getrecursionlimit()
    if old < 6000:
        sys.setrecursionlimit(6000)
    try:
        main.body = p.chunk()
        if p.t[0] != "<eof>":
            raise p.error_expected("<eof>")
    finally:
        if old < 6000:
            sys.setrecursionlimit(old)
    main.end = len(src.text)
    return Chunk(src, main, p.names, p.funcs, p.locals, notes, comments)
