// nvcoop module: applies the NV:MP client.dll patches once NV:MP's client.dll is loaded in this process.
// Registered with NVCOOP_MODULE; compiled into nvcoop.dll by plugin/nvmp/CMakeLists.txt. Game-free
// (includes only nvcoop_api.h), so tests/test_client_patches.cpp exercises it without NVSE.
//
// Config (Data\NVSE\Plugins\nvcoop.ini):
//   [nvmp]
//   enabled=1            0 = never touch NV:MP
//   quested=1            client.quested_not_localized         (recommended first patch)
//   questedRef=0         client.quested_ref_not_localized     (quest items / containers)
//   questedRefScan=0     client.quested_ref_scan_include
//   scriptedRef=0        client.scripted_ref_not_localized
//   ghostDamage=0        client.ghost_damage_* (both)
//   patches=             explicit comma list (full or short names), replaces the flags above
//   autoApply=1          0 = only the RPC applies patches
//   pollFrames=60        fallback poll period while client.dll is not loaded yet
// Env override: NVCOOP_NVMP_PATCHES=quested,questedRef  (same as patches=; empty/"none" disables).
//
// Events (RPC "events"): nvmp.client.detected, nvmp.patch {name,status,rva,ok}, nvmp.patches.done {applied,failed},
// nvmp.client.absent. RPC: nvmp.patches {} -> status; nvmp.patches {"apply":"name"} / {"revert":"name"}.
#include <windows.h>

#include <cstring>
#include <memory>
#include <mutex>
#include <string>

#include "../../common/minijson.h"
#include "../../common/nvcoop_api.h"
#include "client_patcher.h"
#include "players.h"
#include "../ghost_ref.h"

namespace nvmp::client {

// ASCII, case-insensitive, no CRT: is `n` (not necessarily NUL-terminated, `len` wchar_t) exactly "client.dll"?
bool IsClientDllName(const wchar_t* n, size_t len) {
    static const char kName[] = "client.dll";
    if (len != sizeof(kName) - 1) return false;
    for (size_t i = 0; i < len; ++i) {
        wchar_t c = n[i];
        if (c >= L'A' && c <= L'Z') c = (wchar_t)(c - L'A' + L'a');
        if (c != (wchar_t)kName[i]) return false;
    }
    return true;
}

namespace {

// ---- modules -----------------------------------------------------------------------------------
class WinModules : public ModuleSource {
public:
    bool Find(const std::string& name, ModuleView& out) override {
        HMODULE h = GetModuleHandleA(name.c_str());
        if (!h) h = GetModuleHandleA(("nvmp\\" + name).c_str());
        if (!h) return false;
        // Only NV:MP's own client.dll (it lives in an "nvmp" folder): another plugin may also be called client.dll.
        char path[MAX_PATH] = {};
        GetModuleFileNameA(h, path, MAX_PATH);
        for (char* p = path; *p; ++p) if (*p >= 'A' && *p <= 'Z') *p = (char)(*p - 'A' + 'a');
        if (!std::strstr(path, "nvmp")) return false;
        const uint8_t* b = (const uint8_t*)h;
        const IMAGE_DOS_HEADER* dos = (const IMAGE_DOS_HEADER*)b;
        if (dos->e_magic != IMAGE_DOS_SIGNATURE) return false;
        const IMAGE_NT_HEADERS32* nt = (const IMAGE_NT_HEADERS32*)(b + dos->e_lfanew);
        if (nt->Signature != IMAGE_NT_SIGNATURE || nt->OptionalHeader.Magic != IMAGE_NT_OPTIONAL_HDR32_MAGIC) return false;
        out.base = (uint8_t*)h;
        out.size = nt->OptionalHeader.SizeOfImage;
        return true;
    }
};

// ---- DLL notification (ntdll) -------------------------------------------------------------------
struct UStr { USHORT Length, MaximumLength; PWSTR Buffer; };
struct LdrNotifyData { ULONG Flags; const UStr* FullDllName; const UStr* BaseDllName; PVOID DllBase; ULONG SizeOfImage; };
typedef VOID(CALLBACK* LdrNotifyFn)(ULONG reason, const LdrNotifyData* data, PVOID ctx);
typedef LONG(NTAPI* LdrRegFn)(ULONG flags, LdrNotifyFn fn, PVOID ctx, PVOID* cookie);

std::unique_ptr<ClientPatcher> g_patcher;
WinModules g_mods;
PVOID g_cookie = nullptr;

VOID CALLBACK OnDllNotify(ULONG reason, const LdrNotifyData* d, PVOID) {
    // Runs under the loader lock on whatever thread loads a DLL (possibly one the CRT never initialised): touch NOTHING
    // but raw memory here. No CRT calls (the previous _wcsnicmp allocates per-thread locale data), no logging, no allocation.
    if (reason != 1 /*LDR_DLL_NOTIFICATION_REASON_LOADED*/ || !d || !d->BaseDllName || !g_patcher) return;
    const UStr* n = d->BaseDllName;
    if (n->Buffer && IsClientDllName(n->Buffer, n->Length / sizeof(wchar_t))) g_patcher->NotifyModuleLoaded();
}

std::string IniPath() {
    HMODULE self = nullptr;
    GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT, (LPCSTR)&IniPath, &self);
    char p[MAX_PATH] = {};
    GetModuleFileNameA(self, p, MAX_PATH);
    std::string s = p;
    size_t sl = s.find_last_of("\\/");
    return (sl == std::string::npos ? std::string() : s.substr(0, sl + 1)) + "nvcoop.ini";
}

// ---- RPC ----------------------------------------------------------------------------------------
bool RpcPatches(const std::string& paramsJson, std::string& out) {
    if (!g_patcher) { out = "nvmp client patcher is not active"; return false; }
    nvcoop::json::Value v;
    std::string err;
    if (!paramsJson.empty() && paramsJson != "null" && !nvcoop::json::parse(paramsJson, v, err)) { out = "bad params: " + err; return false; }
    for (const char* key : {"apply", "revert"}) {
        const nvcoop::json::Value* n = v.find(key);
        if (!n || !n->isString()) continue;
        const bool apply = key[0] == 'a';
        std::vector<std::string> names = ExpandName(n->asString());
        if (names.empty()) { out = "unknown patch: " + n->asString(); return false; }
        for (auto& name : names) {
            PatchStatus s = apply ? g_patcher->Apply(name) : g_patcher->Revert(name);
            if (s != PatchStatus::Applied && s != PatchStatus::AlreadyApplied && s != PatchStatus::Reverted) {
                out = name + ": " + ToString(s);
                return false;
            }
        }
    }
    out = g_patcher->StatusJson();
    return true;
}

// ---- nvmp.players ---------------------------------------------------------------------------------
// [{netId, name, refId, isLocal}] for every NV:MP character the client knows (remote players are ghosts in the host's world).
std::mutex g_bindMu;
std::vector<SlotBindings> g_bindings;   // explicit coop slot -> NV:MP net id

// Resolves the natives once and walks the character list. Main thread only.
bool Snapshot(std::vector<PlayerInfo>& ps, std::string& out) {
    static PlayersNatives natives;
    static bool resolved = false;
    ModuleView mv;
    if (!g_mods.Find("client.dll", mv)) { out = "NV:MP client.dll is not loaded"; return false; }
    if (!resolved || !natives.managerGlobal) {
        ResolvedPlayers r = ResolvePlayers(mv.base, mv.size);
        if (!r.ok()) { out = "NV:MP layout not recognised: " + r.error; return false; }
#if defined(_M_IX86)
        natives.getComponent = (decltype(natives.getComponent))(mv.base + r.getComponentRva);
        natives.getGameRef = (decltype(natives.getGameRef))(mv.base + r.getGameRefRva);
#endif
        natives.managerGlobal = r.managerGlobal;
        natives.thePlayerGlobal = kThePlayerGlobal_1_4_0_525;
        resolved = true;
        nvcoop::Log("nvmp players: GetComponent=%p GetGameRef=%p managerGlobal=0x%08X", (void*)(mv.base + r.getComponentRva),
                    (void*)(mv.base + r.getGameRefRva), (unsigned)r.managerGlobal);
    }
    bool faulted = false;
    ps = EnumeratePlayers(natives, PlayersLayout{}, &faulted);
    if (faulted) nvcoop::Log("nvmp players: memory fault while walking the character list (partial result)");
    return true;
}

bool RpcPlayers(const std::string&, std::string& out) {
    std::vector<PlayerInfo> ps;
    if (!Snapshot(ps, out)) return false;
    out = PlayersJson(ps);
    return true;
}

// {"slot":1,"netId":11} binds a coop slot to an NV:MP character; {"slot":1,"netId":null} or {"clear":true} removes bindings.
bool RpcBindSlot(const std::string& paramsJson, std::string& out) {
    nvcoop::json::Value v;
    std::string err;
    if (!nvcoop::json::parse(paramsJson, v, err)) { out = "bad params: " + err; return false; }
    std::lock_guard<std::mutex> g(g_bindMu);
    if (v.get("clear").asBool(false)) g_bindings.clear();
    const nvcoop::json::Value* s = v.find("slot");
    if (s && s->isNumber()) {
        const int slot = (int)s->asInt();
        if (slot < 1 || slot > 255) { out = "slot must be 1..255"; return false; }
        for (size_t i = 0; i < g_bindings.size(); ++i)
            if (g_bindings[i].slot == slot) { g_bindings.erase(g_bindings.begin() + (long)i); break; }
        const nvcoop::json::Value* n = v.find("netId");
        if (n && n->isNumber()) g_bindings.push_back({(uint8_t)slot, (uint32_t)n->asInt()});
    }
    out = "[";
    for (size_t i = 0; i < g_bindings.size(); ++i)
        out += (i ? "," : "") + std::string("{\"slot\":") + std::to_string(g_bindings[i].slot) + ",\"netId\":" + std::to_string(g_bindings[i].netId) + "}";
    out += "]";
    return true;
}

// ---- module -------------------------------------------------------------------------------------
void OnLoad(void*) {
    const std::string ini = IniPath();
    ConfigGetter get = [&](const char* key, const char* def) {
        char buf[512];
        GetPrivateProfileStringA("nvmp", key, def, buf, sizeof buf, ini.c_str());
        return std::string(buf);
    };
    std::vector<std::string> warns;
    Settings s = ParseSettings(get, &warns);
    char env[512];
    DWORD n = GetEnvironmentVariableA("NVCOOP_NVMP_PATCHES", env, sizeof env);
    if (n > 0 && n < sizeof env) {
        ConfigGetter eg = [&](const char* key, const char* def) { return std::string(key) == "patches" ? std::string(env) : get(key, def); };
        s = ParseSettings(eg, &warns);
    }
    for (auto& w : warns) nvcoop::Log("nvmp patches: config warning: %s", w.c_str());
    std::string names;
    for (auto& e : s.enabled) names += (names.empty() ? "" : ",") + e;
    nvcoop::Log("nvmp patches: enabled=[%s] autoApply=%d", names.c_str(), s.autoApply);

    g_patcher = std::make_unique<ClientPatcher>(
        std::move(s), g_mods, ProtectedWriter(), [](const char* type, const std::string& json) {
            nvcoop::PushEvent(type, json);
            nvcoop::Log("nvmp event %s %s", type, json.c_str());
        });
    // The loader-lock notification is OPT-IN ([nvmp] ldrNotify=1): polling every pollFrames frames is enough, and a
    // callback invoked on arbitrary loader threads is the riskiest thing this module could do inside the game.
    if (get("ldrNotify", "0") == "1") {
        if (HMODULE nt = GetModuleHandleA("ntdll.dll")) {
            if (auto reg = (LdrRegFn)GetProcAddress(nt, "LdrRegisterDllNotification")) reg(0, OnDllNotify, nullptr, &g_cookie);
        }
        nvcoop::Log("nvmp patches: LdrRegisterDllNotification enabled");
    }
    nvcoop::RegisterMethod("nvmp.patches", RpcPatches, true);
    nvcoop::RegisterMethod("nvmp.players", RpcPlayers, true);
    nvcoop::RegisterMethod("nvmp.bindslot", RpcBindSlot, true);
}

// Last line of defence: an access violation while scanning/patching the foreign module must never take the game down.
// (Own function: __try cannot share a function with objects that need unwinding.)
bool GuardedFrame(ClientPatcher* p) {
    __try {
        p->OnFrame();
        return true;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return false;
    }
}

bool g_faulted = false;

void OnFrame() {
    if (!g_patcher || g_faulted) return;
    if (!GuardedFrame(g_patcher.get())) {
        g_faulted = true;   // never touch NV:MP again this session
        nvcoop::Log("nvmp patches: exception while checking/patching client.dll; module disabled");
        nvcoop::PushEvent("nvmp.fault", "{\"where\":\"OnFrame\"}");
    }
}

}  // namespace

// exposed for tests
ClientPatcher* ActivePatcher() { return g_patcher.get(); }

}  // namespace nvmp::client

namespace nvmp {

uint32_t GhostRefOfPlayer(uint8_t coopSlot) {
    std::vector<client::PlayerInfo> ps;
    std::string err;
    if (!client::Snapshot(ps, err)) return 0;
    std::lock_guard<std::mutex> g(client::g_bindMu);
    return client::SelectGhostRef(ps, coopSlot, client::g_bindings);
}

void BindSlotToNetId(uint8_t coopSlot, uint32_t netId) {
    if (coopSlot == 0) return;
    std::lock_guard<std::mutex> g(client::g_bindMu);
    for (size_t i = 0; i < client::g_bindings.size(); ++i)
        if (client::g_bindings[i].slot == coopSlot) { client::g_bindings.erase(client::g_bindings.begin() + (long)i); break; }
    if (netId) client::g_bindings.push_back({coopSlot, netId});
}

uint32_t NetIdByName(const std::string& name) {
    std::vector<client::PlayerInfo> ps;
    std::string err;
    if (!client::Snapshot(ps, err)) return 0;
    auto low = [](std::string x) { for (auto& c : x) if (c >= 'A' && c <= 'Z') c = (char)(c - 'A' + 'a'); return x; };
    const std::string want = low(name);
    for (const auto& p : ps)
        if (!p.isLocal && low(p.name) == want) return p.netId;
    return 0;
}

namespace client {
NVCOOP_MODULE(nvmp_client_patches, "nvmp_client_patches", OnLoad, nullptr, OnFrame);
}  // namespace client

}  // namespace nvmp
