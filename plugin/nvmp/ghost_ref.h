// Public API of the nvmp lane for other lanes (L10 coop runtime): which local actor reference is the ghost of a guest.
// Implemented in client_patches/client_patches_module.cpp (part of nvcoop.dll). Game main thread only.
//
// Slot model: slot 0 = the host (the local player, no ghost => 0). Slots 1.. are guests. A guest slot is resolved by
//   1. an explicit binding (BindSlotToNetId, or RPC "nvmp.bindslot" {"slot":1,"netId":11}), else
//   2. join order: the k-th NON-local NV:MP character in the client's character list (insertion = join order) that is not
//      bound to another slot.
// NV:MP exposes no account/steam id client-side; the stable key is the session net id (see RPC "nvmp.players"), plus the
// display name. Join order is reliable only if guests join one at a time and nobody leaves; for anything else bind explicitly
// (e.g. after the coop handshake: guest tells its NV:MP name, host looks it up in "nvmp.players" and binds the net id).
#pragma once
#include <cstdint>
#include <string>

namespace nvmp {

// refID of the guest's ghost actor in this (host) game, or 0 = unknown / NV:MP not loaded / ghost not spawned yet.
uint32_t GhostRefOfPlayer(uint8_t coopSlot);

// Explicit mapping; netId 0 removes the binding. Slot must be 1..255.
void BindSlotToNetId(uint8_t coopSlot, uint32_t netId);

// netId of the NV:MP character whose display name equals `name` (case-insensitive, non-local only); 0 if none.
uint32_t NetIdByName(const std::string& name);

}  // namespace nvmp
