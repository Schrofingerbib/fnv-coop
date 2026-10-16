// Inventory snapshots and deltas. Leveled lists and form lists resolve on the machine that rolls them: the host must send what
// ACTUALLY landed in the inventory, never the list (a client applying the list would roll its own item and diverge).
// Game-free; the engine glue fills Inventory from the reference's ExtraContainerChanges.
#pragma once
#include <cstdint>
#include <map>
#include <utility>
#include <vector>

namespace nvhooks {

using Inventory = std::map<uint32_t, int32_t>;  // item refID -> count (delta-style, as stored in the container changes)

// Items whose count increased: (item, +delta), ascending by item id.
std::vector<std::pair<uint32_t, int32_t>> inventoryGained(const Inventory& before, const Inventory& after);
// Items whose count decreased: (item, +removedCount).
std::vector<std::pair<uint32_t, int32_t>> inventoryLost(const Inventory& before, const Inventory& after);

// Form types that resolve to something else when added: TESLevItem 0x34, BGSListForm 0x55 (seen in Cmd_AddItem's type switch).
bool isResolvingItemType(unsigned typeId);

}  // namespace nvhooks
