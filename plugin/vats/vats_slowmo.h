// "Party slow-mo": the optional host setting. When any player enters VATS, ALL players slow together for a short, capped window. Host-authoritative:
// the host's SlowMoArbiter decides (one window at a time, a hard length cap, a cooldown afterwards), every machine's SlowMoFollower mirrors the grant
// the host broadcasts and expires it by itself if the host goes silent. Game-free; the engine side is IVatsEngine::setTimeScale (vats_sync.cpp).
#pragma once
#include <cstdint>
#include "vats/vats_types.h"

namespace nvcoop::vats {

// The grant as it travels (MSlowMo).
struct SlowMoGrant {
    uint32_t epoch = 0;          // bumped by the host for every session it starts (a re-hosted game never competes with old grants)
    uint32_t seq = 0;            // rises with every start / end; the periodic refresh repeats the current one
    bool active = false;
    float scale = 1.0f;          // world time scale while active
    uint32_t remainingMs = 0;    // how long the window still runs, from the moment the host sent it
    PlayerId cause = kNoPlayer;  // the player whose VATS entry started it
    friend bool operator==(const SlowMoGrant& a, const SlowMoGrant& b) {
        return a.epoch == b.epoch && a.seq == b.seq && a.active == b.active && a.scale == b.scale && a.remainingMs == b.remainingMs && a.cause == b.cause;
    }
};

struct SlowMoStats { uint64_t started = 0, endedNatural = 0, endedByCap = 0, blockedByCooldown = 0, blockedDisabled = 0; };

class SlowMoArbiter {
public:
    explicit SlowMoArbiter(const Settings& s = Settings{}, uint32_t epoch = 1) : s_(sanitized(s)), epoch_(epoch) {}
    void setSettings(const Settings& s, uint64_t nowMs);
    // A player's VATS mode (0 = not in VATS). Call on every change; calling again with the same value is harmless.
    void setPlayerMode(PlayerId p, Mode m, uint64_t nowMs);
    void playerLeft(PlayerId p, uint64_t nowMs);
    void tick(uint64_t nowMs);                       // ends the window (cap, or nobody left in VATS)
    bool active() const { return active_; }
    SlowMoGrant grant(uint64_t nowMs) const;         // the grant to broadcast now
    // True once after every start / end: the host broadcasts then (plus a periodic refresh while active).
    bool takeChanged() { const bool c = changed_; changed_ = false; return c; }
    bool anyInVats() const;
    const SlowMoStats& stats() const { return stats_; }

private:
    void start(PlayerId cause, uint64_t nowMs);
    void end(uint64_t nowMs, bool byCap);
    Settings s_;
    uint32_t epoch_;
    Mode modes_[kMaxSlots] = {};
    bool active_ = false, changed_ = false;
    uint64_t untilMs_ = 0, nextAllowedMs_ = 0;
    uint32_t seq_ = 0;
    PlayerId cause_ = kNoPlayer;
    SlowMoStats stats_;
};

// Every machine (the host too): what the host granted, with a local safety expiry.
class SlowMoFollower {
public:
    // Returns true when the visible state (active / scale) changed.
    bool apply(const SlowMoGrant& g, uint64_t nowMs);
    bool tick(uint64_t nowMs);                       // expires a window whose deadline passed without a refresh / end from the host
    bool clear();                                    // host lost / session ended
    bool active() const { return active_; }
    float scale() const { return scale_; }
    uint32_t seq() const { return seq_; }
    uint64_t stale() const { return stale_; }

private:
    bool active_ = false;
    float scale_ = 1.0f;
    uint64_t deadlineMs_ = 0;
    uint32_t epoch_ = 0, seq_ = 0;
    bool any_ = false;
    uint64_t stale_ = 0;
};

constexpr uint32_t kSlowMoGraceMs = 750;            // a follower keeps a window this long past its announced end before it gives up on the host
constexpr uint32_t kSlowMoRefreshMs = 1000;         // the host repeats the current grant this often while a window runs

}  // namespace nvcoop::vats
