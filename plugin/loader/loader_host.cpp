#include "loader_host.h"

#include <algorithm>
#include <chrono>
#include <cstddef>
#include <cstdio>
#include <cstring>

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>

#include "common/nvcoop_api.h"
#include "common/registry.h"

// Mutation twins (see tests): each one breaks exactly one safety rule and the tests must notice.
//   NVLOADER_MUTANT_NOCOPY    load the core in place (the build can no longer overwrite it)
//   NVLOADER_MUTANT_NOPURGE   leave the old core's bridge methods registered after unload
//   NVLOADER_MUTANT_NOVETO    unload even when the core vetoes
//   NVLOADER_MUTANT_NOROLLBACK stay dead when the new core fails to load

namespace nvcoop::loader {
namespace {

constexpr int kOwnerCore = 1;   // RegisterMethodClosure owner tag for methods registered by the core
constexpr int kOwnerDev = 2;    // tag for dev.* methods owned by the host itself

CoreHost* g_current = nullptr;

using Clock = std::chrono::steady_clock;
double MsSince(Clock::time_point t) {
    return std::chrono::duration<double, std::milli>(Clock::now() - t).count();
}

std::string JsonEsc(const std::string& s) {
    std::string o;
    for (unsigned char c : s) {
        switch (c) {
        case '"': o += "\\\""; break;
        case '\\': o += "\\\\"; break;
        case '\n': o += "\\n"; break;
        case '\r': o += "\\r"; break;
        case '\t': o += "\\t"; break;
        default:
            if (c < 0x20) { char b[8]; std::snprintf(b, sizeof b, "\\u%04x", c); o += b; }
            else o += static_cast<char>(c);
        }
    }
    return o;
}

std::string DirOf(const std::string& path) {
    size_t p = path.find_last_of("\\/");
    return p == std::string::npos ? std::string() : path.substr(0, p + 1);
}
std::string StemOf(const std::string& path) {
    size_t p = path.find_last_of("\\/");
    std::string f = p == std::string::npos ? path : path.substr(p + 1);
    size_t d = f.find_last_of('.');
    return d == std::string::npos ? f : f.substr(0, d);
}

void SetStr(void* h, const char* text) { *static_cast<std::string*>(h) = text ? text : ""; }

struct GateScope {
    CoreHost::Gate& g;
    bool ok;
    explicit GateScope(CoreHost::Gate& gate) : g(gate) {
        g.inflight.fetch_add(1);
        ok = g.open.load();
        if (!ok) g.inflight.fetch_sub(1);
    }
    ~GateScope() { if (ok) g.inflight.fetch_sub(1); }
};

// ---- loader -> core api callbacks (stateless C functions; they reach the host through Current()) ----
void ApiLog(const char* line) { nvcoop::Log("%s", line ? line : ""); }
void ApiPushEvent(const char* type, const char* dataJson) {
    nvcoop::PushEvent(type, dataJson ? std::string(dataJson) : std::string());
}
void ApiRegisterMethod(const char* name, NvMethodFn fn, void* user, int mainThread) {
    CoreHost* h = Current();
    if (!h || !name || !fn) return;
    std::shared_ptr<CoreHost::Gate> gate = h->gate();
    nvcoop::RegisterMethodClosure(
        name,
        [gate, fn, user](const std::string& params, std::string& out) -> bool {
            GateScope s(*gate);
            if (!s.ok) { out = "core is reloading, retry"; return false; }
            std::string res;
            NvOut o{&res, &SetStr};
            int r = 0;
            try { r = fn(user, params.c_str(), &o); } catch (...) { res = "exception in core method"; }
            out = res;
            return r != 0;
        },
        mainThread != 0, kOwnerCore);
}
std::string StateTrampoline(const std::string& csv) {
    CoreHost* h = Current();
    if (!h || !h->stateFn) return "{}";
    std::shared_ptr<CoreHost::Gate> gate = h->gate();
    GateScope s(*gate);
    if (!s.ok) return "{\"error\":\"core is reloading\"}";
    std::string res;
    NvOut o{&res, &SetStr};
    try { h->stateFn(csv.c_str(), &o); } catch (...) { res = "{\"error\":\"exception in core state provider\"}"; }
    return res;
}
void ApiSetStateProvider(NvStateFn fn) {
    CoreHost* h = Current();
    if (!h) return;
    h->stateFn = fn;
    nvcoop::SetStateProvider(fn ? &StateTrampoline : nullptr);
}
int ApiBlobPut(const char* key, const void* data, uint32_t len, uint32_t version) {
    CoreHost* h = Current();
    if (!h || !key) return 0;
    std::lock_guard<std::mutex> g(h->blobMu);
    auto& b = h->blobs[key];
    b.data.assign(static_cast<const uint8_t*>(data), static_cast<const uint8_t*>(data) + (data ? len : 0));
    b.version = version;
    return 1;
}
int ApiBlobGet(const char* key, void* buf, uint32_t cap, uint32_t* len, uint32_t* version) {
    CoreHost* h = Current();
    if (!h || !key) return 0;
    std::lock_guard<std::mutex> g(h->blobMu);
    auto it = h->blobs.find(key);
    if (it == h->blobs.end()) return 0;
    if (len) *len = static_cast<uint32_t>(it->second.data.size());
    if (version) *version = it->second.version;
    if (buf && cap) std::memcpy(buf, it->second.data.data(), std::min<size_t>(cap, it->second.data.size()));
    return 1;
}
void ApiBlobErase(const char* key) {
    CoreHost* h = Current();
    if (!h || !key) return;
    std::lock_guard<std::mutex> g(h->blobMu);
    h->blobs.erase(key);
}
int ApiHasMethod(const char* name) {
    nvcoop::registry::MethodEntry me;
    return name && nvcoop::registry::FindMethod(name, me) ? 1 : 0;
}
int ApiCallMethod(const char* name, const char* params, NvOut* out) {
    nvcoop::registry::MethodEntry me;
    if (!name || !nvcoop::registry::FindMethod(name, me)) return -1;
    std::string res;
    bool ok = me.fn(params ? params : "", res);
    if (out && out->set) out->set(out->h, res.c_str());
    return ok ? 1 : 0;
}
void ApiRequestReload(const char* reason) {
    if (CoreHost* h = Current()) h->RequestReload(reason);
}

void PurgeCoreRegistrations(CoreHost& h) {
#ifndef NVLOADER_MUTANT_NOPURGE
    nvcoop::UnregisterMethodsByOwner(kOwnerCore);
#endif
    h.stateFn = nullptr;
    nvcoop::SetStateProvider(nullptr);
}

uint64_t g_seq = 0;   // cache file sequence, unique per process

}  // namespace

CoreHost* Current() { return g_current; }

CoreHost::CoreHost(HostEnv env) : env_(std::move(env)) {
    if (env_.cacheDir.empty()) env_.cacheDir = DirOf(env_.corePath) + "nvcoop_cache\\";
    if (!env_.cacheDir.empty() && env_.cacheDir.back() != '\\' && env_.cacheDir.back() != '/') env_.cacheDir += '\\';
    g_current = this;
    gate_ = std::make_shared<Gate>();
    CleanStaleCache();
}

CoreHost::~CoreHost() {
    StopWatcher();
    nvcoop::UnregisterMethodsByOwner(kOwnerDev);
    nvcoop::UnregisterMethodsByOwner(kOwnerCore);
    if (g_current == this) g_current = nullptr;
}

void CoreHost::CleanStaleCache() {
    // Cache files of dead processes. DeleteFile fails for files still loaded by a live game: that is the filter.
    std::string pattern = env_.cacheDir + StemOf(env_.corePath) + ".*.dll";
    WIN32_FIND_DATAA fd;
    HANDLE h = FindFirstFileA(pattern.c_str(), &fd);
    if (h == INVALID_HANDLE_VALUE) return;
    do { DeleteFileA((env_.cacheDir + fd.cFileName).c_str()); } while (FindNextFileA(h, &fd));
    FindClose(h);
}

std::string CoreHost::MakeCachePath(uint32_t seq) const {
    char b[64];
    std::snprintf(b, sizeof b, ".%lu.%u.dll", GetCurrentProcessId(), seq);
    return env_.cacheDir + StemOf(env_.corePath) + b;
}

void CoreHost::FillApi(uint32_t generation, bool isReload) {
    api = NvLoaderApi{};
    api.abi_version = NVLOADER_ABI_VERSION;
    api.size = sizeof(NvLoaderApi);
    api.generation = generation;
    api.is_reload = isReload ? 1 : 0;
    api.game_dir = env_.gameDir.c_str();
    api.nvse_interface = env_.nvse;
    api.log = &ApiLog;
    api.push_event = &ApiPushEvent;
    api.register_method = &ApiRegisterMethod;
    api.set_state_provider = &ApiSetStateProvider;
    api.blob_put = &ApiBlobPut;
    api.blob_get = &ApiBlobGet;
    api.blob_erase = &ApiBlobErase;
    api.request_reload = &ApiRequestReload;
    api.has_method = &ApiHasMethod;
    api.call_method = &ApiCallMethod;
}

bool CoreHost::LoadCoreFrom(const std::string& sourcePath, bool isReload, uint32_t generation, LoadedCore& out,
                            std::string& err) {
    CreateDirectoryA(env_.cacheDir.c_str(), nullptr);
#ifdef NVLOADER_MUTANT_NOCOPY
    std::string cache = sourcePath;
#else
    std::string cache = MakeCachePath(static_cast<uint32_t>(++g_seq));
    // The linker may still be finishing the file; retry the copy for up to ~2 s.
    bool copied = false;
    for (int i = 0; i < 20 && !copied; ++i) {
        copied = CopyFileA(sourcePath.c_str(), cache.c_str(), FALSE) != 0;
        if (!copied) Sleep(100);
    }
    if (!copied) { err = "cannot copy core '" + sourcePath + "' to cache (error " + std::to_string(GetLastError()) + ")"; return false; }
#endif
    HMODULE m = LoadLibraryExA(cache.c_str(), nullptr, LOAD_WITH_ALTERED_SEARCH_PATH);
    if (!m) {
        err = "LoadLibrary failed for core (error " + std::to_string(GetLastError()) + ")";
#ifndef NVLOADER_MUTANT_NOCOPY
        DeleteFileA(cache.c_str());
#endif
        return false;
    }
    auto fail = [&](const std::string& why) {
        err = why;
        FreeLibrary(m);
#ifndef NVLOADER_MUTANT_NOCOPY
        DeleteFileA(cache.c_str());
#endif
        return false;
    };
    auto init = reinterpret_cast<NvCoreInitFn>(GetProcAddress(m, NVCORE_INIT_EXPORT));
    if (!init) return fail("core has no export " NVCORE_INIT_EXPORT);

    gate_ = std::make_shared<Gate>();
    FillApi(generation, isReload);
    NvCoreApi core{};
    core.size = sizeof(NvCoreApi);
    int ok = 0;
    try { ok = init(&api, &core); } catch (...) { ok = 0; }
    if (!ok) { PurgeCoreRegistrations(*this); return fail(NVCORE_INIT_EXPORT " returned failure"); }
    if (core.abi_version != NVLOADER_ABI_VERSION) {
        PurgeCoreRegistrations(*this);
        return fail("core ABI version " + std::to_string(core.abi_version) + " != loader " + std::to_string(NVLOADER_ABI_VERSION));
    }
    if (core.size < offsetof(NvCoreApi, before_unload) + sizeof(void*) || !core.init || !core.before_unload) {
        PurgeCoreRegistrations(*this);
        return fail("core NvCoreApi is incomplete");
    }
    out.module = m;
    out.cachePath = cache;
    out.core = core;
    // core.init() runs the module onLoad / restore step; calls into the core count as "inside core".
    int iok = 0;
    ++depth_;
    try { iok = core.init(); } catch (...) { iok = 0; }
    --depth_;
    if (!iok) {
        ++depth_;
        try { core.before_unload(); } catch (...) {}   // let a half-initialised core remove its hooks
        --depth_;
        PurgeCoreRegistrations(*this);
        return fail("core init() returned failure");
    }
    return true;
}

void CoreHost::UnloadCore(LoadedCore& c) {
    if (c.module) FreeLibrary(static_cast<HMODULE>(c.module));
    c.module = nullptr;
}

bool CoreHost::Load(std::string& err) {
    if (loaded_) return true;
    RegisterDevMethods();
    LoadedCore n;
    uint32_t gen = generation_ ? generation_ : 1;
    if (!LoadCoreFrom(env_.corePath, generation_ != 0, gen, n, err)) {
        nvcoop::Log("loader: core load failed: %s", err.c_str());
        last_ = ReloadResult{};
        last_.error = err;
        return false;
    }
    cur_ = n;
    cachePath_ = n.cachePath;
    generation_ = gen;
    loaded_ = true;
    nvcoop::Log("loader: core generation %u loaded from %s", generation_, cachePath_.c_str());
    return true;
}

ReloadResult CoreHost::Reload(const char* reason) {
    ReloadResult res;
    lastReason_ = reason ? reason : "";
    auto t0 = Clock::now();
    auto finish = [&](ReloadResult r) {
        last_ = r;
        std::string d = "{\"ok\":" + std::string(r.ok ? "true" : "false") + ",\"generation\":" + std::to_string(r.generation) +
                        ",\"rolledBack\":" + (r.rolledBack ? "true" : "false") + ",\"error\":\"" + JsonEsc(r.error) + "\"}";
        nvcoop::PushEvent(r.ok ? "dev.reloaded" : "dev.reloadFailed", d);
        return r;
    };
    if (depth_ > 0) { res.error = "refused: reload requested from inside a core call (only allowed between frames)"; res.generation = generation_; return finish(res); }
    if (!loaded_) {
        std::string e;
        res.ok = Load(e);
        res.error = e;
        res.generation = generation_;
        res.msLoad = MsSince(t0);
        return finish(res);
    }
    nvcoop::PushEvent("dev.reloading", "{\"reason\":\"" + JsonEsc(lastReason_) + "\"}");

    // 1) stop new bridge calls into the core and wait for the ones in flight
    auto oldGate = gate_;
    oldGate->open = false;
    for (int i = 0; i < 2000 && oldGate->inflight.load() != 0; ++i) Sleep(1);
    if (oldGate->inflight.load() != 0) {
        oldGate->open = true;
        res.error = "refused: a bridge call into the core did not finish within 2 s";
        res.generation = generation_;
        return finish(res);
    }

    // 2) the core quiesces: threads stopped, hooks uninstalled, state saved to blobs
    int ready = 0;
    ++depth_;
    try { ready = cur_.core.before_unload(); } catch (...) { ready = 0; }
    --depth_;
#ifdef NVLOADER_MUTANT_NOVETO
    ready = 1;
#endif
    if (!ready) {
        oldGate->open = true;
        res.error = "core vetoed the unload (see nvcoop.log); old core stays loaded";
        res.generation = generation_;
        return finish(res);
    }
    res.msUnload = MsSince(t0);

    // 3) unload
    PurgeCoreRegistrations(*this);
    std::map<std::string, Blob> blobSnapshot;
    { std::lock_guard<std::mutex> g(blobMu); blobSnapshot = blobs; }
    LoadedCore prev = cur_;
    UnloadCore(cur_);
    loaded_ = false;
    if (prev.cachePath != env_.corePath && GetModuleHandleA(prev.cachePath.c_str()) != nullptr) {
        // something else still holds the module (a leaked thread or hook): the old code is still mapped.
        res.error = "core DLL is still mapped after FreeLibrary (leaked thread or hook?)";
        res.coreDead = true;
        res.generation = 0;
        return finish(res);
    }

    // 4) load the new build
    auto t1 = Clock::now();
    LoadedCore n;
    std::string err;
    if (LoadCoreFrom(env_.corePath, true, generation_ + 1, n, err)) {
        cur_ = n;
        cachePath_ = n.cachePath;
        ++generation_;
        ++reloadCount_;
        loaded_ = true;
        if (prev.cachePath != n.cachePath) DeleteFileA(prev.cachePath.c_str());
        res.ok = true;
        res.generation = generation_;
        res.msLoad = MsSince(t1);
        nvcoop::Log("loader: reload ok, generation %u (%s)", generation_, lastReason_.c_str());
        return finish(res);
    }
    nvcoop::Log("loader: reload failed: %s", err.c_str());
    res.error = err;
#ifndef NVLOADER_MUTANT_NOROLLBACK
    { std::lock_guard<std::mutex> g(blobMu); blobs = blobSnapshot; }
    LoadedCore back;
    std::string err2;
    if (LoadCoreFrom(prev.cachePath, true, generation_, back, err2)) {
        cur_ = back;
        cachePath_ = back.cachePath;
        loaded_ = true;
        if (prev.cachePath != back.cachePath) DeleteFileA(prev.cachePath.c_str());
        res.rolledBack = true;
        res.generation = generation_;
        res.msLoad = MsSince(t1);
        return finish(res);
    }
    res.error += "; rollback also failed: " + err2;
#endif
    res.coreDead = true;
    res.generation = 0;
    return finish(res);
}

bool CoreHost::Unload(std::string& err) {
    if (!loaded_) return true;
    if (depth_ > 0) { err = "refused: inside a core call"; return false; }
    auto oldGate = gate_;
    oldGate->open = false;
    for (int i = 0; i < 2000 && oldGate->inflight.load() != 0; ++i) Sleep(1);
    int ready = 0;
    if (oldGate->inflight.load() == 0) {
        ++depth_;
        try { ready = cur_.core.before_unload(); } catch (...) { ready = 0; }
        --depth_;
    }
    if (!ready) { oldGate->open = true; err = "core vetoed the unload or a call is still in flight"; return false; }
    PurgeCoreRegistrations(*this);
    std::string path = cur_.cachePath;
    UnloadCore(cur_);
    loaded_ = false;
    if (path != env_.corePath) DeleteFileA(path.c_str());
    return true;
}

bool CoreHost::Service() {
    if (!reloadPending.exchange(false)) return false;
    std::string why;
    { std::lock_guard<std::mutex> g(pendingMu); why = pendingReason; }
    Reload(why.c_str());
    return true;
}

void CoreHost::OnFrame() {
    Service();
    if (!loaded_ || !cur_.core.on_frame) return;
    ++depth_;
    try { cur_.core.on_frame(); } catch (...) { nvcoop::Log("loader: exception from core on_frame"); }
    --depth_;
}

void CoreHost::OnMessage(uint32_t type, void* data, uint32_t len) {
    if (!loaded_ || !cur_.core.on_message) return;
    ++depth_;
    try { cur_.core.on_message(type, data, len); } catch (...) { nvcoop::Log("loader: exception from core on_message"); }
    --depth_;
}

void CoreHost::Shutdown() { StopWatcher(); }

void CoreHost::RequestReload(const char* reason) {
    { std::lock_guard<std::mutex> g(pendingMu); pendingReason = reason ? reason : "requested"; }
    reloadPending = true;
}

bool CoreHost::StartWatcher(int intervalMs) {
    if (watcher_.joinable()) return true;
    if (intervalMs < 20) intervalMs = 20;
    stopWatcher_ = false;
    std::string path = env_.corePath;
    watcher_ = std::thread([this, path, intervalMs]() {
        auto stamp = [&](uint64_t& t, uint64_t& sz) {
            WIN32_FILE_ATTRIBUTE_DATA a;
            if (!GetFileAttributesExA(path.c_str(), GetFileExInfoStandard, &a)) return false;
            t = (static_cast<uint64_t>(a.ftLastWriteTime.dwHighDateTime) << 32) | a.ftLastWriteTime.dwLowDateTime;
            sz = (static_cast<uint64_t>(a.nFileSizeHigh) << 32) | a.nFileSizeLow;
            return true;
        };
        uint64_t baseT = 0, baseS = 0, lastT = 0, lastS = 0;
        stamp(baseT, baseS);
        lastT = baseT; lastS = baseS;
        bool changed = false;
        while (!stopWatcher_.load()) {
            Sleep(intervalMs);
            uint64_t t, s;
            if (!stamp(t, s)) continue;
            if (t != lastT || s != lastS) { lastT = t; lastS = s; changed = true; continue; }   // still being written
            if (changed || t != baseT || s != baseS) {
                // stable for one full interval: the build finished
                baseT = t; baseS = s; changed = false;
                RequestReload("file changed");
            }
        }
    });
    return true;
}

void CoreHost::StopWatcher() {
    stopWatcher_ = true;
    if (watcher_.joinable()) watcher_.join();
}

std::string CoreHost::StatusJson() const {
    std::string s = "{\"loaded\":" + std::string(loaded_ ? "true" : "false") + ",\"generation\":" + std::to_string(generation_) +
                    ",\"reloadCount\":" + std::to_string(reloadCount_) + ",\"corePath\":\"" + JsonEsc(env_.corePath) +
                    "\",\"cachePath\":\"" + JsonEsc(cachePath_) + "\",\"watching\":" + (watcher_.joinable() ? "true" : "false") +
                    ",\"pending\":" + (reloadPending.load() ? "true" : "false") + ",\"last\":{\"ok\":" + (last_.ok ? "true" : "false") +
                    ",\"rolledBack\":" + (last_.rolledBack ? "true" : "false") + ",\"coreDead\":" + (last_.coreDead ? "true" : "false") +
                    ",\"error\":\"" + JsonEsc(last_.error) + "\",\"msUnload\":" + std::to_string(last_.msUnload) +
                    ",\"msLoad\":" + std::to_string(last_.msLoad) + ",\"reason\":\"" + JsonEsc(lastReason_) + "\"}}";
    return s;
}

void CoreHost::RegisterDevMethods() {
    if (devRegistered_) return;
    devRegistered_ = true;
    nvcoop::RegisterMethodClosure(
        "dev.reload",
        [this](const std::string& params, std::string& out) -> bool {
            std::string reason = "bridge";
            size_t p = params.find("\"reason\"");
            if (p != std::string::npos) {
                size_t a = params.find('"', params.find(':', p) + 1);
                size_t b = a == std::string::npos ? a : params.find('"', a + 1);
                if (b != std::string::npos) reason = params.substr(a + 1, b - a - 1);
            }
            ReloadResult r = Reload(reason.c_str());
            if (!r.ok) { out = r.error + (r.rolledBack ? " (rolled back to the previous core)" : ""); return false; }
            out = "{\"generation\":" + std::to_string(r.generation) + ",\"msUnload\":" + std::to_string(r.msUnload) +
                  ",\"msLoad\":" + std::to_string(r.msLoad) + "}";
            return true;
        },
        true, kOwnerDev);
    nvcoop::RegisterMethodClosure(
        "dev.status", [this](const std::string&, std::string& out) -> bool { out = StatusJson(); return true; }, true, kOwnerDev);
}

}  // namespace nvcoop::loader
