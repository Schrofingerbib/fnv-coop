// Actor ownership in the plugin (Milestone 3, FULL-SYNC-PLAN 3.2): shared types and the two ports ActorSync talks through.
// Game-free. The engine is reached only through IActorPort (in-game: the hooks glue; tests: a fake engine) and the network only
// through IActorNet (in-game: the coop mux; tests: an in-memory net with loss and duplication).
#pragma once
#include <cstdint>
#include <functional>
#include <string>
#include <vector>
#include "fnvcoop/ownership.h"
#include "fnvcoop/transport.h"
#include "fnvcoop/types.h"
#include "live/live.h"

namespace nvcoop::actors {

namespace live = fnvcoop::live;
using fnvcoop::Bytes;
using fnvcoop::FormRef;
using fnvcoop::OwnerEpoch;
using fnvcoop::PlayerId;
using fnvcoop::kHostPlayer;
using fnvcoop::kNoPlayer;
using live::CellId;
using live::NetId;

// First byte of every datagram this module sends through the coop MuxTransport (0xFC story, 0xD1 dialogue, 0xE5 slides, 0xA1 reserved).
constexpr uint8_t kChannelActors = 0xAC;

// What the module knows about one actor it syncs. `base` is the form the engine actor currently uses (it changes when a leveled pick is
// swapped in); `named` marks story NPCs (death only through a host KillActor effect).
struct ActorMeta {
    NetId netId = 0;
    FormRef ref;
    FormRef base;
    bool named = false;
    bool created = false;        // spawned at run time (SpawnRef); a stand-in clone is never "created"
    CellId cell = 0;
    live::Vec3 pos;
};

// NetId of a placed or created ref: stable on every machine, never zero, never in the 0xFF created-id range.
// (Created refs arrive with a host-allocated id and do not use this.) Collisions are possible in theory; the host refuses a second ref
// with a taken id (Stats::idCollisions).
NetId deriveNetId(const FormRef& ref);

// One player as the ownership service needs to see them (fed by the avatar layer).
struct PlayerPose {
    bool present = false;
    CellId cell = 0;
    live::Vec3 pos;
    std::vector<CellId> adjacent;   // loaded neighbouring cells
};

// ---- the engine side ---------------------------------------------------------------------------------------------------------
// Everything is keyed by NetId. A stand-in clone and the real actor share a NetId: only one of them exists on a machine at a time.
// There is deliberately no Respawn / Reset3D call: a handoff must never recreate the actor (the STR #906 lesson).
class IActorPort {
public:
    virtual ~IActorPort() = default;
    virtual bool loaded(NetId id) = 0;                                    // a live ref (real or stand-in) exists here
    virtual bool cellLoaded(CellId cell) = 0;
    // Owner: native state. pos/rot/vel/anim/ai and the actor values that changed since the last call (absolute values).
    virtual bool sample(NetId id, live::ActorSnapshot& out) = 0;
    virtual bool pose(NetId id, live::ActualPose& out) = 0;               // where the ref really is right now (for smoothing)
    // Non-owner: show this pose (restrained / AI-off actor). out.snapped = teleport, no blending.
    virtual void drive(NetId id, const live::InterpOut& out) = 0;
    virtual void applyAv(NetId id, const std::vector<live::AvValue>& values) = 0;
    // New owner: put the actor exactly where the old owner left it (full authoritative state, merged AVs). No respawn, no Reset3D.
    virtual void place(NetId id, const live::ActorSnapshot& authoritative) = 0;
    virtual void setAi(NetId id, bool on) = 0;                            // owner true; everyone else false (AI-off / restrained)
    virtual void setProtected(NetId id, bool on) = 0;                     // named NPC: local damage cannot kill it
    virtual bool spawnStandIn(const ActorMeta& meta, const live::ActorSnapshot& at) = 0;   // clone for an actor with no local ref
    virtual void removeStandIn(NetId id) = 0;
    virtual void setBase(NetId id, const FormRef& pick) = 0;              // leveled pick: swap the base form to match
    virtual void kill(NetId id) = 0;                                      // apply death (called only when authorised)
    virtual void revive(NetId id) = 0;                                    // scripted resurrect (host ResurrectActor effect)
};

// ---- the network side --------------------------------------------------------------------------------------------------------
class IActorNet {
public:
    virtual ~IActorNet() = default;
    // Client: peer is always the host. Host: any joined client. `data` already starts with kChannelActors.
    virtual void send(PlayerId peer, bool reliable, Bytes data) = 0;
};

struct SyncConfig {
    PlayerId self = kHostPlayer;
    bool isHost = true;
    fnvcoop::OwnershipConfig own;
    live::InterpConfig interp;
    live::InterestConfig interest;
    live::SenderConfig sender;
    uint32_t ownEvalMs = 250;      // host: re-decide every tracked actor this often
    uint32_t settleMs = 300;       // owner: an idle actor sends one reliable settle snapshot after this long
    uint32_t resyncMinMs = 500;    // client: at most one MResync per actor per this long
    uint32_t holdAiMaxMs = 4000;   // safety net: a new owner that committed without the old owner's final state waits at most this long before it drives the AI
    std::function<bool(CellId, CellId)> adjacent;   // neighbouring exterior cells (default: same cell only)
};

struct OwnerInfo {
    bool known = false;            // the ref maps to a tracked actor
    NetId netId = 0;
    PlayerId owner = kNoPlayer;    // committed owner on this machine (kNoPlayer = nobody)
    OwnerEpoch epoch = 0;
    bool pending = false;          // a handoff is in flight
    PlayerId pendingTo = kNoPlayer;
    PlayerId self = kNoPlayer;
    bool mine = false;             // owner == self
    bool aiOn = false;             // what this machine told the engine (AI on = we simulate it)
    bool standIn = false;
    bool dead = false;             // died here (the ownership record is kept so a resurrect continues the epoch history)
};

struct Stats {
    uint64_t published = 0;            // snapshots this machine sampled from owned actors
    uint64_t publishedNotOwner = 0;    // must stay 0
    uint64_t snapshotsApplied = 0, snapshotsDropped = 0;
    uint64_t staleEpochDropped = 0, notOwnerDropped = 0, futureEpoch = 0, unknownActor = 0, staleSeqDropped = 0;
    uint64_t finalsApplied = 0, finalsSent = 0, acksSent = 0, takeovers = 0, handoffsOut = 0;
    uint64_t standInsMade = 0, standInsRemoved = 0;
    uint64_t deathsApplied = 0, namedDeathsBlocked = 0, killsAuthorised = 0;
    uint64_t picksApplied = 0, picksRejected = 0;
    uint64_t resyncsSent = 0, resyncsAnswered = 0, idCollisions = 0, undecodable = 0, spoofed = 0;
    uint64_t ownershipApplied = 0, ownershipDuplicates = 0, ownershipStale = 0;
    uint64_t recruiterChanges = 0;
    uint64_t animEventsSent = 0, animEventsApplied = 0, animEventsRejected = 0, animEventsDuplicate = 0;   // enemy-anim MAnimEvent
    uint64_t aiHolds = 0, aiHoldsReleased = 0, staleFinalsForwarded = 0;   // forced-commit hold of the new owner's AI (see ActorSync::onOwnApplied)
};

}  // namespace nvcoop::actors
