// Engine addresses and layouts used by plugin/vats (FalloutNV.exe 1.4.0.525, reference Steam image = running process, docs/engine-addresses.md section 0).
// Every entry says how it was established. The derivation is in docs/re/vats-realtime.md (static disassembly of <reference exe>, read-only;
// nothing here has ever run in a game). plugin/vats/tools/verify_pins.py re-checks every prologue array, call-site table, the vtable slot and the game-setting
// objects below against the image (it parses this file, so keep the `constexpr uint32_t kName = 0x...;` / `{"name", 0x...}` forms).
// Game-free: plain constants, so the pins are unit-tested against the bytes recorded here.
//
// Confidence words: PROVEN = read in the disassembly and consistent with xNVSE; LIKELY = read, meaning inferred; TODO-RE = not established.
#pragma once
#include <cstddef>
#include <cstdint>

namespace nvcoop::vats::addr {

// ---- data -----------------------------------------------------------------------------------------------------------------------------
// VATSCameraData (xNVSE GameAPI.h): +0 tList* of QUEUED actions (null when empty; EndVATScam pops until null), +8 mode, +0x10 playback object* (PROVEN).
constexpr uint32_t kVatsCamera = 0x011F2250;
constexpr uint32_t kCamTargets = 0x00, kCamMode = 0x08, kCamPlayback = 0x10;
// VATSMenu* (RTTI 0x119F3E8, vtable 0x10700D4). PROVEN: the pointer, +0xF8 (byte), +0xFB (byte), +0x10D / +0x10F / +0x110 (bytes: input flags),
// +0x114 (tick), +0x118 (byte) with +0x11C. +0x100 is the HIGHLIGHTED PART RECORD (LIKELY: only the target switch code and the limb navigation
// 0x7F3070 write it, always with an element of the target record's part list).
constexpr uint32_t kVatsMenuPtr = 0x011DB0D4;
constexpr uint32_t kMenuHighlightPart = 0x100;
// The currently selected VATS target Actor* (PROVEN: 0x7F3C90 / 0x7F3D60 key the target list by it, 0x7F3BF0 asks it for its body-part data, the
// target-time multiplier hook compares the updating actor with it). 0 when nothing is selected.
constexpr uint32_t kSelectedTarget = 0x011F21CC;
// The target-record list: a tList head NODE {record*, next} (PROVEN: walked by 0x7F3C90 with the engine's node accessors 0x6815c0 / 0x726070).
// record: +0 Actor* (PROVEN), +4 dword kind (LIKELY: 1 / 2 are compared; not a count), +8 embedded tList head node of PART records (PROVEN),
// +0x10 byte flag. part: +0x20 body-part index (PROVEN: 0x7F3D60 passes it to 0x7F3BF0), +0x30 Tile* (PROVEN: trait 0xFA9 is set on it).
constexpr uint32_t kTargetListHead = 0x011DB150;
constexpr uint32_t kRecActor = 0x00, kRecKind = 0x04, kRecParts = 0x08;
constexpr uint32_t kPartBodyIndex = 0x20, kPartTile = 0x30;
// The remembered limb: the limb AV code of the last highlighted part, re-selected when the target changes (0x7F3D60(menu, [this])); -1 = none.
// PROVEN as storage (read by 0x7F3D60 callers, written by 0x7F4366 / 0x7F4682 / 0x7EF665 / 0x7EAE61); LIKELY as "the highlight".
constexpr uint32_t kRememberedLimb = 0x011A59F0;
// TimeGlobal (xNVSE g_timeGlobal) and the global time multiplier floats (xNVSE g_globalTimeMult = 0x11AC3A0). +0xC of the TimeGlobal is secondsPassed.
constexpr uint32_t kTimeGlobal = 0x011F6394;
constexpr uint32_t kTimeScaleCurrent = 0x011AC3A0, kTimeScaleTarget = 0x011AC3A4;
// The "game is paused by a menu" byte of the main loop (PROVEN: written at 0x86E6E1 / 0x86E7C9 from MenuMode() || mgr+0x4BC==2, read about 25 times by
// the main loop 0x86E650..0x86FD90 and by the player update at 0x9443CA). PlayerCharacter** (xNVSE g_thePlayer): 0x11DEA3C.
constexpr uint32_t kPausedByMenu = 0x011DEA2B;
constexpr uint32_t kPlayerPtr = 0x011DEA3C;

// ---- game settings (static GameSetting objects: vtable at +0, the value at +4; PROVEN, defaults read from the static initialisers) --------------
struct GameSetting { const char* name; uint32_t object; bool isFloat; float defaultValue; };
inline constexpr GameSetting kSettingPlayerTimeMult = {"fVATSPlayerTimeUpdateMult", 0x011CF374, true, 3.0f};    // the player runs at 3x during playback
inline constexpr GameSetting kSettingTargetTimeMult = {"fVATSTargetTimeUpdateMult", 0x011CFCEC, true, 1.0f};
inline constexpr GameSetting kSettingCamMinTime = {"fVATSCameraMinTime", 0x011CE368, true, 0.0f};                // default read at runtime; 0 here = "not recorded"
inline constexpr GameSetting kSettingCamMaxTime = {"fVATSCameraMaxTime", 0x011CE8C4, true, 0.0f};
inline constexpr GameSetting kSettingPlaybackDelay = {"fVATSPlaybackDelay", 0x011CE338, true, 0.0f};
inline constexpr GameSetting kSettingStrangerOdds = {"fVATSStrangerOdds", 0x011CE738, true, 0.0f};              // Mysterious Stranger
inline constexpr GameSetting kSettingStrangerDistance = {"fVATSStrangerDistance", 0x011CE888, true, 0.0f};
inline constexpr GameSetting kSettingStrangerMaxHp = {"iVATSStrangerMaxHP", 0x011CE7E4, false, 0.0f};
inline constexpr GameSetting kSettingFortuneOdds = {"fVATSFortuneOdds", 0x011CE32C, true, 0.0f};                // Four Leaf Clover (LIKELY)
inline constexpr GameSetting kSettingConcentratedFire = {"iVATSConcentratedFireBonus", 0x011D4210, false, 0.0f};
inline constexpr GameSetting kSettingMaxChance = {"fVATSMaxChance", 0x011CE10C, true, 0.0f};
inline constexpr GameSetting kSettingCritBonus = {"fVATSCriticalChanceBonus", 0x011CE46C, true, 0.0f};
inline constexpr uint32_t kSettingValueOffset = 4;

// ---- functions -------------------------------------------------------------------------------------------------------------------------
// TimeGlobal::SetTimeScale(this = 0x11F6394, float scale, uint32 immediate), thiscall, `ret 8`. PROVEN by its 16 call sites (xNVSE's g_globalTimeMult
// is the float it writes). It stores the target multiplier 0x11AC3A4 and ramps 0x11AC3A0 towards it (immediate != 0: at once). The VATS camera code
// calls it with 1e-4 (freeze) on entry, 0.3 (kill shot), a per-action factor, and 1.0 on exit.
constexpr uint32_t kSetTimeScale = 0x00AA4DB0;
inline constexpr uint8_t kSetTimeScalePrologue[16] = {0x55, 0x8B, 0xEC, 0x83, 0xEC, 0x08, 0x89, 0x4D, 0xF8, 0x8B, 0x45, 0xF8, 0x8A, 0x48, 0x1C, 0x88};
// VATSCameraData::GetPlayerTimeMult(this = 0x11F2250), thiscall, returns DOUBLE in ST0. PROVEN: (target / current global scale) * m, m = 1 outside
// playback, in playback the playback object's factor (+0x48 of the object, when > 0) or fVATSPlayerTimeUpdateMult. Used to speed the PLAYER up while
// the world is slowed. Five call sites. GetTargetTimeMult is the same for the selected target (fVATSTargetTimeUpdateMult), nine call sites.
constexpr uint32_t kGetPlayerTimeMult = 0x009C8CC0, kGetTargetTimeMult = 0x009C8D60;
inline constexpr uint8_t kGetTimeMultPrologue[16] = {0x55, 0x8B, 0xEC, 0x83, 0xEC, 0x10, 0x89, 0x4D, 0xF8, 0xD9, 0xE8, 0xD9, 0x5D, 0xFC, 0x8B, 0x45};
// MenuMode() (xNVSE-documented in docs/re/dialogmenu-menus-vats-inventory.md), cdecl bool. The main loop 0x86E650 calls it at 0x86E6B6 and 0x86E79E
// to fill kPausedByMenu (PROVEN).
constexpr uint32_t kMenuMode = 0x00702360;
inline constexpr uint8_t kMenuModePrologue[16] = {0x55, 0x8B, 0xEC, 0xE8, 0xA8, 0x4E, 0xDB, 0xFF, 0x85, 0xC0, 0x74, 0x21, 0xE8, 0x9F, 0x4E, 0xDB};
// Worker-thread wait of the menu update. While any menu is open the main loop (0x871C6B: `if (MenuMode()) 0x7058E0()`) runs the menu update 0x713C70, which, when
// the worker threads are started (`bool 0x713D90`, = byte 0x11DFA19), waits with INFINITE timeout for a worker event (0x8C7A70, WaitForSingleObject on handle
// group 0 slot 1). That handshake belongs to a PAUSED world. With the pause removed (real-time VATS) the event is never signalled in the VATS zoom (camera mode 3):
// MEASURED 2026-10-27 on the second PC, a minidump of the hung main thread: the stack is 0x871C7C -> 0x70590B -> 0x713CA9 -> 0x8C7A93 -> NtWaitForSingleObject,
// every worker idle, CPU 0, window still answering. The one call at 0x713C8D asks 0x713D90; the thunk answers "no workers" while the VATS menu is open and the
// world is not paused, so the menu update skips the wait. The world-frame readers of the byte (0x825218, 0x8C7AC1) are left alone: they still join the workers.
constexpr uint32_t kThreadsActive = 0x00713D90;
inline constexpr uint8_t kThreadsActivePrologue[16] = {0x55, 0x8B, 0xEC, 0xA0, 0x19, 0xFA, 0x1D, 0x01, 0x5D, 0xC3, 0xCC, 0xCC, 0xCC, 0xCC, 0xCC, 0xCC};
// MenuMode(id), cdecl bool(int id): is the menu with this id the top menu. 0x420 = VATS (PROVEN: the VATS menu update asks exactly this).
constexpr uint32_t kMenuModeOf = 0x00702450;
inline constexpr uint8_t kMenuModeOfPrologue[16] = {0x55, 0x8B, 0xEC, 0x51, 0xE8, 0xB7, 0x4D, 0xDB, 0xFF, 0x85, 0xC0, 0x74, 0x74, 0xE8, 0xAE, 0x4D};
constexpr int kVatsMenuId = 0x420;
// VATSCameraData::Accept: stdcall(void), what xNVSE's EndVATScam calls to finish the selection and start the playback (PROVEN by xNVSE).
constexpr uint32_t kAccept = 0x00705780;
inline constexpr uint8_t kAcceptPrologue[16] = {0x55, 0x8B, 0xEC, 0x83, 0xEC, 0x0C, 0xE8, 0x85, 0x1A, 0xDB, 0xFF, 0x85, 0xC0, 0x74, 0x65, 0xE8};
// VATSCameraData::Unclick(this, uint32* actionType_out, uint8* byte_out): thiscall, pops the last queued action (PROVEN by xNVSE).
constexpr uint32_t kUnclick = 0x009C88A0;
inline constexpr uint8_t kUnclickPrologue[16] = {0x55, 0x8B, 0xEC, 0x83, 0xEC, 0x18, 0x89, 0x4D, 0xEC, 0x8B, 0x45, 0xEC, 0x89, 0x45, 0xFC, 0x83};
// VATSMenu::LimbOfPart(menu, int bodyPartIndex) -> signed byte limb AV code (25..31), 0x1A (torso) when the selected target is not an actor, -1 for
// part 14 (none). thiscall, ret 4. PROVEN. TODO-RE: the BGSBodyPartData lookup inside (0x5E50F0 / 0x5E5190) was read, not named.
constexpr uint32_t kLimbOfPart = 0x007F3BF0;
inline constexpr uint8_t kLimbOfPartPrologue[16] = {0x55, 0x8B, 0xEC, 0x83, 0xEC, 0x10, 0x89, 0x4D, 0xF4, 0x83, 0x3D, 0xCC, 0x21, 0x1F, 0x01, 0x00};
// VATSMenu vtable 0x10700D4. Slot 3 (+0xC) = HandleClick(int tileId, Tile*), thiscall, ret 8 (PROVEN: the menu's own update calls it as slot 3 with
// (3, 0) for "right" and (2, 0) for "left"). tile ids: 2 = previous target, 3 = next target, 4 = accept, 0x1E / 0x1F set the two limb-key flags.
constexpr uint32_t kMenuVtable = 0x010700D4;
constexpr uint32_t kMenuVtblHandleClick = 3;
constexpr uint32_t kMenuHandleClickFn = 0x007EECB0;
inline constexpr uint8_t kMenuHandleClickPrologue[16] = {0x55, 0x8B, 0xEC, 0x6A, 0xFF, 0x68, 0x18, 0xF7, 0xF0, 0x00, 0x64, 0xA1, 0x00, 0x00, 0x00, 0x00};
constexpr int kTilePrevTarget = 2, kTileNextTarget = 3, kTileAccept = 4;
// VATSCameraData::Mode accessor (xNVSE Hooks_Other / the VATS menu use it): thiscall(this) -> [this+8]; not called by us, only pinned.
constexpr uint32_t kGetMode = 0x0044DDC0;
inline constexpr uint8_t kGetModePrologue[16] = {0x55, 0x8B, 0xEC, 0x51, 0x89, 0x4D, 0xFC, 0x8B, 0x45, 0xFC, 0x8B, 0x40, 0x08, 0x8B, 0xE5, 0x5D};
// The engine's hit chance (not called; PROVEN to be the function the VATS menu uses): cdecl, 13+ stack args, one caller (0x7F158E in the VATS menu).
constexpr uint32_t kHitChance = 0x00646F70;   // TODO-RE: arguments. The VATS UI shows the engine's own number for the LOCAL player (skills, perks, range).

// ---- call sites (E8 rel32) -------------------------------------------------------------------------------------------------------------
struct Site { const char* name; uint32_t site; };

// VATS code that asks for a world time scale (all PROVEN to call kSetTimeScale): the VATS camera cluster 0x9C6C30..0x9C8D60 and the exit at 0x850CEE.
inline constexpr Site kTimeScaleSites[] = {
    {"vats_exit_850CEE", 0x00850CEE}, {"cam_9C6F3A", 0x009C6F3A},  {"cam_freeze_9C7E7D", 0x009C7E7D}, {"cam_killshot_9C7EA9", 0x009C7EA9},
    {"cam_action_9C7EC6", 0x009C7EC6}, {"cam_9C7F27", 0x009C7F27}, {"cam_9C83A0", 0x009C83A0},        {"cam_freeze_9C852D", 0x009C852D},
    {"cam_action_9C8565", 0x009C8565}, {"cam_9C8579", 0x009C8579}, {"cam_9C8A82", 0x009C8A82},
};
inline constexpr size_t kTimeScaleSiteCount = sizeof kTimeScaleSites / sizeof kTimeScaleSites[0];
// Callers of the player / target time multipliers (PROVEN: complete E8 scans of the image).
inline constexpr Site kPlayerMultSites[] = {
    {"main_86FA09", 0x0086FA09}, {"actor_929E9D", 0x00929E9D}, {"vats_9483E9", 0x009483E9}, {"vats_950957", 0x00950957}, {"hit_9BED22", 0x009BED22},
};
inline constexpr size_t kPlayerMultSiteCount = sizeof kPlayerMultSites / sizeof kPlayerMultSites[0];
inline constexpr Site kTargetMultSites[] = {
    {"t_567270", 0x00567270}, {"t_8863E5", 0x008863E5}, {"t_886D28", 0x00886D28}, {"t_887BD2", 0x00887BD2}, {"t_88842F", 0x0088842F},
    {"t_88873D", 0x0088873D}, {"t_8B32E2", 0x008B32E2}, {"actor_929EEE", 0x00929EEE}, {"hit_9BED44", 0x009BED44},
};
inline constexpr size_t kTargetMultSiteCount = sizeof kTargetMultSites / sizeof kTargetMultSites[0];
// MenuMode() calls in the main loop that FILL kPausedByMenu (tier 1 = the pause of the whole world), and further MenuMode() calls in the player /
// actor update (tier 2: opt-in `pausesites=2`, LIKELY gates of world simulation, unverified).
inline constexpr Site kPauseSitesMain[] = {{"mainloop_86E6B6", 0x0086E6B6}, {"mainloop_86E79E", 0x0086E79E}};
inline constexpr size_t kPauseSiteMainCount = sizeof kPauseSitesMain / sizeof kPauseSitesMain[0];
inline constexpr Site kPauseSitesExtended[] = {
    {"mainloop_86EEAF", 0x0086EEAF}, {"player_94042D", 0x0094042D}, {"player_940CEB", 0x00940CEB}, {"player_942C7F", 0x00942C7F}, {"player_942E77", 0x00942E77},
    {"proc_89C6CA", 0x0089C6CA},     {"proc_89C74A", 0x0089C74A},   {"proc_89CEBA", 0x0089CEBA},   {"proc_89CF56", 0x0089CF56},
};
inline constexpr size_t kPauseSiteExtendedCount = sizeof kPauseSitesExtended / sizeof kPauseSitesExtended[0];
inline constexpr Site kThreadWaitSites[] = {{"menu_update_713C8D", 0x00713C8D}};
inline constexpr size_t kThreadWaitSiteCount = sizeof kThreadWaitSites / sizeof kThreadWaitSites[0];

// The five bytes a call site must hold: E8 + rel32 reaching `target` (computed, so the table above cannot drift from the target constants).
constexpr uint32_t relTo(uint32_t site, uint32_t target) { return target - (site + 5u); }

}  // namespace nvcoop::vats::addr
