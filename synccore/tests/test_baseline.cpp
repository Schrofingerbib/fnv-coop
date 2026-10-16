// World baseline (join a game in progress): diff, ordering, chunking, rejection, wire.
#include <algorithm>
#include <memory>
#include "fnvcoop/baseline.h"
#include "fnvcoop/replicator.h"
#include "fnvcoop/session.h"
#include "minitest.h"

using namespace fnvcoop;

namespace {
FormRef Q(uint32_t i) { return FormRef("falloutnv.esm", i); }

ShadowState bigWorld(int n, int salt = 0) {
    ShadowState s;
    for (int i = 0; i < n; ++i) {
        s.stages[Q(0x1000 + i)] = uint32_t((i * 7 + salt) % 100);
        s.objectives[{Q(0x1000 + i), uint32_t(i % 4)}] = uint8_t((i + salt) % 4);
        s.scriptVars[{Q(0x1000 + i), uint16_t(i % 8)}] = double((i + salt) % 50) * 0.5;
        if (i % 3 == 0) s.globals[Q(0x3000 + i)] = float(i + salt);
        if (i % 5 == 0) s.enabled[Q(0x4000 + i)] = ((i + salt) % 2) == 0;
        if (i % 11 == 0) s.kills.insert(Q(0x5000 + i));
    }
    return s;
}

struct Rec : IEffectSink {
    std::vector<Effect> baseline, live;
    BaselineResult result; bool completed = false;
    void apply(const Effect& e, const Context& c) override { (c.baseline ? baseline : live).push_back(e); }
    void onBaselineComplete(const BaselineResult& r) override { result = r; completed = true; }
};

struct Rig {
    InMemoryNetwork net{3};
    HostSession host;
    Rec sink;
    EffectCapture cap;
    std::unique_ptr<ClientSession> cs;
    uint64_t now = 0;
    explicit Rig(SharePolicy p = {}) : host(*net.endpoint(0), p) { cs = std::make_unique<ClientSession>(*net.endpoint(1), 0, 77, sink, &cap); }
    void run(uint32_t ms) {
        for (uint32_t t = 0; t < ms; t += 5) { now += 5; net.advance(now); host.tick(now); cs->tick(now); net.advance(now); }
    }
};
}  // namespace

TEST(baseline, identical_worlds_need_nothing_and_differences_are_ordered_by_class) {
    ShadowState host = bigWorld(200), client = host;
    BaselineSummary s = summarizeWorld(client, 1);
    BaselineDiff d = diffBaseline(host, s, SharePolicy(), 1, true, 0, true, {});
    CHECK(d.effects.empty());
    CHECK(d.revertKeys.empty());
    CHECK(d.keptByClient == baselineEntries(host).size());

    // change one of each class on the client
    client.stages[Q(0x1000)] = 99;
    client.objectives[{Q(0x1001), 1}] = 3;
    client.scriptVars[{Q(0x1002), 2}] = 123.0;
    client.globals[Q(0x3000)] = -1.f;
    client.enabled[Q(0x4000)] = !client.enabled[Q(0x4000)];
    s = summarizeWorld(client, 1);
    d = diffBaseline(host, s, SharePolicy(), 1, true, 0, true, {});
    REQUIRE(d.effects.size() == 5);
    // order: stages -> objectives -> vars -> globals -> refs
    CHECK(std::holds_alternative<SetStage>(d.effects[0]));
    CHECK(std::holds_alternative<SetObjective>(d.effects[1]));
    CHECK(std::holds_alternative<SetScriptVar>(d.effects[2]));
    CHECK(std::holds_alternative<SetGlobal>(d.effects[3]));
    CHECK((std::holds_alternative<EnableRef>(d.effects[4]) || std::holds_alternative<DisableRef>(d.effects[4])));
    // host values win, including LOWER stages
    CHECK(std::get<SetStage>(d.effects[0]).stage == host.stages[Q(0x1000)]);
}

TEST(baseline, client_only_kill_is_reverted_and_unshared_categories_are_dropped) {
    ShadowState host = bigWorld(20), client = host;
    client.kills.insert(Q(0x9999));
    BaselineSummary s = summarizeWorld(client, 1);
    BaselineDiff d = diffBaseline(host, s, SharePolicy(), 1, true, 0, true, {});
    REQUIRE(d.revertKeys.size() == 1);
    Effect rev;
    REQUIRE(revertEffectFor(client, d.revertKeys[0], rev));
    CHECK((std::holds_alternative<ResurrectActor>(rev) && std::get<ResurrectActor>(rev).actor == Q(0x9999)));
    // a policy that does not share journal/globals/kills drops those categories from the transfer
    ShadowState c2 = host;
    c2.stages[Q(0x1000)] = 77; c2.globals[Q(0x3000)] = 5.f; c2.kills.clear();
    SharePolicy p; p.journal = false; p.globals = false; p.kills = false;
    d = diffBaseline(host, summarizeWorld(c2, 1), p, 1, true, 0, true, {});
    for (auto& e : d.effects) CHECK((!std::holds_alternative<SetStage>(e) && !std::holds_alternative<SetGlobal>(e) && !std::holds_alternative<KillActor>(e)));
}

TEST(baseline, absolute_karma_and_reputation_become_deltas_for_the_joiner) {
    ShadowState host, client;
    client.ledger[0].karma = 40;
    client.ledger[0].rep[Q(0x500)] = {5, 1};
    std::vector<RepAbs> hostRep = {{Q(0x500), 15, 1}, {Q(0x501), 3, 3}};
    BaselineDiff d = diffBaseline(host, summarizeWorld(client, 1), SharePolicy(), 2, true, -60, true, hostRep);
    int64_t k = 0, fame500 = 0, fame501 = 0;
    for (auto& e : d.effects) {
        if (auto* m = std::get_if<ModKarma>(&e)) { k += m->delta; CHECK(m->to == Recipient::slot(2)); }
        if (auto* r = std::get_if<ModReputation>(&e)) { if (r->faction == Q(0x500)) fame500 += r->fame; else fame501 += r->fame; }
    }
    CHECK(k == -100);
    CHECK(fame500 == 10);
    CHECK(fame501 == 3);
}

TEST(baseline, wire_roundtrip_of_new_messages) {
    BaselineSummaryChunk sc{1, 2, 0, 3, {{0xDEADBEEFCAFEull, 7}, {1, 2}}, true, -5, {{Q(1), 2, 3}}};
    BaselineDiffChunk dc{1, 2, 1, 3, {SetStage{Q(1), 5}, SetScriptVar{Q(2), 3, 1.5}}, {9, 10}};
    BaselineAck ak{1, 2, 1, 4};
    Hello h{5, 6, 7, kHelloWantBaseline, 0xABCDEF};
    for (Message m : {Message(sc), Message(dc), Message(ak), Message(h)}) {
        Bytes b = encodeMessage(m);
        REQUIRE(!b.empty());
        Message out;
        REQUIRE(decodeMessage(b, out) == DecodeStatus::Ok);
        CHECK(out.index() == m.index());
        CHECK(encodeMessage(out) == b);
    }
    Bytes bad = encodeMessage(BaselineAck{1, 2, 1, 4});
    // phase > 1 and index >= total are rejected
    BaselineSummaryChunk worse = sc; worse.index = 3;
    Message out;
    CHECK(decodeMessage(encodeMessage(worse), out) != DecodeStatus::Ok);
}

TEST(baseline, join_in_progress_transfers_chunked_over_a_lossy_link_then_goes_live) {
    Rig r;
    ShadowState host = bigWorld(2500, 0), client = bigWorld(2500, 3);   // ~10k entries each, mostly different
    client.kills.insert(Q(0xAAAA));
    r.host.setWorldBaseline(host, 42);
    CHECK(r.host.hasWorldBaseline());
    // the session already has history before the friend arrives
    r.host.commit(0, Cause{CauseKind::QuestResult, Q(1), 0}, {SetStage{Q(0x1000), 99}, AddItem{Q(5), 2, Recipient::dflt()}});
    LinkParams lp; lp.loss = 0.2; lp.duplicate = 0.1; lp.minLatencyMs = 0; lp.maxLatencyMs = 30;
    r.net.setLink(lp);
    r.cs->setBaselineWorld(client, 42);
    r.cs->connect(r.now, 1);
    // keep committing while the transfer is under way: those txns must be held and applied after the baseline
    for (int i = 0; i < 40; ++i) {
        r.run(100);
        r.host.commit(0, Cause{CauseKind::Script, Q(1), 0}, {SetScriptVar{Q(0x1005), 1, double(i)}});
    }
    r.net.setLink(LinkParams());
    r.run(3000);
    CHECK(r.cs->joined());
    CHECK(r.cs->baselineDone());
    CHECK(r.sink.completed);
    CHECK(r.cs->applied() == r.host.log().head());
    CHECK(r.cs->state() == r.host.state());
    CHECK(r.sink.result.chunks > 10);                       // genuinely chunked
    CHECK(r.sink.result.reverted == 1);                     // the client-only kill
    CHECK(r.host.stats().baselineJoins == 1);
    CHECK(r.host.stats().baselineChunksSent >= r.sink.result.chunks);
    // ordering: the first baseline effects are stages, the last are not
    REQUIRE(!r.sink.baseline.empty());
    size_t firstVar = r.sink.baseline.size(), lastStage = 0;
    for (size_t i = 0; i < r.sink.baseline.size(); ++i) {
        if (std::holds_alternative<SetScriptVar>(r.sink.baseline[i]) && firstVar == r.sink.baseline.size()) firstVar = i;
        if (std::holds_alternative<SetStage>(r.sink.baseline[i])) lastStage = i;
    }
    CHECK(lastStage < firstVar);
    // live effects were applied only after the baseline (none recorded before completion is impossible to see here,
    // but the reward from the pre-join history must have arrived exactly once)
    size_t items = 0;
    for (auto& e : r.sink.live) items += std::holds_alternative<AddItem>(e);
    CHECK(items == 1);
}

TEST(baseline, host_rejects_content_mismatch_and_hosts_without_baseline) {
    {
        Rig r;
        r.host.setWorldBaseline(bigWorld(10), 1);
        r.cs->setBaselineWorld(bigWorld(10), 2);   // different plugin set
        r.cs->connect(r.now, 1);
        r.run(300);
        CHECK(r.cs->rejected());
        CHECK(r.cs->rejectReason() == 2);
        CHECK(!r.cs->joined());
    }
    {
        Rig r;                                     // host never called setWorldBaseline
        r.cs->setBaselineWorld(bigWorld(10), 1);
        r.cs->connect(r.now, 1);
        r.run(300);
        CHECK(r.cs->rejected());
        CHECK(r.cs->rejectReason() == 3);
    }
}

TEST(baseline, set_world_baseline_requires_an_empty_log) {
    Rig r;
    r.host.commit(0, Cause(), {SetStage{Q(1), 1}});
    r.host.setWorldBaseline(bigWorld(5), 1);
    CHECK(!r.host.hasWorldBaseline());
}

TEST(baseline, skipped_baseline_leaves_the_world_divergent_unit_twin) {
    auto run = [] {
        Rig r;
        ShadowState host = bigWorld(50, 0), client = bigWorld(50, 3);
        r.host.setWorldBaseline(host, 1);
        r.cs->setBaselineWorld(client, 1);
        r.cs->connect(r.now, 1);
        r.run(500);
        // World as the sink saw it: start from the client's world, apply what was delivered.
        ShadowState w = client;
        for (auto& e : r.sink.baseline) { if (auto* s = std::get_if<SetStage>(&e)) w.stages[s->quest] = s->stage; }
        return w.stages == host.stages && r.cs->baselineDone();
    };
    CHECK(run());
    Mutations m; m.baselineSkipped = true;
    MutationScope s(m);
    CHECK(!run());
}

TEST(baseline, entries_kept_counter_reaches_the_client_result_and_host_stats) {
    Rig r;
    ShadowState host = bigWorld(1500, 0), client = host;   // ~6k entries, identical except 3
    client.stages[Q(0x1000)] = 99;
    client.globals[Q(0x3000)] = -7.f;
    client.scriptVars[{Q(0x1001), 1}] = 77.0;
    r.host.setWorldBaseline(host, 9);
    r.cs->setBaselineWorld(client, 9);
    r.cs->connect(r.now, 1);
    r.run(1500);
    REQUIRE(r.cs->baselineDone());
    size_t total = baselineEntries(host).size();
    CHECK(total > 5000);
    CHECK(r.sink.result.entriesKept == total - 3);
    CHECK(r.cs->baselineResult().entriesKept == total - 3);
    CHECK(r.host.stats().baselineEntriesKept == total - 3);
    CHECK(r.sink.result.effectsApplied == 3);
}

namespace {
// Shared by the real test and its twin.
bool volatileKeysAreNeverBaselinedOrReplicated() {
    volatileKeys().clear();
    volatileKeys().addVar(Q(0x1000), 3);
    volatileKeys().addOwner(Q(0x2000));
    volatileKeys().addGlobal(Q(0x3000));
    bool ok = true;
    ShadowState host = bigWorld(20), client = host;
    host.scriptVars[{Q(0x1000), 3}] = 1.0;      // volatile var: differs, must not be transferred
    host.scriptVars[{Q(0x2000), 9}] = 5.0;      // volatile owner
    host.globals[Q(0x3000)] = 123.f;            // volatile global
    host.scriptVars[{Q(0x1000), 4}] = 8.0;      // same owner, other var: NOT volatile
    BaselineDiff d = diffBaseline(host, summarizeWorld(client, 1), SharePolicy(), 1, false, 0, false, {});
    int vars = 0;
    for (auto& e : d.effects) {
        if (auto* v = std::get_if<SetScriptVar>(&e)) { ++vars; ok &= !(v->owner == Q(0x1000) && v->index == 3) && !(v->owner == Q(0x2000)); }
        if (auto* g = std::get_if<SetGlobal>(&e)) ok &= !(g->global == Q(0x3000));
    }
    ok &= vars == 1;
    // client-only volatile entries do not trigger reverts either
    client.kills.clear();
    // replication: normalizeTxn drops volatile writes; the rest is kept
    EffectTxn t;
    t.effects = {SetScriptVar{Q(0x1000), 3, 1.0}, SetScriptVar{Q(0x2000), 1, 1.0}, SetGlobal{Q(0x3000), 1.f},
                 SetScriptVar{Q(0x1000), 4, 1.0}, SetGlobal{Q(0x3001), 1.f}};
    ok &= normalizeTxn(t, SharePolicy()) == 3;
    ok &= t.effects.size() == 2;
    volatileKeys().clear();
    return ok;
}
}  // namespace

TEST(baseline, volatile_keys_are_never_baselined_or_replicated) {
    CHECK(volatileKeysAreNeverBaselinedOrReplicated());
    CHECK(volatileKeys().size() == 0);
}

TEST(baseline, volatile_keys_replicator_and_text_format) {
    volatileKeys().clear();
    std::string err;
    CHECK(volatileKeys().loadFromText("# timers\nvar FalloutNV.esm 1000 3\nowner FalloutNV.esm 2000\nglobal FalloutNV.esm 3000 # radio\n", &err));
    CHECK(volatileKeys().size() == 3);
    CHECK(volatileKeys().isVolatileVar(Q(0x1000), 3));
    CHECK(!volatileKeys().isVolatileVar(Q(0x1000), 4));
    CHECK(volatileKeys().isVolatileVar(Q(0x2000), 77));
    CHECK(volatileKeys().isVolatileGlobal(Q(0x3000)));
    CHECK(!volatileKeys().loadFromText("bogus line here", &err));
    CHECK(!err.empty());
    CHECK(!volatileKeys().loadFromText("var FalloutNV.esm 10 99999", &err));   // index out of range
    CHECK(volatileKeys().size() == 3);   // failed loads change nothing
    Rig r;
    StateReplicator rep(r.host);
    for (int i = 0; i < 1000; ++i) { rep.setScriptVar(Q(0x1000), 3, double(i)); rep.setGlobal(Q(0x3000), float(i)); }
    rep.setScriptVar(Q(0x1000), 4, 1.0);
    CHECK(rep.stats().volatileDropped == 2000);
    CHECK(rep.pending() == 1);
    volatileKeys().clear();
}

TEST(twin, unit_volatile) {
    Mutations m; m.ignoreVolatile = true;
    MutationScope s(m);
    CHECK(!volatileKeysAreNeverBaselinedOrReplicated());
    volatileKeys().clear();
}

TEST(baseline, volatile_keys_accept_l7_file_format_names_and_fields) {
    volatileKeys().clear();
    std::string err;
    const char* l7 =
        "# produced by volatile_probe.py\n"
        "var FalloutNV.esm:001000 fTimer\n"
        "var FalloutNV.esm:001000 7\n"
        "field FalloutNV.esm:002000 running\n"
        "field FalloutNV.esm:002001 objectives\n"
        "global FalloutNV.esm:003000\n";
    REQUIRE(volatileKeys().loadFromText(l7, &err));
    CHECK(volatileKeys().unresolvedNames() == 1);
    CHECK(volatileKeys().isVolatileVar(Q(0x1000), 7));
    CHECK(volatileKeys().isVolatileGlobal(Q(0x3000)));
    // names resolve once the engine can map them
    size_t n = volatileKeys().resolveNames([](const FormRef& o, const std::string& name, uint16_t& idx) {
        if (o == Q(0x1000) && name == "fTimer") { idx = 2; return true; }
        return false;
    });
    CHECK(n == 1);
    CHECK(volatileKeys().unresolvedNames() == 0);
    CHECK(volatileKeys().isVolatileVar(Q(0x1000), 2));
    // field exclusions: running / objectives of THESE quests only, stage untouched
    EffectTxn t;
    t.effects = {StartQuest{Q(0x2000)}, SetStage{Q(0x2000), 5}, SetObjective{Q(0x2001), 1, 1}, SetStage{Q(0x2001), 5}, StopQuest{Q(0x2000)}};
    CHECK(normalizeTxn(t, SharePolicy()) == 3);
    CHECK(t.effects.size() == 2);
    ShadowState host, client;
    host.questRunning[Q(0x2000)] = true; host.stages[Q(0x2000)] = 5; host.objectives[{Q(0x2001), 1}] = 3;
    BaselineDiff d = diffBaseline(host, summarizeWorld(client, 1), SharePolicy(), 1, false, 0, false, {});
    int stages = 0, runs = 0, objs = 0;
    for (auto& e : d.effects) { stages += std::holds_alternative<SetStage>(e); runs += std::holds_alternative<StartQuest>(e); objs += std::holds_alternative<SetObjective>(e); }
    CHECK((stages == 1 && runs == 0 && objs == 0));
    // strictness
    CHECK(!volatileKeys().loadFromText("field FalloutNV.esm:0 nonsense\n", &err));
    CHECK(!volatileKeys().loadFromText("global FalloutNV.esm:ZZ\n", &err));
    CHECK(!volatileKeys().loadFromText("whatever FalloutNV.esm:1\n", &err));
    volatileKeys().clear();
}

TEST(baseline, float_noise_is_ignored_in_the_compare_hash_but_values_stay_exact) {
    ShadowState a, b;
    a.scriptVars[{Q(0x1000), 1}] = 8.99999976;
    b.scriptVars[{Q(0x1000), 1}] = 9.00000024;
    a.globals[Q(0x3000)] = 2.49999976f;
    b.globals[Q(0x3000)] = 2.50000024f;
    BaselineSummary sa = summarizeWorld(a, 1), sb = summarizeWorld(b, 1);
    CHECK(sa.entries == sb.entries);                                   // spurious diffs gone
    BaselineDiff same = diffBaseline(a, sb, SharePolicy(), 1, false, 0, false, {});
    CHECK(same.effects.empty());
    // a real change still differs
    b.scriptVars[{Q(0x1000), 1}] = 9.001;
    CHECK(summarizeWorld(a, 1).entries != summarizeWorld(b, 1).entries);
    BaselineDiff d = diffBaseline(a, summarizeWorld(b, 1), SharePolicy(), 1, false, 0, false, {});
    REQUIRE(d.effects.size() == 1);
    // ... and what the joiner receives is the host's exact, unrounded double
    auto* v = std::get_if<SetScriptVar>(&d.effects[0]);
    REQUIRE(v != nullptr);
    CHECK(v->value == 8.99999976);
    // negative zero and tiny values fold to the same hash
    ShadowState z1, z2;
    z1.scriptVars[{Q(1), 0}] = -0.0;
    z2.scriptVars[{Q(1), 0}] = 0.00001;
    CHECK(summarizeWorld(z1, 1).entries == summarizeWorld(z2, 1).entries);
}

TEST(baseline, no_snapshot_flood_after_a_baseline_join_only_ledger_and_uncovered_state) {
    Rig r;
    ShadowState host = bigWorld(300), client = bigWorld(300, 3);
    r.host.setWorldBaseline(host, 7);
    // session history before the join: shared changes, a reward, a barrier release
    r.host.commit(0, Cause{CauseKind::QuestResult, Q(1), 0}, {SetStage{Q(0x1000), 99}, SetScriptVar{Q(0x1000), 1, 5.0}, AddItem{Q(9), 2, Recipient::dflt()},
                                                             ModActorValue{3, 50, Recipient::dflt()}, BarrierArrive{Q(0x77), 0b1}});
    r.cs->setBaselineWorld(client, 7);
    r.cs->connect(r.now, 1);
    r.run(1500);
    REQUIRE(r.cs->baselineDone());
    size_t stage = 0, vars = 0, items = 0, av = 0, rel = 0, other = 0;
    for (auto& e : r.sink.live) {   // everything applied outside the tagged baseline transfer
        if (std::holds_alternative<SetStage>(e)) ++stage;
        else if (std::holds_alternative<SetScriptVar>(e)) ++vars;
        else if (std::holds_alternative<AddItem>(e)) ++items;
        else if (std::holds_alternative<ModActorValue>(e)) ++av;
        else if (std::holds_alternative<BarrierRelease>(e)) ++rel;
        else ++other;
    }
    CHECK(stage == 0);
    CHECK(vars == 0);
    CHECK(items == 1);
    CHECK(av == 1);
    CHECK(rel == 1);
    CHECK(other == 0);
    CHECK(r.sink.live.size() <= 4);
    CHECK(r.cs->state() == r.host.state());
}

TEST(twin, unit_flood) {
    auto untagged = [] {
        Rig r;
        r.host.setWorldBaseline(bigWorld(100), 7);
        r.host.commit(0, Cause{CauseKind::QuestResult, Q(1), 0}, {SetStage{Q(0x1000), 99}, AddItem{Q(9), 1, Recipient::dflt()}});
        r.cs->setBaselineWorld(bigWorld(100, 3), 7);
        r.cs->connect(r.now, 1);
        r.run(1000);
        return r.sink.live.size();
    };
    CHECK(untagged() <= 8);
    Mutations m; m.snapshotFloodAfterBaseline = true;
    MutationScope s(m);
    CHECK(untagged() > 100);
}

TEST(baseline, digest_fast_path_equals_the_entry_based_hash_for_every_entry_kind) {
    ShadowState w = bigWorld(300);
    w.questRunning[Q(1)] = true; w.questRunning[Q(2)] = false; w.questCompleted.insert(Q(3)); w.allObjectivesDone.insert(Q(4));
    w.enabled[Q(5)] = false; w.kills.insert(Q(6)); w.placed[Q(7)] = Placement{Q(8), 1.5f, -2.f, 3.f};
    w.locks[Q(9)] = {true, 50}; w.textures[{Q(10), 2}] = Q(11); w.companionOwner[Q(12)] = 3; w.tethers[Q(13)] = true;
    w.factionRank[{Q(14), Q(15)}] = 2; w.sayOnce.insert(Q(16)); w.slideshowEnded = true; w.saveSuppress = true; w.fastTravelLock[Q(17)] = true;
    CHECK(shardHashesOf(w) == shardHashes(baselineEntries(w)));
    volatileKeys().clear();
    volatileKeys().addVar(Q(0x1000), 0);
    volatileKeys().addGlobal(Q(0x3001));
    volatileKeys().addField(Q(0x1002), VolatileKeys::kFieldStage);
    CHECK(shardHashesOf(w) == shardHashes(baselineEntries(w)));   // also with volatile keys excluded
    ShadowState w2 = w;
    w2.scriptVars[{Q(0x1000), 0}] = 9999.0;                          // volatile: invisible to the hash
    CHECK(shardHashesOf(w) == shardHashesOf(w2));
    w2.stages[Q(0x1003)] += 1;                                       // real change: visible
    CHECK(shardHashesOf(w) != shardHashesOf(w2));
    volatileKeys().clear();
}

TEST(baseline, script_var_values_hash_the_same_whether_parsed_from_text_or_read_as_floats) {
    // the exact values L10 saw as spurious join diffs
    const char* texts[] = {"-0.0160001591", "5.96", "6.01", "5.97", "0.0", "-0.0", "123.4567", "0.00004", "-0.00004", "100000.25"};   // (values exactly on a 1e-4 rounding boundary may still split: documented)
    for (const char* t : texts) {
        double fromText = std::strtod(t, nullptr);
        double fromFloat = static_cast<double>(static_cast<float>(fromText));
        double jitter = fromFloat + 3e-8;
        auto h = [&](double v) {
            ShadowState w;
            w.scriptVars[{Q(0x102037), 7}] = v;
            return summarizeWorld(w, 1).entries;
        };
        CHECK_MSG(h(fromText) == h(fromFloat), t);
        CHECK_MSG(h(fromFloat) == h(jitter), t);
    }
    // a genuinely different value still differs
    ShadowState a, b;
    a.scriptVars[{Q(1), 1}] = 5.96; b.scriptVars[{Q(1), 1}] = 5.9601;
    CHECK(summarizeWorld(a, 1).entries != summarizeWorld(b, 1).entries);
}

// The live-path mechanism behind the "6 spurious script vars": the volatile list names vars by NAME; until resolveNames() runs in a
// process those entries match nothing, so the host's poller commits them into the session while the joiner's dump (which the dumper
// filters by name) lacks them, and the joiner is sent them as if they were missing.
TEST(baseline, unresolved_named_volatile_vars_leak_into_join_diffs_until_resolved) {
    volatileKeys().clear();
    std::string err;
    REQUIRE(volatileKeys().loadFromText("var falloutnv.esm:102037 fTimer\n", &err));
    CHECK(volatileKeys().unresolvedNames() == 1);
    uint64_t declaredHash = volatileKeys().contentHash();
    Rig r;
    ShadowState base = bigWorld(20), client = base;
    r.host.setWorldBaseline(base, 5);
    FormRef owner("falloutnv.esm", 0x102037);
    Cause c; c.kind = CauseKind::Script; c.source = Q(1);
    r.host.commit(0, c, {SetScriptVar{owner, 7, -0.0160001591}});            // the poller commits the volatile var
    r.cs->setBaselineWorld(client, 5);                                          // the joiner's dump excludes it (dumper filter)
    std::string clientView = r.cs->explainSummaryEntry(SetScriptVar{owner, 7, 0});
    r.cs->connect(r.now, 1);
    r.run(800);
    REQUIRE(r.cs->baselineDone());
    size_t leaked = 0;
    for (auto& e : r.sink.baseline) if (auto* v = std::get_if<SetScriptVar>(&e)) leaked += (v->owner == owner);
    CHECK(leaked == 1);                                                         // reproduces the symptom
    CHECK(r.host.explainJoinEntry(1, SetScriptVar{owner, 7, 0}).find("present") != std::string::npos);
    CHECK(clientView.find("absent") != std::string::npos);
    // resolve the name in this process -> commits are dropped, nothing leaks
    size_t n = volatileKeys().resolveNames([&](const FormRef& o, const std::string& name, uint16_t& idx) {
        if (o == owner && name == "fTimer") { idx = 7; return true; }
        return false;
    });
    CHECK(n == 1);
    CHECK(volatileKeys().contentHash() == declaredHash);                        // resolving does not change the handshake hash
    Rig r2;
    r2.host.setWorldBaseline(base, 5);
    r2.host.commit(0, c, {SetScriptVar{owner, 7, -0.0160001591}});
    CHECK(r2.host.state().scriptVars.count({owner, 7}) == 0);                   // dropped at commit
    r2.cs->setBaselineWorld(client, 5);
    r2.cs->connect(r2.now, 1);
    r2.run(800);
    REQUIRE(r2.cs->baselineDone());
    size_t leaked2 = 0;
    for (auto& e : r2.sink.baseline) if (auto* v = std::get_if<SetScriptVar>(&e)) leaked2 += (v->owner == owner);
    CHECK(leaked2 == 0);
    CHECK(r2.host.explainJoinEntry(1, SetScriptVar{owner, 7, 0}).find("VOLATILE") != std::string::npos);
    volatileKeys().clear();
}
