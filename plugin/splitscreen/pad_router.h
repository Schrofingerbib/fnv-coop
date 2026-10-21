#pragma once
// Per-copy gamepad routing: the game asks XInput for pad 0 only, and this copy answers with ITS physical pad (docs/tools/splitscreen.md).
//
// Engine facts (FalloutNV.exe 1.4.0.525, read from the disassembly, tools/splitscreen/re_pins.py checks the bytes):
//  * the per-frame input update 0xA23010 calls XInputGetState(0, &state) through the import thunk 0x9F996E (jmp [0xFDF394],
//    XINPUT1_3.dll ordinal 2). Only user index 0 is ever polled. Rumble goes through the thunk 0xEE820A (jmp [0xFDF398], ordinal 3).
//  * result 0 (and the byte at [this] zero) sets the "gamepad mode" byte 0x11F35C8 to 1. Any other result sets it to 0.
//  * OSInputGlobals::GetControlState (0xA24660) reads ONLY the pad bindings while that byte is 1, and only the keyboard and mouse
//    bindings while it is 0. That is the whole KB/M isolation: a copy with a pad "plugged in" ignores the keyboard for gameplay.
//  * the game does not import XInputGetCapabilities. The router still answers it (xNVSE plugins or a later exe could call it).
//
// Policy, for a copy with pad=N (config.pad >= 0), pad=none (-1) or no routing (-2):
//   -2        every call passes through unchanged.
//   user 0    (or XUSER_INDEX_ANY 0xFF)  -> the physical pad N. If it is unplugged: with ghostPad a neutral CONNECTED pad (so the engine
//             stays in gamepad mode and never reads KB/M), else ERROR_DEVICE_NOT_CONNECTED. pad=none behaves the same way.
//   user 1..3 -> ERROR_DEVICE_NOT_CONNECTED always: the other copies' pads must not show up here under another index.
//   Vibration follows the same mapping (to physical pad N, dropped for a ghost or none).
// The real XInput lives behind IXInput, so the policy is tested without a pad or a DLL.
#include <cstdint>

namespace nvsplit {

constexpr uint32_t kErrSuccess = 0;
constexpr uint32_t kErrInvalidParameter = 87;
constexpr uint32_t kErrNotConnected = 1167;
constexpr uint32_t kUserAny = 0xFF;

// Same layouts as XINPUT_STATE / XINPUT_CAPABILITIES / XINPUT_VIBRATION (static_assert below), so no xinput.h is needed.
#pragma pack(push, 1)
struct PadGamepad {
    uint16_t buttons;
    uint8_t leftTrigger, rightTrigger;
    int16_t thumbLX, thumbLY, thumbRX, thumbRY;
};
struct PadState {
    uint32_t packetNumber;
    PadGamepad gamepad;
};
struct PadVibration {
    uint16_t left, right;
};
struct PadCaps {
    uint8_t type, subType;
    uint16_t flags;
    PadGamepad gamepad;
    PadVibration vibration;
};
#pragma pack(pop)
static_assert(sizeof(PadState) == 16 && sizeof(PadVibration) == 4 && sizeof(PadCaps) == 20, "XInput layouts");

class IXInput {
public:
    virtual ~IXInput() = default;
    virtual uint32_t getState(uint32_t user, PadState* out) = 0;
    virtual uint32_t setState(uint32_t user, PadVibration* v) = 0;
    virtual uint32_t getCaps(uint32_t user, uint32_t flags, PadCaps* out) = 0;
};

struct RouteConfig {
    int pad = -2;  // -2 no routing, -1 none, 0..3 physical pad
    bool ghostPad = false;
};

struct RouterStats {
    uint64_t statePolls = 0;        // virtual pad 0 polls
    uint64_t physicalPolls[4] = {};  // calls made to the real XInput per physical pad (state)
    uint64_t hidden = 0;            // calls for virtual pads 1..3 answered "not connected"
    uint64_t ghost = 0;             // polls answered with the neutral pad
    uint64_t rumbleSent = 0;        // vibration calls forwarded to a physical pad
    uint64_t rumbleDropped = 0;
    uint32_t lastReturnedButtons = 0;  // what the game saw last time (for the smoke playbook)
    int32_t lastReturnedLX = 0;
    uint32_t lastResult = 0;
};

class PadRouter {
public:
    PadRouter(const RouteConfig& cfg, IXInput& real) : cfg_(cfg), real_(real) {}
    uint32_t getState(uint32_t user, PadState* out);
    uint32_t setState(uint32_t user, PadVibration* v);
    uint32_t getCaps(uint32_t user, uint32_t flags, PadCaps* out);
    const RouterStats& stats() const { return stats_; }
    const RouteConfig& config() const { return cfg_; }

private:
    bool virtualZero(uint32_t user) const { return user == 0 || user == kUserAny; }
    RouteConfig cfg_;
    IXInput& real_;
    RouterStats stats_;
};

PadState NeutralPad();
PadCaps NeutralCaps();

}  // namespace nvsplit
