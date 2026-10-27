// The INFO "said once" flag (TESTopicInfo + 0x22, the byte the snapshot dump reads as `saidOnce` and the dialogue module as kSaidOnce).
// A SayOnceFlag effect from the host used to be "unsupported effect" on a client: the client's INFO kept its own flag, its dump never held the
// host's 20 said-once INFOs, and the desync digest named all 20 'client (missing)' and resent them every round (two-copy door_terminal_container_matrix
// 2026-10-22: 43 digests, fast_travel_tether / pnr_vote / enemy_fight: +20 repaired effects). The engine itself sets the byte when an INFO is spoken, so
// writing it is what the effect means. Game-free so a test can run it on a byte buffer; RemoteApplier calls it with the real TESTopicInfo.
#pragma once
#include <cstdint>

#include "fnvcoop_mut.h"

namespace nvhooks {

constexpr uint32_t kInfoSaidOnceOffset = 0x22;

// True when the flag was off and is now on (the effect changed something); false when it was already set or `info` is null.
inline bool markInfoSaidOnce(void* info) {
    if (!info) return false;
    uint8_t* flag = reinterpret_cast<uint8_t*>(info) + kInfoSaidOnceOffset;
    if (FNV_MUT(13)) return false;   // twin 13: the effect is accepted and dropped (the client's INFO never learns it was said)
    if (*flag) return false;
    *flag = 1;
    return true;
}

}  // namespace nvhooks
