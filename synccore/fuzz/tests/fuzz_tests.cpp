// Tests for the co-op fuzzer: oracle unit tests on hand-built logs, clean property runs, one mutation twin per bug class
// (the fuzzer must catch each), shrinking, replay and the saved regression scenarios.
#include <algorithm>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <sstream>
#include "fuzz.h"
#include "minitest.h"

using namespace fnvcoop;
using namespace fnvcoop::fuzz;

namespace {
int seedCount(int dflt) {
    const char* e = std::getenv("FNVCOOP_SEEDS");
    return e ? std::atoi(e) : dflt;
}

RunOptions mini() { RunOptions o; o.target = "mini"; return o; }

Scenario withBug(uint64_t seed, int machines, uint32_t duration, const std::string& bug) {
    Scenario s = generate(seed, machines, duration);
    if (!bug.empty()) s.bugs.push_back(bug);
    return s;
}

struct Caught { int failed = 0, ran = 0; std::map<std::string, int> by; std::string first; uint64_t firstSeed = 0; };
Caught sweep(const std::string& bug, int first, int count, uint32_t duration = 8000) {
    Caught c;
    for (int i = 0; i < count; ++i) {
        uint64_t sd = static_cast<uint64_t>(first + i);
        int machines = 2 + static_cast<int>(sd % 3);
        RunResult r = run(withBug(sd, machines, duration, bug), mini());
        ++c.ran;
        if (!r.ok) {
            ++c.failed;
            for (auto& v : r.violations) ++c.by[v.invariant];
            if (c.first.empty()) { c.first = r.violations[0].invariant + ": " + r.violations[0].detail; c.firstSeed = sd; }
        }
    }
    return c;
}

// ---- a hand-built target so the oracles can be tested on their own
class FakeTarget : public ITarget {
public:
    int n = 3;
    std::vector<ApplyEvent> ap;
    std::vector<OwnEvent> ow;
    std::vector<MsgTrace> ms;
    std::map<int, std::vector<Thing>> views;
    uint64_t ord = 0;
    void apply(const Op&) override {}
    void advance(uint64_t) override {}
    uint64_t now() const override { return 0; }
    void heal() override {}
    int machines() const override { return n; }
    bool connected(int) const override { return true; }
    bool quiescent() const override { return true; }
    std::vector<Thing> view(int m) const override { auto it = views.find(m); return it == views.end() ? std::vector<Thing>() : it->second; }
    uint64_t digest(int m, int cat) const override { uint64_t h = 7 + static_cast<uint64_t>(cat); for (auto& t : view(m)) h = h * 31 + t.x + t.netId; return h; }
    uint64_t repairs(int) const override { return 0; }
    const std::vector<ApplyEvent>& applyLog() const override { return ap; }
    const std::vector<OwnEvent>& ownLog() const override { return ow; }
    const std::vector<MsgTrace>& msgLog() const override { return ms; }
    std::map<std::string, uint64_t> stats() const override { return {}; }
    void addApply(int m, uint64_t idem, int ref, uint32_t epoch, uint32_t seq, int origin, ApplyKind k, bool acc = true, uint32_t wep = 0) {
        ApplyEvent e; e.machine = m; e.ord = ++ord; e.idem = idem; e.ref = ref; e.epoch = epoch; e.seq = seq; e.origin = origin; e.kind = k; e.accepted = acc; e.worldEpoch = wep;
        ap.push_back(e);
    }
    void addOwn(OwnEvent::Kind k, int ref, int owner, uint32_t epoch) {
        OwnEvent o; o.ord = ++ord; o.kind = k; o.ref = ref; o.owner = owner; o.epoch = epoch; ow.push_back(o);
    }
};
bool has(const std::vector<Violation>& v, const char* inv) { for (auto& x : v) if (x.invariant == inv) return true; return false; }
Thing mk(uint32_t id, int x, uint32_t ident = 0, uint8_t type = 1) { Thing t; t.netId = id; t.x = x; t.ident = ident; t.type = type; return t; }
}  // namespace

TEST(fuzz, oracle_double_apply) {
    FakeTarget f;
    f.addApply(1, 5, 10, 0, 0, 0, ApplyKind::Hp);
    f.addApply(2, 5, 10, 0, 0, 0, ApplyKind::Hp);           // other machine, same id: fine
    f.addApply(1, 5, 10, 0, 0, 0, ApplyKind::Hp, false);    // rejected duplicate: fine
    std::vector<Violation> v;
    checkLogs(f, v);
    CHECK(v.empty());
    f.addApply(1, 5, 10, 0, 0, 0, ApplyKind::Hp);           // accepted twice on one machine
    checkLogs(f, v);
    CHECK(has(v, "double_apply"));
}
TEST(fuzz, oracle_one_owner_per_epoch) {
    FakeTarget f;
    f.addOwn(OwnEvent::Grant, 10, 1, 1);
    f.addOwn(OwnEvent::Transfer, 10, 2, 2);
    f.addOwn(OwnEvent::Release, 11, -1, 3);
    std::vector<Violation> v;
    checkLogs(f, v);
    CHECK(v.empty());
    f.addOwn(OwnEvent::Transfer, 10, 0, 2);                  // second owner in epoch 2
    checkLogs(f, v);
    CHECK(has(v, "multi_owner"));
    FakeTarget g;
    g.addOwn(OwnEvent::Grant, 10, 1, 5);
    g.addOwn(OwnEvent::Transfer, 10, 2, 4);                  // epoch backwards
    v.clear();
    checkLogs(g, v);
    CHECK(has(v, "multi_owner"));
}
TEST(fuzz, oracle_stale_and_echo) {
    FakeTarget f;
    f.addOwn(OwnEvent::Grant, 10, 1, 1);
    f.addApply(0, 100, 10, 1, 1, 1, ApplyKind::Pos);
    f.addApply(0, 101, 10, 1, 2, 1, ApplyKind::Pos);
    f.addApply(2, 1, 10, 1, 1, 1, ApplyKind::Pos);
    f.addOwn(OwnEvent::Transfer, 10, 2, 2);
    f.addApply(0, 102, 10, 2, 1, 2, ApplyKind::Pos);
    f.addApply(0, 103, 10, 1, 3, 1, ApplyKind::Pos, false);  // correctly rejected stale update
    std::vector<Violation> v;
    checkLogs(f, v);
    CHECK(v.empty());
    FakeTarget a = f; a.addApply(0, 104, 10, 1, 4, 1, ApplyKind::Pos);   // old owner accepted after the transfer
    v.clear(); checkLogs(a, v); CHECK(has(v, "stale_applied"));
    FakeTarget b = f; b.addApply(2, 9, 10, 1, 7, 1, ApplyKind::Pos);     // older epoch applied on a machine that already saw epoch 1->... sequence
    b.addApply(2, 10, 10, 2, 1, 2, ApplyKind::Pos); b.addApply(2, 11, 10, 1, 8, 1, ApplyKind::Pos);
    v.clear(); checkLogs(b, v); CHECK(has(v, "stale_applied"));
    FakeTarget c = f; c.addApply(1, 12, 10, 1, 2, 1, ApplyKind::Pos);    // sequence 2 after 2 on machine 1? first on m1 -> fine
    c.addApply(1, 13, 10, 1, 1, 1, ApplyKind::Pos);                      // sequence went backwards on machine 1
    v.clear(); checkLogs(c, v); CHECK(has(v, "stale_applied"));
    FakeTarget d = f; d.addApply(1, 14, 77, 0, 0, 1, ApplyKind::PlayerPos);   // m1 applied the echo of its own move
    v.clear(); checkLogs(d, v); CHECK(has(v, "echo_adopted"));
    FakeTarget e; e.addOwn(OwnEvent::WorldEpoch, -1, -1, 2); e.addApply(0, 1, 5, 0, 0, 1, ApplyKind::Intent, true, 1);   // request from before the load
    v.clear(); checkLogs(e, v); CHECK(has(v, "stale_applied"));
}
TEST(fuzz, oracle_converge_and_duplicates) {
    FakeTarget f;
    f.views[0] = {mk(1, 5, 100), mk(2, 6, 101)};
    f.views[1] = {mk(1, 5, 100), mk(2, 6, 101)};
    f.views[2] = {mk(1, 5, 100), mk(2, 6, 101)};
    std::vector<Violation> v;
    checkConverged(f, v);
    CHECK(v.empty());
    f.views[2][1].x = 99;
    checkConverged(f, v);
    CHECK(has(v, "converge"));
    FakeTarget g;
    g.views[0] = g.views[1] = g.views[2] = {mk(1, 5, 100), mk(2, 6, 100)};      // two things with one identity
    v.clear(); checkConverged(g, v); CHECK(has(v, "dup_thing"));
    FakeTarget h;
    h.views[0] = {mk(1, 5), mk(2, 6)}; h.views[1] = {mk(1, 5)}; h.views[2] = {mk(1, 5), mk(2, 6)};     // a missing actor
    v.clear(); checkConverged(h, v); CHECK(has(v, "converge"));
}

TEST(fuzz, generator_is_deterministic_and_json_round_trips) {
    Scenario a = generate(42, 3, 10000), b = generate(42, 3, 10000);
    CHECK(toJson(a) == toJson(b));
    CHECK(toJson(generate(43, 3, 10000)) != toJson(a));
    a.bugs.push_back("two_owners");
    Scenario c;
    std::string err;
    CHECK_MSG(fromJson(toJson(a), c, &err), err);
    CHECK(toJson(c) == toJson(a));
    CHECK(c.ops.size() == a.ops.size() && c.bugs.size() == 1);
    Scenario bad;
    CHECK(!fromJson("{\"format\": \"nope\"}", bad, &err));
    CHECK(!fromJson("not json", bad, &err));
}

TEST(fuzz, run_is_deterministic) {
    Scenario s = generate(7, 4, 8000);
    RunResult a = run(s, mini()), b = run(s, mini());
    CHECK(a.ok && b.ok);
    CHECK(a.virtualMs == b.virtualMs);
    CHECK(a.stats == b.stats);
}

TEST(fuzz, clean_sessions_hold_every_invariant) {
    int n = seedCount(120);
    Caught c = sweep("", 1, n, 10000);
    std::printf("    %d clean sessions, %d failed\n", c.ran, c.failed);
    CHECK_MSG(c.failed == 0, "seed " + std::to_string(c.firstSeed) + " " + c.first);
}

TEST(fuzz, clean_sessions_exercise_the_hard_paths) {
    // Guard against a fuzzer that passes because it does nothing: the sweep must really cause these events.
    std::map<std::string, uint64_t> total;
    for (int sd = 1; sd <= 40; ++sd) {
        RunResult r = run(generate(static_cast<uint64_t>(sd), 2 + sd % 3, 10000), mini());
        for (auto& kv : r.stats) total[kv.first] += kv.second;
    }
    for (const char* k : {"grants", "transfers", "releases", "deaths", "loot_races_lost", "spawn_races_lost", "loads", "joins", "leaves", "stale_old_content_injected",
                          "stale_raw_injected", "dup_packets_dropped", "reject_stale epoch", "reject_stale update sequence", "net_lost", "net_dup"}) {
        CHECK_MSG(total[k] > 0, std::string("never exercised: ") + k);
    }
}

// ---- mutation twins: every injected bug class must be caught, with the right kind of violation
namespace {
void twin(const char* bug, std::initializer_list<const char*> expect, int count = 60) {
    Caught c = sweep(bug, 1, count);
    std::printf("    twin %-22s caught on %d/%d sessions  e.g. %s\n", bug, c.failed, c.ran, c.first.substr(0, 110).c_str());
    CHECK_MSG(c.failed > 0, std::string("bug not caught: ") + bug);
    bool matched = false;
    for (const char* e : expect) if (c.by.count(e)) matched = true;
    CHECK_MSG(matched, std::string("caught, but not as ") + *expect.begin() + ": " + c.first);
}
}  // namespace
TEST(fuzz, twin_stale_epoch_accepted) { twin("stale_epoch_accepted", {"stale_applied"}); }
TEST(fuzz, twin_stale_seq_accepted) { twin("stale_seq_accepted", {"stale_applied"}); }
TEST(fuzz, twin_echo_adopted) { twin("echo_adopted", {"echo_adopted"}); }
TEST(fuzz, twin_no_idempotency) { twin("no_idempotency", {"double_apply"}); }
TEST(fuzz, twin_two_owners) { twin("two_owners", {"multi_owner"}); }
TEST(fuzz, twin_spawn_dup) { twin("spawn_dup", {"dup_thing"}); }
TEST(fuzz, twin_pickup_dup) { twin("pickup_dup", {"dup_thing"}); }
TEST(fuzz, twin_handoff_respawn) { twin("handoff_respawn", {"dup_thing"}); }
TEST(fuzz, twin_digest_unstable) { twin("digest_unstable", {"repairs_after_quiescence"}); }
TEST(fuzz, twin_no_retransmit) { twin("no_retransmit", {"converge", "not_quiescent"}); }
TEST(fuzz, twin_no_world_epoch) { twin("no_world_epoch", {"stale_applied"}, 120); }

TEST(fuzz, every_known_bug_has_a_twin) {
    // Keep this list in step with the twins above: adding a bug class without a twin fails here.
    std::vector<std::string> covered = {"stale_epoch_accepted", "stale_seq_accepted", "echo_adopted", "no_idempotency", "two_owners", "spawn_dup",
                                        "pickup_dup", "handoff_respawn", "digest_unstable", "no_retransmit", "no_world_epoch"};
    auto known = knownBugs("mini");
    CHECK(known.size() == covered.size());
    for (auto& k : known) CHECK_MSG(std::find(covered.begin(), covered.end(), k) != covered.end(), "no twin for " + k);
    Scenario s = generate(1, 3, 1000);
    s.bugs.push_back("not_a_bug");
    RunResult r = run(s, mini());
    CHECK(!r.ok && r.violations[0].invariant == "setup");
}

TEST(fuzz, shrink_reduces_and_replay_reproduces) {
    // find a failing session with a known bug, shrink it, round-trip the file, replay it
    Caught c = sweep("two_owners", 1, 40);
    REQUIRE(c.failed > 0);
    Scenario s = withBug(c.firstSeed, 2 + static_cast<int>(c.firstSeed % 3), 8000, "two_owners");
    int runs = 0;
    Scenario small = shrink(s, mini(), "multi_owner", &runs);
    std::printf("    shrunk %zu ops -> %zu ops in %d runs\n", s.ops.size(), small.ops.size(), runs);
    CHECK(small.ops.size() < s.ops.size() / 4);
    CHECK(small.ops.size() <= 40);
    Scenario back;
    std::string err;
    REQUIRE(fromJson(toJson(small), back, &err));
    RunResult r = run(back, mini());
    CHECK(!r.ok);
    bool found = false;
    for (auto& v : r.violations) if (v.invariant == "multi_owner") found = true;
    CHECK(found);
    // dropping the injected bug makes the very same scenario pass: the shrunk file is the bug, not the environment
    back.bugs.clear();
    CHECK(run(back, mini()).ok);
}

TEST(fuzz, failure_writes_scenario_and_trace) {
    Caught c = sweep("pickup_dup", 1, 40);
    REQUIRE(c.failed > 0);
    Scenario s = withBug(c.firstSeed, 2 + static_cast<int>(c.firstSeed % 3), 8000, "pickup_dup");
    RunOptions o = mini();
    o.traceDir = "out/T2b-fuzz/tmp/trace_test";
    o.traceName = "t";
    RunResult r = run(s, o);
    CHECK(!r.ok);
    std::ifstream sc(o.traceDir + "/t.scenario.json"), tr(o.traceDir + "/t.trace.json");
    CHECK(static_cast<bool>(sc));
    CHECK(static_cast<bool>(tr));
    std::stringstream ss; ss << tr.rdbuf();
    CHECK(ss.str().find("simfuzz-trace-1") != std::string::npos);
    CHECK(ss.str().find("dup_thing") != std::string::npos);
#ifdef FNVCOOP_FUZZ_FLIGHT
    std::ifstream nv(o.traceDir + "/t.m0.nvfr", std::ios::binary);
    CHECK(static_cast<bool>(nv));
#endif
}

TEST(fuzz, saved_regression_scenarios) {
    // Every saved scenario is a shrunk repro of one bug class: it must break the named invariant while the bug is injected
    // (so the file still reproduces) and pass once the bug is removed (so it is a real regression test for the product).
    namespace fs = std::filesystem;
    int n = 0;
    std::error_code ec;
    for (auto& e : fs::directory_iterator(FUZZ_REGRESS_DIR, ec)) {
        if (e.path().extension() != ".json") continue;
        std::ifstream f(e.path(), std::ios::binary);
        std::stringstream ss; ss << f.rdbuf();
        Scenario s; std::string err;
        CHECK_MSG(fromJson(ss.str(), s, &err), e.path().string() + ": " + err);
        if (s.target != "mini") continue;
        std::string name = e.path().filename().string();
        RunResult broken = run(s, mini());
        CHECK_MSG(s.bugs.empty() || !broken.ok, name + ": no longer reproduces its bug");
        if (!s.expect.empty() && !broken.ok) {
            bool found = false;
            for (auto& v : broken.violations) if (v.invariant == s.expect) found = true;
            CHECK_MSG(found, name + ": expected invariant " + s.expect + " but got " + broken.violations[0].invariant);
        }
        s.bugs.clear();
        RunResult clean = run(s, mini());
        CHECK_MSG(clean.ok, name + ": fails without the bug: " + (clean.ok ? "" : clean.violations[0].detail));
        ++n;
    }
    std::printf("    %d saved scenarios\n", n);
    CHECK(n >= 11);
}

TEST(fuzz, chaos_profiles_clean_and_twins) {
    // T5's impairment model (burst loss, reorder, jitter, bandwidth): clean sessions hold, injected bugs are still caught.
    for (const char* p : {"lan", "good_wifi", "bad_wifi", "mobile_tether", "transatlantic", "hell"}) {
        int failed = 0, lost = 0;
        for (int sd = 1; sd <= 40; ++sd) {
            Scenario s = generate(static_cast<uint64_t>(sd), 2 + sd % 3, 8000);
            s.profile = p;
            RunResult r = run(s, mini());
            if (!r.ok) ++failed;
            lost += r.stats["net_lost"] > 0;
        }
        std::printf("    profile %-14s %d/40 failed, loss seen in %d\n", p, failed, lost);
        CHECK_MSG(failed == 0, std::string("clean run failed under profile ") + p);
        if (std::string(p) != "lan") CHECK_MSG(lost > 0, std::string("profile never dropped anything: ") + p);
    }
    for (const char* bug : {"two_owners", "no_idempotency", "no_retransmit", "stale_epoch_accepted"}) {
        int caught = 0;
        for (int sd = 1; sd <= 30; ++sd) {
            Scenario s = generate(static_cast<uint64_t>(sd), 2 + sd % 3, 8000);
            s.profile = "hell"; s.bugs = {bug};
            caught += !run(s, mini()).ok;
        }
        CHECK_MSG(caught > 0, std::string("not caught under hell: ") + bug);
    }
}

TEST(fuzz, network_shaper_hook_drops_duplicates_and_delays) {
    InMemoryNetwork net(1);
    ITransport* a = net.endpoint(0);
    ITransport* b = net.endpoint(1);
    int calls = 0;
    net.setShaper([&](PeerId, PeerId, uint64_t, Bytes&) {
        ++calls;
        if (calls == 1) return std::vector<uint32_t>{};          // dropped
        if (calls == 2) return std::vector<uint32_t>{50, 120};   // duplicated, two delays
        return std::vector<uint32_t>{5};
    });
    a->send(1, Bytes{1});
    a->send(1, Bytes{2});
    a->send(1, Bytes{3});
    net.advance(10);
    PeerId from = 0;
    Bytes d;
    CHECK(b->poll(from, d) && d[0] == 3);   // the only one due by 10 ms
    CHECK(!b->poll(from, d));
    net.advance(60);
    CHECK(b->poll(from, d) && d[0] == 2);
    CHECK(!b->poll(from, d));
    net.advance(200);
    CHECK(b->poll(from, d) && d[0] == 2);
    CHECK(net.stats().lost == 1 && net.stats().duplicated == 1);
}
