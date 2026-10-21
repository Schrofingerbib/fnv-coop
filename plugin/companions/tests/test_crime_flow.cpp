// Crime across machines: witnesses from every machine, one decision on the host, one effect everywhere (twins 903 907 919 920 925 926 and the
// synccore/social crime twins through the choreography).
#include "harness.h"

using namespace stest;

namespace {
const FormRef NCR = factionForm(1);
const cc::Vec3 kHere = {10, 20, 30};

// guard A (net 21) simulated by the host, guard B (net 22) simulated by guest 2, a trooper (net 23) the victim, all NCR in cell 1
void townsfolk(Cluster& c) {
    c.addNpc(npcForm(21), 21, 0, NCR, {0, 0, 0});
    c.addNpc(npcForm(22), 22, 2, NCR, {50, 0, 0});
    c.addNpc(npcForm(23), 23, 0, NCR, {100, 0, 0});
}
sc::CrimeEvent crimeOf(sc::CrimeKind k, PlayerId who, uint32_t victimNet = 23) {
    sc::CrimeEvent e;
    e.kind = k; e.criminal = who; e.faction = NCR; e.cell = 1; e.where = kHere;
    if (victimNet) e.victim = cc::Entity::actor(victimNet);
    return e;
}
size_t alertsFor(Cluster& c, int machine, uint32_t npcNet) {
    size_t n = 0;
    for (auto& a : c.e(machine).alerts) if (a.first == npcForm(npcNet)) ++n;
    return n;
}
}  // namespace

TEST(crime_flow, a_witnessed_assault_marks_the_faction_everywhere_and_each_guard_reacts_once) {   // twin 907
    Cluster c(3);
    townsfolk(c);
    c.e(0).detections = {sees(21, NCR, 1)};   // guard A, simulated by the host, sees guest 1
    c.run(500);
    c.s(1).reportCrime(crimeOf(sc::CrimeKind::Assault, 1));
    c.run(500);
    for (int i = 0; i < 3; ++i) {
        CHECK(c.e(i).standing[NCR].bounty == 25 && c.e(i).standing[NCR].attack && !c.e(i).standing[NCR].enemy);
        CHECK(c.e(i).standingCalls.size() == 1);
    }
    CHECK(c.s(0).stats().crimesCounted == 1);
    // each machine alerts the guards IT simulates, against the criminal's reference on that machine
    CHECK(alertsFor(c, 0, 21) == 1 && alertsFor(c, 0, 22) == 0);
    CHECK(alertsFor(c, 2, 22) == 1 && alertsFor(c, 2, 21) == 0);
    CHECK(c.e(0).alerts[0].second == 0x2001 && c.e(2).alerts[0].second == 0x2001);
    CHECK(c.e(1).alerts.empty());
    // the same alarm arriving again changes nothing
    sc::MAlarm again;
    again.o.id = 0xA1A2A3A4A5A6A7A8ull ^ ((1ull << 48) | 1ull);   // what the first crime's alarm id was (reporter 1, counter 1)
    again.o.faction = NCR; again.o.cell = 1; again.o.where = kHere; again.o.criminal = 1; again.o.untilMs = c.now + 30000;
    const size_t n = c.e(2).alerts.size();
    c.inject(0, 2, again);
    CHECK(c.e(2).alerts.size() == n);
}

TEST(crime_flow, detection_by_any_players_machine_counts_but_only_for_its_own_npcs) {   // twin 903
    Cluster c(3);
    townsfolk(c);
    // only guard B (simulated by guest 2) sees guest 1: guest 2's machine reports it
    c.e(2).detections = {sees(22, NCR, 1)};
    c.run(500);
    sc::CrimeEvent theft = crimeOf(sc::CrimeKind::Theft, 1, 0);
    c.s(1).reportCrime(theft);
    c.run(300);
    CHECK(c.s(0).stats().crimesCounted == 1 && c.e(0).standing[NCR].bounty == 15);
    // a machine cannot make up a witness for an NPC another machine simulates: guest 1 says guard A (the host's) sees guest 2
    Cluster d(3);
    townsfolk(d);
    d.inject(1, 0, sc::MDetect{sees(21, NCR, 2)});
    d.run(300);
    CHECK(d.s(0).stats().detectRejected == 1);
    d.s(2).reportCrime(crimeOf(sc::CrimeKind::Theft, 2, 0));
    d.run(300);
    CHECK(d.s(0).stats().crimesCounted == 0 && d.e(0).standing.count(NCR) == 0);
    // ... nor for an NPC nobody knows
    d.inject(1, 0, sc::MDetect{sees(999, NCR, 1)});
    CHECK(d.s(0).stats().detectRejected == 2);
}

TEST(crime_flow, a_sighting_that_lasts_keeps_witnessing) {   // twin 920
    Cluster c(3);
    townsfolk(c);
    c.e(0).detections = {sees(21, NCR, 1)};
    c.run(12000);   // far longer than the host's 6 s window
    c.s(1).reportCrime(crimeOf(sc::CrimeKind::Theft, 1, 0));
    c.run(300);
    CHECK(c.s(0).stats().crimesCounted == 1);
    // and when the guard looks away the sighting ends: a later crime is unwitnessed
    c.e(0).detections.clear();
    c.run(7000);
    sc::CrimeEvent later = crimeOf(sc::CrimeKind::Theft, 1, 0);
    later.id = 77;
    c.s(1).reportCrime(later);
    c.run(300);
    CHECK(c.s(0).stats().crimesCounted == 1 && c.s(0).stats().crimesRefused == 1);
}

TEST(crime_flow, a_burst_of_reports_is_one_crime) {
    Cluster c(3);
    townsfolk(c);
    c.e(0).detections = {sees(21, NCR, 1)};
    c.run(500);
    for (int i = 0; i < 40; ++i) { c.s(1).reportCrime(crimeOf(sc::CrimeKind::Assault, 1)); c.tick(5); }
    c.run(300);
    CHECK(c.e(0).standing[NCR].bounty == 25 && c.e(2).standing[NCR].bounty == 25);
    CHECK(c.s(0).stats().crimesCounted == 1 && c.s(0).stats().crimesRefused == 39);
}

TEST(crime_flow, a_witnessed_murder_starts_a_war_and_the_reputation_is_paid_once) {
    Cluster c(3);
    townsfolk(c);
    c.e(0).detections = {sees(21, NCR, 1)};
    c.run(500);
    sc::CrimeEvent murder = crimeOf(sc::CrimeKind::Murder, 1);
    murder.victimDead = true;
    murder.nativeMask = 0x02;   // guest 1's own machine killed it natively: its engine already moved its reputation
    c.s(1).reportCrime(murder);
    c.run(500);
    for (int i = 0; i < 3; ++i) {
        CHECK(c.e(i).standing[NCR].murder && c.e(i).standing[NCR].enemy);
        CHECK(c.e(i).hostility.size() == 1 && c.e(i).hostility[0].kind == cc::HostilityKind::SetEnemy);
    }
    // infamy 2 with the faction: host (directly) and guest 2 (committed) once each, guest 1 never (its engine did it)
    CHECK(c.e(0).reps.size() == 1 && std::get<2>(c.e(0).reps[0]) == 2);
    CHECK(c.e(2).reps.size() == 1 && std::get<2>(c.e(2).reps[0]) == 2);
    CHECK(c.e(1).reps.empty());
    CHECK(c.committedCount("crime-reputation") == 1);
    // the pardon undoes all of it, everywhere
    c.s(0).forgive(NCR);
    c.run(500);
    for (int i = 0; i < 3; ++i) {
        CHECK(c.e(i).standing[NCR].clean());
        REQUIRE(c.e(i).hostility.size() == 2);
        CHECK(c.e(i).hostility[1].kind == cc::HostilityKind::SetAlly);
    }
}

TEST(crime_flow, a_joiner_gets_the_wanted_table_in_its_engine) {   // twin 925
    Cluster c(3, nullptr, false);
    townsfolk(c);
    c.s(0).playerJoined(1);
    c.s(1).hostConnected();
    c.e(0).detections = {sees(21, NCR, 1)};
    c.run(500);
    c.s(1).reportCrime(crimeOf(sc::CrimeKind::Assault, 1));
    c.run(500);
    CHECK(c.e(1).standing[NCR].attack);
    CHECK(c.e(2).standing.count(NCR) == 0);
    c.s(0).playerJoined(2);
    c.s(2).hostConnected();
    c.run(500);
    CHECK(c.e(2).standing[NCR].attack && c.e(2).standing[NCR].bounty == 25);
    CHECK(c.s(2).crimeJudge().bounty().digest() == c.s(0).crimeJudge().bounty().digest());
}

TEST(crime_flow, a_guests_theft_is_judged_by_the_host_against_the_guest) {   // twin 926
    Cluster c(3, [](Config& k) { k.crimeRules.hostileAtBounty = 10; });
    townsfolk(c);
    c.e(0).detections = {sees(21, NCR, 2)};   // the host's guard sees guest 2
    c.run(500);
    const FormRef chest = npcForm(500);
    sc::InteractionFacts f;
    f.what = sc::Interaction::TakeItem;
    f.own.owned = true; f.own.ownerForm = npcForm(21); f.own.crimeFaction = NCR; f.own.value = 80;
    c.e(0).interactions[chest] = f;
    c.s(0).onGuestInteraction(2, true, chest);
    c.run(500);
    CHECK(c.s(0).stats().crimesCounted == 1 && c.e(0).standing[NCR].bounty == 15 && c.e(0).standing[NCR].enemy);
    REQUIRE(!c.e(0).alerts.empty());
    CHECK(c.e(0).alerts[0].second == 0x2002);   // the guard hunts guest 2, not the host
    // nothing relevant about a target the engine does not know, and a host "guest" is ignored
    c.s(0).onGuestInteraction(2, true, npcForm(501));
    c.s(0).onGuestInteraction(kHostPlayer, true, chest);
    c.run(100);
    CHECK(c.s(0).stats().crimesCounted == 1);
    // the party's own things are not a crime
    sc::InteractionFacts mine = f;
    mine.own.crimeFaction = sc::playerFaction();
    c.e(0).interactions[chest] = mine;
    c.s(0).onGuestInteraction(2, true, chest);
    c.run(100);
    CHECK(c.s(0).stats().crimesCounted == 1);
}

TEST(crime_flow, trespass_is_reported_once_per_cell_not_every_second) {   // twin 919
    Cluster c(3);
    townsfolk(c);
    c.e(2).detections = {sees(22, NCR, 1)};
    c.run(500);
    c.e(1).trespassing = true;
    c.e(1).trespassFacts.what = sc::Interaction::EnterOwnedCell;
    c.e(1).trespassFacts.own.owned = true;
    c.e(1).trespassFacts.own.crimeFaction = NCR;
    c.e(1).trespassCell = 1;
    c.run(6000);
    CHECK(c.s(1).stats().trespassReports == 1);
    CHECK(c.e(0).standing[NCR].bounty == 5 && !c.e(0).standing[NCR].attack);
    // another cell is another trespass
    c.e(1).trespassCell = 2;
    c.run(2000);
    CHECK(c.s(1).stats().trespassReports == 2);
}

TEST(crime_flow, a_forged_report_and_a_disabled_system_do_nothing) {
    Cluster c(3);
    townsfolk(c);
    c.e(0).detections = {sees(21, NCR, 1)};
    c.run(500);
    // guest 2 reports a murder committed by guest 1
    sc::CrimeEvent frame = crimeOf(sc::CrimeKind::Murder, 1);
    frame.id = (2ull << 48) | 1; frame.reporter = 2;
    c.inject(2, 0, sc::MCrime{frame});
    c.run(300);
    CHECK(c.s(0).stats().crimesCounted == 0 && c.s(0).stats().crimesRefused == 1 && c.e(0).standing.count(NCR) == 0);
    // the host switches the system off: reports are ignored and nothing goes on the wire
    sc::SocialSettings s = c.s(0).settings();
    s.crime = false;
    c.s(0).setSettings(s);
    c.run(300);
    const uint64_t sent = c.s(1).stats().sent;
    c.s(1).reportCrime(crimeOf(sc::CrimeKind::Assault, 1));
    c.run(300);
    CHECK(c.s(1).stats().sent == sent && c.s(0).stats().crimesCounted == 0);
}

TEST(crime_flow, time_forgives_through_the_whole_pipeline) {
    Cluster c(3, [](Config& k) { k.crimeRules.decayPerMinute = 600; });   // 10 points a second
    townsfolk(c);
    c.e(0).detections = {sees(21, NCR, 1)};
    c.run(500);
    c.s(1).reportCrime(crimeOf(sc::CrimeKind::Theft, 1, 0));
    c.run(300);
    CHECK(c.e(2).standing[NCR].bounty == 15);
    c.run(20000);
    CHECK(c.e(2).standing[NCR].bounty == 0 && c.e(0).standing[NCR].bounty == 0);
    CHECK(c.s(2).crimeJudge().bounty().digest() == c.s(0).crimeJudge().bounty().digest());
}
