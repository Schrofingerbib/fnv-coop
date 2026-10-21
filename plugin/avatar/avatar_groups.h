// Animation group ids the puppet driver may ask for. Ids follow the xNVSE enum TESAnimGroup::kAnimGroup_*, the same
// table tools/animcheck/animgroups_table.py is generated from. tools/avatar/check_groups.py proves this table equals
// that Python table line by line (game-free). That the running game uses the same numbering is NOT measured yet
// (docs/tools/T7-anim.md "First in-game check"). The name is what the script command `PlayGroup <name> <flag>` takes.
//
// Weapon-type variants: the engine picks the variant of a group that matches the equipped weapon. We only ever ask for
// the base group. TODO-RE: whether PlayGroup by name resolves the weapon variant itself.
#pragma once
#include <cstddef>
#include <cstring>

namespace nvcoop::avatar {

enum Group : int {
    kGrpIdle = 0,
    kGrpForward = 3,
    kGrpBackward = 4,
    kGrpLeft = 5,
    kGrpRight = 6,
    kGrpFastForward = 7,
    kGrpFastBackward = 8,
    kGrpFastLeft = 9,
    kGrpFastRight = 10,
    kGrpAim = 17,
    kGrpAimUp = 18,
    kGrpAimDown = 19,
    kGrpAimIS = 20,
    kGrpAimISUp = 21,
    kGrpAimISDown = 22,
    kGrpHolster = 23,
    kGrpEquip = 24,
    kGrpUnequip = 25,
    kGrpReloadA = 177,
    kGrpJumpStart = 227,
    kGrpJumpLoop = 228,
    kGrpJumpLand = 229,
    kGrpJumpLoopForward = 236,
    kGrpJumpLandForward = 241,
};

struct GroupInfo {
    int id;
    const char* name;
};

// One entry per line so a script can parse it. Keep sorted by id.
inline constexpr GroupInfo kGroupTable[] = {
    {0, "Idle"},
    {3, "Forward"},
    {4, "Backward"},
    {5, "Left"},
    {6, "Right"},
    {7, "FastForward"},
    {8, "FastBackward"},
    {9, "FastLeft"},
    {10, "FastRight"},
    {17, "Aim"},
    {18, "AimUp"},
    {19, "AimDown"},
    {20, "AimIS"},
    {21, "AimISUp"},
    {22, "AimISDown"},
    {23, "Holster"},
    {24, "Equip"},
    {25, "Unequip"},
    {177, "ReloadA"},
    {227, "JumpStart"},
    {228, "JumpLoop"},
    {229, "JumpLand"},
    {236, "JumpLoopForward"},
    {241, "JumpLandForward"},
};

// nullptr = not a group this layer knows (the script backend then refuses the call instead of guessing).
inline const char* GroupName(int id) {
    for (const GroupInfo& g : kGroupTable)
        if (g.id == id) return g.name;
    return nullptr;
}

// Group categories used by the planner and the tests.
inline bool IsLocomotionGroup(int g) { return g >= kGrpForward && g <= kGrpFastRight; }
inline bool IsRunGroup(int g) { return g >= kGrpFastForward && g <= kGrpFastRight; }
// Groups that play once and end on their own; the engine going back to idle after them is normal, so the
// readback-mismatch re-assert skips them.
inline bool IsOneShot(int g) { return g == kGrpHolster || g == kGrpEquip || g == kGrpUnequip || g == kGrpReloadA || g == kGrpJumpStart || g == kGrpJumpLand || g == kGrpJumpLandForward; }
inline bool IsJumpGroup(int g) { return g == kGrpJumpStart || g == kGrpJumpLoop || g == kGrpJumpLand || g == kGrpJumpLoopForward || g == kGrpJumpLandForward; }

}  // namespace nvcoop::avatar
