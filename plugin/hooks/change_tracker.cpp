#include "change_tracker.h"

namespace nvhooks {

#ifdef NVHOOKS_MUTANT
constexpr bool kMutAlwaysChanged = true;  // re-sends unchanged values every poll (bandwidth blow-up)
constexpr bool kMutPrimeReports = true;   // the priming pass still reports every value (a baseline-less host floods the wire)
#else
constexpr bool kMutAlwaysChanged = false;
constexpr bool kMutPrimeReports = false;
#endif

bool ChangeTracker::update(uint64_t key, double value) {
    auto it = last_.find(key);
    if (it == last_.end()) {
        last_.emplace(key, value);
        return priming_ ? kMutPrimeReports : true;
    }
    if (it->second == value && !kMutAlwaysChanged) return false;
    it->second = value;
    return priming_ ? kMutPrimeReports : true;
}

}  // namespace nvhooks
