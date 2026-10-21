#include "focus_guard.h"

#include <atomic>
#include <mutex>

#include "fnvcoop_mut.h"  // FNV_MUT(n): runtime mutation twins (env FNVCOOP_MUT=n); constant false in production builds

// Runtime mutation twins (FNVCOOP_MUT=<n>, tools/mut/fnvcoop_mut.h) for plugin\splitscreen\run_tests.cmd (each must make the tests fail):
//   1 = pass everything (the engine behaviour this guard exists to prevent)
//   2 = also swallow activation (the engine would never learn it is active again)
//   3 = swallow, but do not forward the other messages to the original procedure
//   4 = Uninstall reports success but leaves the guard in place
//   5 = no reset on WM_NCDESTROY (the guard keeps the destroyed window and rejects the new one)
//   6 = no mutex in Install and Uninstall (two Install calls on one window make the guard chain to itself)
//   7 = WM_NCDESTROY cleanup without the mutex (an Uninstall + Install on another thread during it loses the new window)
namespace nvsplit {
namespace {
std::atomic<HWND> g_hwnd{nullptr};
std::atomic<WNDPROC> g_orig{nullptr};
std::atomic<unsigned long> g_swallowed{0};
std::mutex g_lock;  // held for the whole body of Install and Uninstall, and by GuardProc only for WM_NCDESTROY cleanup
#ifdef FOCUS_GUARD_TESTING
std::atomic<void (*)(HWND)> g_installHook{nullptr};
std::atomic<void (*)(HWND)> g_destroyHook{nullptr};
#endif

LRESULT CALLBACK GuardProc(HWND h, UINT msg, WPARAM wp, LPARAM lp) {
    if (Decide(msg, wp) == Action::Swallow) {
        g_swallowed.fetch_add(1);
        return 0;
    }
    WNDPROC orig = g_orig.load();
    if (FNV_MUT(3)) return DefWindowProcA(h, msg, wp, lp);
    const LRESULT r = orig ? CallWindowProcA(orig, h, msg, wp, lp) : DefWindowProcA(h, msg, wp, lp);
    if (!FNV_MUT(5) && msg == WM_NCDESTROY) {
        // The last message this window gets. The engine has seen it (above). Forget the window, so that Install accepts the
        // window the game makes next. Hold the mutex: otherwise an Uninstall + Install of another window on another thread
        // could run between the check and the stores, and this cleanup would then erase the new window's state. No deadlock:
        // Install and Uninstall send no messages to the window while they hold it (SetWindowLongPtr of GWLP_WNDPROC does not).
        std::unique_lock<std::mutex> lock(g_lock, std::defer_lock);
        if (!FNV_MUT(7)) lock.lock();
        if (g_hwnd.load() == h) {
#ifdef FOCUS_GUARD_TESTING
            if (auto hook = g_destroyHook.load()) hook(h);  // the race test runs Uninstall + Install on another thread here
#endif
            g_orig.store(nullptr);
            g_hwnd.store(nullptr);
        }
    }
    return r;
}
}  // namespace

Action Decide(UINT msg, WPARAM wParam) {
    if (FNV_MUT(1)) return Action::Pass;
    if (msg != WM_ACTIVATE) return Action::Pass;
    if (FNV_MUT(2)) return Action::Swallow;
    return LOWORD(wParam) == WA_INACTIVE ? Action::Swallow : Action::Pass;
}

bool Install(HWND h) {
    std::unique_lock<std::mutex> lock(g_lock, std::defer_lock);
    if (!FNV_MUT(6)) lock.lock();
    if (!h || !IsWindow(h)) return false;
    HWND cur = g_hwnd.load();
    if (cur == h) return true;
    if (cur && cur != h) return false;
    // Store the original first, so a message that arrives between the two calls below already finds it.
    WNDPROC before = reinterpret_cast<WNDPROC>(GetWindowLongPtrA(h, GWLP_WNDPROC));
    if (!before) return false;
    g_orig.store(before);
#ifdef FOCUS_GUARD_TESTING
    if (auto hook = g_installHook.load()) hook(h);  // the race test starts a second Install here
#endif
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
    std::unique_lock<std::mutex> lock(g_lock, std::defer_lock);
    if (!FNV_MUT(6)) lock.lock();
    HWND h = g_hwnd.load();
    if (!h) return false;
    if (!IsWindow(h)) {
        g_hwnd.store(nullptr);
        return false;
    }
    if (reinterpret_cast<WNDPROC>(GetWindowLongPtrA(h, GWLP_WNDPROC)) != &GuardProc) return false;  // another subclass on top
    if (!FNV_MUT(4)) SetWindowLongPtrA(h, GWLP_WNDPROC, reinterpret_cast<LONG_PTR>(g_orig.load()));
    g_hwnd.store(nullptr);
    return true;
}

HWND Installed() { return g_hwnd.load(); }
unsigned long Swallowed() { return g_swallowed.load(); }

#ifdef FOCUS_GUARD_TESTING
namespace test {
void SetInstallHook(void (*hook)(HWND)) { g_installHook.store(hook); }
void SetDestroyHook(void (*hook)(HWND)) { g_destroyHook.store(hook); }
bool OrigIsGuard() { return g_orig.load() == &GuardProc; }
}  // namespace test
#endif

}  // namespace nvsplit
