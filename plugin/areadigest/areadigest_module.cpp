// nvcoop module for the area desync digests (x86, compiled into nvcoop.dll). NOTHING here has run in a game.
//   * reads nvcoop.ini [areadigest] (area_config.h): OFF by default; the module does nothing but answer areadigest.status {"enabled":false}
//   * registers the bridge methods areadigest.status and areadigest.flight
//   * the per-frame work is area_glue.h glueFrame(runtime), called by coop_module.cpp (one line, see area_glue.h)
#include <Windows.h>

#include <cctype>
#include <string>

#include "PluginAPI.h"
#include "areadigest/area_glue.h"
#include "common/nvcoop_api.h"
#include "reload/reload_state.h"

namespace {

std::string IniPath() {
    HMODULE self = nullptr;
    GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT, reinterpret_cast<LPCSTR>(&IniPath), &self);
    char buf[MAX_PATH] = {};
    GetModuleFileNameA(self, buf, MAX_PATH);
    std::string p = buf;
    const size_t s = p.find_last_of("\\/");
    return (s == std::string::npos ? std::string() : p.substr(0, s + 1)) + "nvcoop.ini";
}

nvcoop::areadigest::IniGetter MakeGetter() {
    const std::string ini = IniPath();
    return [ini](const char* section, const char* key, const char* def) -> std::string {
        std::string env = "NVCOOP_";
        for (const char* p = section; *p; ++p) env += static_cast<char>(std::toupper(static_cast<unsigned char>(*p)));
        env += '_';
        for (const char* p = key; *p; ++p) env += static_cast<char>(std::toupper(static_cast<unsigned char>(*p)));
        char ebuf[64] = {};
        if (GetEnvironmentVariableA(env.c_str(), ebuf, sizeof ebuf) > 0) return ebuf;
        char buf[128] = {};
        GetPrivateProfileStringA(section, key, def, buf, sizeof buf, ini.c_str());
        return buf;
    };
}

bool g_loaded = false;

void OnLoad(void*) {
    if (g_loaded) return;
    g_loaded = true;
    nvcoop::areadigest::glueConfigure(MakeGetter());
    nvcoop::RegisterMethodClosure("areadigest.status", [](const std::string&, std::string& out) {
        out = nvcoop::areadigest::glueStatusJson();
        return true;
    }, true, 0);
    nvcoop::RegisterMethodClosure("areadigest.flight", [](const std::string&, std::string& out) {
        return nvcoop::areadigest::glueExportFlight(out);
    }, true, 0);
}

void OnMessage(uint32_t type, void*, uint32_t) {
    switch (type) {
        case NVSEMessagingInterface::kMessage_PostLoadGame:
        case NVSEMessagingInterface::kMessage_NewGame: nvcoop::areadigest::glueWorldLoaded(); break;
        case NVSEMessagingInterface::kMessage_ExitGame:
        case NVSEMessagingInterface::kMessage_ExitGame_Console: nvcoop::areadigest::glueShutdown(); break;
        default: break;
    }
}

}  // namespace

NVCOOP_MODULE(area_digest, "area_digest", OnLoad, OnMessage, nullptr);

// Hot reload: the session part is dropped before the core is replaced and rebuilt on the new core's first session frame.
NVCOOP_RELOAD_STATE(area_digest, "area_digest", 1, 44,
    /*canUnload*/ nullptr,
    /*save*/ [](nvreload::Writer& w) { w.boolean(nvcoop::areadigest::glueEnabled()); return true; },
    /*restore*/ [](uint32_t, nvreload::Reader& r) { (void)r.boolean(); return true; },
    /*migrate*/ nullptr,
    /*shutdown*/ [] { nvcoop::areadigest::glueShutdown(); },
    /*fresh*/ nullptr);
