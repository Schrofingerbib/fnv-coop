// plugin/spawn: spawn-site arbitration inside the plugin (FULL-SYNC-PLAN 3.3). Shared types and the three ports SpawnSync talks through.
// Game-free. The engine is reached only through ISpawnEngine (in-game: engine_adapter over plugin/actors/engine + the world ledger of
// plugin/hooks_world; tests: a fake world), the network only through ISpawnNet (in-game: the coop mux; tests: a lossy in-memory net) and
// the story log only through ISpawnHost (host: HostSession::commit of a SpawnRef effect).
#pragma once
#include <cstdint>
#include <functional>
#include <string>
#include <vector>
#include "fnvcoop/effect.h"
#include "fnvcoop/transport.h"
#include "fnvcoop/types.h"
#include "spawn/spawn.h"

namespace nvcoop::spawn {

namespace sp = fnvcoop::spawn;
using fnvcoop::Bytes;
using fnvcoop::FormRef;
using fnvcoop::PlayerId;
using fnvcoop::kHostPlayer;
using fnvcoop::kNoPlayer;
using sp::CellId;
using sp::NetId;
using sp::SiteId;

using RefId = uint32_t;                 // runtime form id of a loaded reference (xNVSE refID); 0 = none

constexpr uint8_t kChannelSpawn = sp::kChannelSpawn;

// What the engine tells us about one loaded reference. Every field has a safe default; valid=false means the ref is gone.
struct RefInfo {
    bool valid = false;
    RefId ref = 0;
    bool dead = false;
    bool disabled = false;
    bool created = false;               // a dynamic (0xFF) reference
    bool interior = false;              // its cell is an interior
    FormRef base;                       // the base form the ref uses now (after a leveled roll: the rolled NPC / creature)
    FormRef leveledFrom;                // a placed ref whose original base is a leveled list (xNVSE ExtraLeveledCreature, extra 0x2E); empty = not leveled
    FormRef encounterZone;              // the ref's encounter zone (xNVSE ExtraEncounterZone, extra 0x74); empty = none
    FormRef wireRef;                    // placed refs: (plugin, local id) of the reference itself, for site ids
    NetId netId = 0;                    // placed refs: the net id every machine derives (plugin/actors deriveNetId); 0 = unknown
    CellId cell = 0;
    FormRef cellForm;
    sp::Vec3 pos;
};

class ISpawnEngine {
public:
    virtual ~ISpawnEngine() = default;
    virtual bool describe(RefId ref, RefInfo& out) = 0;
    virtual RefId refOfNet(NetId id) = 0;                                   // the loaded ref (placed, created or stand-in) for a net id; 0 = not loaded here
    virtual bool setHeld(RefId ref, bool hold) = 0;                         // hide a rolled actor until the host's verdict (Disable / Enable)
    virtual bool deleteRef(RefId ref) = 0;                                  // this machine's roll is turned off (MarkForDelete)
    virtual bool rebase(RefId ref, const FormRef& base) = 0;                // placed ref: use that base and make sure it is enabled; false = the engine cannot (TODO-RE)
    virtual bool kill(RefId ref) = 0;                                       // the party killed it: a local cell reset must not bring it back
    // Created-ref ledger (plugin/hooks_world SpawnLedger): host FormRef <-> local ref.
    virtual void bindCreated(RefId ref, NetId id) = 0;                      // local `ref` IS created actor `id`
    virtual bool isCreatedBound(NetId id) = 0;                              // a local object for that id exists (or is queued)
    virtual bool netOfCreated(RefId ref, NetId& id) = 0;                    // reverse lookup for refs the ledger created from a SpawnRef
    virtual bool createFromSpawnRef(const fnvcoop::SpawnRef& s) = 0;        // make this machine's own copy of a spawn it did not roll (queued until the cell loads)
};

class ISpawnNet {
public:
    virtual ~ISpawnNet() = default;
    // Client: peer is always the host. Host: any joined client. `data` already starts with kChannelSpawn. Spawn traffic is always reliable.
    virtual void send(PlayerId peer, Bytes data) = 0;
};

class ISpawnHost {
public:
    virtual ~ISpawnHost() = default;
    // Host only: record `s` as an ordinary SpawnRef effect (story log + broadcast to every client; `origin` is the roller).
    virtual void commitSpawn(PlayerId origin, const fnvcoop::SpawnRef& s) = 0;
};

// One roll the local engine made (a leveled placed ref resolved, or a group of created actors an encounter / marker produced).
struct LocalRollMember {
    RefId ref = 0;
    bool created = false;
    NetId netId = 0;                    // placed: the derived net id. created: ignored (the host issues it; a host roll uses the ref id itself)
    FormRef base;
    CellId cell = 0;
    sp::Vec3 pos;
};
struct LocalRoll {
    sp::SiteDef def;
    bool none = false;                  // the engine decided "nothing" (chance none)
    FormRef pick;
    std::vector<LocalRollMember> members;
};

struct SyncConfig {
    PlayerId self = kHostPlayer;
    bool isHost = true;
    sp::Config registry;                // host side tuning (respawn time, ownership hold ...)
    sp::TrackerConfig tracker;
    uint32_t digestEveryMs = 2000;      // client: send the site-table digest this often (the host heals a mismatch)
    uint32_t detectSettleMs = 500;      // unresolved created actors that appear within this window next to each other are one roll
    float detectRadius = 1200.0f;       // ... and within this distance
    uint32_t enforceEveryMs = 2000;     // sweep: re-check the placed members of a few sites this often (a local cell reset brings the dead back)
    uint32_t enforcePerTick = 8;        // ... this many sites per sweep, round robin
};

struct Stats {
    uint64_t rollsLocal = 0, rollsHeld = 0, rollsSuppressed = 0, rollsAdopted = 0;
    uint64_t verdictsAccepted = 0, verdictsRefused = 0;
    uint64_t sent = 0, received = 0, undecodable = 0, spoofed = 0, wrongDirection = 0;
    uint64_t digestsSent = 0, digestMismatch = 0, healsSent = 0, resyncsSent = 0, resyncsAnswered = 0;
    uint64_t bound = 0, shown = 0, dropped = 0, rebased = 0, rebaseFailed = 0, madeDead = 0, hostCopies = 0, spawnCommits = 0;
    uint64_t unresolvedSeen = 0, detectedGroups = 0, detectDropped = 0;
};

struct SiteInfo {
    bool known = false;
    SiteId site = 0;
    sp::SiteState state = sp::SiteState::Unrolled;
    PlayerId owner = kNoPlayer;
    fnvcoop::OwnerEpoch epoch = 0;
    uint32_t generation = 0;
    bool mine = false;
    bool interior = false;
    size_t members = 0, dead = 0;
    FormRef pick;
};

}  // namespace nvcoop::spawn
