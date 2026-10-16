// nvcoop module: installs the L5 hooks at DeferredInit and exposes bridge methods.
//   nvcoop.ini  [coop] role=host|client|guest|gateonly   (default host; client == guest == all hooks + suppression gate)
//   hooks.status                       -> {role, installed, failed, hooks:[{name,status}], criticalOk}
//   capture.begin                      -> clears the capture buffer, (re)enables capture on a host
//   capture.end                        -> {txns:[...], scriptVars:[...], stats:{...}, unmapped:{...}, scripts:{...}}
//   apply.txn {txn:{..}, gate?:true, slot?:0} -> {applied, total, results:[...], scripts:{gated,ran,ranWhileApplying}}
// All run on the main thread.
#include <Windows.h>

#include <string>
#include <vector>

#include "PluginAPI.h"
#include "GameObjects.h"
#include "apply_remote.h"
#include "common/minijson.h"
#include "common/nvcoop_api.h"
#include "effect_json.h"
#include "engine_hooks.h"

using nvcoop::json::Value;

namespace {

nvhooks::CaptureHub g_hub;
std::unique_ptr<nvhooks::RemoteApplier> g_applier;
nvhooks::InitReport g_report;
NVSEConsoleInterface* g_console = nullptr;
nvhooks::Role g_role = nvhooks::Role::Host;
bool g_inited = false;
std::vector<Value> g_txns, g_vars;

int RunLineSEH(const char* line, void* thisObj) {
    __try {
        return g_console->RunScriptLine2(line, static_cast<TESObjectREFR*>(thisObj), false) ? 1 : 0;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return -1;
    }
}

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

const char* RoleName(nvhooks::Role r) {
    return r == nvhooks::Role::Host ? "host" : r == nvhooks::Role::Guest ? "guest" : "gateonly";
}

void InitHooks() {
    if (g_inited) return;
    g_inited = true;
    char role[32] = {};
    GetPrivateProfileStringA("coop", "role", "host", role, sizeof role, IniPath().c_str());
    std::string r = role;
    g_role = (r == "client" || r == "guest") ? nvhooks::Role::Guest : r == "gateonly" ? nvhooks::Role::Client : nvhooks::Role::Host;
    g_hub.addSink([](fnvcoop::EffectTxn&& t) { g_txns.push_back(nvhooks::txnToJson(t)); });
    g_report = nvhooks::Init(g_hub, g_role);
    g_applier.reset(new nvhooks::RemoteApplier(g_hub, nvhooks::resolver()));
    auto exec = [](const std::string& line, uint32_t refID) {
        void* ref = refID ? nvhooks::resolver().lookup(refID) : nullptr;
        if (refID && !ref) return false;
        return RunLineSEH(line.c_str(), ref) == 1;
    };
    if (g_console) nvhooks::setConsoleExec(exec);
    if (g_console) g_applier->setExecLine(exec);
    nvcoop::Log("hooks: role=%s installed=%d failed=%d critical=%d", RoleName(g_role), g_report.ok, g_report.failed,
                g_report.allCritical() ? 1 : 0);
    for (auto& h : g_report.hooks) nvcoop::Log("hooks:   %-34s %s", h.name.c_str(), nvhooks::statusName(h.status));
}

Value ScriptsJson() {
    const auto& s = nvhooks::scriptStats();
    Value o = Value::object();
    o.set("gated", (int64_t)s.gated).set("ran", (int64_t)s.ran).set("ranWhileApplying", (int64_t)s.ranWhileApplying);
    return o;
}

bool MStatus(const std::string&, std::string& out) {
    Value o = Value::object(), hs = Value::array();
    for (auto& h : g_report.hooks) {
        Value e = Value::object();
        e.set("name", h.name).set("status", nvhooks::statusName(h.status));
        hs.push(e);
    }
    o.set("role", RoleName(g_role)).set("inited", g_inited).set("installed", g_report.ok).set("failed", g_report.failed)
        .set("criticalOk", g_report.allCritical()).set("hooks", hs);
    out = o.dump();
    return true;
}

bool MBegin(const std::string&, std::string& out) {
    if (!g_inited) { out = "hooks not initialised (DeferredInit not seen yet)"; return false; }
    g_txns.clear();
    g_vars.clear();
    g_hub.resetStats();
    g_hub.setEnabled(g_role == nvhooks::Role::Host);
    Value o = Value::object();
    o.set("capturing", g_hub.enabled()).set("role", RoleName(g_role));
    out = o.dump();
    return true;
}

bool MEnd(const std::string&, std::string& out) {
    Value txns = Value::array(), vars = Value::array();
    for (auto& t : g_txns) txns.push(t);
    for (auto& v : g_vars) vars.push(v);
    const auto& st = g_hub.stats();
    Value stats = Value::object();
    stats.set("recorded", (int64_t)st.recorded).set("droppedNoScope", (int64_t)st.droppedNoScope)
        .set("staleScopes", (int64_t)st.staleScopes).set("weakTxns", (int64_t)st.weakTxns).set("weakFlushes", (int64_t)st.weakFlushes).set("collapsed", (int64_t)st.collapsed).set("droppedEcho", (int64_t)st.droppedEcho).set("txns", (int64_t)st.txns).set("emptyScopes", (int64_t)st.emptyScopes);
    const auto& um = nvhooks::unmapped();
    Value un = Value::object();
    un.set("questCompleted", (int64_t)um.questCompleted).set("itemOnNonPlayer", (int64_t)um.itemOnNonPlayer)
        .set("avOnNonPlayer", (int64_t)um.avOnNonPlayer);
    Value o = Value::object();
    o.set("txns", txns).set("scriptVars", vars).set("stats", stats).set("unmapped", un).set("scripts", ScriptsJson());
    g_txns.clear();
    g_vars.clear();
    out = o.dump();
    return true;
}

// exec.ref {ref:"FalloutNV.esm:104C79", cmd:"KillActor"}: run a console command with that reference as the calling reference
// (test/diagnostic RPC; goes through the same Script::Execute path a script would, so guest suppression applies).
bool MExecRef(const std::string& params, std::string& out) {
    Value p;
    std::string err;
    if (!nvcoop::json::parse(params, p, err)) { out = "bad params: " + err; return false; }
    fnvcoop::FormRef r;
    if (!nvhooks::strToRef(p.get("ref").asString(""), &r)) { out = "bad ref"; return false; }
    uint32_t id = nvhooks::resolver().fromRef(r);
    if (!id) { out = "plugin not loaded"; return false; }
    bool ok;
    if (p.get("scope").asBool(false)) {  // run inside a capture scope so the mutation hooks record it (a bare console line is "ambient")
        fnvcoop::Cause c;
        c.kind = fnvcoop::CauseKind::Console;
        nvhooks::ScopedCapture sc(g_hub, c);
        ok = nvhooks::execConsoleLineOn(p.get("cmd").asString(""), id);
    } else {
        ok = nvhooks::execConsoleLineOn(p.get("cmd").asString(""), id);
    }
    Value o = Value::object();
    o.set("ok", ok);
    out = o.dump();
    return true;
}

// ref.info {ref}: engine state read directly (no script): position, disabled flag, Actor life state (+0x108: 0 alive, 1 dying, 2 dead).
bool MRefInfo(const std::string& params, std::string& out) {
    Value p;
    std::string err;
    if (!nvcoop::json::parse(params, p, err)) { out = "bad params: " + err; return false; }
    fnvcoop::FormRef r;
    if (!nvhooks::strToRef(p.get("ref").asString(""), &r)) { out = "bad ref"; return false; }
    uint32_t id = nvhooks::resolver().fromRef(r);
    TESObjectREFR* ref = id ? static_cast<TESObjectREFR*>(nvhooks::resolver().lookup(id)) : nullptr;
    if (!ref) { out = "reference not loaded"; return false; }
    Value o = Value::object();
    o.set("x", (double)ref->posX).set("y", (double)ref->posY).set("z", (double)ref->posZ)
        .set("lifeState", (int64_t)*reinterpret_cast<uint32_t*>(reinterpret_cast<char*>(ref) + 0x108))
        .set("flags", (int64_t)ref->flags).set("type", (int64_t)ref->typeID);
    out = o.dump();
    return true;
}

// vars.dump {quest}: all (id,value) pairs of the quest script's variable list as pollHostState sees them.
bool MVarsDump(const std::string& params, std::string& out) {
    Value p;
    std::string err;
    if (!nvcoop::json::parse(params, p, err)) { out = "bad params: " + err; return false; }
    Value arr = Value::array();
    for (const Value& q : p.get("quests").items()) {
        fnvcoop::FormRef r;
        Value e = Value::object();
        e.set("quest", q.asString(""));
        Value vars = Value::object();
        if (nvhooks::strToRef(q.asString(""), &r))
            for (auto& kv : nvhooks::questVarsDump(r)) vars.set(std::to_string(kv.first), kv.second);
        e.set("vars", vars);
        arr.push(e);
    }
    Value o = Value::object();
    o.set("quests", arr);
    out = o.dump();
    return true;
}

bool MControls(const std::string& params, std::string& out) {
    Value p;
    std::string err;
    if (!nvcoop::json::parse(params, p, err)) { out = "bad params: " + err; return false; }
    nvhooks::SetPlayerControlsLocked(p.get("locked").asBool(false));
    Value o = Value::object();
    o.set("locked", nvhooks::playerControlsLocked());
    out = o.dump();
    return true;
}

bool MApply(const std::string& params, std::string& out) {
    if (!g_inited || !g_applier) { out = "hooks not initialised"; return false; }
    Value p;
    std::string err;
    if (!nvcoop::json::parse(params, p, err)) { out = "bad params: " + err; return false; }
    fnvcoop::EffectTxn txn;
    if (!nvhooks::txnFromJson(p.get("txn"), &txn, &err)) { out = "bad txn: " + err; return false; }
    g_applier->setUseGate(p.get("gate").asBool(true));
    auto slot = static_cast<fnvcoop::PlayerId>(p.get("slot").asInt(0));
    auto before = nvhooks::scriptStats();
    Value results = Value::array();
    size_t applied = 0;
    for (const fnvcoop::Effect& e : txn.effects) {
        fnvcoop::EffectTxn one;
        one.origin = txn.origin;
        one.effects.push_back(e);
        auto st = g_applier->apply(e);  // recipient filtering is not needed for the single-player proof
        (void)one;
        (void)slot;
        if (st == nvhooks::ApplyStatus::Ok) ++applied;
        Value r = Value::object();
        r.set("type", nvhooks::effectToJson(e).get("type").asString("?")).set("status", nvhooks::applyStatusName(st))
            .set("error", g_applier->lastError());
        results.push(r);
    }
    auto after = nvhooks::scriptStats();
    Value sc = Value::object();
    sc.set("gated", (int64_t)(after.gated - before.gated)).set("ran", (int64_t)(after.ran - before.ran))
        .set("ranWhileApplying", (int64_t)(after.ranWhileApplying - before.ranWhileApplying));
    Value o = Value::object();
    o.set("applied", (int64_t)applied).set("total", (int64_t)txn.effects.size()).set("results", results).set("scripts", sc)
        .set("gate", p.get("gate").asBool(true));
    out = o.dump();
    return true;
}

void OnLoad(void* nvse) {
    if (nvse) g_console = static_cast<NVSEConsoleInterface*>(static_cast<NVSEInterface*>(nvse)->QueryInterface(kInterface_Console));
    nvcoop::RegisterMethod("hooks.status", MStatus, true);
    nvcoop::RegisterMethod("capture.begin", MBegin, true);
    nvcoop::RegisterMethod("capture.end", MEnd, true);
    nvcoop::RegisterMethod("apply.txn", MApply, true);
    nvcoop::RegisterMethod("controls.lock", MControls, true);
    nvcoop::RegisterMethod("exec.ref", MExecRef, true);
    nvcoop::RegisterMethod("ref.info", MRefInfo, true);
    nvcoop::RegisterMethod("vars.dump", MVarsDump, true);
}
void OnMessage(uint32_t type, void*, uint32_t) {
    if (type == NVSEMessagingInterface::kMessage_DeferredInit) InitHooks();
    else if (type == NVSEMessagingInterface::kMessage_ExitGame || type == NVSEMessagingInterface::kMessage_ExitGame_Console)
        nvhooks::Shutdown();
}

}  // namespace

// Per main-loop frame (outside any script): close capture scopes whose end() never ran.
void OnFrame() {
    if (g_inited && g_hub.enabled()) {
        size_t n = g_hub.reapStale();
        if (n) nvcoop::Log("hooks: reaped %zu stale capture scope(s)", n);
    }
}

NVCOOP_MODULE(l5_hooks, "l5_hooks", OnLoad, OnMessage, OnFrame);
