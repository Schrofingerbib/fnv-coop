// Long-running soak simulation with a realistic Goodsprings traffic shape.
//
//   fnvcoop_soak [--seconds N] [--players P] [--seed S] [--threaded] [--profile F] [--rules F] [--quiet]
//                [--late-disconnect] [--mutate late-reconnect]
//
// Simulates P players (1 host + P-1 clients) for N *virtual* seconds at 30 fps over a lossy link, replaying the effects of the
// VCG01 / VCG02 / VMS16 stage and object scripts (from L3's static analysis, see tools/make_profile.py), per-frame quest-variable
// writes through the StateReplicator, the VCG01 chargen barrier through the rules engine, guest action requests, loot / kill intents,
// client disconnects, restarts with baseline re-joins, host reloads of older saves, and rare silent world corruption (digest heal).
// Reports bytes/s per client, worst frame time, and verifies that every world converges. Exit code 0 = converged.
#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <memory>
#include <set>
#include <sstream>
#include <string>
#include <thread>
#include <vector>
#include "fnvcoop/baseline.h"
#include "fnvcoop/replicator.h"
#include "fnvcoop/rules.h"
#include "fnvcoop/session.h"

using namespace fnvcoop;
using Clock = std::chrono::steady_clock;

namespace {

struct Scene {
    std::string quest, name;
    uint32_t stage = 0;
    int varWrites = 0;
    std::vector<Effect> effects;
};
struct Profile {
    std::map<std::string, FormRef> quests;
    std::vector<Scene> stages, blocks;
    std::map<std::string, int> perFrame;
};

FormRef parseForm(const std::string& t) {
    auto c = t.rfind(':');
    return FormRef::make(t.substr(0, c), static_cast<uint32_t>(std::strtoul(t.c_str() + c + 1, nullptr, 16)));
}

bool loadProfile(const std::string& path, Profile& p) {
    std::ifstream f(path);
    if (!f) return false;
    std::string line;
    Scene* cur = nullptr;
    while (std::getline(f, line)) {
        if (line.empty() || line[0] == '#') continue;
        std::istringstream ls(line);
        std::string k;
        ls >> k;
        if (k == "Q") { std::string e, f2; ls >> e >> f2; p.quests[e] = parseForm(f2); }
        else if (k == "V") { std::string e; int n = 0; ls >> e >> n; p.perFrame[e] = n; }
        else if (k == "S") { Scene s; ls >> s.quest >> s.stage >> s.varWrites; s.name = s.quest + ":" + std::to_string(s.stage); p.stages.push_back(std::move(s)); cur = &p.stages.back(); }
        else if (k == "B") { Scene s; std::string blk, owner; ls >> s.name >> blk >> owner >> s.varWrites; s.name += "/" + blk; p.blocks.push_back(std::move(s)); cur = &p.blocks.back(); }
        else if (k == "E" && cur) {
            std::string t; ls >> t;
            std::string a, b, c;
            if (t == "SetStage") { ls >> a >> b; cur->effects.push_back(SetStage{parseForm(a), uint32_t(std::atoi(b.c_str()))}); }
            else if (t == "SetObjective") { ls >> a >> b >> c; cur->effects.push_back(SetObjective{parseForm(a), uint32_t(std::atoi(b.c_str())), uint8_t(std::atoi(c.c_str()))}); }
            else if (t == "SetGlobal") { ls >> a >> b; cur->effects.push_back(SetGlobal{parseForm(a), float(std::atof(b.c_str()))}); }
            else if (t == "EnableRef") { ls >> a; cur->effects.push_back(EnableRef{parseForm(a)}); }
            else if (t == "DisableRef") { ls >> a; cur->effects.push_back(DisableRef{parseForm(a)}); }
            else if (t == "AddItem") { ls >> a >> b; cur->effects.push_back(AddItem{parseForm(a), std::atoi(b.c_str()), Recipient::dflt()}); }
            else if (t == "RemoveItem") { ls >> a >> b; cur->effects.push_back(RemoveItem{parseForm(a), std::atoi(b.c_str()), Recipient::dflt()}); }
            else if (t == "ModActorValue") { ls >> a >> b; cur->effects.push_back(ModActorValue{a == "1" ? kAvExperience : 2u, std::atoi(b.c_str()), Recipient::dflt()}); }
            else if (t == "ModReputation") { ls >> a >> b >> c; cur->effects.push_back(ModReputation{parseForm(a), std::atoi(b.c_str()), std::atoi(c.c_str()), Recipient::dflt()}); }
            else if (t == "StartQuest") { ls >> a; cur->effects.push_back(StartQuest{parseForm(a)}); }
            else if (t == "StopQuest") { ls >> a; cur->effects.push_back(StopQuest{parseForm(a)}); }
            else if (t == "CompleteQuest") { ls >> a; /* sticky flag: a host reload cannot un-set it (documented limitation), keep it out of the soak */ }
            else if (t == "MoveRef") { ls >> a >> b; cur->effects.push_back(MoveRef{parseForm(a), parseForm(b), 0.f, 0.f, 0.f, false}); }
        }
    }
    return !p.stages.empty();
}

// A stand-in for one client's game engine.
struct Engine : IEffectSink {
    ShadowState w;
    void apply(const Effect& e, const Context& c) override {
        if (auto* k = std::get_if<ModKarma>(&e)) { w.ledger[0].karma += k->delta; return; }
        if (auto* r = std::get_if<ModReputation>(&e)) { auto& v = w.ledger[0].rep[r->faction]; v.first += r->fame; v.second += r->infamy; if (v.first == 0 && v.second == 0) w.ledger[0].rep.erase(r->faction); return; }
        if (std::holds_alternative<AddItem>(e) || std::holds_alternative<RemoveItem>(e) || std::holds_alternative<ModActorValue>(e) ||
            std::holds_alternative<SetPerk>(e) || std::holds_alternative<SetPlayerControls>(e))
            return;   // per-player inventory is not part of the shared-world comparison
        if (c.baseline) {
            std::visit([&](const auto& x) {
                using T = std::decay_t<decltype(x)>;
                if constexpr (std::is_same_v<T, SetStage>) w.stages[x.quest] = x.stage;
                else if constexpr (std::is_same_v<T, SetScriptVar>) w.scriptVars[{x.owner, x.index}] = x.value;
                else if constexpr (std::is_same_v<T, SetGlobal>) w.globals[x.global] = x.value;
                else if constexpr (std::is_same_v<T, KillActor>) w.kills.insert(x.actor);
                else if constexpr (std::is_same_v<T, ResurrectActor>) w.kills.erase(x.actor);
                else if constexpr (std::is_same_v<T, EnableRef>) w.enabled[x.ref] = true;
                else if constexpr (std::is_same_v<T, DisableRef>) w.enabled[x.ref] = false;
                else if constexpr (std::is_same_v<T, SetObjective>) w.objectives[{x.quest, x.objective}] = x.flags;
                else { EffectTxn t; t.effects = {Effect(x)}; w.applyTxn(t); }
            }, e);
        } else {
            EffectTxn t; t.effects = {e}; w.applyTxn(t);
        }
    }
};

struct Pct { double p50 = 0, p99 = 0, max = 0; };
Pct pct(std::vector<float>& v) {
    Pct r;
    if (v.empty()) return r;
    std::sort(v.begin(), v.end());
    r.p50 = v[v.size() / 2];
    r.p99 = v[std::min(v.size() - 1, static_cast<size_t>(double(v.size()) * 0.99))];
    r.max = v.back();
    return r;
}

struct Opts {
    double seconds = 120;
    int players = 4;
    uint64_t seed = 1;
    bool threaded = false, quiet = false;
    bool lateDisconnect = false;   // client 1 drops on the last event frame with the longest reconnect delay (regression for issue #43)
    bool mutLateReconnect = false; // TEST ONLY twin: reconnects are not pulled into the quiet window (the #43 harness bug)
    std::string profile, rules;
};

ShadowState makeBase(const Profile& p, int quests) {
    ShadowState s;
    for (int i = 0; i < quests; ++i) {
        FormRef q("falloutnv.esm", 0x200000 + uint32_t(i));
        s.stages[q] = uint32_t(i % 100);
        s.scriptVars[{q, uint16_t(i % 4)}] = double(i % 50) + 0.25;
        s.objectives[{q, uint32_t(i % 3)}] = uint8_t(i % 4);
        if (i % 2) s.globals[FormRef("falloutnv.esm", 0x300000 + uint32_t(i))] = float(i);
    }
    for (auto& [edid, f] : p.quests) { s.stages[f] = 0; s.questRunning[f] = false; }
    // A real engine dump is complete: every key the scripts can touch exists with its default value, so that a host reload
    // (rewind) never leaves a client holding a key the host does not know. Mirror that for everything the profile writes.
    auto keys = [&](const Scene& sc) {
        for (auto& e : sc.effects) std::visit([&](const auto& x) {
            using T = std::decay_t<decltype(x)>;
            if constexpr (std::is_same_v<T, SetStage>) s.stages.emplace(x.quest, 0u);
            else if constexpr (std::is_same_v<T, SetObjective>) s.objectives.emplace(std::make_pair(x.quest, x.objective), uint8_t(0));
            else if constexpr (std::is_same_v<T, SetGlobal>) s.globals.emplace(x.global, 0.f);
            else if constexpr (std::is_same_v<T, EnableRef> || std::is_same_v<T, DisableRef>) s.enabled.emplace(x.ref, true);
            else if constexpr (std::is_same_v<T, StartQuest> || std::is_same_v<T, StopQuest>) s.questRunning.emplace(x.quest, false);
            else if constexpr (std::is_same_v<T, MoveRef>) s.placed.emplace(x.ref, Placement{x.cell, 0.f, 0.f, 0.f});
        }, e);
    };
    for (auto& sc : p.stages) keys(sc);
    for (auto& sc : p.blocks) keys(sc);
    s.ledger[0].karma = 5;
    return s;
}

int run(const Opts& o) {
    Profile prof;
    if (!loadProfile(o.profile, prof)) { std::printf("cannot load profile %s\n", o.profile.c_str()); return 2; }
    RulesDb db;
    std::string err;
    if (!o.rules.empty() && !db.loadFile(o.rules, &err)) { std::printf("cannot load rules: %s\n", err.c_str()); return 2; }
    Rng rng(o.seed);
    InMemoryNetwork net(o.seed + 17);
    LinkParams lp; lp.minLatencyMs = 30; lp.maxLatencyMs = 80; lp.loss = 0.005; lp.duplicate = 0.002;
    net.setLink(lp);
    net.setBandwidth(2'000'000);
    std::unique_ptr<ThreadWorker> worker;
    if (o.threaded) worker = std::make_unique<ThreadWorker>();

    SharePolicy pol; pol.maxPlayers = uint8_t(o.players);
    HostSession host(*net.endpoint(0), pol);
    host.setWorker(worker.get());
    ShadowState base = makeBase(prof, 1700);   // ~8.3k entries
    const uint64_t kContent = 0x600D;
    host.setWorldBaseline(base, kContent);
    host.setSessionId(1);
    StateReplicator rep(host);

    auto resolve = [&](const std::string& edid, FormRef& out) { auto it = prof.quests.find(edid); if (it == prof.quests.end()) return false; out = it->second; return true; };
    std::unique_ptr<RulesEngine> eng;
    std::map<std::string, const Scene*> stageByKey;
    for (auto& s : prof.stages) stageByKey[s.quest + ":" + std::to_string(s.stage)] = &s;
    auto commitScene = [&](const Scene& s, PlayerId origin, CauseKind kind) {
        Cause c; c.kind = kind; c.source = prof.quests.count(s.quest) ? prof.quests[s.quest] : FormRef(); c.aux = int32_t(s.stage);
        std::vector<Effect> eff;
        if (kind == CauseKind::QuestResult) eff.push_back(SetStage{c.source, s.stage});
        for (auto& e : s.effects) eff.push_back(e);
        host.commit(origin, c, std::move(eff));
    };
    auto makeEngine = [&]() {
        eng = std::make_unique<RulesEngine>(host, db, resolve);
        eng->applyStage = [&](const FormRef& q, uint32_t stage) {
            for (auto& [edid, f] : prof.quests) if (f == q) { auto it = stageByKey.find(edid + ":" + std::to_string(stage)); if (it != stageByKey.end()) commitScene(*it->second, 0, CauseKind::QuestResult); }
        };
    };
    makeEngine();

    struct Cl {
        Engine engine;
        EffectCapture cap;
        std::unique_ptr<ClientSession> cs;
        ShadowState save;            // the client's own save: where it restarts from
        bool want = false;
        uint64_t reconnectAt = 0;
        std::vector<float> frames;
    };
    std::vector<std::unique_ptr<Cl>> cl;
    int nClients = o.players - 1;
    for (int i = 0; i < nClients; ++i) {
        auto c = std::make_unique<Cl>();
        c->engine.w = base;
        for (auto& kv : c->engine.w.stages) if (rng.chance(0.05)) kv.second += 1 + rng.range(0, 4);   // a slightly different save
        c->save = c->engine.w;
        c->cs = std::make_unique<ClientSession>(*net.endpoint(PeerId(i + 1)), 0, uint64_t(100 + i), c->engine, &c->cap);
        c->cs->setWorker(worker.get());
        Cl* cp = c.get();
        c->cs->setBaselineProvider([cp, kContent](ShadowState& w, uint64_t& h) { w = cp->engine.w; h = kContent; return true; });
        c->cs->setDigestInterval(10000);
        c->cs->setBaselineWorld(c->engine.w, kContent);
        cl.push_back(std::move(c));
    }
    int actionIds = 0;
    host.onAction = [&](PlayerId slot, IntentKind k, const FormRef& ref, uint32_t, uint32_t) {
        if (eng && eng->handleAction(slot, k, ref)) return;
        if (prof.blocks.empty()) return;
        const Scene& b = prof.blocks[rng.range(0, uint32_t(prof.blocks.size() - 1))];
        Cause c; c.kind = CauseKind::Script; c.source = ref; ++actionIds;
        std::vector<Effect> eff = b.effects;
        if (eff.empty()) eff.push_back(SetGlobal{FormRef("falloutnv.esm", 0x38), float(actionIds % 100)});
        host.commit(slot, c, std::move(eff));
    };

    // ---- quest-variable churn model: per quest, K vars; each frame `perFrame` writes, ~10% of them change the value
    struct VarKey { FormRef q; uint16_t idx; double value; };
    std::vector<std::vector<VarKey>> churn;
    for (auto& [edid, n] : prof.perFrame) {
        if (n <= 0 || !prof.quests.count(edid)) continue;
        std::vector<VarKey> ks;
        for (int i = 0; i < std::max(4, n / 4); ++i) ks.push_back({prof.quests[edid], uint16_t(i), 0.0});
        churn.push_back(std::move(ks));
    }
    int writesPerFrame = 0;
    for (auto& [e, n] : prof.perFrame) writesPerFrame += n;

    // ---- the play-through
    struct Save { Bytes side; size_t sceneIdx; uint64_t at; };
    std::vector<Save> saves;
    std::vector<const Scene*> playlist;
    for (auto& s : prof.stages) playlist.push_back(&s);
    size_t sceneIdx = 0;
    uint64_t nextScene = 5000, nextSave = 60000;
    struct PendingPass { uint64_t at; int client; FormRef quest; };
    std::vector<PendingPass> passes;

    const uint64_t dtMs = 33;
    const uint64_t totalMs = uint64_t(o.seconds * 1000.0);
    const uint64_t quietMs = std::min<uint64_t>(totalMs / 4, 90000);   // no new events at the end: everything must settle
    uint64_t now = 0;
    uint32_t sessionCtr = 0;
    struct Slow { float us; std::string what; };
    std::vector<Slow> slow;
    std::vector<float> hostFrames, allClientFrames;
    hostFrames.reserve(size_t(totalMs / dtMs) + 10);
    uint64_t reloads = 0, restarts = 0, disconnects = 0, injected = 0, scenes = 0, barrierScenes = 0;
    uint64_t bytesMark[8][2] = {};
    double peakDown[8] = {}, peakUp[8] = {};
    uint64_t windowStart = 0;
    auto wallStart = Clock::now();
    std::vector<uint64_t> reloadAt;
    for (int i = 0; i < 3; ++i) reloadAt.push_back(uint64_t(double(totalMs - quietMs) * (0.2 + 0.25 * i)));
    auto perFrameChance = [&](double perSecond) { return rng.chance(perSecond * double(dtMs) / 1000.0); };

    auto connect = [&](Cl& c) { c.cs->connect(now, c.cs->lastSession() + 1); c.want = true; };
    // Issue #43: a client that dropped (or restarted) late in the event phase drew a reconnect delay of up to 25 s (30 s for a restart),
    // which can land after the main loop ends. The settle loop below never reconnects anyone, so that client stayed away and the run
    // failed as "want=0 joined=0", host slot without peer. It was never a worker race: the same seeds fail without --threaded; the worker
    // only changes the timing of async results, which moves the rng draws and so samples other schedules. Every reconnect now lands at
    // the latest halfway through the quiet window, so it has the rest of it plus the settle loop to rejoin and converge.
    const uint64_t lastReconnectAt = totalMs - quietMs / 2;
    auto reconnectBy = [&](uint64_t at) { return o.mutLateReconnect ? at : std::min(at, lastReconnectAt); };
    bool lateDone = false;
    for (int i = 0; i < nClients; ++i) { cl[size_t(i)]->reconnectAt = 500 + uint64_t(i) * 2000; }

    while (now < totalMs) {
        now += dtMs;
        const bool quiet = now > totalMs - quietMs;
        auto h0 = Clock::now();
        const HostStats hs0 = host.stats();
        bool didReload = false, didSave = false, didScene = false;
        net.advance(now);
        host.tick(now);
        if (eng) eng->tick(now);
        // quest-script variable churn on the host (coalesced by the replicator)
        if (!quiet || rng.chance(0.2)) {
            for (auto& ks : churn) {
                int writes = std::max<int>(1, writesPerFrame / int(std::max<size_t>(1, churn.size())));
                for (int w = 0; w < writes; ++w) {
                    VarKey& k = ks[rng.range(0, uint32_t(ks.size() - 1))];
                    if (rng.chance(0.1)) k.value += 1.0;
                    rep.setScriptVar(k.q, k.idx, k.value);
                }
            }
        }
        rep.flush(now);
        // scenes
        if (!quiet && now >= nextScene && sceneIdx < playlist.size()) {
            const Scene& s = *playlist[sceneIdx++];
            ++scenes;
            didScene = true;
            FormRef q = prof.quests[s.quest];
            if (eng && eng->onStageRequested(q, s.stage, 0) == StageDecision::Deferred) {
                ++barrierScenes;
                eng->onPlayerPassed(0, q);
                for (int i = 0; i < nClients; ++i) passes.push_back({now + 1000 + rng.range(0, 5000), i, q});
            } else {
                commitScene(s, 0, CauseKind::QuestResult);
            }
            nextScene = now + 4000 + rng.range(0, 40000);
            if (sceneIdx >= playlist.size()) sceneIdx = playlist.size();   // the play-through ends; traffic continues as ambient activity
        }
        for (auto it = passes.begin(); it != passes.end();) {
            if (now >= it->at) {
                Cl& c = *cl[size_t(it->client)];
                if (c.cs->joined()) c.cs->submitAction(IntentKind::ScenePass, it->quest);
                it = passes.erase(it);
            } else ++it;
        }
        // saves and host reloads of older saves
        if (!quiet && now >= nextSave) {
            didSave = true;
            saves.push_back({host.saveSidecar(), sceneIdx, now});
            if (saves.size() > 8) saves.erase(saves.begin());
            nextSave = now + 120000;
        }
        for (auto it = reloadAt.begin(); it != reloadAt.end();) {
            if (now >= *it && !saves.empty()) {
                const Save* pick = nullptr;
                for (auto& s : saves) if (s.at + 120000 <= now) pick = &s;
                if (!pick) pick = &saves.front();
                if (host.loadSidecar(pick->side)) {
                    sceneIdx = pick->sceneIdx;
                    rep.clear();
                    makeEngine();
                    passes.clear();
                    nextScene = now + 5000;
                    ++reloads;
                    didReload = true;
                }
                it = reloadAt.erase(it);
            } else if (now >= *it) it = reloadAt.erase(it); else ++it;
        }
        {
            float us = float(std::chrono::duration<double, std::micro>(Clock::now() - h0).count());
            hostFrames.push_back(us);
            if (us > 1500) {
                const HostStats& hs = host.stats();
                std::string w = "host:";
                if (didReload) w += " reload"; if (didSave) w += " save"; if (didScene) w += " scene";
                if (hs.welcomes != hs0.welcomes) w += " welcome";
                if (hs.baselineJoins != hs0.baselineJoins) w += " baselineJoin";
                if (hs.digestsReceived != hs0.digestsReceived) w += " digest";
                if (hs.desyncRepairs != hs0.desyncRepairs) w += " repair";
                if (hs.commits != hs0.commits) w += " commits+" + std::to_string(hs.commits - hs0.commits);
                if (hs.baselineChunksSent != hs0.baselineChunksSent) w += " baselineChunks";
                if (hs.snapshots != hs0.snapshots) w += " snapshot";
                slow.push_back({us, w});
            }
        }

        // clients
        for (int i = 0; i < nClients; ++i) {
            Cl& c = *cl[size_t(i)];
            if (!c.want && now >= c.reconnectAt) {
                if (!c.cs->baselineDone() && c.cs->worldEpoch() == 0) { c.cs->setBaselineWorld(c.engine.w, kContent); }
                net.setConnected(PeerId(i + 1), true);
                connect(c);
            }
            auto t0 = Clock::now();
            const uint64_t sn0 = c.cs->stats().snapshots, rp0 = c.cs->stats().replays, rb0 = c.cs->stats().rebases, ds0 = c.cs->syncStats().digestsSent, ra0 = c.cs->syncStats().repairsApplied;
            const bool bd0 = c.cs->baselineDone(), ba0 = c.cs->baselineActive();
            if (c.want) c.cs->tick(now);
            float us = float(std::chrono::duration<double, std::micro>(Clock::now() - t0).count());
            if (us > 1500) {
                std::string w = "client " + std::to_string(i + 1) + ":";
                if (c.cs->stats().snapshots != sn0) w += " snapshotWelcome";
                if (c.cs->stats().replays != rp0) w += " replayWelcome";
                if (c.cs->stats().rebases != rb0) w += " rebase";
                if (c.cs->syncStats().digestsSent != ds0) w += " digest";
                if (c.cs->syncStats().repairsApplied != ra0) w += " repair";
                if (c.cs->baselineDone() != bd0) w += " baselineDone";
                if (c.cs->baselineActive() != ba0) w += " baselineActiveChanged";
                slow.push_back({us, w});
            }
            c.frames.push_back(us);
            allClientFrames.push_back(us);
            if (c.want && c.cs->joined() && !c.cs->baselineActive() && !quiet) {
                if (perFrameChance(1.0 / 20)) c.cs->submitAction(IntentKind::ActivateRef, FormRef("falloutnv.esm", 0x400000 + rng.range(0, 40)));
                if (perFrameChance(1.0 / 30)) {
                    Cause lc; lc.kind = CauseKind::Loot;
                    c.cap.record(AddItem{FormRef("falloutnv.esm", 0x500000 + rng.range(0, 30)), 1, Recipient::dflt()});
                    c.cs->submitIntent(lc, c.cap.take());
                }
                if (perFrameChance(1.0 / 60)) {
                    FormRef actor("falloutnv.esm", 0x600000 + rng.range(0, 20));
                    Cause kc; kc.kind = CauseKind::Combat;
                    { LocalAction la(&c.cap); c.cap.record(KillActor{actor}); }
                    c.engine.w.kills.insert(actor);
                    c.cs->submitIntent(kc, c.cap.take());
                }
            }
            if (!quiet && o.lateDisconnect && i == 0 && !lateDone && now + dtMs > totalMs - quietMs) {   // the last event frame
                lateDone = true;
                if (c.want) { c.want = false; c.cs->disconnect(); net.setConnected(PeerId(i + 1), false); ++disconnects; host.peerDisconnected(PeerId(i + 1)); }
                c.reconnectAt = reconnectBy(now + 5000 + 25000);   // the longest delay the random disconnect can draw
            } else if (!quiet) {
                if (c.want && perFrameChance(1.0 / 240)) { c.want = false; c.cs->disconnect(); net.setConnected(PeerId(i + 1), false); c.reconnectAt = reconnectBy(now + 5000 + rng.range(0, 25000)); ++disconnects; host.peerDisconnected(PeerId(i + 1)); }
                else if (c.want && perFrameChance(1.0 / 900)) {   // restart the game from the player's own save, join again by baseline
                    c.want = false; c.cs->resetState(); net.setConnected(PeerId(i + 1), false);
                    c.engine.w = c.save;
                    c.cs->setBaselineWorld(c.engine.w, kContent);
                    c.reconnectAt = reconnectBy(now + 8000 + rng.range(0, 20000)); ++restarts; host.peerDisconnected(PeerId(i + 1));
                }
                else if (c.want && c.cs->joined() && perFrameChance(1.0 / 600)) {   // a missed engine apply / stray local script write
                    if (!c.engine.w.stages.empty()) { auto it = c.engine.w.stages.begin(); std::advance(it, rng.range(0, uint32_t(c.engine.w.stages.size() - 1))); it->second += 3; ++injected; }
                }
            }
        }
        net.advance(now);
        if (worker) std::this_thread::sleep_for(std::chrono::microseconds(30));
        if (now - windowStart >= 10000) {   // peak bandwidth over 10 s windows
            for (int i = 0; i < nClients; ++i) {
                uint64_t down = net.bytesBetween(0, PeerId(i + 1)), up = net.bytesBetween(PeerId(i + 1), 0);
                peakDown[i] = std::max(peakDown[i], double(down - bytesMark[i][0]) / 10.0);
                peakUp[i] = std::max(peakUp[i], double(up - bytesMark[i][1]) / 10.0);
                bytesMark[i][0] = down; bytesMark[i][1] = up;
            }
            windowStart = now;
        }
    }
    if (worker) worker->waitIdle();
    for (int i = 0; i < 400; ++i) { now += dtMs; net.advance(now); host.tick(now); if (eng) eng->tick(now); rep.flush(now); for (auto& c : cl) if (c->want) c->cs->tick(now); net.advance(now); }

    // ---- verification
    int bad = 0, joined = 0;
    for (int i = 0; i < nClients; ++i) {
        Cl& c = *cl[size_t(i)];
        bool ok = c.want && c.cs->joined() && c.cs->worldEpoch() == host.epoch() && !c.cs->baselineActive() &&
                  shardHashesOf(c.engine.w) == shardHashesOf(host.state()) && c.cs->state() == host.state();
        joined += c.cs->joined();
        if (!ok) {
            ++bad;
            std::printf("    applied %llu head %llu shadowDiff '%s' worldVsHost differs in %s\n", (unsigned long long)c.cs->applied(), (unsigned long long)host.log().head(),
                        c.cs->state().describeDifference(host.state()).c_str(), shardHashesOf(c.engine.w) == shardHashesOf(host.state()) ? "nothing" : "some shard");
            for (auto& v : host.clients()) std::printf("    host view: slot %d peer %u joined=%d acked=%llu jobOpen=%d\n", int(v.slot), v.peer, int(v.joined), (unsigned long long)v.acked, int(v.baselineJobOpen));
            std::printf("    client stats: buffered %llu dups %llu applied %llu  host welcomes throttled %llu bulkDeferred %llu retxBatches %llu\n", (unsigned long long)c.cs->stats().buffered, (unsigned long long)c.cs->stats().duplicatesDropped, (unsigned long long)c.cs->stats().txnsApplied, (unsigned long long)host.stats().welcomesThrottled, (unsigned long long)host.stats().bulkDeferred, (unsigned long long)host.stats().retransmitBatches);
            if (!host.desyncs().empty()) { const auto& r = host.desyncs().back(); std::printf("    last desync report #%llu slot %d entries %zu first: %s | client: %s\n", (unsigned long long)r.id, int(r.slot), r.totalEntries, r.entries.empty() ? "-" : r.entries[0].host.c_str(), r.entries.empty() ? "-" : r.entries[0].client.c_str()); }
            std::printf("  client %d NOT converged: want=%d joined=%d epoch %u/%u baselineActive=%d worldMatch=%d shadowMatch=%d\n", i + 1, int(c.want), int(c.cs->joined()),
                        c.cs->worldEpoch(), host.epoch(), int(c.cs->baselineActive()), int(shardHashesOf(c.engine.w) == shardHashesOf(host.state())),
                        int(c.cs->state() == host.state()));
        }
    }
    double wall = std::chrono::duration<double>(Clock::now() - wallStart).count();
    Pct hp = pct(hostFrames), cp = pct(allClientFrames);
    if (!o.quiet) {
        std::printf("soak: %d players, %.0f virtual s (%.0f min), %.1f s wall, seed %llu%s, profile %zu stage scripts / %zu blocks, %d quest-var writes per frame\n", o.players, o.seconds,
                    o.seconds / 60, wall, (unsigned long long)o.seed, o.threaded ? ", worker thread" : "", prof.stages.size(), prof.blocks.size(), writesPerFrame);
        std::printf("  events: %llu scenes (%llu behind the chargen barrier), %llu host reloads, %llu client disconnects, %llu client restarts (baseline re-joins), %llu injected corruptions\n",
                    (unsigned long long)scenes, (unsigned long long)barrierScenes, (unsigned long long)reloads, (unsigned long long)disconnects, (unsigned long long)restarts, (unsigned long long)injected);
        std::printf("  host: %llu commits, %llu welcomes, %llu baseline joins, %llu rebases sent, digests ok/mismatch/ratelimited %llu/%llu/%llu, repairs %llu (%llu effects), %zu desync reports\n",
                    (unsigned long long)host.stats().commits, (unsigned long long)host.stats().welcomes, (unsigned long long)host.stats().baselineJoins, (unsigned long long)host.stats().rebasesSent,
                    (unsigned long long)host.stats().digestsOk, (unsigned long long)host.stats().digestsMismatch, (unsigned long long)host.stats().digestsRateLimited,
                    (unsigned long long)host.stats().desyncRepairs, (unsigned long long)host.stats().desyncEffectsRepaired, host.desyncs().size());
        std::printf("  replicator: %llu var writes -> %llu replicated effects (%.1fx coalescing)\n", (unsigned long long)rep.stats().writes, (unsigned long long)rep.stats().effectsCommitted,
                    rep.stats().effectsCommitted ? double(rep.stats().writes) / double(rep.stats().effectsCommitted) : 0.0);
        for (int i = 0; i < nClients; ++i) {
            double down = double(net.bytesBetween(0, PeerId(i + 1))) / o.seconds, up = double(net.bytesBetween(PeerId(i + 1), 0)) / o.seconds;
            std::printf("  client %d: host->client %.2f KB/s avg (peak 10 s window %.2f KB/s), client->host %.2f KB/s avg (peak %.2f KB/s)\n", i + 1, down / 1024, peakDown[i] / 1024, up / 1024, peakUp[i] / 1024);
        }
        std::printf("  frame cost (host tick+commits): p50 %.1f us, p99 %.1f us, worst %.1f us | (client tick): p50 %.1f us, p99 %.1f us, worst %.1f us\n", hp.p50, hp.p99, hp.max, cp.p50, cp.p99, cp.max);
        std::sort(slow.begin(), slow.end(), [](const Slow& a, const Slow& b) { return a.us > b.us; });
        for (size_t i = 0; i < std::min<size_t>(slow.size(), 6); ++i) std::printf("  slow frame %.0f us: %s\n", slow[i].us, slow[i].what.c_str());
        std::printf("  result: %s (%d/%d clients converged)\n", bad == 0 ? "ALL CONVERGED" : "DIVERGED", nClients - bad, nClients);
    }
    // machine-readable summary line for scripts
    std::printf("SOAK_RESULT converged=%d players=%d seconds=%.0f hostWorstUs=%.0f clientWorstUs=%.0f\n", bad == 0 ? 1 : 0, o.players, o.seconds, hp.max, cp.max);
    return bad == 0 ? 0 : 1;
}

}  // namespace

int main(int argc, char** argv) {
    Opts o;
    for (int i = 1; i < argc; ++i) {
        std::string a = argv[i];
        auto next = [&]() -> const char* { return i + 1 < argc ? argv[++i] : ""; };
        if (a == "--seconds") o.seconds = std::atof(next());
        else if (a == "--players") o.players = std::max(2, std::min(8, std::atoi(next())));
        else if (a == "--seed") o.seed = std::strtoull(next(), nullptr, 10);
        else if (a == "--profile") o.profile = next();
        else if (a == "--rules") o.rules = next();
        else if (a == "--threaded") o.threaded = true;
        else if (a == "--quiet") o.quiet = true;
        else if (a == "--late-disconnect") o.lateDisconnect = true;
        else if (a == "--mutate") { std::string m = next(); if (m == "late-reconnect") o.mutLateReconnect = true; else { std::printf("unknown mutation %s\n", m.c_str()); return 2; } }
    }
#ifdef FNVCOOP_PROFILE_DEFAULT
    if (o.profile.empty()) o.profile = FNVCOOP_PROFILE_DEFAULT;
#endif
#ifdef FNVCOOP_RULES_DEFAULT
    if (o.rules.empty()) o.rules = FNVCOOP_RULES_DEFAULT;
#endif
    return run(o);
}
