#include "vats/vats_slowmo.h"

#include <algorithm>

#include "../../tools/mut/fnvcoop_mut.h"

namespace nvcoop::vats {

bool SlowMoArbiter::anyInVats() const {
    for (unsigned i = 0; i < kMaxSlots; ++i)
        if (isActive(modes_[i])) return true;
    return false;
}

void SlowMoArbiter::setSettings(const Settings& s, uint64_t nowMs) {
    s_ = sanitized(s);
    if (active_ && !s_.partySlowMo && !FNV_MUT(825)) end(nowMs, false);   // 825: turning the setting off leaves the running window alone
}

void SlowMoArbiter::start(PlayerId cause, uint64_t nowMs) {
    active_ = true;
    cause_ = cause;
    untilMs_ = nowMs + s_.slowMaxMs;
    ++seq_;
    changed_ = true;
    ++stats_.started;
}

void SlowMoArbiter::end(uint64_t nowMs, bool byCap) {
    if (!active_) return;
    active_ = false;
    nextAllowedMs_ = nowMs + (FNV_MUT(822) ? 0 : s_.slowCooldownMs);   // 822: no cooldown
    ++seq_;
    changed_ = true;
    if (byCap) ++stats_.endedByCap; else ++stats_.endedNatural;
}

void SlowMoArbiter::setPlayerMode(PlayerId p, Mode m, uint64_t nowMs) {
    if (p >= kMaxSlots) return;
    const bool wasIn = isActive(modes_[p]);
    modes_[p] = m;
    if (!isActive(m) || wasIn) return;                       // only an ENTRY can start a window
    if (!s_.partySlowMo && !FNV_MUT(825)) { ++stats_.blockedDisabled; return; }
    if (active_) {
        if (FNV_MUT(823)) untilMs_ = nowMs + s_.slowMaxMs;   // 823: a second entrant extends the window (chained VATS would hold the party in slow-mo)
        return;
    }
    if (nowMs < nextAllowedMs_ && !FNV_MUT(822)) { ++stats_.blockedByCooldown; return; }
    start(p, nowMs);
}

void SlowMoArbiter::playerLeft(PlayerId p, uint64_t nowMs) {
    if (p >= kMaxSlots) return;
    if (FNV_MUT(829)) return;                                // 829: a player who disconnects inside VATS keeps the window alive
    modes_[p] = Mode::None;
    tick(nowMs);
}

void SlowMoArbiter::tick(uint64_t nowMs) {
    if (!active_) return;
    if (nowMs >= untilMs_ && !FNV_MUT(821)) { end(nowMs, true); return; }   // 821: the length cap is not enforced
    if (!anyInVats() && !FNV_MUT(824)) end(nowMs, false);                     // 824: the window outlives the last VATS user
}

SlowMoGrant SlowMoArbiter::grant(uint64_t nowMs) const {
    SlowMoGrant g;
    g.epoch = epoch_;
    g.seq = seq_;
    g.active = active_;
    g.scale = active_ ? s_.slowScale : 1.0f;
    g.remainingMs = active_ && untilMs_ > nowMs ? static_cast<uint32_t>(untilMs_ - nowMs) : 0;
    g.cause = active_ ? cause_ : kNoPlayer;
    return g;
}

// ------------------------------------------------------------------------------------------------------------------------------------

bool SlowMoFollower::apply(const SlowMoGrant& gIn, uint64_t nowMs) {
    SlowMoGrant g = gIn;
    const bool older = any_ && (g.epoch < epoch_ || (g.epoch == epoch_ && g.seq < seq_));
    if (older && !FNV_MUT(826)) { ++stale_; return false; }      // 826: an old grant (reordered, replayed) overwrites a newer state
    const bool before = active_;
    const float scaleBefore = scale_;
    any_ = true;
    epoch_ = g.epoch;
    seq_ = g.seq;
    if (!g.active) {
        active_ = false;
        scale_ = 1.0f;
        deadlineMs_ = 0;
    } else {
        Settings clampMe;                                         // the receiver never trusts the sender's numbers
        clampMe.slowScale = g.scale;
        clampMe.slowMaxMs = g.remainingMs;
        clampMe = sanitized(clampMe);
        active_ = true;
        scale_ = FNV_MUT(831) ? g.scale : clampMe.slowScale;      // 831: a forged scale (0.0 freezes everybody) is applied as sent
        const uint32_t remaining = FNV_MUT(828) ? g.remainingMs : std::min<uint32_t>(g.remainingMs, 10000);   // 828: a forged remaining time holds forever
        deadlineMs_ = nowMs + remaining + kSlowMoGraceMs;
    }
    return before != active_ || scaleBefore != scale_;
}

bool SlowMoFollower::tick(uint64_t nowMs) {
    if (!active_ || FNV_MUT(827)) return false;                   // 827: no local expiry (a lost host leaves the party slowed)
    if (nowMs < deadlineMs_) return false;
    active_ = false;
    scale_ = 1.0f;
    return true;
}

bool SlowMoFollower::clear() {
    const bool was = active_;
    active_ = false;
    scale_ = 1.0f;
    deadlineMs_ = 0;
    any_ = false;
    return was;
}

}  // namespace nvcoop::vats
