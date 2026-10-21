// Locomotion codes carried in synccore/live AnimState::loco (the live layer treats them as opaque; this file is the
// single definition for the sender capture and the puppet planner, so both ends agree).
//
// Direction convention: heading of yaw t is (sin t, cos t); a movement vector at angle phi (relative to the facing,
// positive = clockwise = to the player's right) is Forward within +-45 deg, Backward beyond 135 deg, otherwise Right /
// Left by sign. UNMEASURED against the real engine: a strafe test with T7 (proof_puppet.py) settles it.
#pragma once
#include <cmath>
#include <cstdint>

#include "avatar_types.h"

namespace nvcoop::avatar {

enum Loco : uint8_t {
    kLocoIdle = 0,
    kLocoWalkFwd = 1,
    kLocoWalkBack = 2,
    kLocoWalkLeft = 3,
    kLocoWalkRight = 4,
    kLocoRunFwd = 5,
    kLocoRunBack = 6,
    kLocoRunLeft = 7,
    kLocoRunRight = 8,
    kLocoMax = 8,
};

inline bool LocoIsRun(uint8_t l) { return l >= kLocoRunFwd && l <= kLocoRunRight; }
inline bool LocoIsWalk(uint8_t l) { return l >= kLocoWalkFwd && l <= kLocoWalkRight; }
inline bool LocoIsMoving(uint8_t l) { return l != kLocoIdle && l <= kLocoMax; }

constexpr float kPi = 3.14159265358979f;
constexpr float kTwoPi = 6.28318530717959f;

// Wraps to (-pi, pi].
inline float WrapRad(float a) {
    if (!std::isfinite(a)) return 0.0f;
    a = std::fmod(a, kTwoPi);
    if (a > kPi) a -= kTwoPi;
    if (a <= -kPi) a += kTwoPi;
    return a;
}

inline float HorizontalSpeed(const Vec3& v) { return std::sqrt(v.x * v.x + v.y * v.y); }

// Direction of horizontal velocity relative to the facing: 0 forward, 1 back, 2 left, 3 right. `prev` (-1 for none)
// gives hysteresis so a strafe-run near a 45 degree boundary does not flip every frame.
inline int DirectionOf(const Vec3& vel, float yaw, int prev = -1) {
    float phi = WrapRad(std::atan2(vel.x, vel.y) - yaw);  // 0 = straight ahead, + = to the right
    float a = std::fabs(phi);
    const float fwdEdge = kPi * 0.25f, backEdge = kPi * 0.75f, hyst = kPi * 0.0555f;  // 10 degrees
    auto raw = [&](float fe, float be) {
        if (a < fe) return 0;
        if (a > be) return 1;
        return phi < 0 ? 2 : 3;
    };
    if (prev < 0) return raw(fwdEdge, backEdge);
    // widen the previous class by the hysteresis
    switch (prev) {
        case 0: return raw(fwdEdge + hyst, backEdge);
        case 1: return raw(fwdEdge, backEdge - hyst);
        default: {
            int r = raw(fwdEdge - hyst, backEdge + hyst);
            return r;
        }
    }
}

inline uint8_t MakeLoco(bool run, int dir) {
    uint8_t base = run ? kLocoRunFwd : kLocoWalkFwd;
    return static_cast<uint8_t>(base + (dir == 0 ? 0 : dir == 1 ? 1 : dir == 2 ? 2 : 3));
}
// 0..3 (forward, back, left, right) of a moving code, -1 for idle / unknown.
inline int LocoDir(uint8_t l) {
    if (LocoIsRun(l)) return l - kLocoRunFwd;
    if (LocoIsWalk(l)) return l - kLocoWalkFwd;
    return -1;
}

// Thresholds shared by capture and planner (units/s). First guesses: T7 config.py uses walk 8..280, run 120..900.
struct SpeedBands {
    float standMax = 20.0f;    // below: standing
    float runEnter = 230.0f;   // above: running
    float runExit = 190.0f;    // below: back to walking (hysteresis)
};

// Sender side: what the local player's engine state says, reduced to a locomotion code. `run` comes from the mover flags
// (T7: ActorMover bit 8 = run, bit 7 = walk, bit 9 = sneak, bit 11 = swim); when the flags are unknown the speed decides.
struct LocalRead {
    Vec3 vel;
    float yaw = 0;
    bool haveMoveFlags = false;
    bool moverRun = false;      // bit 8
    bool moverWalk = false;     // bit 7
    bool moverSneak = false;    // bit 9
};

inline uint8_t CaptureLoco(const LocalRead& r, const SpeedBands& b = {}) {
    float sp = HorizontalSpeed(r.vel);
    if (sp < b.standMax) return kLocoIdle;
    bool run = r.haveMoveFlags ? (r.moverRun && !r.moverWalk) : sp > b.runEnter;
    return MakeLoco(run, DirectionOf(r.vel, r.yaw));
}

}  // namespace nvcoop::avatar
