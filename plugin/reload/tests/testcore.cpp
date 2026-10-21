// Test core DLL for the reload host test (x86 only). It is a miniature of the real core: it implements NvCore_Init
// (T3a's ABI), registers reload participants and calls nvcoop::reload::BeforeUnload / AfterLoad exactly like
// plugin/loader/core_entry.cpp. What it carries across reloads:
//   counter   a frame counter + a label (plain state blob)
//   hooks     a REAL nvhooks::HookRegistry patch on a function inside the host process (uninstalled and read back on shutdown)
//   thread    a ManagedThread ticker whose count must keep growing across reloads
//   socket    a UDP socket handed over as a raw SOCKET (not closed by shutdown, adopted by the next core)
//   config    not carried, read fresh by every core at init: [testcore] value= from nvcoop.ini beside this module's own file, the
//             same lookup as IniPath() in the real modules (the loader must have copied the ini into its cache folder)
// Built once; the host test runs it clean and once per FNVCOOP_MUT=1..9 (runtime twins); the host test must fail against every twin.
#ifndef _M_IX86
#error "testcore must be built for x86 (the hook patcher writes rel32 jumps)"
#endif
#include "reload_drain.h"
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>
#include <tlhelp32.h>

#include <atomic>
#include <cstdio>
#include <cstring>
#include <memory>
#include <string>
#include <thread>

#include "common/nvcoop_api.h"
#include "common/reload.h"
#include "hooks/hook_patch.h"
#include "loader/core_thunks.h"
#include "loader/loader_abi.h"
#include "reload_state.h"

#pragma comment(lib, "ws2_32.lib")

#include "fnvcoop_mut.h"  // FNV_MUT(n): runtime mutation twins (env FNVCOOP_MUT=n); constant false in production builds

namespace {

const NvLoaderApi* g_api = nullptr;

// ---- real memory (same as the three hook helpers) ----
struct Win32Memory : nvhooks::MemoryOps {
    bool read(nvhooks::Addr a, void* out, size_t n) override {
        __try {
            std::memcpy(out, reinterpret_cast<const void*>(a), n);
            return true;
        } __except (EXCEPTION_EXECUTE_HANDLER) {
            return false;
        }
    }
    bool write(nvhooks::Addr a, const void* in, size_t n) override {
        DWORD old = 0;
        void* p = reinterpret_cast<void*>(a);
        if (!VirtualProtect(p, n, PAGE_EXECUTE_READWRITE, &old)) return false;
        std::memcpy(p, in, n);
        FlushInstructionCache(GetCurrentProcess(), p, n);
        DWORD tmp;
        VirtualProtect(p, n, old, &tmp);
        return true;
    }
    nvhooks::Addr allocExec(size_t n) override {
        return reinterpret_cast<nvhooks::Addr>(VirtualAlloc(nullptr, n, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE));
    }
    void freeExec(nvhooks::Addr a, size_t) override { VirtualFree(reinterpret_cast<void*>(a), 0, MEM_RELEASE); }
};
Win32Memory g_mem;
std::unique_ptr<nvhooks::HookRegistry> g_reg;

// The host places this function: int __cdecl f(int x) { return x + 1; }  = 55 8B EC 8B 45 08 40 5D C3
const nvhooks::HookSpec kSpec{"host::f", 0, {0x55, 0x8B, 0xEC, 0x8B, 0x45, 0x08, 0x40, 0x5D, 0xC3, 0xCC, 0xCC, 0xCC, 0xCC, 0xCC, 0xCC, 0xCC}, 9, 6};
const uint8_t kPristine[9] = {0x55, 0x8B, 0xEC, 0x8B, 0x45, 0x08, 0x40, 0x5D, 0xC3};

using Fn = int(__cdecl*)(int);
Fn g_orig = nullptr;
std::atomic<uint32_t> g_hookCalls{0};
int __cdecl Detour(int x) {
    ++g_hookCalls;
    return g_orig(x) + 100;
}

// Second hooked function, hammered by worker threads of the host during the whole run. Its detour sleeps (so threads are often
// blocked INSIDE the core: the instruction pointer is in the kernel, only the stack shows it) and can be held on demand.
Fn g_orig2 = nullptr;
std::atomic<int> g_hold{0};
int __cdecl Detour2(int x) {
    int r = g_orig2(x) + 100;
    do {
        Sleep(1);
    } while (g_hold.load());
    return r;
}

nvhooks::HookSpec spec2() {
    nvhooks::HookSpec s = kSpec;
    s.name = "host::g";
    char buf[32] = {};
    GetEnvironmentVariableA("NVTEST_TARGET2", buf, sizeof buf);
    s.target = static_cast<nvhooks::Addr>(std::strtoul(buf, nullptr, 16));
    return s;
}

nvhooks::HookSpec spec() {
    nvhooks::HookSpec s = kSpec;
    char buf[32] = {};
    GetEnvironmentVariableA("NVTEST_TARGET", buf, sizeof buf);
    s.target = static_cast<nvhooks::Addr>(std::strtoul(buf, nullptr, 16));
    return s;
}

// ---- state ----
uint64_t g_counter = 0;
std::string g_label;
uint64_t g_bgBase = 0;               // background ticks carried over from earlier cores
std::atomic<uint64_t> g_bg{0};       // ticks of this core's thread
int g_pristineAtInit = -1;           // 1 = the target's bytes were the original ones when this core started (before it hooked)
int g_threadsAtInit = 0;
uint32_t g_hookCallsBase = 0;
SOCKET g_sock = INVALID_SOCKET;
uint16_t g_port = 0;
bool g_adopted = false;
nvreload::ManagedThread g_ticker;
std::thread g_raw;                   // mutant 2 only
std::atomic<bool> g_rawStop{false};
std::string g_cfg = "unread";        // [testcore] value= read at init from nvcoop.ini beside this module

void readConfig() {
    HMODULE self = nullptr;
    GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                       reinterpret_cast<LPCSTR>(&readConfig), &self);
    char path[MAX_PATH] = {};
    GetModuleFileNameA(self, path, MAX_PATH);
    std::string ini = path;
    ini = ini.substr(0, ini.find_last_of("\\/") + 1) + "nvcoop.ini";
    char v[64] = {};
    GetPrivateProfileStringA("testcore", "value", "missing", v, sizeof v, ini.c_str());
    g_cfg = v;
}

int processThreads() {
    int n = 0;
    HANDLE s = CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0);
    if (s == INVALID_HANDLE_VALUE) return -1;
    THREADENTRY32 te{sizeof te};
    DWORD pid = GetCurrentProcessId();
    if (Thread32First(s, &te))
        do {
            if (te.th32OwnerProcessID == pid) ++n;
        } while (Thread32Next(s, &te));
    CloseHandle(s);
    return n;
}

void ensureHooks() {
    nvhooks::HookSpec s = spec();
    if (!s.target) return;
    if (g_pristineAtInit < 0) {
        uint8_t cur[9] = {};
        g_pristineAtInit = (g_mem.read(s.target, cur, 9) && std::memcmp(cur, kPristine, 9) == 0) ? 1 : 0;
    }
    if (!g_reg) g_reg = std::make_unique<nvhooks::HookRegistry>(g_mem);
    nvhooks::Addr tramp = 0;
    nvhooks::PatchStatus st = g_reg->acquire(s, reinterpret_cast<nvhooks::Addr>(&Detour), &tramp, reinterpret_cast<nvhooks::Addr*>(&g_orig));
    if (nvhooks::installed(st)) g_orig = reinterpret_cast<Fn>(tramp);
    if (FNV_MUT(6)) g_reg->acquire(s, reinterpret_cast<nvhooks::Addr>(&Detour), &tramp);
    nvhooks::HookSpec s2 = spec2();
    if (s2.target) {
        nvhooks::Addr t2 = 0;
        if (nvhooks::installed(g_reg->acquire(s2, reinterpret_cast<nvhooks::Addr>(&Detour2), &t2, reinterpret_cast<nvhooks::Addr*>(&g_orig2)))) g_orig2 = reinterpret_cast<Fn>(t2);
    }  // refcount 2: one release leaves it installed
}

void startTicker() {
    g_ticker.start("ticker", [](const std::function<bool()>& stop) {
        while (!stop()) {
            ++g_bg;
            Sleep(1);
        }
    });
    if (FNV_MUT(2)) {  // a thread nobody tracks or joins
        g_rawStop = false;
        g_raw = std::thread([] { while (!g_rawStop.load()) Sleep(1); });
        g_raw.detach();
    }
}

void openSocket() {
    WSADATA d;
    WSAStartup(MAKEWORD(2, 2), &d);
    g_sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    sockaddr_in a{};
    a.sin_family = AF_INET;
    a.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    a.sin_port = 0;
    if (bind(g_sock, reinterpret_cast<sockaddr*>(&a), sizeof a) != 0) { closesocket(g_sock); g_sock = INVALID_SOCKET; return; }
    int n = sizeof a;
    getsockname(g_sock, reinterpret_cast<sockaddr*>(&a), &n);
    g_port = ntohs(a.sin_port);
}

bool adoptSocket(uint64_t handle, uint16_t port) {
    WSADATA d;
    WSAStartup(MAKEWORD(2, 2), &d);
    SOCKET s = static_cast<SOCKET>(handle);
    sockaddr_in a{};
    int n = sizeof a;
    if (getsockname(s, reinterpret_cast<sockaddr*>(&a), &n) != 0 || ntohs(a.sin_port) != port) return false;
    g_sock = s;
    g_port = port;
    g_adopted = true;
    return true;
}

}  // namespace

// ---- participants ----
NVCOOP_RELOAD_STATE(tc_hooks, "tc_hooks", 1, 10,
    /*canUnload*/ [] { return g_reg && g_reg->auditTampered() ? std::string("a hook was chained over") : std::string(); },
    /*save*/ [](nvreload::Writer& w) { w.u32(g_hookCallsBase + g_hookCalls.load()); return true; },
    /*restore*/ [](uint32_t, nvreload::Reader& r) { g_hookCallsBase = r.u32(); ensureHooks(); return true; },
    /*migrate*/ nullptr,
    /*shutdown*/ [] {
        if (!g_reg) return;
        if (FNV_MUT(1)) return;  // forgot to uninstall
        if (FNV_MUT(6)) {
            nvhooks::HookSpec s = spec();
            g_reg->release(s);  // only one reference dropped
            return;
        }
        g_hold = 0;
        g_reg->restoreAllAndVerify();
        g_reg.reset();
    },
    /*fresh*/ [] { ensureHooks(); });

NVCOOP_RELOAD_STATE(tc_counter, "tc_counter", 1, 50, nullptr,
    /*save*/ [](nvreload::Writer& w) {
        if (FNV_MUT(3)) return true;  // forgot to write anything
        w.u64(g_counter);
        w.str(g_label);
        return true;
    },
    /*restore*/ [](uint32_t, nvreload::Reader& r) {
        if (FNV_MUT(5)) {  // reads the fields in the wrong order
            g_label = r.str();
            g_counter = r.u64();
            return true;
        }
        g_counter = r.u64();
        g_label = r.str();
        return true;
    },
    nullptr, nullptr, [] { g_counter = 0; g_label = "gen" + std::to_string(g_api->generation); });

NVCOOP_RELOAD_STATE(tc_thread, "tc_thread", 1, 60, nullptr,
    /*save*/ [](nvreload::Writer& w) { w.u64(g_bgBase + g_bg.load()); return true; },
    /*restore*/ [](uint32_t, nvreload::Reader& r) { g_bgBase = r.u64(); startTicker(); return true; },
    nullptr,
    /*shutdown*/ [] {
        if (FNV_MUT(2)) return;  // never stops the raw thread (it is not counted by the probe either)
        g_ticker.stopAndJoin();
    },
    /*fresh*/ [] { g_bgBase = 0; startTicker(); });

NVCOOP_RELOAD_STATE(tc_socket, "tc_socket", 1, 70, nullptr,
    /*save*/ [](nvreload::Writer& w) { w.u64(static_cast<uint64_t>(g_sock)); w.u16(g_port); return true; },
    /*restore*/ [](uint32_t, nvreload::Reader& r) {
        uint64_t h = r.u64();
        uint16_t p = r.u16();
        if (!adoptSocket(h, p)) openSocket();
        return true;
    },
    nullptr,
    /*shutdown*/ [] {
        if (FNV_MUT(4) && g_sock != INVALID_SOCKET) closesocket(g_sock);  // closes instead of handing over
    },
    /*fresh*/ [] { openSocket(); });

// ---- the core ABI ----
namespace {
int CoreInit() {
    g_threadsAtInit = processThreads();
    readConfig();   // where the real core's modules read nvcoop.ini: onLoad, before AfterLoad
    g_label = "gen" + std::to_string(g_api->generation);
    return nvcoop::reload::AfterLoad(g_api) ? 1 : 0;
}
void CoreOnMessage(uint32_t, void*, uint32_t) {}
void CoreOnFrame() { ++g_counter; }
int CoreBeforeUnload() { return nvcoop::reload::BeforeUnload(g_api) ? 1 : 0; }
}  // namespace

extern "C" __declspec(dllexport) int NvCore_Init(const NvLoaderApi* api, NvCoreApi* out) {
    if (!api || api->abi_version != NVLOADER_ABI_VERSION) return 0;
    g_api = api;
    nvcoop::loader_core::SetApi(api);
    out->abi_version = NVLOADER_ABI_VERSION;
    out->size = sizeof(NvCoreApi);
    out->init = &CoreInit;
    out->on_message = &CoreOnMessage;
    out->on_frame = &CoreOnFrame;
    out->before_unload = &CoreBeforeUnload;
    return 1;
}

extern "C" __declspec(dllexport) void NvCoreTest_Hold(int on) { g_hold = on; }

// Everything the host test inspects, as "key=value;" text.
extern "C" __declspec(dllexport) int NvCoreTest_Info(char* buf, int cap) {
    int n = std::snprintf(buf, cap, "counter=%llu;label=%s;port=%u;adopted=%d;bg=%llu;hookcalls=%u;pristine=%d;threadsAtInit=%d;gen=%u;livehooks=%d;guardcalls=%u;cfg=%s",
                          static_cast<unsigned long long>(g_counter), g_label.c_str(), static_cast<unsigned>(g_port), g_adopted ? 1 : 0,
                          static_cast<unsigned long long>(g_bgBase + g_bg.load()), g_hookCallsBase + g_hookCalls.load(), g_pristineAtInit,
                          g_threadsAtInit, g_api ? g_api->generation : 0, nvhooks::liveHookCount(), nvreload::guardCalls(), g_cfg.c_str());
    return n;
}
