#include "spawn/spawn_sync.h"
#include <algorithm>
#include "fnvcoop/wire.h"
#include "fnvcoop_mut.h"

// Mutation twins in this file and roll_detector.cpp (FNVCOOP_MUT=n; synccore/spawn owns 1..30, this module 41..60):
// 41 rolls are not held while the verdict is pending   42 the host never commits the SpawnRef effects   43 an accepted roll is not bound
// 44 silent rolls are never sent again                45 placed refs are not rebased / killed         46 the host drops the verdicts of its own rolls
// 47 the host never heals a diverged table             48 a client does not ask for the table          49 sender checks are skipped
// 50 the tracker is not told about site records        51 (detector) groups ignore the cell            52 (detector) groups released unsettled
// 53 unannounced created actors are tracked at once    54 the ignore predicate is not asked            55 purge keeps the old world's bindings
// 56 the host is not told about deaths                 57 the host is not told a player left           58 the host does not make its own copy of a client's spawn
// 59 no periodic sweep of the placed members of sites
namespace nvcoop::spawn {

namespace {
sp::VerdictMsg verdictFromSite(const sp::SiteMsg& s, sp::Verdict why) {
    sp::VerdictMsg v;
    v.site = s.def.id; v.verdict = why; v.generation = s.generation; v.none = s.none; v.pick = s.pick; v.members = s.members;
    return v;
}
}  // namespace

SpawnSync::SpawnSync(const SyncConfig& cfg, ISpawnEngine& eng, ISpawnNet& net, ISpawnHost* host)
    : cfg_(cfg), eng_(eng), net_(net), host_(host), view_(cfg.self), tracker_(cfg.self, cfg.tracker) {
    if (cfg.isHost) registry_.reset(new sp::SiteRegistry(cfg.registry));
}

SpawnSync::~SpawnSync() = default;

bool SpawnSync::ignored(RefId ref) const { return ignore_ && !FNV_MUT(54) && ignore_(ref); }

// ---- session -------------------------------------------------------------------------------------------------------------------
void SpawnSync::playerJoined(PlayerId slot) {
    if (!cfg_.isHost || slot == kNoPlayer) return;
    joined_.insert(slot);
    registry_->playerJoined(slot);
    sendJoinState(slot);
}

void SpawnSync::playerLeft(PlayerId slot) {
    if (!cfg_.isHost) return;
    joined_.erase(slot);
    mismatchStreak_.erase(slot);
    lastHealMs_.erase(slot);
    if (FNV_MUT(57)) return;
    sp::SiteRegistry::Out out;
    registry_->playerLeft(slot, wall_, out);
    flush(out);
}

void SpawnSync::hostConnected() {
    if (cfg_.isHost) return;
    hostUp_ = true;
    lastDigestMs_ = wall_;
    if (FNV_MUT(48)) return;
    sp::MResync r;
    sendBody(kHostPlayer, r);
    ++stats_.resyncsSent;
}

void SpawnSync::hostLost() {
    if (cfg_.isHost) return;
    hostUp_ = false;
    view_.clear();
    applyActions(tracker_.abandon());
}

void SpawnSync::setPlayerLoc(PlayerId slot, const sp::PlayerLoc& loc) {
    if (registry_) registry_->setPlayerLoc(slot, loc);
}

// ---- rolls ---------------------------------------------------------------------------------------------------------------------
sp::RollReport SpawnSync::build(const LocalRoll& r) const {
    sp::RollReport rep;
    rep.def = r.def;
    rep.none = r.none;
    rep.pick = r.pick;
    for (const auto& m : r.members) {
        sp::Member mm;
        mm.created = m.created;
        mm.netId = m.created ? (cfg_.isHost ? m.ref : 0u) : m.netId;   // a host's created ref keeps its engine id; a client's is issued by the host
        mm.handle = m.ref;
        mm.base = m.base;
        mm.cell = m.cell;
        mm.pos = m.pos;
        rep.members.push_back(mm);
    }
    rep.epoch = view_.epochOf(r.def.id);
    return rep;
}

uint32_t SpawnSync::onLocalRoll(const LocalRoll& r) {
    ++stats_.rollsLocal;
    if (!cfg_.isHost && !hostUp_) return 0;                 // no host to ask: the engine's own result stands
    if (r.members.empty() && !r.none) return 0;
    sp::RollReport rep = build(r);
    const sp::SiteView::LocalDecision d = view_.localRoll(rep);
    if (d.kind != sp::SiteView::LocalKind::Send) {
        d.kind == sp::SiteView::LocalKind::Suppress ? ++stats_.rollsSuppressed : ++stats_.rollsAdopted;
        applyActions(tracker_.refuse(rep, verdictFromSite(d.site, d.why)));
        return 0;
    }
    for (const auto& m : r.members) {
        if (FNV_MUT(41)) break;                             // twin 41: the roll is visible while the host decides
        if (eng_.setHeld(m.ref, true)) heldRefs_.insert(m.ref);
        ++stats_.rollsHeld;
    }
    const uint32_t id = tracker_.begin(rep, wall_);
    if (cfg_.isHost) {
        sp::SiteRegistry::Out out;
        registry_->onRoll(rep, cfg_.self, wall_, out);
        flush(out);
    } else {
        ++stats_.sent;
        sp::MRoll m;
        m.r = rep;
        sendBody(kHostPlayer, m);
    }
    return id;
}

void SpawnSync::rollPlaced(RefId ref, const RefInfo& info) {
    const SiteId sid = sp::siteIdOf(sp::SiteKind::Placed, info.wireRef, info.cell);
    // one roll per world, unless the site is due to respawn: then the engine's fresh roll (cell reset) is the candidate for the next generation
    if (rolledPlaced_.count(ref) && view_.stateOf(sid) != sp::SiteState::Respawnable) return;
    if (tracker_.hasPending(sid)) return;                    // its roll is already out
    rolledPlaced_.insert(ref);
    LocalRoll r;
    r.def.kind = sp::SiteKind::Placed;
    r.def.anchor = info.wireRef;
    r.def.cellForm = info.cellForm;
    r.def.cell = info.cell;
    r.def.pos = info.pos;
    r.def.interior = info.interior;
    r.def.id = sp::siteIdOf(sp::SiteKind::Placed, info.wireRef, info.cell);
    r.pick = info.base;
    LocalRollMember m;
    m.ref = ref; m.created = false; m.netId = info.netId; m.base = info.base; m.cell = info.cell; m.pos = info.pos;
    r.members.push_back(m);
    onLocalRoll(r);
}

void SpawnSync::onPlacedLoaded(RefId ref) {
    RefInfo i;
    if (!eng_.describe(ref, i) || !i.valid || i.created || ignored(ref)) return;
    if (!i.leveledFrom.plugin.empty() && i.netId != 0 && !i.dead) rollPlaced(ref, i);
    SiteId s = 0;
    if (i.netId != 0 && view_.siteOfMember(i.netId, s)) enforce(view_.directivesFor(s));
}

void SpawnSync::rollGroup(const std::vector<RefId>& refs) {
    std::vector<RefInfo> infos;
    for (RefId r : refs) {
        RefInfo i;
        if (eng_.describe(r, i) && i.valid && i.created && !i.dead) infos.push_back(i);
    }
    if (infos.empty()) return;
    ++stats_.detectedGroups;
    const RefInfo& first = infos.front();
    LocalRoll roll;
    roll.def.cell = first.cell;
    roll.def.cellForm = first.cellForm;
    roll.def.pos = first.pos;
    roll.def.interior = first.interior;
    if (!first.encounterZone.plugin.empty()) {
        roll.def.kind = sp::SiteKind::Encounter;
        roll.def.anchor = first.encounterZone;
        roll.def.id = sp::siteIdOf(sp::SiteKind::Encounter, first.encounterZone, first.cell);
    } else {
        roll.def.kind = sp::SiteKind::Ambient;
        roll.def.id = sp::ambientSiteId(first.cell, first.pos.x, first.pos.y);
    }
    roll.pick = first.base;
    for (const auto& i : infos) {
        LocalRollMember m;
        m.ref = i.ref; m.created = true; m.base = i.base; m.cell = i.cell; m.pos = i.pos;
        roll.members.push_back(m);
    }
    onLocalRoll(roll);
}

void SpawnSync::detect(uint64_t wallMs) {
    if (det_.size() == 0 || wallMs < nextDetectMs_) return;
    nextDetectMs_ = wallMs + 100;                            // candidates are read from the engine at most 10 times a second
    auto where = [this](RefId r, CellId& cell, sp::Vec3& pos) {
        NetId id = 0;
        if (bound_.count(r) || hostScript_.count(r) || eng_.netOfCreated(r, id)) {   // explained meanwhile (a SpawnRef created it, a script announced it)
            if (heldRefs_.erase(r)) eng_.setHeld(r, false);
            return false;
        }
        RefInfo i;
        if (!eng_.describe(r, i) || !i.valid || i.dead || !i.created) { heldRefs_.erase(r); return false; }
        cell = i.cell; pos = i.pos;
        return true;
    };
    for (const auto& g : det_.take(wallMs, cfg_.detectSettleMs, cfg_.detectRadius, sp::kMaxMembers, where)) rollGroup(g);
}

bool SpawnSync::resolveCreated(RefId ref, NetId& id, FormRef& wire) {
    auto it = bound_.find(ref);
    if (it != bound_.end()) { id = it->second; wire = fnvcoop::createdRef(id); return true; }
    if (cfg_.isHost && hostScript_.count(ref)) { id = ref; wire = fnvcoop::createdRef(ref); return true; }
    NetId n = 0;
    if (eng_.netOfCreated(ref, n)) { id = n; wire = fnvcoop::createdRef(n); return true; }
    if (ignored(ref)) return false;
    if (heldRefs_.count(ref)) return false;
    if (!cfg_.isHost && !hostUp_) return false;
    if (FNV_MUT(53)) { id = ref; wire = fnvcoop::createdRef(ref); return true; }   // twin 53: tracked at once under an id nobody else knows
    ++stats_.unresolvedSeen;
    if (det_.note(ref, wall_) && !FNV_MUT(41)) {     // hide it at once: the engine made it before the host could be asked (twin 41: it stays visible)
        if (eng_.setHeld(ref, true)) heldRefs_.insert(ref);
    }
    return false;
}

bool SpawnSync::boundNet(RefId ref, NetId& id) const {
    auto it = bound_.find(ref);
    if (it == bound_.end()) return false;
    id = it->second;
    return true;
}

// ---- acting on results -----------------------------------------------------------------------------------------------------------
void SpawnSync::applyActions(const std::vector<sp::Action>& acts) {
    for (const auto& a : acts) {
        switch (a.kind) {
            case sp::Action::Bind:
                if (FNV_MUT(43)) break;                      // twin 43: the roller keeps an actor nobody can name
                eng_.bindCreated(a.handle, a.netId);
                bound_[a.handle] = a.netId;
                ++stats_.bound;
                break;
            case sp::Action::Show:
                if (a.handle) { eng_.setHeld(a.handle, false); heldRefs_.erase(a.handle); ++stats_.shown; }
                break;
            case sp::Action::Drop:
                if (a.handle) { eng_.deleteRef(a.handle); heldRefs_.erase(a.handle); bound_.erase(a.handle); ++stats_.dropped; }
                break;
            case sp::Action::Rebase: {
                const RefId ref = a.handle ? a.handle : eng_.refOfNet(a.netId);
                if (!ref) break;
                if (eng_.rebase(ref, a.base)) ++stats_.rebased; else ++stats_.rebaseFailed;
                heldRefs_.erase(ref);
                break;
            }
            case sp::Action::EnsureDead: {
                const RefId ref = a.handle ? a.handle : eng_.refOfNet(a.netId);
                if (!ref) break;
                RefInfo i;
                if (eng_.describe(ref, i) && i.valid && !i.dead && eng_.kill(ref)) ++stats_.madeDead;
                heldRefs_.erase(ref);
                break;
            }
            case sp::Action::Resend:
                if (FNV_MUT(44)) break;                      // twin 44: a lost report is never repeated
                if (cfg_.isHost) {
                    sp::SiteRegistry::Out out;
                    registry_->onRoll(a.report, cfg_.self, wall_, out);
                    flush(out);
                } else {
                    ++stats_.sent;
                    sp::MRoll m;
                    m.r = a.report;
                    sendBody(kHostPlayer, m);
                }
                break;
        }
    }
}

void SpawnSync::enforce(const std::vector<sp::Directive>& ds) {
    if (FNV_MUT(45)) return;                                 // twin 45: placed refs keep whatever this machine's engine rolled
    for (const auto& d : ds) {
        const RefId ref = eng_.refOfNet(d.netId);
        if (!ref || heldRefs_.count(ref)) continue;          // not loaded here yet (enforced again on load), or a verdict is still pending
        RefInfo i;
        if (!eng_.describe(ref, i) || !i.valid) continue;
        if (d.kind == sp::Directive::EnsureDead) {
            if (!i.dead && eng_.kill(ref)) ++stats_.madeDead;
        } else {
            if (i.base != d.base || i.disabled) { if (eng_.rebase(ref, d.base)) ++stats_.rebased; else ++stats_.rebaseFailed; }
        }
    }
}

void SpawnSync::onSiteApplied(const sp::SiteMsg& s) {
    if (!FNV_MUT(50)) applyActions(tracker_.onSite(s, wall_));
    enforce(view_.directivesFor(s.def.id));
}

void SpawnSync::onVerdict(const sp::VerdictMsg& v) {
    const std::vector<sp::Action> acts = tracker_.onVerdict(v, [this](NetId id) { return eng_.isCreatedBound(id); }, wall_);
    applyActions(acts);
    if (v.verdict != sp::Verdict::Accept) {
        // the owner's record may have arrived BEFORE this refusal: placed refs that now wait for it can be settled from the view at once
        sp::SiteMsg m;
        if (view_.find(v.site, m) && !FNV_MUT(50)) applyActions(tracker_.onSite(m, wall_));
    }
}

// ---- host ----------------------------------------------------------------------------------------------------------------------
void SpawnSync::flush(sp::SiteRegistry::Out& out) {
    for (auto& o : out.own) {
        view_.apply(o);
        sp::MOwn m;
        m.m = o;
        broadcast(m);
    }
    for (auto& s : out.sites) {
        view_.apply(s);
        sp::MSite m;
        m.s = s;
        broadcast(m);
        onSiteApplied(s);
    }
    for (auto& c : out.spawns) {
        if (c.origin != cfg_.self && !FNV_MUT(58)) {
            if (eng_.createFromSpawnRef(c.spawn)) ++stats_.hostCopies;      // the host never rolled this one: it needs its own copy
        }
        if (!FNV_MUT(42) && host_) { host_->commitSpawn(c.origin, c.spawn); ++stats_.spawnCommits; }
    }
    for (auto& v : out.verdicts) {
        if (v.to == cfg_.self) {
            if (!FNV_MUT(46)) onVerdict(v.msg);
        } else {
            sp::MVerdict m;
            m.v = v.msg;
            sendBody(v.to, m);
        }
    }
    out.clear();
}

void SpawnSync::sendJoinState(PlayerId slot) {
    sp::SiteRegistry::Out o;
    registry_->snapshot(o);
    for (auto& x : o.own) { sp::MOwn m; m.m = x; sendBody(slot, m); }
    for (auto& s : o.sites) { sp::MSite m; m.s = s; sendBody(slot, m); }
}

void SpawnSync::onRollMsg(PlayerId from, const sp::RollReport& r) {
    if (!joined_.count(from)) { ++stats_.spoofed; return; }
    sp::SiteRegistry::Out out;
    registry_->onRoll(r, from, wall_, out);
    flush(out);
}

void SpawnSync::onMemberDied(NetId id) {
    if (!cfg_.isHost || FNV_MUT(56)) return;
    sp::SiteRegistry::Out out;
    registry_->onMemberDied(id, wall_, out);
    flush(out);
}

void SpawnSync::onCellReset(CellId cell) {
    if (!cfg_.isHost) return;
    sp::SiteRegistry::Out out;
    registry_->onCellReset(cell, out);
    flush(out);
}

void SpawnSync::forceRespawn(SiteId id) {
    if (!cfg_.isHost) return;
    sp::SiteRegistry::Out out;
    registry_->forceRespawn(id, out);
    flush(out);
}

void SpawnSync::onHostSpawnEffect(const sp::SiteDef& def, const fnvcoop::SpawnRef& s) {
    if (!cfg_.isHost) return;
    hostScript_.insert(s.id);
    det_.forget(s.id);
    if (heldRefs_.erase(s.id)) eng_.setHeld(s.id, false);   // the feed saw it a frame before the capture did: it was hidden as a possible roll
    sp::SiteRegistry::Out out;
    registry_->noteScriptSpawn(def, s, wall_, out);
    flush(out);
}

void SpawnSync::onSiteCellLoaded(CellId cell) {
    for (SiteId s : view_.sitesInCell(cell)) enforce(view_.directivesFor(s));
}

// ---- network -------------------------------------------------------------------------------------------------------------------
void SpawnSync::sendBody(PlayerId to, sp::Body b) {
    sp::Envelope e;
    e.src = cfg_.self;
    e.body = std::move(b);
    Bytes bytes = sp::encodeEnvelope(e);
    if (bytes.empty()) return;
    net_.send(to, std::move(bytes));
}

void SpawnSync::broadcast(const sp::Body& b) {
    for (PlayerId p : joined_) sendBody(p, b);
}

void SpawnSync::receive(PlayerId from, const uint8_t* data, size_t size) {
    sp::Envelope e;
    if (!sp::decodeEnvelope(data, size, e)) { ++stats_.undecodable; return; }
    ++stats_.received;
    if (e.src != from && !FNV_MUT(49)) { ++stats_.spoofed; return; }
    if (cfg_.isHost) {
        if (const auto* r = std::get_if<sp::MRoll>(&e.body)) { onRollMsg(from, r->r); return; }
        if (const auto* d = std::get_if<sp::MDigest>(&e.body)) {
            if (!joined_.count(from)) { ++stats_.spoofed; return; }
            if (d->digest == registry_->digest()) { mismatchStreak_[from] = 0; return; }
            ++stats_.digestMismatch;
            // one mismatch can be a record in flight; two in a row (2 s apart) is a real divergence: send the whole table
            if (++mismatchStreak_[from] >= 2 && !FNV_MUT(47)) {
                mismatchStreak_[from] = 0;
                ++stats_.healsSent;
                sendJoinState(from);
            }
            return;
        }
        if (std::get_if<sp::MResync>(&e.body)) {
            if (!joined_.count(from)) { ++stats_.spoofed; return; }
            ++stats_.resyncsAnswered;
            sendJoinState(from);
            return;
        }
        ++stats_.wrongDirection;                              // a client has no business sending verdicts, site records or ownership
        return;
    }
    if (from != kHostPlayer && !FNV_MUT(49)) { ++stats_.spoofed; return; }   // twin 49: a verdict from another client is believed
    if (const auto* v = std::get_if<sp::MVerdict>(&e.body)) { onVerdict(v->v); return; }
    if (const auto* s = std::get_if<sp::MSite>(&e.body)) {
        if (view_.apply(s->s) == fnvcoop::ApplyResult::Applied) onSiteApplied(s->s);
        return;
    }
    if (const auto* o = std::get_if<sp::MOwn>(&e.body)) { view_.apply(o->m); return; }
    ++stats_.wrongDirection;
}

void SpawnSync::tick(uint64_t wallMs, uint64_t gameMs) {
    wall_ = wallMs;
    game_ = gameMs;
    if (cfg_.isHost) {
        registry_->setGameTime(gameMs);
        sp::SiteRegistry::Out out;
        registry_->tick(wallMs, out);
        flush(out);
    }
    applyActions(tracker_.tick(wallMs));
    if (wall_ >= nextEnforceMs_) {
        nextEnforceMs_ = wall_ + cfg_.enforceEveryMs;
        const std::vector<SiteId> ids = view_.ids();
        if (!ids.empty() && !FNV_MUT(59)) {
            const size_t n = std::min<size_t>(cfg_.enforcePerTick, ids.size());
            for (size_t k = 0; k < n; ++k) enforce(view_.directivesFor(ids[(enforceCursor_ + k) % ids.size()]));
            enforceCursor_ = (enforceCursor_ + n) % ids.size();
        }
    }
    if (!cfg_.isHost && hostUp_ && wall_ >= lastDigestMs_ + cfg_.digestEveryMs) {
        lastDigestMs_ = wall_;
        sp::MDigest d;
        d.digest = view_.digest();
        d.sites = static_cast<uint32_t>(view_.siteCount());
        sendBody(kHostPlayer, d);
        ++stats_.digestsSent;
    }
    detect(wallMs);
}

void SpawnSync::purge() {
    if (!FNV_MUT(55)) { bound_.clear(); hostScript_.clear(); heldRefs_.clear(); rolledPlaced_.clear(); }
    det_.clear();
    (void)tracker_.abandon();                                // the refs they point at belong to the world that was just unloaded
    if (!cfg_.isHost) view_.clear();
}

Bytes SpawnSync::saveState() const {
    if (!registry_) return {};
    fnvcoop::Archive w = fnvcoop::Archive::writer();
    registry_->save(w);
    return w.finish();
}

bool SpawnSync::loadState(const Bytes& b) {
    if (!registry_ || b.empty()) return false;
    fnvcoop::Archive r = fnvcoop::Archive::reader(b.data(), b.size());
    if (!registry_->load(r)) return false;
    view_.clear();
    sp::SiteRegistry::Out snap;
    registry_->snapshot(snap);
    for (auto& o : snap.own) { view_.apply(o); sp::MOwn m; m.m = o; broadcast(m); }
    for (auto& s : snap.sites) { view_.apply(s); sp::MSite m; m.s = s; broadcast(m); }
    return true;
}

// ---- queries -------------------------------------------------------------------------------------------------------------------
SiteInfo SpawnSync::siteInfo(SiteId id) const {
    SiteInfo si;
    sp::SiteMsg m;
    if (!view_.find(id, m)) return si;
    si.known = true;
    si.site = id;
    si.state = m.state;
    si.owner = view_.ownerOf(id);
    si.epoch = view_.epochOf(id);
    si.generation = m.generation;
    si.mine = view_.iOwn(id);
    si.interior = m.def.interior;
    si.members = m.members.size();
    for (const auto& x : m.members) if (x.dead) ++si.dead;
    si.pick = m.pick;
    return si;
}

std::vector<SiteInfo> SpawnSync::sites() const {
    std::vector<SiteInfo> v;
    for (SiteId id : view_.ids()) v.push_back(siteInfo(id));
    return v;
}

}  // namespace nvcoop::spawn
