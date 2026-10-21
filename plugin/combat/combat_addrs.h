// Engine addresses and layouts used by plugin/combat (FalloutNV.exe 1.4.0.525, reference Steam image = running process, see docs/engine-addresses.md).
// Every entry says how it was established. The derivation is in docs/re/hit-path.md (static disassembly of <reference exe>, read-only).
// Game-free: plain constants, so the pins are unit-tested against the bytes recorded here.
#pragma once
#include <cstdint>
#include "combat/call_patch.h"

namespace nvcoop::combat::addr {

// ---- the engine's hit funnel -------------------------------------------------------------------------------------------------------
// ApplyHitData: thiscall(Actor* victim, ActorHitData* hit, bool flag), `ret 8`. Confidence M: the arguments and the field uses below are read from
// the disassembly; the NAME is ours. It copies the hit into the victim's process (last-hit data), raises the script events OnHit (0x80) and OnHitWith
// (0x100) through MarkBaseExtraListScriptEvent with the attacker and the weapon, applies the health / fatigue / limb damage and runs the death logic.
constexpr uint32_t kApplyHit = 0x0089A760;
// Aligned-frame prologue: push ebx / mov ebx,esp / sub esp,8 / and esp,-16 / add esp,4 / push ebp / mov ebp,[ebx+4]. Not `55 8B EC`, so the
// entry-detour registry cannot hook it; the six `call` sites below are redirected instead (call_patch.h).
inline constexpr uint8_t kApplyHitPrologue[16] = {0x53, 0x8B, 0xDC, 0x83, 0xEC, 0x08, 0x83, 0xE4, 0xF0, 0x83, 0xC4, 0x04, 0x55, 0x8B, 0x6B, 0x04};
// Every caller: a scan of the whole image finds six `E8 rel32` calls to 0x89A760, no `E9` tail jump and no 4-byte absolute reference to the address
// (so no vtable or function-pointer use). Each pushes `flag` then the hit pointer and has the victim in ecx. 0x89A738 lives inside 0x899CB0 (the
// builder that allocates the 0x64-byte hit data and fills it); the others are in the task, projectile and melee paths (not named).
inline constexpr CallSite kApplyHitCallers[] = {
    {"hit_task_87C4DA", 0x0087C4DA},
    {"hit_builder_89A738", 0x0089A738},
    {"hit_8B91E1", 0x008B91E1},
    {"hit_9B0503", 0x009B0503},
    {"hit_9C1E96", 0x009C1E96},
    {"hit_9CBDE8", 0x009CBDE8},
};
inline constexpr size_t kApplyHitCallerCount = sizeof kApplyHitCallers / sizeof kApplyHitCallers[0];
// The five bytes each site must hold (E8 + rel32 to kApplyHit): recorded from the image, checked again by the unit test.
inline constexpr uint8_t kSiteBytes[kApplyHitCallerCount][5] = {
    {0xE8, 0x81, 0xE2, 0x01, 0x00},
    {0xE8, 0x23, 0x00, 0x00, 0x00},
    {0xE8, 0x7A, 0x15, 0xFE, 0xFF},
    {0xE8, 0x58, 0xA2, 0xEE, 0xFF},
    {0xE8, 0xC5, 0x88, 0xED, 0xFF},
    {0xE8, 0x73, 0xE9, 0xEC, 0xFF},
};

// ---- ActorHitData (xNVSE GameProcess.h, sizeof 0x64; the exe allocates exactly 0x64 bytes at 0x899E62) --------------------------------
// Offsets re-verified in the disassembly of ApplyHitData: +0 source (the OnHit "object"), +0x10 hitLocation (14 = none), +0x14 healthDmg (loaded
// into a local and used as THE health damage), +0x18 wpnBaseDmg, +0x1C fatigueDmg, +0x20 limbDmg, +0x24 blockDTMod, +0x30 weapon, +0x58 flags
// (the engine tests 0x4 crit, 0x8 and 0x400 through 0x58CBA0). +8 (projectile OR explosion) and +0x38 impactPos are from the xNVSE header.
namespace hit {
constexpr uint32_t kSize = 0x64;
constexpr uint32_t kSource = 0x00, kTarget = 0x04, kProjOrExplosion = 0x08, kHitLocation = 0x10, kHealthDmg = 0x14, kWpnBaseDmg = 0x18, kFatigueDmg = 0x1C,
                   kLimbDmg = 0x20, kBlockDTMod = 0x24, kArmorDmg = 0x28, kWeapon = 0x30, kImpactPos = 0x38, kFlags = 0x58, kDmgMult = 0x5C;
// flag bits (xNVSE kHitData_*): crit 0x4, fatal 0x10, dismember 0x20, explode 0x40, cripple 0x80, sneak-attack crit 0x400
constexpr uint32_t kFlagCrit = 0x4, kFlagFatal = 0x10, kFlagDismember = 0x20, kFlagExplode = 0x40, kFlagCripple = 0x80;
constexpr uint32_t kLethalFlags = kFlagFatal | kFlagDismember | kFlagExplode | kFlagCripple;
}  // namespace hit

// ---- TESForm / TESObjectREFR (xNVSE, used already by plugin/hooks) -------------------------------------------------------------------
namespace form {
constexpr uint32_t kTypeId = 0x04, kRefId = 0x0C;          // TESForm
constexpr uint32_t kBaseForm = 0x20, kPosX = 0x30, kParentCell = 0x40;   // TESObjectREFR
constexpr uint8_t kTypeMissile = 0x3D, kTypeGrenade = 0x3E, kTypeBeam = 0x3F, kTypeFlame = 0x40, kTypeContinuousBeam = 0x69;   // projectile refs
}  // namespace form

// ---- misc ------------------------------------------------------------------------------------------------------------------------------
// Actor::lifeState (+0x108: 0 alive, 1 dying, 2 dead) is the actor port's constant (plugin/actors/engine/engine_iface.h ActorRaw); read it there.
// VATSCameraData 0x11F2250, +8 = mode (0 none, 1 target select, 2 limb select, 3 ?, 4 playback): docs/re/dialogmenu-menus-vats-inventory.md ("proven").
constexpr uint32_t kVatsCameraData = 0x011F2250, kVatsModeOffset = 8;

// Kill XP: inside ApplyHitData (0x89E5xx..0x89E6FE) the engine calls a virtual of the VICTIM at vtable offset 0x730 (no arguments, float in st0), scales it
// (GMST iXPLevelKill* tiers, the player's XP multiplier) and passes the integer to Player vtable +0x488 (RewardXP, also what Cmd_RewardXP uses). Confidence M.
inline constexpr uint32_t kActorKillXpVtblOffset = 0x730;
inline constexpr uint32_t kPlayerRewardXpVtblOffset = 0x488;

// TODO-RE (not found, with a working fallback in place):
//   * the projectile-create / weapon-fire function (a spawn feed for ProjectileSpawn): no hook, onProjectileFired() is the entry once found
//   * the blast radius of an Explosion object (BGSExplosion data): radius stays 0 = unknown, hits are sent as plain hits
//   * the tier / perk scaling of the kill XP (the raw value is used), the victim's faction for crime reports
//   * a direct "damage with attacker" call (the owner applies accepted damage with the console's DamageActorValue instead)
}  // namespace nvcoop::combat::addr
