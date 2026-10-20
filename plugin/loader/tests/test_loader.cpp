// Game-free tests for the hot-reload loader (CoreHost) using stub core DLLs. No NVSE, no game.
// Usage: nvloader_test <dir with nvstub_*.dll>   (a scratch dir "run" is created inside it)
// Exit 0 = every check passed. The same file is built against mutant loaders, which must make it fail.
#include <atomic>
#include <chrono>
#include <cstdio>
#include <string>
#include <thread>

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#include <tlhelp32.h>

#include "common/nvcoop_api.h"
#include "common/registry.h"
#include "loader_host.h"

using namespace nvcoop::loader;

static int g_checks = 0, g_failed = 0;
#define CHECK(cond, msg)                                                       \
    do {                                                                       \
        ++g_checks;                                                            \
        if (!(cond)) { ++g_failed; std::printf("FAIL line %d: %s\n", __LINE__, msg); } \
    } while (0)

static std::string g_dir, g_run, g_core, g_cache;

static bool Call(const char* name, const char* params, std::string& out) {
    nvcoop::registry::MethodEntry me;
    if (!nvcoop::registry::FindMethod(name, me)) { out = "no such method"; return false; }
    return me.fn(params, out);
}
static bool Has(const char* name) {
    nvcoop::registry::MethodEntry me;
    return nvcoop::registry::FindMethod(name, me);
}
static bool Contains(const std::string& s, const char* sub) { return s.find(sub) != std::string::npos; }

static bool Put(const char* stub) {   // simulate the build writing a new core
    std::string src = g_dir + stub + ".dll";
    for (int i = 0; i < 20; ++i) {
        if (CopyFileA(src.c_str(), g_core.c_str(), FALSE)) {
            // CopyFile keeps the source's timestamp; a real build writes "now"
            HANDLE h = CreateFileA(g_core.c_str(), FILE_WRITE_ATTRIBUTES, FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr, OPEN_EXISTING, 0, nullptr);
            if (h != INVALID_HANDLE_VALUE) {
                FILETIME ft;
                GetSystemTimeAsFileTime(&ft);
                SetFileTime(h, nullptr, nullptr, &ft);
                CloseHandle(h);
            }
            return true;
        }
        Sleep(50);
    }
    return false;
}
static int CacheFiles() {
    int n = 0;
    WIN32_FIND_DATAA fd;
    HANDLE h = FindFirstFileA((g_cache + "nvcoop_mod.*.dll").c_str(), &fd);
    if (h == INVALID_HANDLE_VALUE) return 0;
    do { ++n; } while (FindNextFileA(h, &fd));
    FindClose(h);
    return n;
}
static int ThreadCount() {
    int n = 0;
    HANDLE s = CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0);
    THREADENTRY32 te{sizeof te};
    if (Thread32First(s, &te))
        do { if (te.th32OwnerProcessID == GetCurrentProcessId()) ++n; } while (Thread32Next(s, &te));
    CloseHandle(s);
    return n;
}
static int HandleCount() {
    DWORD n = 0;
    GetProcessHandleCount(GetCurrentProcess(), &n);
    return static_cast<int>(n);
}
static std::string Info() {
    std::string o;
    Call("stub.info", "{}", o);
    return o;
}

int main(int argc, char** argv) {
    if (argc > 2) {   // smoke mode: nvloader_test <dir> <real core dll>: only maps the DLL and checks its export
        HMODULE m = LoadLibraryExA(argv[2], nullptr, LOAD_WITH_ALTERED_SEARCH_PATH);
        FARPROC f = m ? GetProcAddress(m, "NvCore_Init") : nullptr;
        std::printf("smoke: LoadLibrary %s, NvCore_Init %s\n", m ? "ok" : "FAILED", f ? "found" : "MISSING");
        return (m && f) ? 0 : 1;
    }
    g_dir = argc > 1 ? argv[1] : ".";
    if (!g_dir.empty() && g_dir.back() != '\\' && g_dir.back() != '/') g_dir += '\\';
    g_run = g_dir + "run\\";
    g_core = g_run + "nvcoop_mod.dll";
    g_cache = g_run + "nvcoop_cache\\";
    CreateDirectoryA(g_run.c_str(), nullptr);
    CreateDirectoryA(g_cache.c_str(), nullptr);
    nvcoop::registry::SetLogFile(g_run + "loader_test.log");
    std::remove((g_run + "loader_test.log").c_str());
    SetEnvironmentVariableA("NVSTUB_VETO", nullptr);

    // stale cache file from a "dead" process must be cleaned at start
    { HANDLE f = CreateFileA((g_cache + "nvcoop_mod.1.1.dll").c_str(), GENERIC_WRITE, 0, nullptr, CREATE_ALWAYS, 0, nullptr); if (f != INVALID_HANDLE_VALUE) CloseHandle(f); }
    CHECK(Put("nvstub_v1"), "put v1");

    {
        HostEnv env;
        env.corePath = g_core;
        env.cacheDir = g_cache;
        CoreHost host(env);
        CHECK(CacheFiles() == 0, "stale cache files cleaned at start");

        // ---- first load ----
        std::string err;
        CHECK(host.Load(err), "first load");
        CHECK(host.generation() == 1, "generation 1");
        CHECK(Contains(Info(), "v1:"), "v1 running");
        for (int i = 0; i < 3; ++i) host.OnFrame();
        host.OnMessage(5, nullptr, 0);
        CHECK(Contains(Info(), "frames=3:msgs=1"), "frames and messages reach the core");
        CHECK(Has("stub.only_v1"), "v1 only-method registered");
        auto sp = nvcoop::registry::GetStateProvider();
        CHECK(sp && Contains(sp("abc"), "\"stub\":1"), "state provider routes to core");
        CHECK(Has("dev.reload") && Has("dev.status"), "dev methods registered");
        CHECK(CacheFiles() == 1, "exactly one cache copy while loaded");

        // ---- the build can overwrite the original while the game runs (needs the private copy) ----
        CHECK(Put("nvstub_v2"), "overwrite core file while loaded");
        ReloadResult r = host.Reload("test");
        CHECK(r.ok, ("reload to v2: " + r.error).c_str());
        CHECK(host.generation() == 2, "generation 2");
        CHECK(Contains(Info(), "v2:frames=3"), "v2 running, frames blob preserved");
        CHECK(!Has("stub.only_v1"), "old core's methods are gone");
        CHECK(Has("stub.only_v2"), "new core's methods are registered");
        CHECK(sp = nvcoop::registry::GetStateProvider(), "state provider re-registered");
        CHECK(CacheFiles() == 1, "old cache copy removed after reload");
        {
            auto ev = nvcoop::registry::ReadEvents(0, 0);
            bool seen = false;
            for (auto& e : ev.events) if (e.type == "dev.reloaded") seen = true;
            CHECK(seen, "dev.reloaded event pushed");
        }

        // ---- veto keeps the old core fully working ----
        SetEnvironmentVariableA("NVSTUB_VETO", "1");
        r = host.Reload("veto");
        SetEnvironmentVariableA("NVSTUB_VETO", nullptr);
        CHECK(!r.ok && !r.coreDead, "veto refuses the reload");
        CHECK(host.generation() == 2 && host.loaded(), "still generation 2 after veto");
        CHECK(Contains(Info(), "v2:") && Has("stub.only_v2"), "old core intact after veto");

        // ---- broken builds roll back to the running core ----
        host.OnFrame();   // frames = 4
        int threadsBefore = ThreadCount();
        const char* bad[] = {"nvstub_failinit", "nvstub_noexport", "nvstub_badabi", "nvstub_missing"};
        for (const char* b : bad) {
            if (std::string(b) == "nvstub_missing") DeleteFileA(g_core.c_str()); else CHECK(Put(b), "put bad build");
            r = host.Reload(b);
            CHECK(!r.ok && r.rolledBack && !r.coreDead, (std::string("rollback after ") + b + ": " + r.error).c_str());
            CHECK(host.generation() == 2 && host.loaded(), "generation unchanged after failed reload");
            CHECK(Contains(Info(), "v2:frames=4"), "previous core running again with its state");
            CHECK(Has("stub.only_v2"), "previous core's methods back");
            CHECK(ThreadCount() == threadsBefore, "no leaked thread after a failed reload");
        }
        CHECK(CacheFiles() == 1, "no cache files leaked by failures");

        // ---- recover to a good build ----
        CHECK(Put("nvstub_v1"), "put v1 again");
        r = host.Reload("recover");
        CHECK(r.ok && host.generation() == 3 && Contains(Info(), "v1:frames=4"), "reload back to v1 keeps state");

        // ---- 100 reloads: state kept, no leaked threads, handles or files ----
        {
            int th = ThreadCount(), hd = HandleCount();
            bool allOk = true;
            for (int i = 0; i < 100; ++i) {
                host.OnFrame();
                CHECK(Put((i & 1) ? "nvstub_v1" : "nvstub_v2"), "put alternating build");
                r = host.Reload("loop");
                if (!r.ok) { allOk = false; std::printf("loop %d: %s\n", i, r.error.c_str()); break; }
                if (!Contains(Info(), (std::string((i & 1) ? "v1" : "v2") + ":frames=" + std::to_string(5 + i)).c_str())) {
                    allOk = false;
                    std::printf("loop %d: unexpected info %s\n", i, Info().c_str());
                    break;
                }
            }
            CHECK(allOk, "100 reloads succeeded with frame counter preserved");
            CHECK(host.generation() == 103, "generation 103 after 100 reloads");
            CHECK(ThreadCount() == th, "thread count unchanged after 100 reloads");
            CHECK(HandleCount() <= hd + 4, "no handle leak across 100 reloads");
            CHECK(CacheFiles() == 1, "one cache file after 100 reloads");
        }

        // ---- in-flight bridge call: reload waits for it, a stale closure cannot run into freed code ----
        {
            nvcoop::registry::MethodEntry slow, stale;
            CHECK(nvcoop::registry::FindMethod("stub.slow", slow), "slow method registered");
            nvcoop::registry::FindMethod("stub.info", stale);
            std::string slowOut;
            bool slowOk = false;
            std::thread t([&] { slowOk = slow.fn("{}", slowOut); });
            Sleep(60);
            CHECK(Put("nvstub_v1"), "put v1 (inflight)");
            r = host.Reload("inflight");
            t.join();
            CHECK(r.ok, ("reload with a call in flight: " + r.error).c_str());
            CHECK(slowOk && slowOut == "slow-done", "in-flight call finished before the unload");
            std::string o;
            CHECK(!stale.fn("{}", o) && Contains(o, "reloading"), "stale closure refuses instead of calling freed code");
        }

        // ---- file watcher (opt-in) and request_reload ----
        {
            uint32_t g0 = host.generation();
            CHECK(host.StartWatcher(40), "watcher starts");
            Sleep(150);   // let the watcher take its baseline stamp
            CHECK(Put("nvstub_v2"), "put v2 (watcher)");
            for (int i = 0; i < 100 && host.generation() == g0; ++i) { Sleep(40); host.OnFrame(); }
            CHECK(host.generation() == g0 + 1 && Contains(Info(), "v2:"), "watcher triggered a reload at a frame boundary");
            Sleep(250);
            host.OnFrame();
            CHECK(host.generation() == g0 + 1, "watcher does not reload again without a change");
            host.StopWatcher();
            uint32_t g1 = host.generation();
            host.RequestReload("manual");
            CHECK(host.generation() == g1, "request alone does not reload");
            host.OnFrame();
            CHECK(host.generation() == g1 + 1, "request is served at the next frame");
        }

        // ---- bridge methods ----
        {
            std::string o;
            CHECK(Call("dev.reload", "{\"reason\":\"bridge test\"}", o) && Contains(o, "generation"), "dev.reload via bridge");
            CHECK(Call("dev.status", "{}", o) && Contains(o, "\"loaded\":true") && Contains(o, "bridge test"), "dev.status");
            SetEnvironmentVariableA("NVSTUB_VETO", "1");
            CHECK(!Call("dev.reload", "{}", o) && Contains(o, "vetoed"), "dev.reload reports a veto");
            SetEnvironmentVariableA("NVSTUB_VETO", nullptr);
            CHECK(Call("stub.fail", "{}", o) == false && Contains(o, "deliberate"), "core method failure passes through");
        }

        // ---- orderly unload (what a process-exit path would do) ----
        {
            int th = ThreadCount();
            std::string e;
            CHECK(host.Unload(e) && !host.loaded(), "unload");
            CHECK(!Has("stub.info") && !Has("stub.only_v1") && !Has("stub.only_v2"), "unload removes the core's methods");
            CHECK(ThreadCount() < th, "unload stops the core's thread");
            CHECK(CacheFiles() == 0, "unload deletes the cache copy");
        }
    }

    // ---- core not built yet at game start, then built: dev.reload recovers ----
    {
        DeleteFileA(g_core.c_str());
        HostEnv env;
        env.corePath = g_core;
        env.cacheDir = g_cache;
        CoreHost host(env);
        std::string err;
        CHECK(!host.Load(err), "load fails while the core file is missing");
        CHECK(Has("dev.reload"), "dev.reload exists even with no core");
        CHECK(Put("nvstub_v1"), "put v1 (late)");
        std::string o;
        CHECK(Call("dev.reload", "{}", o) && host.loaded() && host.generation() == 1, "dev.reload loads a core that appeared later");
        std::string e;
        CHECK(host.Unload(e), "unload (late-loaded core)");
    }

    std::printf("loader tests: %d checks, %d failed\n", g_checks, g_failed);
    return g_failed ? 1 : 0;
}
