"""Crash reports of single-player GTA SA written into text logs (not MTA's ``core.log``).

Recognized report shapes (any log or a chat paste may hold several reports):

* ``Exception At Address: 0x00745BDC Base: 0x00400000`` (SA-MP crash dialog) and
  ``Exception at address: 0x0061A5C5, Last function processed: ...`` (mod_sa);
* ``Exception Address: 0x...`` / ``Exception Code: 0x...`` / ``Module: ...`` field blocks
  (crash handlers of ASI loaders, modloader.log and CrashInfo-style reports);
* ``Unhandled exception at 0x0040E6A3 in gta_sa.exe: 0xC0000005: Access violation reading location
  0x00000010`` (Visual Studio / WER text).

Inside a report: registers as ``EAX: 0x...`` or ``EAX=...``, the exception code or its name
(``EXCEPTION_ACCESS_VIOLATION``), the accessed address (``Attempted to read from: 0x...``,
``reading location 0x...``) and a backtrace after a ``Backtrace`` / ``Stack trace`` /
``Call stack`` line (one frame per line, ``=> 0x...``, ``gta_sa.exe+0x...``). Unknown lines are
ignored, so a report is never an error: :func:`parse` returns what it found. Stdlib only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .mta import EXCEPTION_NAMES, GTA_BASE, GTA_END, LogFrame, is_gta, parse_frame_line

__all__ = ["SpCrash", "parse", "has_crash"]

_HEX = r"(?:0x)?([0-9A-Fa-f]{1,8})"
_STARTS = [
    ("exception-at-address", re.compile(r"(?im)\bexception\s+at\s+address\s*[:=]?\s*(?:0x)?([0-9a-f]{1,8})\b")),
    ("unhandled-exception", re.compile(r"(?im)\bunhandled\s+exception\s+(?:at|@)\s+(?:0x)?([0-9a-f]{6,8})\b")),
    ("exception-address", re.compile(r"(?im)^\W*exception\s+address\s*[:=]\s*(?:0x)?([0-9a-f]{1,8})\b")),
    ("crashed-at", re.compile(r"(?im)\b(?:game\s+)?crash(?:ed)?\s+at\s+(?:address\s+)?0x([0-9a-f]{6,8})\b")),
]
_REG = re.compile(r"\b(EAX|EBX|ECX|EDX|ESI|EDI|EBP|ESP|EIP|EFLAGS)\s*[:=]\s*" + _HEX + r"\b", re.I)
_CODE = re.compile(r"(?i)\b(?:exception\s+)?code\s*[:=]\s*0x([0-9a-f]{8})\b|:\s*0x(c[0-9a-f]{7})\s*:")
_CODE_NAME = re.compile(r"\b(?:EXCEPTION_)?(ACCESS_VIOLATION|STACK_OVERFLOW|INT_DIVIDE_BY_ZERO|ILLEGAL_INSTRUCTION|"
                        r"PRIV_INSTRUCTION|IN_PAGE_ERROR|BREAKPOINT|FLT_\w+|ARRAY_BOUNDS_EXCEEDED|"
                        r"DATATYPE_MISALIGNMENT|HEAP_CORRUPTION|STACK_BUFFER_OVERRUN|INVALID_HANDLE)\b")
_MODULE = re.compile(r"(?im)^\W*(?:module|faulting module(?: name)?|module name)\s*[:=]\s*\"?([^\"\r\n]+?)\"?\s*(?:[,;(]|$)")
_IN_MODULE = re.compile(r"(?i)\bin\s+(?:module\s+)?\"?([\w.\- ~+]+?\.(?:exe|dll|asi|cleo))\b")
_BASE = re.compile(r"(?i)\bbase(?:\s+address)?\s*[:=]?\s*0x([0-9a-f]{6,8})\b")
_ACCESS = [
    re.compile(r"(?i)\battempted\s+to\s+(read|write|execute)\s+(?:from|to|at)?\s*:?\s*0x([0-9a-f]{1,8})\b"),
    re.compile(r"(?i)\b(read|writ|execut)(?:e|ing|ed)?\s+(?:from\s+|to\s+)?(?:location|address)\s*:?\s*0x([0-9a-f]{1,8})\b"),
]
_BT_HEAD = re.compile(r"(?i)^\W*(?:back\s*trace|stack\s*trace|call\s*stack|stack\s*back\s*trace)\b[^:]*:?\s*$")
_BT_HEAD_INLINE = re.compile(r"(?i)^\W*(?:back\s*trace|stack\s*trace|call\s*stack)\b[^:]*:\s*(\S.*)$")
_ARROW = re.compile(r"^\s*(?:=>|->|>|\*)\s*")
_ADDR_ONLY = re.compile(r"^\s*0x([0-9A-Fa-f]{6,8})\b")
_EXC_BY_NAME = {v: k for k, v in EXCEPTION_NAMES.items()}


@dataclass(slots=True)
class SpCrash:
    """One crash report found in a text."""

    kind: str                        # which start pattern recognized it
    start: int                       # character offset of the report in the text
    addr: int | None = None          # absolute exception address
    module: str | None = None        # base name of the faulting module (gta_sa.exe for the game)
    off: int | None = None           # offset in that module
    code: int | None = None
    access: str | None = None        # "reading 0x00000010"
    regs: dict[str, int] = field(default_factory=dict)
    frames: list[LogFrame] = field(default_factory=list)
    fields: dict[str, str] = field(default_factory=dict)
    text: str = ""


def _starts(text: str) -> list[tuple[int, str, int]]:
    hits: dict[int, tuple[int, str, int]] = {}
    for kind, rx in _STARTS:
        for m in rx.finditer(text):
            ls = text.rfind("\n", 0, m.start()) + 1
            if ls not in hits:
                hits[ls] = (ls, kind, int(m.group(1), 16))
    return sorted(hits.values())


def has_crash(text: str) -> bool:
    """Whether ``text`` contains at least one report :func:`parse` recognizes."""
    return any(rx.search(text) for _k, rx in _STARTS)


def _module_name(s: str) -> str | None:
    name = s.strip().strip('"').replace("/", "\\").rsplit("\\", 1)[-1].strip()
    if not name:
        return None
    return "gta_sa.exe" if is_gta(name) else name


def _block(text: str, kind: str, start: int, addr: int) -> SpCrash:
    c = SpCrash(kind, start, addr=addr, text=text)
    for m in _REG.finditer(text):
        c.regs.setdefault(m.group(1).lower(), int(m.group(2), 16))
    m = _CODE.search(text)
    if m:
        c.code = int(m.group(1) or m.group(2), 16)
    else:
        nm = _CODE_NAME.search(text)
        if nm:
            c.code = _EXC_BY_NAME.get(nm.group(1).upper())
        elif re.search(r"(?i)access\s+violation", text):
            c.code = 0xC0000005
    for rx in _ACCESS:
        am = rx.search(text)
        if am:
            verb = am.group(1).lower()
            verb = {"read": "reading", "writ": "writing", "write": "writing", "execut": "executing",
                    "execute": "executing"}.get(verb, verb)
            c.access = f"{verb} 0x{int(am.group(2), 16):08x}"
            break
    mm = _MODULE.search(text)
    first = text.splitlines()[0] if text else ""
    im = _IN_MODULE.search(first)
    if mm:
        c.module = _module_name(mm.group(1))
    elif im:
        c.module = _module_name(im.group(1))
    bm = _BASE.search(first) or _BASE.search(text)
    base = int(bm.group(1), 16) if bm else None
    if c.module is None and base == GTA_BASE:
        c.module = "gta_sa.exe"
    if c.module is None and GTA_BASE <= addr < GTA_END:
        c.module = "gta_sa.exe"
    if c.module == "gta_sa.exe":
        c.off = addr - GTA_BASE
    elif base is not None and c.module is not None and addr >= base:
        c.off = addr - base
    for line in text.splitlines()[1:]:
        fm = re.match(r"^\W*([A-Za-z][A-Za-z ]{1,30}?)\s*[:=]\s*(\S.*?)\s*$", line)
        if fm and not _REG.match(line.strip()) and len(c.fields) < 24:
            c.fields.setdefault(fm.group(1).strip(), fm.group(2))
    c.frames = _frames(text)
    return c


def _frames(text: str) -> list[LogFrame]:
    out: list[LogFrame] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        im = _BT_HEAD_INLINE.match(line)
        if im and re.search(r"0x[0-9A-Fa-f]{6,8}", im.group(1)):
            for a in re.findall(r"0x([0-9A-Fa-f]{6,8})", im.group(1)):
                fr = parse_frame_line(f"0x{a}")
                if fr is not None:
                    out.append(fr)
            i += 1
            continue
        if not _BT_HEAD.match(line):
            i += 1
            continue
        i += 1
        while i < len(lines):
            s = _ARROW.sub("", lines[i]).strip()
            if not s:
                if out:
                    break
                i += 1
                continue
            fr = parse_frame_line(s)
            if fr is None:
                break
            am = _ADDR_ONLY.match(s)
            if am and fr.va is None:
                fr.va = int(am.group(1), 16)          # "0x6C04A1B2 SilentPatchSA.asi+0x4A1B2"
            out.append(fr)
            i += 1
        if out:
            break
    return out


def parse(text: str) -> list[SpCrash]:
    """All crash reports of ``text`` in order (the first one is usually the real crash)."""
    starts = _starts(text)
    out: list[SpCrash] = []
    for n, (pos, kind, addr) in enumerate(starts):
        end = starts[n + 1][0] if n + 1 < len(starts) else len(text)
        # a report rarely spans more than ~200 lines; cut long tails (the rest of a log)
        chunk = text[pos:end]
        lines = chunk.splitlines()
        if len(lines) > 200:
            chunk = "\n".join(lines[:200])
        out.append(_block(chunk, kind, pos, addr))
    return out
