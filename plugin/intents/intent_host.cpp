#include "intents/intent_host.h"

#include <algorithm>
#include <sstream>

#include "fnvcoop_mut.h"

namespace nvcoop::intents {

namespace {

// Twin 312 runs the interaction without an acting scope: nothing is captured, the player context is the host's.
class NoScope : public IActingScope {
public:
    bool record(const fnvcoop::Effect&) override { return false; }
};

constexpr size_t kRecentVerdicts = 32;

}  // namespace

IntentHost::IntentHost(HostDeps deps, Config cfg, ContainerRedirect redirect)
    : deps_(std::move(deps)), cfg_(std::move(cfg)), redirect_(std::move(redirect)) {}

Verdict IntentHost::verdictOf(const Intent& in, Result r) const {
    Verdict v;
    v.session = in.session;
    v.id = in.id;
    v.result = r;
    return v;
}

void IntentHost::count(const Intent& in, Result r) {
    if (r == Result::Executed) {
        ++stats_.executed;
        ++stats_.byKind[static_cast<size_t>(in.kind)];
    } else if (r == Result::Duplicate) {
        ++stats_.duplicates;
    } else {
        ++stats_.rejected[static_cast<size_t>(r)];
    }
}

void IntentHost::onDatagram(PlayerId from, const uint8_t* data, size_t size) {
    Intent in;
    if (decode(data, size, &in, nullptr) != WireType::Intent) { ++stats_.badDatagrams; return; }
    handle(from, in);
}

bool IntentHost::rateOk(SlotState& s, uint64_t t) {
    if (FNV_MUT(307)) return true;   // MUTANT 307: no rate limit (a guest can flood the host's script engine)
    if (!s.init) { s.tokens = cfg_.rateBurst; s.refilledAt = t; s.init = true; }
    if (t > s.refilledAt) {
        s.tokens = std::min<double>(cfg_.rateBurst, s.tokens + (t - s.refilledAt) / 1000.0 * cfg_.ratePerSec);
        s.refilledAt = t;
    }
    if (s.tokens < 1.0) return false;
    s.tokens -= 1.0;
    return true;
}

Verdict IntentHost::handle(PlayerId slot, const Intent& in) {
    ++stats_.received;
    if (!deps_.joined || !deps_.joined(slot)) {
        Verdict v = verdictOf(in, Result::NotJoined);
        count(in, v.result);
        return v;   // not a player: no answer, nothing to answer to
    }
    SlotState& s = slots_[slot];
    if (in.session != s.session) { s.session = in.session; s.lastId = 0; s.recent.clear(); }   // a new join: ids start again
    if (in.id == 0 || (in.id <= s.lastId && !FNV_MUT(308))) {   // MUTANT 308: no exactly-once check (a resend runs the interaction twice)
        ++stats_.duplicates;
        Verdict v = verdictOf(in, in.id == 0 ? Result::Stale : Result::Duplicate);
        for (const Verdict& old : s.recent)
            if (old.id == in.id) { v = old; break; }   // the same answer again; nothing runs twice
        if (deps_.send && deps_.send(slot, encode(v))) ++stats_.verdictsSent; else ++stats_.verdictsFailed;
        return v;
    }
    s.lastId = in.id;
    Verdict v;
    if (!rateOk(s, now())) v = verdictOf(in, Result::RateLimited);
    else v = decide(slot, in, false);
    count(in, v.result);
    s.recent.push_back(v);
    while (s.recent.size() > kRecentVerdicts) s.recent.pop_front();
    if (!FNV_MUT(318)) {   // MUTANT 318: the guest never hears back (its pending list fills up and times out)
        if (deps_.send && deps_.send(slot, encode(v))) ++stats_.verdictsSent; else ++stats_.verdictsFailed;
    }
    return v;
}

Result IntentHost::runLegacy(PlayerId slot, fnvcoop::IntentKind kind, const FormRef& ref) {
    ++stats_.legacy;
    Intent in;
    in.target = ref;
    switch (kind) {
        case fnvcoop::IntentKind::ActivateRef: in.kind = Kind::Activate; break;
        case fnvcoop::IntentKind::TriggerEnter: in.kind = Kind::TriggerEnter; break;
        case fnvcoop::IntentKind::PickUp: in.kind = Kind::PickUp; break;
        default: return Result::BadTarget;
    }
    if (!deps_.joined || !deps_.joined(slot)) { count(in, Result::NotJoined); return Result::NotJoined; }
    SlotState& s = slots_[slot];
    Result r = rateOk(s, now()) ? decide(slot, in, true).result : Result::RateLimited;
    count(in, r);
    return r;
}

bool IntentHost::pickActor(PlayerId slot, Kind k, TargetClass cls, Actor* out) const {
    RefId p = deps_.puppetOf ? deps_.puppetOf(slot) : 0;
    if (FNV_MUT(311)) p = 0;   // MUTANT 311: the puppet is never used (everything runs as the host's player)
    if (p) { out->ref = p; out->puppet = true; return true; }
    RefId g = deps_.ghostOf ? deps_.ghostOf(slot) : 0;
    if (g && !FNV_MUT(311)) { out->ref = g; out->ghost = true; return true; }
    if (!standInAllowed(cfg_, k, cls)) return false;
    out->ref = kHostPlayerRef;
    out->standIn = true;
    return true;
}

Result IntentHost::checkReach(const Actor& a, const TargetInfo& t, Kind k) {
    const float reach = reachOf(cfg_, k);
    Vec3 p;
    if (!cfg_.checkReach || a.standIn || FNV_MUT(313) || reach <= 0.f || !t.hasPos || !deps_.eng->position(a.ref, p, nullptr)) {   // MUTANT 313: never checked
        ++stats_.reachUnchecked;
        return Result::Executed;
    }
    ++stats_.reachChecked;
    return distance(p, t.pos) > reach ? Result::OutOfRange : Result::Executed;
}

Result IntentHost::runScoped(PlayerId slot, const Actor& a, Kind k, const FormRef& target, const std::function<Result(IActingScope&)>& fn) {
    Result r = Result::Failed;
    Cause c;
    c.kind = CauseKind::Intent;
    c.source = target;
    c.aux = static_cast<int32_t>(k);
    bool ran = deps_.runAsGuest && deps_.runAsGuest(slot, [&] {
        std::unique_ptr<IActingScope> scope = FNV_MUT(312) ? std::unique_ptr<IActingScope>(new NoScope()) : deps_.eng->beginActing(a.ref, slot, c);
        if (!scope) { r = Result::Failed; return; }
        r = fn(*scope);
    });   // the scope is closed (its txn emitted) before runAsGuest returns, while the origin is still the guest
    if (!ran) return Result::Failed;
    if (r == Result::Executed) {
        if (a.puppet) ++stats_.actedAsPuppet;
        else if (a.ghost) ++stats_.actedAsGhost;
        else ++stats_.actedAsStandIn;
    }
    return r;
}

Verdict IntentHost::decide(PlayerId slot, const Intent& in, bool /*legacy*/) {
    if (!kindEnabled(cfg_, in.kind) && !FNV_MUT(315)) return verdictOf(in, Result::Disabled);   // MUTANT 315: a switched-off kind still runs
    if (in.kind == Kind::ContainerClose) return doContainerClose(slot, in);
    TargetInfo t;
    if (!deps_.eng->describe(in.target, t) || !t.loaded) return verdictOf(in, Result::NotLoaded);
    // plugin/companions: somebody else's companion is not for this guest to use (its wheel opens on its owner's machine only)
    if ((in.kind == Kind::Activate || in.kind == Kind::DialogueStart) && deps_.allowActivation && !FNV_MUT(327)   // MUTANT 327: the question is never asked
        && !deps_.allowActivation(slot, in.target)) {
        ++stats_.activationsRefused;
        return verdictOf(in, Result::Denied);
    }
    Verdict v = dispatch(slot, in, t);
    // ... and what the interaction touched (a take, an owned or locked thing) is judged by the host on the guest's behalf
    const bool opened = in.kind == Kind::Activate || in.kind == Kind::PickUp || in.kind == Kind::ContainerOpen;
    if (deps_.interacted && opened && !FNV_MUT(328) && (v.result == Result::Executed || FNV_MUT(329)) && !v.hasRedirect) {   // 328: never told; 329: told of refused ones too
        ++stats_.interactionsReported;
        deps_.interacted(slot, in.kind == Kind::PickUp || (in.kind == Kind::Activate && t.cls == TargetClass::Item), in.target);
    }
    return v;
}

Verdict IntentHost::dispatch(PlayerId slot, const Intent& in, const TargetInfo& t) {
    switch (in.kind) {
        case Kind::Activate: return doActivate(slot, in, t);
        case Kind::PickUp: return doPickUp(slot, in, t);
        case Kind::ContainerOpen: return doContainerOpen(slot, in, t);
        case Kind::TriggerEnter: return doEvent(slot, in, t, ev::OnTriggerEnter);
        case Kind::TriggerLeave: return doEvent(slot, in, t, ev::OnTriggerLeave);
        case Kind::Hit: return doEvent(slot, in, t, ev::OnHit);
        case Kind::TerminalUse: return doTerminal(slot, in, t);
        case Kind::DialogueStart: {
            if (t.cls != TargetClass::Npc) return verdictOf(in, Result::BadTarget);
            if (t.dead || t.inCombat) return verdictOf(in, Result::Denied);
            if (deps_.dialogueStart) deps_.dialogueStart(in.target, slot);
            return verdictOf(in, Result::Executed);
        }
        default: return verdictOf(in, Result::BadTarget);
    }
}

Verdict IntentHost::doActivate(PlayerId slot, const Intent& in, const TargetInfo& t) {
    TargetClass cls = t.cls;
    if ((cls == TargetClass::Npc || cls == TargetClass::Creature) && t.dead) cls = TargetClass::Container;
    if (cls == TargetClass::Item) return doPickUp(slot, in, t);          // a legacy ActivateRef on a world item is a claim
    if (cls == TargetClass::Container) return doContainerOpen(slot, in, t);
    if (cls == TargetClass::Npc || cls == TargetClass::Creature || cls == TargetClass::Static) return verdictOf(in, Result::BadTarget);   // talk: 0xD1
    Actor a;
    if (!pickActor(slot, Kind::Activate, cls, &a)) return verdictOf(in, Result::NoPuppet);
    if (Result r = checkReach(a, t, Kind::Activate); r != Result::Executed) return verdictOf(in, r);
    Result r = runScoped(slot, a, Kind::Activate, in.target, [&](IActingScope&) { return deps_.eng->activate(a.ref, in.target) ? Result::Executed : Result::Failed; });
    return verdictOf(in, r);
}

Verdict IntentHost::doPickUp(PlayerId slot, const Intent& in, const TargetInfo& t) {
    if (t.disabled) { ++stats_.claimsLost; return verdictOf(in, Result::Taken); }
    if (t.base.plugin.empty() || t.count <= 0) return verdictOf(in, Result::BadTarget);   // nothing the grant could name
    Actor a;
    if (!pickActor(slot, Kind::PickUp, TargetClass::Item, &a)) return verdictOf(in, Result::NoPuppet);
    if (Result r = checkReach(a, t, Kind::PickUp); r != Result::Executed) return verdictOf(in, r);
    auto it = claims_.find(in.target);
    if (it != claims_.end() && !FNV_MUT(309)) {   // MUTANT 309: no claim table (two players both get the item)
        ++stats_.claimsLost;
        return verdictOf(in, Result::Taken);
    }
    claims_[in.target] = Claim{slot, now()};
    claimOrder_.push_back(in.target);
    while (claimOrder_.size() > cfg_.claimMemory) { claims_.erase(claimOrder_.front()); claimOrder_.pop_front(); }
    Result r = runScoped(slot, a, Kind::PickUp, in.target, [&](IActingScope& sc) {
        if (!deps_.eng->disableRef(in.target)) return Result::Failed;   // it stays in the world for everybody
        fnvcoop::AddItem grant;
        grant.item = t.base;
        grant.count = t.count;
        grant.to = fnvcoop::Recipient::origin();   // the guest's inventory only, never the host's
        sc.record(grant);
        deps_.eng->raiseEvent(a.ref, in.target, ev::OnAdd);   // OnAdd blocks of the item (quest items) run with the guest's puppet as `player`
        return Result::Executed;
    });
    if (r == Result::Executed) {
        ++stats_.claimsWon;
    } else {
        claims_.erase(in.target);   // nothing happened: the item is free for the next try
        claimOrder_.erase(std::remove(claimOrder_.begin(), claimOrder_.end(), in.target), claimOrder_.end());
    }
    return verdictOf(in, r);
}

Verdict IntentHost::doContainerOpen(PlayerId slot, const Intent& in, const TargetInfo& t) {
    if (redirect_.isShared(in.target)) {
        FormRef mine;
        if (!redirect_.resolve(in.target, slot, &mine)) return verdictOf(in, Result::Denied);   // no storage for this player there
        Verdict v = verdictOf(in, Result::Executed);
        v.hasRedirect = true;
        v.redirect = mine;
        ++stats_.redirects;
        return v;
    }
    const uint64_t t0 = now();
    auto it = locks_.find(in.target);
    if (it != locks_.end() && t0 - it->second.refreshedMs > cfg_.lockLeaseMs) { locks_.erase(it); ++stats_.lockExpired; it = locks_.end(); }
    if (it != locks_.end() && it->second.slot != slot && !FNV_MUT(310)) {   // MUTANT 310: no in-use lock (two players in one chest, contents race)
        ++stats_.lockBusy;
        Verdict v = verdictOf(in, Result::Busy);
        v.holder = it->second.slot;
        return v;
    }
    Actor a;
    const bool have = pickActor(slot, Kind::ContainerOpen, t.cls, &a);
    if (have) {
        if (Result r = checkReach(a, t, Kind::ContainerOpen); r != Result::Executed) return verdictOf(in, r);
    }
    if (it == locks_.end()) { locks_[in.target] = Lock{slot, t0, t0}; ++stats_.lockGrants; }
    else it->second.refreshedMs = t0;
    if (have) {
        // the OnActivate half (scripted chests, trapped lids): the engine's Activate with the puppet as activator opens no menu for anybody
        runScoped(slot, a, Kind::ContainerOpen, in.target, [&](IActingScope&) { return deps_.eng->activate(a.ref, in.target) ? Result::Executed : Result::Failed; });
    } else {
        ++stats_.scriptsSkipped;
    }
    return verdictOf(in, Result::Executed);
}

Verdict IntentHost::doContainerClose(PlayerId slot, const Intent& in) {
    auto it = locks_.find(in.target);
    if (it != locks_.end() && it->second.slot == slot) { locks_.erase(it); ++stats_.lockReleased; }
    return verdictOf(in, Result::Executed);   // closing something you do not hold is not an error
}

Verdict IntentHost::doEvent(PlayerId slot, const Intent& in, const TargetInfo& t, uint32_t mask) {
    if (mask == ev::OnHit && (t.cls == TargetClass::Npc || t.cls == TargetClass::Creature) && !FNV_MUT(316))   // MUTANT 316: actor hits run as well (double OnHit)
        return verdictOf(in, Result::Denied);
    if (mask == ev::OnTriggerEnter) {
        auto key = std::make_pair(slot, in.target);
        auto it = lastTrigger_.find(key);
        if (it != lastTrigger_.end() && now() - it->second < cfg_.triggerCooldownMs && !FNV_MUT(317))   // MUTANT 317: no cooldown (standing in a volume re-fires)
            return verdictOf(in, Result::Duplicate);
    }
    Actor a;
    if (!pickActor(slot, in.kind, t.cls, &a)) return verdictOf(in, Result::NoPuppet);
    if (Result r = checkReach(a, t, in.kind); r != Result::Executed) return verdictOf(in, r);
    Result r = runScoped(slot, a, in.kind, in.target, [&](IActingScope&) { return deps_.eng->raiseEvent(a.ref, in.target, mask) ? Result::Executed : Result::Failed; });
    if (r == Result::Executed && mask == ev::OnTriggerEnter) {
        if (lastTrigger_.size() > 2048) lastTrigger_.clear();
        lastTrigger_[std::make_pair(slot, in.target)] = now();
    }
    return verdictOf(in, r);
}

Verdict IntentHost::doTerminal(PlayerId slot, const Intent& in, const TargetInfo& t) {
    if (t.cls != TargetClass::Terminal || in.path.empty() || in.path.size() > kMaxPath) return verdictOf(in, Result::BadTarget);
    Actor a;
    if (!pickActor(slot, Kind::TerminalUse, t.cls, &a)) return verdictOf(in, Result::NoPuppet);
    if (Result r = checkReach(a, t, Kind::TerminalUse); r != Result::Executed) return verdictOf(in, r);
    bool supported = true;
    Result r = runScoped(slot, a, Kind::TerminalUse, in.target, [&](IActingScope&) {
        bool ok = deps_.eng->runTerminalEntry(a.ref, in.target, in.path, &supported);
        return !supported ? Result::Unsupported : ok ? Result::Executed : Result::Failed;
    });
    return verdictOf(in, r);
}

void IntentHost::tick() {
    const uint64_t t = now();
    for (auto it = locks_.begin(); it != locks_.end();) {
        if (t - it->second.refreshedMs > cfg_.lockLeaseMs) { it = locks_.erase(it); ++stats_.lockExpired; }
        else ++it;
    }
}

void IntentHost::playerLeft(PlayerId slot) {
    if (!FNV_MUT(314)) {   // MUTANT 314: a leaver's locks stay until the lease runs out (the chest is dead for everyone for two minutes)
        for (auto it = locks_.begin(); it != locks_.end();) {
            if (it->second.slot == slot) { it = locks_.erase(it); ++stats_.lockReleased; }
            else ++it;
        }
    }
    slots_.erase(slot);
    for (auto it = lastTrigger_.begin(); it != lastTrigger_.end();) {
        if (it->first.first == slot) it = lastTrigger_.erase(it);
        else ++it;
    }
}

void IntentHost::worldReset() {
    claims_.clear();
    claimOrder_.clear();
    locks_.clear();
    lastTrigger_.clear();
}

std::vector<IntentHost::LockInfo> IntentHost::locks() const {
    std::vector<LockInfo> v;
    for (const auto& kv : locks_) v.push_back({kv.first, kv.second.slot, kv.second.sinceMs});
    return v;
}

std::string IntentHost::statusJson() const {
    std::ostringstream o;
    o << "{\"role\":\"host\",\"received\":" << stats_.received << ",\"executed\":" << stats_.executed << ",\"legacy\":" << stats_.legacy
      << ",\"duplicates\":" << stats_.duplicates << ",\"badDatagrams\":" << stats_.badDatagrams << ",\"verdictsSent\":" << stats_.verdictsSent
      << ",\"verdictsFailed\":" << stats_.verdictsFailed << ",\"actedAs\":{\"puppet\":" << stats_.actedAsPuppet << ",\"ghost\":" << stats_.actedAsGhost
      << ",\"standIn\":" << stats_.actedAsStandIn << "},\"reach\":{\"checked\":" << stats_.reachChecked << ",\"unchecked\":" << stats_.reachUnchecked
      << "},\"scriptsSkipped\":" << stats_.scriptsSkipped << ",\"claims\":{\"won\":" << stats_.claimsWon << ",\"lost\":" << stats_.claimsLost
      << ",\"held\":" << claims_.size() << "},\"locks\":{\"granted\":" << stats_.lockGrants << ",\"busy\":" << stats_.lockBusy << ",\"expired\":"
      << stats_.lockExpired << ",\"released\":" << stats_.lockReleased << ",\"held\":" << locks_.size() << "},\"redirects\":" << stats_.redirects
      << ",\"byKind\":{";
    bool first = true;
    for (size_t k = 1; k < static_cast<size_t>(Kind::Count_); ++k) {
        if (!stats_.byKind[k]) continue;
        o << (first ? "" : ",") << '"' << kindName(static_cast<Kind>(k)) << "\":" << stats_.byKind[k];
        first = false;
    }
    o << "},\"rejected\":{";
    first = true;
    for (size_t r = 2; r < static_cast<size_t>(Result::Count_); ++r) {
        if (!stats_.rejected[r]) continue;
        o << (first ? "" : ",") << '"' << resultName(static_cast<Result>(r)) << "\":" << stats_.rejected[r];
        first = false;
    }
    o << "}}";
    return o.str();
}

}  // namespace nvcoop::intents
