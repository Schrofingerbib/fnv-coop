#include "hook_patch.h"

#include <cstring>

namespace nvhooks {

// NVHOOKS_MUTANT builds re-introduce classic bugs; tests/test_hooks.cpp must fail against them.
#ifdef NVHOOKS_MUTANT
constexpr bool kMutSkipPrologueCheck = true;  // patch without verifying the bytes
constexpr bool kMutRefcountIgnored = true;    // first release restores even if other users remain
#else
constexpr bool kMutSkipPrologueCheck = false;
constexpr bool kMutRefcountIgnored = false;
#endif

const char* statusName(PatchStatus s) {
    switch (s) {
        case PatchStatus::Ok: return "Ok";
        case PatchStatus::AlreadyInstalled: return "AlreadyInstalled";
        case PatchStatus::PrologueMismatch: return "PrologueMismatch";
        case PatchStatus::AlreadyHooked: return "AlreadyHooked";
        case PatchStatus::BadSpec: return "BadSpec";
        case PatchStatus::ReadFailed: return "ReadFailed";
        case PatchStatus::WriteFailed: return "WriteFailed";
        case PatchStatus::AllocFailed: return "AllocFailed";
        case PatchStatus::NotInstalled: return "NotInstalled";
        case PatchStatus::Tampered: return "Tampered";
        case PatchStatus::Released: return "Released";
        case PatchStatus::Restored: return "Restored";
    }
    return "?";
}

bool specIsValid(const HookSpec& s, std::string* why) {
    auto fail = [&](const char* m) {
        if (why) *why = m;
        return false;
    };
    if (!s.target) return fail("null target");
    if (s.stolen < 5 || s.stolen > 16) return fail("stolen must be 5..16");
    if (s.expectedLen < s.stolen || s.expectedLen > 16) return fail("expectedLen must cover stolen bytes");
    if (!(s.expected[0] == 0x55 && s.expected[1] == 0x8B && s.expected[2] == 0xEC))
        return fail("only standard-frame function entries (55 8B EC) may be hooked");
    return true;
}

static void putRel32(uint8_t* p, Addr from /*addr of the E9 byte*/, Addr to) {
    uint32_t rel = to - (from + 5);
    p[0] = 0xE9;
    std::memcpy(p + 1, &rel, 4);
}

PatchStatus HookRegistry::acquire(const HookSpec& spec, Addr detour, Addr* trampolineOut) {
    std::string why;
    if (!specIsValid(spec, &why)) {
        lastError_ = spec.name + std::string(": ") + why;
        return PatchStatus::BadSpec;
    }
    auto it = hooks_.find(spec.target);
    if (it != hooks_.end()) {
        if (it->second.detour != detour) {
            lastError_ = std::string(spec.name) + ": already hooked with another detour";
            return PatchStatus::BadSpec;
        }
        ++it->second.refs;
        if (trampolineOut) *trampolineOut = it->second.trampoline;
        return PatchStatus::AlreadyInstalled;
    }

    uint8_t cur[16] = {};
    if (!mem_.read(spec.target, cur, spec.expectedLen)) {
        lastError_ = std::string(spec.name) + ": read failed";
        return PatchStatus::ReadFailed;
    }
    if (!kMutSkipPrologueCheck && std::memcmp(cur, spec.expected, spec.expectedLen) != 0) {
        lastError_ = std::string(spec.name) + ": prologue mismatch (wrong exe version or already patched)";
        return (cur[0] == 0xE9 || cur[0] == 0xE8) ? PatchStatus::AlreadyHooked : PatchStatus::PrologueMismatch;
    }

    Installed h;
    h.detour = detour;
    h.stolen = spec.stolen;
    std::memcpy(h.original, cur, 16);
    h.trampSize = spec.stolen + 5u;
    h.trampoline = mem_.allocExec(h.trampSize);
    if (!h.trampoline) {
        lastError_ = std::string(spec.name) + ": trampoline alloc failed";
        return PatchStatus::AllocFailed;
    }

    // Build + write the trampoline first: if anything fails past this point the game code is still untouched.
    uint8_t tramp[32] = {};
    std::memcpy(tramp, cur, spec.stolen);
    putRel32(tramp + spec.stolen, h.trampoline + spec.stolen, spec.target + spec.stolen);
    if (!mem_.write(h.trampoline, tramp, h.trampSize)) {
        mem_.freeExec(h.trampoline, h.trampSize);
        lastError_ = std::string(spec.name) + ": trampoline write failed";
        return PatchStatus::WriteFailed;
    }

    std::memset(h.patch, 0x90, sizeof h.patch);
    putRel32(h.patch, spec.target, detour);
    if (!mem_.write(spec.target, h.patch, spec.stolen)) {
        mem_.freeExec(h.trampoline, h.trampSize);
        lastError_ = std::string(spec.name) + ": patch write failed";
        return PatchStatus::WriteFailed;
    }
    h.refs = 1;
    if (trampolineOut) *trampolineOut = h.trampoline;
    hooks_[spec.target] = h;
    return PatchStatus::Ok;
}

PatchStatus HookRegistry::release(const HookSpec& spec) {
    auto it = hooks_.find(spec.target);
    if (it == hooks_.end()) return PatchStatus::NotInstalled;
    Installed& h = it->second;
    if (!kMutRefcountIgnored && --h.refs > 0) return PatchStatus::Released;

    uint8_t cur[16] = {};
    bool ours = mem_.read(spec.target, cur, h.stolen) && std::memcmp(cur, h.patch, h.stolen) == 0;
    if (!ours) {
        // Another patcher chained over us. Restoring would clobber theirs and leave their jump pointing into a
        // freed trampoline, so keep the trampoline alive and leave the code alone.
        lastError_ = std::string(spec.name) + ": bytes changed since install; not restoring";
        hooks_.erase(it);
        return PatchStatus::Tampered;
    }
    if (!mem_.write(spec.target, h.original, h.stolen)) {
        lastError_ = std::string(spec.name) + ": restore failed";
        return PatchStatus::WriteFailed;
    }
    mem_.freeExec(h.trampoline, h.trampSize);
    hooks_.erase(it);
    return PatchStatus::Restored;
}

int HookRegistry::refCount(const HookSpec& spec) const {
    auto it = hooks_.find(spec.target);
    return it == hooks_.end() ? 0 : it->second.refs;
}

HookRegistry::~HookRegistry() {
    while (!hooks_.empty()) {
        auto it = hooks_.begin();
        it->second.refs = 1;
        HookSpec s{};
        s.target = it->first;
        s.name = "(dtor)";
        release(s);
    }
}

}  // namespace nvhooks
