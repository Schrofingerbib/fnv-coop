// Stub core DLL for the loader tests. Built several times with different STUB_* defines:
//   STUB_VERSION=n     which "build" this is (the test overwrites the core file with a different version)
//   STUB_NO_EXPORT     no NvCore_Init export (broken build)
//   STUB_FAIL_INIT     NvCore_Init succeeds but core init() fails after starting a thread
//   STUB_BAD_ABI       reports a wrong ABI version
// Env var NVSTUB_VETO=1 (read with the Win32 API; this DLL has its own CRT) makes before_unload refuse.
// Config: at init the stub reads [stub] value= from nvcoop.ini and the text of nvcoop_stub.json, both from the folder
// of its OWN module file (the same lookup as IniPath() in the real modules). Method stub.cfg returns what it read.
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <string>
#include <thread>

#include "loader_abi.h"

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>

#ifndef STUB_VERSION
#define STUB_VERSION 1
#endif

namespace {

const NvLoaderApi* g_api = nullptr;
std::atomic<uint32_t> g_frames{0};
std::atomic<uint32_t> g_messages{0};
std::atomic<bool> g_run{false};
std::thread g_thread;

void StartThread() {
    g_run = true;
    g_thread = std::thread([] { while (g_run.load()) Sleep(2); });
}
void StopThread() {
    g_run = false;
    if (g_thread.joinable()) g_thread.join();
}

int MInfo(void*, const char*, NvOut* out) {
    char b[96];
    std::snprintf(b, sizeof b, "v%d:frames=%u:msgs=%u:gen=%u", STUB_VERSION, g_frames.load(), g_messages.load(), g_api->generation);
    out->set(out->h, b);
    return 1;
}
int MOnlyThisVersion(void*, const char*, NvOut* out) { out->set(out->h, "only-here"); return 1; }
int MSlow(void*, const char*, NvOut* out) {
    Sleep(300);
    out->set(out->h, "slow-done");
    return 1;
}
int MFail(void*, const char*, NvOut* out) { out->set(out->h, "deliberate failure"); return 0; }
// ---- config beside this module (what the real modules do in onLoad) ----
std::string g_cfgIni = "unread", g_cfgJson = "unread";
std::string SelfDir() {
    HMODULE self = nullptr;
    GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                       reinterpret_cast<LPCSTR>(&SelfDir), &self);
    char buf[MAX_PATH] = {};
    GetModuleFileNameA(self, buf, MAX_PATH);
    std::string p = buf;
    size_t s = p.find_last_of("\\/");
    return s == std::string::npos ? std::string() : p.substr(0, s + 1);
}
void ReadConfig() {
    const std::string dir = SelfDir();
    char v[128] = {};
    GetPrivateProfileStringA("stub", "value", "missing", v, sizeof v, (dir + "nvcoop.ini").c_str());
    g_cfgIni = v;
    g_cfgJson = "missing";
    HANDLE h = CreateFileA((dir + "nvcoop_stub.json").c_str(), GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr, OPEN_EXISTING, 0, nullptr);
    if (h == INVALID_HANDLE_VALUE) return;
    char b[128] = {};
    DWORD n = 0;
    ReadFile(h, b, sizeof b - 1, &n, nullptr);
    CloseHandle(h);
    g_cfgJson.assign(b, n);
}
int MCfg(void*, const char*, NvOut* out) {
    const std::string s = "ini=" + g_cfgIni + ";json=" + g_cfgJson;
    out->set(out->h, s.c_str());
    return 1;
}

int SState(const char* csv, NvOut* out) {
    std::string s = std::string("{\"stub\":") + std::to_string(STUB_VERSION) + ",\"sections\":\"" + (csv ? csv : "") + "\"}";
    out->set(out->h, s.c_str());
    return 1;
}

int CoreInit() {
    StartThread();
#ifdef STUB_FAIL_INIT
    return 0;
#else
    ReadConfig();
    uint32_t len = 0, ver = 0;
    uint32_t frames = 0;
    if (g_api->is_reload && g_api->blob_get("stub.frames", &frames, sizeof frames, &len, &ver) && len == sizeof frames) g_frames = frames;
    g_api->register_method("stub.info", &MInfo, nullptr, 1);
    g_api->register_method("stub.cfg", &MCfg, nullptr, 1);
    g_api->register_method("stub.slow", &MSlow, nullptr, 0);
    g_api->register_method("stub.fail", &MFail, nullptr, 1);
    static char onlyName[32];
    std::snprintf(onlyName, sizeof onlyName, "stub.only_v%d", STUB_VERSION);   // exists only in this build
    g_api->register_method(onlyName, &MOnlyThisVersion, nullptr, 1);
    g_api->set_state_provider(&SState);
    g_api->push_event("stub.init", nullptr);
    return 1;
#endif
}
void CoreOnMessage(uint32_t, void*, uint32_t) { ++g_messages; }
void CoreOnFrame() { ++g_frames; }
int CoreBeforeUnload() {
    char b[8];
    if (GetEnvironmentVariableA("NVSTUB_VETO", b, sizeof b) > 0 && b[0] == '1') {
        g_api->log("stub: vetoing unload");
        return 0;
    }
    StopThread();
    uint32_t frames = g_frames.load();
    g_api->blob_put("stub.frames", &frames, sizeof frames, 1);
    return 1;
}

}  // namespace

#ifndef STUB_NO_EXPORT
extern "C" __declspec(dllexport) int NvCore_Init(const NvLoaderApi* api, NvCoreApi* out) {
    g_api = api;
    out->abi_version = NVLOADER_ABI_VERSION;
#ifdef STUB_BAD_ABI
    out->abi_version = 99;
#endif
    out->size = sizeof(NvCoreApi);
    out->init = &CoreInit;
    out->on_message = &CoreOnMessage;
    out->on_frame = &CoreOnFrame;
    out->before_unload = &CoreBeforeUnload;
    return 1;
}
#else
extern "C" __declspec(dllexport) int NvStub_Other() { return 0; }
#endif
