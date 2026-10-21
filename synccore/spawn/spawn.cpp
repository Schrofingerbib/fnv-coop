#include "spawn/spawn.h"
#include <algorithm>
#include <cmath>
#include "fnvcoop/wire.h"
#include "fnvcoop_mut.h"
#include "spawn/spawn_wire.h"

// Mutation twins in this file (FNVCOOP_MUT=n, see tools/mut/fnvcoop_mut.h). Numbers 1..30 belong to synccore/spawn; plugin/spawn uses 41+.
//  1 owner check skipped            2 epoch check skipped             3 no idempotency cache         4 AlreadySpawned not enforced
//  5 permanent flag ignored         6 NotDue not enforced             7 issuer repeats an id         8 host roll re-issued (identity lost)
//  9 "nothing" roll not shared     10 deaths never clear a site      11 first death clears a site   12 view applies stale revs
// 13 view never suppresses         14 tracker keeps a bound duplicate 15 tracker keeps rejected created refs
// 16 NotOwner is retried           17 roller id not checked vs sender 18 ambient rolls never merge    19 (spawn_wire.cpp) decoder lax
// 20 cell reset also respawns interiors  21 digest ignores state    22 no auto-commit of site handoffs 23 join table omits cleared sites
// 24 load forgets the id counter   25 member id collision accepted   26 waiting placed refs never released
// 27 duplicates older than the verdict cache are re-evaluated
// 28 a roll's proof that its sender has the cell loaded is forgotten at the next pose update
namespace fnvcoop::spawn {

const char* toString(SiteKind k) {
    switch (k) {
        case SiteKind::Placed: return "Placed";
        case SiteKind::Marker: return "Marker";
        case SiteKind::Encounter: return "Encounter";
        case SiteKind::Script: return "Script";
        case SiteKind::Ambient: return "Ambient";
        default: return "?";
    }
}
const char* toString(SiteState s) {
    switch (s) {
        case SiteState::Unrolled: return "Unrolled";
        case SiteState::Spawned: return "Spawned";
        case SiteState::Cleared: return "Cleared";
        case SiteState::Respawnable: return "Respawnable";
    }
    return "?";
}
const char* toString(Verdict v) {
    switch (v) {
        case Verdict::Accept: return "Accept";
        case Verdict::NotOwner: return "NotOwner";
        case Verdict::StaleEpoch: return "StaleEpoch";
        case Verdict::AlreadySpawned: return "AlreadySpawned";
        case Verdict::ClearedPermanent: return "ClearedPermanent";
        case Verdict::NotDue: return "NotDue";
        case Verdict::BadReport: return "BadReport";
        default: return "?";
    }
}

SiteId siteIdOf(SiteKind kind, const FormRef& anchor, CellId cell, int32_t qx, int32_t qy) {
    uint64_t h = 1469598103934665603ull;
    const uint8_t k = static_cast<uint8_t>(kind);
    h = hash64(&k, 1, h);
    std::string p = anchor.plugin;
    for (auto& c : p) if (c >= 'A' && c <= 'Z') c = static_cast<char>(c - 'A' + 'a');
    h = hash64(p.data(), p.size(), h);
    h = hash64(&anchor.localId, sizeof anchor.localId, h);
    h = hash64(&cell, sizeof cell, h);
    h = hash64(&qx, sizeof qx, h);
    h = hash64(&qy, sizeof qy, h);
    uint32_t id = static_cast<uint32_t>(h ^ (h >> 32));
    return id ? id : 1u;
}

SiteId ambientSiteId(CellId cell, float x, float y) {
    const int32_t qx = static_cast<int32_t>(std::floor(x / kAmbientGrid)), qy = static_cast<int32_t>(std::floor(y / kAmbientGrid));
    return siteIdOf(SiteKind::Ambient, FormRef(), cell, qx, qy);
}

// ---- ids ----------------------------------------------------------------------------------------------------------------------
NetId RangeIssuer::next() {
    if (next_ == 0xFFFFFFFFu) return 0;
    if (FNV_MUT(7)) return next_;   // twin 7: the counter never advances, two rolls get the same id
    return next_++;
}
void RangeIssuer::raiseFloor(NetId used) {
    if (used < kIssuedBase) return;
    if (used >= 0xFFFFFFFEu) { next_ = 0xFFFFFFFFu; return; }
    if (used + 1 > next_) next_ = used + 1;
}

// ---- registry -----------------------------------------------------------------------------------------------------------------
namespace {
uint64_t elapsed(uint64_t now, uint64_t then) { return now > then ? now - then : 0; }
float dist2d(const Vec3& a, const Vec3& b) { return std::sqrt((a.x - b.x) * (a.x - b.x) + (a.y - b.y) * (a.y - b.y)); }
uint64_t digestOf(const std::map<SiteId, SiteMsg>& m) {
    uint64_t h = 1469598103934665603ull;
    for (const auto& kv : m) {
        if (kv.second.state == SiteState::Unrolled) continue;
        uint32_t rec[4] = {kv.first, FNV_MUT(21) ? 0u : static_cast<uint32_t>(kv.second.state), kv.second.generation, static_cast<uint32_t>(kv.second.members.size())};
        h = hash64(rec, sizeof rec, h);
    }
    return h;
}
}  // namespace

SiteRegistry::SiteRegistry(Config cfg, std::shared_ptr<IdIssuer> issuer)
    : cfg_(cfg), issuer_(issuer ? std::move(issuer) : std::make_shared<RangeIssuer>()), own_(cfg.own) {
    own_.playerJoined(kHostPlayer);
}

void SiteRegistry::reset() {
    own_ = fnvcoop::OwnershipService(cfg_.own);
    own_.playerJoined(kHostPlayer);
    for (PlayerId p : joined_) own_.playerJoined(p);
    sites_.clear(); memberSite_.clear(); locs_.clear(); verdicts_.clear(); maxRoll_.clear(); seen_.clear();
    game_ = 0; nextEvalMs_ = 0;
}

void SiteRegistry::playerJoined(PlayerId p) { joined_.insert(p); own_.playerJoined(p); }

void SiteRegistry::playerLeft(PlayerId p, uint64_t wallMs, Out& out) {
    const size_t from = out.own.size();
    std::vector<fnvcoop::ActorNetId> orphans = own_.playerLeft(p, static_cast<uint32_t>(wallMs), out.own);
    locs_.erase(p);
    verdicts_.erase(p);
    maxRoll_.erase(p);
    seen_.erase(p);
    joined_.erase(p);
    autoCommit(out, from, wallMs);
    for (SiteId id : orphans) if (Site* s = find(id)) evaluate(*s, wallMs, out);
}

void SiteRegistry::setPlayerLoc(PlayerId p, const PlayerLoc& loc) { locs_[p] = loc; }

std::vector<fnvcoop::Presence> SiteRegistry::presenceFor(const Site& s) const {
    std::vector<fnvcoop::Presence> who;
    for (const auto& kv : locs_) {
        const PlayerLoc& l = kv.second;
        fnvcoop::Presence p;
        p.player = kv.first;
        p.connected = true;
        p.inCell = l.present && l.cell == s.def.cell;
        p.adjacent = l.present && !p.inCell && std::find(l.adjacent.begin(), l.adjacent.end(), s.def.cell) != l.adjacent.end();
        if (!p.inCell && !p.adjacent && !l.adjacentKnown && !FNV_MUT(28)) {
            auto sp = seen_.find(kv.first);
            if (sp != seen_.end()) {
                auto c = sp->second.find(s.def.cell);
                if (c != sp->second.end() && lastWall_ <= c->second + cfg_.evidenceMs) p.adjacent = true;
            }
        }
        p.distance = dist2d(l.pos, s.def.pos);
        who.push_back(p);
    }
    return who;
}

void SiteRegistry::autoCommit(Out& out, size_t from, uint64_t wallMs) {
    // A site has no state a new owner must receive first, so the host plays the new owner's Ack itself: Transfer is followed by Commit
    // at once and a roll from the new owner is valid as soon as the Commit reaches it.
    stats_.ownershipChanges += out.own.size() - from;
    if (FNV_MUT(22)) return;   // twin 22: handoffs wait for an Ack nobody sends; the site keeps its old owner until the timeout
    std::vector<fnvcoop::OwnMsg> transfers;
    for (size_t i = from; i < out.own.size(); ++i) if (out.own[i].kind == fnvcoop::OwnMsg::Transfer) transfers.push_back(out.own[i]);
    for (const auto& t : transfers) {
        fnvcoop::OwnMsg ack;
        ack.kind = fnvcoop::OwnMsg::Ack; ack.netId = t.netId; ack.epoch = t.epoch; ack.owner = t.owner; ack.prevOwner = t.prevOwner;
        own_.onAck(ack, t.owner, static_cast<uint32_t>(wallMs), out.own);
    }
}

void SiteRegistry::evaluate(Site& s, uint64_t wallMs, Out& out) {
    const size_t from = out.own.size();
    own_.evaluate(s.def.id, presenceFor(s), static_cast<uint32_t>(wallMs), out.own);
    autoCommit(out, from, wallMs);
}

SiteRegistry::Site* SiteRegistry::ensureSite(const SiteDef& def, uint64_t wallMs, Out& out) {
    auto it = sites_.find(def.id);
    if (it != sites_.end()) return &it->second;
    Site& s = sites_[def.id];
    s.def = def;
    const size_t from = out.own.size();
    own_.track(def.id, presenceFor(s), static_cast<uint32_t>(wallMs), out.own);
    autoCommit(out, from, wallMs);
    return &s;
}

SiteRegistry::Site* SiteRegistry::findAmbientNear(const SiteDef& def) {
    for (auto& kv : sites_) {
        Site& s = kv.second;
        if (s.def.kind != SiteKind::Ambient || s.def.cell != def.cell || s.state == SiteState::Unrolled) continue;
        if (dist2d(s.def.pos, def.pos) < cfg_.ambientMerge) return &s;
    }
    return nullptr;
}

uint64_t SiteRegistry::respawnMsOf(const Site& s) const {
    return s.def.respawnHours ? static_cast<uint64_t>(s.def.respawnHours) * 3600ull * 1000ull : cfg_.respawnGameMs;
}

bool SiteRegistry::alive(const Site& s) const {
    for (const auto& m : s.members) if (!m.dead) return true;
    return false;
}

void SiteRegistry::dropIndex(Site& s) {
    for (const auto& m : s.members) {
        auto it = memberSite_.find(m.netId);
        if (it != memberSite_.end() && it->second == s.def.id) memberSite_.erase(it);
    }
}
void SiteRegistry::indexMembers(Site& s) { for (const auto& m : s.members) memberSite_[m.netId] = s.def.id; }

SiteMsg SiteRegistry::toMsg(const Site& s) const {
    SiteMsg m;
    m.def = s.def; m.state = s.state; m.generation = s.generation; m.rev = s.rev; m.none = s.none; m.pick = s.pick;
    m.clearedAtGame = s.clearedAt; m.respawnAtGame = s.respawnAt; m.members = s.members;
    return m;
}

void SiteRegistry::publish(Site& s, Out& out) {
    ++s.rev;
    out.sites.push_back(toMsg(s));
}

void SiteRegistry::clearSite(Site& s, Out& out) {
    s.state = SiteState::Cleared;
    s.clearedAt = game_;
    s.respawnAt = s.def.interior ? 0 : game_ + respawnMsOf(s);
    ++stats_.sitesCleared;
    publish(s, out);
}

void SiteRegistry::refreshDue(Site& s, Out& out) {
    if (s.state == SiteState::Cleared && !s.def.interior && s.respawnAt != 0 && game_ >= s.respawnAt) {
        s.state = SiteState::Respawnable;
        ++stats_.sitesRespawnable;
        publish(s, out);
    }
}

void SiteRegistry::setCached(PlayerId to, uint32_t rollId, const VerdictMsg& v) {
    auto& q = verdicts_[to];
    q.push_back(v);
    (void)rollId;
    while (q.size() > cfg_.verdictCacheKeep) q.pop_front();
}

void SiteRegistry::reply(PlayerId to, const Site* s, const RollReport& r, Verdict v, Out& out, bool cache) {
    VerdictMsg m;
    m.site = s ? s->def.id : r.def.id;
    m.rollId = r.rollId;
    m.verdict = v;
    if (s) {
        m.epoch = own_.epochOf(s->def.id);
        m.owner = own_.ownerOf(s->def.id);
        m.generation = s->generation;
        m.none = s->none;
        m.pick = s->pick;
        m.members = s->members;
    }
    out.verdicts.push_back({to, m});
    if (cache) setCached(to, r.rollId, m);
}

void SiteRegistry::onRoll(const RollReport& r, PlayerId from, uint64_t wallMs, Out& out) {
    ++stats_.rolls;
    auto bad = [&](bool spoof) {
        spoof ? ++stats_.spoofed : ++stats_.badReports;
        ++stats_.rejected;
        reply(from, nullptr, r, Verdict::BadReport, out, false);
    };
    // 1. who is talking
    if (r.roller != from && !FNV_MUT(17)) { bad(true); return; }   // twin 17: a client can roll in somebody else's name
    // 2. is the report sane
    bool ok = r.def.id != 0 && static_cast<uint8_t>(r.def.kind) < static_cast<uint8_t>(SiteKind::Count_) && r.rollId != 0 &&
              r.members.size() <= kMaxMembers && (r.none ? r.members.empty() : !r.members.empty());
    std::set<NetId> seen;
    for (const auto& m : r.members) {
        if (m.created) {
            if (from == kHostPlayer) ok = ok && isCreatedId(m.netId) && !isIssuedId(m.netId);
        } else {
            ok = ok && m.netId != 0 && !isCreatedId(m.netId);
        }
        if (m.netId != 0 && !seen.insert(m.netId).second) ok = false;
    }
    if (!ok) { bad(false); return; }
    // 3. a replay of a roll we already answered: say the same thing again, spawn nothing
    if (!FNV_MUT(3)) {
        auto qi = verdicts_.find(from);
        if (qi != verdicts_.end()) {
            for (const auto& c : qi->second) {
                if (c.rollId == r.rollId) {
                    ++stats_.duplicates;
                    out.verdicts.push_back({from, c});
                    return;
                }
            }
        }
    }
    // a duplicate so late that its verdict left the cache: dropping it is the only way it cannot spawn a second time
    {
        uint32_t& hi = maxRoll_[from];
        if (r.rollId + cfg_.verdictCacheKeep <= hi && !FNV_MUT(27)) { ++stats_.duplicates; reply(from, nullptr, r, Verdict::BadReport, out, false); return; }
        if (r.rollId > hi) hi = r.rollId;
    }
    // 4. the site (an ambient roll next to a known encounter is that encounter)
    Site* s = find(r.def.id);
    if (!s && r.def.kind == SiteKind::Ambient && !FNV_MUT(18)) {
        if (Site* near = findAmbientNear(r.def)) { s = near; ++stats_.ambientMerges; }
    }
    if (!s) s = ensureSite(r.def, wallMs, out);
    // the roller has this cell loaded: that is presence evidence even before its first pose arrives
    lastWall_ = wallMs;
    {
        PlayerLoc& l = locs_[from];
        if (!l.present) { l.present = true; l.cell = r.def.cell; l.pos = r.def.pos; }
        seen_[from][r.def.cell] = wallMs;
    }
    refreshDue(*s, out);
    evaluate(*s, wallMs, out);
    const PlayerId owner = own_.ownerOf(s->def.id);
    const OwnerEpoch epoch = own_.epochOf(s->def.id);
    auto reject = [&](Verdict v) {
        ++stats_.rejected;
        switch (v) {
            case Verdict::NotOwner: ++stats_.notOwner; break;
            case Verdict::StaleEpoch: ++stats_.staleEpoch; break;
            case Verdict::AlreadySpawned: ++stats_.alreadySpawned; break;
            case Verdict::ClearedPermanent: ++stats_.clearedPermanent; break;
            case Verdict::NotDue: ++stats_.notDue; break;
            default: break;
        }
        reply(from, s, r, v, out, true);
    };
    // 5. ownership: one owner per site, under one epoch
    if (owner != from && !FNV_MUT(1)) { reject(Verdict::NotOwner); return; }
    if (r.epoch != 0 && r.epoch != epoch && !FNV_MUT(2)) { reject(Verdict::StaleEpoch); return; }
    // 6. state
    if (s->state == SiteState::Spawned && alive(*s) && !FNV_MUT(4)) { reject(Verdict::AlreadySpawned); return; }
    if (s->state == SiteState::Cleared) {
        if (s->def.interior && !FNV_MUT(5)) { reject(Verdict::ClearedPermanent); return; }
        if (!FNV_MUT(6)) { reject(Verdict::NotDue); return; }
    }
    // 7. accept: ids first (all or nothing), then the record
    std::vector<Member> members = r.members;
    std::vector<NetId> fresh;
    for (auto& m : members) {
        m.dead = false;
        if (m.created && !(from == kHostPlayer && !FNV_MUT(8))) {
            const NetId id = issuer_->next();
            if (id == 0) { bad(false); return; }
            m.netId = id;
            fresh.push_back(id);
        }
        auto ex = memberSite_.find(m.netId);
        if (ex != memberSite_.end() && ex->second != s->def.id && !FNV_MUT(25)) { bad(false); return; }
    }
    stats_.idsIssued += fresh.size();
    for (const auto& m : members) if (m.created && fresh.empty()) ++stats_.hostIds;
    dropIndex(*s);
    ++s->generation;
    ++s->spawnCount;
    s->none = r.none;
    s->pick = r.pick;
    s->members = members;
    if (r.none && !FNV_MUT(9)) {
        s->state = SiteState::Cleared;
        s->clearedAt = game_;
        s->respawnAt = s->def.interior ? 0 : game_ + respawnMsOf(*s);
        ++stats_.sitesCleared;
    } else {
        s->state = r.none ? SiteState::Unrolled : SiteState::Spawned;
        s->clearedAt = s->respawnAt = 0;
    }
    indexMembers(*s);
    ++stats_.accepted;
    reply(from, s, r, Verdict::Accept, out, true);
    for (const auto& m : members) {
        if (!m.created) continue;
        fnvcoop::SpawnRef sp;
        sp.id = m.netId; sp.base = m.base; sp.cell = s->def.cellForm; sp.x = m.pos.x; sp.y = m.pos.y; sp.z = m.pos.z; sp.leveled = false;
        out.spawns.push_back({from, sp});
        ++stats_.spawnsCommitted;
    }
    publish(*s, out);
}

void SiteRegistry::onMemberDied(NetId id, uint64_t wallMs, Out& out) {
    (void)wallMs;
    auto it = memberSite_.find(id);
    if (it == memberSite_.end()) return;
    Site* s = find(it->second);
    if (!s) return;
    Member* mem = nullptr;
    for (auto& m : s->members) if (m.netId == id) mem = &m;
    if (!mem || mem->dead) return;   // idempotent: a replayed death changes nothing
    mem->dead = true;
    if (s->state != SiteState::Spawned) return;
    if (FNV_MUT(10)) return;                                  // twin 10: a fully cleared site stays "Spawned" forever
    if (FNV_MUT(11) || !alive(*s)) clearSite(*s, out);        // twin 11: the first death clears the whole site
}

void SiteRegistry::onCellReset(CellId cell, Out& out) {
    ++stats_.cellResets;
    for (auto& kv : sites_) {
        Site& s = kv.second;
        if (s.def.cell != cell || s.state != SiteState::Cleared) continue;
        if (s.def.interior && !FNV_MUT(20)) continue;   // a cleared interior stays cleared (twin 20: it resets with the cell)
        s.state = SiteState::Respawnable;
        ++stats_.sitesRespawnable;
        publish(s, out);
    }
}

void SiteRegistry::forceRespawn(SiteId id, Out& out) {
    Site* s = find(id);
    if (!s || s->state != SiteState::Cleared) return;
    s->state = SiteState::Respawnable;
    ++stats_.sitesRespawnable;
    publish(*s, out);
}

void SiteRegistry::noteScriptSpawn(const SiteDef& def, const fnvcoop::SpawnRef& sp, uint64_t wallMs, Out& out) {
    if (def.id == 0) return;
    Site* s = ensureSite(def, wallMs, out);
    for (const auto& m : s->members) if (m.netId == sp.id) return;   // the same capture twice
    if (memberSite_.count(sp.id)) return;                            // already the member of another site (a roll of the same actor got there first)
    if (s->state != SiteState::Spawned || !alive(*s) || s->members.size() >= kMaxMembers) {
        dropIndex(*s);
        s->members.clear();
        ++s->generation;
        s->state = SiteState::Spawned;
        s->none = false;
        s->clearedAt = s->respawnAt = 0;
    }
    Member m;
    m.netId = sp.id; m.created = true; m.base = sp.base; m.pos = {sp.x, sp.y, sp.z}; m.cell = def.cell;
    s->members.push_back(m);
    memberSite_[m.netId] = s->def.id;
    ++s->spawnCount;
    publish(*s, out);
}

void SiteRegistry::tick(uint64_t wallMs, Out& out) {
    lastWall_ = wallMs;
    if (wallMs >= nextEvalMs_) {
        nextEvalMs_ = wallMs + cfg_.ownEvalMs;
        for (auto& kv : sites_) evaluate(kv.second, wallMs, out);
    }
    const size_t from = out.own.size();
    own_.tick(static_cast<uint32_t>(wallMs), out.own);
    stats_.ownershipChanges += out.own.size() - from;
    for (auto& kv : sites_) refreshDue(kv.second, out);
}

void SiteRegistry::snapshot(Out& out) const {
    own_.snapshot(out.own);
    for (const auto& kv : sites_) {
        const Site& s = kv.second;
        if (s.state == SiteState::Unrolled) continue;
        if (FNV_MUT(23) && s.state == SiteState::Cleared) continue;   // twin 23: a joiner is never told what is cleared
        out.sites.push_back(toMsg(s));
    }
}

bool SiteRegistry::siteMsg(SiteId id, SiteMsg& out) const {
    const Site* s = find(id);
    if (!s) return false;
    out = toMsg(*s);
    return true;
}

uint64_t SiteRegistry::digest() const {
    std::map<SiteId, SiteMsg> m;
    for (const auto& kv : sites_) m[kv.first] = toMsg(kv.second);
    return digestOf(m);
}

SiteState SiteRegistry::stateOf(SiteId id) const { const Site* s = find(id); return s ? s->state : SiteState::Unrolled; }

bool SiteRegistry::siteOfMember(NetId id, SiteId& out) const {
    auto it = memberSite_.find(id);
    if (it == memberSite_.end()) return false;
    out = it->second;
    return true;
}

size_t SiteRegistry::liveMembers() const {
    size_t n = 0;
    for (const auto& kv : sites_) for (const auto& m : kv.second.members) if (!m.dead) ++n;
    return n;
}

void SiteRegistry::save(fnvcoop::Archive& a) const {
    uint8_t ver = 1;
    a.u8(ver);
    uint32_t next = issuer_->peek();
    a.varu32(next);
    uint64_t g = game_;
    a.varu(g);
    size_t n = sites_.size();
    if (!a.count(n, 1u << 20)) return;
    for (const auto& kv : sites_) {
        SiteMsg m = toMsg(kv.second);
        io(a, m);
    }
}

bool SiteRegistry::load(fnvcoop::Archive& a) {
    uint8_t ver = 0;
    a.u8(ver);
    uint32_t next = 0;
    a.varu32(next);
    uint64_t g = 0;
    a.varu(g);
    size_t n = 0;
    if (!a.ok() || ver != 1 || !a.count(n, 1u << 20)) return false;
    std::map<SiteId, Site> sites;
    for (size_t i = 0; i < n; ++i) {
        SiteMsg m;
        io(a, m);
        if (!a.ok()) return false;
        Site s;
        s.def = m.def; s.state = m.state; s.generation = m.generation; s.rev = m.rev; s.none = m.none; s.pick = m.pick;
        s.clearedAt = m.clearedAtGame; s.respawnAt = m.respawnAtGame; s.members = m.members;
        sites[m.def.id] = std::move(s);
    }
    reset();
    sites_ = std::move(sites);
    game_ = g;
    for (auto& kv : sites_) {
        indexMembers(kv.second);
        for (const auto& m : kv.second.members) issuer_->raiseFloor(m.netId);
    }
    if (!FNV_MUT(24)) issuer_->raiseFloor(next == 0 ? 0 : next - 1);   // twin 24: the counter restarts at the base after a load (ids reused)
    // ownership is rebuilt from presence on the next evaluation
    std::vector<fnvcoop::OwnMsg> scratch;
    for (auto& kv : sites_) own_.track(kv.first, {}, 0, scratch);
    return true;
}

// ---- view ---------------------------------------------------------------------------------------------------------------------
fnvcoop::ApplyResult SiteView::apply(const SiteMsg& m) {
    auto it = sites_.find(m.def.id);
    if (it != sites_.end()) {
        if (m.rev == it->second.rev) { ++stats_.sitesDuplicate; return fnvcoop::ApplyResult::Duplicate; }
        if (m.rev < it->second.rev && !FNV_MUT(12)) { ++stats_.sitesStale; return fnvcoop::ApplyResult::Stale; }   // twin 12: an old record overwrites a new one
    }
    sites_[m.def.id] = m;
    ++stats_.sitesApplied;
    return fnvcoop::ApplyResult::Applied;
}

SiteView::LocalDecision SiteView::localRoll(const RollReport& r) {
    LocalDecision d;
    auto it = sites_.find(r.def.id);
    if (it == sites_.end()) { ++stats_.localSent; return d; }
    const SiteMsg& s = it->second;
    if (s.state == SiteState::Cleared && !FNV_MUT(13)) {
        d.kind = LocalKind::Suppress;
        d.why = s.def.interior ? Verdict::ClearedPermanent : Verdict::NotDue;
        d.site = s;
        ++stats_.localSuppressed;
        return d;
    }
    if (s.state == SiteState::Spawned) {
        bool alive = false;
        for (const auto& m : s.members) if (!m.dead) alive = true;
        if (alive) {
            d.kind = LocalKind::Adopt;
            d.why = Verdict::AlreadySpawned;
            d.site = s;
            ++stats_.localAdopted;
            return d;
        }
    }
    ++stats_.localSent;
    return d;
}

bool SiteView::find(SiteId id, SiteMsg& out) const {
    auto it = sites_.find(id);
    if (it == sites_.end()) return false;
    out = it->second;
    return true;
}

SiteState SiteView::stateOf(SiteId id) const {
    auto it = sites_.find(id);
    return it == sites_.end() ? SiteState::Unrolled : it->second.state;
}

std::vector<Directive> SiteView::directivesFor(SiteId id) const {
    std::vector<Directive> out;
    auto it = sites_.find(id);
    if (it == sites_.end()) return out;
    const SiteMsg& s = it->second;
    if (s.state != SiteState::Spawned && s.state != SiteState::Cleared) return out;
    for (const auto& m : s.members) {
        if (m.created) continue;   // created members arrive as SpawnRef effects
        Directive d;
        d.netId = m.netId;
        if (m.dead || s.state == SiteState::Cleared) { d.kind = Directive::EnsureDead; }
        else { d.kind = Directive::Rebase; d.base = m.base; }
        out.push_back(d);
    }
    return out;
}

std::vector<SiteId> SiteView::ids() const {
    std::vector<SiteId> v;
    for (const auto& kv : sites_) v.push_back(kv.first);
    return v;
}

bool SiteView::siteOfMember(NetId id, SiteId& out) const {
    for (const auto& kv : sites_)
        for (const auto& m : kv.second.members)
            if (m.netId == id) { out = kv.first; return true; }
    return false;
}

std::vector<SiteId> SiteView::sitesInCell(CellId cell) const {
    std::vector<SiteId> v;
    for (const auto& kv : sites_) if (kv.second.def.cell == cell) v.push_back(kv.first);
    return v;
}

uint64_t SiteView::digest() const { return digestOf(sites_); }

// ---- tracker ------------------------------------------------------------------------------------------------------------------
uint32_t RollTracker::begin(RollReport& r, uint64_t nowMs) {
    r.roller = self_;
    r.rollId = nextRoll_++;
    P p;
    p.report = r;
    p.firstAt = p.lastSent = nowMs;
    pending_[r.rollId] = std::move(p);
    ++stats_.begun;
    return r.rollId;
}

bool RollTracker::hasPending(SiteId site) const {
    for (const auto& kv : pending_) if (kv.second.report.def.id == site) return true;
    return false;
}

std::vector<Action> RollTracker::turnOff(const P& p, const VerdictMsg* v) {
    std::vector<Action> acts;
    for (const auto& rm : p.report.members) {
        if (rm.created) {
            if (!FNV_MUT(15)) { Action a; a.kind = Action::Drop; a.handle = rm.handle; a.netId = rm.netId; acts.push_back(a); }
            continue;
        }
        const Member* hit = nullptr;
        if (v) for (const auto& vm : v->members) if (vm.netId == rm.netId) hit = &vm;
        if (hit) {
            Action a; a.handle = rm.handle; a.netId = rm.netId;
            if (hit->dead || v->verdict == Verdict::ClearedPermanent || v->verdict == Verdict::NotDue) a.kind = Action::EnsureDead;
            else { a.kind = Action::Rebase; a.base = hit->base; }
            acts.push_back(a);
            Action s; s.kind = Action::Show; s.handle = rm.handle; s.netId = rm.netId;
            acts.push_back(s);
        } else {
            W w; w.handle = rm.handle; w.netId = rm.netId; w.site = v ? v->site : p.report.def.id; w.since = 0;
            waiting_.push_back(w);
        }
    }
    return acts;
}

std::vector<Action> RollTracker::giveUp(const P& p) {
    std::vector<Action> acts;
    for (const auto& rm : p.report.members) {
        Action a; a.handle = rm.handle; a.netId = rm.netId;
        a.kind = rm.created ? Action::Drop : Action::Show;
        acts.push_back(a);
    }
    return acts;
}

std::vector<Action> RollTracker::onVerdict(const VerdictMsg& v, const std::function<bool(NetId)>& isBound, uint64_t nowMs) {
    std::vector<Action> acts;
    auto it = pending_.find(v.rollId);
    if (it == pending_.end()) { ++stats_.unknownVerdicts; return acts; }
    P p = std::move(it->second);
    pending_.erase(it);
    if (v.verdict == Verdict::Accept) {
        if (v.members.size() != p.report.members.size()) { ++stats_.rejected; return turnOff(p, nullptr); }
        ++stats_.accepted;
        for (size_t i = 0; i < p.report.members.size(); ++i) {
            const Member& rm = p.report.members[i];
            const Member& vm = v.members[i];
            if (rm.created) {
                if (isBound && isBound(vm.netId) && !FNV_MUT(14)) {
                    // the SpawnRef beat the verdict and already made the canonical object here: ours is the extra one
                    Action a; a.kind = Action::Drop; a.handle = rm.handle; a.netId = vm.netId; acts.push_back(a);
                    ++stats_.droppedBound;
                } else {
                    Action b; b.kind = Action::Bind; b.handle = rm.handle; b.netId = vm.netId; acts.push_back(b);
                    Action s; s.kind = Action::Show; s.handle = rm.handle; s.netId = vm.netId; acts.push_back(s);
                    ++stats_.adoptedBound;
                }
            } else {
                Action s; s.kind = Action::Show; s.handle = rm.handle; s.netId = rm.netId; acts.push_back(s);
            }
        }
        return acts;
    }
    if (v.verdict == Verdict::StaleEpoch && v.owner == self_ && p.retries < cfg_.maxStaleRetries) {
        // we ARE the owner now; the report was made under an older tenure. Same content, fresh epoch, new rollId.
        RollReport r = p.report;
        r.epoch = v.epoch;
        r.rollId = nextRoll_++;
        P q;
        q.report = r; q.firstAt = p.firstAt; q.lastSent = nowMs; q.retries = static_cast<uint8_t>(p.retries + 1);
        pending_[r.rollId] = q;
        ++stats_.retried;
        Action a; a.kind = Action::Resend; a.report = r;
        acts.push_back(a);
        return acts;
    }
    if (v.verdict == Verdict::NotOwner && FNV_MUT(16)) {   // twin 16: a roll from a non-owner is sent again and again
        Action a; a.kind = Action::Resend; a.report = p.report;
        pending_[p.report.rollId] = p;
        acts.push_back(a);
        return acts;
    }
    ++stats_.rejected;
    return turnOff(p, &v);
}

std::vector<Action> RollTracker::refuse(const RollReport& r, const VerdictMsg& v) {
    P p;
    p.report = r;
    ++stats_.rejected;
    return turnOff(p, &v);
}

std::vector<Action> RollTracker::onSite(const SiteMsg& m, uint64_t nowMs) {
    std::vector<Action> acts;
    if (m.state == SiteState::Unrolled || m.state == SiteState::Respawnable) return acts;
    for (size_t i = 0; i < waiting_.size();) {
        W& w = waiting_[i];
        if (w.site != m.def.id) { ++i; continue; }
        const Member* hit = nullptr;
        for (const auto& mm : m.members) if (mm.netId == w.netId) hit = &mm;
        if (hit) {
            Action a; a.handle = w.handle; a.netId = w.netId;
            if (hit->dead || m.state == SiteState::Cleared) a.kind = Action::EnsureDead;
            else { a.kind = Action::Rebase; a.base = hit->base; }
            acts.push_back(a);
            Action s; s.kind = Action::Show; s.handle = w.handle; s.netId = w.netId; acts.push_back(s);
        }
        // not a member of the owner's result: the ref stays hidden (the owner's roll did not include it)
        waiting_.erase(waiting_.begin() + static_cast<long>(i));
    }
    (void)nowMs;
    return acts;
}

std::vector<Action> RollTracker::tick(uint64_t nowMs) {
    std::vector<Action> acts;
    for (auto it = pending_.begin(); it != pending_.end();) {
        P& p = it->second;
        if (elapsed(nowMs, p.firstAt) >= cfg_.giveUpMs) {
            ++stats_.gaveUp;
            std::vector<Action> g = giveUp(p);
            acts.insert(acts.end(), g.begin(), g.end());
            it = pending_.erase(it);
            continue;
        }
        if (elapsed(nowMs, p.lastSent) >= cfg_.resendMs) {
            p.lastSent = nowMs;
            ++p.tries;
            ++stats_.resent;
            Action a; a.kind = Action::Resend; a.report = p.report;
            acts.push_back(a);
        }
        ++it;
    }
    for (size_t i = 0; i < waiting_.size();) {
        W& w = waiting_[i];
        if (w.since == 0) w.since = nowMs ? nowMs : 1;
        if (!FNV_MUT(26) && elapsed(nowMs, w.since) >= cfg_.giveUpMs) {
            // the owner never answered for this placed ref: show what the engine made rather than leave it hidden for good
            Action s; s.kind = Action::Show; s.handle = w.handle; s.netId = w.netId; acts.push_back(s);
            ++stats_.gaveUp;
            waiting_.erase(waiting_.begin() + static_cast<long>(i));
            continue;
        }
        ++i;
    }
    return acts;
}

std::vector<Action> RollTracker::abandon() {
    std::vector<Action> acts;
    for (auto& kv : pending_) {
        for (const auto& rm : kv.second.report.members) { Action s; s.kind = Action::Show; s.handle = rm.handle; s.netId = rm.netId; acts.push_back(s); }
        ++stats_.abandoned;
    }
    for (auto& w : waiting_) { Action s; s.kind = Action::Show; s.handle = w.handle; s.netId = w.netId; acts.push_back(s); }
    pending_.clear();
    waiting_.clear();
    return acts;
}

}  // namespace fnvcoop::spawn
