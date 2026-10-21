// Script commands of the world-time lane that change who owns what. Read statically from the reference FalloutNV.exe 1.4.0.525
// (<reference exe>, the same image docs/engine-addresses.md uses); execute addresses come from the command table
// (plugin/hooks/tools/re/command_table.json). tests/world_code/owner_prologues.txt holds the bytes; test_world.cpp pins this header to it.
// NOT yet seen in a running game: the parameter lists the detours hand to ExtractArgs follow the GECK syntax and are exercised by
// plugin/world_time/tools/worldtime_proof.py. A hook whose prologue differs at run time is skipped, never forced.
#pragma once
#include <cstddef>

#include "hook_patch.h"

namespace world_owner_addrs {

// opcode 0x1117 (command table entry 0x11934A8), optional Owner form; stolen 11 = 55 8B EC 51 + the 7-byte `mov dword [ebp-4], 0`
inline constexpr nvhooks::HookSpec Cmd_SetOwnership = {"Cmd_SetOwnership", 0x5b57e0u, {0x55, 0x8b, 0xec, 0x51, 0xc7, 0x45, 0xfc, 0x00, 0x00, 0x00, 0x00, 0x8d, 0x45, 0xfc, 0x50, 0x8b}, 16, 11, 0};
// opcode 0x118C (entry 0x11946F0), no parameters; stolen 7 = 55 8B EC + `cmp dword [ebp+0x10], 0`
inline constexpr nvhooks::HookSpec Cmd_ClearOwnership = {"Cmd_ClearOwnership", 0x5d6d10u, {0x55, 0x8b, 0xec, 0x83, 0x7d, 0x10, 0x00, 0x74, 0x1e, 0x8b, 0x4d, 0x10, 0xe8, 0x9f, 0xd6, 0xff}, 16, 7, 0};
// opcode 0x1119 (entry 0x11934F8), (Cell, Owner)
inline constexpr nvhooks::HookSpec Cmd_SetCellOwnership = {"Cmd_SetCellOwnership", 0x5d17f0u, {0x55, 0x8b, 0xec, 0x83, 0xec, 0x08, 0xc7, 0x45, 0xfc, 0x00, 0x00, 0x00, 0x00, 0xc7, 0x45, 0xf8}, 16, 6, 0};
// opcode 0x10BB (entry 0x1192648), (Cell, int)
inline constexpr nvhooks::HookSpec Cmd_SetCellPublicFlag = {"Cmd_SetCellPublicFlag", 0x5c7660u, {0x55, 0x8b, 0xec, 0x83, 0xec, 0x08, 0xc7, 0x45, 0xfc, 0x00, 0x00, 0x00, 0x00, 0xc7, 0x45, 0xf8}, 16, 6, 0};

inline const nvhooks::HookSpec* const kAll[] = {&Cmd_SetOwnership, &Cmd_ClearOwnership, &Cmd_SetCellOwnership, &Cmd_SetCellPublicFlag};
inline constexpr size_t kCount = sizeof(kAll) / sizeof(kAll[0]);

}  // namespace world_owner_addrs
