// TEST-ONLY fault switches used by the mutation-twin tests. All false in production.
// Each flag re-introduces one classic bug; the matching test must then fail.
#pragma once
namespace fnvcoop {
struct Mutations {
    bool noEchoGuard = false;      // remote applies are captured as local actions again
    bool noIdempotency = false;    // re-delivered txns / intents are applied again
    bool stageLastWins = false;    // stage merge = overwrite instead of max
    bool flagsAnd = false;         // objective flag merge = AND instead of OR
    bool killsReplace = false;     // kill merge = replace instead of union
    bool applyOutOfOrder = false;  // apply txns as they arrive instead of in seq order
    bool slowHashing = false;           // digests hash via per-entry objects (the pre-optimization path)
    bool skipHandshake = false;         // the host accepts any build / rules / mod list
    bool chattyBaseline = false;        // pre-fix behaviour: every Hello re-mints the join job + Welcome, acks re-send whole windows
    bool healDisabled = false;          // desync is detected and reported but NOT repaired
    bool rulesDisabled = false;         // quest rules engine does nothing (no deferrals / recipient rewrites)
    bool barrierReleaseEarly = false;   // a barrier releases on the FIRST arrival
    bool snapshotFloodAfterBaseline = false;  // baseline joiner also applies the whole unfiltered snapshot diff
    bool noEpochBump = false;           // host load does not bump the world epoch (stale txns / no rebase)
    bool ignoreVolatile = false;        // volatile-keys list is not consulted
    bool baselineSkipped = false;       // joiner ignores the world baseline chunks (keeps its own divergent world)
    bool guestRunsQuestScripts = false;  // guests execute quest scripts themselves (divergent local var writes)
    bool failNotCompleted = false;     // FailQuest sets only the failed flag (not completed) - the old CompleteQuest-only capture
    bool sidecarLosesLedger = false;   // the client save sidecar forgets which rewards were delivered (restart re-delivers them)
    bool spawnResurrects = false;      // a replayed SpawnRef recreates a ref that was despawned
    bool stickyObjectsReset = false;   // destroyed / activate-once object flags can be un-set
    bool refInvNoClear = false;        // RemoveAllItems does not clear a ref inventory
    bool activeQuestShared = false;    // ForceActiveQuest (UI focus) is broadcast to everyone instead of the initiator
    bool ignoreFollowerCaps = false;   // recruits beyond the per-owner / party follower caps are accepted
    bool disconnectKeepsLock = false;  // a disconnecting player keeps their control lock (rejoin comes back locked)
    bool guestLocalReplicated = false; // GuestLocalRun quests are captured and replicated like any other script
    bool departedHoldBarrier = false;  // players who left still count as present (a barrier waits for them forever)
    bool noGuestSuppression = false;  // guests keep script-originated effects (ghost-death double fire)
};
inline Mutations& mutations() {
    static Mutations m;
    return m;
}
struct MutationScope {
    Mutations saved;
    explicit MutationScope(const Mutations& m) : saved(mutations()) { mutations() = m; }
    ~MutationScope() { mutations() = saved; }
};
}  // namespace fnvcoop
