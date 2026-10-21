// areadigest: per-area desync detection and healing for the live layer (FULL-SYNC-PLAN section 4, roadmap step 7). Game-free, C++17.
//
//   Every kIntervalMs (2 s) each machine sends the HOST a digest of the areas (cells) it has loaded: per (cell, category) one order-independent
//   hash over the items in it. Categories: actors (alive, health bucket, position bucket, cell), containers (contents hash), objects (open /
//   lock / enabled / destroyed state) and one global story digest. The host compares with its own world:
//
//     digest equal              nothing to do
//     digest differs            host asks the peer for the full items of the mismatching groups (MItemsReq / MItems)
//     item differs materially   (hp more than a tolerance, a different alive flag, a position more than 2 m away, another cell ...)
//                               AND both sides have been quiet for settleMs (no change in flight)
//                               -> the AUTHORITY decides the value: the actor's owner (fetched from the owner when it is a third machine),
//                               the host for everything else. The authoritative values are pushed (MHeal) to the machine that is wrong, or
//                               applied on the host itself when the host is wrong.
//     heal target               re-checks that its own item did not change since it reported it (version), that no handoff is in flight and
//                               that the difference still exists, then applies the values. Position uses the rubber-band rules:
//                               more than 2 m off -> smooth; more than 10 m off or another cell -> snap.
//
//   Nothing is applied twice and nothing stale wins: a heal carries the target's item version and the owner epoch, and every heal is
//   idempotent (the second delivery finds nothing to change). A mismatch that comes back on the same key within a minute is logged as a bug
//   with both values (BugReport). Counters "heals per category per minute" feed the flight recorder and the desync dashboard
//   (tools/areadigest/dashboard.py); the target in tests is 0.
//
//   Story: the global story digest of the session shadow (the same incremental bucket hashes the flight recorder uses) rides in the same
//   message. A mismatch at the same applied seq asks the existing story repair (HostSession::requestDigest -> shard diff -> SyncRepair) to run
//   now, so story, quest and shared-world drift is healed by the machinery that already knows how; its repairs are counted here as story
//   heals. Containers and objects are "delegated" in the same way when their source says so (the story layer owns their effects).
//
// Status: unit-tested game-free only (docs/tools/area-desync.md). Nothing here has run in a game.
#pragma once
#include <array>
#include <cstdint>
#include <deque>
#include <functional>
#include <map>
#include <set>
#include <string>
#include <utility>
#include <vector>
#include "fnvcoop/shadow.h"
#include "fnvcoop/types.h"

namespace fnvcoop::areadigest {

struct MDigest;
struct MItemsReq;
struct MItems;
struct MHeal;
struct Envelope;

constexpr uint8_t kChannelAreaDigest = 0xDA;      // first byte of every datagram of this module (docs/protocol.md section 2)
constexpr uint8_t kAreaDigestWireVersion = 1;

using CellId = uint32_t;
using Key = uint64_t;          // actors: the live NetId; containers / objects: a stable 64-bit id of the reference

enum class Cat : uint8_t { Story = 0, Actors = 1, Containers = 2, Objects = 3, Count_ };
constexpr size_t kCats = static_cast<size_t>(Cat::Count_);
// Same numbering as flight::DigestCategory (kCatStory 0, kCatActors 1, kCatContainers 2, kCatObjects 3).
const char* toString(Cat c);
inline bool isAreaCat(Cat c) { return c == Cat::Actors || c == Cat::Containers || c == Cat::Objects; }

struct Vec3 { float x = 0, y = 0, z = 0; };

// One thing in an area, as one machine sees it.
struct Item {
    Key key = 0;
    Cat cat = Cat::Actors;
    CellId cell = 0;
    bool alive = true;              // actors: not dead. containers / objects: exists and is enabled (not destroyed)
    float hp = 0;                   // actors: current health
    Vec3 pos;                       // actors only
    uint32_t state = 0;             // containers: hash of the contents; objects: packed open / lock / enabled / destroyed state
    uint32_t settledMs = 0xFFFFFFFFu;   // ms since this item last changed here (0xFFFFFFFF = no change seen yet)
    uint32_t version = 0;           // local change counter (ChangeTracker); a heal is applied only if it still matches
    bool operator==(const Item& o) const {
        return key == o.key && cat == o.cat && cell == o.cell && alive == o.alive && hp == o.hp && pos.x == o.pos.x && pos.y == o.pos.y &&
               pos.z == o.pos.z && state == o.state && settledMs == o.settledMs && version == o.version;
    }
};

struct Config {
    PlayerId self = kHostPlayer;
    bool isHost = true;
    uint32_t intervalMs = 2000;          // digest cadence of every machine
    uint32_t settleMs = 1000;            // an item must have been quiet this long on BOTH sides before it is healed
    float unitsPerMetre = 70.0f;         // game units per metre (FNV: 70)
    float smoothMetres = 2.0f;           // actor more than this off: smooth towards the authoritative position
    float snapMetres = 10.0f;            // more than this off, or another cell: snap
    float posBucketMetres = 2.0f;        // position bucket of the digest (the smooth threshold, so every smooth-worthy error is visible)
    float hpBucket = 10.0f;              // health bucket of the digest (absolute health points)
    float hpTolerance = 5.0f;            // health differences up to this are not healed (smaller than the bucket: a boundary straddle is no desync)
    float moveEpsilon = 4.0f;            // ChangeTracker: position changes below this many units are not a change
    uint32_t collectCacheMs = 100;       // own items are re-collected at most this often
    uint32_t caseTimeoutMs = 3000;       // a diff exchange that is not finished after this long is dropped
    uint32_t backoffMs = 10000;          // a mismatch that explained itself as "nothing to heal" is not diffed again for this long (grows x2, capped)
    uint32_t backoffMaxMs = 40000;
    size_t maxGroupsPerCase = 8;
    size_t maxItemsPerReply = 1000;
    size_t maxHealsPerCase = 64;
    uint32_t healBudgetPerMin = 240;     // token bucket: heals (keys) per minute per machine of the host
    uint32_t repeatWindowMs = 60000;     // the same key healed again within this long is a repeated mismatch (logged as a bug)
    uint32_t repeatBugGapMs = 10000;     // at most one bug line per key per this long
    uint32_t storyRepairGapMs = 10000;   // the story repair is asked for at most this often per client
    size_t bugLimit = 64;                // BugReports kept
    bool compareStory = true;
};

// ---- hashing -----------------------------------------------------------------------------------------------------------------
// Hash of one item's compared values. An item that changed less than settleMs ago is in flux: only (cell, alive) are hashed, so a value that
// is still travelling does not make the digest differ on every round. Actors hash the health bucket and the position bucket, containers and
// objects hash `state`.
uint32_t valueHash(const Item& it, const Config& c);
struct GroupInfo { CellId cell = 0; Cat cat = Cat::Actors; uint32_t hash = 0, count = 0; };
// Order-independent group hashes (sum of bucketMix(key, valueHash)), one per non-empty (cell, category), sorted by (cell, cat).
std::vector<GroupInfo> groupsOf(const std::vector<Item>& items, const Config& c);

// Story digest of a session shadow: the incremental bucket hashes the flight recorder uses, folded to one value.
struct StoryDigest {
    bool ok = false;
    uint64_t hash = 0;
    uint32_t count = 0;
    uint64_t seq = 0;
    uint32_t epoch = 0;
};
StoryDigest storyDigestOf(const ShadowState& w, uint64_t seq, uint32_t epoch);

// ---- comparing and the rubber-band rules -----------------------------------------------------------------------------------
enum class RubberMode : uint8_t { None, Smooth, Snap };
// distance in game units; sameCell false = another cell. 0 .. smooth: None, smooth .. snap: Smooth, beyond or another cell: Snap.
RubberMode classifyOffset(float distUnits, bool sameCell, const Config& c);
float distance(const Vec3& a, const Vec3& b);

// What `other` has to change to match `authority`. `material` is false when the difference is within tolerance (a bucket-boundary straddle).
struct Diff {
    bool material = false;
    bool alive = false, hp = false, state = false, cell = false;
    RubberMode pos = RubberMode::None;
    float dist = 0;
};
Diff compareItems(const Item& authority, const Item& other, const Config& c);
std::string describe(const Item& it);

// ---- the smoother: closes a 2 m .. 10 m error without a visible pop --------------------------------------------------------
class Smoother {
public:
    void begin(uint64_t nowMs, const Vec3& from, const Vec3& to, const Config& c);
    bool active() const { return active_; }
    // Position for this frame; false once finished (out is then exactly the target, returned once).
    bool step(uint64_t nowMs, Vec3& out);
    void cancel() { active_ = false; }
    const Vec3& target() const { return to_; }
private:
    bool active_ = false;
    uint64_t t0_ = 0, dur_ = 1;
    Vec3 from_, to_;
};

// ---- the change tracker: fills settledMs and version for the raw items a source reads ---------------------------------------
class ChangeTracker {
public:
    explicit ChangeTracker(const Config& c = Config()) : cfg_(c) {}
    void setConfig(const Config& c) { cfg_ = c; }
    // Raw items (settledMs / version ignored) in, filled items out. Items not passed this time are forgotten.
    void update(std::vector<Item>& items, uint64_t nowMs);
    // One item (a fresh read of a single key): fills settledMs and version from what is remembered; false if the key is unknown.
    bool refresh(Item& it, uint64_t nowMs);
    void forget(Cat c, Key k) { st_.erase({c, k}); }
    size_t size() const { return st_.size(); }
private:
    struct St { Item last; uint64_t changedAt = 0; uint32_t version = 0; };
    bool changed(const Item& a, const Item& b) const;
    Config cfg_;
    std::map<std::pair<Cat, Key>, St> st_;
};

// ---- per-minute counters -----------------------------------------------------------------------------------------------------
// 60 one-second buckets. perMinute(now) = events in the last 60 s.
class RollingRate {
public:
    void add(uint64_t nowMs, uint32_t n = 1);
    uint32_t perMinute(uint64_t nowMs) const;
    uint64_t total() const { return total_; }
private:
    std::array<uint32_t, 60> b_{};
    uint64_t lastSec_ = 0, total_ = 0;
    bool any_ = false;
};

// ---- interfaces ----------------------------------------------------------------------------------------------------------------
struct OwnerView {
    bool known = false;
    PlayerId owner = kNoPlayer;
    uint32_t epoch = 0;
    bool pending = false;       // a handoff is in flight: nobody is authoritative right now
};

enum class HealResult : uint8_t { Applied, Delegated, Raced, Missing, Refused, NoDiff, Count_ };
const char* toString(HealResult r);

// One category's window onto the engine (or a fake). All calls on the game main thread.
class IAreaSource {
public:
    virtual ~IAreaSource() = default;
    virtual Cat category() const = 0;
    // Append this machine's items (settledMs and version filled, see ChangeTracker) and the cells it has loaded (also the empty ones).
    virtual void collect(uint64_t nowMs, std::vector<Item>& items, std::vector<CellId>& loadedCells) = 0;
    // A fresh read of one item. false = it is not here (any more).
    virtual bool find(Key key, Item& out, uint64_t nowMs) = 0;
    // Make the local item match `authoritative` as `plan` says (only the flagged fields). The core already checked version, owner and tolerance.
    virtual HealResult heal(const Item& authoritative, const Diff& plan, uint64_t nowMs) = 0;
    // Actors: who owns the actor on THIS machine's view. Everything else: unknown (the host is the authority).
    virtual bool owner(Key, OwnerView&) { return false; }
    // True: the effects of this category belong to another layer (the story layer). The host then asks IStorySource::requestRepair instead of
    // pushing values.
    virtual bool delegated() const { return false; }
    virtual void tick(uint64_t) {}      // every call of AreaSync::tick (smoothing steps)
};

class IStorySource {
public:
    virtual ~IStorySource() = default;
    virtual bool local(StoryDigest& out) = 0;                // this machine's story digest now (ok=false: unavailable)
    virtual void requestRepair(PlayerId /*client*/) {}       // host: run the story repair for this client now
    virtual uint64_t repairs() { return 0; }                 // host: monotonic count of story repairs sent (HostStats::desyncRepairs)
};

class IAreaNet {
public:
    virtual ~IAreaNet() = default;
    // Client: peer is always the host. Host: a joined client. `data` starts with kChannelAreaDigest. Reliable delivery.
    virtual void send(PlayerId peer, std::vector<uint8_t> data) = 0;
};

struct HealNote {
    Cat cat = Cat::Actors;
    Key key = 0;
    CellId cell = 0;
    PlayerId target = kNoPlayer;     // the machine whose state was wrong
    HealResult result = HealResult::Applied;
    std::string text;                // "actor 0x1234 cell 0x55: hp 40 -> 100"
};
struct BugReport {
    Cat cat = Cat::Actors;
    Key key = 0;
    CellId cell = 0;
    PlayerId peer = kNoPlayer;
    uint32_t count = 0;              // heals of this key inside the repeat window
    uint64_t firstMs = 0, lastMs = 0;
    std::string authority, other;    // both values, describe() format
};
class IAreaObserver {
public:
    virtual ~IAreaObserver() = default;
    virtual void onHeal(const HealNote&) {}
    virtual void onBug(const BugReport&) {}
};

// ---- counters -------------------------------------------------------------------------------------------------------------------
struct CatCounters {
    uint64_t groupsChecked = 0, groupsMismatch = 0;
    uint64_t itemsCompared = 0, falsePositive = 0, inFlight = 0, missing = 0, ownerSilent = 0, pendingHandoff = 0;
    uint64_t healsDecided = 0;       // keys the host decided to correct (any target)
    uint64_t healsApplied = 0;       // keys corrected on THIS machine (a heal that changed something here)
    uint64_t raced = 0, refused = 0, noDiff = 0, delegated = 0;
    uint64_t deferredBudget = 0;
    uint64_t repeats = 0;
};
struct Counters {
    std::array<CatCounters, kCats> cat;
    uint64_t digestsSent = 0, digestsReceived = 0, digestsRateLimited = 0, digestsOk = 0, digestsMismatch = 0;
    uint64_t casesOpened = 0, casesClosed = 0, casesTimedOut = 0, casesBusy = 0, backoffSkips = 0, strays = 0;
    uint64_t itemsSent = 0, healsSent = 0, healMessagesReceived = 0, healsRejected = 0;
    uint64_t storyOk = 0, storyMismatch = 0, storyInFlight = 0, storyEpochSkew = 0, storyRepairRequests = 0, storyRepairsSeen = 0;
    uint64_t badMessages = 0, spoofed = 0, unknownPeer = 0, wrongDirection = 0;
    uint64_t bugs = 0;
};

// ---- the engine --------------------------------------------------------------------------------------------------------------------
class AreaSync {
public:
    AreaSync(Config cfg, IAreaNet& net, IStorySource* story = nullptr);
    ~AreaSync();
    AreaSync(const AreaSync&) = delete;
    AreaSync& operator=(const AreaSync&) = delete;

    void addSource(IAreaSource* s);              // one per category; a second source for the same category replaces the first
    void setObserver(IAreaObserver* o) { obs_ = o; }
    const Config& config() const { return cfg_; }

    // Host: clients coming and going. Client: the link to the host.
    void playerJoined(PlayerId p);
    void playerLeft(PlayerId p);
    void hostConnected();
    void hostLost();

    void tick(uint64_t nowMs);
    void receive(PlayerId from, const uint8_t* data, size_t size);

    const Counters& counters() const { return c_; }
    uint32_t healsPerMinute(Cat c, uint64_t nowMs) const;
    uint64_t healsTotal(Cat c) const;
    uint32_t healsPerMinuteAll(uint64_t nowMs) const;
    uint64_t healsTotalAll() const;
    // One row per category for the flight recorder (FlightRecorder::healCounters) and the dashboard.
    struct CatRow { Cat cat = Cat::Story; uint64_t total = 0; uint32_t perMinute = 0; uint64_t mismatches = 0; };
    std::vector<CatRow> catRows(uint64_t nowMs) const;
    const std::deque<BugReport>& bugs() const { return bugs_; }
    size_t openCases() const { return cases_.size(); }
    std::string statusJson(uint64_t nowMs) const;

private:
    struct Group { uint32_t hash = 0, count = 0; };
    struct GroupId { CellId cell = 0; Cat cat = Cat::Actors; bool operator<(const GroupId& o) const { return cell != o.cell ? cell < o.cell : cat < o.cat; } };
    struct Backoff { uint32_t mine = 0, theirs = 0; uint64_t until = 0; uint32_t span = 0; };
    struct Peer {
        bool joined = false;
        uint64_t lastDigestAt = 0;
        bool hadDigest = false;
        std::set<CellId> loaded;
        std::map<GroupId, Backoff> backoff;
        uint64_t nextStoryRepairAt = 0;
        uint32_t openCase = 0;
    };
    struct Decision { Item hostItem, peerItem; Item auth; bool haveAuth = false; PlayerId owner = kNoPlayer; uint32_t epoch = 0; };
    struct Case {
        uint32_t id = 0;
        PlayerId peer = kNoPlayer;
        uint64_t createdAt = 0;
        std::vector<GroupId> groups;
        enum class St : uint8_t { WaitPeer, WaitOwners } st = St::WaitPeer;
        bool peerTruncated = false;
        std::map<std::pair<Cat, Key>, Item> peerItems;
        std::map<PlayerId, std::map<std::pair<Cat, Key>, Item>> ownerItems;
        std::set<PlayerId> awaiting;
        std::vector<std::pair<std::pair<Cat, Key>, Decision>> work;
        std::map<GroupId, std::pair<uint32_t, uint32_t>> hashesAtOpen;   // (mine, theirs) when opened, for the backoff
        bool transient = false;      // something was still in flight / in a handoff / over budget: do not back this group off
    };
    struct KeyHist { uint32_t count = 0; uint64_t firstMs = 0, lastMs = 0, lastBugMs = 0; };

    void collect(uint64_t nowMs, bool force);
    void sendDigest(uint64_t nowMs);
    void onDigest(PlayerId from, const struct MDigest& m, uint64_t nowMs);
    void onItemsReq(PlayerId from, const struct MItemsReq& m, uint64_t nowMs);
    void onItems(PlayerId from, const struct MItems& m, uint64_t nowMs);
    void onHeal(PlayerId from, const struct MHeal& m, uint64_t nowMs);
    void compareStory(PlayerId from, Peer& p, const struct MDigest& m, uint64_t nowMs);
    void planCase(Case& cs, uint64_t nowMs);
    void resolveCase(Case& cs, uint64_t nowMs);
    void closeCase(uint32_t id, bool timedOut, uint64_t nowMs);
    bool takeToken(uint64_t nowMs);
    IAreaSource* source(Cat c) const;
    bool ownerOf(Cat c, Key k, OwnerView& v) const;
    void noteHeal(Cat c, uint64_t nowMs);
    void noteRepeat(const Item& auth, const Item& other, PlayerId peer, uint64_t nowMs);
    HealResult applyLocal(const Item& auth, uint32_t forVersion, uint32_t epoch, uint64_t nowMs);
    void sendTo(PlayerId peer, const struct Envelope& e);
    void emit(const HealNote& n);

    Config cfg_;
    IAreaNet& net_;
    IStorySource* story_;
    IAreaObserver* obs_ = nullptr;
    std::array<IAreaSource*, kCats> src_{};
    uint64_t now_ = 0;

    // own world
    uint64_t lastCollectAt_ = 0;
    bool collected_ = false;
    std::vector<Item> items_;
    std::set<CellId> loaded_;
    std::map<GroupId, Group> groups_;

    // client
    bool linkUp_ = false;
    uint64_t nextDigestAt_ = 0;
    uint32_t round_ = 0;

    // host
    std::map<PlayerId, Peer> peers_;
    std::map<uint32_t, Case> cases_;
    uint32_t caseSeq_ = 0;
    double tokens_ = -1;
    uint64_t tokenAt_ = 0;
    uint64_t storyRepairsLast_ = 0;
    bool storyRepairsInit_ = false;

    std::array<RollingRate, kCats> rates_;
    std::map<std::pair<Cat, Key>, KeyHist> hist_;
    std::deque<BugReport> bugs_;
    Counters c_;
};

}  // namespace fnvcoop::areadigest
