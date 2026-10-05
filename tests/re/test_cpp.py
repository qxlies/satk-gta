"""satk.re.cpp: blanking, brackets, constant evaluation, arrays, scopes (WP-09)."""

from __future__ import annotations

from satk.re.cpp import (ConstEval, LineIndex, ScopeMap, array_info, blank, iter_calls, match_close, norm_type,
                         split_top)


def test_blank_keeps_offsets_and_lines():
    src = 'int a = 1; // StaticRef<int>(0x1)\n/* x\n y */ const char* s = "0x2 // not a comment";\nchar c = \'}\';\n'
    b = blank(src)
    assert len(b) == len(src) and b.count("\n") == src.count("\n")
    assert "StaticRef" not in b and "0x2" not in b and "}" not in b
    assert "int a = 1;" in b and "const char* s =" in b
    assert blank(src, strings=False).count('"0x2 // not a comment"') == 1
    pre = blank("#define X(a) { a }\nint y;\n#if 1\\\n  cont\nint z;\n", preproc=True)
    assert "{" not in pre and "int y;" in pre and "int z;" in pre and "cont" not in pre


def test_digit_separator_is_not_a_char_literal():
    assert "1'000'000" in blank("int x = 1'000'000;")


def test_line_index():
    li = LineIndex("a\nb\nc")
    assert [li.line(i) for i in (0, 2, 4)] == [1, 2, 3]


def test_match_close_and_split_top():
    s = "StaticRef<std::array<std::pair<int, float>, N>>(0x10)"
    lt = s.index("<")
    gt = match_close(s, lt)
    assert s[lt + 1:gt - 1] == "std::array<std::pair<int, float>, N>"
    assert match_close("(a, (b), c)", 0, angle=False) == 11
    assert match_close("f(a; b)", 1) == -1
    assert split_top("std::array<int, 3>, (A, B), x") == ["std::array<int, 3>", "(A, B)", "x"]
    assert split_top("A = 1 << 3, B", angle=False) == ["A = 1 << 3", "B"]


def test_const_eval_basics():
    ev = ConstEval()
    ev.define("A", "0x10")
    ev.define("B", "A * 2 + 1")
    ev.define("C", "(B - 1) / 4")
    ev.define("Cls::NUM", "std::max(3, C)")
    ev.define("LOOP1", "LOOP2 + 1")
    ev.define("LOOP2", "LOOP1 + 1")
    assert ev.value("B") == 33
    assert ev.value("C") == 8
    assert ev.value("Other::NUM") == 8           # falls back to the last component
    assert ev.value("LOOP1") is None             # cycles do not recurse forever
    assert ev.eval("010") == 8 and ev.eval("1'000") == 1000 and ev.eval("0x10u") == 16
    assert ev.eval("-7 / 2") == -3 and ev.eval("1 ? 2 : 3") == 2 and ev.eval("~0 & 0xFF") == 255
    assert ev.eval("(size_t)A + static_cast<int32>(1)") == 17
    ev.set_value("eKind::KIND_LAST", 6)
    ev.enum_sizes["eKind"] = 1
    assert ev.eval("(eKind::KIND_LAST) + 1") == 7          # parenthesized enum value, not a cast
    assert ev.eval("(eKind)3 + 1") == 4                     # a real cast to an enum type
    assert ev.eval("sizeof(int32) * 2") == 8 and ev.eval("sizeof(void*)") == 4
    assert ev.eval("Unknown + 1") is None and ev.eval("f(1)") is None


def test_type_size_aliases_structs_enums():
    ev = ConstEval()
    ev.sizes["CVector"] = 12
    ev.alias("ModelIndex", "int16")
    ev.enum_sizes["eSmall"] = 1
    ev.define("N", "5")
    assert ev.type_size("CVector[N]") == 60
    assert ev.type_size("std::array<CVector, 2>") == 24
    assert ev.type_size("ModelIndex") == 2
    assert ev.type_size("const eSmall") == 1
    assert ev.type_size("notsa::WEnumU16<eModelID>") == 2
    assert ev.type_size("Foo*") == 4 and ev.type_size("Unknown") is None
    assert ev.type_size("notsa::mdarray<bool, 10, 10>") == 100


def test_array_info():
    ev = ConstEval()
    ev.define("NUM", "20")
    assert array_info("std::array<CPed*, NUM>", ev) == ("CPed*", 20)
    assert array_info("CBaseModelInfo* [NUM]", ev) == ("CBaseModelInfo*", 20)
    assert array_info("float[2][3]", ev) == ("float[3]", 2)
    assert array_info("int32", ev) is None
    assert array_info("std::array<int, UNKNOWN>", ev) == ("int", None)


def test_norm_type():
    assert norm_type("const  CPed *") == "CPed*"
    assert norm_type("std::array< int ,3 >") == "std::array<int, 3>"


def test_scope_map_qualifiers():
    src = blank("""
namespace notsa {
namespace detail { int a; }
}
class CFoo : public CBase<int> {
    static inline auto& x = 1;
    void M() { if (a) { int y; } }
};
void CFoo::Out() const {
    static int z;
}
namespace { int anon; }
struct S final { int q; };
enum class E : uint8 { A, B };
CBuilding::operator new(size_t sz) { return 0; }
""", preproc=True)
    sm = ScopeMap(src)
    assert sm.qualifier(src.index("int a")) == "notsa::detail"
    assert sm.qualifier(src.index("static inline auto& x")) == "CFoo"
    assert sm.qualifier(src.index("int y")) == "CFoo::M"
    assert sm.qualifier(src.index("int y"), functions=False) == "CFoo"
    assert sm.qualifier(src.index("static int z")) == "CFoo::Out"
    assert sm.qualifier(src.index("int anon")) == ""
    assert sm.qualifier(src.index("int q")) == "S"
    assert ("enum", "E") in [lbl for _p, d, lbl in sm.events if d > 0]
    assert ("fn", "CBuilding::operator new") in [lbl for _p, d, lbl in sm.events if d > 0]
    pos = [src.index("int a"), src.index("int y"), src.index("int q")]
    assert sm.qualifiers(pos) == ["notsa::detail", "CFoo::M", "S"]


def test_iter_calls_with_templates():
    b = "MemPut<BYTE>(0x401000 + 1, 0x90); x = HookInstall (A, B, 5); MemPutFast<std::pair<int,int>>(Y, 1);"
    got = [(m.group(1), b[a0:a1]) for m, a0, a1 in iter_calls(b, "MemPutFast|MemPut|HookInstall")]
    assert got == [("MemPut", "0x401000 + 1, 0x90"), ("HookInstall", "A, B, 5"), ("MemPutFast", "Y, 1")]
