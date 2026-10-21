// Companions across machines: recruit, caps, teammate flags, owner-only commands, the wheel gate, inventory mirroring, join, digest heal
// (twins 901 902 909 910 911 912 913 914 915 916 917 921 922 923 924 927 928).
#include "harness.h"

using namespace stest;

namespace {
const FormRef BOONE = npcForm(1), CASS = npcForm(2), DOG = npcForm(3), RAUL = npcForm(4), ARCADE = npcForm(5);

// 3 machines: host (0), guest 1, guest 2. Five NPCs everybody knows; each is simulated by `owner` (the actor layer follows the recruiter).
void world(Cluster& c) {
    c.addNpc(BOONE, 11, 1);
    c.addNpc(CASS, 12, 0);
    c.addNpc(DOG, 13, 1);
    c.addNpc(RAUL, 14, 2);
    c.addNpc(ARCADE, 15, 2);
}
}  // namespace

TEST(companions_flow, a_recruit_reaches_everybody_and_the_actor_layer) {   // twin 923
    Cluster c(3);
    world(c);
    c.s(0).onEffect(recruitFx(BOONE, 1));   // the host's capture sink saw a guest recruit Boone
    c.run(300);
    for (int i = 0; i < 3; ++i) CHECK(c.s(i).book().ownerOf(BOONE) == 1);
    CHECK(c.at(0).fed.size() == 1);   // the follower exception reached plugin/actors exactly once
    CHECK(c.s(0).book().digest() == c.s(1).book().digest() && c.s(0).book().digest() == c.s(2).book().digest());
    // a take-over by another player moves it everywhere
    c.s(0).onEffect(recruitFx(BOONE, 2));
    c.run(300);
    for (int i = 0; i < 3; ++i) CHECK(c.s(i).book().ownerOf(BOONE) == 2);
    // a repeat of the same fact changes nothing and feeds nothing
    const size_t fed = c.at(0).fed.size();
    c.s(0).onEffect(recruitFx(BOONE, 2));
    CHECK(c.at(0).fed.size() == fed);
}

TEST(companions_flow, a_recruit_beyond_the_cap_is_not_learnt_and_its_flag_comes_off) {   // twin 909
    Cluster c(3);
    world(c);
    c.s(0).onEffect(recruitFx(BOONE, 1, 0));
    c.run(200);
    // guest 1 recruits a second humanoid: the session drops it at commit time, but the host's engine ran the script and flagged it
    c.e(0).teammates.insert(RAUL);
    c.s(0).onEffect(recruitFx(RAUL, 1, 0));
    c.run(3500);
    CHECK(!c.s(0).book().isCompanion(RAUL));
    CHECK(c.s(0).stats().recruitsDropped == 1);
    CHECK(c.e(0).teammates.count(RAUL) == 0);      // the stray flag was taken off on the host
    CHECK(c.s(1).book().total() == 1 && c.s(2).book().total() == 1);
    CHECK(c.at(0).fed.size() == 1);
}

TEST(companions_flow, only_my_companions_may_be_flagged_on_my_machine) {   // twins 911, 912
    Cluster c(3);
    world(c);
    // guest 1 recruited Boone before the session: its engine has him flagged. It has not heard from the host yet.
    c.e(1).teammates.insert(BOONE);
    c.at(1).sync.reset(new SocialSync([&] { Config k; k.self = 1; k.isHost = false; return k; }(), c.at(1).world, c.at(1).eng, c.at(1), c.at(1)));
    c.s(1).tick(c.now + 10);
    c.s(1).tick(c.now + 2000);
    CHECK(c.e(1).teammates.count(BOONE) == 1);     // nothing happens before the host's picture arrives
    CHECK(c.e(1).teammateCalls.empty());
    // the host learns that Boone is guest 1's: the roster arrives, still flagged, still left alone
    c.s(0).playerLeft(1);
    c.s(0).playerJoined(1);
    c.s(0).onEffect(recruitFx(BOONE, 1));
    c.run(1500);
    CHECK(c.s(1).synced());
    CHECK(c.e(1).teammates.count(BOONE) == 1);
    // a flag for somebody else's companion IS taken off: Cass is the host's, guest 1's engine has her flagged by mistake
    c.s(0).onEffect(recruitFx(CASS, 0));
    c.e(1).teammates.insert(CASS);
    c.run(1500);
    CHECK(c.e(1).teammates.count(CASS) == 0 && c.e(1).teammates.count(BOONE) == 1);
    // the host's own companion gets flagged on the host if the engine lost the flag
    CHECK(c.e(0).teammates.count(CASS) == 1);
    // cooldown: an engine that refuses to unflag is not hammered every pass
    c.e(2).teammateWorks = false;
    c.e(2).teammates.insert(DOG);
    c.e(2).teammateCalls.clear();
    c.s(0).onEffect(recruitFx(DOG, 1, 1));
    c.run(7000);
    CHECK(c.e(2).teammateCalls.size() >= 1 && c.e(2).teammateCalls.size() <= 3);
}

TEST(companions_flow, a_player_who_leaves_releases_their_companions) {   // twin 910
    Cluster c(3);
    world(c);
    c.s(0).onEffect(recruitFx(BOONE, 1, 0));
    c.s(0).onEffect(recruitFx(DOG, 1, 1));
    c.s(0).onEffect(recruitFx(CASS, 0, 0));
    c.run(300);
    CHECK(c.s(2).book().total() == 3);
    c.s(0).playerLeft(1);
    c.run(300);
    CHECK(c.s(0).book().total() == 1 && c.s(2).book().total() == 1 && c.s(2).book().isCompanion(CASS));
    // the dismisses were committed through the story session (the shadow state and a rejoiner learn them) and fed to the actor layer
    size_t dismissCommits = 0;
    for (auto& b : c.commits) for (auto& e : b.fx) if (const auto* o = std::get_if<fnvcoop::CompanionOwner>(&e)) if (!o->active && o->owner == 1) ++dismissCommits;
    CHECK(dismissCommits == 2);
    size_t fedDismiss = 0;
    for (auto& e : c.at(0).fed) if (const auto* o = std::get_if<fnvcoop::CompanionOwner>(&e)) if (!o->active) ++fedDismiss;
    CHECK(fedDismiss == 2);
}

TEST(companions_flow, commands_are_owner_only_and_state_reaches_everybody) {   // twins 916, 917
    Cluster c(3);
    world(c);
    c.s(0).onEffect(recruitFx(BOONE, 1));
    c.run(300);
    // the owner tells Boone to wait: applied to its engine at once, then the host tells everyone
    CHECK(c.s(1).issueCommand(sc::CmdKind::Wait, BOONE));
    c.run(300);
    REQUIRE(c.e(1).stateCalls.size() == 1);
    CHECK(c.e(1).stateCalls[0].second.mode == sc::FollowMode::Wait && c.e(1).stateCalls[0].second.rev == 1);
    for (int i = 0; i < 3; ++i) CHECK(c.s(i).book().find(BOONE)->state.mode == sc::FollowMode::Wait);
    CHECK(c.e(0).stateCalls.empty() && c.e(2).stateCalls.empty());   // nobody else runs his AI
    // a stranger's own machine refuses at once
    CHECK(!c.s(2).issueCommand(sc::CmdKind::Follow, BOONE));
    CHECK(c.s(2).stats().cmdRefused == 1);
    // ... and a forged command on the wire does nothing and is answered with a refusal
    sc::CompanionCmd forged;
    forged.id = (2ull << 48) | 99; forged.from = 2; forged.npc = BOONE; forged.kind = sc::CmdKind::Follow;
    c.inject(2, 0, sc::MCmd{forged});
    c.run(300);
    for (int i = 0; i < 3; ++i) CHECK(c.s(i).book().find(BOONE)->state.mode == sc::FollowMode::Wait);
    CHECK(c.s(2).stats().cmdNacked >= 1);
    // an owner command that changes nothing is a no-op for everyone
    CHECK(c.s(1).issueCommand(sc::CmdKind::Wait, BOONE));
    c.run(300);
    CHECK(c.e(1).stateCalls.size() == 1);
}

TEST(companions_flow, an_unmapped_engine_state_is_counted_not_lost) {
    Cluster c(2);
    world(c);
    c.e(1).stateMappable = false;
    c.s(0).onEffect(recruitFx(BOONE, 1));
    c.run(300);
    CHECK(c.s(1).issueCommand(sc::CmdKind::SetStance, BOONE, 2));
    c.run(300);
    CHECK(c.s(1).stats().stateUnmapped == 1);
    CHECK(c.s(0).book().find(BOONE)->state.stance == sc::Stance::Aggressive);   // the shared state still travelled
    // when the engine can, the next roster applies it
    c.e(1).stateMappable = true;
    c.s(0).onEffect(recruitFx(DOG, 1, 1));   // any roster change makes the host send the table again
    c.run(300);
    REQUIRE(!c.e(1).stateCalls.empty());
    CHECK(c.e(1).stateCalls.back().second.stance == sc::Stance::Aggressive);
}

TEST(companions_flow, dismiss_and_summon_belong_to_the_owner) {
    Cluster c(3);
    world(c);
    c.s(0).onEffect(recruitFx(BOONE, 1));
    c.s(0).onEffect(recruitFx(CASS, 0));
    c.run(300);
    CHECK(c.s(1).issueCommand(sc::CmdKind::Summon, BOONE));
    CHECK(c.e(1).summons.size() == 1);
    CHECK(!c.s(2).issueCommand(sc::CmdKind::Summon, BOONE));
    CHECK(c.e(2).summons.empty());
    c.run(200);
    CHECK(c.s(1).issueCommand(sc::CmdKind::Dismiss, BOONE));
    c.run(300);
    for (int i = 0; i < 3; ++i) CHECK(!c.s(i).book().isCompanion(BOONE));
    CHECK(c.s(0).book().isCompanion(CASS));
    // the host's own commands go out too
    CHECK(c.s(0).issueCommand(sc::CmdKind::Wait, CASS));
    c.run(300);
    CHECK(c.s(2).book().find(CASS)->state.mode == sc::FollowMode::Wait);
}

TEST(companions_flow, the_wheel_opens_only_for_the_owner_unless_the_host_allows_it) {   // twins 924, 927
    Cluster c(3);
    world(c);
    c.s(0).onEffect(recruitFx(BOONE, 1));
    c.run(300);
    CHECK(c.s(0).allowActivation(1, BOONE));            // the owner
    CHECK(!c.s(0).allowActivation(2, BOONE));           // a stranger (the host judging a forwarded activation)
    CHECK(!c.s(2).allowActivation(2, BOONE));           // ... and on the stranger's own machine
    CHECK(c.s(2).allowActivation(2, RAUL));             // anybody else is just an NPC
    CHECK(c.s(0).stats().activationsDenied == 1);
    sc::SocialSettings s = c.s(0).settings();
    s.foreignCompanions = sc::ForeignPolicy::Allow;
    c.s(0).setSettings(s);
    c.run(300);
    CHECK(c.s(2).settings().foreignCompanions == sc::ForeignPolicy::Allow && c.s(2).settings().rev == c.s(0).settings().rev);
    CHECK(c.s(2).allowActivation(2, BOONE));
}

TEST(companions_flow, the_party_does_not_hit_itself_through_the_wire_view) {   // twin 928
    Cluster c(3);
    world(c);
    c.s(0).onEffect(recruitFx(BOONE, 1));
    c.s(0).onEffect(recruitFx(RAUL, 2));
    c.run(300);
    const cc::Entity boone = cc::Entity::actor(11), raul = cc::Entity::actor(14), rat = cc::Entity::actor(99);
    CHECK(c.s(2).hitPolicy(cc::Entity::player(2), boone) == sc::HitPolicy::Neutralize);   // a player shoots somebody else's companion
    CHECK(c.s(2).hitPolicy(boone, raul) == sc::HitPolicy::Neutralize);
    CHECK(c.s(2).hitPolicy(cc::Entity::player(2), rat) == sc::HitPolicy::Allow);
    CHECK(c.s(2).hitPolicy(rat, boone) == sc::HitPolicy::Allow);
    CHECK(!c.s(2).vatsTargetAllowed(boone) && !c.s(2).vatsTargetAllowed(cc::Entity::player(1)) && c.s(2).vatsTargetAllowed(rat));
    CHECK(c.s(2).responsiblePlayer(boone) == 1 && c.s(2).responsiblePlayer(rat) == kNoPlayer && c.s(2).responsiblePlayer(cc::Entity::player(2)) == 2);
    sc::SocialSettings s = c.s(0).settings();
    s.friendlyFire = true;
    c.s(0).setSettings(s);
    c.run(300);
    CHECK(c.s(2).hitPolicy(cc::Entity::player(2), boone) == sc::HitPolicy::Allow);
}

TEST(companions_flow, the_owners_inventory_changes_reach_the_other_copies_once) {   // twins 913, 914, 915
    Cluster c(3);
    world(c);
    c.s(0).onEffect(recruitFx(BOONE, 1));
    c.run(300);
    // the owner's engine: Boone starts with a stimpak; the first sample is the baseline
    c.e(1).inventory[BOONE][itemForm(1)] = 1;
    c.run(1200);
    CHECK(c.s(1).stats().invSent == 0);
    // the owner gives Boone a rifle and 50 rounds, and takes the stimpak back
    c.e(1).inventory[BOONE][itemForm(2)] = 1;
    c.e(1).inventory[BOONE][itemForm(3)] = 50;
    c.e(1).inventory[BOONE].erase(itemForm(1));
    c.run(1500);
    CHECK(c.s(1).stats().invSent == 3);
    // the host's and the other guest's copies follow; the owner's engine is not touched by the echo
    for (int i : {0, 2}) {
        CHECK(c.e(i).inventory[BOONE][itemForm(2)] == 1);
        CHECK(c.e(i).inventory[BOONE][itemForm(3)] == 50);
        CHECK(c.e(i).inventory[BOONE][itemForm(1)] == -1);   // a base item taken away is a negative change
        CHECK(c.e(i).invCalls.size() == 3);
    }
    CHECK(c.e(1).invCalls.empty());
    // a delta that arrives again changes nothing
    sc::InvDelta d;
    d.txn = (1ull << 48) | 100; d.npc = BOONE; d.item = itemForm(9); d.delta = 7;
    c.inject(0, 2, sc::MInv{d});
    const size_t once = c.e(2).invCalls.size();
    CHECK(c.e(2).inventory[BOONE][itemForm(9)] == 7);
    c.inject(0, 2, sc::MInv{d});
    CHECK(c.e(2).invCalls.size() == once && c.e(2).inventory[BOONE][itemForm(9)] == 7);
    // the host's echo to the owner is ignored
    sc::InvDelta mine; mine.txn = (0ull << 48) | 77; mine.npc = BOONE; mine.item = itemForm(9); mine.delta = 5;
    c.inject(0, 1, sc::MInv{mine});
    CHECK(c.e(1).invCalls.empty());
}

TEST(companions_flow, a_stranger_cannot_change_someone_elses_companions_inventory) {
    Cluster c(3);
    world(c);
    c.s(0).onEffect(recruitFx(BOONE, 1));
    c.run(300);
    sc::InvDelta d; d.txn = (2ull << 48) | 1; d.npc = BOONE; d.item = itemForm(7); d.delta = 100;
    c.inject(2, 0, sc::MInv{d});
    c.run(300);
    CHECK(c.s(0).stats().invRejected == 1 && c.s(0).inventory().count(BOONE, itemForm(7)) == 0);
    CHECK(c.e(0).invCalls.empty() && c.e(1).invCalls.empty());
    sc::InvFull f; f.npc = BOONE; f.items = {{itemForm(7), 100}};
    c.inject(2, 0, sc::MInvFull{f});
    c.run(300);
    CHECK(c.s(0).inventory().count(BOONE, itemForm(7)) == 0 && c.e(0).invCalls.empty());
}

TEST(companions_flow, a_late_joiner_gets_the_picture_and_the_inventory) {   // twin 922
    Cluster late(3, nullptr, false);
    late.addNpc(BOONE, 11, 1);
    late.s(0).playerJoined(1);
    late.s(1).hostConnected();
    late.s(0).onEffect(recruitFx(BOONE, 1));
    late.e(1).inventory[BOONE][itemForm(1)] = 1;
    late.run(1200);
    late.e(1).inventory[BOONE][itemForm(2)] = 4;
    late.run(1500);
    CHECK(late.s(0).inventory().count(BOONE, itemForm(2)) == 4);
    sc::SocialSettings st = late.s(0).settings();
    st.xpMode = cc::XpMode::PartyShare;
    late.s(0).setSettings(st);
    // guest 2 joins late
    late.s(0).playerJoined(2);
    late.s(2).hostConnected();
    late.run(500);
    CHECK(late.s(2).synced());
    CHECK(late.s(2).book().ownerOf(BOONE) == 1);
    CHECK(late.s(2).settings().xpMode == cc::XpMode::PartyShare);
    CHECK(late.e(2).inventory[BOONE][itemForm(2)] == 4);   // what the owner gave him since the recruit, exactly once
}

TEST(companions_flow, a_difference_found_by_the_digest_is_healed) {   // twin 921
    Cluster c(3);
    world(c);
    c.s(0).onEffect(recruitFx(BOONE, 1));
    c.run(300);
    // guest 2's book rots (a lost message in the real world): the host's next digest shows it and one resync fixes it
    sc::MRoster bogus;
    sc::Companion x; x.npc = DOG; x.owner = 2; x.kind = sc::CompanionKind::Critter;
    bogus.all = {x};
    c.inject(0, 2, bogus);
    CHECK(c.s(2).book().digest() != c.s(0).book().digest());
    c.run(6500);
    CHECK(c.s(2).book().digest() == c.s(0).book().digest());
    CHECK(c.s(2).stats().digestMismatch >= 1 && c.s(2).stats().resyncsAsked >= 1);
}

TEST(companions_flow, only_the_hosts_datagrams_count_on_a_client_and_the_senders_slot_on_the_host) {   // twins 901, 902
    Cluster c(3);
    world(c);
    // a datagram that claims to be a roster but comes from guest 2's transport slot
    sc::Companion x; x.npc = DOG; x.owner = 2; x.kind = sc::CompanionKind::Critter;
    sc::MRoster bogus;
    bogus.all = {x};
    c.inject(2, 1, bogus);
    CHECK(!c.s(1).book().isCompanion(DOG) && c.s(1).stats().notFromHost == 1);
    // guest 1 sends a command for guest 2's identity: the host replaces the claimed author with the transport slot
    c.s(0).onEffect(recruitFx(RAUL, 2));
    c.run(200);
    sc::CompanionCmd cmd;
    cmd.id = (2ull << 48) | 5; cmd.from = 2; cmd.npc = RAUL; cmd.kind = sc::CmdKind::Wait;
    c.inject(1, 0, sc::MCmd{cmd}, 2);   // transport says slot 1, the message says it is slot 2
    c.run(200);
    CHECK(c.s(0).stats().spoofed == 1);
    CHECK(c.s(0).book().find(RAUL)->state.mode == sc::FollowMode::Follow);
    // a datagram from a peer that never joined
    Cluster lone(3, nullptr, false);
    sc::MResync r;
    lone.inject(1, 0, r);
    CHECK(lone.s(0).stats().dropped == 1);
}

TEST(companions_flow, the_hosts_own_followers_from_before_the_session_are_adopted_once) {
    Cluster c(3, nullptr, true, false);
    world(c);
    c.e(0).teammates.insert(CASS);       // loaded a save with Cass already in the party
    c.run(300);
    CHECK(c.s(0).book().ownerOf(CASS) == 0 && c.s(1).book().ownerOf(CASS) == 0 && c.s(2).book().ownerOf(CASS) == 0);
    CHECK(c.at(0).fed.size() == 1);
    // a flag that appears later and that the book does not know is a stray: it comes off
    c.e(0).teammates.insert(RAUL);
    c.run(2500);
    CHECK(c.e(0).teammates.count(RAUL) == 0 && c.e(0).teammates.count(CASS) == 1);
    CHECK(!c.s(0).book().isCompanion(RAUL));
}

TEST(companions_flow, a_companion_that_dies_frees_its_recruiters_slot) {   // twin 932
    Cluster c(3);
    world(c);
    c.s(0).onEffect(recruitFx(BOONE, 1));
    c.run(300);
    CHECK(c.s(2).book().isCompanion(BOONE));
    for (int i = 0; i < 3; ++i) c.at(i).world.npcs[BOONE].alive = false;   // the actor layer declared his death everywhere
    c.run(2500);
    for (int i = 0; i < 3; ++i) CHECK(!c.s(i).book().isCompanion(BOONE));
    CHECK(c.committedCount("companion-died") == 1);
    // the slot is free again: guest 1 can recruit another humanoid
    c.s(0).onEffect(recruitFx(DOG, 1, 0));
    c.run(300);
    CHECK(c.s(1).book().ownerOf(DOG) == 1);
}
