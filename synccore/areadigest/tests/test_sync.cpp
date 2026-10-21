// synccore/areadigest session tests: the real AreaSync on every machine of a rig (host + 1..3 clients) over a bus with latency, loss and hooks.
// Groups: quiet, heal, rules, safety, flow, story, delegate. Every twin of areadigest.cpp must make at least one of these fail.
#include <algorithm>
#include <cstring>
#include "th.h"

using namespace adt;

namespace {

constexpr CellId kCell = 0x100;

// Run until the clients' digests have had time to see a stable difference, the diff to travel and the heal to land.
void settle(Rig& r, uint64_t ms = 9000) { r.run(ms); }

std::vector<uint8_t> datagram(const Envelope& e) { return encodeEnvelope(e); }

Envelope healFor(const Item& auth, uint32_t forVersion, uint32_t epoch) {
    Envelope e;
    e.src = 0;
    MHeal h;
    h.caseId = 1;
    h.items.push_back(WireHeal{auth, forVersion, epoch});
    e.body = h;
    return e;
}

Item itemOf(M& m, Key k, uint64_t now) {
    Item it;
    CHECK(m.actors.find(k, it, now));
    return it;
}

std::string jsonOf(Rig& r, int machine) { return r.cl(machine).sync->statusJson(r.bus.now); }

}  // namespace

// ================================================================================================================================
// quiet: a healthy session heals nothing and says very little
// ================================================================================================================================
TEST(quiet, a_healthy_session_has_zero_heals_and_no_diff_traffic) {
    Rig r(3);
    r.addActor(100, kCell, V(0, 0, 0), 0);
    r.addActor(101, kCell, V(500, 0, 0), 1);
    r.addActor(102, kCell, V(0, 900, 0), 2, 60);
    r.addState(Cat::Containers, 500, kCell, 11);
    r.addState(Cat::Objects, 600, kCell, 3);
    r.run(40000);
    CHECK(r.healsAll() == 0);
    CHECK(r.sumDecided() == 0);
    for (size_t i = 0; i < r.n(); ++i) CHECK(r.cl(static_cast<int>(i)).sync->bugs().empty());
    const Counters& hc = r.host().sync->counters();
    // while the items appear, one side can be settled before the other: at most a case or two at the start, then nothing
    CHECK(hc.casesOpened <= 4 && hc.healsSent == 0);
    CHECK(hc.digestsReceived >= 2 * 15 && hc.digestsOk >= hc.digestsReceived - hc.digestsRateLimited - 4);
    const uint64_t sentBefore = r.bus.sent;
    r.run(20000);
    CHECK(r.bus.sent - sentBefore <= 2 * 10 + 1);         // steady state: the host sends nothing, each client one small digest per round
    CHECK(r.host().sync->counters().casesOpened == hc.casesOpened);
    CHECK(r.host().sync->healsPerMinuteAll(r.bus.now) == 0);
}

TEST(quiet, constant_motion_and_combat_in_flight_are_not_desyncs) {
    Rig r(3);
    r.addActor(100, kCell, V(0, 0, 0), 0);
    r.addActor(101, kCell, V(500, 0, 0), 1);
    Rng rng(5);
    // 40 s: the owners move and get hurt every 250 ms, the other machines see every change 150 ms later
    for (int t = 0; t < 160; ++t) {
        const Vec3 p = V(float(t) * 20.0f, float(t % 7) * 10.0f, 0);
        const float hp = 100.0f - float(t % 60);
        r.host().actors.a[100].pos = p; r.host().actors.a[100].hp = hp;
        r.cl(1).actors.a[101].pos = p; r.cl(1).actors.a[101].hp = hp;
        r.run(150);
        for (size_t i = 0; i < r.n(); ++i) {
            if (i != 0) { r.cl(static_cast<int>(i)).actors.a[100].pos = p; r.cl(static_cast<int>(i)).actors.a[100].hp = hp; }
            if (i != 1) { r.cl(static_cast<int>(i)).actors.a[101].pos = p; r.cl(static_cast<int>(i)).actors.a[101].hp = hp; }
        }
        r.run(100);
    }
    CHECK(r.healsAll() == 0);
    CHECK(r.host().sync->counters().cat[1].healsDecided == 0);
}

// ================================================================================================================================
// heal: the authority's value wins
// ================================================================================================================================
TEST(heal, a_wrong_health_on_a_client_is_healed_from_the_host_owner) {
    Rig r(2);
    r.addActor(100, kCell, V(0, 0, 0), 0);
    r.run(4000);
    CHECK(r.healsAll() == 0);
    r.cl(1).actors.a[100].hp = 40;
    settle(r);
    CHECK(r.cl(1).actors.a[100].hp == 100);
    CHECK(r.healsApplied(1, Cat::Actors) == 1);
    CHECK(r.healsApplied(0, Cat::Actors) == 0);
    CHECK(r.cl(1).sync->healsPerMinute(Cat::Actors, r.bus.now) == 1);
    CHECK(r.cl(1).sync->healsTotal(Cat::Actors) == 1);
    CHECK(r.cl(1).actors.healLog.size() == 1);
    CHECK(r.host().sync->counters().cat[1].healsDecided == 1);
    CHECK(r.cl(1).sync->bugs().empty());
    r.run(20000);                                    // and it stays quiet afterwards
    CHECK(r.healsAll() == 1);
}

TEST(heal, a_client_owner_is_authoritative_over_the_hosts_copy) {
    Rig r(2);
    r.addActor(100, kCell, V(0, 0, 0), 1);          // owned by client 1
    r.run(4000);
    r.host().actors.a[100].hp = 10;
    settle(r);
    CHECK(r.host().actors.a[100].hp == 100);         // the host pulled the owner's value
    CHECK(r.cl(1).actors.a[100].hp == 100);
    CHECK(r.healsApplied(0, Cat::Actors) == 1);
    CHECK(r.healsApplied(1, Cat::Actors) == 0);      // the owner is never healed
    CHECK(r.cl(1).actors.healLog.empty());
}

TEST(heal, a_third_machine_owner_is_asked_and_both_others_are_corrected) {
    Rig r(3);
    r.addActor(100, kCell, V(0, 0, 0), 2);          // owned by client 2
    r.run(4000);
    r.cl(1).actors.a[100].hp = 10;
    r.host().actors.a[100].hp = 30;
    settle(r, 14000);
    CHECK(r.host().actors.a[100].hp == 100);
    CHECK(r.cl(1).actors.a[100].hp == 100);
    CHECK(r.cl(2).actors.a[100].hp == 100);
    CHECK(r.healsApplied(0, Cat::Actors) == 1);
    CHECK(r.healsApplied(1, Cat::Actors) == 1);
    CHECK(r.healsApplied(2, Cat::Actors) == 0);
    CHECK(r.cl(2).actors.healLog.empty());
}

TEST(heal, the_host_authority_decides_when_the_owner_is_the_host_even_if_a_client_is_right_later) {
    // the authority is the owner, not the majority: two clients agree with each other and are both wrong
    Rig r(3);
    r.addActor(100, kCell, V(0, 0, 0), 0);
    r.run(4000);
    r.cl(1).actors.a[100].hp = 30;
    r.cl(2).actors.a[100].hp = 30;
    settle(r, 12000);
    CHECK(r.host().actors.a[100].hp == 100 && r.cl(1).actors.a[100].hp == 100 && r.cl(2).actors.a[100].hp == 100);
    CHECK(r.healsApplied(0, Cat::Actors) == 0);
}

TEST(heal, an_unowned_actor_is_not_healed_because_nobody_is_authoritative) {
    Rig r(2);
    r.addActor(100, kCell, V(0, 0, 0), kNoPlayer);
    r.run(4000);
    r.cl(1).actors.a[100].hp = 20;
    settle(r);
    CHECK(r.cl(1).actors.a[100].hp == 20);
    CHECK(r.healsAll() == 0);
    CHECK(r.host().sync->counters().cat[1].ownerSilent >= 1);
}

TEST(heal, containers_and_objects_get_the_hosts_state) {
    Rig r(2);
    r.addState(Cat::Containers, 500, kCell, 11);
    r.addState(Cat::Objects, 600, kCell, 3);
    r.run(4000);
    r.cl(1).cont.a[500].state = 99;
    r.cl(1).obj.a[600].state = 4;
    settle(r);
    CHECK(r.cl(1).cont.a[500].state == 11);
    CHECK(r.cl(1).obj.a[600].state == 3);
    CHECK(r.healsApplied(1, Cat::Containers) == 1 && r.healsApplied(1, Cat::Objects) == 1);
    CHECK(r.host().cont.heals == 0);
    // the host's own wrong copy of a world object is corrected from nobody: the host IS the authority, the client's copy is then wrong
    r.host().obj.a[600].state = 8;
    r.run(4000);
    CHECK(r.cl(1).obj.a[600].state == 3 || r.cl(1).obj.a[600].state == 8);   // either way the two agree after the clients heal to the host
    settle(r);
    CHECK(r.cl(1).obj.a[600].state == r.host().obj.a[600].state);
}

TEST(heal, a_death_that_never_arrived_is_healed_and_corpse_positions_are_ignored) {
    Rig r(2);
    r.addActor(100, kCell, V(0, 0, 0), 0);
    r.addActor(101, kCell, V(0, 500, 0), 0);
    r.run(4000);
    // 101 dies everywhere, then its ragdolls come to rest 6 m apart: no business of the digest
    r.setAlive(101, false);
    r.run(3000);
    r.host().actors.a[101].pos = V(0, 500, 0);
    r.cl(1).actors.a[101].pos = V(6 * kU, 500, 0);
    settle(r, 12000);
    CHECK(r.healsAll() == 0);                        // twin 17
    // 100 dies on the host but the death never reaches the client
    r.host().actors.a[100].alive = false;
    settle(r, 12000);
    CHECK(!r.cl(1).actors.a[100].alive);
    CHECK(r.healsApplied(1, Cat::Actors) == 1);
}

TEST(heal, a_refused_heal_does_not_count_and_a_repeat_is_logged_as_a_bug_with_both_values) {
    Rig r(2);
    r.addActor(100, kCell, V(0, 0, 0), 0);
    r.run(4000);
    r.cl(1).actors.stuck = true;                      // the engine takes the value and then keeps its own (revert)
    r.cl(1).actors.a[100].hp = 40;
    r.run(30000);
    const auto& bugs = r.host().sync->bugs();
    REQUIRE(!bugs.empty());
    CHECK(bugs.size() <= 4);                          // at most one line per key per 10 s
    CHECK(bugs[0].cat == Cat::Actors && bugs[0].key == 100 && bugs[0].peer == 1 && bugs[0].count >= 2);
    CHECK(bugs[0].authority.find("hp 100") != std::string::npos);
    CHECK(bugs[0].other.find("hp 40") != std::string::npos);
    CHECK(r.host().sync->counters().bugs == bugs.size());
    CHECK(r.host().sync->counters().cat[1].repeats >= 1);
}

// ================================================================================================================================
// rules: rubber band, tolerance, buckets
// ================================================================================================================================
TEST(rules, position_errors_follow_the_rubber_band_thresholds) {
    Rig r(2);
    r.addActor(100, kCell, V(0, 0, 0), 0);
    r.run(4000);
    // 1.5 m off: below the smooth threshold, left to the normal interpolation
    r.cl(1).actors.a[100].pos = V(1.5f * kU, 0, 0);
    settle(r);
    CHECK(r.healsAll() == 0 && r.cl(1).actors.heals == 0);
    // 3 m off: smooth
    r.cl(1).actors.a[100].pos = V(3 * kU, 0, 0);
    settle(r);
    CHECK(r.cl(1).actors.heals == 1 && r.cl(1).actors.lastPlan.pos == RubberMode::Smooth);
    CHECK(r.cl(1).actors.a[100].pos.x == 0);
    // 12 m off: snap
    r.cl(1).actors.a[100].pos = V(12 * kU, 0, 0);
    settle(r);
    CHECK(r.cl(1).actors.heals == 2 && r.cl(1).actors.lastPlan.pos == RubberMode::Snap);
    // another cell: snap, and the cell is part of the plan
    r.cl(1).actors.a[100].cell = kCell + 1;
    r.cl(1).actors.cells.insert(kCell + 1);
    r.host().actors.cells.insert(kCell + 1);
    settle(r);
    CHECK(r.cl(1).actors.heals == 3 && r.cl(1).actors.lastPlan.pos == RubberMode::Snap && r.cl(1).actors.lastPlan.cell);
    CHECK(r.cl(1).actors.a[100].cell == kCell);
}

TEST(rules, differences_inside_a_bucket_boundary_straddle_are_not_healed) {
    Rig r(2);
    r.addActor(100, kCell, V(139.9f, 0, 0), 0, 49.9f);
    r.run(4000);
    r.cl(1).actors.a[100].pos = V(140.1f, 0, 0);       // 0.2 units: the same place, another 2 m bucket
    r.cl(1).actors.a[100].hp = 50.1f;                  // the same health, another 10 hp bucket
    settle(r, 12000);
    CHECK(r.healsAll() == 0);                          // twin 16
    CHECK(r.host().sync->counters().cat[1].falsePositive >= 1);
    CHECK(r.host().sync->counters().cat[1].healsDecided == 0);
}

TEST(rules, a_group_that_explains_itself_as_nothing_to_heal_is_not_diffed_every_round) {
    Rig r(2);
    r.addActor(100, kCell, V(139.9f, 0, 0), 0);
    r.run(4000);
    r.cl(1).actors.a[100].pos = V(140.1f, 0, 0);
    r.run(40000);
    const Counters& c = r.host().sync->counters();
    CHECK(c.casesOpened >= 1 && c.casesOpened <= 4);   // twin 29: without the backoff it is one case per 2 s round (about 20)
    CHECK(c.backoffSkips >= 5);
}

TEST(rules, items_missing_on_one_side_are_counted_and_not_healed) {
    Rig r(2);
    r.addActor(100, kCell, V(0, 0, 0), 0);
    r.addActor(101, kCell, V(300, 0, 0), 0);
    r.cl(1).actors.a.erase(101);                       // not tracked / not loaded on the client yet
    r.run(30000);
    CHECK(r.healsAll() == 0);
    CHECK(r.host().sync->counters().cat[1].missing >= 1);
    CHECK(r.host().sync->counters().casesOpened <= 3);
}

TEST(rules, an_area_loaded_on_one_machine_only_is_not_compared) {
    Rig r(2);
    r.addActor(100, kCell, V(0, 0, 0), 0);
    r.cl(1).actors.a[200] = RawActor();                // a whole cell only the client has loaded
    r.cl(1).actors.a[200].cell = 0x777;
    r.cl(1).actors.cells.insert(0x777);
    r.run(8000);
    const uint64_t before = r.host().sync->counters().casesOpened;
    r.run(20000);
    CHECK(r.host().sync->counters().casesOpened == before && r.healsAll() == 0);
    CHECK(r.host().sync->counters().cat[1].missing == 0 && r.cl(1).actors.a.count(200) == 1);   // the client-only cell was never compared
}

TEST(rules, a_change_in_flight_is_waited_for_not_healed) {
    Rig r(2);
    r.addActor(100, kCell, V(0, 0, 0), 0);
    r.run(8000);
    // the digests go out at t = 10000 + 2000 k; a change that lands just before one is still in flux when the diff is taken
    while (r.bus.now % 2000 != 1400) r.run(50);
    r.cl(1).actors.a[100].hp = 40;
    r.run(1500);                                       // the next digest (in flux on the client) and its diff
    CHECK(r.healsAll() == 0);                          // twin 19: healed while the value was still moving
    CHECK(r.host().sync->counters().cat[1].inFlight >= 1);
    settle(r);
    CHECK(r.cl(1).actors.a[100].hp == 100);            // and once it sat still, it was
    CHECK(r.healsApplied(1, Cat::Actors) == 1);
}

TEST(rules, no_heal_while_a_handoff_is_in_flight) {
    Rig r(2);
    r.addActor(100, kCell, V(0, 0, 0), 0);
    r.run(4000);
    r.host().actors.own[100].pending = true;
    r.cl(1).actors.a[100].hp = 20;
    settle(r, 12000);
    CHECK(r.cl(1).actors.a[100].hp == 20 && r.healsAll() == 0);          // twin 20
    CHECK(r.host().sync->counters().cat[1].pendingHandoff >= 1);
    r.host().actors.own[100].pending = false;
    settle(r, 12000);
    CHECK(r.cl(1).actors.a[100].hp == 100 && r.healsApplied(1, Cat::Actors) == 1);
}

TEST(rules, heal_budget_and_case_size_stop_a_storm) {
    Config c;
    c.healBudgetPerMin = 60;
    Rig r(2, c);
    for (Key k = 1; k <= 300; ++k) r.addActor(k, kCell, V(float(k) * 10, 0, 0), 0);
    r.run(4000);
    for (Key k = 1; k <= 300; ++k) r.cl(1).actors.a[k].hp = 10;
    r.run(30000);
    const uint64_t first = r.healsApplied(1, Cat::Actors);
    CHECK(first > 0);
    CHECK(first <= 60 + 30 + 10);                      // capacity plus 30 s of refill (twin 36: no budget, 64 per case)
    CHECK(r.host().sync->counters().cat[1].deferredBudget > 0);
    r.run(400000);                                     // it still converges, just not in one burst
    int wrong = 0;
    for (Key k = 1; k <= 300; ++k) wrong += r.cl(1).actors.a[k].hp != 100;
    CHECK(wrong == 0);
}

// ================================================================================================================================
// safety: stale, forged, duplicated and raced messages change nothing
// ================================================================================================================================
TEST(safety, a_heal_is_dropped_when_the_item_changed_after_it_was_reported) {
    Rig r(2);
    r.addActor(100, kCell, V(0, 0, 0), 0);
    r.run(4000);
    r.cl(1).actors.a[100].hp = 40;
    bool poked = false;
    r.bus.hook = [&](Msg& m) {
        if (m.to == 1 && !poked) {
            Envelope e;
            if (decodeEnvelope(m.data.data(), m.data.size(), e) && std::holds_alternative<MHeal>(e.body)) {
                poked = true;
                r.cl(1).actors.a[100].hp = 55;         // a fresh snapshot lands in the same instant
            }
        }
        return true;
    };
    for (int i = 0; i < 400 && !poked; ++i) r.run(50);
    r.run(200);                                        // the heal arrived while hp was 55 now
    CHECK(poked);
    CHECK(r.cl(1).sync->counters().cat[1].raced == 1);            // twin 23
    CHECK(r.cl(1).actors.heals == 0 && r.cl(1).actors.a[100].hp == 55);
    r.bus.hook = nullptr;
    settle(r);
    CHECK(r.cl(1).actors.a[100].hp == 100 && r.healsApplied(1, Cat::Actors) == 1);
}

TEST(safety, a_heal_from_an_older_owner_epoch_is_rejected) {
    Rig r(2);
    r.addActor(100, kCell, V(0, 0, 0), 0, 100, 5);
    r.run(4000);
    r.cl(1).actors.a[100].hp = 40;
    r.run(2000);
    Item cur = itemOf(r.cl(1), 100, r.bus.now);
    Item auth = cur;
    auth.hp = 100;
    auto bytes = datagram(healFor(auth, cur.version, 4));              // decided under epoch 4, the client already knows epoch 5
    r.cl(1).sync->receive(0, bytes.data(), bytes.size());
    CHECK(r.cl(1).actors.a[100].hp == 40 && r.cl(1).actors.heals == 0);   // twin 24
    bytes = datagram(healFor(auth, cur.version, 5));
    r.cl(1).sync->receive(0, bytes.data(), bytes.size());
    CHECK(r.cl(1).actors.a[100].hp == 100 && r.cl(1).actors.heals == 1);
}

TEST(safety, the_owner_of_an_actor_is_never_overwritten_by_a_heal) {
    Rig r(2);
    r.addActor(100, kCell, V(0, 0, 0), 1);             // client 1 owns it
    r.run(4000);
    r.cl(1).actors.a[100].hp = 60;                      // the owner's truth
    r.run(2000);
    Item cur = itemOf(r.cl(1), 100, r.bus.now);
    Item auth = cur;
    auth.hp = 100;
    auto bytes = datagram(healFor(auth, cur.version, 1));
    r.cl(1).sync->receive(0, bytes.data(), bytes.size());
    CHECK(r.cl(1).actors.a[100].hp == 60 && r.cl(1).actors.heals == 0);   // twin 25
    CHECK(r.cl(1).sync->counters().cat[1].refused == 1);
}

TEST(safety, a_duplicated_heal_changes_nothing_the_second_time) {
    Rig r(2);
    r.addActor(100, kCell, V(0, 0, 0), 0);
    r.run(4000);
    r.cl(1).actors.a[100].hp = 40;
    std::vector<uint8_t> captured;
    r.bus.hook = [&](Msg& m) {
        Envelope e;
        if (m.to == 1 && captured.empty() && decodeEnvelope(m.data.data(), m.data.size(), e) && std::holds_alternative<MHeal>(e.body)) captured = m.data;
        return true;
    };
    r.run(8000);
    REQUIRE(!captured.empty());
    REQUIRE(r.cl(1).actors.heals == 1);
    r.cl(1).sync->receive(0, captured.data(), captured.size());
    r.cl(1).sync->receive(0, captured.data(), captured.size());
    CHECK(r.cl(1).actors.heals == 1 && r.healsApplied(1, Cat::Actors) == 1);
    // the same datagram with the version fixed up still finds nothing to change
    Envelope e;
    REQUIRE(decodeEnvelope(captured.data(), captured.size(), e));
    Item cur = itemOf(r.cl(1), 100, r.bus.now);
    auto bytes = datagram(healFor(std::get<MHeal>(e.body).items[0].item, cur.version, 1));
    r.cl(1).sync->receive(0, bytes.data(), bytes.size());
    CHECK(r.cl(1).actors.heals == 1 && r.cl(1).sync->counters().cat[1].noDiff >= 1);   // twin 26: applied (and counted) although nothing differs
}

TEST(safety, only_the_host_may_push_values_and_only_clients_obey) {
    Rig r(3);
    r.addActor(100, kCell, V(0, 0, 0), 0);
    r.run(4000);
    r.cl(1).actors.a[100].hp = 40;
    r.run(2000);
    Item cur = itemOf(r.cl(1), 100, r.bus.now);
    Item auth = cur;
    auth.hp = 100;
    auto bytes = datagram(healFor(auth, cur.version, 1));
    r.cl(1).sync->receive(2, bytes.data(), bytes.size());              // from another client
    CHECK(r.cl(1).actors.a[100].hp == 40 && r.cl(1).sync->counters().wrongDirection == 1);   // twin 27
    // the host never obeys a heal or a request, a client never takes a digest
    r.host().sync->receive(1, bytes.data(), bytes.size());
    CHECK(r.host().sync->counters().wrongDirection == 1);
    Envelope d;
    d.src = 2;
    d.body = MDigest();
    auto db = datagram(d);
    r.cl(1).sync->receive(2, db.data(), db.size());
    CHECK(r.cl(1).sync->counters().wrongDirection == 2);
    // items from a machine that was never asked
    Envelope it;
    it.src = 2;
    MItems mi;
    mi.caseId = 999;
    it.body = mi;
    auto ib = datagram(it);
    r.host().sync->receive(2, ib.data(), ib.size());
    CHECK(r.host().sync->counters().strays == 1);
}

TEST(safety, forged_senders_garbage_and_unknown_peers_are_counted_and_ignored) {
    Rig r(2);
    r.addActor(100, kCell, V(0, 0, 0), 0);
    r.run(3000);
    Envelope d;
    d.src = 7;                                          // claims to be slot 7, arrives from slot 1
    d.body = MDigest();
    auto b = datagram(d);
    r.host().sync->receive(1, b.data(), b.size());
    CHECK(r.host().sync->counters().spoofed == 1);
    d.src = 5;
    b = datagram(d);
    r.host().sync->receive(5, b.data(), b.size());     // slot 5 never joined
    CHECK(r.host().sync->counters().unknownPeer == 1);
    const uint8_t junk[] = {kChannelAreaDigest, 9, 9, 9};
    r.host().sync->receive(1, junk, sizeof junk);
    r.host().sync->receive(1, junk, 0);
    CHECK(r.host().sync->counters().badMessages == 2);
}

TEST(safety, a_digest_flood_opens_one_case_at_most) {
    Rig r(2);
    r.addActor(100, kCell, V(0, 0, 0), 0);
    r.run(4000);
    r.cl(1).actors.a[100].hp = 40;
    r.run(2500);
    r.bus.sendFilter = [](const Msg& m) { Envelope e; return !(decodeEnvelope(m.data.data(), m.data.size(), e) && std::holds_alternative<MDigest>(e.body)); };
    Envelope e;
    e.src = 1;
    MDigest dg;
    dg.cells = {kCell};
    dg.groups.push_back(WireGroup{kCell, Cat::Actors, 1, 1});
    e.body = dg;
    auto b = datagram(e);
    const uint64_t casesBefore = r.host().sync->counters().casesOpened;
    for (int i = 0; i < 20; ++i) r.host().sync->receive(1, b.data(), b.size());
    CHECK(r.host().sync->counters().digestsRateLimited >= 19);   // twin 18
    CHECK(r.host().sync->counters().casesOpened <= casesBefore + 1);
}

TEST(safety, a_stalled_exchange_times_out_and_does_not_block_the_next) {
    Rig r(2);
    r.addActor(100, kCell, V(0, 0, 0), 0);
    r.run(4000);
    r.cl(1).actors.a[100].hp = 40;
    r.bus.sendFilter = [](const Msg& m) {               // the client's answers (items) never arrive; its digests do
        Envelope e;
        return !(m.from == 1 && decodeEnvelope(m.data.data(), m.data.size(), e) && std::holds_alternative<MItems>(e.body));
    };
    r.run(12000);
    CHECK(r.host().sync->counters().casesTimedOut >= 1);      // twin 35
    CHECK(r.host().sync->openCases() <= 1);
    r.bus.sendFilter = nullptr;
    settle(r, 45000);
    CHECK(r.cl(1).actors.a[100].hp == 100);
}

TEST(safety, items_from_a_machine_that_was_not_asked_are_not_taken_as_the_owners) {
    Rig r(3);
    r.addActor(100, kCell, V(0, 0, 0), 2);               // owner: client 2
    r.run(4000);
    uint32_t caseId = 0;
    r.bus.hook = [&](Msg& m) {                           // the host asks client 2 for the owner's value; swallow the question
        Envelope e;
        if (m.to == 2 && decodeEnvelope(m.data.data(), m.data.size(), e)) {
            if (auto* q = std::get_if<MItemsReq>(&e.body)) if (!q->keys.empty()) { caseId = q->caseId; return false; }
        }
        return true;
    };
    r.cl(1).actors.a[100].hp = 10;
    r.host().actors.a[100].hp = 30;
    for (int i = 0; i < 400 && !caseId; ++i) r.run(50);
    REQUIRE(caseId != 0);
    // client 1 (not asked as an owner) sends "the owner's" items for that case: a forged answer
    Envelope e;
    e.src = 1;
    MItems mi;
    mi.caseId = caseId;
    Item forged = itemOf(r.cl(1), 100, r.bus.now);
    forged.hp = 77;
    forged.settledMs = 100000;
    mi.items.push_back(forged);
    e.body = mi;
    auto b = datagram(e);
    r.host().sync->receive(1, b.data(), b.size());
    CHECK(r.host().sync->counters().strays == 1);          // twin 37
    r.run(300);
    CHECK(r.host().actors.a[100].hp != 77 && r.cl(1).actors.a[100].hp != 77);   // the forged value is nowhere
    r.bus.hook = nullptr;
}

TEST(safety, a_client_that_leaves_mid_exchange_leaves_nothing_behind) {
    Rig r(3);
    r.addActor(100, kCell, V(0, 0, 0), 2);              // owner is client 2: the case waits for it
    r.run(4000);
    r.cl(1).actors.a[100].hp = 10;
    r.host().actors.a[100].hp = 30;
    r.bus.cut[2][0] = true;
    r.run(6000);
    r.host().sync->playerLeft(2);
    r.host().sync->playerLeft(1);
    CHECK(r.host().sync->openCases() == 0);
    r.host().sync->playerJoined(1);
    r.bus.cut[2][0] = false;
    r.run(10000);                                       // no crash, no stuck state
    CHECK(r.host().sync->openCases() <= 1);
}

TEST(safety, a_lossy_duplicating_link_converges_and_never_heals_a_corruption_twice) {
    Rig r(3);
    r.bus.loss = 0.1;
    r.bus.dup = 0.15;
    r.bus.jitter = 80;
    for (Key k = 1; k <= 6; ++k) r.addActor(k, kCell, V(float(k) * 300, 0, 0), static_cast<PlayerId>(k % 3));   // owner of key k: machine k % 3
    r.run(6000);
    r.cl(2).actors.a[1].hp = 5;                         // each corruption sits on a machine that does NOT own the actor
    r.cl(1).actors.a[3].hp = 5;
    r.cl(1).actors.a[2].hp = 5;
    r.cl(2).actors.a[4].pos = V(900, 900, 0);
    r.host().actors.a[5].hp = 5;
    r.run(240000);
    for (Key k = 1; k <= 6; ++k) {
        const RawActor& truth = r.cl(static_cast<int>(k % 3)).actors.a[k];   // the owner's copy is the truth
        for (size_t i = 0; i < r.n(); ++i) {
            const RawActor& x = r.cl(static_cast<int>(i)).actors.a[k];
            CHECK(std::fabs(x.hp - truth.hp) <= 5 && x.cell == truth.cell);
            CHECK(distance(x.pos, truth.pos) <= 2 * kU);
        }
    }
    CHECK(r.healsApplied(1, Cat::Actors) + r.healsApplied(2, Cat::Actors) + r.healsApplied(0, Cat::Actors) <= 6);   // 5 corruptions at most, owners untouched
}

// ================================================================================================================================
// story: the global digest and the existing repair
// ================================================================================================================================
TEST(story, equal_shadows_at_the_same_seq_are_fine_and_cost_no_repair) {
    Rig r(2);
    for (int i = 0; i < 3; ++i) { r.host().story.commit(0x200 + i, 10); r.cl(1).story.commit(0x200 + i, 10); }
    r.run(12000);
    CHECK(r.host().sync->counters().storyOk >= 4 && r.host().sync->counters().storyMismatch == 0);
    CHECK(r.host().story.repairRequests.empty());
}

TEST(story, a_client_that_is_behind_is_in_flight_not_desynced) {
    Rig r(2);
    r.host().story.commit(0x200, 10);
    r.host().story.commit(0x201, 10);
    r.cl(1).story.commit(0x200, 10);                    // one txn behind
    r.run(12000);
    CHECK(r.host().sync->counters().storyMismatch == 0);        // twin 38
    CHECK(r.host().sync->counters().storyInFlight >= 2);
    CHECK(r.host().story.repairRequests.empty());
}

TEST(story, different_values_at_the_same_seq_ask_the_existing_repair_once_per_gap) {
    Rig r(2);
    r.host().story.commit(0x200, 10);
    r.cl(1).story.commit(0x200, 20);
    r.run(7000);
    REQUIRE(r.host().story.repairRequests.size() == 1);
    CHECK(r.host().story.repairRequests[0] == 1);
    CHECK(r.host().sync->counters().storyMismatch >= 3);
    r.run(6000);                                         // the 10 s gap passed: asked again, not before
    CHECK(r.host().story.repairRequests.size() == 2);
    // the story layer reports the two repairs it sent: they are the story heals
    r.host().story.repairCount = 2;
    r.run(4000);
    CHECK(r.host().sync->healsTotal(Cat::Story) == 2);          // twin 28
    CHECK(r.host().sync->healsPerMinute(Cat::Story, r.bus.now) == 2);
    CHECK(r.host().sync->healsPerMinuteAll(r.bus.now) == 2);
}

TEST(story, digests_of_different_world_epochs_are_not_compared) {
    Rig r(2);
    r.host().story.commit(0x200, 10);
    r.cl(1).story.commit(0x200, 20);
    r.cl(1).story.epoch = 2;
    r.run(8000);
    CHECK(r.host().sync->counters().storyEpochSkew >= 2 && r.host().sync->counters().storyMismatch == 0);   // twin 39
    CHECK(r.host().story.repairRequests.empty());
}

TEST(story, an_unavailable_story_digest_is_skipped) {
    Rig r(2);
    r.cl(1).story.available = false;
    r.host().story.commit(0x200, 10);
    r.run(8000);
    CHECK(r.host().sync->counters().storyMismatch == 0 && r.host().sync->counters().storyOk == 0);
}

// ================================================================================================================================
// delegate: categories whose effects belong to the story layer
// ================================================================================================================================
TEST(delegate, a_delegated_category_asks_the_story_repair_and_pushes_no_values) {
    Rig r(2, Config(), true, /*containers*/ true, /*objects*/ false);
    r.addState(Cat::Containers, 500, kCell, 11);
    r.run(4000);
    r.cl(1).cont.a[500].state = 99;
    r.run(9000);
    CHECK(r.host().story.repairRequests.size() == 1 && r.host().story.repairRequests[0] == 1);
    CHECK(r.cl(1).cont.heals == 0 && r.host().sync->counters().healsSent == 0);
    CHECK(r.host().sync->counters().cat[2].delegated >= 1);
    CHECK(r.healsApplied(1, Cat::Containers) == 0);
}

// ================================================================================================================================
// flow: connection state, status
// ================================================================================================================================
TEST(flow, a_client_digests_only_while_the_host_link_is_up) {
    Rig r(2);
    r.addActor(100, kCell, V(0, 0, 0), 0);
    r.run(6000);
    const uint64_t before = r.cl(1).sync->counters().digestsSent;
    CHECK(before >= 2);
    r.cl(1).sync->hostLost();
    r.run(10000);
    CHECK(r.cl(1).sync->counters().digestsSent == before);
    r.cl(1).sync->hostConnected();
    r.run(6000);
    CHECK(r.cl(1).sync->counters().digestsSent > before);
}

TEST(flow, status_json_has_the_per_category_heal_counters) {
    Rig r(2);
    r.addActor(100, kCell, V(0, 0, 0), 0);
    r.run(4000);
    r.cl(1).actors.a[100].hp = 40;
    settle(r);
    const std::string j = jsonOf(r, 1);
    int depth = 0;
    bool balanced = true;
    for (char ch : j) {
        if (ch == '{' || ch == '[') ++depth;
        if (ch == '}' || ch == ']') { --depth; if (depth < 0) balanced = false; }
    }
    CHECK(balanced && depth == 0);
    for (const char* k : {"\"heals\":{\"total\":1,\"perMin\":1", "\"byCat\":{\"story\":", "\"actors\":{\"total\":1", "\"containers\":", "\"objects\":",
                          "\"role\":\"client\"", "\"counters\":", "\"bugs\":["})
        CHECK_MSG(j.find(k) != std::string::npos, k);
    const std::string h = r.host().sync->statusJson(r.bus.now);
    CHECK(h.find("\"role\":\"host\"") != std::string::npos && h.find("\"heals\":{\"total\":0,\"perMin\":0") != std::string::npos);
}

TEST(flow, observer_sees_every_heal_and_every_bug) {
    struct Obs : IAreaObserver {
        std::vector<HealNote> heals;
        std::vector<BugReport> bugs;
        void onHeal(const HealNote& n) override { heals.push_back(n); }
        void onBug(const BugReport& b) override { bugs.push_back(b); }
    } obs;
    Rig r(2);
    r.cl(1).sync->setObserver(&obs);
    r.host().sync->setObserver(&obs);
    r.addActor(100, kCell, V(0, 0, 0), 0);
    r.run(4000);
    r.cl(1).actors.a[100].hp = 40;
    settle(r);
    REQUIRE(obs.heals.size() == 1);
    CHECK(obs.heals[0].cat == Cat::Actors && obs.heals[0].key == 100 && obs.heals[0].target == 1 && obs.heals[0].result == HealResult::Applied);
    CHECK(obs.heals[0].text.find("hp 40") != std::string::npos && obs.heals[0].text.find("hp 100") != std::string::npos);
    CHECK(obs.bugs.empty());
}
