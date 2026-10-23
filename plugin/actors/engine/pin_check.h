// Engine pins of the actor adapter (A4, 2026-10-22). Game-free: the verdict of a prologue comparison and the policy for WHEN to compare.
//
// The adapter calls three engine functions directly (Call_PlaceAtMe, Actor::GetActorValue, TESObjectREFR::IsDisabled) and only after the first
// bytes of the callee equal the bytes the address table pinned. In game 2026-10-21 the three checks refused on one copy ("pins placeAtMe=0
// getActorValue=0 isDisabled=0", the console fallback ran) and passed on the two-copy run. What could differ between two runs of the same exe:
//   * WHEN the bytes were read: the check ran once, at the first actor read, and the answer was kept for the rest of the process. A read before
//   * WHAT was there: a function another plugin (or our own hook lane) detoured begins with a jump (E9) and still forwards to the original;
//     getActorValue / isDisabled accepted that, placeAtMe did not.
// So the verdict now has a Detoured state for all three, a refused check is repeated (every kRetryMs, kMaxChecks times) until the pins hold,
// and every refusal logs the 16 bytes found next to the 16 expected, once per distinct reading.
#pragma once
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>

#include "fnvcoop_mut.h"

namespace nvcoop::actors::engine {

enum class PinState : uint8_t {
    Match,       // the pinned bytes
    Detoured,    // starts with a near jump (E9): somebody else's patch that forwards to the original; callable
    Mismatch,    // other bytes: a different build of the exe, an reference-too-early image, or a patch that does not forward
    Unreadable,  // the 16 bytes could not be read
};

inline bool pinOk(PinState s) { return s == PinState::Match || s == PinState::Detoured; }

inline PinState classifyPin(bool readable, const uint8_t* got, const uint8_t* expected) {
    if (!readable) return PinState::Unreadable;
    if (std::memcmp(got, expected, 16) == 0) return PinState::Match;
    if (got[0] == 0xE9 && !FNV_MUT(69)) return PinState::Detoured;   // MUTANT 69: a detoured function is refused (the old placeAtMe rule)
    return PinState::Mismatch;
}

inline std::string hex16(const uint8_t* b) {
    std::string s;
    char t[4];
    for (int i = 0; i < 16; ++i) {
        std::snprintf(t, sizeof t, "%02x", b[i]);
        s += t;
        if (i != 15) s += ' ';
    }
    return s;
}

// The log line of a refused pin: the name, the state, the address, what was read and what was pinned.
inline std::string pinRefusal(const char* name, uintptr_t addr, PinState st, const uint8_t* got, const uint8_t* expected) {
    char head[96];
    std::snprintf(head, sizeof head, "pin %s at %08X %s", name, static_cast<unsigned>(addr), st == PinState::Unreadable ? "UNREADABLE" : "REFUSED");
    std::string s = head;
    if (st != PinState::Unreadable) s += ": found " + hex16(got);
    s += " expected " + hex16(expected);
    return s;
}

// When to look again. A pin set that holds is final. One that does not is re-checked every kRetryMs, kMaxChecks times in all, so a read taken
// too early (or before another plugin's patch landed) never decides the whole session; after the last check the answer stands.
class PinPolicy {
public:
    static constexpr uint64_t kRetryMs = 3000;
    static constexpr int kMaxChecks = 20;

    bool due(uint64_t nowMs) const {
        if (final_) return false;
        if (checks_ == 0) return true;
        if (FNV_MUT(71)) return false;   // MUTANT 71: the first answer is final, whatever it was (the old behaviour)
        return nowMs - lastMs_ >= kRetryMs;
    }
    // allOk: every pin held on this check.
    void record(bool allOk, uint64_t nowMs) {
        ++checks_;
        lastMs_ = nowMs;
        if (allOk || checks_ >= kMaxChecks) final_ = true;
    }
    bool final() const { return final_; }
    int checks() const { return checks_; }

private:
    bool final_ = false;
    int checks_ = 0;
    uint64_t lastMs_ = 0;
};

}  // namespace nvcoop::actors::engine
