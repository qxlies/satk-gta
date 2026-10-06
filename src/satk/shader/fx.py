"""Static reading of D3DX9 effect files (``.fx``) the way MTA loads them. Stdlib only.

MTA compiles a shader with ``D3DXCreateEffect`` (profile ``fx_2_0`` effects, shader models up to 3.0) and its own
include handler (``CIncludeManager`` in the MTA client): an ``#include`` path is relative to the folder of the
FIRST ``.fx`` file (also inside nested includes), a path starting with ``/`` or ``\\`` is relative to the
resource root (the folder of ``meta.xml``), and any path containing ``..`` is refused. MTA defines the macro
``IS_DEPTHBUFFER_RAWZ`` (0 or 1) plus the macros of ``dxCreateShader``.

:func:`load` resolves includes with those rules into one flattened source with ``#line`` markers (so compiler
messages point at the original files) and parses the top level: parameters (type, name, semantic, annotations,
initializer), samplers and their textures, functions, techniques and passes (state assignments, ``compile``
targets). It is a reader for checks, not a compiler: :mod:`satk.shader.check` runs ``fxc.exe`` when present.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["Param", "Pass", "Technique", "Include", "Effect", "Issue", "load", "parse", "resource_root",
           "strip_comments", "TEXTURE_TYPES"]

TEXTURE_TYPES = frozenset({"texture", "texture1d", "texture2d", "texture3d", "texturecube"})
SAMPLER_TYPES = frozenset({"sampler", "sampler1d", "sampler2d", "sampler3d", "samplercube", "sampler_state"})
_MODIFIERS = frozenset({"static", "uniform", "shared", "const", "extern", "volatile", "row_major", "column_major",
                        "inline", "precise", "nointerpolation"})
_INCLUDE = re.compile(r'^[ \t]*#[ \t]*include[ \t]*(?:"([^"\n]*)"|<([^>\n]*)>)', re.M)
_TOKEN = re.compile(r'\s+|(?P<str>"(?:[^"\\\n]|\\.)*")'
                    r'|(?P<num>\d+\.?\d*(?:[eE][-+]?\d+)?[fFhHlL]?|\.\d+(?:[eE][-+]?\d+)?[fF]?)'
                    r'|(?P<id>[A-Za-z_][A-Za-z0-9_]*)|(?P<pp>#[^\n]*)|(?P<op>.)')


@dataclass
class Issue:
    """A problem found while reading: severity error|warn|info, code, file, line, message."""

    sev: str
    code: str
    file: str
    line: int
    msg: str

    def row(self) -> list:
        return [self.sev, self.code, f"{self.file}:{self.line}" if self.line else self.file, self.msg]


@dataclass
class Param:
    name: str
    type: str                     # lower-case base type: float4, int, texture, sampler2d, float4x4 ...
    semantic: str | None
    annotations: dict[str, str]
    file: str
    line: int
    init: str | None = None       # initializer text (sampler_state body for samplers)
    array: bool = False
    row_major: bool = False
    static: bool = False          # 'static' globals are not effect parameters

    @property
    def key(self) -> str:
        """What MTA looks the parameter up by (``dxSetShaderValue``, automatic values): the semantic if any."""
        return (self.semantic or self.name).upper()

    @property
    def is_texture(self) -> bool:
        return self.type in TEXTURE_TYPES

    @property
    def is_sampler(self) -> bool:
        return self.type in SAMPLER_TYPES


@dataclass
class Pass:
    name: str
    line: int
    states: list[tuple[str, str, int]] = field(default_factory=list)   # (key as written, value, line)
    vs: tuple[str, str] | None = None        # (profile, entry function)
    ps: tuple[str, str] | None = None


@dataclass
class Technique:
    name: str
    line: int
    file: str
    keyword: str = "technique"
    passes: list[Pass] = field(default_factory=list)


@dataclass
class Include:
    text: str                 # as written
    file: str                 # file that contains the directive
    line: int
    path: Path | None = None  # resolved file
    problem: str | None = None


@dataclass
class Effect:
    path: Path
    root: Path                                   # resource root (meta.xml folder, else the .fx folder)
    source: str = ""                             # flattened source with #line markers
    files: list[Path] = field(default_factory=list)
    includes: list[Include] = field(default_factory=list)
    params: list[Param] = field(default_factory=list)
    functions: dict[str, int] = field(default_factory=dict)
    techniques: list[Technique] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    macros_used: set[str] = field(default_factory=set)

    def param(self, name: str) -> Param | None:
        n = name.lower()
        for p in self.params:
            if p.name.lower() == n:
                return p
        return None

    def keys(self) -> dict[str, Param]:
        """``KEY -> Param`` as MTA files them (semantic or name, upper case; texture and value maps merged)."""
        return {p.key: p for p in self.params if not p.is_sampler}


def resource_root(fx: Path, max_up: int = 6) -> Path:
    """Folder of the nearest ``meta.xml`` above ``fx`` (at most ``max_up`` levels), else the folder of ``fx``."""
    d = fx.parent
    for _ in range(max_up + 1):
        if (d / "meta.xml").is_file():
            return d
        if d.parent == d:
            break
        d = d.parent
    return fx.parent


def strip_comments(text: str) -> tuple[str, list[str]]:
    """``text`` with ``//`` and ``/* */`` comments blanked (line breaks kept) and a list of problems."""
    out: list[str] = []
    i, n = 0, len(text)
    problems: list[str] = []
    while i < n:
        c = text[i]
        if c == '"':
            j = i + 1
            while j < n and text[j] != '"' and text[j] != "\n":
                j += 2 if text[j] == "\\" else 1
            if j >= n or text[j] == "\n":
                problems.append(f"line {text.count(chr(10), 0, i) + 1}: unterminated string")
            out.append(text[i:j + 1])
            i = j + 1
        elif text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            i = j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            if j < 0:
                problems.append(f"line {text.count(chr(10), 0, i) + 1}: unterminated /* comment")
                j = n - 2
            seg = text[i:j + 2]
            out.append(re.sub(r"[^\n]", " ", seg))
            i = j + 2
        else:
            out.append(c)
            i += 1
    return "".join(out), problems


# --------------------------------------------------------------------------- includes


def load(path: str | Path, *, root: Path | None = None) -> Effect:
    """Read ``path`` and its includes (MTA rules) and parse the result."""
    fx = Path(path).resolve()
    eff = Effect(fx, (root or resource_root(fx)).resolve())
    rel_dir = fx.parent
    stack: list[Path] = []

    def label(p: Path) -> str:
        try:
            return p.relative_to(eff.root).as_posix()
        except ValueError:
            return p.name

    def expand(p: Path, depth: int) -> str:
        raw = p.read_bytes().decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
        if p not in eff.files:
            eff.files.append(p)
        clean, probs = strip_comments(raw)
        for pr in probs:
            ln = int(pr.split(":")[0].split()[1])
            eff.issues.append(Issue("error", "SYNTAX", label(p), ln, pr.split(": ", 1)[1]))
        lines = raw.split("\n")
        clean_lines = clean.split("\n")
        out: list[str] = [f'#line 1 "{label(p)}"']
        for no, (orig, cl) in enumerate(zip(lines, clean_lines), start=1):
            m = _INCLUDE.match(cl)
            if not m:
                out.append(orig)
                continue
            text = (m.group(1) if m.group(1) is not None else m.group(2)).strip()
            inc = Include(text, label(p), no)
            eff.includes.append(inc)
            conf = text.replace("\\", "/")
            if conf.startswith("/"):
                target = eff.root / conf.lstrip("/")
            else:
                target = rel_dir / conf
            if ".." in conf:
                inc.problem = "illegal"
                eff.issues.append(Issue("error", "INCLUDE_ILLEGAL", label(p), no,
                                        f'#include "{text}": MTA refuses include paths with ".." (they could leave '
                                        "the resource folder)"))
                out.append(orig)
                continue
            target = target.resolve()
            inc.path = target
            if not target.is_file() or target.stat().st_size == 0:
                inc.problem = "missing"
                where = "the resource root" if conf.startswith("/") else "the folder of the first .fx file"
                eff.issues.append(Issue("error", "INCLUDE_MISSING", label(p), no,
                                        f'#include "{text}": not found (MTA looks relative to {where})'))
                out.append(orig)
                continue
            if target in stack or depth >= 16:
                inc.problem = "recursive"
                eff.issues.append(Issue("error", "INCLUDE_LOOP", label(p), no, f'#include "{text}" includes itself'))
                out.append("")
                continue
            stack.append(target)
            out.append(expand(target, depth + 1))
            stack.pop()
            out.append(f'#line {no + 1} "{label(p)}"')
        return "\n".join(out)

    stack.append(fx)
    eff.source = expand(fx, 0)
    parse(eff)
    return eff


# --------------------------------------------------------------------------- tokens


@dataclass
class _Tok:
    kind: str     # str num id pp op
    text: str
    file: str
    line: int


def _tokens(source: str) -> list[_Tok]:
    clean, _ = strip_comments(source)
    toks: list[_Tok] = []
    file, line = "?", 1
    for m in _TOKEN.finditer(clean):
        kind = m.lastgroup
        txt = m.group(0)
        if kind is None:
            line += txt.count("\n")
            continue
        if kind == "pp":
            lm = re.match(r'#\s*line\s+(\d+)(?:\s+"([^"]*)")?', txt)
            if lm:
                line = int(lm.group(1)) - 1
                if lm.group(2) is not None:
                    file = lm.group(2)
            continue
        toks.append(_Tok(kind, txt, file, line))
    return toks


class _Reader:
    def __init__(self, toks: list[_Tok]):
        self.t = toks
        self.i = 0

    def peek(self, k: int = 0) -> _Tok | None:
        j = self.i + k
        return self.t[j] if j < len(self.t) else None

    def next(self) -> _Tok | None:
        tok = self.peek()
        self.i += 1
        return tok

    def at(self, text: str, k: int = 0) -> bool:
        tok = self.peek(k)
        return tok is not None and tok.text == text

    def skip_block(self, open_: str = "{", close: str = "}") -> list[_Tok]:
        """Consume a balanced block starting at the current ``open_`` token; returns the inner tokens."""
        assert self.at(open_)
        depth = 0
        start = self.i
        while self.peek() is not None:
            tok = self.next()
            if tok.text == open_:
                depth += 1
            elif tok.text == close:
                depth -= 1
                if depth == 0:
                    return self.t[start + 1:self.i - 1]
        return self.t[start + 1:]


def _annotations(toks: list[_Tok]) -> dict[str, str]:
    """``< string name = "value"; int x = 1; >`` -> ``{name: value}`` (string quotes removed)."""
    out: dict[str, str] = {}
    cur: list[_Tok] = []
    for tok in toks + [_Tok("op", ";", "", 0)]:
        if tok.text != ";":
            cur.append(tok)
            continue
        if len(cur) >= 4 and cur[-2].text == "=" and cur[-3].kind == "id":
            v = cur[-1]
            out[cur[-3].text] = v.text[1:-1] if v.kind == "str" else v.text
        elif len(cur) >= 3 and cur[1].kind == "id" and "=" in [c.text for c in cur]:
            k = [c.text for c in cur].index("=")
            if k >= 2:
                out[cur[k - 1].text] = " ".join(c.text for c in cur[k + 1:]).strip('"')
        cur = []
    return out


def _text(toks: list[_Tok]) -> str:
    return " ".join(t.text for t in toks)


# --------------------------------------------------------------------------- parse


def parse(eff: Effect) -> Effect:
    """Fill parameters, functions and techniques of ``eff`` from ``eff.source``."""
    toks = _tokens(eff.source)
    eff.macros_used = {t.text for t in toks if t.kind == "id" and t.text == "IS_DEPTHBUFFER_RAWZ"}
    if "IS_DEPTHBUFFER_RAWZ" in eff.source:
        eff.macros_used.add("IS_DEPTHBUFFER_RAWZ")
    _balance(eff, toks)
    r = _Reader(toks)
    while r.peek() is not None:
        tok = r.peek()
        low = tok.text.lower()
        if tok.kind == "id" and low in ("technique", "technique10", "technique11"):
            _technique(eff, r)
        elif tok.kind == "id" and low in ("struct", "cbuffer", "tbuffer", "interface", "class"):
            r.next()
            while r.peek() is not None and not r.at("{") and not r.at(";"):
                r.next()
            if r.at("{"):
                r.skip_block()
            if r.at(";"):
                r.next()
        elif tok.kind == "id" and low == "typedef":
            while r.peek() is not None and not r.at(";"):
                r.next()
            r.next()
        elif tok.text == ";":
            r.next()
        elif tok.kind == "pp":
            r.next()
        else:
            _declaration(eff, r)
    return eff


def _balance(eff: Effect, toks: list[_Tok]) -> None:
    pairs = {")": "(", "]": "[", "}": "{"}
    stack: list[_Tok] = []
    for t in toks:
        if t.text in "([{":
            stack.append(t)
        elif t.text in pairs:
            if not stack or stack[-1].text != pairs[t.text]:
                eff.issues.append(Issue("error", "SYNTAX", t.file, t.line, f"unbalanced {t.text!r}"))
                return
            stack.pop()
    if stack:
        t = stack[-1]
        eff.issues.append(Issue("error", "SYNTAX", t.file, t.line, f"{t.text!r} is never closed"))


def _declaration(eff: Effect, r: _Reader) -> None:
    """A global variable, sampler or function (anything up to ``;`` or a function body)."""
    stmt: list[_Tok] = []
    paren_seen = False
    eq_seen = False
    while r.peek() is not None:
        tok = r.peek()
        if tok.text == ";":
            r.next()
            break
        if tok.text == "(" and not eq_seen:
            paren_seen = True
        if tok.text == "=":
            eq_seen = True
        if tok.text == "<" and not paren_seen and not eq_seen and stmt and stmt[-1].kind in ("id",) and \
                any(s.kind == "id" for s in stmt[:-1]):
            inner = r.skip_block("<", ">")
            stmt.append(_Tok("annot", _text(inner), tok.file, tok.line))
            stmt[-1].inner = inner  # type: ignore[attr-defined]
            continue
        if tok.text == "{":
            if paren_seen and not eq_seen:
                _function(eff, stmt)
                r.skip_block()
                return
            inner = r.skip_block()
            blk = _Tok("block", _text(inner), tok.file, tok.line)
            blk.inner = inner  # type: ignore[attr-defined]
            stmt.append(blk)
            continue
        stmt.append(r.next())
    if not stmt:
        return
    if paren_seen and not eq_seen:
        _function(eff, stmt)   # a prototype
        return
    _variable(eff, stmt)


def _function(eff: Effect, stmt: list[_Tok]) -> None:
    for k, t in enumerate(stmt):
        if t.text == "(" and k > 0 and stmt[k - 1].kind == "id":
            eff.functions.setdefault(stmt[k - 1].text, stmt[k - 1].line)
            return


def _variable(eff: Effect, stmt: list[_Tok]) -> None:
    i = 0
    mods: set[str] = set()
    while i < len(stmt) and stmt[i].kind == "id" and stmt[i].text.lower() in _MODIFIERS:
        mods.add(stmt[i].text.lower())
        i += 1
    if i >= len(stmt) or stmt[i].kind != "id":
        return
    typ = stmt[i].text.lower()
    i += 1
    if i < len(stmt) and stmt[i].text == "<" and typ in ("vector", "matrix"):
        while i < len(stmt) and stmt[i].text != ">":
            i += 1
        i += 1
    if i >= len(stmt) or stmt[i].kind != "id":
        return
    first = stmt[i]
    rest = stmt[i + 1:]
    array = bool(rest) and rest[0].text == "["
    semantic = None
    annots: dict[str, str] = {}
    init = None
    k = 0
    while k < len(rest):
        t = rest[k]
        if t.text == "[":
            while k < len(rest) and rest[k].text != "]":
                k += 1
        elif t.text == ":" and k + 1 < len(rest) and rest[k + 1].kind == "id" and semantic is None and init is None:
            semantic = rest[k + 1].text
            k += 1
        elif t.kind == "annot":
            annots = _annotations(getattr(t, "inner", []))
        elif t.text == "=":
            tail = rest[k + 1:]
            init = _text(tail)
            if tail and tail[0].kind == "id" and tail[0].text.lower() == "sampler_state" and len(tail) > 1:
                init = "sampler_state " + _text(getattr(tail[1], "inner", []))
            elif len(tail) == 1 and tail[0].kind == "block":
                init = _text(getattr(tail[0], "inner", []))
            break
        elif t.text == ",":
            break   # 'float a, b;' -> only the first name matters for these checks
        k += 1
    eff.params.append(Param(first.text, typ, semantic, annots, first.file, first.line, init, array,
                            "row_major" in mods, "static" in mods))


def _technique(eff: Effect, r: _Reader) -> None:
    kw = r.next()
    name = ""
    if r.peek() is not None and r.peek().kind == "id":
        name = r.next().text
    if r.at("<"):
        r.skip_block("<", ">")
    tech = Technique(name, kw.line, kw.file, kw.text.lower())
    eff.techniques.append(tech)
    if not r.at("{"):
        eff.issues.append(Issue("error", "SYNTAX", kw.file, kw.line, f"technique {name!r} has no body"))
        return
    body = r.skip_block()
    br = _Reader(body)
    while br.peek() is not None:
        tok = br.next()
        if tok.kind == "id" and tok.text.lower() == "pass":
            pname = ""
            if br.peek() is not None and br.peek().kind == "id":
                pname = br.next().text
            if br.at("<"):
                br.skip_block("<", ">")
            ps = Pass(pname, tok.line)
            tech.passes.append(ps)
            if br.at("{"):
                _pass_states(ps, br.skip_block())


def _pass_states(ps: Pass, toks: list[_Tok]) -> None:
    cur: list[_Tok] = []
    for tok in toks + [_Tok("op", ";", "", 0)]:
        if tok.text != ";":
            cur.append(tok)
            continue
        if cur:
            eq = next((k for k, t in enumerate(cur) if t.text == "="), None)
            if eq is not None and eq > 0:
                key = "".join(t.text for t in cur[:eq])
                val = cur[eq + 1:]
                ps.states.append((key, _text(val), cur[0].line))
                low = cur[0].text.lower()
                compiled = len(val) >= 3 and val[0].text.lower() == "compile"
                if low in ("vertexshader", "pixelshader") and compiled:
                    target = (val[1].text.lower(), val[2].text)
                    if low == "vertexshader":
                        ps.vs = target
                    else:
                        ps.ps = target
        cur = []
