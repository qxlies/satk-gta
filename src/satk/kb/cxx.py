"""C++ scanning for the knowledge base (owner M2-02): definitions, classes, constants, chunks.

Regex-and-brackets reading of donor sources (built on :mod:`satk.re.cpp`), enough for:

* :func:`scan_file` — one file: out-of-class function definitions with the ``// 0xADDR`` comment
  above them, class/struct/union definitions with their data members and method declarations
  (nested and anonymous types included), ``constexpr``/``#define``/enum constants with lines,
  ``VALIDATE_SIZE``/``VALIDATE_OFFSET``/``static_assert(sizeof|offsetof)`` assertions and
  ``#define NAME 0xADDR`` address names;
* :func:`chunks` — split a file into search chunks (function-sized pieces) with headings;
* :func:`camel_words` — ``CStreaming::RequestModel`` -> ``c streaming request model`` for FTS.

Nothing here writes files; extracted names, signatures and line numbers go into ``work/kb``.
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field

from ..re.cpp import LineIndex, blank, match_close, norm_type, split_top

__all__ = ["Member", "Method", "ClassDef", "FuncDef", "ConstDef", "Assert", "AddrDefine", "FileScan",
           "scan_file", "chunks", "camel_words", "parse_members", "ADDR_RE", "exe_addrs"]

# --------------------------------------------------------------------------- records


@dataclass(slots=True)
class Member:
    """One data member (or an anonymous struct/union block when ``block`` is set)."""

    name: str
    type: str
    line: int
    bits: str | None = None
    nested: "ClassDef | None" = None     # type defined inline (``struct { ... } m_x;``)
    block: "ClassDef | None" = None      # anonymous struct/union whose members are flattened
    align: int | None = None             # alignas(N)


@dataclass(slots=True)
class Method:
    name: str
    sig: str
    line: int
    virtual: bool = False
    static: bool = False


@dataclass(slots=True)
class ClassDef:
    name: str                            # qualified with namespaces/outer classes
    kind: str                            # class | struct | union
    line: int
    end_line: int
    bases: list[str] = field(default_factory=list)
    members: list[Member] = field(default_factory=list)
    methods: list[Method] = field(default_factory=list)
    virtual: bool = False
    pack: int | None = None
    align: int | None = None
    path: str = ""
    start: int = 0                       # offsets of the body in the file text
    end: int = 0
    template: bool = False
    tparams: list[str] = field(default_factory=list)   # template parameter names

    @property
    def short(self) -> str:
        return self.name.rsplit("::", 1)[-1]


@dataclass(slots=True)
class FuncDef:
    name: str                            # qualified (Class::Method)
    sig: str
    line: int
    end_line: int
    addr: int | None = None
    doc: str | None = None


@dataclass(slots=True)
class ConstDef:
    name: str
    expr: str
    line: int
    kind: str                            # const | enum
    owner: str | None = None             # enum name or enclosing class


@dataclass(slots=True)
class Assert:
    kind: str                            # size | offset
    type: str
    member: str | None
    expr: str
    line: int


@dataclass(slots=True)
class AddrDefine:
    name: str
    addr: int
    line: int
    comment: str | None = None


@dataclass
class FileScan:
    path: str
    funcs: list[FuncDef] = field(default_factory=list)
    classes: list[ClassDef] = field(default_factory=list)
    consts: list[ConstDef] = field(default_factory=list)
    asserts: list[Assert] = field(default_factory=list)
    defines: list[AddrDefine] = field(default_factory=list)
    aliases: list[tuple[str, str]] = field(default_factory=list)
    enum_types: list[tuple[str, str | None]] = field(default_factory=list)  # (enum name, underlying)


# --------------------------------------------------------------------------- small helpers

ADDR_RE = re.compile(r"\b0[xX]0{0,2}([0-9A-Fa-f]{6})\b")
_WS = re.compile(r"\s+")
_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z0-9])|[A-Z]?[a-z]+|[A-Z]+|\d+")


def exe_addrs(text: str) -> set[int]:
    """gta_sa.exe virtual addresses mentioned in ``text`` (``0x5B8E64``, ``0x05B8E64``)."""
    out = set()
    for m in ADDR_RE.finditer(text):
        a = int(m.group(1), 16)
        if 0x401000 <= a < 0x1600000:
            out.add(a)
    return out


def camel_words(name: str) -> str:
    """``CStreaming::ms_aInfoForModel`` -> ``c streaming ms a info for model`` (lower case)."""
    parts: list[str] = []
    for tok in re.findall(r"[A-Za-z0-9]+", name):
        parts.extend(w.lower() for w in _CAMEL.findall(tok))
    return " ".join(parts)


def _squash(s: str, limit: int = 300) -> str:
    """One-line signature: ``CLink<CEntity*>* CStreaming::AddEntity(CEntity* entity)``."""
    s = _WS.sub(" ", s).strip()
    s = re.sub(r"\s*([<>])\s*", r"\1", s)
    s = re.sub(r"\s*([*&]+)\s*", r"\1 ", s)
    s = re.sub(r"\s*,\s*", ", ", s)
    s = re.sub(r"\(\s*", "(", s)
    s = re.sub(r"\s*\)", ")", s)
    s = re.sub(r"([*&]) ([>,)\]])", r"\1\2", s)
    s = re.sub(r"\s*::\s*", "::", s).strip()
    return s if len(s) <= limit else s[:limit - 3] + "..."


# --------------------------------------------------------------------------- regexes

_CLASS = re.compile(
    r"\b(class|struct|union)\s+"
    r"((?:(?:alignas|__declspec)\s*\((?:[^()]|\([^()]*\))*\)\s*|\[\[[^\]]*\]\]\s*|[A-Z][A-Z0-9_]*\s+)*)"
    r"([A-Za-z_]\w*)\s*(final\s*)?(?::(?!:)\s*([^{;]*?))?\s*\{")
_NAMESPACE = re.compile(r"\bnamespace\s+([A-Za-z_][\w:]*)?\s*\{")
_PACK = re.compile(r"^[ \t]*#[ \t]*pragma[ \t]+pack[ \t]*\(([^)]*)\)", re.M)
_QFN = re.compile(
    r"(?<![\w:.>~])((?:[A-Za-z_]\w*(?:\s*<[^<>;{}()]*(?:<[^<>;{}()]*>[^<>;{}()]*)*>)?\s*::\s*)+"
    r"(?:~?[A-Za-z_]\w*|operator\s*(?:\(\s*\)|[^\s\w(]+|\s+[A-Za-z_][\w:]*\**)))\s*\(")
_ADDR_COMMENT = re.compile(r"^\s*//\s*(?:0[xX])([0-9A-Fa-f]{6,8})\b(.*)$")
_COMMENT_LINE = re.compile(r"^\s*//+\s?(.*)$")
_DEFINE = re.compile(r"^[ \t]*#[ \t]*define[ \t]+([A-Za-z_]\w*)(?![\w(])[ \t]+([^\n]*?)[ \t]*$", re.M)
_CONSTEXPR = re.compile(
    r"\bconstexpr\b(?:\s+(?:static|inline|const|unsigned|signed|long|short))*\s+[A-Za-z_][\w:<>, ]*?[\s*&]+"
    r"([A-Za-z_]\w*)\s*(?:=\s*([^;{}]+?)|\{\s*([^;{}]*?)\s*\})\s*;")
_CONST = re.compile(
    r"\b(?:static\s+)?const\s+(?:unsigned\s+|signed\s+)?(?:int|int8|int16|int32|uint8|uint16|uint32|uint|size_t|"
    r"float|auto|int32_t|uint32_t|long|short|char)\s+([A-Za-z_]\w*)\s*=\s*([^;{}]+?)\s*;")
_USING = re.compile(r"\busing\s+([A-Za-z_]\w*)\s*=\s*([^;{}()]+?)\s*;")
_TYPEDEF = re.compile(r"\btypedef\s+([^;{}()]+?)\s+([A-Za-z_]\w*)\s*(\[[^\];]*\])?\s*;")
_ENUM = re.compile(r"\benum\s+(?:class\s+|struct\s+)?(?:[A-Z][A-Z0-9_]*\s+)?([A-Za-z_]\w*)?\s*(?::\s*([\w:\s]+?))?\s*\{")
_ENUM_MEMBER = re.compile(r"^\s*(?:\[\[[^\]]*\]\]\s*)?([A-Za-z_]\w*)\s*(?:\[\[[^\]]*\]\]\s*)?(?:=\s*(.+?))?\s*$", re.S)
_VSIZE = re.compile(r"\bVALIDATE_SIZE\s*\(")
_VOFF = re.compile(r"\bVALIDATE_OFFSET\s*\(")
_SASSERT = re.compile(r"\bstatic_assert\s*\(\s*(sizeof|offsetof)\s*\(")
_ACCESS = re.compile(r"^\s*(?:(?:public|private|protected)\s*:(?!:)\s*)+")
_LEAD_MACRO = re.compile(r"^\s*(?:[A-Z][A-Z0-9_]{2,}\s*\((?:[^()]|\([^()]*\))*\)\s*)+")
_ATTR = re.compile(r"\[\[[^\]]*\]\]")
_ALIGNAS = re.compile(r"\balignas\s*\(\s*([^()]*)\s*\)")
_SKIP_START = re.compile(r"^(?:using|typedef|friend|template|static_assert|namespace|enum|VALIDATE_|RH_|"
                         r"PLUGIN_|NOTSA_|#)\b")
_FNPTR = re.compile(r"\(\s*(?:__\w+\s+)?\*\s*(?:const\s+)?([A-Za-z_]\w*)\s*(\[[^\]]*\])?\s*\)\s*\(")
_HEX_DEFINE = re.compile(r"^[ \t]*#[ \t]*define[ \t]+([A-Za-z_]\w*)[ \t]+\(?\s*(0[xX][0-9A-Fa-f]+)\s*\)?[ \t]*(?://+\s*(.*))?$",
                         re.M)


def _collapse_templates(s: str) -> str:
    """Replace ``<...>`` groups by ``<>`` (innermost first) so commas/parens inside vanish."""
    prev = None
    while prev != s:
        prev = s
        s = re.sub(r"<[^<>;{}]*>", "<>", s)
    return s


# --------------------------------------------------------------------------- pack / namespaces


def _pack_changes(text: str) -> list[tuple[int, int | None]]:
    """``[(pos, pack)]`` after every ``#pragma pack``; ``None`` = default."""
    out: list[tuple[int, int | None]] = []
    stack: list[int | None] = []
    cur: int | None = None
    for m in _PACK.finditer(text):
        args = [a.strip() for a in m.group(1).split(",") if a.strip()]
        if not args:
            cur = None
        elif args[0] == "push":
            stack.append(cur)
            nums = [a for a in args[1:] if a.isdigit()]
            if nums:
                cur = int(nums[-1])
        elif args[0] == "pop":
            cur = stack.pop() if stack else None
        elif args[0].isdigit():
            cur = int(args[0])
        out.append((m.end(), cur))
    return out


def _pack_at(changes: list[tuple[int, int | None]], pos: int) -> int | None:
    i = bisect.bisect_right([p for p, _v in changes], pos)
    return changes[i - 1][1] if i else None


def _namespaces(b: str) -> list[tuple[int, int, str]]:
    """``[(start, end, name)]`` of named namespace bodies in blanked text."""
    out = []
    for m in _NAMESPACE.finditer(b):
        if not m.group(1):
            continue
        end = match_close(b, m.end() - 1)
        if end > 0:
            out.append((m.end(), end, m.group(1)))
    return out


def _ns_qual(nss: list[tuple[int, int, str]], pos: int) -> str:
    return "::".join(n for s, e, n in nss if s <= pos < e)


# --------------------------------------------------------------------------- class bodies


def _head_kind(head: str) -> str:
    """What a ``{`` after ``head`` (statement text so far) opens: type | func | init | other."""
    h = _ACCESS.sub("", head)
    h = _LEAD_MACRO.sub("", _ATTR.sub("", h)).strip()
    if not h:
        return "other"
    ht = _collapse_templates(h)
    if re.match(r"^(?:typedef\s+)?(?:(?:const|volatile|static|inline|constexpr|mutable)\s+)*(struct|class|union|enum)\b", ht) \
            and "(" not in re.sub(r"(?:alignas|__declspec)\s*\([^()]*\)", "", ht).split(":", 1)[0]:
        return "type"
    if "(" in ht:
        # ") : m_a(1), m_b" -> a brace initializer inside a constructor init list
        if re.search(r"\)\s*(?:noexcept\s*)?:(?!:)", ht) and re.search(r"[\w>]\s*$", ht):
            return "init"
        if re.search(r"\)\s*(?:const|noexcept|override|final|volatile|&|&&|mutable|\s)*"
                     r"(?:->\s*[^{]+)?$", ht) or re.search(r"\)\s*(?:noexcept\s*)?:(?!:)", ht):
            return "func"
        return "init"
    if re.search(r"[\w>\]]\s*(=\s*)?$", ht):
        return "init"
    return "other"


def _strip_inits(decl: str) -> str:
    """Drop ``= value`` and ``{value}`` default member initializers."""
    out, depth, i = [], 0, 0
    while i < len(decl):
        c = decl[i]
        if c == "{" and depth == 0:
            j = match_close(decl, i)
            i = j if j > 0 else len(decl)
            continue
        if c in "([<":
            depth += 1
        elif c in ")]>":
            depth = max(0, depth - 1)
        elif c == "=" and depth == 0 and decl[i + 1:i + 2] != "=" and decl[i - 1:i] not in "!<>=":
            break
        out.append(c)
        i += 1
    return "".join(out).strip()


def _declarator(d: str) -> tuple[str, str, str | None] | None:
    """``*name[4] : 3`` -> (prefix ptr/ref, ``name[4]``-style dims kept, bits); name must exist."""
    bits = None
    m = re.match(r"^(.*?[^:]):(?!:)\s*([^:]+)$", d)
    if m and not re.search(r"::\s*$", m.group(1)):
        d, bits = m.group(1).strip(), m.group(2).strip()
    m = re.match(r"^([*&\s]*(?:const\s*)?)([A-Za-z_]\w*)\s*((?:\[[^\]]*\]\s*)*)$", d)
    if not m:
        return None
    return m.group(1).replace(" ", "").replace("const", ""), m.group(2) + _WS.sub("", m.group(3)), bits


def _split_type_name(first: str) -> tuple[str, str] | None:
    """``CPed* m_apPeds[4]`` -> (``CPed``, ``*m_apPeds[4]``)."""
    m = re.match(r"^(.*?)([*&\s]+(?:const\s+)?|\s+)([A-Za-z_]\w*\s*(?:\[[^\]]*\]\s*)*(?::\s*[^:].*)?)$", first, re.S)
    if not m:
        return None
    t = m.group(1).strip()
    if not t or t in ("return", "else", "delete", "goto", "case"):
        return None
    return t, (m.group(2).strip() + m.group(3).strip())


def parse_members(cls: ClassDef, body: str, body_off: int, li: LineIndex, *,
                  nested_out: list[ClassDef]) -> None:
    """Fill ``cls.members``/``cls.methods`` from the blanked ``body`` (between the braces)."""
    i, start, paren, n = 0, 0, 0, len(body)
    while i < n:
        c = body[i]
        if c in "([":
            paren += 1
        elif c in ")]":
            paren = max(0, paren - 1)
        elif paren == 0 and c == ";":
            _statement(cls, body[start:i], body_off + start, li)
            start = i + 1
        elif paren == 0 and c == "{":
            head = body[start:i]
            kind = _head_kind(head)
            close = match_close(body, i)
            if close < 0:
                return
            if kind == "func":
                _method(cls, head, body_off + start, li, defined=True)
                i = start = close
                continue
            if kind == "type":
                j = _stmt_end(body, close)
                decl = body[close:j].strip()
                h = _LEAD_MACRO.sub("", _ATTR.sub("", _ACCESS.sub("", head))).strip()
                h = re.sub(r"^typedef\s+", "", h)
                mt = re.match(r"^(?:(?:const|volatile|static|inline|constexpr|mutable)\s+)*(struct|class|union|enum)\b"
                              r"\s*((?:(?:alignas|__declspec)\s*\([^()]*\)\s*|[A-Z][A-Z0-9_]*\s+)*)"
                              r"([A-Za-z_]\w*)?\s*(?:final\s*)?(?::(?!:)\s*(.*))?$", h, re.S)
                kw = mt.group(1) if mt else "struct"
                if re.match(r"^(?:\w+\s+)*static\b", h):
                    decl = ""   # static member of an inline type: the type is kept, no storage here
                if kw != "enum":
                    nm = mt.group(3) if mt else None
                    inner = ClassDef(name=f"{cls.name}::{nm}" if nm else f"{cls.name}::<anon>", kind=kw,
                                     line=li.line(body_off + i), end_line=li.line(body_off + close),
                                     bases=_bases(mt.group(4) if mt else None), pack=cls.pack,
                                     path=cls.path, start=body_off + i + 1, end=body_off + close - 1)
                    am = _ALIGNAS.search(mt.group(2) or "") if mt else None
                    if am and am.group(1).strip().isdigit():
                        inner.align = int(am.group(1))
                    parse_members(inner, body[i + 1:close - 1], body_off + i + 1, li, nested_out=nested_out)
                    if "typedef" in head:
                        decl = ""
                    if nm and not decl.strip():
                        nested_out.append(inner)
                    elif not nm and not decl.strip():
                        cls.members.append(Member(name="", type=kw, line=inner.line, block=inner))
                    else:
                        if nm:
                            nested_out.append(inner)
                        for d in split_top(_strip_inits(decl)):
                            r = _declarator(d.strip())
                            if r:
                                ptr, nmdims, bits = r
                                name, dims = _name_dims(nmdims)
                                t = (inner.name if nm else kw) + ptr + dims
                                cls.members.append(Member(name=name, type=t, line=li.line(body_off + j), bits=bits,
                                                          nested=None if ptr else inner))
                i = start = j + 1
                continue
            if kind == "init":
                i = close
                continue
            i = start = close
            continue
        i += 1
    tail = body[start:].strip()
    if tail:
        _statement(cls, tail, body_off + start, li)


def _stmt_end(s: str, i: int) -> int:
    depth = 0
    while i < len(s):
        c = s[i]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == ";" and depth <= 0:
            return i
        i += 1
    return len(s)


def _name_dims(nmdims: str) -> tuple[str, str]:
    k = nmdims.find("[")
    return (nmdims, "") if k < 0 else (nmdims[:k], nmdims[k:])


def _bases(text: str | None) -> list[str]:
    if not text or not text.strip():
        return []
    out = []
    for b in split_top(text):
        b = re.sub(r"\b(?:public|private|protected|virtual)\b", "", b).strip()
        if b:
            out.append(norm_type(b))
    return out


def _method(cls: ClassDef, head: str, pos: int, li: LineIndex, *, defined: bool) -> None:
    h = _LEAD_MACRO.sub("", _ATTR.sub("", _ACCESS.sub("", head))).strip()
    if not h:
        return
    hc = _collapse_templates(h)
    m = re.search(r"(~?[A-Za-z_]\w*|operator\s*(?:\(\s*\)|[^\s\w(]+|\s+[\w:]+\**))\s*\(", hc)
    if not m:
        return
    virtual = bool(re.search(r"\bvirtual\b", hc)) or bool(re.search(r"\)\s*(?:const\s*)?override\b", hc))
    static = bool(re.match(r"^(?:\w+\s+)*static\b", hc))
    if virtual:
        cls.virtual = True
    sig = h.split(")")[0] + ")" if not defined else re.split(r"\)\s*(?::(?!:)|$)", h)[0] + ")"
    sig = re.sub(r"^(?:(?:virtual|static|inline|constexpr|explicit|friend|__forceinline|[A-Z][A-Z0-9_]{3,})\s+)+", "",
                 sig.strip())
    off = len(head) - len(head.lstrip())
    cls.methods.append(Method(name=re.sub(r"\s+", "", m.group(1)), sig=_squash(sig), line=li.line(pos + off),
                              virtual=virtual, static=static))


def _statement(cls: ClassDef, stmt: str, pos: int, li: LineIndex) -> None:
    lead = len(stmt) - len(stmt.lstrip())
    s = _ACCESS.sub("", stmt)
    s = _LEAD_MACRO.sub("", _ATTR.sub("", s)).strip()
    if not s:
        return
    line = li.line(pos + lead + (len(stmt.lstrip()) - len(stmt.lstrip().lstrip())))
    k = stmt.find(s[:12]) if s[:12] else -1
    if k >= 0:
        line = li.line(pos + k)
    if _SKIP_START.match(s):
        return
    sc = _collapse_templates(_strip_inits(s))
    if re.match(r"^(?:class|struct|union)\s+[\w:]+\s*$", sc):  # forward declaration
        return
    fp = _FNPTR.search(sc)
    if fp is not None and fp.start() != sc.find("("):
        fp = None   # void SetCallback(void (*cb)(int)) is a method with a function-pointer parameter
    if "(" in sc and not fp:
        # a method declaration (or a macro call we cannot size)
        _method(cls, stmt, pos, li, defined=False)
        if re.search(r"\bvirtual\b", sc) or re.search(r"\boverride\b", sc):
            cls.virtual = True
        return
    words = re.findall(r"[A-Za-z_]\w*", sc.split("=")[0])
    if "static" in words[:4] or "typedef" in words[:1]:
        return
    align = None
    am = _ALIGNAS.search(s)
    if am and am.group(1).strip().isdigit():
        align = int(am.group(1))
    s = _ALIGNAS.sub("", s)
    s = re.sub(r"^(?:(?:mutable|inline|constexpr)\s+)+", "", s.strip())
    if fp:
        cls.members.append(Member(name=fp.group(1), type="void*" + _WS.sub("", fp.group(2) or ""), line=line))
        return
    parts = split_top(_strip_inits(s), ",", angle=True)
    if not parts or not parts[0]:
        return
    tn = _split_type_name(parts[0])
    if tn is None:
        return
    base_t, first = tn
    for idx, d in enumerate([first] + [_strip_inits(p) for p in parts[1:]]):
        r = _declarator(d.strip())
        if r is None:
            continue
        ptr, nmdims, bits = r
        name, dims = _name_dims(nmdims)
        if name in ("const", "volatile", "operator"):
            continue
        cls.members.append(Member(name=name, type=norm_type(base_t + ptr + dims), line=line, bits=bits, align=align))


# --------------------------------------------------------------------------- files


def _classes(text: str, bp: str, li: LineIndex, path: str) -> list[ClassDef]:
    packs = _pack_changes(text)
    nss = _namespaces(bp)
    out: list[ClassDef] = []
    last_end = -1
    for m in _CLASS.finditer(bp):
        if m.start() < last_end:
            continue  # nested: parsed with the outer class
        pre = bp[max(0, m.start() - 12):m.start()]
        if re.search(r"\benum\s*$", pre):
            continue
        brace = m.end() - 1
        close = match_close(bp, brace)
        if close < 0:
            continue
        tail = bp[close:close + 200].lstrip()
        if tail[:1] not in (";", "") and not re.match(r"^[A-Za-z_*&]", tail):
            pass  # still a definition; trailing declarators are ignored at file scope
        pre_t = bp[max(0, m.start() - 300):m.start()]
        tm = re.search(r"template\s*<([^;{}]*)>\s*(?:requires[^;{}]*)?$", pre_t)
        template = tm is not None
        q = _ns_qual(nss, m.start())
        name = f"{q}::{m.group(3)}" if q else m.group(3)
        cd = ClassDef(name=name, kind=m.group(1), line=li.line(m.start()), end_line=li.line(close),
                      bases=_bases(m.group(5)), pack=_pack_at(packs, m.start()), path=path,
                      start=brace + 1, end=close - 1, template=template)
        if tm is not None:
            for prm in split_top(tm.group(1)):
                pm = re.match(r"^(.*?)([A-Za-z_]\w*)\s*(?:=.*)?$", prm.strip(), re.S)
                if pm:
                    cd.tparams.append(pm.group(2))
        am = _ALIGNAS.search(m.group(2) or "")
        if am and am.group(1).strip().isdigit():
            cd.align = int(am.group(1))
        nested: list[ClassDef] = []
        parse_members(cd, bp[brace + 1:close - 1], brace + 1, li, nested_out=nested)
        out.append(cd)
        out.extend(nested)
        last_end = close
    return out


def _funcs(text: str, bp: str, li: LineIndex, lines: list[str]) -> list[FuncDef]:
    out: list[FuncDef] = []
    for m in _QFN.finditer(bp):
        par = m.end() - 1
        close = match_close(bp, par, angle=False)
        if close < 0:
            continue
        j = close
        n = len(bp)
        # qualifiers / trailing return / ctor init list up to the body brace
        body = -1
        depth = 0
        k = j
        while k < n:
            c = bp[k]
            if c == ";" and depth == 0:
                break
            if c in "([":
                depth += 1
            elif c in ")]":
                depth -= 1
            elif c == "{" and depth == 0:
                prev = bp[:k].rstrip()[-1:]
                if re.match(r"[\w>]", prev) and re.search(r"\)\s*(?:noexcept\s*)?:(?!:)", bp[close - 1:k]):
                    e = match_close(bp, k)
                    if e < 0:
                        break
                    k = e
                    continue
                body = k
                break
            elif c == "}" and depth == 0:
                break
            k += 1
        if body < 0:
            continue
        between = bp[close:body]
        if not re.fullmatch(r"\s*(?:(?:const|noexcept(?:\s*\([^)]*\))?|override|final|volatile|&&|&|mutable|"
                            r"throw\s*\([^)]*\))\s*)*(?:->\s*[^{;]+)?(?::(?!:)[\s\S]*)?", between):
            continue
        # statement start: after the previous ; } { (preprocessor lines are blank in bp)
        s0 = max(bp.rfind(";", 0, m.start()), bp.rfind("}", 0, m.start()), bp.rfind("{", 0, m.start())) + 1
        head = bp[s0:m.start()]
        head = re.sub(r"template\s*<[^{};]*>", " ", head)
        if re.search(r"[=(,!&|]\s*$", head) or re.search(r"\b(?:return|new|delete|else|case|goto|throw)\s*$", head):
            continue
        name = re.sub(r"\s+", "", m.group(1))
        name = re.sub(r"<[^()]*?>(?=::)", "", name)
        params = bp[par:close]
        quals = re.split(r"(?::(?!:)|->)", between)[0].strip() if between.strip() else ""
        sig = _squash(f"{head.strip()} {name}{params} {quals}")
        line = li.line(m.start() if not head.strip() else s0 + (len(head) - len(head.lstrip())))
        end = match_close(bp, body)
        addr, doc = _comment_above(lines, line)
        out.append(FuncDef(name=name, sig=sig, line=line, end_line=li.line(end if end > 0 else body),
                           addr=addr, doc=doc))
    return out


def _comment_above(lines: list[str], line: int) -> tuple[int | None, str | None]:
    """``// 0xADDR`` and doc text in the comment block right above ``line`` (1-based)."""
    addr = None
    doc: list[str] = []
    k = line - 2
    seen = 0
    while k >= 0 and seen < 8:
        ln = lines[k]
        if not ln.strip():
            if seen == 0 and k >= line - 3:
                k -= 1
                continue
            break
        ma = _ADDR_COMMENT.match(ln)
        if ma:
            if addr is None:
                addr = int(ma.group(1), 16)
            rest = ma.group(2).strip(" -:/")
            if rest:
                doc.insert(0, rest)
        else:
            mc = _COMMENT_LINE.match(ln)
            if not mc:
                break
            t = mc.group(1).strip()
            if t and not t.startswith(("=", "-", "*", "TODO")) or (t.startswith("TODO") and len(doc) < 1):
                doc.insert(0, t)
        seen += 1
        k -= 1
    text = " ".join(doc).strip()
    return addr, (text[:200] if text else None)


def _consts(text: str, b: str, li: LineIndex, owner_at) -> tuple[list[ConstDef], list[tuple[str, str]],
                                                                    list[tuple[str, str | None]]]:
    out: list[ConstDef] = []
    aliases: list[tuple[str, str]] = []
    enum_types: list[tuple[str, str | None]] = []
    for m in _DEFINE.finditer(b):
        val = m.group(2).strip()
        if val and not val.startswith(("{", "\\")):
            out.append(ConstDef(m.group(1), val, li.line(m.start(1)), "const", None))
    for m in _CONSTEXPR.finditer(b):
        expr = m.group(2) if m.group(2) is not None else m.group(3)
        if expr and expr.strip():
            out.append(ConstDef(m.group(1), expr.strip(), li.line(m.start(1)), "const", owner_at(m.start())))
    for m in _CONST.finditer(b):
        out.append(ConstDef(m.group(1), m.group(2).strip(), li.line(m.start(1)), "const", owner_at(m.start())))
    for m in _USING.finditer(b):
        tgt = m.group(2).strip()
        if not tgt.startswith("namespace"):
            aliases.append((m.group(1), tgt))
    for m in _TYPEDEF.finditer(b):
        if not m.group(3):
            aliases.append((m.group(2), m.group(1)))
    if "enum" in b:
        for m in _ENUM.finditer(b):
            brace = m.end() - 1
            end = match_close(b, brace)
            if end < 0:
                continue
            ename = m.group(1) or ""
            enum_types.append((ename, m.group(2).strip() if m.group(2) else None))
            inner = b[brace + 1:end - 1]
            pos = 0
            for item in split_top(inner, ",", angle=False):
                k = inner.find(item, pos) if item else -1
                if k >= 0:
                    pos = k + len(item)
                mm = _ENUM_MEMBER.match(item)
                if mm:
                    at = brace + 1 + (k if k >= 0 else 0)
                    out.append(ConstDef(mm.group(1), (mm.group(2) or "").strip(), li.line(at), "enum", ename or None))
    return out, aliases, enum_types


def _asserts(bp: str, li: LineIndex) -> list[Assert]:
    out: list[Assert] = []
    for rx, kind in ((_VSIZE, "size"), (_VOFF, "offset")):
        for m in rx.finditer(bp):
            close = match_close(bp, m.end() - 1, angle=False)
            if close < 0:
                continue
            args = split_top(bp[m.end():close - 1])
            if kind == "size" and len(args) == 2 and args[0]:
                out.append(Assert("size", norm_type(args[0]), None, args[1], li.line(m.start())))
            elif kind == "offset" and len(args) == 3 and args[0]:
                out.append(Assert("offset", norm_type(args[0]), args[1].strip(), args[2], li.line(m.start())))
    for m in _SASSERT.finditer(bp):
        inner_open = m.end() - 1
        close = match_close(bp, inner_open, angle=False)
        if close < 0:
            continue
        args = split_top(bp[inner_open + 1:close - 1])
        rest = bp[close:close + 80]
        mv = re.match(r"\s*==\s*([^,)]+)", rest)
        if not mv:
            continue
        if m.group(1) == "sizeof" and len(args) == 1:
            out.append(Assert("size", norm_type(args[0]), None, mv.group(1).strip(), li.line(m.start())))
        elif m.group(1) == "offsetof" and len(args) == 2:
            out.append(Assert("offset", norm_type(args[0]), args[1].strip(), mv.group(1).strip(), li.line(m.start())))
    return out


def _line_start(text: str, line: int) -> int:
    pos = 0
    for _ in range(line - 1):
        pos = text.find("\n", pos) + 1
        if pos <= 0:
            return len(text)
    return pos


def scan_file(path: str, text: str, *, funcs: bool = True, classes: bool = True, consts: bool = True,
              defines: bool = False) -> FileScan:
    """Scan one C/C++ file (see the module docstring)."""
    res = FileScan(path)
    li = LineIndex(text)
    b = blank(text)
    bp = blank(text, preproc=True)
    lines = text.splitlines()
    if classes and re.search(r"\b(?:class|struct|union)\b", bp):
        res.classes = _classes(text, bp, li, path)
    if funcs and "::" in bp:
        res.funcs = _funcs(text, bp, li, lines)
    if consts:
        ranges = sorted((c.start, c.end, c.name) for c in res.classes)

        def owner_at(pos: int) -> str | None:
            best = None
            for s, e, n in ranges:
                if s <= pos < e and (best is None or s >= best[0]):
                    best = (s, n)
            return best[1] if best else None

        res.consts, res.aliases, res.enum_types = _consts(text, b, li, owner_at)
    if "VALIDATE_" in bp or "static_assert" in bp:
        res.asserts = _asserts(bp, li)
        if res.asserts:
            # VALIDATE_SIZE(Header, ...) inside namespace V1 / class CShopping names V1::Header / CShopping::Header
            nss = _namespaces(bp)
            cranges = [(c.start, c.end, c.name) for c in res.classes]
            for a in res.asserts:
                if "::" in a.type or "<" in a.type:
                    continue
                pos = _line_start(text, a.line)
                inner = [(s0, n) for s0, e0, n in cranges if s0 <= pos < e0]
                q = max(inner)[1] if inner else _ns_qual(nss, pos)
                if q:
                    a.type = f"{q}::{a.type}"
    if defines:
        for m in _HEX_DEFINE.finditer(text):
            v = int(m.group(2), 16)
            if 0x401000 <= v < 0x1600000:
                c = (m.group(3) or "").strip() or None
                res.defines.append(AddrDefine(m.group(1), v, li.line(m.start(1)), c[:200] if c else None))
    return res


# --------------------------------------------------------------------------- chunks

_TOPLEVEL = re.compile(r"^(?:[A-Za-z_~]|template\b|//\s*0x)")


def chunks(text: str, *, max_lines: int = 80, min_lines: int = 12, markdown: bool = False
           ) -> list[tuple[int, int]]:
    """``[(first_line, last_line)]`` (1-based, inclusive) covering the non-empty parts of ``text``.

    Code: boundaries at top-level lines (column 0 after a blank/comment line, ``// 0xADDR``),
    at most ``max_lines`` per chunk. Markdown: at headings.
    """
    lines = text.splitlines()
    n = len(lines)
    out: list[tuple[int, int]] = []
    start = 0
    for i in range(1, n):
        ln = lines[i]
        size = i - start
        if markdown:
            cut = (ln.startswith("#") and size >= 3) or (size >= max_lines and not ln.strip())
        else:
            prev = lines[i - 1].strip()
            boundary = bool(_TOPLEVEL.match(ln)) and (not prev or prev.startswith("//") or prev == "}"
                                                      or prev.endswith(";"))
            cut = (boundary and size >= min_lines) or (size >= max_lines and not ln.strip()) or size >= max_lines * 2
        if cut:
            out.append((start, i - 1))
            start = i
    out.append((start, n - 1))
    res = []
    for a, z in out:
        while a <= z and not lines[a].strip():
            a += 1
        while z >= a and not lines[z].strip():
            z -= 1
        if a <= z:
            res.append((a + 1, z + 1))
    return res
