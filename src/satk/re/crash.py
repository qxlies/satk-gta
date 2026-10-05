"""Extract code addresses from free text: bare addresses, ``module+0xOFF``, MTA crash dumps (WP-09).

Understood (SPEC §4.9.3 step 1; formats of MTA ``Client/core/CCrashDumpWriter.cpp``):

* ``0x53BF09``, ``53BF09`` (a whole token), ``gta_sa.exe+0x13BF09``, ``core.dll+0x1234``;
* ``Reason: ... at gta_sa.exe+0x0013BF09 (IDA: 0x0053BF09)`` (the IDA value is used for gta_sa.exe);
* ``Module = C:\\...\\gta_sa.exe`` followed by ``Offset = 0x0013BF09`` (offset relative to the module);
* ``Module: ...`` / ``Module Offset: 0x...``, ``Resolved Module Offset``/``Resolved IDA Address``;
* ``EIP=0x0053BF09`` / ``EIP=0053BF09`` register values that fall inside gta_sa.exe.

Addresses of other modules are returned as ``module+off`` (their symbolization is WP-15).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = ["CrashItem", "parse_text", "GTA_MODULES", "IMAGE_BASE", "IMAGE_END"]

IMAGE_BASE = 0x400000
IMAGE_END = 0x1577000
GTA_MODULES = {"gta_sa.exe", "gta_sa_mta.exe", "gta-sa.exe", "proxy_sa.exe", "gta_sa"}

_MODPLUS = re.compile(r"(?<![\w.])([A-Za-z0-9_\-.]+\.(?:exe|dll|asi|cleo|so))\s*\+\s*"
                      r"(0x[0-9A-Fa-f]+|[0-9A-Fa-f]+)\b(?:\s*\(IDA(?:\s+via\s+[^:)]*)?:\s*(0x[0-9A-Fa-f]+)\))?")
_MODULE_LINE = re.compile(r"^\s*(?:Resolved\s+)?Module(?:\s+Name|\s+Path)?\s*[=:]\s*(.+?)\s*$", re.I)
_MODULE_BASE_LINE = re.compile(r"^\s*(?:Resolved\s+)?Module\s+Base(?:\s+Address)?\s*[=:]", re.I)
_OFFSET_LINE = re.compile(r"\b(?:Resolved\s+)?(?:Module\s+)?(?:Offset|RVA)\s*[=:]\s*(0x[0-9A-Fa-f]+)", re.I)
_IDA_LINE = re.compile(r"\b(?:Resolved\s+)?IDA(?:\s+Address)?\s*[=:]\s*(0x[0-9A-Fa-f]+)", re.I)
_EIP = re.compile(r"\bEIP\s*=\s*(?:0x)?([0-9A-Fa-f]{6,8})\b")
_HEX = re.compile(r"(?<![\w+])0x([0-9A-Fa-f]{5,8})\b")
_REG_VALUE = re.compile(r"\b(?:EAX|EBX|ECX|EDX|ESI|EDI|EBP|ESP|EIP|EFL|EFLAGS|FLG|CS|DS|SS|ES|FS|GS|Code|"
                        r"Exception\s+Code)\s*[=:]\s*(?:0x)?[0-9A-Fa-f]+\b", re.I)
_BARE = re.compile(r"^(?:0x)?[0-9A-Fa-f]{5,8}$")


@dataclass(frozen=True, slots=True)
class CrashItem:
    raw: str
    module: str                 # 'gta_sa.exe' or another module name ('?' when unknown)
    off: int | None             # module-relative offset (RVA) when known
    addr: int | None            # absolute gta_sa.exe address (None for other modules)

    @property
    def is_gta(self) -> bool:
        return self.addr is not None


def _basename(p: str) -> str:
    return re.split(r"[\\/]", p.strip().strip('"'))[-1].strip()


def _gta(mod: str) -> bool:
    return mod.lower() in GTA_MODULES


def parse_text(text: str) -> list[CrashItem]:
    """Unique addresses in order of appearance."""
    out: list[CrashItem] = []
    seen: set[tuple] = set()

    def add(item: CrashItem) -> None:
        key = ("gta", item.addr) if item.addr is not None else (item.module.lower(), item.off)
        if key not in seen:
            seen.add(key)
            out.append(item)

    tokens = text.split()
    if tokens and all(_BARE.match(t.strip(",;")) or ("+" in t and _MODPLUS.fullmatch(t.strip(",;"))) for t in tokens):
        for t in tokens:
            t = t.strip(",;")
            m = _MODPLUS.fullmatch(t)
            if m:
                _add_modplus(m, t, add)
            else:
                a = int(t, 16)
                add(CrashItem(t, "gta_sa.exe", a - IMAGE_BASE, a))
        return out

    module: str | None = None
    for line in text.splitlines():
        if _MODULE_BASE_LINE.match(line):
            continue  # module metadata, not an instruction pointer or a new module name
        consumed = False
        for m in _MODPLUS.finditer(line):
            _add_modplus(m, m.group(0).strip(), add)
            consumed = True
        mm = _MODULE_LINE.match(line)
        if mm and not _OFFSET_LINE.search(line):
            module = _basename(mm.group(1))
            continue
        om = _OFFSET_LINE.search(line)
        if om and not consumed:
            off = int(om.group(1), 16)
            mod = module or "?"
            if _gta(mod):
                add(CrashItem(om.group(0).strip(), "gta_sa.exe", off, IMAGE_BASE + off))
            else:
                add(CrashItem(om.group(0).strip(), mod, off, None))
            consumed = True
        im = _IDA_LINE.search(line)
        if im and not consumed and (module is None or _gta(module)):
            a = int(im.group(1), 16)
            if IMAGE_BASE <= a < IMAGE_END:
                add(CrashItem(im.group(0).strip(), "gta_sa.exe", a - IMAGE_BASE, a))
            consumed = True
        for em in _EIP.finditer(line):
            a = int(em.group(1), 16)
            if IMAGE_BASE <= a < IMAGE_END:
                add(CrashItem(em.group(0).strip(), "gta_sa.exe", a - IMAGE_BASE, a))
            consumed = True
        if consumed:
            continue
        for hm in _HEX.finditer(_REG_VALUE.sub(" ", line)):   # register values are data, not code
            a = int(hm.group(1), 16)
            if IMAGE_BASE <= a < IMAGE_END:
                add(CrashItem(hm.group(0), "gta_sa.exe", a - IMAGE_BASE, a))
    return out


def _add_modplus(m: re.Match, raw: str, add) -> None:
    mod = _basename(m.group(1))
    off = int(m.group(2), 16)
    if _gta(mod):
        a = int(m.group(3), 16) if m.group(3) else IMAGE_BASE + off
        add(CrashItem(raw, "gta_sa.exe", a - IMAGE_BASE, a))
    else:
        add(CrashItem(raw, mod, off, None))
