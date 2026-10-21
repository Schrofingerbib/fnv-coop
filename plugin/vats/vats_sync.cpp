#include "vats/vats_sync.h"

#include <algorithm>
#include <cmath>
#include <cstdio>

#include "vats/vats_api.h"
#include "../../tools/mut/fnvcoop_mut.h"

namespace nvcoop::vats {

VatsSync::VatsSync(const Config& cfg, IVatsEngine& engine, IVatsNet& net)
    : cfg_(cfg), engine_(engine), net_(net), settings_(sanitized(cfg.settings)), arbiter_(effectiveSlowSettings(), cfg.epoch), ledger_(cfg.perks) {}

VatsSync::~VatsSync() = default;

Settings VatsSync::effectiveSlowSettings() const {
    Settings s = settings_;
    s.partySlowMo = s.partySlowMo && s.realtime;
    return s;
}

// ---------------------------------------------------------------------------------------------------------------------------- sending
void VatsSync::send(PlayerId dest, const Envelope& e) { net_.send(dest, encodeEnvelope(e)); }

void VatsSync::broadcastHostOnly(const Body& b) {
    if (!cfg_.isHost) return;
    for (PlayerId p : joined_) {
        Envelope e;
        e.src = cfg_.self; e.dest = p; e.hops = 0; e.body = b;
        send(p, e);
    }
}

void VatsSync::sendConfig(PlayerId dest) {
    if (!cfg_.isHost) return;
    MConfig m;
    m.epoch = cfg_.epoch;
    m.s = settings_;
    Envelope e;
    e.src = cfg_.self; e.dest = dest; e.body = m;
    send(dest, e);
    ++stats_.configsSent;
}

void VatsSync::sendSlowMo(PlayerId dest, uint64_t nowMs) {
    if (!cfg_.isHost) return;
    MSlowMo m;
    m.g = arbiter_.grant(nowMs);
    Envelope e;
    e.src = cfg_.self; e.dest = dest; e.body = m;
    send(dest, e);
    ++stats_.slowMoSent;
}

void VatsSync::announcePhase(Mode m, uint64_t nowMs) {
    lastPhaseMs_ = nowMs;
    MPhase p;
    p.player = cfg_.self;
    p.mode = m;
    p.queued = static_cast<uint8_t>(std::min(255, std::max(0, lastQueued_)));
    p.seq = ++phaseSeq_;
    if (isSelecting(m)) {
        const VatsView v = readView(engine_);
        p.target = engine_.netIdOfRef(v.targetRef);
        p.limb = v.highlighted;
    }
    if (cfg_.self < kMaxSlots) remoteModes_[cfg_.self] = m;
    ++stats_.phasesSent;
    if (cfg_.isHost) {
        arbiter_.setPlayerMode(cfg_.self, m, nowMs);
        for (PlayerId q : joined_) {
            Envelope e;
            e.src = cfg_.self; e.dest = q; e.body = p;
            send(q, e);
        }
    } else {
        Envelope e;
        e.src = cfg_.self; e.dest = kNoPlayer; e.body = p;
        send(kHostPlayer, e);
    }
}

// ---------------------------------------------------------------------------------------------------------------------------- session
void VatsSync::playerJoined(PlayerId slot, uint64_t nowMs) {
    if (!cfg_.isHost || slot >= kMaxSlots || slot == cfg_.self) return;
    joined_.insert(slot);
    sendConfig(slot);
    if (follower_.active() || arbiter_.active()) sendSlowMo(slot, nowMs);
}

void VatsSync::playerLeft(PlayerId slot, uint64_t nowMs) {
    if (!cfg_.isHost || slot >= kMaxSlots) return;
    joined_.erase(slot);
    remoteModes_[slot] = Mode::None;
    arbiter_.playerLeft(slot, nowMs);
}

void VatsSync::hostLost() {
    if (cfg_.isHost) return;
    follower_.clear();
    if (!FNV_MUT(895)) settings_ = sanitized(cfg_.settings);   // 895: the dead host's settings stay in force
    for (unsigned i = 0; i < kMaxSlots; ++i) remoteModes_[i] = Mode::None;
    applySlowMoToEngine(0);
}

bool VatsSync::setSettings(const Settings& in, uint64_t nowMs) {
    if (!cfg_.isHost && !FNV_MUT(905)) return false;                 // 905: a client changes its own settings (the host's would win again at the next MConfig)
    const Settings next = sanitized(in);
    if (next == settings_) return false;
    settings_ = next;
    arbiter_.setSettings(effectiveSlowSettings(), nowMs);
    if (cfg_.isHost && !FNV_MUT(906)) {                              // 906: the new settings stay on the host until the next periodic repeat
        lastConfigMs_ = nowMs;
        for (PlayerId q : joined_) sendConfig(q);
    }
    return true;
}

void VatsSync::shutdown() {
    follower_.clear();
    ledger_.clear();
    if (appliedScale_ != 1.0f && !FNV_MUT(891)) {   // 891: the party slow-mo scale outlives the session
        if (engine_.setTimeScale(1.0f, true)) appliedScale_ = 1.0f;
    }
    if (cameraShortened_) { engine_.applyCameraTimes(false); cameraShortened_ = false; ++stats_.cameraChanges; }
}

// ---------------------------------------------------------------------------------------------------------------------------- receiving
void VatsSync::handleConfig(const MConfig& m) {
    if (cfg_.isHost && !FNV_MUT(894)) { ++stats_.rejected; return; }   // 894: the host takes a client's settings (host authority broken)
    if (FNV_MUT(893)) return;                                          // 893: a client ignores the host's settings
    settings_ = sanitized(m.s);
    ++stats_.configsApplied;
    arbiter_.setSettings(effectiveSlowSettings(), 0);
}

void VatsSync::receive(PlayerId from, const uint8_t* data, size_t size, uint64_t nowMs) {
    Envelope e;
    if (!decodeEnvelope(data, size, e)) { ++stats_.undecodable; return; }
    if (cfg_.isHost) {
        if (from >= kMaxSlots) { ++stats_.rejected; return; }
        if (e.src != from) {
            ++stats_.spoofed;
            if (!FNV_MUT(896)) e.src = from;   // 896: the claimed author is believed
        }
    } else if (from != kHostPlayer) {
        ++stats_.rejected;                     // a client talks to the host only
        return;
    }
    const bool relay = cfg_.isHost && e.hops < kMaxHops;
    if (cfg_.isHost && e.hops >= kMaxHops) ++stats_.hopLimit;
    if (auto* m = std::get_if<MConfig>(&e.body)) {
        handleConfig(*m);
    } else if (auto* p = std::get_if<MPhase>(&e.body)) {
        if (cfg_.isHost) {
            if (p->player != e.src && !FNV_MUT(897)) { ++stats_.spoofed; return; }   // 897: a client starts a slow-mo window "for" somebody else
            if (p->player >= kMaxSlots) { ++stats_.rejected; return; }
            arbiter_.setPlayerMode(p->player, p->mode, nowMs);
        } else if (p->player >= kMaxSlots || p->player == cfg_.self) {
            return;
        }
        remoteModes_[p->player] = p->mode;
        ++stats_.phasesReceived;
        if (relay) {
            for (PlayerId q : joined_) {
                if (q == e.src) continue;
                Envelope out = e;
                out.dest = q; out.hops = static_cast<uint8_t>(e.hops + 1);
                send(q, out);
                ++stats_.relayed;
            }
        }
    } else if (auto* a = std::get_if<MAction>(&e.body)) {
        if (cfg_.isHost && a->shooter != e.src) { ++stats_.spoofed; return; }
        ++stats_.actionsReceived;
        if (relay) {
            for (PlayerId q : joined_) {
                if (q == e.src) continue;
                Envelope out = e;
                out.dest = q; out.hops = static_cast<uint8_t>(e.hops + 1);
                send(q, out);
                ++stats_.relayed;
            }
        }
    } else if (auto* s = std::get_if<MSlowMo>(&e.body)) {
        if (cfg_.isHost) { ++stats_.rejected; return; }                          // only the host grants
        if (follower_.apply(s->g, nowMs)) ++stats_.slowMoApplied;
    }
}

// ---------------------------------------------------------------------------------------------------------------------------- frame
PolicyInput VatsSync::policyInput() const {
    PolicyInput in;
    in.settings = settings_;
    in.sessionActive = true;
    in.mode = engine_.mode();
    in.vatsMenuOpen = engine_.vatsMenuOpen();
    in.partySlowActive = follower_.active();
    in.partyScale = follower_.scale();
    return in;
}

void VatsSync::applySlowMoToEngine(uint64_t nowMs) {
    PolicyInput in;
    in.settings = settings_;
    in.sessionActive = true;
    in.partySlowActive = follower_.active();
    in.partyScale = follower_.scale();
    float desired = realtimeActive(in) ? desiredWorldScale(in) : 1.0f;
    if (FNV_MUT(892)) desired = follower_.active() ? follower_.scale() : desired;   // 892: applied even when the local settings say real time is off
    if (desired != appliedScale_) {
        if (engine_.setTimeScale(desired, false)) { appliedScale_ = desired; ++stats_.scaleSets; }
        else ++stats_.scaleFailed;
        return;
    }
    if (appliedScale_ != 1.0f && nowMs >= lastReassertMs_ + 250 && !FNV_MUT(898)) {   // 898: nothing holds the scale if other code changes it
        lastReassertMs_ = nowMs;
        if (std::fabs(engine_.timeScaleTarget() - desired) > 0.01f && engine_.setTimeScale(desired, false)) ++stats_.scaleReasserts;
    }
}

void VatsSync::onQueuedChanged(int queued, uint64_t nowMs) {
    if (queued > lastQueued_) {
        const VatsView v = readView(engine_);
        ledger_.onActionQueued(v.targetRef, v.highlighted, nowMs);
        ++stats_.actionsQueued;
        queuedAtMs_ = nowMs;
        acceptSent_ = false;
        partyTargetQueued_ = v.targetRef && engine_.isPartyRef(v.targetRef);
        MAction a;
        a.shooter = cfg_.self;
        a.victim = engine_.netIdOfRef(v.targetRef);
        a.limb = v.highlighted;
        a.queued = static_cast<uint8_t>(std::min(255, queued));
        a.seq = ++actionSeq_;
        if (cfg_.isHost) {
            for (PlayerId q : joined_) { Envelope e; e.src = cfg_.self; e.dest = q; e.body = a; send(q, e); }
        } else {
            Envelope e; e.src = cfg_.self; e.dest = kNoPlayer; e.body = a;
            send(kHostPlayer, e);
        }
    } else if (queued == 0) {
        acceptSent_ = false;
        partyTargetQueued_ = false;
    }
    lastQueued_ = queued;
}

void VatsSync::autoFire(uint64_t nowMs) {
    if (!settings_.autoFire && !FNV_MUT(883)) return;                       // 883: auto fire ignores its switch
    if (!realtimeActive(policyInput()) && !FNV_MUT(884)) return;            // 884: auto fire also in vanilla (not real-time) VATS
    if (lastQueued_ <= 0 || partyTargetQueued_) return;
    if (acceptSent_ && !FNV_MUT(882)) return;                               // 882: accept is sent every frame
    if (!FNV_MUT(881) && nowMs < queuedAtMs_ + settings_.autoFireDelayMs) return;   // 881: no pause for a second queued action
    if (engine_.accept()) {
        acceptSent_ = true;
        ++stats_.autoFires;
    } else {
        ++stats_.autoFireRefused;
        queuedAtMs_ = nowMs;                                                // try again after another delay
    }
}

void VatsSync::guard(uint64_t nowMs) {
    if (!settings_.guardFriendly && !FNV_MUT(885)) return;                  // 885: the guard ignores its switch
    if (!realtimeActive(policyInput())) return;
    const uint32_t sel = engine_.selectedTargetRef();
    if (partyTargetQueued_ && lastQueued_ > 0 && !FNV_MUT(887)) {           // 887: an action already queued at a party member stays queued
        if (engine_.unclickLast()) { ++stats_.guardCancelled; partyTargetQueued_ = false; lastQueued_ = std::max(0, lastQueued_ - 1); }
    }
    if (!sel || !engine_.isPartyRef(sel)) return;
    if (nowMs < lastGuardMs_ + cfg_.guardCooldownMs && !FNV_MUT(886)) return;   // 886: the guard cycles every frame
    lastGuardMs_ = nowMs;
    if (engine_.cycleTarget(true) && engine_.selectedTargetRef() != sel) ++stats_.guardSkips;
    else ++stats_.guardBlocked;                                             // the party member is the only target: nothing to skip to
}

void VatsSync::tick(uint64_t nowMs) {
    if (!started_) {
        started_ = true;
        lastConfigMs_ = lastPhaseMs_ = lastSlowMoMs_ = nowMs;
    }
    const Mode mode = engine_.mode();
    if (mode != lastMode_) {
        ledger_.onModeChanged(lastMode_, mode, nowMs);
        lastMode_ = mode;
        if (!isActive(mode)) { lastQueued_ = 0; acceptSent_ = false; partyTargetQueued_ = false; }
        announcePhase(mode, nowMs);
    } else if (isActive(mode) && nowMs >= lastPhaseMs_ + cfg_.phaseHeartbeatMs) {
        announcePhase(mode, nowMs);
    }
    if (isSelecting(mode)) {
        const int queued = engine_.queuedCount();
        if (queued != lastQueued_) onQueuedChanged(queued, nowMs);
        guard(nowMs);
        autoFire(nowMs);
    } else if (isPlayback(mode)) {
        lastQueued_ = engine_.queuedCount();
        if (FNV_MUT(899) && lastQueued_ > 0) autoFire(nowMs);                // 899: auto fire also during the playback (double accept)
    }
    if (cfg_.isHost) {
        arbiter_.tick(nowMs);
        const bool changed = arbiter_.takeChanged();
        if (changed || (arbiter_.active() && nowMs >= lastSlowMoMs_ + kSlowMoRefreshMs)) {
            lastSlowMoMs_ = nowMs;
            const SlowMoGrant g = arbiter_.grant(nowMs);
            follower_.apply(g, nowMs);
            MSlowMo m;
            m.g = g;
            broadcastHostOnly(m);
            stats_.slowMoSent += joined_.size();
        }
        if (nowMs >= lastConfigMs_ + cfg_.configRepeatMs) {
            lastConfigMs_ = nowMs;
            for (PlayerId q : joined_) sendConfig(q);
        }
    }
    follower_.tick(nowMs);
    applySlowMoToEngine(nowMs);
    const bool wantShort = !settings_.killCam && realtimeActive(policyInput());
    if (wantShort != cameraShortened_) {
        if (engine_.applyCameraTimes(wantShort)) { cameraShortened_ = wantShort; ++stats_.cameraChanges; }
    }
}

// ---------------------------------------------------------------------------------------------------------------------------- events
void VatsSync::onActorDied(uint32_t ref, bool decidedHere, bool fromNetwork, uint64_t nowMs) {
    if (!settings_.perks || !realtimeActive(policyInput())) return;
    VatsKill k;
    const uint64_t nativeBefore = ledger_.nativeKills();
    if (!ledger_.onActorDied(ref, decidedHere, fromNetwork, nowMs, &k)) {
        if (ledger_.nativeKills() > nativeBefore) ++stats_.vatsKillsNative;
        return;
    }
    ++stats_.vatsKillsRemote;
    const bool has = engine_.playerHasPerk(cfg_.perks.grsEditorId);
    if (!has) ++stats_.grsNoPerk;
    const int refund = grimReaperRefund(cfg_.perks, has, engine_.actionPoints(), engine_.maxActionPoints());
    if (refund > 0 && engine_.restoreActionPoints(refund)) {
        ++stats_.grsRefunds;
        stats_.grsApGiven += static_cast<uint64_t>(refund);
    }
}

uint32_t VatsSync::attackerFor(uint32_t attackerRef, uint64_t nowMs) const {
    if (!settings_.perks || !realtimeActive(policyInput())) return attackerRef;
    AttackerFacts f;
    f.attackerRef = attackerRef;
    f.localPlayerRef = engine_.localPlayerRef();
    f.attackerTracked = attackerRef && engine_.netIdOfRef(attackerRef) != 0;
    f.attackerIsParty = attackerRef && engine_.isPartyRef(attackerRef);
    f.localPlayback = lastMode_ == Mode::Playback || ledger_.playbackRecently(nowMs);
    return nvcoop::vats::attackerFor(f);
}

std::string VatsSync::statusJson(uint64_t nowMs) const {
    char b[2048];
    const SlowMoStats& sm = arbiter_.stats();
    auto u = [](uint64_t v) { return static_cast<unsigned long long>(v); };
    std::snprintf(b, sizeof b,
        "{\"role\":\"%s\",\"self\":%u,\"mode\":\"%s\",\"settings\":{\"realtime\":%s,\"partySlowMo\":%s,\"slowScale\":%.3f,\"slowMaxMs\":%u,\"slowCooldownMs\":%u,"
        "\"autoFire\":%s,\"autoFireDelayMs\":%u,\"killCam\":%s,\"guardFriendly\":%s,\"perks\":%s},"
        "\"partySlow\":{\"active\":%s,\"scale\":%.3f,\"appliedScale\":%.3f,\"started\":%llu,\"endedByCap\":%llu,\"endedNatural\":%llu,\"blockedByCooldown\":%llu},"
        "\"ledger\":{\"pending\":%llu,\"kills\":%llu,\"nativeKills\":%llu,\"playbackRecently\":%s},"
        "\"stats\":{\"phasesSent\":%llu,\"phasesReceived\":%llu,\"actionsQueued\":%llu,\"actionsReceived\":%llu,\"configsSent\":%llu,\"configsApplied\":%llu,"
        "\"slowMoSent\":%llu,\"slowMoApplied\":%llu,\"autoFires\":%llu,\"autoFireRefused\":%llu,\"guardSkips\":%llu,\"guardBlocked\":%llu,\"guardCancelled\":%llu,"
        "\"scaleSets\":%llu,\"scaleReasserts\":%llu,\"scaleFailed\":%llu,\"cameraChanges\":%llu,\"grsRefunds\":%llu,\"grsApGiven\":%llu,\"grsNoPerk\":%llu,"
        "\"vatsKillsRemote\":%llu,\"vatsKillsNative\":%llu,\"undecodable\":%llu,\"spoofed\":%llu,\"rejected\":%llu,\"relayed\":%llu}}",
        cfg_.isHost ? "host" : "client", static_cast<unsigned>(cfg_.self), toString(lastMode_),
        settings_.realtime ? "true" : "false", settings_.partySlowMo ? "true" : "false", settings_.slowScale, settings_.slowMaxMs, settings_.slowCooldownMs,
        settings_.autoFire ? "true" : "false", settings_.autoFireDelayMs, settings_.killCam ? "true" : "false", settings_.guardFriendly ? "true" : "false",
        settings_.perks ? "true" : "false",
        follower_.active() ? "true" : "false", follower_.scale(), appliedScale_, u(sm.started), u(sm.endedByCap), u(sm.endedNatural), u(sm.blockedByCooldown),
        u(ledger_.pending()), u(ledger_.kills()), u(ledger_.nativeKills()), ledger_.playbackRecently(nowMs) ? "true" : "false",
        u(stats_.phasesSent), u(stats_.phasesReceived), u(stats_.actionsQueued), u(stats_.actionsReceived), u(stats_.configsSent), u(stats_.configsApplied),
        u(stats_.slowMoSent), u(stats_.slowMoApplied), u(stats_.autoFires), u(stats_.autoFireRefused), u(stats_.guardSkips), u(stats_.guardBlocked),
        u(stats_.guardCancelled), u(stats_.scaleSets), u(stats_.scaleReasserts), u(stats_.scaleFailed), u(stats_.cameraChanges), u(stats_.grsRefunds),
        u(stats_.grsApGiven), u(stats_.grsNoPerk), u(stats_.vatsKillsRemote), u(stats_.vatsKillsNative), u(stats_.undecodable), u(stats_.spoofed),
        u(stats_.rejected), u(stats_.relayed));
    return b;
}

}  // namespace nvcoop::vats
