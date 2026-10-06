"""satk.mta.luaparse: the Lua 5.1 parser behind ``satk mta lint``.

The expected messages were produced by MTA's own Lua (``lua5.1.dll`` of the fork, ``luaL_loadbuffer``); the
``engine`` tests compare the parser with that DLL directly (fixture ``lua_dll`` of ``conftest.py``).
"""

from __future__ import annotations

import random
import re

import pytest

from satk.mta import luaparse as L

VALID = r'''
-- comment
--[[ long
comment ]]
--[==[ another ]==]
local a, b, c = 1, 2.5e3, 0x1F
local s = "esc \n \t \\ \" \065 \
continued" .. 'single' .. [[long
string]] .. [=[level ]] one]=]
local t = {1, 2; x = 3, ["y"] = 4, [5] = function(...) return ... end, nested = {}}
local function fact(n) if n <= 1 then return 1 else return n * fact(n - 1) end end
function t.method(self, x) return self, x end
function t:m2(x) return self.x + x end
local obj = {}
obj.a.b.c = 1
x, y = y, x
for i = 10, 1, -1 do if i % 2 == 0 then break end end
for k, v in pairs(t) do local _ = k .. tostring(v) end
while false do end
repeat local done = true until done
do local inner = 1 end
local f = function() return end
print(#t, not a, -b, a ^ b ^ c, a .. b .. c, a and b or c, a < b, a <= b, a > b, a >= b, a == b, a ~= b)
print "call with string"
print { "call with table" }
obj:method "x"
obj:method { 1 }
local v = (f)()
local function va(...) local n = select("#", ...) return arg, n end
return t;
'''


def _err(code: str) -> str | None:
    try:
        L.parse(code)
        return None
    except L.LuaSyntaxError as e:
        return f"{e.line}: {e.text}"


def test_valid_chunk_parses_and_resolves_scopes():
    ch = L.parse(VALID)
    globals_ = {n.name for n in ch.names}
    assert {"print", "pairs", "tostring", "select", "x", "y"} <= globals_
    assert not {"a", "t", "fact", "f", "self", "arg", "obj"} & globals_       # locals, params, implicit arg
    writes = {n.name for n in ch.names if n.ctx in ("w", "f")}
    assert writes == {"x", "y"}
    fact = next(v for v in ch.locals if v.name == "fact")
    assert fact.kind == "function" and fact.reads >= 1                       # the recursive call
    va = next(f for f in ch.funcs if f.name == "va")
    assert va.is_vararg and [p.name for p in va.params] == ["arg"]
    m2 = next(f for f in ch.funcs if f.name == "t:m2")
    assert m2.method and m2.params[0].name == "self"
    nodes = list(L.walk(ch.body))
    assert sum(isinstance(n, L.Call) for n in nodes) >= 8
    assert sum(isinstance(n, L.Func) for n in nodes) == len(ch.funcs)


def test_upvalues_and_positions():
    ch = L.parse("local up = 1\nlocal function f()\n  return function() return up end\nend\nglobal = up\n")
    inner = [f for f in ch.funcs if f.name is None][0]
    outer = [f for f in ch.funcs if f.name == "f"][0]
    assert len(inner.upvalues) == 1 and len(outer.upvalues) == 1
    g = [n for n in ch.names if n.name == "global"][0]
    assert ch.src.linecol(g.pos) == (5, 1) and g.ctx == "w" and g.func.line == 0


@pytest.mark.parametrize("code,expected", [
    ("function f() return 1", "1: 'end' expected near '<eof>'"),
    ("x = 1..2", "1: malformed number near '1..2'"),
    ("x = [[ a [[ b ]] ]]", "1: nesting of [[...]] is deprecated near '['"),
    ("local a = f\n(g)()", "2: ambiguous syntax (function call x new statement) near '('"),
    ("goto continue", "1: '=' expected near 'continue'"),
    ("x = a != b", "1: unexpected symbol near '!'"),
    ("x = 3 // 2", "1: unexpected symbol near '/'"),
    ("while true do break x = 1 end", "1: 'end' expected near 'x'"),
    ("break", "1: no loop to break near '<eof>'"),
    ("x = function(a,) end", "1: <name> or '...' expected near ')'"),
    ("for i = 1 do end", "1: ',' expected near 'do'"),
    ("for i, in x do end", "1: '<name>' expected near 'in'"),
    ("local 1 = 2", "1: '<name>' expected near '1'"),
    ("x = {,}", "1: unexpected symbol near ','"),
    ("x = '\\256'", "1: escape sequence too large near '''"),
    ("x = \"unterminated\ny = 1", "1: unfinished string near '\"unterminated'"),
    ("x = [=", "1: invalid long string delimiter near '[='"),
    ("x = 0x", "1: malformed number near '0x'"),
    ("f() = 1", "1: unexpected symbol near '='"),
    ("(a) = 1", "1: syntax error near '='"),
    ("a:b", "1: function arguments expected near '<eof>'"),
    ("x = 1 + ", "1: unexpected symbol near '<eof>'"),
    ("return return", "1: unexpected symbol near 'return'"),
    ("if a then\n  x = 1\nelse\n  y = 2\n", "5: 'end' expected (to close 'if' at line 1) near '<eof>'"),
    ("x = 1 y\nz = \"unterminated", "2: '=' expected near 'z'"),          # lazy lexer: the grammar error wins
    ("return 1; return 2", "1: '<eof>' expected near 'return'"),
    ("function f() return ... end", "1: cannot use '...' outside a vararg function near '...'"),
    ("--[[ unclosed comment", "1: unfinished long comment near '<eof>'"),
    ("x = [==[ unclosed", "1: unfinished long string near '<eof>'"),
    ("local x <const> = 1", "1: unexpected symbol near '<'"),
    ("x = 1 \x00 y = 2", "1: unexpected symbol"),
])
def test_errors_read_like_the_reference_compiler(code, expected):
    assert _err(code) == expected


@pytest.mark.parametrize("code", ["local t = {f\n(1)}", "x = ...", "x = 0x1p4", "x = 5.", "x = .5e-3",
                                  "local a = {x\n= 1}", "x = [==[ ]] ]=] ]==]", "--[= not long\nx = 1",
                                  "x = 'a\\q'", "f'a''b'"])
def test_reference_quirks_are_accepted(code):
    assert _err(code) is None


def test_compiler_limits():
    names = ", ".join(f"a{i}" for i in range(200))
    assert _err(f"local {names} = 1") is None
    assert _err(f"local {names}, z = 1") == "1: main function has more than 200 local variables"
    ups = "\n".join(f"local u{i} = {i}" for i in range(61))
    body = "+".join(f"u{i}" for i in range(61))
    assert _err(f"{ups}\nfunction f() return {body} end") == "62: function at line 62 has more than 60 upvalues"
    assert _err("x = " + "(" * 197 + "1" + ")" * 197) is None
    assert _err("x = " + "(" * 199 + "1" + ")" * 199) == "1: chunk has too many syntax levels"
    assert _err("x = " + "..".join(["'a'"] * 200)) == "1: chunk has too many syntax levels"


def test_lexer_notes_and_comments():
    ch = L.parse("local s = '\\x41' -- satk:ignore ESCAPE_52\nlocal t = 1 -- plain\n")
    assert [n[0] for n in ch.notes] == ["ESCAPE_52"]
    assert len(ch.comments) == 1 and "satk:ignore" in ch.comments[0][1]


def test_line_counting_matches_lua_newlines():
    src = L.Source("a\r\nb\n\rc\rd\ne")
    assert [src.linecol(src.text.index(c))[0] for c in "abcde"] == [1, 2, 3, 4, 5]


# --------------------------------------------------------------------------- against MTA's Lua


def _oracle(lua_dll):
    from luart import Lua

    rt = Lua(lua_dll)

    def run(code: str) -> str | None:
        msg = rt.compile(code, "t")
        return None if msg is None else re.sub(r"^t:", "", msg)

    return rt, run


_SNIPS = ["end", "(", ")", "{", "}", "[[", "]]", "[=[", "--[[", '"', "'", "..", "...", "=", "==", "local",
          "function", "if", "then", "else", "elseif", "do", "while", "for", "in", "repeat", "until", "return",
          "break", "!", "~", "//", "&", "\\", "\\x41", "\\300", "1..2", "0x", "1e", ".5", "goto", "::", ",", ";",
          ":", "\n(", "\n", "#", "not", "and", "or", "[", "]", "\"\\\n", "\r", "[==", "@", "a.b:c", "f{", "nil",
          "-", "%", "^", "<=", "~=", "--", "3e+"]


@pytest.mark.engine
def test_parser_agrees_with_mtas_lua_on_mutations(lua_dll):
    from satk.mta.scaffold import render

    corpus = [VALID] + [t for k in ("script", "shader", "vehicle-pack") for n, t in render(k, "t1").items()
                        if n.endswith(".lua")]
    rng = random.Random(20261005)
    rt, oracle = _oracle(lua_dll)
    try:
        bad = []
        for _ in range(1500):
            s = rng.choice(corpus)
            for _k in range(rng.randint(1, 3)):
                pos = rng.randrange(len(s) + 1)
                op = rng.random()
                if op < 0.5:
                    s = s[:pos] + rng.choice(_SNIPS) + s[pos:]
                elif op < 0.9:
                    s = s[:pos] + s[pos + rng.randint(1, 12):]
                else:
                    s = s[:pos]
            if oracle(s) != _err(s):
                bad.append((s, oracle(s), _err(s)))
        assert not bad, bad[:3]
    finally:
        rt.close()


@pytest.mark.engine
@pytest.mark.slow
def test_parser_agrees_on_lua_files_of_the_mta_trees(lua_dll):
    from satk.core.paths import cfg

    roots = []
    eng = cfg().paths.get("engine")
    if eng:
        roots.append(eng / "Bin")
    src = cfg().paths.get("src")
    if src and (src / "mtasa-neon").is_dir():
        roots.append(src / "mtasa-neon")
    files = [p for r in roots for p in r.rglob("*.lua")]
    if not files:
        pytest.skip("no Lua files in the MTA trees")
    rt, oracle = _oracle(lua_dll)
    try:
        bad = []
        for p in files:
            data = p.read_bytes()
            if data.startswith(b"\xef\xbb\xbf"):
                data = data[3:]
            text = data.decode("utf-8", errors="replace")
            if oracle(text) != _err(text):
                bad.append((p.name, oracle(text), _err(text)))
        assert not bad, bad[:3]
    finally:
        rt.close()
