// SpawnSync: spawn-site arbitration wired into one machine (FULL-SYNC-PLAN 3.3). Game-free; one instance per machine.
//
//   Roller  : the machine whose engine just rolled a spawn (a leveled placed ref resolved, an encounter / marker made actors). It HOLDS the
//             actors (hidden), asks the host, and acts on the verdict: Accept = bind them to the issued net ids and show them; refusal =
//             delete the created ones / rebase the placed ones to the shared result (the owner's roll is shown instead).
//   Host    : runs sp::SiteRegistry (one owner per site, epochs, ids, respawn timers, permanent interiors). An accepted roll becomes SpawnRef
//             effects (ISpawnHost::commitSpawn) for every other machine, and the host makes its own copy when it was not the roller.
//   Others  : mirror the site table (sp::SiteView), enforce what it says about placed refs (rebase to the owner's pick, keep the dead dead)
//             and answer "may my engine's roll stand?" without a round trip when the answer is already known.
//   Heal    : a client sends its table digest every digestEveryMs; the host answers a repeated mismatch with the whole table.
#pragma once
#include <map>
#include <memory>
#include <set>
#include <vector>
#include "spawn/roll_detector.h"
#include "spawn/spawn_types.h"
#include "spawn/spawn_wire.h"

namespace nvcoop::spawn {

class SpawnSync {
public:
    SpawnSync(const SyncConfig& cfg, ISpawnEngine& eng, ISpawnNet& net, ISpawnHost* host);
    ~SpawnSync();

    PlayerId self() const { return cfg_.self; }
    bool isHost() const { return cfg_.isHost; }

    // ---- session ----
    void playerJoined(PlayerId slot);   // host: a client joined; it is sent the whole table
    void playerLeft(PlayerId slot);     // host: its sites move to the next player
    void hostConnected();               // client: the link to the host is up (asks for the whole table)
    void hostLost();                    // client: forget the table; held rolls are released
    void setPlayerLoc(PlayerId slot, const sp::PlayerLoc& loc);   // host: where every player is (incl. itself); drives site ownership

    // ---- engine events ----
    uint32_t onLocalRoll(const LocalRoll& r);           // returns the rollId, 0 = settled without a round trip (or offline)
    void onPlacedLoaded(RefId ref);                      // the feed tracked an actor: roll it if it is a leveled placed ref, enforce its site
    void onMemberDied(NetId id);                         // host: an actor died / was removed (from the actor layer's death event)
    void onCellReset(CellId cell);                       // host: the engine reset a cell
    void forceRespawn(SiteId id);                        // host: story authority resets a site (even a permanent one)
    void onHostSpawnEffect(const sp::SiteDef& def, const fnvcoop::SpawnRef& s);   // host: a script's PlaceAtMe was captured (its id is the host ref)
    void onSiteCellLoaded(CellId cell);                  // re-enforce the sites of a cell that just loaded
    // The created-id resolver for the actor feed (actors::engine::setCreatedIdResolver). True = the ref has a net id and may be tracked.
    // An unresolvable ref that is not ours to ignore is remembered as the product of a local roll.
    bool resolveCreated(RefId ref, NetId& id, FormRef& wire);
    void setIgnore(std::function<bool(RefId)> f) { ignore_ = std::move(f); }   // puppets, stand-ins ...: never rolls
    void purge();                                        // a new world was loaded: bindings, held rolls and candidates are gone

    // ---- network ----
    void receive(PlayerId from, const uint8_t* data, size_t size);   // one datagram from the mux (starts with kChannelSpawn)
    void tick(uint64_t wallMs, uint64_t gameMs);

    // ---- host save / load: the site table and the id counter travel with the save (empty / false on a client) ----
    Bytes saveState() const;
    bool loadState(const Bytes& b);

    // ---- queries ----
    SiteInfo siteInfo(SiteId id) const;
    std::vector<SiteInfo> sites() const;
    size_t pendingRolls() const { return tracker_.pending(); }
    size_t waitingPlaced() const { return tracker_.waiting(); }
    size_t candidates() const { return det_.size(); }
    bool linkUp() const { return cfg_.isHost || hostUp_; }
    uint64_t digest() const { return view_.digest(); }
    const Stats& stats() const { return stats_; }
    const sp::SiteView& view() const { return view_; }
    const sp::SiteRegistry* registry() const { return registry_.get(); }
    const sp::TrackerStats& trackerStats() const { return tracker_.stats(); }
    bool boundNet(RefId ref, NetId& id) const;

private:
    sp::RollReport build(const LocalRoll& r) const;
    void applyActions(const std::vector<sp::Action>& acts);
    void enforce(const std::vector<sp::Directive>& ds);
    void onSiteApplied(const sp::SiteMsg& s);
    void onVerdict(const sp::VerdictMsg& v);
    void flush(sp::SiteRegistry::Out& out);
    void sendBody(PlayerId to, sp::Body b);
    void broadcast(const sp::Body& b);
    void sendJoinState(PlayerId slot);
    void onRollMsg(PlayerId from, const sp::RollReport& r);
    void detect(uint64_t wallMs);
    void rollGroup(const std::vector<RefId>& refs);
    void rollPlaced(RefId ref, const RefInfo& info);
    bool ignored(RefId ref) const;

    SyncConfig cfg_;
    ISpawnEngine& eng_;
    ISpawnNet& net_;
    ISpawnHost* host_;
    std::unique_ptr<sp::SiteRegistry> registry_;     // host only
    sp::SiteView view_;
    sp::RollTracker tracker_;
    RollDetector det_;
    std::function<bool(RefId)> ignore_;
    std::map<RefId, NetId> bound_;                   // refs this machine bound to issued / host ids
    std::set<RefId> hostScript_;                     // host: created refs a script spawned (identity ids, announced by the capture hub)
    std::set<RefId> heldRefs_;                       // refs hidden while a verdict is pending
    std::set<RefId> rolledPlaced_;                   // placed refs already rolled this world (a re-track does not roll again)
    std::set<PlayerId> joined_;                      // host
    std::map<PlayerId, uint32_t> mismatchStreak_;    // host: consecutive digest mismatches per client
    std::map<PlayerId, uint64_t> lastHealMs_;
    uint64_t wall_ = 0, game_ = 0, lastDigestMs_ = 0, nextEnforceMs_ = 0, nextDetectMs_ = 0;
    size_t enforceCursor_ = 0;
    bool hostUp_ = false;                            // client
    Stats stats_;
};

}  // namespace nvcoop::spawn
