// One simulated peer: a World replica, an ownership view (the host also runs the OwnershipService), the live-layer
// protocol, a small reliable-ordered link over a lossy datagram transport, and a deterministic NPC AI for the actors
// it owns. No threads, no wall clock: the harness feeds bytes in (receive), advances time (tick) and carries the
// outbox to whatever transport it uses (InMemoryNetwork in Cluster, T2b's fuzz links, ...).
#pragma once
#include <deque>
#include <map>
#include <memory>
#include <set>
#include <string>
#include <vector>
#include "fnvcoop/engine/faults.h"
#include "fnvcoop/engine/messages.h"
#include "fnvcoop/engine/observer.h"
#include "fnvcoop/engine/world.h"
#include "fnvcoop/ownership.h"
#include "fnvcoop/rng.h"
#include "fnvcoop/transport.h"

namespace fnvcoop::engine {

struct MachineConfig {
    PlayerId self = kHostPlayer;
    uint64_t seed = 1;
    WorldSpec spec;
    OwnershipConfig own;
    uint32_t poseMs = 50;          // player pose publish interval (20 Hz)
    uint32_t snapshotMs = 50;      // owner snapshots near players (20 Hz)
    uint32_t farSnapshotMs = 200;  // owner snapshots far from every player (5 Hz)
    uint32_t settleMs = 300;       // actor idle this long -> one reliable settle snapshot
    uint32_t ownEvalMs = 250;      // host ownership / respawn evaluation
    uint32_t digestMs = 600;       // digest report interval
    uint32_t rtoMs = 120;          // reliable link retransmit timeout
    float aggroRange = 3000, meleeRange = 150, npcSpeed = 300, wanderSpeed = 80;   // units, units per second
    int32_t npcDamage = 5;
    uint32_t npcAttackMs = 1000;
    // hit sanity checks done by the victim's owner (FULL-SYNC-PLAN 3.4)
    float maxWeaponRange = 6000, rangeSlack = 700, impactSlack = 400;
    int32_t maxDamage = 500;
    uint32_t maxHitAgeMs = 30000;   // a hit stamped older than this is stale (combat-core default is 3 s; the model rides out the long outages of the hell profile, where a reliable hit still has to land once it arrives)
    uint32_t maxHitsPerSec = 20;
    combat::XpMode xpMode = combat::XpMode::KillingBlow;   // kill XP: the killing blow, or an equal party share
    uint32_t holdAiMaxMs = 8000;   // safety net: a new owner that committed without the old owner's final state waits at most this long before it drives the AI
};

struct Outgoing { PeerId to = 0; ITransport::Channel ch = ITransport::Channel::Reliable; Bytes data; };

struct MachineStats {
    uint64_t framesBadCrc = 0, framesStaleConn = 0, linkDuplicates = 0, linkRetransmits = 0, droppedStaleSession = 0, undecodable = 0;
    uint64_t hitsApplied = 0, hitsRejected = 0, hitsForwarded = 0, hitDuplicates = 0;
    uint64_t pickupsWon = 0, pickupsLost = 0, rollbacks = 0, dropsDeduped = 0;
    uint64_t containerOps = 0, containerRefused = 0, closeDigestMismatch = 0;
    uint64_t spawns = 0, spawnRollsIgnored = 0, spawnRollsRepeated = 0;
    uint64_t snapshotsApplied = 0, snapshotsDropped = 0, eventsApplied = 0, eventsDuplicate = 0;
    uint64_t fxApplied = 0, fxDuplicate = 0;
    uint64_t digestsIgnored = 0, repairsSent = 0, repairsApplied = 0, repairKeys = 0, ownerDiffsSkipped = 0;
    uint64_t doubleApplied = 0;      // an id the machine had already applied was applied again (must stay 0)
    uint64_t breaches = 0;           // a rule the protocol enforces was broken and the break took effect (only the test faults get here; must stay 0)
    uint64_t resyncs = 0, hostLoads = 0, finalSnaps = 0, acks = 0;
    // combat-core
    uint64_t shotsFired = 0, shotsSeen = 0, visualShotsDealtNothing = 0, blastsDeclared = 0, blastsSeen = 0, blastHitsSent = 0;
    uint64_t limbsCrippled = 0, deathsDeclared = 0, deathsRefused = 0, killsCredited = 0;
};

class Machine {
public:
    explicit Machine(const MachineConfig& cfg);

    PlayerId self() const { return cfg_.self; }
    bool isHost() const { return cfg_.self == kHostPlayer; }
    bool synced() const { return synced_; }
    uint32_t sess() const { return sess_; }
    uint64_t now() const { return now_; }
    void setObserver(IEngineObserver* o) { obs_ = o; }

    // ---- wiring ----
    std::vector<Outgoing> takeOutbox();
    void peerConnected(PeerId p, uint32_t connEpoch);   // host: a client arrived; client: the host link is (re)established
    void peerDisconnected(PeerId p);
    void receive(PeerId from, const Bytes& data);        // one datagram (link frame)
    // Application-level delivery that skips the link layer (replay and injection). channel: 0 reliable, 1 unreliable.
    void receiveEnvelope(PeerId from, const Bytes& envelope, uint8_t channel);
    void tick(uint64_t nowMs);

    // ---- local actions (every one goes through doAction) ----
    bool doAction(const Action& i);
    bool move(CellId cell, Vec3 pos, float yaw = 0);
    bool attackActor(NetId victim, int32_t damage, Limb limb = Limb::Torso, Vec3 impact = {}, float weaponRange = 1500);
    bool pickup(NetId item);
    bool drop(const FormRef& base);
    bool openContainer(NetId c);
    bool closeContainer(NetId c);
    bool containerTake(NetId c, const FormRef& base, int32_t n);
    bool containerPut(NetId c, const FormRef& base, int32_t n);
    bool activate(NetId obj);
    bool useDoor(NetId obj);
    // combat-core: a projectile fired at an actor (flightMs later it lands: a hit, or a blast when explosive), and an explosion at a point.
    bool fireProjectile(NetId victim, int32_t damage, uint32_t flightMs, bool explosive);
    bool detonate(Vec3 center, float radius, int32_t damage);
    // host-only story actions (run through the existing Effect path)
    bool recruit(NetId actor, PlayerId owner);
    bool dismiss(NetId actor);
    bool setStage(const FormRef& quest, uint32_t stage);
    bool setLock(NetId obj, bool locked, int32_t level);
    bool killByStory(NetId actor);
    bool commitEffects(const std::vector<Effect>& fx);

    // ---- host save / load (FULL-SYNC-PLAN 3.9) ----
    Bytes hostSave() const;
    bool hostLoad(const Bytes& save);

    // ---- state for tests and oracles ----
    World& world() { return world_; }
    const World& world() const { return world_; }
    OwnershipView& view() { return view_; }
    const OwnershipView& view() const { return view_; }
    const OwnershipService* service() const { return svc_.get(); }
    const combat::DeathLedger& deaths() const { return deaths_; }
    const combat::ProjectileRegistry& projectiles() const { return projectiles_; }
    const combat::CombatRules& combatRules() const { return rules_; }
    const combat::ICombatWorld& combatWorld() const { return *cview_; }
    int32_t xpEarned() const { return xpEarned_; }
    OwnershipService* service() { return svc_.get(); }
    const MachineStats& stats() const { return stats_; }
    uint64_t healCount() const { return stats_.repairsSent + stats_.repairsApplied; }
    uint64_t digest(Category c) const { return world_.digest(c); }
    // Items this machine's player carries.
    int64_t inventoryCount(const FormRef& base) const;
    std::map<FormRef, int64_t> inventoryTotals() const;
    std::vector<NetId> ownedActors() const;           // actors the view says this machine owns
    const std::vector<std::string>& repairLog() const { return repairLog_; }
    const std::vector<std::string>& rejectLog() const { return rejectLog_; }
    // Rules the protocol enforces (validation, ownership, locks, arbitration ...) that a test fault switched off AND whose absence changed the outcome:
    // "<name>: <detail>". The cluster oracle reports every one. Always empty without faults.
    const std::vector<std::string>& breaches() const { return breaches_; }
    bool handingOff(NetId id) const { return handingOff_.count(id) != 0; }
    size_t outboxSize() const { return outbox_.size(); }
    size_t unackedCount() const { size_t n = 0; for (auto& kv : links_) n += kv.second.unacked.size(); return n; }
    void setAiFrozen(bool f) { aiFrozen_ = f; }   // actors keep owner bookkeeping and settle, but no AI decisions (quiescence)

private:
    struct Unacked { uint64_t seq; Bytes frame; uint64_t sentAt; };
    struct Link {
        uint32_t connEpoch = 0;
        uint64_t nextSend = 1, expect = 1;
        std::deque<Unacked> unacked;
        std::map<uint64_t, Bytes> ahead;
        bool ackDirty = false;
    };
    struct Wander { uint64_t nextMs = 0; float dx = 0, dy = 0; };
    struct Mismatch { std::map<uint32_t, std::pair<uint64_t, uint64_t>> keys; };   // key -> {reporter's hash, host's hash} at the last report that differed

    // link
    void emitFrame(PeerId to, uint8_t type, uint64_t seq, const Bytes& payload, ITransport::Channel ch);
    void sendPayload(PeerId to, const Bytes& payload, bool reliable);
    void onFrame(PeerId from, const Bytes& data);
    void flushAcks();
    // envelopes
    void sendBody(Body b, PlayerId dest);              // client: always via the host; host: direct / fan-out
    void relay(const Envelope& e);                     // host: forward a client's broadcast to the other clients
    void dispatch(PeerId from, const Envelope& e);
    bool toHost(const Envelope& e) const { return e.dest == kHostPlayer; }

    // handlers
    void onFullState(const MFullState& m);
    bool onPose(const Envelope& e, const MPose& m);
    bool onVitals(const Envelope& e, const MVitals& m);
    bool onActorSnap(const Envelope& e, const MActorSnap& m);
    bool onActorEvent(const Envelope& e, const MActorEvent& m);
    void onHit(const Envelope& e, MHit h);
    void onOwn(const Envelope& e, const MOwn& m);
    void onEffects(const MEffects& m);
    void onDropReq(const Envelope& e, const MDropReq& m);
    void onItemCreated(const MItemCreated& m);
    void onPickup(const Envelope& e, const MPickup& m);
    void onItemTaken(const MItemTaken& m);
    void onContainerReq(const Envelope& e, const MContainerReq& m);
    void onContainerRes(const MContainerRes& m);
    void onContainerSync(const MContainerSync& m);
    void onSpawnRoll(const Envelope& e, const MSpawnRoll& m);
    void onSpawnActor(const MSpawnActor& m);
    void onSiteState(const MSiteState& m);
    void onActivate(const Envelope& e, const MActivate& m);
    void onDigest(const Envelope& e, const MDigest& m);
    void onRepair(const MRepair& m);

    // ownership glue
    void trackOwnerChange(const OwnMsg& m);            // host: the hysteresis oracle
    void emitOwn(const std::vector<OwnMsg>& out);      // host: apply to own view, run hooks, broadcast
    void onOwnApplied(const OwnMsg& m, ApplyResult r);
    std::vector<Presence> presenceFor(const ActorState& a) const;
    void evalOwnership();
    void maybeAck(NetId id);
    // simulation
    void aiStep(ActorState& a, uint32_t dtMs);
    void publishActor(ActorState& a, bool settle, bool final);
    void declareDeath(ActorState& a, const char* why);
    void applyDamage(ActorState& a, const combat::HitEvent& h);
    LootList rollLoot(Rng& r) const;
    void makeCorpse(ActorState& a, const LootList& loot);
    void applyHitToSelf(const Envelope& e, const MHit& h);
    // combat-core glue (machine_combat.cpp)
    void initCombat();                                   // rules from the config, the world view, empty ledgers
    void resetCombat();                                  // a new world (full state / host load): forget ids and flying shots
    combat::HitEvent toHitEvent(const MHit& h) const;
    MHit fromHitEvent(const combat::HitEvent& e) const;
    bool onProjectile(const Envelope& e, const MProjectile& m);
    bool onExplosion(const Envelope& e, const MExplosion& m);
    bool doProjectile(const Action& i);
    bool doExplode(const Action& i);
    void stepShots();                                    // projectiles that reach their target
    void declareBlast(const combat::ExplosionEvent& ev); // the source's owner: broadcast it and send one hit per victim to its owner
    void runBlastLocally(const combat::ExplosionEvent& ev);   // test fault: apply the blast to our own copies, owner or not
    void recordStoryDeath(NetId id, bool killed);        // the story host killed / resurrected an actor: keep the death ledger in step
    void creditKill(const MActorEvent& ev);              // kill XP: our own player earns its share, once per death
    // host world logic
    NetId hostSpawnAt(SpawnSiteState& site, PlayerId roller);
    void evalSites();
    void onActorDiedHost(NetId id);
    void pushRepair(PlayerId target, Category cat, const std::vector<uint32_t>& keys, const std::vector<uint32_t>& removes);
    MFullState buildFullState() const;
    void loadCellsFor(CellId cell);
    void publishPose(bool force);
    void refreshAi(NetId id);   // one actor: aiOn = we own it and are not handing it off
    bool drivesAi(const ActorState& a) const;   // we own it, are not handing it off, and no previous owner may still be driving it
    void releaseAiHold(NetId id);
    void breach(const std::string& name, const std::string& detail);
    void releaseHoldsFrom(PlayerId prev);   // the previous owner is gone: nothing can still be driving what it owned
    void refreshAiFlags();   // aiOn = we own it and are not handing it off
    void sendHit(const MHit& h, PlayerId dest);
    bool applyOnce(uint64_t key);                      // false if `key` was already applied (and the dedupe fault is off)
    uint64_t nextId() { return (static_cast<uint64_t>(cfg_.self) << 48) | (static_cast<uint64_t>(connEpoch_ & 0xFFFFu) << 32) | ++idCounter_; }
    uint32_t seqBase() const { return connEpoch_ * 100000u; }

    MachineConfig cfg_;
    Rng rng_;
    World world_;
    OwnershipView view_;
    std::unique_ptr<OwnershipService> svc_;   // host only
    IEngineObserver* obs_ = nullptr;
    MachineStats stats_;
    std::vector<Outgoing> outbox_;
    std::map<PeerId, Link> links_;
    bool synced_ = false;
    uint32_t sess_ = 1, connEpoch_ = 1;
    uint64_t now_ = 0, lastTick_ = ~0ull, idCounter_ = 0;
    bool poseDirty_ = true;
    bool aiFrozen_ = false;
    bool poseUnsettled_ = false;
    uint64_t lastPoseChangeMs_ = 0;
    std::map<NetId, OwnerEpoch> lastOwnedEpoch_;
    uint64_t nextPoseMs_ = 0, nextOwnEvalMs_ = 0, nextDigestMs_ = 0;
    uint32_t poseCounter_ = 0, vitalsCounter_ = 0;
    uint64_t fxSeq_ = 0, lastFxSeq_ = 0;
    std::vector<CellId> loaded_;
    std::set<uint64_t> applied_;                       // idempotency keys
    std::set<NetId> handingOff_;                       // actors we are giving away (stop AI, final state sent)
    std::set<NetId> acked_;
    // New owner of a forced commit (the old owner's final state never reached us): the old owner may still be driving the actor until it hears the
    // Transfer. We keep our AI off until its final state (or its departure) proves it stopped. id -> {previous owner, since ms}.
    std::map<NetId, std::pair<PlayerId, uint64_t>> awaitOld_;
    std::vector<NetId> diedAfterRelay_, ackAfterRelay_;
    std::map<NetId, Wander> wander_;
    std::map<NetId, uint64_t> lastMoveMs_, lastPublishMs_, lastAttackMs_, settledAt_;
    std::map<PlayerId, std::deque<uint64_t>> hitTimes_;
    std::map<uint64_t, NetId> pendingClaims_;          // claimId -> item (optimistic pickups awaiting the host)
    std::map<uint64_t, std::pair<FormRef, int32_t>> pendingPuts_;
    std::map<std::pair<PlayerId, uint8_t>, uint32_t> digestSeen_;   // host: newest digest report number judged per reporter and category
    uint32_t digestSeq_ = 0;                                         // client: report counter
    std::map<std::pair<PlayerId, uint8_t>, Mismatch> mismatch_;   // host: the keys that differed in the last report, per reporter and category
    std::set<NetId> rolledSites_;                      // client: sites we already reported for the current cell load
    std::set<uint64_t> dropOrigins_;
    std::vector<std::string> repairLog_, rejectLog_, breaches_;
    // combat-core
    struct Shot { uint64_t proj = 0; NetId victim = 0; int32_t damage = 0; bool explosive = false; uint64_t dueMs = 0; bool visual = false; };
    combat::CombatRules rules_;
    combat::HitValidator hits_;
    combat::DeathLedger deaths_;
    combat::ProjectileRegistry projectiles_;
    combat::ExplosionLedger explosions_;
    combat::XpLedger xpLedger_;
    std::unique_ptr<combat::ICombatWorld> cview_;
    std::vector<Shot> shots_;
    int32_t xpEarned_ = 0;
    std::set<NetId> xpPaidFor_;                          // actors whose death already paid this machine's player (oracle: never twice)
    std::map<NetId, uint64_t> ownChangedAt_;           // host: when each actor's owner last changed (the hysteresis oracle)
    uint32_t dropCounter_ = 0;
};

}  // namespace fnvcoop::engine
