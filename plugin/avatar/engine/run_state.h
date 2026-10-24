// When the avatar runtime exists, and what the status line says while it does not (2026-10-22). Game-free; avatar_module.cpp calls it.
//
// The runtime is created on the first checked frame with a player in a loaded cell (a puppet template, a cell to place it in and a body to
// read the look from all need a world). A copy that sits at the main menu is therefore ENABLED but NOT RUNNING, and the live-wiring proof saw
// exactly that: {'enabled': True, 'running': False} on both machines of a session nobody had loaded a save into. That is the designed state, not a
// wiring fault, and the status now says so (`why`) instead of leaving the reader to guess.
#pragma once
#include <cstdint>

#include "fnvcoop_mut.h"

namespace nvcoop::avatar {

constexpr uint64_t kRuntimeCheckEveryFrames = 30;

// frames = frames counted since the module loaded (after the increment of this frame).
inline bool shouldCreateRuntime(bool enabled, bool haveBackend, bool haveRuntime, uint64_t frames, bool playerReady) {
    if (!enabled || !haveBackend || haveRuntime) return false;
    if (frames % kRuntimeCheckEveryFrames != 0) return false;
    if (FNV_MUT(72)) return true;   // twin 72: created without a player (the template and the cell do not exist yet)
    return playerReady;
}

inline const char* notRunningBecause(bool enabled, bool haveRuntime) {
    if (haveRuntime) return "";
    return enabled ? "waiting for a loaded game (the runtime starts on the first frame with a player in a cell)" : "[avatar] enabled=0";
}

}  // namespace nvcoop::avatar
