// Property tests over many seeds + whole-system mutation twins.
#include <cstdlib>
#include "minitest.h"
#include "sim.h"

using namespace fnvcoop;
using namespace fnvcoop::sim;

namespace {
struct Agg {
    int seeds = 0, failed = 0;
    uint64_t commits = 0, disconnects = 0, fresh = 0, snapshots = 0, replays = 0, retransmits = 0, dupDropped = 0,
             sent = 0, lost = 0, dup = 0, corrupt = 0, intents = 0;
    std::vector<std::string> firstViolations;
};

Agg runSeeds(uint64_t first, int count, bool verbose = false) {
    Agg a;
    for (int i = 0; i < count; ++i) {
        SimConfig cfg = configForSeed(first + uint64_t(i));
        SimResult r = runSim(cfg);
        ++a.seeds;
        a.commits += r.commits; a.disconnects += r.disconnects; a.fresh += r.freshRestarts; a.snapshots += r.snapshots;
        a.replays += r.replays; a.retransmits += r.retransmitBatches; a.dupDropped += r.duplicatesDropped;
        a.sent += r.msgsSent; a.lost += r.msgsLost; a.dup += r.msgsDup; a.corrupt += r.msgsCorrupt; a.intents += r.intents;
        if (!r.ok) {
            ++a.failed;
            if (a.firstViolations.size() < 3) a.firstViolations.push_back("seed " + std::to_string(cfg.seed) + ": " + r.violations[0]);
            if (verbose) std::printf("    seed %llu: %s [hostLoads %llu clientLoads %llu blocked %llu rebases %llu restarts %llu]\n", (unsigned long long)cfg.seed, r.violations[0].c_str(), (unsigned long long)r.hostLoads, (unsigned long long)r.clientLoads, (unsigned long long)r.clientLoadsBlocked, (unsigned long long)r.rebases, (unsigned long long)r.freshRestarts);
        }
    }
    return a;
}

int seedCount(int dflt) {
    const char* e = std::getenv("FNVCOOP_SEEDS");
    return e ? std::atoi(e) : dflt;
}

// Whole-system twin: with the bug injected, at least one seed must violate an invariant, and the clean run must pass.
void expectDetected(const Mutations& m, const char* label, const char* mustMention = nullptr) {
    Agg clean = runSeeds(1, 60);
    CHECK_MSG(clean.failed == 0, "clean run must pass before the twin means anything");
    Agg bad;
    {
        MutationScope s(m);
        bad = runSeeds(1, 60);
    }
    std::printf("    twin %s: caught on %d/%d seeds", label, bad.failed, bad.seeds);
    if (!bad.firstViolations.empty()) std::printf("  e.g. %s", bad.firstViolations[0].c_str());
    std::printf("\n");
    CHECK(bad.failed > 0);
    if (mustMention && !bad.firstViolations.empty())
        CHECK_MSG(bad.firstViolations[0].find(mustMention) != std::string::npos, bad.firstViolations[0]);
}
}  // namespace

TEST(sim, single_seed_smoke_and_determinism) {
    SimConfig c = configForSeed(1);
    SimResult a = runSim(c), b = runSim(c);
    CHECK_MSG(a.ok, a.violations.empty() ? "" : a.violations[0]);
    CHECK(a.commits > 0);
    CHECK(a.commits == b.commits && a.msgsSent == b.msgsSent && a.msgsLost == b.msgsLost && a.snapshots == b.snapshots &&
          a.replays == b.replays && a.virtualMs == b.virtualMs);
}

TEST(sim, perfect_link_converges) {
    SimConfig c = configForSeed(5);
    c.link = LinkParams();
    c.disconnectPerTick = 0;
    SimResult r = runSim(c);
    CHECK_MSG(r.ok, r.violations.empty() ? "" : r.violations[0]);
    CHECK(r.msgsLost == 0);
}

TEST(sim, harsh_link_and_churn_converge) {
    SimConfig c = configForSeed(9);
    c.clients = 4; c.policy.maxPlayers = 6;
    c.link.minLatencyMs = 20; c.link.maxLatencyMs = 400;
    c.link.loss = 0.5; c.link.duplicate = 0.4; c.link.corrupt = 0.1;
    c.disconnectPerTick = 0.01; c.freshRestartProb = 0.5; c.durationMs = 10000;
    SimResult r = runSim(c);
    CHECK_MSG(r.ok, r.violations.empty() ? "" : r.violations[0]);
    CHECK(r.disconnects > 0);
    CHECK(r.msgsLost > 0);
}

TEST(sim, seeds_1000_converge) {
    int n = seedCount(1000);
    const char* f = std::getenv("FNVCOOP_FIRST");
    Agg a = runSeeds(f ? uint64_t(std::atoll(f)) : 1, n, true);
    std::printf("    %d seeds: %d failed | commits %llu intents %llu | disconnects %llu fresh-restarts %llu | snapshot joins %llu replay joins %llu\n"
                "    msgs sent %llu lost %llu duplicated %llu corrupted %llu | retransmit batches %llu | duplicate txns dropped %llu\n",
                a.seeds, a.failed, (unsigned long long)a.commits, (unsigned long long)a.intents, (unsigned long long)a.disconnects,
                (unsigned long long)a.fresh, (unsigned long long)a.snapshots, (unsigned long long)a.replays, (unsigned long long)a.sent,
                (unsigned long long)a.lost, (unsigned long long)a.dup, (unsigned long long)a.corrupt, (unsigned long long)a.retransmits,
                (unsigned long long)a.dupDropped);
    CHECK(a.seeds >= 1000 || n < 1000);
    CHECK(a.failed == 0);
    // The chaos must actually have exercised the hard paths, or the pass means nothing.
    CHECK(a.snapshots > 0); CHECK(a.replays > 0); CHECK(a.lost > 0); CHECK(a.dup > 0); CHECK(a.dupDropped > 0);
    CHECK(a.disconnects > 0); CHECK(a.fresh > 0); CHECK(a.intents > 0);
}

// ---- whole-system mutation twins: each re-introduces one classic bug; the sim must notice --------

TEST(sim, ghost_death_scripts_are_host_only) {
    // Across seeds the host must have run death scripts (guest-owned NPC kills), and no guest produced any.
    Agg a = runSeeds(1, 100);
    uint64_t deaths = 0;
    for (int i = 0; i < 100; ++i) deaths += runSim(configForSeed(uint64_t(1 + i))).deathScripts;
    CHECK(deaths > 50);
    CHECK(a.failed == 0);
}

TEST(sim, quest_var_churn_1000_writes_per_second_converges_and_is_coalesced) {
    uint64_t writes = 0, effects = 0, actions = 0;
    for (uint64_t seed = 300; seed < 340; ++seed) {
        SimConfig c = configForSeed(seed);
        c.varWritesPerTick = 10;  // 10 writes / 10 ms tick = 1000 writes per second
        SimResult r = runSim(c);
        CHECK_MSG(r.ok, r.violations.empty() ? "" : r.violations[0]);
        writes += r.varWrites; effects += r.varEffects; actions += r.actionsExecuted;
    }
    std::printf("    churn: %llu var/global writes -> %llu replicated effects (%.1fx coalescing); %llu guest action requests executed once each\n",
                (unsigned long long)writes, (unsigned long long)effects, effects ? double(writes) / double(effects) : 0.0, (unsigned long long)actions);
    CHECK(writes > 10000);
    CHECK(effects * 2 < writes);   // coalescing + budget at least halve the traffic (20 distinct keys, 600/s budget)
    CHECK(actions > 0);
}

TEST(sim, join_in_progress_baseline_seeds_converge) {
    uint64_t joins = 0, effects = 0, reverts = 0, chunks = 0, n = 0;
    for (uint64_t seed = 1; seed < 400; seed += 4) {   // seed % 4 == 1 -> baselineJoin
        SimConfig c = configForSeed(seed);
        REQUIRE(c.baselineJoin);
        SimResult r = runSim(c);
        CHECK_MSG(r.ok, r.violations.empty() ? "" : r.violations[0]);
        joins += r.baselineJoins; effects += r.baselineEffects; reverts += r.baselineReverts; chunks += r.baselineChunks; ++n;
    }
    std::printf("    baseline: %llu seeds, %llu baseline joins, %llu effects, %llu client-only reverts, %llu chunks sent\n",
                (unsigned long long)n, (unsigned long long)joins, (unsigned long long)effects, (unsigned long long)reverts, (unsigned long long)chunks);
    CHECK(joins >= n);
    CHECK(effects > 1000);
    CHECK(reverts > 0);
}

TEST(sim, host_and_client_save_load_scenarios_converge) {
    uint64_t hl = 0, cl = 0, blocked = 0, rebases = 0, stale = 0, n = 0;
    for (uint64_t seed = 1; seed < 800; ++seed) {
        if (seed % 8 != 1 && seed % 8 != 5) continue;
        SimConfig c = configForSeed(seed);
        SimResult r = runSim(c);
        CHECK_MSG(r.ok, "seed " + std::to_string(seed) + ": " + (r.violations.empty() ? "" : r.violations[0]));
        hl += r.hostLoads; cl += r.clientLoads; blocked += r.clientLoadsBlocked; rebases += r.rebases; stale += r.staleEpochDropped; ++n;
    }
    std::printf("    save/load: %llu seeds | host loads of older saves %llu | client loads %llu (blocked by policy %llu) | client rebases %llu | stale-epoch batches dropped %llu\n",
                (unsigned long long)n, (unsigned long long)hl, (unsigned long long)cl, (unsigned long long)blocked, (unsigned long long)rebases, (unsigned long long)stale);
    CHECK(hl > 50);
    CHECK(cl > 20);
    CHECK(rebases > 50);
}

TEST(sim, desync_injection_is_healed_across_chaos_seeds) {
    uint64_t inj = 0, mism = 0, rep = 0, reports = 0, n = 0;
    for (uint64_t seed = 2; seed < 1500; seed += 5) {
        SimConfig c = configForSeed(seed);
        if (!c.desync) continue;
        SimResult r = runSim(c);
        CHECK_MSG(r.ok, "seed " + std::to_string(seed) + ": " + (r.violations.empty() ? "" : r.violations[0]));
        inj += r.injected; mism += r.digestsMismatch; rep += r.desyncRepairs; reports += r.desyncReports; ++n;
    }
    std::printf("    desync sim: %llu seeds | injected corruptions %llu | mismatching digests %llu | repairs sent %llu | reports %llu\n",
                (unsigned long long)n, (unsigned long long)inj, (unsigned long long)mism, (unsigned long long)rep, (unsigned long long)reports);
    CHECK(inj > 100);
    CHECK(mism > 50);
    CHECK(rep > 50);
}

TEST(twin, heal_disabled) { Mutations m; m.healDisabled = true; expectDetected(m, "heal_disabled"); }
TEST(twin, no_epoch_bump) { Mutations m; m.noEpochBump = true; expectDetected(m, "no_epoch_bump"); }
TEST(twin, baseline_skipped) { Mutations m; m.baselineSkipped = true; expectDetected(m, "baseline_skipped"); }
TEST(twin, guest_runs_quest_scripts) { Mutations m; m.guestRunsQuestScripts = true; expectDetected(m, "guest_runs_quest_scripts"); }
TEST(twin, no_guest_suppression) { Mutations m; m.noGuestSuppression = true; expectDetected(m, "no_guest_suppression", "ghost"); }
TEST(twin, no_echo_guard) { Mutations m; m.noEchoGuard = true; m.noGuestSuppression = true; expectDetected(m, "no_echo_guard"); }
TEST(twin, no_idempotency) { Mutations m; m.noIdempotency = true; expectDetected(m, "no_idempotency"); }
TEST(twin, stage_last_wins) { Mutations m; m.stageLastWins = true; expectDetected(m, "stage_last_wins"); }
TEST(twin, flags_and) { Mutations m; m.flagsAnd = true; expectDetected(m, "flags_and"); }
TEST(twin, kills_replace) { Mutations m; m.killsReplace = true; expectDetected(m, "kills_replace"); }
TEST(twin, apply_out_of_order) { Mutations m; m.applyOutOfOrder = true; expectDetected(m, "apply_out_of_order"); }
