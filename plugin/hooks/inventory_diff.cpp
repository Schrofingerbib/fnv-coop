#include "inventory_diff.h"

namespace nvhooks {

#ifdef NVHOOKS_MUTANT
constexpr bool kMutIgnoreLost = true;  // pretend nothing was ever removed
#else
constexpr bool kMutIgnoreLost = false;
#endif

std::vector<std::pair<uint32_t, int32_t>> inventoryGained(const Inventory& before, const Inventory& after) {
    std::vector<std::pair<uint32_t, int32_t>> out;
    for (const auto& [item, n] : after) {
        auto it = before.find(item);
        int32_t was = it == before.end() ? 0 : it->second;
        if (n > was) out.emplace_back(item, n - was);
    }
    return out;
}

std::vector<std::pair<uint32_t, int32_t>> inventoryLost(const Inventory& before, const Inventory& after) {
    std::vector<std::pair<uint32_t, int32_t>> out;
    if (kMutIgnoreLost) return out;
    for (const auto& [item, n] : before) {
        auto it = after.find(item);
        int32_t now = it == after.end() ? 0 : it->second;
        if (now < n) out.emplace_back(item, n - now);
    }
    return out;
}

bool isResolvingItemType(unsigned t) {
#ifdef NVHOOKS_MUTANT
    return t == 0x55;  // forgets leveled lists, the exact bug L7 found (VCG02 stage 40)
#else
    return t == 0x34 || t == 0x55;
#endif
}

}  // namespace nvhooks
