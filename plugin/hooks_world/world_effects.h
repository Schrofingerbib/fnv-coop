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
// flags1 / flags2: the two optional integers of the script command (`SetEnemy FactionA FactionB [flagA] [flagB]`), replayed as given (the capture used to
// drop them, so `SetEnemy A B 1 1` reached the other machines as `SetEnemy A B`).
struct SetRelation {
    fnvcoop::FormRef a, b;
    Relation kind = Relation::Neutral;
    uint8_t flags1 = 0, flags2 = 0;
    bool operator==(const SetRelation& o) const { return a == o.a && b == o.b && kind == o.kind && flags1 == o.flags1 && flags2 == o.flags2; }
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

// Lock / open / destroyed state of a door, container or activator that a SCRIPT changed (UnLock, Lock, SetOpenState, SetDestroyed). The same fact also travels as
// the ObjectState effect of the capture hub; this copy goes through the world channel, which no state filter stands in front of (the shadow only forwards an
// effect it counts as a change, and it does not know the engine's own lock state), and which a client that joins later gets replayed. mask = the
// ObjectState mask bits (kObjOpen 1, kObjLock 2, kObjDestroyed 4).
struct SetObjectState {
    fnvcoop::FormRef ref;
    uint8_t mask = 0;
    bool open = false, locked = false;
    int32_t level = 0;
    bool destroyed = false;
    bool operator==(const SetObjectState& o) const {
        return ref == o.ref && mask == o.mask && open == o.open && locked == o.locked && level == o.level && destroyed == o.destroyed;
    }
};

// Presentation of a host-run script (G39): what the script shows or plays on the screen and speakers of the machine that runs it. A guest never runs the
// script, so without this it sees no fade and hears no sound. One-shot: never replayed to a late joiner (isEphemeral).
enum class PresentKind : uint8_t { ApplyImageSpace = 0, RemoveImageSpace = 1, PlayMusic = 2, PlaySound = 3 };
struct Present {
    PresentKind kind = PresentKind::ApplyImageSpace;
    fnvcoop::FormRef form;   // the ImageSpaceModifier / Music type / Sound
    float value = 0;         // ApplyImageSpace / RemoveImageSpace: the optional strength; PlaySound: the optional integer argument
    bool hasValue = false;
    bool operator==(const Present& o) const { return kind == o.kind && form == o.form && value == o.value && hasValue == o.hasValue; }
};

using WorldEffect = std::variant<SetActorValueRef, SetEssential, SetRestrained, SetRelation, SetScriptPackage, SetIgnoreCrime,
                                 SetFactionPlayerEnemy, SetOwner, SetCellOwner, SetCellPublic, SetObjectState, Present>;

const char* worldEffectName(const WorldEffect& e);
// True for one-shot effects: sent to the machines connected now, never kept for a late joiner.
bool isEphemeral(const WorldEffect& e);
// The key Runtime::pushWorldEffect coalesces on (a later state effect for the same subject replaces the earlier one); empty = a delta or an event.
std::string worldEffectKey(const WorldEffect& e);

}  // namespace nvworld
