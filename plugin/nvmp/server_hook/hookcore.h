// Minimal x86 inline hook: verifies the expected prologue bytes, copies whole instructions into an
// executable trampoline, overwrites the first 5 bytes with `jmp stub` (rest NOP-filled).
// The caller guarantees the copied bytes contain no relative operands (checked by the site analysis in L6.md).
// Windows/x86 only.
#pragma once
#include <cstddef>
#include <cstdint>

namespace nvmp::hook {

enum class HookStatus { Ok, BadArgs, PrologueMismatch, AllocFailed, ProtectFailed, AlreadyInstalled };
const char* ToString(HookStatus s);

struct InlineHook {
    uint8_t* target = nullptr;
    uint8_t* tramp = nullptr;   // call/jmp this to run the original function
    uint8_t orig[16] = {};
    size_t len = 0;             // bytes copied (>= 5, <= 16): whole instructions
    bool installed = false;
};

// `expected` is compared against the target's first `copyLen` bytes; mismatch => nothing is written.
HookStatus InstallInlineHook(InlineHook& h, uint8_t* target, const uint8_t* expected, size_t copyLen, const void* stub);
bool RemoveInlineHook(InlineHook& h);

}  // namespace nvmp::hook
