// Mutation twins: switch on one known bug class; the scenario that guards it must then report a violation.
#include "scenarios.h"
#include "minitest.h"

using namespace fnvcoop;
using namespace fnvcoop::engine;
using namespace fnvcoop::testing;

namespace {
LinkParams dupLink() {
    LinkParams l;
    l.minLatencyMs = 5; l.maxLatencyMs = 25; l.duplicate = 0.3; l.loss = 0.05;
    return l;
}
}  // namespace

#define OWN_TWIN(name, flag, scenario) \
    TEST(twin, name) { OwnershipFaults f; f.flag = true; { OwnershipFaultScope s(f); CHECK(!(scenario)); } }
#define ENGINE_TWIN(name, flag, scenario) \
    TEST(twin, name) { EngineFaults f; f.flag = true; { EngineFaultScope s(f); CHECK(!(scenario)); } }

OWN_TWIN(own_no_epoch, noEpochBump, churnHolds(1, 400))
OWN_TWIN(own_stale_epoch, acceptStaleEpoch, staleEchoRejected())
OWN_TWIN(own_adopt_echo, adoptEcho, staleEchoRejected())
OWN_TWIN(own_transfer_no_ack, transferNoAck, handoffKeepsLastState(1))
OWN_TWIN(own_no_hysteresis, noHysteresis, noFlap())
OWN_TWIN(own_follower_ignored, followerIgnored, followerStaysWithRecruiter())
OWN_TWIN(own_accept_non_owner, acceptNonOwner, churnHolds(1, 400))

ENGINE_TWIN(no_dedupe, noDedupe, questStageSync(1, dupLink()))
ENGINE_TWIN(hit_no_validation, hitNoValidation, forgedHitsRejected(1))
ENGINE_TWIN(hit_by_non_owner, hitAppliedByNonOwner, hitToNonOwnerRejected(1))
ENGINE_TWIN(pickup_no_arbitration, pickupNoArbitration, lootRace(1))
ENGINE_TWIN(pickup_no_rollback, noPickupRollback, lootRace(1))
ENGINE_TWIN(spawn_no_arbitration, spawnNoArbitration, spawnOnce(1))
ENGINE_TWIN(loot_per_machine, lootPerMachine, fightScenario(1, {}))
ENGINE_TWIN(pick_per_machine, pickPerMachine, spawnOnce(1))
ENGINE_TWIN(container_no_lock, containerNoLock, containerMatrix(1))
ENGINE_TWIN(named_killable, namedActorKillable, namedProtected(1))
ENGINE_TWIN(no_session_epoch, noSessionEpoch, saveLoadRejoin(1))
ENGINE_TWIN(heal_disabled, healDisabled, healsCorruption(1))
ENGINE_TWIN(drop_no_idempotency, dropNoIdempotency, dropIdempotent(1))
ENGINE_TWIN(join_no_ownership, joinNoOwnership, lateJoinerGetsOwnership(1))

// A former owner that never noticed it lost the actor keeps publishing; the receivers must reject it. The bug only
// bites when a receiver also applies old-epoch updates, so the twin combines the two switches.
TEST(twin, owner_keeps_publishing) {
    EngineFaults e; e.ownerKeepsPublishing = true;
    OwnershipFaults o; o.acceptStaleEpoch = true;
    EngineFaultScope es(e);
    OwnershipFaultScope os(o);
    CHECK(!formerOwnerSilenced(1));
}
