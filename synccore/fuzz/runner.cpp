// Scenario runner, delta-debugging shrinker and failure traces.
#include <algorithm>
#include <cstdio>
#include <fstream>
#include <sstream>
#include "fuzz.h"
#ifdef FNVCOOP_FUZZ_FLIGHT
#include "flight/flight.h"
#endif
#ifdef _WIN32
#include <direct.h>
#else
#include <sys/stat.h>
#endif

namespace fnvcoop::fuzz {

RunResult run(const Scenario& s, const RunOptions& opt) {
    RunResult r;
    std::string err;
    setKeepMessages(!opt.traceDir.empty());
    auto t = makeTarget(opt.target, s, &err);
    if (!t) {
        r.ok = false;
        r.violations.push_back({"setup", err, 0});
        return r;
    }
    for (const Op& op : s.ops) {
        t->advance(op.t);
        t->apply(op);
        ++r.opsRun;
    }
    t->advance(std::max<uint64_t>(s.durationMs, t->now()));
    t->heal();

    // Wait for quiescence; one more second must stay quiet too (ownership re-evaluation and heartbeats can still fire right after the heal).
    uint64_t start = t->now();
    bool quiet = false;
    while (t->now() - start < opt.quiesceCapMs) {
        while (!t->quiescent() && t->now() - start < opt.quiesceCapMs) t->advance(t->now() + 100);
        if (!t->quiescent()) break;
        t->advance(t->now() + 1000);
        if (t->quiescent()) { quiet = true; break; }
    }
    if (!quiet) r.violations.push_back({"not_quiescent", "traffic still moving " + std::to_string(opt.quiesceCapMs) + " ms after the link was healed", t->now()});
    if (quiet) t->markQuiescent();
    checkConverged(*t, r.violations);

    uint64_t repairsBefore = 0;
    for (int m = 0; m < t->machines(); ++m) repairsBefore += t->repairs(m);
    t->advance(t->now() + opt.afterQuiesceMs);
    uint64_t repairsAfter = 0;
    for (int m = 0; m < t->machines(); ++m) repairsAfter += t->repairs(m);
    if (quiet && repairsAfter > repairsBefore)
        r.violations.push_back({"repairs_after_quiescence", std::to_string(repairsAfter - repairsBefore) + " repairs in the " + std::to_string(opt.afterQuiesceMs) + " ms after quiescence (must be 0)", t->now()});
    checkLogs(*t, r.violations);
    for (auto& v : t->nativeViolations()) r.violations.push_back(v);

    r.ok = r.violations.empty();
    r.stats = t->stats();
    r.virtualMs = t->now();
    if (!r.ok && !opt.traceDir.empty()) writeTraces(*t, s, r, opt.traceDir, opt.traceName);
    return r;
}

static bool hasInvariant(const RunResult& r, const std::string& inv) {
    for (auto& v : r.violations) if (v.invariant == inv) return true;
    return false;
}

Scenario shrink(const Scenario& s0, const RunOptions& opt0, const std::string& invariant, int* runsOut, bool onlyWithBugs) {
    RunOptions opt = opt0;
    opt.traceDir.clear();
    int runs = 0;
    const int kMaxRuns = 4000;
    auto fails = [&](const Scenario& c) {
        if (runs >= kMaxRuns) return false;
        ++runs;
        if (!hasInvariant(run(c, opt), invariant)) return false;
        if (!onlyWithBugs || c.bugs.empty()) return true;
        Scenario clean = c;
        clean.bugs.clear();
        ++runs;
        return run(clean, opt).ok;   // the injected bugs, not the real code, must be what breaks it
    };
    Scenario best = s0;
    // ddmin over ops
    std::vector<Op> ops = best.ops;
    size_t n = 2;
    while (ops.size() >= 2 && runs < kMaxRuns) {
        size_t chunk = (ops.size() + n - 1) / n;
        bool reduced = false;
        for (size_t start = 0; start < ops.size(); start += chunk) {
            std::vector<Op> comp;
            comp.reserve(ops.size());
            for (size_t i = 0; i < ops.size(); ++i) if (i < start || i >= start + chunk) comp.push_back(ops[i]);
            Scenario c = best; c.ops = comp;
            if (fails(c)) { ops = comp; n = std::max<size_t>(n - 1, 2); reduced = true; break; }
        }
        if (!reduced) {
            if (n >= ops.size()) break;
            n = std::min(n * 2, ops.size());
        }
    }
    // a lone op may also be removable (needs the empty list to fail on its own)
    if (ops.size() == 1) { Scenario c = best; c.ops.clear(); if (fails(c)) ops.clear(); }
    best.ops = ops;
    // simplify the environment
    auto tryScenario = [&](Scenario c) { if (fails(c)) { best = c; return true; } return false; };
    if (!best.profile.empty()) { Scenario c = best; c.profile.clear(); tryScenario(c); }   // a plain link, if the bug does not need the chaos profile
    { Scenario c = best; c.link = LinkParams(); tryScenario(c); }
    { Scenario c = best; c.link.corrupt = 0; tryScenario(c); }
    { Scenario c = best; c.link.duplicate = 0; tryScenario(c); }
    { Scenario c = best; c.link.loss = 0; tryScenario(c); }
    { Scenario c = best; c.link.maxLatencyMs = c.link.minLatencyMs; tryScenario(c); }
    for (int m = best.machines; m > 2; --m) {
        bool used = false;
        for (auto& o : best.ops) if (o.m >= m - 1 || (o.kind == OpKind::Stale && o.a >= m - 1) || (o.kind == OpKind::SimulKill && o.b >= m - 1)) used = true;
        if (used) break;
        Scenario c = best; c.machines = m - 1;
        if (!tryScenario(c)) break;
    }
    { Scenario c = best; c.durationMs = best.ops.empty() ? 1 : best.ops.back().t + 1; tryScenario(c); }
    if (runsOut) *runsOut = runs;
    return best;
}

// ------------------------------------------------------------------ traces
static void mkdirs(const std::string& dir) {
    std::string cur;
    for (size_t i = 0; i <= dir.size(); ++i) {
        if (i == dir.size() || dir[i] == '/' || dir[i] == '\\') {
            if (!cur.empty() && cur.back() != ':') {
#ifdef _WIN32
                _mkdir(cur.c_str());
#else
                mkdir(cur.c_str(), 0755);
#endif
            }
        }
        if (i < dir.size()) cur += dir[i];
    }
}
static std::string esc(const std::string& s) {
    std::string o;
    for (char c : s) { if (c == '"' || c == '\\') o += '\\'; if (static_cast<unsigned char>(c) >= 32) o += c; }
    return o;
}

bool writeTraces(const ITarget& t, const Scenario& s, const RunResult& r, const std::string& dir, const std::string& name) {
    mkdirs(dir);
    std::string base = dir + "/" + name;
    { std::ofstream f(base + ".scenario.json", std::ios::binary); f << toJson(s); if (!f) return false; }
    // JSON trace: same field meaning as the NVFR record kinds (net out/in, ownership, applied, digest-free).
    std::ofstream f(base + ".trace.json", std::ios::binary);
    if (!f) return false;
    f << "{\n \"format\": \"simfuzz-trace-1\", \"seed\": " << s.seed << ", \"machines\": " << t.machines() << ",\n \"violations\": [";
    for (size_t i = 0; i < r.violations.size(); ++i)
        f << (i ? ", " : "") << "{\"invariant\": \"" << r.violations[i].invariant << "\", \"detail\": \"" << esc(r.violations[i].detail) << "\"}";
    f << "],\n \"own\": [";
    bool first = true;
    for (auto& o : t.ownLog()) {
        f << (first ? "\n  " : ",\n  ") << "{\"t\": " << o.tMs << ", \"ord\": " << o.ord << ", \"op\": " << int(o.kind) << ", \"ref\": " << o.ref << ", \"owner\": " << o.owner
          << ", \"prev\": " << o.prevOwner << ", \"epoch\": " << o.epoch << "}";
        first = false;
    }
    f << "\n ],\n \"applied\": [";
    first = true;
    for (auto& e : t.applyLog()) {
        f << (first ? "\n  " : ",\n  ") << "{\"t\": " << e.tMs << ", \"ord\": " << e.ord << ", \"m\": " << e.machine << ", \"kind\": \"" << applyKindName(e.kind) << "\", \"ref\": " << e.ref
          << ", \"epoch\": " << e.epoch << ", \"seq\": " << e.seq << ", \"origin\": " << e.origin << ", \"idem\": " << e.idem << ", \"accepted\": " << (e.accepted ? "true" : "false")
          << ", \"why\": \"" << esc(e.why) << "\"}";
        first = false;
    }
    f << "\n ],\n \"net\": [";
    first = true;
    for (auto& m : t.msgLog()) {
        f << (first ? "\n  " : ",\n  ") << "{\"t\": " << m.tMs << ", \"m\": " << m.machine << ", \"dir\": \"" << (m.out ? "out" : "in") << "\", \"peer\": " << m.peer << ", \"ch\": " << int(m.channel)
          << ", \"len\": " << m.raw.size() << "}";
        first = false;
    }
    f << "\n ]\n}\n";

#ifdef FNVCOOP_FUZZ_FLIGHT
    if (t.dumpFlight(base, r.violations)) return true;   // the target recorded through the engine observer: one recorder path
    // One .nvfr per machine in the T1a format (docs/tools/flight-format.md): net out/in datagrams (our own packets, not decodable
    // as synccore messages), ownership grants / transfers / releases and rejections, markers for the other rejections and the
    // violations, and a final digest per category.
    for (int m = 0; m < t.machines(); ++m) {
        uint64_t nowMs = 0;
        flight::Config cfg;
        cfg.slot = static_cast<uint8_t>(m);
        cfg.role = m == 0 ? flight::kRoleHost : flight::kRoleClient;
        cfg.label = "simfuzz-m" + std::to_string(m);
        cfg.ringBytes = 64u << 20;
        flight::FlightRecorder rec(cfg);
        rec.setClockSource([&nowMs] { return nowMs * 1000; });
        rec.setWallStart(0);
        struct Ref { uint64_t t, ord; int kind; size_t idx; };
        std::vector<Ref> evs;
        const auto& net = t.msgLog();
        for (size_t i = 0; i < net.size(); ++i) if (net[i].machine == m) evs.push_back({net[i].tMs, 0, 0, i});
        if (m == 0) for (size_t i = 0; i < t.ownLog().size(); ++i) evs.push_back({t.ownLog()[i].tMs, t.ownLog()[i].ord, 1, i});
        for (size_t i = 0; i < t.applyLog().size(); ++i)
            if (t.applyLog()[i].machine == m && !t.applyLog()[i].accepted) evs.push_back({t.applyLog()[i].tMs, t.applyLog()[i].ord, 2, i});
        std::stable_sort(evs.begin(), evs.end(), [](const Ref& a, const Ref& b) { return a.t != b.t ? a.t < b.t : a.ord < b.ord; });
        auto slot = [](int v) { return v < 0 ? flight::kNoSlot : static_cast<uint8_t>(v); };
        for (auto& e : evs) {
            nowMs = e.t;
            rec.setFrame(static_cast<uint32_t>(e.t / 16));
            if (e.kind == 0) {
                const MsgTrace& mt = net[e.idx];
                if (mt.out) rec.netOut(static_cast<PeerId>(mt.peer), mt.raw, mt.channel);
                else rec.netIn(static_cast<PeerId>(mt.peer), mt.raw);
            } else if (e.kind == 1) {
                const OwnEvent& o = t.ownLog()[e.idx];
                if (o.kind == OwnEvent::WorldEpoch) { rec.marker(flight::kMarkNote, "world epoch " + std::to_string(o.epoch) + " (host load)"); continue; }
                flight::OwnershipEvent oe;
                oe.op = o.kind == OwnEvent::Grant ? flight::kOwnGrant : o.kind == OwnEvent::Transfer ? flight::kOwnTransfer : flight::kOwnRelease;
                oe.actor = static_cast<uint64_t>(o.ref);
                oe.epoch = o.epoch;
                oe.fromOwner = slot(o.prevOwner);
                oe.toOwner = slot(o.owner);
                rec.ownership(oe);
            } else {
                const ApplyEvent& a = t.applyLog()[e.idx];
                std::string why = a.why ? a.why : "";
                bool isStale = why.find("stale") != std::string::npos, isOwner = why.find("owner") != std::string::npos, isEcho = why.find("echo") != std::string::npos || why.find("Echo") != std::string::npos;
                if (a.ref >= 0 && (isStale || isOwner || isEcho)) {
                    flight::OwnershipEvent oe;
                    oe.op = isEcho ? flight::kOwnRejectEcho : (isStale ? flight::kOwnRejectStale : flight::kOwnRejectNonOwner);
                    oe.actor = static_cast<uint64_t>(a.ref);
                    oe.epoch = a.epoch;
                    oe.fromOwner = slot(a.origin);
                    oe.toOwner = static_cast<uint8_t>(m);
                    rec.ownership(oe);
                } else {
                    rec.marker(flight::kMarkNote, std::string("reject ") + applyKindName(a.kind) + " ref " + std::to_string(a.ref) + ": " + why);
                }
            }
        }
        nowMs = t.now();
        for (int c = 0; c < kDigestCats; ++c) {
            flight::DigestEvent d;
            d.category = static_cast<uint8_t>(c);
            d.seq = 0xF0000000u + static_cast<uint64_t>(c);   // same id on every machine: the final end-of-run point
            d.hash = t.digest(m, c);
            rec.digest(d);
        }
        for (auto& v : r.violations) rec.marker(flight::kMarkDesync, v.invariant + ": " + v.detail);
        rec.dump(base + ".m" + std::to_string(m) + ".nvfr", flight::kDumpManual);
    }
#endif
    return true;
}

}  // namespace fnvcoop::fuzz
