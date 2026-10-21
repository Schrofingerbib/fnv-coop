#include "wt_session.h"

#include "fnvcoop_mut.h"

namespace nvwt {

PartyView WorldTimeSession::wrapParty(PartyView pv, WorldTimeSession* self) {
    auto base = pv.inCombat;
    pv.inCombat = [self, base]() { return (base ? base() : 0u) | self->reportedCombat(); };   // the host's own view plus what the clients reported
    return pv;
}

uint32_t WorldTimeSession::reportedCombat() const {
    uint32_t m = 0;
    for (const auto& kv : reported_)
        if (kv.second.inCombat && (now_ - kv.second.atMs <= cfg_.statusTtlMs || FNV_MUT(168))) m |= bit(kv.first);   // MUTANT 168: a combat report never expires (a dropped client blocks rest forever)
    return m;
}

WorldTimeSession::WorldTimeSession(Role role, PlayerId self, SessionConfig cfg, SessionEnv env)
    : role_(role),
      self_(self),
      cfg_(cfg),
      env_(std::move(env)),
      authority_(cfg.authority),
      follower_(cfg.follower),
      broker_(cfg.broker, wrapParty(env_.party, this), env_.ask, [this](const Resolved& r) { onResolved(r); }) {
    authority_.setPartyTravel(cfg_.broker.travel == TravelMode::Party);
}

void WorldTimeSession::setTravelMode(TravelMode m) {
    cfg_.broker.travel = m;
    broker_.setTravelMode(m);
    authority_.setPartyTravel(m == TravelMode::Party);
    authority_.forceBeacon();   // clients learn it at once
}

void WorldTimeSession::sendTo(PlayerId to, const Msg& m) {
    if (env_.send) env_.send(to, encode(m));
}

void WorldTimeSession::broadcastBeacon(const Beacon& b) {
    ++stats_.beaconsSent;
    sendTo(kNoPlayer, b);
}

void WorldTimeSession::sendVerdict(PlayerId to, uint32_t extId, ReqKind kind, bool granted, Reason why, uint32_t minutes, bool party) {
    if (FNV_MUT(157) && !granted) return;   // MUTANT 157: a refusal is never told to the player who asked (they wait for the timeout)
    Verdict v;
    v.reqId = extId;
    v.kind = kind;
    v.granted = granted;
    v.reason = why;
    v.minutes = minutes;
    v.party = party;
    ++stats_.verdictsSent;
    sendTo(to, v);
}

// ------------------------------------------------------------------------------------------------------------------------ frame
void WorldTimeSession::frame(uint64_t nowMs) {
    now_ = nowMs;
    if (!env_.engine) return;
    GameTime t;
    if (role_ == Role::Host) {
        broker_.tick(nowMs);
        if (env_.engine->readClock(&t)) {
            if (auto b = authority_.tick(nowMs, t, env_.engine->timeScale())) broadcastBeacon(*b);
        }
        return;
    }
    if (!follower_.haveBeacon() && (!helloSent_ || nowMs - lastHelloMs_ >= cfg_.helloRetryMs)) {
        helloSent_ = true;
        lastHelloMs_ = nowMs;
        ++stats_.helloSent;
        sendTo(kHostPlayer, Hello());
    }
    if (env_.engine->readClock(&t)) {
        if (auto o = follower_.tick(nowMs, t)) {
            if (env_.engine->writeClock(o->to)) {
                follower_.noteApplied(nowMs);
                ++(o->advance ? stats_.advances : stats_.snaps);
            }
        }
    }
    if (env_.localInCombat) {
        const bool c = env_.localInCombat();
        const bool refresh = !statusSent_ || (nowMs - lastStatusMs_ >= cfg_.statusEveryMs && !FNV_MUT(169));   // MUTANT 169: only changes are sent, so the host's copy expires during a long fight
        if (c != lastSentCombat_ || refresh) {
            statusSent_ = true;
            lastStatusMs_ = nowMs;
            lastSentCombat_ = c;
            ++stats_.statusSent;
            Status st;
            st.inCombat = c;
            sendTo(kHostPlayer, st);
        }
    }
    if (awaiting_.active && nowMs >= awaiting_.deadlineMs && !FNV_MUT(163)) {   // MUTANT 163: a lost answer leaves the player locked out forever
        awaiting_.active = false;
        ++stats_.requestsTimedOut;
        env_.engine->notify(reasonText(Reason::Timeout));
    }
}

// ------------------------------------------------------------------------------------------------------------------------ join / leave
void WorldTimeSession::onPlayerJoined(PlayerId slot, uint64_t) {
    if (role_ != Role::Host || slot >= 8 || slot == kHostPlayer) return;
    joinedClients_ |= bit(slot);
    lastReq_.erase(slot);   // a new process counts its request ids from 1 again
    if (!FNV_MUT(165)) authority_.forceBeacon();   // MUTANT 165: a joiner waits for the next periodic beacon
}

void WorldTimeSession::onPlayerLeft(PlayerId slot, uint64_t nowMs) {
    if (role_ != Role::Host || slot >= 8) return;
    joinedClients_ &= ~bit(slot);
    lastReq_.erase(slot);
    reported_.erase(slot);
    broker_.onPlayerLeft(slot, nowMs);
}

void WorldTimeSession::onHostLoaded() {
    if (role_ == Role::Host) authority_.noteLoaded();
}

bool WorldTimeSession::onPromptResult(uint32_t promptId, bool confirmed, bool timedOut, uint64_t nowMs) {
    now_ = nowMs;
    return role_ == Role::Host && broker_.onPromptResult(promptId, confirmed, timedOut, nowMs);
}

void WorldTimeSession::onAdvanceTimeEffect(uint32_t minutes, uint64_t nowMs) {
    if (role_ != Role::Client) return;
    follower_.onAdvanceTime(minutes, nowMs);
}

// ------------------------------------------------------------------------------------------------------------------------ host
void WorldTimeSession::executeSkip(uint32_t minutes) {
    if (!env_.engine) return;
    GameTime cur;
    if (!env_.engine->readClock(&cur)) return;
    const GameTime target = advanceBy(cur, static_cast<double>(minutes));
    if (!env_.engine->writeClock(target)) return;
    ++stats_.skipsExecuted;
    if (!FNV_MUT(155) && env_.record) env_.record(fnvcoop::AdvanceTime{minutes});   // MUTANT 155: the story log never learns about the skip
    if (FNV_MUT(156)) return;                                                          // MUTANT 156: no immediate beacon, clients wait for the periodic one
    broadcastBeacon(authority_.noteSkip(now_, target, env_.engine->timeScale()));
}

void WorldTimeSession::onResolved(const Resolved& r) {
    uint32_t ext = 0;
    auto it = extId_.find(r.reqId);
    if (it != extId_.end()) { ext = it->second; extId_.erase(it); }
    const bool remote = r.initiator != self_;
    if (r.kind != ReqKind::Travel) {
        if (r.granted) executeSkip(r.minutes);
        if (remote) sendVerdict(r.initiator, ext, r.kind, r.granted, r.reason, r.minutes, false);
        else if (env_.engine) env_.engine->notify(r.granted ? "Time passes." : reasonText(r.reason));
        return;
    }
    if (!r.granted) {
        if (remote) sendVerdict(r.initiator, ext, r.kind, false, r.reason, 0, false);
        else if (env_.engine) env_.engine->notify(reasonText(r.reason));
        return;
    }
    if (remote) sendVerdict(r.initiator, ext, r.kind, true, Reason::None, 0, r.party);
    if (r.party && !FNV_MUT(158)) {   // MUTANT 158: only the asker travels, the rest of the party stays behind
        TravelGo go;
        go.reqId = ext ? ext : r.reqId;
        go.marker = r.marker;
        go.mover = r.initiator;
        go.party = true;
        sendTo(kNoPlayer, go);
    }
    if (!FNV_MUT(160) && env_.engine && env_.engine->startTravel(r.marker)) ++stats_.travelsStarted;   // MUTANT 160: the host itself stays where it is
}

void WorldTimeSession::hostRequest(PlayerId from, ReqKind kind, uint32_t minutes, const FormRef& marker, uint32_t extId, uint64_t nowMs) {
    ++stats_.requestsHandled;
    const Outcome o = broker_.request(from, kind, minutes, marker, nowMs);
    if (o.kind == Outcome::Asked) {
        extId_[o.reqId] = extId;
        return;
    }
    if (o.kind == Outcome::Denied) {
        sendVerdict(from, extId, kind, false, o.reason, minutes, false);
        return;
    }
    // Granted at once (nobody else to ask / no consent needed)
    if (kind != ReqKind::Travel) executeSkip(minutes);
    sendVerdict(from, extId, kind, true, Reason::None, minutes, false);
}

void WorldTimeSession::hostPacket(PlayerId from, const Msg& m, uint64_t nowMs) {
    const bool fromClient = from < 8 && from != kHostPlayer && ((joinedClients_ & bit(from)) != 0);
    if (!fromClient && !FNV_MUT(162)) {   // MUTANT 162: requests from a slot that is not a joined client are served
        ++stats_.unexpected;
        return;
    }
    if (auto* h = std::get_if<Hello>(&m)) {
        (void)h;
        if (!FNV_MUT(161)) authority_.forceBeacon();   // MUTANT 161: a client that asks for the time is ignored
        return;
    }
    if (auto* st = std::get_if<Status>(&m)) {
        ++stats_.statusRecv;
        if (!FNV_MUT(167)) reported_[from] = Report{st->inCombat, nowMs};   // MUTANT 167: combat reports are ignored
        return;
    }
    if (auto* s = std::get_if<SkipRequest>(&m)) {
        auto it = lastReq_.find(from);
        if (it != lastReq_.end() && s->reqId <= it->second && !FNV_MUT(153)) {   // MUTANT 153: a duplicated request is asked about twice
            ++stats_.duplicateRequests;
            return;
        }
        lastReq_[from] = s->reqId;
        hostRequest(from, s->kind, s->minutes, FormRef(), s->reqId, nowMs);
        return;
    }
    if (auto* t = std::get_if<TravelRequest>(&m)) {
        auto it = lastReq_.find(from);
        if (it != lastReq_.end() && t->reqId <= it->second && !FNV_MUT(153)) {
            ++stats_.duplicateRequests;
            return;
        }
        lastReq_[from] = t->reqId;
        hostRequest(from, ReqKind::Travel, 0, t->marker, t->reqId, nowMs);
        return;
    }
    ++stats_.unexpected;   // a Beacon / Verdict / TravelGo from a client
}

// ------------------------------------------------------------------------------------------------------------------------ client
void WorldTimeSession::clientPacket(PlayerId from, const Msg& m, uint64_t nowMs) {
    if (from != kHostPlayer && !FNV_MUT(162)) {   // only the host speaks to a client
        ++stats_.unexpected;
        return;
    }
    if (auto* b = std::get_if<Beacon>(&m)) {
        ++stats_.beaconsRecv;
        partyTravel_ = b->partyTravel;
        follower_.onBeacon(*b, nowMs);
        return;
    }
    if (auto* v = std::get_if<Verdict>(&m)) {
        ++stats_.verdictsRecv;
        if (!awaiting_.active || (v->reqId != awaiting_.reqId && !FNV_MUT(154))) return;   // MUTANT 154: any verdict answers the open request
        const Awaiting a = awaiting_;
        awaiting_.active = false;
        if (!v->granted) {
            if (env_.engine) env_.engine->notify(reasonText(v->reason));
            return;
        }
        if (a.kind == ReqKind::Travel) {
            if (!v->party && env_.engine && env_.engine->startTravel(a.marker)) ++stats_.travelsStarted;
        } else if (env_.engine) {
            env_.engine->notify("Time passes.");
        }
        return;
    }
    if (auto* g = std::get_if<TravelGo>(&m)) {
        if (!g->party && g->mover != self_ && !FNV_MUT(164)) return;   // MUTANT 164: a single traveller drags every other client along
        if (env_.engine && env_.engine->startTravel(g->marker)) ++stats_.travelsStarted;
        if (awaiting_.active && awaiting_.kind == ReqKind::Travel) awaiting_.active = false;
        return;
    }
    ++stats_.unexpected;   // requests are for the host
}

void WorldTimeSession::onPacket(PlayerId from, const Bytes& data, uint64_t nowMs) {
    now_ = nowMs;
    Msg m;
    if (decode(data, &m) != DecodeError::Ok) {
        ++stats_.badPackets;
        return;
    }
    if (role_ == Role::Host) hostPacket(from, m, nowMs);
    else clientPacket(from, m, nowMs);
}

void WorldTimeSession::clientAsk(const Msg& m, uint32_t reqId, ReqKind kind, const FormRef& marker, uint64_t nowMs) {
    awaiting_.active = true;
    awaiting_.reqId = reqId;
    awaiting_.kind = kind;
    awaiting_.marker = marker;
    awaiting_.deadlineMs = nowMs + cfg_.requestTimeoutMs;
    sendTo(kHostPlayer, m);
}

// ------------------------------------------------------------------------------------------------------------------------ local player
bool WorldTimeSession::onLocalRest(ReqKind kind, uint32_t minutes, uint64_t nowMs) {
    now_ = nowMs;
    if (kind == ReqKind::Travel) return true;
    if (role_ == Role::Host) {
        const Outcome o = broker_.request(self_, kind, minutes, FormRef(), nowMs);
        if (o.kind == Outcome::Granted) return true;   // alone: the game's own wait / sleep runs, the beacon follows the jump
        if (o.kind == Outcome::Denied && env_.engine) env_.engine->notify(reasonText(o.reason));
        return false;
    }
    if (FNV_MUT(166)) return true;   // MUTANT 166: a client sleeps on its own and its clock runs away from the party
    if (awaiting_.active) return false;
    SkipRequest r;
    r.reqId = ++nextReq_;
    r.kind = kind;
    r.minutes = minutes;
    clientAsk(r, r.reqId, kind, FormRef(), nowMs);
    return false;
}

bool WorldTimeSession::onLocalTravel(const FormRef& marker, uint64_t nowMs) {
    now_ = nowMs;
    if (role_ == Role::Host) {
        const Outcome o = broker_.request(self_, ReqKind::Travel, 0, marker, nowMs);
        if (o.kind == Outcome::Granted) return true;
        if (o.kind == Outcome::Denied && env_.engine) env_.engine->notify(reasonText(o.reason));
        return false;
    }
    const bool tethered = env_.party.tethered && env_.party.tethered();
    if (!(partyTravel_ || tethered) || FNV_MUT(159)) return true;   // MUTANT 159: a party-travel client goes without asking
    if (awaiting_.active) return false;
    TravelRequest r;
    r.reqId = ++nextReq_;
    r.marker = marker;
    clientAsk(r, r.reqId, ReqKind::Travel, marker, nowMs);
    return false;
}

}  // namespace nvwt
