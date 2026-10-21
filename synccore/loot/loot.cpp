#include "loot/loot.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstring>
#include "fnvcoop/wire.h"
#include "fnvcoop_mut.h"
#include "loot/loot_wire.h"

namespace fnvcoop::loot {

// ================================================================================================================ helpers

float distance(const Vec3& a, const Vec3& b) {
    const float dx = a.x - b.x, dy = a.y - b.y, dz = a.z - b.z;
    return std::sqrt(dx * dx + dy * dy + dz * dz);
}

void normalize(Items& items) {
    for (auto it = items.begin(); it != items.end();) {
        if (it->second <= 0) it = items.erase(it);
        else ++it;
    }
}

bool validItems(const Items& items) {
    if (items.size() > kMaxStacks) return false;
    for (const auto& kv : items) {
        if (kv.first.plugin.empty() || kv.first.plugin.size() > kMaxTextLen) return false;
        if (kv.second < 0 || kv.second > kMaxCount) return false;
    }
    return true;
}

namespace {

uint64_t mix(uint64_t h, uint64_t v) {
    h ^= v + 0x9E3779B97F4A7C15ull + (h << 6) + (h >> 2);
    h *= 0xFF51AFD7ED558CCDull;
    return h ^ (h >> 33);
}
uint64_t formHash(const FormRef& f) {
    uint64_t h = hash64(f.plugin.data(), f.plugin.size());
    return mix(h, f.localId);
}

uint64_t tableDigest(const std::map<NetId, ContainerRec>& cs, const std::map<NetId, WorldItem>& is) {
    uint64_t h = 1469598103934665603ull;
    for (const auto& kv : cs) {
        const ContainerRec& c = kv.second;
        h = mix(h, c.id);
        h = mix(h, static_cast<uint64_t>(c.kind));
        if (!FNV_MUT(135)) h = mix(h, digestOf(c.items));   // twin 135: the contents are not part of the digest (a silent divergence never heals)
    }
    h = mix(h, 0xC0FFEE);
    for (const auto& kv : is) {
        const WorldItem& w = kv.second;
        h = mix(h, w.id);
        h = mix(h, formHash(w.base));
        h = mix(h, static_cast<uint64_t>(w.count));
    }
    return h;
}

bool validBase(const FormRef& b) { return !b.plugin.empty() && b.plugin.size() <= kMaxTextLen; }

bool validRec(const ContainerRec& r) {
    return r.id != 0 && !isIssuedItemId(r.id) && static_cast<uint8_t>(r.kind) < static_cast<uint8_t>(ContainerKind::Count_) && validItems(r.items);
}

bool validPlacedItem(const WorldItem& w, NetId id) {
    return w.id == id && !isIssuedItemId(id) && validBase(w.base) && w.count > 0 && w.count <= kMaxCount;
}

}  // namespace

uint64_t digestOf(const Items& items) {
    uint64_t h = 0x6C6F6F74ull;
    for (const auto& kv : items) {
        if (kv.second <= 0) continue;
        h = mix(h, formHash(kv.first));
        h = mix(h, static_cast<uint64_t>(kv.second));
    }
    return h;
}

std::vector<ItemDiff> diffItems(const Items& a, const Items& b) {
    std::vector<ItemDiff> out;
    auto ia = a.begin();
    auto ib = b.begin();
    auto count = [](const Items::const_iterator& it) { return it->second > 0 ? it->second : 0; };
    while (ia != a.end() || ib != b.end()) {
        if (ib == b.end() || (ia != a.end() && ia->first < ib->first)) {
            if (count(ia) != 0) out.push_back({ia->first, count(ia), 0});
            ++ia;
        } else if (ia == a.end() || ib->first < ia->first) {
            if (count(ib) != 0) out.push_back({ib->first, 0, count(ib)});
            ++ib;
        } else {
            if (count(ia) != count(ib)) out.push_back({ia->first, count(ia), count(ib)});
            ++ia; ++ib;
        }
    }
    return out;
}

std::string describe(const Items& items) {
    std::string s;
    for (const auto& kv : items) {
        if (!s.empty()) s += ", ";
        char b[64];
        std::snprintf(b, sizeof b, ":%06X x%d", kv.first.localId, kv.second);
        s += kv.first.plugin + b;
    }
    return s.empty() ? "(empty)" : s;
}

const char* toString(ContainerKind k) { return k == ContainerKind::Corpse ? "corpse" : "container"; }

const char* toString(OpKind k) {
    static const char* n[] = {"register", "open", "take", "put", "close", "drop", "claim"};
    return static_cast<size_t>(k) < sizeof n / sizeof *n ? n[static_cast<size_t>(k)] : "?";
}

const char* toString(Status s) {
    static const char* n[] = {"ok", "busy", "not-holder", "insufficient", "unknown", "already-taken", "already-resolved", "digest-mismatch", "bad-request", "stale"};
    return static_cast<size_t>(s) < sizeof n / sizeof *n ? n[static_cast<size_t>(s)] : "?";
}

const char* toString(Notice n) {
    static const char* t[] = {"in-use", "already-taken", "container-refused", "container-unknown"};
    return static_cast<size_t>(n) < sizeof t / sizeof *t ? t[static_cast<size_t>(n)] : "?";
}

const char* bodyName(const Body& b) {
    static const char* names[] = {"Register", "Open", "Move", "Close", "Hold", "Drop", "Claim", "Resync", "Digest", "Touch",
                                  "Result", "Container", "Delta", "Lock", "ItemNew", "ItemGone", "ContainerGone", "TableBegin", "TableEnd",
                                  "Clutter", "ClutterOwn"};
    return names[b.index()];
}

bool isRequest(const Body& b) { return b.index() <= 9 || std::holds_alternative<MClutter>(b); }
bool isUnreliable(const Body& b) { return std::holds_alternative<MClutter>(b); }

// ================================================================================================================ host

LootHost::LootHost(HostConfig cfg) : cfg_(cfg) { joined_.insert(kHostPlayer); }

void LootHost::sendAll(const Body& b, Out& out, PlayerId except) {
    for (PlayerId p : joined_)
        if (p != except) out.push_back({p, b});
}

void LootHost::reply(PlayerId to, const Result& r, Out& out) { out.push_back({to, Body(r)}); }

bool LootHost::cached(PlayerId from, uint32_t txn, Result& out) {
    if (FNV_MUT(104)) return false;   // twin 104: no idempotency ledger (a replayed request is applied again)
    Ledger& l = ledgers_[from];
    for (const auto& d : l.done)
        if (d.first == txn) { out = d.second; return true; }
    return false;
}

void LootHost::remember(PlayerId from, uint32_t txn, const Result& r) {
    if (FNV_MUT(107) && r.op == OpKind::Drop) return;   // twin 107: a drop's answer is not remembered (a replay makes a second world item)
    Ledger& l = ledgers_[from];
    l.done.emplace_back(txn, r);
    while (l.done.size() > cfg_.ledgerWindow) {
        l.floor = std::max(l.floor, l.done.front().first);
        l.done.pop_front();
    }
}

void LootHost::playerJoined(PlayerId p, Out& out) {
    if (p >= kMaxSlots) return;
    joined_.insert(p);
    ledgers_.erase(p);
    sendTable(p, out);
}

void LootHost::playerLeft(PlayerId p, uint64_t now, Out& out) {
    (void)now;
    if (p == kHostPlayer) return;
    joined_.erase(p);
    ledgers_.erase(p);
    lastHeal_.erase(p);
    if (!FNV_MUT(112)) {   // twin 112: a leaver's locks stay (the container is closed for everybody until the lease runs out)
        for (auto& kv : containers_) {
            if (kv.second.holder == p) {
                release(kv.second, out);
                ++stats_.locksReleasedOnLeave;
            }
        }
    }
    for (auto& kv : clutter_) {
        if (kv.second.owner != p) continue;
        kv.second.owner = kNoPlayer;
        ++kv.second.epoch;
        kv.second.lastSeq = 0;
        MClutterOwn o;
        o.net = kv.first; o.owner = kNoPlayer; o.epoch = kv.second.epoch; o.pos = kv.second.pos; o.rot = kv.second.rot;
        sendAll(o, out);
    }
}

void LootHost::sendTable(PlayerId to, Out& out) {
    MTableBegin b;
    b.seq = ++tableSeq_;
    out.push_back({to, Body(b)});
    uint32_t nc = 0, ni = 0;
    if (!FNV_MUT(129)) {   // twin 129: the join table leaves the containers out
        for (const auto& kv : containers_) { out.push_back({to, Body(MContainer{kv.second})}); ++nc; }
    }
    for (const auto& kv : items_) { out.push_back({to, Body(MItemNew{kv.second})}); ++ni; }
    for (const auto& kv : clutter_) {
        MClutterOwn o;
        o.net = kv.first; o.owner = kv.second.owner; o.epoch = kv.second.epoch; o.pos = kv.second.pos; o.rot = kv.second.rot;
        out.push_back({to, Body(o)});
    }
    MTableEnd e;
    e.seq = b.seq; e.containers = nc; e.items = ni;
    out.push_back({to, Body(e)});
}

void LootHost::release(ContainerRec& c, Out& out) {
    c.holder = kNoPlayer;
    ++c.lockSeq;
    held_.erase(c.id);
    MLock l;
    l.cont = c.id; l.lockSeq = c.lockSeq; l.holder = kNoPlayer;
    sendAll(l, out);
}

void LootHost::grant(ContainerRec& c, PlayerId to, uint64_t now, Out& out) {
    c.holder = to;
    ++c.lockSeq;
    held_[c.id].lastMs = now;
    MLock l;
    l.cont = c.id; l.lockSeq = c.lockSeq; l.holder = to;
    sendAll(l, out, to);
}

ContainerRec* LootHost::ensure(const ContainerRec& adopt, Out& out, PlayerId except) {
    auto it = containers_.find(adopt.id);
    if (it != containers_.end()) return &it->second;
    if (containers_.size() >= cfg_.maxContainers) return nullptr;
    ContainerRec c = adopt;
    normalize(c.items);
    c.rev = 1;
    c.holder = kNoPlayer;
    c.lockSeq = 0;
    auto res = containers_.emplace(c.id, std::move(c));
    sendAll(MContainer{res.first->second}, out, except);
    return &res.first->second;
}

void LootHost::rememberTaken(NetId id, PlayerId by) {
    recentTaken_[id] = by;
    recentOrder_.push_back(id);
    while (recentOrder_.size() > cfg_.recentTakenMax) {
        recentTaken_.erase(recentOrder_.front());
        recentOrder_.pop_front();
    }
}

void LootHost::onRequest(PlayerId from, const Body& b, uint64_t now, Out& out) {
    if (!joined_.count(from)) { ++stats_.wrongDirection; return; }   // only joined slots may speak
    if (!isRequest(b)) { ++stats_.wrongDirection; return; }
    ++stats_.requests;

    // idempotency: every request with a txn is answered from the ledger when it was seen before
    uint32_t txn = 0;
    OpKind op = OpKind::Open;
    NetId id = 0;
    if (auto* r = std::get_if<ReqRegister>(&b)) { txn = r->txn; op = OpKind::Register; id = r->rec.id; }
    else if (auto* o = std::get_if<ReqOpen>(&b)) { txn = o->txn; op = OpKind::Open; id = o->cont; }
    else if (auto* m = std::get_if<ReqMove>(&b)) { txn = m->txn; op = m->put ? OpKind::Put : OpKind::Take; id = m->cont; }
    else if (auto* c = std::get_if<ReqClose>(&b)) { txn = c->txn; op = OpKind::Close; id = c->cont; }
    else if (auto* d = std::get_if<ReqDrop>(&b)) { txn = d->txn; op = OpKind::Drop; }
    else if (auto* cl = std::get_if<ReqClaim>(&b)) { txn = cl->txn; op = OpKind::Claim; id = cl->item; }
    const bool hasTxn = std::holds_alternative<ReqRegister>(b) || std::holds_alternative<ReqOpen>(b) || std::holds_alternative<ReqMove>(b) ||
                        std::holds_alternative<ReqClose>(b) || std::holds_alternative<ReqDrop>(b) || std::holds_alternative<ReqClaim>(b);
    if (hasTxn) {
        Result r;
        r.txn = txn; r.op = op; r.id = id;
        if (txn == 0) { r.status = Status::BadRequest; ++stats_.badRequest; reply(from, r, out); return; }
        if (cached(from, txn, r)) {
            ++stats_.replays;
            if (r.hasRec && !FNV_MUT(137)) {   // twin 137: a replayed answer carries the state of the first answer (the sender would roll its container back in time)
                auto cur = containers_.find(r.id);
                if (cur != containers_.end()) { r.rec = cur->second; r.rev = cur->second.rev; }
            }
            reply(from, r, out);
            return;
        }
        Ledger& l = ledgers_[from];
        if (!FNV_MUT(104) && txn <= l.floor) { r.status = Status::Stale; ++stats_.stale; reply(from, r, out); return; }
    }

    if (auto* r = std::get_if<ReqRegister>(&b)) doRegister(from, *r, now, out);
    else if (auto* o2 = std::get_if<ReqOpen>(&b)) doOpen(from, *o2, now, out);
    else if (auto* m2 = std::get_if<ReqMove>(&b)) doMove(from, *m2, now, out);
    else if (auto* c2 = std::get_if<ReqClose>(&b)) doClose(from, *c2, now, out);
    else if (auto* h = std::get_if<ReqHold>(&b)) {
        auto it = containers_.find(h->cont);
        if (it != containers_.end() && it->second.holder == from) held_[h->cont].lastMs = now;
    }
    else if (auto* d2 = std::get_if<ReqDrop>(&b)) doDrop(from, *d2, now, out);
    else if (auto* cl2 = std::get_if<ReqClaim>(&b)) doClaim(from, *cl2, now, out);
    else if (auto* rs = std::get_if<ReqResync>(&b)) doResync(from, *rs, out);
    else if (auto* dg = std::get_if<ReqDigest>(&b)) doDigest(from, *dg, now, out);
    else if (auto* t = std::get_if<ReqTouch>(&b)) doTouch(from, *t, now, out);
    else if (auto* cm = std::get_if<MClutter>(&b)) doClutter(from, *cm, now, out);
}

// ---- register: leveled loot is resolved ONCE; the first resolver's real items become the shared contents -------------------------------
void LootHost::doRegister(PlayerId from, const ReqRegister& r, uint64_t now, Out& out) {
    (void)now;
    Result res;
    res.txn = r.txn; res.op = OpKind::Register; res.id = r.rec.id;
    if (!validRec(r.rec)) {
        res.status = Status::BadRequest; ++stats_.badRequest;
        reply(from, res, out);
        return;
    }
    auto it = containers_.find(r.rec.id);
    if (it == containers_.end()) {
        if (!ensure(r.rec, out, from)) {
            res.status = Status::BadRequest; ++stats_.badRequest;
            reply(from, res, out);
            return;
        }
        ++stats_.registered;
        res.rev = containers_[r.rec.id].rev;
    } else if (FNV_MUT(109)) {   // twin 109: a second resolve overwrites the shared contents (each machine's own roll wins in turn)
        it->second.items = r.rec.items;
        normalize(it->second.items);
        ++it->second.rev;
        sendAll(MContainer{it->second}, out, from);
        res.rev = it->second.rev;
    } else {
        ContainerRec& c = it->second;
        res.rev = c.rev;
        Items mine = r.rec.items;
        normalize(mine);
        if (digestOf(mine) != digestOf(c.items)) {
            res.status = Status::AlreadyResolved;
            res.hasRec = !FNV_MUT(130);   // twin 130: the refusal does not carry the shared contents (the sender keeps its own roll)
            res.rec = c;
            ++stats_.alreadyResolved;
        }
    }
    remember(from, r.txn, res);
    reply(from, res, out);
}

// ---- open: one lock per container ------------------------------------------------------------------------------------------------------
void LootHost::doOpen(PlayerId from, const ReqOpen& r, uint64_t now, Out& out) {
    Result res;
    res.txn = r.txn; res.op = OpKind::Open; res.id = r.cont;
    ContainerRec* c = nullptr;
    auto it = containers_.find(r.cont);
    if (it != containers_.end()) c = &it->second;
    else if (r.hasAdopt && r.adopt.id == r.cont && validRec(r.adopt)) {
        c = ensure(r.adopt, out, from);
        if (c) ++stats_.adopted;
    }
    if (!c) {
        res.status = (r.hasAdopt && !validRec(r.adopt)) ? Status::BadRequest : Status::Unknown;
        reply(from, res, out);
        remember(from, r.txn, res);
        return;
    }
    ++stats_.opens;
    if (c->holder != kNoPlayer && c->holder != from) {
        const bool leaseOver = !FNV_MUT(113) && now >= held_[c->id].lastMs && now - held_[c->id].lastMs > cfg_.lockLeaseMs;
        if (leaseOver || !joined_.count(c->holder)) {
            release(*c, out);
            ++stats_.locksExpired;
        } else if (!FNV_MUT(101)) {   // twin 101: the lock is not enforced (two players hold the same container)
            res.status = Status::Busy; res.other = c->holder; res.hasRec = true; res.rec = *c; res.rev = c->rev;
            ++stats_.busy;
            reply(from, res, out);
            remember(from, r.txn, res);
            return;
        }
    }
    if (c->holder != from) grant(*c, from, now, out);
    else held_[c->id].lastMs = now;
    res.rev = c->rev; res.hasRec = true; res.rec = *c;
    remember(from, r.txn, res);
    reply(from, res, out);
}

// ---- take / put ----------------------------------------------------------------------------------------------------------------------------
void LootHost::doMove(PlayerId from, const ReqMove& r, uint64_t now, Out& out) {
    Result res;
    res.txn = r.txn; res.op = r.put ? OpKind::Put : OpKind::Take; res.id = r.cont; res.base = r.base; res.count = r.count;
    auto fail = [&](Status s, const ContainerRec* c) {
        res.status = s;
        if (c) { res.hasRec = true; res.rec = *c; res.rev = c->rev; }
        ++stats_.refused;
        remember(from, r.txn, res);
        reply(from, res, out);
    };
    if (!validBase(r.base) || r.count <= 0 || r.count > kMaxCount) { ++stats_.badRequest; fail(Status::BadRequest, nullptr); return; }
    auto it = containers_.find(r.cont);
    if (it == containers_.end()) { fail(Status::Unknown, nullptr); return; }
    ContainerRec& c = it->second;
    if (c.holder != from && !FNV_MUT(102)) {   // twin 102: a take / put from a player who does not hold the lock is applied
        ++stats_.notHolder;
        fail(Status::NotHolder, &c);
        return;
    }
    if (c.holder == from) held_[c.id].lastMs = now;
    const int32_t have = [&] { auto f = c.items.find(r.base); return f == c.items.end() ? 0 : f->second; }();
    int32_t used = r.count;
    int32_t after = 0;
    if (!r.put) {
        if (have < r.count) {
            if (!FNV_MUT(103)) { ++stats_.insufficient; fail(Status::Insufficient, &c); return; }
            // twin 103: a take beyond the stock is granted in full (the player keeps items the container never had)
            after = 0;
        } else {
            after = have - r.count;
        }
        ++stats_.takes;
    } else {
        if (static_cast<int64_t>(have) + r.count > kMaxCount || (have == 0 && c.items.size() >= kMaxStacks)) { ++stats_.badRequest; fail(Status::BadRequest, &c); return; }
        after = have + r.count;
        ++stats_.puts;
    }
    if (after == 0) c.items.erase(r.base);
    else c.items[r.base] = after;
    ++c.rev;
    res.count = used;
    res.rev = c.rev;
    MDelta d;
    d.cont = c.id; d.rev = c.rev; d.base = r.base; d.newCount = after;
    if (!FNV_MUT(127)) sendAll(d, out, from);   // twin 127: the others are not told about the change (their copies diverge until the digest heals them)
    remember(from, r.txn, res);
    reply(from, res, out);
}

// ---- close: the digest is checked ------------------------------------------------------------------------------------------------------
void LootHost::doClose(PlayerId from, const ReqClose& r, uint64_t now, Out& out) {
    (void)now;
    Result res;
    res.txn = r.txn; res.op = OpKind::Close; res.id = r.cont;
    auto it = containers_.find(r.cont);
    if (it == containers_.end()) {
        res.status = Status::Unknown;
        remember(from, r.txn, res);
        reply(from, res, out);
        return;
    }
    ContainerRec& c = it->second;
    res.rev = c.rev;
    ++stats_.closes;
    if (c.holder != from) {
        res.status = Status::NotHolder; res.hasRec = true; res.rec = c;   // the lock was lost (lease, leave): heal the closer's copy
        ++stats_.notHolder;
    } else {
        if (!FNV_MUT(108) && r.digest != digestOf(c.items)) {   // twin 108: the digest is never compared
            res.status = Status::DigestMismatch; res.hasRec = true; res.rec = c;
            ++stats_.closeMismatch;
        }
        if (!FNV_MUT(133)) release(c, out);   // twin 133: closing does not release the lock
    }
    remember(from, r.txn, res);
    reply(from, res, out);
}

// ---- drops ----------------------------------------------------------------------------------------------------------------------------------
void LootHost::doDrop(PlayerId from, const ReqDrop& r, uint64_t now, Out& out) {
    Result res;
    res.txn = r.txn; res.op = OpKind::Drop;
    if (!validBase(r.base) || r.count <= 0 || r.count > kMaxCount || items_.size() >= cfg_.maxItems || nextItemId_ > kItemIdEnd) {
        res.status = Status::BadRequest; ++stats_.badRequest;
        remember(from, r.txn, res);
        reply(from, res, out);
        return;
    }
    WorldItem w;
    w.id = nextItemId_++;
    w.base = r.base; w.count = r.count; w.meta = r.meta; w.cell = r.cell; w.pos = r.pos; w.rot = r.rot;
    w.dropper = from; w.dropTxn = r.txn;
    items_[w.id] = w;
    ++stats_.drops;
    claimClutter(w.id, from, r.pos, r.rot, now, out);
    res.id = w.id; res.newId = w.id; res.base = w.base; res.count = w.count;
    remember(from, r.txn, res);
    reply(from, res, out);
    sendAll(MItemNew{w}, out);   // the dropper too: it binds its own local ref to the issued id
}

// ---- pickups are claims; first claim wins -------------------------------------------------------------------------------------------------
void LootHost::doClaim(PlayerId from, const ReqClaim& r, uint64_t now, Out& out) {
    (void)now;
    Result res;
    res.txn = r.txn; res.op = OpKind::Claim; res.id = r.item;
    auto finish = [&]() { remember(from, r.txn, res); reply(from, res, out); };
    if (r.item == 0) { res.status = Status::BadRequest; ++stats_.badRequest; finish(); return; }
    auto it = items_.find(r.item);
    if (it != items_.end()) {
        WorldItem w = it->second;
        if (!FNV_MUT(105)) items_.erase(it);   // twin 105: the claimed item stays in the table (the next claim wins too)
        rememberTaken(r.item, from);
        clutter_.erase(r.item);
        res.base = w.base; res.count = w.count;
        ++stats_.claimsWon;
        MItemGone g;
        g.item = r.item; g.by = from; g.txn = r.txn;
        if (!FNV_MUT(126)) sendAll(g, out, from);   // twin 126: nobody else is told the item is gone (their world refs stay; losers are not warned)
        finish();
        return;
    }
    if (!isIssuedItemId(r.item)) {   // a placed item: the host learns it at the first claim
        auto tomb = placedTaken_.find(r.item);
        if (tomb != placedTaken_.end()) {
            res.status = Status::AlreadyTaken; res.other = tomb->second;
            ++stats_.claimsLost;
            finish();
            return;
        }
        if (r.hasAdopt && validPlacedItem(r.adopt, r.item) && placedTaken_.size() < cfg_.maxPlacedTaken) {
            if (!FNV_MUT(115)) placedTaken_[r.item] = from;   // twin 115: no tombstone (the same placed item can be picked up again and again)
            res.base = r.adopt.base; res.count = r.adopt.count;
            ++stats_.claimsWon; ++stats_.placedClaims;
            MItemGone g;
            g.item = r.item; g.by = from; g.txn = r.txn;
            sendAll(g, out, from);
            finish();
            return;
        }
        res.status = Status::Unknown;
        ++stats_.claimsLost;
        finish();
        return;
    }
    auto rt = recentTaken_.find(r.item);
    if (rt != recentTaken_.end()) {
        res.status = Status::AlreadyTaken; res.other = rt->second;
        ++stats_.claimsLost;
        finish();
        return;
    }
    if (FNV_MUT(106) && r.hasAdopt) {   // twin 106: a claim for an issued id the host does not know is believed
        res.base = r.adopt.base; res.count = r.adopt.count;
        ++stats_.claimsWon;
        finish();
        return;
    }
    res.status = Status::Unknown;
    ++stats_.claimsLost;
    finish();
}

// ---- clutter ----------------------------------------------------------------------------------------------------------------------------------
void LootHost::claimClutter(NetId net, PlayerId owner, const Vec3& pos, const Vec3& rot, uint64_t now, Out& out) {
    Clutter& c = clutter_[net];
    c.owner = owner;
    ++c.epoch;
    c.lastSeq = 0;
    c.lastMs = now;
    c.moving = true;
    c.pos = pos; c.rot = rot;
    MClutterOwn o;
    o.net = net; o.owner = owner; o.epoch = c.epoch; o.pos = pos; o.rot = rot;
    sendAll(o, out);
}

void LootHost::doTouch(PlayerId from, const ReqTouch& r, uint64_t now, Out& out) {
    if (r.net == 0) { ++stats_.badRequest; return; }
    auto it = clutter_.find(r.net);
    if (it == clutter_.end()) {
        if (clutter_.size() >= cfg_.maxItems * 4u) { ++stats_.badRequest; return; }
        ++stats_.clutterTracked;
        claimClutter(r.net, from, r.pos, r.rot, now, out);
        return;
    }
    Clutter& c = it->second;
    if (c.owner == from) return;
    const bool idle = !c.moving || now < c.lastMs || now - c.lastMs >= cfg_.restMs || !joined_.count(c.owner) || c.owner == kNoPlayer;
    if (idle || FNV_MUT(119)) {   // twin 119: a second mover takes the object away from the player who is moving it
        ++stats_.clutterTakeovers;
        claimClutter(r.net, from, r.pos, r.rot, now, out);
    } else {
        ++stats_.clutterDenied;
        MClutterOwn o;
        o.net = r.net; o.owner = c.owner; o.epoch = c.epoch; o.pos = c.pos; o.rot = c.rot;
        out.push_back({from, Body(o)});   // tells the asker who owns it now
    }
}

void LootHost::doClutter(PlayerId from, const MClutter& m, uint64_t now, Out& out) {
    auto it = clutter_.find(m.net);
    if (it == clutter_.end()) { ++stats_.clutterRejected; return; }
    Clutter& c = it->second;
    if (c.owner != from && !FNV_MUT(117)) { ++stats_.clutterRejected; return; }   // twin 117: anybody may publish a pose for anything
    if (m.epoch != c.epoch && !(FNV_MUT(116) && m.epoch < c.epoch)) { ++stats_.clutterRejected; return; }   // twin 116: an older epoch is believed
    if (m.seq <= c.lastSeq && m.epoch == c.epoch) { ++stats_.clutterRejected; return; }
    c.lastSeq = m.seq;
    c.lastMs = now;
    c.moving = m.moving;
    c.pos = m.pos; c.rot = m.rot;
    auto wi = items_.find(m.net);
    if (wi != items_.end()) { wi->second.pos = m.pos; wi->second.rot = m.rot; }
    ++stats_.clutterRelayed;
    sendAll(m, out, from);
}

// ---- heal ------------------------------------------------------------------------------------------------------------------------------------
void LootHost::doDigest(PlayerId from, const ReqDigest& r, uint64_t now, Out& out) {
    ++stats_.digestsSeen;
    if (r.digest == digest()) return;
    ++stats_.digestMismatch;
    auto it = lastHeal_.find(from);
    if (it != lastHeal_.end() && now >= it->second && now - it->second < cfg_.healMinMs) return;
    lastHeal_[from] = now;
    ++stats_.healsSent;
    sendTable(from, out);
}

void LootHost::doResync(PlayerId from, const ReqResync& r, Out& out) {
    ++stats_.resyncsAnswered;
    if (r.all) { sendTable(from, out); return; }
    auto it = containers_.find(r.cont);
    if (it != containers_.end()) out.push_back({from, Body(MContainer{it->second})});
    else out.push_back({from, Body(MContainerGone{r.cont})});
}

bool LootHost::externalSet(NetId cont, const Items& items, uint64_t now, Out& out) {
    (void)now;
    auto it = containers_.find(cont);
    if (it == containers_.end() || !validItems(items)) { ++stats_.externalRefused; return false; }
    ContainerRec& c = it->second;
    if (c.holder != kNoPlayer && !FNV_MUT(128)) { ++stats_.externalRefused; return false; }   // twin 128: a script's change overwrites what the holder is working on
    Items want = items;
    normalize(want);
    if (digestOf(want) == digestOf(c.items)) return true;
    c.items = std::move(want);
    ++c.rev;
    ++stats_.externalSets;
    sendAll(MContainer{c}, out);
    return true;
}

void LootHost::resetCell(CellId cell, Out& out) {
    for (auto it = containers_.begin(); it != containers_.end();) {
        if (it->second.cell == cell && it->second.holder == kNoPlayer) {
            sendAll(MContainerGone{it->first}, out);
            held_.erase(it->first);
            it = containers_.erase(it);
            ++stats_.cellResets;
        } else {
            ++it;
        }
    }
}

void LootHost::tick(uint64_t now, Out& out) {
    if (FNV_MUT(113)) return;   // twin 113: a lock never runs out
    std::vector<NetId> expired;
    for (const auto& kv : held_)
        if (now >= kv.second.lastMs && now - kv.second.lastMs > cfg_.lockLeaseMs) expired.push_back(kv.first);
    for (NetId id : expired) {
        auto it = containers_.find(id);
        if (it == containers_.end()) { held_.erase(id); continue; }
        release(it->second, out);
        ++stats_.locksExpired;
    }
}

const ContainerRec* LootHost::container(NetId id) const {
    auto it = containers_.find(id);
    return it == containers_.end() ? nullptr : &it->second;
}
const WorldItem* LootHost::item(NetId id) const {
    auto it = items_.find(id);
    return it == items_.end() ? nullptr : &it->second;
}
bool LootHost::placedTaken(NetId id, PlayerId* by) const {
    auto it = placedTaken_.find(id);
    if (it == placedTaken_.end()) return false;
    if (by) *by = it->second;
    return true;
}
PlayerId LootHost::clutterOwner(NetId net, uint32_t* epoch) const {
    auto it = clutter_.find(net);
    if (it == clutter_.end()) return kNoPlayer;
    if (epoch) *epoch = it->second.epoch;
    return it->second.owner;
}
uint64_t LootHost::digest() const { return tableDigest(containers_, items_); }

std::vector<uint8_t> LootHost::save() const {
    Archive a = Archive::writer();
    uint8_t ver = 1;
    a.u8(ver);
    uint32_t next = nextItemId_;
    a.varu32(next);
    size_t n = containers_.size();
    a.count(n, kMaxTableRecords);
    for (const auto& kv : containers_) {
        ContainerRec c = kv.second;
        c.holder = kNoPlayer;
        io(a, c);
    }
    n = items_.size();
    a.count(n, kMaxTableRecords);
    for (const auto& kv : items_) { WorldItem w = kv.second; io(a, w); }
    n = placedTaken_.size();
    a.count(n, kMaxTableRecords);
    for (const auto& kv : placedTaken_) {
        uint32_t id = kv.first;
        uint8_t by = kv.second;
        a.varu32(id); a.u8(by);
    }
    return a.finish();
}

bool LootHost::load(const std::vector<uint8_t>& bytes) {
    Archive a = Archive::reader(bytes.data(), bytes.size());
    uint8_t ver = 0;
    a.u8(ver);
    if (!a.ok() || ver != 1) return false;
    uint32_t next = 0;
    a.varu32(next);
    std::map<NetId, ContainerRec> cs;
    std::map<NetId, WorldItem> is;
    std::map<NetId, PlayerId> pt;
    size_t n = 0;
    if (!a.count(n, kMaxTableRecords)) return false;
    for (size_t i = 0; i < n; ++i) {
        ContainerRec c;
        io(a, c);
        if (!a.ok() || !validRec(c)) return false;
        c.holder = kNoPlayer;
        cs[c.id] = std::move(c);
    }
    if (!a.count(n, kMaxTableRecords)) return false;
    for (size_t i = 0; i < n; ++i) {
        WorldItem w;
        io(a, w);
        if (!a.ok()) return false;
        is[w.id] = std::move(w);
    }
    if (!a.count(n, kMaxTableRecords)) return false;
    for (size_t i = 0; i < n; ++i) {
        uint32_t id = 0;
        uint8_t by = 0;
        a.varu32(id); a.u8(by);
        if (!a.ok()) return false;
        pt[id] = by;
    }
    if (!a.ok() || !a.atEnd()) return false;
    containers_ = std::move(cs);
    items_ = std::move(is);
    placedTaken_ = std::move(pt);
    held_.clear();
    clutter_.clear();
    recentTaken_.clear();
    recentOrder_.clear();
    ledgers_.clear();
    NetId floor = next;
    for (const auto& kv : items_) floor = std::max<NetId>(floor, kv.first + 1);
    nextItemId_ = FNV_MUT(114) ? kItemIdBase : std::max<NetId>(floor, kItemIdBase);   // twin 114: the id counter restarts (a new drop reuses an old id)
    return true;
}

// ================================================================================================================ client

LootClient::LootClient(ClientConfig cfg, Send send) : cfg_(cfg), send_(std::move(send)) {}

void LootClient::sendBody(const Body& b) {
    if (send_) send_(b);
}

void LootClient::hostLinkUp(uint64_t now) {
    up_ = true;
    txn_ = 0;
    pending_.clear();
    inTable_ = false;
    lastDigestMs_ = now;
    lastRxMs_ = now;
    ReqResync r;
    r.all = true;
    sendBody(r);
}

void LootClient::worldReset() {
    // a save was loaded (or the world was torn down): every engine ref is gone, the host's table is fetched again
    pending_.clear();
    containers_.clear();
    items_.clear();
    local_.clear();
    dropRef_.clear();
    boundEarly_.clear();
    deferredPickup_.clear();
    materialized_.clear();
    gone_.clear();
    goneOrder_.clear();
    owned_.clear();
    seenClutter_.clear();
    actions_.clear();
    inTable_ = false;
}

void LootClient::hostLinkDown() {
    up_ = false;
    stats_.abandoned += pending_.size();
    pending_.clear();
    for (auto& kv : local_) kv.second.granted = false;
}

uint32_t LootClient::issue(Body body, uint32_t txn, OpKind op, NetId id, uint64_t now, Pending p) {
    p.body = std::move(body);
    p.op = op; p.id = id;
    p.firstMs = p.lastMs = now;
    pending_[txn] = p;
    ++stats_.sent;
    if (!up_) {   // nobody to ask: the request fails at once (and rolls back like a refusal)
        Result r;
        r.txn = txn; r.op = op; r.id = id; r.status = Status::Unknown;
        onResult(r, now);
        return txn;
    }
    sendBody(p.body);
    return txn;
}

size_t LootClient::pendingFor(NetId cont) const {
    size_t n = 0;
    for (const auto& kv : pending_)
        if (kv.second.id == cont && kv.second.op != OpKind::Drop && kv.second.op != OpKind::Claim) ++n;
    return n;
}

Items LootClient::expected(NetId cont, const Items& authoritative) const {
    Items out = authoritative;
    if (FNV_MUT(136)) return out;   // twin 136: the player's not-yet-confirmed moves are forgotten when contents are replaced
    // apply in txn order: the host applies them in this order after the answer we are looking at
    for (const auto& kv : pending_) {
        const Pending& p = kv.second;
        if (p.id != cont || (p.op != OpKind::Take && p.op != OpKind::Put)) continue;
        int32_t& c = out[p.base];
        c += p.put ? p.count : -p.count;
        if (c <= 0) out.erase(p.base);
    }
    return out;
}

void LootClient::replaceContents(NetId cont, const Items& want) {
    Local& l = local_[cont];
    Items w = want;
    normalize(w);
    l.baseline = w;
    Action a;
    a.kind = Action::ContainerSet; a.id = cont; a.items = std::move(w);
    push(std::move(a));
    ++stats_.contentsReplaced;
}

// ---- engine events ----------------------------------------------------------------------------------------------------------------------

void LootClient::resolved(NetId cont, ContainerKind kind, CellId cell, const Vec3& pos, const Items& items, uint64_t now) {
    ReqRegister r;
    r.txn = nextTxn();
    r.rec.id = cont; r.rec.kind = kind; r.rec.cell = cell; r.rec.pos = pos; r.rec.items = items;
    normalize(r.rec.items);
    ++stats_.registers;
    Pending p;
    issue(r, r.txn, OpKind::Register, cont, now, p);
}

void LootClient::opened(NetId cont, ContainerKind kind, CellId cell, const Vec3& pos, const Items& items, uint64_t now) {
    Local& l = local_[cont];
    l.open = true; l.granted = false; l.closeWanted = false; l.closeSent = false; l.readyAsked = false;
    l.baseline = items;
    normalize(l.baseline);
    l.lastHoldMs = now;
    ReqOpen o;
    o.txn = nextTxn();
    o.cont = cont;
    if (!containers_.count(cont)) {
        o.hasAdopt = true;
        o.adopt.id = cont; o.adopt.kind = kind; o.adopt.cell = cell; o.adopt.pos = pos; o.adopt.items = l.baseline;
    }
    ++stats_.opens;
    Pending p;
    issue(o, o.txn, OpKind::Open, cont, now, p);
}

void LootClient::observed(NetId cont, const Items& itemsIn, uint64_t now) {
    auto it = local_.find(cont);
    if (it == local_.end() || (!it->second.open && !it->second.closeWanted)) return;
    Local& l = it->second;
    Items items = itemsIn;
    normalize(items);
    // what changed since the last look: less in the container = the player TOOK it, more = the player PUT it
    std::vector<ItemDiff> d = diffItems(l.baseline, items);
    l.baseline = items;
    for (const ItemDiff& x : d) {
        ReqMove m;
        m.txn = nextTxn();
        m.cont = cont; m.base = x.base;
        m.put = x.b > x.a;
        m.count = m.put ? x.b - x.a : x.a - x.b;
        if (m.put) ++stats_.puts; else ++stats_.takes;
        Pending p;
        p.base = x.base; p.count = m.count; p.put = m.put;
        issue(m, m.txn, m.put ? OpKind::Put : OpKind::Take, cont, now, p);
    }
}

void LootClient::closed(NetId cont, const Items& items, uint64_t now) {
    auto it = local_.find(cont);
    if (it == local_.end()) return;
    observed(cont, items, now);
    it->second.open = false;
    it->second.closeWanted = true;
    maybeReadyToClose(cont);
}

void LootClient::maybeReadyToClose(NetId cont) {
    auto it = local_.find(cont);
    if (it == local_.end()) return;
    Local& l = it->second;
    if (!l.closeWanted || l.closeSent || l.readyAsked) return;
    if (pendingFor(cont) != 0 && !FNV_MUT(134)) return;   // twin 134: the close (and its digest) goes out while moves are still unanswered
    l.readyAsked = true;
    Action a;
    a.kind = Action::ReadyToClose; a.id = cont;
    push(std::move(a));
}

void LootClient::closeNow(NetId cont, const Items& itemsIn, uint64_t now) {
    auto it = local_.find(cont);
    if (it == local_.end() || !it->second.closeWanted || it->second.closeSent) return;
    Local& l = it->second;
    Items items = itemsIn;
    normalize(items);
    l.closeSent = true;
    l.baseline = items;
    ReqClose c;
    c.txn = nextTxn();
    c.cont = cont;
    c.digest = digestOf(items);
    ++stats_.closes;
    Pending p;
    issue(c, c.txn, OpKind::Close, cont, now, p);
}

void LootClient::rebaseline(NetId cont, const Items& items) {
    auto it = local_.find(cont);
    if (it == local_.end()) return;
    it->second.baseline = items;
    normalize(it->second.baseline);
}

bool LootClient::audit(NetId cont, const Items& engineItems, uint64_t now) {
    (void)now;
    auto c = containers_.find(cont);
    if (c == containers_.end()) return false;
    auto l = local_.find(cont);
    if (l != local_.end() && (l->second.open || l->second.closeWanted || l->second.closeSent)) return false;
    if (pendingFor(cont) != 0) return false;
    Items have = engineItems;
    normalize(have);
    if (digestOf(have) == digestOf(c->second.items)) return false;
    ++stats_.auditCorrections;
    replaceContents(cont, c->second.items);
    return true;
}

uint32_t LootClient::dropped(uint32_t localRef, const FormRef& base, int32_t count, uint32_t meta, CellId cell, const Vec3& pos, const Vec3& rot, uint64_t now) {
    ReqDrop d;
    d.txn = nextTxn();
    d.base = base; d.count = count; d.meta = meta; d.cell = cell; d.pos = pos; d.rot = rot;
    dropRef_[d.txn] = localRef;
    ++stats_.drops;
    Pending p;
    p.ref = localRef; p.base = base; p.count = count;
    issue(d, d.txn, OpKind::Drop, 0, now, p);
    return d.txn;
}

void LootClient::pickedUp(uint32_t localRef, NetId item, const FormRef& base, int32_t count, uint32_t meta, CellId cell, const Vec3& pos, uint64_t now) {
    if (item == 0) {   // a dropped item of ours that has no issued id yet: ask once it has one
        for (const auto& kv : dropRef_) {
            if (kv.second == localRef) {
                DeferredPick d;
                d.base = base; d.count = count; d.meta = meta; d.cell = cell; d.pos = pos;
                deferredPickup_[localRef] = d;
                return;
            }
        }
        return;
    }
    ReqClaim c;
    c.txn = nextTxn();
    c.item = item;
    if (!isIssuedItemId(item)) {
        c.hasAdopt = true;
        c.adopt.id = item; c.adopt.base = base; c.adopt.count = count; c.adopt.meta = meta; c.adopt.cell = cell; c.adopt.pos = pos;
    }
    ++stats_.claims;
    Pending p;
    p.ref = localRef; p.base = base; p.count = count;
    issue(c, c.txn, OpKind::Claim, item, now, p);
}

void LootClient::touch(NetId net, const Vec3& pos, const Vec3& rot, uint64_t now) {
    if (!up_ || net == 0 || owned_.count(net)) return;
    auto t = touchedMs_.find(net);
    if (t != touchedMs_.end() && now >= t->second && now - t->second < 300) return;
    touchedMs_[net] = now;
    ReqTouch r;
    r.net = net; r.pos = pos; r.rot = rot;
    sendBody(r);
}

namespace {
float angDiff(const Vec3& a, const Vec3& b) {
    auto wrap = [](float d) { while (d > 3.14159265f) d -= 6.2831853f; while (d < -3.14159265f) d += 6.2831853f; return std::fabs(d); };
    return std::max({wrap(a.x - b.x), wrap(a.y - b.y), wrap(a.z - b.z)});
}
}  // namespace

void LootClient::clutterSamples(const std::vector<Sample>& samples, uint64_t now) {
    if (!up_) return;
    const ClutterTuning& t = cfg_.clutter;
    for (const Sample& s : samples) {
        auto it = owned_.find(s.net);
        if (it == owned_.end()) continue;
        Own& o = it->second;
        const bool moved = !o.sentOnce || distance(s.pos, o.lastPos) > t.posEps || angDiff(s.rot, o.lastRot) > t.rotEps;
        auto emit = [&](bool moving) {
            MClutter m;
            m.net = s.net; m.epoch = o.epoch; m.seq = ++o.seq; m.pos = s.pos; m.rot = s.rot; m.moving = moving;
            o.lastPos = s.pos; o.lastRot = s.rot; o.sentOnce = true; o.lastSendMs = now; o.moving = moving;
            ++stats_.clutterSent;
            sendBody(m);
        };
        if (moved) {
            o.lastMoveMs = now;
            if (!o.sentOnce || now < o.lastSendMs || now - o.lastSendMs >= t.minIntervalMs) emit(true);
        } else if (o.moving && now >= o.lastMoveMs && now - o.lastMoveMs >= t.restMs) {
            emit(false);   // the one "at rest" datagram; after it nothing is sent until the object moves again
        } else if (FNV_MUT(118) && now - o.lastSendMs >= t.minIntervalMs) {
            emit(o.moving);   // twin 118: a resting object keeps being sent
        }
    }
}

// ---- messages from the host ---------------------------------------------------------------------------------------------------------------

void LootClient::onMessage(const Body& b, uint64_t now) {
    lastRxMs_ = now;
    if (isRequest(b) && !std::holds_alternative<MClutter>(b)) { ++stats_.wrongDirection; return; }
    if (auto* r = std::get_if<Result>(&b)) onResult(*r, now);
    else if (auto* c = std::get_if<MContainer>(&b)) applyRecord(c->rec, inTable_, true, now);
    else if (auto* d = std::get_if<MDelta>(&b)) applyDelta(*d, now);
    else if (auto* l = std::get_if<MLock>(&b)) {
        auto it = containers_.find(l->cont);
        if (it == containers_.end()) { askResync(l->cont, now); return; }
        if (l->lockSeq <= it->second.lockSeq) return;
        it->second.lockSeq = l->lockSeq;
        it->second.holder = l->holder;
        auto lc = local_.find(l->cont);
        if (lc != local_.end() && lc->second.open && lc->second.granted && l->holder != cfg_.self) {   // our lock ran out while the menu was open
            lc->second.granted = false;
            lc->second.open = false;   // the glue closes the menu for us (CloseMenu): it does not report that as a close
            Action a;
            a.kind = Action::CloseMenu; a.id = l->cont;
            push(std::move(a));
            Action n;
            n.kind = Action::Notify; n.id = l->cont; n.notice = Notice::InUse;
            push(std::move(n));
        }
    }
    else if (auto* n = std::get_if<MItemNew>(&b)) {
        const WorldItem& w = n->item;
        if (gone_.count(w.id)) return;
        items_[w.id] = w;
        if (inTable_) tableItems_.insert(w.id);
        bindDrop(w);
    }
    else if (auto* g = std::get_if<MItemGone>(&b)) forgetItem(g->item, g->by);
    else if (auto* cg = std::get_if<MContainerGone>(&b)) {
        auto lc = local_.find(cg->cont);
        if (lc == local_.end() || !lc->second.open) containers_.erase(cg->cont);
    }
    else if (auto* tb = std::get_if<MTableBegin>(&b)) {
        (void)tb;
        inTable_ = true;
        tableStartMs_ = now;
        tableContainers_.clear();
        tableItems_.clear();
    }
    else if (auto* te = std::get_if<MTableEnd>(&b)) {
        if (inTable_ && (tableContainers_.size() != te->containers || tableItems_.size() != te->items)) {
            inTable_ = false;   // a record of the table never arrived: nothing may be pruned on the strength of an incomplete list
            ++stats_.tablesIncomplete;
        }
        if (inTable_) {
            for (auto it = containers_.begin(); it != containers_.end();) {
                auto lc = local_.find(it->first);
                const bool busy = lc != local_.end() && (lc->second.open || lc->second.closeWanted);
                if (!tableContainers_.count(it->first) && !busy) it = containers_.erase(it);
                else ++it;
            }
            for (auto it = items_.begin(); it != items_.end();) {
                if (!tableItems_.count(it->first)) {
                    Action a;
                    a.kind = Action::RemoveItem; a.id = it->first;
                    push(std::move(a));
                    materialized_.erase(it->first);
                    it = items_.erase(it);
                } else {
                    ++it;
                }
            }
            inTable_ = false;
            ++stats_.tablesApplied;
        }
    }
    else if (auto* cl = std::get_if<MClutter>(&b)) onClutter(*cl);
    else if (auto* co = std::get_if<MClutterOwn>(&b)) onClutterOwn(*co);
}

void LootClient::bindDrop(const WorldItem& w) {
    if (materialized_.count(w.id)) return;   // a ref for this item exists (a table pushed again, a duplicated datagram)
    if (w.dropper == cfg_.self && !FNV_MUT(132)) {   // twin 132: our own drop is created again as if somebody else had dropped it
        auto d = dropRef_.find(w.dropTxn);
        if (d != dropRef_.end()) {
            Action a;
            a.kind = Action::BindDrop; a.id = w.id; a.ref = d->second;
            push(std::move(a));
            ++stats_.bound;
            materialized_.insert(w.id);
            const uint32_t ref = d->second;
            dropRef_.erase(d);
            auto dp = deferredPickup_.find(ref);
            if (dp != deferredPickup_.end()) {
                DeferredPick p = dp->second;
                deferredPickup_.erase(dp);
                pickedUp(ref, w.id, p.base, p.count, p.meta, p.cell, p.pos, lastRxMs_);
            }
            return;
        }
        auto e = boundEarly_.find(w.id);
        if (e != boundEarly_.end()) {
            Action a;
            a.kind = Action::BindDrop; a.id = w.id; a.ref = e->second;
            push(std::move(a));
            ++stats_.bound;
            materialized_.insert(w.id);
            const uint32_t ref = e->second;
            boundEarly_.erase(e);
            auto dp = deferredPickup_.find(ref);
            if (dp != deferredPickup_.end()) {
                DeferredPick p = dp->second;
                deferredPickup_.erase(dp);
                pickedUp(ref, w.id, p.base, p.count, p.meta, p.cell, p.pos, lastRxMs_);
            }
            return;
        }
    }
    Action a;
    a.kind = Action::CreateItem; a.id = w.id; a.item = w;
    push(std::move(a));
    materialized_.insert(w.id);
    ++stats_.created;
}

void LootClient::forgetItem(NetId id, PlayerId by) {
    items_.erase(id);
    materialized_.erase(id);
    gone_.insert(id);
    goneOrder_.push_back(id);
    while (goneOrder_.size() > cfg_.goneMemory) { gone_.erase(goneOrder_.front()); goneOrder_.pop_front(); }
    if (by == cfg_.self) return;   // our own claim: the engine already removed the ref
    Action a;
    a.kind = Action::RemoveItem; a.id = id;
    push(std::move(a));
    ++stats_.removed;
}

void LootClient::askResync(NetId cont, uint64_t now) {
    if (!up_) return;
    auto it = lastResync_.find(cont);
    if (it != lastResync_.end() && now >= it->second && now - it->second < cfg_.resyncMinMs) return;
    lastResync_[cont] = now;
    ++stats_.resyncsAsked;
    ReqResync r;
    r.cont = cont;
    sendBody(r);
}

void LootClient::applyRecord(const ContainerRec& rec, bool fromTable, bool eager, uint64_t now) {
    (void)now;
    auto it = containers_.find(rec.id);
    const bool had = it != containers_.end();
    if (had && !fromTable && rec.rev < it->second.rev) return;   // an older full record
    const bool changed = !had || digestOf(it->second.items) != digestOf(rec.items);
    uint32_t keepSeq = had ? it->second.lockSeq : 0;
    PlayerId keepHolder = had ? it->second.holder : kNoPlayer;
    ContainerRec& cur = containers_[rec.id];
    cur = rec;
    if (had && keepSeq > rec.lockSeq) { cur.lockSeq = keepSeq; cur.holder = keepHolder; }
    if (fromTable) tableContainers_.insert(rec.id);
    auto lc = local_.find(rec.id);
    const bool openHere = lc != local_.end() && (lc->second.open || lc->second.closeWanted);
    // somebody else holds it now and we thought the lock was ours (the lease ran out, or the MLock was lost): the menu must not go on
    if (lc != local_.end() && lc->second.open && lc->second.granted && cur.holder != kNoPlayer && cur.holder != cfg_.self) {
        lc->second.granted = false;
        lc->second.open = false;
        Action a;
        a.kind = Action::CloseMenu; a.id = rec.id;
        push(std::move(a));
        Action n;
        n.kind = Action::Notify; n.id = rec.id; n.notice = Notice::InUse;
        push(std::move(n));
    }
    // a table never rewrites containers that are new to this machine (they are replaced when opened), but a container whose contents
    // changed under a machine that missed the change (a cut link) is corrected
    if (changed && eager && cfg_.eagerApply && !openHere && (!fromTable || had)) {
        Action a;
        a.kind = Action::ContainerSet; a.id = rec.id; a.items = rec.items;
        push(std::move(a));
    }
}

void LootClient::applyDelta(const MDelta& d, uint64_t now) {
    auto it = containers_.find(d.cont);
    if (it == containers_.end()) { askResync(d.cont, now); return; }
    ContainerRec& c = it->second;
    if (d.rev <= c.rev && !FNV_MUT(111)) { ++stats_.deltasStale; return; }   // twin 111: an old or duplicate delta is applied again
    if (d.rev != c.rev + 1 && !FNV_MUT(110)) {   // twin 110: a gap in the revisions is ignored (a lost delta is never noticed)
        ++stats_.gaps;
        askResync(d.cont, now);
        return;
    }
    if (d.newCount <= 0) c.items.erase(d.base);
    else c.items[d.base] = d.newCount;
    c.rev = d.rev;
    ++stats_.deltasApplied;
    auto lc = local_.find(d.cont);
    const bool openHere = lc != local_.end() && (lc->second.open || lc->second.closeWanted);
    if (cfg_.eagerApply && !openHere) {
        Action a;
        a.kind = Action::ContainerSet; a.id = d.cont; a.items = c.items;
        push(std::move(a));
    }
}

void LootClient::onResult(const Result& r, uint64_t now) {
    auto pit = pending_.find(r.txn);
    if (pit == pending_.end()) { ++stats_.duplicateResults; return; }
    Pending p = pit->second;
    pending_.erase(pit);
    ++stats_.results;
    if (r.status == Status::Stale) ++stats_.staleResults;

    switch (p.op) {
    case OpKind::Register: {
        if (r.status == Status::Ok) {
            if (!containers_.count(r.id)) {
                auto* body = std::get_if<ReqRegister>(&p.body);
                if (body) { ContainerRec c = body->rec; c.rev = r.rev; containers_[c.id] = c; }
            }
        } else if (r.status == Status::AlreadyResolved && r.hasRec) {
            applyRecord(r.rec, false, false, now);
            replaceContents(r.id, r.rec.items);   // the shared contents win over what this engine rolled
        }
        break;
    }
    case OpKind::Open: {
        Local& l = local_[r.id];
        if (r.status == Status::Ok && r.hasRec) {
            applyRecord(r.rec, false, false, now);
            l.granted = true;
            l.lastHoldMs = now;
            if (!FNV_MUT(131)) {   // twin 131: the shared contents are never put into this engine's container
                const Items want = expected(r.id, r.rec.items);
                if (digestOf(want) != digestOf(l.baseline)) replaceContents(r.id, want);
            }
        } else if (r.status == Status::Busy) {
            ++stats_.busy;
            if (r.hasRec) applyRecord(r.rec, false, false, now);
            l.open = false; l.granted = false; l.closeWanted = false;
            Action a;
            a.kind = Action::CloseMenu; a.id = r.id;
            push(std::move(a));
            Action n;
            n.kind = Action::Notify; n.id = r.id; n.notice = Notice::InUse;
            push(std::move(n));
        } else if (r.status == Status::Unknown && l.open && !p.adoptRetried && up_) {
            // the host forgot the container (cell reset): offer our contents again
            ReqOpen o;
            o.txn = nextTxn();
            o.cont = r.id;
            o.hasAdopt = true;
            o.adopt.id = r.id; o.adopt.items = l.baseline;
            auto* old = std::get_if<ReqOpen>(&p.body);
            if (old && old->hasAdopt) { o.adopt = old->adopt; o.adopt.items = l.baseline; }
            Pending np;
            np.adoptRetried = true;
            issue(o, o.txn, OpKind::Open, r.id, now, np);
        } else {
            l.open = false; l.granted = false; l.closeWanted = false;
            Action a;
            a.kind = Action::CloseMenu; a.id = r.id;
            push(std::move(a));
            Action n;
            n.kind = Action::Notify; n.id = r.id; n.notice = Notice::ContainerUnknown;
            push(std::move(n));
        }
        break;
    }
    case OpKind::Take:
    case OpKind::Put: {
        if (r.status == Status::Ok) {
            // our own change is not echoed back to us: apply it to the view ourselves (in revision order)
            auto vc = containers_.find(r.id);
            if (vc != containers_.end() && r.rev == vc->second.rev + 1) {
                int32_t& n = vc->second.items[p.base];
                n += p.put ? p.count : -p.count;
                if (n <= 0) vc->second.items.erase(p.base);
                vc->second.rev = r.rev;
            } else if (vc != containers_.end() && r.rev > vc->second.rev) {
                askResync(r.id, now);
            }
        } else {
            ++stats_.rollbacks;
            stats_.rollbackItems += static_cast<uint64_t>(p.count);
            if (p.op == OpKind::Take && !FNV_MUT(122)) {   // twin 122: a refused take keeps the items it gave the player
                Action a;
                a.kind = Action::PlayerAdjust; a.base = p.base; a.delta = -p.count;
                push(std::move(a));
            } else if (p.op == OpKind::Put && !FNV_MUT(123)) {   // twin 123: a refused put loses the items the player gave up
                Action a;
                a.kind = Action::PlayerAdjust; a.base = p.base; a.delta = p.count;
                push(std::move(a));
            }
            Action n;
            n.kind = Action::Notify; n.id = r.id; n.notice = Notice::ContainerRefused;
            push(std::move(n));
            if (r.hasRec) {
                applyRecord(r.rec, false, false, now);
                replaceContents(r.id, expected(r.id, r.rec.items));
            } else {
                askResync(r.id, now);
            }
        }
        break;
    }
    case OpKind::Close: {
        auto lc = local_.find(r.id);
        if (r.status == Status::DigestMismatch || r.status == Status::NotHolder) {
            if (r.status == Status::DigestMismatch) ++stats_.closeMismatch;
            if (r.hasRec) {
                applyRecord(r.rec, false, false, now);
                replaceContents(r.id, r.rec.items);
            }
        }
        if (lc != local_.end()) {
            lc->second.closeSent = false;
            if (!lc->second.open) lc->second.closeWanted = false;   // reconcileLocal() forgets it once the engine copy is in line
        }
        if (r.status == Status::Ok) {
            auto vc = containers_.find(r.id);
            if (vc != containers_.end() && vc->second.holder == cfg_.self && (lc == local_.end() || !lc->second.open)) vc->second.holder = kNoPlayer;   // the release itself
        }
        break;
    }
    case OpKind::Drop: {
        if (r.status == Status::Ok) {
            auto d = dropRef_.find(r.txn);
            if (d != dropRef_.end()) {
                const uint32_t ref = d->second;
                if (items_.count(r.newId) && !materialized_.count(r.newId)) {   // MItemNew is here but could not bind (no ref known then)
                    Action a;
                    a.kind = Action::BindDrop; a.id = r.newId; a.ref = ref;
                    push(std::move(a));
                    ++stats_.bound;
                    materialized_.insert(r.newId);
                } else if (!items_.count(r.newId)) {
                    boundEarly_[r.newId] = ref;
                }
                dropRef_.erase(d);
            }
        } else {
            dropRef_.erase(r.txn);
            auto* body = std::get_if<ReqDrop>(&p.body);
            if (body) {   // the host refused the drop: give the item back to the player and remove the local ref
                Action a;
                a.kind = Action::PlayerAdjust; a.base = body->base; a.delta = body->count;
                push(std::move(a));
            }
        }
        break;
    }
    case OpKind::Claim: {
        if (r.status == Status::Ok) {
            ++stats_.claimsWon;
            if (r.count > 0 && r.count != p.count) {
                Action a;
                a.kind = Action::PlayerAdjust; a.base = p.base; a.delta = r.count - p.count;
                push(std::move(a));
            }
        } else {
            ++stats_.claimsLost;
            if (!FNV_MUT(124)) {   // twin 124: a lost pickup keeps the item in the loser's inventory (the item exists twice)
                Action a;
                a.kind = Action::PlayerAdjust; a.base = p.base; a.delta = -p.count;
                push(std::move(a));
            }
            Action n;
            n.kind = Action::Notify; n.id = r.id; n.notice = Notice::AlreadyTaken;
            push(std::move(n));
            items_.erase(r.id);
        }
        break;
    }
    default: break;
    }
    maybeReadyToClose(r.id);
}

// ---- clutter from the host ---------------------------------------------------------------------------------------------------------------

void LootClient::onClutterOwn(const MClutterOwn& m) {
    Seen& s = seenClutter_[m.net];
    if (m.epoch < s.epoch) return;
    s.epoch = m.epoch; s.seq = 0; s.owner = m.owner;
    if (m.owner == cfg_.self) {
        Own& o = owned_[m.net];
        if (o.epoch != m.epoch) { o = Own(); o.epoch = m.epoch; }
        ++stats_.clutterOwned;
        Action a;
        a.kind = Action::ClutterOwner; a.id = m.net; a.flag = true;
        push(std::move(a));
    } else if (owned_.erase(m.net)) {
        Action a;
        a.kind = Action::ClutterOwner; a.id = m.net; a.flag = false;
        push(std::move(a));
    }
}

void LootClient::onClutter(const MClutter& m) {
    Seen& s = seenClutter_[m.net];
    if (m.epoch < s.epoch || (m.epoch == s.epoch && m.seq <= s.seq)) { ++stats_.clutterStale; return; }
    if (owned_.count(m.net) && owned_[m.net].epoch >= m.epoch) { ++stats_.clutterStale; return; }
    s.epoch = m.epoch; s.seq = m.seq;
    auto it = items_.find(m.net);
    if (it != items_.end()) { it->second.pos = m.pos; it->second.rot = m.rot; }
    ++stats_.clutterApplied;
    Action a;
    a.kind = Action::SetPose; a.id = m.net; a.pos = m.pos; a.rot = m.rot; a.flag = m.moving;
    push(std::move(a));
}

// ---- time ---------------------------------------------------------------------------------------------------------------------------------

void LootClient::tick(uint64_t now) {
    if (inTable_ && now >= tableStartMs_ && now - tableStartMs_ > 3000) inTable_ = false;   // the end of a table was lost: stop waiting for it (nothing is pruned)
    if (up_) {
        std::vector<uint32_t> giveUp;
        for (auto& kv : pending_) {
            Pending& p = kv.second;
            if (now >= p.firstMs && now - p.firstMs >= cfg_.giveUpMs) { giveUp.push_back(kv.first); continue; }
            if (!FNV_MUT(125) && now >= p.lastMs && now - p.lastMs >= cfg_.resendMs) {   // twin 125: a lost request is never sent again
                p.lastMs = now;
                ++p.tries;
                ++stats_.resent;
                sendBody(p.body);
            }
        }
        for (uint32_t txn : giveUp) {
            auto it = pending_.find(txn);
            if (it == pending_.end()) continue;
            ++stats_.gaveUp;
            Result r;
            r.txn = txn; r.op = it->second.op; r.id = it->second.id; r.status = Status::Unknown;
            const OpKind op = it->second.op;
            const NetId id = it->second.id;
            onResult(r, now);
            if (op == OpKind::Take || op == OpKind::Put || op == OpKind::Open || op == OpKind::Close) askResync(id, now);
        }
        for (auto& kv : local_) {
            Local& l = kv.second;
            if (l.open && l.granted && now >= l.lastHoldMs && now - l.lastHoldMs >= cfg_.holdEveryMs) {
                l.lastHoldMs = now;
                ReqHold h;
                h.cont = kv.first;
                sendBody(h);
            }
        }
        if (now >= lastDigestMs_ && now - lastDigestMs_ >= cfg_.digestEveryMs && pending_.empty() && !inTable_ &&
            now >= lastRxMs_ && now - lastRxMs_ >= 300) {
            lastDigestMs_ = now;
            ReqDigest d;
            d.digest = digest();
            d.containers = static_cast<uint32_t>(containers_.size());
            d.items = static_cast<uint32_t>(items_.size());
            ++stats_.digestsSent;
            sendBody(d);
        }
    }
    std::vector<NetId> ids;
    for (const auto& kv : local_) ids.push_back(kv.first);
    for (NetId id : ids) maybeReadyToClose(id);
    reconcileLocal(now);
}

// A container that is no longer open here must (a) show the shared contents in this engine (changes that arrived while the menu was up were
// held back) and (b) not stay locked by us because a close got lost on the way.
void LootClient::reconcileLocal(uint64_t now) {
    std::vector<NetId> ids;
    for (const auto& kv : local_) ids.push_back(kv.first);
    for (NetId id : ids) {
        auto it = local_.find(id);
        if (it == local_.end()) continue;
        Local& l = it->second;
        if (l.open || l.closeWanted || l.closeSent || pendingFor(id) != 0) continue;
        auto c = containers_.find(id);
        if (c != containers_.end() && digestOf(l.baseline) != digestOf(c->second.items)) replaceContents(id, c->second.items);
        local_.erase(it);
    }
    if (!up_) return;
    for (auto& kv : containers_) {
        if (kv.second.holder != cfg_.self) continue;
        auto it = local_.find(kv.first);
        if (it != local_.end() || pendingFor(kv.first) != 0) continue;
        // the table says we hold it, we think we do not: release it (the digest of the shared contents cannot mismatch)
        auto last = strayAskMs_.find(kv.first);
        if (last != strayAskMs_.end() && now >= last->second && now - last->second < cfg_.resendMs) continue;
        strayAskMs_[kv.first] = now;
        ReqClose c;
        c.txn = nextTxn();
        c.cont = kv.first;
        c.digest = digestOf(kv.second.items);
        Pending p;
        issue(c, c.txn, OpKind::Close, kv.first, now, p);
        ++stats_.strayReleases;
    }
}

std::vector<Action> LootClient::takeActions() {
    std::vector<Action> out;
    out.swap(actions_);
    return out;
}

const ContainerRec* LootClient::container(NetId id) const {
    auto it = containers_.find(id);
    return it == containers_.end() ? nullptr : &it->second;
}
const WorldItem* LootClient::item(NetId id) const {
    auto it = items_.find(id);
    return it == items_.end() ? nullptr : &it->second;
}
bool LootClient::isOpen(NetId cont) const {
    auto it = local_.find(cont);
    return it != local_.end() && it->second.open;
}
bool LootClient::holdsLock(NetId cont) const {
    auto it = local_.find(cont);
    return it != local_.end() && it->second.granted;
}
std::string LootClient::debugLocal(NetId cont) const {
    auto it = local_.find(cont);
    if (it == local_.end()) return "none";
    const Local& l = it->second;
    char b[160];
    std::snprintf(b, sizeof b, "open=%d granted=%d closeWanted=%d closeSent=%d readyAsked=%d pending=%zu", l.open, l.granted, l.closeWanted, l.closeSent, l.readyAsked, pendingFor(cont));
    return b;
}
bool LootClient::owns(NetId clutter) const { return owned_.count(clutter) != 0; }
uint64_t LootClient::digest() const { return tableDigest(containers_, items_); }

}  // namespace fnvcoop::loot
