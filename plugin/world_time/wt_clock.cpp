#include "wt_clock.h"

#include <algorithm>
#include <cmath>

#include "fnvcoop_mut.h"

namespace nvwt {

// ------------------------------------------------------------------------------------------------------------------------ calendar
int daysInMonth(int month) {
    static const int kDays[12] = {31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31};
    if (month < 1 || month > 12) return 31;
    return kDays[month - 1];
}

bool validTime(const GameTime& t) {
    if (t.month < 1 || t.month > 12 || t.day < 1 || t.day > 31) return false;
    if (!std::isfinite(t.hour) || t.hour < 0.0 || t.hour >= 24.0) return false;
    if (!std::isfinite(t.daysPassed) || std::fabs(t.daysPassed) > 1.0e7) return false;
    if (t.year < 0 || t.year > 100000) return false;
    return true;
}

namespace {
void stepDay(GameTime& r, int dir) {
    if (dir > 0) {
        ++r.day;
        if (r.day > daysInMonth(r.month)) {
            r.day = 1;
            if (FNV_MUT(106)) return;   // MUTANT 106: the month never rolls over (day 1 of the SAME month again)
            ++r.month;
            if (r.month > 12) { r.month = 1; ++r.year; }
        }
    } else {
        --r.day;
        if (r.day < 1) {
            --r.month;
            if (r.month < 1) { r.month = 12; --r.year; }
            r.day = daysInMonth(r.month);
        }
    }
}
}  // namespace

GameTime advanceBy(const GameTime& t, double minutes) {
    GameTime r = t;
    if (!std::isfinite(minutes)) return r;
    double total = r.hour * 60.0 + minutes;
    double dd = std::floor(total / 1440.0);
    total -= dd * 1440.0;
    if (total >= 1440.0) { total -= 1440.0; dd += 1.0; }
    if (total < 0.0) total = 0.0;
    r.hour = total / 60.0;
    r.daysPassed += minutes / 1440.0;
    long long days = static_cast<long long>(dd);
    days = std::max(-4000LL, std::min(4000LL, days));
    for (; days > 0; --days) stepDay(r, +1);
    for (; days < 0; ++days) stepDay(r, -1);
    return r;
}

double deltaMinutes(const GameTime& a, const GameTime& b) {
    const double dhRaw = (a.hour - b.hour) * 60.0;
    double dh = dhRaw;
    if (!FNV_MUT(102)) dh -= 1440.0 * std::floor((dh + 720.0) / 1440.0);   // MUTANT 102: no wrap, a clock past midnight is a day off
    const double dd = (a.daysPassed - b.daysPassed) * 1440.0;
    // A: GameDaysPassed is continuous (the usual guess): the hour of day gives the fine part, the day count the whole days.
    const double viaWrap = dh + 1440.0 * std::round((dd - dh) / 1440.0);
    // B: GameDaysPassed counts whole days and changes at midnight: the raw hour difference plus the day difference.
    const double viaDays = dhRaw + dd;
    // The two readings differ by a whole number of days only when a midnight sits between the samples. Clocks that are being kept in sync
    // are close, so the smaller one is the truth in every case that matters; far apart clocks are snapped whichever way they are read.
    return std::fabs(viaDays) < std::fabs(viaWrap) && !FNV_MUT(102) ? viaDays : viaWrap;
}

// ------------------------------------------------------------------------------------------------------------------------ authority
Beacon ClockAuthority::make(uint64_t nowMs, const GameTime& cur, float timeScale, bool jump) {
    Beacon b;
    b.seq = ++seq_;
    b.time = cur;
    b.timeScale = timeScale;
    b.skipEpoch = epoch_;
    b.jump = jump;
    b.partyTravel = partyTravel_;
    float rate = timeScale / 60.0f;
    if (haveBeacon_ && !jump && !FNV_MUT(104) && nowMs > lastBeaconMs_) {   // MUTANT 104: always the nominal rate (a paused host clock looks like it runs)
        const double measured = deltaMinutes(cur, lastBeaconTime_) / (static_cast<double>(nowMs - lastBeaconMs_) / 1000.0);
        rate = static_cast<float>(std::max(0.0, std::min(1000.0, measured)));
    }
    b.rate = rate;
    haveBeacon_ = true;
    lastBeaconMs_ = nowMs;
    lastBeaconTime_ = cur;
    force_ = false;
    forceJump_ = false;
    return b;
}

std::optional<Beacon> ClockAuthority::tick(uint64_t nowMs, const GameTime& cur, float timeScale) {
    if (!validTime(cur)) return std::nullopt;
    if (!haveLast_) {
        haveLast_ = true;
        last_ = cur;
        lastMs_ = nowMs;
        return make(nowMs, cur, timeScale, true);   // the first beacon of a host process is a jump: followers must not trust their own clock
    }
    bool jump = forceJump_;
    const double dt = static_cast<double>(nowMs - lastMs_) / 1000.0;
    if (dt > 0.0) {
        const double got = deltaMinutes(cur, last_);
        const double expect = std::max(0.0, static_cast<double>(timeScale)) / 60.0 * dt;
        if (got < -cfg_.jumpMinutes) jump = true;                        // the clock went backwards: a load or a script
        if (!FNV_MUT(103) && got > expect + cfg_.jumpMinutes) jump = true;  // MUTANT 103: forward leaps (a script, a sleep) are not noticed
        const double leap = got - expect;
        if (cfg_.onceEnabled && !forceJump_ && !force_ && leap >= cfg_.onceMinMinutes && leap > cfg_.jumpMinutes) {
            if (haveEvent_ && nowMs - eventMs_ <= cfg_.onceWindowMs && std::fabs(leap - eventSize_) <= cfg_.onceToleranceMinutes && !FNV_MUT(170)) {
                // the same party event again (a second player used the door): take it back, nobody was told about it
                // (MUTANT 170: every player's use skips the time again)
                undoTo_ = advanceBy(cur, FNV_MUT(171) ? leap : -leap);   // MUTANT 171: the clock is moved the wrong way
                undoPending_ = true;
                ++repeatsUndone_;
                last_ = undoTo_;
                lastMs_ = nowMs;
                return std::nullopt;
            }
            haveEvent_ = true;
            eventMs_ = nowMs;
            eventSize_ = leap;
        }
    }
    last_ = cur;
    lastMs_ = nowMs;
    const bool due = force_ || jump || nowMs - lastBeaconMs_ >= (FNV_MUT(101) ? 0xFFFFFFFFull : cfg_.intervalMs);   // MUTANT 101: no periodic beacon
    if (!due) return std::nullopt;
    if (!force_ && !jump && nowMs - lastBeaconMs_ < cfg_.minGapMs) return std::nullopt;
    return make(nowMs, cur, timeScale, jump);
}

Beacon ClockAuthority::noteSkip(uint64_t nowMs, const GameTime& after, float timeScale) {
    ++epoch_;
    last_ = after;
    lastMs_ = nowMs;
    haveLast_ = true;
    return make(nowMs, after, timeScale, true);
}

// ------------------------------------------------------------------------------------------------------------------------ follower
void ClockFollower::onBeacon(const Beacon& b, uint64_t nowMs) {
    if (!validTime(b.time)) return;
    if (haveBeacon_ && !FNV_MUT(107)) {   // MUTANT 107: a reordered old beacon replaces a newer one
        const bool newer = b.seq > beacon_.seq;
        const bool restarted = b.jump && b.seq <= beacon_.seq;   // a host that restarted counts from 1 again and says so with a jump
        if (!newer && !restarted) return;
    }
    if (!haveBeacon_) { effectsSeen_ = b.skipEpoch; }
    beacon_ = b;
    beaconMs_ = nowMs;
    haveBeacon_ = true;
    epochSeen_ = b.skipEpoch;
    if (b.jump) pendingJump_ = true;
    if (holding_ && epochSeen_ >= awaitEpoch_) holding_ = false;
    if (epochSeen_ >= effectsSeen_) pendingAdvance_ = 0;   // the beacon already contains every skip this machine heard about
}

void ClockFollower::onAdvanceTime(uint32_t minutes, uint64_t nowMs) {
    ++effectsSeen_;
    if (!haveBeacon_) return;                                  // the first beacon sets the clock outright
    if (effectsSeen_ <= epochSeen_ && !FNV_MUT(105)) return;   // MUTANT 105: a skip that a beacon already carries is applied a second time
    pendingAdvance_ += minutes;
    holding_ = true;
    holdSinceMs_ = nowMs;
    awaitEpoch_ = effectsSeen_;
}

GameTime ClockFollower::expectedAt(uint64_t nowMs) const {
    if (!haveBeacon_) return GameTime();
    double sec = nowMs > beaconMs_ ? static_cast<double>(nowMs - beaconMs_) / 1000.0 : 0.0;
    sec = std::min(sec, cfg_.maxExtrapolateSec);
    const double rate = beacon_.rate > 0 ? beacon_.rate : 0.0;
    return advanceBy(beacon_.time, sec * rate);
}

std::optional<SnapOrder> ClockFollower::tick(uint64_t nowMs, const GameTime& local) {
    if (!haveBeacon_ || !validTime(local)) return std::nullopt;
    if (!FNV_MUT(108) && nowMs - beaconMs_ > cfg_.maxBeaconAgeMs) return std::nullopt;   // MUTANT 108: a dead host's last beacon keeps pulling the clock
    if (pendingAdvance_ > 0.0) {
        SnapOrder o;
        o.advance = true;
        o.hard = true;
        o.to = advanceBy(local, pendingAdvance_);
        pendingAdvance_ = 0.0;
        return o;
    }
    if (holding_) {
        if (nowMs - holdSinceMs_ < cfg_.skipHoldMs) return std::nullopt;
        holding_ = false;
    }
    const GameTime exp = expectedAt(nowMs);
    const double drift = deltaMinutes(local, exp);
    lastDrift_ = drift;
    SnapOrder o;
    o.to = exp;
    o.driftMinutes = drift;
    if (pendingJump_ && !FNV_MUT(109)) {   // MUTANT 109: a jump beacon is treated like drift (tolerance and rate limit apply)
        pendingJump_ = false;
        if (std::fabs(drift) > 0.5) {
            o.hard = true;
            ++snaps_;
            return o;
        }
        return std::nullopt;
    }
    pendingJump_ = false;
    if (std::fabs(drift) <= cfg_.toleranceMinutes) return std::nullopt;
    if (haveSnap_ && nowMs - lastSnapMs_ < cfg_.snapGapMs) return std::nullopt;
    ++snaps_;
    return o;
}

}  // namespace nvwt
