// nvmp_server_hook.dll: host-ownership policy for the NV:MP 9.4 story server (nvmp_storyserver.exe, x86).
// Injected suspended by nvmp_hook_launcher.exe before the server's main thread runs; installs 3 inline hooks
// located by signature (never by fixed RVA). All decisions are logged to server_hook.log next to the server.
// Design and offsets: internal lane notes (round 2).
//
//   encounter handler  RVA 0x9BFD0  drop encounters of host-owned FormIDs coming from a non-host sender
//   hot-swap reselect  RVA 0x9E350  keep host-owned refs on the host while the host is connected
//   connection accept  RVA 0x7FFB0  learn which connection is loopback (= the host)
#include <windows.h>

#include <algorithm>
#include <cstdarg>
#include <cstdio>
#include <cstring>
#include <map>
#include <mutex>
#include <string>
#include <vector>

#include "../sigscan.h"
#include "hookcore.h"
#include "sites.h"
#include "hostfilter.h"
#include "resolve.h"

using namespace nvmp;
using namespace nvmp::hook;

// ---------------------------------------------------------------------------------------------
// state
// ---------------------------------------------------------------------------------------------
namespace {
HookConfig g_cfg;
HostOwnedStore g_store;
std::mutex g_peerMu;
PeerTable g_peers;
std::wstring g_dir;

InlineHook g_hEnc, g_hResel, g_hConn;
uintptr_t g_charMgrGlobal = 0;
uint32_t g_connRva = 0;  // address of the global pointer to the character manager
typedef int(__cdecl* AddrFmtFn)(const void* src, char* dst, int size);
AddrFmtFn g_addrFn = nullptr;

FILE* g_log = nullptr;
CRITICAL_SECTION g_logCs;
volatile LONG g_stopPoll = 0;

// counters
volatile LONG g_nEncounterFlagged = 0, g_nDropped = 0, g_nAllowedFlagged = 0, g_nBlocked = 0;

void Log(const char* fmt, ...) {
    if (!g_log) return;
    char buf[1024];
    va_list ap;
    va_start(ap, fmt);
    _vsnprintf_s(buf, sizeof(buf), _TRUNCATE, fmt, ap);
    va_end(ap);
    SYSTEMTIME st;
    GetLocalTime(&st);
    EnterCriticalSection(&g_logCs);
    fprintf(g_log, "%02d:%02d:%02d.%03d [%lu] %s\n", st.wHour, st.wMinute, st.wSecond, st.wMilliseconds,
            GetCurrentThreadId(), buf);
    fflush(g_log);
    LeaveCriticalSection(&g_logCs);
}

// Log a decision only when it differs from the last one for the same (kind, formId): reselect can run often.
std::mutex g_dedupeMu;
std::map<uint64_t, std::pair<int, unsigned>> g_lastVerdict;
bool ShouldLogDecision(uint32_t kind, uint32_t formId, int verdict) {
    std::lock_guard<std::mutex> g(g_dedupeMu);
    auto& e = g_lastVerdict[((uint64_t)kind << 32) | formId];
    if (e.first == verdict + 1) {
        ++e.second;
        return (e.second % 500) == 0;  // periodic reminder
    }
    e.first = verdict + 1;
    e.second = 0;
    return true;
}

// ---- SEH-guarded reads (no C++ objects in these functions) -----------------------------------
bool ReadU32(const void* p, uint32_t* out) {
    __try { *out = *(volatile const uint32_t*)p; return true; } __except (EXCEPTION_EXECUTE_HANDLER) { return false; }
}
bool ReadU8(const void* p, uint8_t* out) {
    __try { *out = *(volatile const uint8_t*)p; return true; } __except (EXCEPTION_EXECUTE_HANDLER) { return false; }
}
bool ReadPtr(const void* p, uintptr_t* out) {
    __try { *out = *(volatile const uintptr_t*)p; return true; } __except (EXCEPTION_EXECUTE_HANDLER) { return false; }
}
bool CallFormId(void* obj, uint32_t* out) {  // obj->vtable[0xA4/4]()  (thiscall, returns the FormID)
    __try {
        void** vt = *(void***)obj;
        typedef uint32_t(__thiscall * Fn)(void*);
        *out = ((Fn)vt[0xA4 / 4])(obj);
        return true;
    } __except (EXCEPTION_EXECUTE_HANDLER) { return false; }
}
bool FormatAddr(void* peer, char* buf) {
    __try { return g_addrFn((const char*)peer + 0x18, buf, 0x40) >= 0; } __except (EXCEPTION_EXECUTE_HANDLER) { return false; }
}
// Identity dwords of all connected characters in manager order (insertion order = join order).
int ReadCharKeys(uint32_t* out, int maxN) {
    if (!g_charMgrGlobal) return -1;
    __try {
        uintptr_t mgr = *(volatile uintptr_t*)g_charMgrGlobal;
        if (!mgr) return -1;
        uintptr_t sentinel = *(volatile uintptr_t*)(mgr + 0x54);
        if (!sentinel) return -1;
        uintptr_t node = *(volatile uintptr_t*)sentinel;
        int n = 0, guard = 0;
        while (node && node != sentinel && n < maxN && guard++ < 1024) {
            uintptr_t chr = *(volatile uintptr_t*)(node + 0xC);
            if (chr) {
                void** vt = *(void***)chr;
                typedef const uint32_t*(__thiscall * KeyFn)(void*);
                const uint32_t* k = ((KeyFn)vt[1])((void*)chr);
                if (k) out[n++] = *k;
            }
            node = *(volatile uintptr_t*)node;
        }
        return n;
    } __except (EXCEPTION_EXECUTE_HANDLER) { return -1; }
}

std::vector<uint32_t> CharKeys() {
    uint32_t keys[64];
    int n = ReadCharKeys(keys, 64);
    return n > 0 ? std::vector<uint32_t>(keys, keys + n) : std::vector<uint32_t>();
}

// ---------------------------------------------------------------------------------------------
// decisions (called from the stubs)
// ---------------------------------------------------------------------------------------------
extern "C" unsigned __cdecl OnEncounter(void* msg, void* wrapper) {
    uint32_t type = 0, formId = 0;
    if (!msg || !ReadU32((char*)msg + 0x88, &type) || !ReadU32((char*)msg + 0x110, &formId)) return 0;
    std::shared_ptr<const HostOwnedSet> set = g_store.Get();
    if (!g_cfg.enabled || !set->Contains(formId)) {
        if (g_cfg.logAll) Log("encounter form=%08X type=%u -> allow(not-flagged)", formId, type);
        return 0;
    }
    SenderRef s;
    uintptr_t conn = 0;
    if (wrapper && ReadPtr((char*)wrapper + 4, &conn) && conn) {
        s.ptr = conn;
        ReadU32((char*)conn + 4, &s.key);
    }
    std::vector<uint32_t> keys = CharKeys();
    uint32_t firstKey = keys.empty() ? 0 : keys.front();
    HostResolution r;
    {
        std::lock_guard<std::mutex> g(g_peerMu);
        r = ResolveSender(g_peers, s, firstKey, g_cfg.mode);
    }
    EncounterVerdict v = DecideEncounter(*set, g_cfg, formId, type, r.kind);
    InterlockedIncrement(&g_nEncounterFlagged);
    if (IsDrop(v)) InterlockedIncrement(&g_nDropped); else InterlockedIncrement(&g_nAllowedFlagged);
    if (ShouldLogDecision(1, formId, (int)v))
        Log("encounter form=%08X type=%u sender(conn=%p key=%08X) via %s -> %s", formId, type, (void*)s.ptr, s.key, r.reason,
            ToString(v));
    return IsDrop(v) ? 1u : 0u;
}

extern "C" unsigned __cdecl OnReselect(void* obj) {
    if (!g_cfg.enabled || !g_cfg.blockHandover || !obj) return 0;
    uint32_t formId = 0;
    if (!CallFormId(obj, &formId)) return 0;
    std::shared_ptr<const HostOwnedSet> set = g_store.Get();
    if (!set->Contains(formId)) return 0;
    std::vector<uint32_t> keys = CharKeys();
    uint32_t firstKey = keys.empty() ? 0 : keys.front();
    bool present;
    {
        std::lock_guard<std::mutex> g(g_peerMu);
        present = IsHostPresent(g_peers, keys, firstKey, g_cfg.mode);
    }
    HandoverVerdict v = DecideHandover(*set, g_cfg, formId, present);
    if (IsBlock(v)) InterlockedIncrement(&g_nBlocked);
    if (ShouldLogDecision(2, formId, (int)v))
        Log("reselect form=%08X obj=%p connectedChars=%u hostPresent=%d -> %s", formId, obj, (unsigned)keys.size(), present ? 1 : 0,
            ToString(v));
    return IsBlock(v) ? 1u : 0u;
}

extern "C" void __cdecl OnConnect(void* mgr, void* ev) {
    uintptr_t peer = 0;
    if (!ev || !ReadPtr((char*)ev + 8, &peer) || !peer) return;
    PeerInfo p;
    p.ptr = peer;
    ReadU32((char*)peer + 4, &p.key0);
    ReadU32((char*)peer + 0x10, &p.key1);
    uint8_t idMode = 1;
    if (mgr) ReadU8((char*)mgr + 0xAC, &idMode);
    char buf[0x40] = {};
    if (!idMode && g_addrFn && FormatAddr((void*)peer, buf)) {
        buf[sizeof(buf) - 1] = 0;
        p.addr = buf;
        p.loopback = IsLoopbackAddress(p.addr);
        for (const auto& a : g_cfg.hostAddresses)
            if (p.addr == a) p.loopback = true;
    }
    {
        std::lock_guard<std::mutex> g(g_peerMu);
        g_peers.Add(p);
    }
    Log("connection peer=%p key0=%08X key1=%08X addr=%s idMode=%d -> %s", (void*)peer, p.key0, p.key1,
        p.addr.empty() ? "(unavailable)" : p.addr.c_str(), idMode, p.loopback ? "HOST (loopback/listed)" : "guest");
}

// ---------------------------------------------------------------------------------------------
// stubs (naked): pushad; call decision; popad; either `jmp trampoline` or return to the caller
// ---------------------------------------------------------------------------------------------
uint8_t* g_encTramp = nullptr;
uint8_t* g_reselTramp = nullptr;
uint8_t* g_connTramp = nullptr;

__declspec(naked) void EncounterStub() {
    __asm {
        pushad
        mov eax, [esp + 0x24]      // stack arg 0 (packet wrapper) behind pushad + return address
        push eax
        push ecx                   // this = encounter message
        call OnEncounter
        add esp, 8
        test eax, eax
        jnz drop
        popad
        jmp dword ptr[g_encTramp]
    drop:
        popad
        ret 4
    }
}

__declspec(naked) void ReselectStub() {
    __asm {
        pushad
        push ecx                   // this = net object
        call OnReselect
        add esp, 4
        test eax, eax
        jnz block
        popad
        jmp dword ptr[g_reselTramp]
    block:
        popad
        ret 4
    }
}

__declspec(naked) void ConnectStub() {
    __asm {
        pushad
        mov eax, [esp + 0x24]
        push eax
        push ecx
        call OnConnect
        add esp, 8
        popad
        jmp dword ptr[g_connTramp]
    }
}

// ---------------------------------------------------------------------------------------------
// install
// ---------------------------------------------------------------------------------------------
std::string ReadWholeFile(const std::wstring& path, bool* ok) {
    std::string out;
    FILE* f = _wfopen(path.c_str(), L"rb");
    if (!f) { *ok = false; return out; }
    char buf[4096];
    size_t n;
    while ((n = fread(buf, 1, sizeof(buf), f)) > 0) out.append(buf, n);
    fclose(f);
    *ok = true;
    return out;
}

uint64_t FileStamp(const std::wstring& path) {
    WIN32_FILE_ATTRIBUTE_DATA d;
    if (!GetFileAttributesExW(path.c_str(), GetFileExInfoStandard, &d)) return 0;  // missing => 0
    return ((uint64_t)d.ftLastWriteTime.dwHighDateTime << 32 | d.ftLastWriteTime.dwLowDateTime) ^ ((uint64_t)d.nFileSizeLow * 0x9E3779B97F4A7C15ull) ^ 1;
}

DWORD WINAPI PollThread(LPVOID) {
    const std::wstring path = g_dir + L"hostowned.txt";
    bool firstMissing = true;
    uint64_t lastCount = 0;
    while (!g_stopPoll) {
        uint64_t stamp = FileStamp(path);
        bool reloaded = g_store.PollOnce(stamp, [&]() {
            bool ok = false;
            std::string t = ReadWholeFile(path, &ok);
            return ok ? t : std::string();
        });
        if (reloaded) {
            auto s = g_store.Get();
            if (stamp == 0) {
                if (firstMissing) Log("hostowned.txt not found next to the server: no ref is host-owned (create it to enable)");
                firstMissing = false;
            } else {
                Log("hostowned.txt loaded: %zu FormIDs, %zu lines, %zu rejected (reload #%llu)", s->ids.size(), s->lines, s->rejected,
                    (unsigned long long)g_store.reloads());
                for (const auto& e : s->errors) Log("  rejected %s", e.c_str());
                Log("  resolution: %zu named, %zu remapped via file header, %zu unresolved (skipped), %zu with bad mod index", s->named, s->remapped,
                    s->unresolved, s->badIndex);
                for (const auto& w : s->warnings) Log("  !!!! WARNING: %s", w.c_str());
                if (s->unresolved || s->badIndex || s->rejected)
                    Log("  !!!! WARNING: %zu hostowned entries were skipped/suspect: check the load order above and hostowned.txt", s->unresolved + s->badIndex + s->rejected);
            }
            lastCount = s->ids.size();
        }
        Sleep(g_cfg.pollMs);
    }
    (void)lastCount;
    return 0;
}

bool IsStoryServerExe(std::wstring* dirOut) {
    wchar_t path[MAX_PATH * 2] = {};
    GetModuleFileNameW(nullptr, path, MAX_PATH * 2);
    std::wstring p = path;
    size_t sl = p.find_last_of(L"\\/");
    *dirOut = sl == std::wstring::npos ? L"" : p.substr(0, sl + 1);
    std::wstring name = sl == std::wstring::npos ? p : p.substr(sl + 1);
    for (auto& c : name) c = (wchar_t)towlower(c);
    char force[8] = {};
    GetEnvironmentVariableA("NVMP_HOOK_FORCE", force, sizeof(force));
    return name.find(L"storyserver") != std::wstring::npos || force[0] == '1';
}

// Plugin order the server will use, to turn "Plugin.esm:ID" (or the file header's assumed indices) into runtime FormIDs.
// Priority: explicit hostowned_loadorder.txt, plugins_override.txt, Data\plugins.txt / plugins.txt, %LOCALAPPDATA%\FalloutNV\plugins.txt
// (the server logs "Using plugins_override.txt" / "Using system plugins.txt"), then file times of Data\*.esm,*.esp
// (the server's own last resort), then the canonical default. The last two are marked as guesses.
LoadOrder DiscoverLoadOrder() {
    wchar_t lad[MAX_PATH] = {};
    GetEnvironmentVariableW(L"LOCALAPPDATA", lad, MAX_PATH);
    struct Cand { std::wstring path; const char* label; };
    std::vector<Cand> cands = {
        {g_dir + L"hostowned_loadorder.txt", "hostowned_loadorder.txt"},
        {g_dir + L"plugins_override.txt", "plugins_override.txt"},
        {g_dir + L"Data\\plugins_override.txt", "Data\\plugins_override.txt"},
        {g_dir + L"Data\\plugins.txt", "Data\\plugins.txt"},
        {g_dir + L"plugins.txt", "plugins.txt"},
    };
    if (lad[0]) cands.push_back({std::wstring(lad) + L"\\FalloutNV\\plugins.txt", "%LOCALAPPDATA%\\FalloutNV\\plugins.txt"});
    for (const auto& c : cands) {
        bool ok = false;
        std::string t = ReadWholeFile(c.path, &ok);
        if (!ok) continue;
        LoadOrder lo = ParseLoadOrder(t, c.label);
        if (lo.plugins.size() > 1) return lo;
        Log("load order source %s exists but lists no plugins besides FalloutNV.esm (ignored)", c.label);
    }
    // file-time order of Data\*.esm then *.esp
    struct F { std::string name; bool esm; unsigned long long t; };
    std::vector<F> files;
    for (const wchar_t* pat : {L"Data\\*.esm", L"Data\\*.esp"}) {
        WIN32_FIND_DATAW fd;
        HANDLE h = FindFirstFileW((g_dir + pat).c_str(), &fd);
        if (h == INVALID_HANDLE_VALUE) continue;
        do {
            char nm[MAX_PATH * 3] = {};
            WideCharToMultiByte(CP_ACP, 0, fd.cFileName, -1, nm, sizeof(nm), nullptr, nullptr);
            files.push_back({nm, pat[5] == L'e' && pat[6] == L's' && pat[7] == L'm', ((unsigned long long)fd.ftLastWriteTime.dwHighDateTime << 32) | fd.ftLastWriteTime.dwLowDateTime});
        } while (FindNextFileW(h, &fd));
        FindClose(h);
    }
    if (files.size() > 1) {
        std::sort(files.begin(), files.end(), [](const F& a, const F& b) { return a.esm != b.esm ? a.esm : a.t < b.t; });
        LoadOrder lo;
        lo.source = "Data\\ file times (server fallback)";
        lo.guessed = true;
        lo.plugins.push_back("FalloutNV.esm");
        for (const auto& f : files) {
            bool dup = false;
            for (const auto& p : lo.plugins) if (_stricmp(p.c_str(), f.name.c_str()) == 0) dup = true;
            if (!dup) lo.plugins.push_back(f.name);
        }
        return lo;
    }
    return CanonicalAssumedOrder();
}

void Init() {
    InitializeCriticalSection(&g_logCs);
    if (!IsStoryServerExe(&g_dir)) return;  // refuse to hook anything else (silently: no log in foreign dirs)
    g_log = _wfopen((g_dir + L"server_hook.log").c_str(), L"ab");
    Log("==== nvmp_server_hook loaded (build %s %s) ====", __DATE__, __TIME__);

    bool ok = false;
    std::string cfgText = ReadWholeFile(g_dir + L"server_hook.cfg", &ok);
    std::vector<std::string> warns;
    if (ok) g_cfg = ParseConfig(cfgText, &warns);
    for (auto& w : warns) Log("cfg warning: %s", w.c_str());
    Log("config%s: enabled=%d block_handover=%d drop_actors=%d drop_references=%d host_mode=%d host_addresses=%zu poll_ms=%u log_all=%d",
        ok ? "" : " (defaults, no server_hook.cfg)", g_cfg.enabled, g_cfg.blockHandover, g_cfg.dropActors, g_cfg.dropReferences, (int)g_cfg.mode,
        g_cfg.hostAddresses.size(), g_cfg.pollMs, g_cfg.logAll);
    if (!g_cfg.enabled) { Log("disabled by config: no hooks installed"); return; }

    {
        LoadOrder lo = DiscoverLoadOrder();
        Log("load order (%s%s): %zu plugins", lo.source.c_str(), lo.guessed ? ", GUESSED" : "", lo.plugins.size());
        for (size_t i = 0; i < lo.plugins.size(); ++i) Log("  %02zX = %s", i, lo.plugins[i].c_str());
        if (lo.guessed) Log("  !!!! WARNING: load order is guessed; if FormID high bytes look wrong, put hostowned_loadorder.txt (one plugin per line) next to the server");
        g_store.SetLoadOrder(std::move(lo));
    }

    uint8_t* base = (uint8_t*)GetModuleHandleW(nullptr);
    std::vector<SectionInfo> secs;
    uint32_t soi = 0;
    if (!ParseSections(base, 0x1000, secs, nullptr, &soi) || !soi) { Log("cannot parse server PE headers"); return; }

    ResolvedSites rs;
    std::vector<std::string> rlog;
    bool haveEnc = ResolveSites(base, soi, (uintptr_t)base, rs, rlog);
    for (auto& l : rlog) Log("%s", l.c_str());
    if (!haveEnc) { Log("ABORT: encounter handler not found; nothing installed"); return; }
    g_charMgrGlobal = rs.charMgrGlobal;
    if (!g_charMgrGlobal) Log("note: character manager not found; 'first connected character' fallback disabled");
    if (rs.addrFnRva) g_addrFn = (AddrFmtFn)(base + rs.addrFnRva);
    g_connRva = rs.connRva;

    // install: connection (optional) and reselect (optional) first; encounter last so a partial failure never
    // leaves only half the policy active
    if (g_connRva && g_addrFn) {
        Pattern p;
        ParsePattern(kConn.sig, p);
        HookStatus st = InstallInlineHook(g_hConn, base + g_connRva, p.bytes.data(), kConn.copyLen, (const void*)&ConnectStub);
        if (st == HookStatus::Ok) { g_connTramp = g_hConn.tramp; Log("HOOK %-18s installed at 0x%p (rva 0x%X)", kConn.name, (void*)g_hConn.target, g_connRva); }
        else Log("HOOK %-18s FAIL (%s)", kConn.name, ToString(st));
    }
    if (!g_hConn.installed) Log("note: connection hook not installed; host falls back to 'first connected character'");
    if (rs.reselRva) {
        Pattern p;
        ParsePattern(kResel.sig, p);
        HookStatus st = InstallInlineHook(g_hResel, base + rs.reselRva, p.bytes.data(), kResel.copyLen, (const void*)&ReselectStub);
        if (st == HookStatus::Ok) { g_reselTramp = g_hResel.tramp; Log("HOOK %-18s installed at 0x%p (rva 0x%X)", kResel.name, (void*)g_hResel.target, rs.reselRva); }
        else Log("HOOK %-18s FAIL (%s)", kResel.name, ToString(st));
    }
    if (!g_hResel.installed) Log("note: reselect hook not installed; hand-over to guests is not blocked");
    {
        Pattern p;
        ParsePattern(kEnc.sig, p);
        HookStatus st = InstallInlineHook(g_hEnc, base + rs.encRva, p.bytes.data(), kEnc.copyLen, (const void*)&EncounterStub);
        if (st != HookStatus::Ok) { Log("ABORT: encounter hook failed (%s)", ToString(st)); return; }
        g_encTramp = g_hEnc.tramp;
        Log("HOOK %-18s installed at 0x%p (rva 0x%X)", kEnc.name, (void*)g_hEnc.target, rs.encRva);
    }

    CreateThread(nullptr, 0, PollThread, nullptr, 0, nullptr);
    Log("ready");
}
}  // namespace

BOOL APIENTRY DllMain(HMODULE h, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        DisableThreadLibraryCalls(h);
        Init();
    } else if (reason == DLL_PROCESS_DETACH) {
        InterlockedExchange(&g_stopPoll, 1);
        if (g_log) Log("unload: flagged encounters=%ld dropped=%ld allowed=%ld handovers blocked=%ld", g_nEncounterFlagged, g_nDropped,
                       g_nAllowedFlagged, g_nBlocked);
        // hooks are left in place on purpose: the process is exiting and a late write could race a running stub
    }
    return TRUE;
}
