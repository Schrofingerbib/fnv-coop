// Script commands whose PRESENTATION (image-space fades, music, sounds) a host-run script shows only on the host (G39). Read statically from the
// reference FalloutNV.exe 1.4.0.525 (<reference exe>, the same image docs/engine-addresses.md uses); execute addresses and parameter lists
// come from the command table (plugin/hooks/tools/re/command_table.json plus the CommandInfo entry of each command). tests/world_code/present_prologues.txt
// holds the bytes; test_world.cpp pins this header to it. NOT yet seen in a running game: a hook whose prologue differs at run time is skipped, never forced.
#pragma once
#include <cstddef>

#include "hook_patch.h"

namespace world_present_addrs {

// opcode 0x1191 (entry 0x11947B8), params (Imagespace Modifier ID, Float optional); no calling reference
inline constexpr nvhooks::HookSpec Cmd_ApplyImageSpaceModifier = {"Cmd_ApplyImageSpaceModifier", 0x5b76f0u, {0x55, 0x8b, 0xec, 0x83, 0xec, 0x08, 0xc7, 0x45, 0xf8, 0x00, 0x00, 0x00, 0x00, 0xd9, 0xe8, 0xd9}, 16, 6, 0};
// opcode 0x1192 (entry 0x11947E0), params (Imagespace Modifier ID, Float optional)
inline constexpr nvhooks::HookSpec Cmd_RemoveImageSpaceModifier = {"Cmd_RemoveImageSpaceModifier", 0x5b7760u, {0x55, 0x8b, 0xec, 0x83, 0xec, 0x08, 0xc7, 0x45, 0xf8, 0x00, 0x00, 0x00, 0x00, 0xd9, 0xe8, 0xd9}, 16, 6, 0};
// opcode 0x11FF (entry 0x11958E8), params (Music type)
inline constexpr nvhooks::HookSpec Cmd_PlayMusic = {"Cmd_PlayMusic", 0x5c24b0u, {0x55, 0x8b, 0xec, 0x51, 0x8d, 0x45, 0xfc, 0x50, 0x8b, 0x4d, 0x1c, 0x51, 0x8b, 0x55, 0x18, 0x52}, 16, 7, 0};
// opcode 0x1026 (entry 0x1190F00), params (Sound, Integer optional); the SEH prologue is `push -1; push handler` so 5 bytes end on an instruction boundary
inline constexpr nvhooks::HookSpec Cmd_PlaySound = {"Cmd_PlaySound", 0x5c21e0u, {0x55, 0x8b, 0xec, 0x6a, 0xff, 0x68, 0xd0, 0x92, 0xef, 0x00, 0x64, 0xa1, 0x00, 0x00, 0x00, 0x00}, 16, 5, 0};

inline const nvhooks::HookSpec* const kAll[] = {&Cmd_ApplyImageSpaceModifier, &Cmd_RemoveImageSpaceModifier, &Cmd_PlayMusic, &Cmd_PlaySound};
inline constexpr size_t kCount = sizeof(kAll) / sizeof(kAll[0]);

}  // namespace world_present_addrs
