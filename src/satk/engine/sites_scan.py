"""``satk engine sites-scan``: candidate patch operands for a static array (``--base --size --stride``).

A candidate is a 32-bit operand of an instruction in ``.text`` or ``.HOODLUM`` that holds an address in
``[base, base + size + stride]`` (the window is one stride longer than the array: end-plus-field
operands are legal) **and** that Ghidra has a data xref for. The xref's ``from`` is the instruction
address and the operand starts 1-7 bytes after it. Each candidate is classified by its instruction:

``operand-in-array``
    value below ``base + size``: an element address, to be rewritten when the array moves or grows.
``true-end``
    value == ``base + size`` in a compare (``81 /7 id``, ``3D id``) or ``lea``: a loop bound.
``end-plus-field``
    value in ``(base + size, base + size + stride]`` in a compare or ``lea``: a bound biased by a
    field offset (legal operand, patch together with the true end).
``variable-after-array``
    value >= ``base + size`` in anything else (memory access, ``mov reg, imm``, ``push imm``): the next
    object in memory, **never patch**.

Flags: ``static-init`` (the containing function has no code caller: CRT static constructors and
destructors, callbacks), ``in-hoodlum`` (the instruction lies in ``.HOODLUM``), ``in-relocated-function``
(the instruction lies in the dead ``.text`` slot of a relocated function), ``unclassified`` (the
instruction form was not recognised; classified by value only), ``no-array-ref-in-function`` (a bound whose
function has no ``operand-in-array`` reference: probably the bound of the next object).
Stdlib only.
"""

from __future__ import annotations

import bisect
import json
import re
import struct
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from ..re.pe import PeImage
from .sites_data import FuncInfo, RelocFunc
from .x86 import decode

__all__ = ["CLASSES", "Candidate", "ScanResult", "scan_array", "classify"]

CLASSES = ("operand-in-array", "true-end", "end-plus-field", "variable-after-array")
_ADDR_RE = re.compile(r'^\{"addr":\s*"0x([0-9A-Fa-f]+)"')


@dataclass(frozen=True, slots=True)
class Candidate:
    va: int  # operand address (first byte of the 32-bit field)
    insn: int  # instruction address
    value: int
    cls: str
    form: str
    flags: tuple[str, ...]
    func: int | None
    func_name: str
    raw: str  # instruction bytes, hex


@dataclass
class ScanResult:
    base: int
    size: int
    stride: int
    candidates: list[Candidate]
    notes: list[str] = field(default_factory=list)

    @property
    def end(self) -> int:
        return self.base + self.size

    def counts(self) -> dict[str, int]:
        c = Counter(x.cls for x in self.candidates)
        return {k: c.get(k, 0) for k in CLASSES}


def classify(value: int, end: int, ins, field_off: int) -> tuple[str, str]:
    """``(class, form)`` for an operand ``value`` whose field starts ``field_off`` bytes into ``ins``."""
    is_imm = ins is not None and ins.valid and ins.imm_len == 4 and ins.imm_off == field_off
    is_disp = ins is not None and ins.valid and ins.disp_len == 4 and ins.disp_off == field_off
    op = ins.opcode if ins is not None and ins.valid else -1
    if is_imm and (op == 0x3D or (op == 0x81 and ins.reg == 7)):
        form = "cmp imm32"
        bound = True
    elif is_disp and op == 0x8D:
        form = "lea"
        bound = True
    elif is_imm:
        form = "imm32 (mov/push/op)"
        bound = False
    elif is_disp and ins.mem == "abs":
        form = "mem abs"
        bound = False
    elif is_disp:
        form = "mem reg+disp32"
        bound = False
    else:
        form = "unrecognised"
        bound = False
    if value < end:
        return "operand-in-array", form
    if bound:
        return ("true-end" if value == end else "end-plus-field"), form
    return "variable-after-array", form


def _code_froms(path: Path) -> dict[int, tuple[int | None, str]]:
    """``{instruction address: (function entry, function name)}`` of every code data-xref of the export."""
    out: dict[int, tuple[int | None, str]] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.startswith("{"):
                continue
            d = json.loads(line)
            for r in d.get("refs", []):
                if r.get("from_kind") == "code":
                    fn = r.get("func")
                    out.setdefault(int(r["from"], 16), (int(fn, 16) if fn else None, str(r.get("func_name") or "")))
    return out


def _raw_hits(image: PeImage, ranges: list[tuple[int, int]], lo: int, hi: int) -> set[int]:
    """Addresses of 4-byte little-endian values in ``[lo, hi]`` in the given VA ranges (any alignment)."""
    out: set[int] = set()
    pairs = [(h & 0xFF, (h >> 8) & 0xFF) for h in range(lo >> 16, (hi >> 16) + 1)]
    for rlo, rhi in ranges:
        sec = image.section_at(rlo)
        if sec is None:
            continue
        buf = image.raw(sec)
        a = rlo - sec.va
        b = min(rhi - sec.va, len(buf))
        for b2, b3 in pairs:
            pat = re.compile(b"(?=(?s:..)" + re.escape(bytes((b2, b3))) + b")", re.S)
            for m in pat.finditer(buf, a, b):
                p = m.start()
                if p + 4 > b:
                    continue
                v = struct.unpack_from("<I", buf, p)[0]
                if lo <= v <= hi:
                    out.add(sec.va + p)
    return out


def scan_array(image: PeImage, xrefs_path: Path, base: int, size: int, stride: int, *,
               funcs: dict[int, FuncInfo] | None = None, callers: dict[int, set[str]] | None = None,
               relocs: list[RelocFunc] | None = None) -> ScanResult:
    """Candidates for the array ``[base, base + size)`` with element size ``stride`` (see module doc)."""
    end = base + size
    lo, hi = base, end + stride
    text = image.section(".text", 0)
    hood = image.section(".HOODLUM")
    ranges = [(s.va, s.va + min(s.vsize, s.raw_size)) for s in (text, hood) if s is not None]
    rel_sorted = sorted(relocs or [], key=lambda r: r.entry)
    rel_starts = [r.entry for r in rel_sorted]
    froms = _code_froms(xrefs_path)
    cands: list[Candidate] = []
    for va in sorted(_raw_hits(image, ranges, lo, hi)):
        value = image.u32(va)
        hit = None
        for k in range(1, 8):
            f = va - k
            info = froms.get(f)
            if info is None:
                continue
            raw = image.read(f, 16)
            ins = decode(raw, 0) if raw else None
            if ins is not None and ins.valid and any(ln == 4 and off == k for off, ln in
                                                     ((ins.disp_off, ins.disp_len), (ins.imm_off, ins.imm_len))):
                hit = (f, info, raw, ins, k)
                break
            if (ins is None or not ins.valid) and hit is None:
                hit = (f, info, raw, None, k)  # the decoder does not know this opcode: keep, classified by value
        if hit is None:
            continue
        frm, (func, fname), raw, ins, k = hit
        cls, form = classify(value, end, ins, k)
        flags: list[str] = []
        if func is not None and callers is not None and not callers.get(func):
            flags.append("static-init")
        if hood is not None and hood.va <= frm < hood.va + hood.vsize:
            flags.append("in-hoodlum")
        if rel_sorted:
            i = bisect.bisect_right(rel_starts, frm) - 1
            if i >= 0 and rel_sorted[i].entry <= frm < rel_sorted[i].end:
                flags.append("in-relocated-function")
        if form == "unrecognised":
            flags.append("unclassified")
        n = ins.length if ins is not None and ins.valid else min(8, len(raw))
        cands.append(Candidate(va, frm, value, cls, form, tuple(flags), func, fname, raw[:n].hex(" ")))
    # a bound (true-end / end-plus-field) in a function that never touches the array itself is
    # probably the bound of the next object: tell the author
    with_array = {c.func for c in cands if c.cls == "operand-in-array" and c.func is not None}
    out = []
    for c in cands:
        if c.cls in ("true-end", "end-plus-field") and c.func not in with_array:
            c = Candidate(c.va, c.insn, c.value, c.cls, c.form, c.flags + ("no-array-ref-in-function",),
                          c.func, c.func_name, c.raw)
        out.append(c)
    return ScanResult(base, size, stride, sorted(out, key=lambda c: (c.va, c.insn)))
