// Shared fixtures for the actor-port tests: an in-memory ledger file, a port over the fake engine, event collectors.
#pragma once
#include <memory>
#include <string>
#include <vector>

#include "actors/engine/actor_feed.h"
#include "actors/engine/engine_port.h"
#include "actors/engine/ledger.h"
#include "actors/engine/registry.h"
#include "fake_engine.h"

namespace nvae_test {

struct MemStore : IFileStore {
    std::string text;
    bool has = false, failWrite = false;
    int writes = 0;
    bool read(std::string& out) override { if (!has) return false; out = text; return true; }
    bool write(const std::string& t) override { if (failWrite) return false; text = t; has = true; ++writes; return true; }
};

inline Config rigConfig() {
    Config c;
    c.maxLinesPerFrame = 24;
    c.driveMinMs = 33;
    c.pollDeathMs = 100;
    c.deathGraceMs = 1500;
    c.scanMs = 500;
    c.saveGraceMs = 3000;
    return c;
}

inline live::InterpOut interpAt(live::Vec3 pos, float yaw = 0, CellId cell = 1, bool snapped = false) {
    live::InterpOut o;
    o.state = snapped ? live::InterpState::Snapped : live::InterpState::Interpolating;
    o.cell = cell; o.pos = pos; o.rot.y = yaw; o.snapped = snapped;
    return o;
}

inline NetId netIdFor(FakeEngine& e, RefId ref) { return deriveNetId(e.refForm(ref)); }

// One machine's port over a fake engine, with every event the port emits collected.
struct PortRig {
    FakeEngine eng;
    MemStore store;
    Ledger led;
    EnginePort port;
    uint64_t t = 10000;
    std::vector<TrackedEvent> tracked;
    std::vector<DiedEvent> died;
    std::vector<LostEvent> lost;
    std::vector<std::pair<NetId, live::InterpOut>> driven;

    explicit PortRig(Config c = rigConfig()) : led(&store), port(eng, led, c) {
        registry().reset();
        registry().cfg = c;
        registry().addTracked([this](const TrackedEvent& e) { tracked.push_back(e); });
        registry().addDied([this](const DiedEvent& e) { died.push_back(e); });
        registry().addLost([this](const LostEvent& e) { lost.push_back(e); });
        registry().addDrive([this](const DriveEvent& e) { driven.push_back({e.netId, *e.out}); });
        port.beginFrame(t);
    }
    ~PortRig() { registry().reset(); }

    void frame(uint32_t dt = 40) {
        t += dt;
        port.beginFrame(t);
    }
    // frame + flush
    void step(uint32_t dt = 40) { frame(dt); port.flush(); }

    ActorMeta metaFor(RefId ref, uint32_t baseLocal = 0x500, bool named = false) {
        ActorMeta m;
        m.ref = eng.refForm(ref);
        m.netId = deriveNetId(m.ref);
        m.base = fnvcoop::FormRef("falloutnv.esm", baseLocal);
        m.named = named;
        auto it = eng.a.find(ref);
        if (it != eng.a.end()) { m.cell = it->second.cell; m.pos = it->second.pos; }
        return m;
    }
    // A placed NPC that exists in the fake world and is bound to the port. Returns its NetId.
    NetId npc(RefId ref, live::Vec3 pos = {1000, 1000, 0}, uint32_t baseLocal = 0x500, bool named = false, CellId cell = 1) {
        eng.addNpc(ref, baseLocal, pos, cell);
        ActorMeta m = metaFor(ref, baseLocal, named);
        port.bindReal(m, ref);
        return m.netId;
    }
};

}  // namespace nvae_test
