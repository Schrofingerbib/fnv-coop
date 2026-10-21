// Game-free reload test in a real host process (x86): loads the test core through T3a's CoreHost, then unloads and reloads it
// 100 times and proves, from OUTSIDE the core, that nothing leaked and nothing was lost:
//   state     frame counter, label, background tick count, hook-call count and the UDP socket survive every reload
//   hooks     the patched host function is byte-identical to the original at the start of every new core, and active after init
//   threads   the process thread count returns to the baseline after every reload (polled, not trusted from the core)
//   handles   the process handle count does not creep; at most one cached core DLL file; only one core module mapped
//   refusal   a hook chained over by "another mod" vetoes the reload and the old core keeps running
//   config    nvcoop.ini beside the core file is rewritten before every reload; each new core reads the new value beside
//             its own module (the loader's cache copy), as the real modules do
// Usage: test_reload_host <core dll> <work dir>.   Exit 0 = every check passed. Run with FNVCOOP_MUT=1..9 (runtime twins of the test core) it must fail,
// and with FNVCOOP_MUT=25 / 26 (runtime twins of the loader: noconfig / configonce, loader_host.cpp) it must fail too.
#include <winsock2.h>
#include <windows.h>
#include <psapi.h>
#include <tlhelp32.h>

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <atomic>
#include <map>
#include <thread>
#include <vector>
#include <string>

#include "common/nvcoop_api.h"
#include "common/registry.h"
#include "loader/loader_host.h"

using namespace nvcoop::loader;

static int g_checks = 0, g_failed = 0;
#define CHECK(cond, msg)                                                                \
    do {                                                                                \
        ++g_checks;                                                                     \
        if (!(cond)) {                                                                  \
            ++g_failed;                                                                 \
            std::printf("FAIL line %d: %s\n", __LINE__, std::string(msg).c_str());      \
        }                                                                               \
    } while (0)

typedef int(__cdecl* InfoFn)(char*, int);
typedef int(__cdecl* TargetFn)(int);

static std::string g_run, g_core, g_cache;
static uint8_t* g_target = nullptr;

static int ThreadCount() {
    int n = 0;
    HANDLE s = CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0);
    THREADENTRY32 te{sizeof te};
    if (Thread32First(s, &te))
        do {
            if (te.th32OwnerProcessID == GetCurrentProcessId()) ++n;
        } while (Thread32Next(s, &te));
    CloseHandle(s);
    return n;
}
static int HandleCount() {
    DWORD n = 0;
    GetProcessHandleCount(GetCurrentProcess(), &n);
    return static_cast<int>(n);
}
static int CacheFiles() {
    int n = 0;
    WIN32_FIND_DATAA fd;
    HANDLE h = FindFirstFileA((g_cache + "nvcoop_mod.*.dll").c_str(), &fd);
    if (h == INVALID_HANDLE_VALUE) return 0;
    do {
        ++n;
    } while (FindNextFileA(h, &fd));
    FindClose(h);
    return n;
}
static int CoreModulesMapped() {
    HMODULE mods[1024];
    DWORD need = 0;
    int n = 0;
    if (!EnumProcessModules(GetCurrentProcess(), mods, sizeof mods, &need)) return -1;
    for (DWORD i = 0; i < need / sizeof(HMODULE); ++i) {
        char name[MAX_PATH] = {};
        GetModuleFileNameA(mods[i], name, MAX_PATH);
        if (std::strstr(name, "nvcoop_mod.")) ++n;
    }
    return n;
}
static std::string Slurp(const std::string& p) {
    std::string s;
    if (FILE* f = std::fopen(p.c_str(), "rb")) {
        char b[4096];
        size_t n;
        while ((n = std::fread(b, 1, sizeof b, f)) > 0) s.append(b, n);
        std::fclose(f);
    }
    return s;
}

// Which core module is mapped right now (the cache copy the loader made).
static FARPROC FindExport(const char* name) {
    HMODULE mods[1024];
    DWORD need = 0;
    EnumProcessModules(GetCurrentProcess(), mods, sizeof mods, &need);
    for (DWORD i = 0; i < need / sizeof(HMODULE); ++i) {
        char mn[MAX_PATH] = {};
        GetModuleFileNameA(mods[i], mn, MAX_PATH);
        if (std::strstr(mn, "nvcoop_mod.")) return GetProcAddress(mods[i], name);
    }
    return nullptr;
}
static InfoFn FindInfo() {
    HMODULE mods[1024];
    DWORD need = 0;
    EnumProcessModules(GetCurrentProcess(), mods, sizeof mods, &need);
    for (DWORD i = 0; i < need / sizeof(HMODULE); ++i) {
        char name[MAX_PATH] = {};
        GetModuleFileNameA(mods[i], name, MAX_PATH);
        if (std::strstr(name, "nvcoop_mod.")) return reinterpret_cast<InfoFn>(GetProcAddress(mods[i], "NvCoreTest_Info"));
    }
    return nullptr;
}
static std::map<std::string, std::string> Info() {
    std::map<std::string, std::string> m;
    InfoFn f = FindInfo();
    if (!f) return m;
    char buf[512] = {};
    f(buf, sizeof buf);
    std::string s = buf;
    size_t p = 0;
    while (p < s.size()) {
        size_t semi = s.find(';', p);
        std::string kv = s.substr(p, semi == std::string::npos ? std::string::npos : semi - p);
        size_t eq = kv.find('=');
        if (eq != std::string::npos) m[kv.substr(0, eq)] = kv.substr(eq + 1);
        if (semi == std::string::npos) break;
        p = semi + 1;
    }
    return m;
}
static unsigned long long U(const std::map<std::string, std::string>& m, const char* k) {
    auto it = m.find(k);
    return it == m.end() ? ~0ull : std::strtoull(it->second.c_str(), nullptr, 10);
}

// nvcoop.ini next to the core file (= next to the loader dll in the game). The value is unique per process and per
// write, so a cache copy left by an earlier run or an earlier load can never match.
static std::string g_tag;
static std::string IniValue(int cycle) { return "c" + std::to_string(cycle) + "x" + g_tag; }
static void WriteIni(const std::string& value) {
    const std::string p = g_run + "nvcoop.ini";
    SetFileAttributesA(p.c_str(), FILE_ATTRIBUTE_NORMAL);
    if (FILE* f = std::fopen(p.c_str(), "wb")) {
        const std::string text = "[testcore]\r\nvalue=" + value + "\r\n";
        std::fwrite(text.data(), 1, text.size(), f);
        std::fclose(f);
    }
}

// The host function the core hooks: int __cdecl f(int x) { return x + 1; }
static uint8_t* g_target2 = nullptr;
static void MakeTarget() {
    g_target = static_cast<uint8_t*>(VirtualAlloc(nullptr, 4096, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE));
    const uint8_t code[16] = {0x55, 0x8B, 0xEC, 0x8B, 0x45, 0x08, 0x40, 0x5D, 0xC3, 0xCC, 0xCC, 0xCC, 0xCC, 0xCC, 0xCC, 0xCC};
    std::memcpy(g_target, code, sizeof code);
    g_target2 = g_target + 0x100;
    std::memcpy(g_target2, code, sizeof code);
    char buf[32];
    std::snprintf(buf, sizeof buf, "%08lX", static_cast<unsigned long>(reinterpret_cast<uintptr_t>(g_target)));
    SetEnvironmentVariableA("NVTEST_TARGET", buf);
    std::snprintf(buf, sizeof buf, "%08lX", static_cast<unsigned long>(reinterpret_cast<uintptr_t>(g_target2)));
    SetEnvironmentVariableA("NVTEST_TARGET2", buf);
}

// Worker threads hammer the second hooked function for the whole run. Its detour sleeps, so at any moment some worker is
// blocked INSIDE the core. A reload that does not drain them unmaps code under their feet (crash) and the results go wrong.
static std::atomic<bool> g_workersRun{false};
static std::atomic<unsigned long long> g_workerCalls{0}, g_workerBad{0}, g_workerHooked{0};
static std::vector<std::thread> g_workers;
static void StartWorkers(int n) {
    g_workersRun = true;
    for (int i = 0; i < n; ++i)
        g_workers.emplace_back([i] {
            TargetFn g = reinterpret_cast<TargetFn>(g_target2);
            int x = 10 * (i + 1);
            while (g_workersRun.load()) {
                int r = g(x);
                ++g_workerCalls;
                if (r == x + 1) {} else if (r == x + 101) ++g_workerHooked; else ++g_workerBad;
                Sleep(0);
            }
        });
}
static void StopWorkers() {
    g_workersRun = false;
    for (auto& w : g_workers) w.join();
    g_workers.clear();
}

int main(int argc, char** argv) {
    if (argc < 3) {
        std::printf("usage: test_reload_host <core dll> <work dir>\n");
        return 2;
    }
    std::setvbuf(stdout, nullptr, _IONBF, 0);
    SetUnhandledExceptionFilter([](EXCEPTION_POINTERS* ep) -> LONG {
        std::printf("CRASH code=%08lX at %p (thread %lu), access address %p\n", ep->ExceptionRecord->ExceptionCode, ep->ExceptionRecord->ExceptionAddress,
                    GetCurrentThreadId(), ep->ExceptionRecord->NumberParameters > 1 ? (void*)ep->ExceptionRecord->ExceptionInformation[1] : nullptr);
        ExitProcess(3);
        return EXCEPTION_EXECUTE_HANDLER;
    });
    std::string coreSrc = argv[1];
    std::string dir = argv[2];
    if (!dir.empty() && dir.back() != '\\' && dir.back() != '/') dir += '\\';
    g_run = dir + "run\\";
    g_core = g_run + "nvcoop_mod.dll";
    g_cache = g_run + "nvcoop_cache\\";
    CreateDirectoryA(dir.c_str(), nullptr);
    CreateDirectoryA(g_run.c_str(), nullptr);
    CreateDirectoryA(g_cache.c_str(), nullptr);
    nvcoop::registry::SetLogFile(g_run + "reload_host.log");
    std::remove((g_run + "reload_host.log").c_str());
    CHECK(CopyFileA(coreSrc.c_str(), g_core.c_str(), FALSE) != 0, "copy test core into place");
    g_tag = std::to_string(GetCurrentProcessId());
    SetFileAttributesA((g_cache + "nvcoop.ini").c_str(), FILE_ATTRIBUTE_NORMAL);
    DeleteFileA((g_cache + "nvcoop.ini").c_str());   // nothing left in the cache by an earlier run
    WriteIni(IniValue(0));

    WSADATA wsa;
    WSAStartup(MAKEWORD(2, 2), &wsa);
    SetEnvironmentVariableA("NVRELOAD_DRAIN_MS", "400");  // read by the core's glue when it loads
    MakeTarget();
    TargetFn f = reinterpret_cast<TargetFn>(g_target);
    CHECK(f(1) == 2, "target works before any core");
    uint8_t pristine[16];
    std::memcpy(pristine, g_target, 16);

    const int kCycles = 100;
    {
        HostEnv env;
        env.corePath = g_core;
        env.cacheDir = g_cache;
        CoreHost host(env);
        std::string err;
        CHECK(host.Load(err), "first load: " + err);
        auto info = Info();
        CHECK(info["cfg"] == IniValue(0), "first core read nvcoop.ini beside its own module: cfg=" + info["cfg"]);
        CHECK(U(info, "pristine") == 1, "first core saw the original function bytes");
        CHECK(f(1) == 102, "first core hooked the host function");
        CHECK(std::memcmp(g_target, pristine, 6) != 0, "hook bytes are in place");
        unsigned long long port0 = U(info, "port");
        CHECK(port0 != 0 && port0 != ~0ull, "first core opened a socket");

        StartWorkers(4);
        Sleep(30);  // let the thread count settle after the first load
        const int threadBase = ThreadCount();  // includes the workers
        const int handleBase = HandleCount();
        unsigned long long frames = 0, lastBg = U(Info(), "bg");
        unsigned long long hookCalls = U(Info(), "hookcalls");
        int worstThreads = 0, worstHandles = 0;

        for (int i = 1; i <= kCycles; ++i) {
            for (int k = 0; k < 3; ++k) {
                host.OnFrame();
                ++frames;
            }
            Sleep(3);
            CHECK(f(2) == 103, "hook active before reload");
            ++hookCalls;
            WriteIni(IniValue(i));   // the ini is edited while the old core runs; only the next core may see it
            ReloadResult r = host.Reload("test");
            if (!r.ok) {
                CHECK(false, "reload " + std::to_string(i) + " failed: " + r.error);
                break;
            }
            info = Info();
            std::string tag = " (cycle " + std::to_string(i) + ")";
            CHECK(host.generation() == static_cast<uint32_t>(i + 1), "generation advances" + tag);
            CHECK(info["cfg"] == IniValue(i), "the new core read the ini edited before this reload: cfg=" + info["cfg"] + tag);
            CHECK(U(info, "counter") == frames, "frame counter survived: " + std::to_string(U(info, "counter")) + " != " + std::to_string(frames) + tag);
            CHECK(info["label"] == "gen1", "label saved by the first core is restored by every later one" + tag);
            CHECK(U(info, "gen") == static_cast<unsigned long long>(i + 1), "the core running is the new generation" + tag);
            CHECK(U(info, "port") == port0, "socket handed over, same port" + tag);
            CHECK(U(info, "adopted") == 1, "new core adopted the old socket" + tag);
            CHECK(U(info, "bg") >= lastBg, "background tick count carried over" + tag);
            lastBg = U(info, "bg");
            CHECK(U(info, "hookcalls") == hookCalls, "hook-call count carried over" + tag);
            CHECK(U(info, "guardcalls") >= 2, "hook writes went through the freeze guard" + tag);
            CHECK(U(info, "pristine") == 1, "old core restored the original bytes before the new core started" + tag);
            CHECK(U(info, "livehooks") == 2, "exactly the two expected hooks live in the new core" + tag);
            CHECK(f(5) == 106, "hook active in the new core" + tag);
            ++hookCalls;
            int th = 0;
            for (int w = 0; w < 100; ++w) {  // poll up to 500 ms: joined threads disappear from the snapshot a moment later
                th = ThreadCount();
                if (th <= threadBase) break;
                Sleep(5);
            }
            if (th - threadBase > worstThreads) worstThreads = th - threadBase;
            CHECK(th <= threadBase, "thread count back to baseline: " + std::to_string(th) + " vs " + std::to_string(threadBase) + tag);
            int hd = HandleCount() - handleBase;
            if (hd > worstHandles) worstHandles = hd;
            CHECK(CoreModulesMapped() == 1, "exactly one core module mapped" + tag);
            CHECK(CacheFiles() <= 1, "at most one cached core DLL on disk" + tag);
            if (g_failed > 5) break;  // do not flood
        }
        CHECK(worstHandles <= 8, "handle count did not creep (worst +" + std::to_string(worstHandles) + ")");
        std::printf("cycles done: worst extra threads %d, worst extra handles %d\n", worstThreads, worstHandles);

        // ---- a thread stuck inside a detour: the drain must time out, refuse, and put the hooks back ----
        {
            auto before = Info();
            auto hold = reinterpret_cast<void(__cdecl*)(int)>(FindExport("NvCoreTest_Hold"));
            CHECK(hold != nullptr, "test core exports NvCoreTest_Hold");
            if (hold) hold(1);
            Sleep(20);  // a worker is now blocked in the detour (its instruction pointer is in the kernel)
            ReloadResult r = host.Reload("held");
            CHECK(!r.ok, "reload refused while a thread is inside a detour");
            {
                // The failure must be explained in the log: what timed out, how long, which thread, and what it was inside.
                std::string lg = Slurp(g_run + "reload_host.log");
                CHECK(lg.find("REFUSED by the drain") != std::string::npos, "the log says the drain refused the reload");
                CHECK(lg.find("drain timed out after") != std::string::npos && lg.find("limit 400 ms") != std::string::npos, "the log names the drain timeout and its limit");
                CHECK(lg.find("is still inside the code that would be unloaded") != std::string::npos, "the log says a thread is still inside");
                CHECK(lg.find("return address 0x") != std::string::npos || lg.find("instruction pointer 0x") != std::string::npos, "the log says where the thread was found");
            }
            CHECK(host.generation() == static_cast<uint32_t>(kCycles + 1), "same generation keeps running after the drain refusal");
            if (hold) hold(0);
            Sleep(20);
            CHECK(reinterpret_cast<TargetFn>(g_target)(2) == 103, "hooks were put back after the refused drain");
            host.OnFrame();
            CHECK(U(Info(), "counter") == U(before, "counter") + 1, "core still fully running after the drain refusal");
            ReloadResult r2 = host.Reload("released");
            CHECK(r2.ok, "reload works once the thread has left: " + r2.error);
        }
        CHECK(g_workerBad.load() == 0, "no worker ever saw a wrong result: " + std::to_string(g_workerBad.load()));
        CHECK(g_workerHooked.load() > 0 && g_workerCalls.load() > 1000, "workers really ran through the hook: calls " + std::to_string(g_workerCalls.load()));
        std::printf("workers: %llu calls, %llu through the hook\n", g_workerCalls.load(), g_workerHooked.load());
        StopWorkers();

        // ---- another mod chains over our hook: the reload must be refused and the old core must keep working ----
        {
            auto before = Info();
            uint8_t saved = g_target[1];
            g_target[1] ^= 0x55;  // "somebody else" patched the first bytes after our jump
            ReloadResult r = host.Reload("tampered");
            CHECK(!r.ok, "reload refused while a hook is chained over");
            CHECK(host.generation() == static_cast<uint32_t>(kCycles + 2), "same generation keeps running after the refusal");
            host.OnFrame();
            auto after = Info();
            CHECK(U(after, "counter") == U(before, "counter") + 1, "refused reload left the old core fully running");
            CHECK(U(after, "livehooks") == 2, "refused reload did not uninstall anything");
            g_target[1] = saved;
            ReloadResult r2 = host.Reload("after tamper cleared");
            CHECK(r2.ok, "reload works again once the chain is gone: " + r2.error);
            CHECK(f(1) == 102, "hook active after the recovered reload");
        }
    }
    // The CoreHost destructor does not unload the core (the OS does). Nothing more to check.
    std::printf("%d checks, %d failed\n", g_checks, g_failed);
    std::string log = Slurp(g_run + "reload_host.log");
    if (g_failed) std::printf("---- log tail ----\n%s\n", log.size() > 3000 ? log.substr(log.size() - 3000).c_str() : log.c_str());
    return g_failed ? 1 : 0;
}
