// Flight recorder tests. Each real test has a mutation twin (group "twin"): the twin re-introduces one bug and
// must make the same expectations FAIL. Run: flight_tests [--group g] [--name n]
#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <map>
#include <memory>
#include <thread>
#ifdef _WIN32
#include <process.h>
#else
#include <unistd.h>
#endif
#include "flight/flight.h"
#include "fnvcoop/session.h"
#include "fnvcoop/protocol.h"
#include "fnvcoop/transport.h"
#include "fnvcoop/rng.h"
#include "../../tests/minitest.h"

using namespace fnvcoop;
using namespace fnvcoop::flight;

namespace {

FormRef Q(uint32_t i) { return FormRef("falloutnv.esm", i); }
Cause quest(uint32_t q = 1) { Cause c; c.kind = CauseKind::QuestResult; c.source = Q(q); return c; }

static bool g_quiet = false;
static bool fail(int line) { if (!g_quiet) std::printf("    check failed at test_flight.cpp:%d\n", line); return false; }
struct Mut {
    explicit Mut(void (*set)(FlightMutations&)) { flightMutations() = FlightMutations(); if (set) set(flightMutations()); g_quiet = true; }
    ~Mut() { flightMutations() = FlightMutations(); g_quiet = false; }
};

Config cfgFor(uint8_t slot, uint8_t role, size_t ring = 1u << 20, const char* label = "t") {
    Config c; c.slot = slot; c.role = role; c.ringBytes = ring; c.label = label; return c;
}

// ctest runs the flight tests in parallel in one working directory: scratch files carry the process id, or two tests
// would dump over each other ("out_flight_tmp.nvfr") and one of them reads the other one's trace.
std::string scratch(const char* stem, const char* ext = ".nvfr") {
#ifdef _WIN32
    int pid = _getpid();
#else
    int pid = static_cast<int>(getpid());
#endif
    return std::string(stem) + "_" + std::to_string(pid) + ext;
}

Trace dumpAndParse(FlightRecorder& r, uint8_t reason = kDumpTest) {
    std::string path = scratch("out_flight_tmp");
    Trace t;
    bool ok = r.dump(path, reason);
    REQUIRE(ok);
    std::string err;
    REQUIRE(readTrace(path, t, &err));
    std::remove(path.c_str());
    return t;
}

size_t countType(const Trace& t, uint8_t type) { size_t n = 0; for (auto& r : t.records) if (r.type == type) ++n; return n; }

// A host + clients over the in-memory network, every endpoint wrapped in a RecordingTransport.
struct RecSinkLocal : IEffectSink {
    std::vector<uint64_t> seqs;
    void apply(const Effect&, const Context& c) override { seqs.push_back(c.seq); }
};
struct Machine {
    FlightRecorder rec;
    std::unique_ptr<RecordingTransport> rt;
    Machine(const Config& c, uint64_t* clock) : rec(c) {
        rec.setClockSource([clock] { return *clock * 1000; });
        rec.setWallStart(1700000000000ull);
    }
};
struct Sim {
    uint64_t now = 1000;
    InMemoryNetwork net{7};
    Machine h, c;
    RecSinkLocal csink;
    std::unique_ptr<RecordingSink> crec;
    std::unique_ptr<HostSession> host;
    std::unique_ptr<ClientSession> cli;
    Sim() : h(cfgFor(0, kRoleHost, 1u << 20, "host"), &now), c(cfgFor(1, kRoleClient, 1u << 20, "client"), &now) {
        h.rt.reset(new RecordingTransport(*net.endpoint(0), h.rec));
        c.rt.reset(new RecordingTransport(*net.endpoint(1), c.rec));
        crec.reset(new RecordingSink(csink, c.rec));
        host.reset(new HostSession(*h.rt));
        cli.reset(new ClientSession(*c.rt, 0, 111, *crec));
    }
    void run(uint32_t ms) {
        for (uint32_t t = 0; t < ms; t += 5) {
            now += 5;
            h.rec.setFrame(static_cast<uint32_t>(now / 16)); c.rec.setFrame(static_cast<uint32_t>(now / 16));
            net.advance(now);
            host->tick(now);
            cli->tick(now);
            net.advance(now);
        }
    }
    void scenario() {
        cli->connect(now, 1);
        run(200);
        for (uint32_t i = 1; i <= 5; ++i) { host->commit(0, quest(), {SetStage{Q(1), i * 10}}); run(30); }
        run(300);
    }
};

// ---- the checks shared by a test and its twin: each returns true iff the trace is right ----

bool checkSessionTrace() {
    Sim s;
    s.scenario();
    if (!(s.cli->state() == s.host->state())) return false;
    Trace ht = dumpAndParse(s.h.rec), ct = dumpAndParse(s.c.rec);
    if (ht.header.slot != 0 || ht.header.role != kRoleHost || ct.header.slot != 1 || ct.header.role != kRoleClient) return false;
    // host sent TxnBatches covering seq 1..5; client received them
    uint64_t hostMax = 0, cliMax = 0;
    size_t hostIn = 0, cliIn = 0;
    for (auto& r : ht.records) {
        NetInfo n;
        if (r.type == kNetOut && parseNet(r, n) && n.msgType == 4) hostMax = std::max(hostMax, n.seqHi);
        if (r.type == kNetIn) ++hostIn;
    }
    uint64_t lastAck = 0;
    for (auto& r : ct.records) {
        NetInfo n;
        if (r.type == kNetIn && parseNet(r, n)) { ++cliIn; if (n.msgType == 4) cliMax = std::max(cliMax, n.seqHi); }
        if (r.type == kNetOut && parseNet(r, n) && n.msgType == 5) lastAck = std::max(lastAck, n.seqLo);
    }
    if (hostMax != 5 || cliMax != 5 || lastAck != 5 || hostIn == 0 || cliIn == 0) return false;
    // the client's applied records (seq != 0) are exactly 1..5 in order
    std::vector<uint64_t> applied;
    for (auto& r : ct.records) if (r.type == kTxnApplied) {
        uint64_t seq = 0;
        for (int k = 0; k < 8; ++k) seq |= static_cast<uint64_t>(r.payload[k]) << (8 * k);
        if (seq) applied.push_back(seq);
    }
    if (applied != std::vector<uint64_t>{1, 2, 3, 4, 5}) return false;
    // session field decoded and equal to the Hello session
    for (auto& r : ct.records) { NetInfo n; if (r.type == kNetOut && parseNet(r, n) && n.msgType == 1 && n.session != 1) return false; }
    return true;
}

bool checkRingDrop() {
    FlightRecorder r(cfgFor(0, kRoleSim, 4096));
    for (uint32_t i = 0; i < 200; ++i) r.marker(kMarkNote, "m" + std::to_string(i));
    Trace t = dumpAndParse(r);
    if (t.header.dropped == 0) return false;
    // newest marker must be there, the first one gone, order preserved
    std::vector<std::string> texts;
    for (auto& rec : t.records) if (rec.type == kMarker) texts.emplace_back(rec.payload.begin() + 4, rec.payload.end());
    if (texts.empty() || texts.back() != "m199" || texts.front() == "m0") return false;
    for (size_t i = 1; i < texts.size(); ++i) {
        int a = std::atoi(texts[i - 1].c_str() + 1), b = std::atoi(texts[i].c_str() + 1);
        if (b != a + 1) return false;
    }
    return t.header.dropped + texts.size() == 200;
}

bool checkTruncation() {
    Config c = cfgFor(0, kRoleSim, 1u << 20); c.maxRawBytes = 100;
    FlightRecorder r(c);
    Bytes big(5000, 0x5A); big[0] = 0xD1;
    r.netOut(3, big, 1);
    Trace t = dumpAndParse(r);
    for (auto& rec : t.records) if (rec.type == kNetOut) {
        NetInfo n;
        if (!parseNet(rec, n)) return false;
        return (rec.flags & kFlagTruncated) && n.rawLen == 5000 && n.raw.size() == 100 && n.mux == 0xD1 && n.decode == 2 && n.channel == 1 && n.peer == 3;
    }
    return false;
}

bool checkRecords() {
    FlightRecorder r(cfgFor(2, kRoleClient));
    r.setSession(9, 4);
    r.meta("build", "x");
    OwnershipEvent o; o.op = kOwnTransfer; o.actor = 0xABCDEF0123ull; o.epoch = 7; o.fromOwner = 0; o.toOwner = 2; o.reason = 2; o.txnId = 99;
    r.ownership(o);
    DigestEvent d; d.category = kCatActors; d.epoch = 4; d.seq = 12; d.hash = 0x1122334455667788ull; d.itemCount = 3;
    d.shards = {1, 2, 3}; d.keys = {{10, 100}, {11, 101}};
    r.digest(d);
    IEffectSink::Context ctx; ctx.seq = 12; ctx.epoch = 4; ctx.origin = 1; ctx.index = 2; ctx.repair = true; ctx.catchUp = true;
    Cause cz; cz.kind = CauseKind::Loot; ctx.cause = &cz;
    r.applied(SetStage{Q(1), 30}, ctx);
    r.captured(1, 5, 0, {SetStage{Q(1), 30}, SetGlobal{Q(2), 1.0f}}, CauseKind::Intent, 4);
    r.marker(kMarkSave, "saved");
    Trace t = dumpAndParse(r);
    if (t.header.sessionId != 9 || t.header.epoch != 4 || t.header.slot != 2) return false;
    if (t.records.empty() || t.records[0].type != kMeta) return false;
    bool sawO = false, sawD = false, sawA = false, sawC = false, sawM = false;
    for (auto& rec : t.records) {
        OwnershipEvent o2; DigestEvent d2;
        if (parseOwnership(rec, o2)) sawO = o2.op == kOwnTransfer && o2.actor == o.actor && o2.epoch == 7 && o2.fromOwner == 0 && o2.toOwner == 2 && o2.reason == 2 && o2.txnId == 99;
        if (parseDigest(rec, d2)) sawD = d2.category == kCatActors && d2.seq == 12 && d2.hash == d.hash && d2.itemCount == 3 && d2.shards == d.shards && d2.keys == d.keys;
        if (rec.type == kTxnApplied)
            sawA = rec.payload.size() == 28 && rec.payload[17] == (2 | 8) && rec.payload[18] == static_cast<uint8_t>(CauseKind::Loot) && rec.payload[19] == 0;
        if (rec.type == kTxnCaptured) sawC = rec.payload[0] == 1 && rec.payload[1] == 5;
        if (rec.type == kMarker) sawM = rec.payload[0] == kMarkSave;
    }
    return sawO && sawD && sawA && sawC && sawM;
}

bool checkDerivedDigest() {
    FlightRecorder r(cfgFor(0, kRoleHost));
    SyncDigest sd; sd.session = 4; sd.epoch = 2; sd.seq = 77; sd.digestId = 1; sd.shards.assign(128, 0); sd.shards[5] = 0xDEAD;
    Bytes wire = encodeMessage(sd);
    r.netIn(1, wire);
    Trace t = dumpAndParse(r);
    bool net = false, dig = false;
    for (auto& rec : t.records) {
        NetInfo n; DigestEvent d;
        if (rec.type == kNetIn && parseNet(rec, n)) net = n.msgType == 13 && n.seqLo == 77 && n.epoch == 2 && n.session == 4;
        if (rec.type == kDigest && parseDigest(rec, d)) dig = (rec.flags & kFlagDerived) && d.seq == 77 && d.shards.size() == 128 && d.shards[5] == 0xDEAD && d.epoch == 2;
    }
    return net && dig;
}

bool checkDroppedHeader() {
    FlightRecorder r(cfgFor(0, kRoleSim, 4096));
    for (int i = 0; i < 300; ++i) r.marker(kMarkNote, "xxxxxxxxxxxxxxxxxxxx");
    Trace t = dumpAndParse(r);
    return t.header.dropped > 0 && r.stats().dropped == t.header.dropped;
}

bool checkWithSessionMessages(bool wantNetIn) {
    FlightRecorder r(cfgFor(0, kRoleSim));
    Ack a; a.session = 1; a.upTo = 42; a.worldEpoch = 3;
    r.netIn(5, encodeMessage(a));
    Trace t = dumpAndParse(r);
    return (countType(t, kNetIn) == 1) == wantNetIn;
}

}  // namespace

TEST(flight, session_trace_matches_the_wire) { CHECK(checkSessionTrace()); }
TEST(flight, ring_drops_oldest_and_counts) { CHECK(checkRingDrop()); }
TEST(flight, oversize_datagram_is_cut_and_flagged) { CHECK(checkTruncation()); }
TEST(flight, record_types_round_trip) { CHECK(checkRecords()); }
TEST(flight, sync_digest_yields_a_digest_record) { CHECK(checkDerivedDigest()); }
TEST(flight, header_reports_dropped) { CHECK(checkDroppedHeader()); }
TEST(flight, received_datagrams_are_recorded) { CHECK(checkWithSessionMessages(true)); }

TEST(flight, disabled_recorder_records_nothing) {
    FlightRecorder r(cfgFor(0, kRoleSim));
    r.setEnabled(false);
    r.marker(kMarkNote, "x"); r.netOut(1, Bytes{1, 2, 3}); r.netIn(1, Bytes{1});
    Trace t = dumpAndParse(r);
    CHECK(t.records.size() == 1);   // only the Meta record
}

TEST(flight, cut_short_file_parses_up_to_the_last_full_record) {
    FlightRecorder r(cfgFor(0, kRoleSim));
    for (int i = 0; i < 10; ++i) r.marker(kMarkNote, "abc");
    const std::string cutPath = scratch("out_flight_cut");
    r.dump(cutPath);
    Trace full; REQUIRE(readTrace(cutPath, full));
    FILE* f = std::fopen(cutPath.c_str(), "rb");
    std::vector<uint8_t> b(100000); size_t n = std::fread(b.data(), 1, b.size(), f); std::fclose(f); b.resize(n);
    std::remove(cutPath.c_str());
    b.resize(b.size() - 3);
    Trace cut; REQUIRE(parseTrace(b, cut));
    CHECK(cut.records.size() + 1 == full.records.size());
    std::vector<uint8_t> bad(b.begin(), b.begin() + 10);
    Trace junk; CHECK(!parseTrace(bad, junk));
}

TEST(flight, unknown_files_are_rejected) {
    std::vector<uint8_t> b(64, 0);
    Trace t; std::string err;
    CHECK(!parseTrace(b, t, &err));
    b[0] = 'N'; b[1] = 'V'; b[2] = 'F'; b[3] = 'R'; b[4] = 9;   // future version
    CHECK(!parseTrace(b, t, &err));
}

TEST(flight, dump_to_dir_and_desync_rate_limit) {
    Config c = cfgFor(1, kRoleClient); c.dir = "out_flight_dir"; c.autoDumpMax = 2; c.autoDumpMinIntervalMs = 0;
    FlightRecorder r(c);
    r.marker(kMarkNote, "hello");
    std::string p1 = r.onDesync("shard 3"), p2 = r.onDesync("shard 4"), p3 = r.onDesync("shard 5");
    CHECK(!p1.empty()); CHECK(!p2.empty()); CHECK(p3.empty());
    CHECK(r.stats().autoDumpsSkipped == 1);
    Trace t; REQUIRE(readTrace(p1, t));
    CHECK(t.header.reason == kDumpDesync);
    CHECK(countType(t, kMarker) == 2);   // note + desync marker
    std::string crash = r.dumpCrash();
    CHECK(!crash.empty());
    std::remove(p1.c_str()); std::remove(p2.c_str()); std::remove(crash.c_str());
    std::remove("out_flight_dir");
}

TEST(flight, threads_can_record_concurrently) {
    FlightRecorder r(cfgFor(0, kRoleSim, 1u << 22));
    std::vector<std::thread> ts;
    for (int t = 0; t < 4; ++t) ts.emplace_back([&r, t] { for (int i = 0; i < 2000; ++i) r.marker(kMarkNote, "t" + std::to_string(t)); });
    for (auto& th : ts) th.join();
    Trace t = dumpAndParse(r);
    CHECK(countType(t, kMarker) == 8000);
}

TEST(flight, wrap_around_keeps_big_records_intact) {
    Config c = cfgFor(0, kRoleSim, 10000);
    FlightRecorder r(c);
    SyncDigest sd; sd.session = 1; sd.epoch = 1; sd.seq = 1; sd.shards.assign(128, 7);
    for (int i = 0; i < 100; ++i) { sd.seq = i + 1; r.netIn(1, encodeMessage(sd)); }
    Trace t = dumpAndParse(r);
    size_t good = 0; uint64_t last = 0;
    for (auto& rec : t.records) { NetInfo n; if (rec.type == kNetIn && parseNet(rec, n)) { CHECK(n.decode == 0); CHECK(n.seqLo == last + 1 || last == 0); last = n.seqLo; ++good; } }
    CHECK(good >= 5);
    CHECK(last == 100);
}

// ---- bucketed digests: a changed value must be pinned to its bucket, then to its key ----
static std::vector<std::pair<uint64_t, uint32_t>> makeEntries(size_t n) {
    Rng rng(42);
    std::vector<std::pair<uint64_t, uint32_t>> v;
    for (size_t i = 0; i < n; ++i) v.emplace_back(rng.next(), rng.u32());
    return v;
}
static std::map<uint64_t, uint32_t> detailOf(const Trace& t) {
    std::map<uint64_t, uint32_t> m;
    for (auto& r : t.records) { DigestEvent d; if (r.type == kDigest && (r.flags & kFlagDetail) && parseDigest(r, d)) for (auto& kv : d.keys) m[kv.first] = kv.second; }
    return m;
}
bool checkPinpoint() {
    auto a = makeEntries(9000);
    auto b = a;
    b.back().second ^= 0x1234;   // one value differs on the other machine (the LAST entry: also catches a detail list that loses its tail)
    const uint64_t badKey = a.back().first;
    DigestEvent da = bucketedDigest(kCatWorld, 3, 0, a), db = bucketedDigest(kCatWorld, 3, 0, b);
    if (da.shards.size() != kDigestBuckets || da.hash == db.hash) return fail(__LINE__);
    std::vector<size_t> diffB;
    for (size_t i = 0; i < kDigestBuckets; ++i) if (da.shards[i] != db.shards[i]) diffB.push_back(i);
    if (diffB.size() != 1 || diffB[0] != (badKey & 0xFF)) return fail(__LINE__);
    FlightRecorder ra(cfgFor(0, kRoleHost, 8u << 20)), rb(cfgFor(1, kRoleClient, 8u << 20));
    ra.digest(da); ra.digestDetail(kCatWorld, 3, 0, a);
    rb.digest(db); rb.digestDetail(kCatWorld, 3, 0, b);
    Trace ta = dumpAndParse(ra), tb = dumpAndParse(rb);
    std::map<uint64_t, uint32_t> ma = detailOf(ta), mb = detailOf(tb);
    if (ma.size() != 9000) return fail(__LINE__);   // all entries survived the 4000-per-record split
    std::vector<uint64_t> diffK;
    for (auto& kv : ma) { auto it = mb.find(kv.first); if (it == mb.end() || it->second != kv.second) diffK.push_back(kv.first); }
    for (auto& kv : mb) if (!ma.count(kv.first)) diffK.push_back(kv.first);
    return diffK.size() == 1 && diffK[0] == badKey ? true : fail(__LINE__);
}
TEST(flight, one_changed_value_is_pinned_to_its_bucket_and_key) { CHECK(checkPinpoint()); }
TEST(flight, bucket_digest_ignores_entry_order) {
    auto a = makeEntries(2000);
    auto b = a;
    std::reverse(b.begin(), b.end());
    CHECK(bucketedDigest(kCatStory, 1, 5, a).shards == bucketedDigest(kCatStory, 1, 5, b).shards);
    CHECK(bucketedDigest(kCatStory, 1, 5, a).hash == bucketedDigest(kCatStory, 1, 5, b).hash);
    b.pop_back();
    CHECK(bucketedDigest(kCatStory, 1, 5, a).hash != bucketedDigest(kCatStory, 1, 5, b).hash);
}
TEST(perf, bucketed_digest_of_20000_entries) {
    auto a = makeEntries(20000);
    auto t0 = std::chrono::steady_clock::now();
    DigestEvent d;
    for (int i = 0; i < 20; ++i) d = bucketedDigest(kCatStory, 1, 5, a);
    double us = std::chrono::duration<double, std::micro>(std::chrono::steady_clock::now() - t0).count() / 20;
    std::printf("    bucketedDigest(20000 entries): %.0f us (only at quiet points, never per frame)\n", us);
    CHECK(us < 5000);
}

TEST(perf, story_digest_from_a_realistic_shared_state) {
    // 640 quests (stage, running, 3 objectives each) plus 20000 other entries (globals, enabled refs)
    ShadowState st;
    for (uint32_t q = 0; q < 640; ++q) {
        FormRef f("falloutnv.esm", 0x100000 + q);
        st.stages[f] = q % 50; st.questRunning[f] = (q & 1) != 0;
        for (uint32_t o = 0; o < 3; ++o) st.objectives[{f, o}] = static_cast<uint8_t>(o);
    }
    for (uint32_t i = 0; i < 10000; ++i) st.globals[FormRef("falloutnv.esm", 0x200000 + i)] = static_cast<float>(i);
    for (uint32_t i = 0; i < 10000; ++i) st.enabled[FormRef("falloutnv.esm", 0x300000 + i)] = (i & 1) != 0;
    const int reps = 20;
    size_t n = 0;
    auto t0 = std::chrono::steady_clock::now();
    for (int r = 0; r < reps; ++r) {
        std::vector<std::pair<uint64_t, uint32_t>> es;
        for (const BaselineEntry& e : baselineEntries(st)) es.emplace_back(e.key, e.value);
        n = es.size();
        DigestEvent d = bucketedDigest(kCatStory, 1, r, es);
        (void)d;
    }
    double slow = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count() / reps;
    DigestEvent fast;
    t0 = std::chrono::steady_clock::now();
    // one applied effect between digests; every 4th is a SetGlobal, which dirties the biggest segment (10000 entries): the worst case
    double worstMs = 0, totalMs = 0;
    bucketedDigestOf(kCatStory, 1, 7, st);   // the first digest of a state is a full walk (one time)
    for (int r = 0; r < reps; ++r) {
        EffectTxn txn;
        if (r % 4 == 3) txn.effects.push_back(SetGlobal{FormRef("falloutnv.esm", 0x200000 + r), 1000.0f + r});
        else txn.effects.push_back(SetStage{FormRef("falloutnv.esm", 0x100000 + r), 60u + r});
        txn.seq = 100 + r;
        st.applyTxn(txn);
        auto t1 = std::chrono::steady_clock::now();
        fast = bucketedDigestOf(kCatStory, 1, 7, st);
        double one = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t1).count();
        worstMs = std::max(worstMs, one); totalMs += one;
    }
    double ms = totalMs / reps;
    std::printf("    incremental digest after one effect: mean %.3f ms, worst %.3f ms (SetGlobal touches a 10000-entry map)\n", ms, worstMs);
    std::vector<std::pair<uint64_t, uint32_t>> es;
    for (const BaselineEntry& e : baselineEntries(st)) es.emplace_back(e.key, e.value);
    DigestEvent ref = bucketedDigest(kCatStory, 1, 7, es);
    std::printf("    story digest of %zu entries: via baselineEntries %.2f ms, direct from the maps %.2f ms\n", n, slow, ms);
    CHECK(n > 20000);
    CHECK(fast.shards == ref.shards && fast.hash == ref.hash && fast.itemCount == ref.itemCount);   // both paths agree
    CHECK(worstMs < 2.0);
}

// breakDirty simulates an effect that forgot to mark its segment dirty (the twin must see the cache go stale)
static bool checkIncremental(bool breakDirty) {
    ShadowState st;
    Rng rng(77);
    auto fr = [&](uint32_t n) { return FormRef("falloutnv.esm", 0x1000 + rng.next() % n); };
    for (int i = 0; i < 3000; ++i) {
        EffectTxn txn; txn.seq = 1 + i;
        switch (rng.next() % 15) {
            case 0: txn.effects.push_back(SetStage{fr(40), uint32_t(rng.next() % 90)}); break;
            case 1: txn.effects.push_back(SetGlobal{fr(40), float(rng.next() % 100)}); break;
            case 2: txn.effects.push_back(EnableRef{fr(40)}); break;
            case 3: txn.effects.push_back(DisableRef{fr(40)}); break;
            case 4: txn.effects.push_back(KillActor{fr(40)}); break;
            case 5: txn.effects.push_back(ResurrectActor{fr(40)}); break;
            case 6: txn.effects.push_back(CompleteQuest{fr(40)}); break;
            case 7: txn.effects.push_back(FailQuest{fr(40)}); break;
            case 8: txn.effects.push_back(StartQuest{fr(40)}); break;
            case 9: txn.effects.push_back(AddTopic{fr(40)}); break;
            case 10: txn.effects.push_back(AddNote{fr(40)}); break;
            case 11: txn.effects.push_back(RemoveNote{fr(40)}); break;
            case 12: txn.effects.push_back(SetLock{fr(40), (rng.next() & 1) != 0, int32_t(rng.next() % 4)}); break;
            case 13: { MoveRef m; m.ref = fr(40); m.cell = fr(5); m.x = float(rng.next() % 9); txn.effects.push_back(m); break; }
            default: txn.effects.push_back(SetTimeScale{float(rng.next() % 5)}); break;
        }
        st.applyTxn(txn);
        if (breakDirty) st.bucketCache.dirty = 0;
        if (rng.next() % 5 == 0) {   // digest at irregular points, not after every effect
            size_t a = 0, b = 0;
            auto cached = bucketHashesCached(st, &a);
            auto full = bucketHashesOf(st, &b);
            if (!(cached == full && a == b)) return false;
        }
    }
    return st.bucketCache.verifyFailures == 0 && st.bucketCache.calls > 100;
}
TEST(flight, incremental_digest_equals_full_walk_after_random_effects) { CHECK(checkIncremental(false)); }

// ---- golden files: deterministic traces the Python reader tests use; also a drift detector for the format ----
static std::vector<uint8_t> goldenBytes(bool host) {
    Sim s;
    s.scenario();
    FlightRecorder& r = host ? s.h.rec : s.c.rec;
    OwnershipEvent o; o.op = kOwnGrant; o.actor = 0x1001; o.epoch = 1; o.toOwner = host ? 0 : 1; o.reason = 1; o.txnId = 3;
    r.ownership(o);
    DigestEvent d; d.category = kCatActors; d.epoch = 1; d.seq = 5; d.hash = 0xFEEDFACEull; d.itemCount = 2; d.keys = {{0x1001, 11}, {0x1002, 12}};
    r.digest(d);
    r.marker(kMarkTestStep, "golden");
    std::string p = scratch("out_flight_golden");
    REQUIRE(r.dump(p, kDumpTest));
    FILE* f = std::fopen(p.c_str(), "rb");
    std::vector<uint8_t> b(1u << 22); size_t n = std::fread(b.data(), 1, b.size(), f); std::fclose(f); b.resize(n);
    std::remove(p.c_str());
    return b;
}
static std::vector<uint8_t> readFile(const std::string& p, bool* ok) {
    FILE* f = std::fopen(p.c_str(), "rb");
    *ok = f != nullptr;
    std::vector<uint8_t> b;
    if (!f) return b;
    b.resize(1u << 22); size_t n = std::fread(b.data(), 1, b.size(), f); std::fclose(f); b.resize(n);
    return b;
}
static bool goldenMatches() {
    const char* dir = std::getenv("FNVCOOP_FLIGHT_GOLDEN");
    if (!dir) return true;   // not configured (run via run_tests.bat)
    for (int host = 0; host < 2; ++host) {
        std::string p = std::string(dir) + (host ? "/sample_host.nvfr" : "/sample_client.nvfr");
        std::vector<uint8_t> a = goldenBytes(host != 0);
        if (std::getenv("FNVCOOP_FLIGHT_WRITE_GOLDEN")) { FILE* f = std::fopen(p.c_str(), "wb"); if (f) { std::fwrite(a.data(), 1, a.size(), f); std::fclose(f); } continue; }
        bool ok; std::vector<uint8_t> b = readFile(p, &ok);
        if (!ok || a != b) return false;
    }
    return true;
}
TEST(flight, golden_samples_are_deterministic_and_unchanged) {
    CHECK(goldenBytes(true) == goldenBytes(true));
    CHECK(goldenBytes(false) == goldenBytes(false));
    CHECK(goldenMatches());
}

// Fresh traces for tests/cross_test.py (the Python reader, tools/flight/nvfr.py, must read what this writer writes).
TEST(flight, emit_fresh_traces_for_the_python_reader) {
    const char* dir = std::getenv("FNVCOOP_FLIGHT_EMIT");
    if (!dir) return;
    for (int host = 0; host < 2; ++host) {
        std::vector<uint8_t> b = goldenBytes(host != 0);
        std::string p = std::string(dir) + (host ? "/fresh_host.nvfr" : "/fresh_client.nvfr");
        FILE* f = std::fopen(p.c_str(), "wb");
        REQUIRE(f != nullptr);
        std::fwrite(b.data(), 1, b.size(), f);
        std::fclose(f);
    }
}

// ---- performance: recording overhead per frame must stay far below 0.1 ms ----
static double perFrameUs(int frames, bool busy) {
    FlightRecorder r(cfgFor(0, kRoleHost, 16u << 20));
    Ack a; a.session = 1; a.upTo = 5; a.worldEpoch = 1;
    Bytes ack = encodeMessage(a);
    Bytes dgram(200, 0xFC);
    IEffectSink::Context ctx; ctx.seq = 1;
    Effect e = SetStage{Q(1), 10};
    auto t0 = std::chrono::steady_clock::now();
    for (int f = 0; f < frames; ++f) {
        r.setFrame(static_cast<uint32_t>(f));
        if (busy) {
            for (int i = 0; i < 20; ++i) { r.netOut(1, dgram); r.netIn(1, dgram); }
            for (int i = 0; i < 10; ++i) r.applied(e, ctx);
        } else if (f % 30 == 0) {   // idle: roughly an ack pair every half second at 60 fps
            r.netOut(1, ack); r.netIn(1, ack);
        }
        if (f % 120 == 0) { DigestEvent d; d.category = kCatStory; d.hash = f; r.digest(d); }
    }
    auto t1 = std::chrono::steady_clock::now();
    return std::chrono::duration<double, std::micro>(t1 - t0).count() / frames;
}
TEST(perf, idle_frame_overhead_is_far_below_0_1_ms) {
    double idle = perFrameUs(200000, false);
    double busy = perFrameUs(20000, true);
    std::printf("    flight overhead: idle %.3f us/frame, busy (40 datagrams + 10 effects) %.3f us/frame (budget 100 us)\n", idle, busy);
    CHECK(idle < 100.0);
    CHECK(idle < 5.0);
    CHECK(busy < 100.0);
}

// ---- mutation twins: every injected bug must make the matching check fail ----
TEST(twin, drop_newest) { Mut m([](FlightMutations& f) { f.dropNewest = true; }); CHECK(!checkRingDrop()); }
TEST(twin, no_net_in) { Mut m([](FlightMutations& f) { f.noNetIn = true; }); CHECK(!checkWithSessionMessages(true)); CHECK(!checkSessionTrace()); }
TEST(twin, zero_seq) { Mut m([](FlightMutations& f) { f.zeroSeq = true; }); CHECK(!checkSessionTrace()); CHECK(!checkDerivedDigest()); }
TEST(twin, no_trunc_flag) { Mut m([](FlightMutations& f) { f.noTruncFlag = true; }); CHECK(!checkTruncation()); }
TEST(twin, no_dropped_count) { Mut m([](FlightMutations& f) { f.noDroppedCount = true; }); CHECK(!checkDroppedHeader()); CHECK(!checkRingDrop()); }
TEST(twin, no_applied_flags) { Mut m([](FlightMutations& f) { f.noAppliedFlags = true; }); CHECK(!checkRecords()); }
TEST(twin, incremental_misses_dirty) { CHECK(!checkIncremental(true)); }
TEST(twin, buckets_ignore_value) { Mut m([](FlightMutations& f) { f.bucketsIgnoreValue = true; }); CHECK(!checkPinpoint()); }
TEST(twin, detail_drops_key) { Mut m([](FlightMutations& f) { f.detailDropsKey = true; }); CHECK(!checkPinpoint()); }
TEST(twin, no_derived_digest) { Mut m([](FlightMutations& f) { f.noDerivedDigest = true; }); CHECK(!checkDerivedDigest()); }
