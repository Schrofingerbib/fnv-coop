// Crime: detections, witnesses, the host's judge, bounty (wanted score), alarms, hostility, pardon, guest interactions (twins 831..854).
#include "util.h"

using namespace social_test;

namespace {
const FormRef F = faction(1);   // the victim's crime faction
// The first bounty update of a decision, or an empty one (a broken build must FAIL a check here, never crash on an empty vector).
const BountyUpdate& b0(const CrimeDecision& d) { static const BountyUpdate none; return d.bounty.empty() ? none : d.bounty[0]; }
struct Rig {
    CrimeJudge judge;
    DetectionBook det{6000};
    explicit Rig(CrimeRules r = defaultCrimeRules()) : judge(r, kHostPlayer) {}
};
}  // namespace

TEST(crime, a_witnessed_assault_adds_to_the_score_and_raises_the_alarm) {   // twins 840, 852
    Rig r;
    r.det.report(sees(7, F, 1), 1000);
    CrimeDecision d = r.judge.judge(crime(1, CrimeKind::Assault, 1, F), 1, 1500, r.det);
    CHECK(d.counted() && d.witnessed && d.blamed == 1);
    REQUIRE(d.bounty.size() == 1);
    CHECK(d.bounty[0].faction == F && d.bounty[0].st.bounty == 25 && d.bounty[0].st.attack && !d.bounty[0].st.murder && !d.bounty[0].st.enemy);
    CHECK(d.bounty[0].st.rev == 1);
    CHECK(d.hostility.empty());
    REQUIRE(d.alarms.size() == 1);
    CHECK(d.alarms[0].faction == F && d.alarms[0].cell == 5 && d.alarms[0].criminal == 1 && d.alarms[0].untilMs == 1500 + 30000);
    // a replica applying the host's updates in order, then a second one: both must land (revisions rise)
    BountyBook replica;
    CHECK(replica.apply(d.bounty[0]));
    r.det.report(sees(7, F, 1), 9000);
    CrimeDecision d2 = r.judge.judge(crime(2, CrimeKind::Assault, 1, F), 1, 10000, r.det);
    REQUIRE(d2.bounty.size() == 1);
    CHECK(d2.bounty[0].st.bounty == 50 && d2.bounty[0].st.rev == 2);
    CHECK(replica.apply(d2.bounty[0]));
    CHECK(replica.get(F).bounty == 50);
    CHECK(replica.digest() == r.judge.bounty().digest());
    // the second assault happened with an alarm still in force for (F, cell 5): no second alarm
    CHECK(d2.alarms.empty());
}

TEST(crime, a_witnessed_murder_turns_the_faction_hostile_at_once) {   // twin 839
    CrimeRules rules = defaultCrimeRules();
    rules.bounty[static_cast<size_t>(CrimeKind::Murder)] = 50;   // below hostileAtBounty: only the murder rule can flip the faction
    Rig r(rules);
    r.det.report(sees(8, F, 2), 1000);
    CrimeEvent e = crime(1, CrimeKind::Murder, 2, F);
    e.victimDead = true;
    CrimeDecision d = r.judge.judge(e, 2, 1200, r.det);
    CHECK(d.counted());
    REQUIRE(d.bounty.size() == 1);
    CHECK(d.bounty[0].st.murder && d.bounty[0].st.enemy && d.bounty[0].st.attack && d.bounty[0].st.bounty == 50);
    REQUIRE(d.hostility.size() == 1);
    CHECK(d.hostility[0].kind == cc::HostilityKind::SetEnemy && d.hostility[0].factionA == playerFaction() && d.hostility[0].factionB == F && d.hostility[0].cause == 2);
    CHECK(r.judge.hostility().hostile(playerFaction(), F));
    REQUIRE(d.reputation.size() == 1);
    CHECK(d.reputation[0].faction == F && d.reputation[0].infamy == 2 && d.reputation[0].fame == 0);
    CHECK(d.alarms.size() == 1);
}

TEST(crime, only_a_witness_of_the_right_faction_in_the_right_cell_and_time_counts) {   // twins 837, 838, 848, 849, 850
    Rig r;
    // nobody saw it (a theft: no victim to witness it)
    CrimeEvent theft = crime(1, CrimeKind::Theft, 1, F, cc::Entity());
    CrimeDecision d = r.judge.judge(theft, 1, 1000, r.det);
    CHECK(!d.counted() && d.reason == CrimeReason::Unwitnessed && d.bounty.empty() && d.alarms.empty());
    // a witness of another faction
    r.det.report(sees(9, faction(2), 1), 2000);
    CHECK(r.judge.judge(crime(2, CrimeKind::Theft, 1, F, cc::Entity()), 1, 2100, r.det).reason == CrimeReason::Unwitnessed);
    // the right faction but another cell
    r.det.report(sees(10, F, 1, 99), 2000);
    CHECK(r.judge.judge(crime(3, CrimeKind::Theft, 1, F, cc::Entity()), 1, 2200, r.det).reason == CrimeReason::Unwitnessed);
    // seeing somebody else
    r.det.report(sees(11, F, 2), 2000);
    CHECK(r.judge.judge(crime(4, CrimeKind::Theft, 1, F, cc::Entity()), 1, 2300, r.det).reason == CrimeReason::Unwitnessed);
    // the right one, but long ago
    r.det.report(sees(12, F, 1), 1000);
    CHECK(r.judge.judge(crime(5, CrimeKind::Theft, 1, F, cc::Entity()), 1, 1000 + 6001, r.det).reason == CrimeReason::Unwitnessed);
    // fresh
    r.det.report(sees(12, F, 1), 20000);
    CrimeDecision ok = r.judge.judge(crime(6, CrimeKind::Theft, 1, F, cc::Entity()), 1, 21000, r.det);
    CHECK(ok.counted() && b0(ok).st.bounty == 15 && !b0(ok).st.attack && !b0(ok).st.enemy);
    // the witness died: it no longer witnesses
    Rig r2;
    r2.det.report(sees(12, F, 1), 20000);
    r2.det.forget(cc::Entity::actor(12));
    CHECK(r2.judge.judge(crime(7, CrimeKind::Theft, 1, F, cc::Entity()), 1, 20500, r2.det).reason == CrimeReason::Unwitnessed);
    // a lost sight clears the entry
    Rig r3;
    r3.det.report(sees(12, F, 1), 20000);
    Detection lost = sees(12, F, 1); lost.seen = false;
    r3.det.report(lost, 20100);
    CHECK(r3.det.size() == 0);
}

TEST(crime, detection_by_any_players_machine_counts_and_a_surviving_victim_is_a_witness) {
    Rig r;
    // an NPC seen by player 2 witnesses player 2's crime, whoever's machine reported the sighting
    r.det.report(sees(20, F, 2), 1000);
    CHECK(r.judge.judge(crime(1, CrimeKind::Theft, 2, F, cc::Entity()), 2, 1500, r.det).counted());
    // the host reporting on behalf of the guest (stealing handled through the host)
    CrimeEvent viaHost = crime(2, CrimeKind::Theft, 2, F, cc::Entity());
    viaHost.reporter = kHostPlayer;
    r.det.report(sees(20, F, 2), 20000);
    CHECK(r.judge.judge(viaHost, kHostPlayer, 20500, r.det).counted());
    // an assault on a living person is seen by that person
    Rig r2;
    CHECK(r2.judge.judge(crime(3, CrimeKind::Assault, 1, F, cc::Entity::actor(7)), 1, 1000, r2.det).counted());
    // ... a murder is not (it is dead)
    CrimeEvent murder = crime(4, CrimeKind::Murder, 1, F, cc::Entity::actor(7));
    murder.victimDead = true;
    CHECK(r2.judge.judge(murder, 1, 9000, r2.det).reason == CrimeReason::Unwitnessed);
    CrimeRules noVictim = defaultCrimeRules();
    noVictim.victimWitnesses = false;
    Rig r3(noVictim);
    CHECK(r3.judge.judge(crime(5, CrimeKind::Assault, 1, F, cc::Entity::actor(7)), 1, 1000, r3.det).reason == CrimeReason::Unwitnessed);
}

TEST(crime, not_every_act_is_a_crime) {   // twins 832, 833, 834, 835
    Rig r;
    r.det.report(sees(7, F, 1), 1000);
    // framing: player 2 reports player 1's crime
    CHECK(r.judge.judge(crime(1, CrimeKind::Assault, 1, F), 2, 1100, r.det).reason == CrimeReason::BadReporter);
    // the victim was already hostile
    CrimeEvent hostile = crime(2, CrimeKind::Assault, 1, F);
    hostile.victimHostile = true;
    CHECK(r.judge.judge(hostile, 1, 1200, r.det).reason == CrimeReason::NotACrime);
    // the party's own faction
    CHECK(r.judge.judge(crime(3, CrimeKind::Assault, 1, playerFaction()), 1, 1300, r.det).reason == CrimeReason::OwnFaction);
    // no faction cares
    CHECK(r.judge.judge(crime(4, CrimeKind::Assault, 1, FormRef()), 1, 1400, r.det).reason == CrimeReason::NoFaction);
    // a slot that does not exist
    CHECK(r.judge.judge(crime(5, CrimeKind::Assault, 9, F), 9, 1500, r.det).reason == CrimeReason::BadReporter);
    // the real one counts once; the same report again does not
    CHECK(r.judge.judge(crime(6, CrimeKind::Assault, 1, F), 1, 1600, r.det).counted());
    CHECK(r.judge.judge(crime(6, CrimeKind::Assault, 1, F), 1, 9600, r.det).reason == CrimeReason::Duplicate);
    // nothing was booked for the refused ones
    CHECK(r.judge.bounty().get(F).bounty == 25);
    // a companion's crime when companions' crimes do not count
    CrimeRules lenient = defaultCrimeRules();
    lenient.companionCrimesCount = false;
    Rig r2(lenient);
    r2.det.report(sees(7, F, 1), 1000);
    CrimeEvent byComp = crime(7, CrimeKind::Assault, 1, F);
    byComp.byCompanion = true;
    CHECK(r2.judge.judge(byComp, 1, 1100, r2.det).reason == CrimeReason::NotACrime);
}

TEST(crime, a_burst_is_one_crime) {   // twin 836
    Rig r;
    r.det.report(sees(7, F, 1), 1000);
    CHECK(r.judge.judge(crime(1, CrimeKind::Assault, 1, F), 1, 1000, r.det).counted());
    for (uint64_t i = 2; i < 30; ++i) CHECK(r.judge.judge(crime(i, CrimeKind::Assault, 1, F), 1, 1000 + i * 10, r.det).reason == CrimeReason::Burst);
    CHECK(r.judge.bounty().get(F).bounty == 25);
    // after the window it is a new assault
    r.det.report(sees(7, F, 1), 5000);
    CHECK(r.judge.judge(crime(100, CrimeKind::Assault, 1, F), 1, 5000, r.det).counted());
    CHECK(r.judge.bounty().get(F).bounty == 50);
    // a different crime inside the window is not part of the burst
    CrimeEvent murder = crime(101, CrimeKind::Murder, 1, F);
    murder.victimDead = true;
    CHECK(r.judge.judge(murder, 1, 5100, r.det).counted());
    // a burst of one player does not hide another player's crime
    r.det.report(sees(7, F, 2), 5100);
    CHECK(r.judge.judge(crime(102, CrimeKind::Assault, 2, F), 2, 5150, r.det).counted());
}

TEST(crime, enough_small_crimes_turn_the_faction_hostile) {
    CrimeRules rules = defaultCrimeRules();
    rules.hostileAtBounty = 30;
    Rig r(rules);
    r.det.report(sees(7, F, 1), 1000);
    CrimeDecision a = r.judge.judge(crime(1, CrimeKind::Theft, 1, F, cc::Entity()), 1, 1000, r.det);
    CHECK(a.counted() && !b0(a).st.enemy && a.hostility.empty() && a.alarms.empty());
    r.det.report(sees(7, F, 1), 5000);
    CrimeDecision b = r.judge.judge(crime(2, CrimeKind::Theft, 1, F, cc::Entity()), 1, 5000, r.det);
    CHECK(b.counted() && b0(b).st.enemy && b0(b).st.bounty == 30);
    CHECK(b.hostility.size() == 1 && !b.alarms.empty());
    r.det.report(sees(7, F, 1), 9000);
    CrimeDecision c = r.judge.judge(crime(3, CrimeKind::Theft, 1, F, cc::Entity()), 1, 9000, r.det);
    CHECK(c.counted() && c.hostility.empty());   // already hostile: nothing new
    // the ceiling
    CrimeRules cap = defaultCrimeRules();
    cap.maxBounty = 40;
    BountyBook bb;
    CHECK(bb.add(F, 100, true, false, false, cap.maxBounty).st.bounty == 40);   // twin 851
}

TEST(crime, a_pardon_clears_the_score_the_flags_the_war_and_the_alarm) {   // twins 841, 853
    Rig r;
    r.det.report(sees(7, F, 1), 1000);
    CrimeEvent murder = crime(1, CrimeKind::Murder, 1, F);
    murder.victimDead = true;
    CHECK(r.judge.judge(murder, 1, 1000, r.det).counted());
    CHECK(r.judge.bounty().get(F).enemy);
    CrimeDecision p = r.judge.forgive(F, 0);
    REQUIRE(p.bounty.size() == 1);
    CHECK(p.bounty[0].st.bounty == 0);
    const FactionStanding st = r.judge.bounty().get(F);
    CHECK(st.bounty == 0 && !st.attack && !st.murder && !st.enemy && st.rev == 2);
    REQUIRE(p.hostility.size() == 1);
    CHECK(p.hostility[0].kind == cc::HostilityKind::SetAlly && !r.judge.hostility().hostile(playerFaction(), F));
    // pardoning a clean faction does nothing
    CHECK(r.judge.forgive(F, 0).bounty.empty());
    // the alarm of the pardoned faction is gone: the next crime in that cell raises a new one
    r.det.report(sees(7, F, 1), 20000);
    CrimeDecision again = r.judge.judge(crime(2, CrimeKind::Assault, 1, F), 1, 20000, r.det);
    CHECK(again.counted() && again.alarms.size() == 1);
    // a client cannot pardon
    CrimeJudge client(defaultCrimeRules(), 2);
    CHECK(client.forgive(F, 2).reason == CrimeReason::NotACrime);
}

TEST(crime, only_the_host_judges) {   // twin 831
    CrimeJudge client(defaultCrimeRules(), 2);
    DetectionBook det;
    det.report(sees(7, F, 2), 1000);
    CHECK(client.judge(crime(1, CrimeKind::Murder, 2, F), 2, 1100, det).reason == CrimeReason::NotACrime);
    CHECK(client.bounty().size() == 0);
}

TEST(crime, time_forgives_but_not_during_a_crime_spree) {   // twin 842
    CrimeRules rules = defaultCrimeRules();
    rules.decayPerMinute = 60;   // one point per second
    Rig r(rules);
    r.judge.tick(1000);
    r.det.report(sees(7, F, 1), 1000);
    CHECK(r.judge.judge(crime(1, CrimeKind::Theft, 1, F, cc::Entity()), 1, 1000, r.det).counted());
    CHECK(r.judge.tick(5000).empty());   // 4 s after the crime: still a spree
    std::vector<BountyUpdate> u = r.judge.tick(20000);
    REQUIRE(u.size() == 1);
    CHECK(u[0].st.bounty == 0 && u[0].st.rev == 2);   // 15 points, 15 s since the last tick: all forgiven
    CHECK(r.judge.tick(30000).empty());               // nothing left
    // decay off: nothing ever
    Rig off;
    off.judge.tick(1000);
    off.det.report(sees(7, F, 1), 1000);
    off.judge.judge(crime(1, CrimeKind::Theft, 1, F, cc::Entity()), 1, 1000, off.det);
    CHECK(off.judge.tick(900000).empty());
}

TEST(crime, a_stale_or_replayed_bounty_update_is_ignored) {   // twin 854
    BountyBook host, replica;
    BountyUpdate u1 = host.add(F, 25, true, false, false, 1000);
    BountyUpdate u2 = host.add(F, 25, true, false, false, 1000);
    CHECK(replica.apply(u2));
    CHECK(!replica.apply(u1));   // a late older update must not roll the replica back
    CHECK(replica.get(F).bounty == 50);
    CHECK(!replica.apply(u2));   // a duplicate
    BountyBook fresh;
    fresh.replace(host.roster());
    CHECK(fresh.digest() == host.digest());
}

TEST(crime, a_guests_interaction_becomes_a_crime_on_the_host) {   // twins 843, 844, 845, 846
    const std::set<FormRef> party = {playerFaction()};
    InteractionFacts f;
    f.what = Interaction::TakeItem;
    f.own.owned = true; f.own.ownerForm = npc(5); f.own.crimeFaction = F; f.own.value = 120;
    CrimeEvent e;
    CHECK(classifyInteraction(f, 2, kHostPlayer, 77, 5, {1, 2, 3}, party, e));
    CHECK(e.kind == CrimeKind::Theft && e.criminal == 2 && e.reporter == kHostPlayer && e.faction == F && e.value == 120 && e.id == 77);
    // nobody's thing
    InteractionFacts nobodys = f; nobodys.own.owned = false;
    CHECK(!classifyInteraction(nobodys, 2, 0, 1, 5, {}, party, e));
    // owned by the party's own faction
    InteractionFacts mine = f; mine.own.crimeFaction = playerFaction();
    CHECK(!classifyInteraction(mine, 2, 0, 1, 5, {}, party, e));
    // the owner says it is fine
    InteractionFacts ok = f; ok.ownerPresentAndFriendly = true;
    CHECK(!classifyInteraction(ok, 2, 0, 1, 5, {}, party, e));
    // lockpicking and hacking are break-ins, not thefts
    InteractionFacts pick = f; pick.what = Interaction::PickLock;
    CHECK(classifyInteraction(pick, 2, 0, 1, 5, {}, party, e) && e.kind == CrimeKind::BreakIn);
    pick.what = Interaction::HackTerminal;
    CHECK(classifyInteraction(pick, 2, 0, 1, 5, {}, party, e) && e.kind == CrimeKind::BreakIn);
    // opening an owned, unlocked container is fine; a locked one is a break-in
    InteractionFacts open = f; open.what = Interaction::OpenContainer;
    CHECK(!classifyInteraction(open, 2, 0, 1, 5, {}, party, e));
    open.locked = true;
    CHECK(classifyInteraction(open, 2, 0, 1, 5, {}, party, e) && e.kind == CrimeKind::BreakIn);
    // trespass: an owned bed, but not a public place
    InteractionFacts bed = f; bed.what = Interaction::UseBed;
    CHECK(classifyInteraction(bed, 2, 0, 1, 5, {}, party, e) && e.kind == CrimeKind::Trespass);
    bed.own.publicPlace = true;
    CHECK(!classifyInteraction(bed, 2, 0, 1, 5, {}, party, e));
    // vandalism and pickpocketing
    InteractionFacts van = f; van.what = Interaction::Destroy;
    CHECK(classifyInteraction(van, 2, 0, 1, 5, {}, party, e) && e.kind == CrimeKind::Vandalism);
    InteractionFacts pp; pp.what = Interaction::Pickpocket; pp.own.crimeFaction = F;
    CHECK(classifyInteraction(pp, 2, 0, 1, 5, {}, party, e, cc::Entity::actor(9)) && e.kind == CrimeKind::Pickpocket && e.victim == cc::Entity::actor(9));
    InteractionFacts pp2; pp2.what = Interaction::Pickpocket;
    CHECK(!classifyInteraction(pp2, 2, 0, 1, 5, {}, party, e));
    // an owner that is a faction itself
    InteractionFacts byFaction = f; byFaction.own.crimeFaction = FormRef(); byFaction.own.ownerIsFaction = true; byFaction.own.ownerForm = faction(4);
    CHECK(classifyInteraction(byFaction, 2, 0, 1, 5, {}, party, e) && e.faction == faction(4));
}

TEST(crime, wire_round_trips) {
    CrimeEvent e = crime(9, CrimeKind::BreakIn, 2, F);
    e.byCompanion = true; e.nativeMask = 0x05; e.value = 33; e.tick = 123; e.victimDead = true;
    CrimeEvent e2 = roundTrip(e);
    CHECK(e2.id == 9 && e2.kind == CrimeKind::BreakIn && e2.criminal == 2 && e2.byCompanion && e2.nativeMask == 5 && e2.value == 33 && e2.faction == F && e2.victim == e.victim && e2.where == e.where);
    Detection d = sees(3, F, 1);
    Detection d2 = roundTrip(d);
    CHECK(d2.observer == d.observer && d2.target == 1 && d2.faction == F && d2.cell == 5 && d2.seen);
    BountyUpdate u; u.faction = F; u.st.bounty = 40; u.st.attack = true; u.st.enemy = true; u.st.rev = 3;
    CHECK(roundTrip(u) == u);
    AlarmOrder a; a.id = 5; a.faction = F; a.cell = 7; a.where = {1, 2, 3}; a.criminal = 1; a.untilMs = 99999;
    CHECK(roundTrip(a) == a);
    // out of range slots and kinds are refused
    CrimeEvent badKind = e; badKind.kind = static_cast<CrimeKind>(40);
    CHECK(roundTrip(badKind).id == 0);
}
