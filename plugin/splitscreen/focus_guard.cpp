#include "focus_guard.h"

#include <atomic>

// Mutation twins for plugin\splitscreen\run_tests.cmd (each must make the tests fail):
//   1 = pass everything (the engine behaviour this guard exists to prevent)
//   2 = also swallow activation (the engine would never learn it is active again)
//   3 = swallow, but do not forward the other messages to the original procedure
//   4 = Uninstall reports success but leaves the guard in place
#ifndef FOCUS_GUARD_MUTANT
#define FOCUS_GUARD_MUTANT 0
#endif

namespace nvsplit {
namespace {
std::atomic<HWND> g_hwnd{nullptr};
std::atomic<WNDPROC> g_orig{nullptr};
std::atomic<unsigned long> g_swallowed{0};

LRESULT CALLBACK GuardProc(HWND h, UINT msg, WPARAM wp, LPARAM lp) {
    if (Decide(msg, wp) == Action::Swallow) {
        g_swallowed.fetch_add(1);
        return 0;
    }
    WNDPROC orig = g_orig.load();
#if FOCUS_GUARD_MUTANT == 3
    return DefWindowProcA(h, msg, wp, lp);
#endif
    return orig ? CallWindowProcA(orig, h, msg, wp, lp) : DefWindowProcA(h, msg, wp, lp);
}
}  // namespace

Action Decide(UINT msg, WPARAM wParam) {
#if FOCUS_GUARD_MUTANT == 1
    (void)msg; (void)wParam;
    return Action::Pass;
#else
    if (msg != WM_ACTIVATE) return Action::Pass;
#if FOCUS_GUARD_MUTANT == 2
    return Action::Swallow;
#else
    return LOWORD(wParam) == WA_INACTIVE ? Action::Swallow : Action::Pass;
#endif
#endif
}

bool Install(HWND h) {
    if (!h || !IsWindow(h)) return false;
    HWND cur = g_hwnd.load();
    if (cur == h) return true;
    if (cur && cur != h) return false;
    // Store the original first, so a message that arrives between the two calls below already finds it.
    WNDPROC before = reinterpret_cast<WNDPROC>(GetWindowLongPtrA(h, GWLP_WNDPROC));
    if (!before) return false;
    g_orig.store(before);
    SetLastError(0);
    LONG_PTR prev = SetWindowLongPtrA(h, GWLP_WNDPROC, reinterpret_cast<LONG_PTR>(&GuardProc));
    if (!prev && GetLastError() != 0) {
        g_orig.store(nullptr);
        return false;
    }
    // Someone may have subclassed the window between the read and the write: chain to what was really there.
    g_orig.store(reinterpret_cast<WNDPROC>(prev));
    g_hwnd.store(h);
    return true;
}

bool Uninstall() {
    HWND h = g_hwnd.load();
    if (!h) return false;
    if (!IsWindow(h)) {
        g_hwnd.store(nullptr);
        return false;
    }
    if (reinterpret_cast<WNDPROC>(GetWindowLongPtrA(h, GWLP_WNDPROC)) != &GuardProc) return false;  // another subclass on top
#if FOCUS_GUARD_MUTANT != 4
    SetWindowLongPtrA(h, GWLP_WNDPROC, reinterpret_cast<LONG_PTR>(g_orig.load()));
#endif
    g_hwnd.store(nullptr);
    return true;
}

HWND Installed() { return g_hwnd.load(); }
unsigned long Swallowed() { return g_swallowed.load(); }

}  // namespace nvsplit
