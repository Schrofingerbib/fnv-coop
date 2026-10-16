#include "change_tracker.h"

namespace nvhooks {

#ifdef NVHOOKS_MUTANT
constexpr bool kMutAlwaysChanged = true;  // re-sends unchanged values every poll (bandwidth blow-up)
#else
constexpr bool kMutAlwaysChanged = false;
#endif

bool ChangeTracker::update(uint64_t key, double value) {
    auto it = last_.find(key);
    if (it == last_.end()) {
        last_.emplace(key, value);
        return true;
    }
    if (it->second == value && !kMutAlwaysChanged) return false;
    it->second = value;
    return true;
}

}  // namespace nvhooks
