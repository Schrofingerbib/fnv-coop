#include "actors/engine/actor_feed.h"

#include <algorithm>
#include <cmath>
#include <cstdlib>

#include "actors/engine/registry.h"
#include "fnvcoop_mut.h"

namespace nvcoop::actors::engine {

namespace {
constexpr uint64_t kPoseMs = 50;
constexpr int kMissesToDrop = 2;     // scans a tracked actor may be missing before it counts as unloaded (list truncation, one-frame cell flips)
}  // namespace

ActorFeed::ActorFeed(IActorEngine& eng, EnginePort& port, Ledger& ledger, std::function<ActorSync*()> syncProvider, std::function<uint64_t()> sessionGen)
    : eng_(eng), port_(port), led_(ledger), syncFn_(std::move(syncProvider)), genFn_(std::move(sessionGen)) {}

// ---- adjacency -----------------------------------------------------------------------------------------------------------------------------

bool ActorFeed::adjacent(CellId a, CellId b) {
    if (a == b) return true;
    if (cellCacheFrame_ != now_) { cellCache_.clear(); cellCacheFrame_ = now_; }
    auto get = [&](CellId c) -> const CellInfo& {
        auto it = cellCache_.find(c);
        if (it != cellCache_.end()) return it->second;
        CellInfo ci;
        eng_.cellInfo(c, ci);
        return cellCache_.emplace(c, ci).first->second;
    };
    const CellInfo& x = get(a);
    const CellInfo& y = get(b);
    if (!x.known || !y.known || !x.exterior || !y.exterior) return false;
    if (x.world != y.world && !FNV_MUT(57)) return false;   // twin 57: grid squares of different worldspaces touch
    const int radius = 2;   // the game loads a 5x5 grid by default (uGridsToLoad = 5)
    return std::abs(x.x - y.x) <= radius && std::abs(x.y - y.y) <= radius;
}

// ---- frame ---------------------------------------------------------------------------------------------------------------------------------

void ActorFeed::tick(uint64_t nowMs) {
    now_ = nowMs;
    if (registry().cfgVersion != cfgSeen_ && !FNV_MUT(66)) {   // engine::configure() ran after this feed was built (twin 66: it is never noticed)
        cfgSeen_ = registry().cfgVersion;
        setConfig(registry().cfg);
    }
    port_.beginFrame(nowMs);
    if (!eng_.worldReady()) return;   // menus, loading screens
    const uint32_t ep = eng_.worldEpoch();
    if (!haveEpoch_ || ep != epoch_) {
        const bool first = !haveEpoch_;
        epoch_ = ep;
        haveEpoch_ = true;
        onWorldChanged();
        (void)first;
    }
    ActorSync* s = syncFn_ ? syncFn_() : nullptr;
    const uint64_t gen = genFn_ ? genFn_() : 0;
    if (s != sync_ || gen != gen_) {
        if (sync_) endSession();
        sync_ = s;
        gen_ = gen;
        if (s) startSession(s);
    }
    if (!sync_) {
        port_.flush();   // ledger writes and the sweep still run without a session
        return;
    }
    if (cfg_.enabled) {
        // the players' places first: tracking an actor makes the host grant it at once, and that grant needs to know who is near
        if (nowMs >= nextPoseMs_) { nextPoseMs_ = nowMs + kPoseMs; if (!FNV_MUT(56)) feedPlayerPose(nowMs); }
        if (dirty_ || nowMs >= nextScanMs_) scan(nowMs);
        if (nowMs >= nextDeathMs_) { nextDeathMs_ = nowMs + cfg_.pollDeathMs; pollDeaths(nowMs); }
        rebaseSwapped(nowMs);
    }
    port_.flush();
}

void ActorFeed::scanNow(uint64_t nowMs) {
    now_ = nowMs;
    port_.beginFrame(nowMs);
    sync_ = syncFn_ ? syncFn_() : nullptr;
    if (sync_) scan(nowMs);
}

void ActorFeed::onWorldChanged() {
    ++st_.worldChanges;
    // the old world is gone: nothing in it can be called any more
    if (sync_) for (auto& kv : fed_) sync_->onActorUnloaded(kv.first);
    fed_.clear();
    byRef_.clear();
    rebase_.clear();
    port_.forgetWorld();
    if (sync_ && !FNV_MUT(52)) sync_->purgeStandIns();   // twin 52: ActorSync keeps believing in clones that died with the old world
    if (!FNV_MUT(55)) port_.undoStale();                 // twin 55: what a save / crash left in the world stays there
    dirty_ = true;
}

void ActorFeed::startSession(ActorSync* s) {
    (void)s;
    ++st_.sessions;
    fed_.clear();
    byRef_.clear();
    rebase_.clear();
    if (!FNV_MUT(51)) { dirty_ = true; nextScanMs_ = 0; }   // twin 51: a new session waits for the next periodic scan before it learns the actors that are loaded
}

void ActorFeed::endSession() {
    if (!FNV_MUT(50)) port_.releaseAll();   // twin 50: NPCs stay restrained and clones stay standing after the session
    fed_.clear();
    byRef_.clear();
    rebase_.clear();
}

// ---- scan ----------------------------------------------------------------------------------------------------------------------------------

bool ActorFeed::identify(RefId ref, const ActorRaw& raw, ActorMeta& meta) {
    meta = ActorMeta{};
    fnvcoop::FormRef fr;
    NetId nid = 0;
    if (raw.created()) {
        Registry& reg = registry();
        if (!FNV_MUT(53)) {
            if (!reg.createdId || !reg.createdId(ref, nid, fr) || !nid) { ++st_.createdSkipped; return false; }
        } else {
            nid = ref; fr = fnvcoop::FormRef("*created*", ref);   // BAD (twin 53): a machine-local id is used on the wire
        }
        meta.created = true;
    } else {
        if (!eng_.toFormRef(ref, fr)) return false;
        nid = deriveNetId(fr);
    }
    meta.netId = nid;
    meta.ref = fr;
    fnvcoop::FormRef base;
    if (raw.baseId && eng_.toFormRef(raw.baseId, base)) meta.base = base;
    meta.named = raw.baseUnique || raw.baseEssential;
    meta.cell = raw.cell;
    meta.pos = raw.pos;
    return true;
}

void ActorFeed::scan(uint64_t nowMs) {
    ++st_.scans;
    dirty_ = false;
    nextScanMs_ = nowMs + cfg_.scanMs;
    ActorSync* s = sync_;
    if (!s) return;

    std::vector<RefId> refs;
    eng_.loadedActors(refs, 4096);   // the engine's list is not sorted by distance: take it all, cap by distance below
    const RefId player = eng_.playerRef();
    ActorRaw pr;
    live::Vec3 ppos;
    if (player && eng_.read(player, pr)) ppos = pr.pos;

    struct Cand { RefId ref; ActorRaw raw; float d2; };
    std::vector<Cand> fresh;
    std::set<NetId> seen;
    std::vector<NetId> excludedKnown;
    const RefFilter& exclude = registry().exclude;
    for (RefId ref : refs) {
        if (!ref || ref == player || ref == kLocalPlayerRef) continue;
        if (exclude && exclude(ref) && !FNV_MUT(59)) {   // twin 59: a puppet / ghost another layer drives is tracked as an NPC and fought over
            auto k = byRef_.find(ref);
            if (k != byRef_.end()) excludedKnown.push_back(k->second);
            continue;
        }
        if (port_.isOurClone(ref) && !FNV_MUT(43)) continue;   // twin 43: our own stand-ins are tracked as if they were new actors
        ActorRaw raw;
        if (!eng_.read(ref, raw) || !raw.valid) continue;
        if (raw.isPlayer && !FNV_MUT(41)) continue;            // twin 41: the player is tracked as an NPC
        if (raw.deleted) continue;
        auto known = byRef_.find(ref);
        if (known != byRef_.end()) {
            seen.insert(known->second);
            continue;
        }
        if (raw.disabled && port_.reclaimDisabled(ref)) raw.disabled = false;   // disabled by a pick swap whose clone is gone
        if (raw.disabled && !FNV_MUT(44)) continue;            // twin 44: disabled (not yet / no longer in the world) actors are tracked
        if (raw.dead() && !FNV_MUT(42)) continue;              // twin 42: corpses are tracked and published forever
        const float dx = raw.pos.x - ppos.x, dy = raw.pos.y - ppos.y, dz = raw.pos.z - ppos.z;
        fresh.push_back({ref, raw, dx * dx + dy * dy + dz * dz});
    }
    std::sort(fresh.begin(), fresh.end(), [](const Cand& a, const Cand& b) { return a.d2 < b.d2; });

    for (Cand& c : fresh) {
        if (fed_.size() >= cfg_.maxScanActors) break;
        ActorMeta meta;
        if (!identify(c.ref, c.raw, meta)) continue;
        if (fed_.count(meta.netId)) continue;   // two refs with one identity: the first wins (the host also refuses a second one)
        port_.bindReal(meta, c.ref);
        if (!s->track(meta)) {
            ++st_.trackFailed;
            if (!FNV_MUT(54)) port_.unbindReal(meta.netId);   // twin 54: an actor ActorSync refused stays bound and is driven
            continue;
        }
        s->onActorLoaded(meta.netId);   // a stand-in for it is removed
        Fed f;
        f.ref = c.ref;
        f.meta = meta;
        fed_[meta.netId] = f;
        byRef_[c.ref] = meta.netId;
        seen.insert(meta.netId);
        ++st_.trackedNew;
        registry().emit(TrackedEvent{meta.netId, c.ref, meta.ref, meta.base, meta.cell, meta.named, meta.created, false});
    }

    for (NetId id : excludedKnown) drop(id, false);   // became another layer's (a puppet was made from it): not ours any more

    // the ones that were not in the list: gone only when the engine confirms it, and not on the first miss
    std::vector<NetId> gone;
    for (auto& kv : fed_) {
        if (seen.count(kv.first)) { kv.second.misses = 0; continue; }
        ActorRaw raw;
        const bool exists = eng_.read(kv.second.ref, raw) && raw.valid && !raw.deleted && (raw.cell == 0 || eng_.cellLoaded(raw.cell));
        if (exists && !FNV_MUT(45)) { kv.second.misses = 0; continue; }
        if (++kv.second.misses >= (FNV_MUT(46) ? 1 : kMissesToDrop) || raw.deleted) gone.push_back(kv.first);
    }
    for (NetId id : gone) drop(id, false);
}

void ActorFeed::drop(NetId id, bool destroyed) {
    auto it = fed_.find(id);
    if (it == fed_.end()) return;
    const RefId ref = it->second.ref;
    port_.unbindReal(id);
    byRef_.erase(ref);
    fed_.erase(it);
    ++st_.trackedGone;
    if (sync_) {
        sync_->onActorUnloaded(id);
        if (destroyed) sync_->untrack(id);
    }
    registry().emit(LostEvent{id, ref, false});
}

// ---- deaths ----------------------------------------------------------------------------------------------------------------------------------

void ActorFeed::pollDeaths(uint64_t nowMs) {
    ActorSync* s = sync_;
    if (!s) return;
    ++st_.deathsPolled;
    for (auto& kv : fed_) {
        Fed& f = kv.second;
        const NetId id = kv.first;
        if (f.dead) continue;
        const RefId r = port_.activeRef(id);
        ActorRaw raw;
        if (!r || !eng_.read(r, raw)) continue;
        if (!raw.dead()) { f.deadSinceMs = 0; continue; }
        if (s->isDead(id)) { f.dead = true; continue; }   // the network already declared it (port.kill ran)
        const OwnerInfo o = s->ownerOf(id);
        if (o.mine && !FNV_MUT(47)) {
            // the owner's engine killed it: publish, unless the rules forbid (a named NPC dies only by a host KillActor)
            if (s->onLocalDeath(id)) {
                f.dead = true;
                ++st_.deathsReported;
                registry().emit(DiedEvent{id, r, true, false, true});
            } else {
                ++st_.deathsRefused;
                port_.revive(id);
            }
            continue;
        }
        // A story NPC's copy that is dead here can only have died through a host KillActor effect (protection stops every other death), and
        // that effect may not have reached ActorSync yet: never undo it.
        if (f.meta.named && !FNV_MUT(60)) continue;   // twin 60: a story kill applied on this machine is revived after the grace
        // a copy that died locally (a local hit, an explosion) before the owner's death message arrived: the owner decides. Give the
        // message time to arrive, then undo the local death.
        if (!f.deadSinceMs) f.deadSinceMs = nowMs ? nowMs : 1;
        if (nowMs >= f.deadSinceMs + (FNV_MUT(49) ? 0 : cfg_.deathGraceMs) && !FNV_MUT(48)) {
            ++st_.deathsRefused;
            port_.revive(id);
            f.deadSinceMs = 0;
        }
    }
}

bool ActorFeed::onLocalDeathRef(RefId ref) {
    ActorSync* s = sync_;
    NetId id = netOfRef(ref);
    if (!id) id = port_.netOfRef(ref);
    if (!s || !id) return true;
    auto it = fed_.find(id);
    if (it != fed_.end() && it->second.dead) return true;
    const bool ok = s->onLocalDeath(id);
    if (ok && it != fed_.end()) {
        it->second.dead = true;
        ++st_.deathsReported;
        registry().emit(DiedEvent{id, ref, true, false, true});
    } else if (!ok) {
        ++st_.deathsRefused;
    }
    return ok;
}

// ---- players ----------------------------------------------------------------------------------------------------------------------------------

void ActorFeed::feedPlayerPose(uint64_t) {
    ActorSync* s = sync_;
    const RefId player = eng_.playerRef();
    ActorRaw raw;
    if (!s || !player || !eng_.read(player, raw)) return;
    PlayerPose p;
    p.present = true;
    p.cell = raw.cell;
    p.pos = raw.pos;
    std::set<CellId> adj;
    for (auto& kv : fed_)
        if (kv.second.meta.cell != raw.cell && adjacent(raw.cell, kv.second.meta.cell)) adj.insert(kv.second.meta.cell);
    p.adjacent.assign(adj.begin(), adj.end());
    s->setPlayerPose(s->self(), p);
    ++st_.poseUpdates;
}

// ---- saves --------------------------------------------------------------------------------------------------------------------------------------

void ActorFeed::beforeSave() {
    if (sync_) sync_->purgeStandIns();   // ActorSync's flags and the port's clones go together; they come back after the grace
    const std::vector<NetId> reverted = port_.beforeSave();
    for (NetId id : reverted) rebase_.push_back({id, now_ + cfg_.saveGraceMs});
}

void ActorFeed::rebaseSwapped(uint64_t nowMs) {
    if (rebase_.empty()) return;
    std::vector<std::pair<NetId, uint64_t>> keep;
    for (auto& p : rebase_) {
        if (nowMs < p.second) { keep.push_back(p); continue; }
        auto it = fed_.find(p.first);
        if (!sync_ || it == fed_.end()) continue;
        ActorRaw raw;
        fnvcoop::FormRef base;
        if (eng_.read(it->second.ref, raw) && eng_.toFormRef(raw.baseId, base)) {
            sync_->onNativeBase(p.first, base);   // ActorSync compares with the shared pick and asks the port for the swap again
            ++st_.rebased;
        }
    }
    rebase_ = std::move(keep);
}

}  // namespace nvcoop::actors::engine
