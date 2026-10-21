// One machine for the feed / API tests: fake engine, port, feed and a REAL ActorSync (host alone, or a client that never hears from a host).
#pragma once
#include <memory>

#include "port_rig.h"

namespace nvae_test {

struct NullNet : IActorNet {
    uint64_t sends = 0;
    void send(PlayerId, bool, Bytes) override { ++sends; }
};

struct FeedRig {
    FakeEngine eng;
    MemStore store;
    Ledger led;
    EnginePort port;
    NullNet net;
    std::unique_ptr<ActorSync> sync;
    uint64_t gen = 0;
    bool host;
    ActorFeed feed;
    uint64_t t = 10000;
    std::vector<TrackedEvent> tracked;
    std::vector<DiedEvent> died;
    std::vector<LostEvent> lost;

    explicit FeedRig(bool isHost = true, Config c = rigConfig())
        : led(&store), port(eng, led, c), host(isHost), feed(eng, port, led, [this] { return sync.get(); }, [this] { return gen; }) {
        registry().reset();
        registry().cfg = c;
        feed.setConfig(c);
        registry().addTracked([this](const TrackedEvent& e) { tracked.push_back(e); });
        registry().addDied([this](const DiedEvent& e) { died.push_back(e); });
        registry().addLost([this](const LostEvent& e) { lost.push_back(e); });
        wireApi(port, feed, eng, [this] { return sync.get(); });
    }
    ~FeedRig() { registry().reset(); }

    void startSession() {
        SyncConfig sc;
        sc.self = host ? 0 : 1;
        sc.isHost = host;
        sc.adjacent = [this](CellId a, CellId b) { return feed.adjacent(a, b); };
        sync.reset(new ActorSync(sc, port, net));
        if (!host) sync->hostConnected();
        ++gen;
    }
    void endSession() { sync.reset(); ++gen; }
    void frame(uint32_t dt = 50) {
        t += dt;
        feed.tick(t);
        if (sync) sync->tick(t);
        eng.simulate(dt);
    }
    void run(uint32_t ms, uint32_t dt = 50) { for (uint32_t x = 0; x < ms; x += dt) frame(dt); }
    NetId id(RefId ref) { return deriveNetId(eng.refForm(ref)); }
};

}  // namespace nvae_test
