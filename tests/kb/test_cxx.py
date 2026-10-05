"""satk.kb.cxx: definitions, classes, constants, asserts, chunks (synthetic C++ only)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from kb_synth import ENTITY_H, MTA_PED_H, STREAMING_CPP, STREAMING_H  # noqa: E402

from satk.kb.cxx import camel_words, chunks, exe_addrs, scan_file  # noqa: E402


def _cls(fs, name):
    return next(c for c in fs.classes if c.name == name)


def test_function_definitions_with_address_and_doc():
    fs = scan_file("source/game_sa/Streaming.cpp", STREAMING_CPP)
    by = {f.name: f for f in fs.funcs}
    rm = by["CStreaming::RequestModel"]
    assert rm.addr == 0x4087E0
    assert rm.doc == "Request a given model to be loaded."
    assert rm.sig == "void CStreaming::RequestModel(int32 modelId, int32 streamingFlags)"
    assert rm.line == 11
    assert by["CStreaming::RequestModelStream"].addr == 0x40CBA0
    assert by["CStreaming::InjectHooks"].addr is None
    # a call inside a body is not a definition
    assert sum(1 for f in fs.funcs if f.name == "CStreaming::RequestModel") == 1


def test_ctor_init_list_and_operator():
    src = """
// 0x401000
CFoo::CFoo(int a) : m_a(a), m_b{2} {
}
bool CFoo::operator==(const CFoo& o) const {
    return m_a == o.m_a;
}
template<typename T> void notsa::Bar<T>::Run() { if (CFoo::Check(1)) { x(); } }
"""
    fs = scan_file("x.cpp", src)
    names = [f.name for f in fs.funcs]
    assert "CFoo::CFoo" in names and "CFoo::operator==" in names and "notsa::Bar::Run" in names
    assert "CFoo::Check" not in names
    assert next(f for f in fs.funcs if f.name == "CFoo::CFoo").addr == 0x401000


def test_class_members():
    fs = scan_file("Ped.h", ENTITY_H)
    ped = _cls(fs, "CPed")
    assert ped.bases == ["CPlaceable"]
    names = [m.name for m in ped.members]
    # statics, using, methods are not members; anonymous struct is a block
    assert "ms_counter" not in names and "Ref" not in names and "GetHealth" not in names
    assert names[:2] == ["field_14", ""]
    block = ped.members[1].block
    assert [m.name for m in block.members] == ["bIsStanding", "bWasStanding", "knock", "bLast"]
    assert block.members[2].bits == "2"
    m = {x.name: x for x in ped.members if x.name}
    assert m["m_wArea"].type == "uint16"          # brace initializer with a cast is not a method
    assert m["m_aPoints"].type == "std::array<CVector, 2>"
    assert m["m_pCallback"].type == "void*"       # function pointer
    assert m["m_sub"].type.endswith("[2]") and m["m_sub"].nested is not None
    assert any(x.block is not None and x.block.kind == "union" for x in ped.members)
    assert {x.name for x in ped.methods} >= {"GetHealth", "Update", "CPed"}
    assert _cls(fs, "CPlaceable").virtual
    assert any(c.name == "CPed::Sub" for c in fs.classes)
    ff = _cls(fs, "FixedFloat")
    assert ff.template and ff.tparams == ["T", "C"]


def test_constants_enums_asserts_aliases():
    fs = scan_file("Streaming.h", STREAMING_H)
    consts = {c.name: c for c in fs.consts}
    assert consts["NUM_STREAMING_CHANNELS"].expr == "2" and consts["NUM_STREAMING_CHANNELS"].kind == "const"
    assert consts["MAX_IMG_FILES"].expr == "8"
    assert consts["RESOURCE_ID_TXD"].kind == "enum" and consts["RESOURCE_ID_TXD"].owner == "eResourceFirstID"
    assert consts["RESOURCE_ID_COL"].expr == ""
    assert ("eResourceFirstID", "int32") in fs.enum_types
    sizes = [(a.type, a.expr) for a in fs.asserts if a.kind == "size"]
    assert ("tStreamingChannel", "0x44") in sizes


def test_mta_defines_and_static_asserts():
    fs = scan_file("Client/game_sa/CPedSA.h", MTA_PED_H, defines=True)
    d = {x.name: x for x in fs.defines}
    assert d["FUNC_CStreaming__RequestModel"].addr == 0x4087E0
    assert d["HOOKPOS_CPed_Update"].comment == "ped update hook"
    assert d["VAR_CStreaming_memoryAvailable"].addr == 0x8A5A80
    kinds = {(a.kind, a.type, a.member, a.expr) for a in fs.asserts}
    assert ("size", "CPedSAInterface", None, "0x20") in kinds
    assert ("offset", "CPedSAInterface", "fHealth", "0x1C") in kinds


def test_pragma_pack_and_alignas():
    src = """
#pragma pack(push, 1)
struct A { uint8 a; uint32 b; };
#pragma pack(pop)
struct alignas(16) B { float x; };
struct C { uint8 c; };
"""
    fs = scan_file("p.h", src)
    assert _cls(fs, "A").pack == 1
    assert _cls(fs, "B").align == 16
    assert _cls(fs, "C").pack is None


def test_namespaces_qualify_classes():
    fs = scan_file("n.h", "namespace notsa { namespace detail { struct Foo { int a; }; } }\nstruct Bar { int b; };")
    assert {c.name for c in fs.classes} == {"notsa::detail::Foo", "Bar"}


def test_chunks_cover_text():
    text = "\n".join([f"void F{i}() {{\n    x();\n}}\n" for i in range(40)])
    cs = chunks(text, max_lines=40, min_lines=12)
    assert cs[0][0] == 1 and cs[-1][1] == len(text.splitlines())
    assert all(z - a < 80 for a, z in cs)
    assert all(b[0] > a[1] for a, b in zip(cs, cs[1:]))
    md = chunks("# A\ntext\nmore\nmore\n## B\nb text\n", markdown=True)
    assert md == [(1, 4), (5, 6)]


def test_camel_words_and_addresses():
    assert camel_words("CStreaming::ms_aInfoForModel") == "c streaming ms a info for model"
    assert camel_words("RwTexDictionaryFindNamedTexture") == "rw tex dictionary find named texture"
    assert exe_addrs("mov 0x05B8E55, 0x8A5A80; 0x1234; 0xFFFFFFFF") == {0x5B8E55, 0x8A5A80}


def test_function_pointer_member_vs_parameter():
    src = """class A { public:
  void (*m_pCb)(A*, void*);
  void SetFinishCallback(void(*callback)(A*, void*), void* data);
  int (__thiscall *m_fn)(int);
};"""
    c = scan_file("a.h", src).classes[0]
    assert [(m.name, m.type) for m in c.members] == [("m_pCb", "void*"), ("m_fn", "void*")]
    assert [m.name for m in c.methods] == ["SetFinishCallback"]
