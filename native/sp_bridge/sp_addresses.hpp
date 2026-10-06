// sp_addresses.hpp - generated from satk.spbridge.addresses by `satk sp addresses --emit-header`.
// 1.0 US (HOODLUM) engine addresses for satk_sp.asi. Do not edit by hand; edit the Python table.
// Public engine knowledge (gta-reversed, plugin-sdk); MIT. No game bytes here, only numbers.
#ifndef SATK_SP_ADDRESSES_HPP
#define SATK_SP_ADDRESSES_HPP
#include <cstdint>
namespace sp { namespace addr {
  static const char* const kGameVersion = "1.0us";
  // --- functions ---
  static const std::uint32_t CGame_Process = 0x53BEE0; // CGame::Process
  static const std::uint32_t CCamera_TakeControlNoEntity = 0x50C8B0; // CCamera::TakeControlNoEntity
  static const std::uint32_t CCamera_SetCamPositionForFixedMode = 0x50BEC0; // CCamera::SetCamPositionForFixedMode
  static const std::uint32_t CCamera_Restore = 0x50B930; // CCamera::Restore
  static const std::uint32_t CCamera_RestoreWithJumpCut = 0x50BAB0; // CCamera::RestoreWithJumpCut
  static const std::uint32_t CClock_SetGameClock = 0x52D150; // CClock::SetGameClock
  static const std::uint32_t CWeather_ForceWeatherNow = 0x72A4F0; // CWeather::ForceWeatherNow
  static const std::uint32_t CWeather_ReleaseWeather = 0x72A510; // CWeather::ReleaseWeather
  static const std::uint32_t FindPlayerPed = 0x56E210; // FindPlayerPed
  static const std::uint32_t FindPlayerVehicle = 0x56E0D0; // FindPlayerVehicle
  static const std::uint32_t CWorld_Add = 0x563220; // CWorld::Add
  static const std::uint32_t CWorld_Remove = 0x563280; // CWorld::Remove
  static const std::uint32_t CWorld_RemoveReferencesToDeletedObject = 0x565510; // CWorld::RemoveReferencesToDeletedObject
  static const std::uint32_t CWorld_ProcessLineOfSight = 0x56BA00; // CWorld::ProcessLineOfSight
  static const std::uint32_t CWorld_FindGroundZForCoord = 0x569660; // CWorld::FindGroundZForCoord
  static const std::uint32_t CStreaming_RequestModel = 0x4087E0; // CStreaming::RequestModel
  static const std::uint32_t CStreaming_LoadAllRequestedModels = 0x40EA10; // CStreaming::LoadAllRequestedModels
  static const std::uint32_t CStreaming_LoadScene = 0x40EB70; // CStreaming::LoadScene
  static const std::uint32_t CStreaming_SetModelIsDeletable = 0x409C10; // CStreaming::SetModelIsDeletable
  static const std::uint32_t CStreaming_SetMissionDoesntRequireModel = 0x409C90; // CStreaming::SetMissionDoesntRequireModel
  static const std::uint32_t CCarCtrl_CreateCarForScript = 0x431F80; // CCarCtrl::CreateCarForScript
  static const std::uint32_t CPopulation_AddPed = 0x612710; // CPopulation::AddPed
  static const std::uint32_t CObject_Create = 0x5A1F60; // CObject::Create
  static const std::uint32_t CTheScripts_ClearSpaceForMissionEntity = 0x486B00; // CTheScripts::ClearSpaceForMissionEntity
  static const std::uint32_t CModelInfo_GetModelInfo = 0x4C5940; // CModelInfo::GetModelInfo
  static const std::uint32_t RwD3D9GetCurrentD3DDevice = 0x7F9D50; // RwD3D9GetCurrentD3DDevice
  // --- globals ---
  static const std::uint32_t TheCamera = 0xB6F028; // TheCamera
  static const std::uint32_t CDraw_ms_fFOV = 0x8D5038; // CDraw::ms_fFOV
  static const std::uint32_t CDraw_ms_fAspectRatio = 0xC3EFA4; // CDraw::ms_fAspectRatio
  static const std::uint32_t Scene = 0xC17038; // Scene
  static const std::uint32_t RwD3DDevice = 0xC97C28; // RwD3DDevice
  static const std::uint32_t CTimer_m_FrameCounter = 0xB7CB4C; // CTimer::m_FrameCounter
  static const std::uint32_t CTimer_m_snTimeInMilliseconds = 0xB7CB84; // CTimer::m_snTimeInMilliseconds
  static const std::uint32_t CTimer_ms_fTimeStep = 0xB7CB5C; // CTimer::ms_fTimeStep
  static const std::uint32_t CTimer_m_UserPause = 0xB7CB49; // CTimer::m_UserPause
  static const std::uint32_t CTimer_m_CodePause = 0xB7CB48; // CTimer::m_CodePause
  static const std::uint32_t CClock_ms_nGameClockHours = 0xB70153; // CClock::ms_nGameClockHours
  static const std::uint32_t CClock_ms_nGameClockMinutes = 0xB70152; // CClock::ms_nGameClockMinutes
  static const std::uint32_t CWeather_ForcedWeatherType = 0xC81318; // CWeather::ForcedWeatherType
  static const std::uint32_t CWeather_OldWeatherType = 0xC81320; // CWeather::OldWeatherType
  static const std::uint32_t CWeather_NewWeatherType = 0xC8131C; // CWeather::NewWeatherType
  static const std::uint32_t CWeather_InterpolationValue = 0xC8130C; // CWeather::InterpolationValue
  static const std::uint32_t CTheScripts_bDisplayHud = 0xA444A0; // CTheScripts::bDisplayHud
  static const std::uint32_t CHud_bScriptDontDisplayRadar = 0xBAA3FB; // CHud::bScriptDontDisplayRadar
  static const std::uint32_t CGame_currArea = 0xB72914; // CGame::currArea
  static const std::uint32_t CModelInfo_ms_modelInfoPtrs = 0xA9B0C8; // CModelInfo::ms_modelInfoPtrs
  // --- hook sites (call rel32 -> target) ---
  static const std::uint32_t gameProcessEvent = 0x53E981; // call CGame::Process (in Idle)
  static const std::uint32_t gameProcessEvent_target = 0x53BEE0;
  // --- pools: {ptr-to-CPool*, element stride} ---
  static const std::uint32_t ms_pPedPool_ptr = 0xB74490; static const std::uint32_t ms_pPedPool_stride = 0x7C4; // ped
  static const std::uint32_t ms_pVehiclePool_ptr = 0xB74494; static const std::uint32_t ms_pVehiclePool_stride = 0xA18; // vehicle
  static const std::uint32_t ms_pBuildingPool_ptr = 0xB74498; static const std::uint32_t ms_pBuildingPool_stride = 0x38; // building
  static const std::uint32_t ms_pObjectPool_ptr = 0xB7449C; static const std::uint32_t ms_pObjectPool_stride = 0x19C; // object
  static const std::uint32_t ms_pDummyPool_ptr = 0xB744A0; static const std::uint32_t ms_pDummyPool_stride = 0x38; // dummy
  // --- struct offsets ---
  static const std::uint32_t off_CPlaceable_m_placement = 0x4;
  static const std::uint32_t off_CSimpleTransform_pos = 0x0;
  static const std::uint32_t off_CPlaceable_m_matrix = 0x14;
  static const std::uint32_t off_CMatrix_pos = 0x30;
  static const std::uint32_t off_CEntity_m_pRwObject = 0x18;
  static const std::uint32_t off_CEntity_m_nModelIndex = 0x22;
  static const std::uint32_t off_CEntity_m_nType_byte = 0x36;
  static const std::uint32_t off_CEntity_m_nAreaCode = 0x2F;
  static const std::uint32_t off_CBaseModelInfo_m_nTxdIndex = 0xA;
  static const std::uint32_t off_CBaseModelInfo_m_nFlags = 0x12;
  static const std::uint32_t off_CBaseModelInfo_m_fDrawDistance = 0x18;
  static const std::uint32_t off_CPed_m_nPedType = 0x598;
  static const std::uint32_t off_CVehicle_m_nCreatedBy = 0x4A4;
  static const std::uint32_t off_CObject_m_nObjectType = 0x13C;
  static const std::uint32_t off_CCamera_m_aCams = 0x174;
  static const std::uint32_t off_CCamera_m_nActiveCam = 0x59;
  static const std::uint32_t off_CCam_size = 0x238;
  static const std::uint32_t off_CCam_m_vecSource = 0x19C;
  static const std::uint32_t off_CCam_m_vecFront = 0x190;
  static const std::uint32_t off_CCam_m_vecUp = 0x1B4;
  static const std::uint32_t off_CCam_m_fFOV = 0xB4;
  static const std::uint32_t off_CCam_m_vecCamFixedModeSource = 0x160;
  static const std::uint32_t off_CCam_m_vecCamFixedModeUpOffSet = 0x16C;
  // --- streaming flags ---
  static const std::uint32_t streaming_game_required = 0x02;
  static const std::uint32_t streaming_mission_required = 0x04;
  static const std::uint32_t streaming_keep_in_memory = 0x08;
  static const std::uint32_t streaming_priority_request = 0x10;
} } // namespace sp::addr
#endif // SATK_SP_ADDRESSES_HPP
