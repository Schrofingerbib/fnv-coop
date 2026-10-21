// ActorFeed: tells ActorSync what the engine has. One per machine, ticked once per frame by the module.
//   * scan      : enumerates the loaded actors (every Config::scanMs, or sooner when xNVSE reports a ref event), tracks new ones
//                 (ActorSync::track after EnginePort::bindReal), reports the ones that went away (onActorUnloaded);
//   * deaths    : polls tracked actors for a death the engine produced itself and asks the ownership rules (ActorSync::onLocalDeath);
//                 an owner's death is published, anything else is a death nobody authorised and is undone after a grace period;
//   * players   : the local player's place goes to ActorSync::setPlayerPose;
//   * sessions  : a new ActorSync (session start, rejoin) is re-fed; the end of a session releases every change made to the world;
//                 a loaded game forgets the old world and undoes whatever a save left behind (the ledger);
//   * saves     : beforeSave() removes stand-ins / pick clones so a save does not contain them.
// Game-free: the engine, the port and the ActorSync provider are injected.
#pragma once
#include <functional>
#include <map>
#include <set>
#include <vector>

#include "actors/actor_sync.h"
#include "actors/engine/engine_iface.h"
#include "actors/engine/engine_port.h"

namespace nvcoop::actors::engine {

struct FeedStats {
    uint64_t scans = 0, trackedNew = 0, trackedGone = 0, trackFailed = 0, createdSkipped = 0;
    uint64_t deathsPolled = 0, deathsReported = 0, deathsRefused = 0;
    uint64_t sessions = 0, worldChanges = 0, poseUpdates = 0, rebased = 0;
};

class ActorFeed {
public:
    ActorFeed(IActorEngine& eng, EnginePort& port, Ledger& ledger, std::function<ActorSync*()> syncProvider, std::function<uint64_t()> sessionGen = nullptr);

    void setConfig(const Config& c) { cfg_ = c; port_.setConfig(c); }
    const Config& config() const { return cfg_; }

    void tick(uint64_t nowMs);                // once per frame
    void markDirty() { dirty_ = true; }       // an xNVSE ref event: scan at the next tick
    bool onLocalDeathRef(RefId ref);          // engine::onLocalDeath: the engine is about to kill `ref` (true = allowed)
    void beforeSave();                        // engine::beforeSave

    // SyncConfig::adjacent: neighbouring exterior grid cells of one worldspace
    bool adjacent(CellId a, CellId b);

    const FeedStats& stats() const { return st_; }
    size_t fedCount() const { return fed_.size(); }
    bool fed(NetId id) const { return fed_.count(id) != 0; }
    NetId netOfRef(RefId ref) const { auto it = byRef_.find(ref); return it == byRef_.end() ? 0 : it->second; }

    // test hooks
    void scanNow(uint64_t nowMs);

private:
    struct Fed {
        RefId ref = 0;
        ActorMeta meta;
        int misses = 0;
        bool dead = false;
        uint64_t deadSinceMs = 0;
    };
    void onWorldChanged();
    void startSession(ActorSync* s);
    void endSession();
    void scan(uint64_t nowMs);
    void pollDeaths(uint64_t nowMs);
    void feedPlayerPose(uint64_t nowMs);
    void rebaseSwapped(uint64_t nowMs);
    void drop(NetId id, bool destroyed);
    bool identify(RefId ref, const ActorRaw& raw, ActorMeta& meta);

    IActorEngine& eng_;
    EnginePort& port_;
    Ledger& led_;
    std::function<ActorSync*()> syncFn_;
    std::function<uint64_t()> genFn_;
    Config cfg_;
    ActorSync* sync_ = nullptr;
    uint64_t gen_ = 0;
    uint32_t cfgSeen_ = 0;
    uint32_t epoch_ = 0;
    bool haveEpoch_ = false;
    bool dirty_ = true;
    uint64_t now_ = 0, nextScanMs_ = 0, nextDeathMs_ = 0, nextPoseMs_ = 0, nextStaleMs_ = 0;
    std::map<NetId, Fed> fed_;
    std::map<RefId, NetId> byRef_;
    std::vector<std::pair<NetId, uint64_t>> rebase_;   // pick swaps reverted by beforeSave: redo them after the save grace
    std::map<CellId, CellInfo> cellCache_;
    uint64_t cellCacheFrame_ = ~0ull;
    FeedStats st_;
};

}  // namespace nvcoop::actors::engine
