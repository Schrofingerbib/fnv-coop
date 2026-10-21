// plugin/vats: the real-time perk rework (VATS kill ledger, Grim Reaper's Sprint refund, Mysterious Stranger credit).
#include "vats/vats_perks.h"
#include "minitest.h"

using namespace nvcoop::vats;

namespace {

PerkSettings ps() { return PerkSettings{}; }

// queue an action at `target` at t, start the playback at t+100
void queueAndPlay(VatsLedger& l, uint32_t target, uint64_t t, LimbId limb = LimbId::Head) {
    l.onActionQueued(target, limb, t);
    l.onModeChanged(Mode::TargetSelect, Mode::Playback, t + 100);
}

TEST(vats_perks, a_remote_kill_during_the_playback_of_a_queued_action_is_a_vats_kill) {
    VatsLedger l(ps());
    queueAndPlay(l, 0x500, 1000, LimbId::LeftArm);
    VatsKill k;
    CHECK(l.onActorDied(0x500, false, true, 1500, &k));
    CHECK(k.victimRef == 0x500 && k.limb == LimbId::LeftArm);
    CHECK(l.kills() == 1);
}

TEST(vats_perks, the_kill_window_runs_a_little_past_the_end_of_the_playback) {
    VatsLedger l(ps());
    queueAndPlay(l, 0x500, 1000);
    l.onModeChanged(Mode::Playback, Mode::None, 2000);
    VatsKill k;
    CHECK(l.onActorDied(0x500, false, true, 2000 + 3999, &k));          // the death message from the owner's machine arrives late
    queueAndPlay(l, 0x501, 10000);
    l.onModeChanged(Mode::Playback, Mode::None, 11000);
    CHECK(!l.onActorDied(0x501, false, true, 11000 + 4001, &k));        // too late: not ours any more
}

TEST(vats_perks, a_death_before_any_playback_or_of_an_unqueued_actor_is_not_a_vats_kill) {
    VatsLedger l(ps());
    VatsKill k;
    l.onActionQueued(0x500, LimbId::Head, 1000);                         // queued but the playback has not started
    CHECK(!l.onActorDied(0x500, false, true, 1200, &k));
    CHECK(!l.onActorDied(0x999, false, true, 1200, &k));                 // never targeted
    CHECK(l.kills() == 0);
}

TEST(vats_perks, an_action_queued_after_a_playback_began_does_not_count_for_that_playback) {
    VatsLedger l(ps());
    l.onModeChanged(Mode::TargetSelect, Mode::Playback, 500);
    l.onModeChanged(Mode::Playback, Mode::None, 900);
    l.onActionQueued(0x500, LimbId::Head, 1000);                         // queued after the old playback ended; the new one has not started
    VatsKill k;
    CHECK(!l.onActorDied(0x500, false, true, 1200, &k));
}

TEST(vats_perks, a_kill_pays_once) {
    VatsLedger l(ps());
    queueAndPlay(l, 0x500, 1000);
    VatsKill k;
    CHECK(l.onActorDied(0x500, false, true, 1500, &k));
    CHECK(!l.onActorDied(0x500, false, true, 1501, &k));                 // the same death reported again (network + local)
    CHECK(l.kills() == 1);
}

TEST(vats_perks, a_death_the_local_engine_settled_is_left_to_the_engines_own_perk_code) {
    VatsLedger l(ps());
    queueAndPlay(l, 0x500, 1000);
    VatsKill k;
    CHECK(!l.onActorDied(0x500, true, false, 1500, &k));                 // decided here by the engine's own hit code
    CHECK(l.kills() == 0 && l.nativeKills() == 1);
    queueAndPlay(l, 0x501, 3000);
    CHECK(l.onActorDied(0x501, false, true, 3500, &k));                  // decided elsewhere
    queueAndPlay(l, 0x502, 6000);
    CHECK(l.onActorDied(0x502, true, true, 6500, &k));                   // we own it but the kill arrived from the network (a remote KillActor)
}

TEST(vats_perks, old_queued_actions_are_forgotten) {
    PerkSettings s = ps();
    s.actionTtlMs = 5000;
    VatsLedger l(s);
    l.onActionQueued(0x500, LimbId::Head, 1000);
    l.onActionQueued(0x501, LimbId::Head, 20000);                        // the first one is older than the ttl now
    CHECK(l.pending() == 1);
}

TEST(vats_perks, grim_reapers_sprint_gives_back_ap_up_to_the_maximum_only_with_the_perk) {
    PerkSettings s = ps();
    s.grsApRestore = 20;
    CHECK(grimReaperRefund(s, true, 50, 100) == 20);
    CHECK(grimReaperRefund(s, true, 90, 100) == 10);                     // never above the pool
    CHECK(grimReaperRefund(s, true, 100, 100) == 0);
    CHECK(grimReaperRefund(s, false, 50, 100) == 0);                     // no perk, no refund
    CHECK(grimReaperRefund(s, true, -1, 100) == 0);                      // unknown pool: do not guess
    CHECK(grimReaperRefund(s, true, 50, -1) == 0);
    s.grsRestoreAll = true;
    CHECK(grimReaperRefund(s, true, 35, 100) == 65);
    s.grsEnabled = false;
    CHECK(grimReaperRefund(s, true, 35, 100) == 0);
    s.grsEnabled = true; s.grsRestoreAll = false; s.grsApRestore = -5;
    CHECK(grimReaperRefund(s, true, 35, 100) == 0);
}

TEST(vats_perks, the_stranger_is_credited_to_the_player_only_during_the_players_own_playback) {
    AttackerFacts f;
    f.attackerRef = 0xABCD00; f.localPlayerRef = 0x14; f.localPlayback = true;
    CHECK(attackerFor(f) == 0x14);                                       // an untracked actor shooting during my playback = my Stranger
    f.localPlayback = false;
    CHECK(attackerFor(f) == 0xABCD00);                                   // outside the playback nothing is rewritten
    f.localPlayback = true;
    f.attackerTracked = true;
    CHECK(attackerFor(f) == 0xABCD00);                                   // a synced NPC keeps its own hits
    f.attackerTracked = false; f.attackerIsParty = true;
    CHECK(attackerFor(f) == 0xABCD00);                                   // another player's puppet stays that player's
    f.attackerIsParty = false; f.attackerRef = 0x14;
    CHECK(attackerFor(f) == 0x14);
    f.attackerRef = 0;
    CHECK(attackerFor(f) == 0);                                          // no attacker (environment) stays none
    f.attackerRef = 0xABCD00; f.localPlayerRef = 0;
    CHECK(attackerFor(f) == 0xABCD00);                                   // without a known local player nothing is rewritten
}

TEST(vats_perks, the_perk_table_says_which_perks_are_native_and_which_need_glue) {
    size_t n = 0;
    const PerkInfo* t = perkTable(&n);
    REQUIRE(n >= 7);
    size_t glue = 0, native = 0;
    for (size_t i = 0; i < n; ++i) {
        CHECK(t[i].title != nullptr && t[i].note != nullptr);
        if (t[i].how == PerkTreatment::Glue) ++glue; else ++native;
    }
    CHECK(glue == 2);
    CHECK(native >= 5);
}

}  // namespace
