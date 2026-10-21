// live-core: schema v2 animation extension (one-shot action group + counter + dead / knocked / swim / talk / block / ragdoll bits).
// The scenarios live in scenarios.h so the mutation twins (test_twins.cpp) run the very same checks against a broken codec.
#include "minitest.h"
#include "scenarios.h"

using namespace live_test;

TEST(anim, schema_version_is_two) { CHECK(kLiveSchemaVersion == 2); }

TEST(anim, extension_round_trips_in_full_and_delta_snapshots) { CHECK(animExtRoundTrips()); }

TEST(anim, a_lone_one_shot_counter_bump_is_sent) { CHECK(animSeqOnlyChangeSent()); }

TEST(anim, a_one_shot_in_a_lost_datagram_arrives_with_the_next_one) { CHECK(animOneShotSurvivesLoss()); }

TEST(anim, decoder_accepts_only_canonical_forms) { CHECK(animExtMalformedRejected()); }

TEST(anim, no_extension_means_the_v1_wire_form) {
    // A snapshot whose extension is all zero is byte-identical in size to a v1 snapshot; with an extension it is exactly 3 bytes longer.
    Pair a, b;
    ActorSnapshot plain = mk(44, 1, 1, 1000, 1, 2, 3);
    plain.anim.locomotion = 3; plain.anim.weaponDrawn = true;
    ActorSnapshot ext = plain;
    ext.anim.action = 32; ext.anim.actionSeq = 1;
    a.tx.queue(kHost, plain, plain.tick);
    b.tx.queue(kHost, ext, ext.tick);
    auto pa = a.tx.flush(kHost, plain.tick), pb = b.tx.flush(kHost, ext.tick);
    REQUIRE(pa.size() == 1 && pb.size() == 1);
    CHECK(pb[0].size() == pa[0].size() + 3);
    // the flags byte of the plain one has no extension bit
    CHECK(animHasExt(plain.anim) == false && animHasExt(ext.anim) == true);
    CHECK((packAnimFlags(ext.anim) & kAnimFlagExt) == 0);
}

TEST(anim, action_counter_compare_wraps) {
    CHECK(actionSeqNewer(1, 2));
    CHECK(!actionSeqNewer(2, 1));
    CHECK(!actionSeqNewer(7, 7));
    CHECK(actionSeqNewer(255, 0));      // wrap
    CHECK(actionSeqNewer(250, 5));      // wrap
    CHECK(!actionSeqNewer(5, 250));
    CHECK(actionSeqNewer(0, 127));
    CHECK(!actionSeqNewer(0, 128));     // exactly half the range is "older"
}

TEST(anim, interpolation_buffer_carries_the_extension_of_the_older_snapshot) {
    // The anim state is a zero order hold of the snapshot the render time has passed, so a one-shot shows at the same moment the
    // interpolated position reaches the snapshot that announced it.
    InterpBuffer ib;
    for (uint32_t k = 0; k < 6; ++k) {
        ActorSnapshot s = mk(45, 1, k + 1, 1000 + k * 50, k * 10.0f);
        if (k >= 3) { s.anim.action = 223; s.anim.actionSeq = 1; }
        ib.push(s, s.tick + 20);
    }
    // render time = now - 20 (offset) - 100 (delay)
    InterpOut early = ib.sample(1000 + 100 + 20 + 90);    // render at tick 1090: before the snapshot at 1150
    InterpOut late = ib.sample(1000 + 100 + 20 + 160);    // render at tick 1160: after it
    CHECK(early.anim.action == 0 && early.anim.actionSeq == 0);
    CHECK(late.anim.action == 223 && late.anim.actionSeq == 1);
}
