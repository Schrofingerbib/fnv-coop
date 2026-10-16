// Apply planning for world effects (game-free, unit-tested): turns an Effect into the list of engine steps a client must
// run. The glue (world_apply.cpp) only executes steps; every decision (which command, on which ref, dedupe, queueing) is here.
#pragma once
#include <functional>
#include <string>
#include <vector>

#include "world_core.h"

namespace nvworld {

enum class StepKind : uint8_t {
    ConsoleOnRef,   // run `line` with `refID` as the calling reference
    ConsoleGlobal,  // run `line` with no calling reference
    CreateSpawn,    // create `baseID` at `spawn` (cell/pos); the glue then calls SpawnLedger::onCreated(spawn.ref, newRefID)
    SetRefVar,      // write script variable `varIndex` of ref `refID`
};
struct Step {
    StepKind kind = StepKind::ConsoleGlobal;
    uint32_t refID = 0;
    std::string line;
    fnvcoop::SpawnRef spawn;
    uint32_t baseID = 0, cellID = 0;
    uint16_t varIndex = 0;
    double varValue = 0;
};

enum class PlanStatus : uint8_t { Ok, FormMissing, Queued, Unsupported };
struct Plan {
    PlanStatus status = PlanStatus::Ok;
    std::vector<Step> steps;
    std::string error;
};
const char* planStatusName(PlanStatus s);

struct PlanEnv {
    nvhooks::FormResolver& res;
    SpawnLedger& ledger;
    // Is the spawn's cell loaded on this machine right now? (Interiors: the player is inside it. Exteriors: it is in the grid.)
    std::function<bool(const fnvcoop::FormRef& cell)> cellReady;
};

// Effects of this lane: SpawnRef, DespawnRef, RefScriptVar. Anything else: Unsupported (L5's RemoteApplier owns it).
Plan planEffect(const fnvcoop::Effect& e, PlanEnv& env);
Plan planWorldEffect(const WorldEffect& e, PlanEnv& env);
// Spawns that were queued for a cell that has loaded since: one Plan per spawn, in arrival order.
std::vector<Plan> planQueuedSpawns(PlanEnv& env);

// True for effect types this lane applies.
bool handlesEffect(const fnvcoop::Effect& e);

}  // namespace nvworld
