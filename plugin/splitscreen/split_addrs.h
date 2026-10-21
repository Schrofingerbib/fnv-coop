#pragma once
// Engine addresses used by plugin/splitscreen (FalloutNV.exe 1.4.0.525), every one read from a reference exe (<reference exe>)
// and checked by plugin/splitscreen/tests/test_splitscreen.cpp (SPLIT_EXE=<exe>) and tools/splitscreen/re_pins.py.
// Nothing here is patched. The hooks are IAT patches found by walking the import table; these addresses only let the plugin
// confirm it runs on the exe they were read from, and read the gamepad-mode byte for split.status.
//
// Facts, with the disassembly they come from (docs/tools/splitscreen.md "Input facts"):
//   0x9F996E  jmp [0xFDF394]  XInputGetState thunk (XINPUT1_3.dll ordinal 2). Callers: 0x709FFB 0x70C525 0x7D4399 0xA2304C.
//   0xEE820A  jmp [0xFDF398]  XInputSetState thunk (ordinal 3). Callers: 0xA257AA 0xA25858 0xA26A39.
//   0xA23010  per-frame input update: copies the previous pad state, calls XInputGetState(0, 0x11F35A8), sets the byte 0x11F35C8 to
//             1 when the call returned 0 and [this] is 0, else to 0; then polls the DirectInput devices.
//   0xA24660  OSInputGlobals::GetControlState (also pinned by plugin/bots/bots_addrs.h): byte 0x11F35C8 != 0 reads the pad bindings only
//             (device type 3), == 0 reads devices 0 and 1 (keyboard, mouse) only.
#include <cstdint>

namespace nvsplit::addr {

inline constexpr uint32_t kImageBase = 0x400000;
inline constexpr uint32_t GamepadModeFlag = 0x11F35C8;  // byte; shared with plugin/bots/bots_addrs.h GamepadModeFlag
inline constexpr uint32_t XInputGetStateIatRva = 0xFDF394 - kImageBase;  // informational: the runtime finds the slot by name or value
inline constexpr uint32_t XInputSetStateIatRva = 0xFDF398 - kImageBase;

struct CodePin {
    const char* name;
    uint32_t addr;
    uint8_t bytes[16];
};

inline constexpr CodePin kPins[] = {
    {"PadPoll (per-frame input update)", 0xA23010, {0x55, 0x8B, 0xEC, 0x83, 0xEC, 0x0C, 0x89, 0x4D, 0xF4, 0xA1, 0xA8, 0x35, 0x1F, 0x01, 0xA3, 0xB8}},
    {"GetControlState", 0xA24660, {0x55, 0x8B, 0xEC, 0x83, 0xEC, 0x14, 0x89, 0x4D, 0xF0, 0x83, 0x7D, 0x08, 0x1C, 0x0F, 0x8D, 0x1F}},
    {"XInputGetState thunk", 0x9F996E, {0xFF, 0x25, 0x94, 0xF3, 0xFD, 0x00, 0xCC, 0xCC, 0xCC, 0xCC, 0xCC, 0xCC, 0xCC, 0xCC, 0xCC, 0xCC}},
    {"XInputSetState thunk", 0xEE820A, {0xFF, 0x25, 0x98, 0xF3, 0xFD, 0x00, 0xFF, 0x25, 0x74, 0xF1, 0xFD, 0x00, 0xCC, 0xCC, 0xCC, 0xCC}},
    {"set-active handler", 0x871C90, {0x55, 0x8B, 0xEC, 0x51, 0x89, 0x4D, 0xFC, 0xB9, 0xD8, 0xEE, 0x1D, 0x01, 0xE8, 0xBF, 0x70, 0xB9}},
};
inline constexpr int kPinCount = sizeof(kPins) / sizeof(kPins[0]);

}  // namespace nvsplit::addr
