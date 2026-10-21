// Wire messages of the fake engine's live layer (actors, combat, loot, containers, spawns, digests).
// Encoded with fnvcoop::Archive. One Envelope per datagram payload; a ReliableLink frame wraps it (machine.cpp).
#pragma once
#include <variant>
#include <vector>
#include "fnvcoop/effect.h"
#include "fnvcoop/engine/types.h"
#include "fnvcoop/ownership.h"
#include "fnvcoop/transport.h"

namespace fnvcoop::engine {

constexpr uint8_t kEngineWireVersion = 1;

using LootList = std::vector<std::pair<FormRef, int32_t>>;

struct MFullState { uint32_t sess = 0; uint64_t hostNowMs = 0; uint64_t fxSeq = 0; Bytes world; std::vector<OwnMsg> own; };
struct MPose { PlayerId slot = kNoPlayer; bool present = true; CellId cell = 0; Vec3 pos; float yaw = 0; uint32_t seq = 0; bool settle = false; };   // settle: the player stopped; sent reliably once
struct MVitals { PlayerId slot = kNoPlayer; int32_t health = 100; bool dead = false; uint32_t seq = 0; };
// Owner -> everyone (via host). `final` = the old owner's last state during a handoff.
struct MActorSnap { NetId id = 0; OwnerEpoch epoch = 0; uint32_t seq = 0; CellId cell = 0; Vec3 pos; float yaw = 0;
                    int32_t health = 0; bool dead = false; bool settle = false; bool final = false; };
enum class ActorEventKind : uint8_t { Vitals = 0, Death = 1 };
struct MActorEvent { NetId id = 0; OwnerEpoch epoch = 0; uint64_t eventId = 0; ActorEventKind kind = ActorEventKind::Vitals;
                     int32_t health = 0; uint8_t limbMask = 0; bool dead = false; LootList loot;
                     uint32_t seq = 0; CellId cell = 0; Vec3 pos; float yaw = 0;   // pos/seq: the owner's state when it happened (a corpse stops where it fell)
                     int32_t limbDmg[combat::kLimbCount] = {0, 0, 0, 0, 0, 0};   // the owner's per-limb damage counters, so a later owner continues them
                     uint8_t killerKind = 0; uint32_t killerId = 0; };           // who dealt the blow (combat::Entity), for kill XP
struct MHit { uint64_t hitId = 0; bool attackerIsPlayer = true; uint32_t attacker = 0; bool victimIsPlayer = false; uint32_t victim = 0;
              int32_t damage = 0; Limb limb = Limb::Torso; Vec3 impact; float weaponRange = 0; uint8_t hops = 0;
              uint32_t firedAt = 0;             // host-time ms when it was fired (combat::HitEvent::firedAtTick)
              uint32_t attackerEpoch = 0;       // npc attackers: the owner epoch the reporter knew
              uint64_t projectile = 0;          // combat::ProjectileSpawn::id when a projectile carried it
              uint64_t explosionId = 0; };      // non-zero: explosion damage (impact = blast centre, weaponRange = radius)
// Shooter's owner -> everyone: a projectile. Only the owner's copy is real; the others show it (combat::ProjectileRole::VisualOnly).
// `aim` is where the owner's physics will land it (the visual copy only needs it for the impact effect), `damage` is the owner's number.
struct MProjectile { combat::ProjectileSpawn p; NetId aim = 0; int32_t damage = 0; uint32_t flightMs = 0; };
// Source's owner -> everyone: an explosion went off. Damage is NOT in this message: it travels as one MHit per victim to that victim's owner.
struct MExplosion { combat::ExplosionEvent e; };
struct MOwn { OwnMsg m; };
struct MEffects { uint64_t seq = 0; std::vector<Effect> fx; };
struct MDropReq { uint32_t reqId = 0; FormRef base; CellId cell = 0; Vec3 pos; };
struct MItemCreated { NetId id = 0; FormRef base; CellId cell = 0; Vec3 pos; uint64_t origin = 0; };
struct MPickup { uint64_t claimId = 0; NetId item = 0; };
struct MItemTaken { NetId item = 0; PlayerId by = kNoPlayer; uint64_t claimId = 0; };
enum class ContainerOpKind : uint8_t { Open = 0, Close = 1, Take = 2, Put = 3 };
struct MContainerReq { uint64_t opId = 0; NetId cont = 0; ContainerOpKind op = ContainerOpKind::Open; FormRef base; int32_t count = 0; uint64_t closeHash = 0; };
struct MContainerRes { uint64_t opId = 0; NetId cont = 0; ContainerOpKind op = ContainerOpKind::Open; bool ok = false; FormRef base; int32_t count = 0; };
struct MContainerSync { NetId cont = 0; FormRef base; int32_t newCount = 0; };
struct MSpawnRoll { NetId site = 0; };
struct MSpawnActor { NetId id = 0; NetId site = 0; FormRef base; FormRef pick; CellId cell = 0; Vec3 pos; };
struct MSiteState { NetId site = 0; NetId spawned = 0; bool cleared = false; uint64_t clearedAtMs = 0; uint32_t spawnCount = 0; };
struct MActivate { NetId obj = 0; };
struct MDigest { uint8_t cat = 0; uint32_t seq = 0; uint64_t total = 0; std::vector<std::pair<uint32_t, uint64_t>> keys; };
struct MRepair { uint8_t cat = 0; std::vector<std::pair<uint32_t, Bytes>> states; std::vector<uint32_t> removes; };

using Body = std::variant<MFullState, MPose, MVitals, MActorSnap, MActorEvent, MHit, MOwn, MEffects, MDropReq, MItemCreated,
                          MPickup, MItemTaken, MContainerReq, MContainerRes, MContainerSync, MSpawnRoll, MSpawnActor,
                          MSiteState, MActivate, MDigest, MRepair, MProjectile, MExplosion>;

struct Envelope {
    uint32_t sess = 0;
    PlayerId src = kNoPlayer;
    PlayerId dest = kNoPlayer;   // kNoPlayer = everyone except src
    Body body;
};

const char* bodyName(const Body& b);
bool reliableByDefault(const Body& b);     // pose and non-settle snapshots, digests: unreliable; everything else reliable
Bytes encode(const Envelope& e);
bool decode(const Bytes& data, Envelope& out);   // false on any malformed input (never throws)

// A local player/host action. Everything a test or fuzzer can do to one machine goes through Machine::doAction(Action),
// so a recorded Action stream is a complete, replayable script.
enum class ActionKind : uint8_t {
    Move = 0, Attack, Pickup, Drop, ContainerOpen, ContainerClose, ContainerTake, ContainerPut, Activate, UseDoor,
    Recruit, Dismiss, HostSetStage, HostSetLock, HostKill, Revive, Projectile, Explode, Count_
};
struct Action {
    ActionKind kind = ActionKind::Move;
    uint32_t a = 0;         // cell / victim / item / container / object / actor
    int32_t n = 0;          // damage / count / stage / level
    Vec3 pos;               // move target / impact point / blast centre
    float f = 0;            // yaw / weapon range / blast radius / projectile flight time (ms)
    FormRef base;           // item base / quest
    Limb limb = Limb::Torso;
    bool flag = false;      // HostSetLock: locked / Projectile: explosive
};
void io(Archive& a, Action& v);
const char* toString(ActionKind k);

}  // namespace fnvcoop::engine
