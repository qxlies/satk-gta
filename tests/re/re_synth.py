"""Synthetic inputs for satk.re tests (WP-09): a tiny PE32 "gta_sa.exe" and fake source trees.

Everything here is invented for the tests (no game bytes, no code from gta-reversed/plugin-sdk/MTA);
the C++ snippets only mimic the *shapes* the scanners look for.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from pathlib import Path

IMAGE_BASE = 0x400000
TEXT, RDATA, DATA, HOOD = 0x401000, 0x403000, 0x404000, 0x405000
EXEC = 0x60000020
RO = 0x40000040
RW = 0xC0000040
EXEC_RW = 0xE0000020


def make_pe(sections: list[tuple[str, int, bytes, int]], *, base: int = IMAGE_BASE) -> bytes:
    """Minimal PE32 with the given ``(name, va, data, flags)`` sections (va absolute)."""
    nsec = len(sections)
    hdr_size = 0x400
    raw = bytearray(hdr_size)
    raw[0:2] = b"MZ"
    struct.pack_into("<I", raw, 0x3C, 0x80)
    pe = 0x80
    raw[pe:pe + 4] = b"PE\0\0"
    struct.pack_into("<HHIIIHH", raw, pe + 4, 0x14C, nsec, 0x427101CA, 0, 0, 224, 0x10F)
    opt = pe + 24
    last = max(va + len(data) for _n, va, data, _f in sections)
    size_of_image = ((last - base) + 0xFFF) & ~0xFFF
    struct.pack_into("<H", raw, opt, 0x10B)
    struct.pack_into("<I", raw, opt + 16, TEXT - base)          # entry
    struct.pack_into("<I", raw, opt + 28, base)                 # image base
    struct.pack_into("<II", raw, opt + 32, 0x1000, 0x200)       # alignments
    struct.pack_into("<II", raw, opt + 56, size_of_image, hdr_size)
    struct.pack_into("<I", raw, opt + 64, 0x1234)               # checksum
    struct.pack_into("<I", raw, opt + 92, 16)
    off = opt + 224
    file_off = hdr_size
    blobs = []
    for name, va, data, flags in sections:
        rsize = (len(data) + 0x1FF) & ~0x1FF
        hdr = name.encode().ljust(8, b"\0") + struct.pack("<IIII", len(data), va - base, rsize, file_off)
        hdr += b"\0" * 12 + struct.pack("<I", flags)
        raw[off:off + 40] = hdr
        off += 40
        blobs.append(bytes(data).ljust(rsize, b"\0"))
        file_off += rsize
    return bytes(raw) + b"".join(blobs)


class Code:
    """A byte buffer addressed by VA."""

    def __init__(self, base: int, size: int, fill: int = 0xCC):
        self.base = base
        self.buf = bytearray([fill]) * size

    def put(self, va: int, data: bytes) -> None:
        o = va - self.base
        self.buf[o:o + len(data)] = data

    def call(self, va: int, target: int) -> None:
        self.put(va, b"\xE8" + struct.pack("<i", target - (va + 5)))

    def jmp(self, va: int, target: int) -> None:
        self.put(va, b"\xE9" + struct.pack("<i", target - (va + 5)))

    def u32(self, va: int, v: int) -> None:
        self.put(va, struct.pack("<I", v))


@dataclass
class World:
    root: Path
    exe: Path
    gtarev: Path
    psdk: Path
    mta: Path
    trunk: Path


# Function layout of the fake .text (all addresses absolute):
F_BAR = 0x401000        # CFoo::Bar (hooks.json), calls F_CALLEE
F_BAZ = 0x401040        # CFoo::Baz (hooks.json): JMP thunk into .HOODLUM body H_BAZ
F_OPEN = 0x401050       # CDoor::Open (hooks.json): push -1; jmp MOVED (moved body in .text)
F_CALLEE = 0x401100     # unnamed, only a call target
F_PAD = 0x401180        # unnamed, only padding evidence
F_RW = 0x401200         # RwFoo (plugin-sdk wrapper)
F_DATA = 0x401240       # unnamed, only referenced from .rdata (function pointer table)
F_LOW = 0x401280        # unnamed padding start that contains the moved body
MOVED = 0x4012C5        # target of CDoor::Open's thunk
F_QUX = 0x401300        # CFoo::Qux (plugin-sdk addrof)
H_BAZ = 0x405010        # .HOODLUM body of CFoo::Baz
VTBL = RDATA            # CFoo vtable: [F_BAR, F_PAD]
FNPTRS = RDATA + 0x40   # pointer table with F_DATA
G_STATE = DATA + 0x10   # int32 gState
G_ARR = DATA + 0x20     # std::array<int16, NUM_THINGS> CFoo::ms_things (NUM_THINGS = 8)
G_SDK = DATA + 0x40     # plugin-sdk global CFoo::ms_count


def _text() -> bytes:
    c = Code(TEXT, 0x2000, fill=0x90)
    # CFoo::Bar: push ebp; mov ebp,esp; call F_CALLEE; pop ebp; ret; (0x401000..)
    c.put(F_BAR, b"\x55\x8B\xEC")
    c.call(F_BAR + 3, F_CALLEE)
    c.put(F_BAR + 8, b"\x5D\xC3")
    # CFoo::Baz: JMP H_BAZ
    c.jmp(F_BAZ, H_BAZ)
    # CDoor::Open: push -1; jmp MOVED
    c.put(F_OPEN, b"\x6A\xFF")
    c.jmp(F_OPEN + 2, MOVED)
    # F_CALLEE: preceded by nops (0x90 fill); body ends with ret
    c.put(F_CALLEE, b"\x8B\x44\x24\x04\xC3")
    # F_PAD: right after "ret + nops"
    c.put(F_PAD - 0x10, b"\x33\xC0\xC3")
    c.put(F_PAD, b"\x56\x8B\xF1\x5E\xC3")
    # RwFoo
    c.put(F_RW, b"\x33\xC0\xC2\x04\x00")
    # F_DATA (only pointer-referenced); keep the bytes before it a non-terminator so that only the
    # data pointer can find it: 0x401230.. = "mov eax, 1" (no ret) directly abutting? use 'C3' then
    # nops is padding evidence too, so end the previous block with a plain instruction byte.
    c.put(F_DATA - 0x10, b"\x8B\xC1" + b"\x90" * 14)
    c.put(F_DATA, b"\x8B\xC1\xC3")
    # F_LOW .. MOVED region
    c.put(F_LOW - 0x10, b"\xC3")
    c.put(F_LOW, b"\x51\x52\x5A\x59\xC3")
    c.put(MOVED, b"\x68\x78\x56\x34\x12")
    c.jmp(MOVED + 5, F_OPEN + 7)
    # CFoo::Qux
    c.put(F_QUX - 0x10, b"\xC3")
    c.put(F_QUX, b"\x31\xC0\xC3")
    # a stray E8 inside CFoo::Qux's tail pointing at an unaligned address (filtered out)
    c.call(F_QUX + 0x20, F_QUX + 0x43)
    return bytes(c.buf)


def _rdata() -> bytes:
    c = Code(RDATA, 0x100, fill=0)
    c.u32(VTBL, F_BAR)
    c.u32(VTBL + 4, F_PAD)
    c.u32(FNPTRS, F_DATA)
    return bytes(c.buf)


def _hoodlum() -> bytes:
    c = Code(HOOD, 0x100, fill=0x90)
    c.put(H_BAZ, b"\x55\x8B\xEC\x33\xC0\x5D\xC3")
    return bytes(c.buf)


def make_exe(path: Path) -> Path:
    data = make_pe([
        (".text", TEXT, _text(), EXEC),
        (".rdata", RDATA, _rdata(), RO),
        (".data", DATA, bytes(0x100), RW),
        (".HOODLUM", HOOD, _hoodlum(), EXEC_RW),
    ])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _w(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8", newline="\n")


HOOKS = [
    {"AddressGTA": "0x401000", "Category": "CFoo", "Name": "Bar", "InstallSourceLocation": "game_sa/Foo.cpp:7",
     "IsReversed": True, "State": "REDIRECT_TO_OURS", "IsLocked": False},
    {"AddressGTA": "0x401040", "Category": "CFoo", "Name": "Baz", "InstallSourceLocation": "game_sa/Foo.cpp:8",
     "IsReversed": False, "State": "REDIRECT_TO_GTA", "IsLocked": True},
    {"AddressGTA": "0x401050", "Category": "CDoor", "Name": "Open", "InstallSourceLocation": "game_sa/Door2.cpp:5",
     "IsReversed": True, "State": "REDIRECT_TO_OURS", "IsLocked": False},
]

FOO_CPP = """\
#include "StdInc.h"
#include "Foo.h"

void CFoo::InjectHooks() {
    RH_ScopedVirtualClass(CFoo, 0x403000, 2);
    RH_ScopedCategoryGlobal();
    RH_ScopedInstall(Bar, 0x401000);
    RH_ScopedInstall(Baz, 0x401040);
}

// 0x401000
int32 CFoo::Bar(int32 x) {
    static auto& lastValue = StaticRef<float>(0x404050);
    return x + 1; // see "Bar" docs
}
"""

FOO_H = """\
#pragma once
/* a comment with StaticRef<int>(0x404999) inside must be ignored */
constexpr int32 NUM_THINGS = 4 * 2;
enum eKind : uint8 { KIND_A, KIND_B = 5, KIND_C, KIND_TOTAL };

static inline auto& gState = StaticRef<int32>(0x404010);

class CFoo {
public:
    static inline auto& ms_things = StaticRef<std::array<int16, NUM_THINGS>>(0x404020);
    static inline auto& ms_kinds = StaticRef<eKind[KIND_TOTAL]>(0x404030);
    int32 m_a;
    float m_b;

    static void InjectHooks();
    int32 Bar(int32 x);
};
VALIDATE_SIZE(CFoo, 0x8);
VALIDATE_SIZE(CBar, sizeof(CFoo) * 2 + 4);
"""

DOOR_CPP = """\
void CDoor::InjectHooks() {
    RH_ScopedClass(CDoor);
    RH_ScopedInstall(Open, 0x401050);
}

void CDoor::Open(float ratio) {
    m_ratio = ratio;
}
"""

POOLS_CPP = """\
auto& ms_pThingPool = StaticRef<CThingPool*>(0x404060);
void CPools::Initialise() {
    ms_pThingPool = new CThingPool(NUM_THINGS * 10, "Things");
}
"""

PSDK_RW = """\
RwBool RwFoo(void) {
    return ((RwBool(__cdecl *)(void))0x401200)();
}
void *&RwEngineInstance = *(void **)0x404070;
"""

PSDK_FOO = """\
int addrof(CFoo::Bar) = ADDRESS_BY_VERSION(0x401000, 0, 0, 0, 0, 0);
int gaddrof(CFoo::Bar) = GLOBAL_ADDRESS_BY_VERSION(0x401000, 0, 0, 0, 0, 0);
int addrof(CFoo::Qux) = ADDRESS_BY_VERSION(0x401300, 0, 0, 0, 0, 0);
int ctor_addr(CFoo) = ADDRESS_BY_VERSION(0x401340, 0, 0, 0, 0, 0);
int &CFoo::ms_count = *reinterpret_cast<int *>(GLOBAL_ADDRESS_BY_VERSION(0x404040, 0, 0, 0, 0, 0));
"""

PSDK_META = """\
namespace plugin {
META_BEGIN(CFoo::Qux)
    static int address;
    static const int id = 0x401300;
    using refs_t = RefList<
        0x401003, GAME_10US_COMPACT, H_CALL, 0x401000, 1,
        0x401010, GAME_10EU, H_CALL, 0x401000, 1>;
META_END
}
"""

MTA_MP = """\
#include "StdInc.h"
#define HOOKPOS_FooEntry        0x401008
#define HOOKSIZE_FooEntry       5
#define HOOKPOS_OpenBody        (0x4012C5 + 0)
#define CALL_BarCallee          0x401003
#define VAR_State               0x404010

void CMultiplayerSA::InitHooks() {
    EZHookInstall(FooEntry);
    HookInstall(HOOKPOS_OpenBody, (DWORD)HOOK_OpenBody, 6);
    HookInstallCall(CALL_BarCallee, (DWORD)HOOK_BarCallee);
    MemPut<BYTE>(0x401010, 0x90);
    MemPutFast<DWORD>(VAR_State, 1);
    MemSet((void*)0x401020, 0x90, 3);
    MemCpy((void*)0x405012, "\\xEB\\x02", 2);
    for (int i = 0; i < 4; i++)
        MemPut<BYTE>(pDynamic + i, 0);
}
"""

MTA_HELPERS_H = """\
void MemSet(void* dwDest, int cValue, uint uiAmount);
template <class T, class U>
void MemPut(U ptr, const T value);
"""

MTA_MANIFEST = """\
// Generated (synthetic)
NATIVE_FILE_ID_POINTER(Model, 0x0040100C, 0x00A9B0C8, 0x00000000)  // comment
NATIVE_FILE_ID_VALUE(0x004011F0, 0x000062A7, 0x00009E40)
static constexpr SEntry ENTRIES[] = {
    {0x00401184, 5, 0},
};
"""

LIMITS_TOML = """\
[meta]
trunk_rev = "x"

[[limit]]
name = "pool.thing"
kind = "pool"
vanilla = 80
trunk = 160
neon = 320
global = "0x404060"

[[limit]]
name = "world.bounds"
kind = "world"
vanilla = 3000
trunk = 3000

[[limit]]
name = "bogus"
kind = "nonsense"
vanilla = 1
"""


def make_world(root: Path) -> World:
    exe = make_exe(root / "game" / "gta_sa.exe")
    gr = root / "src" / "gta-reversed"
    _w(gr, "docs/hooks.json", json.dumps(HOOKS))
    _w(gr, "source/game_sa/Foo.cpp", FOO_CPP)
    _w(gr, "source/game_sa/Foo.h", FOO_H)
    _w(gr, "source/game_sa/Door2.cpp", DOOR_CPP)
    _w(gr, "source/game_sa/Pools.cpp", POOLS_CPP)
    ps = root / "src" / "plugin-sdk-sa"
    _w(ps, "plugin_sa/game_sa/RenderWare.cpp", PSDK_RW)
    _w(ps, "plugin_sa/game_sa/CFoo.cpp", PSDK_FOO)
    _w(ps, "plugin_sa/game_sa/meta/meta.CFoo.h", PSDK_META)
    mta = root / "src" / "mtasa"
    _w(mta, "Client/multiplayer_sa/CMultiplayerSA.cpp", MTA_MP)
    _w(mta, "Client/game_sa/gamesa_init.h", MTA_HELPERS_H)
    _w(mta, "Client/game_sa/CTestManifest.inc", MTA_MANIFEST)
    trunk = root / "engine" / "mtasa"
    _w(trunk, "docs/limits.toml", LIMITS_TOML)
    _w(trunk, "Client/multiplayer_sa/CMultiplayerSA.cpp", MTA_MP.replace("0x401010", "0x401011"))
    return World(root, exe, gr, ps, mta, trunk)
