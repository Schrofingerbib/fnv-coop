// enemy-anim: the non-owner driver against the fake engine. Mutation twins: FNV_MUT(n), see the number in each comment.
#include "anim_helpers.h"

using namespace anim_test;

namespace {
live::AnimState idleAnim() { return live::AnimState{}; }
}

// ---- T-pose guard, 3D rebuilds, menus ----------------------------------------------------------------------------------------------------
TEST(driver, waits_for_the_skeleton_then_plays_a_group_at_once) {   // twin 38
    Bench b;
    b.npc().has3D = false;
    Walker w;
    for (int i = 0; i < 6; ++i) b.frame(w.step(33, 0, 0));
    CHECK(b.eng.count(kId, "play:") == 0);
    CHECK(b.drv.stats().waiting3D >= 1);
    b.npc().has3D = true;   // the skeleton appears, in its bind pose
    CHECK(b.npc().moveGroup == NpcReads::kNoSequence);
    b.frame(w.step(33, 0, 0));
    CHECK(b.npc().moveGroup == kGroupIdle);
    CHECK(b.drv.stats().tposeFixes == 1);
}

TEST(driver, a_rebuilt_skeleton_gets_its_animation_back_immediately) {   // twin 40
    Bench b;
    Walker w;
    b.walk(w, 600, 130, 1);
    CHECK(b.npc().moveGroup == kGroupForward);
    b.eng.rebuild3D(kId);   // cell reload: bind pose again
    b.frame(w.step(33, 130, 1));
    CHECK(b.npc().moveGroup == kGroupForward);
    CHECK(b.drv.stats().rebuilds3D == 1);
}

TEST(driver, a_menu_that_resets_animation_is_undone_when_it_closes) {   // twin 39
    Bench b;
    Walker w;
    b.walk(w, 600, 130, 1);
    b.eng.menuOpen = true;
    for (int i = 0; i < 10; ++i) b.frame(w.step(33, 130, 1));
    b.eng.closeMenu();   // the engine resets every animation
    CHECK(b.npc().moveGroup == kGroupIdle);
    b.frame(w.step(33, 130, 1));
    b.frame(w.step(33, 130, 1));
    CHECK(b.npc().moveGroup == kGroupForward);
    CHECK(b.drv.stats().menuReasserts == 1);
}

TEST(driver, nothing_is_sent_while_a_menu_is_open) {
    Bench b;
    Walker w;
    b.walk(w, 300, 130, 1);
    const uint64_t before = b.eng.totalCommands;
    b.eng.menuOpen = true;
    for (int i = 0; i < 20; ++i) b.frame(w.step(33, 300, 5));
    CHECK(b.eng.totalCommands == before);
}

TEST(driver, a_group_the_engine_dropped_is_found_by_the_read_back) {   // twin 54
    Bench b;
    Walker w;
    b.walk(w, 600, 130, 1);
    CHECK(b.npc().moveGroup == kGroupForward);
    b.npc().moveGroup = kGroupIdle;   // the engine silently went back to idle (hold flag lost)
    b.npc().hold = true;
    b.walk(w, 1200, 130, 1);
    CHECK(b.npc().moveGroup == kGroupForward);
    CHECK(b.drv.stats().groupMismatches >= 1 && b.drv.stats().reasserts >= 1);
}

TEST(driver, a_backend_that_cannot_read_the_group_still_reasserts_a_moving_loop_but_not_idle) {
    Bench b;
    b.npc().hideMoveGroup = true;
    Walker w;
    b.walk(w, 300, 130, 1);
    b.npc().moveGroup = kGroupIdle;
    b.walk(w, 2500, 130, 1);
    CHECK(b.npc().moveGroup == kGroupForward);   // blind re-issue after blindReassertMs
    Bench s;
    s.npc().hideMoveGroup = true;
    s.frame(mkOut(V(), 0, V(), idleAnim()));
    const int playsBefore = s.eng.count(kId, "play:");
    for (int i = 0; i < 200; ++i) s.frame(mkOut(V(), 0, V(), idleAnim()));
    CHECK(s.eng.count(kId, "play:") == playsBefore);   // a standing actor's idle is never re-issued blindly (it would restart its idle animation)
}

TEST(driver, refused_commands_back_off_and_recover) {
    Bench b;
    b.npc().refuseAll = true;
    Walker w;
    b.walk(w, 5000, 130, 1);
    CHECK(b.drv.stats().refused > 0 && b.drv.stats().refused <= 12);   // exponential backoff, not one per frame
    b.npc().refuseAll = false;
    b.walk(w, 9000, 130, 1);
    CHECK(b.npc().moveGroup == kGroupForward);
}

// ---- locomotion --------------------------------------------------------------------------------------------------------------------------
TEST(driver, walk_and_run_follow_the_displayed_speed) {
    Bench b;
    Walker w;
    b.walk(w, 800, 130, 1);
    CHECK(b.npc().moveGroup == kGroupForward);
    b.walk(w, 800, 300, 5);
    CHECK(b.npc().moveGroup == kGroupFastRight - 3);   // FastForward
    b.walk(w, 1000, 0, 0);
    CHECK(b.npc().moveGroup == kGroupIdle);
}

TEST(driver, strafing_and_backing_up_use_their_own_groups) {
    Bench b;
    Walker w;
    w.yaw = 0;
    // moving along +X while facing +Y = strafe right
    for (int i = 0; i < 30; ++i) {
        w.pos.x += 130 * 0.033f;
        live::AnimState a; a.locomotion = 4;
        b.frame(mkOut(w.pos, w.yaw, V(130, 0, 0), a));
    }
    CHECK(b.npc().moveGroup == kGroupForward + 3);   // Right
    for (int i = 0; i < 30; ++i) {
        w.pos.y -= 130 * 0.033f;
        live::AnimState a; a.locomotion = 2;
        b.frame(mkOut(w.pos, w.yaw, V(0, -130, 0), a));
    }
    CHECK(b.npc().moveGroup == kGroupForward + 1);   // Backward
}

TEST(driver, a_lost_or_late_owner_code_is_corrected_from_the_speed) {   // twin 30
    Bench b;
    Walker w;
    b.walk(w, 800, 300, 0);   // the body runs at 300 u/s while the owner's code still says idle
    CHECK(b.npc().moveGroup == kGroupFastRight - 3);
    Bench c;
    Walker w2;
    c.walk(w2, 800, 130, 5);  // and the other way: the code says run, the body walks
    CHECK(c.npc().moveGroup == kGroupForward);
}

TEST(driver, a_speed_glitch_shorter_than_the_debounce_does_not_flip_the_group) {   // twin 31
    Bench b;
    Walker w;
    b.walk(w, 900, 130, 1);
    CHECK(b.npc().moveGroup == kGroupForward);
    bool everRan = false;
    for (int i = 0; i < 2; ++i) { b.frame(w.step(33, 700, 1)); everRan = everRan || b.npc().moveGroup == kGroupFastRight - 3; }
    for (int i = 0; i < 30; ++i) { b.frame(w.step(33, 130, 1)); everRan = everRan || b.npc().moveGroup == kGroupFastRight - 3; }
    CHECK(!everRan);
}

TEST(driver, a_dry_stream_stands_still_instead_of_running_in_place) {   // twin 32
    Bench b;
    Walker w;
    b.walk(w, 800, 300, 5);
    CHECK(b.npc().moveGroup == kGroupFastRight - 3);
    w.state = live::InterpState::Holding;
    for (int i = 0; i < 10; ++i) b.frame(mkOut(w.pos, w.yaw, V(0, 0, 0), w.anim, false, live::InterpState::Holding));
    CHECK(b.npc().moveGroup == kGroupIdle);
}

TEST(driver, a_teleport_restarts_the_speed_measurement) {
    Bench b;
    Walker w;
    b.walk(w, 800, 130, 1);
    w.pos = V(5000, 5000, 0);
    live::InterpOut o = mkOut(w.pos, 0, V(0, 130, 0), w.anim, /*snapped=*/true);
    b.frame(o);
    CHECK(b.drv.info(kId).speed < 200.0f);   // the 4000 unit jump is not a speed
}

TEST(driver, playback_rate_follows_the_displayed_speed) {   // twin 48
    Bench b;
    b.npc().rateSupported = true;
    Walker w;
    b.walk(w, 1000, 100, 1);   // slower than the walk animation's nominal speed
    CHECK(b.npc().moveGroup == kGroupForward);
    CHECK(b.npc().rate > 0.6f && b.npc().rate < 0.85f);
    b.walk(w, 1000, 190, 1);
    CHECK(b.npc().rate > 1.2f && b.npc().rate < 1.5f);
    b.walk(w, 1000, 0, 0);
    CHECK(b.npc().rate == 1.0f);   // back at the natural speed once it stops
}

TEST(driver, an_engine_without_playback_rate_is_asked_only_once) {
    Bench b;   // rateSupported = false
    Walker w;
    b.walk(w, 3000, 100, 1);
    CHECK(b.drv.stats().rateChanges == 0 && b.drv.stats().unsupported == 1);
}

// ---- turn in place, jump -----------------------------------------------------------------------------------------------------------------
TEST(driver, turning_on_the_spot_plays_the_turn_animation_in_the_right_direction) {   // twin 47
    Bench b;
    Walker w;
    b.walk(w, 300, 0, 0);
    for (int i = 0; i < 20; ++i) { w.yaw += 2.0f * 0.033f; b.frame(w.step(33, 0, 0)); }   // clockwise, 2 rad/s
    CHECK(b.npc().moveGroup == kGroupTurnRight);
    b.walk(w, 800, 0, 0);
    CHECK(b.npc().moveGroup == kGroupIdle);
    for (int i = 0; i < 20; ++i) { w.yaw -= 2.0f * 0.033f; b.frame(w.step(33, 0, 0)); }
    CHECK(b.npc().moveGroup == kGroupTurnLeft);
}

TEST(driver, a_slow_drift_of_the_facing_is_not_a_turn) {
    Bench b;
    Walker w;
    b.walk(w, 300, 0, 0);
    for (int i = 0; i < 60; ++i) { w.yaw += 0.2f * 0.033f; b.frame(w.step(33, 0, 0)); }
    CHECK(b.eng.plays(kId, kGroupTurnRight) == 0 && b.drv.stats().turnsPlayed == 0);
}

TEST(driver, a_jump_plays_start_loop_and_land) {   // twin 49
    Bench b;
    Walker w;
    b.walk(w, 400, 0, 0);
    w.anim.jump = true;
    b.frame(w.step(33, 0, 0));
    CHECK(b.npc().moveGroup == kGroupJumpStart);
    for (int i = 0; i < 12; ++i) b.frame(w.step(33, 0, 0));
    CHECK(b.npc().moveGroup == kGroupJumpLoop);
    w.anim.jump = false;
    b.frame(w.step(33, 0, 0));
    CHECK(b.npc().moveGroup == kGroupJumpLand);
    for (int i = 0; i < 30; ++i) b.frame(w.step(33, 0, 0));
    CHECK(b.npc().moveGroup == kGroupIdle);
    CHECK(b.drv.stats().jumpsPlayed == 1);
}

// ---- weapon ------------------------------------------------------------------------------------------------------------------------------
TEST(driver, a_weapon_is_drawn_and_holstered_with_the_transition_animation) {   // twin 33
    Bench b;
    Walker w;
    b.walk(w, 300, 0, 0);
    w.anim.weaponDrawn = true;
    b.frame(w.step(33, 0, 0));
    CHECK(b.npc().weaponOut);
    CHECK(b.npc().moveGroup == kGroupEquip);
    b.walk(w, 1500, 0, 0);
    CHECK(b.npc().weaponOut && b.drv.info(kId).weaponConfirmed);
    w.anim.weaponDrawn = false;
    b.frame(w.step(33, 0, 0));
    CHECK(!b.npc().weaponOut && b.npc().moveGroup == kGroupUnequip);
    CHECK(b.drv.stats().weaponChanges == 2);
}

TEST(driver, a_copy_first_seen_with_its_weapon_out_gets_it_out_without_a_draw_animation) {
    Bench b;
    Walker w;
    w.anim.weaponDrawn = true;
    b.frame(w.step(33, 0, 0));
    CHECK(b.npc().weaponOut);
    CHECK(b.eng.plays(kId, kGroupEquip) == 0);
    CHECK(b.drv.stats().weaponChanges == 0);
}

TEST(driver, a_weapon_the_engine_ignored_is_retried_until_it_is_out) {   // twin 34
    Bench b;
    b.npc().ignoreWeaponCalls = 2;
    Walker w;
    b.walk(w, 300, 0, 0);
    w.anim.weaponDrawn = true;
    b.walk(w, 4000, 0, 0);
    CHECK(b.npc().weaponOut);
    CHECK(b.drv.stats().weaponVerifyFailures >= 1 && b.drv.stats().weaponConfirmed >= 1);
}

TEST(driver, a_weapon_the_engine_never_takes_is_not_hammered) {
    Bench b;
    b.npc().ignoreWeaponCalls = 1000;
    Walker w;
    b.walk(w, 300, 0, 0);
    w.anim.weaponDrawn = true;
    b.walk(w, 9000, 0, 0);
    CHECK(!b.npc().weaponOut);
    CHECK(b.drv.stats().weaponLadderGiveUps >= 1);
    CHECK(b.eng.count(kId, "weapon:") <= 12);   // 3 tries per ladder, one ladder per weaponRetryMs
}

TEST(driver, a_backend_without_a_weapon_call_falls_back_to_the_transition_animation) {
    Bench b;
    b.npc().weaponSupported = false;
    Walker w;
    b.walk(w, 300, 0, 0);
    w.anim.weaponDrawn = true;
    b.frame(w.step(33, 0, 0));
    CHECK(b.npc().moveGroup == kGroupEquip);
    w.anim.weaponDrawn = false;
    b.walk(w, 800, 0, 0);
    b.frame(w.step(33, 0, 0));
    CHECK(b.eng.count(kId, "weapon:") == 0);
    CHECK(b.drv.stats().unsupported == 1);   // asked once, never again
}

TEST(driver, aiming_with_the_weapon_out_uses_the_aim_groups_by_pitch) {
    Bench b;
    Walker w;
    w.anim.weaponDrawn = true; w.anim.aim = true;
    live::InterpOut o = w.step(33, 0, 0);
    for (int i = 0; i < 15; ++i) b.frame(o);
    CHECK(b.npc().moveGroup == kGroupAim);
    CHECK(b.npc().aiming);
    o.rot.x = 0.5f;
    for (int i = 0; i < 6; ++i) b.frame(o);
    CHECK(b.npc().moveGroup == kGroupAim + 1);   // AimUp
    o.rot.x = -0.5f;
    for (int i = 0; i < 6; ++i) b.frame(o);
    CHECK(b.npc().moveGroup == kGroupAim + 2);   // AimDown
}

TEST(driver, sneak_is_applied_for_humanoids_only) {   // twin 51
    Bench b;
    Walker w;
    w.anim.sneak = true;
    b.frame(w.step(33, 0, 0));
    CHECK(b.npc().sneak);
    Bench c;
    c.npc().creature = true;
    Walker w2; w2.anim.sneak = true;
    c.frame(w2.step(33, 0, 0));
    CHECK(!c.npc().sneak);
}

// ---- the owner's one-shots ---------------------------------------------------------------------------------------------------------------
namespace {
live::InterpOut withAction(Walker& w, uint8_t group, uint8_t seq) {
    w.anim.action = group; w.anim.actionSeq = seq;
    return w.step(33, 0, 0);
}
}

TEST(driver, the_owners_attack_is_played_once_per_event) {   // twin 35
    Bench b;
    Walker w;
    b.walk(w, 300, 0, 0);
    for (int i = 0; i < 8; ++i) b.frame(withAction(w, 32, 1));   // the event lasts several snapshots
    CHECK(b.eng.plays(kId, 32) == 1);
    for (int i = 0; i < 8; ++i) b.frame(withAction(w, 0, 1));
    for (int i = 0; i < 8; ++i) b.frame(withAction(w, 32, 2));   // a second swing of the same group
    CHECK(b.eng.plays(kId, 32) == 2);
    CHECK(b.drv.stats().oneShotsPlayed == 2);
}

TEST(driver, an_event_that_was_already_running_when_the_copy_appeared_is_not_replayed) {   // twin 36
    Bench b;
    Walker w;
    b.frame(withAction(w, 32, 7));
    b.frame(withAction(w, 32, 7));
    CHECK(b.eng.plays(kId, 32) == 0);
    b.frame(withAction(w, 32, 8));
    CHECK(b.eng.plays(kId, 32) == 1);
}

TEST(driver, the_event_counter_wraps_around) {   // twin 37
    Bench b;
    Walker w;
    b.frame(withAction(w, 0, 253));
    int played = 0;
    for (uint8_t s : {static_cast<uint8_t>(254), static_cast<uint8_t>(255), static_cast<uint8_t>(0), static_cast<uint8_t>(1), static_cast<uint8_t>(2)}) {
        for (int i = 0; i < 3; ++i) b.frame(withAction(w, 32, s));
        ++played;
        CHECK(b.eng.plays(kId, 32) == played);
    }
}

TEST(driver, an_old_counter_value_is_stale_not_an_event) {
    Bench b;
    Walker w;
    b.frame(withAction(w, 0, 200));
    b.frame(withAction(w, 32, 100));   // 100 is "older" than 200 (half range rule): ignored
    CHECK(b.eng.plays(kId, 32) == 0 && b.drv.stats().oneShotsStale == 1);
}

TEST(driver, an_event_whose_group_ended_before_it_was_seen_is_counted_not_guessed) {
    Bench b;
    Walker w;
    b.frame(withAction(w, 0, 1));
    b.frame(withAction(w, 0, 2));   // the counter moved but the stream no longer names a group
    CHECK(b.drv.stats().oneShotsMissed == 1);
    b.frame(withAction(w, 250, 3));   // not a group we know
    CHECK(b.drv.stats().oneShotsMissed == 2);
    b.frame(withAction(w, kGroupForward, 4));   // a movement group is not an action
    CHECK(b.drv.stats().oneShotsMissed == 3 && b.eng.plays(kId, kGroupForward) <= 1);
}

TEST(driver, a_reaction_is_not_stomped_by_the_movement_group_and_movement_comes_back) {
    Bench b;
    Walker w;
    b.walk(w, 800, 130, 1);
    const int walkPlaysBefore = b.eng.plays(kId, kGroupForward);
    for (int i = 0; i < 4; ++i) b.frame(withAction(w, kGroupStagger, 1));
    // while the stagger plays (700 ms) the movement group is not re-issued
    for (int i = 0; i < 12; ++i) { w.anim.action = 0; b.frame(w.step(33, 130, 1)); }
    CHECK(b.eng.plays(kId, kGroupStagger) == 1);
    CHECK(b.eng.plays(kId, kGroupForward) == walkPlaysBefore);
    for (int i = 0; i < 20; ++i) b.frame(w.step(33, 130, 1));
    CHECK(b.eng.plays(kId, kGroupForward) == walkPlaysBefore + 1);   // and afterwards it is asserted again
}

TEST(driver, a_held_action_ends_when_the_owner_stops_reporting_it) {
    Bench b;
    Walker w;
    b.frame(withAction(w, 0, 0));
    for (int i = 0; i < 10; ++i) b.frame(withAction(w, kGroupBlockIdle, 1));
    CHECK(b.npc().actionGroup == kGroupBlockIdle);
    const int idlesBefore = b.eng.plays(kId, kGroupIdle);
    for (int i = 0; i < 6; ++i) b.frame(withAction(w, 0, 1));
    CHECK(b.eng.plays(kId, kGroupIdle) == idlesBefore + 1);   // the block is released by a fresh idle
    CHECK(b.npc().actionGroup == -1);
}

TEST(driver, attacks_can_be_switched_off) {
    DriverConfig c; c.playAttacks = false;
    Bench b(c);
    Walker w;
    b.frame(withAction(w, 0, 0));
    for (int i = 0; i < 5; ++i) b.frame(withAction(w, 32, 1));
    CHECK(b.eng.plays(kId, 32) == 0);
}

// ---- knockdown ---------------------------------------------------------------------------------------------------------------------------
TEST(driver, a_knockdown_is_replayed_and_the_actor_gets_up_again) {
    Bench b;
    b.npc().knockSupported = true;
    Walker w;
    b.walk(w, 400, 0, 0);
    w.anim.knocked = true;
    b.walk(w, 800, 0, 0);
    CHECK(b.npc().knocked && b.npc().knockCalls == 1);
    const int idles = b.eng.plays(kId, kGroupIdle);
    CHECK(b.eng.plays(kId, kGroupStagger) == 0);
    w.anim.knocked = false;
    b.frame(w.step(33, 0, 0));
    CHECK(!b.npc().knocked && b.npc().getUpCalls == 1);
    b.walk(w, 2000, 0, 0);
    CHECK(b.eng.plays(kId, kGroupIdle) > idles);   // movement is asserted again after the get-up window
}

TEST(driver, without_a_knockdown_call_a_stagger_stands_in_for_it) {   // twin 50
    Bench b;   // knockSupported = false
    Walker w;
    b.walk(w, 400, 0, 0);
    w.anim.knocked = true;
    b.walk(w, 800, 0, 0);
    CHECK(b.eng.plays(kId, kGroupStagger) == 1 && b.drv.stats().knockdownFallbacks == 1);
    CHECK(b.drv.stats().unsupported == 1);
}

TEST(driver, a_body_on_the_floor_gets_no_movement_group) {
    Bench b;
    Walker w;
    b.walk(w, 400, 0, 0);
    const int before = b.eng.count(kId, "play:");
    w.anim.ragdoll = true;
    b.walk(w, 800, 130, 1);
    CHECK(b.eng.count(kId, "play:") == before);
}

// ---- death, corpse -----------------------------------------------------------------------------------------------------------------------
TEST(driver, a_death_the_engine_ignored_is_verified_and_repeated) {   // twin 41
    Bench b;
    b.npc().ignoreKillCalls = 2;
    Walker w;
    b.walk(w, 300, 130, 1);
    b.drv.onDeath(kId, b.now, /*fromNetwork=*/true);
    for (int i = 0; i < 200; ++i) b.frame(mkOut(V(), 0, V()));
    CHECK(b.npc().dead);
    CHECK(b.npc().kills == 3 && b.drv.stats().killRetries == 3);
    CHECK(b.drv.deadInfo(kId).verified);
}

TEST(driver, a_death_that_never_takes_is_given_up_not_repeated_forever) {
    Bench b;
    b.npc().ignoreKillCalls = 1000;
    b.drv.onDeath(kId, b.now, true);
    for (int i = 0; i < 600; ++i) b.frame(mkOut(V(), 0, V()));
    CHECK(b.npc().kills == 4 && b.drv.stats().killGaveUp == 1);
}

TEST(driver, the_owners_own_death_is_not_second_guessed) {
    Bench b;
    b.npc().dead = true;
    b.drv.onDeath(kId, b.now, /*fromNetwork=*/false);
    for (int i = 0; i < 100; ++i) b.frame(mkOut(V(), 0, V()));
    CHECK(b.npc().kills == 0);
}

TEST(driver, a_dead_actor_is_never_driven) {   // twin 46
    Bench b;
    Walker w;
    b.walk(w, 300, 130, 1);
    b.npc().dead = true;
    b.drv.onDeath(kId, b.now, false);
    const uint64_t before = b.eng.totalCommands;
    b.walk(w, 1000, 130, 1);
    CHECK(b.eng.totalCommands == before && b.npc().playsAfterDeath == 0);
    CHECK(b.drv.info(kId).known == false);
}

namespace {
// A body that dies at the origin and slides to (200, 50) in 1.5 s; the owner's body came to rest at `owner`.
void killAndSend(Bench& b, Vec3 owner, uint32_t poseDelayMs = 300) {
    b.npc().pos = V(0, 0, 0);
    b.eng.startDeath(kId, V(200, 50, 0));
    b.drv.onDeath(kId, b.now, true);
    for (uint32_t t = 0; t < poseDelayMs; t += 33) b.frame(mkOut(V(), 0, V()));
    CorpsePose p; p.pos = owner; p.yawRad = 0.75f; p.cell = 1; p.ragdoll = true;
    b.drv.onCorpse(kId, p, b.now);
}
}

TEST(driver, the_corpse_ends_up_where_the_owners_body_lies) {   // twins 42, 43
    Bench b;
    killAndSend(b, V(260, 40, 0));
    for (int i = 0; i < 250; ++i) b.frame(mkOut(V(), 0, V()));
    CHECK(std::fabs(b.npc().pos.x - 260) < 1.0f && std::fabs(b.npc().pos.y - 40) < 1.0f);
    CHECK(std::fabs(b.npc().yaw - 0.75f) < 1e-4f);
    CHECK(b.npc().placeCalls == 1 && !b.npc().placedWhileSliding);   // moved once, after the local ragdoll came to rest
    CHECK(b.drv.stats().corpseCorrections == 1 && b.drv.deadInfo(kId).resolved);
}

TEST(driver, a_corpse_that_already_lies_in_the_right_place_is_left_alone) {   // twin 44
    Bench b;
    killAndSend(b, V(210, 55, 0));   // 11 units from where the local ragdoll rests
    for (int i = 0; i < 250; ++i) b.frame(mkOut(V(), 0, V()));
    CHECK(b.npc().placeCalls == 0);
    CHECK(b.drv.stats().corpseWithinTolerance == 1 && b.drv.deadInfo(kId).resolved);
}

TEST(driver, a_pose_that_arrives_before_the_death_is_kept) {
    Bench b;
    CorpsePose p; p.pos = V(500, 500, 0); p.cell = 1;
    b.drv.onCorpse(kId, p, b.now);
    b.npc().pos = V(0, 0, 0);
    b.eng.startDeath(kId, V(100, 0, 0));
    b.drv.onDeath(kId, b.now, true);
    CHECK(b.drv.deadInfo(kId).havePose);
    for (int i = 0; i < 250; ++i) b.frame(mkOut(V(), 0, V()));
    CHECK(std::fabs(b.npc().pos.x - 500) < 1.0f);
}

TEST(driver, a_missing_pose_is_counted_and_a_late_one_is_still_applied) {
    Bench b;
    b.npc().pos = V(0, 0, 0);
    b.eng.startDeath(kId, V(100, 0, 0));
    b.drv.onDeath(kId, b.now, true);
    for (int i = 0; i < 520; ++i) b.frame(mkOut(V(), 0, V()));   // 17 s
    CHECK(b.drv.stats().corpseMissing == 1 && b.npc().placeCalls == 0);
    CorpsePose p; p.pos = V(400, 0, 0); p.cell = 1;
    b.drv.onCorpse(kId, p, b.now);
    for (int i = 0; i < 40; ++i) b.frame(mkOut(V(), 0, V()));
    CHECK(b.drv.stats().corpseLate == 1 && std::fabs(b.npc().pos.x - 400) < 1.0f);
}

TEST(driver, a_corpse_that_cannot_be_moved_is_given_up_after_a_few_tries) {
    Bench b;
    killAndSend(b, V(600, 0, 0));
    b.npc().refuseAll = true;
    for (int i = 0; i < 600; ++i) b.frame(mkOut(V(), 0, V()));
    CHECK(b.drv.stats().corpseGaveUp == 1 && b.drv.stats().corpseCorrections == 4);
}

TEST(driver, a_scripted_resurrect_clears_the_death) {
    Bench b;
    b.npc().dead = true;
    b.drv.onDeath(kId, b.now, false);
    CHECK(b.drv.isDead(kId));
    b.drv.revive(kId);
    b.npc().dead = false;
    Walker w;
    b.walk(w, 300, 130, 1);
    CHECK(!b.drv.isDead(kId) && b.npc().moveGroup == kGroupForward);
}

// ---- budget, worlds, ownership ------------------------------------------------------------------------------------------------------------
TEST(driver, a_crowd_cannot_flood_the_console) {   // twin 45
    DriverConfig c;
    Bench b(c);
    const int n = 100;
    for (int i = 2; i <= n; ++i) b.eng.add(static_cast<NetId>(i));
    Walker w;
    // everyone starts standing, then everyone starts running in the same tick
    for (int i = 1; i <= n; ++i) b.frame(w.step(33, 0, 0), 0, static_cast<NetId>(i));
    b.drv.tick(b.eng.now);
    Walker run = w;
    uint64_t maxPerTick = 0;
    for (int tick = 0; tick < 40; ++tick) {
        b.eng.advance(33);
        const uint64_t before = b.eng.totalCommands;
        for (int i = 1; i <= n; ++i) b.drv.drive(static_cast<NetId>(i), run.step(0, 0, 0), b.eng.now);
        run.pos.y += 300 * 0.033f;
        b.drv.tick(b.eng.now);
        maxPerTick = std::max(maxPerTick, b.eng.totalCommands - before);
    }
    CHECK(maxPerTick <= c.hardBudget);
    CHECK(b.drv.stats().deferredBudget > 0);
}

TEST(driver, a_loaded_game_starts_from_nothing) {   // twin 52
    Bench b;
    Walker w;
    b.walk(w, 400, 130, 1);
    CHECK(b.drv.info(kId).known);
    b.eng.worldEpoch++;
    b.drv.tick(b.eng.now + 33);
    CHECK(!b.drv.info(kId).known && b.drv.stats().worldResets == 1);
}

TEST(driver, the_actors_that_becomes_ours_is_released_to_its_own_ai) {   // twin 53
    Bench b;
    Walker w;
    b.walk(w, 600, 130, 1);
    b.drv.release(kId);
    CHECK(b.npc().releaseCalls == 1 && !b.drv.info(kId).known);
    b.drv.release(kId);   // a second release of an actor we no longer hold does nothing
    CHECK(b.npc().releaseCalls == 1);
}

TEST(system, a_copy_nobody_drives_any_more_and_that_is_now_ours_is_released) {
    FakeNpcEngine eng;
    RecordingNet net;
    NpcAnimSystem sys(eng, net);
    eng.add(kId);
    Walker w;
    bool mine = false;
    auto isMine = [&](NetId) { return mine; };
    for (int i = 0; i < 30; ++i) { eng.advance(33); sys.drive(kId, w.step(33, 130, 1), eng.now); sys.tick(eng.now, isMine); }
    CHECK(sys.driver().driven().size() == 1);
    for (int i = 0; i < 60; ++i) { eng.advance(33); sys.tick(eng.now, isMine); }   // not driven, not ours: kept as is (out of range)
    CHECK(eng.a[kId].releaseCalls == 0 && sys.driver().driven().size() == 1);
    mine = true;
    for (int i = 0; i < 3; ++i) { eng.advance(33); sys.tick(eng.now, isMine); }
    CHECK(eng.a[kId].releaseCalls == 1 && sys.driver().driven().empty());
}

TEST(driver, the_owners_talking_is_shown_once_per_conversation_and_not_on_creatures) {   // twin 55
    Bench b;
    Walker w;
    b.walk(w, 300, 0, 0);
    w.anim.talk = true;
    for (int i = 0; i < 6; ++i) b.frame(w.step(33, 0, 0));
    CHECK(b.eng.plays(kId, kGroupTalking) == 1);
    w.anim.talk = false;
    for (int i = 0; i < 6; ++i) b.frame(w.step(33, 0, 0));
    w.anim.talk = true;
    for (int i = 0; i < 6; ++i) b.frame(w.step(33, 0, 0));
    CHECK(b.eng.plays(kId, kGroupTalking) == 2);
    Bench c;
    c.npc().creature = true;
    Walker w2; w2.anim.talk = true;
    for (int i = 0; i < 6; ++i) c.frame(w2.step(33, 0, 0));
    CHECK(c.eng.plays(kId, kGroupTalking) == 0);
}

TEST(system, a_handoff_continues_the_one_shot_counter_of_the_previous_owner) {   // twin 62
    FakeNpcEngine eng;
    RecordingNet net;
    NpcAnimSystem sys(eng, net);
    eng.add(kId);
    Walker w;
    bool mine = false;
    auto isMine = [&](NetId) { return mine; };
    w.anim.actionSeq = 57;   // the previous owner has played 57 one-shots
    for (int i = 0; i < 20; ++i) { eng.advance(33); sys.drive(kId, w.step(33, 0, 0), eng.now); sys.tick(eng.now, isMine); }
    mine = true;
    for (int i = 0; i < 40; ++i) { eng.advance(33); sys.tick(eng.now, isMine); }   // not driven any more and now ours: released
    CHECK(eng.a[kId].releaseCalls == 1);
    // this machine's engine now plays an attack on the actor it owns
    eng.a[kId].actionGroup = 32; eng.a[kId].actionStart = eng.now; eng.a[kId].actionEnd = eng.now + 500;
    live::ActorSnapshot s;
    s.netId = kId;
    CHECK(sys.sampleOwned(kId, s, eng.now));
    CHECK(s.anim.action == 32 && s.anim.actionSeq == 58);   // 58 is newer than 57 for everybody who saw the previous owner; a restarted counter (1) would look stale
}

TEST(system, a_body_whose_cell_unloaded_before_its_pose_arrived_is_still_placed_when_it_loads_again) {
    FakeNpcEngine eng;
    RecordingNet net;
    NpcAnimSystem sys(eng, net);
    eng.add(kId, V(0, 0, 0));
    eng.startDeath(kId, V(100, 0, 0));
    sys.onDied(kId, false, true, eng.now);
    for (int i = 0; i < 20; ++i) { eng.advance(33); sys.tick(eng.now); }
    eng.a[kId].exists = false;      // the cell unloaded
    sys.onLost(kId);
    CorpsePose p; p.pos = V(500, 0, 0); p.cell = 1;
    sys.onNetEvent(kId, encodeCorpse(p), eng.now);
    for (int i = 0; i < 100; ++i) { eng.advance(33); sys.tick(eng.now); }
    CHECK(eng.a[kId].placeCalls == 0);   // nothing to move while it is not loaded
    eng.a[kId].exists = true;            // loaded again, lying where its own ragdoll left it
    for (int i = 0; i < 100; ++i) { eng.advance(33); sys.tick(eng.now); }
    CHECK(eng.a[kId].placeCalls == 1 && std::fabs(eng.a[kId].pos.x - 500) < 1.0f);
}

TEST(driver, a_weapon_state_that_drifts_after_it_was_confirmed_is_restored) {   // twin 56
    Bench b;
    Walker w;
    b.walk(w, 300, 0, 0);
    w.anim.weaponDrawn = true;
    b.walk(w, 1500, 0, 0);
    CHECK(b.npc().weaponOut && b.drv.info(kId).weaponConfirmed);
    b.npc().weaponOut = false;     // the restrained actor's own logic put the weapon away
    b.walk(w, 2500, 0, 0);
    CHECK(b.npc().weaponOut);
    CHECK(b.drv.stats().weaponDrifts >= 1);
}
