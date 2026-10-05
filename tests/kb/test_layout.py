"""satk.kb.layout: MSVC x86 struct layouts from parsed classes (synthetic C++ only)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from kb_synth import ENTITY_H  # noqa: E402

from satk.kb.cxx import scan_file  # noqa: E402
from satk.kb.layout import Layouts  # noqa: E402
from satk.re.cpp import ConstEval  # noqa: E402


def _layouts(src: str, **kw) -> Layouts:
    fs = scan_file("t.h", src)
    ev = ConstEval()
    for c in fs.consts:
        if c.kind == "const":
            ev.define(c.name, c.expr)
    for a, t in fs.aliases:
        ev.alias(a, t)
    for e, under in fs.enum_types:
        ev.enum_sizes[e] = {"int16": 2, "uint8": 1, "int8": 1}.get(under or "", 4)
    sizes = kw.pop("sizes", None)
    if sizes is None:
        sizes = {a.type: ev.eval(a.expr) for a in fs.asserts if a.kind == "size"}
    return Layouts(fs.classes, ev, sizes=sizes, **kw)


def _fields(L: Layouts, name: str) -> dict:
    lay = L.layout(L.best_def(name))
    return {f.name: (f.off, f.size, f.bit, f.bits) for f in lay.fields}


def test_alignment_and_padding():
    L = _layouts("struct S { uint8 a; uint32 b; uint16 c; double d; uint8 e; };")
    f = _fields(L, "S")
    assert f["a"][0] == 0 and f["b"][0] == 4 and f["c"][0] == 8 and f["d"][0] == 16 and f["e"][0] == 24
    lay = L.layout(L.best_def("S"))
    assert lay.size == 32 and lay.align == 8


def test_pragma_pack():
    L = _layouts("#pragma pack(push, 1)\nstruct P { uint8 a; uint32 b; };\n#pragma pack(pop)\n")
    assert _fields(L, "P")["b"][0] == 1
    assert L.layout(L.best_def("P")).size == 5


def test_entity_layout_matches_asserted_sizes():
    L = _layouts(ENTITY_H)
    plc = L.layout(L.best_def("CPlaceable"))
    assert plc.vptr and plc.size == 0x14
    ped = L.layout(L.best_def("CPed"))
    assert not ped.vptr and ped.size == 0x50 and not ped.unknown
    f = {x.name: x for x in ped.fields}
    assert f["field_14"].off == 0x14
    # bit-fields share one byte: bool:1, bool:1, uint8:2, bool:1
    assert (f["bIsStanding"].off, f["bIsStanding"].bit) == (0x18, 0)
    assert (f["knock"].off, f["knock"].bit, f["knock"].bits) == (0x18, 2, 2)
    assert (f["bLast"].off, f["bLast"].bit) == (0x18, 4)
    assert f["m_wArea"].off == 0x1A and f["m_fHealth"].off == 0x1C
    assert f["m_aPoints"].off == 0x24 and f["m_aPoints"].size == 24
    assert f["m_nRaw"].off == f["m_fRaw"].off == 0x3C           # union members overlap
    assert f["m_sub"].off == 0x40 and f["m_sub"].size == 8       # struct Sub {...} m_sub[2]
    assert f["m_sub.b"].off == 0x42
    assert f["m_pCallback"].off == 0x48
    assert f["m_weapon"].off == 0x4C and f["m_weapon"].size == 2  # eWeaponTypeS16 (WEnum)


def test_template_instantiation():
    L = _layouts(ENTITY_H)
    assert L.size_align("FixedFloat<int16, 8.f>") == (2, 2)
    assert L.layout(L.best_def("Packed")).size == 4


def test_asserted_size_beats_computed():
    L = _layouts("struct W { uint32 a; uint32 b; };\nstruct H { W w; uint8 z; };", sizes={"W": 12})
    assert _fields(L, "H")["z"][0] == 12


def test_anchor_resyncs_after_unknown_type_and_flags_conflicts():
    src = "struct A { Mystery m; float x; float y; };"
    L = _layouts(src, offsets={("A", "y"): (0x40, "plugin-sdk")})
    lay = L.layout(L.best_def("A"))
    f = {x.name: x for x in lay.fields}
    assert f["x"].off is None and lay.unknown == ["Mystery"]
    assert f["y"].off == 0x40 and f["y"].src == "plugin-sdk"
    L2 = _layouts("struct B { float x; float y; };", offsets={("B", "y"): (0x8, "assert")})
    f2 = {x.name: x for x in L2.layout(L2.best_def("B")).fields}
    assert f2["y"].off == 8 and f2["y"].note == "calc 0x4"
    assert L2.layout(L2.best_def("B")).anchor_conflicts == 1


def test_inheritance_vptr_and_empty_base():
    src = """
struct Empty {};
class Base { public: virtual void F(); int a; };
class Derived : public Base, public Empty { public: int b; };
class NoVirt { int c; };
class Poly : public NoVirt { public: virtual ~Poly(); int d; };
"""
    L = _layouts(src)
    assert L.layout(L.best_def("Base")).size == 8
    assert _fields(L, "Derived")["b"][0] == 8
    poly = L.layout(L.best_def("Poly"))
    assert poly.vptr and {f.name: f.off for f in poly.fields}["d"] == 8


def test_bitfield_unit_change_starts_new_unit():
    L = _layouts("struct F { uint8 a : 3; uint8 b : 6; uint32 c : 4; };")
    f = _fields(L, "F")
    assert f["a"][:3] == (0, 1, 0) and f["b"][:3] == (1, 1, 0)
    assert f["c"][:3] == (4, 4, 0)
    assert L.layout(L.best_def("F")).size == 8
