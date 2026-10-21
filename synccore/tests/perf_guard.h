// Load-robust timing budgets for the perf group (synccore/tests/test_perf.cpp). Header only, game-free.
//
// Problem (Track A inbox 2026-10-21): on a shared PC with other builds running, the perf group's worst-frame budgets flaked. The old guard
// multiplied budgets by a CPU calibration loop measured once at the start, but a saturated machine hurts in two ways the loop does not see:
//   * throughput: everything is k times slower                      -> multiply the budgets by k (the calibration loop)
//   * scheduling stalls: the thread is preempted for a time slice     -> a worst-frame number grows by tens of ms, independent of k
// and the load itself changes during the run. This guard
//   * probes BOTH (factor and longest stall) before every attempt (a retry re-probes: the load changes during a run),
//   * waits briefly for a saturated machine to calm down before the first attempt,
//   * gives a throughput budget  budget * k  and a worst-frame budget  budget * k + slackUs  (slack = observed stall, capped),
//   * retries a failing body (more attempts when the machine is saturated), re-probing every time; a body must fail EVERY attempt to fail,
//   * caps both allowances, so that a machine reporting absurd load cannot excuse a real regression.
// A real regression (the code several times slower than its budget on a quiet machine) fails every attempt: the probes stay quiet, the
// allowances stay tiny. tests/test_perfguard.cpp proves both directions with a simulated machine, including 10 mutation twins.
//
// The environment probe and the sleep are injectable, so the logic is unit-tested without any real timing.
#pragma once
#include <algorithm>
#include <chrono>
#include <cstdio>
#include <functional>

#include "fnvcoop_mut.h"   // FNV_MUT(n): twins 1..10 of the perfguard group (constant false outside the test build)
#include "minitest.h"

namespace perfguard {

struct Env {
    double factor = 1.0;    // CPU slowdown against a quiet reference machine (>= 1 after clamping)
    double stallUs = 0.0;   // longest scheduling stall the probe saw (us)
};

struct Budget {
    double k = 1.0;         // multiply every throughput budget (percentiles, medians) by this
    double slackUs = 0.0;   // ADD this to every worst-frame (max) budget
    bool saturated = false; // the machine looked saturated when this attempt started
    bool medianGate = false; // judge the per-iteration MEDIAN instead of the p90: on a saturated machine a preempted iteration lasts whole time slices,
                             // so a tenth of the iterations can be inflated 5x or more (the calibration loop cannot see that); the median still moves
                             // with real slowness, and a real regression is several times over budget there too
    int attempt = 0;        // 0-based
};

struct Config {
    double scale = 1.0;                 // FNVCOOP_PERF_SCALE: manual loosening
    bool strict = false;                // FNVCOOP_PERF_STRICT: absolute budgets, one attempt, no allowances
    int quietAttempts = 3;
    int saturatedAttempts = 6;
    double saturatedFactor = 2.5;       // factor above this, or ...
    double saturatedStallUs = 4000.0;   // ... a stall above this, counts as saturated
    double maxFactor = 16.0;            // allowance caps: beyond them the run is judged as if the machine were this loaded
    double maxSlackUs = 60000.0;
    double stallMultiplier = 2.0;       // slack = stall seen * this (the next stall may be a bit longer than the one probed)
    int waitQuietMs = 2000;             // how long to wait for a saturated machine to calm down before the first attempt
    int pollMs = 250;
    int backoffMs = 200;                // sleep before a retry on a saturated machine
};

struct Guard {
    Config cfg;
    std::function<Env()> probe;         // required
    std::function<void(int)> sleepMs;   // required
    // counters (tests and the report line)
    bool verbose = true;                // print one line per retry (the unit tests of the guard itself switch it off)
    int attemptsRun = 0, waits = 0, probes = 0;

    Env probeOnce() {
        ++probes;
        Env e = probe();
        e.factor = std::max(1.0, e.factor);
        e.stallUs = std::max(0.0, e.stallUs);
        return e;
    }
    bool isSaturated(const Env& e) const { return e.factor > cfg.saturatedFactor || e.stallUs > cfg.saturatedStallUs; }

    Budget budgetFor(const Env& e, int attempt) const {
        Budget b;
        b.attempt = attempt;
        b.saturated = isSaturated(e);
        b.medianGate = !cfg.strict && ((b.saturated && !FNV_MUT(9)) || FNV_MUT(10));   // MUTANT 9: never (the p90 is judged under saturation); MUTANT 10: always (a quiet machine is judged loosely)
        if (cfg.strict) { b.k = cfg.scale; return b; }
        double f = FNV_MUT(5) ? e.factor : std::min(e.factor, cfg.maxFactor);                         // MUTANT 5: no cap on the factor
        double slack = FNV_MUT(6) ? e.stallUs * cfg.stallMultiplier : std::min(e.stallUs * cfg.stallMultiplier, cfg.maxSlackUs);   // MUTANT 6: no cap on the stall allowance
        b.k = cfg.scale * (FNV_MUT(1) ? 1.0 : f);                                                       // MUTANT 1: the CPU factor is ignored
        b.slackUs = FNV_MUT(3) ? 0.0 : slack;                                                           // MUTANT 3: the stall allowance is ignored
        return b;
    }

    // Runs `body(budget)` up to the allowed number of attempts; passes when ONE attempt records no new failures. On failure the failures of the
    // last attempt stay recorded in minitest.
    template <class F> void run(F&& body) {
        if (cfg.strict) { body(budgetFor(Env{}, 0)); ++attemptsRun; return; }
        Env first = probeOnce();
        if (isSaturated(first) && !FNV_MUT(8)) {   // MUTANT 8: no waiting for the machine to calm down
            // let a burst of other work (a compile) pass before measuring anything
            int waited = 0;
            while (isSaturated(first) && waited < cfg.waitQuietMs) {
                sleepMs(cfg.pollMs);
                ++waits;
                waited += cfg.pollMs;
                first = probeOnce();
            }
        }
        const int maxAttempts = (isSaturated(first) && !FNV_MUT(7)) ? cfg.saturatedAttempts : cfg.quietAttempts;   // MUTANT 7: a saturated machine gets no extra attempts
        Env env = first;
        for (int a = 0; a < maxAttempts; ++a) {
            if (a > 0) {
                if (isSaturated(env)) sleepMs(cfg.backoffMs);
                if (!FNV_MUT(4)) env = probeOnce();          // MUTANT 4: the first probe is reused for every attempt (stale load)
            }
            Budget b = budgetFor(env, a);
            int failuresBefore = minitest::state().failures;
            body(b);
            ++attemptsRun;
            if (minitest::state().failures == failuresBefore) return;
            if (FNV_MUT(2)) { minitest::state().failures = failuresBefore; return; }   // MUTANT 2: a failing body is forgiven (any regression passes)
            if (a == maxAttempts - 1) return;                // out of attempts: the failure stands
            minitest::state().failures = failuresBefore;
            if (verbose) std::printf("    (timing budget missed on attempt %d - k=%.2f slack=%.0f us%s; retrying with a fresh load probe)\n", a + 1, b.k, b.slackUs,
                        b.saturated ? ", machine saturated" : "");
        }
    }
};

// ---- the real probe: CPU calibration loop + longest scheduling stall ---------------------------------------------------------------------
// `calibrateUs` is supplied by the caller (test_perf.cpp owns the reference loop and its constant).
inline double longestStallUs(int spinMs, int rounds) {
    using Clock = std::chrono::steady_clock;
    double worst = 0;
    for (int r = 0; r < rounds; ++r) {
        auto start = Clock::now();
        auto prev = start;
        while (true) {
            auto now = Clock::now();
            worst = std::max(worst, std::chrono::duration<double, std::micro>(now - prev).count());
            prev = now;
            if (now - start >= std::chrono::milliseconds(spinMs)) break;
        }
    }
    return worst;
}

}  // namespace perfguard
