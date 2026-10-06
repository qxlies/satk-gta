"""Curated engine and authoring facts about gta_sa.exe 1.0 US (report 24 section 6; owner M2-02).

Our own data (numbers, addresses, names, short English notes), no third-party text. Two lists:

* :data:`FACTS` - engine facts that ``satk kb build`` stores and re-checks against the fresh KB and the clean
  executable. Every fact carries the trust level of the report (``K`` gta-reversed code, ``2`` a second
  independent source, ``E`` gta_sa.exe bytes) and machine checks:

  * ``("func", name, addr)`` - gta-reversed function (hooks.json) at ``addr``;
  * ``("global", name, addr)`` - gta-reversed global (``StaticRef``); ``name`` may be unqualified;
  * ``("size", type, n)`` / ``("size", type, n, source)`` - asserted struct size;
  * ``("limit", name, n)`` - pool/array/store length found in gta-reversed;
  * ``("const", name, n)`` / ``("const", name, n, source)`` - evaluated constant or ``#define``;
  * ``("u8"|"u16"|"u32", va, n)`` / ``("f32", va, x)`` - value in gta_sa.exe at ``va``;
  * ``("section", name, va)`` / ``("section", name, va, nth)`` - PE section start.

  A fact is ``verified`` when all its checks pass, ``mismatch`` when one fails and ``unchecked`` when a check
  could not be run (no exe, source missing).

* :data:`ASSET_FACTS` - authoring facts (``asset.*``): what a new model must follow to work and look right in
  the stock engine. ``verify`` says what the test-suite checks against the vanilla index, the game files or
  gta_sa.exe; ``checks`` (same grammar as above) are optional. ``satk kb fact asset`` and ``satk kb search``
  serve them from this module (status ``tested``).
"""

from __future__ import annotations

__all__ = ["FACTS", "ASSET_FACTS", "CONFIDENCE", "topic", "all_facts"]

#: report-24 letters -> stored confidence
CONFIDENCE = {"K": "code", "K2": "2src", "KE": "exe", "K2E": "exe", "E": "exe"}

# key, title, value, addrs, refs, sources, conf, checks, note
FACTS: list[dict] = [
    {"key": "exe.layout", "title": "Section layout of the HOODLUM gta_sa.exe",
     "value": ".text 0x401000-0x857000; _rwcseg 0x857000; .rdata 0x858000; .data 0x8A4000 (.bss up to 0xC9E000); _TEXT_HA "
        "0xC9E000; _rwdseg 0xCAF000; HOODLUM layer: .text 0xCB1000, .data 0x1301000, .HOODLUM 0x1556000; image base "
        "0x400000",
     "refs": ["PE header"], "sources": ["exe"], "conf": "E",
     "checks": [("section", ".text", 0x401000), ("section", "_rwcseg", 0x857000), ("section", ".rdata", 0x858000),
                 ("section", ".data", 0x8A4000), ("section", "_rwdseg", 0xCAF000), ("section", ".text", 0xCB1000, 1),
                 ("section", ".HOODLUM", 0x1556000)]},
    {"key": "pools.initialise", "title": "CPools::Initialise creates the pools",
     "value": "17 pools; their pointers lie in a row at 0xB74484..0xB744C4",
     "addrs": [0x550F10, 0xB74484, 0xB744C4],
     "refs": ["game_sa/Pools.cpp"], "sources": ["gta-reversed", "plugin-sdk", "MTA CPoolsSA"], "conf": "K2",
     "checks": [("func", "CPools::Initialise", 0x550F10), ("global", "ms_pPtrNodeSingleLinkPool", 0xB74484),
                 ("global", "ms_pPedAttractorPool", 0xB744C4)]},
    {"key": "pools.ped", "title": "Ped pool",
     "value": "140 slots; element 0x7C4 (CCopPed, the largest ped class)",
     "addrs": [0xB74490, 0x550FF2],
     "refs": ["game_sa/Pools.cpp", "PedPool.h"], "sources": ["gta-reversed", "plugin-sdk", "MTA"], "conf": "K2E",
     "checks": [("global", "ms_pPedPool", 0xB74490), ("limit", "ms_pPedPool", 140), ("u32", 0x550FF2, 140),
                 ("size", "CCopPed", 0x7C4)],
     "note": "the size immediate is at 0x550FF2"},
    {"key": "pools.vehicle", "title": "Vehicle pool",
     "value": "110 slots; element 0xA18 (CHeli); push 110 (imm8) at 0x551029",
     "addrs": [0xB74494, 0x55102A],
     "refs": ["game_sa/Pools.cpp", "VehiclePool.h"], "sources": ["gta-reversed", "MTA"], "conf": "K2",
     "checks": [("global", "ms_pVehiclePool", 0xB74494), ("limit", "ms_pVehiclePool", 110), ("u8", 0x55102A, 110),
                 ("size", "CHeli", 0xA18)]},
    {"key": "pools.building_dummy", "title": "Building and dummy pools",
     "value": "Buildings 13 000, Dummys 2 500",
     "addrs": [0xB74498, 0xB744A0, 0x55105F, 0x5510CF],
     "refs": ["game_sa/Pools.cpp"], "sources": ["gta-reversed", "Sacky Limit Adjuster", "MTA"], "conf": "K2E",
     "checks": [("limit", "ms_pBuildingPool", 13000), ("limit", "ms_pDummyPool", 2500), ("u32", 0x55105F, 13000),
                 ("u32", 0x5510CF, 2500), ("global", "ms_pBuildingPool", 0xB74498),
                 ("global", "ms_pDummyPool", 0xB744A0)]},
    {"key": "pools.object", "title": "Object pool",
     "value": "350 (MTA upstream raises it to 1 200); matrix list 900 (MTA: 3 600)",
     "addrs": [0xB7449C, 0x551097, 0x54F3A1],
     "refs": ["game_sa/Pools.cpp", "MTA Client/game_sa/CGameSA.cpp"], "sources": ["gta-reversed", "MTA"], "conf": "K2E",
     "checks": [("limit", "ms_pObjectPool", 350), ("u32", 0x551097, 350), ("u32", 0x54F3A1, 900),
                 ("global", "ms_pObjectPool", 0xB7449C), ("const", "MAX_OBJECTS", 1200, "mta-upstream")]},
    {"key": "pools.colmodel", "title": "ColModel pool",
     "value": "10 150 (MTA: 12 000; Neon MAX_COL_MODELS 30 000)",
     "addrs": [0xB744A4, 0x551107],
     "refs": ["game_sa/Pools.cpp", "MTA Client/sdk/game/Common.h"], "sources": ["gta-reversed", "MTA"], "conf": "K2",
     "checks": [("limit", "ms_pColModelPool", 10150), ("u32", 0x551107, 10150),
                 ("global", "ms_pColModelPool", 0xB744A4), ("const", "MAX_COL_MODELS", 30000, "mta-neon")]},
    {"key": "pools.ptrnodes", "title": "List node pools",
     "value": "PtrNode Single 70 000, PtrNode Double 3 200, EntryInfoNode 500 (MTA: 90 000 / 74 800 / 72 600)",
     "addrs": [0xB74484, 0xB74488, 0xB7448C, 0x550F46],
     "refs": ["game_sa/Pools.cpp", "MTA Client/sdk/game/Common.h"], "sources": ["gta-reversed", "MTA"], "conf": "K2E",
     "checks": [("limit", "ms_pPtrNodeSingleLinkPool", 70000), ("limit", "ms_pPtrNodeDoubleLinkPool", 3200),
                 ("limit", "ms_pEntryInfoNodePool", 500), ("u32", 0x550F46, 70000)]},
    {"key": "pools.tasks_events", "title": "Task and event pools",
     "value": "Task 500, Event 200, PedIntelligence 140 (MTA: Task 5 000, Event 5 000)",
     "addrs": [0xB744A8, 0xB744AC, 0xB744C0],
     "refs": ["game_sa/Pools.cpp"], "sources": ["gta-reversed", "MTA"], "conf": "K2",
     "checks": [("limit", "ms_pTaskPool", 500), ("limit", "ms_pEventPool", 200),
                 ("limit", "ms_pPedIntelligencePool", 140), ("global", "ms_pTaskPool", 0xB744A8),
                 ("global", "ms_pEventPool", 0xB744AC), ("global", "ms_pPedIntelligencePool", 0xB744C0)]},
    {"key": "pools.small", "title": "Small pools",
     "value": "PointRoute 64, PatrolRoute 32, NodeRoute 64, TaskAllocator 16, PedAttractors 64",
     "addrs": [0xB744B0, 0xB744B4, 0xB744B8, 0xB744BC, 0xB744C4],
     "refs": ["game_sa/Pools.cpp"], "sources": ["gta-reversed", "plugin-sdk", "MTA CLASS_CTaskAllocatorPool"], "conf": "K2",
     "checks": [("limit", "ms_pPointRoutePool", 64), ("limit", "ms_pPatrolRoutePool", 32),
                 ("limit", "ms_pNodeRoutePool", 64), ("limit", "ms_pTaskAllocatorPool", 16),
                 ("limit", "ms_pPedAttractorPool", 64), ("global", "ms_pTaskAllocatorPool", 0xB744BC)],
     "note": "gtamods has an error: TaskAllocator 0xB744CC, the right address is 0xB744BC"},
    {"key": "pools.handle", "title": "Pool object handle (SCM and MTA)",
     "value": "handle = (index << 8) | ref; ref has 7 bits; bit 0x80 of the flag byte = free slot",
     "refs": ["game_sa/Core/Pool.h"], "sources": ["gta-reversed", "gtamods Object_pool"], "conf": "K2",
     "checks": [("func", "CPools::GetPedRef", 0x54FF60), ("func", "CPools::GetPed", 0x54FF90)]},
    {"key": "stores.txd", "title": "TXD store",
     "value": "5 000 slots",
     "addrs": [0xC8800C],
     "refs": ["game_sa/TxdStore.cpp"], "sources": ["gta-reversed"], "conf": "K",
     "checks": [("global", "CTxdStore::ms_pTxdPool", 0xC8800C)]},
    {"key": "stores.col_ipl", "title": "COL and IPL stores",
     "value": "COL files 255, IPL files 256",
     "addrs": [0x965560, 0x411458, 0x8E3FB0, 0x405F26],
     "refs": ["game_sa/Collision/ColStore.cpp", "game_sa/IplStore.cpp"], "sources": ["gta-reversed", "MTA CLimitsSA"], "conf": "K2E",
     "checks": [("u32", 0x411458, 255), ("u32", 0x405F26, 256)]},
    {"key": "pools.misc", "title": "Other pools",
     "value": "Entry-exits 400, stunt jumps 256, QuadTreeNodes 400",
     "addrs": [0x96A7D8, 0xA9A888, 0xB745BC, 0x552C3F],
     "refs": ["game_sa/EntryExitManager.cpp", "game_sa/StuntJumpManager.cpp", "game_sa/QuadTreeNode.h"], "sources": ["gta-reversed"], "conf": "K2E",
     "checks": [("u32", 0x552C3F, 400)]},
    {"key": "modelinfo.ptrs", "title": "Model info table",
     "value": "CModelInfo::ms_modelInfoPtrs[20000] at 0xA9B0C8 (plugin-sdk reads the pointer from the operand at "
        "0x40CD67)",
     "addrs": [0xA9B0C8, 0x40CD67],
     "refs": ["game_sa/Models/ModelInfo.h"], "sources": ["gta-reversed", "plugin-sdk"], "conf": "K2",
     "checks": [("global", "CModelInfo::ms_modelInfoPtrs", 0xA9B0C8),
                 ("limit", "CModelInfo::ms_modelInfoPtrs", 20000)]},
    {"key": "modelinfo.stores", "title": "Model info store capacities",
     "value": "atomic 14 000, time 169, clump 92, vehicle 212, ped 278, weapon 51, damage-atomic 70, 2dfx 100, lod-atomic "
        "1, lod-time 1",
     "refs": ["game_sa/Models/ModelInfo.h"], "sources": ["gta-reversed"], "conf": "K",
     "checks": [("limit", "CModelInfo::ms_atomicModelInfoStore", 14000),
                 ("limit", "CModelInfo::ms_timeModelInfoStore", 169),
                 ("limit", "CModelInfo::ms_clumpModelInfoStore", 92),
                 ("limit", "CModelInfo::ms_vehicleModelInfoStore", 212),
                 ("limit", "CModelInfo::ms_pedModelInfoStore", 278),
                 ("limit", "CModelInfo::ms_weaponModelInfoStore", 51),
                 ("limit", "CModelInfo::ms_damageAtomicModelInfoStore", 70),
                 ("limit", "CModelInfo::ms_lodAtomicModelInfoStore", 1),
                 ("limit", "CModelInfo::ms_lodTimeModelInfoStore", 1)]},
    {"key": "streaming.id_ranges", "title": "Streaming resource id ranges",
     "value": "DFF 0-19999, TXD 20000-24999, COL 25000-25254, IPL 25255-25510, DAT 25511-25574, IFP 25575-25754, RRR "
        "25755-26229, SCM 26230-26311; 26 316 in total",
     "refs": ["game_sa/Streaming.h"], "sources": ["gta-reversed"], "conf": "K",
     "checks": [("const", "RESOURCE_ID_TXD", 20000), ("const", "RESOURCE_ID_COL", 25000),
                 ("const", "RESOURCE_ID_IPL", 25255), ("const", "RESOURCE_ID_DAT", 25511),
                 ("const", "RESOURCE_ID_IFP", 25575), ("const", "RESOURCE_ID_RRR", 25755),
                 ("const", "RESOURCE_ID_SCM", 26230), ("const", "RESOURCE_ID_TOTAL", 26316)]},
    {"key": "ids.vanilla", "title": "Ids used by the stock game",
     "value": "14 832 IDE definitions, ids 0-18630 without duplicates; vehicles 400-611 (212); the SA-MP layer adds "
        "11682-19999",
     "refs": ["report 14 sections 5.2, 5.6"], "sources": ["satk index", "gta-reversed"], "conf": "K2",
     "checks": [("const", "MODEL_VEHICLE_FIRST", 400)]},
    {"key": "streaming.info_array", "title": "CStreamingInfo array",
     "value": "26 316 entries of 0x14 bytes in CStreaming::ms_aInfoForModel (plugin-sdk reads it through the operand at "
        "0x408B25)",
     "addrs": [0x8E4CC0, 0x408B25],
     "refs": ["game_sa/Streaming.h", "game_sa/StreamingInfo.h"], "sources": ["gta-reversed", "plugin-sdk"], "conf": "K2",
     "checks": [("global", "CStreaming::ms_aInfoForModel", 0x8E4CC0), ("size", "CStreamingInfo", 0x14)]},
    {"key": "streaming.memory", "title": "Streaming memory",
     "value": "stock 50 MiB: static 25 600 000 -> stream.ini 'memory' (KB) -> CStreaming::Init2 writes 52 428 800",
     "addrs": [0x8A5A80, 0x5BCD50, 0x5BCD78, 0x5B8E64],
     "refs": ["game_sa/Streaming.cpp", "game_sa/Game.cpp"], "sources": ["gta-reversed", "MTA CLimitsSA/CCore", "exe"], "conf": "K2E",
     "checks": [("global", "CStreaming::ms_memoryAvailable", 0x8A5A80), ("u32", 0x8A5A80, 0x186A000),
                 ("u32", 0x5B8E66, 0x8A5A80), ("u32", 0x5B8E6A, 0x3200000), ("func", "CStreaming::Init2", 0x5B8AD0)],
     "note": "mov [0x8A5A80],0x3200000 at 0x5B8E64 overwrites the stream.ini value; MTA patches out all three writes and "
       "sets its own"},
    {"key": "streaming.vehicles", "title": "Streamed vehicle count",
     "value": "static 12 -> stream.ini 'vehicles' -> Init2 sets 22",
     "addrs": [0x8A5A84, 0x5B8E6E],
     "refs": ["game_sa/Streaming.cpp"], "sources": ["gta-reversed", "MTA SetStreamingVehicles", "exe"], "conf": "K2E",
     "checks": [("u32", 0x8A5A84, 12), ("u32", 0x5B8E70, 0x8A5A84), ("u32", 0x5B8E74, 22)]},
    {"key": "streaming.img_channels", "title": "IMG archives and read channels",
     "value": "up to 8 IMG archives (ms_files), 2 048-byte sectors, 2 channels (ms_channel)",
     "addrs": [0x8E48D8, 0x8E4A60, 0x8E4CB4, 0x8E4CB8],
     "refs": ["game_sa/Streaming.h"], "sources": ["gta-reversed"], "conf": "K",
     "checks": [("global", "CStreaming::ms_files", 0x8E48D8), ("global", "CStreaming::ms_channel", 0x8E4A60),
                 ("global", "CStreaming::ms_memoryUsedBytes", 0x8E4CB4),
                 ("global", "CStreaming::ms_numModelsRequested", 0x8E4CB8)]},
    {"key": "streaming.rw_instances", "title": "Streaming RW instance list",
     "value": "ms_rwObjectInstances.Init(12000); MTA upstream -> 30 000; OLA and SilentPatch patch the same immediate",
     "addrs": [0x5B8E55],
     "refs": ["game_sa/Streaming.cpp", "MTA Client/game_sa/CGameSA.cpp"], "sources": ["gta-reversed", "MTA", "exe"], "conf": "K2E",
     "checks": [("u32", 0x5B8E55, 12000)]},
    {"key": "world.bounds", "title": "World bounds and sectors",
     "value": "WORLD_BOUNDS +-3000; 120x120 sectors of 50 units (ms_aSectors); repeat sectors 16x16; LOD lists 30x30 of "
        "200 units; MAP_Z_LOW_LIMIT -100",
     "addrs": [0xB7D0B8, 0xB992B8, 0xB99EB8],
     "refs": ["game_sa/World.h"], "sources": ["gta-reversed"], "conf": "K",
     "checks": [("global", "CWorld::ms_aSectors", 0xB7D0B8), ("global", "CWorld::ms_aRepeatSectors", 0xB992B8),
                 ("global", "CWorld::ms_aLodPtrLists", 0xB99EB8)]},
    {"key": "paths.grid", "title": "Path node grid",
     "value": "8x8 = 64 areas of 750 units (nodes0..63.dat = DAT ids 0-63); ThePaths at 0x96F050",
     "addrs": [0x96F050],
     "refs": ["game_sa/PathFind.h"], "sources": ["gta-reversed"], "conf": "K",
     "checks": [("global", "ThePaths", 0x96F050)]},
    {"key": "renderer.lists", "title": "Render lists",
     "value": "visible entities 1 000, visible LOD 1 000, super-LOD 50, invisible 150 (Neon MAX_VISIBLE_* 8 192)",
     "addrs": [0xB75898, 0xB748F8, 0xB74830, 0xB745D8],
     "refs": ["game_sa/Renderer.h"], "sources": ["gta-reversed"], "conf": "K",
     "checks": [("global", "CRenderer::ms_aVisibleEntityPtrs", 0xB75898),
                 ("global", "CRenderer::ms_aVisibleLodPtrs", 0xB748F8),
                 ("global", "CRenderer::ms_aVisibleSuperLodPtrs", 0xB74830),
                 ("global", "CRenderer::ms_aInVisibleEntityPtrs", 0xB745D8)]},
    {"key": "renderer.lod_scale", "title": "LOD distance multipliers",
     "value": "ms_lodDistScale 1.2, ms_lowLodDistScale 1.0",
     "addrs": [0x8CD800, 0x8CD804],
     "refs": ["game_sa/Renderer.h"], "sources": ["gta-reversed", "exe"], "conf": "KE",
     "checks": [("f32", 0x8CD800, 1.2), ("f32", 0x8CD804, 1.0), ("global", "CRenderer::ms_lodDistScale", 0x8CD800)]},
    {"key": "gameplay.arrays", "title": "Gameplay arrays (coronas, pickups, radar blips)",
     "value": "coronas 64 (aCoronas), pickups 620 (aPickUps), radar blips 175 (ms_RadarTrace)",
     "addrs": [0xC3E058, 0x9788C0, 0xBA86F0],
     "refs": ["game_sa/Coronas.h", "game_sa/Pickups.h", "game_sa/Radar.h"], "sources": ["gta-reversed", "plugin-sdk", "MTA ARRAY_CORONAS"], "conf": "K2",
     "checks": [("global", "CCoronas::aCoronas", 0xC3E058), ("global", "CPickups::aPickUps", 0x9788C0),
                 ("global", "CRadar::ms_RadarTrace", 0xBA86F0)]},
    {"key": "gameplay.arrays2", "title": "Gameplay arrays (car generators, garages, fires, explosions, projectiles)",
     "value": "car generators 500, garages 50, fires 60 (gFireManager), explosions 16, projectiles 32",
     "addrs": [0xC27AD0, 0xB71F80, 0xC88950, 0xC89110, 0xC891A8],
     "refs": ["game_sa/TheCarGenerators.h", "game_sa/Garages.h", "game_sa/FireManager.h", "game_sa/Explosion.h", "game_sa/ProjectileInfo.h"], "sources": ["gta-reversed", "gtamods"], "conf": "K2",
     "checks": [("global", "gFireManager", 0xB71F80)]},
    {"key": "players", "title": "Players",
     "value": "CWorld::Players[2] at 0xB7CD98; CPlayerInfo = 0x190 bytes; m_pPed at offset +0; FindPlayerPed 0x56E210",
     "addrs": [0xB7CD98, 0x56E210],
     "refs": ["game_sa/World.h", "game_sa/PlayerInfo.h"], "sources": ["gta-reversed", "gtamods"], "conf": "K2",
     "checks": [("global", "CWorld::Players", 0xB7CD98), ("size", "CPlayerInfo", 0x190),
                 ("func", "FindPlayerPed", 0x56E210)]},
    {"key": "scripts.space", "title": "Script memory",
     "value": "ScriptSpace: MAIN 200 000 + MISSION 69 000 bytes; global variable n = 0xA49960 + 4n",
     "addrs": [0xA49960],
     "refs": ["game_sa/Scripts/TheScripts.h"], "sources": ["gta-reversed", "gtamods"], "conf": "K2",
     "checks": [("global", "CTheScripts::ScriptSpace", 0xA49960)]},
    {"key": "scripts.threads", "title": "Script threads",
     "value": "96 CRunningScript (ScriptsArray); 1 024 mission local variables; ScriptParams[32]",
     "addrs": [0xA8B430, 0xA8B42C, 0xA8B428, 0xA48960, 0xA43C78],
     "refs": ["game_sa/Scripts/TheScripts.h", "game_sa/Scripts/RunningScript.h"], "sources": ["gta-reversed", "gtamods"], "conf": "K2",
     "checks": [("global", "CTheScripts::ScriptsArray", 0xA8B430),
                 ("global", "CTheScripts::pActiveScripts", 0xA8B42C),
                 ("global", "CTheScripts::pIdleScripts", 0xA8B428), ("limit", "CTheScripts::ScriptsArray", 96)]},
    {"key": "timer", "title": "Timer",
     "value": "m_snTimeInMilliseconds 0xB7CB84, ms_fTimeStep 0xB7CB5C, ms_fTimeScale 0xB7CB64, game_FPS 0xB7CB50, "
        "m_FrameCounter 0xB7CB4C; CTimer::Update 0x561B10",
     "addrs": [0xB7CB84, 0xB7CB5C, 0xB7CB64, 0xB7CB50, 0xB7CB4C, 0x561B10],
     "refs": ["game_sa/Timer.h"], "sources": ["gta-reversed", "plugin-sdk", "gtamods"], "conf": "K2",
     "checks": [("global", "CTimer::m_snTimeInMilliseconds", 0xB7CB84), ("global", "CTimer::ms_fTimeStep", 0xB7CB5C),
                 ("global", "CTimer::ms_fTimeScale", 0xB7CB64), ("global", "CTimer::game_FPS", 0xB7CB50),
                 ("global", "CTimer::m_FrameCounter", 0xB7CB4C), ("func", "CTimer::Update", 0x561B10)]},
    {"key": "clock", "title": "Game clock",
     "value": "hour 0xB70153, minute 0xB70152, ms per game minute 0xB7015C (.bss, set at run time, usually 1 000)",
     "addrs": [0xB70153, 0xB70152, 0xB7015C],
     "refs": ["game_sa/Clock.h"], "sources": ["gta-reversed", "gtamods"], "conf": "K2",
     "checks": [("global", "CClock::ms_nGameClockHours", 0xB70153),
                 ("global", "CClock::ms_nGameClockMinutes", 0xB70152),
                 ("global", "CClock::ms_nMillisecondsPerGameMinute", 0xB7015C)]},
    {"key": "weather", "title": "Weather and timecyc",
     "value": "23 weather types (0-22; 20 = UNDERWATER, 21-22 EXTRACOLOURS); 8 timecyc hours (0, 5, 6, 7, 12, 19, 20, 22)",
     "addrs": [0xC81320, 0xC8131C, 0xC81318],
     "refs": ["game_sa/Enums/eWeatherType.h", "game_sa/TimeCycle.h", "game_sa/Weather.h"], "sources": ["gta-reversed", "gtamods"], "conf": "K2",
     "checks": [("global", "CWeather::OldWeatherType", 0xC81320), ("global", "CWeather::NewWeatherType", 0xC8131C),
                 ("global", "CWeather::ForcedWeatherType", 0xC81318)],
     "note": "gtamods calls 0xC81320 'Current weather'; in the code it is OldWeatherType, the start of the interpolation"},
    {"key": "handling", "title": "Handling",
     "value": "gHandlingDataMgr: 210 cars + 13 bikes + 24 flying + 12 boats; tHandlingData = 0xE0; the car array starts "
        "at +0x14 (0xC2B9DC)",
     "addrs": [0xC2B9C8, 0xC2B9DC],
     "refs": ["game_sa/cHandlingDataMgr.h", "game_sa/tHandlingData.h"], "sources": ["gta-reversed", "gtamods"], "conf": "K2",
     "checks": [("global", "gHandlingDataMgr", 0xC2B9C8), ("size", "tHandlingData", 0xE0)]},
    {"key": "sizes.entities", "title": "Entity sizes",
     "value": "CEntity 0x38, CBuilding 0x38, CPhysical 0x138, CObject 0x17C, CPed 0x79C, CPlayerPed 0x7A4, CVehicle "
        "0x5A0, CAutomobile 0x988, CBike 0x814, CBoat 0x7E8, CTrain 0x6AC, CPlane 0xA04, CHeli 0xA18, CCamera "
        "0xD78, CColModel 0x30",
     "refs": ["VALIDATE_SIZE"], "sources": ["gta-reversed", "plugin-sdk"], "conf": "K2",
     "checks": [("size", "CEntity", 0x38), ("size", "CBuilding", 0x38), ("size", "CPhysical", 0x138),
                 ("size", "CObject", 0x17C), ("size", "CPed", 0x79C), ("size", "CPlayerPed", 0x7A4),
                 ("size", "CVehicle", 0x5A0), ("size", "CAutomobile", 0x988), ("size", "CBike", 0x814),
                 ("size", "CBoat", 0x7E8), ("size", "CTrain", 0x6AC), ("size", "CPlane", 0xA04),
                 ("size", "CHeli", 0xA18), ("size", "CCamera", 0xD78), ("size", "CColModel", 0x30),
                 ("size", "CPed", 0x79C, "plugin-sdk"), ("size", "CVehicle", 0x5A0, "plugin-sdk")]},
    {"key": "singletons", "title": "Key singletons",
     "value": "TheCamera 0xB6F028, RwEngineInstance 0xC97B24, IDirect3DDevice9* 0xC97C28, FrontEndMenuManager 0xBA6748, "
        "TheText 0xC1B340, CGame::currArea 0xB72914",
     "addrs": [0xB6F028, 0xC97B24, 0xC97C28, 0xBA6748, 0xC1B340, 0xB72914],
     "refs": ["gta-reversed", "plugin-sdk common.cpp"], "sources": ["gta-reversed", "plugin-sdk", "gtamods"], "conf": "K2",
     "checks": [("global", "TheCamera", 0xB6F028), ("global", "RwEngineInstance", 0xC97B24),
                 ("global", "FrontEndMenuManager", 0xBA6748), ("global", "TheText", 0xC1B340),
                 ("global", "CGame::currArea", 0xB72914)]},
    {"key": "anims", "title": "Animations",
     "value": "2 500 hierarchies (ms_aAnimations), 180 blocks (ms_aAnimBlocks = TOTAL_IFP)",
     "addrs": [0xB4EA40, 0xB5D4A0],
     "refs": ["game_sa/Animation/AnimManager.h"], "sources": ["gta-reversed"], "conf": "K",
     "checks": [("global", "CAnimManager::ms_aAnimations", 0xB4EA40),
                 ("global", "CAnimManager::ms_aAnimBlocks", 0xB5D4A0), ("limit", "CAnimManager::ms_aAnimations", 2500),
                 ("limit", "CAnimManager::ms_aAnimBlocks", 180)]},
    {"key": "entrypoints", "title": "Main entry points",
     "value": "CGame::Initialise -> Init1 -> CFileLoader::LoadLevel -> CStreaming::Init2; per frame: Idle, "
        "CGame::Process, CWorld::Process, CStreaming::Update, CTheScripts::Process, RenderScene, "
        "CRenderer::ConstructRenderList",
     "addrs": [0x53BC80, 0x5BF840, 0x5B9030, 0x5B8AD0, 0x53E920, 0x53BEE0, 0x5684A0, 0x40E670, 0x46A000, 0x53DF40, 0x5556E0],
     "refs": ["docs/hooks.json"], "sources": ["gta-reversed"], "conf": "K",
     "checks": [("func", "CGame::Initialise", 0x53BC80), ("func", "CGame::Init1", 0x5BF840),
                 ("func", "CFileLoader::LoadLevel", 0x5B9030), ("func", "CStreaming::Init2", 0x5B8AD0),
                 ("func", "Idle", 0x53E920), ("func", "CGame::Process", 0x53BEE0),
                 ("func", "CWorld::Process", 0x5684A0), ("func", "CStreaming::Update", 0x40E670),
                 ("func", "CTheScripts::Process", 0x46A000), ("func", "RenderScene", 0x53DF40),
                 ("func", "CRenderer::ConstructRenderList", 0x5556E0)]},
]

# key, title, value, refs, sources, conf, verify, checks (optional)
ASSET_FACTS: list[dict] = [
    {"key": "asset.vehicle.lamp_keys", "title": "Vehicle lamp colour keys",
     "value": "material colours 255,175,0 (front left), 0,255,200 (front right), 185,255,0 (rear left), 255,60,0 (rear "
        "right) switch with the lamps only on texture vehiclelights128; the engine draws it white and swaps in "
        "vehiclelightson128 when the lamp is on; on any other texture the key is just a colour",
     "refs": ["CVehicleModelInfo::SetupLightFlags", "vehicle.txd"], "sources": ["satk index", "exe"], "conf": "KE",
     "verify": ["all lamp-key materials of the 212 vanilla vehicles use vehiclelights128",
                "gta_sa.exe holds the names vehiclelights128 and vehiclelightson128"]},
    {"key": "asset.vehicle.paint_keys", "title": "Vehicle paint colour keys",
     "value": "60,255,0 = primary, 255,0,175 = secondary, 0,255,255 = third, 255,0,255 = fourth carcols colour (alpha "
        "ignored); vanilla puts 86 % of its primary-key materials on vehiclegrunge256 (57 %, dirt shows) or "
        "vehiclegeneric256 (30 %), the rest on paintjob remaps and police decals; paint on another texture never "
        "gets dirty",
     "refs": ["CVehicleModelInfo::SetEditableMaterialsCB"], "sources": ["satk index"], "conf": "K2",
     "verify": ["181 of 212 vanilla vehicles carry the primary key; 2 382 primary-key materials, 1 356 on "
                 "vehiclegrunge256 and 703 on vehiclegeneric256"]},
    {"key": "asset.vehicle.wheel_scale", "title": "Wheel size = IDE wheel_scale",
     "value": "the wheel mesh diameter equals the vehicles.ide wheel_scale (ratio 0.99-1.00-1.03 over vanilla cars); the "
        "engine uses wheel_scale/2 as the physics radius; one wheel mesh is cloned to every wheel dummy (wheel_id "
        "-1)",
     "refs": ["data/vehicles.ide"], "sources": ["satk index", "game files"], "conf": "K2",
     "verify": ["wheel mesh height / wheel_scale is 0.95..1.05 on sampled vanilla sedans"]},
    {"key": "asset.vehicle.col_piece", "title": "Vehicle COL sphere piece byte = eCarPiece",
     "value": "the second surface byte of each sphere of a vehicle's embedded COL names the damaged part (eCarPiece: 0 "
        "default, 1 bonnet, 2 boot, 3 bump front, 4 bump rear, 5-8 doors, 9-12 wings, 13-16 wheels, "
        "17-18 bike wheels, 19 windscreen); 98.7 % of the 5 414 vanilla vehicle spheres use 0..21 (65 % are 0, "
        "the rest mostly bumpers and front doors; 70 spheres carry 255); names: satk kb sym eCarPiece",
     "refs": ["game_sa/Entity/Vehicle/Vehicle.h eCarPiece"], "sources": ["game files", "gta-reversed"], "conf": "K2",
     "verify": ["piece bytes of all vanilla vehicle COLs: 5 414 spheres, 98.7 % in 0..21, 3 546 of them 0"]},
    {"key": "asset.vehicle.no_mips", "title": "Vehicle textures have no mipmaps",
     "value": "all 593 textures of vanilla vehicle TXDs and vehicle.txd have exactly 1 level; do not add mip chains to "
        "vehicle or ped TXDs",
     "refs": ["models/gta3.img", "models/generic/vehicle.txd"], "sources": ["satk index"], "conf": "K",
     "verify": ["every texture of the vanilla vehicle TXDs has levels = 1"]},
    {"key": "asset.vehicle.upgrade_frames", "title": "Tuning frames ug_*",
     "value": "ug_bonnet, ug_bonnet_left/right, ug_spoiler, ug_wing_left/right, ug_roof, ug_nitro, ug_lights, "
        "ug_frontbullbar, ug_backbullbar (+ _dam versions) are the attach points of carmods.dat upgrades; the "
        "engine finds them by name; 83 of 212 vanilla vehicles have them; keep every ug_* frame of a replaced model "
        "(a missing one crashes when that upgrade is fitted)",
     "refs": ["data/carmods.dat"], "sources": ["satk index", "exe"], "conf": "KE",
     "verify": ["83 vanilla vehicles have ug_* frames",
                "gta_sa.exe holds the standard ug_* names"]},
    {"key": "asset.vehicle.hinges", "title": "Hinged parts are modelled in their dummy's space",
     "value": "door_*_dummy sits on the hinge at the front edge of the door, bonnet_dummy at the rear edge of the bonnet, "
        "boot_dummy at the front edge of the boot; the _ok/_dam geometry hangs off the dummy (doors and boot extend "
        "to -y, the bonnet to +y) because the engine rotates the part about the dummy origin",
     "refs": ["CAutomobile::ProcessSwingingDoor"], "sources": ["game files"], "conf": "K",
     "verify": ["on sampled vanilla sedans door_lf_ok lies at y <= 0 and bonnet_ok at y >= 0 of their dummies"]},
    {"key": "asset.vehicle.glass", "title": "Vehicle glass material",
     "value": "windscreen_ok uses vehiclegeneric256 in white with alpha 128 (112 of the vanilla windscreens); dark glass "
        "is untextured black alpha 128; windscreen_dam uses vehicleshatter128",
     "refs": ["models/gta3.img"], "sources": ["satk index"], "conf": "K",
     "verify": ["the most common windscreen_ok material of vanilla vehicles is ffffff80 on vehiclegeneric256"]},
    {"key": "asset.vehicle.normals", "title": "Vehicle geometry carries normals, no prelight",
     "value": "all 3 323 geometries of vanilla vehicles have vertex normals and none has prelight or night colours (cars "
        "are lit by the car pipeline); smooth shading comes from shared normals: weld, smooth, mark sharp edges, "
        "then write normals",
     "refs": ["models/gta3.img"], "sources": ["satk index"], "conf": "K",
     "verify": ["every vanilla vehicle geometry has the normals flag and none the prelit flag"]},
    {"key": "asset.vehicle.dirt", "title": "Vehicle dirt levels",
     "value": "CCarFXRenderer::InitialiseDirtTexture (0x5D5BC0) builds 16 dirt levels from vehiclegrunge256: level i "
        "(0..15) has RGB = c*i/16 + 255*(16-i)/16 with alpha kept; spawned cars get 0..14; the raw texture equals "
        "level 16, dirtier than anything the game shows",
     "refs": ["game_sa/Fx/CarFXRenderer.cpp"], "sources": ["gta-reversed", "satk index"], "conf": "K2",
     "checks": [("func", "CCarFXRenderer::InitialiseDirtTexture", 0x5D5BC0)],
     "verify": ["vehicle.txd holds vehiclegrunge256",
                "a function starts at 0x5D5BC0 in the symbol tables"]},
    {"key": "asset.vehicle.frames", "title": "Frame names per vehicle type",
     "value": "the engine looks frames up by name in 12 fixed tables (ms_vehicleDescs 0x8A7740, one per vehicle type: "
        "automobile 61 names = 40 parts, 15 dummies, 6 extras) and peds in m_pPedIds 0x8A6268; a missing name loses "
        "that part; list them with satk re nodes --type <type>",
     "refs": ["CVehicleModelInfo::ms_vehicleDescs", "CPedModelInfo::m_pPedIds"], "sources": ["exe", "gta-reversed"], "conf": "KE",
     "checks": [("global", "CVehicleModelInfo::ms_vehicleDescs", 0x8A7740),
                 ("global", "CPedModelInfo::m_pPedIds", 0x8A6268)],
     "verify": ["satk re nodes reads 12 vehicle tables and the ped table from gta_sa.exe"]},
    {"key": "asset.world.big_building", "title": "Big-building rule",
     "value": "an entity is set up as a big building (drawn from far, kept in the LOD lists) when it has LOD children or "
        "LODDistMultiplier (1.0) x draw distance > 300; vanilla: 4 474 definitions draw > 300, 4 288 of them are "
        "LOD models; a high-detail model with collision should keep draw <= 299",
     "refs": ["CEntity::SetupBigBuilding", "CColAccel::addIPLEntity"], "sources": ["gta-reversed", "satk index"], "conf": "K2",
     "checks": [("func", "CEntity::SetupBigBuilding", 0x533150)],
     "verify": ["over 90 % of vanilla definitions with draw > 300 are placed as LODs"]},
    {"key": "asset.col.face_light", "title": "COL face light byte",
     "value": "the light byte of a COL2/COL3 mesh face holds day brightness in the low nibble and night brightness in the "
        "high nibble (0..15); peds and cars standing on it take that light; 0 = ambient only (about 3x darker by "
        "day) plus vehicle headlights; vanilla building COLs set it on 96 % of faces",
     "refs": ["CColTriangle"], "sources": ["game files", "gta-reversed"], "conf": "K2",
     "verify": ["over 90 % of the faces of vanilla lae* building COLs have a non-zero day nibble"]},
    {"key": "asset.ide.flags", "title": "IDE object flag bits",
     "value": "0x1 road (wet reflections), 0x4 draw last (alpha; 2 332 vanilla definitions), 0x8 additive, 0x40 no "
        "z-buffer write, 0x80 receives no shadows (5 079), 0x200/0x400 glass type 1/2, 0x800 garage door, 0x1000 "
        "damageable, 0x2000 tree, 0x4000 palm, 0x8000 does not collide with flyers, 0x100000 tag, 0x200000 backface "
        "culling off (1 526), 0x400000 breakable statue; field names: satk kb struct sItemDefinitionFlags --bits",
     "refs": ["game_sa/Enums/eItemDefinitionFlags.h", "SetAtomicModelInfoFlags 0x5B3B20"], "sources": ["gta-reversed", "satk index"], "conf": "K2",
     "verify": ["the listed bits are used by vanilla objs/tobj definitions with the counts given"]},
    {"key": "asset.streaming.budget", "title": "Streaming memory budget",
     "value": "the stock game streams everything (models, textures, collisions) within 50 MiB = 52 428 800 bytes "
        "(CStreaming::Init2 writes it over stream.ini); oversized TXDs push other models out; check a pack with "
        "satk texture budget when it is available",
     "refs": ["game_sa/Streaming.cpp"], "sources": ["exe"], "conf": "KE",
     "checks": [("u32", 0x5B8E6A, 0x3200000)],
     "verify": ["gta_sa.exe writes 0x3200000 at 0x5B8E64"]},
    {"key": "asset.capacity.stores", "title": "Stock capacity left for new content",
     "value": "model stores used by vanilla: vehicle 212 of 212, ped 276 of 278, weapon 50 of 51, object 14 045 of 14 070 "
        "(14 000 + 70 damageable), timed 160 of 169, clump 89 of 92; COL slots 252 of 255 (the generic slot + "
        "251 COL files in the IMGs); TXD slots about 4 040 of 5 000; IDE 2dfx 97 of 100; entry-exits 376 of 400; "
        "more needs a limit adjuster or new ids (satk id free, satk mod check)",
     "refs": ["CModelInfo stores", "CColStore", "CTxdStore", "CEntryExitManager"],
     "sources": ["satk index", "gta-reversed"], "conf": "K2",
     "checks": [("limit", "CModelInfo::ms_vehicleModelInfoStore", 212),
                 ("limit", "CModelInfo::ms_pedModelInfoStore", 278)],
     "verify": ["vanilla index counts: cars 212, peds 276, weap 50, objs 14 045, tobj 160, anim + hier 89, enex "
                "376, IDE 2dfx 97, COL files in IMGs 251, TXDs 4 000-4 100"]},
]


def topic(key: str) -> str:
    return key.split(".", 1)[0]


def all_facts() -> list[dict]:
    """Engine facts followed by the authoring facts."""
    return FACTS + ASSET_FACTS
