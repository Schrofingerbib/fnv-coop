#pragma once
// Focus guard: keeps an unfocused or minimized game copy simulating (Track B, split-screen "focus faking").
//
// Why: the window procedure of FalloutNV.exe 1.4.0.525 (0x86A0A0, registered by WinMain 0x86A850) handles
// WM_ACTIVATE with LOWORD(wParam) == WA_INACTIVE during gameplay like this: it opens the pause menu (StartMenu, menu id
// 1013, via 0x709470) unless that menu or the loading menu is already open, then calls the "set active" handler
// (0x871C90) with false, and returns 0 without DefWindowProc. bAlwaysActive=1 only forces the active flag back on; it does
// not stop the pause menu. So a test copy that the plugin minimizes, or a split-screen copy that loses focus, stops its
// world (game time, movement, AI) while frames and console commands still run.
//
// The guard subclasses the game window and answers WM_ACTIVATE(WA_INACTIVE) itself with 0 (the engine's own return value
// for it), so the engine never sees the deactivation. Every other message goes to the engine unchanged.
// It is the only deactivation path in that procedure: it does not handle WM_ACTIVATEAPP, WM_SETFOCUS or WM_KILLFOCUS
// (they go to DefWindowProc), and WM_NCACTIVATE(FALSE) only writes a flag at 0x11DEEF4 that no code reads (one
// reference in the whole .text section, the write at 0x86A26F). Checked against a Steamless-unpacked 1.4.0.525 image.
// The subclass must live in a DLL that is never unloaded (the monolithic nvcoop.dll or the split-mode loader), otherwise a
// hot reload would leave the window procedure pointing into freed code.
//
// Window lifetime: the game can destroy its window and make a new one (tools/runner/winproc.py: the game recreates its
// window after the splash). When the guarded window gets WM_NCDESTROY, the guard forwards it to the original procedure
// first, then forgets the window and the original procedure. Install then accepts the new window. The guard does not use
// IsWindow for this, because Windows can give a destroyed window's handle value to a new window.
//
// Threads: Install and Uninstall hold one mutex for their whole body, so calls from different threads run one at a time.
// Without it, two Install calls on the same window could both subclass it, and the guard would then chain to itself.
// The guard's window procedure does not take the mutex. It reads only atomics, and on WM_NCDESTROY it clears them.
#include <windows.h>

namespace nvsplit {

enum class Action { Pass, Swallow };

// The whole policy, no side effects.
Action Decide(UINT msg, WPARAM wParam);

// Subclasses h once. Returns true when the guard is on h after the call (installed now or earlier).
// Returns false when the guard is already on another live window, or SetWindowLongPtr fails.
// Safe from any thread of the process: calls are serialized by the mutex (see "Threads" above).
bool Install(HWND h);

// Puts the original window procedure back if the guard is still the top of the chain. Returns true if it did.
// Holds the same mutex as Install.
bool Uninstall();

HWND Installed();
unsigned long Swallowed();  // WM_ACTIVATE(WA_INACTIVE) messages kept from the engine so far

#ifdef FOCUS_GUARD_TESTING
// Test-only hooks. The test build defines FOCUS_GUARD_TESTING. The plugin build does not, so these do not exist there.
namespace test {
// Install calls hook(h) after it reads the old window procedure and before it calls SetWindowLongPtrA. Null = no call.
void SetInstallHook(void (*hook)(HWND));
// True when the saved original procedure is the guard itself. The guard would then call itself until the stack overflows.
bool OrigIsGuard();
}  // namespace test
#endif

}  // namespace nvsplit
