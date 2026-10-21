#include "fnvcoop_mut.h"  // FNV_MUT(n): runtime mutation twins (env FNVCOOP_MUT=n); constant false in production builds
#include "common/reload.h"

#include <cstdio>
#include <cstdlib>

#include "common/nvcoop_api.h"
#include "hooks/hook_patch.h"
#include "reload_drain.h"
#include "reload_state.h"

namespace nvcoop::reload {
namespace {

constexpr const char* kBlobKey = "nvreload.state";
constexpr uint32_t kBlobVersion = 1;

nvreload::Report g_last;
std::string g_lastPhase = "none";
uint32_t g_lastBlobBytes = 0;
bool g_probesAdded = false;

// Hook writes freeze every other thread (and fix up one stopped inside the patched bytes) so none runs torn code.
void guardBegin(nvhooks::Addr target, uint8_t stolen, nvhooks::Addr tramp, bool installing) {
    if (installing) nvreload::freezeOthers(target, stolen, static_cast<intptr_t>(tramp) - static_cast<intptr_t>(target));
    else nvreload::freezeOthers(tramp, stolen, static_cast<intptr_t>(target) - static_cast<intptr_t>(tramp));
}
void guardEnd() { nvreload::unfreezeOthers(); }

void ensureProbes() {
    if (g_probesAdded) return;
    g_probesAdded = true;
    if (!FNV_MUT(9)) nvhooks::setWriteGuard(&guardBegin, &guardEnd);   // twin 9: the write guard is not installed
    nvreload::global().addLeakProbe(nvreload::LeakProbe{"hooks", [] { return nvhooks::liveHookCount(); }, 0});
    nvreload::global().addLeakProbe(nvreload::LeakProbe{"hooks-chained-by-others", [] { return nvhooks::tamperedCount(); }, 0});
}

void logReport(const NvLoaderApi* api, const char* what, const nvreload::Report& r) {
    std::string t = r.text();
    size_t pos = 0;
    while (pos < t.size()) {
        size_t nl = t.find('\n', pos);
        std::string line = t.substr(pos, nl == std::string::npos ? std::string::npos : nl - pos);
        if (!line.empty()) {
            std::string out = std::string("reload ") + what + ":" + line;
            if (api && api->log) api->log(out.c_str());
        }
        if (nl == std::string::npos) break;
        pos = nl + 1;
    }
}

std::string esc(const std::string& s) {
    std::string o;
    for (unsigned char c : s) {
        if (c == '"' || c == '\\') { o += '\\'; o += static_cast<char>(c); }
        else if (c < 0x20) { char b[8]; std::snprintf(b, sizeof b, "\\u%04x", c); o += b; }
        else o += static_cast<char>(c);
    }
    return o;
}

std::string arr(const std::vector<std::string>& v) {
    std::string o = "[";
    for (size_t i = 0; i < v.size(); ++i) o += (i ? ",\"" : "\"") + esc(v[i]) + "\"";
    return o + "]";
}

bool MState(const std::string&, std::string& out) {
    out = StatusJson();
    return true;
}

}  // namespace

bool BeforeUnload(const NvLoaderApi* api) {
    ensureProbes();
    nvreload::Report rep;
    nvreload::Bytes blob;
    g_lastPhase = "before_unload";
    if (!nvreload::global().saveAll(&blob, &rep)) {
        g_last = rep;
        logReport(api, "REFUSED (core keeps running)", rep);
        return false;
    }
    // Drain: take the hooks off (original bytes back, trampolines kept) so nothing can ENTER a detour, then wait until no other
    // thread is executing inside the core or a trampoline. On a timeout the hooks go back and the reload is refused cleanly.
    {
        unsigned timeoutMs = 2000;
        if (const char* e = std::getenv("NVRELOAD_DRAIN_MS")) timeoutMs = static_cast<unsigned>(std::atoi(e));
        int cannot = nvhooks::suspendAllHooks();
        nvreload::DrainResult d;
        if (!cannot) {
            std::vector<nvreload::CodeRange> ranges;
            ranges.push_back(nvreload::moduleRangeOf(reinterpret_cast<const void*>(&BeforeUnload)));
            std::vector<std::pair<nvhooks::Addr, size_t>> tr;
            nvhooks::collectAllTrampolines(&tr);
            for (auto& t : tr) ranges.push_back({t.first, t.first + t.second});
            d = nvreload::waitDrained(ranges, nvreload::managedThreadIds(), timeoutMs);
        }
        if (cannot || !d.drained) {
            nvhooks::resumeAllHooks();
            rep.failures.push_back(cannot ? std::to_string(cannot) + " hook(s) could not be taken off" : "drain: " + d.why);
            g_last = rep;
            logReport(api, "REFUSED by the drain (hooks put back, core keeps running)", rep);
            return false;
        }
        rep.notes.push_back("drained after " + std::to_string(d.attempts) + " attempt(s), " + std::to_string(d.scanned) + " threads inspected");
    }
    int leaks = nvreload::global().shutdownAll(&rep);
    g_last = rep;
    g_lastBlobBytes = static_cast<uint32_t>(blob.size());
    if (leaks > 0 || !rep.failures.empty()) {
        logReport(api, "UNSAFE after shutdown (core degraded, do not unload)", rep);
        if (api && api->blob_erase) api->blob_erase(kBlobKey);
        return false;
    }
    if (!api || !api->blob_put || !api->blob_put(kBlobKey, blob.data(), static_cast<uint32_t>(blob.size()), kBlobVersion)) {
        if (api && api->log) api->log("reload: loader refused the state blob");
        return false;
    }
    logReport(api, "ok", rep);
    return true;
}

bool AfterLoad(const NvLoaderApi* api) {
    ensureProbes();
    static bool methodRegistered = false;
    if (!methodRegistered) {
        methodRegistered = true;
        nvcoop::RegisterMethod("reload.state", MState, true);
    }
    nvreload::Report rep;
    nvreload::Bytes blob;
    g_lastPhase = "after_load";
    if (api && api->is_reload && api->blob_get) {
        uint32_t len = 0, ver = 0;
        if (api->blob_get(kBlobKey, nullptr, 0, &len, &ver) && ver == kBlobVersion && len > 0) {
            blob.resize(len);
            uint32_t got = 0;
            if (!api->blob_get(kBlobKey, blob.data(), len, &got, &ver) || got != len) blob.clear();
        } else {
            rep.notes.push_back("no usable state blob from the previous core");
        }
        if (api->blob_erase) api->blob_erase(kBlobKey);  // consumed: a second reload must not see a stale one
    }
    nvreload::global().restoreAll(blob, &rep);
    g_last = rep;
    logReport(api, "after_load", rep);
    return true;
}

std::string StatusJson() {
    ensureProbes();
    auto& reg = nvreload::global();
    std::string s = "{\"phase\":\"" + esc(g_lastPhase) + "\",\"participants\":[";
    bool first = true;
    for (auto& n : reg.names()) {
        if (!first) s += ",";
        first = false;
        s += "{\"name\":\"" + esc(n) + "\",\"version\":" + std::to_string(reg.versionOf(n)) + "}";
    }
    s += "],\"liveHooks\":" + std::to_string(nvhooks::liveHookCount()) + ",\"liveThreads\":" + std::to_string(nvreload::liveThreads()) +
         ",\"chainedHooks\":" + std::to_string(nvhooks::tamperedCount()) + ",\"lastBlobBytes\":" + std::to_string(g_lastBlobBytes) +
         ",\"restored\":" + std::to_string(g_last.restored) + ",\"fresh\":" + std::to_string(g_last.freshStarts) +
         ",\"migrated\":" + std::to_string(g_last.migrated) + ",\"dropped\":" + std::to_string(g_last.dropped) +
         ",\"failures\":" + arr(g_last.failures) + ",\"leaks\":" + arr(g_last.leaks) + "}";
    return s;
}

}  // namespace nvcoop::reload
