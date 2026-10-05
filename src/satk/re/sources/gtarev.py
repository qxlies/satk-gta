"""gta-reversed sources -> globals, vtables, struct sizes, constants, limits (SPEC §4.9.1; WP-09).

Read only through a :class:`~satk.re.gitsrc.SourceTree` (``git cat-file`` on ``origin/master``,
no checkout). Extracted per file:

* ``StaticRef<T>(0xADDR)`` / ``ScopedStaticRef<T>(0xADDR, ...)`` with nested template arguments,
  the declared name (``auto& NAME = ...``, ``NAME{ StaticRef... }``, ``return StaticRef...`` in an
  accessor) and its namespace/class/function scope -> :class:`GlobalSym` (array element type,
  length and sizes when they can be evaluated);
* ``RH_ScopedVirtualClass(cls, 0xVTBL, n)`` -> :class:`VtableSym`;
* ``VALIDATE_SIZE(T, expr)`` -> :class:`StructSize` (``sizeof`` and constants evaluated);
* ``constexpr``/``const``/``#define``/enum constants and ``using``/``typedef`` aliases feed one
  :class:`~satk.re.cpp.ConstEval` used for array lengths and sizes;
* pools (``ms_pXPool = new CXPool(N, "...")``), stores (``CStore<T, N>``), arrays and
  ``TOTAL_*_MODEL_IDS`` -> :class:`LimitDef`.

Nothing from these sources is ever written into the satk repository; only names, addresses,
numbers and ``file:line`` references go into ``work/re/symdb.sqlite``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..cpp import (PRIMITIVE_SIZES, ConstEval, LineIndex, ScopeMap, array_info, blank, iter_calls, match_close,
                   norm_type, split_top)
from ..gitsrc import SourceTree
from . import FuncSym, GlobalSym, LimitDef, StructSize, VtableSym
from .hook_source import scan_installs

__all__ = ["GtaRevScan", "scan_gtarev", "SOURCE_PREFIX"]

SOURCE_PREFIX = "source"
EXTS = (".h", ".hpp", ".cpp", ".inl")

_DEFINE = re.compile(r"^[ \t]*#[ \t]*define[ \t]+([A-Za-z_]\w*)(?![\w(])[ \t]+([^\n]*?)[ \t]*$", re.M)
_CONSTEXPR = re.compile(
    r"\bconstexpr\b(?:\s+(?:static|inline|const|unsigned|signed|long|short))*\s+[A-Za-z_][\w:<>, ]*?[\s*&]+"
    r"([A-Za-z_]\w*)\s*(?:=\s*([^;{}]+?)|\{\s*([^;{}]*?)\s*\})\s*;")
_CONST = re.compile(
    r"\b(?:static\s+)?const\s+(?:unsigned\s+|signed\s+)?(?:int|int8|int16|int32|uint8|uint16|uint32|uint|size_t|"
    r"float|auto|int32_t|uint32_t|long|short|char)\s+([A-Za-z_]\w*)\s*=\s*([^;{}]+?)\s*;")
_USING = re.compile(r"\busing\s+([A-Za-z_]\w*)\s*=\s*([^;{}()]+?)\s*;")
_TYPEDEF = re.compile(r"\btypedef\s+([^;{}()]+?)\s+([A-Za-z_]\w*)\s*(\[[^\];]*\])?\s*;")
_ENUM = re.compile(r"\benum\s+(?:class\s+|struct\s+)?([A-Za-z_]\w*)?\s*(?::\s*([\w:\s]+?))?\s*\{")
_ENUM_MEMBER = re.compile(r"^(?:\[\[[^\]]*\]\]\s*)?([A-Za-z_]\w*)\s*(?:\[\[[^\]]*\]\]\s*)?(?:=\s*(.+))?$", re.S)
_STATICREF = re.compile(r"\b(Scoped)?StaticRef\s*<")
_NAME_BEFORE = re.compile(r"([A-Za-z_~]\w*(?:\s*::\s*[A-Za-z_]\w*)*)\s*(=|\{)\s*$")
_RETURN_BEFORE = re.compile(r"\breturn\s*$")
_POOL = re.compile(r"\b([A-Za-z_]\w*)\s*=\s*new\s+(C\w+)\s*\(\s*([^,()]+?)\s*,\s*\"([^\"]*)\"")
_STORE = re.compile(r"^(?:\w+::)*CStore<(.+)>$")
_TOTAL_IDS = re.compile(r"^TOTAL_\w+_MODEL_IDS$")
#: Platform constants the sources use but do not define.
_WELL_KNOWN = {"MAX_PATH": 260, "TRUE": 1, "FALSE": 0, "CHAR_BIT": 8}


@dataclass
class GtaRevScan:
    funcs: list[FuncSym] = field(default_factory=list)
    globals: list[GlobalSym] = field(default_factory=list)
    vtables: list[VtableSym] = field(default_factory=list)
    sizes: list[StructSize] = field(default_factory=list)
    limits: list[LimitDef] = field(default_factory=list)
    consts: ConstEval = field(default_factory=ConstEval)
    stats: dict = field(default_factory=dict)


@dataclass
class _Ref:
    addr: int
    name: str
    type: str
    path: str
    line: int


def _rel(path: str) -> str:
    """``source/game_sa/Door.cpp`` -> ``game_sa/Door.cpp`` (hooks.json convention)."""
    return path[len(SOURCE_PREFIX) + 1:] if path.startswith(SOURCE_PREFIX + "/") else path


def _collect_consts(ev: ConstEval, b: str) -> int:
    n = 0
    for m in _DEFINE.finditer(b):
        val = m.group(2).strip()
        if val and not val.startswith(("{", "\\")):
            ev.define(m.group(1), val)
            n += 1
    for m in _CONSTEXPR.finditer(b):
        expr = m.group(2) if m.group(2) is not None else m.group(3)
        if expr and expr.strip():
            ev.define(m.group(1), expr.strip())
            n += 1
    for m in _CONST.finditer(b):
        ev.define(m.group(1), m.group(2).strip())
        n += 1
    for m in _USING.finditer(b):
        tgt = m.group(2).strip()
        if not tgt.startswith("namespace"):
            ev.alias(m.group(1), tgt)
    for m in _TYPEDEF.finditer(b):
        if not m.group(3):
            ev.alias(m.group(2), m.group(1))
    return n


def _collect_enums(ev: ConstEval, b: str, out: list[tuple[str, list[tuple[str, str | None]]]]) -> int:
    """Append ``(enum name, [(member, expr|None)])`` to ``out``; values are resolved later in order."""
    n = 0
    for m in _ENUM.finditer(b):
        brace = m.end() - 1
        end = match_close(b, brace)
        if end < 0:
            continue
        ename = m.group(1) or ""
        under = m.group(2)
        if ename:
            size = PRIMITIVE_SIZES.get(norm_type(under)) if under else 4
            if size is None and under:
                size = ev.type_size(under)
            ev.enum_sizes.setdefault(ename, size or 4)
        members: list[tuple[str, str | None]] = []
        for item in split_top(b[brace + 1:end - 1], ",", angle=False):
            mm = _ENUM_MEMBER.match(item.strip())
            if mm:
                members.append((mm.group(1), mm.group(2).strip() if mm.group(2) else None))
        if members:
            out.append((ename, members))
            n += len(members)
    return n


def _resolve_enums(ev: ConstEval, enums: list[tuple[str, list[tuple[str, str | None]]]]) -> int:
    """Assign enum values in declaration order (iteratively: no deep recursion on long enums)."""
    pending = list(enums)
    resolved = 0
    for _round in range(4):
        left = []
        for ename, members in pending:
            prev: int | None = -1
            ok = True
            for member, expr in members:
                key = f"{ename}::{member}" if ename else member
                if key in ev.values:
                    v = ev.values[key]
                elif expr is None:
                    v = prev + 1 if isinstance(prev, int) else None
                else:
                    v = ev.eval(expr)
                if isinstance(v, bool):
                    v = int(v)
                if not isinstance(v, int):
                    ok = False
                    prev = None
                    continue
                if key not in ev.values:
                    ev.values[key] = v
                    resolved += 1
                if member not in ev.values and member not in ev.raw:
                    ev.values[member] = v
                prev = v
            if not ok:
                left.append((ename, members))
        if len(left) == len(pending):
            break
        pending = left
    return resolved


def _eval_sizes(ev: ConstEval, pending: list[tuple[str, str, str, int]]) -> list[StructSize]:
    """Evaluate VALIDATE_SIZE expressions to a fixpoint (sizes may use other sizes)."""
    done: dict[str, StructSize] = {}
    todo = list(pending)
    for _round in range(6):
        left = []
        for name, expr, path, line in todo:
            if name in done:
                continue
            v = ev.eval(expr)
            if isinstance(v, int) and v > 0:
                done[name] = StructSize(name, v, _rel(path), line)
                ev.sizes.setdefault(name, v)
                short = name.split("<")[0].rsplit("::", 1)[-1]
                if "<" not in name:
                    ev.sizes.setdefault(short, v)
            else:
                left.append((name, expr, path, line))
        if len(left) == len(todo):
            break
        todo = left
    return list(done.values())


def _typed_global(ref: _Ref, ev: ConstEval) -> GlobalSym:
    t = ref.type
    info = array_info(t, ev)
    elem = n = esz = None
    if info is not None:
        elem, n = info
        esz = ev.type_size(elem)
    total = ev.type_size(t)
    if total is None and esz is not None and n is not None:
        total = esz * n
    return GlobalSym(addr=ref.addr, name=ref.name, type=t, elem_type=elem, array_len=n, elem_size=esz,
                     byte_size=total, origin="gta_reversed", src_file=_rel(ref.path), src_line=ref.line)


def scan_gtarev(tree: SourceTree) -> GtaRevScan:
    """Scan ``source/**`` of a gta-reversed tree."""
    res = GtaRevScan()
    ev = res.consts
    refs: list[_Ref] = []
    vt_seen: dict[int, VtableSym] = {}
    vt_total = 0
    size_pending: list[tuple[str, str, str, int]] = []
    size_total = 0
    pools: list[tuple[str, str, str, str, int]] = []  # (qualified var, ctor, count expr, relpath, line)
    n_files = n_consts = n_enums = 0
    enums: list[tuple[str, list[tuple[str, str | None]]]] = []
    for k, v in _WELL_KNOWN.items():
        ev.set_value(k, v)
    unnamed_refs = 0

    for path, text in tree.files((SOURCE_PREFIX,), EXTS):
        n_files += 1
        b = blank(text)                      # comments/strings blanked, preprocessor kept
        n_consts += _collect_consts(ev, b)
        if "enum" in b:
            n_enums += _collect_enums(ev, b, enums)
        has_ref = "StaticRef" in b
        has_vt = "RH_ScopedVirtualClass" in b
        has_size = "VALIDATE_SIZE" in b
        has_pool = "Pool" in text and "new " in text
        has_hook = "RH_Scoped" in b and "Install" in b
        if not (has_ref or has_vt or has_size or has_pool or has_hook):
            continue
        bp = blank(text, preproc=True)       # also without #define lines (macro bodies)
        li = LineIndex(text)
        if has_hook:
            res.funcs.extend(scan_installs(text, bp, _rel(path), ev))
        if has_vt:
            for m, a0, a1 in iter_calls(bp, "RH_ScopedVirtualClass"):
                args = split_top(bp[a0:a1])
                if len(args) != 3:
                    continue
                addr, slots = ev.eval(args[1]), ev.eval(args[2])
                if not isinstance(addr, int) or not isinstance(slots, int):
                    continue
                vt_total += 1
                vt_seen.setdefault(addr, VtableSym(addr, norm_type(args[0]), slots, _rel(path),
                                                    li.line(m.start())))
        if has_size:
            for m, a0, a1 in iter_calls(bp, "VALIDATE_SIZE"):
                args = split_top(bp[a0:a1])
                if len(args) != 2 or not args[0]:
                    continue
                size_total += 1
                size_pending.append((norm_type(args[0]), args[1], path, li.line(m.start())))
        if has_ref:
            matches = []
            for m in _STATICREF.finditer(bp):
                lt = m.end() - 1
                gt = match_close(bp, lt)
                if gt < 0:
                    continue
                j = gt
                while j < len(bp) and bp[j] in " \t\r\n":
                    j += 1
                if j >= len(bp) or bp[j] != "(":
                    continue
                close = match_close(bp, j, angle=False)
                if close < 0:
                    continue
                args = split_top(bp[j + 1:close - 1])
                if not args:
                    continue
                matches.append((m.start(), bp[lt + 1:gt - 1], args[0]))
            if matches:
                scopes = ScopeMap(bp)
                quals = scopes.qualifiers([p for p, _t, _a in matches])
                for (pos, ttxt, aexpr), qual in zip(matches, quals):
                    addr = ev.eval(aexpr)
                    if not isinstance(addr, int) or addr < 0x400000:
                        continue
                    before = bp[max(0, pos - 240):pos]
                    nm = _NAME_BEFORE.search(before)
                    if nm:
                        name = re.sub(r"\s+", "", nm.group(1))
                        if "::" not in name and qual:
                            name = f"{qual}::{name}"
                    elif _RETURN_BEFORE.search(before) and qual:
                        name = qual                      # accessor: auto& Foo() { return StaticRef... }
                    else:
                        unnamed_refs += 1
                        name = (f"{qual}::" if qual else "") + f"g_{addr:x}"
                    refs.append(_Ref(addr, name, norm_type(ttxt), path, li.line(pos)))
        if has_pool:
            pool_hits = list(_POOL.finditer(text))
            if pool_hits:
                scopes = ScopeMap(bp)
                quals = scopes.qualifiers([m.start() for m in pool_hits], functions=True)
                for m, q in zip(pool_hits, quals):
                    cls = q.rsplit("::", 1)[0] if "::" in q else ""
                    var = m.group(1)
                    pools.append((f"{cls}::{var}" if cls else var, m.group(2), m.group(3), _rel(path),
                                  li.line(m.start())))

    enum_values = _resolve_enums(ev, enums)
    res.sizes = _eval_sizes(ev, size_pending)
    res.vtables = [vt_seen[a] for a in sorted(vt_seen)]

    # globals: first definition per address wins (headers before .cpp is not guaranteed; keep the
    # one with an array length or the longest qualified name as a tie-break)
    by_addr: dict[int, GlobalSym] = {}
    for r in refs:
        g = _typed_global(r, ev)
        cur = by_addr.get(r.addr)
        if cur is None or (cur.array_len is None and g.array_len is not None):
            by_addr[r.addr] = g
    res.globals = [by_addr[a] for a in sorted(by_addr)]

    # limits
    limits: dict[str, LimitDef] = {}
    by_name = {g.name: g for g in res.globals}
    for g in res.globals:
        m = _STORE.match(g.type or "")
        if m:
            parts = split_top(m.group(1))
            n = ev.eval(parts[1]) if len(parts) == 2 else None
            if isinstance(n, int):
                limits[g.name] = LimitDef(g.name, "store", n, g.addr, f"gta-reversed:{g.src_file}:{g.src_line}")
            continue
        if g.array_len is not None:
            limits[g.name] = LimitDef(g.name, "array", g.array_len, g.addr, f"gta-reversed:{g.src_file}:{g.src_line}")
    for var, _ctor, expr, rel, line in pools:
        n = ev.eval(expr)
        if isinstance(n, int):
            g = by_name.get(var) or by_name.get(var.rsplit("::", 1)[-1])
            limits.setdefault(var, LimitDef(var, "pool", n, g.addr if g else None, f"gta-reversed:{rel}:{line}"))
    for name in sorted(ev.raw):
        if _TOTAL_IDS.match(name) or name == "RESOURCE_ID_TOTAL":
            v = ev.value(name)
            if isinstance(v, int):
                limits.setdefault(name, LimitDef(name, "id_range", v, None, "gta-reversed:constant"))
    res.limits = [limits[k] for k in sorted(limits)]

    res.stats = {
        "files": n_files,
        "hook_sites": len(res.funcs),
        "hook_starts": len({f.addr for f in res.funcs}),
        "staticref_sites": len(refs),
        "staticref_unique": len(res.globals),
        "staticref_unnamed": unnamed_refs,
        "global_arrays": sum(1 for g in res.globals if g.array_len is not None),
        "global_typed_size": sum(1 for g in res.globals if g.byte_size is not None),
        "vtable_sites": vt_total,
        "vtable_unique": len(res.vtables),
        "validate_size_sites": size_total,
        "struct_size_unique": len(res.sizes),
        "struct_size_unevaluated": len({n for n, *_ in size_pending} - {s.name for s in res.sizes}),
        "constants": n_consts,
        "enum_members": n_enums,
        "enum_values": enum_values,
        "pools": sum(1 for x in res.limits if x.kind == "pool"),
    }
    if getattr(tree, "missing", None):
        res.stats["missing_blobs"] = len(tree.missing)
    return res
