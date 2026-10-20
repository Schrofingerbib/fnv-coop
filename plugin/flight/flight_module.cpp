// Flight recorder module: ini [flight], bridge methods flight.dump / flight.status, crash handler lifecycle.
// The recording itself happens inside coop's Runtime (Runtime::setFlight), see coop_module.cpp.
#include <windows.h>
#include "common/nvcoop_api.h"
#include "flight_service.h"
#include "reload/reload_state.h"

namespace {

std::string DllDir() {
    HMODULE self = nullptr;
    GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                       reinterpret_cast<LPCSTR>(&DllDir), &self);
    char buf[MAX_PATH] = {};
    GetModuleFileNameA(self, buf, MAX_PATH);
    std::string p = buf;
    size_t s = p.find_last_of("\/");
    return s == std::string::npos ? std::string() : p.substr(0, s + 1);
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

bool MDump(const std::string& params, std::string& out) { return nvcoop::flight::service().dumpJson(params, out); }
bool MStatus(const std::string&, std::string& out) { out = nvcoop::flight::service().statusJson(); return true; }

void OnLoad(void*) {
    nvcoop::flight::ServiceConfig cfg;
    cfg.dir = DllDir() + "nvcoop_flight";
    nvcoop::flight::parseServiceConfig(ReadWholeFile(DllDir() + "nvcoop.ini"), &cfg);
    bool on = nvcoop::flight::service().init(cfg, fnvcoop::flight::kNoSlot, fnvcoop::flight::kRoleSim);
    nvcoop::Log("flight: %s dir=%s ring=%uMB crash=%d", on ? "recording" : "off", cfg.dir.c_str(), (unsigned)cfg.ringMb, cfg.crashDump ? 1 : 0);
    nvcoop::RegisterMethod("flight.dump", MDump, true);
    nvcoop::RegisterMethod("flight.status", MStatus, true);
}

}  // namespace

NVCOOP_MODULE(t1a_flight, "t1a_flight", OnLoad, nullptr, nullptr);

// Hot reload: the crash filter lives in this DLL, so it must be removed (and the recorder flushed) before the core unloads.
// Order 5 = shutdown runs last, so the filter covers a crash in any other participant's shutdown. The new core re-inits in OnLoad.
NVCOOP_RELOAD_STATE(t1a_flight, "t1a_flight", 1, 5,
    /*canUnload*/ nullptr, /*save*/ nullptr, /*restore*/ nullptr, /*migrate*/ nullptr,
    /*shutdown*/ [] { nvcoop::flight::service().shutdown(); },
    /*fresh*/ nullptr);
