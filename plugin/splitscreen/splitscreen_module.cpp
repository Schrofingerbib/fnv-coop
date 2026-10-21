// nvcoop module for split-screen mode (Track B, docs/tools/splitscreen.md). Inert unless nvcoop.ini has
//   [splitscreen]
//   enable=1
// Game glue only: every decision lives in the game-free libraries next to this file (split_config, pad_router, iat_patch, focus_fake,
// audio_policy), which the unit tests cover. This file wires them to Win32:
//   * IAT patches in FalloutNV.exe: XInputGetState / XInputSetState / XInputGetCapabilities (when imported) -> PadRouter,
//     USER32!GetActiveWindow -> focus_fake. No engine code is patched.
//   * a worker thread that mutes this process's audio session when [splitscreen] audio=mute.
//   * bridge methods split.status (always) and split.inject (only with devInject=1).
// Hot reload: the detours live in this core, so shutdown puts every slot back (canUnload refuses when another patcher chained over a
// slot) and waits until no thread is inside a detour.
#include <Windows.h>
#include <audiopolicy.h>
#include <mmdeviceapi.h>

#include <atomic>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <functional>
#include <memory>
#include <sstream>
#include <string>
#include <vector>

#include "audio_policy.h"
#include "common/minijson.h"
#include "common/nvcoop_api.h"
#include "focus_fake.h"
#include "iat_patch.h"
#include "pad_router.h"
#include "reload/reload_state.h"
#include "split_addrs.h"
#include "split_config.h"
#ifdef NVCOOP_WITH_FOCUS_GUARD
#include "focus_guard.h"
#endif

namespace {

using nvsplit::PadState;

// ---------------------------------------------------------------------------------------------- plumbing
std::string IniPath() {
    HMODULE self = nullptr;
    GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT, reinterpret_cast<LPCSTR>(&IniPath), &self);
    char buf[MAX_PATH] = {};
    GetModuleFileNameA(self, buf, MAX_PATH);
    std::string p = buf;
    const size_t s = p.find_last_of("\\/");
    return (s == std::string::npos ? std::string() : p.substr(0, s + 1)) + "nvcoop.ini";
}

bool ReadWhole(const std::string& path, std::string* out) {
    std::ifstream f(path, std::ios::binary);
    if (!f) return false;
    std::ostringstream ss;
    ss << f.rdbuf();
    *out = ss.str();
    return true;
}

// Tiny functions, one per __try, so no C++ object meets structured exception handling.
bool SafeRead32(uint32_t addr, uint32_t* v) {
    __try {
        *v = *reinterpret_cast<volatile const uint32_t*>(static_cast<uintptr_t>(addr));
        return true;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return false;
    }
}
bool SafeCopy(uint32_t addr, void* dst, size_t n) {
    __try {
        std::memcpy(dst, reinterpret_cast<const void*>(static_cast<uintptr_t>(addr)), n);
        return true;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return false;
    }
}

struct Win32Memory : nvsplit::IMemory32 {
    bool read32(uint32_t a, uint32_t* v) override { return SafeRead32(a, v); }
    bool write32(uint32_t a, uint32_t v) override {
        void* p = reinterpret_cast<void*>(static_cast<uintptr_t>(a));
        DWORD old = 0;
        if (!VirtualProtect(p, 4, PAGE_READWRITE, &old)) return false;
        InterlockedExchange(reinterpret_cast<volatile LONG*>(p), static_cast<LONG>(v));  // one aligned store: a thread sees the old or the new slot
        DWORD ignore = 0;
        VirtualProtect(p, 4, old, &ignore);
        return true;
    }
};

struct LiveExports : nvsplit::IExports {
    uint32_t addressOf(const std::string& dll, const std::string& func) override {
        HMODULE h = GetModuleHandleA(dll.c_str());
        if (!h) return 0;
        return static_cast<uint32_t>(reinterpret_cast<uintptr_t>(GetProcAddress(h, func.c_str())));
    }
};

BOOL CALLBACK ThreadWindowProc(HWND h, LPARAM lp) {
    HWND* best = reinterpret_cast<HWND*>(lp);  // [0] visible unowned, [1] any unowned
    if (GetWindow(h, GW_OWNER) != nullptr) return TRUE;
    if (IsWindowVisible(h)) { best[0] = h; return FALSE; }
    if (!best[1]) best[1] = h;
    return TRUE;
}
struct Win32Windows : nvsplit::IWindowQuery {
    uintptr_t topWindowOfThread(uint32_t tid) override {
        HWND best[2] = {nullptr, nullptr};
        EnumThreadWindows(tid, ThreadWindowProc, reinterpret_cast<LPARAM>(best));
        return reinterpret_cast<uintptr_t>(best[0] ? best[0] : best[1]);
    }
};

// ---------------------------------------------------------------------------------------------- Core Audio sessions
template <class T>
struct Com {
    T* p = nullptr;
    Com() = default;
    Com(const Com&) = delete;
    Com& operator=(const Com&) = delete;
    ~Com() { if (p) p->Release(); }
    T** put() { return &p; }
    T* operator->() const { return p; }
    explicit operator bool() const { return p != nullptr; }
};

std::string Utf8(const wchar_t* w) {
    if (!w) return std::string();
    const int n = WideCharToMultiByte(CP_UTF8, 0, w, -1, nullptr, 0, nullptr, nullptr);
    if (n <= 1) return std::string();
    std::string s(static_cast<size_t>(n - 1), '\0');
    WideCharToMultiByte(CP_UTF8, 0, w, -1, &s[0], n, nullptr, nullptr);
    return s;
}

// f(control, volume, id) returns true to stop. Visits the sessions of every active render endpoint.
template <class F>
bool ForEachSession(F&& f) {
    Com<IMMDeviceEnumerator> en;
    if (FAILED(CoCreateInstance(__uuidof(MMDeviceEnumerator), nullptr, CLSCTX_ALL, __uuidof(IMMDeviceEnumerator), reinterpret_cast<void**>(en.put())))) return false;
    Com<IMMDeviceCollection> devs;
    if (FAILED(en->EnumAudioEndpoints(eRender, DEVICE_STATE_ACTIVE, devs.put()))) return false;
    UINT nd = 0;
    devs->GetCount(&nd);
    for (UINT d = 0; d < nd; ++d) {
        Com<IMMDevice> dev;
        if (FAILED(devs->Item(d, dev.put()))) continue;
        Com<IAudioSessionManager2> mgr;
        if (FAILED(dev->Activate(__uuidof(IAudioSessionManager2), CLSCTX_ALL, nullptr, reinterpret_cast<void**>(mgr.put())))) continue;
        Com<IAudioSessionEnumerator> se;
        if (FAILED(mgr->GetSessionEnumerator(se.put()))) continue;
        int n = 0;
        se->GetCount(&n);
        for (int i = 0; i < n; ++i) {
            Com<IAudioSessionControl> c;
            if (FAILED(se->GetSession(i, c.put()))) continue;
            Com<IAudioSessionControl2> c2;
            Com<ISimpleAudioVolume> vol;
            if (FAILED(c->QueryInterface(__uuidof(IAudioSessionControl2), reinterpret_cast<void**>(c2.put())))) continue;
            if (FAILED(c->QueryInterface(__uuidof(ISimpleAudioVolume), reinterpret_cast<void**>(vol.put())))) continue;
            LPWSTR wid = nullptr;
            std::string id;
            if (SUCCEEDED(c2->GetSessionInstanceIdentifier(&wid))) {
                id = Utf8(wid);
                CoTaskMemFree(wid);
            }
            if (f(c2.p, vol.p, id)) return true;
        }
    }
    return true;
}

struct CoreAudioSessions : nvsplit::ISessions {
    bool list(std::vector<nvsplit::AudioSession>* out) override {
        return ForEachSession([out](IAudioSessionControl2* c2, ISimpleAudioVolume* vol, const std::string& id) {
            DWORD pid = 0;
            BOOL muted = FALSE;
            c2->GetProcessId(&pid);
            vol->GetMute(&muted);
            nvsplit::AudioSession a;
            a.id = id;
            a.pid = pid;
            a.muted = muted != FALSE;
            out->push_back(a);
            return false;
        });
    }
    bool setMute(const std::string& id, bool mute) override {
        bool done = false;
        const bool ran = ForEachSession([&](IAudioSessionControl2*, ISimpleAudioVolume* vol, const std::string& sid) {
            if (sid != id) return false;
            done = SUCCEEDED(vol->SetMute(mute ? TRUE : FALSE, nullptr));
            return true;
        });
        return ran && done;
    }
};

// ---------------------------------------------------------------------------------------------- state
using GetStateFn = DWORD(WINAPI*)(DWORD, void*);
using SetStateFn = DWORD(WINAPI*)(DWORD, void*);
using GetCapsFn = DWORD(WINAPI*)(DWORD, DWORD, void*);
using GetActiveFn = HWND(WINAPI*)();

nvsplit::Config g_cfg;
bool g_inited = false;
Win32Memory g_mem;
LiveExports g_exports;
Win32Windows g_windows;
std::unique_ptr<nvsplit::IatPatcher> g_patcher;
std::unique_ptr<nvsplit::PadRouter> g_router;
std::unique_ptr<nvsplit::AudioMuter> g_muter;
nvreload::ManagedThread g_audioThread;
std::atomic<int> g_inFlight{0};
std::atomic<int> g_mutedNow{0};
std::atomic<uint64_t> g_audioPasses{0};
std::atomic<uint64_t> g_activeFaked{0};
int g_hookGet = 0, g_hookSet = 0, g_hookCaps = 0, g_hookActive = 0;
bool g_pinsOk = false;
std::vector<std::string> g_pinMismatch;
std::string g_cfgError;

volatile LONG g_origGet = 0, g_origSet = 0, g_origCaps = 0, g_origActive = 0;

// A synthetic physical pad (devInject=1): the router's "real XInput" answers from it instead of the hardware, so the smoke playbook can
// press "pad 1" without a person.
struct Inject {
    bool active[4] = {false, false, false, false};
    PadState state[4] = {};
    uint32_t packet[4] = {0, 0, 0, 0};
} g_inject;

struct RealXInput : nvsplit::IXInput {
    uint32_t getState(uint32_t user, PadState* out) override {
        if (user < 4 && g_inject.active[user]) {
            *out = g_inject.state[user];
            out->packetNumber = ++g_inject.packet[user];
            return nvsplit::kErrSuccess;
        }
        auto fn = reinterpret_cast<GetStateFn>(static_cast<uintptr_t>(g_origGet));
        return fn ? fn(user, out) : nvsplit::kErrNotConnected;
    }
    uint32_t setState(uint32_t user, nvsplit::PadVibration* v) override {
        if (user < 4 && g_inject.active[user]) return nvsplit::kErrSuccess;
        auto fn = reinterpret_cast<SetStateFn>(static_cast<uintptr_t>(g_origSet));
        return fn ? fn(user, v) : nvsplit::kErrNotConnected;
    }
    uint32_t getCaps(uint32_t user, uint32_t flags, nvsplit::PadCaps* out) override {
        if (user < 4 && g_inject.active[user]) { *out = nvsplit::NeutralCaps(); return nvsplit::kErrSuccess; }
        auto fn = reinterpret_cast<GetCapsFn>(static_cast<uintptr_t>(g_origCaps));
        return fn ? fn(user, flags, out) : nvsplit::kErrNotConnected;
    }
} g_real;

struct InFlight {
    InFlight() { g_inFlight.fetch_add(1); }
    ~InFlight() { g_inFlight.fetch_sub(1); }
};

DWORD WINAPI DetourGetState(DWORD user, void* out) {
    InFlight f;
    nvsplit::PadRouter* r = g_router.get();
    if (!r) {
        auto fn = reinterpret_cast<GetStateFn>(static_cast<uintptr_t>(g_origGet));
        return fn ? fn(user, out) : nvsplit::kErrNotConnected;
    }
    return r->getState(user, static_cast<PadState*>(out));
}
DWORD WINAPI DetourSetState(DWORD user, void* vib) {
    InFlight f;
    nvsplit::PadRouter* r = g_router.get();
    if (!r) {
        auto fn = reinterpret_cast<SetStateFn>(static_cast<uintptr_t>(g_origSet));
        return fn ? fn(user, vib) : nvsplit::kErrNotConnected;
    }
    return r->setState(user, static_cast<nvsplit::PadVibration*>(vib));
}
DWORD WINAPI DetourGetCaps(DWORD user, DWORD flags, void* caps) {
    InFlight f;
    nvsplit::PadRouter* r = g_router.get();
    if (!r) {
        auto fn = reinterpret_cast<GetCapsFn>(static_cast<uintptr_t>(g_origCaps));
        return fn ? fn(user, flags, caps) : nvsplit::kErrNotConnected;
    }
    return r->getCaps(user, flags, static_cast<nvsplit::PadCaps*>(caps));
}
HWND WINAPI DetourGetActiveWindow() {
    InFlight f;
    auto fn = reinterpret_cast<GetActiveFn>(static_cast<uintptr_t>(g_origActive));
    HWND real = fn ? fn() : nullptr;
    const uintptr_t r = nvsplit::ResolveActiveWindow(g_cfg.focusFake, reinterpret_cast<uintptr_t>(real), GetCurrentThreadId(), g_windows);
    if (r != reinterpret_cast<uintptr_t>(real)) g_activeFaked.fetch_add(1);
    return reinterpret_cast<HWND>(r);
}

// Hooks every import slot of `func` from a DLL whose name starts with `dllPrefix`. Returns how many slots were patched.
int HookImports(const std::vector<nvsplit::ImportRef>& all, const char* dllPrefix, const char* func, void* detour, volatile LONG* origOut) {
    const uint32_t base = static_cast<uint32_t>(reinterpret_cast<uintptr_t>(GetModuleHandleA(nullptr)));
    int n = 0;
    for (const nvsplit::ImportRef& r : nvsplit::FindImportSlots(all, dllPrefix, func, &g_exports)) {
        uint32_t cur = 0;
        const uint32_t slot = base + r.slotRva;
        if (!g_mem.read32(slot, &cur) || cur == 0) continue;
        if (*origOut == 0) *origOut = static_cast<LONG>(cur);  // set before the patch lands, so a detour never sees a null original
        uint32_t orig = 0;
        if (g_patcher->hook(slot, static_cast<uint32_t>(reinterpret_cast<uintptr_t>(detour)), &orig)) ++n;
    }
    return n;
}

void CheckPins() {
    g_pinMismatch.clear();
    for (const auto& p : nvsplit::addr::kPins) {
        uint8_t got[16] = {};
        if (!SafeCopy(p.addr, got, sizeof got) || std::memcmp(got, p.bytes, sizeof got) != 0) g_pinMismatch.push_back(p.name);
    }
    g_pinsOk = g_pinMismatch.empty();
}

int GamepadModeByte() {
    if (!g_pinsOk) return -1;
    uint8_t b = 0;
    return SafeCopy(nvsplit::addr::GamepadModeFlag, &b, 1) ? b : -1;
}

void StartAudio() {
    if (g_cfg.audio != nvsplit::AudioMode::Mute) return;
    g_muter = std::make_unique<nvsplit::AudioMuter>(g_cfg.audio, GetCurrentProcessId());
    g_audioThread.start("split_audio", [](const std::function<bool()>& stopped) {
        const HRESULT hr = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
        {
            CoreAudioSessions sessions;
            while (!stopped()) {
                g_muter->tick(sessions);
                g_mutedNow.store(g_muter->mutedCount());
                g_audioPasses.store(g_muter->passes());
                for (int i = 0; i < 10 && !stopped(); ++i) Sleep(100);
            }
            g_muter->release(sessions);
            g_mutedNow.store(g_muter->mutedCount());
        }
        if (SUCCEEDED(hr)) CoUninitialize();
    });
}

void Shutdown() {
    if (!g_inited) return;
    g_audioThread.stopAndJoin();
    if (g_patcher) {
        const int failed = g_patcher->unhookAll();
        if (failed) nvcoop::Log("splitscreen: %d import slot(s) could not be restored", failed);
    }
    for (int i = 0; i < 200 && g_inFlight.load() > 0; ++i) Sleep(10);  // a thread may still be inside a detour
    g_router.reset();
    g_muter.reset();
    g_patcher.reset();
    g_inited = false;
}

bool StatusMethod(const std::string&, std::string& out) {
    using nvcoop::json::Value;
    Value r = Value::object();
    r.set("enabled", g_inited);
    r.set("configError", g_cfgError);
    r.set("player", g_cfg.player);
    r.set("players", g_cfg.players);
    r.set("pad", g_cfg.pad);
    r.set("kbm", g_cfg.kbm);
    r.set("ghostPad", g_cfg.ghostPad);
    r.set("audioMode", g_cfg.audio == nvsplit::AudioMode::Mute ? "mute" : "play");
    r.set("focusFake", g_cfg.focusFake);
    Value hooks = Value::object();
    hooks.set("xinputGetState", g_hookGet);
    hooks.set("xinputSetState", g_hookSet);
    hooks.set("xinputGetCapabilities", g_hookCaps);
    hooks.set("getActiveWindow", g_hookActive);
    hooks.set("intact", g_patcher ? g_patcher->allIntact() : false);
    r.set("hooks", hooks);
    Value pins = Value::object();
    pins.set("ok", g_pinsOk);
    Value bad = Value::array();
    for (const auto& n : g_pinMismatch) bad.push(n);
    pins.set("mismatch", bad);
    r.set("pins", pins);
    Value pad = Value::object();
    if (g_router) {
        const nvsplit::RouterStats& s = g_router->stats();
        pad.set("statePolls", static_cast<uint64_t>(s.statePolls));
        Value phys = Value::array();
        for (int i = 0; i < 4; ++i) phys.push(static_cast<uint64_t>(s.physicalPolls[i]));
        pad.set("physicalPolls", phys);
        pad.set("hidden", static_cast<uint64_t>(s.hidden));
        pad.set("ghost", static_cast<uint64_t>(s.ghost));
        pad.set("rumbleSent", static_cast<uint64_t>(s.rumbleSent));
        pad.set("rumbleDropped", static_cast<uint64_t>(s.rumbleDropped));
        pad.set("lastResult", static_cast<uint64_t>(s.lastResult));
        pad.set("lastButtons", static_cast<uint64_t>(s.lastReturnedButtons));
        pad.set("lastLX", static_cast<int>(s.lastReturnedLX));
    }
    pad.set("gamepadMode", GamepadModeByte());
    r.set("padStats", pad);
    Value audio = Value::object();
    audio.set("mutedSessions", g_mutedNow.load());
    audio.set("passes", static_cast<uint64_t>(g_audioPasses.load()));
    r.set("audio", audio);
    Value focus = Value::object();
    focus.set("activeWindowFaked", static_cast<uint64_t>(g_activeFaked.load()));
#ifdef NVCOOP_WITH_FOCUS_GUARD
    focus.set("guardInstalled", nvsplit::Installed() != nullptr);
    focus.set("swallowedDeactivations", static_cast<uint64_t>(nvsplit::Swallowed()));
#endif
    r.set("focus", focus);
    // The window of this copy: where it is, how big, whether it is borderless, in front, minimized.
    Value win = Value::object();
    HWND h = reinterpret_cast<HWND>(g_windows.topWindowOfThread(GetCurrentThreadId()));
    win.set("found", h != nullptr);
    if (h) {
        RECT wr = {}, cr = {};
        GetWindowRect(h, &wr);
        GetClientRect(h, &cr);
        POINT tl = {0, 0};
        ClientToScreen(h, &tl);
        const LONG style = GetWindowLongA(h, GWL_STYLE);
        win.set("x", static_cast<int>(wr.left));
        win.set("y", static_cast<int>(wr.top));
        win.set("w", static_cast<int>(wr.right - wr.left));
        win.set("h", static_cast<int>(wr.bottom - wr.top));
        win.set("clientX", static_cast<int>(tl.x));
        win.set("clientY", static_cast<int>(tl.y));
        win.set("clientW", static_cast<int>(cr.right - cr.left));
        win.set("clientH", static_cast<int>(cr.bottom - cr.top));
        win.set("style", static_cast<uint64_t>(static_cast<unsigned long>(style)));
        win.set("borderless", (style & WS_CAPTION) == 0 && (style & WS_THICKFRAME) == 0);
        win.set("foreground", GetForegroundWindow() == h);
        win.set("minimized", IsIconic(h) != FALSE);
    }
    r.set("window", win);
    out = r.dump();
    return true;
}

// split.inject {pad:0..3, buttons, lx, ly, rx, ry, lt, rt} or {pad, clear:true}: devInject=1 only.
bool InjectMethod(const std::string& params, std::string& out) {
    nvcoop::json::Value v;
    std::string err;
    if (!nvcoop::json::parse(params.empty() ? "{}" : params, v, err)) { out = "bad params: " + err; return false; }
    const int pad = static_cast<int>(v.get("pad").asInt(-1));
    if (pad < 0 || pad > 3) { out = "pad must be 0..3"; return false; }
    if (v.get("clear").asBool(false)) {
        g_inject.active[pad] = false;
    } else {
        PadState s = {};
        s.gamepad.buttons = static_cast<uint16_t>(v.get("buttons").asInt(0));
        s.gamepad.thumbLX = static_cast<int16_t>(v.get("lx").asInt(0));
        s.gamepad.thumbLY = static_cast<int16_t>(v.get("ly").asInt(0));
        s.gamepad.thumbRX = static_cast<int16_t>(v.get("rx").asInt(0));
        s.gamepad.thumbRY = static_cast<int16_t>(v.get("ry").asInt(0));
        s.gamepad.leftTrigger = static_cast<uint8_t>(v.get("lt").asInt(0));
        s.gamepad.rightTrigger = static_cast<uint8_t>(v.get("rt").asInt(0));
        g_inject.state[pad] = s;
        g_inject.active[pad] = true;
    }
    out = "{\"pad\":" + std::to_string(pad) + ",\"active\":" + (g_inject.active[pad] ? "true" : "false") + "}";
    return true;
}

void Init() {
    if (g_inited) return;
    std::string text;
    if (!ReadWhole(IniPath(), &text)) return;
    nvsplit::Config cfg;
    g_cfgError.clear();
    if (!nvsplit::ParseConfig(text, &cfg, &g_cfgError)) nvcoop::Log("splitscreen: %s (split-screen stays off)", g_cfgError.c_str());
    g_cfg = cfg;
    if (!g_cfg.enable) return;

    CheckPins();
    if (!g_pinsOk) nvcoop::Log("splitscreen: %zu engine pin(s) do not match this exe; routing still uses the import table", g_pinMismatch.size());

    const HMODULE exe = GetModuleHandleA(nullptr);
    const auto* dos = reinterpret_cast<const IMAGE_DOS_HEADER*>(exe);
    const auto* nt = reinterpret_cast<const IMAGE_NT_HEADERS32*>(reinterpret_cast<const uint8_t*>(exe) + dos->e_lfanew);
    std::vector<nvsplit::ImportRef> imports;
    std::string err;
    if (!nvsplit::EnumImports(reinterpret_cast<const uint8_t*>(exe), nt->OptionalHeader.SizeOfImage, &imports, &err))
        nvcoop::Log("splitscreen: import table: %s", err.c_str());

    nvsplit::RouteConfig rc;
    rc.pad = g_cfg.pad;
    rc.ghostPad = g_cfg.ghostPad;
    g_router = std::make_unique<nvsplit::PadRouter>(rc, g_real);
    g_patcher = std::make_unique<nvsplit::IatPatcher>(g_mem);
    if (g_cfg.routesPad()) {
        g_hookGet = HookImports(imports, "xinput", "XInputGetState", reinterpret_cast<void*>(&DetourGetState), &g_origGet);
        g_hookSet = HookImports(imports, "xinput", "XInputSetState", reinterpret_cast<void*>(&DetourSetState), &g_origSet);
        g_hookCaps = HookImports(imports, "xinput", "XInputGetCapabilities", reinterpret_cast<void*>(&DetourGetCaps), &g_origCaps);
    }
    if (g_cfg.focusFake) g_hookActive = HookImports(imports, "user32", "GetActiveWindow", reinterpret_cast<void*>(&DetourGetActiveWindow), &g_origActive);
    StartAudio();
    g_inited = true;
    nvcoop::Log("splitscreen: seat %d of %d, pad=%d kbm=%d ghostPad=%d audio=%s; hooks get=%d set=%d caps=%d active=%d pins=%s", g_cfg.player + 1,
                g_cfg.players, g_cfg.pad, g_cfg.kbm ? 1 : 0, g_cfg.ghostPad ? 1 : 0, g_cfg.audio == nvsplit::AudioMode::Mute ? "mute" : "play",
                g_hookGet, g_hookSet, g_hookCaps, g_hookActive, g_pinsOk ? "ok" : "MISMATCH");
    if (g_cfg.routesPad() && g_hookGet == 0)
        nvcoop::Log("splitscreen: WARNING no XInputGetState import slot found: pad routing is NOT active");
    if (g_cfg.devInject) nvcoop::RegisterMethodClosure("split.inject", InjectMethod, true, 0);
}

void OnLoad(void*) {
    nvcoop::RegisterMethodClosure("split.status", StatusMethod, true, 0);
    Init();
}

}  // namespace

NVCOOP_MODULE(trackb_splitscreen, "trackb_splitscreen", OnLoad, nullptr, nullptr);

NVCOOP_RELOAD_STATE(trackb_splitscreen, "trackb_splitscreen", 1, 20,
    /*canUnload*/ [] {
        return (g_patcher && !g_patcher->allIntact()) ? std::string("an import slot was chained over by another mod") : std::string();
    },
    /*save*/ [](nvreload::Writer& w) {
        w.boolean(g_inited);
        return true;
    },
    /*restore*/ [](uint32_t, nvreload::Reader& r) {
        if (r.boolean()) Init();
        return true;
    },
    /*migrate*/ nullptr,
    /*shutdown*/ [] { Shutdown(); },
    /*fresh*/ nullptr);
