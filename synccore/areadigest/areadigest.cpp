#include "areadigest/areadigest.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <sstream>
#include "areadigest/ad_wire.h"
#include "fnvcoop/baseline.h"
#include "fnvcoop/wire.h"
#include "fnvcoop_mut.h"

namespace fnvcoop::areadigest {

// ================================================================================================================================
// small helpers
// ================================================================================================================================
const char* toString(Cat c) {
    switch (c) {
        case Cat::Story: return "story";
        case Cat::Actors: return "actors";
        case Cat::Containers: return "containers";
        case Cat::Objects: return "objects";
        default: return "?";
    }
}

const char* toString(HealResult r) {
    switch (r) {
        case HealResult::Applied: return "applied";
        case HealResult::Delegated: return "delegated";
        case HealResult::Raced: return "raced";
        case HealResult::Missing: return "missing";
        case HealResult::Refused: return "refused";
        case HealResult::NoDiff: return "nodiff";
        default: return "?";
    }
}

float distance(const Vec3& a, const Vec3& b) {
    const double dx = double(a.x) - b.x, dy = double(a.y) - b.y, dz = double(a.z) - b.z;
    return static_cast<float>(std::sqrt(dx * dx + dy * dy + dz * dz));
}

namespace {

int32_t bucketOf(float v, float size) {
    if (!(size > 0)) return 0;
    const double q = std::floor(double(v) / double(size));
    if (q > 2e9) return 2000000000;
    if (q < -2e9) return -2000000000;
    return static_cast<int32_t>(q);
}

std::string jsonEscape(const std::string& s) {
    std::string o;
    for (char ch : s) {
        if (ch == '"' || ch == '\\') { o += '\\'; o += ch; }
        else if (static_cast<unsigned char>(ch) < 0x20) o += ' ';
        else o += ch;
    }
    return o;
}

std::string hex(uint64_t v) {
    char b[32];
    std::snprintf(b, sizeof b, "0x%llx", static_cast<unsigned long long>(v));
    return b;
}

}  // namespace

// ================================================================================================================================
// hashing
// ================================================================================================================================
uint32_t valueHash(const Item& it, const Config& c) {
    uint32_t f[8];
    size_t n = 0;
    if (!FNV_MUT(6)) f[n++] = it.cell;                    // twin 6: the cell is not hashed
    if (!FNV_MUT(7)) f[n++] = it.alive ? 1u : 0u;         // twin 7: alive is not hashed
    const bool settled = FNV_MUT(1) ? true : it.settledMs >= c.settleMs;   // twin 1: items in flux hash their moving values too
    if (settled) {
        if (it.cat == Cat::Actors) {
            if (it.alive || FNV_MUT(2)) {                  // twin 2: a corpse's health and position are hashed too
                if (!FNV_MUT(3)) f[n++] = static_cast<uint32_t>(bucketOf(it.hp, c.hpBucket));   // twin 3: health is not hashed
                if (!FNV_MUT(4)) {                         // twin 4: the position is not hashed
                    const float pb = c.posBucketMetres * c.unitsPerMetre;
                    f[n++] = static_cast<uint32_t>(bucketOf(it.pos.x, pb));
                    f[n++] = static_cast<uint32_t>(bucketOf(it.pos.y, pb));
                    f[n++] = static_cast<uint32_t>(bucketOf(it.pos.z, pb));
                }
            }
        } else if (!FNV_MUT(5)) {                          // twin 5: containers / objects: the state is not hashed
            f[n++] = it.state;
        }
    }
    const uint64_t h = hash64(f, n * sizeof(uint32_t));
    return static_cast<uint32_t>(h ^ (h >> 32));
}

std::vector<GroupInfo> groupsOf(const std::vector<Item>& items, const Config& c) {
    std::map<std::pair<CellId, uint8_t>, GroupInfo> m;
    for (const Item& it : items) {
        if (!isAreaCat(it.cat)) continue;
        GroupInfo& g = m[{it.cell, static_cast<uint8_t>(it.cat)}];
        g.cell = it.cell;
        g.cat = it.cat;
        g.hash += bucketMix(it.key, valueHash(it, c));
        ++g.count;
    }
    std::vector<GroupInfo> out;
    out.reserve(m.size());
    for (auto& kv : m) out.push_back(kv.second);
    return out;
}

StoryDigest storyDigestOf(const ShadowState& w, uint64_t seq, uint32_t epoch) {
    StoryDigest d;
    size_t n = 0;
    const std::vector<uint32_t> b = bucketHashesCached(w, &n);
    d.ok = true;
    d.count = static_cast<uint32_t>(n);
    d.seq = seq;
    d.epoch = epoch;
    // twin 8: the digest covers the entry count only, so a changed value is invisible
    d.hash = FNV_MUT(8) ? hash64(&d.count, sizeof d.count) : hash64(b.data(), b.size() * sizeof(uint32_t), 0x5eed0001ull ^ n);
    return d;
}

// ================================================================================================================================
// comparing, rubber band
// ================================================================================================================================
RubberMode classifyOffset(float distUnits, bool sameCell, const Config& c) {
    const float smooth = c.smoothMetres * c.unitsPerMetre;
    const float snap = FNV_MUT(10) ? smooth : c.snapMetres * c.unitsPerMetre;   // twin 10: everything beyond the smooth distance snaps
    if (!sameCell && !FNV_MUT(11)) return RubberMode::Snap;                        // twin 11: another cell does not snap
    if (distUnits > snap) return RubberMode::Snap;
    if (FNV_MUT(9) ? distUnits >= smooth : distUnits > smooth) return RubberMode::Smooth;   // twin 9: exactly 2 m already smooths
    return RubberMode::None;
}

Diff compareItems(const Item& a, const Item& o, const Config& c) {
    Diff d;
    if (a.cell != o.cell) {
        d.cell = true;
        d.material = true;
        if (a.cat == Cat::Actors) d.pos = RubberMode::Snap;
    }
    if (a.alive != o.alive) { d.alive = true; d.material = true; }
    if (a.cat == Cat::Actors) {
        if ((a.alive && o.alive) || FNV_MUT(17)) {   // twin 17: corpses are compared (ragdolls differ between machines: constant false heals)
            const float tol = FNV_MUT(16) ? 0.0f : c.hpTolerance;   // twin 16: no health tolerance
            if (std::fabs(a.hp - o.hp) > tol) { d.hp = true; d.material = true; }
            d.dist = distance(a.pos, o.pos);
            const RubberMode m = classifyOffset(d.dist, a.cell == o.cell, c);
            if (m != RubberMode::None) { d.pos = m; d.material = true; }
        }
    } else if (a.state != o.state) {
        d.state = true;
        d.material = true;
    }
    return d;
}

std::string describe(const Item& it) {
    char b[200];
    if (it.cat == Cat::Actors) {
        std::snprintf(b, sizeof b, "actor %s cell %s %s hp %.0f pos (%.1f,%.1f,%.1f)", hex(it.key).c_str(), hex(it.cell).c_str(),
                      it.alive ? "alive" : "dead", it.hp, it.pos.x, it.pos.y, it.pos.z);
    } else {
        std::snprintf(b, sizeof b, "%s %s cell %s %s state %s", it.cat == Cat::Containers ? "container" : "object", hex(it.key).c_str(),
                      hex(it.cell).c_str(), it.alive ? "present" : "gone", hex(it.state).c_str());
    }
    return b;
}

// ================================================================================================================================
// smoother
// ================================================================================================================================
void Smoother::begin(uint64_t nowMs, const Vec3& from, const Vec3& to, const Config& c) {
    from_ = from;
    to_ = to;
    t0_ = nowMs;
    const double metres = double(distance(from, to)) / (c.unitsPerMetre > 0 ? c.unitsPerMetre : 70.0);
    dur_ = static_cast<uint64_t>(std::min(1500.0, std::max(300.0, metres * 150.0)));
    active_ = true;
}

bool Smoother::step(uint64_t nowMs, Vec3& out) {
    if (!active_) { out = to_; return false; }
    if (FNV_MUT(12) || nowMs >= t0_ + dur_) {   // twin 12: the smoother jumps straight to the target
        active_ = false;
        out = to_;
        return false;
    }
    const float t = nowMs <= t0_ ? 0.0f : static_cast<float>(nowMs - t0_) / static_cast<float>(dur_);
    out.x = from_.x + (to_.x - from_.x) * t;
    out.y = from_.y + (to_.y - from_.y) * t;
    out.z = from_.z + (to_.z - from_.z) * t;
    return true;
}

// ================================================================================================================================
// change tracker
// ================================================================================================================================
bool ChangeTracker::changed(const Item& a, const Item& b) const {
    if (a.cell != b.cell || a.alive != b.alive || a.state != b.state) return true;
    if (a.cat == Cat::Actors) {
        if (std::fabs(a.hp - b.hp) >= 0.5f) return true;
        if (a.alive && distance(a.pos, b.pos) > (FNV_MUT(15) ? 0.0f : cfg_.moveEpsilon)) return true;   // twin 15: any wobble is a change
    }
    return false;
}

void ChangeTracker::update(std::vector<Item>& items, uint64_t nowMs) {
    std::set<std::pair<Cat, Key>> seen;
    for (Item& it : items) {
        const std::pair<Cat, Key> k{it.cat, it.key};
        seen.insert(k);
        auto f = st_.find(k);
        if (f == st_.end()) {
            St s;
            s.last = it;
            s.changedAt = nowMs;
            s.version = 1;
            st_[k] = s;
            it.settledMs = 0;
            it.version = 1;
            continue;
        }
        St& s = f->second;
        if (changed(s.last, it)) {
            s.last = it;
            s.changedAt = nowMs;
            if (!FNV_MUT(14)) ++s.version;   // twin 14: the version never moves, so a heal can land on an item that changed in between
        }
        const uint64_t age = nowMs >= s.changedAt ? nowMs - s.changedAt : 0;
        it.settledMs = static_cast<uint32_t>(std::min<uint64_t>(age, 0xFFFFFFFEull));
        it.version = s.version;
    }
    for (auto i = st_.begin(); i != st_.end();) {
        if (!seen.count(i->first)) i = st_.erase(i);
        else ++i;
    }
}

bool ChangeTracker::refresh(Item& it, uint64_t nowMs) {
    const std::pair<Cat, Key> k{it.cat, it.key};
    auto f = st_.find(k);
    if (f == st_.end()) {
        St s;
        s.last = it;
        s.changedAt = nowMs;
        s.version = 1;
        st_[k] = s;
        it.settledMs = 0;
        it.version = 1;
        return false;
    }
    St& s = f->second;
    if (changed(s.last, it)) {
        s.last = it;
        s.changedAt = nowMs;
        if (!FNV_MUT(14)) ++s.version;
    }
    const uint64_t age = nowMs >= s.changedAt ? nowMs - s.changedAt : 0;
    it.settledMs = static_cast<uint32_t>(std::min<uint64_t>(age, 0xFFFFFFFEull));
    it.version = s.version;
    return true;
}

// ================================================================================================================================
// rolling counter
// ================================================================================================================================
void RollingRate::add(uint64_t nowMs, uint32_t n) {
    const uint64_t sec = nowMs / 1000;
    if (!any_) {
        any_ = true;
        lastSec_ = sec;
    } else if (sec > lastSec_) {
        const uint64_t span = std::min<uint64_t>(sec - lastSec_, 60);
        for (uint64_t i = 1; i <= span; ++i) b_[(lastSec_ + i) % 60] = 0;
        lastSec_ = sec;
    }
    b_[lastSec_ % 60] += n;
    total_ += n;
}

uint32_t RollingRate::perMinute(uint64_t nowMs) const {
    if (!any_) return 0;
    if (FNV_MUT(13)) return static_cast<uint32_t>(total_);   // twin 13: the per-minute count never expires
    const int64_t sec = static_cast<int64_t>(nowMs / 1000);
    const int64_t last = static_cast<int64_t>(lastSec_);
    uint32_t sum = 0;
    for (int64_t i = 0; i < 60; ++i) {
        const int64_t s = sec - i;
        if (s < 0 || s > last || last - s >= 60) continue;
        sum += b_[static_cast<size_t>(s % 60)];
    }
    return sum;
}

// ================================================================================================================================
// AreaSync
// ================================================================================================================================
AreaSync::AreaSync(Config cfg, IAreaNet& net, IStorySource* story) : cfg_(cfg), net_(net), story_(story) {}
AreaSync::~AreaSync() = default;

void AreaSync::addSource(IAreaSource* s) {
    if (!s) return;
    const Cat c = s->category();
    if (!isAreaCat(c)) return;
    src_[static_cast<size_t>(c)] = s;
}

IAreaSource* AreaSync::source(Cat c) const {
    const size_t i = static_cast<size_t>(c);
    return i < kCats ? src_[i] : nullptr;
}

bool AreaSync::ownerOf(Cat c, Key k, OwnerView& v) const {
    IAreaSource* s = source(c);
    return s && s->owner(k, v);
}

void AreaSync::playerJoined(PlayerId p) {
    if (!cfg_.isHost) return;
    Peer& pe = peers_[p];
    pe = Peer{};
    pe.joined = true;
}

void AreaSync::playerLeft(PlayerId p) {
    auto it = peers_.find(p);
    if (it == peers_.end()) return;
    for (auto c = cases_.begin(); c != cases_.end();) {
        if (c->second.peer == p || c->second.awaiting.count(p)) {
            ++c_.casesClosed;
            c = cases_.erase(c);
        } else {
            ++c;
        }
    }
    peers_.erase(it);
    for (auto& kv : peers_) {   // a case that waited for the leaver as an owner is gone with it; its requester may ask again
        if (kv.second.openCase && !cases_.count(kv.second.openCase)) kv.second.openCase = 0;
    }
}

void AreaSync::hostConnected() {
    linkUp_ = true;
    nextDigestAt_ = now_ + cfg_.intervalMs / 2;
}

void AreaSync::hostLost() {
    linkUp_ = false;
}

void AreaSync::collect(uint64_t nowMs, bool force) {
    if (collected_ && !force && nowMs < lastCollectAt_ + cfg_.collectCacheMs && nowMs >= lastCollectAt_) return;
    items_.clear();
    loaded_.clear();
    std::vector<CellId> cells;
    for (IAreaSource* s : src_) {
        if (s) s->collect(nowMs, items_, cells);
    }
    for (CellId c : cells) loaded_.insert(c);
    for (const Item& it : items_) loaded_.insert(it.cell);
    groups_.clear();
    for (const GroupInfo& g : groupsOf(items_, cfg_)) groups_[GroupId{g.cell, g.cat}] = Group{g.hash, g.count};
    lastCollectAt_ = nowMs;
    collected_ = true;
}

void AreaSync::sendTo(PlayerId peer, const Envelope& e0) {
    Envelope e = e0;
    e.src = cfg_.self;
    std::vector<uint8_t> bytes = encodeEnvelope(e);
    if (bytes.empty()) { ++c_.badMessages; return; }
    net_.send(peer, std::move(bytes));
}

void AreaSync::tick(uint64_t nowMs) {
    now_ = nowMs;
    for (IAreaSource* s : src_) {
        if (s) s->tick(nowMs);
    }
    if (!cfg_.isHost) {
        if (linkUp_ && nowMs >= nextDigestAt_) sendDigest(nowMs);
        return;
    }
    // host: finish or drop exchanges that stalled
    std::vector<uint32_t> late;
    for (auto& kv : cases_) {
        if (!FNV_MUT(35) && nowMs >= kv.second.createdAt + cfg_.caseTimeoutMs) late.push_back(kv.first);   // twin 35: a stalled exchange stays open forever
    }
    for (uint32_t id : late) closeCase(id, true, nowMs);
    if (story_) {
        const uint64_t r = story_->repairs();
        if (!storyRepairsInit_) {
            storyRepairsInit_ = true;
            storyRepairsLast_ = r;
        } else if (r > storyRepairsLast_) {
            const uint64_t d = r - storyRepairsLast_;
            storyRepairsLast_ = r;
            if (!FNV_MUT(28)) {   // twin 28: story repairs are not counted as heals
                rates_[static_cast<size_t>(Cat::Story)].add(nowMs, static_cast<uint32_t>(d));
                c_.cat[static_cast<size_t>(Cat::Story)].healsApplied += d;
                c_.cat[static_cast<size_t>(Cat::Story)].healsDecided += d;
            }
            c_.storyRepairsSeen += d;
        }
    }
}

void AreaSync::sendDigest(uint64_t nowMs) {
    collect(nowMs, false);
    Envelope e;
    MDigest d;
    d.round = ++round_;
    d.cells.assign(loaded_.begin(), loaded_.end());
    if (d.cells.size() > kMaxCells) d.cells.resize(kMaxCells);
    for (const auto& kv : groups_) {
        if (d.groups.size() >= kMaxGroups) break;
        d.groups.push_back(WireGroup{kv.first.cell, kv.first.cat, kv.second.hash, kv.second.count});
    }
    StoryDigest sd;
    if (cfg_.compareStory && story_ && story_->local(sd) && sd.ok) {
        d.story.has = true;
        d.story.hash = sd.hash;
        d.story.count = sd.count;
        d.story.seq = sd.seq;
        d.story.epoch = sd.epoch;
    }
    e.body = std::move(d);
    sendTo(kHostPlayer, e);
    ++c_.digestsSent;
    nextDigestAt_ = nowMs + cfg_.intervalMs;
}

void AreaSync::receive(PlayerId from, const uint8_t* data, size_t size) {
    Envelope e;
    if (!decodeEnvelope(data, size, e)) { ++c_.badMessages; return; }
    if (e.src != from) ++c_.spoofed;    // the transport peer is the truth, never the claim inside the datagram
    const uint64_t now = now_;
    if (auto* m = std::get_if<MDigest>(&e.body)) {
        if (!cfg_.isHost) { ++c_.wrongDirection; return; }
        onDigest(from, *m, now);
    } else if (auto* r = std::get_if<MItemsReq>(&e.body)) {
        if (cfg_.isHost || (from != kHostPlayer && !FNV_MUT(27))) { ++c_.wrongDirection; return; }   // twin 27: a client obeys anybody
        onItemsReq(from, *r, now);
    } else if (auto* it = std::get_if<MItems>(&e.body)) {
        if (!cfg_.isHost) { ++c_.wrongDirection; return; }
        onItems(from, *it, now);
    } else if (auto* h = std::get_if<MHeal>(&e.body)) {
        if (cfg_.isHost || (from != kHostPlayer && !FNV_MUT(27))) { ++c_.wrongDirection; return; }   // only the host may push values
        onHeal(from, *h, now);
    }
}

// ---------------------------------------------------------------------------------------------------------------- host: digests
void AreaSync::compareStory(PlayerId from, Peer& p, const MDigest& m, uint64_t nowMs) {
    if (!cfg_.compareStory || !story_ || !m.story.has) return;
    StoryDigest mine;
    if (!story_->local(mine) || !mine.ok) return;
    CatCounters& sc = c_.cat[static_cast<size_t>(Cat::Story)];
    if (m.story.epoch != mine.epoch && !FNV_MUT(39)) {   // twin 39: digests of different world epochs are compared
        ++c_.storyEpochSkew;
        return;
    }
    if (m.story.seq != mine.seq && !FNV_MUT(38)) {       // twin 38: digests at different applied seqs are compared (txns in flight look like drift)
        ++c_.storyInFlight;
        ++sc.inFlight;
        return;
    }
    ++sc.groupsChecked;
    if (m.story.hash == mine.hash && m.story.count == mine.count) {
        ++c_.storyOk;
        return;
    }
    ++c_.storyMismatch;
    ++sc.groupsMismatch;
    if (nowMs >= p.nextStoryRepairAt) {
        p.nextStoryRepairAt = nowMs + cfg_.storyRepairGapMs;
        ++c_.storyRepairRequests;
        story_->requestRepair(from);
    }
}

void AreaSync::onDigest(PlayerId from, const MDigest& m, uint64_t nowMs) {
    auto pit = peers_.find(from);
    if (pit == peers_.end() || !pit->second.joined) { ++c_.unknownPeer; return; }
    Peer& p = pit->second;
    ++c_.digestsReceived;
    if (!FNV_MUT(18) && p.hadDigest && nowMs < p.lastDigestAt + cfg_.intervalMs / 2) {   // twin 18: no rate limit
        ++c_.digestsRateLimited;
        return;
    }
    p.hadDigest = true;
    p.lastDigestAt = nowMs;
    p.loaded.clear();
    for (CellId c : m.cells) p.loaded.insert(c);
    compareStory(from, p, m, nowMs);

    collect(nowMs, false);
    std::map<GroupId, WireGroup> theirs;
    for (const WireGroup& g : m.groups) theirs[GroupId{g.cell, g.cat}] = g;

    std::vector<GroupId> bad;
    std::map<GroupId, std::pair<uint32_t, uint32_t>> hashes;
    bool anyMismatch = false;
    for (CellId cell : p.loaded) {
        if (!loaded_.count(cell)) continue;      // an area only one side has loaded cannot be compared
        for (Cat cat : {Cat::Actors, Cat::Containers, Cat::Objects}) {
            if (!source(cat)) continue;          // a category this machine does not read cannot be judged
            const GroupId gid{cell, cat};
            Group mine;
            auto mi = groups_.find(gid);
            if (mi != groups_.end()) mine = mi->second;
            Group th;
            auto ti = theirs.find(gid);
            if (ti != theirs.end()) { th.hash = ti->second.hash; th.count = ti->second.count; }
            CatCounters& cc = c_.cat[static_cast<size_t>(cat)];
            ++cc.groupsChecked;
            const bool same = FNV_MUT(34) ? mine.count == th.count : (mine.hash == th.hash && mine.count == th.count);   // twin 34: only the item count is compared
            if (same) continue;
            ++cc.groupsMismatch;
            anyMismatch = true;
            auto bo = p.backoff.find(gid);
            if (!FNV_MUT(29) && bo != p.backoff.end() && nowMs < bo->second.until && bo->second.mine == mine.hash && bo->second.theirs == th.hash) {
                ++c_.backoffSkips;               // twin 29: no backoff (the same unhealable group is diffed every round)
                continue;
            }
            bad.push_back(gid);
            hashes[gid] = {mine.hash, th.hash};
        }
    }
    if (!anyMismatch) { ++c_.digestsOk; return; }
    ++c_.digestsMismatch;
    if (bad.empty()) return;
    if (p.openCase && cases_.count(p.openCase)) { ++c_.casesBusy; return; }
    if (bad.size() > cfg_.maxGroupsPerCase) bad.resize(cfg_.maxGroupsPerCase);

    Case cs;
    cs.id = ++caseSeq_;
    cs.peer = from;
    cs.createdAt = nowMs;
    cs.groups = bad;
    cs.hashesAtOpen = hashes;
    p.openCase = cs.id;
    ++c_.casesOpened;
    Envelope e;
    MItemsReq rq;
    rq.caseId = cs.id;
    for (const GroupId& g : bad) rq.groups.push_back(WireGroupId{g.cell, g.cat});
    e.body = std::move(rq);
    cases_[cs.id] = std::move(cs);
    sendTo(from, e);
}

// ---------------------------------------------------------------------------------------------------------------- client: answers
void AreaSync::onItemsReq(PlayerId from, const MItemsReq& m, uint64_t nowMs) {
    (void)from;
    collect(nowMs, false);
    MItems r;
    r.caseId = m.caseId;
    std::set<GroupId> want;
    for (const WireGroupId& g : m.groups) want.insert(GroupId{g.cell, g.cat});
    if (!want.empty()) {
        for (const Item& it : items_) {
            if (!want.count(GroupId{it.cell, it.cat})) continue;
            if (r.items.size() >= cfg_.maxItemsPerReply) { r.truncated = true; break; }
            r.items.push_back(it);
        }
    }
    for (const WireKey& k : m.keys) {
        IAreaSource* s = source(k.cat);
        Item it;
        if (!s || !s->find(k.key, it, nowMs)) continue;
        if (r.items.size() >= cfg_.maxItemsPerReply) { r.truncated = true; break; }
        r.items.push_back(it);
    }
    c_.itemsSent += r.items.size();
    Envelope e;
    e.body = std::move(r);
    sendTo(kHostPlayer, e);
}

// ---------------------------------------------------------------------------------------------------------------- host: the diff
void AreaSync::onItems(PlayerId from, const MItems& m, uint64_t nowMs) {
    auto it = cases_.find(m.caseId);
    if (it == cases_.end()) { ++c_.strays; return; }
    Case& cs = it->second;
    if (cs.st == Case::St::WaitPeer) {
        if (from != cs.peer) { ++c_.strays; return; }
        std::set<GroupId> inCase(cs.groups.begin(), cs.groups.end());
        for (const Item& item : m.items) {
            if (!isAreaCat(item.cat)) continue;
            if (!inCase.count(GroupId{item.cell, item.cat})) continue;   // only what was asked for
            cs.peerItems[{item.cat, item.key}] = item;
        }
        cs.peerTruncated = m.truncated;
        planCase(cs, nowMs);
    } else {
        if (!cs.awaiting.count(from) && !FNV_MUT(37)) { ++c_.strays; return; }   // twin 37: items from a machine that was not asked are accepted
        for (const Item& item : m.items) {
            if (!isAreaCat(item.cat)) continue;
            cs.ownerItems[from][{item.cat, item.key}] = item;
        }
        cs.awaiting.erase(from);
        if (cs.awaiting.empty()) resolveCase(cs, nowMs);
    }
}

void AreaSync::planCase(Case& cs, uint64_t nowMs) {
    collect(nowMs, false);
    std::set<GroupId> inCase(cs.groups.begin(), cs.groups.end());
    std::map<std::pair<Cat, Key>, Item> hostItems;
    for (const Item& it : items_) {
        if (inCase.count(GroupId{it.cell, it.cat})) hostItems[{it.cat, it.key}] = it;
    }
    std::set<std::pair<Cat, Key>> keys;
    for (auto& kv : hostItems) keys.insert(kv.first);
    for (auto& kv : cs.peerItems) keys.insert(kv.first);

    std::map<PlayerId, std::vector<WireKey>> fetch;
    for (const auto& k : keys) {
        const Cat cat = k.first;
        CatCounters& cc = c_.cat[static_cast<size_t>(cat)];
        auto hi = hostItems.find(k);
        auto pi = cs.peerItems.find(k);
        if (hi == hostItems.end() || pi == cs.peerItems.end()) {
            if (pi == cs.peerItems.end() && cs.peerTruncated) continue;   // the reply was cut: absence proves nothing
            ++cc.missing;                                                  // one side does not have it loaded: nothing to push
            continue;
        }
        ++cc.itemsCompared;
        const bool unsettled = hi->second.settledMs < cfg_.settleMs || pi->second.settledMs < cfg_.settleMs;
        if (unsettled) cs.transient = true;   // one side was still settling: whatever this round says, look again next round
        const Diff d = compareItems(hi->second, pi->second, cfg_);
        if (!d.material) { ++cc.falsePositive; continue; }
        if (!FNV_MUT(19) && unsettled) {      // twin 19: in-flight values are healed
            ++cc.inFlight;
            continue;
        }
        OwnerView ov;
        const bool haveOwner = ownerOf(cat, k.second, ov);
        if (haveOwner && ov.known && ov.pending && !FNV_MUT(20)) { ++cc.pendingHandoff; cs.transient = true; continue; }   // twin 20: a handoff in flight does not stop a heal
        PlayerId owner = kHostPlayer;
        if (cat == Cat::Actors) {
            if (!haveOwner || !ov.known || ov.owner == kNoPlayer) { ++cc.ownerSilent; continue; }   // nobody is authoritative for an unowned actor
            owner = FNV_MUT(21) ? kHostPlayer : ov.owner;   // twin 21: the host is always the authority, whoever owns the actor
        }
        Decision dc;
        dc.hostItem = hi->second;
        dc.peerItem = pi->second;
        dc.owner = owner;
        dc.epoch = haveOwner && ov.known ? ov.epoch : 0;
        if (owner == kHostPlayer) {
            dc.auth = hi->second;
            dc.haveAuth = true;
        } else if (owner == cs.peer) {
            dc.auth = pi->second;
            dc.haveAuth = true;
        } else {
            auto po = peers_.find(owner);
            if (po == peers_.end() || !po->second.joined) { ++cc.ownerSilent; continue; }
            fetch[owner].push_back(WireKey{cat, k.second});
        }
        cs.work.push_back({k, dc});
    }
    if (!fetch.empty() && !FNV_MUT(22)) {   // twin 22: a third machine that owns the actor is never asked; the host's copy decides
        cs.st = Case::St::WaitOwners;
        for (auto& kv : fetch) {
            Envelope e;
            MItemsReq rq;
            rq.caseId = cs.id;
            rq.keys = kv.second;
            e.body = std::move(rq);
            cs.awaiting.insert(kv.first);
            sendTo(kv.first, e);
        }
        return;
    }
    resolveCase(cs, nowMs);
}

bool AreaSync::takeToken(uint64_t nowMs) {
    if (FNV_MUT(36)) return true;   // twin 36: no heal budget
    const double cap = double(cfg_.healBudgetPerMin);
    if (tokens_ < 0) { tokens_ = cap; tokenAt_ = nowMs; }
    if (nowMs > tokenAt_) {
        tokens_ = std::min(cap, tokens_ + cap * double(nowMs - tokenAt_) / 60000.0);
        tokenAt_ = nowMs;
    }
    if (tokens_ < 1.0) return false;
    tokens_ -= 1.0;
    return true;
}

void AreaSync::noteHeal(Cat c, uint64_t nowMs) {
    rates_[static_cast<size_t>(c)].add(nowMs, 1);
    ++c_.cat[static_cast<size_t>(c)].healsApplied;
}

void AreaSync::emit(const HealNote& n) {
    if (obs_) obs_->onHeal(n);
}

void AreaSync::noteRepeat(const Item& auth, const Item& other, PlayerId peer, uint64_t nowMs) {
    if (FNV_MUT(30)) return;   // twin 30: a key that keeps coming back is never reported
    KeyHist& h = hist_[{auth.cat, auth.key}];
    if (h.count && nowMs >= h.lastMs && nowMs - h.lastMs <= cfg_.repeatWindowMs) ++h.count;
    else { h.count = 1; h.firstMs = nowMs; h.lastBugMs = 0; }
    h.lastMs = nowMs;
    if (h.count >= 2) {
        CatCounters& cc = c_.cat[static_cast<size_t>(auth.cat)];
        ++cc.repeats;
        if (h.lastBugMs == 0 || nowMs >= h.lastBugMs + cfg_.repeatBugGapMs) {
            h.lastBugMs = nowMs ? nowMs : 1;
            BugReport b;
            b.cat = auth.cat;
            b.key = auth.key;
            b.cell = other.cell;
            b.peer = peer;
            b.count = h.count;
            b.firstMs = h.firstMs;
            b.lastMs = nowMs;
            b.authority = describe(auth);
            b.other = describe(other);
            bugs_.push_back(b);
            while (bugs_.size() > cfg_.bugLimit) bugs_.pop_front();
            ++c_.bugs;
            if (obs_) obs_->onBug(b);
        }
    }
    if (hist_.size() > 2048) {
        for (auto i = hist_.begin(); i != hist_.end();) {
            if (nowMs > i->second.lastMs + 5ull * cfg_.repeatWindowMs) i = hist_.erase(i);
            else ++i;
        }
    }
}

void AreaSync::resolveCase(Case& cs, uint64_t nowMs) {
    auto pit = peers_.find(cs.peer);
    if (pit == peers_.end()) { closeCase(cs.id, false, nowMs); return; }
    Peer& peer = pit->second;
    MHeal toPeer;
    toPeer.caseId = cs.id;
    std::set<GroupId> healedGroups;
    bool delegatePeer = false;
    size_t decided = 0;
    for (auto& w : cs.work) {
        const Cat cat = w.first.first;
        CatCounters& cc = c_.cat[static_cast<size_t>(cat)];
        Decision& dc = w.second;
        Item auth = dc.auth;
        if (!dc.haveAuth) {
            auto oi = cs.ownerItems.find(dc.owner);
            const Item* found = nullptr;
            if (oi != cs.ownerItems.end()) {
                auto fi = oi->second.find(w.first);
                if (fi != oi->second.end()) found = &fi->second;
            }
            if (!found) { ++cc.ownerSilent; continue; }
            auth = *found;
            if (auth.settledMs < cfg_.settleMs && !FNV_MUT(19)) { ++cc.inFlight; cs.transient = true; continue; }
        }
        IAreaSource* src = source(cat);
        const bool delegated = src && src->delegated();
        const Diff dPeer = compareItems(auth, dc.peerItem, cfg_);
        bool hostWrong = dc.owner != kHostPlayer && compareItems(auth, dc.hostItem, cfg_).material;
        if (hostWrong && src) {
            // the host's own copy may have been corrected meanwhile (another exchange with a third machine): judge it as it is NOW
            Item cur;
            if (src->find(w.first.second, cur, nowMs)) {
                dc.hostItem = cur;
                hostWrong = compareItems(auth, cur, cfg_).material;
            }
        }
        const bool peerWrong = dc.owner != cs.peer && dPeer.material;
        if (!peerWrong && !hostWrong) { ++cc.falsePositive; continue; }
        if (delegated) {
            // the story layer owns these effects: ask it to repair instead of pushing values
            ++cc.delegated;
            if (peerWrong) delegatePeer = true;
            healedGroups.insert(GroupId{dc.peerItem.cell, cat});
            healedGroups.insert(GroupId{dc.hostItem.cell, cat});
            continue;
        }
        if (decided >= cfg_.maxHealsPerCase) { ++cc.deferredBudget; cs.transient = true; continue; }
        if (peerWrong) {
            if (!takeToken(nowMs)) { ++cc.deferredBudget; cs.transient = true; continue; }
            ++decided;
            ++cc.healsDecided;
            ++c_.healsSent;
            toPeer.items.push_back(WireHeal{auth, dc.peerItem.version, dc.epoch});
            healedGroups.insert(GroupId{dc.peerItem.cell, cat});
            noteRepeat(auth, dc.peerItem, cs.peer, nowMs);
        }
        if (hostWrong) {
            if (!takeToken(nowMs)) { ++cc.deferredBudget; cs.transient = true; continue; }
            ++decided;
            ++cc.healsDecided;
            healedGroups.insert(GroupId{dc.hostItem.cell, cat});
            noteRepeat(auth, dc.hostItem, kHostPlayer, nowMs);
            applyLocal(auth, dc.hostItem.version, dc.epoch, nowMs);
        }
    }
    if (!toPeer.items.empty()) {
        Envelope e;
        e.body = std::move(toPeer);
        sendTo(cs.peer, e);
    }
    if (delegatePeer && story_ && nowMs >= peer.nextStoryRepairAt) {
        peer.nextStoryRepairAt = nowMs + cfg_.storyRepairGapMs;
        ++c_.storyRepairRequests;
        story_->requestRepair(cs.peer);
    }
    // groups that explained themselves as "nothing to heal" are not diffed again while their hashes stay the same; a group with something
    // still in flight, in a handoff or over budget is looked at again next round
    for (const GroupId& g : cs.groups) {
        if (healedGroups.count(g) || cs.transient) { peer.backoff.erase(g); continue; }
        Backoff& b = peer.backoff[g];
        auto h = cs.hashesAtOpen.find(g);
        if (h != cs.hashesAtOpen.end()) { b.mine = h->second.first; b.theirs = h->second.second; }
        b.span = b.span ? std::min<uint32_t>(b.span * 2, cfg_.backoffMaxMs) : cfg_.backoffMs;
        b.until = nowMs + b.span;
    }
    closeCase(cs.id, false, nowMs);
}

void AreaSync::closeCase(uint32_t id, bool timedOut, uint64_t nowMs) {
    auto it = cases_.find(id);
    if (it == cases_.end()) return;
    auto pit = peers_.find(it->second.peer);
    if (pit != peers_.end()) {
        Peer& p = pit->second;
        if (p.openCase == id) p.openCase = 0;
        if (timedOut) {
            for (const GroupId& g : it->second.groups) {
                Backoff& b = p.backoff[g];
                auto h = it->second.hashesAtOpen.find(g);
                if (h != it->second.hashesAtOpen.end()) { b.mine = h->second.first; b.theirs = h->second.second; }
                b.span = b.span ? std::min<uint32_t>(b.span * 2, cfg_.backoffMaxMs) : cfg_.backoffMs;
                b.until = nowMs + b.span;
            }
        }
    }
    if (timedOut) ++c_.casesTimedOut;
    ++c_.casesClosed;
    cases_.erase(it);
}

// ---------------------------------------------------------------------------------------------------------------- the machine that is wrong
HealResult AreaSync::applyLocal(const Item& auth, uint32_t forVersion, uint32_t epoch, uint64_t nowMs) {
    CatCounters& cc = c_.cat[static_cast<size_t>(auth.cat)];
    IAreaSource* s = source(auth.cat);
    if (!s) { ++cc.refused; return HealResult::Refused; }
    Item local;
    if (!s->find(auth.key, local, nowMs)) { ++cc.missing; return HealResult::Missing; }
    if (local.version != forVersion && !FNV_MUT(23)) { ++cc.raced; return HealResult::Raced; }   // twin 23: the item may have changed since it was reported
    OwnerView ov;
    if (s->owner(auth.key, ov) && ov.known) {
        if (ov.pending) { ++cc.pendingHandoff; return HealResult::Raced; }
        if (ov.epoch > epoch && !FNV_MUT(24)) { ++cc.raced; return HealResult::Raced; }           // twin 24: a heal from an older owner epoch lands
        if (ov.owner == cfg_.self && !FNV_MUT(25)) { ++cc.refused; return HealResult::Refused; } // twin 25: the owner of an actor can be overwritten
    }
    const Diff plan = compareItems(auth, local, cfg_);
    if (!plan.material && !FNV_MUT(26)) { ++cc.noDiff; return HealResult::NoDiff; }              // twin 26: a heal that no longer differs is applied (and counted) anyway
    const HealResult r = s->heal(auth, plan, nowMs);
    switch (r) {
        case HealResult::Applied:
        case HealResult::Delegated: {
            noteHeal(auth.cat, nowMs);
            if (r == HealResult::Delegated) ++cc.delegated;
            HealNote n;
            n.cat = auth.cat;
            n.key = auth.key;
            n.cell = local.cell;
            n.target = cfg_.self;
            n.result = r;
            n.text = describe(local) + " -> " + describe(auth);
            emit(n);
            break;
        }
        case HealResult::Raced: ++cc.raced; break;
        case HealResult::Missing: ++cc.missing; break;
        case HealResult::Refused: ++cc.refused; break;
        case HealResult::NoDiff: ++cc.noDiff; break;
        default: break;
    }
    return r;
}

void AreaSync::onHeal(PlayerId from, const MHeal& m, uint64_t nowMs) {
    (void)from;
    ++c_.healMessagesReceived;
    for (const WireHeal& h : m.items) {
        if (!isAreaCat(h.item.cat)) { ++c_.healsRejected; continue; }
        applyLocal(h.item, h.forVersion, h.epoch, nowMs);
    }
}

// ================================================================================================================================
// counters out
// ================================================================================================================================
uint32_t AreaSync::healsPerMinute(Cat c, uint64_t nowMs) const {
    const size_t i = static_cast<size_t>(c);
    return i < kCats ? rates_[i].perMinute(nowMs) : 0;
}
uint64_t AreaSync::healsTotal(Cat c) const {
    const size_t i = static_cast<size_t>(c);
    return i < kCats ? rates_[i].total() : 0;
}
uint32_t AreaSync::healsPerMinuteAll(uint64_t nowMs) const {
    uint32_t n = 0;
    for (size_t i = 0; i < kCats; ++i) n += rates_[i].perMinute(nowMs);
    return n;
}
uint64_t AreaSync::healsTotalAll() const {
    uint64_t n = 0;
    for (size_t i = 0; i < kCats; ++i) n += rates_[i].total();
    return n;
}

std::vector<AreaSync::CatRow> AreaSync::catRows(uint64_t nowMs) const {
    std::vector<CatRow> v;
    for (size_t i = 0; i < kCats; ++i) {
        CatRow r;
        r.cat = static_cast<Cat>(i);
        r.total = rates_[i].total();
        r.perMinute = rates_[i].perMinute(nowMs);
        r.mismatches = c_.cat[i].groupsMismatch;
        v.push_back(r);
    }
    return v;
}

std::string AreaSync::statusJson(uint64_t nowMs) const {
    std::ostringstream o;
    o << "{\"enabled\":true,\"role\":\"" << (cfg_.isHost ? "host" : "client") << "\",\"self\":" << int(cfg_.self)
      << ",\"linkUp\":" << ((cfg_.isHost || linkUp_) ? "true" : "false") << ",\"peers\":" << peers_.size() << ",\"openCases\":" << cases_.size()
      << ",\"heals\":{\"total\":" << healsTotalAll() << ",\"perMin\":" << healsPerMinuteAll(nowMs) << ",\"byCat\":{";
    for (size_t i = 0; i < kCats; ++i) {
        const CatCounters& cc = c_.cat[i];
        if (i) o << ',';
        o << '"' << toString(static_cast<Cat>(i)) << "\":{\"total\":" << rates_[i].total() << ",\"perMin\":" << rates_[i].perMinute(nowMs)
          << ",\"checked\":" << cc.groupsChecked << ",\"mismatches\":" << cc.groupsMismatch << ",\"decided\":" << cc.healsDecided
          << ",\"inFlight\":" << cc.inFlight << ",\"falsePositive\":" << cc.falsePositive << ",\"raced\":" << cc.raced
          << ",\"refused\":" << cc.refused << ",\"missing\":" << cc.missing << ",\"delegated\":" << cc.delegated
          << ",\"ownerSilent\":" << cc.ownerSilent << ",\"repeats\":" << cc.repeats << '}';
    }
    o << "}},\"counters\":{\"digestsSent\":" << c_.digestsSent << ",\"digestsReceived\":" << c_.digestsReceived
      << ",\"digestsOk\":" << c_.digestsOk << ",\"digestsMismatch\":" << c_.digestsMismatch << ",\"rateLimited\":" << c_.digestsRateLimited
      << ",\"casesOpened\":" << c_.casesOpened << ",\"casesTimedOut\":" << c_.casesTimedOut << ",\"casesBusy\":" << c_.casesBusy
      << ",\"backoffSkips\":" << c_.backoffSkips << ",\"healsSent\":" << c_.healsSent << ",\"healMessages\":" << c_.healMessagesReceived
      << ",\"storyOk\":" << c_.storyOk << ",\"storyMismatch\":" << c_.storyMismatch << ",\"storyInFlight\":" << c_.storyInFlight
      << ",\"storyRepairRequests\":" << c_.storyRepairRequests << ",\"badMessages\":" << c_.badMessages << ",\"spoofed\":" << c_.spoofed
      << ",\"bugs\":" << c_.bugs << "},\"bugs\":[";
    for (size_t i = 0; i < bugs_.size(); ++i) {
        const BugReport& b = bugs_[i];
        if (i) o << ',';
        o << "{\"cat\":\"" << toString(b.cat) << "\",\"key\":\"" << hex(b.key) << "\",\"cell\":\"" << hex(b.cell) << "\",\"peer\":" << int(b.peer)
          << ",\"count\":" << b.count << ",\"authority\":\"" << jsonEscape(b.authority) << "\",\"other\":\"" << jsonEscape(b.other) << "\"}";
    }
    o << "]}";
    return o.str();
}

}  // namespace fnvcoop::areadigest
