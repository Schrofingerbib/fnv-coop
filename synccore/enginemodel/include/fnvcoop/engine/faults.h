// TEST-ONLY fault switches for the fake engine (same idea as fnvcoop::Mutations and fnvcoop::OwnershipFaults).
// Each flag re-introduces one classic bug; the invariant oracle that guards it must then fire. All false in production.
#pragma once

namespace fnvcoop::engine {

struct EngineFaults {
    bool noDedupe = false;              // links and txn ids stop dropping duplicates: re-delivered messages apply twice
    bool hitNoValidation = false;       // the victim's owner applies any hit (range, damage, rate, impact point unchecked)
    bool hitAppliedByNonOwner = false;  // a machine that is not the victim's owner applies a hit it receives (and forwards it)
    bool pickupNoArbitration = false;   // the host grants every pickup claim: both racers keep the item
    bool noPickupRollback = false;      // a pickup loser keeps the optimistically added item
    bool spawnNoArbitration = false;    // the host spawns for every roll: an encounter appears once per roller
    bool lootPerMachine = false;        // every machine rolls corpse loot itself instead of using the owner's roll
    bool pickPerMachine = false;        // every machine makes its own leveled pick
    bool containerNoLock = false;       // a container can be open for two players at once
    bool namedActorKillable = false;    // the owner can kill story NPCs through combat
    bool noSessionEpoch = false;        // messages from before a host load are applied after it
    bool healDisabled = false;          // digest mismatches are detected but never repaired
    bool dropNoIdempotency = false;     // a re-sent drop request creates a second world item
    bool ownerKeepsPublishing = false;  // a former owner keeps sending snapshots after losing the actor
    bool joinNoOwnership = false;       // the full-state sent to a joiner omits the ownership table
    bool digestBucketsPositions = false;   // digests round positions to 256 units and health to tens: differences below that are never healed
    bool digestNoSeq = false;           // the host judges every digest datagram it receives, so a link-duplicated or reordered report looks like a stable mismatch
    bool newOwnerNoWait = false;        // a forced commit starts the new owner's AI at once, while the old owner may not have heard of the handoff yet
    bool createdKeyIsPlayer = false;    // digest keys of created actors (0xFF......) are read as player slots: a repair overwrites a player's own state
    // combat-core (FULL-SYNC-PLAN 3.4)
    bool deathNotIdempotent = false;    // a dead actor can be hit and declared dead again: its loot is rolled and its corpse rebuilt a second time
    bool limbNotSynced = false;         // hit events leave the crippled-limb mask out: other machines never learn which limbs are crippled
    bool visualShotDamages = false;     // the visual-only copy of a projectile also applies damage on the machine that merely shows it
    bool blastRunsEverywhere = false;   // every machine that hears about an explosion applies the blast damage to its own copies of the actors in range
    bool joinNoTombstones = false;      // the full state a joiner gets leaves out the epochs of released (dead) actors: a late copy of an older assignment revives an owner for a corpse
    bool staleFinalNotForwarded = false;   // the host drops a previous owner's final state after a forced commit instead of passing it to the new owner, which keeps waiting (frozen actor) until its safety timeout
};
inline EngineFaults& engineFaults() { static EngineFaults f; return f; }
struct EngineFaultScope {
    EngineFaults saved;
    explicit EngineFaultScope(const EngineFaults& f) : saved(engineFaults()) { engineFaults() = f; }
    ~EngineFaultScope() { engineFaults() = saved; }
};

}  // namespace fnvcoop::engine
