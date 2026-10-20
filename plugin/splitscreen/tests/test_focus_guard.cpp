// Game-free tests for the focus guard, on a real hidden Win32 window whose procedure stands in for the engine's.
// Prints "focus guard: N checks, M failed". plugin\splitscreen\run_tests.cmd builds it once per FOCUS_GUARD_MUTANT.
// The test build defines FOCUS_GUARD_TESTING (CMakeLists.txt), which gives the test-only hooks in focus_guard.h.
#include "../focus_guard.h"

#include <atomic>
#include <cstdio>
#include <thread>
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
HWND g_installedAtNcDestroy = nullptr;  // nvsplit::Installed() while the engine procedure handles WM_NCDESTROY

// Stand-in for the engine procedure 0x86A0A0: records every message; WM_USER+1 returns 42 to prove return values pass.
LRESULT CALLBACK EngineProc(HWND h, UINT msg, WPARAM wp, LPARAM lp) {
    g_seen.push_back({msg, wp});
    if (msg == WM_NCDESTROY) g_installedAtNcDestroy = nvsplit::Installed();
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

// Race test: the first Install calls this after it reads the old procedure and before it subclasses the window.
// It starts a second thread that installs on the same window, then waits. With the mutex, the second Install waits for the
// first one and then finds the window installed. Without it, the second Install subclasses the window inside this gap, and
// the first one then reads the guard back as "the original procedure".
std::thread g_racer;
std::atomic<bool> g_racerResult{false};
void RaceHook(HWND h) {
    nvsplit::test::SetInstallHook(nullptr);  // one call only: the second Install must not start a third
    g_racer = std::thread([h] { g_racerResult.store(nvsplit::Install(h)); });
    Sleep(100);
}

// Destroy race: while the guarded window A is in its WM_NCDESTROY cleanup, another thread uninstalls A and installs B.
// With the mutex the other thread waits for the cleanup and then installs B cleanly. Without it, the cleanup erases B's state.
std::thread g_destroyRacer;
HWND g_raceB = nullptr;
std::atomic<bool> g_destroyRacerInstalled{false};
void DestroyRaceHook(HWND) {
    nvsplit::test::SetDestroyHook(nullptr);
    std::thread([] {
        nvsplit::Uninstall();
        g_destroyRacerInstalled.store(nvsplit::Install(g_raceB));
    }).swap(g_destroyRacer);
    Sleep(100);
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
    // The first Install races a second Install on the same window from another thread (RaceHook).
    nvsplit::test::SetInstallHook(&RaceHook);
    CHECK(nvsplit::Install(h));
    const bool raced = g_racer.joinable();
    CHECK(raced);  // the hook ran, so the race really happened
    if (raced) g_racer.join();
    CHECK(g_racerResult.load());  // the second Install found the guard on h
    // Check the chain before any message goes through the window: a guard that chains to itself overflows the stack.
    const bool chainOk = !nvsplit::test::OrigIsGuard();
    CHECK(chainOk);
    if (!chainOk) {
        SetWindowLongPtrA(h, GWLP_WNDPROC, reinterpret_cast<LONG_PTR>(&EngineProc));  // no message can reach the loop now
        std::printf("focus guard: %d checks, %d failed (stopped: the guard chains to itself)\n", g_checks, g_failed);
        return 1;
    }
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

    // ---- the guarded window is destroyed (the game recreates its window): the guard forgets it, takes the new one ----
    g_seen.clear();
    g_installedAtNcDestroy = nullptr;
    DestroyWindow(h);  // through the outer subclass Top::Proc, then the guard
    CHECK(CountSeen(WM_NCDESTROY, 0) == 1);  // the engine procedure got WM_NCDESTROY
    CHECK(g_installedAtNcDestroy == h);      // ... before the guard forgot the window
    CHECK(nvsplit::Installed() == nullptr);
    HWND h2 = MakeWindow("nvsplit_focus_test_c");
    CHECK(h2 != nullptr);
    CHECK(nvsplit::Install(h2));
    CHECK(nvsplit::Installed() == h2);
    const unsigned long swallowedBefore = nvsplit::Swallowed();
    g_seen.clear();
    r = SendMessageA(h2, WM_ACTIVATE, MAKEWPARAM(WA_INACTIVE, 0), 0);
    CHECK(r == 0);
    CHECK(CountSeen(WM_ACTIVATE, MAKEWPARAM(WA_INACTIVE, 0)) == 0);  // swallowed on the new window
    CHECK(nvsplit::Swallowed() == swallowedBefore + 1);
    r = SendMessageA(h2, WM_USER + 1, 9, 0);
    CHECK(r == 42);  // the new window's own procedure is the one the guard chains to
    CHECK(CountSeen(WM_USER + 1, 9) == 1);
    g_seen.clear();
    DestroyWindow(h2);  // this time the guard is the top of the chain
    CHECK(CountSeen(WM_NCDESTROY, 0) == 1);
    CHECK(nvsplit::Installed() == nullptr);

    // ---- WM_NCDESTROY cleanup vs Uninstall + Install of another window on another thread ----
    HWND a = MakeWindow("nvsplit_focus_test_d");
    g_raceB = MakeWindow("nvsplit_focus_test_e");
    CHECK(a != nullptr && g_raceB != nullptr);
    CHECK(nvsplit::Install(a));
    nvsplit::test::SetDestroyHook(&DestroyRaceHook);
    DestroyWindow(a);
    if (g_destroyRacer.joinable()) g_destroyRacer.join();
    CHECK(g_destroyRacerInstalled.load());
    CHECK(nvsplit::Installed() == g_raceB);  // the cleanup of A must not erase B's state
    r = SendMessageA(g_raceB, WM_USER + 1, 11, 0);
    CHECK(r == 42);  // B's own procedure still gets its messages through the guard
    g_seen.clear();
    SendMessageA(g_raceB, WM_ACTIVATE, MAKEWPARAM(WA_INACTIVE, 0), 0);
    CHECK(CountSeen(WM_ACTIVATE, MAKEWPARAM(WA_INACTIVE, 0)) == 0);
    DestroyWindow(g_raceB);
    CHECK(nvsplit::Installed() == nullptr);

    std::printf("focus guard: %d checks, %d failed\n", g_checks, g_failed);
    return g_failed ? 1 : 0;
}
