#include "wt_broker.h"

#include <iterator>
#include <vector>

#include "fnvcoop_mut.h"

namespace nvwt {

const char* reasonText(Reason r) {
    switch (r) {
        case Reason::None: return "";
        case Reason::Combat: return "Not while someone is in a fight.";
        case Reason::Busy: return "The party is already deciding something.";
        case Reason::Cooldown: return "The party just said no. Try again in a moment.";
        case Reason::TooLong: return "That is too long to wait.";
        case Reason::Locked: return "Fast travel is not possible right now.";
        case Reason::Timeout: return "Nobody answered in time.";
        case Reason::Declined: return "Somebody is not ready.";
        case Reason::Left: return "The player who asked has left.";
        case Reason::Invalid: return "That cannot be done right now.";
        case Reason::NotJoined: return "You are not in the party yet.";
        case Reason::PartyChanged: return "The party changed. Ask again.";
    }
    return "";
}

std::string ConsentBroker::describe(ReqKind kind, uint32_t minutes) {
    if (kind == ReqKind::Travel) return "Fast travel together with the whole party?";
    std::string span;
    if (minutes % 60 == 0) {
        const uint32_t h = minutes / 60;
        span = std::to_string(h) + (h == 1 ? " hour" : " hours");
    } else {
        span = std::to_string(minutes) + " minutes";
    }
    return std::string(kind == ReqKind::Sleep ? "Sleep for " : "Wait for ") + span + " for everyone?";
}

namespace {
inline int idx(ReqKind k) { return k == ReqKind::Travel ? 2 : 0; }   // Wait and Sleep share one cooldown
}  // namespace

Reason ConsentBroker::baseDenial(PlayerId initiator, ReqKind kind, uint32_t minutes, uint64_t nowMs) const {
    const uint32_t joined = party_.joined ? party_.joined() : bit(initiator);
    if (!(joined & bit(initiator))) return Reason::NotJoined;
    if (kind != ReqKind::Travel) {
        if (minutes == 0) return Reason::Invalid;
        if (minutes > cfg_.maxMinutes && !FNV_MUT(121)) return Reason::TooLong;   // MUTANT 121: no cap on a wait / sleep
    }
    if (everDenied_[idx(kind)] && nowMs - lastDeniedMs_[idx(kind)] < cfg_.cooldownMs && !FNV_MUT(116)) return Reason::Cooldown;   // MUTANT 116: a no does not start a cooldown
    const uint32_t fighting = party_.inCombat ? party_.inCombat() : 0;
    if (kind != ReqKind::Travel) {
        if (fighting != 0 && !FNV_MUT(118)) return Reason::Combat;   // MUTANT 118: the party rests in the middle of a fight
    } else {
        if (party_.travelLocked && party_.travelLocked()) return Reason::Locked;
        if (fighting & bit(initiator)) return Reason::Combat;
    }
    return Reason::None;
}

Outcome ConsentBroker::request(PlayerId initiator, ReqKind kind, uint32_t minutes, const FormRef& marker, uint64_t nowMs) {
    Outcome o;
    auto deny = [&](Reason r) {
        o.kind = Outcome::Denied;
        o.reason = r;
        ++stats_.denied;
        return o;
    };
    if (!pending_.empty() && !FNV_MUT(117)) return deny(Reason::Busy);   // MUTANT 117: a second question replaces the first
    if (FNV_MUT(117) && !pending_.empty()) pending_.clear();
    const Reason r = baseDenial(initiator, kind, minutes, nowMs);
    if (r != Reason::None) return deny(r);

    const uint32_t joined = party_.joined ? party_.joined() : bit(initiator);
    const bool tether = party_.tethered && party_.tethered() && !FNV_MUT(122);   // MUTANT 122: a story tether does not make travel a party decision
    const bool consent = kind != ReqKind::Travel || cfg_.travel == TravelMode::Party || tether;
    const bool others = (joined & ~bit(initiator)) != 0;
    if (kind == ReqKind::Travel && consent && others && (party_.inCombat ? party_.inCombat() : 0) != 0) return deny(Reason::Combat);
    if (!consent || (!others && !FNV_MUT(119))) {   // MUTANT 119: even a lone player is asked (and nobody can answer)
        o.kind = Outcome::Granted;
        o.reqId = ++nextReq_;
        o.party = false;
        ++stats_.granted;
        return o;
    }
    Pending p;
    p.reqId = ++nextReq_;
    p.kind = kind;
    p.initiator = initiator;
    p.minutes = minutes;
    p.marker = marker;
    p.joinedAtAsk = joined;
    p.party = kind == ReqKind::Travel;
    p.askedMs = nowMs;
    p.promptId = ask_ ? ask_(initiator, kind, describe(kind, minutes), cfg_.timeoutMs) : 0;
    if (p.promptId == 0) return deny(Reason::Invalid);
    ++stats_.asked;
    o.kind = Outcome::Asked;
    o.reqId = p.reqId;
    o.party = p.party;
    pending_[p.reqId] = std::move(p);
    return o;
}

Reason ConsentBroker::recheck(const Pending& p) const {
    const uint32_t joined = party_.joined ? party_.joined() : bit(p.initiator);
    if (!(joined & bit(p.initiator))) return Reason::Left;
    if (joined & ~p.joinedAtAsk) return Reason::PartyChanged;
    const uint32_t fighting = party_.inCombat ? party_.inCombat() : 0;
    if (p.kind != ReqKind::Travel) return fighting != 0 ? Reason::Combat : Reason::None;
    if (party_.travelLocked && party_.travelLocked()) return Reason::Locked;
    if (fighting & (p.party ? 0xFFu : bit(p.initiator))) return Reason::Combat;
    return Reason::None;
}

void ConsentBroker::finish(Pending p, bool granted, Reason why, uint64_t nowMs) {
    pending_.erase(p.reqId);
    if (granted) {
        ++stats_.granted;
    } else {
        ++stats_.denied;
        if ((why == Reason::Declined || why == Reason::Timeout) && !FNV_MUT(116)) {
            lastDeniedMs_[idx(p.kind)] = nowMs;
            everDenied_[idx(p.kind)] = true;
        }
    }
    Resolved r;
    r.reqId = p.reqId;
    r.kind = p.kind;
    r.initiator = p.initiator;
    r.minutes = p.minutes;
    r.marker = p.marker;
    r.granted = granted;
    r.reason = granted ? Reason::None : why;
    r.party = granted && p.party;
    if (resolve_) resolve_(r);
}

bool ConsentBroker::onPromptResult(uint32_t promptId, bool confirmed, bool timedOut, uint64_t nowMs) {
    for (auto& kv : pending_) {
        if (kv.second.promptId != promptId) continue;
        Pending p = kv.second;
        if (timedOut && !FNV_MUT(120)) {   // MUTANT 120: a timed-out question counts as a yes
            finish(std::move(p), false, Reason::Timeout, nowMs);
        } else if (!confirmed && !FNV_MUT(120)) {
            finish(std::move(p), false, Reason::Declined, nowMs);
        } else {
            const Reason why = FNV_MUT(115) ? Reason::None : recheck(p);   // MUTANT 115: the answer is trusted without checking the party again
            finish(std::move(p), why == Reason::None, why, nowMs);
        }
        return true;
    }
    auto late = cancelledPrompts_.find(promptId);
    if (late != cancelledPrompts_.end()) {
        ++stats_.lateAnswers;
        return true;   // ours, but the request was already resolved: swallow it
    }
    return false;
}

void ConsentBroker::onPlayerLeft(PlayerId slot, uint64_t nowMs) {
    if (FNV_MUT(123)) return;   // MUTANT 123: a player who left keeps the question open until the timeout
    std::vector<Pending> gone;
    for (auto& kv : pending_) if (kv.second.initiator == slot) gone.push_back(kv.second);
    for (auto& p : gone) {
        cancelledPrompts_[p.promptId] = nowMs;
        finish(p, false, Reason::Left, nowMs);
    }
}

void ConsentBroker::tick(uint64_t nowMs) {
    std::vector<Pending> late;
    for (auto& kv : pending_)
        if (nowMs - kv.second.askedMs >= static_cast<uint64_t>(cfg_.timeoutMs) + cfg_.graceMs) late.push_back(kv.second);
    for (auto& p : late) {
        if (!FNV_MUT(124)) cancelledPrompts_[p.promptId] = nowMs;   // MUTANT 124: the late answer of a timed-out question is not recognised
        finish(p, false, Reason::Timeout, nowMs);
    }
    for (auto it = cancelledPrompts_.begin(); it != cancelledPrompts_.end();)
        it = (nowMs - it->second > 300000) ? cancelledPrompts_.erase(it) : std::next(it);
}

}  // namespace nvwt
