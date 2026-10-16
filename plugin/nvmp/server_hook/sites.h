// Signature sites in nvmp_storyserver.exe 9.4 used by the hook (see internal lane notes, round 2).
// Every signature was verified unique against the real binary by plugin/nvmp/tools/verify_sigs.py (hook sites section).
#pragma once
#include <cstddef>

namespace nvmp::hook {

struct Site {
    const char* name;
    const char* sig;
    size_t copyLen;
    size_t strImm;          // offset of an imm32 pointer to a string inside the match (0 = none)
    const char* strPrefix;  // that string must start with this (disambiguates structural twins)
};
inline const Site kEnc = {"encounter handler",
                   "55 8B EC 83 E4 F8 83 EC 2C 8B 55 08 53 56 57 8B 52 04 83 EC 10 8B F4 8B D9 89 5C 24 24 8B 42 04", 6, 0, nullptr};
inline const Site kResel = {"hot-swap reselect",
                     "55 8B EC 6A FF 68 ?? ?? ?? ?? 64 A1 00 00 00 00 50 83 EC 2C 53 56 57 A1 ?? ?? ?? ?? 33 C5 50 8D 45 F4 64 A3 00 00 "
                     "00 00 8B F9 89 7D E8 80 BF AC 01 00 00 00",
                     5, 0, nullptr};
inline const Site kConn = {"connection accept",
                    "55 8B EC 80 B9 AC 00 00 00 00 8B 45 08 56 8B 70 08 75 ?? 6A 40 8D 46 18 68 ?? ?? ?? ?? 50 E8 ?? ?? ?? ?? 85 C0 B9 ?? ?? "
                    "?? ?? BE ?? ?? ?? ?? 0F 49 F1 E8 ?? ?? ?? ?? 56 68 ?? ?? ?? ?? 6A 02 FF 35",
                    10, 57, "New connection from"};
// The encounter handler calls the character lookup-by-id function at kEncCallsFindChar (E8 rel32). That function
// has a structural twin for another manager, so it is reached through this call, never by signature alone.
// Its `mov eax,[charMgrGlobal]` has the imm32 at +kFindCharGlobalImm.
inline constexpr size_t kEncCallsFindChar = 0x35;
inline constexpr size_t kFindCharGlobalImm = 61;
inline const char* const kFindCharCheck = "A1";  // opcode expected at kFindCharGlobalImm - 1

}  // namespace nvmp::hook
