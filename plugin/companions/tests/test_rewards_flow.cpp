// Kill rewards across machines: paid once, to the right players, by the host's settings (twins 904 905 906 931 and the synccore/social reward twins).
#include "harness.h"

using namespace stest;

namespace {
sc::KillReport killBy(uint64_t id, PlayerId killer, int32_t xp = 100, sc::KarmaClass kc = sc::KarmaClass::Unknown) {
    sc::KillReport k;
    k.deathId = id;
    k.victim.who = cc::Entity::actor(100);
    k.victim.xp = xp;
    k.victim.karma = kc;
    k.killer = cc::Entity::player(killer);
    k.cell = 1;
    k.where = {0, 0, 0};
    return k;
}
}  // namespace

TEST(rewards_flow, a_kill_pays_the_killing_blow_once_however_many_machines_report_it) {   // twins 904, 906
    Cluster c(3);
    c.s(1).reportKill(killBy(7, 1));   // the killer's machine
    c.run(300);
    c.s(0).reportKill(killBy(7, 1));   // the victim's owner (the host) reports the same death
    c.s(2).reportKill(killBy(7, 1));   // ... and so does a bystander
    c.run(300);
    CHECK(c.e(1).xp == 100 && c.e(1).xpCalls == 1);
    CHECK(c.e(0).xp == 0 && c.e(2).xp == 0);
    CHECK(c.s(0).stats().killsSettled == 1);
    CHECK(c.committedCount("kill") == 1);
}

TEST(rewards_flow, the_hosts_own_part_is_applied_directly_and_not_committed) {   // twin 905
    Cluster c(3);
    c.s(0).reportKill(killBy(8, 0));
    c.run(300);
    CHECK(c.e(0).xp == 100 && c.e(0).xpCalls == 1);
    CHECK(c.e(1).xp == 0 && c.e(2).xp == 0);
    CHECK(c.commits.empty());   // nothing for the story session to deliver
    CHECK(c.s(0).stats().xpLocal == 1);
}

TEST(rewards_flow, party_share_is_the_hosts_setting_and_follows_who_is_present) {
    Cluster c(3);
    sc::SocialSettings s = c.s(0).settings();
    s.xpMode = cc::XpMode::PartyShare;
    c.s(0).setSettings(s);
    c.run(200);
    c.s(2).reportKill(killBy(9, 2));
    c.run(300);
    CHECK(c.e(2).xp == 34 && c.e(0).xp == 33 && c.e(1).xp == 33);
    CHECK(c.e(0).xpCalls == 1 && c.e(1).xpCalls == 1 && c.e(2).xpCalls == 1);
    // only the players in the same cell share
    s.scope = sc::XpScope::SameCell;
    c.s(0).setSettings(s);
    c.at(0).world.places[1].cell = 2;   // guest 1 is somewhere else
    c.s(2).reportKill(killBy(10, 2));
    c.run(300);
    CHECK(c.e(2).xp == 34 + 50 && c.e(0).xp == 33 + 50 && c.e(1).xp == 33);
}

TEST(rewards_flow, a_player_the_host_cannot_place_still_shares_by_everybody_connected) {
    Cluster c(3);
    sc::SocialSettings s = c.s(0).settings();
    s.xpMode = cc::XpMode::PartyShare;
    c.s(0).setSettings(s);
    c.at(0).world.places.erase(c.at(0).world.places.begin() + 2);   // no puppet for guest 2 on the host
    c.run(200);
    c.s(1).reportKill(killBy(21, 1));
    c.run(300);
    CHECK(c.e(1).xp == 34 && c.e(0).xp == 33 && c.e(2).xp == 33);
    // ... but not when only "the same cell" counts: an unplaced player is not known to be there
    s.scope = sc::XpScope::SameCell;
    c.s(0).setSettings(s);
    c.s(1).reportKill(killBy(22, 1));
    c.run(300);
    CHECK(c.e(2).xp == 33 && c.e(0).xp == 33 + 50 && c.e(1).xp == 34 + 50);
}

TEST(rewards_flow, a_companions_kill_pays_its_recruiter) {
    Cluster c(3);
    sc::KillReport k = killBy(11, 0);
    k.killer = cc::Entity::actor(300);
    k.killerRecruiter = 2;
    c.s(0).reportKill(k);
    c.run(300);
    CHECK(c.e(2).xp == 100 && c.e(0).xp == 0 && c.e(1).xp == 0);
    // an NPC killing an NPC pays nobody
    sc::KillReport npc = killBy(12, 0);
    npc.killer = cc::Entity::actor(301);
    c.s(0).reportKill(npc);
    c.run(300);
    CHECK(c.e(0).xp == 0 && c.e(1).xp == 0 && c.e(2).xp == 100);
}

TEST(rewards_flow, what_the_killers_engine_paid_is_not_paid_again_and_karma_is_shared) {
    Cluster c(3);
    c.e(1).xp = 100;   // guest 1's own machine killed the good-natured victim natively: its engine paid XP and moved its karma
    c.e(1).karma = -3;
    sc::KillReport k = killBy(13, 1, 100, sc::KarmaClass::Good);
    k.nativeMask = 0x02;
    c.s(1).reportKill(k);
    c.run(300);
    CHECK(c.e(1).xp == 100 && c.e(1).xpCalls == 0 && c.e(1).karma == -3 && c.e(1).karmaCalls == 0);
    CHECK(c.e(0).karma == -3 && c.e(0).karmaCalls == 1);   // the party is one Courier: everybody else's karma follows
    CHECK(c.e(2).karma == -3 && c.e(2).karmaCalls == 1);
    // personal karma by the session's SharePolicy
    sc::SocialSettings s = c.s(0).settings();
    s.shareKarma = false;
    c.s(0).setSettings(s);
    c.run(200);
    c.s(2).reportKill(killBy(14, 2, 0, sc::KarmaClass::VeryGood));
    c.run(300);
    CHECK(c.e(2).karma == -3 - 5 && c.e(0).karma == -3 && c.e(1).karma == -3);
    // killing a hostile good-natured NPC costs nothing
    sc::KillReport hostile = killBy(15, 2, 0, sc::KarmaClass::Good);
    hostile.victim.hostileToParty = true;
    c.s(2).reportKill(hostile);
    c.run(300);
    CHECK(c.e(2).karma == -8);
}

TEST(rewards_flow, a_client_can_only_say_that_its_own_engine_paid) {   // twin 931
    Cluster c(3);
    sc::SocialSettings s = c.s(0).settings();
    s.xpMode = cc::XpMode::PartyShare;
    c.s(0).setSettings(s);
    c.run(200);
    sc::KillReport k = killBy(16, 1);
    k.nativeMask = 0x04;   // guest 1 claims that player 2's engine already paid: it did not
    c.s(1).reportKill(k);
    c.run(300);
    CHECK(c.e(2).xp == 33 && c.e(0).xp == 33 && c.e(1).xp == 34);
}

TEST(rewards_flow, a_failed_commit_is_counted_and_a_switched_off_system_is_silent) {
    Cluster c(3);
    c.commitWorks = false;
    c.s(1).reportKill(killBy(17, 1));
    c.run(300);
    CHECK(c.s(0).stats().commitFailed == 1 && c.e(1).xp == 0);
    Cluster off(3, [](Config& k) { k.rewards = false; });
    off.s(1).reportKill(killBy(18, 1));
    off.s(0).reportKill(killBy(18, 0));
    off.run(300);
    CHECK(off.e(0).xp == 0 && off.e(1).xp == 0 && off.s(0).stats().killsSettled == 0);
    CHECK(off.s(1).stats().killsReported == 0);
}
