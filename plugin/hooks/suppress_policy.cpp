#include "suppress_policy.h"

#include "fnvcoop/authority.h"

namespace nvhooks {

#ifdef NVHOOKS_MUTANT
constexpr bool kMutIgnoreApplying = true;   // suppression not lifted while applying a host txn
constexpr bool kMutQuestGateOff = true;     // quest scripts run on guests
constexpr bool kMutIgnoreDepth = true;      // block even outside scripts (breaks loads / console)
#else
constexpr bool kMutIgnoreApplying = false;
constexpr bool kMutQuestGateOff = false;
constexpr bool kMutIgnoreDepth = false;
#endif

bool SuppressPolicy::skipScript(uint16_t scriptType, uint32_t scriptRefID) {
#ifndef NVHOOKS_MUTANT
    if (scriptRefID && localRun_.count(scriptRefID)) return false;  // GuestLocalRun: this quest script runs on every machine
#endif
    if (kMutQuestGateOff) return false;
    if (!guest_ || !questGate_ || scriptType != kScriptTypeQuest) return false;
    if (fnvcoop::questScriptsMayRun(fnvcoop::Role::Guest)) return false;  // L4 authority model (test-only override)
    if (applying_ > 0 && !kMutIgnoreApplying) return false;
    ++stats_.questScripts;
    return true;
}

bool SuppressPolicy::blockMutation() {
    if (!guest_) return false;
    if (applying_ > 0 && !kMutIgnoreApplying) return false;
    if (depth_ <= 0 && !kMutIgnoreDepth) return false;
    ++stats_.mutations;
    return true;
}

bool SuppressPolicy::blockResultScript() {
    if (!guest_) return false;
    if (applying_ > 0 && !kMutIgnoreApplying) return false;
    ++stats_.resultScripts;
    return true;
}

#ifdef NVHOOKS_MUTANT
constexpr bool kMutGateIgnoresBypass = true;  // deferred stage can never be re-issued (livelock)
#else
constexpr bool kMutGateIgnoresBypass = false;
#endif

bool StageGate::allow(uint32_t quest, uint32_t stage, bool applying) {
    if (!fn_) return true;
    if (applying) return true;
    if (bypass_ > 0 && !kMutGateIgnoresBypass) return true;
    if (fn_(quest, stage)) return true;
    ++deferred_;
    return false;
}

}  // namespace nvhooks
