// The opcode base nvcoop hands to NVSE in NVSEPlugin_Load (nvse->SetOpcodeBase).
//
// NVSE keys a plugin's cosave data by that base. A plugin that never calls SetOpcodeBase, or that passes the default 0x2000 (kNVSEOpcodeTest),
// has base 0 in a release NVSE, and Serialization.cpp (HandleSaveGame) then skips its save callback with "plugin has the default opcode base
// registered for serialization": no record is ever written. That is why the actor port's 'ACLG' cosave (plugin/actors/engine/engine_module.cpp)
// was missing from every save: the in-game actor_port_save_check read "cosave of the loaded game added 0 ledger entries" and the restrained NPC
// stayed frozen after the load (retest 2026-10-22; autosaveaclgcheck.nvse held only NVSE's own MODS / array / string sections).
// NVSE accepts a base in (0x2000, 0x8000). The value is only an id for the cosave here (nvcoop registers no script commands); it must not
// change between versions, or saves written by an older build lose their records.
#pragma once
#include <cstdint>

#include "fnvcoop_mut.h"

namespace nvcoop {

constexpr uint32_t kNvseDefaultOpcodeBase = 0x2000;   // NVSE's kNVSEOpcodeTest: refused as a serialization id in a release NVSE
constexpr uint32_t kNvseOpcodeLimit = 0x8000;         // NVSE asserts opcode < 0x8000
constexpr uint32_t kNvseOpcodeBase = 0x7F10;

constexpr bool opcodeBaseSerializes(uint32_t base) { return base > kNvseDefaultOpcodeBase && base < kNvseOpcodeLimit; }
static_assert(opcodeBaseSerializes(kNvseOpcodeBase), "nvcoop's opcode base must be one NVSE accepts as a serialization id");

// What NVSEPlugin_Load passes to SetOpcodeBase.
inline uint32_t opcodeBaseToRegister() { return FNV_MUT(14) ? kNvseDefaultOpcodeBase : kNvseOpcodeBase; }   // twin 14: the default base (no cosave record)

}  // namespace nvcoop
