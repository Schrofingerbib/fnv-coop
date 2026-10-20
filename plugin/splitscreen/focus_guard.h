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
#include <windows.h>

namespace nvsplit {

enum class Action { Pass, Swallow };

// The whole policy, no side effects.
Action Decide(UINT msg, WPARAM wParam);

// Subclasses h once. Returns true when the guard is on h after the call (installed now or earlier).
// Returns false when the guard is already on another window, or SetWindowLongPtr fails. Safe from any thread of the process.
bool Install(HWND h);

// Puts the original window procedure back if the guard is still the top of the chain. Returns true if it did.
bool Uninstall();

HWND Installed();
unsigned long Swallowed();  // WM_ACTIVATE(WA_INACTIVE) messages kept from the engine so far

}  // namespace nvsplit
