// Helpers shared by the NPC animation tests: building InterpOut values, a walker that produces displayed motion, a bench around one driver,
// a recording network, and a full owner -> live codec -> interpolation -> driver pipeline with a slide meter.
#pragma once
#include <cmath>
#include <deque>
#include <memory>
#include <string>
#include <vector>

#include "fake_npc_engine.h"
#include "fnvcoop/rng.h"
#include "minitest.h"
#include "npc_anim_driver.h"
#include "npc_anim_sampler.h"
#include "npc_anim_system.h"

namespace anim_test {

using nvcoop::actors::anim::CorpsePose;
namespace live = fnvcoop::live;

constexpr float kPi = 3.14159265f;
constexpr NetId kId = 1;

inline Vec3 V(float x = 0, float y = 0, float z = 0) { Vec3 v; v.x = x; v.y = y; v.z = z; return v; }

inline live::InterpOut mkOut(Vec3 pos, float yaw, Vec3 vel, live::AnimState a = {}, bool snapped = false,
                             live::InterpState st = live::InterpState::Interpolating, CellId cell = 1) {
    live::InterpOut o;
    o.state = st; o.cell = cell; o.pos = pos; o.rot = V(0, yaw, 0); o.vel = vel; o.anim = a; o.snapped = snapped;
    return o;
}

// Produces the displayed motion of a body walking along its heading at a chosen speed.
struct Walker {
    Vec3 pos = V(1000, 1000, 0);
    float yaw = 0;
    live::AnimState anim;
    CellId cell = 1;
    live::InterpState state = live::InterpState::Interpolating;
    live::InterpOut step(uint32_t dtMs, float speed, uint8_t code) {
        const float sec = static_cast<float>(dtMs) / 1000.0f;
        pos.x += std::sin(yaw) * speed * sec;
        pos.y += std::cos(yaw) * speed * sec;
        anim.locomotion = code;
        return mkOut(pos, yaw, V(std::sin(yaw) * speed, std::cos(yaw) * speed, 0), anim, false, state, cell);
    }
};

// One driver on a fake engine; frame() advances the clock by one drive period.
struct Bench {
    FakeNpcEngine eng;
    NpcAnimDriver drv;
    uint32_t now = 0;
    explicit Bench(const DriverConfig& c = {}) : drv(eng, c) { eng.add(kId); now = eng.now; }
    void frame(const live::InterpOut& o, uint32_t dt = 33, NetId id = kId) {
        eng.advance(dt); now = eng.now;
        drv.drive(id, o, now);
        drv.tick(now);
    }
    // Run `ms` of walking at `speed` with the owner code `code`.
    void walk(Walker& w, uint32_t ms, float speed, uint8_t code, uint32_t dt = 33) {
        for (uint32_t t = 0; t < ms; t += dt) frame(w.step(dt, speed, code), dt);
    }
    FakeNpc& npc(NetId id = kId) { return eng.a[id]; }
};

// IAnimNet that records, and can refuse the first n attempts.
struct RecordingNet : IAnimNet {
    std::vector<std::pair<NetId, std::vector<uint8_t>>> sent;
    int refuse = 0;
    uint32_t attempts = 0;
    bool sendEvent(NetId id, const std::vector<uint8_t>& payload) override {
        ++attempts;
        if (refuse > 0) { --refuse; return false; }
        sent.push_back({id, payload});
        return true;
    }
};

}  // namespace anim_test
