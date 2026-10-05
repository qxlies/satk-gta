"""satk.re sources on synthetic trees: hooks.json, gta-reversed, plugin-sdk, MTA, exe, limits (WP-09)."""

from __future__ import annotations

import pytest

from satk.core.errors import SatkError
from satk.re.gitsrc import DirTree
from satk.re.pe import PeImage, PeError, pe_sections, va_to_off
from satk.re.sources.exe import classify_thunk, read_vtable, scan_exe
from satk.re.sources.gtarev import scan_gtarev
from satk.re.sources.hooks import load_hooks, parse_hooks
from satk.re.sources.limits import load_limits_toml, merge_limits
from satk.re.sources.mta import scan_mta
from satk.re.sources.pluginsdk import scan_pluginsdk

import re_synth as C


def test_pe_reader(world):
    img = PeImage.open(world.exe)
    assert img.image_base == 0x400000 and img.checksum == 0x1234
    assert [s.name for s in img.sections] == [".text", ".rdata", ".data", ".HOODLUM"]
    t = img.section(".text")
    assert t.va == C.TEXT and t.executable and img.section_at(C.H_BAZ).name == ".HOODLUM"
    assert img.read(C.F_BAR, 3) == b"\x55\x8B\xEC" and img.u32(C.VTBL) == C.F_BAR
    assert img.read(0x300000, 4) == b"" and img.u32(0x9999999) is None
    secs = pe_sections(img.data)                       # shared satk.formats.pe helpers
    assert secs[0][0] == ".text" and va_to_off(secs, C.TEXT) == 0x400
    assert secs == [(s.name, s.va - img.image_base, s.vsize, s.raw_off, s.raw_size) for s in img.sections]
    with pytest.raises(PeError):
        PeImage(b"MZ" + b"\0" * 100)


def test_exe_scan_and_thunks(world):
    img = PeImage.open(world.exe)
    ex = scan_exe(img)
    assert C.F_CALLEE in ex.e8_starts and C.F_QUX + 0x43 in ex.e8_all and C.F_QUX + 0x43 not in ex.e8_starts
    assert C.F_DATA in ex.data_ptr_starts and C.F_DATA not in ex.padding_starts
    assert {C.F_PAD, C.F_LOW, C.F_RW, C.F_CALLEE} <= ex.padding_starts
    assert C.MOVED not in ex.padding_starts           # not aligned
    assert classify_thunk(img, C.F_BAZ) == ("hoodlum", C.H_BAZ)
    assert classify_thunk(img, C.F_OPEN) == ("seh_push_jmp", C.MOVED)
    assert classify_thunk(img, C.F_BAR) is None
    assert read_vtable(img, C.VTBL, 2) == [C.F_BAR, C.F_PAD]


def test_hooks_json(world):
    funcs, raw = load_hooks(DirTree(world.gtarev))
    assert raw == 3 and [f.qual for f in funcs] == ["CFoo::Bar", "CFoo::Baz", "CDoor::Open"]
    bar = funcs[0]
    assert (bar.src_file, bar.src_line, bar.reversed, bar.hook_state, bar.locked) == \
        ("game_sa/Foo.cpp", 7, True, "REDIRECT_TO_OURS", False)
    assert parse_hooks('[{"AddressGTA":"zz","Name":"x"},{"AddressGTA":"0x1","Name":"","Category":"C"}]') == []
    with pytest.raises(SatkError):
        parse_hooks("{not json")
    with pytest.raises(SatkError) as e:
        load_hooks(DirTree(world.psdk))
    assert e.value.code == "NOT_FOUND"


def test_gtarev_scan(world):
    r = scan_gtarev(DirTree(world.gtarev))
    g = {x.addr: x for x in r.globals}
    assert g[C.G_STATE].name == "gState" and g[C.G_STATE].type == "int32" and g[C.G_STATE].byte_size == 4
    things = g[C.G_ARR]
    assert (things.name, things.elem_type, things.array_len, things.elem_size, things.byte_size) == \
        ("CFoo::ms_things", "int16", 8, 2, 16)
    kinds = g[0x404030]
    assert (kinds.elem_type, kinds.array_len, kinds.byte_size) == ("eKind", 7, 7)   # enum : uint8, KIND_TOTAL = 7
    assert g[0x404050].name == "CFoo::Bar::lastValue"
    assert 0x404999 not in g                                                       # inside a comment
    assert g[0x404050].src_file == "game_sa/Foo.cpp" and g[0x404050].src_line == 13
    assert [(v.addr, v.cls, v.slots) for v in r.vtables] == [(0x403000, "CFoo", 2)]
    sizes = {s.name: s.size for s in r.sizes}
    assert sizes == {"CFoo": 8, "CBar": 20}
    lim = {x.name: x for x in r.limits}
    assert lim["CFoo::ms_things"].kind == "array" and lim["CFoo::ms_things"].vanilla == 8
    assert lim["CPools::ms_pThingPool"].kind == "pool" and lim["CPools::ms_pThingPool"].vanilla == 80
    assert lim["CPools::ms_pThingPool"].global_addr == 0x404060
    assert r.stats["staticref_unique"] == 5 and r.stats["vtable_unique"] == 1


def test_gtarev_scoped_installs_supply_missing_function_names(world):
    path = world.gtarev / "source/game_sa/NewHooks.cpp"
    path.write_text('''void CNew::InjectHooks() {
    RH_ScopedClass(CNew);
    RH_ScopedInstall(Update, 0x401380, {.Reversed = false});
    RH_ScopedOverloadedInstall(Set, "Number", 0x401390, void(CNew::*)(int));
    RH_ScopedNamedInstall(operator+, "Add", 0x4013A0);
    RH_ScopedConstructorInstall(0x4013B0, "Default", {});
    RH_ScopedDestructorInstall(0x4013C0);
    // RH_ScopedInstall(Commented, 0x4013D0);
    const char* example = "RH_ScopedInstall(InString, 0x4013E0)";
}
namespace Other {
void InjectHooks() {
    RH_ScopedNamespace(Other);
    RH_ScopedGlobalInstall(Tick, 0x4013F0);
}
}
void CElse::InjectHooks() {
    RH_ScopedVirtualClass(CElse, 0x403080, 1);
    RH_ScopedVMTInstall(Run, 0x401400);
}
''', encoding="utf-8")
    scan = scan_gtarev(DirTree(world.gtarev))
    funcs = {f.addr: f for f in scan.funcs}
    assert {addr: f.qual for addr, f in funcs.items() if addr >= 0x401380} == {
        0x401380: "CNew::Update", 0x401390: "CNew::Set-Number", 0x4013A0: "CNew::Add",
        0x4013B0: "CNew::CNew-Default", 0x4013C0: "CNew::~CNew", 0x4013F0: "Other::Tick",
        0x401400: "CElse::Run",
    }
    assert funcs[0x401380].reversed is False
    assert (funcs[0x401380].src_file, funcs[0x401380].src_line) == ("game_sa/NewHooks.cpp", 3)
    assert funcs[0x401380].origin == "gta_reversed"


def test_exe_finds_hoodlum_jumps_without_known_aligned_starts():
    code = C.Code(C.TEXT, 0x200, fill=0x90)
    entry = C.TEXT + 0x23  # no alignment, padding terminator, pointer or call-target evidence
    code.jmp(entry, C.HOOD + 0x10)
    code.jmp(C.TEXT + 0x43, C.RDATA)  # not executable; not a thunk candidate
    code.jmp(C.TEXT + 0x63, C.HOOD + 0x100)  # zero-filled section padding
    body = C.Code(C.HOOD, 0x100, fill=0x90)
    body.put(C.HOOD + 0x10, b"\x33\xC0\xC3")
    image = PeImage(C.make_pe([(".text", C.TEXT, bytes(code.buf), C.EXEC),
                               (".rdata", C.RDATA, bytes(0x100), C.RO),
                               (".HOODLUM", C.HOOD, bytes(body.buf), C.EXEC_RW)]))
    scan = scan_exe(image)
    assert scan.hoodlum_jumps == {entry: C.HOOD + 0x10}


def test_pluginsdk_scan(world):
    r = scan_pluginsdk(DirTree(world.psdk))
    names = {f.addr: f.qual for f in r.funcs}
    assert names[0x401200] == "RwFoo" and names[0x401300] == "CFoo::Qux" and names[0x401340] == "CFoo::CFoo"
    assert names[0x401000] == "CFoo::Bar"
    g = {x.addr: x for x in r.globals}
    assert g[0x404040].name == "CFoo::ms_count" and g[0x404040].type == "int"
    assert g[0x404070].name == "RwEngineInstance"
    assert [(c.site, c.callee, c.caller, c.kind) for c in r.calls] == [(0x401003, 0x401300, 0x401000, "call")]


def test_mta_scan(world):
    r = scan_mta(DirTree(world.mta))
    got = sorted((p.addr, p.kind, p.len, p.symbol, p.note, p.src_line) for p in r.patches)
    want = sorted([
        (0x401008, "hookpos", 5, "HOOKPOS_FooEntry", None, 2),
        (0x4012C5, "hookpos", None, "HOOKPOS_OpenBody", None, 4),
        (0x401008, "hookinstall", 5, "HOOKPOS_FooEntry", None, 9),
        (0x4012C5, "hookinstall", 6, "HOOKPOS_OpenBody", None, 10),
        (0x401003, "hookinstallcall", 5, "CALL_BarCallee", None, 11),
        (0x401010, "memput", 1, None, None, 12),
        (0x404010, "memput", 4, "VAR_State", "fast", 13),
        (0x401020, "memset", 3, None, None, 14),
        (0x405012, "memcpy", 2, None, None, 15),
        (0x40100C, "reloc_manifest", 4, "NATIVE_FILE_ID_POINTER", "Model", 2),
        (0x4011F0, "reloc_manifest", None, "NATIVE_FILE_ID_VALUE", None, 3),
        (0x401184, "reloc_manifest", None, "ENTRIES", None, 5),
    ])
    assert got == want
    assert r.stats["unresolved"] == {"memput": 1}      # MemPut<BYTE>(pDynamic + i, 0)
    assert all(p.src_file.startswith("Client/") for p in r.patches)
    r2 = scan_mta(DirTree(world.mta), manifests=False)
    assert not [p for p in r2.patches if p.kind == "reloc_manifest"]


def test_limits_merge(world):
    tree = DirTree(world.trunk)
    entries = load_limits_toml(tree)
    assert entries is not None and len(entries) == 3
    base = scan_gtarev(DirTree(world.gtarev)).limits
    rows, st = merge_limits(base, entries, "engine/mtasa:docs/limits.toml")
    byname = {r[0]: r for r in rows}
    assert byname["CPools::ms_pThingPool"][4:6] == (160, 320)
    assert byname["world.bounds"][1] == "world" and byname["world.bounds"][2] == 3000
    assert "bogus" not in byname
    assert st["matched"] == 1 and st["added"] == 1 and st["skipped"] == 1
    assert load_limits_toml(DirTree(world.mta)) is None and load_limits_toml(None) is None
