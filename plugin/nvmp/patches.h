// Signature-guarded runtime patch table for NV:MP 9.4 (client.dll, nvmp_storyserver.exe).
// Every patch is located by a unique byte signature (sigscan.h), never by a hardcoded RVA, and its
// original bytes are verified before writing. Any doubt (0 or >1 signature matches, unexpected bytes)
// means NOTHING is written. Not loaded into the game by this lane; header-only deps (no windows.h
// here) so it compiles x86 anywhere. patches.cpp uses Win32 only for the protected writer / module lookup.
//
// Rule for signatures: the bytes a patch rewrites MUST be wildcards in the signature, otherwise the
// signature stops matching once the patch is applied (apply would no longer be idempotent and revert
// could not re-find the site). ValidateDef() enforces this.
#pragma once
#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

#include "sigscan.h"

namespace nvmp {

struct PatchDef {
    std::string name;
    std::string module;     // "client.dll" or "nvmp_storyserver.exe"
    std::string signature;  // IDA-style, unique within the module's executable sections
    size_t offset = 0;      // from signature match start to the first rewritten byte
    std::vector<uint8_t> expected;     // original bytes at the site
    std::vector<uint8_t> replacement;  // same length as expected; empty for dynamic patches until built
    bool defaultOn = false;            // recommended to apply by default
    std::string purpose;
    // Informational (verified against NV:MP 9.4 by tools/verify_sigs.py): RVA of the first rewritten byte.
    uint32_t rva94 = 0;
};

// All 9.4 patches. Order is application order.
const std::vector<PatchDef>& PatchTable();
const PatchDef* FindPatch(const std::string& name);

// Overlay text patch with its replacement built for a NUL-terminated ASCII string that must outlive
// the patch (x86 only: `mov eax, imm32 ; nop` -- the original `lea eax,[ebp-0x228]` is 6 bytes).
// Returns a def with empty replacement if the pointer does not fit in 32 bits.
PatchDef MakeOverlayTextPatch(const char* text);

// "" if the definition is internally consistent, otherwise a description of the problem.
std::string ValidateDef(const PatchDef& d);

enum class PatchStatus {
    Applied,
    AlreadyApplied,
    Reverted,
    NotApplied,        // revert of something never applied
    BadDefinition,
    SigNotFound,
    SigAmbiguous,
    ExpectedMismatch,  // signature unique but the bytes at the site are neither original nor patched
    RevertMismatch,    // site no longer holds our replacement; refuse to touch it
    WriteFailed,
    ModuleNotFound,
};
const char* ToString(PatchStatus s);

struct MemoryWriter {
    virtual ~MemoryWriter() = default;
    virtual bool Write(uint8_t* dst, const uint8_t* src, size_t n) = 0;
};
MemoryWriter& DirectWriter();     // plain memcpy (tests, already-writable buffers)
MemoryWriter& ProtectedWriter();  // VirtualProtect + write + restore + FlushInstructionCache (Windows)

struct PatchState {
    const PatchDef* def = nullptr;
    PatchDef owned;  // copy when applying a dynamically built def
    uint8_t* addr = nullptr;
    uint32_t rva = 0;
    std::vector<uint8_t> original;
    bool applied = false;
};

// `image` is a mapped PE (RVA layout), `imageSize` its SizeOfImage.
PatchStatus ApplyPatch(const PatchDef& d, uint8_t* image, size_t imageSize, MemoryWriter& w, PatchState& st);
PatchStatus RevertPatch(PatchState& st, MemoryWriter& w);

// Dry run: locate + verify only, nothing written. On Ok-ish result fills rva.
PatchStatus CheckPatch(const PatchDef& d, const uint8_t* image, size_t imageSize, uint32_t* rva = nullptr);

#ifdef _WIN32
// Look the module up with GetModuleHandleA (also tries "nvmp\\<module>") and apply in-process.
PatchStatus ApplyPatchInProcess(const PatchDef& d, PatchState& st);
#endif

}  // namespace nvmp
