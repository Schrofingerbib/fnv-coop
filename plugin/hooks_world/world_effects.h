// L12 lane-local world effects: NPC state that has no synccore Effect type yet (requested from L4, see internal lane notes).
// Field layout mirrors the proposed L4 types so switching is a mechanical rename. Game-free.
//
// Until L4 lands them these travel as WorldEffect through WorldLog (world_core.h) and the bridge (world.drain / world.apply).
#pragma once
#include <cstdint>
#include <string>
#include <variant>

#include "fnvcoop/types.h"

namespace nvworld {

// Non-player actor value change. mode: 0 set base (SetAV/ForceAV), 1 mod delta (ModAV), 2 damage (DamageAV), 3 restore.
enum class AvMode : uint8_t { Set = 0, Mod = 1, Damage = 2, Restore = 3 };
struct SetActorValueRef {
    fnvcoop::FormRef actor;
    uint32_t av = 0;
    float value = 0;
    AvMode mode = AvMode::Set;
    bool operator==(const SetActorValueRef& o) const {
        return actor == o.actor && av == o.av && value == o.value && mode == o.mode;
    }
};
// SetEssential (base NPC form) / SetActorRefEssential (this reference only).
struct SetEssential {
    fnvcoop::FormRef actor;
    bool essential = false;
    bool refOnly = false;
    bool operator==(const SetEssential& o) const {
        return actor == o.actor && essential == o.essential && refOnly == o.refOnly;
    }
};
struct SetRestrained {
    fnvcoop::FormRef actor;
    bool on = false;
    bool operator==(const SetRestrained& o) const { return actor == o.actor && on == o.on; }
};
// SetEnemy / SetAlly between two factions. kind 0 neutral, 1 enemy, 2 ally.
enum class Relation : uint8_t { Neutral = 0, Enemy = 1, Ally = 2 };
struct SetRelation {
    fnvcoop::FormRef a, b;
    Relation kind = Relation::Neutral;
    bool operator==(const SetRelation& o) const { return a == o.a && b == o.b && kind == o.kind; }
};
struct SetScriptPackage {
    fnvcoop::FormRef actor, package;
    bool add = true;
    bool operator==(const SetScriptPackage& o) const { return actor == o.actor && package == o.package && add == o.add; }
};

// IgnoreCrime on a (story) NPC: crimes against / by it are not reported while on.
struct SetIgnoreCrime {
    fnvcoop::FormRef actor;
    bool on = false;
    bool operator==(const SetIgnoreCrime& o) const { return actor == o.actor && on == o.on; }
};
// SetPCEnemyOfFaction (on = 1/0) and ClearFactionPlayerEnemyFlag (on = 0): the faction's "player is its enemy" flag.
struct SetFactionPlayerEnemy {
    fnvcoop::FormRef faction;
    bool on = false;
    bool operator==(const SetFactionPlayerEnemy& o) const { return faction == o.faction && on == o.on; }
};

// Ownership flags (world-time lane). SetOwnership [owner] / ClearOwnership on a reference. `owner` empty (no plugin) = the command had no
// argument, which the engine reads as "owned by the player"; clear = ClearOwnership (no owner at all).
struct SetOwner {
    fnvcoop::FormRef ref;
    fnvcoop::FormRef owner;
    bool clear = false;
    bool operator==(const SetOwner& o) const { return ref == o.ref && owner == o.owner && clear == o.clear; }
};
// SetCellOwnership <cell> <owner>: who owns every unowned thing in the cell.
struct SetCellOwner {
    fnvcoop::FormRef cell;
    fnvcoop::FormRef owner;
    bool operator==(const SetCellOwner& o) const { return cell == o.cell && owner == o.owner; }
};
// SetCellPublicFlag <cell> <0|1>: a public cell (sleeping and waiting are not trespassing).
struct SetCellPublic {
    fnvcoop::FormRef cell;
    bool on = false;
    bool operator==(const SetCellPublic& o) const { return cell == o.cell && on == o.on; }
};

using WorldEffect = std::variant<SetActorValueRef, SetEssential, SetRestrained, SetRelation, SetScriptPackage, SetIgnoreCrime,
                                 SetFactionPlayerEnemy, SetOwner, SetCellOwner, SetCellPublic>;

const char* worldEffectName(const WorldEffect& e);

}  // namespace nvworld
