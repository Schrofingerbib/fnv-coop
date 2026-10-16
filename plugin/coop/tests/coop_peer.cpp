// coop_peer: one co-op node (host or client) over the real UDP transport with a FakeWorld engine.
// Used by the two-process integration tests, and as the "fake client" for the in-game host test.
//
//   coop_peer --role host   [--bind 127.0.0.1] [--port 0]
//   coop_peer --role client [--host 127.0.0.1] --port N [--token T]
//
// stdin commands (one per line), stdout replies (one line each, flushed):
//   scope <json>            host: run a capture scope on the fake engine ({"cause":..,"effects":[..{"scope":..}]})
//   inject <json>           host: commit as if captured; client: apply as if received
//   wait_joined <n> <ms>    host: n clients joined; client: joined            -> OK wait_joined | TIMEOUT wait_joined
//   wait_seq <n> <ms>       host: head>=n and all clients acked; client: applied>=n
//   wait_txns <n> <ms>      client: at least n log entries                    (for in-game tests where the host is the game)
//   dump | status | log     -> DUMP/STATUS/LOG <json>
//   quit
// Unsolicited lines:  READY <json>,  TXN <json> for every commit/apply log entry.
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <deque>
#include <iostream>
#include <mutex>
#include <string>
#include <vector>
#include <thread>

#include "baseline_file.h"
#include "fake_world.h"

using namespace nvcoop::coop;
using namespace nvcoop::coop::test;
using nvcoop::json::Value;

namespace {
// Dumper backed by a baseline file (exported from a real game) with deliberate, counted differences.
class FileDumper : public IWorldDumper {
public:
    fnvcoop::ShadowState original, mutated, target;   // target: what verify_baseline compares against (default: original)
    bool haveTarget = false;
    uint64_t hash = 0;
    Value report = Value::object();
    int mutatedEntries = 0;   // entries whose value differs or that are missing on the client
    bool dump(fnvcoop::ShadowState* out, uint64_t* h, std::string*) override { *out = mutated; *h = hash; return true; }

    bool load(const std::string& path) {
        if (!LoadBaselineFile(path, &original, &hash)) return false;
        mutated = original;
        return true;
    }
    void mutate() {
        auto& m = mutated;
        Value changed = Value::object(), stagesTo = Value::object(), runningTo = Value::object();
        int n = 0;
        // stages: 5 quests moved ahead, 3 untouched-at-zero quests set to 9
        int a = 0, b = 0;
        for (auto& [k, v] : m.stages) {
            if (v > 0 && a < 5) { changed.set("stage:" + nvhooks::refToStr(k), (int64_t)v); v += 3; stagesTo.set(nvhooks::refToStr(k), (int64_t)v); ++a; ++n; }
            else if (v == 0 && b < 3) { changed.set("stage:" + nvhooks::refToStr(k), (int64_t)v); v = 9; stagesTo.set(nvhooks::refToStr(k), (int64_t)v); ++b; ++n; }
        }
        int g = 0;
        for (auto& [k, v] : m.globals) if (g < 4) { changed.set("global:" + nvhooks::refToStr(k), (double)v); v += 1.0f; ++g; ++n; }
        std::vector<fnvcoop::FormRef> last;
        for (auto it = m.globals.rbegin(); it != m.globals.rend() && last.size() < 2; ++it) last.push_back(it->first);
        for (auto& key : last) {
            changed.set("global:" + nvhooks::refToStr(key), (double)m.globals[key]);
            m.globals.erase(key);   // missing on the client: the host must send it
            ++n;
        }
        int o = 0;
        for (auto& [k, v] : m.objectives) if (o < 3) { changed.set("obj:" + nvhooks::refToStr(k.first) + "#" + std::to_string(k.second), (int64_t)v); v = (uint8_t)((v + 1) & 3); ++o; ++n; }
        int r = 0;
        for (auto& [k, v] : m.questRunning) if (r < 3) { changed.set("running:" + nvhooks::refToStr(k), v); v = !v; runningTo.set(nvhooks::refToStr(k), v); ++r; ++n; }
        int64_t karmaHost = m.ledger[0].karma;
        m.ledger[0].karma += 5;
        changed.set("karma", karmaHost);
        mutatedEntries = n;
        report.set("stagesTo", stagesTo).set("runningTo", runningTo).set("changed", changed).set("mutatedEntries", n).set("totalStages", (int64_t)original.stages.size())
            .set("totalGlobals", (int64_t)original.globals.size()).set("totalObjectives", (int64_t)original.objectives.size())
            .set("totalQuestRunning", (int64_t)original.questRunning.size());
    }
    // load the subset FakeWorld understands, so applying the baseline effects visibly repairs it
    void toWorld(FakeWorld& w) const {
        for (auto& [k, v] : mutated.stages) w.stages[nvhooks::refToStr(k)] = v;
        for (auto& [k, v] : mutated.globals) w.globals[nvhooks::refToStr(k)] = v;
        for (auto& [k, v] : mutated.objectives) w.objectives[nvhooks::refToStr(k.first) + "#" + std::to_string(k.second)] = v;
        for (auto& [k, v] : mutated.questRunning) w.questRunning[nvhooks::refToStr(k)] = v;
        for (auto& [k, v] : mutated.enabled) w.enabled[nvhooks::refToStr(k)] = v;
        w.karma = mutated.ledger[0].karma;
    }
    // compare the world against the original (host) values for the subset FakeWorld holds
    int mismatches(FakeWorld& w, std::string* first) const {
        const fnvcoop::ShadowState& tgt = haveTarget ? target : original;
        int bad = 0;
        auto note = [&](const std::string& s2) { if (!bad++ && first) *first = s2; };
        for (auto& [k, v] : tgt.stages) { auto it = w.stages.find(nvhooks::refToStr(k)); if (it == w.stages.end() || it->second != v) note("stage " + nvhooks::refToStr(k)); }
        for (auto& [k, v] : tgt.globals) { auto it = w.globals.find(nvhooks::refToStr(k)); if (it == w.globals.end() || it->second != v) note("global " + nvhooks::refToStr(k)); }
        for (auto& [k, v] : tgt.objectives) { auto it = w.objectives.find(nvhooks::refToStr(k.first) + "#" + std::to_string(k.second)); if (it == w.objectives.end() || it->second != v) note("objective " + nvhooks::refToStr(k.first)); }
        for (auto& [k, v] : tgt.questRunning) { auto it = w.questRunning.find(nvhooks::refToStr(k)); if (it == w.questRunning.end() || it->second != v) note("running " + nvhooks::refToStr(k)); }
        if (w.karma != tgt.ledger[0].karma) note("karma");
        return bad;
    }
};

uint64_t nowMs() {
    return std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now().time_since_epoch()).count();
}
void out(const std::string& s) { std::fwrite(s.data(), 1, s.size(), stdout); std::fputc('\n', stdout); std::fflush(stdout); }

std::mutex g_mu;
std::deque<std::string> g_cmds;
std::atomic<bool> g_eof{false};

void readerThread() {
    std::string line;
    while (std::getline(std::cin, line)) {
        std::lock_guard<std::mutex> lk(g_mu);
        g_cmds.push_back(line);
    }
    g_eof = true;
}
}  // namespace

int main(int argc, char** argv) {
    Config cfg;
    cfg.enable = true;
    cfg.listen = "127.0.0.1";
    cfg.port = 0;
    std::string roleStr;
    int seedQuests = 0;
    std::string baselineFile, verifyFile, explainSpec;
    bool explained = false;
    bool mutate = false;
    std::vector<std::string> sets;
    for (int i = 1; i < argc; ++i) {
        auto arg = [&](const char* name) { return std::strcmp(argv[i], name) == 0 && i + 1 < argc; };
        if (arg("--role")) roleStr = argv[++i];
        else if (arg("--bind")) cfg.listen = argv[++i];
        else if (arg("--host")) cfg.host = argv[++i];
        else if (arg("--port")) cfg.port = static_cast<uint16_t>(std::atoi(argv[++i]));
        else if (arg("--transport")) cfg.transport = argv[++i];
        else if (arg("--seed")) seedQuests = std::atoi(argv[++i]);
        else if (arg("--baseline")) cfg.baseline = std::atoi(argv[++i]) != 0;
        else if (arg("--set")) sets.push_back(argv[++i]);   // stage:<ref>=<n> | global:<ref>=<f> | obj:<ref>#<i>=<flags> | karma:=<n>
        else if (arg("--baseline-file")) baselineFile = argv[++i];
        else if (arg("--explain")) explainSpec = argv[++i];     // "ref:var,ref:var": print client.explainSummaryEntry while the baseline runs
        else if (arg("--verify-file")) verifyFile = argv[++i];
        else if (arg("--mutate")) mutate = std::atoi(argv[++i]) != 0;
        else if (arg("--token")) cfg.token = std::strtoull(argv[++i], nullptr, 0);
    }
    cfg.role = roleStr == "host" ? Role::Host : roleStr == "client" ? Role::Client : Role::Off;
    if (cfg.role == Role::Off) { std::fprintf(stderr, "usage: coop_peer --role host|client ...\n"); return 2; }

    nvhooks::CaptureHub hub;
    FakeWorld world;
    world.seed(seedQuests);
    for (auto& st : sets) {
        size_t c = st.find(':'), e = st.rfind('=');
        std::string kind = st.substr(0, c), key = st.substr(c + 1, e - c - 1), val = st.substr(e + 1);
        if (kind == "stage") world.stages[key] = (uint32_t)std::stoul(val);
        else if (kind == "global") world.globals[key] = std::stof(val);
        else if (kind == "obj") world.objectives[key] = std::stoi(val);
        else if (kind == "karma") world.karma = std::stoll(val);
        else if (kind == "running") world.questRunning[key] = val != "0";
    }
    Runtime rt(cfg, &hub, &world, nowMs);
    FileDumper fileDumper;
    if (!baselineFile.empty()) {
        if (!fileDumper.load(baselineFile)) { out("ERROR cannot load baseline file"); return 3; }
        if (!verifyFile.empty()) { uint64_t h2 = 0; if (!LoadBaselineFile(verifyFile, &fileDumper.target, &h2)) { out("ERROR cannot load verify file"); return 3; } fileDumper.haveTarget = true; }
        if (mutate) fileDumper.mutate();
        fileDumper.toWorld(world);
        rt.setDumper(&fileDumper);
    } else {
        rt.setDumper(&world);
    }
    rt.setActionExecutor([&](fnvcoop::PlayerId slot, fnvcoop::IntentKind k, const fnvcoop::FormRef& r) { world.executeAction(hub, slot, k, r); });
    rt.onLog = [](const LogEntry& e) {
        fnvcoop::EffectTxn t;
        t.origin = e.origin; t.cause = e.cause; t.effects = e.effects;
        Value v = nvhooks::txnToJson(t);
        v.set("dir", e.dir).set("seq", (int64_t)e.seq);
        if (!e.results.empty()) { Value r = Value::array(); for (auto& s : e.results) r.push(s); v.set("results", r); }
        out("TXN " + v.dump());
    };
    std::string err;
    if (!rt.start(&err)) { out("ERROR " + err); return 3; }
    {
        Value r = Value::object();
        r.set("role", roleName(cfg.role)).set("port", (int64_t)rt.boundPort());
        out("READY " + r.dump());
    }

    std::thread(readerThread).detach();

    struct Wait { std::string kind; int64_t n = 0; uint64_t deadline = 0; bool active = false; } wait;

    auto joinedCount = [&]() -> int64_t {
        if (rt.host()) { int64_t n = 0; for (auto& c : rt.host()->clients()) n += c.joined; return n; }
        return rt.client() && rt.client()->joined() ? 1 : 0;
    };
    auto waitDone = [&]() -> bool {
        if (wait.kind == "wait_joined") return joinedCount() >= wait.n;
        if (wait.kind == "wait_seq") {
            if (rt.host()) return (int64_t)rt.host()->log().head() >= wait.n && rt.host()->allSynced();
            return (int64_t)rt.client()->applied() >= wait.n;
        }
        if (wait.kind == "wait_baseline") return rt.client() && rt.client()->baselineDone();
        if (wait.kind == "wait_txns") return (int64_t)rt.counters().applied >= wait.n;
        return true;
    };

    for (;;) {
        rt.frame();
        if (!explainSpec.empty() && !explained && rt.client() && rt.client()->baselineActive()) {
            explained = true;
            std::string spec = explainSpec;
            size_t pos = 0;
            while (pos < spec.size()) {
                size_t e = spec.find(',', pos);
                std::string item = spec.substr(pos, e == std::string::npos ? std::string::npos : e - pos);
                pos = e == std::string::npos ? spec.size() : e + 1;
                size_t c = item.rfind(':');
                fnvcoop::FormRef fr;
                if (c == std::string::npos || !nvhooks::strToRef(item.substr(0, c), &fr)) continue;
                fnvcoop::SetScriptVar probe;
                probe.owner = fr;
                probe.index = static_cast<uint16_t>(std::atoi(item.c_str() + c + 1));
                out("EXPLAIN " + item + " :: " + rt.client()->explainSummaryEntry(probe));
            }
        }
        std::string cmd;
        {
            std::lock_guard<std::mutex> lk(g_mu);
            if (!wait.active && !g_cmds.empty()) { cmd = g_cmds.front(); g_cmds.pop_front(); }
        }
        if (wait.active) {
            if (waitDone()) { out("OK " + wait.kind); wait.active = false; }
            else if (nowMs() > wait.deadline) { out("TIMEOUT " + wait.kind); wait.active = false; }
        }
        if (cmd.empty()) {
            if (g_eof) { std::lock_guard<std::mutex> lk(g_mu); if (g_cmds.empty() && !wait.active) break; }
            std::this_thread::sleep_for(std::chrono::milliseconds(2));
            continue;
        }
        size_t sp = cmd.find(' ');
        std::string verb = cmd.substr(0, sp), rest = sp == std::string::npos ? "" : cmd.substr(sp + 1);
        if (verb == "quit") break;
        if (verb == "scope") {
            Value v;
            std::string perr;
            if (!rt.host()) { out("ERROR scope: not a host"); continue; }
            if (!nvcoop::json::parse(rest, v, perr)) { out("ERROR scope: " + perr); continue; }
            world.runScope(hub, v);
            rt.frame();
            out("OK scope");
        } else if (verb == "inject") {
            std::string res, ierr;
            if (rt.inject(rest, &res, &ierr)) out("OK inject " + res); else out("ERROR inject: " + ierr);
        } else if (verb == "wait_joined" || verb == "wait_seq" || verb == "wait_txns" || verb == "wait_baseline") {
            long long n = 0, ms = 0;
            std::sscanf(rest.c_str(), "%lld %lld", &n, &ms);
            wait.kind = verb; wait.n = n; wait.deadline = nowMs() + (uint64_t)ms; wait.active = true;
        } else if (verb == "action") {
            char kind[32] = {};
            char ref[128] = {};
            std::sscanf(rest.c_str(), "%31s %127s", kind, ref);
            fnvcoop::FormRef fr;
            fnvcoop::IntentKind k = std::strcmp(kind, "scenepass") == 0 ? fnvcoop::IntentKind::ScenePass
                                  : std::strcmp(kind, "reportcell") == 0 ? fnvcoop::IntentKind::ReportCell
                                  : std::strcmp(kind, "pickup") == 0 ? fnvcoop::IntentKind::PickUp
                                  : std::strcmp(kind, "trigger") == 0 ? fnvcoop::IntentKind::TriggerEnter : fnvcoop::IntentKind::ActivateRef;
            if (!nvhooks::strToRef(ref, &fr)) { out("ERROR action: bad ref"); continue; }
            uint32_t id = rt.submitAction(k, fr);
            out(id ? "OK action " + std::to_string(id) : std::string("ERROR action: not joined or syncing"));
        } else if (verb == "mutation") {
            out("MUTATION " + fileDumper.report.dump());
        } else if (verb == "verify_baseline") {
            std::string first;
            int bad = fileDumper.mismatches(world, &first);
            Value r = Value::object();
            r.set("mismatches", bad).set("first", first);
            out("VERIFY " + r.dump());
        } else if (verb == "dump") {
            out("DUMP " + world.dump().dump());
        } else if (verb == "status") {
            out("STATUS " + rt.statusJson());
        } else if (verb == "log") {
            out("LOG " + rt.logJson(512));
        } else {
            out("ERROR unknown command: " + verb);
        }
    }
    // final flush of the transport so a host does not vanish with unsent retransmits
    rt.stop();
    return 0;
}
