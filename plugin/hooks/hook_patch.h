// Entry-point detours with a fail-safe prologue check, ref-counted install/uninstall.
//
// Game-free: all memory access goes through MemoryOps, so the logic is unit-tested on a fake buffer
// (tests/test_hooks.cpp) and driven by Win32Memory inside the game. Addresses are 32-bit game addresses.
//
// Rules the patcher enforces (a violation => refuse, never write):
//   1. The first `expectedLen` bytes at `target` must equal `expected` (wrong exe version / another mod already
//      patched this function => PrologueMismatch / AlreadyHooked, zero bytes written).
//   2. `stolen` is 5..16, <= expectedLen, and the function must start with the standard frame prologue
//      (55 8B EC): we only hook function ENTRIES, so the stolen bytes never contain a rel32 branch that would need
//      relocating into the trampoline.
//   3. Trampoline = stolen bytes + `jmp target+stolen`. The detour calls the original through it.
#pragma once
#include <cstddef>
#include <cstdint>
#include <map>
#include <string>
#include <vector>

namespace nvhooks {

using Addr = uint32_t;

struct HookSpec {
    const char* name;
    Addr target;
    uint8_t expected[16];
    uint8_t expectedLen;
    uint8_t stolen;
    int16_t retBytes = -1;  // callee-popped stack bytes (`ret N`; 0 = cdecl/`ret`), -1 = unknown. Pinned against detour signatures.
};

struct MemoryOps {
    virtual ~MemoryOps() = default;
    virtual bool read(Addr a, void* out, size_t n) = 0;
    virtual bool write(Addr a, const void* in, size_t n) = 0;  // handles page protection / icache
    virtual Addr allocExec(size_t n) = 0;                      // 0 on failure
    virtual void freeExec(Addr a, size_t n) = 0;
};

enum class PatchStatus {
    Ok,                // newly installed
    AlreadyInstalled,  // our own hook, refcount incremented
    PrologueMismatch,  // bytes differ from the expected signature: nothing written
    AlreadyHooked,     // first byte is E9/E8 (somebody else detoured it): nothing written
    BadSpec,           // spec violates rule 2 (or a second detour for the same target)
    ReadFailed,
    WriteFailed,
    AllocFailed,
    NotInstalled,      // release() of an unknown hook
    Tampered,          // release(): bytes no longer ours, left untouched
    Released,          // refcount decremented, still installed
    Restored,          // last ref dropped, original bytes restored
};
const char* statusName(PatchStatus s);
inline bool installed(PatchStatus s) { return s == PatchStatus::Ok || s == PatchStatus::AlreadyInstalled; }

class HookRegistry {
public:
    explicit HookRegistry(MemoryOps& mem) : mem_(mem) {}
    ~HookRegistry();  // restores everything still installed
    // On success *trampolineOut is the address to call for the original function.
    PatchStatus acquire(const HookSpec& spec, Addr detour, Addr* trampolineOut);
    PatchStatus release(const HookSpec& spec);
    int refCount(const HookSpec& spec) const;
    size_t installedCount() const { return hooks_.size(); }
    std::string lastError() const { return lastError_; }

private:
    struct Installed {
        Addr detour = 0;
        Addr trampoline = 0;
        size_t trampSize = 0;
        uint8_t stolen = 0;
        uint8_t original[16] = {};
        uint8_t patch[16] = {};
        int refs = 0;
    };
    MemoryOps& mem_;
    std::map<Addr, Installed> hooks_;
    std::string lastError_;
};

// Validate a spec without touching memory (rule 2).
bool specIsValid(const HookSpec& s, std::string* why = nullptr);

}  // namespace nvhooks
