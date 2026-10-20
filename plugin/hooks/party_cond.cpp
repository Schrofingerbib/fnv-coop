#include "party_cond.h"

namespace nvhooks {

const char* condFuncName(CondFunc f) {
    switch (f) {
        case CondFunc::GetDistance: return "GetDistance";
        case CondFunc::GetInCell: return "GetInCell";
        case CondFunc::GetInSameCell: return "GetInSameCell";
        case CondFunc::IsInCombat: return "IsInCombat";
        case CondFunc::GetDetected: return "GetDetected";
        case CondFunc::GetHealthPercentage: return "GetHealthPercentage";
        case CondFunc::GetItemCount: return "GetItemCount";
        case CondFunc::GetActorValue: return "GetActorValue";
        default: return "";
    }
}

bool param1IsReference(CondFunc f) {
    return f == CondFunc::GetDistance || f == CondFunc::GetInSameCell || f == CondFunc::GetDetected;
}

SwapPlan planSwap(CondFunc f, uint32_t thisObjRefID, uint32_t param1RefID) {
    SwapPlan p;
    p.thisObj = thisObjRefID == kHostPlayerRef;
    p.param1 = param1IsReference(f) && param1RefID == kHostPlayerRef;
    return p;
}

bool evaluateOverParty(CondFunc f, const std::vector<PartyMember>& members, const MemberEval& evalAs, const Reducer& reduce, uint8_t initiator,
                       double* reduced, std::vector<CondValue>* valuesOut) {
    std::vector<CondValue> vals;
    vals.reserve(members.size());
    for (const PartyMember& m : members) {
        double v = 0;
        if (evalAs(m, &v)) vals.push_back({m.slot, v});
    }
    if (vals.size() < 2) return false;
    if (valuesOut) *valuesOut = vals;
    *reduced = reduce ? reduce(condFuncName(f), vals, initiator) : vals.front().value;
    return true;
}

}  // namespace nvhooks
