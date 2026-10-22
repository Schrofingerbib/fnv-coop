// Condition-function queries (world.query): which address to CALL for an engine condition evaluator. Game-free and unit-tested;
// world_hooks.cpp (queryCondition) only feeds it real memory reads and nvhooks::trampolineOfAny.
//
// Why this exists (Track A inbox, 2026-10-21): our own capture detours patch some evaluators (Cond_GetActorValue 0x59C4F0,
// Cond_GetItemCount 0x59D8E0, ...). Their live entry bytes then start with E9 (a jump into nvcoop.dll), and the old check
// "the evaluator must start with 55 8B EC" refused every such call, so world.query GetActorValue failed for every ref.
// Rule now: a clean prologue is called as is; a prologue that is OUR jump (the hook registry knows a trampoline for that address) is
// called through the TRAMPOLINE (the original function, and it does not trip our own capture detour); anything else is refused.
#pragma once
#include <cstddef>
#include <cstdint>
#include <functional>
#include <string>

#include "fnvcoop_mut.h"  // FNV_MUT(n): runtime mutation twins 43..46 live in world_query.cpp

namespace nvworld {

// world.actors {type}: does a reference of the player's cell belong in the list? 0x3A (REFR), 0x3B (Character) and 0x3C (Creature) are REFERENCE
// types, read from the reference itself; any other value is the BASE form type (0x1B container, 0x1C door). Only 0x3B used to be a reference type,
// so `type: 60` compared a creature reference's BASE type (TESCreature 0x2B) with 0x3C and listed nothing: spawn_proof counted [0, 0, 0] radroaches
// on every machine in the sweep of 2026-10-22 although the roll had made one.
inline bool actorTypeMatches(uint32_t want, uint32_t refType, uint32_t baseType) {
    const bool wantRef = FNV_MUT(52) ? want == 0x3B : (want >= 0x3A && want <= 0x3C);   // twin 52: only characters are a reference type
    return (wantRef ? refType : baseType) == want;
}

struct Evaluator {
    const char* name;
    uint32_t addr;     // evaluator = 4th field of the engine command table entry (table 0x118E8E0, stride 0x28)
    bool formParam;    // p1 is a form id that must reach the evaluator as a TESForm*
};

// Exact (case-sensitive) name lookup in the supported evaluator table; nullptr = unknown function.
const Evaluator* findEvaluator(const std::string& fn);
// The whole table (tests pin it against plugin/hooks/engine_addrs.h).
const Evaluator* evaluators(size_t* count);

enum class EvalCall {
    Refuse,      // nothing may be called (unreadable, foreign patch, unusable trampoline)
    Live,        // the live entry still has the original prologue: call it
    Trampoline,  // the live entry is our own detour: call the trampoline (original code)
};
struct EvalPlan {
    EvalCall kind = EvalCall::Refuse;
    uint32_t addr = 0;           // address to call; 0 when Refuse
    const char* why = "";        // short reason (logs, query failure text)
};

using ReadMem = std::function<bool(uint32_t addr, void* out, size_t n)>;
using TrampolineOf = std::function<uint32_t(uint32_t target)>;   // nvhooks::trampolineOfAny; 0 = not hooked by us

EvalPlan resolveEvaluator(const Evaluator& q, const ReadMem& read, const TrampolineOf& trampolineOf);

}  // namespace nvworld
