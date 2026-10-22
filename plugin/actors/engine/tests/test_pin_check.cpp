// A4 (2026-10-22): the engine pins of the actor adapter. A function another patch detoured is callable, not refused; a refused pin is looked at again
// and logs the bytes it found; a pin set that holds is final. Twins: 69 (a detour is refused: the old placeAtMe rule), 71 (the first answer is
// final: the old once-per-process check).
#include <cstdint>
#include <cstring>
#include <string>

#include "actors/engine/pin_check.h"
#include "minitest.h"

using namespace nvcoop::actors::engine;

namespace {
const uint8_t kExpected[16] = {0x53, 0x8b, 0xdc, 0x83, 0xec, 0x08, 0x83, 0xe4, 0xf0, 0x83, 0xc4, 0x04, 0x55, 0x8b, 0x6b, 0x04};
}

TEST(pins, a_detoured_function_is_callable) {
    uint8_t got[16];
    std::memcpy(got, kExpected, 16);
    CHECK(classifyPin(true, got, kExpected) == PinState::Match);
    got[0] = 0xE9; got[1] = 0x10; got[2] = 0x20;                      // another plugin's patch: jmp rel32 that forwards to the original
    CHECK(classifyPin(true, got, kExpected) == PinState::Detoured);   // twin 69: refused
    CHECK(pinOk(classifyPin(true, got, kExpected)));
    got[0] = 0x90;                                                     // something else entirely
    CHECK(classifyPin(true, got, kExpected) == PinState::Mismatch && !pinOk(PinState::Mismatch));
    CHECK(classifyPin(false, got, kExpected) == PinState::Unreadable && !pinOk(PinState::Unreadable));
}

TEST(pins, a_refusal_logs_the_bytes_found_and_expected) {
    uint8_t got[16] = {0x90, 0x90, 0x90, 0x90, 0x90, 0x90, 0x90, 0x90, 0x90, 0x90, 0x90, 0x90, 0x90, 0x90, 0x90, 0x90};
    std::string s = pinRefusal("Call_PlaceAtMe", 0x5c4b30, PinState::Mismatch, got, kExpected);
    CHECK(s.find("Call_PlaceAtMe") != std::string::npos && s.find("005c4b30") == std::string::npos && s.find("005C4B30") != std::string::npos);
    CHECK(s.find("found 90 90 90 90 90 90 90 90 90 90 90 90 90 90 90 90") != std::string::npos);
    CHECK(s.find("expected 53 8b dc 83 ec 08 83 e4 f0 83 c4 04 55 8b 6b 04") != std::string::npos);
    CHECK(pinRefusal("x", 1, PinState::Unreadable, got, kExpected).find("UNREADABLE") != std::string::npos);
    CHECK(pinRefusal("x", 1, PinState::Unreadable, got, kExpected).find("found") == std::string::npos);
    CHECK(hex16(kExpected).size() == 16 * 3 - 1);
}

TEST(pins, a_refused_check_is_repeated_until_the_pins_hold) {
    PinPolicy p;
    CHECK(p.due(1000));                       // never checked: due at the first use
    p.record(false, 1000);                    // refused (an early read, or another plugin had not patched yet)
    CHECK(!p.final() && p.checks() == 1);
    CHECK(!p.due(1000 + PinPolicy::kRetryMs - 1));
    CHECK(p.due(1000 + PinPolicy::kRetryMs)); // twin 71: never due again
    p.record(true, 5000);                     // holds now
    CHECK(p.final() && !p.due(1000000));
}

TEST(pins, a_pin_set_that_never_holds_stops_after_the_last_check) {
    PinPolicy p;
    uint64_t now = 0;
    for (int i = 0; i < PinPolicy::kMaxChecks; ++i) {
        CHECK(p.due(now));
        p.record(false, now);
        now += PinPolicy::kRetryMs;
    }
    CHECK(p.final() && !p.due(now + 1000000) && p.checks() == PinPolicy::kMaxChecks);
}
