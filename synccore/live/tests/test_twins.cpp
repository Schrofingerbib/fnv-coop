// Mutation twins: switch on one known bug class; the scenario that guards it must then report a violation.
#include "minitest.h"
#include "scenarios.h"

using namespace live_test;

#define LIVE_TWIN(name, flag, scenario) \
    TEST(twin, name) { LiveFaults f; f.flag = true; { LiveFaultScope s(f); CHECK(!(scenario)); } }

LIVE_TWIN(delta_from_latest, deltaFromLatest, deltaUsesAckedBaseline())
LIVE_TWIN(accept_stale_seq, acceptStaleSeq, staleSeqRejected())
LIVE_TWIN(accept_stale_epoch, acceptStaleEpoch, staleEpochRejected())
LIVE_TWIN(accept_non_owner, acceptNonOwner, wrongOwnerRejected())
LIVE_TWIN(accept_forged_future_epoch, acceptNonOwner, forgedFutureEpochRejected())
LIVE_TWIN(no_extrapolation_cap, noExtrapolationCap, extrapolationCapped())
LIVE_TWIN(extrapolate_only, extrapolateOnly, interpMidpoint())
LIVE_TWIN(no_snap, noSnap, teleportSnaps())
LIVE_TWIN(no_smooth, noSmooth, shoveIsSmoothed())
LIVE_TWIN(snap_ignores_cell, snapIgnoresCell, cellChangeSnaps())
LIVE_TWIN(near_rate_as_far, nearRateAsFar, ratesAre20And5())
LIVE_TWIN(no_far_tier, noFarTier, ratesAre20And5())
LIVE_TWIN(ack_ignored, ackIgnored, deltaIsSmall())
LIVE_TWIN(drop_av_changes, dropAvChanges, avSurvivesLoss())
LIVE_TWIN(no_resync_request, noResyncRequest, resyncAfterReceiverLoss())
// schema v2 animation extension (enemy-anim)
LIVE_TWIN(drop_anim_ext, dropAnimExt, animExtRoundTrips())
LIVE_TWIN(drop_anim_ext_one_shot, dropAnimExt, animOneShotSurvivesLoss())
LIVE_TWIN(anim_ext_no_delta, animExtNoDelta, animSeqOnlyChangeSent())
LIVE_TWIN(accept_bad_anim_ext, acceptBadAnimExt, animExtMalformedRejected())
