"""plugin-sdk (``plugin_sa/game_sa``) -> function names, globals, call sites (SPEC §4.9.1; WP-09).

Read through a :class:`~satk.re.gitsrc.SourceTree` (no checkout). Address = the 1.0 US value
(first argument of ``ADDRESS_BY_VERSION``; same layout as the HOODLUM exe for ``.text``).

* names: ``int addrof(C::F) = ADDRESS_BY_VERSION(0x...)`` and the ``_o``/``ctor``/``dtor``/
  ``del_dtor`` variants; ``META_BEGIN(C::F) ... id = 0x...``; wrapper bodies whose single
  fixed-address call is the whole body (``return ((T(__thiscall*)(...))0x5E4720)(...)``,
  ``plugin::CallMethod<0x...>``), which covers the RenderWare API in ``RenderWare.cpp``;
* globals: ``T& C::x = *reinterpret_cast<T*>(GLOBAL_ADDRESS_BY_VERSION(0x...))`` and
  ``T& x = *(T*)0x...;`` (arrays ``T (&x)[N]``);
* call sites: ``RefList<site, GAME_10US_COMPACT|GAME_10US_HOODLUM, H_CALL|H_JUMP|H_CALLBACK, caller, n>``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..cpp import LineIndex, ScopeMap, blank, match_close, norm_type, split_top
from ..gitsrc import SourceTree
from . import CallSite, FuncSym, GlobalSym

__all__ = ["PluginSdkScan", "scan_pluginsdk", "PREFIX"]

PREFIX = "plugin_sa/game_sa"
EXTS = (".h", ".hpp", ".cpp")

_ADDR_MACROS = ("addrof_o", "addrof", "gaddrof_o", "gaddrof", "ctor_addr_o", "ctor_addr", "ctor_gaddr_o",
                "ctor_gaddr", "dtor_addr", "dtor_gaddr", "del_dtor_addr", "del_dtor_gaddr")
_ADDR_DEF = re.compile(r"\bint\s+(" + "|".join(_ADDR_MACROS) + r")\s*\(")
_ABV = re.compile(r"\s*=\s*(?:GLOBAL_)?ADDRESS_BY_VERSION\s*\(\s*(0x[0-9A-Fa-f]+)")
_META = re.compile(r"\b((?:CTOR_|DTOR_|DEL_DTOR_)?META_BEGIN(?:_OVERLOADED)?)\s*\(")
_META_ID = re.compile(r"\bid\s*=\s*(0x[0-9A-Fa-f]+)")
_REFLIST = re.compile(r"\bRefList\s*<")
_REF_ITEM = re.compile(r"(0x[0-9A-Fa-f]+)\s*,\s*(GAME_\w+)\s*,\s*(H_\w+)\s*,\s*(0x[0-9A-Fa-f]+|0)\s*,\s*(\d+)")
_GABV = re.compile(r"GLOBAL_ADDRESS_BY_VERSION\s*\(\s*(0x[0-9A-Fa-f]+)")
_DEREF_GLOBAL = re.compile(
    r"(?m)^[ \t]*([^;{}=\n]*?[&)\]][^;{}=\n]*?)=\s*\*\s*\(\s*([^;]*?)\)\s*(0x[0-9A-Fa-f]{5,8})\s*;")
_DECL_NAME = re.compile(r"\(?\s*&?\s*([A-Za-z_]\w*(?:::[A-Za-z_]\w*)*)\s*\)?\s*((?:\[[^\]]*\])*)\s*$")
_CASTCALL = re.compile(r"\)\s*(0x[0-9A-Fa-f]{5,8})\s*\)\s*\(")
_TPLCALL = re.compile(r"\bCall(?:Method)?(?:AndReturn)?\s*<[^;{}()]*?\b(0x[0-9A-Fa-f]{5,8})\b")
_VERSIONS = ("GAME_10US_COMPACT", "GAME_10US_HOODLUM")


@dataclass
class PluginSdkScan:
    funcs: list[FuncSym] = field(default_factory=list)
    globals: list[GlobalSym] = field(default_factory=list)
    calls: list[CallSite] = field(default_factory=list)
    stats: dict = field(default_factory=dict)


def _special_name(macro: str, cls: str) -> str:
    short = cls.rsplit("::", 1)[-1]
    if macro.startswith("del_dtor"):
        return f"{cls}::scalar_deleting_dtor"
    if macro.startswith("dtor"):
        return f"{cls}::~{short}"
    if macro.startswith("ctor"):
        return f"{cls}::{short}"
    return cls


def _split_qual(qual: str) -> tuple[str | None, str]:
    q = re.sub(r"\s+", "", qual)
    if "::" in q:
        cls, name = q.rsplit("::", 1)
        return cls, name
    return None, q


def _add(funcs: dict[int, FuncSym], addr: int, qual: str, path: str, line: int, how: dict, key: str) -> None:
    qual = re.sub(r"\s+", "", qual)
    if not qual or addr < 0x401000:
        return
    how[key] = how.get(key, 0) + 1
    if addr in funcs:
        return
    cls, name = _split_qual(qual)
    funcs[addr] = FuncSym(addr=addr, qual=qual, cls=cls, name=name, origin="plugin_sdk",
                          src_file=path, src_line=line, how=key)


def _global(name: str, decl_type: str, dims: str, addr: int, path: str, line: int) -> GlobalSym:
    t = norm_type(decl_type)
    t = t.rstrip("&").rstrip()
    elem = None
    n = None
    if dims:
        elem = t
        nums = re.findall(r"\[([^\]]*)\]", dims)
        try:
            n = int(nums[0], 0)
            t = f"{elem}[{nums[0]}]" + "".join(f"[{x}]" for x in nums[1:])
            if len(nums) > 1:
                elem = elem + "".join(f"[{x}]" for x in nums[1:])
        except ValueError:
            n = None
    return GlobalSym(addr=addr, name=name, type=t or None, elem_type=elem, array_len=n, elem_size=None,
                     byte_size=None, origin="plugin_sdk", src_file=path, src_line=line)


def scan_pluginsdk(tree: SourceTree) -> PluginSdkScan:
    """Scan ``plugin_sa/game_sa/**``."""
    res = PluginSdkScan()
    funcs: dict[int, FuncSym] = {}
    globs: dict[int, GlobalSym] = {}
    calls: dict[tuple[int, int], CallSite] = {}
    how: dict[str, int] = {}
    n_files = abv = gabv = metas = 0
    for path, text in tree.files((PREFIX,), EXTS):
        n_files += 1
        b = blank(text)
        li = LineIndex(text)
        # -- int addrof(C::F) = ADDRESS_BY_VERSION(0x...) ------------------------------
        for m in _ADDR_DEF.finditer(b):
            p0 = m.end() - 1
            p1 = match_close(b, p0, angle=False)
            if p1 < 0:
                continue
            mm = _ABV.match(b, p1)
            if not mm:
                continue
            abv += 1
            macro = m.group(1)
            args = split_top(b[p0 + 1:p1 - 1])
            if not args or not args[0]:
                continue
            qual = args[0] if macro.startswith(("addrof", "gaddrof")) else _special_name(macro, args[0])
            _add(funcs, int(mm.group(1), 16), qual, path, li.line(m.start()), how, "addrof")
        # -- META_BEGIN(...) { id = 0x...; refs_t = RefList<...> } -------------------------
        if "META_BEGIN" in b:
            heads = list(_META.finditer(b))
            for k, m in enumerate(heads):
                p0 = m.end() - 1
                p1 = match_close(b, p0, angle=False)
                if p1 < 0:
                    continue
                stop = heads[k + 1].start() if k + 1 < len(heads) else len(b)
                body = b[p1:stop]
                idm = _META_ID.search(body)
                if not idm:
                    continue
                metas += 1
                callee = int(idm.group(1), 16)
                args = split_top(b[p0 + 1:p1 - 1])
                macro = m.group(1)
                if args and args[0]:
                    qual = _special_name(macro.split("META")[0].lower() + "addr", args[0]) \
                        if macro.startswith(("CTOR", "DTOR", "DEL_DTOR")) else args[0]
                    _add(funcs, callee, qual, path, li.line(m.start()), how, "meta")
                rl = _REFLIST.search(body)
                if rl:
                    q0 = p1 + rl.end() - 1
                    q1 = match_close(b, q0)
                    if q1 > 0:
                        for it in _REF_ITEM.finditer(b[q0 + 1:q1 - 1]):
                            if it.group(2) not in _VERSIONS:
                                continue
                            site = int(it.group(1), 16)
                            caller = int(it.group(4), 16) or None
                            kind = it.group(3)[2:].lower()
                            calls.setdefault((site, callee), CallSite(site, callee, caller, kind, "plugin_sdk"))
        # -- wrapper bodies with one fixed-address call ----------------------------------
        if path.endswith((".cpp", ".hpp")) and ("0x" in b):
            bp = blank(text, preproc=True)
            sm = ScopeMap(bp)
            stack: list[tuple[str, str | None]] = []
            for pos, d, label in sm.events:
                if d < 0:
                    if stack:
                        stack.pop()
                    continue
                outer = list(stack)
                stack.append(label)
                kind, name = label
                if kind != "fn" or not name or any(k == "fn" for k, _n in outer):
                    continue
                end = match_close(bp, pos)
                if end < 0 or end - pos > 900:
                    continue
                body = bp[pos + 1:end - 1]
                hits = {int(x, 16) for x in _CASTCALL.findall(body)} | {int(x, 16) for x in _TPLCALL.findall(body)}
                if len(hits) != 1 or body.count(";") > 2:
                    continue
                q = name
                scope = [n for k, n in outer if k in ("ns", "class") and n and n != "plugin"]
                if "::" not in q and outer and outer[-1][0] == "class":
                    q = "::".join(scope + [q])
                _add(funcs, hits.pop(), q, path, li.line(pos), how, "wrapper")
        # -- globals ---------------------------------------------------------------------
        if "GLOBAL_ADDRESS_BY_VERSION" in b:
            for m in _GABV.finditer(b):
                gabv += 1
                # statement start
                k = m.start()
                while k > 0 and b[k - 1] not in ";{}":
                    k -= 1
                stmt = b[k:m.start()]
                if re.match(r"\s*int\s+\w*g?addr", stmt):
                    continue
                eq = stmt.find("=")
                if eq < 0:
                    continue
                decl = stmt[:eq].rstrip()
                nm = _DECL_NAME.search(decl)
                if not nm:
                    continue
                name = nm.group(1)
                dtype = decl[:nm.start()]
                addr = int(m.group(1), 16)
                globs.setdefault(addr, _global(name, dtype, nm.group(2), addr, path, li.line(m.start())))
        for m in _DEREF_GLOBAL.finditer(b):
            decl = m.group(1).rstrip()
            nm = _DECL_NAME.search(decl)
            if not nm:
                continue
            addr = int(m.group(3), 16)
            globs.setdefault(addr, _global(nm.group(1), decl[:nm.start()], nm.group(2), addr, path,
                                           li.line(m.start(1))))
    res.funcs = [funcs[a] for a in sorted(funcs)]
    res.globals = [globs[a] for a in sorted(globs)]
    res.calls = [calls[k] for k in sorted(calls)]
    res.stats = {"files": n_files, "address_by_version": abv, "global_address_by_version": gabv,
                 "meta_blocks": metas, "names": len(res.funcs), "names_by_method": how,
                 "globals": len(res.globals), "callsites": len(res.calls)}
    return res
