// fnvcoop_simfuzz: property-based co-op fuzzer CLI. Normally driven by tools/simfuzz/run.py.
//   --minutes M | --count K     how long / how many seeds (default 200 seeds)
//   --seed S                    first seed
//   --players P                 machines per session (2..4); 0 = vary per seed
//   --duration MS               chaos phase length per session (default 15000)
//   --target mini|em
//   --bug NAME                  inject a bug class (repeatable); a run with a bug is expected to fail
//   --replay FILE               run one scenario file; exit 1 and print VIOLATION lines when an invariant breaks
//   --profile NAME              link from a T5 chaos profile (lan good_wifi bad_wifi mobile_tether transatlantic hell)
//   --out DIR                   where failures (shrunk scenario + traces) are written (default out/simfuzz)
//   --fixture BUG[,BUG2]        find a failing session for BUG (or several faults injected together), shrink it and write <out>/regress_BUG.json (a saved regression)
//   --shrink FILE [--save PATH] shrink a failing scenario file (ddmin over ops, then the environment) and write it to PATH (default <out>/shrunk.scenario.json);
//                               extra --bug flags are injected first, so a repro that needs several faults at once can be saved with them in its mutations
//   --no-shrink, --max-fail N, --list-bugs, --quiet
#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <sstream>
#include "fuzz.h"
#include "chaos/impair.h"

using namespace fnvcoop::fuzz;

static std::string readFile(const std::string& p, bool* ok) {
    std::ifstream f(p, std::ios::binary);
    *ok = static_cast<bool>(f);
    std::stringstream ss;
    ss << f.rdbuf();
    return ss.str();
}

// The most specific broken invariant: a convergence failure is usually only the symptom of one of the others.
static std::string mainInvariant(const RunResult& r) {
    static const char* order[] = {"double_apply", "multi_owner", "stale_applied", "echo_adopted", "dup_thing", "repairs_after_quiescence", "not_quiescent", "converge"};
    for (const char* o : order)
        for (auto& v : r.violations) if (v.invariant == o) return o;
    return r.violations[0].invariant;
}

static std::string detailOf(const RunResult& r, const std::string& inv) {
    for (auto& v : r.violations) if (v.invariant == inv) return v.detail;
    return r.violations[0].detail;
}

int main(int argc, char** argv) {
    double minutes = 0;
    int count = 200, players = 0, maxFail = 3;
    uint64_t seed = 1;
    uint32_t duration = 15000;
    std::string out = "out/simfuzz", replay, fixture, profile, shrinkFile, savePath;
    RunOptions opt;
    std::vector<std::string> bugs;
    bool shrinkOn = true, quiet = false;
    bool countGiven = false, targetGiven = false;
    for (int i = 1; i < argc; ++i) {
        auto a = [&](const char* n) { return !std::strcmp(argv[i], n) && i + 1 < argc; };
        if (a("--minutes")) minutes = std::atof(argv[++i]);
        else if (a("--count")) { count = std::atoi(argv[++i]); countGiven = true; }
        else if (a("--seed")) seed = std::strtoull(argv[++i], nullptr, 10);
        else if (a("--players")) players = std::atoi(argv[++i]);
        else if (a("--duration")) duration = static_cast<uint32_t>(std::atoi(argv[++i]));
        else if (a("--target")) { opt.target = argv[++i]; targetGiven = true; }
        else if (a("--bug")) bugs.push_back(argv[++i]);
        else if (a("--replay")) replay = argv[++i];
        else if (a("--shrink")) shrinkFile = argv[++i];
        else if (a("--save")) savePath = argv[++i];
        else if (a("--fixture")) fixture = argv[++i];
        else if (a("--profile")) profile = argv[++i];   // T5 chaos profile: lan good_wifi bad_wifi mobile_tether transatlantic hell
        else if (a("--out")) out = argv[++i];
        else if (a("--max-fail")) maxFail = std::atoi(argv[++i]);
        else if (!std::strcmp(argv[i], "--no-shrink")) shrinkOn = false;
        else if (!std::strcmp(argv[i], "--quiet")) quiet = true;
        else if (!std::strcmp(argv[i], "--list-bugs")) {
            for (auto& b : knownBugs(opt.target)) std::printf("%s\n", b.c_str());
            return 0;
        } else { std::fprintf(stderr, "unknown or incomplete argument: %s\n", argv[i]); return 2; }
    }

    if (!fixture.empty()) {
        std::vector<std::string> fixtureBugs;   // --fixture a,b injects several faults together (file regress_a_b.json)
        std::string fileTag;
        for (size_t b = 0; b <= fixture.size();) {
            size_t e = fixture.find(',', b);
            if (e == std::string::npos) e = fixture.size();
            if (e > b) { fixtureBugs.push_back(fixture.substr(b, e - b)); fileTag += (fileTag.empty() ? "" : "_") + fixtureBugs.back(); }
            b = e + 1;
        }
        for (uint64_t sd = 1; sd <= 600; ++sd) {
            Scenario s = generate(sd, 2 + static_cast<int>(sd % 3), 8000, opt.target);
            s.bugs = fixtureBugs;
            s.target = opt.target;
            s.profile = profile;   // --profile hell makes slow-link bugs (a forced commit before the old owner heard) reproducible
            RunResult r = run(s, opt);
            if (r.ok) continue;
            // A repro of the fault must pass without it. A session that fails on the real code too is a different bug (or a stale oracle), not this fault's repro:
            // saving it would give a "regression" that no longer says anything about the fault.
            Scenario cleanRun = s;
            cleanRun.bugs.clear();
            if (!run(cleanRun, opt).ok) continue;
            // prefer the invariant the bug is meant to break: the first violation after sorting by our priority list
            std::string inv = mainInvariant(r);
            Scenario small = shrink(s, opt, inv, nullptr, true);
            small.expect = inv;
            std::string path = out + "/regress_" + fileTag + ".json";
            std::ofstream f(path, std::ios::binary);
            f << toJson(small);
            std::printf("fixture %s: seed %llu, %s, %zu ops -> %s\n", fixture.c_str(), static_cast<unsigned long long>(sd), inv.c_str(), small.ops.size(), path.c_str());
            return f ? 0 : 2;
        }
        std::fprintf(stderr, "no failing session found for %s\n", fixture.c_str());
        return 1;
    }

    if (!shrinkFile.empty()) {
        bool ok;
        std::string text = readFile(shrinkFile, &ok);
        Scenario s;
        std::string err;
        if (!ok || !fromJson(text, s, &err)) { std::fprintf(stderr, "cannot read scenario %s: %s\n", shrinkFile.c_str(), err.c_str()); return 2; }
        for (auto& b : bugs) s.bugs.push_back(b);
        RunOptions ro = opt;
        if (!targetGiven && !s.target.empty()) ro.target = s.target;
        RunResult r = run(s, ro);
        if (r.ok) { std::fprintf(stderr, "%s passes: nothing to shrink\n", shrinkFile.c_str()); return 1; }
        const std::string inv = mainInvariant(r);
        if (!s.bugs.empty()) {   // a repro that needs injected faults must pass without them, or it is not a repro of those faults
            Scenario cleanRun = s;
            cleanRun.bugs.clear();
            if (!run(cleanRun, ro).ok) { std::fprintf(stderr, "%s fails without the injected bugs too: it is a failure of the real code, not a repro of those faults\n", shrinkFile.c_str()); return 1; }
        }
        int runs = 0;
        Scenario small = shrink(s, ro, inv, &runs, !s.bugs.empty());
        small.expect = inv;
        const std::string path = savePath.empty() ? out + "/shrunk.scenario.json" : savePath;
        std::ofstream f(path, std::ios::binary);
        f << toJson(small);
        std::printf("shrunk %s: %zu -> %zu ops, %d machines, %u ms in %d runs, breaks %s -> %s\n", shrinkFile.c_str(), s.ops.size(), small.ops.size(), small.machines, small.durationMs, runs, inv.c_str(), path.c_str());
        return f ? 0 : 2;
    }

    if (!replay.empty()) {
        bool ok;
        std::string text = readFile(replay, &ok);
        Scenario s;
        std::string err;
        if (!ok || !fromJson(text, s, &err)) { std::fprintf(stderr, "cannot read scenario %s: %s\n", replay.c_str(), err.c_str()); return 2; }
        for (auto& b : bugs) s.bugs.push_back(b);
        RunOptions ro = opt;
        if (!targetGiven && !s.target.empty()) ro.target = s.target;   // a scenario file knows which target it was recorded on
        if (!out.empty() && out != "out/simfuzz") { ro.traceDir = out; ro.traceName = "replay"; }
        RunResult r = run(s, ro);
        for (auto& v : r.violations) std::printf("VIOLATION %s %s\n", v.invariant.c_str(), v.detail.c_str());
        std::printf("replay %s: %zu ops, %llu virtual ms, %s\n", replay.c_str(), s.ops.size(), static_cast<unsigned long long>(r.virtualMs), r.ok ? "all invariants held" : "FAILED");
        return r.ok ? 0 : 1;
    }

    auto t0 = std::chrono::steady_clock::now();
    auto elapsedMin = [&] { return std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count() / 60.0; };
    int ran = 0, failed = 0;
    std::map<std::string, uint64_t> total;
    std::map<std::string, int> byInvariant;
    for (uint64_t k = 0;; ++k) {
        if (minutes > 0 && !countGiven) { if (elapsedMin() >= minutes) break; }
        else if (static_cast<int>(k) >= count) break;
        uint64_t sd = seed + k;
        int mc = players > 0 ? players : 2 + static_cast<int>(sd % 3);
        Scenario s = generate(sd, mc, duration, opt.target);
        s.bugs = bugs;
        s.target = opt.target;
        s.profile = profile;
        RunResult r = run(s, opt);
        ++ran;
        for (auto& kv : r.stats) total[kv.first] += kv.second;
        if (r.ok) continue;
        ++failed;
        for (auto& v : r.violations) ++byInvariant[v.invariant];
        std::string inv = mainInvariant(r);
        std::printf("FAIL seed %llu (%d machines): %s: %s\n", static_cast<unsigned long long>(sd), mc, inv.c_str(), detailOf(r, inv).c_str());
        std::string name = "fail_seed" + std::to_string(sd) + "_" + inv;
        RunOptions to = opt;
        to.traceDir = out;
        to.traceName = name + ".full";
        run(s, to);
        if (shrinkOn) {
            int runs = 0;
            Scenario small = shrink(s, opt, inv, &runs);
            std::string json = toJson(small);
            std::string hash = fnvHex(json);
            RunOptions so = opt;
            so.traceDir = out;
            so.traceName = name + ".min";
            RunResult sr = run(small, so);
            std::printf("  shrunk %zu -> %zu ops, %d machines, %u ms in %d runs; still fails: %s\n", s.ops.size(), small.ops.size(), small.machines, small.durationMs, runs,
                        sr.ok ? "NO (bug in shrinker)" : sr.violations[0].invariant.c_str());
            std::printf("  minimized scenario: %s/%s.min.scenario.json  (hash %s)\n", out.c_str(), name.c_str(), hash.c_str());
            std::printf("  replay: fnvcoop_simfuzz --replay %s/%s.min.scenario.json\n", out.c_str(), name.c_str());
        } else {
            std::printf("  scenario: %s/%s.full.scenario.json\n", out.c_str(), name.c_str());
        }
        if (failed >= maxFail) { std::printf("stopping after %d failures\n", failed); break; }
    }
    if (!quiet) {
        std::printf("coverage:");
        for (const char* k : {"op_move", "op_hit", "op_pickup", "op_spawn", "op_leave", "op_join", "op_load", "op_stale", "op_projectile", "op_explosion", "op_limbhit", "op_simulkill", "hits_applied", "grants", "transfers", "releases", "deaths", "loot_races_lost",
                              "spawn_races_lost", "stale_old_content_injected", "stale_raw_injected", "dup_packets_dropped", "loads", "repairs_served"})
            std::printf(" %s=%llu", k, static_cast<unsigned long long>(total[k]));
        std::printf("\n");
        std::printf("rejected (correctly):");
        for (auto& kv : total) if (kv.first.rfind("reject_", 0) == 0) std::printf(" %s=%llu", kv.first.c_str() + 7, static_cast<unsigned long long>(kv.second));
        std::printf("\n");
    }
    std::printf("simfuzz: %d sessions, %d failed, target %s, %zu injected bug classes\n", ran, failed, opt.target.c_str(), bugs.size());
    for (auto& kv : byInvariant) std::printf("  %s: %d\n", kv.first.c_str(), kv.second);
    if (failed == 0 && ran > 0 && bugs.empty()) std::printf("RESULT: ALL OK\n");
    return failed ? 1 : 0;
}
