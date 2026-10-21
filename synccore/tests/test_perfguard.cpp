// perf_guard.h: the load-robust budget logic of the perf group, proven on a SIMULATED machine (no real timing, so it cannot flake itself).
// Group "perfguard". Twins: FNVCOOP_MUT=1..10 (ctest twin_perfguard_N); each must make at least one test here fail.
//   1 CPU factor ignored            2 failing body forgiven       3 stall allowance ignored   4 stale probe reused for every attempt
//   5 factor cap removed            6 stall cap removed           7 no extra attempts when saturated   8 no waiting for a saturated machine to calm down
//   9 saturated machines still judge the p90   10 quiet machines judge the median (too loose)
#include <vector>

#include "minitest.h"
#include "perf_guard.h"

using perfguard::Budget;
using perfguard::Env;
using perfguard::Guard;

namespace {

// A machine whose load probe replays a script (the last entry repeats); sleeping only adds up the requested time.
struct Sim {
    std::vector<Env> envs;
    size_t next = 0;
    int slept = 0;
    Guard g;
    explicit Sim(std::vector<Env> e) : envs(std::move(e)) {
        g.probe = [this] { Env v = envs[std::min(next, envs.size() - 1)]; ++next; return v; };
        g.sleepMs = [this](int ms) { slept += ms; };
        g.verbose = false;
    }
};

// Runs the guard on `body` and reports whether it PASSED; the failures a failing body recorded are removed again so the test itself stays green.
template <class F> bool guardPasses(Sim& s, F&& body) {
    int before = minitest::state().failures;
    s.g.run(body);
    bool failed = minitest::state().failures > before;
    minitest::state().failures = before;
    return !failed;
}

// The code under test: nominal costs in microseconds on a quiet machine. `load` slows throughput; `stallUs` is a scheduler preemption
// that lands in the worst frame. The budgets are the ones the real tests use (p90 of 100 us, worst frame 5000 us).
constexpr double kThroughputBudget = 100.0, kWorstBudget = 5000.0;
struct Code { double cost = 40, worst = 300, load = 1, stallUs = 0; };
// The simulated code's own checks: a miss counts as a failure for the guard to judge but prints nothing (these misses are intended).
#define SIM_CHECK(cond) do { ++minitest::state().checks; if (!(cond)) ++minitest::state().failures; } while (0)
void judge(const Code& c, const Budget& b) {
    SIM_CHECK(c.cost * c.load < kThroughputBudget * b.k);
    SIM_CHECK(c.worst * c.load + c.stallUs < kWorstBudget * b.k + b.slackUs);
}

const Env kQuiet{1.0, 200.0};
const Env kBusy{6.0, 40000.0};   // a saturated PC: 6x slower, 40 ms scheduler stalls

}  // namespace

TEST(perfguard, quiet_machine_good_code_passes_on_the_first_attempt) {
    Sim s({kQuiet});
    Code c;
    CHECK(guardPasses(s, [&](const Budget& b) { judge(c, b); }));
    CHECK(s.g.attemptsRun == 1);
    CHECK(s.slept == 0);
    Budget b = s.g.budgetFor(kQuiet, 0);
    CHECK(b.k == 1.0 && !b.saturated && b.slackUs == 400.0);
    CHECK(!b.medianGate);   // a quiet machine is judged on the p90, like the budgets were calibrated
}

TEST(perfguard, real_regression_fails_on_a_quiet_machine) {
    Sim s({kQuiet});
    Code c;
    c.cost = 1000;   // 10x its budget, nothing to do with load
    CHECK(!guardPasses(s, [&](const Budget& b) { judge(c, b); }));
    CHECK(s.g.attemptsRun == 3);   // it had its three chances
    Sim s2({kQuiet});
    Code w;
    w.worst = 40000;   // the worst frame regressed 8x
    CHECK(!guardPasses(s2, [&](const Budget& b) { judge(w, b); }));
}

TEST(perfguard, saturated_machine_good_code_passes) {
    Sim s({kBusy});
    Code c;
    c.load = 6;            // everything 6x slower ...
    c.stallUs = 38000;     // ... and one 38 ms scheduler stall lands in the worst frame
    CHECK(guardPasses(s, [&](const Budget& b) { judge(c, b); }));
    CHECK(s.g.attemptsRun == 1);
    Budget b = s.g.budgetFor(kBusy, 0);
    CHECK(b.saturated);
    CHECK(b.k == 6.0);
    CHECK(b.slackUs == 60000.0);   // 40 ms * 2 = 80 ms, capped at 60 ms
    CHECK(b.medianGate);           // preemption inflates the tail, not the median
}

TEST(perfguard, real_regression_on_a_saturated_machine_still_fails) {
    Sim s({kBusy});
    Code c;
    c.load = 6;
    c.cost = 400;          // 10x slower than nominal ON TOP of the load
    CHECK(!guardPasses(s, [&](const Budget& b) { judge(c, b); }));
    CHECK(s.g.attemptsRun == 6);   // saturated machines get six chances, not three
}

TEST(perfguard, load_probe_is_repeated_for_every_attempt) {
    // quiet when the run starts, saturated from then on: attempt 1 is judged with quiet budgets and misses, attempt 2 re-probes and passes
    Sim s({kQuiet, kBusy});
    Code c;
    c.load = 8;
    c.stallUs = 20000;
    std::vector<double> ks;
    CHECK(guardPasses(s, [&](const Budget& b) { ks.push_back(b.k); judge(c, b); }));
    CHECK(ks.size() == 2);
    if (ks.size() == 2) CHECK(ks[0] == 1.0 && ks[1] == 6.0);
    CHECK(s.g.probes == 2);
}

TEST(perfguard, an_absurd_load_report_cannot_excuse_a_regression) {
    const Env insane{1000.0, 10e6};
    {   // 30x over budget: more than the factor cap (16x), though the probe claims 1000x
        Sim s({insane});
        Code c;
        c.cost = 3000;
        CHECK(!guardPasses(s, [&](const Budget& b) { judge(c, b); }));
        CHECK(s.g.budgetFor(insane, 0).k == 16.0);
    }
    {   // a 500 ms worst frame: more than 16x5 ms + the 60 ms stall cap
        Sim s({insane});
        Code c;
        c.stallUs = 500000;
        CHECK(!guardPasses(s, [&](const Budget& b) { judge(c, b); }));
    }
}

TEST(perfguard, waits_for_a_saturated_machine_to_calm_down) {
    Sim s({kBusy, kBusy, kQuiet});
    Code c;
    bool firstSaturated = true;
    int calls = 0;
    CHECK(guardPasses(s, [&](const Budget& b) { if (calls++ == 0) firstSaturated = b.saturated; judge(c, b); }));
    CHECK(s.g.waits == 2);
    CHECK(s.slept == 500);
    CHECK(!firstSaturated);
    CHECK(s.g.attemptsRun == 1);
    // a machine that never calms down: the wait is bounded and the run goes ahead with the saturated allowances
    Sim t({kBusy});
    CHECK(guardPasses(t, [&](const Budget& b) { judge(c, b); }));
    CHECK(t.g.waits == 8);
    CHECK(t.slept == 2000);
}

TEST(perfguard, noise_passes_on_a_later_attempt) {
    {   // saturated: fails 4 times (a compile burst), passes on the 5th
        Sim s({kBusy});
        Code c;
        int n = 0;
        CHECK(guardPasses(s, [&](const Budget& b) { Code x = c; if (n++ < 4) x.cost = 10000; judge(x, b); }));
        CHECK(s.g.attemptsRun == 5);
    }
    {   // quiet: fails twice, passes on the 3rd; a body failing all three still fails
        Sim s({kQuiet});
        Code c;
        int n = 0;
        CHECK(guardPasses(s, [&](const Budget& b) { Code x = c; if (n++ < 2) x.cost = 10000; judge(x, b); }));
        CHECK(s.g.attemptsRun == 3);
        Sim t({kQuiet});
        int m = 0;
        CHECK(!guardPasses(t, [&](const Budget& b) { Code x = c; if (m++ < 3) x.cost = 10000; judge(x, b); }));
        CHECK(t.g.attemptsRun == 3);
    }
}

TEST(perfguard, strict_mode_is_absolute_and_has_no_retries) {
    Sim s({kBusy});
    s.g.cfg.strict = true;
    s.g.cfg.scale = 1.0;
    Code c;
    c.load = 6;
    CHECK(!guardPasses(s, [&](const Budget& b) { judge(c, b); }));
    CHECK(s.g.attemptsRun == 1);
    CHECK(s.g.probes == 0);
    Budget b = s.g.budgetFor(kBusy, 0);
    CHECK(b.k == 1.0 && b.slackUs == 0.0 && !b.medianGate);
}

TEST(perfguard, scale_and_nonsense_probe_values) {
    Sim s({Env{0.2, -5.0}});
    s.g.cfg.scale = 2.0;
    Env e = s.g.probeOnce();
    CHECK(e.factor == 1.0 && e.stallUs == 0.0);   // a probe cannot make budgets tighter than the reference machine
    Budget b = s.g.budgetFor(e, 0);
    CHECK(b.k == 2.0 && b.slackUs == 0.0 && !b.saturated);
    CHECK(s.g.isSaturated(Env{2.6, 0}));
    CHECK(s.g.isSaturated(Env{1.0, 4001}));
    CHECK(!s.g.isSaturated(Env{2.5, 4000}));
}

TEST(perfguard, real_stall_probe_runs) {
    double us = perfguard::longestStallUs(3, 2);
    CHECK(us >= 0.0);
    CHECK(us < 30e6);   // returns; no bound on how busy the machine is
}
