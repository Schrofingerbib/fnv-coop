#include "patches.h"

#include <cstring>

#ifdef _WIN32
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#endif

namespace nvmp {

// ---------------------------------------------------------------------------------------------
// Patch table, NV:MP 9.4 (client.dll sha256 e2b5c251..., nvmp_storyserver.exe 9.4 build).
// Every signature was verified to match exactly once by plugin/nvmp/tools/verify_sigs.py and by the
// unit test's real-binary check. Wildcards ("??") cover every rewritten byte (see patches.h).
// ---------------------------------------------------------------------------------------------
const std::vector<PatchDef>& PatchTable() {
    static const std::vector<PatchDef> t = [] {
        std::vector<PatchDef> v;
        {
            // FUN_100cc9e0 (NetEncounterManager "Encountered Actor"):
            //   call [vtable+0x94] ; test al,al ; je skip        <- 74 1D
            //   "Requesting localized, is quested" ; mov bl,1
            // je -> jmp : the "is quested" result is ignored, bl (localize flag) is never set by it.
            PatchDef d;
            d.name = "client.quested_not_localized";
            d.module = "client.dll";
            d.signature =
                "8B CF FF D6 84 C0 ?? ?? E8 ?? ?? ?? ?? 68 ?? ?? ?? ?? 6A 03 FF 35 ?? ?? ?? ?? 50 E8 ?? ?? ?? ??"
                " 83 C4 10 B3 01 80 7F 0F FF ?? ?? E8";
            d.offset = 6;
            d.expected = {0x74};
            d.replacement = {0xEB};
            d.defaultOn = true;
            d.rva94 = 0xCCAF6;
            d.purpose = "quest-flagged actors (vtable+0x94 IsQuestItem) are no longer force-localized; stay host-synced";
            v.push_back(d);
        }
        {
            // Same function: `cmp byte [edi+0xf],0xFF ; jne skip` = mod index 0xFF (runtime-created ref).
            PatchDef d;
            d.name = "client.scripted_ref_not_localized";
            d.module = "client.dll";
            d.signature =
                "B3 01 80 7F 0F FF ?? ?? E8 ?? ?? ?? ?? 68 ?? ?? ?? ?? 6A 03 FF 35 ?? ?? ?? ?? 50 E8 ?? ?? ?? ??"
                " 83 C4 10 B3 01 8B 85";
            d.offset = 6;
            d.expected = {0x75};
            d.replacement = {0xEB};
            d.defaultOn = false;  // runtime-created refs may have no stable id across machines: opt-in only
            d.rva94 = 0xCCB19;
            d.purpose = "mod-index-0xFF (script-spawned) refs are no longer force-localized";
            v.push_back(d);
        }
        {
            // "Encountered Reference" (0x100CD020): quest check on the ref (vtable+0x94) and on its base
            // object (0x10196B30 -> vtable+0x94); either one sets the byte at [ebp-0xA7D] that is sent with
            // the encounter. `jne SET` -> `jmp PAST_SET` skips both checks. (Same idea as patch 1, for
            // non-actor refs / containers / quest items.) The 2 bytes include the jne displacement.
            PatchDef d;
            d.name = "client.quested_ref_not_localized";
            d.module = "client.dll";
            d.signature =
                "8B CB FF D6 84 C0 ?? ?? 8B CB E8 ?? ?? ?? ?? 8B F8 8B 0F 8B B1 94 00 00 00 8B CE FF 15 ?? ?? ?? ??"
                " 8B CF FF D6 84 C0 74 07 C6 85";
            d.offset = 6;
            d.expected = {0x75, 0x21};
            d.replacement = {0xEB, 0x28};
            d.defaultOn = false;
            d.rva94 = 0xCD09E;
            d.purpose = "quest-flagged references (and refs whose base is quest-flagged) are not force-localized";
            v.push_back(d);
        }
        {
            // Reference candidate scan (0x100CDFC8): `vtable+0x94 ; test al,al ; jne SKIP` drops quest refs
            // before they are ever encountered. jne rel32 -> jo rel32 (never taken after `test`).
            PatchDef d;
            d.name = "client.quested_ref_scan_include";
            d.module = "client.dll";
            d.signature =
                "8B 07 8B B0 94 00 00 00 8B CE FF 15 ?? ?? ?? ?? 8B CF FF D6 84 C0 ?? ?? ?? ?? ?? ?? 68 ?? ?? ?? ??"
                " FF 15 ?? ?? ?? ?? 83 C4 04 85 C0";
            d.offset = 22;
            d.expected = {0x0F, 0x85};
            d.replacement = {0x0F, 0x80};
            d.defaultOn = false;
            d.rva94 = 0xCDFDE;
            d.purpose = "quest-flagged references are no longer excluded from the encounter candidate scan";
            v.push_back(d);
        }
        {
            // [RD] receive-damage validator (strings "[RD] victim %s is not local"). 9.4 layout:
            //   cmp byte [edi+0x1AE],0 ; jne NOT_LOCAL       (+7,  0F 85)
            //   mov eax,[edi+8] ; shr eax,31 ; test al,al ; je NOT_LOCAL   (+21, 0F 84)
            // jne/je rel32 -> jo rel32 (0F 80): OF is always 0 after cmp-with-0 / test, so never taken.
            PatchDef d;
            d.name = "client.ghost_damage_locality_byte";
            d.module = "client.dll";
            d.signature =
                "80 BF AE 01 00 00 00 ?? ?? ?? ?? ?? ?? 8B 47 08 C1 E8 1F 84 C0 ?? ?? ?? ?? ?? ??"
                " 8D 44 24 0C C7 44 24 0C ?? ?? ?? ?? 50 8B CF E8";
            d.offset = 7;
            d.expected = {0x0F, 0x85};
            d.replacement = {0x0F, 0x80};
            d.defaultOn = false;
            d.rva94 = 0x3E30C;
            d.purpose = "Companion ghost-damage port, part 1: victim byte +0x1AE != 0 (non-owner copy) no longer rejects received damage";
            v.push_back(d);
        }
        {
            PatchDef d;
            d.name = "client.ghost_damage_flag31";
            d.module = "client.dll";
            d.signature =
                "80 BF AE 01 00 00 00 ?? ?? ?? ?? ?? ?? 8B 47 08 C1 E8 1F 84 C0 ?? ?? ?? ?? ?? ??"
                " 8D 44 24 0C C7 44 24 0C ?? ?? ?? ?? 50 8B CF E8";
            d.offset = 21;
            d.expected = {0x0F, 0x84};
            d.replacement = {0x0F, 0x80};
            d.defaultOn = false;
            d.rva94 = 0x3E31A;
            d.purpose = "Companion 'ghost damage' port, part 2: victim [+8] bit31 clear no longer rejects damage";
            v.push_back(d);
        }
        {
            // Connected line: sprintf("Connected [%s][%s][%ums][%ukb][%ukb]%s", ...). The LAST %s arg is
            // pushed first: `lea eax,[ebp-0x228] ; add esp,0x14 ; push eax`. Rewrite the 6-byte lea into
            // `mov eax, imm32 ; nop` (imm32 filled by MakeOverlayTextPatch).
            PatchDef d;
            d.name = "client.overlay_text";
            d.module = "client.dll";
            d.signature =
                "8B 8D 98 FC FF FF ?? ?? ?? ?? ?? ?? 83 C4 14 50 8B 41 44 C1 E8 0A 50 8B 41 40 C1 E8 0A 50";
            d.offset = 6;
            d.expected = {0x8D, 0x85, 0xD8, 0xFD, 0xFF, 0xFF};
            d.replacement = {};  // dynamic
            d.defaultOn = false;
            d.rva94 = 0x3224B;
            d.purpose = "overlay 'Connected' line: replace trailing %s argument (needs MakeOverlayTextPatch)";
            v.push_back(d);
        }
        {
            // nvmp_storyserver.exe attack handler (function with "Actor is invalid, or not right type: %p [%s]
            // for attack message"): cmp edi,[eax] ; je OK ; "sender is not owned by the connecting message".
            PatchDef d;
            d.name = "server.attack_owner_bypass";
            d.module = "nvmp_storyserver.exe";
            d.signature =
                "3B 38 ?? ?? E8 ?? ?? ?? ?? 68 ?? ?? ?? ?? 6A 00 FF 35 ?? ?? ?? ?? 50 E8 ?? ?? ?? ??"
                " 83 C4 10 5F 5E 5B 8B E5 5D C2 04 00";
            d.offset = 2;
            d.expected = {0x74};
            d.replacement = {0xEB};
            d.defaultOn = false;
            d.rva94 = 0x9A212;
            d.purpose = "server accepts attacks on actors owned by another player (Companion ownership bypass port)";
            v.push_back(d);
        }
        return v;
    }();
    return t;
}

const PatchDef* FindPatch(const std::string& name) {
    for (const auto& p : PatchTable())
        if (p.name == name) return &p;
    return nullptr;
}

PatchDef MakeOverlayTextPatch(const char* text) {
    PatchDef d = *FindPatch("client.overlay_text");
    uintptr_t p = reinterpret_cast<uintptr_t>(text);
    if (p > 0xFFFFFFFFu) { d.replacement.clear(); return d; }
    uint32_t a = static_cast<uint32_t>(p);
    d.replacement = {0xB8, static_cast<uint8_t>(a), static_cast<uint8_t>(a >> 8), static_cast<uint8_t>(a >> 16),
                     static_cast<uint8_t>(a >> 24), 0x90};
    return d;
}

std::string ValidateDef(const PatchDef& d) {
    Pattern p;
    std::string err;
    if (!ParsePattern(d.signature, p, &err)) return "signature: " + err;
    if (d.expected.empty()) return "expected is empty";
    if (d.expected.size() != d.replacement.size()) return "expected/replacement length differ";
    if (d.expected == d.replacement) return "replacement equals expected (no-op patch)";
    if (d.offset + d.expected.size() > p.size()) return "patched bytes extend past the signature";
    for (size_t i = 0; i < d.expected.size(); ++i)
        if (p.mask[d.offset + i] != 0x00) return "patched byte is not a wildcard in the signature";
    return {};
}

const char* ToString(PatchStatus s) {
    switch (s) {
        case PatchStatus::Applied: return "applied";
        case PatchStatus::AlreadyApplied: return "already-applied";
        case PatchStatus::Reverted: return "reverted";
        case PatchStatus::NotApplied: return "not-applied";
        case PatchStatus::BadDefinition: return "bad-definition";
        case PatchStatus::SigNotFound: return "signature-not-found";
        case PatchStatus::SigAmbiguous: return "signature-ambiguous";
        case PatchStatus::ExpectedMismatch: return "expected-bytes-mismatch";
        case PatchStatus::RevertMismatch: return "revert-mismatch";
        case PatchStatus::WriteFailed: return "write-failed";
        case PatchStatus::ModuleNotFound: return "module-not-found";
    }
    return "?";
}

namespace {
struct Direct : MemoryWriter {
    bool Write(uint8_t* dst, const uint8_t* src, size_t n) override {
        std::memcpy(dst, src, n);
        return true;
    }
};
#ifdef _WIN32
struct Protected : MemoryWriter {
    bool Write(uint8_t* dst, const uint8_t* src, size_t n) override {
        DWORD old = 0;
        if (!VirtualProtect(dst, n, PAGE_EXECUTE_READWRITE, &old)) return false;
        std::memcpy(dst, src, n);
        DWORD tmp = 0;
        VirtualProtect(dst, n, old, &tmp);
        FlushInstructionCache(GetCurrentProcess(), dst, n);
        return true;
    }
};
#else
using Protected = Direct;
#endif

PatchStatus Locate(const PatchDef& d, const uint8_t* image, size_t imageSize, uint32_t& rva) {
    if (!ValidateDef(d).empty()) return PatchStatus::BadDefinition;
    ScanResult r = ScanImage(image, imageSize, d.signature);
    if (r.status == ScanStatus::NotFound) return PatchStatus::SigNotFound;
    if (r.status == ScanStatus::Ambiguous) return PatchStatus::SigAmbiguous;
    if (!r.ok()) return PatchStatus::BadDefinition;
    rva = static_cast<uint32_t>(r.offset + d.offset);
    return PatchStatus::Applied;  // "located"
}
}  // namespace

MemoryWriter& DirectWriter() { static Direct w; return w; }
MemoryWriter& ProtectedWriter() { static Protected w; return w; }

PatchStatus CheckPatch(const PatchDef& d, const uint8_t* image, size_t imageSize, uint32_t* rva) {
    uint32_t at = 0;
    PatchStatus s = Locate(d, image, imageSize, at);
    if (s != PatchStatus::Applied) return s;
    if (rva) *rva = at;
    if (at + d.expected.size() > imageSize) return PatchStatus::BadDefinition;
    const uint8_t* site = image + at;
    if (std::memcmp(site, d.replacement.data(), d.replacement.size()) == 0) return PatchStatus::AlreadyApplied;
    if (std::memcmp(site, d.expected.data(), d.expected.size()) == 0) return PatchStatus::Applied;
    return PatchStatus::ExpectedMismatch;
}

PatchStatus ApplyPatch(const PatchDef& d, uint8_t* image, size_t imageSize, MemoryWriter& w, PatchState& st) {
    uint32_t at = 0;
    PatchStatus s = Locate(d, image, imageSize, at);
    if (s != PatchStatus::Applied) return s;
    if (at + d.expected.size() > imageSize) return PatchStatus::BadDefinition;
    uint8_t* site = image + at;
    st = PatchState{};
    st.owned = d;
    st.def = &st.owned;
    st.addr = site;
    st.rva = at;
    st.original.assign(site, site + d.expected.size());
    if (std::memcmp(site, d.replacement.data(), d.replacement.size()) == 0) {
        st.applied = true;  // idempotent: remember so revert can still restore the documented original
        st.original = d.expected;
        return PatchStatus::AlreadyApplied;
    }
#ifndef PATCHES_MUTANT_SKIP_VERIFY
    if (std::memcmp(site, d.expected.data(), d.expected.size()) != 0) return PatchStatus::ExpectedMismatch;
#endif
    if (!w.Write(site, d.replacement.data(), d.replacement.size())) return PatchStatus::WriteFailed;
    st.applied = true;
    return PatchStatus::Applied;
}

PatchStatus RevertPatch(PatchState& st, MemoryWriter& w) {
    if (!st.applied) return PatchStatus::NotApplied;
    const auto& rep = st.owned.replacement;   // never st.def: a moved PatchState would leave it pointing at the old object
    if (std::memcmp(st.addr, rep.data(), rep.size()) != 0) return PatchStatus::RevertMismatch;
    if (!w.Write(st.addr, st.original.data(), st.original.size())) return PatchStatus::WriteFailed;
    st.applied = false;
    return PatchStatus::Reverted;
}

#ifdef _WIN32
PatchStatus ApplyPatchInProcess(const PatchDef& d, PatchState& st) {
    HMODULE h = GetModuleHandleA(d.module.c_str());
    if (!h) h = GetModuleHandleA(("nvmp\\" + d.module).c_str());
    if (!h) return PatchStatus::ModuleNotFound;
    uint8_t* base = reinterpret_cast<uint8_t*>(h);
    uint32_t soi = 0;
    std::vector<SectionInfo> secs;
    // headers are always readable in a loaded module
    if (!ParseSections(base, 0x1000, secs, nullptr, &soi) || soi == 0) return PatchStatus::BadDefinition;
    return ApplyPatch(d, base, soi, ProtectedWriter(), st);
}
#endif

}  // namespace nvmp
