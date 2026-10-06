"""1.0 US (HOODLUM) engine addresses used by the single-player bridge ASI (SPEC lane spbridge).

This table is the **single source of truth** for ``satk_sp.asi``: the generated C++ header
``native/sp_bridge/sp_addresses.hpp`` mirrors it (``satk sp addresses --emit-header``), and the
tests check both that the header matches this table (:mod:`tests.spbridge.test_addresses`) and
that every address agrees with the knowledge base / symbol DB and, for a hook site, with the
bytes of the clean ``gta_sa.exe`` (the ``game`` marker reads the clean copy read-only).

Everything here is public, well-documented engine knowledge from gta-reversed and plugin-sdk for
the 1.0 US executable; no game bytes are stored, only numbers and names.

Kinds:

* ``func`` — a function entry point (called through a cast function pointer);
* ``global`` — a process-global variable (read/written directly);
* ``hook`` — a ``call rel32`` instruction the ASI detours; ``target`` is the function the original
  ``call`` reaches, so the ASI can verify the site before patching and chain to the original.

Pools store their element records as the size of the *largest* subclass the pool template names
(``CPool<CVehicle, CHeli>`` → ``sizeof(CHeli)``); :data:`POOLS` records that element size so the
ASI can stride the object array. The byte-map entry is 1 byte (``nId:7`` + ``bEmpty:1``); the GTA
handle of slot ``i`` is ``(i << 8) | byteMap[i]``.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["GAME_VERSION", "Addr", "Pool", "FUNCS", "GLOBALS", "HOOKS", "POOLS",
           "ENTITY_OFFSETS", "ENTITY_TYPES", "STREAMING_FLAGS", "all_symbols", "cpp_header"]

#: The only edition the ASI supports. Other editions get a clear refusal at load time.
GAME_VERSION = "1.0us"


@dataclass(frozen=True)
class Addr:
    """One named address with its engine symbol (``symbol`` is what the KB/symbol DB stores)."""

    name: str
    addr: int
    kind: str  # func | global | hook
    symbol: str
    note: str = ""
    target: int = 0  # hook only: the function the original `call rel32` reaches


@dataclass(frozen=True)
class Pool:
    """One entity pool: a ``CPool<...>**`` global plus the element stride (largest subclass)."""

    name: str
    ptr_addr: int  # address of the CPool<...>* global
    elem_size: int  # sizeof(largest subclass) = pool element stride
    kind: str  # EntityRef kind the pool yields
    symbol: str


# --------------------------------------------------------------------------- functions

FUNCS: tuple[Addr, ...] = (
    # frame / scene
    Addr("CGame_Process", 0x53BEE0, "func", "CGame::Process", "per-frame game tick (hook chains to it)"),
    # camera
    Addr("CCamera_TakeControlNoEntity", 0x50C8B0, "func", "CCamera::TakeControlNoEntity",
         "take the camera into a scripted fixed mode"),
    Addr("CCamera_SetCamPositionForFixedMode", 0x50BEC0, "func", "CCamera::SetCamPositionForFixedMode",
         "set the source/up offset of the fixed camera"),
    Addr("CCamera_Restore", 0x50B930, "func", "CCamera::Restore", "return the camera to the player"),
    Addr("CCamera_RestoreWithJumpCut", 0x50BAB0, "func", "CCamera::RestoreWithJumpCut", "restore with a jump cut"),
    # environment
    Addr("CClock_SetGameClock", 0x52D150, "func", "CClock::SetGameClock", "set hours/minutes/day"),
    Addr("CWeather_ForceWeatherNow", 0x72A4F0, "func", "CWeather::ForceWeatherNow", "force a weather id now"),
    Addr("CWeather_ReleaseWeather", 0x72A510, "func", "CWeather::ReleaseWeather", "stop forcing the weather"),
    # world / player
    Addr("FindPlayerPed", 0x56E210, "func", "FindPlayerPed", "the player ped (NULL in some states)"),
    Addr("FindPlayerVehicle", 0x56E0D0, "func", "FindPlayerVehicle", "the player's current vehicle or NULL"),
    Addr("CWorld_Add", 0x563220, "func", "CWorld::Add", "add an entity to the world sectors"),
    Addr("CWorld_Remove", 0x563280, "func", "CWorld::Remove", "remove an entity from the world sectors"),
    Addr("CWorld_RemoveReferencesToDeletedObject", 0x565510, "func", "CWorld::RemoveReferencesToDeletedObject",
         "clear references before deleting an entity"),
    Addr("CWorld_ProcessLineOfSight", 0x56BA00, "func", "CWorld::ProcessLineOfSight", "collision ray for pick/raycast"),
    Addr("CWorld_FindGroundZForCoord", 0x569660, "func", "CWorld::FindGroundZForCoord", "ground Z under (x, y)"),
    # streaming
    Addr("CStreaming_RequestModel", 0x4087E0, "func", "CStreaming::RequestModel", "request a model (flags)"),
    Addr("CStreaming_LoadAllRequestedModels", 0x40EA10, "func", "CStreaming::LoadAllRequestedModels",
         "block until requested models are loaded"),
    Addr("CStreaming_LoadScene", 0x40EB70, "func", "CStreaming::LoadScene", "stream the world around a point"),
    Addr("CStreaming_SetModelIsDeletable", 0x409C10, "func", "CStreaming::SetModelIsDeletable",
         "allow a model to be streamed out again"),
    Addr("CStreaming_SetMissionDoesntRequireModel", 0x409C90, "func", "CStreaming::SetMissionDoesntRequireModel",
         "drop the mission-required flag of a model"),
    # spawning
    Addr("CCarCtrl_CreateCarForScript", 0x431F80, "func", "CCarCtrl::CreateCarForScript",
         "spawn a vehicle (boat/automobile/bike/...)"),
    Addr("CPopulation_AddPed", 0x612710, "func", "CPopulation::AddPed", "spawn a ped of a given ped type"),
    Addr("CObject_Create", 0x5A1F60, "func", "CObject::Create", "create a CObject for a model id"),
    Addr("CTheScripts_ClearSpaceForMissionEntity", 0x486B00, "func", "CTheScripts::ClearSpaceForMissionEntity",
         "push away things overlapping a freshly spawned entity"),
    # model info
    Addr("CModelInfo_GetModelInfo", 0x4C5940, "func", "CModelInfo::GetModelInfo", "CBaseModelInfo* for a model id"),
    # renderware device
    Addr("RwD3D9GetCurrentD3DDevice", 0x7F9D50, "func", "RwD3D9GetCurrentD3DDevice", "the live IDirect3DDevice9*"),
)

# --------------------------------------------------------------------------- globals

GLOBALS: tuple[Addr, ...] = (
    Addr("TheCamera", 0xB6F028, "global", "TheCamera", "CCamera (size 0xD78)"),
    Addr("CDraw_ms_fFOV", 0x8D5038, "global", "CDraw::ms_fFOV", "current vertical-ish FOV used by the game"),
    Addr("CDraw_ms_fAspectRatio", 0xC3EFA4, "global", "CDraw::ms_fAspectRatio", "current aspect ratio"),
    Addr("Scene", 0xC17038, "global", "Scene", "CScene {RpWorld*, RwCamera*}"),
    Addr("RwD3DDevice", 0xC97C28, "global", "RwD3DDevice", "LPDIRECT3DDEVICE9 (also via RwD3D9GetCurrentD3DDevice)"),
    Addr("CTimer_m_FrameCounter", 0xB7CB4C, "global", "CTimer::m_FrameCounter", "uint32 frame counter"),
    Addr("CTimer_m_snTimeInMilliseconds", 0xB7CB84, "global", "CTimer::m_snTimeInMilliseconds", "game time in ms"),
    Addr("CTimer_ms_fTimeStep", 0xB7CB5C, "global", "CTimer::ms_fTimeStep", "frame time step"),
    Addr("CTimer_m_UserPause", 0xB7CB49, "global", "CTimer::m_UserPause", "bool user pause"),
    Addr("CTimer_m_CodePause", 0xB7CB48, "global", "CTimer::m_CodePause", "bool code pause"),
    Addr("CClock_ms_nGameClockHours", 0xB70153, "global", "CClock::ms_nGameClockHours", "uint8 hour"),
    Addr("CClock_ms_nGameClockMinutes", 0xB70152, "global", "CClock::ms_nGameClockMinutes", "uint8 minute"),
    Addr("CWeather_ForcedWeatherType", 0xC81318, "global", "CWeather::ForcedWeatherType", "int16 forced weather"),
    Addr("CWeather_OldWeatherType", 0xC81320, "global", "CWeather::OldWeatherType", "int16 weather A"),
    Addr("CWeather_NewWeatherType", 0xC8131C, "global", "CWeather::NewWeatherType", "int16 weather B"),
    Addr("CWeather_InterpolationValue", 0xC8130C, "global", "CWeather::InterpolationValue", "float A->B blend"),
    Addr("CTheScripts_bDisplayHud", 0xA444A0, "global", "CTheScripts::bDisplayHud", "bool HUD on"),
    Addr("CHud_bScriptDontDisplayRadar", 0xBAA3FB, "global", "CHud::bScriptDontDisplayRadar", "bool hide radar"),
    Addr("CGame_currArea", 0xB72914, "global", "CGame::currArea", "int current interior/area id"),
    Addr("CModelInfo_ms_modelInfoPtrs", 0xA9B0C8, "global", "CModelInfo::ms_modelInfoPtrs",
         "CBaseModelInfo*[] (20000)"),
)

# --------------------------------------------------------------------------- hook sites

#: The ``call CGame::Process`` inside ``Idle`` (``gameProcessEvent`` of plugin-sdk, 1.0 US). The
#: ASI rewrites this one ``call rel32`` to a trampoline that runs ``CGame::Process`` and then pumps
#: the socket on the main thread. ``Idle`` itself is locked by gta-reversed's hook manager, so the
#: call site is the safe, single-instruction place to attach.
HOOKS: tuple[Addr, ...] = (
    Addr("gameProcessEvent", 0x53E981, "hook", "call CGame::Process (in Idle)",
         "per-frame main-thread pump point", target=0x53BEE0),
)

# --------------------------------------------------------------------------- pools

POOLS: tuple[Pool, ...] = (
    Pool("ped", 0xB74490, 0x7C4, "ped", "CPools::ms_pPedPool"),         # CPool<CPed, CCopPed>, sizeof(CCopPed)=0x7C4
    Pool("vehicle", 0xB74494, 0xA18, "vehicle", "CPools::ms_pVehiclePool"),  # CPool<CVehicle, CHeli>, sizeof(CHeli)=0xA18
    Pool("building", 0xB74498, 0x38, "building", "CPools::ms_pBuildingPool"),  # CPool<CBuilding>
    Pool("object", 0xB7449C, 0x19C, "object", "CPools::ms_pObjectPool"),   # CPool<CObject, CCutsceneObject>=0x19C
    Pool("dummy", 0xB744A0, 0x38, "dummy", "CPools::ms_pDummyPool"),       # CPool<CDummy>
)

# --------------------------------------------------------------------------- struct offsets

#: Byte offsets inside engine objects the ASI reads (from plugin-sdk / gta-reversed, 1.0 US).
ENTITY_OFFSETS: dict[str, int] = {
    "CPlaceable.m_placement": 0x04,   # CSimpleTransform {CVector pos; float heading}
    "CSimpleTransform.pos": 0x00,
    "CPlaceable.m_matrix": 0x14,      # CMatrixLink* (NULL for many buildings)
    "CMatrix.pos": 0x30,              # CVector in a CMatrix
    "CEntity.m_pRwObject": 0x18,
    "CEntity.m_nModelIndex": 0x22,    # uint16
    "CEntity.m_nType_byte": 0x36,     # byte holding m_nType:3 | m_nStatus:5
    "CEntity.m_nAreaCode": 0x2F,
    "CBaseModelInfo.m_nTxdIndex": 0x0A,
    "CBaseModelInfo.m_nFlags": 0x12,
    "CBaseModelInfo.m_fDrawDistance": 0x18,
    "CPed.m_nPedType": 0x598,
    "CVehicle.m_nCreatedBy": 0x4A4,  # eVehicleCreatedBy byte
    "CObject.m_nObjectType": 0x13C,
    "CCamera.m_aCams": 0x174,          # std::array<CCam, 3>; stride = sizeof(CCam) = 0x238
    "CCamera.m_nActiveCam": 0x59,
    "CCam.size": 0x238,
    "CCam.m_vecSource": 0x19C,
    "CCam.m_vecFront": 0x190,
    "CCam.m_vecUp": 0x1B4,
    "CCam.m_fFOV": 0xB4,
    "CCam.m_vecCamFixedModeSource": 0x160,
    "CCam.m_vecCamFixedModeUpOffSet": 0x16C,
}

#: ``m_nType`` field (low 3 bits of :data:`ENTITY_OFFSETS`\['CEntity.m_nType_byte']).
ENTITY_TYPES: dict[int, str] = {0: "nothing", 1: "building", 2: "vehicle", 3: "ped", 4: "object", 5: "dummy"}

#: ``CStreaming::RequestModel`` flag bits (``eStreamingFlags``).
STREAMING_FLAGS: dict[str, int] = {
    "game_required": 0x02, "mission_required": 0x04, "keep_in_memory": 0x08, "priority_request": 0x10,
}


def all_symbols() -> tuple[Addr, ...]:
    """Every ``func``/``global``/``hook`` address plus the pool pointers, for the KB cross-check."""
    pool_addrs = tuple(Addr(f"{p.symbol.split('::')[-1]}", p.ptr_addr, "global", p.symbol,
                            f"CPool element stride 0x{p.elem_size:X}") for p in POOLS)
    return FUNCS + GLOBALS + HOOKS + pool_addrs


# --------------------------------------------------------------------------- C++ header

def _cpp_lines() -> list[str]:
    out: list[str] = []
    out.append("// sp_addresses.hpp - generated from satk.spbridge.addresses by `satk sp addresses --emit-header`.")
    out.append("// 1.0 US (HOODLUM) engine addresses for satk_sp.asi. Do not edit by hand; edit the Python table.")
    out.append("// Public engine knowledge (gta-reversed, plugin-sdk); MIT. No game bytes here, only numbers.")
    out.append("#ifndef SATK_SP_ADDRESSES_HPP")
    out.append("#define SATK_SP_ADDRESSES_HPP")
    out.append("#include <cstdint>")
    out.append("namespace sp { namespace addr {")
    out.append(f'  static const char* const kGameVersion = "{GAME_VERSION}";')
    out.append("  // --- functions ---")
    for a in FUNCS:
        out.append(f"  static const std::uint32_t {a.name} = 0x{a.addr:06X}; // {a.symbol}")
    out.append("  // --- globals ---")
    for a in GLOBALS:
        out.append(f"  static const std::uint32_t {a.name} = 0x{a.addr:06X}; // {a.symbol}")
    out.append("  // --- hook sites (call rel32 -> target) ---")
    for a in HOOKS:
        out.append(f"  static const std::uint32_t {a.name} = 0x{a.addr:06X}; // {a.symbol}")
        out.append(f"  static const std::uint32_t {a.name}_target = 0x{a.target:06X};")
    out.append("  // --- pools: {ptr-to-CPool*, element stride} ---")
    for p in POOLS:
        nm = p.symbol.split("::")[-1]
        out.append(f"  static const std::uint32_t {nm}_ptr = 0x{p.ptr_addr:06X}; "
                   f"static const std::uint32_t {nm}_stride = 0x{p.elem_size:X}; // {p.kind}")
    out.append("  // --- struct offsets ---")
    for k, v in ENTITY_OFFSETS.items():
        cname = "off_" + k.replace(".", "_").replace("[", "").replace("]", "")
        out.append(f"  static const std::uint32_t {cname} = 0x{v:X};")
    out.append("  // --- streaming flags ---")
    for k, v in STREAMING_FLAGS.items():
        out.append(f"  static const std::uint32_t streaming_{k} = 0x{v:02X};")
    out.append("} } // namespace sp::addr")
    out.append("#endif // SATK_SP_ADDRESSES_HPP")
    return out


def cpp_header() -> str:
    """The exact text of ``native/sp_bridge/sp_addresses.hpp`` (checked for drift by the tests)."""
    return "\n".join(_cpp_lines()) + "\n"
