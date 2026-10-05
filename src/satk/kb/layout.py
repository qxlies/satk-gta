"""Struct layouts for the knowledge base (owner M2-02): member offsets on the 32-bit MSVC target.

:class:`Layouts` computes ``(size, align)`` of types and the offsets of class members from the
:class:`~satk.kb.cxx.ClassDef` records of one source:

* bases first (a class that introduces virtual methods gets a vtable pointer at 0), then the
  members in order with natural alignment capped by ``#pragma pack``; unions overlap;
  anonymous structs/unions are laid out as blocks and flattened;
* MSVC bit-fields: consecutive bit-fields share a storage unit while the declared type has the
  same size and the bits fit;
* sizes of named types: computed layouts, asserted sizes (``VALIDATE_SIZE``/``static_assert``),
  primitives, pointers, enums (underlying type), arrays (``T[N]``, ``std::array<T, N>``) via the
  source's :class:`~satk.re.cpp.ConstEval`;
* anchors: asserted member offsets (``VALIDATE_OFFSET``/``offsetof``, also from another source for
  the same class and member name) override the running offset, so one unknown type does not make
  every later offset unknown.

The verdict per class (``layout``): ``ok`` — computed size equals the asserted one; ``mismatch``;
``unverified`` — no asserted size; ``partial`` — some member type has no known size.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

from ..re.cpp import PRIMITIVE_SIZES, ConstEval, array_info, norm_type, split_top
from .cxx import ClassDef, Member

__all__ = ["Field", "ClassLayout", "Layouts"]

_ALIGN8 = {"double", "int64", "uint64", "int64_t", "uint64_t", "long long", "unsigned long long", "__int64",
           "unsigned __int64", "LARGE_INTEGER", "std::int64_t", "std::uint64_t", "RwInt64", "RwUInt64"}
#: Common types the sources use without defining them in the scanned tree.
_EXTERNAL = {
    "RwV3d": (12, 4), "RwV2d": (8, 4), "RwRGBA": (4, 1), "RwRGBAReal": (16, 4), "RwMatrix": (64, 4),
    "RwSphere": (16, 4), "RwBBox": (24, 4), "RwRect": (16, 4), "RwLLLink": (8, 4), "RwLinkList": (8, 4),
    "RwObject": (8, 4), "RwTexCoords": (8, 4), "RwSurfaceProperties": (12, 4),
    "D3DMATRIX": (64, 4), "D3DXMATRIX": (64, 4), "D3DCOLORVALUE": (16, 4), "D3DVECTOR": (12, 4),
    "RECT": (16, 4), "POINT": (8, 4), "GUID": (16, 4), "CRITICAL_SECTION": (24, 4), "OVERLAPPED": (20, 4),
    "std::string": (24, 4), "std::wstring": (24, 4), "std::mutex": (80, 8),
    "std::atomic<bool>": (1, 1), "std::atomic<int>": (4, 4), "std::atomic<uint32>": (4, 4),
    "std::atomic<int32>": (4, 4), "std::atomic_bool": (1, 1),
}
_PTR_WRAPPERS = re.compile(r"^(?:notsa::)?(?:EntityRef|RegisteredRef|RefPtr|ref_ptr|unique_ptr_t|std::unique_ptr)<.*>$")


@dataclass(slots=True)
class Field:
    name: str
    type: str
    off: int | None
    size: int | None
    bit: int | None = None
    bits: int | None = None
    src: str = "calc"          # assert | <source key> | calc
    line: int | None = None
    note: str | None = None


@dataclass
class ClassLayout:
    cls: ClassDef
    size: int | None
    align: int
    fields: list[Field] = field(default_factory=list)
    vptr: bool = False
    unknown: list[str] = field(default_factory=list)   # member types without a known size
    anchors_used: int = 0
    anchor_conflicts: int = 0


def _al(off: int, a: int) -> int:
    return (off + a - 1) // a * a if a > 1 else off


def _guess_align(size: int) -> int:
    return 4 if size % 4 == 0 else 2 if size % 2 == 0 else 1


class Layouts:
    """Layouts of one source's classes (memoized; cycles and unknown types give ``None``)."""

    def __init__(self, classes: list[ClassDef], ev: ConstEval, *, sizes: dict[str, int] | None = None,
                 offsets: dict[tuple[str, str], tuple[int, str]] | None = None,
                 xoffsets: dict[tuple[str, str], tuple[int, str]] | None = None,
                 enum_sizes: dict[str, int] | None = None):
        self.ev = ev
        self.sizes = dict(sizes or {})                 # asserted sizes: type -> bytes
        self.offsets = dict(offsets or {})             # (class short name, member) -> (offset, "assert")
        self.xoffsets = dict(xoffsets or {})           # same, asserted by another source (advisory)
        self.enum_sizes = dict(enum_sizes or {})
        self._short_sizes: dict[str, set[int]] = {}
        for k, v in self.sizes.items():
            self._short_sizes.setdefault(k.rsplit("::", 1)[-1], set()).add(v)
        self.by_name: dict[str, list[ClassDef]] = {}
        self.templates: dict[str, ClassDef] = {}
        for c in classes:
            if c.template:
                if c.tparams:
                    for key in (c.name, c.short):
                        cur = self.templates.get(key)
                        if cur is None or len(c.members) > len(cur.members):
                            self.templates[key] = c
                continue
            for key in {c.name, c.short}:
                self.by_name.setdefault(key, []).append(c)
        self._memo: dict[int, ClassLayout | None] = {}
        self._busy: set[int] = set()
        self._inst: dict[str, ClassDef | None] = {}

    # -- lookup -----------------------------------------------------------------------

    def best_def(self, name: str) -> ClassDef | None:
        """The definition of ``name`` with the most members (asserted size preferred)."""
        cands = self.by_name.get(name) or self.by_name.get(name.rsplit("::", 1)[-1]) or []
        if not cands:
            return None
        return max(cands, key=lambda c: (c.name == name, len(c.members) + len(c.bases), -c.line))

    def asserted(self, name: str) -> int | None:
        """Asserted size of ``name``; a short name only when all its asserts agree."""
        if name in self.sizes:
            return self.sizes[name]
        vals = self._short_sizes.get(name.rsplit("::", 1)[-1])
        if vals and len(vals) == 1:
            return next(iter(vals))
        return None

    def size_align(self, t: str, _depth: int = 0) -> tuple[int, int] | None:
        """``(sizeof, alignof)`` of type ``t`` or ``None``."""
        if _depth > 16:
            return None
        t = norm_type(t)
        if not t:
            return None
        if t.endswith(("*", "&")) or _PTR_WRAPPERS.match(t):
            return 4, 4
        info = array_info(t, self.ev)
        if info is not None:
            elem, n = info
            sa = self.size_align(elem, _depth + 1)
            if sa is None or n is None:
                return None
            return sa[0] * n, sa[1]
        base = t[5:] if t.startswith("std::") and t[5:] in PRIMITIVE_SIZES else t
        if base in PRIMITIVE_SIZES and PRIMITIVE_SIZES[base]:
            s = PRIMITIVE_SIZES[base]
            return s, 8 if base in _ALIGN8 or t in _ALIGN8 else s
        m = re.match(r"^(?:notsa::)?WEnum([US])(8|16|32|64)<", t)
        if m:
            s = int(m.group(2)) // 8
            return s, s
        if t in _EXTERNAL:
            return _EXTERNAL[t]
        for key in (t, t.rsplit("::", 1)[-1]):
            if key in self.enum_sizes:
                s = self.enum_sizes[key]
                return s, s
            if key in self.ev.enum_sizes and key not in self.by_name:
                s = self.ev.enum_sizes[key]
                return s, s
        d = self.best_def(t) if "<" not in t else None
        if d is not None:
            a = self.asserted(t) or self.asserted(d.name)   # an asserted size beats a computed one
            lay = self.layout(d)
            if a is not None:
                al = lay.align if lay is not None and lay.size is not None and not lay.unknown else _guess_align(a)
                return a, al if a % al == 0 else _guess_align(a)
            if lay is not None and lay.size is not None and not lay.unknown:
                return lay.size, lay.align
            return None
        a = self.asserted(t)
        if a is not None:
            return a, _guess_align(a)
        if "<" in t:
            inst = self.instantiate(t)
            if inst is not None:
                lay = self.layout(inst)
                if lay is not None and lay.size is not None and not lay.unknown:
                    return lay.size, lay.align
        m = re.match(r"^(?:[\w:]*::)?(e\w+?)([SU])(8|16|32|64)$", t)
        if m and (m.group(1) in self.ev.enum_sizes or m.group(1) in self.enum_sizes):
            s = int(m.group(3)) // 8     # NOTSA_WENUM_DEFS_FOR(eX): eXS16 = WEnumS16<eX>
            return s, s
        for key in (t, t.rsplit("::", 1)[-1]):
            if key in self.ev.aliases and self.ev.aliases[key] != t:
                return self.size_align(self.ev.aliases[key], _depth + 1)
        s = self.ev.type_size(t)
        if s:
            return s, _guess_align(s)
        return None

    def instantiate(self, t: str) -> ClassDef | None:
        """``FixedFloat<int16, 8.f>`` -> a copy of the template with parameters substituted."""
        if t in self._inst:
            return self._inst[t]
        self._inst[t] = None
        m = re.match(r"^([\w:]+)<(.*)>$", t)
        if not m:
            return None
        tdef = self.templates.get(m.group(1)) or self.templates.get(m.group(1).rsplit("::", 1)[-1])
        if tdef is None:
            return None
        args = split_top(m.group(2))
        subst = dict(zip(tdef.tparams, args))

        def sub(text: str) -> str:
            for k, v in subst.items():
                text = re.sub(rf"(?<![\w:]){re.escape(k)}(?!\w)", v, text)
            return norm_type(text)

        def copy(d: ClassDef) -> ClassDef:
            return replace(d, name=t if d is tdef else d.name, template=False, tparams=[],
                           bases=[sub(b) for b in d.bases],
                           members=[replace(mm, type=sub(mm.type), bits=sub(mm.bits) if mm.bits else None,
                                            nested=copy(mm.nested) if mm.nested else None,
                                            block=copy(mm.block) if mm.block else None) for mm in d.members])

        inst = copy(tdef)
        self._inst[t] = inst
        return inst

    # -- layout -----------------------------------------------------------------------

    def _polymorphic(self, c: ClassDef, depth: int = 0) -> bool:
        if c.virtual:
            return True
        if depth > 12:
            return False
        for b in c.bases:
            d = self.best_def(b)
            if d is not None and d is not c and self._polymorphic(d, depth + 1):
                return True
        return False

    def layout(self, c: ClassDef) -> ClassLayout | None:
        key = id(c)
        if key in self._memo:
            return self._memo[key]
        if key in self._busy:
            return None
        self._busy.add(key)
        try:
            lay = self._compute(c)
        finally:
            self._busy.discard(key)
        self._memo[key] = lay
        return lay

    def _compute(self, c: ClassDef) -> ClassLayout:
        """Own asserted offsets first; another source's offsets only when the plain layout fails."""
        a = self._pass(c, use_x=False)
        asserted = self.asserted(c.name)
        good = not a.unknown and a.size is not None and (asserted is None or a.size == asserted)
        if good or not self.xoffsets:
            return self._annotate(c, a)
        b = self._pass(c, use_x=True)

        def score(x: ClassLayout) -> tuple:
            return (x.size is not None and x.size == asserted, sum(1 for f in x.fields if f.off is not None),
                    -x.anchor_conflicts)

        return b if score(b) > score(a) else self._annotate(c, a)

    def _annotate(self, c: ClassDef, lay: ClassLayout) -> ClassLayout:
        """Mark fields that another source asserts: agreement -> its name in ``src``, else a note."""
        if not self.xoffsets:
            return lay
        for f in lay.fields:
            x = self.xoffsets.get((c.short, f.name)) if f.bit in (None, 0) else None
            if x is None or f.src != "calc":
                continue
            if f.off == x[0]:
                f.src = x[1]
                lay.anchors_used += 1
            elif f.off is not None:
                f.note = f"{x[1]} 0x{x[0]:x}"
                lay.anchor_conflicts += 1
        return lay

    def _pass(self, c: ClassDef, *, use_x: bool) -> ClassLayout:
        lay = ClassLayout(cls=c, size=None, align=1)
        off: int | None = 0
        maxal = 1
        base_poly = False
        bases: list[tuple[str, tuple[int, int] | None]] = []
        for b in c.bases:
            d = self.best_def(b)
            if d is not None and self._polymorphic(d):
                base_poly = True
            bases.append((b, self.size_align(b)))
        if c.virtual and not base_poly:
            lay.vptr = True
            off, maxal = 4, 4
        for b, sa in bases:
            if sa is None:
                lay.unknown.append(b)
                off = None
                continue
            d = self.best_def(b)
            empty = d is not None and not d.members and not d.bases and not d.virtual
            if off is not None and not empty:
                off = _al(off, sa[1]) + sa[0]
            maxal = max(maxal, sa[1])
        start_off = off
        off, maxal2 = self._members(c, c.members, off, lay, union=(c.kind == "union"), use_x=use_x)
        maxal = max(maxal, maxal2)
        if c.align:
            maxal = max(maxal, c.align)
        if off is not None:
            size = _al(off, maxal)
            if size == 0 and start_off == 0:
                size = 1  # empty class
            lay.size = size
        lay.align = maxal
        return lay

    def _anchor(self, cls_short: str, name: str, use_x: bool) -> tuple[int, str] | None:
        a = self.offsets.get((cls_short, name))
        if a is None and use_x:
            a = self.xoffsets.get((cls_short, name))
        return a

    def _members(self, c: ClassDef, members: list[Member], off: int | None, lay: ClassLayout, *,
                 union: bool, use_x: bool = False) -> tuple[int | None, int]:
        """Lay out ``members`` starting at ``off``; returns (end offset, max alignment)."""
        maxal = 1
        start = off
        end = off
        unit: tuple[int, int, int] | None = None   # (unit offset, unit size, bits used)
        pack = c.pack
        for m in members:
            if m.block is not None:
                sub = m.block
                sub_lay = ClassLayout(cls=sub, size=None, align=1)
                s_off, s_al = self._members(sub, sub.members, 0, sub_lay, union=(sub.kind == "union"))
                if sub.align:
                    s_al = max(s_al, sub.align)
                a = min(s_al, pack) if pack else s_al
                maxal = max(maxal, a)
                unit = None
                at = None if (start if union else off) is None else _al(start if union else off, a)
                for f in sub_lay.fields:
                    lay.fields.append(Field(f.name, f.type, None if at is None or f.off is None else at + f.off,
                                            f.size, f.bit, f.bits, f.src, f.line, f.note))
                lay.unknown.extend(sub_lay.unknown)
                size = None if s_off is None else _al(s_off, s_al)
                if union:
                    if at is not None and size is not None and end is not None:
                        end = max(end, at + size)
                    elif size is None:
                        end = None
                else:
                    off = None if at is None or size is None else at + size
                continue
            t = m.type
            if m.nested is None:
                sa = self.size_align(t)
            else:
                sa = self._nested_sa(m.nested)
                for dim in re.findall(r"\[([^\]]+)\]", t):   # struct { ... } m_a[2];
                    n = self.ev.eval(dim)
                    sa = (sa[0] * n, sa[1]) if sa is not None and isinstance(n, int) else None
            anchor = self._anchor(c.short, m.name, use_x) if m.name else None
            cur = start if union else off
            note = None
            if sa is None:
                lay.unknown.append(t)
            a = 1
            if sa is not None:
                a = sa[1] if m.align is None else max(sa[1], m.align)
                if pack:
                    a = min(a, pack)
                maxal = max(maxal, a)
            bits = None
            if m.bits is not None:
                bv = self.ev.eval(m.bits)
                bits = bv if isinstance(bv, int) else None
            if bits is not None and sa is not None and not union:
                usize = sa[0]
                if bits == 0:
                    unit = None
                    continue
                if unit is not None and unit[1] == usize and unit[2] + bits <= usize * 8 and cur is not None:
                    uoff, _us, used = unit
                    lay.fields.append(Field(m.name, t, uoff, usize, used, bits, "calc", m.line))
                    unit = (uoff, usize, used + bits)
                    continue
                at = None if cur is None else _al(cur, a)
                src = "calc"
                if anchor is not None:
                    if at is not None and at != anchor[0]:
                        note = f"calc 0x{at:x}"
                        lay.anchor_conflicts += 1
                    at, src = anchor
                    lay.anchors_used += 1
                lay.fields.append(Field(m.name, t, at, usize, 0, bits, src, m.line, note))
                unit = (at, usize, bits) if at is not None else None
                off = None if at is None else at + usize
                continue
            unit = None
            at = None if cur is None or sa is None else _al(cur, a)
            src = "calc"
            if anchor is not None:
                if at is not None and at != anchor[0]:
                    note = f"calc 0x{at:x}"
                    lay.anchor_conflicts += 1
                at, src = anchor
                lay.anchors_used += 1
            size = sa[0] if sa is not None else None
            lay.fields.append(Field(m.name, t, at, size, None, None, src, m.line, note))
            if m.nested is not None and size is not None and at is not None:
                nl = self.layout(m.nested)
                if nl is not None:
                    for f in nl.fields:
                        if f.off is not None:
                            lay.fields.append(Field(f"{m.name}.{f.name}", f.type, at + f.off, f.size, f.bit,
                                                    f.bits, f.src, f.line, f.note))
            if union:
                if at is not None and size is not None and end is not None:
                    end = max(end, at + size)
                else:
                    end = None
            else:
                off = None if at is None or size is None else at + size
        return (end if union else off), maxal

    def _nested_sa(self, d: ClassDef) -> tuple[int, int] | None:
        lay = self.layout(d)
        if lay is None or lay.size is None:
            return None
        return lay.size, lay.align
