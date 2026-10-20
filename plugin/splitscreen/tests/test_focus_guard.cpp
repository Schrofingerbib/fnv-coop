// Game-free tests for the focus guard, on a real hidden Win32 window whose procedure stands in for the engine's.
// Prints "focus guard: N checks, M failed". plugin\splitscreen\run_tests.cmd builds it once per FOCUS_GUARD_MUTANT.
#include "../focus_guard.h"

#include <cstdio>
#include <vector>

namespace {
int g_checks = 0, g_failed = 0;
void Check(bool ok, const char* what, int line) {
    ++g_checks;
    if (!ok) {
        ++g_failed;
        std::printf("  FAIL line %d: %s\n", line, what);
    }
}
#define CHECK(x) Check((x), #x, __LINE__)

struct Seen { UINT msg; WPARAM wp; };
std::vector<Seen> g_seen;  // what the stand-in engine procedure received

// Stand-in for the engine procedure 0x86A0A0: records every message; WM_USER+1 returns 42 to prove return values pass.
LRESULT CALLBACK EngineProc(HWND h, UINT msg, WPARAM wp, LPARAM lp) {
    g_seen.push_back({msg, wp});
    if (msg == WM_USER + 1) return 42;
    if (msg == WM_ACTIVATE) return 0;
    return DefWindowProcA(h, msg, wp, lp);
}

int CountSeen(UINT msg, WPARAM wp) {
    int n = 0;
    for (const auto& s : g_seen) n += (s.msg == msg && s.wp == wp);
    return n;
}

HWND MakeWindow(const char* cls) {
    WNDCLASSA wc{};
    wc.lpfnWndProc = EngineProc;
    wc.hInstance = GetModuleHandleA(nullptr);
    wc.lpszClassName = cls;
    RegisterClassA(&wc);
    return CreateWindowExA(0, cls, "focus guard test", WS_OVERLAPPEDWINDOW, 0, 0, 64, 64, nullptr, nullptr, wc.hInstance, nullptr);
}
}  // namespace

int main() {
    using nvsplit::Action;
    using nvsplit::Decide;

    // ---- the policy table (matches what the engine's WM_ACTIVATE handler does with each value) ----
    CHECK(Decide(WM_ACTIVATE, MAKEWPARAM(WA_INACTIVE, 0)) == Action::Swallow);
    CHECK(Decide(WM_ACTIVATE, MAKEWPARAM(WA_INACTIVE, 1)) == Action::Swallow);  // deactivated while minimized
    CHECK(Decide(WM_ACTIVATE, MAKEWPARAM(WA_ACTIVE, 0)) == Action::Pass);
    CHECK(Decide(WM_ACTIVATE, MAKEWPARAM(WA_CLICKACTIVE, 0)) == Action::Pass);
    CHECK(Decide(WM_ACTIVATEAPP, FALSE) == Action::Pass);  // the engine leaves it to DefWindowProc
    CHECK(Decide(WM_SIZE, SIZE_MINIMIZED) == Action::Pass); // the engine returns 0 for WM_SIZE without acting
    CHECK(Decide(WM_KILLFOCUS, 0) == Action::Pass);

    // ---- a real window ----
    HWND h = MakeWindow("nvsplit_focus_test_a");
    CHECK(h != nullptr);
    CHECK(nvsplit::Installed() == nullptr);
    CHECK(nvsplit::Install(h));
    CHECK(nvsplit::Installed() == h);
    CHECK(nvsplit::Install(h));  // idempotent
    HWND other = MakeWindow("nvsplit_focus_test_b");
    CHECK(!nvsplit::Install(other));  // one game window per process

    g_seen.clear();
    LRESULT r = SendMessageA(h, WM_ACTIVATE, MAKEWPARAM(WA_INACTIVE, 0), 0);
    CHECK(r == 0);
    CHECK(CountSeen(WM_ACTIVATE, MAKEWPARAM(WA_INACTIVE, 0)) == 0);  // the engine never sees the deactivation
    CHECK(nvsplit::Swallowed() == 1);

    SendMessageA(h, WM_ACTIVATE, MAKEWPARAM(WA_ACTIVE, 0), 0);
    SendMessageA(h, WM_ACTIVATE, MAKEWPARAM(WA_CLICKACTIVE, 0), 0);
    CHECK(CountSeen(WM_ACTIVATE, MAKEWPARAM(WA_ACTIVE, 0)) == 1);  // activation still reaches the engine, exactly once
    CHECK(CountSeen(WM_ACTIVATE, MAKEWPARAM(WA_CLICKACTIVE, 0)) == 1);

    r = SendMessageA(h, WM_USER + 1, 7, 0);
    CHECK(r == 42);  // other messages reach the engine procedure and its return value comes back
    CHECK(CountSeen(WM_USER + 1, 7) == 1);

    SendMessageA(h, WM_ACTIVATE, MAKEWPARAM(WA_INACTIVE, 1), 0);
    CHECK(nvsplit::Swallowed() == 2);

    // ---- uninstall restores the original procedure ----
    CHECK(nvsplit::Uninstall());
    const bool restored = reinterpret_cast<WNDPROC>(GetWindowLongPtrA(h, GWLP_WNDPROC)) == &EngineProc;
    CHECK(restored);
    if (!restored) {  // the rest re-installs on this window; a guard left behind would chain to itself
        std::printf("focus guard: %d checks, %d failed (stopped: original procedure not restored)\n", g_checks, g_failed);
        return 1;
    }
    CHECK(nvsplit::Installed() == nullptr);
    g_seen.clear();
    SendMessageA(h, WM_ACTIVATE, MAKEWPARAM(WA_INACTIVE, 0), 0);
    CHECK(CountSeen(WM_ACTIVATE, MAKEWPARAM(WA_INACTIVE, 0)) == 1);
    CHECK(nvsplit::Swallowed() == 2);

    // ---- a second subclass on top: Uninstall must not cut it out ----
    CHECK(nvsplit::Install(h));
    static WNDPROC s_under = nullptr;
    s_under = reinterpret_cast<WNDPROC>(GetWindowLongPtrA(h, GWLP_WNDPROC));
    struct Top { static LRESULT CALLBACK Proc(HWND w, UINT m, WPARAM a, LPARAM b) { return CallWindowProcA(s_under, w, m, a, b); } };
    SetWindowLongPtrA(h, GWLP_WNDPROC, reinterpret_cast<LONG_PTR>(&Top::Proc));
    CHECK(!nvsplit::Uninstall());
    CHECK(reinterpret_cast<WNDPROC>(GetWindowLongPtrA(h, GWLP_WNDPROC)) == &Top::Proc);
    g_seen.clear();
    SendMessageA(h, WM_ACTIVATE, MAKEWPARAM(WA_INACTIVE, 0), 0);
    CHECK(CountSeen(WM_ACTIVATE, MAKEWPARAM(WA_INACTIVE, 0)) == 0);  // still guarded through the outer subclass

    // ---- bad input ----
    CHECK(!nvsplit::Install(nullptr));
    DestroyWindow(other);
    DestroyWindow(h);

    std::printf("focus guard: %d checks, %d failed\n", g_checks, g_failed);
    return g_failed ? 1 : 0;
}
