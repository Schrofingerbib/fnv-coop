// Spawn-site arbitration (FULL-SYNC-PLAN 3.3). Game-free product code in synccore, reused by plugin/spawn.
//
// Problem: every machine's engine rolls spawns on its own. A leveled marker, an encounter zone, an ambient random encounter or a
// script's PlaceAtMe would appear once PER MACHINE, each with its own random pick, and a cell reset on one machine would bring back
// actors the party already killed. Rule: ONE owner per site rolls; the host decides whether the roll counts; everybody shows that one
// result.
//
//   SiteRegistry  host only. Site table, one owner per site (fnvcoop::OwnershipService, epochs), roll arbitration, net ids for created
//                 (0xFF) refs, clear / respawn timers (game time), permanent interiors, cell reset, digest, save/load.
//   SiteView      every machine (host too). Mirrors the host's site table; answers "may my engine's roll stand?" locally when the
//                 answer is already known, and tells the engine what to enforce (Directive).
//   RollTracker   the machine that rolled. Holds its pending rolls, retries lost reports, turns the result of the host's verdict into
//                 engine actions (bind to the issued net id, show, drop the local roll, rebase to the shared pick).
//
// Nothing here touches a clock, a socket or the engine. Time is passed in: wall milliseconds for ownership / retries, GAME milliseconds
// (host authoritative, advances with Wait/Sleep) for clear and respawn timers.
//
// Identity: placed refs (a leveled ACHR/ACRE in the world, a marker's actor) have a net id every machine derives the same way
// (plugin/actors deriveNetId). Created refs (0xFF prefix) get a SERVER-ISSUED id: a client's own 0xFF id means nothing on another
// machine. Issued ids live in [kIssuedBase, 0xFFFFFFFF] so they can never collide with the ids the host engine hands out itself (those
// start at 0xFF000000 and count up); a host roll keeps its own engine id (identity), like script spawns always did.
#pragma once
#include <cstdint>
#include <deque>
#include <functional>
#include <map>
#include <memory>
#include <set>
#include <string>
#include <vector>
#include "fnvcoop/effect.h"
#include "fnvcoop/ownership.h"
#include "fnvcoop/types.h"

namespace fnvcoop { class Archive; }

namespace fnvcoop::spawn {

using fnvcoop::FormRef;
using fnvcoop::OwnerEpoch;
using fnvcoop::PlayerId;
using fnvcoop::kHostPlayer;
using fnvcoop::kNoPlayer;

using SiteId = uint32_t;
using NetId = uint32_t;
using CellId = uint32_t;

constexpr uint8_t kChannelSpawn = 0xA5;           // first byte of every datagram of this module (docs/protocol.md section 2)
constexpr uint8_t kSpawnWireVersion = 1;
constexpr NetId kIssuedBase = 0xFFC00000u;        // server-issued ids for created refs rolled by a CLIENT
constexpr size_t kMaxMembers = 24;                // actors one roll may create
constexpr float kAmbientGrid = 2048.0f;           // Ambient sites: area key = cell + this grid
constexpr float kAmbientMerge = 1500.0f;          // Ambient rolls closer than this (same cell) are the same encounter

inline bool isCreatedId(NetId id) { return (id >> 24) == 0xFF; }
inline bool isIssuedId(NetId id) { return id >= kIssuedBase; }

enum class SiteKind : uint8_t {
    Placed = 0,      // a placed leveled actor / creature (ACHR/ACRE with a leveled base): the ref exists on every machine, the PICK is rolled
    Marker = 1,      // a spawn marker / XMarker that spawns created actors
    Encounter = 2,   // an encounter zone's spawn
    Script = 3,      // PlaceAtMe from a script that more than one machine can run (GuestLocalRun, object scripts)
    Ambient = 4,     // an engine-made random encounter with no known site form: keyed by cell + area
    Count_
};
const char* toString(SiteKind k);

enum class SiteState : uint8_t {
    Unrolled = 0,     // nobody has rolled yet
    Spawned = 1,      // rolled; at least one member is alive
    Cleared = 2,      // every member died (or the roll was "nothing"); stays so until the respawn time (never, for a permanent site)
    Respawnable = 3,  // the respawn time has passed (or the host reset the cell): the owner's next roll counts
};
const char* toString(SiteState s);

enum class Verdict : uint8_t {
    Accept = 0,
    NotOwner,          // the sender is not the owner of the site
    StaleEpoch,        // the sender is the owner, but rolled under an older ownership epoch
    AlreadySpawned,    // the site already has a live roll: show that one (verdict carries its members and pick)
    ClearedPermanent,  // a cleared interior stays cleared: the sender's engine respawned it on its own
    NotDue,            // cleared, respawn time not reached
    BadReport,         // malformed, spoofed or id-colliding report
    Count_
};
const char* toString(Verdict v);

struct Vec3 {
    float x = 0, y = 0, z = 0;
    friend bool operator==(const Vec3& a, const Vec3& b) { return a.x == b.x && a.y == b.y && a.z == b.z; }
};

// What the machine that sees the site knows about it. `id` must be the same on every machine (siteIdOf).
struct SiteDef {
    SiteId id = 0;
    SiteKind kind = SiteKind::Placed;
    FormRef anchor;          // the marker / zone / placed ref / script form this site is made of (empty for Ambient)
    FormRef cellForm;        // the cell form (SpawnRef.cell); may be empty when only `cell` is known
    CellId cell = 0;         // cell id used for presence (same space as plugin/actors CellId)
    Vec3 pos;
    bool interior = false;   // a cleared interior stays cleared
    uint32_t respawnHours = 0;   // 0 = registry default
    friend bool operator==(const SiteDef& a, const SiteDef& b) {
        return a.id == b.id && a.kind == b.kind && a.anchor == b.anchor && a.cellForm == b.cellForm && a.cell == b.cell && a.pos == b.pos &&
               a.interior == b.interior && a.respawnHours == b.respawnHours;
    }
};

// Stable site ids: the same inputs give the same id on every machine. Never 0.
SiteId siteIdOf(SiteKind kind, const FormRef& anchor, CellId cell = 0, int32_t qx = 0, int32_t qy = 0);
SiteId ambientSiteId(CellId cell, float x, float y);

// One actor a roll produced.
struct Member {
    NetId netId = 0;         // placed: derived from the ref (reporter sets it). created: 0 in a CLIENT's report (issued by the host); a host
                             // report carries the host engine's own 0xFF id
    bool created = false;    // created (0xFF) ref: other machines get it from a SpawnRef; false = a placed ref every machine has
    bool dead = false;       // registry state (never set by a reporter)
    uint32_t handle = 0;     // the reporter's local handle for this actor; echoed in the verdict so it can adopt its own ref
    FormRef base;            // the base form the reporter's engine ROLLED (a real npc/creature, never a leveled list)
    CellId cell = 0;
    Vec3 pos;
    friend bool operator==(const Member& a, const Member& b) {
        return a.netId == b.netId && a.created == b.created && a.dead == b.dead && a.handle == b.handle && a.base == b.base && a.cell == b.cell && a.pos == b.pos;
    }
};

struct RollReport {
    SiteDef def;
    PlayerId roller = kNoPlayer;
    OwnerEpoch epoch = 0;        // the site epoch the roller believes it holds (0 = it does not know yet)
    uint32_t rollId = 0;         // per roller, idempotency key (a replay of the same rollId never spawns twice)
    bool none = false;           // the roll decided "nothing spawns this time" (chance none): shared like any other result
    FormRef pick;                // the leveled pick for the whole group (empty if none)
    std::vector<Member> members;
};

struct VerdictMsg {
    SiteId site = 0;
    uint32_t rollId = 0;
    Verdict verdict = Verdict::BadReport;
    OwnerEpoch epoch = 0;        // the site's current epoch
    PlayerId owner = kNoPlayer;  // the site's current owner
    uint32_t generation = 0;
    bool none = false;
    FormRef pick;
    std::vector<Member> members; // Accept: the roller's members in the same order, with net ids. Rejects: the site's current members
};

// The host's authoritative site record, sent to everybody on every change (and as the join table).
struct SiteMsg {
    SiteDef def;
    SiteState state = SiteState::Unrolled;
    uint32_t generation = 0;     // +1 on every accepted roll
    uint32_t rev = 0;            // +1 on every change; the view applies only newer revs
    bool none = false;
    FormRef pick;
    uint64_t clearedAtGame = 0, respawnAtGame = 0;   // game ms; respawnAt 0 = never (permanent)
    std::vector<Member> members;
};

// What an engine must enforce for a site (idempotent; derived from the view, so it can be replayed on every cell load).
struct Directive {
    enum Kind : uint8_t {
        Rebase = 1,      // placed ref: use `base` (the owner's pick) and make sure the ref is enabled
        EnsureDead = 2,  // placed ref: it must be dead (the party killed it; a local cell reset brought it back)
    };
    Kind kind = Rebase;
    NetId netId = 0;
    FormRef base;
    friend bool operator==(const Directive& a, const Directive& b) { return a.kind == b.kind && a.netId == b.netId && a.base == b.base; }
};

struct PlayerLoc {
    bool present = false;
    CellId cell = 0;
    Vec3 pos;
    std::vector<CellId> adjacent;   // loaded neighbouring cells
    bool adjacentKnown = false;     // true: `adjacent` is the full list of loaded neighbours; false: unknown, so a roll's proof of a loaded cell is believed
};

// Server-issued ids for created refs.
class IdIssuer {
public:
    virtual ~IdIssuer() = default;
    virtual NetId next() = 0;                 // 0 = exhausted
    virtual void raiseFloor(NetId used) = 0;  // an id at or above kIssuedBase is in use (host load / baseline): never hand it out again
    virtual NetId peek() const = 0;
};
class RangeIssuer : public IdIssuer {
public:
    NetId next() override;
    void raiseFloor(NetId used) override;
    NetId peek() const override { return next_; }
private:
    NetId next_ = kIssuedBase;
};

struct Config {
    uint64_t respawnGameMs = 72ull * 3600 * 1000;   // how long a cleared EXTERIOR site stays cleared (game time). ASSUMED: FNV's cell respawn
                                                    // GMST is not read from the game yet (docs/tools/spawn-arb.md, open items)
    uint32_t ownEvalMs = 250;                       // host: re-decide site ownership this often
    uint32_t verdictCacheKeep = 64;                 // per roller, for idempotent replays
    float ambientMerge = kAmbientMerge;
    uint64_t evidenceMs = 180000;                   // a roll proves its sender has that cell loaded; that counts as "adjacent" presence for this long
    fnvcoop::OwnershipConfig own;
};

struct RegistryStats {
    uint64_t rolls = 0, accepted = 0, rejected = 0, duplicates = 0, spoofed = 0, badReports = 0;
    uint64_t notOwner = 0, staleEpoch = 0, alreadySpawned = 0, clearedPermanent = 0, notDue = 0;
    uint64_t spawnsCommitted = 0, idsIssued = 0, hostIds = 0;
    uint64_t sitesCleared = 0, sitesRespawnable = 0, cellResets = 0, ambientMerges = 0, ownershipChanges = 0;
};

// ---- host ---------------------------------------------------------------------------------------------------------------------
class SiteRegistry {
public:
    struct Out {
        std::vector<fnvcoop::OwnMsg> own;               // broadcast to everybody (apply to the host's own view too)
        std::vector<SiteMsg> sites;                     // broadcast to everybody
        struct V { PlayerId to; VerdictMsg msg; };
        std::vector<V> verdicts;                        // each to its roller (the host's own: handle locally)
        struct C { PlayerId origin; fnvcoop::SpawnRef spawn; };
        std::vector<C> spawns;                          // created members to commit as SpawnRef effects, in order
        void clear() { own.clear(); sites.clear(); verdicts.clear(); spawns.clear(); }
        bool empty() const { return own.empty() && sites.empty() && verdicts.empty() && spawns.empty(); }
    };

    explicit SiteRegistry(Config cfg = {}, std::shared_ptr<IdIssuer> issuer = nullptr);

    // ---- session ----
    void playerJoined(PlayerId p);
    void playerLeft(PlayerId p, uint64_t wallMs, Out& out);
    void setPlayerLoc(PlayerId p, const PlayerLoc& loc);

    // ---- time (game ms, host authoritative) ----
    void setGameTime(uint64_t gameMs) { game_ = gameMs; }
    uint64_t gameTime() const { return game_; }

    // ---- events ----
    // `from` is the sender the transport authenticated (a client's report with another roller id is spoofed). The verdict is appended to
    // out.verdicts (a replayed rollId gets the cached verdict again and spawns nothing).
    void onRoll(const RollReport& r, PlayerId from, uint64_t wallMs, Out& out);
    // A member died or was removed (host: from the actor layer's death callback). Idempotent.
    void onMemberDied(NetId id, uint64_t wallMs, Out& out);
    // The host's engine reset a cell: cleared exterior sites in it become respawnable. Permanent (interior) sites are NOT touched.
    void onCellReset(CellId cell, Out& out);
    // Story authority forces a respawn (e.g. a quest script resets an interior): works on permanent sites too.
    void forceRespawn(SiteId id, Out& out);
    // A host script's PlaceAtMe was captured (SpawnRef effect): track the actor as a member of a Script site so a cleared script site is
    // known and a second machine's roll of the same encounter is rejected. No commit (the effect already went out).
    void noteScriptSpawn(const SiteDef& def, const fnvcoop::SpawnRef& sp, uint64_t wallMs, Out& out);
    void tick(uint64_t wallMs, Out& out);   // ownership re-evaluation, respawn timers

    // ---- join / heal ----
    void snapshot(Out& out) const;          // the whole table: own (Grant per owned site) + one SiteMsg per rolled site
    bool siteMsg(SiteId id, SiteMsg& out) const;
    uint64_t digest() const;                // over (site, state, generation, member count) of rolled sites; equals SiteView::digest() when in sync

    // ---- host save / load (the table and the id counter; ownership is rebuilt) ----
    void save(fnvcoop::Archive& a) const;
    bool load(fnvcoop::Archive& a);
    void reset();

    // ---- queries ----
    SiteState stateOf(SiteId id) const;
    PlayerId ownerOf(SiteId id) const { return own_.ownerOf(id); }
    OwnerEpoch epochOf(SiteId id) const { return own_.epochOf(id); }
    bool findSite(SiteId id, SiteMsg& out) const { return siteMsg(id, out); }
    bool siteOfMember(NetId id, SiteId& out) const;
    size_t siteCount() const { return sites_.size(); }
    size_t liveMembers() const;
    const RegistryStats& stats() const { return stats_; }
    const Config& config() const { return cfg_; }
    IdIssuer& issuer() { return *issuer_; }
    const IdIssuer& issuer() const { return *issuer_; }

private:
    struct Site {
        SiteDef def;
        SiteState state = SiteState::Unrolled;
        uint32_t generation = 0, rev = 0;
        bool none = false;
        FormRef pick;
        uint64_t clearedAt = 0, respawnAt = 0;
        std::vector<Member> members;
        uint32_t spawnCount = 0;
    };
    Site* find(SiteId id) { auto it = sites_.find(id); return it == sites_.end() ? nullptr : &it->second; }
    const Site* find(SiteId id) const { auto it = sites_.find(id); return it == sites_.end() ? nullptr : &it->second; }
    Site* ensureSite(const SiteDef& def, uint64_t wallMs, Out& out);
    std::vector<fnvcoop::Presence> presenceFor(const Site& s) const;
    void evaluate(Site& s, uint64_t wallMs, Out& out);
    void autoCommit(Out& out, size_t from, uint64_t wallMs);
    void refreshDue(Site& s, Out& out);
    SiteMsg toMsg(const Site& s) const;
    void publish(Site& s, Out& out);
    void clearSite(Site& s, Out& out);
    void reply(PlayerId to, const Site* s, const RollReport& r, Verdict v, Out& out, bool cache);
    void setCached(PlayerId to, uint32_t rollId, const VerdictMsg& v);
    uint64_t respawnMsOf(const Site& s) const;
    bool alive(const Site& s) const;
    void indexMembers(Site& s);
    void dropIndex(Site& s);
    Site* findAmbientNear(const SiteDef& def);

    Config cfg_;
    std::shared_ptr<IdIssuer> issuer_;
    fnvcoop::OwnershipService own_;
    std::map<SiteId, Site> sites_;
    std::map<NetId, SiteId> memberSite_;
    std::map<PlayerId, PlayerLoc> locs_;
    std::map<PlayerId, std::deque<VerdictMsg>> verdicts_;
    std::map<PlayerId, std::map<CellId, uint64_t>> seen_;   // cells a player proved it has loaded by rolling there (wall ms): survives pose updates
    uint64_t lastWall_ = 0;
    std::map<PlayerId, uint32_t> maxRoll_;      // highest rollId seen per roller: anything older than the verdict cache window is a late duplicate
    std::set<PlayerId> joined_;
    uint64_t game_ = 0, nextEvalMs_ = 0;
    RegistryStats stats_;
};

// ---- every machine ------------------------------------------------------------------------------------------------------------
struct ViewStats {
    uint64_t sitesApplied = 0, sitesDuplicate = 0, sitesStale = 0;
    uint64_t localSuppressed = 0, localAdopted = 0, localSent = 0;
};

class SiteView {
public:
    explicit SiteView(PlayerId self = kNoPlayer) : own_(self), self_(self) {}
    void setSelf(PlayerId p) { self_ = p; own_.setSelf(p); }
    PlayerId self() const { return self_; }

    // The host's site record. Newer revs replace older ones; a replay or an older rev changes nothing.
    fnvcoop::ApplyResult apply(const SiteMsg& m);
    fnvcoop::ApplyResult apply(const fnvcoop::OwnMsg& m) { return own_.apply(m); }
    void clear() { sites_.clear(); own_.clear(); }

    // May a roll my engine just made stand? Answered locally only when the answer cannot change by waiting:
    //   Suppress  the site is cleared and not due (permanent or not): throw the roll away / keep the actors dead
    //   Adopt     the site already has a live roll: show that one (members + pick), throw mine away
    //   Send      everything else: hold the roll and ask the host
    enum class LocalKind : uint8_t { Send, Suppress, Adopt };
    struct LocalDecision {
        LocalKind kind = LocalKind::Send;
        Verdict why = Verdict::Accept;
        SiteMsg site;     // Suppress / Adopt: the record the decision is based on
    };
    LocalDecision localRoll(const RollReport& r);

    bool find(SiteId id, SiteMsg& out) const;
    SiteState stateOf(SiteId id) const;
    PlayerId ownerOf(SiteId id) const { return own_.ownerOf(id); }
    OwnerEpoch epochOf(SiteId id) const { return own_.epochOf(id); }
    bool iOwn(SiteId id) const { return self_ != kNoPlayer && own_.ownerOf(id) == self_; }
    // What this machine must enforce for the site right now (call after apply(), and again whenever the site's cell loads).
    std::vector<Directive> directivesFor(SiteId id) const;
    std::vector<SiteId> sitesInCell(CellId cell) const;
    std::vector<SiteId> ids() const;
    bool siteOfMember(NetId id, SiteId& out) const;     // linear scan: use for rare events (an actor loaded), not per frame
    uint64_t digest() const;
    size_t siteCount() const { return sites_.size(); }
    const ViewStats& stats() const { return stats_; }
    const fnvcoop::OwnershipView& ownership() const { return own_; }

private:
    fnvcoop::OwnershipView own_;
    PlayerId self_;
    std::map<SiteId, SiteMsg> sites_;
    ViewStats stats_;
};

// ---- the machine that rolled --------------------------------------------------------------------------------------------------
struct TrackerConfig {
    uint32_t resendMs = 1500;     // no verdict yet: send the report again
    uint32_t giveUpMs = 12000;    // still nothing: turn the roll off
    uint8_t maxStaleRetries = 2;  // a StaleEpoch verdict from the current owner re-rolls under the fresh epoch this many times
};

// One thing the engine must do.
struct Action {
    enum Kind : uint8_t {
        Bind = 1,        // local `handle` IS actor `netId` now (created ref: bind it in the spawn ledger, so the SpawnRef does not make a second one)
        Show = 2,        // release the hold on `handle`
        Drop = 3,        // delete the local created ref `handle` (this machine's roll is turned off; the owner's result is shown instead)
        Rebase = 4,      // placed ref `netId`: use `base` and enable it
        EnsureDead = 5,  // placed ref `netId` must be dead
        Resend = 6,      // send `report` to the host again
    };
    Kind kind = Show;
    uint32_t handle = 0;
    NetId netId = 0;
    FormRef base;
    RollReport report;
};

struct TrackerStats {
    uint64_t begun = 0, accepted = 0, rejected = 0, resent = 0, retried = 0, gaveUp = 0, abandoned = 0, unknownVerdicts = 0, adoptedBound = 0, droppedBound = 0;
};

class RollTracker {
public:
    explicit RollTracker(PlayerId self = kNoPlayer, TrackerConfig cfg = {}) : self_(self), cfg_(cfg) {}
    void setSelf(PlayerId p) { self_ = p; }

    // Registers a roll that is being sent to the host: stamps roller + rollId (the report is changed in place) and returns the rollId.
    uint32_t begin(RollReport& r, uint64_t nowMs);
    // `isBound(netId)`: a local object for that issued id already exists (its SpawnRef arrived first).
    std::vector<Action> onVerdict(const VerdictMsg& v, const std::function<bool(NetId)>& isBound, uint64_t nowMs);
    // The answer was found locally (SiteView::localRoll said Suppress / Adopt): turn the roll off the way a refusal would. Nothing is pending.
    std::vector<Action> refuse(const RollReport& r, const VerdictMsg& v);
    // A site record arrived: placed refs that were held because another machine owns the site are rebased / killed / left hidden now.
    std::vector<Action> onSite(const SiteMsg& m, uint64_t nowMs);
    std::vector<Action> tick(uint64_t nowMs);      // Resend actions for silent rolls, Drop/Show for expired ones
    std::vector<Action> abandon();                  // link lost / session over: nothing will answer
    size_t pending() const { return pending_.size(); }
    size_t waiting() const { return waiting_.size(); }
    bool hasPending(SiteId site) const;
    const TrackerStats& stats() const { return stats_; }

private:
    struct P { RollReport report; uint64_t firstAt = 0, lastSent = 0; uint32_t tries = 1; uint8_t retries = 0; };
    // A placed ref held back because another machine owns its site: shown (rebased) when the owner's record arrives, hidden for good if
    // that record does not include it, shown anyway after giveUpMs if nothing ever arrives.
    struct W { uint32_t handle = 0; NetId netId = 0; SiteId site = 0; uint64_t since = 0; };
    std::vector<Action> turnOff(const P& p, const VerdictMsg* v);
    std::vector<Action> giveUp(const P& p);
    PlayerId self_;
    TrackerConfig cfg_;
    uint32_t nextRoll_ = 1;
    std::map<uint32_t, P> pending_;
    std::vector<W> waiting_;
    TrackerStats stats_;
};

}  // namespace fnvcoop::spawn
