// Connects the party-aware condition evaluators (party_cond.h / engine_hooks.cpp) to the rules engine: the per-member values the evaluators
// collect are reduced by RulesEngine::reduceCondition (initiator, any-party-member, min / max / sum-over-party, entry:all-in,exit:any-in).
//
// Header only on purpose: the instantiating translation unit (plugin/coop, tests) already links fnvcoop; the hooks library itself does not.
//
//   nvhooks::setPartyConditions(provider, nvhooks::makeRulesReducer(rules));   // `rules` must outlive the hooks
#pragma once
#include <string>
#include <vector>

#include "fnvcoop/rules.h"
#include "party_cond.h"

namespace nvhooks {

// rules.json spells the actor-value function both ways ("GetAV" 19x, "GetActorValue" 7x); the engine function is GetActorValue. Use the
// spelling this quest's PartyAwareCond rule actually lists (the engine name when it lists neither, i.e. the default "initiator" applies).
inline std::string ruleFuncName(const fnvcoop::RulesEngine& rules, const fnvcoop::FormRef& quest, const std::string& engineName) {
    if (engineName != "GetActorValue") return engineName;
    if (const fnvcoop::QuestRules* q = rules.rulesFor(quest)) {
        if (const fnvcoop::Rule* r = q->find("PartyAwareCond")) {
            auto has = [&](const char* n) {
                for (const auto& kv : r->funcs) {
                    std::string k = kv.first;
                    for (char& c : k) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
                    if (k == n) return true;
                }
                return false;
            };
            if (!has("getactorvalue") && has("getav")) return "GetAV";
        }
    }
    return engineName;
}

inline ConditionReducer makeRulesReducer(fnvcoop::RulesEngine& rules) {
    return [&rules](const fnvcoop::FormRef& quest, const std::string& func, const std::vector<CondValue>& vals, uint8_t initiator,
                    fnvcoop::CondPhase phase) -> double {
        std::vector<fnvcoop::PartyValue> pv;
        pv.reserve(vals.size());
        for (const CondValue& v : vals) pv.push_back({static_cast<fnvcoop::PlayerId>(v.slot), v.value});
#ifdef NVHOOKS_MUTANT
        phase = fnvcoop::CondPhase::Entry;  // mutant: a trigger that is already occupied is never held by a single member
#endif
        return rules.reduceCondition(quest, ruleFuncName(rules, quest, func), pv, static_cast<fnvcoop::PlayerId>(initiator), phase);
    };
}

}  // namespace nvhooks
