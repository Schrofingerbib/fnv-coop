// Player-ref condition evaluation for co-op (game-free logic; the detours live in engine_hooks.cpp).
//
// Condition evaluators (the per-function "eval" handlers of the command table: bool(thisObj, param1, param2, double* result), cdecl)
// are called with the local player (ref 0x14) as an operand. For party-aware functions the detour re-evaluates the SAME condition once per
// party member with that operand swapped for the member's ghost reference, and hands the per-member values to a reducer (L4
// RulesEngine::reduceCondition: initiator / any-party-member / min-over-party / entry:all-in,exit:any-in).
//
// Meaning of "player" per function (internal lane notes round 9):
//   spatial   GetDistance, GetInCell, GetInSameCell : any / min over the party (rules.json decides the reduction)
//   awareness IsInCombat, GetDetected, GetHealthPercentage : per member; rules.json decides (default: initiator)
//   inventory GetItemCount, GetActorValue : per member; rules.json decides (sum-over-party for pooled collections, max-over-party for
//             "the strongest member" checks, default: initiator)
#pragma once
#include <cstdint>
#include <functional>
#include <string>
#include <vector>

#include "fnvcoop/rules.h"

namespace nvhooks {

enum class CondFunc : uint8_t { GetDistance, GetInCell, GetInSameCell, IsInCombat, GetDetected, GetHealthPercentage, GetItemCount, GetActorValue, Count };
const char* condFuncName(CondFunc f);  // the name rules.json uses (lower-cased by the rules engine)

constexpr uint32_t kHostPlayerRef = 0x14;

struct PartyMember {
    uint8_t slot = 0;
    uint32_t refID = 0;  // 0x14 for the local player, the ghost reference of every other present member
};
struct CondValue {
    uint8_t slot = 0;
    double value = 0;
};

// Which operands of the call are "the player" and get swapped. param1 is only inspected for functions whose param1 is a reference
// (GetDistance, GetInSameCell, GetDetected); `param1RefID` must be 0 when param1 is not a form.
struct SwapPlan {
    bool thisObj = false;
    bool param1 = false;
    bool any() const { return thisObj || param1; }
};
SwapPlan planSwap(CondFunc f, uint32_t thisObjRefID, uint32_t param1RefID);
bool param1IsReference(CondFunc f);

// Evaluates the condition once per member. `evalAs(member, &value)` runs the original evaluator with the player operand(s) replaced by
// that member; false = member could not be evaluated (ghost not loaded) and is skipped. Returns false when fewer than two members were
// evaluated (caller then uses the plain engine result). Otherwise *reduced is the reducer's answer.
using MemberEval = std::function<bool(const PartyMember&, double* value)>;
using Reducer = std::function<double(const std::string& func, const std::vector<CondValue>&, uint8_t initiator)>;
// The reducer the evaluators call: RulesEngine::reduceCondition behind a thin adapter (rules_reducer.h). `phase` is Entry unless the caller
// opened a ConditionQuestScope with CondPhase::Exit (a trigger that is already occupied asks "is anybody still inside").
using ConditionReducer = std::function<double(const fnvcoop::FormRef& quest, const std::string& func, const std::vector<CondValue>&,
                                              uint8_t initiator, fnvcoop::CondPhase phase)>;

bool evaluateOverParty(CondFunc f, const std::vector<PartyMember>& members, const MemberEval& evalAs, const Reducer& reduce, uint8_t initiator,
                       double* reduced, std::vector<CondValue>* valuesOut = nullptr);

}  // namespace nvhooks
