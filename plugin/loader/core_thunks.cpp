// Core-side implementation of nvcoop_api.h for the split build (cmake -DNVCOOP_SPLIT_LOADER=ON).
// In the monolith these functions live in common/nvcoop_api.cpp; in the core DLL they forward to the
// permanent loader through the C ABI, so module code compiles and behaves unchanged.
#include <cstddef>
#include <cstdarg>
#include <cstdio>
#include <memory>
#include <string>
#include <utility>
#include <vector>

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>

#include "common/registry.h"
#include "core_thunks.h"

namespace nvcoop {
namespace {

struct PendingMethod {
    std::string name;
    MethodFn fn;
    bool mainThread;
};
struct State {
    std::vector<Module> modules;
    std::vector<PendingMethod> pending;     // registered before the loader api was known (static init)
    std::vector<std::unique_ptr<MethodClosure>> closures;   // owned here; freed when this core DLL unloads
    StateProvider provider = nullptr;
    bool providerPending = false;
};
State& S() {
    static State s;   // function-static: safe for NVCOOP_MODULE static initialisers in other TUs
    return s;
}

const NvLoaderApi* g_api = nullptr;

int MethodTramp(void* user, const char* params, NvOut* out) {
    auto fn = reinterpret_cast<MethodFn>(user);
    std::string res;
    bool ok = fn(params ? params : "", res);
    out->set(out->h, res.c_str());
    return ok ? 1 : 0;
}

int ClosureTramp(void* user, const char* params, NvOut* out) {
    std::string res;
    bool ok = (*static_cast<MethodClosure*>(user))(params ? params : "", res);
    out->set(out->h, res.c_str());
    return ok ? 1 : 0;
}

int StateTramp(const char* csv, NvOut* out) {
    StateProvider fn = S().provider;
    if (!fn) { out->set(out->h, "{}"); return 0; }
    std::string res = fn(csv ? csv : "");
    out->set(out->h, res.c_str());
    return 1;
}

}  // namespace

namespace loader_core {
void SetApi(const NvLoaderApi* api) {
    g_api = api;
    if (!api) return;
    State& s = S();
    for (auto& p : s.pending) api->register_method(p.name.c_str(), &MethodTramp, reinterpret_cast<void*>(p.fn), p.mainThread ? 1 : 0);
    s.pending.clear();
    if (s.providerPending) api->set_state_provider(s.provider ? &StateTramp : nullptr);
    s.providerPending = false;
}
const NvLoaderApi* Api() { return g_api; }
}  // namespace loader_core

void PushEvent(const char* type, const std::string& dataJson) {
    if (g_api && g_api->push_event) g_api->push_event(type ? type : "", dataJson.empty() ? nullptr : dataJson.c_str());
}

void SetStateProvider(StateProvider fn) {
    State& s = S();
    s.provider = fn;
    if (g_api) g_api->set_state_provider(fn ? &StateTramp : nullptr);
    else s.providerPending = true;
}

void RegisterMethod(const char* name, MethodFn fn, bool mainThread) {
    if (!name || !fn) return;
    if (g_api) g_api->register_method(name, &MethodTramp, reinterpret_cast<void*>(fn), mainThread ? 1 : 0);
    else S().pending.push_back({name, fn, mainThread});
}

void RegisterMethodClosure(const char* name, MethodClosure fn, bool mainThread, int /*owner*/) {
    if (!name || !fn) return;
    State& s = S();
    s.closures.push_back(std::make_unique<MethodClosure>(std::move(fn)));
    void* user = s.closures.back().get();
    // pending (pre-api) registrations of closures are not supported: modules register from onLoad
    if (g_api) g_api->register_method(name, &ClosureTramp, user, mainThread ? 1 : 0);
}

// The loader removes everything this core registered before it unloads it.
void UnregisterMethodsByOwner(int) {}

void RegisterModule(const Module& m) { S().modules.push_back(m); }

// The loader owns the NVSE serialization callbacks and forwards to these until it unloads this core (see loader_abi.h set_cosave).
void SetCosaveCallbacks(void (*save)(void* intfc), void (*load)(void* intfc)) {
    if (g_api && g_api->size >= offsetof(NvLoaderApi, set_cosave) + sizeof(void*) && g_api->set_cosave) g_api->set_cosave(save, load);
    else Log("cosave: this loader cannot forward cosave callbacks (built before set_cosave): the cosave is not written");
}
void RunCosave(bool, void*) {}   // only the loader's forwarders run cosaves
bool HasCosaveCallbacks() { return false; }

void Log(const char* fmt, ...) {
    char buf[1024];
    va_list ap;
    va_start(ap, fmt);
    std::vsnprintf(buf, sizeof buf, fmt, ap);
    va_end(ap);
    if (g_api && g_api->log) g_api->log(buf);
    else { OutputDebugStringA("[nvcoop-core] "); OutputDebugStringA(buf); OutputDebugStringA("\n"); }
}

namespace registry {
// Core-side view of the loader's method registry (used by modules that call bridge methods in-process).
bool FindMethod(const std::string& name, MethodEntry& out) {
    if (!g_api || !g_api->has_method || !g_api->has_method(name.c_str())) return false;
    const NvLoaderApi* api = g_api;
    out.mainThread = true;
    out.owner = 0;
    out.fn = [api, name](const std::string& params, std::string& res) -> bool {
        std::string text;
        NvOut o{&text, [](void* h, const char* t) { *static_cast<std::string*>(h) = t ? t : ""; }};
        int r = api->call_method(name.c_str(), params.c_str(), &o);
        res = r < 0 ? "no such method" : text;
        return r > 0;
    };
    return true;
}
const std::vector<Module>& Modules() { return S().modules; }
}  // namespace registry

}  // namespace nvcoop
