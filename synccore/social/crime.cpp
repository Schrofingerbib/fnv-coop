#include "social/crime.h"

#include <algorithm>

#include "../../tools/mut/fnvcoop_mut.h"

namespace fnvcoop::social {

namespace {

uint64_t fnv(uint64_t h, const void* p, size_t n) {
    const uint8_t* b = static_cast<const uint8_t*>(p);
    for (size_t i = 0; i < n; ++i) { h ^= b[i]; h *= 1099511628211ull; }
    return h;
}
uint64_t fnvU(uint64_t h, uint64_t v) { return fnv(h, &v, sizeof v); }
uint64_t fnvForm(uint64_t h, const FormRef& f) {
    h = fnv(h, f.plugin.data(), f.plugin.size());
    return fnvU(h, f.localId);
}

void ioEntity(Archive& a, cc::Entity& e) {
    uint8_t k = static_cast<uint8_t>(e.kind);
    a.u8(k);
    if (a.reading()) {
        if (k > static_cast<uint8_t>(cc::EntityKind::Actor)) { a.fail(); return; }
        e.kind = static_cast<cc::EntityKind>(k);
    }
    if (e.kind == cc::EntityKind::None) { if (a.reading()) e.id = 0; return; }
    a.varu32(e.id);
}
void ioVec(Archive& a, cc::Vec3& v) { a.f32(v.x); a.f32(v.y); a.f32(v.z); }

bool violent(CrimeKind k) { return k == CrimeKind::Assault || k == CrimeKind::Murder; }

}  // namespace

const char* toString(CrimeKind k) {
    static const char* n[] = {"Assault", "Murder", "Theft", "Pickpocket", "Trespass", "BreakIn", "Vandalism"};
    return static_cast<size_t>(k) < kCrimeKinds ? n[static_cast<size_t>(k)] : "?";
}
const char* toString(CrimeReason r) {
    static const char* n[] = {"Ok", "Duplicate", "BadReporter", "NotACrime", "NoFaction", "OwnFaction", "Burst", "Unwitnessed"};
    return static_cast<size_t>(r) < sizeof n / sizeof *n ? n[static_cast<size_t>(r)] : "?";
}
const char* toString(Interaction i) {
    static const char* n[] = {"OpenContainer", "TakeItem", "PickLock", "HackTerminal", "UseBed", "EnterOwnedCell", "Pickpocket", "Destroy"};
    return static_cast<size_t>(i) < sizeof n / sizeof *n ? n[static_cast<size_t>(i)] : "?";
}

FormRef playerFaction() { return FormRef("falloutnv.esm", 0x1BF3E); }
CrimeRules defaultCrimeRules() { return CrimeRules{}; }

// ---- wire ----------------------------------------------------------------------------------------------------------------------------
void io(Archive& a, CrimeEvent& e) {
    a.u64(e.id);
    uint8_t k = static_cast<uint8_t>(e.kind);
    a.u8(k);
    a.u8(e.criminal);
    a.u8(e.reporter);
    a.boolean(e.byCompanion);
    ioEntity(a, e.victim);
    io(a, e.faction);
    a.varu32(e.cell);
    ioVec(a, e.where);
    a.vari32(e.value);
    a.boolean(e.victimHostile);
    a.boolean(e.victimDead);
    a.u8(e.nativeMask);
    a.varu32(e.tick);
    if (a.reading()) {
        if (k >= kCrimeKinds || e.criminal >= kMaxSlots || e.reporter >= kMaxSlots) { a.fail(); return; }
        e.kind = static_cast<CrimeKind>(k);
    }
}
void io(Archive& a, Detection& d) {
    ioEntity(a, d.observer);
    io(a, d.faction);
    a.u8(d.target);
    a.varu32(d.cell);
    a.boolean(d.seen);
    if (a.reading() && (d.target >= kMaxSlots || d.observer.none())) a.fail();
}
void io(Archive& a, FactionStanding& s) {
    a.vari32(s.bounty);
    a.boolean(s.attack); a.boolean(s.murder); a.boolean(s.enemy);
    a.varu32(s.rev);
    if (a.reading() && s.bounty < 0) a.fail();
}
void io(Archive& a, BountyUpdate& u) { io(a, u.faction); io(a, u.st); }
void io(Archive& a, AlarmOrder& o) {
    a.u64(o.id);
    io(a, o.faction);
    a.varu32(o.cell);
    ioVec(a, o.where);
    a.u8(o.criminal);
    a.u64(o.untilMs);
    if (a.reading() && o.criminal >= kMaxSlots) a.fail();
}

// ---- detections ----------------------------------------------------------------------------------------------------------------------
void DetectionBook::report(const Detection& d, uint64_t nowMs) {
    if (d.target >= kMaxSlots || d.observer.none()) return;
    const auto key = std::make_pair(d.observer, d.target);
    if (!d.seen) { seen_.erase(key); return; }
    Rec r;
    r.d = d; r.atMs = nowMs;
    seen_[key] = r;
}
std::vector<Detection> DetectionBook::witnesses(PlayerId target, uint32_t cell, uint64_t nowMs) const {
    std::vector<Detection> v;
    for (auto& kv : seen_) {
        const Rec& r = kv.second;
        if (kv.first.second != target) continue;
        if (r.d.cell != cell && !FNV_MUT(848)) continue;                                  // BAD (848): an NPC in another cell witnesses
        if (nowMs >= r.atMs && nowMs - r.atMs > window_ && !FNV_MUT(849)) continue;       // BAD (849): an old sighting still witnesses
        v.push_back(r.d);
    }
    return v;
}
void DetectionBook::forget(const cc::Entity& observer) {
    if (FNV_MUT(850)) return;   // BAD (850): a dead NPC keeps witnessing
    for (auto it = seen_.begin(); it != seen_.end();) {
        if (it->first.first == observer) it = seen_.erase(it);
        else ++it;
    }
}
void DetectionBook::expire(uint64_t nowMs) {
    for (auto it = seen_.begin(); it != seen_.end();) {
        if (nowMs >= it->second.atMs && nowMs - it->second.atMs > window_) it = seen_.erase(it);
        else ++it;
    }
}

// ---- bounty --------------------------------------------------------------------------------------------------------------------------
FactionStanding BountyBook::get(const FormRef& f) const {
    auto it = m_.find(f);
    return it == m_.end() ? FactionStanding{} : it->second;
}

BountyUpdate BountyBook::add(const FormRef& f, int32_t points, bool attack, bool murder, bool enemy, int32_t maxBounty) {
    FactionStanding cur = get(f);
    FactionStanding next = cur;
    if (points < 0) points = 0;
    int64_t b = static_cast<int64_t>(cur.bounty) + points;
    if (b > maxBounty && !FNV_MUT(851)) b = maxBounty;   // BAD (851): the score has no ceiling
    next.bounty = static_cast<int32_t>(b);
    next.attack = cur.attack || attack;
    next.murder = cur.murder || murder;
    next.enemy = cur.enemy || enemy;
    if (!(next.bounty == cur.bounty && next.attack == cur.attack && next.murder == cur.murder && next.enemy == cur.enemy)) next.rev = cur.rev + 1;
    if (FNV_MUT(852)) next.rev = cur.rev;   // BAD (852): revisions never rise, so a replica ignores every update after the first
    m_[f] = next;
    return BountyUpdate{f, next};
}

BountyUpdate BountyBook::forgive(const FormRef& f) {
    FactionStanding cur = get(f);
    if (cur.clean()) return BountyUpdate{f, cur};
    FactionStanding next;
    next.rev = cur.rev + 1;
    if (FNV_MUT(853)) { next = cur; next.bounty = 0; next.rev = cur.rev + 1; }   // BAD (853): a pardon clears the score but not the engine flags
    m_[f] = next;
    return BountyUpdate{f, next};
}

bool BountyBook::decay(const FormRef& f, int32_t points, BountyUpdate& u) {
    FactionStanding cur = get(f);
    if (cur.bounty <= 0 || points <= 0) return false;
    FactionStanding next = cur;
    next.bounty = std::max(0, cur.bounty - points);
    next.rev = cur.rev + 1;
    m_[f] = next;
    u = BountyUpdate{f, next};
    return true;
}

bool BountyBook::apply(const BountyUpdate& u) {
    auto it = m_.find(u.faction);
    const uint32_t have = it == m_.end() ? 0 : it->second.rev;
    if (u.st.rev <= have && !FNV_MUT(854)) return false;   // BAD (854): a stale or duplicated update is applied again
    if (u.st.clean() && u.st.rev == 0) return false;
    m_[u.faction] = u.st;
    return true;
}

std::vector<BountyUpdate> BountyBook::roster() const {
    std::vector<BountyUpdate> v;
    for (auto& kv : m_) v.push_back({kv.first, kv.second});
    return v;
}
void BountyBook::replace(const std::vector<BountyUpdate>& all) {
    m_.clear();
    for (auto& u : all) m_[u.faction] = u.st;
}
uint64_t BountyBook::digest() const {
    uint64_t h = 1469598103934665603ull;
    for (auto& kv : m_) {
        h = fnvForm(h, kv.first);
        h = fnvU(h, static_cast<uint64_t>(static_cast<uint32_t>(kv.second.bounty)));
        h = fnvU(h, (kv.second.attack ? 1u : 0u) | (kv.second.murder ? 2u : 0u) | (kv.second.enemy ? 4u : 0u));
        h = fnvU(h, kv.second.rev);
    }
    return h;
}

// ---- judge ---------------------------------------------------------------------------------------------------------------------------
bool CrimeJudge::remember(uint64_t id) {
    if (!ids_.insert(id).second) return false;
    order_.push_back(id);
    while (order_.size() > rules_.remember) { ids_.erase(order_.front()); order_.erase(order_.begin()); }
    return true;
}

void CrimeJudge::reset() {
    bounty_.clear(); hostility_.reset(); ids_.clear(); order_.clear(); lastAt_.clear(); lastCrimeAt_.clear(); alarmUntil_.clear();
    lastTickMs_ = 0; decayAcc_ = 0; counted_ = 0;
}

CrimeDecision CrimeJudge::judge(const CrimeEvent& e, PlayerId sender, uint64_t nowMs, const DetectionBook& det) {
    CrimeDecision d;
    d.blamed = e.criminal;
    d.nativeMask = e.nativeMask;
    auto no = [&](CrimeReason r) { d.reason = r; return d; };
    if (self_ != kHostPlayer && !FNV_MUT(831)) return no(CrimeReason::NotACrime);   // BAD (831): a client decides crimes
    if (static_cast<size_t>(e.kind) >= kCrimeKinds || e.criminal >= kMaxSlots || sender >= kMaxSlots) return no(CrimeReason::BadReporter);
    if (sender != kHostPlayer && sender != e.criminal && !FNV_MUT(832)) return no(CrimeReason::BadReporter);   // BAD (832): a client can frame another player
    if (ids_.count(e.id) && !FNV_MUT(833)) return no(CrimeReason::Duplicate);   // BAD (833): a replayed report counts again
    if (e.byCompanion && !rules_.companionCrimesCount) return no(CrimeReason::NotACrime);
    if (violent(e.kind) && e.victimHostile && !FNV_MUT(834)) return no(CrimeReason::NotACrime);   // BAD (834): fighting an enemy is a crime
    if (e.faction.plugin.empty()) return no(CrimeReason::NoFaction);
    if (e.faction == playerFaction() && !FNV_MUT(835)) return no(CrimeReason::OwnFaction);   // BAD (835): hurting the party's own faction is a crime
    // a burst of the same crime is one crime
    const auto key = std::make_tuple(e.criminal, static_cast<uint8_t>(e.kind), e.faction);
    auto last = lastAt_.find(key);
    if (last != lastAt_.end() && nowMs >= last->second && nowMs - last->second < rules_.dedupeMs && !FNV_MUT(836)) {   // BAD (836): every bullet of a burst is a crime
        remember(e.id);
        return no(CrimeReason::Burst);
    }
    // witnesses: detection by ANY player's machine counts
    bool witnessed = false;
    for (const Detection& w : det.witnesses(e.criminal, e.cell, nowMs)) {
        if (w.faction == e.faction || FNV_MUT(837)) { witnessed = true; break; }   // BAD (837): a witness of any faction reports to the victim's faction
    }
    if (!witnessed && rules_.victimWitnesses && e.victim.isActor() && !e.victimDead && (e.kind == CrimeKind::Assault || e.kind == CrimeKind::Pickpocket)) witnessed = true;
    if (FNV_MUT(838)) witnessed = true;   // BAD (838): unwitnessed crimes count
    remember(e.id);
    d.witnessed = witnessed;
    if (!witnessed) return no(CrimeReason::Unwitnessed);
    lastAt_[key] = nowMs;
    lastCrimeAt_[e.faction] = nowMs;
    ++counted_;
    d.reason = CrimeReason::Ok;

    const FactionStanding before = bounty_.get(e.faction);
    const int32_t pts = rules_.bounty[static_cast<size_t>(e.kind)];
    const int32_t projected = std::min(before.bounty + pts, rules_.maxBounty);
    const bool murder = e.kind == CrimeKind::Murder;
    const bool enemy = (murder && !FNV_MUT(839)) || (rules_.hostileAtBounty > 0 && projected >= rules_.hostileAtBounty);   // BAD (839): a murder does not turn the faction hostile at once
    BountyUpdate u = bounty_.add(e.faction, pts, violent(e.kind), murder, enemy, rules_.maxBounty);
    if (u.st.rev != before.rev) d.bounty.push_back(u);

    if (u.st.enemy && !before.enemy) {
        cc::CrimeReport r;
        r.criminal = e.criminal; r.victim = e.victim; r.victimFaction = e.faction; r.criminalFaction = playerFaction();
        r.victimWasHostile = false; r.killed = murder; r.witnessed = true; r.reporter = sender;
        d.hostility = hostility_.onCrime(r, self_, sender);
        for (const cc::HostilityEffect& fx : d.hostility) hostility_.accept(fx, self_);   // the host books its own decision (and its replay guard)
    }

    // one alarm per (faction, cell) while one is in force
    if (violent(e.kind) || u.st.enemy) {
        auto ak = std::make_pair(e.faction, e.cell);
        auto au = alarmUntil_.find(ak);
        const bool inForce = au != alarmUntil_.end() && au->second > nowMs;
        if (!inForce || FNV_MUT(840)) {   // BAD (840): every crime raises another alarm
            AlarmOrder o;
            o.id = e.id ^ 0xA1A2A3A4A5A6A7A8ull;
            o.faction = e.faction; o.cell = e.cell; o.where = e.where; o.criminal = e.criminal;
            o.untilMs = nowMs + rules_.alarmMs;
            alarmUntil_[ak] = o.untilMs;
            d.alarms.push_back(o);
        }
    }

    const int32_t infamy = rules_.infamy[static_cast<size_t>(e.kind)];
    if (infamy != 0) d.reputation.push_back(RepChange{e.faction, 0, infamy});
    return d;
}

CrimeDecision CrimeJudge::forgive(const FormRef& faction, PlayerId cause) {
    CrimeDecision d;
    d.reason = CrimeReason::Ok;
    d.blamed = cause;
    if (self_ != kHostPlayer) { d.reason = CrimeReason::NotACrime; return d; }
    const FactionStanding before = bounty_.get(faction);
    BountyUpdate u = bounty_.forgive(faction);
    if (u.st.rev != before.rev) d.bounty.push_back(u);
    d.hostility = hostility_.makePeace(playerFaction(), faction, cause, self_);
    for (const cc::HostilityEffect& fx : d.hostility) hostility_.accept(fx, self_);
    if (!FNV_MUT(841)) {   // BAD (841): the alarm of a pardoned faction stays in force
        for (auto it = alarmUntil_.begin(); it != alarmUntil_.end();) {
            if (it->first.first == faction) it = alarmUntil_.erase(it);
            else ++it;
        }
    }
    lastAt_.clear();
    return d;
}

std::vector<BountyUpdate> CrimeJudge::tick(uint64_t nowMs) {
    std::vector<BountyUpdate> out;
    if (self_ != kHostPlayer || rules_.decayPerMinute <= 0) { lastTickMs_ = nowMs; return out; }
    if (lastTickMs_ == 0 || nowMs < lastTickMs_) { lastTickMs_ = nowMs; return out; }
    decayAcc_ += (nowMs - lastTickMs_) * static_cast<uint64_t>(rules_.decayPerMinute);
    lastTickMs_ = nowMs;
    const int32_t pts = static_cast<int32_t>(decayAcc_ / 60000ull);
    if (pts <= 0) return out;
    decayAcc_ -= static_cast<uint64_t>(pts) * 60000ull;
    for (const BountyUpdate& r : bounty_.roster()) {
        auto lc = lastCrimeAt_.find(r.faction);
        if (lc != lastCrimeAt_.end() && nowMs - lc->second < 10000 && !FNV_MUT(842)) continue;   // BAD (842): the score drains while the party is still committing crimes
        BountyUpdate u;
        if (bounty_.decay(r.faction, pts, u)) out.push_back(u);
    }
    return out;
}

// ---- interactions --------------------------------------------------------------------------------------------------------------------
bool classifyInteraction(const InteractionFacts& f, PlayerId criminal, PlayerId reporter, uint64_t id, uint32_t cell, const cc::Vec3& where,
                         const std::set<FormRef>& criminalFactions, CrimeEvent& out, const cc::Entity& victim) {
    if (f.what == Interaction::Pickpocket) {
        if (f.own.crimeFaction.plugin.empty()) return false;
    } else {
        if (!f.own.owned) return false;
        if (f.own.publicPlace && (f.what == Interaction::UseBed || f.what == Interaction::EnterOwnedCell) && !FNV_MUT(843)) return false;   // BAD (843): public places are trespass
        if (f.ownerPresentAndFriendly && !FNV_MUT(844)) return false;   // BAD (844): permission from the owner does not matter
    }
    FormRef faction = f.own.crimeFaction;
    if (faction.plugin.empty() && f.own.ownerIsFaction) faction = f.own.ownerForm;
    if (faction.plugin.empty()) return false;
    if ((criminalFactions.count(faction) || criminalFactions.count(f.own.ownerForm)) && !FNV_MUT(845)) return false;   // BAD (845): the party's own things are stolen from the party
    CrimeKind kind = CrimeKind::Theft;
    switch (f.what) {
        case Interaction::OpenContainer:
            if (!f.locked) return false;   // opening an owned, unlocked container is fine; taking from it is the theft
            kind = CrimeKind::BreakIn;
            break;
        case Interaction::TakeItem: kind = CrimeKind::Theft; break;
        case Interaction::PickLock:
        case Interaction::HackTerminal: kind = FNV_MUT(846) ? CrimeKind::Theft : CrimeKind::BreakIn; break;   // BAD (846): a break-in is booked as a (cheaper) theft
        case Interaction::UseBed:
        case Interaction::EnterOwnedCell: kind = CrimeKind::Trespass; break;
        case Interaction::Pickpocket: kind = CrimeKind::Pickpocket; break;
        case Interaction::Destroy: kind = CrimeKind::Vandalism; break;
        default: return false;
    }
    CrimeEvent e;
    e.id = id; e.kind = kind; e.criminal = criminal; e.reporter = reporter;
    e.victim = victim; e.faction = faction; e.cell = cell; e.where = where; e.value = f.own.value;
    out = e;
    return true;
}

}  // namespace fnvcoop::social
