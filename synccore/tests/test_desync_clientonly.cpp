// Issue #25: the desync check never converged in the real game: every repair sent 256 of ~305 effects and the open entries were
// always the same 64 client-only StartQuest/StopQuest keys. This is the game-free version of that shape: more differing entries
// than one repair message may carry (maxRepairEffects = 256), 64 of them only the client has.
//
// What it shows (Linux/fake world, no game):
//  - client-only keys that have an inverse (a kill -> ResurrectActor) converge, also above the 256 cap: the budget is not the cause;
//  - client-only quest running/stopped keys never converged: revertEffectFor() (baseline.cpp) has no inverse for StartQuest/StopQuest,
//    the client skipped such a revert without a trace (session.cpp, SyncRepair), and the host sent it again each round.
//    Fixed (issue #25): the host settles these keys with its OWN running state, committed as a normal host txn (HostSession::hostQuestRunning,
//    default: the completeness rule, a quest the host does not list is not running), and the client now counts a revert it has no inverse
//    for (SyncStats::repairRevertsUnrevertable). Twin: questRunningReverted brings the old revert keys back.
#include "fnvcoop/session.h"
#include "minitest.h"

using namespace fnvcoop;

namespace {
FormRef Q(uint32_t i) { return FormRef("falloutnv.esm", i); }

// The client's engine: applies baseline/repair effects as forced writes, like the Engine in test_desync.cpp.
struct Engine : IEffectSink {
    ShadowState w;
    void apply(const Effect& e, const Context& c) override {
        if (c.baseline) {
            std::visit([&](const auto& x) {
                using T = std::decay_t<decltype(x)>;
                if constexpr (std::is_same_v<T, SetStage>) w.stages[x.quest] = x.stage;
                else { EffectTxn t; t.effects = {e}; w.applyTxn(t); }
            }, e);
        } else {
            EffectTxn t; t.effects = {e}; w.applyTxn(t);
        }
    }
};

constexpr int kDrifted = 241, kClientOnly = 64;   // 305 open entries, as in the issue

enum class OnlyKind { Kill, QuestRunning };

struct Result {
    bool setupOk = false, healed = false;
    uint64_t reports = 0, repairs = 0, effectsRepaired = 0, adopted = 0, clientUnrevertable = 0;
};

Result clientOnlyDrift(OnlyKind only, bool breakHeal, bool revertQuestRunning = false) {
    InMemoryNetwork net{12};
    HostSession host(*net.endpoint(0));
    Engine eng;
    EffectCapture cap;
    ClientSession cs(*net.endpoint(1), 0, 10, eng, &cap);
    cs.setBaselineProvider([&](ShadowState& w, uint64_t& h) { w = eng.w; h = 1; return true; });
    cs.setDigestInterval(200);
    HostSession::DesyncOptions o;
    o.minDigestIntervalMs = 100;
    o.maxRepairEffects = 256;   // the default per repair message
    host.setDesyncOptions(o);
    uint64_t now = 0;
    auto run = [&](uint32_t ms) {
        for (uint32_t t = 0; t < ms; t += 5) { now += 5; net.advance(now); host.tick(now); cs.tick(now); net.advance(now); }
    };
    cs.connect(now, cs.lastSession() + 1);
    run(400);
    for (int b = 0; b < 3; ++b) {
        Cause c; c.kind = CauseKind::Script; c.source = Q(1);
        std::vector<Effect> fx;
        for (int i = 0; i < 100 && b * 100 + i < kDrifted + 10; ++i) fx.push_back(SetStage{Q(0x100 + b * 100 + i), uint32_t(b * 100 + i + 1)});
        host.commit(0, c, fx);
        run(20);
    }
    run(1500);
    Result r;
    auto inSync = [&] { return shardHashes(baselineEntries(eng.w)) == shardHashes(baselineEntries(host.state())); };
    if (!inSync()) return r;
    r.setupOk = true;
    for (int i = 0; i < kDrifted; ++i) eng.w.stages[Q(0x100 + i)] += 5;                    // differing values: fixable by the host
    for (int i = 0; i < kClientOnly; ++i) {                                                // keys only the client has
        if (only == OnlyKind::Kill) eng.w.kills.insert(Q(0x4000 + i));
        else eng.w.questRunning[Q(0x4000 + i)] = (i % 2) == 0;
    }
    Mutations m;
    m.healDisabled = breakHeal;
    m.questRunningReverted = revertQuestRunning;
    MutationScope scope(m);
    for (int i = 0; i < 2000 && !r.healed; ++i) { run(100); r.healed = inSync(); }   // up to 200 virtual seconds
    r.reports = host.desyncs().size();
    r.repairs = host.stats().desyncRepairs;
    r.effectsRepaired = host.stats().desyncEffectsRepaired;
    r.adopted = host.stats().questRunningAdopted;
    r.clientUnrevertable = cs.syncStats().repairRevertsUnrevertable;
    return r;
}
}  // namespace

TEST(desync_clientonly, client_only_keys_with_an_inverse_converge_even_above_the_repair_cap) {
    Result r = clientOnlyDrift(OnlyKind::Kill, false);
    REQUIRE(r.setupOk);
    CHECK(r.healed);
    CHECK(r.repairs >= 2);   // 305 open entries do not fit one repair message (cap 256): it took more than one round
}

// Issue #25: what the real game showed (was KNOWN BUG, WILL_FAIL until fixed).
TEST(desync_clientonly, client_only_quest_running_keys_converge) {
    Result r = clientOnlyDrift(OnlyKind::QuestRunning, false);
    REQUIRE(r.setupOk);
    CHECK_MSG(r.healed, "repairs " + std::to_string(r.repairs) + ", effects sent " + std::to_string(r.effectsRepaired));
    CHECK_MSG(r.adopted == uint64_t(kClientOnly), "adopted " + std::to_string(r.adopted));   // each key settled once, by the host's running state
    CHECK(r.clientUnrevertable == 0);   // nothing went out as a revert the client cannot do
}

// Twin of the #25 fix: client-only quest running keys sent as revert keys again (the old behaviour) never converge, and the client's
// new counter shows why (the revert was silent before).
TEST(twin, unit_desync_clientonly_quest_running_reverted) {
    Result bad = clientOnlyDrift(OnlyKind::QuestRunning, false, true);
    REQUIRE(bad.setupOk);
    CHECK(!bad.healed);
    CHECK(bad.adopted == 0);
    CHECK(bad.clientUnrevertable >= uint64_t(kClientOnly));
}

TEST(twin, unit_desync_clientonly_heal_disabled) {
    Result ok = clientOnlyDrift(OnlyKind::Kill, false);
    CHECK(ok.healed);
    Result bad = clientOnlyDrift(OnlyKind::Kill, true);
    REQUIRE(bad.setupOk);
    CHECK(!bad.healed);       // with repairs off the drift stays ...
    CHECK(bad.reports >= 1);  // ... but is still detected and reported
}
