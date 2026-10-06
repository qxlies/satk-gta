"""Scripting API references for the knowledge base: MTA:SA Lua functions and SA-MP/open.mp Pawn natives.

Pure parsers (stdlib only, no I/O): they take file texts and return records; :mod:`.scriptapi_build`
stores them into ``work/kb/kb.sqlite`` and :mod:`.scriptapi_query` answers ``satk kb mta`` / ``satk kb native``.

**MTA** (:func:`parse_mta`) reads the Lua definition sources of an mtasa-blue tree
(``{Client,Server,Shared}/mods/deathmatch/logic``):

* registrations: ``{"name", Func}`` entries of ``lua_CFunction`` arrays and
  ``CLuaCFunctions::AddFunction("name", Func[, restricted])``; the side is the tree root (``Client`` /
  ``Server``; ``Shared`` = both, narrowed by ``#ifdef MTA_CLIENT``);
* arguments: ``ArgumentParser<F>`` / ``ArgumentParserWarn<false, F[, G]>`` -> the C++ parameter list of ``F``
  (exact; overloads give several signatures); classic ``int F(lua_State*)`` bodies -> the
  ``CScriptArgReader`` calls in source order (approximate: branches become optional arguments), the
  return from ``lua_push*`` calls, and the wiki-style ``// bool name ( ... )`` comment when the body has one;
* OOP: ``lua_classfunction`` / ``lua_classvariable`` between ``lua_newclass`` and
  ``lua_registerclass`` / ``lua_registerstaticclass``;
* events: ``AddEvent("onX", "arg1, arg2", ...)``; enums: ``IMPLEMENT_ENUM_BEGIN`` ... ``ADD_ENUM(v, "name")``
  tables (the strings a string argument of that C++ enum type accepts).

**Pawn** (:func:`parse_pawn`): ``native`` and ``forward`` declarations of ``.inc`` files with tags,
references, arrays, defaults and ``...``, ``= Alias`` natives, ``#pragma deprecated``, and simple numeric
``#define`` constants.

Nothing here copies documentation text: names, types, parameter names, file and line.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

from ..re.cpp import LineIndex, blank, match_close, split_top

__all__ = ["Arg", "MtaFunc", "MtaOop", "MtaClass", "MtaEvent", "MtaEnum", "MtaApi", "parse_mta", "lua_type",
           "lua_name", "render_sig", "PawnParam", "PawnSym", "parse_pawn", "render_pawn", "MTA_ROOTS",
           "MTA_PREFIXES", "types_table"]

#: Tree roots of the deathmatch logic and their side.
MTA_ROOTS = {"Client": "client", "Server": "server", "Shared": "shared"}
#: Prefixes read from an mtasa-blue tree.
MTA_PREFIXES = ("Client/mods/deathmatch/logic", "Server/mods/deathmatch/logic", "Shared/mods/deathmatch/logic")


# --------------------------------------------------------------------------- records


@dataclass(slots=True)
class Arg:
    name: str
    type: str
    opt: bool = False
    default: str | None = None

    def render(self) -> str:
        if self.type == "...":
            return "..."
        s = f"{self.type} {self.name}".strip()
        if self.default is not None:
            s += f" = {self.default}"
        return f"[{s}]" if self.opt else s


@dataclass
class MtaFunc:
    """One registration of a Lua function on one side (``client`` or ``server``)."""

    name: str
    side: str
    impl: str                       # C++ expression registered (``CLuaEngineDefs::EngineRequestModel``)
    path: str                       # registration file
    line: int
    parser: str = "unknown"         # argparser | argreader | raw | unknown
    variants: list[list[Arg]] = field(default_factory=list)   # argument lists (overloads)
    ret: str | None = None
    doc_sig: str | None = None      # ``bool name ( ... )`` comment from the implementation
    impl_path: str | None = None
    impl_line: int | None = None
    flags: dict = field(default_factory=dict)   # restricted, compat, debug, approx, overloads, unresolved
    enums: list[str] = field(default_factory=list)  # C++ enum types used by arguments


@dataclass(slots=True)
class MtaOop:
    cls: str
    member: str
    kind: str                       # method | var
    side: str
    func: str | None = None         # global function name (method) or getter
    setter: str | None = None       # var: setter global function
    impl: str | None = None         # C function when no global name is given
    path: str = ""
    line: int = 0


@dataclass(slots=True)
class MtaClass:
    name: str
    side: str
    parent: str | None
    static: bool
    path: str
    line: int


@dataclass(slots=True)
class MtaEvent:
    name: str
    side: str
    params: str
    path: str
    line: int


@dataclass(slots=True)
class MtaEnum:
    name: str                       # Lua-facing table name (``client-model-type``)
    ctype: str                      # C++ type (``eClientModelType``)
    side: str
    values: list[str]
    path: str
    line: int


@dataclass
class MtaApi:
    funcs: list[MtaFunc] = field(default_factory=list)
    classes: list[MtaClass] = field(default_factory=list)
    oop: list[MtaOop] = field(default_factory=list)
    events: list[MtaEvent] = field(default_factory=list)
    enums: list[MtaEnum] = field(default_factory=list)
    stats: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- types


@lru_cache(maxsize=1)
def types_table() -> dict:
    """``data/scriptapi/mta_types.json`` (scalars, elements, prefixes)."""
    from ..core import resources

    return resources.read_json("scriptapi", "mta_types.json")


def _clean_type(t: str) -> str:
    t = re.sub(r"\b(?:const|volatile|struct|class|enum|typename|static|inline|constexpr|mutable)\b", " ", t)
    t = re.sub(r"\[\[[^\]]*\]\]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"\s*([*&<>,])\s*", r"\1", t).replace(",", ", ")
    return t.rstrip("&").strip()


def _tmpl(t: str, name: str) -> list[str] | None:
    """Template arguments when ``t`` is ``name<...>`` (``std::`` optional), else ``None``."""
    m = re.match(rf"^(?:std::|SharedUtil::)?{name}<(.*)>$", t)
    return split_top(m.group(1)) if m else None


def lua_type(cpp: str, enums: dict[str, str] | None = None) -> tuple[str, bool]:
    """C++ parameter/return type -> (MTA script type, optional). ``""`` = hidden (``lua_State*``).

    ``std::optional<T>`` is optional, ``std::variant<A, B>`` is ``A|B``, containers are ``table``, a known
    C++ enum is ``string`` (its accepted values are listed separately), a pointer to an element class is
    its element type.
    """
    tt = types_table()
    t = _clean_type(cpp)
    if not t:
        return "", False
    if t in tt["scalars"]:
        return tt["scalars"][t], False
    if (a := _tmpl(t, "optional")) is not None:
        inner, _o = lua_type(a[0], enums)
        return inner, True
    if (a := _tmpl(t, "variant")) is not None:
        parts: list[str] = []
        opt = False
        for x in a:
            lt, o = lua_type(x, enums)
            opt = opt or o or lt == "nil"
            for p in lt.split("|"):
                if p and p != "nil" and p not in parts:
                    parts.append(p)
        parts = [x for x in parts if x != "bool"] + [x for x in parts if x == "bool"]   # failure value last
        return "|".join(parts) or "nil", opt
    for name in ("vector", "array", "unordered_map", "map", "list", "set", "unordered_set", "span", "deque",
                 "CFastHashMap", "CFastHashSet", "CLuaTable"):
        if _tmpl(t, name) is not None:
            return "table", False
    for name in ("shared_ptr", "unique_ptr", "reference_wrapper"):
        if (a := _tmpl(t, name)) is not None:
            return lua_type(a[0] + "*", enums)
    if (a := _tmpl(t, "CLuaMultiReturn")) is not None or (a := _tmpl(t, "tuple")) is not None:
        return ", ".join(lua_type(x, enums)[0] or "nil" for x in a), False
    if (a := _tmpl(t, "pair")) is not None:
        return ", ".join(lua_type(x, enums)[0] or "nil" for x in a), False
    if enums is not None:
        short = t.rsplit("::", 1)[-1] if not t.endswith("::Enum") else t
        if t in enums or short in enums:
            return "string", False
    if t.endswith("*"):
        base = t.rstrip("*").strip()
        if base in tt["scalars"] and tt["scalars"].get(base) == "string":
            return "string", False
        if base in ("char", "unsigned char"):
            return "string", False
        if base in tt["elements"]:
            return tt["elements"][base], False
        if base + "*" in tt["scalars"]:
            return tt["scalars"][base + "*"], False
        short = base.rsplit("::", 1)[-1]
        if short in tt["elements"]:
            return tt["elements"][short], False
        for p in tt["strip_prefixes"]:
            if short.startswith(p) and len(short) > len(p) and short[len(p)].isupper():
                s = short[len(p):]
                return s[0].lower() + s[1:], False
        return "userdata", False
    short = t.rsplit("::", 1)[-1]
    if short in tt["scalars"]:
        return tt["scalars"][short], False
    if re.fullmatch(r"e[A-Z]\w*|E[A-Z]\w*|\w+::Enum|\w+Type|\w+Mode", t):
        return "string", False      # an enum without a string table: still passed as its name
    return t, False


def lua_name(cpp_name: str) -> str:
    """``uiModelID`` -> ``modelID``, ``pVehicle`` -> ``vehicle``, ``strName`` -> ``name`` (Hungarian prefixes)."""
    n = cpp_name.strip().lstrip("_")
    if n.startswith("m_"):
        n = n[2:]
    for p in types_table()["hungarian"]:
        if n.startswith(p) and len(n) > len(p) and n[len(p)].isupper():
            rest = n[len(p):]
            if len(rest) > 1 and rest[1].isupper():          # an acronym: ``strID`` -> ``ID``
                return rest
            return rest[0].lower() + rest[1:]
    return n


def render_sig(name: str, args: list[Arg], ret: str | None) -> str:
    r = (ret or "").strip()
    return f"{r + ' ' if r else ''}{name}({', '.join(a.render() for a in args)})"


# --------------------------------------------------------------------------- C++ helpers

_PP = re.compile(r"^[ \t]*#[ \t]*(if|ifdef|ifndef|elif|else|endif)\b(.*)$", re.M)


def _pp_lines(text: str) -> list[tuple[str | None, bool, bool]]:
    """Per line (1-based index - 1): ``(side, debug_only, dead)`` from ``MTA_CLIENT``/``MTA_DEBUG``/``#if 0``."""
    n = text.count("\n") + 1
    out: list[tuple[str | None, bool, bool]] = [(None, False, False)] * n
    stack: list[tuple[str, str]] = []             # (kind client|server|debug|dead|live|other, branch if|else)
    events: dict[int, tuple[str, str]] = {}
    for m in _PP.finditer(text):
        events[text.count("\n", 0, m.start())] = (m.group(1), m.group(2).strip())

    def state() -> tuple[str | None, bool, bool]:
        side, debug, dead = None, False, False
        for kind, branch in stack:
            if kind in ("client", "server"):
                s = kind if branch == "if" else ("server" if kind == "client" else "client")
                side = s if side in (None, s) else "none"
            elif kind == "debug" and branch == "if":
                debug = True
            elif kind == "dead" and branch == "if":
                dead = True
            elif kind == "live" and branch == "else":
                dead = True
        return side, debug, dead

    cur = state()
    for i in range(n):
        ev = events.get(i)
        if ev:
            d, cond = ev
            cond = re.sub(r"//.*$|/\*.*?\*/", "", cond).strip()
            if d in ("if", "ifdef", "ifndef"):
                kind = "other"
                if re.fullmatch(r"(?:defined\s*\(?\s*)?MTA_CLIENT\)?", cond):
                    kind = "server" if d == "ifndef" else "client"
                elif re.fullmatch(r"!\s*defined\s*\(?\s*MTA_CLIENT\s*\)?", cond):
                    kind = "server"
                elif re.fullmatch(r"(?:defined\s*\(?\s*)?MTA_DEBUG\)?", cond) and d != "ifndef":
                    kind = "debug"
                elif d == "if" and cond in ("0", "false"):
                    kind = "dead"
                elif d == "if" and cond in ("1", "true"):
                    kind = "live"
                stack.append((kind, "if"))
            elif d in ("else", "elif") and stack:
                kind = stack[-1][0]
                stack[-1] = (kind if d == "else" else "other", "else")
            elif d == "endif" and stack:
                stack.pop()
            cur = state()
        out[i] = cur
    return out


_DEF_HEAD = re.compile(
    r"(?P<name>(?:[A-Za-z_]\w*\s*::\s*)*~?[A-Za-z_]\w*)\s*\((?P<params>[^()]*(?:\([^()]*\)[^()]*)*)\)\s*"
    r"(?:const\s*)?(?:noexcept\s*)?(?:override\s*)?(?:->\s*(?P<trail>[^{};]+?)\s*)?$")
_KEYWORDS = {"if", "for", "while", "switch", "return", "sizeof", "catch", "do", "else", "case", "new", "delete"}


@dataclass(slots=True)
class _Def:
    qual: str
    ret: str
    params: str
    path: str
    line: int
    body: tuple[int, int]           # offsets in the file text (inside the braces)
    file: "_File"


@dataclass
class _File:
    path: str
    root: str                       # client | server | shared
    text: str
    code: str                       # comments blanked, strings kept
    bare: str                       # comments, strings and preprocessor lines blanked
    li: LineIndex
    pp: list[tuple[str | None, bool, bool]]


def _file(path: str, text: str) -> _File:
    code = blank(text, strings=False)
    bare = blank(text, strings=True, preproc=True)
    root = MTA_ROOTS.get(path.split("/", 1)[0], "shared")
    return _File(path, root, text, code, bare, LineIndex(text), _pp_lines(text))


def _ret_type(head: str) -> str:
    """Return type in front of a function name (the end of a declaration head)."""
    h = re.sub(r"\btemplate\s*<[^{};]*?>\s*", " ", head)
    h = re.split(r"[;}]|\)\s*\n|\n\s*\n", h)[-1]
    h = re.sub(r"\[\[[^\]]*\]\]", " ", h)
    h = re.sub(r"\b(?:static|inline|constexpr|virtual|extern|explicit|friend|LUA_DECLARE\w*)\b", " ", h)
    return re.sub(r"\s+", " ", h).strip()


def _defs(f: _File) -> list[_Def]:
    """Function definitions at file/namespace scope (bodies are skipped, classes too)."""
    s = f.bare
    out: list[_Def] = []
    stmt = 0
    i = 0
    tok = re.compile(r"[{};]")
    while True:
        m = tok.search(s, i)
        if not m:
            break
        j = m.start()
        c = m.group()
        if c != "{":
            stmt = i = j + 1
            continue
        head = s[stmt:j]
        if re.search(r"\bnamespace\b[\w\s:]*$", head) or re.search(r"\bextern\s*$", head):
            stmt = i = j + 1
            continue
        end = match_close(s, j)
        if end < 0:
            break
        hm = _DEF_HEAD.search(head)
        if hm and not re.search(r"\b(?:class|struct|union|enum)\b", head[:hm.start()].split(";")[-1]) \
                and hm.group("name").split("::")[-1].strip() not in _KEYWORDS and "=" not in head[hm.end():]:
            name = re.sub(r"\s+", "", hm.group("name"))
            pre = head[:hm.start()]
            ret = hm.group("trail") or _ret_type(pre)
            if not re.search(r"[=]\s*$", pre.strip()) and not pre.rstrip().endswith((",", "(", "return")):
                out.append(_Def(name, ret.strip(), re.sub(r"\s+", " ", f.code[stmt + hm.start("params"):
                                                                                  stmt + hm.end("params")]).strip(),
                                f.path, f.li.line(stmt + hm.start("name")), (j + 1, end - 1), f))
        stmt = i = end
        # a definition ends at its brace; ``= {...};`` and class bodies run on to the ``;``
    return out


_HDR_DECL = re.compile(
    r"\b(?:static\s+)?(?:inline\s+)?(?P<ret>[A-Za-z_][\w:<>,\s\*&]*?[\w>\*&])\s+(?P<name>[A-Za-z_]\w*)\s*"
    r"\((?P<params>[^;{}()]*(?:\([^;{}()]*\)[^;{}()]*)*)\)\s*(?:noexcept\s*)?[;{]")


def _header_decls(f: _File) -> list[_Def]:
    """``static T Name(params);`` declarations (headers) usable for ``ArgumentParser`` targets."""
    out = []
    for m in _HDR_DECL.finditer(f.bare):
        ret = m.group("ret").strip()
        if ret in ("return", "else", "new", "delete", "LUA_DECLARE") or m.group("name") in _KEYWORDS:
            continue
        p0, p1 = m.start("params"), m.end("params")
        out.append(_Def(m.group("name"), _ret_type(ret), re.sub(r"\s+", " ", f.code[p0:p1]).strip(), f.path,
                        f.li.line(m.start("name")), (m.end(), m.end()), f))
    return out


def _param(p: str) -> tuple[str, str] | None:
    """``const CVector& vecPos`` -> (type, name). Unnamed parameters get an empty name."""
    p = re.sub(r"\s*=.*$", "", p.strip(), flags=re.S).strip()
    if not p or p == "void":
        return None
    if p == "...":
        return "...", ""
    m = re.match(r"^(.*?[\s\*&>])([A-Za-z_]\w*)\s*(\[[^\]]*\])?$", p, re.S)
    if m and m.group(1).strip() and not m.group(1).strip().endswith(("::", "<", ",")) \
            and m.group(2) not in ("int", "float", "bool", "double", "char", "short", "long", "unsigned", "signed"):
        t = m.group(1).strip()
        if m.group(3):
            t += "*"
        return t, m.group(2)
    return p, ""


# --------------------------------------------------------------------------- CScriptArgReader

_READ_TYPES = {
    "ReadString": "string", "ReadCharStringRef": "string", "ReadStringName": "string", "ReadAnyAsString": "var",
    "ReadBool": "bool", "ReadFunction": "function", "ReadLuaArguments": "...", "ReadLuaArgument": "var",
    "ReadLuaArgumentsTable": "table", "ReadStringTable": "table", "ReadNumberTable": "table",
    "ReadUserDataTable": "table", "ReadStringMap": "table", "ReadPairTable": "table", "ReadEnumStringList": "table",
    "ReadColor": "color", "ReadMatrix": "Matrix", "ReadTable": "table",
}
#: ``argStream.ReadX(`` / ``argStream.ReadUserData<CGUIEdit>(`` / ``argStream.Skip(``; ``{rv}`` = reader names.
_READ_PAT = r"\b(?P<var>{rv})\s*\.\s*(?P<fn>Read\w+|Skip)\s*(?:<[^;()]*>\s*)?\("
_PUSH = re.compile(r"\blua_(push\w+|newtable|createtable)\s*\(\s*\w+\s*(?:,\s*(?P<arg>[^;]*?))?\)\s*;")
_PUSH_TYPES = {"pushboolean": "bool", "pushnumber": "number", "pushinteger": "int", "pushstring": "string",
               "pushlstring": "string", "pushelement": "element", "pushnil": "nil", "pushresource": "resource",
               "pushtimer": "timer", "pushuserdata": "userdata", "pushlightuserdata": "userdata",
               "pushvector": "Vector3", "pushmatrix": "Matrix", "pushxmlnode": "xml-node", "pushaccount": "account",
               "pushacl": "acl", "pushaclgroup": "acl-group", "pushban": "ban", "pushquery": "db-query",
               "pushtextdisplay": "textdisplay", "pushtextitem": "textitem", "newtable": "table",
               "createtable": "table", "pushtable": "table"}
_TABLE_SET = re.compile(r"\blua_(?:settable|rawset|rawseti|setfield)\s*\(")


def _var_type(body: str, var: str, before: int) -> str | None:
    """Declared type of local ``var`` (the last declaration before ``before``)."""
    best = None
    for m in re.finditer(rf"\b{re.escape(var)}\b", body[:before]):
        k = m.start()
        st = max(body.rfind(";", 0, k), body.rfind("{", 0, k), body.rfind("}", 0, k)) + 1
        prefix = body[st:k]
        if "(" in re.sub(r"<[^<>]*>", "", prefix) or "=" in prefix.split(",")[0] and "," not in prefix:
            continue
        pieces = split_top(prefix + "\x00", ",")
        first = pieces[0].replace("\x00", "")
        if any(not re.fullmatch(r"\s*[\*&]*\s*\w+\s*(?:=.*|\[.*\])?\s*\x00?", x, re.S) for x in pieces[1:]):
            continue
        first = re.sub(r"=.*$", "", first, flags=re.S)
        if len(pieces) > 1:
            first = re.sub(r"\b\w+\s*(?:\[.*\])?\s*$", "", first.strip())
        tm = re.match(r"^\s*(?:(?:static|const|unsigned|signed)\s+)*([A-Za-z_][\w:]*(?:\s*<.*>)?)\s*([\*&]*)\s*$",
                      first, re.S)
        if not tm or tm.group(1) in ("return", "else", "case", "goto", "delete", "new", "throw"):
            continue
        base = tm.group(1)
        if re.match(r"^\s*unsigned\b", first):
            base = "unsigned " + base
        best = (base + tm.group(2)).strip()
    return best


@dataclass(slots=True)
class _Region:
    start: int
    end: int
    kind: str                       # next (if NextIs...) | generic (if/else, both with reads) | loop
    branch: str                     # if | else | loop
    group: int


def _cond_regions(body: str, rv: str) -> list[_Region]:
    """Statements whose reads are conditional: ``if (argStream.NextIs...)`` (+ its ``else``), any ``if``/``else``
    pair with reads in both branches (alternatives), ``while``/``for`` loops over the reader."""
    out: list[_Region] = []
    read = re.compile(rf"\b(?:{rv})\s*\.\s*(?:Read|Skip)")
    for g, m in enumerate(re.finditer(r"\b(if|while|for)\s*\(", body)):
        p = m.end() - 1
        e = match_close(body, p)
        if e < 0:
            continue
        cond = body[p:e]
        s, end = _stmt_span(body, e)
        if m.group(1) != "if":
            if re.search(rf"\b(?:{rv})\s*\.", cond) or read.search(body, s, end):
                out.append(_Region(s, end, "loop", "loop", g))
            continue
        em = re.match(r"\s*else\b", body[end:])
        s2, e2 = _stmt_span(body, end + em.end()) if em else (end, end)
        if re.search(rf"\b(?:{rv})\s*\.\s*Next(?:Is|CouldBe)", cond):
            out.append(_Region(s, end, "next", "if", g))
            if em:
                out.append(_Region(s2, e2, "next", "else", g))
        elif em and read.search(body, s, end) and read.search(body, s2, e2) \
                and not re.fullmatch(r"\(\s*!\s*\w+\s*\.\s*HasErrors\s*\(\s*\)\s*\)", cond):
            out.append(_Region(s, end, "generic", "if", g))
            out.append(_Region(s2, e2, "generic", "else", g))
    return out


def _stmt_span(body: str, i: int) -> tuple[int, int]:
    """The statement starting at ``i``: a ``{...}`` block or up to the ``;``."""
    k = i
    while k < len(body) and body[k] in " \t\r\n":
        k += 1
    if k < len(body) and body[k] == "{":
        e = match_close(body, k)
        return k, (e if e > 0 else len(body))
    if body.startswith("if", k):
        p = body.find("(", k)
        e = match_close(body, p) if p >= 0 else -1
        if e > 0:
            s, end = _stmt_span(body, e)
            em = re.match(r"\s*else\b", body[end:])
            if em:
                _s2, end = _stmt_span(body, end + em.end())
            return k, end
    e = body.find(";", k)
    return k, (e + 1 if e >= 0 else len(body))


def _split_call(s: str) -> list[str]:
    """Top-level comma split that respects string and char literals."""
    out: list[str] = []
    cur: list[str] = []
    depth = 0
    quote = ""
    i = 0
    while i < len(s):
        c = s[i]
        if quote:
            cur.append(c)
            if c == "\\" and i + 1 < len(s):
                cur.append(s[i + 1])
                i += 2
                continue
            if c == quote:
                quote = ""
        elif c in ("\"", "'"):
            quote = c
            cur.append(c)
        elif c in "([{<":
            depth += 1
            cur.append(c)
        elif c in ")]}>":
            depth = max(0, depth - 1)
            cur.append(c)
        elif c == "," and depth == 0:
            out.append("".join(cur).strip())
            cur = []
        else:
            cur.append(c)
        i += 1
    out.append("".join(cur).strip())
    return out


#: Read methods whose second argument is not a default value (``ReadNumber(x, false)`` = no sign check).
_NO_DEFAULT_2ND: dict[str, tuple[str, ...] | None] = {"ReadNumber": ("true", "false"), "ReadPairTable": None}


def _argreader(d: _Def, enums: dict[str, str]) -> tuple[list[Arg], str | None, bool, list[str]]:
    """Arguments, return, approx flag and enum types of a classic ``int F(lua_State*)`` body."""
    body = d.file.code[d.body[0]:d.body[1]]
    rvs = re.findall(r"\bCScriptArgReader\s+(\w+)", body)
    args: list[Arg] = []
    used_enums: list[str] = []
    approx = False
    if rvs:
        rv = "|".join(map(re.escape, dict.fromkeys(rvs)))
        regions = _cond_regions(body, rv)
        approx = bool(regions)
        if_args: dict[int, list[int]] = {}       # group -> arg indices read in its if-branch
        else_seen: dict[int, int] = {}           # group -> reads seen in its else-branch
        for m in re.finditer(_READ_PAT.format(rv=rv), body):
            fn = m.group("fn")
            if fn in ("ReadFunctionComplete",):
                continue
            p = m.end() - 1
            e = match_close(body, p)
            parts = _split_call(body[p + 1:e - 1]) if e > 0 else []
            if fn == "Skip":
                n = int(parts[0]) if parts and parts[0].isdigit() else 1
                inside = [r for r in regions if r.start <= m.start() < r.end]
                reg = min(inside, key=lambda r: r.end - r.start) if inside else None
                if reg is not None and reg.branch == "else":     # "no value" for an if-branch argument
                    else_seen[reg.group] = else_seen.get(reg.group, 0) + n
                    continue
                args += [Arg(f"arg{len(args) + 1}", "var") for _ in range(n)]
                continue
            var = re.sub(r"^[\*&\s]+", "", parts[0]) if parts and parts[0] else ""
            var = re.sub(r"\[.*$|\.\w+$|->\w+$", "", var).strip()
            vt = _var_type(body, var, m.start()) if re.fullmatch(r"\w+", var or "") else None
            base = fn.replace("ReadIfNextIs", "Read").replace("ReadIfNextCouldBe", "Read")
            default = parts[1].strip() if len(parts) > 1 else None
            if base in _NO_DEFAULT_2ND and default is not None:
                bad = _NO_DEFAULT_2ND[base]
                if bad is None or (default in bad and len(parts) == 2):
                    default = None
            opt = default is not None or base != fn
            if base in _READ_TYPES:
                t = _READ_TYPES[base]
            elif base == "ReadUserData":
                t = lua_type(vt or "CElement*", enums)[0] if vt else "element"
                if t in ("", "userdata") and vt and not vt.endswith("*"):
                    t = lua_type(vt + "*", enums)[0]
            elif base == "ReadNumber":
                t = lua_type(vt or "float", enums)[0] if vt else "float"
                t = t if t in ("int", "float") else ("float" if not vt else "int")
            elif base in ("ReadEnumString", "ReadEnumStringOrNumber"):
                ct = (vt or "").replace("*", "").strip()
                t = "string" if base == "ReadEnumString" else "string|int"
                if ct:
                    used_enums.append(ct)
            elif base in ("ReadVector", "ReadVector3D", "ReadVector2D", "ReadVector4D"):
                t = {"CVector2D": "Vector2", "CVector4D": "Vector4"}.get((vt or "").strip(), "Vector3")
                if base == "ReadVector2D":
                    t = "Vector2"
            else:
                t = "var"
            name = lua_name(var) if var else f"arg{len(args) + 1}"
            inside = [r for r in regions if r.start <= m.start() < r.end]
            reg = min(inside, key=lambda r: r.end - r.start) if inside else None
            if reg is not None:
                if reg.branch == "loop":
                    opt = True
                elif reg.branch == "else":
                    k = else_seen.get(reg.group, 0)
                    else_seen[reg.group] = k + 1
                    alts = if_args.get(reg.group, [])
                    if k < len(alts):                   # the same position, read another way
                        a = args[alts[k]]
                        if t not in a.type.split("|"):
                            a.type += "|" + t
                        continue
                    opt = True
                elif reg.kind == "next":
                    opt = True
            dup = next((a for a in args if a.name == name and name), None)
            if dup is not None:
                if t not in dup.type.split("|"):
                    dup.type += "|" + t
                continue
            if reg is not None and reg.branch == "if":
                if_args.setdefault(reg.group, []).append(len(args))
            args.append(Arg(name, t, opt, _short_default(default)))
    ret = _pushes(body)
    return args, ret, approx, used_enums


def _short_default(v: str | None) -> str | None:
    if v is None:
        return None
    v = re.sub(r"\s+", " ", v).strip()
    v = re.sub(r"^(?:static_cast|reinterpret_cast)<[^>]*>\((.*)\)$", r"\1", v)
    if re.fullmatch(r"(?:-?\d+\.?\d*f?|0x[0-9A-Fa-f]+|true|false|\"[^\"]*\"|nullptr|NULL)", v):
        return {"nullptr": "nil", "NULL": "nil"}.get(v, v.rstrip("f") if re.fullmatch(r"-?\d+\.\d*f", v) else v)
    if v in ("LUA_REFNIL", "-2"):
        return "nil"
    if re.fullmatch(r"[A-Z][A-Z0-9_]+|\w+::\w+", v):
        return v
    if len(v) <= 24 and not re.search(r"[(){};]", v):
        return v
    return "..."


def _pushes(body: str) -> str | None:
    kinds: list[str] = []
    for m in _PUSH.finditer(body):
        k = m.group(1)
        t = _PUSH_TYPES.get(k)
        if t is None:
            continue
        if t != "table" and _TABLE_SET.search(body, m.end(), m.end() + 160):
            continue                # a key or value of a table being filled, not a return value
        if k == "pushboolean":
            a = (m.group("arg") or "").strip()
            t = a if a in ("true", "false") else "bool"
        if t not in kinds:
            kinds.append(t)
    if "bool" in kinds or "true" in kinds:      # success true / failure false: a bool
        kinds = [k for k in kinds if k not in ("true", "false")]
        if "bool" not in kinds:
            kinds.append("bool")
    # values first, then the usual failure values
    kinds = [k for k in kinds if k not in ("bool", "true", "nil", "false")] + \
        [k for k in ("bool", "true", "nil", "false") if k in kinds]
    if not kinds:
        return None
    nret = [int(x) for x in re.findall(r"\breturn\s+(\d+)\s*;", body)]
    n = max(nret) if nret else 1
    main = [k for k in kinds if k not in ("false", "nil")]
    if 1 < n <= 8 and len(main) == 1:      # getElementPosition: float, float, float (or false)
        rest = [k for k in kinds if k in ("false", "nil")]
        return ", ".join(main * n) + "".join(f"|{k}" for k in rest)
    s = "|".join(kinds)
    if n > 1:
        s += f" (up to {n} values)"
    return s


def _doc_sig(d: _Def, lua: str) -> str | None:
    """A ``// bool name ( args )`` comment in the first lines of the body (MTA's own wiki-style note)."""
    text = d.file.text[d.body[0]:d.body[1]]
    for ln in text.splitlines()[:6]:
        s = ln.strip()
        if not s:
            continue
        if not s.startswith("//"):
            if s.startswith("/*"):
                continue
            break
        c = s.lstrip("/").strip().rstrip(";").strip()
        if re.search(rf"\b{re.escape(lua)}\s*\(", c) and len(c) <= 300:
            return re.sub(r"\s+", " ", c.replace("( ", "(").replace(" )", ")"))
    return None


# --------------------------------------------------------------------------- MTA parser

_ARR = re.compile(r"lua_CFunction\s*>\s*\w+\s*\[\s*\]\s*(?:=\s*)?\{")
_ADDF = re.compile(r"\bAddFunction\s*\(\s*\"")
_OOPCALL = re.compile(r"\b(lua_newclass|lua_classfunction|lua_classvariable|lua_registerclass|lua_registerstaticclass)"
                      r"\s*\(")
_EVENT = re.compile(r"\bAddEvent\s*\(\s*\"(\w+)\"\s*,\s*\"([^\"]*)\"")
_ENUM_BLOCK = re.compile(r"IMPLEMENT_ENUM(?:_CLASS)?_BEGIN\s*\(\s*([^)]+?)\s*\)(.*?)"
                         r"IMPLEMENT_ENUM(?:_CLASS)?_END(?:_DEFAULTS)?\s*\(\s*\"([^\"]*)\"", re.S)
_ADD_ENUM = re.compile(r"\bADD_ENUM\s*\(\s*([^,]+?)\s*,\s*\"([^\"]*)\"\s*\)|\bADD_ENUM1\s*\(\s*([\w:]+)\s*\)")


def _impl_key(expr: str) -> str:
    """``ArgumentParserWarn<false, CLuaX::F>`` -> ``F``; ``CLuaX::F`` -> ``F`` (to match OOP and globals)."""
    e = re.sub(r"\s+", "", expr or "").lstrip("&")
    m = re.fullmatch(r"ArgumentParser(Warn)?<(.*)>", e)
    if m:
        t = split_top(m.group(2))
        t = t[1:] if m.group(1) else t
        return "+".join(x.rsplit("::", 1)[-1] for x in t)
    return e.rsplit("::", 1)[-1]


def _str(x: str) -> str | None:
    x = x.strip()
    m = re.fullmatch(r"\"([^\"]*)\"", x)
    return m.group(1) if m else None


def _sides(f: _File, pos: int) -> list[str]:
    side, _dbg, dead = f.pp[f.li.line(pos) - 1]
    if dead or side == "none":
        return []
    if f.root != "shared":
        return [f.root] if side in (None, f.root) else []
    return [side] if side else ["client", "server"]


def _enclosing_class(defs: list[_Def], pos: int) -> str | None:
    for d in defs:
        if d.body[0] <= pos <= d.body[1]:
            return d.qual.rsplit("::", 1)[0] if "::" in d.qual else None
    return None


def parse_mta(files: dict[str, str]) -> MtaApi:
    """Parse ``{path: text}`` of an mtasa-blue tree (paths under :data:`MTA_PREFIXES`)."""
    api = MtaApi()
    parsed = [_file(p, t) for p, t in sorted(files.items())]
    defs_by_file: dict[str, list[_Def]] = {}
    by_short: dict[str, list[_Def]] = {}
    decl_short: dict[str, list[_Def]] = {}
    for f in parsed:
        if f.path.endswith((".cpp", ".inl")):
            ds = _defs(f)
            defs_by_file[f.path] = ds
            for d in ds:
                by_short.setdefault(d.qual.rsplit("::", 1)[-1], []).append(d)
        elif f.path.endswith((".h", ".hpp")) and ("luadefs" in f.path or "/lua/" in f.path):
            for d in _header_decls(f):
                decl_short.setdefault(d.qual, []).append(d)
    # enums first: argument types refer to them
    enum_names: dict[str, str] = {}
    for f in parsed:
        if "IMPLEMENT_ENUM" not in f.code:
            continue
        for m in _ENUM_BLOCK.finditer(f.code):
            ctype = re.sub(r"\s+", "", m.group(1))
            vals = []
            for v in _ADD_ENUM.finditer(m.group(2)):
                vals.append(v.group(2) if v.group(2) is not None else v.group(3).rsplit("::", 1)[-1])
            if not vals:
                continue
            for side in (_sides(f, m.start()) or [f.root]):
                api.enums.append(MtaEnum(m.group(3) or ctype, ctype, side, vals, f.path, f.li.line(m.start())))
            enum_names.setdefault(ctype, m.group(3) or ctype)
            enum_names.setdefault(ctype.rsplit("::", 1)[-1] if not ctype.endswith("::Enum") else ctype,
                                  m.group(3) or ctype)

    def resolve(expr: str, f: _File, pos: int, side: str) -> list[_Def]:
        """Definitions for a registered C++ name, preferring the registering class and side."""
        q = re.sub(r"^&|\s+", "", expr)
        short = q.rsplit("::", 1)[-1]
        cands = by_short.get(short, [])
        if not cands:
            return decl_short.get(short, [])[:1]
        owner = q.rsplit("::", 1)[0] if "::" in q else _enclosing_class(defs_by_file.get(f.path, []), pos)
        roots = {"client": ("client", "shared"), "server": ("server", "shared")}.get(side, ("shared",))

        def rank(d: _Def) -> tuple:
            dq = d.qual.rsplit("::", 1)[0] if "::" in d.qual else None
            return (d.qual != q and dq != owner, d.file.root not in roots,
                    roots.index(d.file.root) if d.file.root in roots else 9, d.path != f.path, d.path, d.line)

        best = sorted(cands, key=rank)[0]
        if "::" in q and best.qual != q and best.qual.rsplit("::", 1)[-1] == short and \
                best.qual.rsplit("::", 1)[0] != q.rsplit("::", 1)[0] and "::" in best.qual:
            hdr = decl_short.get(short, [])
            if hdr:
                return hdr[:1]
        return [best]

    def describe(fn: MtaFunc, expr: str, f: _File, pos: int) -> None:
        e = expr.strip()
        m = re.fullmatch(r"ArgumentParser(Warn)?\s*<(.*)>", e, re.S)
        if m:
            fn.parser = "argparser"
            targets = split_top(m.group(2))
            if m.group(1):                  # ArgumentParserWarn<value on error, F...>
                targets = targets[1:]
            for t in targets:
                if t.startswith("["):
                    fn.flags["lambda"] = True
                    continue
                ds = resolve(t, f, pos, fn.side)
                if not ds:
                    fn.flags["unresolved"] = True
                    continue
                d = ds[0]
                args: list[Arg] = []
                for p in split_top(d.params) if d.params else []:
                    tp = _param(p)
                    if tp is None:
                        continue
                    ct, cn = tp
                    lt, opt = lua_type(ct, enum_names)
                    if not lt:
                        continue
                    ce = _clean_type(ct)
                    for x in re.findall(r"[\w:]+", ce):
                        if x in enum_names or (x.rsplit("::", 1)[-1] in enum_names and not x.endswith("::Enum")):
                            fn.enums.append(x)
                    args.append(Arg(lua_name(cn) if cn else f"arg{len(args) + 1}", lt, opt))
                fn.variants.append(args)
                if fn.ret is None:
                    rt, ropt = lua_type(d.ret, enum_names) if d.ret else ("", False)
                    rt = "var..." if rt == "..." else rt
                    fn.ret = (rt + ("|nil" if ropt else "")) if rt else None
                if fn.impl_path is None:
                    fn.impl_path, fn.impl_line = d.path, d.line
            if len(fn.variants) > 1:
                fn.flags["overloads"] = len(fn.variants)
            return
        if re.fullmatch(r"&?\s*(?:\w+\s*::\s*)*\w+", e):
            ds = resolve(e, f, pos, fn.side)
            if not ds:
                fn.flags["unresolved"] = True
                return
            d = ds[0]
            fn.impl_path, fn.impl_line = d.path, d.line
            body = d.file.code[d.body[0]:d.body[1]]
            if "CScriptArgReader" in body:
                fn.parser = "argreader"
                args, ret, approx, used = _argreader(d, enum_names)
                fn.variants.append(args)
                fn.ret = ret
                if approx:
                    fn.flags["approx"] = True
                fn.enums += used
            elif d.body[0] != d.body[1]:
                fn.parser = "raw"
                fn.ret = _pushes(body)
            fn.doc_sig = _doc_sig(d, fn.name)
            return
        if e.startswith("["):              # an inline lambda (lua_State*) -> int
            fn.parser = "raw"
            fn.ret = _pushes(e)
            fn.impl = "lambda"
            return
        fn.flags["expr"] = e[:80]

    funcs: list[MtaFunc] = []
    for f in parsed:
        if "lua_CFunction" not in f.code and "AddFunction" not in f.code:
            continue
        compat = "Compatibility" in f.path
        for am in _ARR.finditer(f.code):
            o = am.end() - 1
            close = match_close(f.bare, o)
            if close < 0:
                continue
            k = o + 1
            while True:
                em = re.compile(r"\{\s*\"").search(f.code, k, close)
                if not em:
                    break
                e_end = match_close(f.bare, em.start())
                if e_end < 0:
                    break
                parts = split_top(f.code[em.start() + 1:e_end - 1])
                k = e_end
                if len(parts) < 2 or _str(parts[0]) is None:
                    continue
                for side in _sides(f, em.start()):
                    fn = MtaFunc(_str(parts[0]) or "", side, re.sub(r"\s+", " ", parts[1]).strip(), f.path,
                                 f.li.line(em.start()))
                    if compat:
                        fn.flags["compat"] = True
                    if f.pp[fn.line - 1][1]:
                        fn.flags["debug"] = True
                    describe(fn, parts[1], f, em.start())
                    funcs.append(fn)
        for am in _ADDF.finditer(f.code):
            p = f.code.find("(", am.start())
            e = match_close(f.bare, p)
            if e < 0:
                continue
            parts = split_top(f.code[p + 1:e - 1])
            if len(parts) < 2 or _str(parts[0]) is None:
                continue
            for side in _sides(f, am.start()):
                fn = MtaFunc(_str(parts[0]) or "", side, re.sub(r"\s+", " ", parts[1]).strip(), f.path,
                             f.li.line(am.start()))
                if len(parts) > 2 and parts[2].strip() == "true":
                    fn.flags["restricted"] = True
                if compat:
                    fn.flags["compat"] = True
                describe(fn, parts[1], f, am.start())
                funcs.append(fn)
    # one row per (name, side): a later registration (an override) wins, like CLuaCFunctions::AddFunction
    uniq: dict[tuple[str, str], MtaFunc] = {}
    for fn in funcs:
        fn.enums = sorted(dict.fromkeys(fn.enums))
        uniq[(fn.name, fn.side)] = fn
    api.funcs = sorted(uniq.values(), key=lambda x: (x.name.lower(), x.side))
    # -- OOP
    impl_to_global: dict[tuple[str, str], str] = {}
    globals_by_side = {(fn.name, fn.side) for fn in api.funcs}
    for fn in api.funcs:
        impl_to_global.setdefault((_impl_key(fn.impl), fn.side), fn.name)

    def to_global(expr: str, side: str) -> str | None:
        """The global function registered with the same C++ implementation (or ``OOP_X`` -> ``x``)."""
        k = _impl_key(expr)
        g = impl_to_global.get((k, side))
        if g is None:
            om = re.fullmatch(r"OOP_(\w+)", k)
            if om:
                cand = om.group(1)[0].lower() + om.group(1)[1:]
                g = cand if (cand, side) in globals_by_side else None
        return g
    for f in parsed:
        if "lua_newclass" not in f.code:
            continue
        pending: list[MtaOop] = []
        for m in _OOPCALL.finditer(f.code):
            kind = m.group(1)
            p = m.end() - 1
            e = match_close(f.bare, p)
            if e < 0:
                continue
            a = split_top(f.code[p + 1:e - 1])
            sides = _sides(f, m.start())
            line = f.li.line(m.start())
            if kind == "lua_newclass":
                pending = []
            elif kind == "lua_classfunction" and len(a) >= 3 and _str(a[1]) is not None:
                for side in sides:
                    o = MtaOop("", _str(a[1]) or "", "method", side, path=f.path, line=line)
                    if _str(a[2]) is not None:
                        o.func = _str(a[2]) or None
                        if len(a) >= 4:
                            o.impl = re.sub(r"\s+", " ", a[3])
                    else:
                        o.func = to_global(a[2], side)
                        if o.func is None or _impl_key(a[2]).startswith("OOP_"):
                            o.impl = re.sub(r"\s+", " ", a[2])     # an OOP-only implementation
                    pending.append(o)
            elif kind == "lua_classvariable" and len(a) >= 3 and _str(a[1]) is not None:
                for side in sides:
                    o = MtaOop("", _str(a[1]) or "", "var", side, path=f.path, line=line)
                    if len(a) >= 6 or (len(a) >= 4 and _str(a[2]) is not None and len(a) > 4):
                        o.setter, o.func = _str(a[2]) or None, _str(a[3]) or None
                        sf, gf = a[4], a[5] if len(a) > 5 else ""
                    else:
                        o.setter, o.func = _str(a[2]), _str(a[3]) if len(a) > 3 else None
                        sf, gf = (a[2], a[3] if len(a) > 3 else "")
                    if o.setter is None and sf and sf not in ("NULL", "nullptr") and _str(sf) is None:
                        o.setter = to_global(sf, side)
                    if o.func is None and gf and gf not in ("NULL", "nullptr") and _str(gf) is None:
                        o.func = to_global(gf, side)
                        if o.func is None or _impl_key(gf).startswith("OOP_"):
                            o.impl = re.sub(r"\s+", " ", gf)
                    o.setter = o.setter or None
                    o.func = o.func or None
                    pending.append(o)
            elif kind in ("lua_registerclass", "lua_registerstaticclass") and a and _str(a[1] if len(a) > 1 else ""):
                cname = _str(a[1]) or ""
                parent = _str(a[2]) if len(a) > 2 else None
                for side in sides:
                    api.classes.append(MtaClass(cname, side, parent or None, kind.endswith("staticclass"), f.path,
                                                line))
                for o in pending:
                    o.cls = cname
                    api.oop.append(o)
                pending = []
    # -- events
    for f in parsed:
        if "AddEvent" not in f.code:
            continue
        for m in _EVENT.finditer(f.code):
            for side in _sides(f, m.start()):
                params = re.sub(r"\s*,\s*", ", ", m.group(2).strip())
                api.events.append(MtaEvent(m.group(1), side, params, f.path, f.li.line(m.start())))
    ev: dict[tuple[str, str], MtaEvent] = {}
    for e in api.events:
        ev.setdefault((e.name, e.side), e)
    api.events = sorted(ev.values(), key=lambda e: (e.name.lower(), e.side))
    cl: dict[tuple[str, str], MtaClass] = {}
    for c in api.classes:
        cl[(c.name, c.side)] = c
    api.classes = sorted(cl.values(), key=lambda c: (c.name.lower(), c.side))
    oo: dict[tuple[str, str, str, str], MtaOop] = {}
    for o in api.oop:
        oo[(o.cls, o.member, o.kind, o.side)] = o
    api.oop = sorted(oo.values(), key=lambda o: (o.cls.lower(), o.member.lower(), o.kind, o.side))
    en: dict[tuple[str, str], MtaEnum] = {}
    for e in api.enums:
        en.setdefault((e.ctype, e.side), e)
    api.enums = sorted(en.values(), key=lambda e: (e.name, e.side))
    api.stats = {
        "functions": len(api.funcs),
        "client": sum(1 for x in api.funcs if x.side == "client"),
        "server": sum(1 for x in api.funcs if x.side == "server"),
        "argparser": sum(1 for x in api.funcs if x.parser == "argparser"),
        "argreader": sum(1 for x in api.funcs if x.parser == "argreader"),
        "unresolved": sum(1 for x in api.funcs if x.flags.get("unresolved") or x.parser == "unknown"),
        "doc_sig": sum(1 for x in api.funcs if x.doc_sig),
        "classes": len({c.name for c in api.classes}),
        "oop": len(api.oop),
        "events": len(api.events),
        "enums": len({e.ctype for e in api.enums}),
    }
    return api


# --------------------------------------------------------------------------- Pawn


@dataclass(slots=True)
class PawnParam:
    name: str
    tag: str = ""                   # ``Float``, ``bool``, ``{Float,_}``
    ref: bool = False               # ``&x``
    const: bool = False
    array: str | None = None        # ``[]``, ``[24]``, ``[][3]``
    default: str | None = None

    def render(self) -> str:
        s = ("const " if self.const else "") + ("&" if self.ref else "") + (f"{self.tag}:" if self.tag else "")
        s += self.name + (self.array or "")
        if self.default is not None:
            s += f" = {self.default}"
        return s


@dataclass
class PawnSym:
    kind: str                       # native | callback | const
    name: str
    tag: str
    params: list[PawnParam]
    path: str
    line: int
    alias: str | None = None        # ``native X(...) = Y;``
    value: str | None = None        # const
    deprecated: bool = False


_PAWN_DECL = re.compile(r"(?m)^[ \t]*(native|forward)\s+(?:(?P<tag>\{[^}]*\}|[A-Za-z_@][\w@]*)\s*:\s*)?"
                        r"(?P<name>[A-Za-z_@][\w@.]*)\s*\(")
_PAWN_DEFINE = re.compile(r"(?m)^[ \t]*#define\s+(?P<name>[A-Z][A-Z_][A-Za-z0-9_]+)\s+(?P<val>[^\n]*?)\s*$")
_PAWN_VALUE = re.compile(r"\(?\s*-?\s*(?:0x[0-9A-Fa-f]+|0b[01]+|\d+(?:\.\d+)?|'[^']'|[A-Z][A-Z0-9_]*)"
                         r"(?:\s*[-+*/|<>&]+\s*(?:0x[0-9A-Fa-f]+|\d+(?:\.\d+)?|[A-Z][A-Z0-9_]*))*\s*\)?"
                         r"|\"[^\"]{0,80}\"|[A-Za-z_]\w*:\s*-?\d+(?:\.\d+)?")


def _pawn_blank(text: str) -> str:
    """Comments -> spaces (strings and lines kept)."""
    def sub(m: re.Match) -> str:
        s = m.group(0)
        if s.startswith(("\"", "'")):
            return s
        return re.sub(r"[^\n]", " ", s)

    return re.sub(r"\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*'|//[^\n]*|/\*.*?\*/", sub, text, flags=re.S)


def _pawn_param(p: str) -> PawnParam | None:
    p = p.strip()
    if not p:
        return None
    default = None
    eq = split_top(p, "=", angle=False)
    if len(eq) > 1:
        p, default = eq[0].strip(), "=".join(eq[1:]).strip()
    const = bool(re.match(r"const\b", p))
    p = re.sub(r"^const\s+", "", p)
    ref = p.startswith("&")
    p = p.lstrip("&").strip()
    tag = ""
    m = re.match(r"^(\{[^}]*\}|[A-Za-z_@][\w@]*)\s*:\s*(?!:)(.*)$", p)
    if m:
        tag, p = re.sub(r"\s+", "", m.group(1)), m.group(2).strip()
    arr = None
    am = re.match(r"^([A-Za-z_@][\w@.]*|\.\.\.)\s*((?:\[[^\]]*\]\s*)*)$", p)
    if not am:
        return PawnParam(re.sub(r"\s+", " ", p), tag, ref, const, None, default)
    if am.group(2):
        arr = re.sub(r"\s+", "", am.group(2))
    return PawnParam(am.group(1), tag, ref, const, arr, default)


def parse_pawn(text: str, path: str) -> list[PawnSym]:
    """``native``/``forward`` declarations and simple numeric ``#define`` constants of one ``.inc`` file."""
    code = _pawn_blank(text)
    li = LineIndex(code)
    out: list[PawnSym] = []
    deprecated_lines: set[int] = set()
    for m in re.finditer(r"(?m)^[ \t]*#pragma\s+deprecated\b", code):
        deprecated_lines.add(li.line(m.start()))
    for m in _PAWN_DECL.finditer(code):
        p = m.end() - 1
        e = match_close(code, p, angle=False)
        if e < 0:
            continue
        params = [x for x in (_pawn_param(s) for s in split_top(code[p + 1:e - 1], angle=False)) if x]
        tail = code[e:e + 120]
        alias = None
        am = re.match(r"\s*=\s*([A-Za-z_@][\w@]*)\s*;", tail)
        if am:
            alias = am.group(1)
        line = li.line(m.start())
        kind = "native" if m.group(1) == "native" else "callback"
        tag = re.sub(r"\s+", "", m.group("tag") or "")
        out.append(PawnSym(kind, m.group("name"), tag, params, path, line, alias=alias,
                           deprecated=(line - 1) in deprecated_lines))
    for m in _PAWN_DEFINE.finditer(code):
        val = m.group("val").strip()
        if not val or len(val) > 60 or not _PAWN_VALUE.fullmatch(val):
            continue
        out.append(PawnSym("const", m.group("name"), "", [], path, li.line(m.start()), value=val))
    return out


def render_pawn(s: PawnSym) -> str:
    head = {"native": "native ", "callback": "forward "}.get(s.kind, "")
    if s.kind == "const":
        return f"#define {s.name} {s.value}"
    sig = f"{head}{s.tag + ':' if s.tag else ''}{s.name}({', '.join(p.render() for p in s.params)})"
    return sig + (f" = {s.alias}" if s.alias else "")
