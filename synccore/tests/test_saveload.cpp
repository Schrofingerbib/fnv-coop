// Save / load semantics inside a session: host sidecar, world epochs, rebase, client loads.
#include <atomic>
#include <chrono>
#include <memory>
#include <thread>
#include "fnvcoop/session.h"
#include "minitest.h"

using namespace fnvcoop;

namespace {
FormRef Q(uint32_t i) { return FormRef("falloutnv.esm", i); }

ShadowState world(int n, int salt = 0) {
    ShadowState s;
    for (int i = 0; i < n; ++i) {
        s.stages[Q(0x1000 + i)] = uint32_t((i * 7 + salt) % 100);
        s.scriptVars[{Q(0x1000 + i), uint16_t(i % 4)}] = double((i + salt) % 50);
        s.globals[Q(0x3000 + i)] = float(i + salt);
    }
    s.ledger[0].karma = 10;
    return s;
}

struct Rec : IEffectSink {
    std::vector<Effect> live, baseline;
    size_t items = 0;
    void apply(const Effect& e, const Context& c) override {
        (c.baseline ? baseline : live).push_back(e);
        if (std::holds_alternative<AddItem>(e)) ++items;
    }
};

struct Client {
    Rec sink;
    EffectCapture cap;
    ShadowState dump;   // what this client's "engine" currently holds
    std::unique_ptr<ClientSession> cs;
    Client(ITransport* t, uint64_t token, const ShadowState& w) : dump(w) {
        cs = std::make_unique<ClientSession>(*t, 0, token, sink, &cap);
        cs->setBaselineProvider([this](ShadowState& out, uint64_t& h) { out = dump; h = 5; return true; });
    }
};

struct Rig {
    InMemoryNetwork net{9};
    std::unique_ptr<HostSession> host;
    std::vector<std::unique_ptr<Client>> cl;
    uint64_t now = 0;
    explicit Rig(SharePolicy p = {}) : host(std::make_unique<HostSession>(*net.endpoint(0), p)) {}
    Client& add(uint64_t token, const ShadowState& w) {
        cl.push_back(std::make_unique<Client>(net.endpoint(PeerId(cl.size() + 1)), token, w));
        return *cl.back();
    }
    void joinAll() {
        for (auto& c : cl) { c->cs->setBaselineWorld(c->dump, 5); c->cs->connect(now, c->cs->lastSession() + 1); }
    }
    void run(uint32_t ms) {
        for (uint32_t t = 0; t < ms; t += 5) {
            now += 5; net.advance(now); host->tick(now);
            for (auto& c : cl) c->cs->tick(now);
            net.advance(now);
        }
    }
    bool allLive() {
        for (auto& c : cl) if (!c->cs->joined() || c->cs->baselineActive() || c->cs->applied() != host->log().head()) return false;
        return true;
    }
};
Cause quest() { Cause c; c.kind = CauseKind::QuestResult; c.source = Q(1); return c; }
}  // namespace

TEST(saveload, sidecar_roundtrip_and_corruption) {
    Rig r;
    r.host->setWorldBaseline(world(30), 5);
    r.host->setSessionId(0xABCD);
    auto& a = r.add(11, world(30, 3));
    r.joinAll();
    r.run(500);
    r.host->commit(0, quest(), {SetStage{Q(0x1000), 99}, ModKarma{-5, Recipient::dflt()}});
    r.run(200);
    Bytes side = r.host->saveSidecar();
    REQUIRE(!side.empty());
    HostSession h2(*r.net.endpoint(50));
    CHECK(h2.loadSidecar(side));
    CHECK(h2.sessionId() == 0xABCD);
    CHECK(h2.epoch() > r.host->epoch());
    CHECK(h2.hasWorldBaseline());
    CHECK(h2.state().stages.at(Q(0x1000)) == 99);                       // the saved shared world
    CHECK(h2.log().head() == 0);                                         // history restarts in the new epoch
    CHECK(h2.clients().size() == 1);                                     // token -> slot survives
    CHECK(h2.clients()[0].slot == 1);
    (void)a;
    // corruption: truncations and bit flips are rejected or harmless; the failed host is untouched
    Rng rng(4);
    for (size_t cut = 0; cut < side.size(); cut += 5) {
        HostSession h3(*r.net.endpoint(60));
        CHECK(!h3.loadSidecar(Bytes(side.begin(), side.begin() + cut)));
        CHECK(h3.log().head() == 0 && !h3.hasWorldBaseline());
    }
    for (int i = 0; i < 500; ++i) {
        Bytes g = side;
        g[rng.u32() % g.size()] ^= uint8_t(1u << (rng.u32() % 8));
        HostSession h3(*r.net.endpoint(61));
        h3.loadSidecar(g);   // may be accepted (a harmless value flip) or rejected; must not crash
    }
}

TEST(saveload, host_loads_older_save_clients_rebase_and_stale_traffic_is_dropped) {
    Rig r;
    r.host->setWorldBaseline(world(40), 5);
    auto& a = r.add(1, world(40, 2));
    auto& b = r.add(2, world(40, 5));
    r.joinAll();
    r.run(800);
    REQUIRE(r.allLive());
    r.host->commit(0, quest(), {SetStage{Q(0x1000), 10}});
    r.run(100);
    Bytes save = r.host->saveSidecar();                                     // <- the save we will go back to
    for (int i = 0; i < 5; ++i) r.host->commit(0, quest(), {SetStage{Q(0x1000), uint32_t(20 + i)}, AddItem{Q(9), 1, Recipient::dflt()}});
    r.run(200);
    REQUIRE(r.allLive());
    uint32_t oldEpoch = r.host->epoch();
    size_t itemsBefore = a.sink.items;
    // ---- the host loads the old save ----
    CHECK(r.host->loadSidecar(save));
    CHECK(r.host->epoch() == oldEpoch + 1);
    // a straggler old-epoch batch (as if it was in flight) must be dropped, not applied
    TxnBatch stale; stale.session = a.cs->lastSession(); stale.worldEpoch = oldEpoch;
    EffectTxn t; t.seq = a.cs->applied() + 1; t.effects = {SetStage{Q(0x1000), 77}};
    stale.txns.push_back(t);
    a.dump = world(40, 2);   // what the client's engine holds right now (whatever it has; the baseline fixes the shared part)
    r.net.endpoint(0)->send(1, encodeMessage(stale));
    r.run(1500);
    CHECK(r.allLive());
    CHECK(a.cs->worldEpoch() == oldEpoch + 1);
    CHECK(b.cs->worldEpoch() == oldEpoch + 1);
    CHECK(a.cs->stats().rebases == 1);
    CHECK(a.cs->state() == r.host->state());
    CHECK(b.cs->state() == r.host->state());
    CHECK(a.sink.items == itemsBefore);                                      // old-epoch rewards are not re-sent: per-player state is not rewound
    // new epoch works normally
    r.host->commit(0, quest(), {SetStage{Q(0x1000), 55}, AddItem{Q(9), 1, Recipient::dflt()}});
    r.run(300);
    CHECK(a.cs->applied() == r.host->log().head());
    CHECK(a.sink.items == itemsBefore + 1);
    CHECK(r.host->stats().rebasesSent >= 2);
}

namespace {
bool staleTxnsAreFenced() {
    Rig r;
    r.host->setWorldBaseline(world(10), 5);
    auto& a = r.add(1, world(10, 1));
    r.joinAll();
    r.run(600);
    r.host->commit(0, quest(), {SetStage{Q(0x1000), 5}});
    r.run(100);
    Bytes save = r.host->saveSidecar();
    for (int i = 0; i < 4; ++i) r.host->commit(0, quest(), {SetStage{Q(0x1000), uint32_t(30 + i)}});
    r.run(200);
    r.host->loadSidecar(save);
    r.run(1500);
    // after the load the client must follow the NEW timeline: stage 5, same epoch as the host, in sync
    return a.cs->worldEpoch() == r.host->epoch() && a.cs->state() == r.host->state() && r.host->state().stages.at(Q(0x1000)) == 5 &&
           a.cs->joined();
}
}  // namespace

TEST(saveload, epoch_fencing_holds) { CHECK(staleTxnsAreFenced()); }

TEST(twin, unit_epoch) {
    Mutations m; m.noEpochBump = true;
    MutationScope s(m);
    CHECK(!staleTxnsAreFenced());
}

TEST(saveload, reload_world_from_a_fresh_dump_and_offline_client_rebases_on_return) {
    Rig r;
    r.host->setWorldBaseline(world(20), 5);
    auto& a = r.add(1, world(20, 1));
    r.joinAll();
    r.run(600);
    a.cs->disconnect(); r.net.setConnected(1, false);
    CHECK(r.host->reloadWorld(world(20, 9), 5));       // host loaded a save with no sidecar: re-dumped
    r.net.setConnected(1, true);
    a.dump = world(20, 1);
    a.cs->connect(r.now, a.cs->lastSession() + 1);      // plain reconnect with old state: Hello(lastEpoch=old) -> Rebase -> baseline
    r.run(1500);
    CHECK(a.cs->joined());
    CHECK(a.cs->stats().rebases == 1);
    CHECK(a.cs->worldEpoch() == r.host->epoch());
    CHECK(a.cs->state() == r.host->state());
    CHECK(r.host->state().stages.at(Q(0x1000)) == world(20, 9).stages.at(Q(0x1000)));
}

TEST(saveload, rebase_without_provider_is_reported_not_silent) {
    Rig r;
    r.host->setWorldBaseline(world(5), 5);
    auto& a = r.add(1, world(5, 1));
    r.joinAll();
    r.run(400);
    a.cs->setBaselineProvider(nullptr);
    r.host->reloadWorld(world(5, 2), 5);
    r.run(500);
    CHECK(a.cs->rebaseRequired());
    CHECK(!a.cs->joined());
}

TEST(saveload, client_save_load_resumes_without_baseline_and_policy_can_block) {
    Rig r;
    r.host->setWorldBaseline(world(20), 5);
    auto& a = r.add(1, world(20, 1));
    r.joinAll();
    r.run(600);
    r.host->commit(0, quest(), {SetStage{Q(0x1000), 11}, AddItem{Q(9), 1, Recipient::dflt()}});
    r.run(200);
    REQUIRE(a.cs->canSaveState());
    Bytes mine = a.cs->saveState();                               // the client quicksaves
    size_t itemsAtSave = a.sink.items;
    r.host->commit(0, quest(), {SetStage{Q(0x1000), 12}, AddItem{Q(9), 1, Recipient::dflt()}});   // happens after the save
    r.run(200);
    CHECK(a.sink.items == itemsAtSave + 1);
    // the client loads its quicksave: its world is the saved one; the session catches it up (stage 12 + the 2nd reward), no baseline
    uint64_t snapshotsBefore = a.cs->stats().snapshots;
    CHECK(a.cs->clientLoadAllowed());
    CHECK(a.cs->onLocalSaveLoaded(r.now, &mine));
    r.run(600);
    CHECK(a.cs->joined());
    CHECK(!a.cs->baselineDone());                                  // resumed from the saved shadow, no world transfer
    CHECK(a.cs->stats().rebases == 0);
    CHECK(a.cs->applied() == r.host->log().head());
    CHECK(a.cs->state() == r.host->state());
    CHECK(a.sink.items == itemsAtSave + 2);                        // the post-save reward is delivered again, exactly once more
    (void)snapshotsBefore;
    // an unclean / foreign / garbage sidecar falls back to a full baseline rejoin
    Bytes junk = {1, 2, 3};
    a.dump = world(20, 4);
    CHECK(a.cs->onLocalSaveLoaded(r.now, &junk));
    r.run(1200);
    CHECK(a.cs->joined());
    CHECK(a.cs->baselineDone());
    CHECK(a.cs->state() == r.host->state());
    // policy: Block => the engine must refuse client loads
    SharePolicy p; p.clientLoad = SharePolicy::ClientLoad::Block;
    Rig r2(p);
    r2.host->setWorldBaseline(world(5), 5);
    auto& b = r2.add(1, world(5, 1));
    CHECK(b.cs->clientLoadAllowed());                              // before joining there is nothing to protect
    r2.joinAll();
    r2.run(400);
    CHECK(!b.cs->clientLoadAllowed());
    CHECK(!b.cs->onLocalSaveLoaded(r2.now, nullptr));
    CHECK(b.cs->joined());                                         // and nothing changed
}

TEST(saveload, unclean_client_state_is_not_saveable_for_resume) {
    Rig r;
    r.host->setWorldBaseline(world(5), 5);
    auto& a = r.add(1, world(5, 1));
    r.joinAll();
    r.run(400);
    Cause c; c.kind = CauseKind::Loot;
    a.cs->submitIntent(c, {AddItem{Q(5), 1, Recipient::dflt()}});   // an applied-locally action is still in flight
    CHECK(!a.cs->canSaveState());
    Bytes s = a.cs->saveState();
    r.run(400);
    // loading that unclean sidecar must not resume (it would double-apply the in-flight reward): full baseline instead
    CHECK(a.cs->onLocalSaveLoaded(r.now, &s));
    r.run(1200);
    CHECK(a.cs->joined());
    CHECK(a.cs->baselineDone());
}

namespace {
ShadowState bigWorld(int quests) {
    ShadowState s;
    for (int i = 0; i < quests; ++i) {
        s.stages[Q(0x1000 + i)] = uint32_t(i % 100);
        s.scriptVars[{Q(0x1000 + i), uint16_t(i % 4)}] = double(i % 50);
        s.objectives[{Q(0x1000 + i), uint32_t(i % 3)}] = uint8_t(i % 4);
        if (i % 2) s.globals[Q(0x3000 + i)] = float(i);
    }
    s.ledger[0].karma = 5;
    return s;
}
}  // namespace

namespace {
struct RebaseRun { bool done = false; uint64_t ms = 0, bytes = 0, welcomes = 0, throttled = 0, chunks = 0; };

// A real game ticks the host at ~30 fps (with long stalls) over a link with limited bandwidth. A rebase of a big world must
// converge, with traffic bounded by a small multiple of the world size, and not livelock on repeated Hellos / acks.
RebaseRun rebaseBigWorld(int mode, IWorker* worker = nullptr) {
    InMemoryNetwork net{4};
    HostSession host(*net.endpoint(0));
    Rec sink; EffectCapture cap;
    ShadowState clientWorld = bigWorld(1700);          // ~8.3k baseline entries
    ClientSession cs(*net.endpoint(1), 0, 7, sink, &cap);
    cs.setWorker(worker);
    host.setWorker(worker);
    cs.setBaselineProvider([&](ShadowState& w, uint64_t& h) { w = clientWorld; h = 3; return true; });
    host.setWorldBaseline(bigWorld(1700), 3);
    host.setSessionId(5);
    uint64_t now = 0;
    const uint32_t dt = mode == 1 ? 100 : 33;          // 30 Hz, 10 Hz
    LinkParams lp; lp.minLatencyMs = 5; lp.maxLatencyMs = 40;
    if (mode == 2) { lp.loss = 0.05; lp.duplicate = 0.05; }
    net.setLink(lp);
    net.setBandwidth(mode == 3 ? 120000 : 400000);     // bytes/s: a slow pipe makes every redundant Welcome/chunk hurt
    auto step = [&](uint32_t stall = 0) {
        now += dt + stall; net.advance(now); host.tick(now); cs.tick(now); net.advance(now);
        if (worker) std::this_thread::sleep_for(std::chrono::microseconds(200));   // let the worker thread make progress in wall time
    };
    cs.setBaselineWorld(clientWorld, 3);
    cs.connect(now, 1);
    for (int i = 0; i < (worker ? 40000 : 4000) && !cs.baselineDone(); ++i) step(mode == 2 && i % 25 == 0 ? 400 : 0);
    RebaseRun r;
    if (!cs.baselineDone()) return r;
    Bytes side = host.saveSidecar();
    for (int i = 0; i < 3; ++i) { Cause c; c.kind = CauseKind::Script; c.source = Q(1); host.commit(0, c, {SetStage{Q(0x1000), uint32_t(50 + i)}}); step(); }
    if (!host.loadSidecar(side)) return r;
    clientWorld.stages[Q(0x1001)] = 77;
    uint64_t bytes0 = net.stats().bytesSent, t0 = now;
    uint64_t w0 = host.stats().welcomes, c0 = host.stats().baselineChunksSent, th0 = host.stats().welcomesThrottled;
    for (int i = 0; i < (worker ? 30000 : 3000) && !r.done; ++i) {        // up to ~100-300 virtual seconds
        step(mode == 2 && i % 25 == 0 ? 400 : 0);
        r.done = cs.joined() && cs.worldEpoch() == host.epoch() && !cs.baselineActive() && cs.applied() == host.log().head();
    }
    r.ms = now - t0; r.bytes = net.stats().bytesSent - bytes0;
    r.welcomes = host.stats().welcomes - w0; r.chunks = host.stats().baselineChunksSent - c0; r.throttled = host.stats().welcomesThrottled - th0;
    return r;
}
}  // namespace

TEST(saveload, rebase_of_a_big_world_at_low_tick_rates_converges_with_bounded_bytes) {
    for (int mode = 0; mode < 4; ++mode) {
        RebaseRun r = rebaseBigWorld(mode);
        std::printf("    mode %d: rebase %s in %llu ms, %llu KB on the wire, welcomes %llu (throttled %llu), diff chunks %llu\n", mode, r.done ? "done" : "NOT DONE",
                    (unsigned long long)r.ms, (unsigned long long)(r.bytes / 1024), (unsigned long long)r.welcomes, (unsigned long long)r.throttled,
                    (unsigned long long)r.chunks);
        CHECK(r.done);
        CHECK(r.bytes < 1200u * 1024);      // a few times the world size, never the 100s of MB of the livelock
        CHECK(r.welcomes <= 4);
    }
}

TEST(saveload, the_same_big_rebase_with_a_worker_thread_gives_the_same_result) {
    ThreadWorker worker;
    for (int mode = 0; mode < 3; ++mode) {
        RebaseRun a = rebaseBigWorld(mode), b = rebaseBigWorld(mode, &worker);
        worker.waitIdle();
        std::printf("    mode %d: inline %s %llu KB | threaded %s %llu KB\n", mode, a.done ? "done" : "NOT DONE", (unsigned long long)(a.bytes / 1024),
                    b.done ? "done" : "NOT DONE", (unsigned long long)(b.bytes / 1024));
        CHECK(a.done);
        CHECK(b.done);
        CHECK(b.bytes < 1200u * 1024);
    }
}

// The pre-fix behaviour (every Hello re-mints the join + a full Welcome, every ack re-sends the whole window) must be caught by the byte budget.
TEST(twin, unit_chatty_baseline) {
    Mutations m; m.chattyBaseline = true;
    MutationScope s(m);
    int bad = 0;
    for (int mode = 0; mode < 4; ++mode) {
        RebaseRun r = rebaseBigWorld(mode);
        std::printf("    chatty mode %d: %s, %llu KB, welcomes %llu\n", mode, r.done ? "done" : "NOT DONE", (unsigned long long)(r.bytes / 1024), (unsigned long long)r.welcomes);
        if (!r.done || r.bytes >= 1200u * 1024 || r.welcomes > 4) ++bad;
    }
    CHECK(bad > 0);
}

// Issue #46: runAsync queued a completion that owned the MainQueue it sits in, so a session destroyed while a result was still queued leaked the
// queue and the result (a shared_ptr cycle). Here the "session" drops its queue while the finished job's completion is still waiting.
namespace {
struct Tracked {
    static inline std::atomic<int> alive{0};
    int v = 0;
    explicit Tracked(int x) : v(x) { ++alive; }
    Tracked(const Tracked& o) : v(o.v) { ++alive; }
    Tracked(Tracked&& o) noexcept : v(o.v) { ++alive; }
    ~Tracked() { --alive; }
};
// true when dropping the queue frees it and the queued result
bool queuedCompletionDoesNotKeepItsQueueAlive() {
    bool ok = true;
    std::weak_ptr<MainQueue> weak;
    {
        ThreadWorker worker;
        auto q = std::make_shared<MainQueue>();
        weak = q;
        bool ran = false;
        runAsync<Tracked>(&worker, q, [] { return Tracked(7); }, [&ran](Tracked) { ran = true; });
        worker.waitIdle();                       // the completion is now queued on q, undrained
        ok = ok && !ran && q->outstanding() == 1;
        q.reset();                               // the owning session goes away without draining
    }
    ok = ok && weak.expired() && Tracked::alive.load() == 0;
    return ok;
}
}  // namespace

TEST(saveload, a_queued_async_completion_does_not_keep_its_queue_alive) {
    CHECK(queuedCompletionDoesNotKeepItsQueueAlive());
    // and the normal path still delivers the result on drain()
    ThreadWorker worker;
    auto q = std::make_shared<MainQueue>();
    int got = 0;
    runAsync<Tracked>(&worker, q, [] { return Tracked(41); }, [&got](Tracked t) { got = t.v + 1; });
    worker.waitIdle();
    CHECK(q->drain() == 1);
    CHECK(got == 42);
    CHECK(q->outstanding() == 0);
}

TEST(twin, unit_async_completion_leak) {
    Mutations m; m.asyncDoneHoldsQueue = true;
    MutationScope s(m);
    CHECK(!queuedCompletionDoesNotKeepItsQueueAlive());
}
