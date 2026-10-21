#include "pad_router.h"

#include <cstring>

#include "fnvcoop_mut.h"

// Runtime mutation twins (FNVCOOP_MUT=<n>), numbered after split_config.cpp (8..11):
//   12 = virtual pad 0 always reads physical pad 0 (the pad number is ignored)
//   13 = virtual pads 1..3 pass through to the same physical index (the other copies' pads leak in)
//   14 = ghostPad ignored: an unplugged pad reports "not connected" and the engine falls back to KB/M
//   15 = vibration goes to the virtual index, not to the physical pad
//   16 = GetCapabilities is not routed
//   17 = a null output pointer is accepted (success, nothing written)
//   18 = pad=none reads physical pad 0
//   19 = the ghost pad carries the state of physical pad 0
//   20 = with no routing (pad unset) the virtual pads 1..3 are hidden
namespace nvsplit {

PadState NeutralPad() {
    PadState s;
    std::memset(&s, 0, sizeof s);
    return s;
}
PadCaps NeutralCaps() {
    PadCaps c;
    std::memset(&c, 0, sizeof c);
    c.type = 1;       // XINPUT_DEVTYPE_GAMEPAD
    c.subType = 1;    // XINPUT_DEVSUBTYPE_GAMEPAD
    c.flags = 0;
    c.gamepad.buttons = 0xF3FF;  // every button a standard pad has
    c.gamepad.leftTrigger = c.gamepad.rightTrigger = 0xFF;
    c.gamepad.thumbLX = c.gamepad.thumbLY = c.gamepad.thumbRX = c.gamepad.thumbRY = static_cast<int16_t>(-64);  // what a real pad reports
    c.vibration.left = c.vibration.right = 0xFF;
    return c;
}

uint32_t PadRouter::getState(uint32_t user, PadState* out) {
    if (!out && !FNV_MUT(17)) return kErrInvalidParameter;
    if (cfg_.pad == -2) {
        if (FNV_MUT(20) && !virtualZero(user)) { ++stats_.hidden; return kErrNotConnected; }
        return real_.getState(user, out);
    }
    if (!virtualZero(user)) {
        if (FNV_MUT(13) && user < 4) return real_.getState(user, out);
        ++stats_.hidden;
        return kErrNotConnected;
    }
    ++stats_.statePolls;
    if (!out) return kErrSuccess;  // twin 17 only
    int phys = cfg_.pad;
    if (FNV_MUT(12) && phys >= 0) phys = 0;
    if (FNV_MUT(18) && phys < 0) phys = 0;
    uint32_t r = kErrNotConnected;
    if (phys >= 0 && phys < 4) {
        ++stats_.physicalPolls[phys];
        r = real_.getState(static_cast<uint32_t>(phys), out);
    }
    if (r != kErrSuccess) {
        if (cfg_.ghostPad && !FNV_MUT(14)) {
            ++stats_.ghost;
            *out = NeutralPad();
            if (FNV_MUT(19)) real_.getState(0, out);
            r = kErrSuccess;
        } else {
            r = kErrNotConnected;
        }
    }
    stats_.lastResult = r;
    if (r == kErrSuccess) {
        stats_.lastReturnedButtons = out->gamepad.buttons;
        stats_.lastReturnedLX = out->gamepad.thumbLX;
    } else {
        stats_.lastReturnedButtons = 0;
        stats_.lastReturnedLX = 0;
    }
    return r;
}

uint32_t PadRouter::setState(uint32_t user, PadVibration* v) {
    if (cfg_.pad == -2) return real_.setState(user, v);
    if (!virtualZero(user)) return kErrNotConnected;
    if (cfg_.pad >= 0 && cfg_.pad < 4) {
        const uint32_t target = FNV_MUT(15) ? user : static_cast<uint32_t>(cfg_.pad);
        const uint32_t r = real_.setState(target, v);
        if (r == kErrSuccess) { ++stats_.rumbleSent; return r; }
        if (!cfg_.ghostPad) return r;
    } else if (!cfg_.ghostPad) {
        return kErrNotConnected;
    }
    ++stats_.rumbleDropped;  // a ghost or unplugged pad: the engine believes the pad is there, nothing shakes
    return kErrSuccess;
}

uint32_t PadRouter::getCaps(uint32_t user, uint32_t flags, PadCaps* out) {
    if (!out) return kErrInvalidParameter;
    if (cfg_.pad == -2) return real_.getCaps(user, flags, out);
    if (FNV_MUT(16)) return real_.getCaps(user, flags, out);
    if (!virtualZero(user)) return kErrNotConnected;
    if (cfg_.pad >= 0 && cfg_.pad < 4) {
        const uint32_t r = real_.getCaps(static_cast<uint32_t>(cfg_.pad), flags, out);
        if (r == kErrSuccess || !cfg_.ghostPad) return r;
    } else if (!cfg_.ghostPad) {
        return kErrNotConnected;
    }
    *out = NeutralCaps();
    return kErrSuccess;
}

}  // namespace nvsplit
