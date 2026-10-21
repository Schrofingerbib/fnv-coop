#pragma once
// Focus faking, part 2 (part 1 is focus_guard.h: WM_ACTIVATE(WA_INACTIVE) never reaches the engine).
//
// FalloutNV.exe 1.4.0.525 passes the result of USER32!GetActiveWindow() as the window handle of every DirectInput
// SetCooperativeLevel call (call sites 0xA22784, 0xA227A3, 0xA229AB, 0xA229CD, 0xA23C7E, 0xA23CAF, 0xA248C8 with the flags 0x16,
// 0x15, 6 and 5: all contain DISCL_FOREGROUND), and the set-active handler 0x871C90 (which calls the pad poll 0xA23010) runs those
// again. GetActiveWindow returns NULL on a thread whose window is not the active one, so a copy that started in the background
// (split-screen starts several copies and only one is in front) would call SetCooperativeLevel(NULL, ...), which fails; its
// keyboard and mouse never get set up. This policy answers the game window instead of NULL for the game thread, and nothing else:
// a non-NULL answer (a dialog the engine opened) is never replaced.
// Because every DirectInput level is FOREGROUND, a copy that is not the foreground window gets no keyboard or mouse data from the
// system, and the focused copy gets it all: that is the "KB/M meant for another window never arrives" rule (docs/tools/splitscreen.md).
#include <cstdint>

namespace nvsplit {

class IWindowQuery {
public:
    virtual ~IWindowQuery() = default;
    // First visible top-level window without an owner that belongs to thread `tid`; 0 if none.
    virtual uintptr_t topWindowOfThread(uint32_t tid) = 0;
};

// real = what the system returned. Returns what the game should see.
uintptr_t ResolveActiveWindow(bool enabled, uintptr_t real, uint32_t callerThread, IWindowQuery& q);

}  // namespace nvsplit
