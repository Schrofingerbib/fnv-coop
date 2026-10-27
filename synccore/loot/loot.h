// Loot, containers, dropped items and physics clutter (FULL-SYNC-PLAN 3.5). Game-free product code in synccore, reused by plugin/loot.
//
// Rules (docs/tools/loot.md has the long form):
//   * Container and corpse contents are SHARED state. The host is the owner of that state. Open, take and put are INTENTS sent to the host;
//     the machine that acts applies them optimistically (the engine already did) and ROLLS BACK when the host refuses.
//   * One "in use" lock per container: the container menu behaves like a barter menu. A second opener is told Busy. The lock has a lease
//     (a holder that vanished does not block the container for ever) and goes when the holder leaves.
//   * Leveled loot is resolved ONCE, by whoever resolves it first (the owner of the cell or of the dying actor, at cell load / corpse
//     creation), and the REAL items are sent. Everybody else replaces what their own engine rolled by the shared contents.
//   * A dropped item (xNVSE OnDrop) becomes a networked world ref with a host-issued net id. A pickup is a CLAIM: first claim wins; a loser
//     is told "already taken" and rolls back. Placed items (no host id) are claimed the same way, with a tombstone so they stay taken.
//   * Every transfer is an idempotent transaction with a per-sender txn id. A replay returns the cached result and changes nothing.
//     The container digest is compared when the container closes; a mismatch heals the closer's copy and is counted.
//   * Physics clutter: only the machine that moves an object publishes it, only while it moves, plus one final "at rest" snapshot. The
//     host judges owner / epoch / seq and relays. A resting object costs nothing.
//   * Player inventories stay per player. Nothing in this module ever sends or applies a player's inventory as such: the player's side of a
//     transfer is local (the engine did it) and is only corrected by a rollback.
//
// Parts:
//   LootHost     host only. Table of containers and world items, locks, idempotency ledger, claim arbitration, clutter owners, digest, save/load.
//   LootClient   every machine, the host's own too (its requests are delivered to LootHost in-process). Mirrors the table, remembers its
//                pending requests, resends them, and turns each answer into engine ACTIONS (rollback, replace contents, bind a dropped ref ...).
// Nothing here touches a clock, a socket or the engine. Time is passed in (wall milliseconds).
#pragma once
#include <cstdint>
#include <deque>
#include <functional>
#include <map>
#include <set>
#include <string>
#include <variant>
#include <vector>
#include "fnvcoop/types.h"

namespace fnvcoop { class Archive; }

namespace fnvcoop::loot {

using fnvcoop::FormRef;
using fnvcoop::PlayerId;
using fnvcoop::kHostPlayer;
using fnvcoop::kNoPlayer;

using NetId = uint32_t;
using CellId = uint32_t;

constexpr uint8_t kChannelLoot = 0xB1;            // first byte of every datagram of this module (docs/protocol.md section 2)
constexpr uint8_t kLootWireVersion = 2;   // 2: ReqClaim and MItemGone carry the placed item's own ref (a client cannot find a placed world item from its net id alone)
constexpr NetId kItemIdBase = 0xFFB00000u;        // host-issued ids of dropped world items: [kItemIdBase, kItemIdEnd]
constexpr NetId kItemIdEnd = 0xFFBFFFFFu;         // (spawn-arb issues [0xFFC00000, 0xFFFFFFFF]; the host engine's own 0xFF ids count up from 0xFF000000)
constexpr size_t kMaxStacks = 512;                // distinct bases in one container
constexpr int32_t kMaxCount = 10000000;           // one stack
constexpr size_t kMaxTableRecords = 1u << 20;     // hostile-input bound for a table transfer
constexpr size_t kMaxTextLen = 255;

inline bool isIssuedItemId(NetId id) { return id >= kItemIdBase && id <= kItemIdEnd; }

struct Vec3 {
    float x = 0, y = 0, z = 0;
    friend bool operator==(const Vec3& a, const Vec3& b) { return a.x == b.x && a.y == b.y && a.z == b.z; }
};
float distance(const Vec3& a, const Vec3& b);

// ---------------------------------------------------------------------------------------------------------------- contents
// Item base form -> count. Counts above zero only (normalize() drops the rest). Per-instance data (condition, mods) is NOT part of a stack:
// it travels as `meta` on a dropped item only (docs/tools/loot.md, honest limits).
using Items = std::map<FormRef, int32_t>;
void normalize(Items& items);                     // drop non-positive counts
bool validItems(const Items& items);              // sizes and counts within the caps, no empty plugin name
uint64_t digestOf(const Items& items);            // order independent, zero counts ignored
struct ItemDiff { FormRef base; int32_t a = 0, b = 0; };
std::vector<ItemDiff> diffItems(const Items& a, const Items& b);   // every base whose count differs
std::string describe(const Items& items);        // "base x3, base x1" for logs

enum class ContainerKind : uint8_t { Container = 0, Corpse = 1, Count_ };
const char* toString(ContainerKind k);

struct ContainerRec {
    NetId id = 0;                 // placed container: the ref's net id (every machine derives it). corpse: the actor's net id
    ContainerKind kind = ContainerKind::Container;
    CellId cell = 0;
    Vec3 pos;
    Items items;
    uint32_t rev = 0;             // +1 per content change (host)
    PlayerId holder = kNoPlayer;  // who has it open (host: authoritative; others: mirrored)
    uint32_t lockSeq = 0;         // +1 per lock change, orders MLock messages
};

struct WorldItem {
    NetId id = 0;                 // dropped: issued by the host. placed: the ref's net id
    FormRef base;
    int32_t count = 1;
    uint32_t meta = 0;            // opaque per-instance data the engine glue wants carried (e.g. a condition bucket)
    CellId cell = 0;
    Vec3 pos, rot;
    PlayerId dropper = kNoPlayer; // dropped items: who dropped it
    uint32_t dropTxn = 0;         // ... and with which request (the dropper binds its own local ref to the issued id)
};

enum class OpKind : uint8_t { Register = 0, Open, Take, Put, Close, Drop, Claim, Count_ };
const char* toString(OpKind k);

enum class Status : uint8_t {
    Ok = 0,
    Busy,              // somebody else has the container open (Result.other)
    NotHolder,         // the sender does not hold the container
    Insufficient,      // the container does not have that many
    Unknown,           // the host has no such container / item
    AlreadyTaken,      // a pickup claim lost (Result.other = the winner)
    AlreadyResolved,   // the container was resolved already: the sender must adopt the shared contents (Result.rec)
    DigestMismatch,    // close: the closer's copy differed from the shared contents (healed through Result.rec)
    BadRequest,        // malformed, caps exceeded
    Stale,             // a replay older than the idempotency window
    Count_
};
const char* toString(Status s);

// ---------------------------------------------------------------------------------------------------------------- messages
// client -> host
struct ReqRegister { uint32_t txn = 0; ContainerRec rec; };                  // "I resolved this container / corpse": rec.items are the real items
struct ReqOpen { uint32_t txn = 0; NetId cont = 0; bool hasAdopt = false; ContainerRec adopt; };   // adopt: unregistered container, my contents become the shared ones
struct ReqMove { uint32_t txn = 0; NetId cont = 0; FormRef base; int32_t count = 0; bool put = false; };   // take (put=false) / put
struct ReqClose { uint32_t txn = 0; NetId cont = 0; uint64_t digest = 0; };
struct ReqHold { NetId cont = 0; };                                          // keep-alive for the lock
struct ReqDrop { uint32_t txn = 0; FormRef base; int32_t count = 1; uint32_t meta = 0; CellId cell = 0; Vec3 pos, rot; };
struct ReqClaim { uint32_t txn = 0; NetId item = 0; bool hasAdopt = false; WorldItem adopt; FormRef ref; };   // adopt: a placed item the host has not heard of. ref (with adopt): the placed world ref itself, plugin:localId
struct ReqResync { NetId cont = 0; bool all = false; };
struct ReqDigest { uint64_t digest = 0; uint32_t containers = 0, items = 0; };
struct ReqTouch { NetId net = 0; Vec3 pos, rot; };                          // "I am moving this physics object": asks for clutter ownership
// host -> client
struct Result {
    uint32_t txn = 0;
    OpKind op = OpKind::Open;
    Status status = Status::Ok;
    NetId id = 0;                 // container / item the request was about
    uint32_t rev = 0;
    PlayerId other = kNoPlayer;   // Busy: the holder. AlreadyTaken: the winner
    FormRef base;                 // Claim Ok: what the item is
    int32_t count = 0;            // Take / Put / Claim: the amount the host used
    NetId newId = 0;              // Drop: the issued id
    bool hasRec = false;
    ContainerRec rec;             // open granted, or the authoritative state on a refusal
};
struct MContainer { ContainerRec rec; };                                     // a full record (resolve broadcast, resync, table)
struct MDelta { NetId cont = 0; uint32_t rev = 0; FormRef base; int32_t newCount = 0; };   // absolute count of one base after the change
struct MLock { NetId cont = 0; uint32_t lockSeq = 0; PlayerId holder = kNoPlayer; };
struct MItemNew { WorldItem item; };
struct MItemGone { NetId item = 0; PlayerId by = kNoPlayer; uint32_t txn = 0; FormRef ref; };   // ref: a PLACED item's world ref (empty for a dropped one: the engine knows it by its net id)
struct MContainerGone { NetId cont = 0; };                                   // the host forgot it (cell reset): the next resolve starts over
struct MTableBegin { uint32_t seq = 0; };
struct MTableEnd { uint32_t seq = 0; uint32_t containers = 0, items = 0; };  // ids not mentioned between Begin and End are removed from the view
struct MClutter { NetId net = 0; uint32_t epoch = 0; uint32_t seq = 0; Vec3 pos, rot; bool moving = false; };   // either direction; unreliable
struct MClutterOwn { NetId net = 0; PlayerId owner = kNoPlayer; uint32_t epoch = 0; Vec3 pos, rot; };

using Body = std::variant<ReqRegister, ReqOpen, ReqMove, ReqClose, ReqHold, ReqDrop, ReqClaim, ReqResync, ReqDigest, ReqTouch,
                          Result, MContainer, MDelta, MLock, MItemNew, MItemGone, MContainerGone, MTableBegin, MTableEnd, MClutter, MClutterOwn>;
const char* bodyName(const Body& b);
bool isRequest(const Body& b);                    // sent by a client to the host
bool isUnreliable(const Body& b);                 // MClutter only

// ---------------------------------------------------------------------------------------------------------------- host
struct HostConfig {
    uint32_t lockLeaseMs = 600000;      // a holder that sends nothing (no op, no ReqHold) for this long loses the lock
    uint32_t ledgerWindow = 256;        // results remembered per sender for replays
    uint32_t recentTakenMax = 4096;     // taken dropped items remembered (answers a late loser with AlreadyTaken)
    uint32_t maxContainers = 65536;
    uint32_t maxItems = 16384;          // dropped items alive at once
    uint32_t maxPlacedTaken = 262144;   // tombstones of placed items that were picked up
    uint32_t maxTableTombstones = 4096; // tombstones (with their ref) pushed in one join table, lowest id first: a client that joins after a pickup removes the item too
    uint32_t restMs = 400;              // clutter: an owner silent this long is idle and may be taken over
    uint32_t healMinMs = 2000;          // a digest mismatch triggers at most one table push per sender in this time
    uint32_t maxDigestAgeMs = 0;        // unused (reserved)
};

struct HostStats {
    uint64_t requests = 0, replays = 0, stale = 0, wrongDirection = 0, badRequest = 0;
    uint64_t registered = 0, adopted = 0, alreadyResolved = 0;
    uint64_t opens = 0, busy = 0, locksExpired = 0, locksReleasedOnLeave = 0;
    uint64_t takes = 0, puts = 0, refused = 0, notHolder = 0, insufficient = 0;
    uint64_t closes = 0, closeMismatch = 0;
    uint64_t drops = 0, claimsWon = 0, claimsLost = 0, placedClaims = 0, claimsRepeated = 0;   // claimsRepeated: the same player claimed an item it already won (answered Ok, not AlreadyTaken)
    uint64_t externalSets = 0, externalRefused = 0, cellResets = 0;
    uint64_t digestsSeen = 0, digestMismatch = 0, healsSent = 0, resyncsAnswered = 0;
    uint64_t clutterTracked = 0, clutterRelayed = 0, clutterRejected = 0, clutterTakeovers = 0, clutterDenied = 0;
};

class LootHost {
public:
    struct Outgoing { PlayerId to = kNoPlayer; Body body; };
    using Out = std::vector<Outgoing>;

    explicit LootHost(HostConfig cfg = {});

    const HostConfig& config() const { return cfg_; }
    const HostStats& stats() const { return stats_; }

    // membership. Slot 0 (the host's own player) must be joined too: the host machine runs a LootClient like everybody else.
    void playerJoined(PlayerId p, Out& out);                 // resets that sender's ledger and sends the whole table
    void playerLeft(PlayerId p, uint64_t now, Out& out);     // releases its locks and clutter
    bool isJoined(PlayerId p) const { return joined_.count(p) != 0; }

    // One request from slot `from` (the transport peer, never a claimed value). Anything that is not a request is ignored (counted).
    void onRequest(PlayerId from, const Body& b, uint64_t now, Out& out);

    // The host's own engine is the authority for what scripts do to a container nobody holds (RefAddItem effects, quest rewards ...).
    // Returns false when the container is unknown or held.
    bool externalSet(NetId cont, const Items& items, uint64_t now, Out& out);
    // The host's cell reset: forget the unheld containers of a cell (and their lock state) so the next resolve starts over.
    void resetCell(CellId cell, Out& out);
    void tick(uint64_t now, Out& out);                       // lock leases

    const ContainerRec* container(NetId id) const;
    const WorldItem* item(NetId id) const;
    bool placedTaken(NetId id, PlayerId* by = nullptr) const;
    size_t containerCount() const { return containers_.size(); }
    size_t itemCount() const { return items_.size(); }
    const std::map<NetId, ContainerRec>& containers() const { return containers_; }
    const std::map<NetId, WorldItem>& items() const { return items_; }
    PlayerId clutterOwner(NetId net, uint32_t* epoch = nullptr) const;
    NetId nextIssuedId() const { return nextItemId_; }
    uint64_t digest() const;

    // host save sidecar: containers (with contents), placed tombstones, the id counter. Locks, ledgers and clutter are transient.
    std::vector<uint8_t> save() const;
    bool load(const std::vector<uint8_t>& bytes);

private:
    struct Ledger { std::deque<std::pair<uint32_t, Result>> done; uint32_t floor = 0; };
    struct Held { uint64_t lastMs = 0; };
    struct Clutter { PlayerId owner = kNoPlayer; uint32_t epoch = 0; uint32_t lastSeq = 0; uint64_t lastMs = 0; bool moving = false; Vec3 pos, rot; };

    bool cached(PlayerId from, uint32_t txn, Result& out);
    void remember(PlayerId from, uint32_t txn, const Result& r);
    void reply(PlayerId to, const Result& r, Out& out);
    void sendAll(const Body& b, Out& out, PlayerId except = kNoPlayer);
    void sendTable(PlayerId to, Out& out);
    ContainerRec* ensure(const ContainerRec& adopt, Out& out, PlayerId except);
    void release(ContainerRec& c, Out& out);
    void grant(ContainerRec& c, PlayerId to, uint64_t now, Out& out);
    void rememberTaken(NetId id, PlayerId by);

    void doRegister(PlayerId from, const ReqRegister& r, uint64_t now, Out& out);
    void doOpen(PlayerId from, const ReqOpen& r, uint64_t now, Out& out);
    void doMove(PlayerId from, const ReqMove& r, uint64_t now, Out& out);
    void doClose(PlayerId from, const ReqClose& r, uint64_t now, Out& out);
    void doDrop(PlayerId from, const ReqDrop& r, uint64_t now, Out& out);
    void doClaim(PlayerId from, const ReqClaim& r, uint64_t now, Out& out);
    void doTouch(PlayerId from, const ReqTouch& r, uint64_t now, Out& out);
    void doClutter(PlayerId from, const MClutter& m, uint64_t now, Out& out);
    void doDigest(PlayerId from, const ReqDigest& r, uint64_t now, Out& out);
    void doResync(PlayerId from, const ReqResync& r, Out& out);
    void claimClutter(NetId net, PlayerId owner, const Vec3& pos, const Vec3& rot, uint64_t now, Out& out);

    HostConfig cfg_;
    HostStats stats_;
    std::set<PlayerId> joined_;
    std::map<NetId, ContainerRec> containers_;
    std::map<NetId, Held> held_;
    std::map<NetId, WorldItem> items_;
    std::map<NetId, PlayerId> placedTaken_;
    std::map<NetId, FormRef> placedRef_;   // the world ref a tombstone belongs to (plugin:localId), so a join table can tell a late client which item to remove
    std::map<NetId, PlayerId> recentTaken_;
    std::deque<NetId> recentOrder_;
    std::map<PlayerId, Ledger> ledgers_;
    std::map<NetId, Clutter> clutter_;
    std::map<PlayerId, uint64_t> lastHeal_;
    NetId nextItemId_ = kItemIdBase;
    uint32_t tableSeq_ = 0;
};

// ---------------------------------------------------------------------------------------------------------------- client
enum class Notice : uint8_t { InUse = 0, AlreadyTaken, ContainerRefused, ContainerUnknown, Count_ };
const char* toString(Notice n);

// What the engine glue must do. Every action is idempotent and safe to fail (a ref that is not loaded here: nothing to do).
struct Action {
    enum Kind : uint8_t {
        PlayerAdjust,     // add `delta` of `base` to the LOCAL player's inventory (negative = remove). Rollback of an optimistic transfer
        ContainerSet,     // make the engine container `id` hold exactly `items`
        CloseMenu,        // close the container menu of `id`
        Notify,           // tell the player (`notice`)
        BindDrop,         // the local world ref `ref` IS item `id` (issued by the host)
        CreateItem,       // make a local world ref for `item` (somebody else dropped it, or a late join)
        RemoveItem,       // delete the local world ref of item `id` (it was taken). `placed` names the ref of a placed item (empty: a dropped item, found by `id`)
        SetPose,          // clutter `id`: show this pose (moving = interpolate / hold physics, !moving = final resting pose)
        ReadyToClose,     // container `id`: every pending op settled, read the engine contents and call LootClient::closeNow
        ClutterOwner,     // this machine now (owner=true) or no longer owns clutter `id`
    };
    Kind kind = PlayerAdjust;
    NetId id = 0;
    uint32_t ref = 0;     // BindDrop: the engine's local ref
    FormRef base;
    int32_t delta = 0;
    Items items;
    WorldItem item;
    FormRef placed;       // RemoveItem of a placed item: its world ref
    Notice notice = Notice::InUse;
    Vec3 pos, rot;
    bool flag = false;    // SetPose: moving. ClutterOwner: owner
};

// Tuning of the clutter sender. Units are game units and radians.
struct ClutterTuning {
    float posEps = 2.0f;           // movement below this since the last datagram is noise
    float rotEps = 0.05f;          // radians
    uint32_t minIntervalMs = 66;   // at most ~15 datagrams per second per object while it moves
    uint32_t restMs = 400;         // quiet for this long: one final "at rest" datagram, then nothing
};

struct ClientConfig {
    PlayerId self = kNoPlayer;
    uint32_t resendMs = 1500;           // an unanswered request is sent again after this long (same txn: the host replays its answer)
    uint32_t giveUpMs = 60000;          // ... and abandoned (rolled back, container resynced) after this long
    uint32_t holdEveryMs = 30000;       // lock keep-alive while a container is open
    uint32_t digestEveryMs = 2000;      // table digest to the host (it heals a mismatch)
    uint32_t resyncMinMs = 1000;        // a container resync is asked for at most this often
    bool eagerApply = true;             // a delta / record for a container that is not open here is applied to the engine copy
    uint32_t goneMemory = 4096;         // taken item ids remembered so a late "item created" is ignored
    ClutterTuning clutter;
};

struct ClientStats {
    uint64_t sent = 0, resent = 0, results = 0, duplicateResults = 0, staleResults = 0, gaveUp = 0, abandoned = 0;
    uint64_t opens = 0, busy = 0, takes = 0, puts = 0, rollbacks = 0, rollbackItems = 0;
    uint64_t contentsReplaced = 0, registers = 0, adopted = 0, closes = 0, closeMismatch = 0;
    uint64_t drops = 0, bound = 0, created = 0, removed = 0, claims = 0, claimsWon = 0, claimsLost = 0, claimsDuplicate = 0, removedPlaced = 0;   // claimsDuplicate: a pickup of an item whose claim is already in flight (not sent twice)
    uint64_t deltasApplied = 0, deltasStale = 0, gaps = 0, resyncsAsked = 0, tablesApplied = 0, tablesIncomplete = 0, digestsSent = 0, wrongDirection = 0;
    uint64_t clutterSent = 0, clutterApplied = 0, clutterStale = 0, clutterOwned = 0, strayReleases = 0, auditCorrections = 0;
};

class LootClient {
public:
    using Send = std::function<void(const Body&)>;
    LootClient(ClientConfig cfg, Send send);

    const ClientConfig& config() const { return cfg_; }
    const ClientStats& stats() const { return stats_; }
    void setSelf(PlayerId p) { cfg_.self = p; }

    // ---- link ----
    void hostLinkUp(uint64_t now);        // fresh session: txn counter and view restart, the host sends the table
    void hostLinkDown();                  // pending requests are abandoned (counted); the view is kept
    void worldReset();                    // a save was loaded: every engine ref is gone, forget the view (hostLinkUp fetches the table again)
    bool linkUp() const { return up_; }

    // ---- engine events (all on the machine's main thread) ----
    // A container / corpse was resolved here (cell load, corpse creation). `items` are the real items in the engine copy.
    void resolved(NetId cont, ContainerKind kind, CellId cell, const Vec3& pos, const Items& items, uint64_t now);
    // The local player opened the container menu. `items` is the engine copy at that moment.
    void opened(NetId cont, ContainerKind kind, CellId cell, const Vec3& pos, const Items& items, uint64_t now);
    // While the menu is open (every poll): the engine copy now. Differences against the last seen state become take / put requests.
    void observed(NetId cont, const Items& items, uint64_t now);
    // The menu closed. `items` is the engine copy now. The Close request goes out once every pending op of that container is settled.
    void closed(NetId cont, const Items& items, uint64_t now);
    // Answer to Action::ReadyToClose.
    void closeNow(NetId cont, const Items& items, uint64_t now);
    // After the glue executed actions on a container: the engine copy now, so the watcher does not read its own correction as a player op.
    void rebaseline(NetId cont, const Items& items);
    // Engine copy audit (the glue calls it round-robin for containers it has loaded and that are not open): when the engine copy differs from the
    // shared contents it is corrected. Returns true when a correction was queued.
    bool audit(NetId cont, const Items& engineItems, uint64_t now);
    // The local player dropped something (xNVSE OnDrop). `localRef` is the engine's ref of the dropped object.
    uint32_t dropped(uint32_t localRef, const FormRef& base, int32_t count, uint32_t meta, CellId cell, const Vec3& pos, const Vec3& rot, uint64_t now);
    // The local player picked a world item up. `item` = its net id (placed: derived, dropped: issued). The engine already gave the player the item.
    void pickedUp(uint32_t localRef, NetId item, const FormRef& base, int32_t count, uint32_t meta, CellId cell, const Vec3& pos, uint64_t now,
                  const FormRef& placedRef = FormRef());   // placedRef: a placed item's own ref (plugin:localId), so the others can find and remove it
    // Physics: samples of the clutter objects this machine can see moving (call every frame; the sender decides what is worth a datagram).
    struct Sample { NetId net = 0; Vec3 pos, rot; };
    void clutterSamples(const std::vector<Sample>& samples, uint64_t now);
    // The local player is moving a physics object (asks for ownership; idempotent)
    void touch(NetId net, const Vec3& pos, const Vec3& rot, uint64_t now);

    // ---- the host ----
    void onMessage(const Body& b, uint64_t now);
    void tick(uint64_t now);
    std::vector<Action> takeActions();

    // ---- queries ----
    const ContainerRec* container(NetId id) const;
    const WorldItem* item(NetId id) const;
    bool isOpen(NetId cont) const;
    bool holdsLock(NetId cont) const;
    // No menu / transfer of this container is in flight on this machine (nothing open, nothing waiting to close, no unanswered request): a menu-less
    // transfer (a take or put the engine did without the container menu) may start one. Used by plugin/loot (LootSync::screenTransfer).
    bool transferIdle(NetId cont) const;
    size_t pendingCount() const { return pending_.size(); }
    size_t pendingFor(NetId cont) const;
    uint64_t digest() const;
    bool owns(NetId clutter) const;
    std::string debugLocal(NetId cont) const;       // "open=1 granted=0 ..." for logs and tests
    void containerIds(std::vector<NetId>& out, size_t max) const {   // every container of the shared view (the glue looks at those that have a ref on this machine)
        out.clear();
        for (const auto& kv : containers_) { if (out.size() >= max) break; out.push_back(kv.first); }
    }
    size_t containerCount() const { return containers_.size(); }
    size_t itemCount() const { return items_.size(); }

private:
    struct Pending {
        Body body;                    // what was sent (replayed verbatim)
        OpKind op = OpKind::Open;
        NetId id = 0;
        FormRef base;
        int32_t count = 0;
        bool put = false;
        uint32_t ref = 0;             // Drop: the local ref
        uint64_t firstMs = 0, lastMs = 0;
        uint32_t tries = 1;
        bool adoptRetried = false;
    };
    struct DeferredPick { FormRef base; int32_t count = 1; uint32_t meta = 0; CellId cell = 0; Vec3 pos; };
    struct Local {
        bool open = false;
        bool granted = false;
        bool closeWanted = false;
        bool closeSent = false;
        bool readyAsked = false;
        Items baseline;               // the engine copy as of the last observation / correction
        uint64_t lastHoldMs = 0;
    };

    uint32_t nextTxn() { return ++txn_; }
    uint32_t issue(Body body, uint32_t txn, OpKind op, NetId id, uint64_t now, Pending p);
    void sendBody(const Body& b);
    void push(Action a) { actions_.push_back(std::move(a)); }
    Items expected(NetId cont, const Items& authoritative) const;     // authoritative + the effect of this container's pending take / put
    void replaceContents(NetId cont, const Items& want);
    void onResult(const Result& r, uint64_t now);
    void rollbackMove(const Pending& p);
    void applyRecord(const ContainerRec& rec, bool fromTable, bool eager, uint64_t now);
    void applyDelta(const MDelta& d, uint64_t now);
    void askResync(NetId cont, uint64_t now);
    void forgetItem(NetId id, PlayerId by, const FormRef& placedRef);
    void maybeReadyToClose(NetId cont);
    void reconcileLocal(uint64_t now);
    void bindDrop(const WorldItem& it);
    void onClutter(const MClutter& m);
    void onClutterOwn(const MClutterOwn& m);

    ClientConfig cfg_;
    Send send_;
    ClientStats stats_;
    bool up_ = false;
    uint32_t txn_ = 0;
    std::map<uint32_t, Pending> pending_;
    std::map<NetId, ContainerRec> containers_;
    std::map<NetId, WorldItem> items_;
    std::map<NetId, Local> local_;
    std::map<uint32_t, uint32_t> dropRef_;          // drop txn -> local ref waiting for its issued id
    std::map<NetId, uint32_t> boundEarly_;          // issued id -> local ref (the Result came before MItemNew)
    std::map<uint32_t, DeferredPick> deferredPickup_;   // local ref of a drop that is not bound yet -> picked up already
    std::set<NetId> materialized_;                  // items that already have a local world ref (BindDrop / CreateItem was emitted)
    uint64_t lastRxMs_ = 0;
    std::set<NetId> gone_;
    std::deque<NetId> goneOrder_;
    std::map<NetId, uint64_t> lastResync_;
    std::map<NetId, uint64_t> strayAskMs_;
    std::vector<Action> actions_;
    // table transfer in progress
    bool inTable_ = false;
    uint64_t tableStartMs_ = 0;
    std::set<NetId> tableContainers_, tableItems_;
    uint64_t lastDigestMs_ = 0;
    // clutter
    struct Own { uint32_t epoch = 0; uint32_t seq = 0; Vec3 lastPos, lastRot; bool sentOnce = false; uint64_t lastSendMs = 0, lastMoveMs = 0; bool moving = false; };
    struct Seen { uint32_t epoch = 0; uint32_t seq = 0; PlayerId owner = kNoPlayer; };
    std::map<NetId, Own> owned_;
    std::map<NetId, Seen> seenClutter_;
    std::map<NetId, uint64_t> touchedMs_;
};

}  // namespace fnvcoop::loot
