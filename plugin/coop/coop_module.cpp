// nvcoop module (L10): starts the co-op Runtime inside the game and exposes the bridge methods.
//   nvcoop.ini  [coop] enable=1  role=host|client|guest  listen=127.0.0.1  host=127.0.0.1  port=27960  token=<n>
//   coop.status                      -> role/running/peers/last seq/txn counts (Runtime::statusJson)
//   coop.log   {n?:int}              -> recent txns (commit on host, apply on client, inject)
//   coop.inject {txn:{cause,origin,effects}} -> host: commit as if captured; client: apply as if received
// Starts lazily from onFrame once L5's hooks are initialised (nvhooks::hub() != null), so the module order of the
// DeferredInit message does not matter. All of it runs on the game main thread.
#include <Windows.h>

#include <cstring>
#include <memory>
#include <unordered_map>
#include <string>

#include "GameData.h"
#include "GameObjects.h"
#include "PluginAPI.h"
#include "apply_remote.h"
#include "common/minijson.h"
#include "common/nvcoop_api.h"
#include "baseline_file.h"
#include "coop_runtime.h"
#include "effect_json.h"
#include "engine_hooks.h"
#ifdef NVCOOP_WITH_SNAPSHOT
#include "fnvcoop_snapshot/baseline_dump.h"
#endif

namespace {
using namespace nvcoop::coop;

bool g_scriptedLock = false;   // SetPlayerControls from the host's scripts (rules engine scope=all)

class EngineWorld : public IWorldApplier {
public:
    ApplyResult apply(const fnvcoop::Effect& e, const fnvcoop::IEffectSink::Context& ctx) override {
        // session-level effects the engine layer realizes locally (no engine primitive involved)
        if (auto* sc = std::get_if<fnvcoop::SetPlayerControls>(&e)) {
            g_scriptedLock = !sc->enable;   // coarse v1: any disable locks all seven control classes (mask granularity: L5 TODO)
            return {true, "Ok"};
        }
        if (std::get_if<fnvcoop::SaveSuppress>(&e) || std::get_if<fnvcoop::PartyTether>(&e) || std::get_if<fnvcoop::BarrierRelease>(&e) ||
            std::get_if<fnvcoop::BarrierArrive>(&e))
            return {true, "Ok"};   // state lives in the shared shadow (coop.status.saveSuppress); nothing to write into the engine
        if (!applier) return {false, "NoApplier"};
        // baseline effects FORCE-SET (may lower a stage, full objective flag byte); live txns merge normally
        nvhooks::ApplyStatus st = ctx.baseline ? applier->applyForce(e) : applier->apply(e);
        ApplyResult r;
        r.ok = st == nvhooks::ApplyStatus::Ok;
        r.status = nvhooks::applyStatusName(st);
        if (!r.ok && !applier->lastError().empty()) r.status += ": " + applier->lastError();
        return r;
    }
    void onJoined(fnvcoop::PlayerId slot, const fnvcoop::SharePolicy&) override {
        nvcoop::Log("coop: joined as slot %d", (int)slot);
    }
    nvhooks::RemoteApplier* applier = nullptr;
};

// Engine dumper for the baseline join: L7's DumpBaselineShadow (needs the snapshot library linked in).
class EngineDumper : public IWorldDumper {
public:
    bool dump(fnvcoop::ShadowState* out, uint64_t* hash, std::string* err) override {
#ifdef NVCOOP_WITH_SNAPSHOT
        bool ok = fnvcoop::snapshot::DumpBaselineShadow(out, hash, err);
        nvcoop::Log("coop: baseline dump %s; %s", ok ? "ok" : "FAILED", fnvcoop::snapshot::LastBaselineStats().summary.c_str());
        return ok;
#else
        (void)out; (void)hash;
        *err = "plugin built without the snapshot library (NVCOOP_LINK_SNAPSHOT=OFF)";
        return false;
#endif
    }
};
EngineDumper g_dumper;
bool g_gameLoaded = false;

Config g_cfg;
bool g_cfgLoaded = false;
std::unique_ptr<Runtime> g_rt;
std::unique_ptr<nvhooks::RemoteApplier> g_applier;
EngineWorld g_world;
bool g_failed = false;
std::string g_failMsg;
uint64_t g_waitFrames = 0;

// L5 pollHostState -> L4 StateReplicator (quest vars / globals are host-authoritative and never ride in capture txns)
class PollSink : public nvhooks::StateSink {
public:
    void scriptVar(const fnvcoop::FormRef& quest, uint16_t index, double value) override {
        if (g_rt && g_rt->replicator()) g_rt->replicator()->setScriptVar(quest, index, value);
    }
    void global(const fnvcoop::FormRef& g, float value) override {
        if (g_rt && g_rt->replicator()) g_rt->replicator()->setGlobal(g, value);
    }
};
PollSink g_pollSink;
bool g_polling = false;

std::string IniPath() {
    HMODULE self = nullptr;
    GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                       reinterpret_cast<LPCSTR>(&IniPath), &self);
    char buf[MAX_PATH] = {};
    GetModuleFileNameA(self, buf, MAX_PATH);
    std::string p = buf;
    size_t s = p.find_last_of("\\/");
    return (s == std::string::npos ? std::string() : p.substr(0, s + 1)) + "nvcoop.ini";
}

std::string ReadWholeFile(const std::string& path) {
    HANDLE h = CreateFileA(path.c_str(), GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr, OPEN_EXISTING, 0, nullptr);
    if (h == INVALID_HANDLE_VALUE) return {};
    std::string s;
    char buf[4096];
    DWORD n = 0;
    while (ReadFile(h, buf, sizeof buf, &n, nullptr) && n) s.append(buf, n);
    CloseHandle(h);
    return s;
}

uint64_t NowMs() { return GetTickCount64(); }

std::string DllDir() {
    std::string p = IniPath();
    size_t s = p.find_last_of("\\/");
    return s == std::string::npos ? std::string() : p.substr(0, s + 1);
}

// nvcoop_edids.json {"VCG01":"falloutnv.esm:104c1c",...}, nvcoop_infoquest.json {"<info>":"<quest>"} (tools/gen_coop_data.py)
std::unordered_map<std::string, std::string> LoadStringMap(const std::string& path) {
    std::unordered_map<std::string, std::string> m;
    std::string text = ReadWholeFile(path);
    if (text.empty()) return m;
    nvcoop::json::Value v;
    std::string err;
    if (!nvcoop::json::parse(text, v, err)) { nvcoop::Log("coop: cannot parse %s: %s", path.c_str(), err.c_str()); return m; }
    for (auto& kv : v.members()) m[kv.first] = kv.second.asString("");
    return m;
}
std::unordered_map<std::string, std::string> g_edids, g_infoQuest;
std::function<uint32_t(fnvcoop::PlayerId)> g_ghostOf;   // slot -> the guest's ghost refID on the host (L6); empty = stand-in player
fnvcoop::FormRef g_passQuest;      // [coop] scenepass=<EDID>:<stage>: local player counts as "finished" when the quest reaches the stage
int g_passStage = -1;
bool g_passSent = false;
bool g_rejectLogged = false;

// Build identity for the join handshake: hash of this very dll, its PE link timestamp as the build number, and the mod load order.
uint64_t Fnv64File(const std::string& path) {
    std::string b = ReadWholeFile(path);
    uint64_t h = 1469598103934665603ull;
    for (unsigned char c : b) { h ^= c; h *= 1099511628211ull; }
    return h;
}

fnvcoop::HandshakeInfo MakeHandshake() {
    fnvcoop::HandshakeInfo hs;
    HMODULE self = nullptr;
    GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT, reinterpret_cast<LPCSTR>(&MakeHandshake), &self);
    char buf[MAX_PATH] = {};
    GetModuleFileNameA(self, buf, MAX_PATH);
    hs.buildHash = Fnv64File(buf);
    auto* dos = reinterpret_cast<const IMAGE_DOS_HEADER*>(self);
    auto* nt = reinterpret_cast<const IMAGE_NT_HEADERS*>(reinterpret_cast<const char*>(self) + dos->e_lfanew);
    hs.buildNumber = nt->FileHeader.TimeDateStamp;    // link time: monotonically increasing per build
    hs.buildName = std::string("nvcoop ") + __DATE__ + " " + __TIME__;
    if (DataHandler* dh = DataHandler::Get()) {
        UInt32 n = dh->GetActiveModCount();
        for (UInt32 i = 0; i < 255 && i < n + 8; ++i) {
            const char* name = dh->GetNthModName(i);
            if (name && *name) hs.plugins.push_back(name);
        }
    }
    return hs;
}

void LoadConfig() {
    if (g_cfgLoaded) return;
    g_cfgLoaded = true;
    g_cfg.handshake = true;   // real installs check build / rules / volatile / mod list on join; ini handshake=0 for fake-peer tests
    std::string err;
    if (!ParseConfig(ReadWholeFile(IniPath()), &g_cfg, &err)) {
        g_failed = true;
        g_failMsg = "nvcoop.ini [coop]: " + err;
        nvcoop::Log("coop: %s", g_failMsg.c_str());
        return;
    }
    nvcoop::Log("coop: enable=%d role=%s listen=%s host=%s port=%u", g_cfg.enable ? 1 : 0, roleName(g_cfg.role), g_cfg.listen.c_str(),
                g_cfg.host.c_str(), (unsigned)g_cfg.port);
}

void ParseScenePass() {
    char buf[128] = {};
    GetPrivateProfileStringA("coop", "scenepass", "", buf, sizeof buf, IniPath().c_str());
    std::string v = buf;
    size_t c = v.find(':');
    if (v.empty() || c == std::string::npos) return;
    auto it = g_edids.find(v.substr(0, c));
    if (it == g_edids.end()) {   // clients do not load the host rules; load the EDID map just for this
        g_edids = LoadStringMap(DllDir() + "nvcoop_edids.json");
        it = g_edids.find(v.substr(0, c));
    }
    if (it != g_edids.end() && nvhooks::strToRef(it->second, &g_passQuest)) {
        g_passStage = std::atoi(v.c_str() + c + 1);
        nvcoop::Log("coop: scenepass trigger %s stage >= %d", it->second.c_str(), g_passStage);
    }
}

void TryStart() {
    if (g_rt || g_failed || !g_cfg.enable || g_cfg.role == Role::Off) return;
    if (g_cfg.baseline && !g_gameLoaded) return;   // the dump needs a loaded save
    nvhooks::CaptureHub* hub = nvhooks::hub();
    if (!hub) {  // hooks not initialised yet (L5 inits at DeferredInit); wait
        ++g_waitFrames;
        return;
    }
    g_applier.reset(new nvhooks::RemoteApplier(*hub, nvhooks::resolver()));
    g_world.applier = g_applier.get();
    g_rt.reset(new Runtime(g_cfg, hub, &g_world, NowMs));
    g_rt->setDumper(&g_dumper);
    if (g_cfg.handshake) {
        g_rt->setHandshakeBase(MakeHandshake());
        if (g_cfg.rulesFile.empty()) {   // clients hash the same rules.json the host loads
            std::string def = DllDir() + "nvcoop_rules.json";
            if (!ReadWholeFile(def).empty()) { g_cfg.rulesFile = def; g_rt->setRulesFile(def); }
        }
    }
    ParseScenePass();
    if (g_cfg.role == Role::Host) {
        // quest rules engine: rules.json (ini rules=, default nvcoop_rules.json next to the dll) + EDID / INFO->quest maps
        if (g_cfg.rulesFile.empty()) {
            std::string def = DllDir() + "nvcoop_rules.json";
            if (!ReadWholeFile(def).empty()) g_cfg.rulesFile = def;
        }
        if (!g_cfg.rulesFile.empty()) {
            g_rt->setRulesFile(g_cfg.rulesFile);
            g_edids = LoadStringMap(DllDir() + "nvcoop_edids.json");
            g_infoQuest = LoadStringMap(DllDir() + "nvcoop_infoquest.json");
            nvcoop::Log("coop: rules=%s edids=%u infoquest=%u", g_cfg.rulesFile.c_str(), (unsigned)g_edids.size(), (unsigned)g_infoQuest.size());
            g_rt->setEdidResolver([](const std::string& edid, fnvcoop::FormRef& out) {
                auto it = g_edids.find(edid);
                return it != g_edids.end() && nvhooks::strToRef(it->second, &out);
            });
            g_rt->setInfoQuestResolver([](const fnvcoop::FormRef& info, fnvcoop::FormRef& q) {
                auto it = g_infoQuest.find(nvhooks::refToStr(info));
                return it != g_infoQuest.end() && nvhooks::strToRef(it->second, &q);
            });
            g_rt->setStageApplier([](const fnvcoop::FormRef& q, uint32_t st) {
                // re-issue the deferred stage as the engine would have: scripts run, capture opens (host hub), gate bypassed
                uint32_t id = nvhooks::resolver().fromRef(q);
                char line[64];
                std::snprintf(line, sizeof line, "setstage %08X %u", id, st);
                nvhooks::StageGateBypass bypass;
                bool ok = id != 0 && nvhooks::execConsoleLine(line);
                nvcoop::Log("coop: rules released stage %s=%u -> '%s' %s", nvhooks::refToStr(q).c_str(), st, line, ok ? "issued" : "FAILED");
                nvcoop::PushEvent("coop.stageReleased", std::string("{\"quest\":\"") + nvhooks::refToStr(q) + "\",\"stage\":" + std::to_string(st) +
                                                            ",\"issued\":" + (ok ? "true" : "false") + "}");
            });
            nvhooks::setStageGate([](const fnvcoop::FormRef& q, uint32_t st) { return g_rt ? g_rt->stageRequested(q, st, 0) : true; });
        }
    }
    g_rt->setActionExecutor([](fnvcoop::PlayerId slot, fnvcoop::IntentKind kind, const fnvcoop::FormRef& ref) {
        // Guest interaction (docs/protocol.md section 12): the host runs the REAL activation of `ref` inside a capture scope whose
        // origin the runtime forces to `slot`, so rewards / karma / XP route to the guest and stage changes reach everybody.
        nvhooks::CaptureHub* hub = nvhooks::hub();
        uint32_t target = nvhooks::resolver().fromRef(ref);
        if (!hub || !target) { nvcoop::Log("coop: guest action %d on %s from slot %d: target not found", (int)kind, nvhooks::refToStr(ref).c_str(), (int)slot); return; }
        if (kind == fnvcoop::IntentKind::TriggerEnter) {
            nvcoop::Log("coop: guest TriggerEnter on %s from slot %d: not drivable yet (needs the L5 event-dispatch hook)", nvhooks::refToStr(ref).c_str(), (int)slot);
            return;
        }
        // v1 acting player = the host's own player (0x14) so `IsActionRef player` / `GetDistance player` pass; the guest's ghost ref
        // (L6) replaces it once available. Effects are attributed to the guest by origin (known limitation: the host's player
        // really receives the item too).
        uint32_t actor = g_ghostOf ? g_ghostOf(slot) : 0;
        if (!actor) actor = 0x14;
        fnvcoop::Cause c;
        c.kind = fnvcoop::CauseKind::Intent;
        c.source = ref;
        c.aux = static_cast<int32_t>(kind);
        nvhooks::ScopedCapture scope(*hub, c);
        char line[64];
        std::snprintf(line, sizeof line, "Activate %08X 1", actor);
        bool ok = nvhooks::execConsoleLineOn(line, target);
        nvcoop::Log("coop: guest action %d on %s from slot %d -> '%s' as %08X: %s", (int)kind, nvhooks::refToStr(ref).c_str(), (int)slot, line, actor, ok ? "issued" : "FAILED");
        nvcoop::PushEvent("coop.guestAction", std::string("{\"slot\":") + std::to_string((int)slot) + ",\"ref\":\"" + nvhooks::refToStr(ref) + "\",\"issued\":" + (ok ? "true" : "false") + "}");
    });
    if (g_cfg.role == Role::Client && g_cfg.forwardActivate) {
        nvhooks::setActivateObserver([](const fnvcoop::FormRef& target) {
            if (!g_rt || g_rt->syncing()) return nvhooks::ActivateAction::Proceed;   // (gameplay is locked while syncing anyway)
            return g_rt->submitAction(fnvcoop::IntentKind::ActivateRef, target) ? nvhooks::ActivateAction::Suppress
                                                                              : nvhooks::ActivateAction::Proceed;
        });
    }
    if (g_cfg.dialogue) {
        DialogueHooks h;
        // TODO(L5/L8): run the INFO result script under capture; 0 = nothing committed
        h.run = [](const fnvcoop_dlg::ResultRequest&) -> uint64_t { return 0; };
        h.sink = [](const fnvcoop_dlg::Event& e) { nvcoop::PushEvent("dlg.event", std::string("{\"kind\":") + std::to_string((int)e.kind) + "}"); };
        g_rt->enableDialogue(h);
    }
    std::string err;
    if (!g_rt->start(&err)) {
        g_failed = true;
        g_failMsg = err;
        nvcoop::Log("coop: start failed: %s", err.c_str());
        return;
    }
    nvcoop::Log("coop: started role=%s port=%u", roleName(g_cfg.role), (unsigned)g_rt->boundPort());
    nvcoop::PushEvent("coop.started", g_rt->statusJson());
}

bool MStatus(const std::string&, std::string& out) {
    if (g_rt) { out = g_rt->statusJson(); return true; }
    nvcoop::json::Value o = nvcoop::json::Value::object();
    o.set("role", roleName(g_cfg.role)).set("enabled", g_cfg.enable).set("running", false).set("error", g_failMsg)
        .set("waitingForHooks", g_cfg.enable && !g_failed && nvhooks::hub() == nullptr).set("waitFrames", (int64_t)g_waitFrames);
    out = o.dump();
    return true;
}

bool MLog(const std::string& params, std::string& out) {
    if (!g_rt) { out = "coop not running"; return false; }
    nvcoop::json::Value p;
    std::string err;
    size_t n = 64;
    if (!params.empty() && nvcoop::json::parse(params, p, err)) n = static_cast<size_t>(p.get("n").asInt(64));
    out = g_rt->logJson(n);
    return true;
}

bool MAction(const std::string& params, std::string& out) {
    if (!g_rt || !g_rt->client()) { out = "coop client not running"; return false; }
    nvcoop::json::Value p;
    std::string err;
    if (!nvcoop::json::parse(params, p, err)) { out = "bad params: " + err; return false; }
    fnvcoop::FormRef ref;
    if (!nvhooks::strToRef(p.get("ref").asString(""), &ref)) { out = "ref must be '<plugin>:<hex>'"; return false; }
    std::string k = p.get("kind").asString("activate");
    auto kind = k == "pickup" ? fnvcoop::IntentKind::PickUp : k == "trigger" ? fnvcoop::IntentKind::TriggerEnter : fnvcoop::IntentKind::ActivateRef;
    uint32_t id = g_rt->submitAction(kind, ref);
    out = std::string("{\"id\":") + std::to_string(id) + ",\"syncing\":" + (g_rt->syncing() ? "true" : "false") + "}";
    return id != 0 ? true : (out = "not joined or still syncing: " + out, false);
}

// coop.baseline.export {path}: dump the live world (L7) to a baseline file; returns entry counts. Main thread.
bool MExport(const std::string& params, std::string& out) {
    nvcoop::json::Value p;
    std::string err;
    if (!nvcoop::json::parse(params, p, err)) { out = "bad params: " + err; return false; }
    std::string path = p.get("path").asString("");
    if (path.empty()) { out = "path required"; return false; }
    fnvcoop::ShadowState st;
    uint64_t hash = 0;
    if (!p.get("fresh").asBool(false) && g_rt && g_rt->usedBaseline(&hash)) st = *g_rt->usedBaseline(&hash);   // exactly what the host session uses
    else if (!g_dumper.dump(&st, &hash, &err)) { out = err; return false; }
    if (!SaveBaselineFile(path, st, hash)) { out = "cannot write " + path; return false; }
    nvcoop::json::Value o = nvcoop::json::Value::object();
    o.set("path", path).set("hash", std::to_string(hash)).set("stages", (int64_t)st.stages.size()).set("objectives", (int64_t)st.objectives.size())
        .set("globals", (int64_t)st.globals.size()).set("scriptVars", (int64_t)st.scriptVars.size()).set("questRunning", (int64_t)st.questRunning.size())
        .set("factionRank", (int64_t)st.factionRank.size()).set("sayOnce", (int64_t)st.sayOnce.size()).set("enabled", (int64_t)st.enabled.size())
        .set("kills", (int64_t)st.kills.size()).set("karma", st.ledger[0].karma);
    out = o.dump();
    return true;
}

bool ParseRef(const nvcoop::json::Value& p, const char* key, fnvcoop::FormRef* out, std::string& err) {
    if (!nvhooks::strToRef(p.get(key).asString(""), out)) { err = std::string(key) + " must be '<plugin>:<hex>'"; return false; }
    return true;
}

// coop.scenePass {quest}: the local player finished their per-player step (chargen). TEST/automation entry until L5 detects it.
bool MScenePass(const std::string& params, std::string& out) {
    if (!g_rt) { out = "coop not running"; return false; }
    nvcoop::json::Value p;
    std::string err;
    fnvcoop::FormRef q;
    if (!nvcoop::json::parse(params, p, err) || !ParseRef(p, "quest", &q, err)) { out = err; return false; }
    g_rt->scenePass(q);
    out = "{}";
    return true;
}

// coop.stageRequested {quest, stage}: ask the rules engine whether the host may advance now (the SetStage gate calls the same function)
bool MStageRequested(const std::string& params, std::string& out) {
    if (!g_rt) { out = "coop not running"; return false; }
    nvcoop::json::Value p;
    std::string err;
    fnvcoop::FormRef q;
    if (!nvcoop::json::parse(params, p, err) || !ParseRef(p, "quest", &q, err)) { out = err; return false; }
    bool proceed = g_rt->stageRequested(q, (uint32_t)p.get("stage").asInt(0));
    out = std::string("{\"proceed\":") + (proceed ? "true" : "false") + "}";
    return true;
}

// coop.baseline.vars {quests:["falloutnv.esm:104c1c",..]}: script vars of those owners as seen by the baseline the host session uses vs the
// host's current session world (baseline + commits). Diagnostic for baseline/poll disagreements.
bool MBaselineVars(const std::string& params, std::string& out) {
    if (!g_rt || !g_rt->host()) { out = "coop host not running"; return false; }
    nvcoop::json::Value p;
    std::string err;
    if (!nvcoop::json::parse(params, p, err)) { out = "bad params: " + err; return false; }
    uint64_t h = 0;
    const fnvcoop::ShadowState* base = g_rt->usedBaseline(&h);
    const fnvcoop::ShadowState& world = g_rt->host()->world();
    nvcoop::json::Value res = nvcoop::json::Value::object();
    for (const auto& q : p.get("quests").items()) {
        fnvcoop::FormRef ref;
        if (!nvhooks::strToRef(q.asString(""), &ref)) continue;
        nvcoop::json::Value o = nvcoop::json::Value::object();
        auto dumpMap = [&](const fnvcoop::ShadowState& st) {
            nvcoop::json::Value m = nvcoop::json::Value::object();
            for (auto& kv : st.scriptVars)
                if (kv.first.first == ref) m.set(std::to_string(kv.first.second), kv.second);
            return m;
        };
        if (base) o.set("baseline", dumpMap(*base));
        o.set("sessionWorld", dumpMap(world));
        res.set(q.asString(""), o);
    }
    out = res.dump();
    return true;
}

// coop.explain {quest, var, slot?}: HostSession::explainJoinEntry for a script var (L4 diagnostics: present/absent, hashes, VOLATILE)
bool MExplain(const std::string& params, std::string& out) {
    if (!g_rt || !g_rt->host()) { out = "coop host not running"; return false; }
    nvcoop::json::Value p;
    std::string err;
    fnvcoop::FormRef q;
    if (!nvcoop::json::parse(params, p, err) || !ParseRef(p, "quest", &q, err)) { out = err; return false; }
    fnvcoop::SetScriptVar probe;
    probe.owner = q;
    probe.index = static_cast<uint16_t>(p.get("var").asInt(0));
    nvcoop::json::Value o = nvcoop::json::Value::object();
    o.set("explain", g_rt->host()->explainJoinEntry(static_cast<fnvcoop::PlayerId>(p.get("slot").asInt(1)), probe))
        .set("unresolvedVolatileNames", (int64_t)fnvcoop::volatileKeys().unresolvedNames());
    out = o.dump();
    return true;
}

bool MInject(const std::string& params, std::string& out) {
    if (!g_rt) { out = "coop not running"; return false; }
    std::string res, err;
    if (!g_rt->inject(params, &res, &err)) { out = err; return false; }
    out = res;
    return true;
}

void OnLoad(void*) {
    LoadConfig();
    nvcoop::RegisterMethod("coop.status", MStatus, true);
    nvcoop::RegisterMethod("coop.log", MLog, true);
    nvcoop::RegisterMethod("coop.inject", MInject, true);
    nvcoop::RegisterMethod("coop.baseline.export", MExport, true);
    nvcoop::RegisterMethod("coop.baseline.vars", MBaselineVars, true);
    nvcoop::RegisterMethod("coop.explain", MExplain, true);
    nvcoop::RegisterMethod("coop.action", MAction, true);
    nvcoop::RegisterMethod("coop.scenePass", MScenePass, true);
    nvcoop::RegisterMethod("coop.stageRequested", MStageRequested, true);   // client: {kind:"activate|pickup|trigger", ref:"falloutnv.esm:00ABCD"}
}

void OnFrame() {
    if (!g_rt) TryStart();
    if (g_rt) {
        g_rt->frame();
        if (g_rt->replicator() && g_cfg.baseline) {   // polling needs a known host world (baseline) or the first poll would flood
            if (!g_polling) { nvhooks::enableStatePolling(true); g_polling = true; }
            nvhooks::pollHostState(g_pollSink);
        }
        // v1 gameplay block while the world-baseline transfer runs (L5 DisablePlayerControls path; idempotent)
        if (g_rt->client() && g_rt->client()->rejected() && !g_rejectLogged) {
            g_rejectLogged = true;
            nvcoop::Log("coop: JOIN REJECTED: %s", g_rt->client()->rejectText().c_str());
            nvcoop::PushEvent("coop.rejected", std::string("{\"reason\":") + std::to_string((int)g_rt->client()->rejectReason()) + "}");
        }
        if (g_passStage >= 0 && !g_passSent && nvhooks::localQuestStage(g_passQuest) >= g_passStage) {
            g_passSent = true;
            nvcoop::Log("coop: local player reached the scenepass stage -> ScenePass");
            g_rt->scenePass(g_passQuest);
        }
        bool lock = g_rt->syncing() || g_scriptedLock;
        {   // report the player's cell every 30 frames (party tether / presence): host -> rules engine, client -> ReportCell action
            static uint64_t frames = 0;
            static uint32_t lastCell = 0;
            if (++frames % 30 == 0) {
                PlayerCharacter* pc = g_thePlayer ? *g_thePlayer : nullptr;
                uint32_t cell = pc && pc->parentCell ? pc->parentCell->refID : 0;
                if (cell && cell != lastCell) {
                    fnvcoop::FormRef ref;
                    if (nvhooks::resolver().toRef(cell, &ref)) { lastCell = cell; g_rt->reportCell(ref); }
                }
            }
        }
        static bool lastLock = false;
        nvhooks::SetPlayerControlsLocked(lock);
        if (lock != lastLock) {
            lastLock = lock;
            nvcoop::PushEvent("coop.controlsLocked", lock ? "{\"locked\":true}" : "{\"locked\":false}");
            nvcoop::Log("coop: gameplay controls %s (baseline sync)", lock ? "LOCKED" : "unlocked");
        }
    }
}

bool WriteBytes(const std::string& path, const fnvcoop::Bytes& b) {
    HANDLE h = CreateFileA(path.c_str(), GENERIC_WRITE, 0, nullptr, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (h == INVALID_HANDLE_VALUE) return false;
    DWORD n = 0;
    bool ok = WriteFile(h, b.data(), (DWORD)b.size(), &n, nullptr) && n == b.size();
    CloseHandle(h);
    return ok;
}

// NVSE passes the file name as given ("autosave_x.fos"), sometimes a full path: resolve against the game's Saves dir
std::string ResolveSavePath(const std::string& p) {
    if (p.size() > 1 && (p[1] == ':' || p[0] == '\\' || p[0] == '/')) return p;
    if (p.find_first_of("\\/") != std::string::npos) return p;
    return "Saves\\" + p;
}
std::string g_loadPath;   // .fos path announced by PreLoadGame (PostLoadGame carries none)

void OnMessage(uint32_t type, void* data, uint32_t len) {
    // Save/load: sidecar `<save>.fos.fnvcoop` next to the save (L4 round 6). Host sidecar = shared world + epoch + token map.
    if (type == NVSEMessagingInterface::kMessage_SaveGame && data && g_rt && g_cfg.baseline) {
        std::string path = ResolveSavePath(std::string(static_cast<const char*>(data), len ? strnlen(static_cast<const char*>(data), len) : strlen(static_cast<const char*>(data))));
        fnvcoop::Bytes b = g_rt->saveSidecar();
        if (!b.empty() && WriteBytes(path + ".fnvcoop", b)) nvcoop::Log("coop: wrote sidecar %s.fnvcoop (%u bytes)", path.c_str(), (unsigned)b.size());
        else nvcoop::Log("coop: no sidecar written for %s (empty or write failed)", path.c_str());
    }
    if (type == NVSEMessagingInterface::kMessage_PreLoadGame && data)
        g_loadPath = ResolveSavePath(std::string(static_cast<const char*>(data), len ? strnlen(static_cast<const char*>(data), len) : strlen(static_cast<const char*>(data))));
    if (type == NVSEMessagingInterface::kMessage_PostLoadGame && data && g_rt && g_rt->running() && g_cfg.baseline) {
        std::string raw = ReadWholeFile(g_loadPath + ".fnvcoop");
        fnvcoop::Bytes side(raw.begin(), raw.end());
        std::string what;
        bool ok = g_rt->onGameLoaded(&side, &what);
        nvcoop::Log("coop: game loaded (%s): %s -> %s", g_loadPath.c_str(), what.c_str(), ok ? "ok" : "not handled");
        nvcoop::PushEvent("coop.loaded", std::string("{\"ok\":") + (ok ? "true" : "false") + "}");
    }
    if (type == NVSEMessagingInterface::kMessage_PostLoadGame && data) g_gameLoaded = true;
    if (type == NVSEMessagingInterface::kMessage_ExitGame || type == NVSEMessagingInterface::kMessage_ExitGame_Console) {
        if (g_rt) { nvhooks::SetPlayerControlsLocked(false); g_rt->stop(); }
    }
}

}  // namespace

NVCOOP_MODULE(l10_coop, "l10_coop", OnLoad, OnMessage, OnFrame);
