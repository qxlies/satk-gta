"""Synthetic donor trees for satk.kb tests (no real gta-reversed/MTA code; invented names/numbers)."""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from pathlib import Path

GR_HOOKS = [
    {"AddressGTA": "0x4087E0", "Category": "CStreaming", "Name": "RequestModel",
     "InstallSourceLocation": "game_sa/Streaming.cpp:5", "IsReversed": True, "State": "on", "IsLocked": False},
    {"AddressGTA": "0x40CBA0", "Category": "CStreaming", "Name": "RequestModelStream",
     "InstallSourceLocation": "game_sa/Streaming.cpp:6", "IsReversed": False, "State": "off", "IsLocked": True},
    {"AddressGTA": "0x550F10", "Category": "CPools", "Name": "Initialise",
     "InstallSourceLocation": "game_sa/Pools.cpp:4", "IsReversed": True, "State": "on", "IsLocked": False},
    {"AddressGTA": "0x56E210", "Category": "", "Name": "FindPlayerPed",
     "InstallSourceLocation": "game_sa/Pools.cpp:5", "IsReversed": True, "State": "on", "IsLocked": False},
]

STREAMING_H = """#pragma once
#include "Base.h"

constexpr int32 NUM_STREAMING_CHANNELS = 2;
#define MAX_IMG_FILES 8

enum eResourceFirstID : int32 {
    RESOURCE_ID_DFF = 0,
    RESOURCE_ID_TXD = RESOURCE_ID_DFF + 20000,
    RESOURCE_ID_COL,
};

struct tStreamingChannel {
    int32 modelIds[16];
    int32 status;
};
VALIDATE_SIZE(tStreamingChannel, 0x44);

class CStreaming {
public:
    static inline auto& ms_memoryAvailable = StaticRef<size_t>(0x8A5A80);
    static void RequestModel(int32 modelId, int32 flags);
    static void RequestModelStream(int32 channelId);
};
"""

STREAMING_CPP = """#include "Streaming.h"

void CStreaming::InjectHooks() {
    RH_ScopedClass(CStreaming);
    RH_ScopedInstall(RequestModel, 0x4087E0);
    RH_ScopedInstall(RequestModelStream, 0x40CBA0);
}

// 0x4087E0
// Request a given model to be loaded.
void CStreaming::RequestModel(int32 modelId, int32 streamingFlags) {
    if (modelId < 0) {
        return;
    }
    ms_memoryAvailable = 0x3200000; // see 0x5B8E64
}

// 0x40CBA0
void CStreaming::RequestModelStream(int32 channelId) {
    RequestModel(1, 2);
}
"""

POOLS_CPP = """#include "Pools.h"

auto& ms_pPedPool = StaticRef<CPedPool*>(0xB74490);
void CPools::InjectHooks() {
    RH_ScopedInstall(Initialise, 0x550F10);
}

// 0x550F10
void CPools::Initialise() {
    ms_pPedPool = new CPedPool(140, "Peds");
}
"""

ENTITY_H = """#pragma once

class CPlaceable {
public:
    CVector m_pos;
    CMatrixLink* m_matrix;
    virtual ~CPlaceable();
};
VALIDATE_SIZE(CPlaceable, 0x14);

class CPed : public CPlaceable {
public:
    using Ref = int;
    static inline int16 ms_counter = 1;
protected:
    char    field_14[4];
public:
    struct {
        bool bIsStanding : 1 = false;
        bool bWasStanding : 1;
        uint8 knock : 2;
        bool bLast : 1;
    };
    uint16  m_wArea{ (uint16)-1 };
    float   m_fHealth;
    CPed*   m_pTarget;
    std::array<CVector, 2> m_aPoints;
    union {
        int32 m_nRaw;
        float m_fRaw;
    };
    struct Sub { int16 a; int16 b; } m_sub[2];
    void (*m_pCallback)(CPed*);
    eWeaponTypeS16 m_weapon;

    CPed(int x) : m_fHealth(100.0f) {}
    float GetHealth() const { return m_fHealth; }
    void Update();
};
VALIDATE_SIZE(CPed, 0x50);

struct CVector { float x, y, z; };
VALIDATE_SIZE(CVector, 0xC);

enum eWeaponType : int32 { WEAPON_UNARMED, WEAPON_PISTOL = 22 };
NOTSA_WENUM_DEFS_FOR(eWeaponType);

template<typename T, float C>
class FixedFloat {
    T value;
};

struct Packed {
    FixedFloat<int16, 8.f> f;
    uint8 b;
};
"""

COMMANDS_CPP = """#include "Commands.hpp"

auto SetCharHealth(CPed& ped, int32 health) {
    ped.m_fHealth = (float)health;
}

void notsa::script::commands::character::RegisterHandlers() {
    REGISTER_COMMAND_HANDLER(COMMAND_SET_CHAR_HEALTH, SetCharHealth);
}
"""

PS_PED_H = """#pragma once
#include "PluginBase.h"

class PLUGIN_API CPed : public CPlaceable {
public:
    char field_14[4];
    unsigned char m_flags[2];
    float m_fHealth;
    SUPPORTED_10US static void RequestThing(int mode);
};
VALIDATE_SIZE(CPed, 0x50);
VALIDATE_OFFSET(CPed, field_14, 0x14);
VALIDATE_OFFSET(CPed, m_fHealth, 0x1C);
"""

PS_STREAMING_CPP = """#include "CStreaming.h"

PLUGIN_SOURCE_FILE

int addrof(CStreaming::RequestModel) = ADDRESS_BY_VERSION(0x4087E0, 0, 0, 0, 0, 0);
int gaddrof(CStreaming::RequestModel) = GLOBAL_ADDRESS_BY_VERSION(0x4087E0, 0, 0, 0, 0, 0);

void CStreaming::RequestModel(int dwModelId, int flags) {
    plugin::CallDynGlobal<int, int>(gaddrof(CStreaming::RequestModel), dwModelId, flags);
}
"""

MTA_COMMON_H = """#pragma once
#define MAX_BUILDINGS 13000
#define MAX_OBJECTS 1200
"""

MTA_PED_H = """#pragma once
#define FUNC_CStreaming__RequestModel 0x4087E0
#define HOOKPOS_CPed_Update 0x5E8B20 // ped update hook
#define VAR_CStreaming_memoryAvailable 0x08A5A80

class CPedSAInterface : public CPlaceableSAInterface {
public:
    std::uint8_t unk_14[4];
    std::uint8_t flags[4];
    float fHealth;
};
static_assert(sizeof(CPedSAInterface) == 0x20, "Invalid size");
static_assert(offsetof(CPedSAInterface, fHealth) == 0x1C, "bad");

class CPlaceableSAInterface {
public:
    void* vtbl;
    float pos[3];
    void* matrix;
};
"""

NEON_COMMON_H = MTA_COMMON_H.replace("13000", "32000") + "#define MAX_COL_MODELS 30000\n"

CLEO_INDEX = """# Opcode Index

| Opcode | Name | Class | Extension | Params | Flags | Description |
|--------|------|-------|-----------|--------|-------|-------------|
| `0223` | SET_CHAR_HEALTH | Char | default | 2 |  | Sets the character's health |
| `0A8C` | WRITE_MEMORY | Memory | CLEO | 4 | static | Writes the value at the memory address |
| `0A8D` | READ_MEMORY | Memory | CLEO | 3 | static | Reads a value from the game memory |
| `0A8D` | READ_MEMORY_WITH_OFFSET | Memory | SAMPFUNCS | 3 | static | Reads memory with an offset |
"""

CLEO_CHAR = """# Char Opcodes

### `0223` SET_CHAR_HEALTH
Sets the character's health

**Class:** `Char.SetHealth`

**Input:**
- `self: Char`
- `health: int`

**Details:**

Health above the maximum is clamped.

---
"""

CLEO_EXT = """# CLEO

### Memory

### `0A8C` WRITE_MEMORY
Writes the value at the memory address

**Class:** `Memory.Write`
**Flags:** static

**Input:**
- `address: int`
- `size: int`
- `value: any`
- `vp: bool`

---

### `0A8D` READ_MEMORY
Reads a value from the game memory

**Class:** `Memory.Read`
**Flags:** static

**Input:**
- `address: int`
- `size: int`
- `vp: bool`

**Output:**
- `result: any (variable)`

---
"""

RESEARCH_MD = """# 99 — Тестовый отчёт

## Память стриминга

Память стриминга в ванили равна 50 MiB.

## Другое

Ничего интересного.
"""


@dataclass
class World:
    root: Path
    gtarev: Path
    psdk: Path
    upstream: Path
    neon: Path
    cleo: Path
    research: Path
    exe: Path


def _w(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def make_pe(path: Path, patches: dict[int, bytes]) -> Path:
    """Minimal PE32 image: one ``.text`` section at 0x401000 (0x2000 bytes) with ``patches`` applied."""
    sec_raw = bytearray(0x2000)
    for va, data in patches.items():
        off = va - 0x401000
        sec_raw[off:off + len(data)] = data
    mz = bytearray(0x80)
    mz[0:2] = b"MZ"
    struct.pack_into("<I", mz, 0x3C, 0x80)
    pe = bytearray(b"PE\0\0")
    pe += struct.pack("<HHIIIHH", 0x14C, 1, 0, 0, 0, 224, 0x102)
    opt = bytearray(224)
    struct.pack_into("<H", opt, 0, 0x10B)
    struct.pack_into("<I", opt, 16, 0x1000)
    struct.pack_into("<I", opt, 28, 0x400000)
    sect = bytearray(40)
    sect[0:5] = b".text"
    struct.pack_into("<IIII", sect, 8, 0x2000, 0x1000, 0x2000, 0x400)
    struct.pack_into("<I", sect, 36, 0x60000020)
    head = bytes(mz + pe + opt + sect)
    data = head + bytes(0x400 - len(head)) + bytes(sec_raw)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def make_world(root: Path) -> World:
    gr = root / "gta-reversed"
    _w(gr / "docs" / "hooks.json", json.dumps(GR_HOOKS))
    _w(gr / "docs" / "ReversedClasses.md", "# Reversed classes\n\n## Streaming\n\nCStreaming is fully reversed.\n")
    _w(gr / "source" / "game_sa" / "Streaming.h", STREAMING_H)
    _w(gr / "source" / "game_sa" / "Streaming.cpp", STREAMING_CPP)
    _w(gr / "source" / "game_sa" / "Pools.cpp", POOLS_CPP)
    _w(gr / "source" / "game_sa" / "Entity" / "Ped.h", ENTITY_H)
    _w(gr / "source" / "game_sa" / "Scripts" / "Commands" / "CharacterCommands.cpp", COMMANDS_CPP)
    ps = root / "plugin-sdk"
    _w(ps / "plugin_sa" / "game_sa" / "CPed.h", PS_PED_H)
    _w(ps / "plugin_sa" / "game_sa" / "CStreaming.cpp", PS_STREAMING_CPP)
    up = root / "mta-upstream"
    _w(up / "Client" / "sdk" / "game" / "Common.h", MTA_COMMON_H)
    _w(up / "Client" / "game_sa" / "CPedSA.h", MTA_PED_H)
    neon = root / "mta-neon"
    _w(neon / "Client" / "sdk" / "game" / "Common.h", NEON_COMMON_H)
    _w(neon / "Client" / "game_sa" / "CPedSA.h", MTA_PED_H)
    cleo = root / "cleo-ai"
    _w(cleo / "reference" / "opcode-index.md", CLEO_INDEX)
    _w(cleo / "reference" / "opcodes-by-class" / "Char.md", CLEO_CHAR)
    _w(cleo / "reference" / "ext-CLEO.md", CLEO_EXT)
    _w(cleo / "reference" / "syntax-guide.md", "# Syntax\n\n## Labels\n\nUse `:label` for labels.\n")
    research = root / "research"
    _w(research / "99-test.md", RESEARCH_MD)
    exe = make_pe(root / "exe" / "gta_sa.exe", {0x401010: struct.pack("<I", 140)})
    return World(root, gr, ps, up, neon, cleo, research, exe)


def make_inputs(world: World, **skip):
    from satk.kb.build import Inputs
    from satk.re.gitsrc import DirTree

    inp = Inputs(gtarev=DirTree(world.gtarev), pluginsdk=DirTree(world.psdk), upstream=DirTree(world.upstream),
                 neon=DirTree(world.neon), cleo=DirTree(world.cleo), research=DirTree(world.research), exe=world.exe,
                 repos={"gta-reversed": "src/gta-reversed", "plugin-sdk": "src/plugin-sdk-sa"})
    for k in skip:
        setattr(inp, k, None)
    return inp
