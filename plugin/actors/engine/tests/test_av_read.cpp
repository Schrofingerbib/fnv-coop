// A5 (2026-10-22): which engine read of an actor value the adapter believes. Twin 58: the direct call wins.
#include "actors/engine/av_read.h"
#include "minitest.h"

using namespace nvcoop::actors::engine;

TEST(av_read, the_evaluator_includes_the_damage_the_direct_call_misses) {
    // the game's numbers: powder ganger 104C70, not loaded: console GetAV Health 40, avOwner slot 3 says 60
    AvReads r;
    r.evalOk = true; r.eval = 40;
    r.directOk = true; r.direct = 60;
    float v = -1;
    CHECK(pickActorValue(r, v));
    CHECK(v == 40.0f);   // twin 58: 60
}

TEST(av_read, a_loaded_actor_has_both_reads_agree) {
    AvReads r;
    r.evalOk = true; r.eval = 35;
    r.directOk = true; r.direct = 35;
    float v = 0;
    CHECK(pickActorValue(r, v) && v == 35.0f);
}

TEST(av_read, the_direct_call_is_the_fallback_and_nothing_is_a_failure) {
    float v = -1;
    AvReads onlyDirect;
    onlyDirect.directOk = true; onlyDirect.direct = 45;
    CHECK(pickActorValue(onlyDirect, v) && v == 45.0f);
    AvReads onlyEval;
    onlyEval.evalOk = true; onlyEval.eval = 20;
    CHECK(pickActorValue(onlyEval, v) && v == 20.0f);
    v = -1;
    CHECK(!pickActorValue(AvReads{}, v));
    CHECK(v == -1.0f);   // untouched
}
