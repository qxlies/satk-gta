"""gta_sa.exe (HOODLUM 1.0 US) -> sections, function-start candidates, thunks, vtable slots (WP-09).

Without a disassembler (SPEC §8: Ghidra/capstone deferred) function starts come from byte
evidence in the main ``.text`` (0x401000-0x857000):

* ``call_target`` — targets of ``E8 rel32`` that are 16-byte aligned and preceded by a plausible
  function end (``ret``/``jmp``/padding byte);
* ``data_ptr`` — 16-byte aligned pointers into ``.text`` stored in ``.rdata``/``.data`` (function
  pointer tables, vtables), same plausibility rule;
* ``padding`` — 16-byte aligned addresses right after ``ret``/``jmp`` + a run of ``90``/``CC``
  padding (MSVC 7.1 aligns every function with ``nop``), unless the dword there looks like a
  switch table (points back into ``.text``).

Thunks at a function entry: ``E9 rel32`` into ``.HOODLUM`` (``hoodlum``), into ``.text``
(``other_jmp``) and ``6A FF E9 rel32`` (``seh_push_jmp``: ``push -1; jmp body``).
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field

from ..pe import PeImage, Section

__all__ = ["ExeScan", "scan_exe", "classify_thunk", "read_vtable", "TEXT_NAME", "HOODLUM_NAME"]

TEXT_NAME = ".text"
HOODLUM_NAME = ".HOODLUM"
_PAD = re.compile(rb"(?:\xc3|\xc2..|\xe9....|\xeb.)[\x90\xcc]+", re.S)


@dataclass
class ExeScan:
    text: Section
    hoodlum: Section | None
    e8_all: set[int] = field(default_factory=set)       # distinct E8 targets inside .text (no filter)
    e8_starts: set[int] = field(default_factory=set)    # aligned + plausible
    data_ptr_starts: set[int] = field(default_factory=set)
    padding_starts: set[int] = field(default_factory=set)
    hoodlum_jumps: dict[int, int] = field(default_factory=dict)  # entries need not be aligned or already named
    stats: dict = field(default_factory=dict)


def _plausible(code: bytes, base: int, t: int) -> bool:
    """A 16-aligned ``t`` preceded by a function end: padding/ret byte, ``ret n``, ``jmp``."""
    o = t - base
    if o < 5 or o >= len(code):
        return o == 0
    p = code[o - 1]
    return p in (0x90, 0xCC, 0xC3) or code[o - 3] == 0xC2 or code[o - 5] == 0xE9 or code[o - 2] == 0xEB


def scan_exe(img: PeImage) -> ExeScan:
    text = img.section(TEXT_NAME)
    if text is None:
        raise ValueError("no .text section")
    hood = img.section(HOODLUM_NAME)
    res = ExeScan(text=text, hoodlum=hood)
    code = img.raw(text)
    base = text.va
    end = text.va + min(text.vsize, text.raw_size) if text.vsize else text.va + text.raw_size
    n = len(code)
    unpack = struct.unpack_from
    for m in re.finditer(b"\xe8", code):
        i = m.start()
        if i + 5 > n:
            break
        t = base + i + 5 + unpack("<i", code, i + 1)[0]
        if base <= t < end:
            res.e8_all.add(t)
    res.e8_starts = {t for t in res.e8_all if t % 16 == 0 and _plausible(code, base, t)}
    if hood is not None and hood.executable:
        for m in re.finditer(b"\xe9", code):
            i = m.start()
            if i + 5 > n:
                break
            target = (base + i + 5 + unpack("<i", code, i + 1)[0]) & 0xFFFFFFFF
            body = img.read(target, 16) if hood.contains(target) else b""
            # E9 bytes can occur inside other instructions/data. A destination consisting
            # only of zero/NOP/INT3 padding is not evidence of a moved function.
            if body and any(byte not in (0, 0x90, 0xCC) for byte in body):
                res.hoodlum_jumps[base + i] = target
    for s in (img.section(".rdata"), img.section(".data")):   # the first (original) data sections
        if s is None or s.executable:
            continue
        blob = img.raw(s)
        for j in range(0, len(blob) - 3, 4):
            v = unpack("<I", blob, j)[0]
            if base <= v < end and v % 16 == 0 and _plausible(code, base, v):
                res.data_ptr_starts.add(v)
    for m in _PAD.finditer(code):
        p = base + m.end()
        if p % 16 or p >= end:
            continue
        o = p - base
        if o + 4 <= n:
            v = unpack("<I", code, o)[0]
            if base <= v < end:      # switch jump table, not code
                continue
        res.padding_starts.add(p)
    res.stats = {
        "e8_targets": len(res.e8_all),
        "e8_starts": len(res.e8_starts),
        "data_ptr_starts": len(res.data_ptr_starts),
        "padding_starts": len(res.padding_starts),
        "hoodlum_jumps": len(res.hoodlum_jumps),
    }
    return res


def classify_thunk(img: PeImage, addr: int) -> tuple[str, int] | None:
    """``(kind, target)`` if ``addr`` starts with a jump thunk, else ``None``."""
    b = img.read(addr, 7)
    if len(b) >= 5 and b[0] == 0xE9:
        t = (addr + 5 + struct.unpack_from("<i", b, 1)[0]) & 0xFFFFFFFF
        s = img.section_at(t)
        if s is None or not s.executable:
            return None
        return ("hoodlum" if s.name == HOODLUM_NAME else "other_jmp"), t
    if len(b) >= 7 and b[0] == 0x6A and b[1] == 0xFF and b[2] == 0xE9:
        t = (addr + 7 + struct.unpack_from("<i", b, 3)[0]) & 0xFFFFFFFF
        s = img.section_at(t)
        if s is None or not s.executable:
            return None
        return "seh_push_jmp", t
    return None


def read_vtable(img: PeImage, addr: int, slots: int) -> list[int]:
    """Slot values (function pointers) of a vtable; stops at unbacked memory."""
    out = []
    for i in range(max(0, min(slots, 4096))):
        v = img.u32(addr + 4 * i)
        if v is None:
            break
        out.append(v)
    return out
