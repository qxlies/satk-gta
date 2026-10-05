"""MTA client sources -> patch sites on gta_sa.exe (SPEC §4.9.1; owner WP-09).

Scans ``Client/multiplayer_sa`` and ``Client/game_sa`` of one tree (upstream, our trunk or Neon):

* ``#define HOOKPOS_X <expr>`` -> ``hookpos`` (the define line is the patch location);
* ``HookInstall(addr, hook, n)`` -> ``hookinstall`` (len n, default 5); ``EZHookInstall(X)`` and
  ``EZHookInstallChecked/Restore(X)``, ``MAKE_HOOK_INFO(X)`` -> ``hookinstall`` at ``HOOKPOS_X``
  (len ``HOOKSIZE_X``); ``HookInstallCall`` -> ``hookinstallcall`` (len 5);
  ``HookInstallMethod`` -> ``vtable_slot`` (len 4);
* ``MemPut<T>/MemPutFast<T>`` (and ``MemAdd/MemSub/MemAnd/MemOr``) -> ``memput`` (len sizeof T; the
  variant goes to ``note``), ``MemSet*`` -> ``memset``, ``MemCpy*`` -> ``memcpy`` (len = 3rd arg);
* ``Client/game_sa/*Manifest*.inc`` relocation manifests (Neon) -> ``reloc_manifest``.

Addresses are evaluated with :class:`~satk.re.cpp.ConstEval` over every ``#define``/``constexpr``/
``const DWORD`` in the scanned directories (and ``Client/sdk``), so ``HOOKPOS_X + 3``, ``VAR_Y`` and
``(DWORD)0x6194A0`` all resolve. Dynamic addresses (pointers, loop variables) are counted as
unresolved and skipped. Only sites inside the gta_sa.exe image are kept.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..cpp import ConstEval, LineIndex, blank, iter_calls, match_close, split_top
from ..gitsrc import SourceTree
from . import PatchSite

__all__ = ["MtaScan", "scan_mta", "SITE_PREFIXES", "DEF_PREFIXES", "IMAGE_LO", "IMAGE_HI"]

SITE_PREFIXES = ("Client/multiplayer_sa", "Client/game_sa")
DEF_PREFIXES = SITE_PREFIXES + ("Client/sdk",)
EXTS = (".cpp", ".h", ".hpp", ".inc")
IMAGE_LO, IMAGE_HI = 0x401000, 0x1577000

_DEFINE = re.compile(r"^[ \t]*#[ \t]*define[ \t]+([A-Za-z_]\w*)(?![\w(])[ \t]+([^\n]*?)[ \t]*$", re.M)
_CONSTS = [
    re.compile(r"\b(?:static\s+|inline\s+|constexpr\s+|const\s+)+(?:auto|DWORD|std::uintptr_t|uintptr_t|unsigned\s+int|"
               r"unsigned\s+long|int|uint|std::uint32_t|uint32_t|size_t|BYTE|WORD|unsigned\s+char|float)\s+"
               r"([A-Za-z_]\w*)\s*(?:=\s*([^;{}]+?)|\{\s*([^;{}]+?)\s*\})\s*;"),
    re.compile(r"^[ \t]*(?:static\s+)?DWORD\s+([A-Za-z_]\w*)\s*=\s*(0x[0-9A-Fa-f]+)\s*;", re.M),
]
_WRITERS = {
    "MemPut": ("memput", None), "MemPutFast": ("memput", "fast"),
    "MemAdd": ("memput", "add"), "MemSub": ("memput", "sub"), "MemAnd": ("memput", "and"),
    "MemOr": ("memput", "or"), "MemAndFast": ("memput", "and_fast"), "MemOrFast": ("memput", "or_fast"),
    "MemSet": ("memset", None), "MemSetFast": ("memset", "fast"),
    "MemCpy": ("memcpy", None), "MemCpyFast": ("memcpy", "fast"),
    "HookInstall": ("hookinstall", None), "HookInstallCall": ("hookinstallcall", None),
    "HookInstallMethod": ("vtable_slot", None),
}
_EZ = {"EZHookInstall": None, "EZHookInstallChecked": "checked", "EZHookInstallRestore": "restore",
       "MAKE_HOOK_INFO": "hook_info"}
_DECL_WORDS = {"return", "else", "do", "case", "co_return", "throw"}
_IDENT = re.compile(r"[A-Za-z_]\w*")
_NATIVE = re.compile(r"\b(NATIVE_[A-Z0-9_]+)\s*\(")
_ROW = re.compile(r"^[ \t]*\{\s*(0x[0-9A-Fa-f]{6,8})\b", re.M)
_ARRAY_HEAD = re.compile(r"\b([A-Za-z_]\w*)\s*\[\s*\]\s*=\s*\{")
_HEX = re.compile(r"^0x[0-9A-Fa-f]+$")


@dataclass
class MtaScan:
    patches: list[PatchSite] = field(default_factory=list)
    consts: ConstEval = field(default_factory=ConstEval)
    stats: dict = field(default_factory=dict)


def _is_decl(b: str, pos: int) -> bool:
    """True if the identifier at ``pos`` is being declared/defined, not called."""
    k = pos - 1
    while k >= 0 and b[k] in " \t\r\n":
        k -= 1
    if k < 0:
        return False
    c = b[k]
    if c == ">":                      # "->MemPut(" is a call; "Foo<T> MemPut(" declares
        return not (k > 0 and b[k - 1] == "-")
    if c in "*&":                     # "void* MemPut(" / "T& MemPut("
        return True
    if c.isalnum() or c == "_":       # "void MemPut(" declares, "return MemPut(" calls
        j = k
        while j >= 0 and (b[j].isalnum() or b[j] == "_"):
            j -= 1
        return b[j + 1:k + 1] not in _DECL_WORDS
    return False                      # "::MemPut(", ".MemPut(", "(MemPut(", "; MemPut(" are calls


def _addr(ev: ConstEval, expr: str) -> int | None:
    v = ev.eval(expr)
    if isinstance(v, int) and IMAGE_LO <= v < IMAGE_HI:
        return v
    return None


def _symbol(expr: str) -> str | None:
    for m in _IDENT.finditer(expr):
        w = m.group(0)
        if w.startswith(("HOOKPOS_", "VAR_", "FUNC_", "ADDR_", "CALL_", "HOOKCHECK_", "ARRAY_", "CLASS_")) or \
                (w.isupper() and len(w) > 3 and w not in ("DWORD", "BYTE", "WORD", "PVOID", "LPVOID", "BOOL", "PBYTE")):
            return w
    return None


def scan_mta(tree: SourceTree, *, manifests: bool = True) -> MtaScan:
    """Scan one MTA tree; ``patches`` carry ``src_file`` relative to the repository root."""
    res = MtaScan()
    ev = res.consts
    files = list(tree.files(DEF_PREFIXES, EXTS))
    for _path, text in files:
        b = blank(text)
        for m in _DEFINE.finditer(b):
            val = m.group(2).strip()
            if val:
                ev.define(m.group(1), val)
        for pat in _CONSTS:
            for m in pat.finditer(b):
                expr = m.group(2) if m.group(2) is not None else (m.group(3) if m.lastindex and m.lastindex >= 3 else None)
                if expr:
                    ev.define(m.group(1), expr.strip())
    patches: list[PatchSite] = []
    unresolved: dict[str, int] = {}
    calls: dict[str, list[int]] = {}
    hookpos_defs = 0
    for path, text in files:
        if not path.startswith(SITE_PREFIXES):
            continue
        b = blank(text)
        li = LineIndex(text)
        if "HOOKPOS_" in b:
            for m in _DEFINE.finditer(b):
                if not m.group(1).startswith("HOOKPOS_"):
                    continue
                hookpos_defs += 1
                a = _addr(ev, m.group(2))
                if a is None:
                    unresolved["hookpos"] = unresolved.get("hookpos", 0) + 1
                    continue
                size = ev.value("HOOKSIZE_" + m.group(1)[8:])
                patches.append(PatchSite(a, "hookpos", path, li.line(m.start(1)),
                                         len=size if isinstance(size, int) and 0 < size < 64 else None,
                                         symbol=m.group(1)))
        if manifests and path.endswith(".inc") and "Manifest" in path.rsplit("/", 1)[-1]:
            patches.extend(_manifest_sites(b, path, li))
        if not any(w in b for w in ("Mem", "Hook", "MAKE_HOOK_INFO")):
            continue
        bp = blank(text, preproc=True)
        for m, a0, a1 in iter_calls(bp, "|".join(sorted(_WRITERS, key=len, reverse=True))):
            fname = m.group(1)
            if _is_decl(bp, m.start(1)):
                continue
            kind, note = _WRITERS[fname]
            args = split_top(bp[a0:a1])
            if not args or not args[0]:
                continue
            a = _addr(ev, args[0])
            cnt = calls.setdefault(fname, [0, 0])
            cnt[0 if a is not None else 1] += 1
            if a is None:
                unresolved[kind] = unresolved.get(kind, 0) + 1
                continue
            length: int | None = None
            if kind == "memput" and m.group(2):
                tpl_end = match_close(bp, m.end() - 1)
                if tpl_end > 0:
                    t = split_top(bp[m.end():tpl_end - 1])[0]
                    length = ev.type_size(t)
            elif kind in ("memset", "memcpy") and len(args) >= 3:
                v = ev.eval(args[2])
                length = v if isinstance(v, int) and v > 0 else None
            elif kind == "hookinstall":
                v = ev.eval(args[2]) if len(args) >= 3 else 5
                length = v if isinstance(v, int) and v > 0 else None
            elif kind == "hookinstallcall":
                length = 5
            elif kind == "vtable_slot":
                length = 4
            patches.append(PatchSite(a, kind, path, li.line(m.start(1)), len=length,
                                     symbol=_symbol(args[0]), note=note if note else None))
        for m, a0, a1 in iter_calls(bp, "|".join(_EZ)):
            if _is_decl(bp, m.start(1)):
                continue
            name = bp[a0:a1].strip()
            if not re.fullmatch(r"[A-Za-z_]\w*", name):
                continue
            a = _addr(ev, "HOOKPOS_" + name)
            if a is None:
                unresolved["ezhook"] = unresolved.get("ezhook", 0) + 1
                continue
            size = ev.value("HOOKSIZE_" + name)
            patches.append(PatchSite(a, "hookinstall", path, li.line(m.start(1)),
                                     len=size if isinstance(size, int) and 0 < size < 64 else 5,
                                     symbol="HOOKPOS_" + name, note=_EZ[m.group(1)]))
    res.patches = patches
    kinds: dict[str, int] = {}
    for p in patches:
        k = p.kind + (f":{p.note}" if p.note in ("fast",) else "")
        kinds[k] = kinds.get(k, 0) + 1
    res.stats = {"files": len(files), "hookpos_defines": hookpos_defs, "sites": len(patches),
                 "by_kind": dict(sorted(kinds.items())), "unresolved": dict(sorted(unresolved.items())),
                 # call sites per writer: [resolved, unresolved] (comparable with plain source counts)
                 "calls": dict(sorted(calls.items()))}
    return res


def _manifest_sites(b: str, path: str, li: LineIndex) -> list[PatchSite]:
    out: list[PatchSite] = []
    for m in _NATIVE.finditer(b):
        end = match_close(b, m.end() - 1, angle=False)
        if end < 0:
            continue
        args = split_top(b[m.end():end - 1], angle=False)
        site = None
        cat = None
        for a in args:
            if _HEX.match(a):
                v = int(a, 16)
                if IMAGE_LO <= v < IMAGE_HI:
                    site = v
                break
            if cat is None and re.fullmatch(r"[A-Za-z_]\w*", a):
                cat = a
        if site is None:
            continue
        length = 4 if m.group(1).endswith("_POINTER") else None
        out.append(PatchSite(site, "reloc_manifest", path, li.line(m.start()), len=length, symbol=m.group(1),
                             note=cat))
    heads = [(h.start(), h.group(1)) for h in _ARRAY_HEAD.finditer(b)]
    for m in _ROW.finditer(b):
        v = int(m.group(1), 16)
        if not IMAGE_LO <= v < IMAGE_HI:
            continue
        name = None
        for pos, nm in heads:
            if pos < m.start():
                name = nm
        out.append(PatchSite(v, "reloc_manifest", path, li.line(m.start(1)), symbol=name))
    return out
