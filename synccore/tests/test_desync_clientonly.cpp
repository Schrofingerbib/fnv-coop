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

enum class OnlyKind { Kill, QuestRunning, StageZero, StageNonZero, QuestCompleted, QuestFailed, ObjectiveZero, ObjectiveNonZero, AllObjectivesDone, ScriptVarZero, ScriptVarNonZero, GlobalNonZero };

// What the host's engine says about the client-only quest keys (HostSession::hostQuestState / hostObjectives).
enum class HostReader { None, Agrees, Disagrees, Fails };
// What the host engine reads for the client-only script vars: the same value, another value, nothing for any of them (unknown), or the hook itself fails.
enum class VarReader { None, Same, Other, Unknown, Fails };
// What the host engine reads for the client-only globals (value 7 on the client): the same value, another one, nothing for any of them, or the hook fails.
enum class GlobReader { None, Same, Other, Unknown, Fails };
// Rarely varied knobs of clientOnlyDrift. runReader: the host engine's quest running flags (0 no hook = the completeness default, 1 mirrors the client's own
// engine, 2 a hook that cannot read any of them).
struct Extra {
    int runReader = 0;
    GlobReader glob = GlobReader::None;
    bool globalReverted = false, runningBlindStop = false, ackIgnored = false, settleCountsAsRepair = false;
};

struct Result {
    bool setupOk = false, healed = false;
    uint64_t reports = 0, repairs = 0, effectsRepaired = 0, adopted = 0, stageAdopted = 0, clientUnrevertable = 0, stateAdopted = 0, stateDisagreed = 0;
    uint64_t objZeroAdopted = 0, objStateAdopted = 0, objStateDisagreed = 0;
    uint64_t varAdopted = 0, varDisagreed = 0, skippedNoInverse = 0;
    uint64_t idleMismatches = 0, idleEffects = 0, idleDigestsOk = 0;   // counted over 60 virtual seconds AFTER the digest first agreed (idle play) or after the 200 s of healing
    uint64_t settled = 0, acked = 0, gaveUp = 0, globAdopted = 0, globDisagreed = 0, runningUnread = 0;
    size_t runningAfter = 0;   // quests of the drift the CLIENT's engine still has running at the end
    size_t openClientOnly = 0;   // client-only entries still listed in the newest report
};

Result clientOnlyDrift(OnlyKind only, bool breakHeal, bool revertQuestRunning = false, bool revertStageZero = false,
                       HostReader reader = HostReader::None, bool revertQuestState = false, bool revertObjectiveZero = false,
                       bool revertObjectiveState = false, VarReader vreader = VarReader::None, bool revertScriptVar = false,
                       bool noInverseResent = false, int onlyCount = kClientOnly, bool idleCheck = false, const Extra& x = Extra()) {
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
    if (reader != HostReader::None) {
        // The fake host engine: a client-only stage 7 / completed / failed quest is confirmed (stage 9 >= 7, flags equal) or contradicted.
        host.hostQuestState = [reader, only](const std::vector<FormRef>& qs, std::map<FormRef, HostSession::HostQuestState>& out) {
            if (reader == HostReader::Fails) return false;
            for (auto& q : qs) {
                HostSession::HostQuestState s;
                const bool yes = reader == HostReader::Agrees;
                s.stage = yes ? 9 : 3;
                s.completed = yes && (only == OnlyKind::QuestCompleted || only == OnlyKind::QuestFailed);
                s.failed = yes && only == OnlyKind::QuestFailed;
                out[q] = s;
            }
            return true;
        };
    }
    if (reader != HostReader::None && (only == OnlyKind::ObjectiveNonZero || only == OnlyKind::AllObjectivesDone)) {
        // The fake host engine: the client-only objective has flags 3 (displayed + completed) on the host too, or something else.
        host.hostObjectives = [reader](const std::vector<FormRef>& qs, std::map<FormRef, std::map<uint32_t, uint8_t>>& out) {
            if (reader == HostReader::Fails) return false;
            for (auto& q : qs) {
                const bool yes = reader == HostReader::Agrees;
                out[q] = {{1, uint8_t(yes ? 3 : 1)}, {2, uint8_t(yes ? 3 : 0)}};
            }
            return true;
        };
    }
    if (vreader != VarReader::None) {
        // The fake host engine: the client-only var holds the same value (7 for the non-zero kind, 0 for the zero kind), another one, or cannot be read.
        host.hostScriptVars = [vreader, only](const std::vector<std::pair<FormRef, uint16_t>>& vars, std::map<std::pair<FormRef, uint16_t>, double>& out) {
            if (vreader == VarReader::Fails) return false;
            if (vreader == VarReader::Unknown) return true;
            for (auto& v : vars) out[v] = vreader == VarReader::Same ? (only == OnlyKind::ScriptVarNonZero ? 7.0 : 0.0) : 99.0;
            return true;
        };
    }
    if (x.runReader != 0) {
        // The fake host engine's running flags: the same save as the client's engine (even index running), or unreadable.
        host.hostQuestRunning = [mode = x.runReader](const std::vector<FormRef>& qs, std::map<FormRef, bool>& out) {
            if (mode == 2) return true;
            for (auto& q : qs) out[q] = ((q.localId - 0x4000) % 2) == 0;
            return true;
        };
    }
    if (x.glob != GlobReader::None) {
        host.hostGlobals = [g = x.glob](const std::vector<FormRef>& gl, std::map<FormRef, double>& out) {
            if (g == GlobReader::Fails) return false;
            if (g == GlobReader::Unknown) return true;
            for (auto& q : gl) out[q] = g == GlobReader::Same ? 7.0 : 99.0;
            return true;
        };
    }
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
    for (int i = 0; i < onlyCount; ++i) {                                                // keys only the client has
        if (only == OnlyKind::Kill) eng.w.kills.insert(Q(0x4000 + i));
        else if (only == OnlyKind::StageZero) eng.w.stages[Q(0x4000 + i)] = 0;   // quest not started on the client, absent on the host
        else if (only == OnlyKind::StageNonZero) eng.w.stages[Q(0x4000 + i)] = 7;   // real save state both engines loaded, never recorded by the host
        else if (only == OnlyKind::QuestCompleted) eng.w.questCompleted.insert(Q(0x4000 + i));
        else if (only == OnlyKind::QuestFailed) { eng.w.questCompleted.insert(Q(0x4000 + i)); eng.w.questFailed.insert(Q(0x4000 + i)); }
        else if (only == OnlyKind::ObjectiveZero) eng.w.objectives[{Q(0x4000 + i), 10}] = 0;   // objective neither displayed nor completed, absent on the host
        else if (only == OnlyKind::ObjectiveNonZero) eng.w.objectives[{Q(0x4000 + i), 1}] = 3;
        else if (only == OnlyKind::ScriptVarZero) eng.w.scriptVars[{Q(0x4000 + i), uint16_t(i % 5)}] = 0.0;   // an untouched quest var, never recorded by the baseline-less host
        else if (only == OnlyKind::ScriptVarNonZero) eng.w.scriptVars[{Q(0x4000 + i), uint16_t(i % 5)}] = 7.0;   // a var the save holds at 7
        else if (only == OnlyKind::GlobalNonZero) eng.w.globals[Q(0x4000 + i)] = 7.0f;   // a global the save holds at 7
        else if (only == OnlyKind::AllObjectivesDone) { eng.w.objectives[{Q(0x4000 + i), 1}] = 3; eng.w.objectives[{Q(0x4000 + i), 2}] = 3; eng.w.allObjectivesDone.insert(Q(0x4000 + i)); }
        else eng.w.questRunning[Q(0x4000 + i)] = (i % 2) == 0;
    }
    Mutations m;
    m.questStateReverted = revertQuestState;
    m.healDisabled = breakHeal;
    m.questRunningReverted = revertQuestRunning;
    m.stageZeroReverted = revertStageZero;
    m.objectiveZeroReverted = revertObjectiveZero;
    m.objectiveStateReverted = revertObjectiveState;
    m.scriptVarReverted = revertScriptVar;
    m.noInverseResent = noInverseResent;
    m.globalReverted = x.globalReverted;
    m.runningBlindStop = x.runningBlindStop;
    m.ackIgnored = x.ackIgnored;
    m.settleCountsAsRepair = x.settleCountsAsRepair;
    MutationScope scope(m);
    for (int i = 0; i < 2000 && !r.healed; ++i) { run(100); r.healed = inSync(); }   // up to 200 virtual seconds
    if (idleCheck) {
        const uint64_t mis = host.stats().digestsMismatch, eff = host.stats().desyncEffectsRepaired, ok = host.stats().digestsOk;
        run(60000);
        r.idleMismatches = host.stats().digestsMismatch - mis;
        r.idleEffects = host.stats().desyncEffectsRepaired - eff;
        r.idleDigestsOk = host.stats().digestsOk - ok;
    }
    r.reports = host.desyncs().size();
    r.repairs = host.stats().desyncRepairs;
    r.effectsRepaired = host.stats().desyncEffectsRepaired;
    r.adopted = host.stats().questRunningAdopted;
    r.stageAdopted = host.stats().stageZeroAdopted;
    r.clientUnrevertable = cs.syncStats().repairRevertsUnrevertable;
    r.stateAdopted = host.stats().questStateAdopted;
    r.stateDisagreed = host.stats().questStateDisagreed;
    r.objZeroAdopted = host.stats().objectiveZeroAdopted;
    r.objStateAdopted = host.stats().objectiveStateAdopted;
    r.objStateDisagreed = host.stats().objectiveStateDisagreed;
    r.varAdopted = host.stats().scriptVarAdopted;
    r.varDisagreed = host.stats().scriptVarDisagreed;
    r.skippedNoInverse = host.stats().revertsSkippedNoInverse;
    r.settled = host.stats().effectsSettled;
    r.acked = host.stats().divergenceAcked;
    r.gaveUp = host.stats().repairGaveUp;
    r.globAdopted = host.stats().globalAdopted;
    r.globDisagreed = host.stats().globalDisagreed;
    r.runningUnread = host.stats().runningUnread;
    for (int i = 0; i < onlyCount; ++i) { auto it = eng.w.questRunning.find(Q(0x4000 + i)); if (it != eng.w.questRunning.end() && it->second) ++r.runningAfter; }
    // client-only entries the host listed at all (a settled / acknowledged key is reported in the round that handled it, then the digest agrees)
    for (auto& rp : host.desyncs())
        for (auto& e : rp.entries) r.openClientOnly += e.clientOnly ? 1 : 0;
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

// The real game's other open key class: 275 "SetStage q = 0" keys only the client has. SetStage merges by max and has no inverse, so the
// old revert keys were skipped by the client and resent every round (a repair of ~200 effects each time, the host's digest never ok).
TEST(desync_clientonly, client_only_stage_zero_keys_converge) {
    Result r = clientOnlyDrift(OnlyKind::StageZero, false);
    REQUIRE(r.setupOk);
    CHECK_MSG(r.healed, "repairs " + std::to_string(r.repairs) + ", effects sent " + std::to_string(r.effectsRepaired));
    CHECK_MSG(r.stageAdopted == uint64_t(kClientOnly), "stage-zero adopted " + std::to_string(r.stageAdopted));   // each key settled once
}

// Twin: stage-0 keys sent as revert keys again (the old behaviour) never converge.
TEST(twin, unit_desync_clientonly_stage_zero_reverted) {
    Result bad = clientOnlyDrift(OnlyKind::StageZero, false, false, true);
    REQUIRE(bad.setupOk);
    CHECK(!bad.healed);
    CHECK(bad.stageAdopted == 0);
}

// The real game's remaining open keys: real save state both engines loaded (SetStage q = 5, CompleteQuest) that the baseline-less host never
// recorded. The host settles them after ITS OWN engine confirms the value (HostSession::hostQuestState).
TEST(desync_clientonly, client_only_nonzero_stage_keys_converge_when_the_host_engine_agrees) {
    Result r = clientOnlyDrift(OnlyKind::StageNonZero, false, false, false, HostReader::Agrees);
    REQUIRE(r.setupOk);
    CHECK_MSG(r.healed, "repairs " + std::to_string(r.repairs) + ", effects sent " + std::to_string(r.effectsRepaired));
    CHECK_MSG(r.stateAdopted == uint64_t(kClientOnly), "adopted " + std::to_string(r.stateAdopted));
    CHECK(r.stateDisagreed == 0);
    CHECK(r.clientUnrevertable == 0);
}

TEST(desync_clientonly, client_only_completed_quest_keys_converge_when_the_host_engine_agrees) {
    Result r = clientOnlyDrift(OnlyKind::QuestCompleted, false, false, false, HostReader::Agrees);
    REQUIRE(r.setupOk);
    CHECK_MSG(r.healed, "repairs " + std::to_string(r.repairs) + ", effects sent " + std::to_string(r.effectsRepaired));
    CHECK_MSG(r.stateAdopted == uint64_t(kClientOnly), "adopted " + std::to_string(r.stateAdopted));
    CHECK(r.clientUnrevertable == 0);
}

TEST(desync_clientonly, client_only_failed_quest_keys_converge_when_the_host_engine_agrees) {
    Result r = clientOnlyDrift(OnlyKind::QuestFailed, false, false, false, HostReader::Agrees);
    REQUIRE(r.setupOk);
    CHECK_MSG(r.healed, "repairs " + std::to_string(r.repairs) + ", effects sent " + std::to_string(r.effectsRepaired));
    CHECK_MSG(r.stateAdopted == uint64_t(kClientOnly), "adopted " + std::to_string(r.stateAdopted));
}

// A host engine that contradicts the client (lower stage, quest not completed): a real divergence. Nothing is adopted, the host world is
// untouched, and the entries stay open and are reported every round.
TEST(desync_clientonly, client_only_quest_state_the_host_engine_disagrees_with_is_not_adopted) {
    for (OnlyKind k : {OnlyKind::StageNonZero, OnlyKind::QuestCompleted, OnlyKind::QuestFailed}) {
        Result r = clientOnlyDrift(k, false, false, false, HostReader::Disagrees);
        REQUIRE(r.setupOk);
        CHECK(!r.healed);
        CHECK_MSG(r.stateAdopted == 0, "adopted " + std::to_string(r.stateAdopted));
        CHECK(r.stateDisagreed >= uint64_t(kClientOnly));
        CHECK(r.acked > 0);   // acknowledged: listed in the report once, then no longer a mismatch
        CHECK(r.clientUnrevertable == 0);   // and not resent as a revert the client cannot do
    }
}

// A reader that cannot read leaves the old behaviour (nothing is adopted blindly).
TEST(desync_clientonly, client_only_quest_state_without_a_working_host_reader_adopts_nothing) {
    for (HostReader hr : {HostReader::None, HostReader::Fails}) {
        Result r = clientOnlyDrift(OnlyKind::StageNonZero, false, false, false, hr);
        REQUIRE(r.setupOk);
        CHECK(!r.healed);
        CHECK(r.stateAdopted == 0);
    }
}

// Twin: the non-zero stage / completed keys sent as revert keys again (the old behaviour) never converge, the client cannot undo them.
TEST(twin, unit_desync_clientonly_quest_state_reverted) {
    for (OnlyKind k : {OnlyKind::StageNonZero, OnlyKind::QuestCompleted}) {
        Result ok = clientOnlyDrift(k, false, false, false, HostReader::Agrees);
        CHECK(ok.healed);
        Result bad = clientOnlyDrift(k, false, false, false, HostReader::Agrees, true);
        REQUIRE(bad.setupOk);
        CHECK(!bad.healed);
        CHECK(bad.stateAdopted == 0);
        CHECK(bad.clientUnrevertable >= uint64_t(kClientOnly));
    }
}

// The next open key class in the real game: 100+ client-only "SetObjective q#n flags 0" keys. Both engines loaded the same save and have the
// objective (not displayed, not completed), the baseline-less host's shared world never recorded it. SetObjective has no inverse, so the old
// revert keys were skipped by the client and resent every round. The host commits the same flags 0 (no engine read needed: it is the default).
TEST(desync_clientonly, client_only_objective_zero_keys_converge) {
    Result r = clientOnlyDrift(OnlyKind::ObjectiveZero, false);
    REQUIRE(r.setupOk);
    CHECK_MSG(r.healed, "repairs " + std::to_string(r.repairs) + ", effects sent " + std::to_string(r.effectsRepaired));
    CHECK_MSG(r.objZeroAdopted == uint64_t(kClientOnly), "objective-zero adopted " + std::to_string(r.objZeroAdopted));   // each key settled once
    CHECK(r.clientUnrevertable == 0);
}

// Twin: objective flags 0 keys sent as revert keys again (the old behaviour) never converge.
TEST(twin, unit_desync_clientonly_objective_zero_reverted) {
    Result bad = clientOnlyDrift(OnlyKind::ObjectiveZero, false, false, false, HostReader::None, false, true);
    REQUIRE(bad.setupOk);
    CHECK(!bad.healed);
    CHECK(bad.objZeroAdopted == 0);
    CHECK(bad.clientUnrevertable >= uint64_t(kClientOnly));
}

// Objectives with real state (displayed / completed, all objectives completed): adopted only where the host engine shows the same flags.
TEST(desync_clientonly, client_only_nonzero_objective_keys_converge_when_the_host_engine_agrees) {
    Result r = clientOnlyDrift(OnlyKind::ObjectiveNonZero, false, false, false, HostReader::Agrees);
    REQUIRE(r.setupOk);
    CHECK_MSG(r.healed, "repairs " + std::to_string(r.repairs) + ", effects sent " + std::to_string(r.effectsRepaired));
    CHECK_MSG(r.objStateAdopted == uint64_t(kClientOnly), "adopted " + std::to_string(r.objStateAdopted));
    CHECK(r.objStateDisagreed == 0);
    CHECK(r.clientUnrevertable == 0);
}

TEST(desync_clientonly, client_only_all_objectives_done_keys_converge_when_the_host_engine_agrees) {
    Result r = clientOnlyDrift(OnlyKind::AllObjectivesDone, false, false, false, HostReader::Agrees);
    REQUIRE(r.setupOk);
    CHECK_MSG(r.healed, "repairs " + std::to_string(r.repairs) + ", effects sent " + std::to_string(r.effectsRepaired));
    CHECK_MSG(r.objStateAdopted == uint64_t(kClientOnly) * 3, "adopted " + std::to_string(r.objStateAdopted));   // two objectives + the all-done key per quest
    CHECK(r.clientUnrevertable == 0);
}

// A host engine with different flags (or a quest that is not fully done): a real divergence. Nothing is adopted and the entries stay reported.
TEST(desync_clientonly, client_only_objective_state_the_host_engine_disagrees_with_is_not_adopted) {
    for (OnlyKind k : {OnlyKind::ObjectiveNonZero, OnlyKind::AllObjectivesDone}) {
        Result r = clientOnlyDrift(k, false, false, false, HostReader::Disagrees);
        REQUIRE(r.setupOk);
        CHECK(!r.healed);
        CHECK_MSG(r.objStateAdopted == 0, "adopted " + std::to_string(r.objStateAdopted));
        CHECK(r.objStateDisagreed >= uint64_t(kClientOnly));
        CHECK(r.acked > 0);   // acknowledged: listed in the report once, then no longer a mismatch
        CHECK(r.clientUnrevertable == 0);
    }
}

// A reader that cannot read leaves the old behaviour (nothing is adopted blindly).
TEST(desync_clientonly, client_only_objective_state_without_a_working_host_reader_adopts_nothing) {
    for (HostReader hr : {HostReader::None, HostReader::Fails}) {
        Result r = clientOnlyDrift(OnlyKind::ObjectiveNonZero, false, false, false, hr);
        REQUIRE(r.setupOk);
        CHECK(!r.healed);
        CHECK(r.objStateAdopted == 0);
    }
}

// Twin: non-zero objective / all-done keys sent as revert keys again (the old behaviour) never converge.
TEST(twin, unit_desync_clientonly_objective_state_reverted) {
    for (OnlyKind k : {OnlyKind::ObjectiveNonZero, OnlyKind::AllObjectivesDone}) {
        Result ok = clientOnlyDrift(k, false, false, false, HostReader::Agrees);
        CHECK(ok.healed);
        Result bad = clientOnlyDrift(k, false, false, false, HostReader::Agrees, false, false, true);
        REQUIRE(bad.setupOk);
        CHECK(!bad.healed);
        CHECK(bad.objStateAdopted == 0);
        CHECK(bad.clientUnrevertable >= uint64_t(kClientOnly));
    }
}

TEST(twin, unit_desync_clientonly_heal_disabled) {
    Result ok = clientOnlyDrift(OnlyKind::Kill, false);
    CHECK(ok.healed);
    Result bad = clientOnlyDrift(OnlyKind::Kill, true);
    REQUIRE(bad.setupOk);
    CHECK(!bad.healed);       // with repairs off the drift stays ...
    CHECK(bad.reports >= 1);  // ... but is still detected and reported
}

// The 2026-10-26 two-PC benchmark: ~300 client-only "SetScriptVar quest var n = 0" keys (every quest var the client's dump lists, which the
// baseline-less host primed and dropped), 256 revert keys per digest round, 1180 repair effects per minute and a mismatch in every phase,
// idle included. An untouched default is the absent key's own meaning: the host commits the same 0 once and the digests agree. The key index
// is i % 5, so the 64 keys span several vars per quest.
TEST(desync_clientonly, client_only_zero_script_vars_converge_without_any_reader) {
    Result r = clientOnlyDrift(OnlyKind::ScriptVarZero, false);
    REQUIRE(r.setupOk);
    CHECK_MSG(r.healed, "repairs " + std::to_string(r.repairs) + ", effects sent " + std::to_string(r.effectsRepaired));
    CHECK_MSG(r.varAdopted == uint64_t(kClientOnly), "adopted " + std::to_string(r.varAdopted));   // each key settled once
    CHECK(r.clientUnrevertable == 0);   // nothing went out as a revert the client cannot do
    CHECK(r.skippedNoInverse == 0);
}

TEST(desync_clientonly, client_only_zero_script_vars_the_host_engine_shows_at_zero_converge) {
    for (VarReader vr : {VarReader::Same, VarReader::Unknown, VarReader::Fails}) {
        Result r = clientOnlyDrift(OnlyKind::ScriptVarZero, false, false, false, HostReader::None, false, false, false, vr);
        REQUIRE(r.setupOk);
        CHECK(r.healed);
        CHECK(r.varAdopted == uint64_t(kClientOnly));
        CHECK(r.varDisagreed == 0);
    }
}

// A host engine that holds another value for the var: a real divergence, never papered over with a committed 0.
TEST(desync_clientonly, client_only_zero_script_vars_the_host_engine_holds_differently_are_not_adopted) {
    Result r = clientOnlyDrift(OnlyKind::ScriptVarZero, false, false, false, HostReader::None, false, false, false, VarReader::Other);
    REQUIRE(r.setupOk);
    CHECK(!r.healed);
    CHECK_MSG(r.varAdopted == 0, "adopted " + std::to_string(r.varAdopted));
    CHECK(r.varDisagreed >= uint64_t(kClientOnly));
    CHECK(r.acked > 0);   // acknowledged: listed in the report once, then no longer a mismatch
    CHECK(r.clientUnrevertable == 0);   // and not resent as a revert the client cannot do
}

// Twin: zero script vars sent as revert keys again (the old behaviour) never converge, and the client counts every one it cannot undo.
TEST(twin, unit_desync_clientonly_script_var_reverted) {
    Result bad = clientOnlyDrift(OnlyKind::ScriptVarZero, false, false, false, HostReader::None, false, false, false, VarReader::None, true);
    REQUIRE(bad.setupOk);
    CHECK(!bad.healed);
    CHECK(bad.varAdopted == 0);
    CHECK(bad.clientUnrevertable >= uint64_t(kClientOnly));
}

// Save state the host never recorded (a var at 7): adopted only where the host's own engine reads the same value.
TEST(desync_clientonly, client_only_nonzero_script_vars_converge_when_the_host_engine_agrees) {
    Result r = clientOnlyDrift(OnlyKind::ScriptVarNonZero, false, false, false, HostReader::None, false, false, false, VarReader::Same);
    REQUIRE(r.setupOk);
    CHECK_MSG(r.healed, "repairs " + std::to_string(r.repairs) + ", effects sent " + std::to_string(r.effectsRepaired));
    CHECK(r.varAdopted == uint64_t(kClientOnly));
    CHECK(r.varDisagreed == 0);
    CHECK(r.clientUnrevertable == 0);
}

TEST(desync_clientonly, client_only_nonzero_script_vars_the_host_engine_disagrees_with_are_not_adopted) {
    Result r = clientOnlyDrift(OnlyKind::ScriptVarNonZero, false, false, false, HostReader::None, false, false, false, VarReader::Other);
    REQUIRE(r.setupOk);
    CHECK(!r.healed);
    CHECK(r.varAdopted == 0);
    CHECK(r.varDisagreed >= uint64_t(kClientOnly));
    CHECK(r.acked > 0);   // acknowledged: listed in the report once, then no longer a mismatch
    CHECK(r.clientUnrevertable == 0);
}

// No reader, a failing reader or a reader that cannot read the owner: nothing non-zero is adopted blindly, and (the repair loop must not
// resend the same keys every round) nothing goes out as a revert key either: the client has no inverse for a script var.
TEST(desync_clientonly, client_only_nonzero_script_vars_without_a_host_reader_adopt_nothing_and_resend_nothing) {
    for (VarReader vr : {VarReader::None, VarReader::Fails, VarReader::Unknown}) {
        Result r = clientOnlyDrift(OnlyKind::ScriptVarNonZero, false, false, false, HostReader::None, false, false, false, vr);
        REQUIRE(r.setupOk);
        CHECK(!r.healed);
        CHECK(r.varAdopted == 0);
        CHECK(r.clientUnrevertable == 0);
    }
}

// The repair loop itself: every kind of client-only entry without an inverse (here a non-zero stage and a non-zero script var, with no reader)
// used to be a revert key sent again each round (256 effects per 10 s). It is counted and never sent; the 241 drifted stages that the host CAN
// fix are repaired once and stay fixed, so the effects sent stay near the drift, not rounds x 256. Keys the host can neither adopt nor repair
// are acknowledged once (reported, counted), after which the digest agrees again instead of mismatching every round.
TEST(desync_clientonly, unrevertable_client_only_keys_are_not_resent_every_round) {
    for (OnlyKind k : {OnlyKind::ScriptVarNonZero, OnlyKind::StageNonZero}) {
        Result r = clientOnlyDrift(k, false, false, false, HostReader::None, false, false, false, VarReader::None, false, false, kClientOnly, true);
        REQUIRE(r.setupOk);
        CHECK(!r.healed);                  // nothing can repair these without a host reader
        CHECK(r.reports >= 1);
        CHECK_MSG(r.effectsRepaired < 2 * uint64_t(kDrifted), "effects repaired " + std::to_string(r.effectsRepaired) + " in " + std::to_string(r.reports) + " reports");
        CHECK(r.skippedNoInverse >= uint64_t(kClientOnly));
        CHECK(r.clientUnrevertable == 0);
        CHECK_MSG(r.acked == uint64_t(kClientOnly), "acknowledged " + std::to_string(r.acked));
        CHECK_MSG(r.idleMismatches == 0 && r.idleEffects == 0 && r.idleDigestsOk > 5,
                  "idle: " + std::to_string(r.idleMismatches) + " mismatches, " + std::to_string(r.idleEffects) + " effects, " + std::to_string(r.idleDigestsOk) + " ok");
    }
}

// Twin: the revert keys without an inverse sent every round again (the old behaviour) are resent without end.
TEST(twin, unit_desync_clientonly_no_inverse_resent) {
    Result bad = clientOnlyDrift(OnlyKind::ScriptVarNonZero, false, false, false, HostReader::None, false, false, false, VarReader::None, false, true);
    REQUIRE(bad.setupOk);
    CHECK(!bad.healed);
    CHECK(bad.skippedNoInverse == 0);
    CHECK(bad.clientUnrevertable >= uint64_t(kClientOnly));
    CHECK(bad.effectsRepaired >= 2 * uint64_t(kClientOnly));
}

// The benchmark's shape: 300 client-only "SetScriptVar quest var n = 0" keys (more than one 256-effect repair message holds) next to the
// 241 drifted stages. Once the digest agrees, idle play must show 0 mismatches and 0 repair effects (it showed 5.8 and 1180 per minute),
// with a digest that keeps arriving and agreeing. (The real keys: 71622 var 1, 135F63 var 7, 1349A7 var 5, CEF3A vars 8 / 24 / 40, EFB3B var 7 ...)
TEST(desync_clientonly, idle_play_after_the_benchmark_shaped_script_var_drift_has_no_mismatch_and_no_repair) {
    Result r = clientOnlyDrift(OnlyKind::ScriptVarZero, false, false, false, HostReader::None, false, false, false, VarReader::None, false, false, 300, true);
    REQUIRE(r.setupOk);
    CHECK_MSG(r.healed, "repairs " + std::to_string(r.repairs) + ", effects sent " + std::to_string(r.effectsRepaired));
    CHECK_MSG(r.varAdopted == 300, "adopted " + std::to_string(r.varAdopted));
    CHECK_MSG(r.idleMismatches == 0, "idle mismatches " + std::to_string(r.idleMismatches));
    CHECK_MSG(r.idleEffects == 0, "idle repair effects " + std::to_string(r.idleEffects));
    CHECK_MSG(r.idleDigestsOk >= 3, "idle ok digests " + std::to_string(r.idleDigestsOk));
}

// Twin: the old revert keys keep the digest open in idle play, a repair of 256 effects in every round.
TEST(twin, unit_desync_clientonly_script_var_idle_mismatch) {
    Result bad = clientOnlyDrift(OnlyKind::ScriptVarZero, false, false, false, HostReader::None, false, false, false, VarReader::None, true, false, 300, true);
    REQUIRE(bad.setupOk);
    CHECK(!bad.healed);
    CHECK(bad.effectsRepaired >= 256 * 3);   // at least three full rounds of the same 256 revert keys
}

// ---- the 2026-10-27 two-PC benchmark: what was left after the script var fix ----

// Keys the host's own engine contradicts (the benchmark's 8 completed quests the host engine did not show completed) are a real divergence the
// host can neither adopt nor repair. They used to keep the digest mismatching every round for ever (5.9 per minute, never ok). They are
// acknowledged once: reported, counted, never resent, and the digest agrees on everything else again. A change on either side withdraws it.
TEST(desync_clientonly, known_divergences_are_acknowledged_once_and_the_digest_goes_ok) {
    {
        Result r = clientOnlyDrift(OnlyKind::QuestCompleted, false, false, false, HostReader::Disagrees, false, false, false, VarReader::None, false, false, kClientOnly, true);
        REQUIRE(r.setupOk);
        CHECK(!r.healed);   // the engines really differ ...
        CHECK_MSG(r.acked == uint64_t(kClientOnly), "acknowledged " + std::to_string(r.acked));
        CHECK_MSG(r.idleMismatches == 0 && r.idleEffects == 0 && r.idleDigestsOk > 5,   // ... but it is no longer a mismatch in every round
                  "idle: " + std::to_string(r.idleMismatches) + " mismatches, " + std::to_string(r.idleEffects) + " effects, " + std::to_string(r.idleDigestsOk) + " ok");
        CHECK(r.acked > 0);   // acknowledged: listed in the report once, then no longer a mismatch
    }
    {
        Result r = clientOnlyDrift(OnlyKind::ScriptVarNonZero, false, false, false, HostReader::None, false, false, false, VarReader::Other, false, false, kClientOnly, true);
        REQUIRE(r.setupOk);
        CHECK(r.acked == uint64_t(kClientOnly));
        CHECK(r.idleMismatches == 0 && r.idleDigestsOk > 5);
    }
    {
        Result r = clientOnlyDrift(OnlyKind::StageNonZero, false, false, false, HostReader::Disagrees, false, false, false, VarReader::None, false, false, kClientOnly, true);
        REQUIRE(r.setupOk);
        CHECK(r.acked == uint64_t(kClientOnly));
        CHECK(r.idleMismatches == 0 && r.idleDigestsOk > 5);
    }
}

// Twin: no acknowledgement. The key stays a mismatch in every digest round, as in the benchmark (6 per minute, never ok).
TEST(twin, unit_desync_clientonly_known_divergence_never_acknowledged) {
    Extra x; x.ackIgnored = true;
    Result bad = clientOnlyDrift(OnlyKind::QuestCompleted, false, false, false, HostReader::Disagrees, false, false, false, VarReader::None, false, false, kClientOnly, true, x);
    REQUIRE(bad.setupOk);
    CHECK(bad.acked == 0);
    CHECK_MSG(bad.idleMismatches > 20 && bad.idleDigestsOk == 0, "idle: " + std::to_string(bad.idleMismatches) + " mismatches, " + std::to_string(bad.idleDigestsOk) + " ok");
}

// An acknowledgement is withdrawn when either side moves on: here the client's value changes, the digest mismatches again and the key is
// looked at afresh (and, the engine still disagreeing, acknowledged again).
TEST(desync_clientonly, an_acknowledged_divergence_is_withdrawn_when_the_client_value_changes) {
    InMemoryNetwork net{12};
    HostSession host(*net.endpoint(0));
    Engine eng;
    EffectCapture cap;
    ClientSession cs(*net.endpoint(1), 0, 10, eng, &cap);
    cs.setBaselineProvider([&](ShadowState& w, uint64_t& h) { w = eng.w; h = 1; return true; });
    cs.setDigestInterval(200);
    HostSession::DesyncOptions o;
    o.minDigestIntervalMs = 100;
    host.setDesyncOptions(o);
    host.hostQuestState = [](const std::vector<FormRef>& qs, std::map<FormRef, HostSession::HostQuestState>& out) {
        for (auto& q : qs) { HostSession::HostQuestState s; s.stage = 1; out[q] = s; }   // the host engine is at stage 1: the client's stage 7 is a divergence
        return true;
    };
    uint64_t now = 0;
    auto run = [&](uint32_t ms) { for (uint32_t t = 0; t < ms; t += 5) { now += 5; net.advance(now); host.tick(now); cs.tick(now); net.advance(now); } };
    cs.connect(now, cs.lastSession() + 1);
    run(600);
    eng.w.stages[Q(0x5000)] = 7;
    run(3000);
    CHECK(host.stats().divergenceAcked == 1);
    const uint64_t okBefore = host.stats().digestsOk, misBefore = host.stats().digestsMismatch;
    run(2000);
    CHECK(host.stats().digestsOk > okBefore + 3);
    CHECK(host.stats().digestsMismatch == misBefore);
    eng.w.stages[Q(0x5000)] = 8;   // the client moves on: a new value, a new look
    run(3000);
    CHECK(host.stats().ackDropped >= 1);
    CHECK(host.stats().divergenceAcked == 2);
    CHECK(host.stats().digestsMismatch > misBefore);
}

// Client-only globals: the baseline-less host only records globals that changed since the session started. Adopted where the host engine
// holds the same value; a different value is acknowledged; no reader is never resent as a revert key.
TEST(desync_clientonly, client_only_globals_converge_when_the_host_engine_agrees) {
    Extra x; x.glob = GlobReader::Same;
    Result r = clientOnlyDrift(OnlyKind::GlobalNonZero, false, false, false, HostReader::None, false, false, false, VarReader::None, false, false, kClientOnly, false, x);
    REQUIRE(r.setupOk);
    CHECK_MSG(r.healed, "repairs " + std::to_string(r.repairs) + ", effects sent " + std::to_string(r.effectsRepaired));
    CHECK_MSG(r.globAdopted == uint64_t(kClientOnly), "adopted " + std::to_string(r.globAdopted));
    CHECK(r.clientUnrevertable == 0);
    CHECK(r.effectsRepaired < uint64_t(kDrifted) + 10);
}

TEST(desync_clientonly, client_only_globals_the_host_engine_disagrees_with_or_cannot_read_are_not_adopted_and_not_resent) {
    for (GlobReader g : {GlobReader::None, GlobReader::Other, GlobReader::Fails, GlobReader::Unknown}) {
        Extra x; x.glob = g;
        Result r = clientOnlyDrift(OnlyKind::GlobalNonZero, false, false, false, HostReader::None, false, false, false, VarReader::None, false, false, kClientOnly, true, x);
        REQUIRE(r.setupOk);
        CHECK(!r.healed);
        CHECK(r.globAdopted == 0);
        CHECK(r.clientUnrevertable == 0);
        CHECK_MSG(r.acked == uint64_t(kClientOnly), "acknowledged " + std::to_string(r.acked));   // Unknown: after kUnreadRounds rounds
        CHECK(r.idleMismatches == 0 && r.idleDigestsOk > 5);
    }
}

TEST(twin, unit_desync_clientonly_global_reverted) {
    Extra x; x.glob = GlobReader::Same; x.globalReverted = true;
    Result bad = clientOnlyDrift(OnlyKind::GlobalNonZero, false, false, false, HostReader::None, false, false, false, VarReader::None, false, false, kClientOnly, false, x);
    REQUIRE(bad.setupOk);
    CHECK(!bad.healed);
    CHECK(bad.globAdopted == 0);
    CHECK(bad.clientUnrevertable >= uint64_t(kClientOnly));
}

// The 2026-10-27 benchmark's worst find: the baseline-less host had no running-flag reader, so it committed "not running" for every client-only
// quest key, including the ~190 quests the client had running. Its client applied that txn and STOPPED them. With a host engine reader the
// host commits what its own engine shows (running stays running), and a quest it cannot read is left alone.
TEST(desync_clientonly, a_host_engine_reader_keeps_the_clients_running_quests_running) {
    Extra x; x.runReader = 1;
    Result r = clientOnlyDrift(OnlyKind::QuestRunning, false, false, false, HostReader::None, false, false, false, VarReader::None, false, false, kClientOnly, false, x);
    REQUIRE(r.setupOk);
    CHECK(r.healed);
    CHECK_MSG(r.adopted == uint64_t(kClientOnly), "adopted " + std::to_string(r.adopted));
    CHECK_MSG(r.runningAfter == size_t(kClientOnly / 2), "the client still runs " + std::to_string(r.runningAfter) + " of " + std::to_string(kClientOnly / 2));
}

TEST(twin, unit_desync_clientonly_host_stops_the_clients_running_quests_blindly) {
    Extra x; x.runReader = 1; x.runningBlindStop = true;
    Result bad = clientOnlyDrift(OnlyKind::QuestRunning, false, false, false, HostReader::None, false, false, false, VarReader::None, false, false, kClientOnly, false, x);
    REQUIRE(bad.setupOk);
    CHECK_MSG(bad.runningAfter == 0, "the client still runs " + std::to_string(bad.runningAfter));   // every running quest was stopped by the host's txn
}

TEST(desync_clientonly, quests_the_host_engine_cannot_read_are_left_alone_then_acknowledged) {
    Extra x; x.runReader = 2;
    Result r = clientOnlyDrift(OnlyKind::QuestRunning, false, false, false, HostReader::None, false, false, false, VarReader::None, false, false, kClientOnly, true, x);
    REQUIRE(r.setupOk);
    CHECK(r.adopted == 0);
    CHECK(r.runningAfter == size_t(kClientOnly / 2));   // nothing was stopped
    CHECK(r.runningUnread >= uint64_t(kClientOnly));
    CHECK(r.acked == uint64_t(kClientOnly));
    CHECK(r.idleMismatches == 0 && r.idleDigestsOk > 5);
}

// What counts as repair: settling the keys the host never recorded is bookkeeping done once, not drift repair. The benchmark's 744 "repair
// effects per minute" were ~5000 settled keys plus a few real fixes. Repair effects stay at the real drift; the settled keys are counted apart.
TEST(desync_clientonly, settled_keys_are_not_counted_as_repair_effects) {
    Result r = clientOnlyDrift(OnlyKind::StageZero, false, false, false, HostReader::None, false, false, false, VarReader::None, false, false, 600);
    REQUIRE(r.setupOk);
    CHECK(r.healed);
    CHECK_MSG(r.settled == 600, "settled " + std::to_string(r.settled));
    CHECK_MSG(r.effectsRepaired < uint64_t(kDrifted) + 10, "repair effects " + std::to_string(r.effectsRepaired));   // only the 241 drifted stages
}

TEST(twin, unit_desync_clientonly_settled_keys_counted_as_repair_effects) {
    Extra x; x.settleCountsAsRepair = true;
    Result bad = clientOnlyDrift(OnlyKind::StageZero, false, false, false, HostReader::None, false, false, false, VarReader::None, false, false, 600, false, x);
    REQUIRE(bad.setupOk);
    CHECK(bad.effectsRepaired >= 600 + uint64_t(kDrifted));
}

// The 2026-10-27 benchmark's world, entry for entry (counts from the client's shard replies in the host's flight recording): 588 quests not running +
// 372 running, 629 stage-0, 1470 objective flags 0, 2130 vars at 0, 53 non-zero vars, 9 completed quests the host engine does not show completed.
// Real digest cadence (one every 10 s). The baseline-less host knows none of it. Measured then: 25 digest rounds to settle (8 shards per round, the
// stuck quest shards asked first every round), a mismatch in EVERY round after that, 744 repair effects per minute, 190 running quests stopped.
namespace {
struct BenchWorld {
    uint64_t mismatches = 0, okDigests = 0, repairEffects = 0, settled = 0, acked = 0, followUps = 0;
    uint64_t mismatchesAfter60s = 0, repairEffectsAfter60s = 0;
    size_t runningBefore = 0, runningAfter = 0, firstOkAtS = 0;
    bool setupOk = false;
};
BenchWorld benchWorld(bool ackOff = false, bool lowestFirst = false, bool noFollowUp = false, bool neverShrink = false) {
    BenchWorld b;
    InMemoryNetwork net{12};
    HostSession host(*net.endpoint(0));
    Engine eng;
    EffectCapture cap;
    ClientSession cs(*net.endpoint(1), 0, 10, eng, &cap);
    cs.setBaselineProvider([&](ShadowState& w, uint64_t& h) { w = eng.w; h = 1; return true; });
    cs.setDigestInterval(10000);   // the real cadence
    HostSession::DesyncOptions o;
    o.minDigestIntervalMs = 2000;
    host.setDesyncOptions(o);
    // the host engine: the same save as the client's engine, except that the 9 quests are not completed on it
    host.hostQuestRunning = [&](const std::vector<FormRef>& qs, std::map<FormRef, bool>& out) {
        for (auto& q : qs) { auto it = eng.w.questRunning.find(q); if (it != eng.w.questRunning.end()) out[q] = it->second; }
        return true;
    };
    host.hostQuestState = [&](const std::vector<FormRef>& qs, std::map<FormRef, HostSession::HostQuestState>& out) {
        for (auto& q : qs) { HostSession::HostQuestState s; s.stage = 0; out[q] = s; }
        return true;
    };
    host.hostScriptVars = [&](const std::vector<std::pair<FormRef, uint16_t>>& vs, std::map<std::pair<FormRef, uint16_t>, double>& out) {
        for (auto& v : vs) { auto it = eng.w.scriptVars.find(v); if (it != eng.w.scriptVars.end()) out[v] = it->second; }
        return true;
    };
    uint64_t now = 0;
    auto run = [&](uint32_t ms) { for (uint32_t t = 0; t < ms; t += 5) { now += 5; net.advance(now); host.tick(now); cs.tick(now); net.advance(now); } };
    cs.connect(now, cs.lastSession() + 1);
    run(1000);
    for (int i = 0; i < 960; ++i) eng.w.questRunning[Q(0x6000 + i)] = i >= 588;
    for (int i = 0; i < 629; ++i) eng.w.stages[Q(0x7000 + i)] = 0;
    for (int i = 0; i < 1470; ++i) eng.w.objectives[{Q(0x8000 + i / 3), uint32_t(10 + 10 * (i % 3))}] = 0;
    for (int i = 0; i < 2130; ++i) eng.w.scriptVars[{Q(0x9000 + i / 6), uint16_t(1 + i % 6)}] = 0.0;
    for (int i = 0; i < 53; ++i) eng.w.scriptVars[{Q(0xA000 + i), uint16_t(1 + i % 3)}] = 1.0 + i % 4;
    for (int i = 0; i < 9; ++i) eng.w.questCompleted.insert(Q(0xB000 + i));
    for (auto& [q, on] : eng.w.questRunning) if (on) ++b.runningBefore;
    b.setupOk = true;
    MutationScope scope([&] { Mutations m; m.ackIgnored = ackOff; m.askLowestFirst = lowestFirst; m.noSettleFollowUp = noFollowUp; m.askNeverShrinks = neverShrink; return m; }());
    uint64_t misAt60 = 0, effAt60 = 0;
    for (int sec = 1; sec <= 300; ++sec) {
        run(1000);
        if (sec == 60) { misAt60 = host.stats().digestsMismatch; effAt60 = host.stats().desyncEffectsRepaired; }
        if (!b.firstOkAtS && host.stats().digestsOk > 0) b.firstOkAtS = size_t(sec);
    }
    b.mismatches = host.stats().digestsMismatch;
    b.okDigests = host.stats().digestsOk;
    b.repairEffects = host.stats().desyncEffectsRepaired;
    b.settled = host.stats().effectsSettled;
    b.acked = host.stats().divergenceAcked;
    b.followUps = host.stats().settleFollowUps;
    b.mismatchesAfter60s = b.mismatches - misAt60;
    b.repairEffectsAfter60s = b.repairEffects - effAt60;
    for (auto& [q, on] : eng.w.questRunning) if (on) ++b.runningAfter;
    return b;
}
}  // namespace

TEST(desync_clientonly, the_benchmark_world_settles_in_a_few_rounds_and_then_stays_quiet) {
    BenchWorld b = benchWorld();
    REQUIRE(b.setupOk);
    std::printf("    mismatches %llu (after 60s: %llu) ok %llu, repair effects %llu (after 60s: %llu), settled %llu, acked %llu, follow-ups %llu, first ok at %zu s\n",
                (unsigned long long)b.mismatches, (unsigned long long)b.mismatchesAfter60s, (unsigned long long)b.okDigests, (unsigned long long)b.repairEffects,
                (unsigned long long)b.repairEffectsAfter60s, (unsigned long long)b.settled, (unsigned long long)b.acked, (unsigned long long)b.followUps, b.firstOkAtS);
    CHECK(b.firstOkAtS > 0 && b.firstOkAtS <= 60);
    CHECK_MSG(b.mismatchesAfter60s == 0, "mismatches after the first minute: " + std::to_string(b.mismatchesAfter60s));   // 5.9 per minute before
    CHECK_MSG(b.repairEffects <= 5, "repair effects: " + std::to_string(b.repairEffects));                                 // 744 per minute before
    CHECK(b.settled >= 4800);                                                                                              // the keys are settled, counted apart
    CHECK_MSG(b.acked == 9, "acknowledged " + std::to_string(b.acked));                                                    // the 8 + 1 completed quests
    CHECK_MSG(b.runningAfter == b.runningBefore, "running quests " + std::to_string(b.runningBefore) + " -> " + std::to_string(b.runningAfter));
}

// Twins: without the acknowledgement the 9 quests keep the digest mismatching in every round; with the bad shards always asked lowest first and
// no acknowledgement the stuck quest shards starve the rest (the benchmark took 25 rounds); without the immediate follow-up the settle needs
// one 10 s digest per round.
TEST(twin, unit_desync_settle_no_acknowledgement) {
    BenchWorld b = benchWorld(true);
    CHECK_MSG(b.mismatchesAfter60s >= 20, "mismatches after the first minute: " + std::to_string(b.mismatchesAfter60s));
    CHECK(b.okDigests == 0);
}
TEST(twin, unit_desync_settle_lowest_shards_first) {
    BenchWorld good = benchWorld(true);
    BenchWorld bad = benchWorld(true, true);
    CHECK_MSG(bad.settled < good.settled, "settled " + std::to_string(bad.settled) + " vs " + std::to_string(good.settled));
}
TEST(twin, unit_desync_settle_no_follow_up) {
    BenchWorld bad = benchWorld(false, false, true);
    CHECK_MSG(bad.firstOkAtS > 60 || bad.firstOkAtS == 0, "first ok at " + std::to_string(bad.firstOkAtS));
}
TEST(twin, unit_desync_settle_ask_never_shrinks) {
    BenchWorld bad = benchWorld(false, false, false, true);
    CHECK_MSG(bad.settled < 5000 || bad.firstOkAtS == 0 || bad.firstOkAtS > 60, "settled " + std::to_string(bad.settled) + ", first ok at " + std::to_string(bad.firstOkAtS));
}

// Two-copy lab 2026-10-27 (spawn, idle): the first divergence was "SetGlobal 37 = 19, client (missing)" and the digest never went ok. 0x37 is GameDay: the
// client's world dump leaves the clock globals out by name, while the baseline-less host (which never dumped, so never loaded the volatile list) polled
// and committed it. With the clock on the volatile list (it is now, plugin/snapshot/data/volatile_keys.txt) neither side counts it. Without it, a key the
// client cannot carry is repaired kMaxRepairAttempts times and then given up on, not resent for ever.
namespace {
struct ClockRun { uint64_t mismatchesAfter = 0, okAfter = 0, effectsRepaired = 0, gaveUp = 0, acked = 0; };
ClockRun clockGlobalRun(bool onVolatileList, bool repairForever) {
    InMemoryNetwork net{12};
    HostSession host(*net.endpoint(0));
    Engine eng;
    EffectCapture cap;
    ClientSession cs(*net.endpoint(1), 0, 10, eng, &cap);
    cs.setBaselineProvider([&](ShadowState& w, uint64_t& h) { w = eng.w; w.globals.erase(Q(0x37)); h = 1; return true; });   // the dump has no clock
    cs.setDigestInterval(200);
    HostSession::DesyncOptions o;
    o.minDigestIntervalMs = 100;
    host.setDesyncOptions(o);
    volatileKeys().clear();
    if (onVolatileList) volatileKeys().addGlobal(Q(0x37));
    uint64_t now = 0;
    auto run = [&](uint32_t ms) { for (uint32_t t = 0; t < ms; t += 5) { now += 5; net.advance(now); host.tick(now); cs.tick(now); net.advance(now); } };
    MutationScope scope([&] { Mutations m; m.repairForever = repairForever; return m; }());
    cs.connect(now, cs.lastSession() + 1);
    run(600);
    Cause c; c.kind = CauseKind::Script; c.source = Q(1);
    host.commit(0, c, {SetGlobal{Q(0x37), 19.0f}});   // the poll saw the clock move
    run(5000);
    const uint64_t mis = host.stats().digestsMismatch, ok = host.stats().digestsOk;
    run(30000);
    ClockRun r;
    r.mismatchesAfter = host.stats().digestsMismatch - mis;
    r.okAfter = host.stats().digestsOk - ok;
    r.effectsRepaired = host.stats().desyncEffectsRepaired;
    r.gaveUp = host.stats().repairGaveUp;
    r.acked = host.stats().divergenceAcked;
    volatileKeys().clear();
    return r;
}
}  // namespace

TEST(desync_clientonly, the_clock_global_on_the_volatile_list_never_mismatches) {
    ClockRun r = clockGlobalRun(true, false);
    CHECK(r.mismatchesAfter == 0 && r.okAfter > 50);
    CHECK(r.effectsRepaired == 0 && r.acked == 0);
}

TEST(desync_clientonly, a_host_global_the_client_dump_cannot_carry_is_given_up_after_a_few_repairs) {
    ClockRun r = clockGlobalRun(false, false);
    CHECK_MSG(r.effectsRepaired <= HostSession::kMaxRepairAttempts, "repair effects " + std::to_string(r.effectsRepaired));
    CHECK(r.gaveUp == 1 && r.acked == 1);
    CHECK_MSG(r.mismatchesAfter == 0 && r.okAfter > 50, "after: " + std::to_string(r.mismatchesAfter) + " mismatches, " + std::to_string(r.okAfter) + " ok");
}

// Twin: no give-up. The key is repaired in every round for ever (the lab's mismatch in every digest).
TEST(twin, unit_desync_clientonly_repair_forever) {
    ClockRun bad = clockGlobalRun(false, true);
    CHECK_MSG(bad.effectsRepaired > 20 && bad.okAfter == 0, "repair effects " + std::to_string(bad.effectsRepaired) + ", ok " + std::to_string(bad.okAfter));
}
