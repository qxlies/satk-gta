// sp_engine.hpp - typed access to the 1.0 US engine for satk_sp.asi (game-only at runtime).
//
// MIT License. Copyright (c) 2026 satk contributors. Header-only, C++14, x86.
//
// Every entry point and global comes from sp_addresses.hpp (generated from the satk knowledge
// base). The casts compile anywhere; they are only meaningful inside a running gta_sa.exe 1.0 US.
// This header contains no game code, only address casts and small struct shapes documented by
// gta-reversed / plugin-sdk. Keep it side-effect free: the backend decides when to call.

#ifndef SP_ENGINE_HPP
#define SP_ENGINE_HPP

#include <cstdint>
#include <cmath>

#include "sp_addresses.hpp"

namespace sp {
namespace engine {

struct Vec3 { float x = 0, y = 0, z = 0; };

template <typename Fn>
inline Fn at(std::uint32_t a) { return reinterpret_cast<Fn>(a); }

template <typename T>
inline T& glob(std::uint32_t a) { return *reinterpret_cast<T*>(a); }

// --- camera ---------------------------------------------------------------------------------

// Active CCam inside TheCamera (m_aCams[m_nActiveCam]).
inline std::uint8_t* active_cam() {
    auto* cam = reinterpret_cast<std::uint8_t*>(addr::TheCamera);
    const std::uint8_t idx = *(cam + addr::off_CCamera_m_nActiveCam);
    return cam + addr::off_CCamera_m_aCams + (std::size_t)idx * addr::off_CCam_size;
}

inline Vec3 cam_source() { return *reinterpret_cast<Vec3*>(active_cam() + addr::off_CCam_m_vecSource); }
inline Vec3 cam_front() { return *reinterpret_cast<Vec3*>(active_cam() + addr::off_CCam_m_vecFront); }
inline Vec3 cam_up() { return *reinterpret_cast<Vec3*>(active_cam() + addr::off_CCam_m_vecUp); }
inline float cam_fov() { return *reinterpret_cast<float*>(active_cam() + addr::off_CCam_m_fFOV); }

// Place the camera at `src` looking at `target` with FOV `fov_deg` (CCamera fixed mode).
inline void set_fixed_camera(const Vec3& src, const Vec3& target, float /*fov_deg*/) {
    Vec3 up{0, 0, 0};
    // SetCamPositionForFixedMode(const CVector& source, const CVector& upOffset)
    at<void(__thiscall*)(void*, const Vec3&, const Vec3&)>(addr::CCamera_SetCamPositionForFixedMode)(
        reinterpret_cast<void*>(addr::TheCamera), src, up);
    // TakeControlNoEntity(const CVector& point, eSwitchType switchType=2 jump cut, int who=1 script)
    at<void(__thiscall*)(void*, const Vec3&, short, int)>(addr::CCamera_TakeControlNoEntity)(
        reinterpret_cast<void*>(addr::TheCamera), target, 2, 1);
}

inline void restore_camera() {
    at<void(__thiscall*)(void*)>(addr::CCamera_Restore)(reinterpret_cast<void*>(addr::TheCamera));
}

// --- environment ----------------------------------------------------------------------------

inline void set_clock(int hours, int minutes) {
    glob<std::uint8_t>(addr::CClock_ms_nGameClockHours) = (std::uint8_t)hours;
    glob<std::uint8_t>(addr::CClock_ms_nGameClockMinutes) = (std::uint8_t)minutes;
}
inline int clock_hours() { return glob<std::uint8_t>(addr::CClock_ms_nGameClockHours); }
inline int clock_minutes() { return glob<std::uint8_t>(addr::CClock_ms_nGameClockMinutes); }

inline void force_weather(int weather) {
    at<void(__cdecl*)(short)>(addr::CWeather_ForceWeatherNow)((short)weather);
}
inline void release_weather() { at<void(__cdecl*)()>(addr::CWeather_ReleaseWeather)(); }
inline int weather_now() { return glob<std::int16_t>(addr::CWeather_NewWeatherType); }

inline void set_freeze(bool on) { glob<bool>(addr::CTimer_m_UserPause) = on; }
inline bool frozen() { return glob<bool>(addr::CTimer_m_UserPause); }

// --- world / player -------------------------------------------------------------------------

inline void* player_ped() { return at<void*(__cdecl*)()>(addr::FindPlayerPed)(); }
inline void* player_vehicle() { return at<void*(__cdecl*)()>(addr::FindPlayerVehicle)(); }

inline std::uint32_t frame_counter() { return glob<std::uint32_t>(addr::CTimer_m_FrameCounter); }

inline Vec3 entity_pos(void* ent) {
    auto* e = reinterpret_cast<std::uint8_t*>(ent);
    void* matrix = *reinterpret_cast<void**>(e + addr::off_CPlaceable_m_matrix);
    if (matrix) return *reinterpret_cast<Vec3*>(reinterpret_cast<std::uint8_t*>(matrix) + addr::off_CMatrix_pos);
    return *reinterpret_cast<Vec3*>(e + addr::off_CPlaceable_m_placement + addr::off_CSimpleTransform_pos);
}

inline std::uint16_t entity_model(void* ent) {
    return *reinterpret_cast<std::uint16_t*>(reinterpret_cast<std::uint8_t*>(ent) + addr::off_CEntity_m_nModelIndex);
}
inline int entity_type(void* ent) {
    const std::uint8_t b = *reinterpret_cast<std::uint8_t*>(reinterpret_cast<std::uint8_t*>(ent) + addr::off_CEntity_m_nType_byte);
    return b & 0x07;
}

// Teleport any entity through its vtable slot 14 (Teleport(CVector, bool)).
inline void teleport_entity(void* ent, const Vec3& dst, bool reset_rot) {
    void** vtbl = *reinterpret_cast<void***>(ent);
    at<void(__thiscall*)(void*, Vec3, bool)>(reinterpret_cast<std::uint32_t>(vtbl[14]))(ent, dst, reset_rot);
}

inline void world_add(void* ent) { at<void(__thiscall*)(void*)>(addr::CWorld_Add)(ent); }
inline void world_remove(void* ent) { at<void(__thiscall*)(void*)>(addr::CWorld_Remove)(ent); }
inline float ground_z(float x, float y) { return at<float(__cdecl*)(float, float)>(addr::CWorld_FindGroundZForCoord)(x, y); }

// --- streaming ------------------------------------------------------------------------------

inline void request_model(int id, int flags) { at<void(__cdecl*)(int, int)>(addr::CStreaming_RequestModel)(id, flags); }
inline void load_all_requested(bool priority_only) { at<void(__cdecl*)(bool)>(addr::CStreaming_LoadAllRequestedModels)(priority_only); }
inline void load_scene(const Vec3& p) { at<void(__cdecl*)(const Vec3&)>(addr::CStreaming_LoadScene)(p); }
inline void set_model_deletable(int id) { at<void(__cdecl*)(int)>(addr::CStreaming_SetModelIsDeletable)(id); }

inline void stream_around(const Vec3& p) {
    load_scene(p);
    load_all_requested(false);
}

// --- model info -----------------------------------------------------------------------------

inline void* model_info(int id) { return at<void*(__cdecl*)(int)>(addr::CModelInfo_GetModelInfo)(id); }
inline bool model_is_vehicle_range(int id) { return id >= 400 && id <= 611; }

// --- spawning -------------------------------------------------------------------------------

// Request + block-load a model with mission flags so it is not streamed out from under us.
inline void ensure_model(int id) {
    request_model(id, (int)(addr::streaming_mission_required | addr::streaming_keep_in_memory));
    load_all_requested(true);
}

inline void* spawn_vehicle(int model, const Vec3& pos) {
    ensure_model(model);
    // CCarCtrl::CreateCarForScript(int model, CVector pos, bool missionCleanup)
    return at<void*(__cdecl*)(int, Vec3, bool)>(addr::CCarCtrl_CreateCarForScript)(model, pos, false);
}

inline void* spawn_ped(int ped_type, int model, const Vec3& pos) {
    ensure_model(model);
    // CPopulation::AddPed(ePedType, eModelID, const CVector&, bool makeWander)
    return at<void*(__cdecl*)(int, int, const Vec3&, bool)>(addr::CPopulation_AddPed)(ped_type, model, pos, false);
}

inline void* spawn_object(int model, const Vec3& pos) {
    ensure_model(model);
    void* obj = at<void*(__cdecl*)(int, bool)>(addr::CObject_Create)(model, false);
    if (obj) {
        teleport_entity(obj, pos, true);
        world_add(obj);
    }
    return obj;
}

// --- HUD ------------------------------------------------------------------------------------

inline void set_hud(bool on) { glob<bool>(addr::CTheScripts_bDisplayHud) = on; }
inline bool hud_on() { return glob<bool>(addr::CTheScripts_bDisplayHud); }
inline void set_radar_hidden(bool hidden) { glob<bool>(addr::CHud_bScriptDontDisplayRadar) = hidden; }

// --- pools (entity.query) -------------------------------------------------------------------

struct Pool {
    void* objects;         // CPool::m_pObjects
    std::uint8_t* bytemap; // CPool::m_byteMap (nId:7 | bEmpty:1)
    int size;              // CPool::m_nSize
};

// Read a CPool<...>* global into a flat view; returns false when the pool is not created yet.
inline bool pool_view(std::uint32_t ptr_addr, Pool& out) {
    auto* pool = *reinterpret_cast<std::uint8_t**>(ptr_addr);
    if (!pool) return false;
    out.objects = *reinterpret_cast<void**>(pool + 0x0);
    out.bytemap = *reinterpret_cast<std::uint8_t**>(pool + 0x4);
    out.size = *reinterpret_cast<int*>(pool + 0x8);
    return out.objects && out.bytemap && out.size > 0;
}

inline bool slot_used(const Pool& p, int i) { return (p.bytemap[i] & 0x80u) == 0; }  // bEmpty is bit 7

inline void* pool_object(const Pool& p, int i, std::uint32_t stride) {
    return reinterpret_cast<std::uint8_t*>(p.objects) + (std::size_t)i * stride;
}

// GTA handle of slot i: (i << 8) | byteMap[i]  (byteMap value with bEmpty cleared for live slots).
inline int pool_handle(const Pool& p, int i) { return (i << 8) | (p.bytemap[i] & 0xFF); }

}  // namespace engine
}  // namespace sp

#endif  // SP_ENGINE_HPP
