// plugin/vats: the party slow-mo arbiter (host) and follower (everyone).
#include "vats/vats_slowmo.h"
#include "minitest.h"

using namespace nvcoop::vats;

namespace {

Settings on() {
    Settings s;
    s.partySlowMo = true;
    s.slowScale = 0.35f;
    s.slowMaxMs = 3000;
    s.slowCooldownMs = 10000;
    return s;
}

TEST(vats_slowmo, a_vats_entry_starts_one_capped_window_for_everybody) {
    SlowMoArbiter a(on(), 3);
    a.setPlayerMode(1, Mode::TargetSelect, 1000);
    CHECK(a.active());
    CHECK(a.takeChanged());
    CHECK(!a.takeChanged());
    const SlowMoGrant g = a.grant(1000);
    CHECK(g.active && g.scale == 0.35f && g.remainingMs == 3000 && g.cause == 1 && g.epoch == 3 && g.seq == 1);
    CHECK(a.grant(2000).remainingMs == 2000);
    CHECK(a.stats().started == 1);
}

TEST(vats_slowmo, the_window_never_outlives_its_cap_even_if_the_player_stays_in_vats) {
    SlowMoArbiter a(on());
    a.setPlayerMode(0, Mode::TargetSelect, 1000);
    a.takeChanged();
    a.tick(3999);
    CHECK(a.active());
    a.tick(4000);
    CHECK(!a.active());
    CHECK(a.takeChanged());
    CHECK(a.stats().endedByCap == 1);
    CHECK(!a.grant(4000).active && a.grant(4000).scale == 1.0f);
}

TEST(vats_slowmo, the_window_ends_when_the_last_vats_user_leaves) {
    SlowMoArbiter a(on());
    a.setPlayerMode(0, Mode::TargetSelect, 1000);
    a.setPlayerMode(2, Mode::LimbSelect, 1100);
    a.setPlayerMode(0, Mode::None, 1500);
    a.tick(1500);
    CHECK(a.active());                                  // player 2 is still in VATS
    a.setPlayerMode(2, Mode::Playback, 1600);
    a.tick(1600);
    CHECK(a.active());                                  // playback counts as being in VATS
    a.setPlayerMode(2, Mode::None, 1700);
    a.tick(1700);
    CHECK(!a.active());
    CHECK(a.stats().endedNatural == 1);
}

TEST(vats_slowmo, a_second_entrant_does_not_extend_the_window) {
    SlowMoArbiter a(on());
    a.setPlayerMode(0, Mode::TargetSelect, 1000);
    a.setPlayerMode(1, Mode::TargetSelect, 3500);       // enters late in the window
    a.tick(4000);
    CHECK(!a.active());
    CHECK(a.stats().started == 1);
}

TEST(vats_slowmo, a_cooldown_follows_every_window) {
    SlowMoArbiter a(on());
    a.setPlayerMode(0, Mode::TargetSelect, 1000);
    a.tick(4000);                                       // capped
    CHECK(!a.active());
    a.setPlayerMode(0, Mode::None, 4100);
    a.setPlayerMode(0, Mode::TargetSelect, 5000);       // re-enters at once
    CHECK(!a.active());
    CHECK(a.stats().blockedByCooldown == 1);
    a.setPlayerMode(0, Mode::None, 5100);
    a.setPlayerMode(0, Mode::TargetSelect, 14500);      // 4000 + 10000 = 14000 has passed
    CHECK(a.active());
    CHECK(a.stats().started == 2);
}

TEST(vats_slowmo, staying_in_vats_does_not_retrigger_after_the_window) {
    SlowMoArbiter a(on());
    a.setPlayerMode(0, Mode::TargetSelect, 1000);
    a.tick(4000);
    a.tick(20000);
    CHECK(!a.active());                                 // only an ENTRY starts a window
    a.setPlayerMode(0, Mode::LimbSelect, 20001);        // a mode change inside VATS is not an entry
    CHECK(!a.active());
}

TEST(vats_slowmo, a_player_who_disconnects_inside_vats_ends_the_window) {
    SlowMoArbiter a(on());
    a.setPlayerMode(1, Mode::TargetSelect, 1000);
    a.playerLeft(1, 1200);
    CHECK(!a.active());
    SlowMoArbiter b(on());
    b.playerLeft(9, 100);                               // out-of-range slot: ignored
    CHECK(!b.active());
}

TEST(vats_slowmo, the_host_setting_off_never_starts_a_window_and_ends_a_running_one) {
    Settings s = on();
    s.partySlowMo = false;
    SlowMoArbiter a(s);
    a.setPlayerMode(0, Mode::TargetSelect, 1000);
    CHECK(!a.active());
    CHECK(a.stats().blockedDisabled == 1);
    SlowMoArbiter b(on());
    b.setPlayerMode(0, Mode::TargetSelect, 1000);
    CHECK(b.active());
    b.setSettings(s, 1500);
    CHECK(!b.active());
}

TEST(vats_slowmo, the_arbiter_clamps_what_it_is_given) {
    Settings s = on();
    s.slowScale = 0.0f;
    s.slowMaxMs = 999999;
    SlowMoArbiter a(s);
    a.setPlayerMode(0, Mode::TargetSelect, 0);
    const SlowMoGrant g = a.grant(0);
    CHECK(g.scale >= 0.10f);
    CHECK(g.remainingMs <= 10000);
}

TEST(vats_slowmo, the_follower_mirrors_the_grant_and_expires_without_the_host) {
    SlowMoFollower f;
    SlowMoGrant g;
    g.epoch = 1; g.seq = 1; g.active = true; g.scale = 0.4f; g.remainingMs = 2000; g.cause = 2;
    CHECK(f.apply(g, 5000));
    CHECK(f.active() && f.scale() == 0.4f);
    CHECK(!f.tick(6000));
    CHECK(!f.tick(7000));                               // end announced at 7000, grace until 7750
    CHECK(f.active());
    CHECK(f.tick(7751));                                // the host never sent the end
    CHECK(!f.active() && f.scale() == 1.0f);
}

TEST(vats_slowmo, a_refresh_extends_the_deadline_and_an_end_clears_at_once) {
    SlowMoFollower f;
    SlowMoGrant g;
    g.epoch = 1; g.seq = 4; g.active = true; g.scale = 0.5f; g.remainingMs = 2000;
    f.apply(g, 1000);
    g.remainingMs = 1000;                               // the same grant, refreshed (same seq)
    CHECK(!f.apply(g, 2000));                           // nothing visible changed
    CHECK(f.active());
    CHECK(!f.tick(3700));                               // 2000 + 1000 + grace 750 = 3750
    CHECK(f.tick(3800));
    SlowMoGrant on2 = g; on2.seq = 5; on2.remainingMs = 3000;
    f.apply(on2, 4000);
    SlowMoGrant end = on2; end.seq = 6; end.active = false; end.remainingMs = 0;
    CHECK(f.apply(end, 4100));
    CHECK(!f.active());
}

TEST(vats_slowmo, a_stale_or_replayed_grant_changes_nothing) {
    SlowMoFollower f;
    SlowMoGrant g;
    g.epoch = 2; g.seq = 10; g.active = false;
    f.apply(g, 100);
    SlowMoGrant old;
    old.epoch = 2; old.seq = 7; old.active = true; old.scale = 0.2f; old.remainingMs = 3000;
    CHECK(!f.apply(old, 200));
    CHECK(!f.active());
    CHECK(f.stale() == 1);
    SlowMoGrant oldEpoch = old; oldEpoch.epoch = 1; oldEpoch.seq = 99;
    CHECK(!f.apply(oldEpoch, 300));                     // an older host session loses against a newer one whatever its seq
    CHECK(!f.active());
    SlowMoGrant newer; newer.epoch = 3; newer.seq = 1; newer.active = true; newer.scale = 0.5f; newer.remainingMs = 1000;
    CHECK(f.apply(newer, 400));                         // a new host session starts counting again from 1
    CHECK(f.active());
}

TEST(vats_slowmo, forged_numbers_cannot_freeze_or_hold_the_party) {
    SlowMoFollower f;
    SlowMoGrant g;
    g.epoch = 1; g.seq = 1; g.active = true; g.scale = 0.0f; g.remainingMs = 4000000000u;
    f.apply(g, 1000);
    CHECK(f.active());
    CHECK(f.scale() >= 0.10f);                          // a frozen world is refused
    CHECK(f.tick(1000 + 10000 + 751));                  // an hour-long remaining time is cut to the cap
    CHECK(!f.active());
}

TEST(vats_slowmo, clear_forgets_the_session) {
    SlowMoFollower f;
    SlowMoGrant g;
    g.epoch = 5; g.seq = 9; g.active = true; g.scale = 0.5f; g.remainingMs = 1000;
    f.apply(g, 0);
    CHECK(f.clear());
    CHECK(!f.active() && f.scale() == 1.0f);
    SlowMoGrant fresh; fresh.epoch = 1; fresh.seq = 1; fresh.active = true; fresh.scale = 0.5f; fresh.remainingMs = 1000;
    CHECK(f.apply(fresh, 10));                          // a new host after clear(): the old epoch does not block it
}

}  // namespace
