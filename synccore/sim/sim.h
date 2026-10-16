// Deterministic simulator: 1 host + N fake clients, virtual clock, lossy link, disconnect/rejoin, seeded.
// Invariant after quiescence: every client's world == an independent oracle of the committed txn stream,
// every client's ShadowState == the host's, no effect applied twice, no remote apply re-emitted (echo).
#pragma once
#include <cstdint>
#include <string>
#include <vector>
#include "fnvcoop/session.h"

namespace fnvcoop::sim {

struct SimConfig {
    uint64_t seed = 1;
    int clients = 3;                 // 1..7 (maxPlayers is set to clients+1 unless policy overrides)
    uint32_t durationMs = 6000;      // chaos phase length (virtual)
    uint32_t tickMs = 10;
    LinkParams link;                 // chaos-phase link
    double txnPerTick = 0.04;        // host script commits
    double actionPerTick = 0.01;     // client-local actions (per client)
    double disconnectPerTick = 0.002;
    double freshRestartProb = 0.25;  // on reconnect: lose all client state
    double compactPerTick = 0.001;
    int varWritesPerTick = 0;        // host quest-script var/global writes per tick (10 per 10 ms tick = 1000/s)
    double actionRequestPerTick = 0.01;  // guest ActivateRef/TriggerEnter/PickUp requests (per client)
    bool desync = false;             // clients digest their worlds; silent corruption is injected and must self-heal
    double corruptPerTick = 0.004;
    uint32_t digestMs = 600;
    bool hostLoads = false;          // host randomly loads an earlier save mid-session (needs baselineJoin)
    bool clientLoads = false;        // clients randomly load their own earlier saves
    double hostLoadPerTick = 0.003, clientLoadPerTick = 0.004;
    bool baselineJoin = false;       // host + clients start from DIVERGENT worlds (own saves); clients join late via baseline
    SharePolicy policy;
};

// Derives a varied but reproducible config from a seed (used by the 1000-seed property tests).
SimConfig configForSeed(uint64_t seed);

struct SimResult {
    bool ok = true;
    std::vector<std::string> violations;  // first few only
    size_t violationCount = 0;
    // stats
    uint64_t commits = 0, intents = 0, disconnects = 0, freshRestarts = 0, snapshots = 0, replays = 0,
             retransmitBatches = 0, duplicatesDropped = 0, deathScripts = 0, varWrites = 0, varEffects = 0, actionsExecuted = 0, injected = 0, digestsMismatch = 0, desyncRepairs = 0, desyncReports = 0, hostLoads = 0, clientLoads = 0, clientLoadsBlocked = 0, rebases = 0, staleEpochDropped = 0, baselineJoins = 0, baselineEffects = 0, baselineReverts = 0, baselineChunks = 0, msgsSent = 0, msgsLost = 0, msgsDup = 0, msgsCorrupt = 0,
             virtualMs = 0;
};

SimResult runSim(const SimConfig& cfg);

// Random content generators (also used by the wire fuzz tests).
Effect randomEffect(Rng& r, int maxPlayers);
Cause randomCause(Rng& r);

}  // namespace fnvcoop::sim
