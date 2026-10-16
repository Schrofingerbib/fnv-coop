#include "hookcore.h"

#include <cstring>

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

namespace nvmp::hook {

const char* ToString(HookStatus s) {
    switch (s) {
        case HookStatus::Ok: return "ok";
        case HookStatus::BadArgs: return "bad-args";
        case HookStatus::PrologueMismatch: return "prologue-mismatch";
        case HookStatus::AllocFailed: return "alloc-failed";
        case HookStatus::ProtectFailed: return "protect-failed";
        case HookStatus::AlreadyInstalled: return "already-installed";
    }
    return "?";
}

static void WriteJmp(uint8_t* at, const void* to) {
    at[0] = 0xE9;
    int32_t rel = (int32_t)((uintptr_t)to - ((uintptr_t)at + 5));
    std::memcpy(at + 1, &rel, 4);
}

HookStatus InstallInlineHook(InlineHook& h, uint8_t* target, const uint8_t* expected, size_t copyLen, const void* stub) {
    if (h.installed) return HookStatus::AlreadyInstalled;
    if (!target || !expected || !stub || copyLen < 5 || copyLen > sizeof(h.orig)) return HookStatus::BadArgs;
#ifndef HOOK_MUTANT_SKIP_PROLOGUE_CHECK
    if (std::memcmp(target, expected, copyLen) != 0) return HookStatus::PrologueMismatch;
#endif
    uint8_t* tramp = (uint8_t*)VirtualAlloc(nullptr, 32, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    if (!tramp) return HookStatus::AllocFailed;
    std::memcpy(tramp, target, copyLen);
    WriteJmp(tramp + copyLen, target + copyLen);

    DWORD old = 0;
    if (!VirtualProtect(target, copyLen, PAGE_EXECUTE_READWRITE, &old)) {
        VirtualFree(tramp, 0, MEM_RELEASE);
        return HookStatus::ProtectFailed;
    }
    std::memcpy(h.orig, target, copyLen);
    WriteJmp(target, stub);
    for (size_t i = 5; i < copyLen; ++i) target[i] = 0x90;
    DWORD tmp = 0;
    VirtualProtect(target, copyLen, old, &tmp);
    FlushInstructionCache(GetCurrentProcess(), target, copyLen);
    FlushInstructionCache(GetCurrentProcess(), tramp, copyLen + 5);

    h.target = target;
    h.tramp = tramp;
    h.len = copyLen;
    h.installed = true;
    return HookStatus::Ok;
}

bool RemoveInlineHook(InlineHook& h) {
    if (!h.installed) return false;
    DWORD old = 0;
    if (!VirtualProtect(h.target, h.len, PAGE_EXECUTE_READWRITE, &old)) return false;
    std::memcpy(h.target, h.orig, h.len);
    DWORD tmp = 0;
    VirtualProtect(h.target, h.len, old, &tmp);
    FlushInstructionCache(GetCurrentProcess(), h.target, h.len);
    // the trampoline is intentionally leaked: another thread may still be executing inside it
    h.installed = false;
    return true;
}

}  // namespace nvmp::hook
