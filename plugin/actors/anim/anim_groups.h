// Animation group ids and names for NPC / creature driving (enemy-anim). Ids follow the xNVSE enum TESAnimGroup::kAnimGroup_*, the same
// table tools/animcheck/animgroups_table.py is generated from; gen_group_names.py --check proves the names and the constants below agree.
// That the running game uses the same numbering is NOT measured yet. The name is what the script command `PlayGroup <name> <flag>` takes;
// the engine resolves the weapon-type variant itself from the actor's equipped weapon (TODO-RE: unmeasured for PlayGroup by name).
#pragma once
#include <cstddef>
#include <cstdint>
#include <cstring>

namespace nvcoop::actors::anim {

enum GroupId : int {
    kGroupIdle = 0,
    kGroupDynamicIdle = 1,
    kGroupSpecialIdle = 2,
    kGroupForward = 3,        // 3..6 walk forward, backward, left, right
    kGroupFastRight = 10,     // 7..10 run forward, backward, left, right
    kGroupDodgeForward = 11,  // 11..14 dodge forward, back, left, right
    kGroupDodgeRight = 14,
    kGroupTurnLeft = 15,
    kGroupTurnRight = 16,
    kGroupAim = 17,           // 17..22 Aim, AimUp, AimDown, AimIS, AimISUp, AimISDown
    kGroupAimISDown = 22,
    kGroupHolster = 23,
    kGroupEquip = 24,         // draw
    kGroupUnequip = 25,       // holster
    kGroupAttackFirst = 26,   // 26..167 every attack / mine placing / throw variant
    kGroupAttackLast = 167,
    kGroupCounter = 168,
    kGroupStomp = 169,
    kGroupBlockIdle = 170,
    kGroupBlockHit = 171,
    kGroupRecoil = 172,
    kGroupReloadFirst = 173,  // 173..199 reload start / reload A..Z
    kGroupReloadLast = 199,
    kGroupJamFirst = 200,     // 200..222 weapon jam
    kGroupJamLast = 222,
    kGroupStagger = 223,
    kGroupDeath = 224,
    kGroupTalking = 225,
    kGroupJumpStart = 227,
    kGroupJumpLoop = 228,
    kGroupJumpLand = 229,
    kGroupJumpLoopForward = 236,
    kGroupJumpLandForward = 241,
    kGroupLast = 244,
};
constexpr int kGroupCount = 245;

// "" for an id outside the table. Never guess: the script backend refuses a group it cannot name.
inline const char* groupName(int id) {
    static const char* const kNames[] = {
#include "anim_group_names.inc"
    };
    static_assert(sizeof(kNames) / sizeof(kNames[0]) == kGroupCount, "anim_group_names.inc must hold exactly kGroupCount names");
    if (id < 0 || id >= kGroupCount) return "";
    return kNames[id];
}
inline bool validGroup(int id) { return id > 0 && id <= kGroupLast; }

inline bool isLocomotionGroup(int g) { return g >= kGroupForward && g <= kGroupFastRight; }
inline bool isDodgeGroup(int g) { return g >= kGroupDodgeForward && g <= kGroupDodgeRight; }
inline bool isTurnGroup(int g) { return g == kGroupTurnLeft || g == kGroupTurnRight; }
inline bool isAimGroup(int g) { return g >= kGroupAim && g <= kGroupAimISDown; }
inline bool isAttackGroup(int g) { return (g >= kGroupAttackFirst && g <= kGroupAttackLast) || g == kGroupCounter || g == kGroupStomp; }
inline bool isReloadGroup(int g) { return g >= kGroupReloadFirst && g <= kGroupReloadLast; }
inline bool isJamGroup(int g) { return g >= kGroupJamFirst && g <= kGroupJamLast; }
inline bool isReactionGroup(int g) { return g == kGroupStagger || g == kGroupRecoil || g == kGroupBlockHit; }
inline bool isJumpGroup(int g) { return g == kGroupJumpStart || g == kGroupJumpLoop || g == kGroupJumpLand || (g >= kGroupJumpLoopForward && g <= kGroupJumpLoopForward + 3) || (g >= kGroupJumpLandForward && g <= kGroupLast); }

// A group the owner's sampler reports as a one-shot "action" (and a non-owner replays once). Locomotion, aim, idle, jump, death and
// the draw / holster transitions are state (carried by other fields), not actions.
inline bool isActionGroup(int g) {
    return isAttackGroup(g) || isReloadGroup(g) || isJamGroup(g) || isReactionGroup(g) || isDodgeGroup(g) || g == kGroupBlockIdle;
}
// Actions that should keep playing while the owner holds them (automatic fire, blocking): replayed with the hold flag.
inline bool isHeldAction(int g) { return g == kGroupBlockIdle || (g >= 74 && g <= 79); }   // BlockIdle, AttackLoop* (74..79)
// Approximate playing time (ms) of a one-shot when the engine cannot tell us (first guesses, none measured).
inline uint32_t nominalActionMs(int g) {
    if (isReloadGroup(g)) return 2200;
    if (g == kGroupStagger) return 700;
    if (g == kGroupRecoil) return 450;
    if (g == kGroupBlockHit) return 400;
    if (isDodgeGroup(g)) return 600;
    if (isJamGroup(g)) return 900;
    if (isAttackGroup(g)) return 600;
    return 500;
}

}  // namespace nvcoop::actors::anim
