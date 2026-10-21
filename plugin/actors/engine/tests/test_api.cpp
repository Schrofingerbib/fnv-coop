// The public surface (actor_engine_api.h) as the other lanes see it. Twins 61 (remote-driven rule), 62 (hit forwarding), 63 (death consults the rules).
#include <string>

#include "feed_rig.h"
#include "minitest.h"

using namespace nvae_test;

namespace {
void settle(FeedRig& r) { r.startSession(); r.run(1500); }
}  // namespace

TEST(api, a_host_owned_actor_is_mine_and_not_remote_driven) {
    FeedRig r;
    r.eng.addNpc(0x100, 0x500, {1000, 1000, 0});
    settle(r);
    const OwnerRef o = ownerOf(0x100);
    CHECK(o.known && o.mine && o.ownerSlot == 0 && o.epoch >= 1 && !o.pending && !o.standIn && !o.dead);
    CHECK(o.netId == r.id(0x100));
    CHECK(!isRemoteDriven(0x100));
    CHECK(netIdOf(0x100) == r.id(0x100) && refOf(r.id(0x100)) == 0x100 && !isStandIn(0x100));
    const OwnerRef byNet = ownerOfNet(r.id(0x100));
    CHECK(byNet.known && byNet.mine && byNet.epoch == o.epoch);
}

TEST(api, untracked_refs_are_unknown) {
    FeedRig r;
    r.eng.addNpc(0x100, 0x500, {1000, 1000, 0});
    settle(r);
    CHECK(!ownerOf(0x14).known && !ownerOf(0x999).known && !ownerOf(0).known);
    CHECK(!isRemoteDriven(0x14) && !isRemoteDriven(0x999));
    CHECK(netIdOf(0x999) == 0 && refOf(424242) == 0);
    LocalHitInfo h;
    h.victim = 0x14; h.attacker = 0x100;
    CHECK(routeLocalHit(h).route == HitRoute::Untracked);
    bool called = false;
    setHitForwarder([&](const LocalHitInfo&, const HitRouting&) { called = true; });
    CHECK(!onLocalHit(h) && !called);
}

TEST(api, the_hit_hook_forwards_only_what_another_owner_resolves) {
    FeedRig r;
    r.eng.addNpc(0x100, 0x500, {1000, 1000, 0});
    r.eng.addNpc(0x101, 0x500, {1200, 1000, 0});
    settle(r);
    int forwarded = 0;
    HitRoute last = HitRoute::Untracked;
    setHitForwarder([&](const LocalHitInfo&, const HitRouting& rt) { ++forwarded; last = rt.route; });
    LocalHitInfo h;
    h.attacker = 0x14; h.victim = 0x100; h.damage = 12;
    const HitRouting rt = routeLocalHit(h);
    CHECK(rt.route == HitRoute::LocalOwner && rt.victim.mine);
    CHECK(!onLocalHit(h));                       // mine: the caller applies it
    CHECK(forwarded == 0);
    // the attacker's identity travels with the routing
    h.attacker = 0x101;
    CHECK(routeLocalHit(h).attackerNet == r.id(0x101));
    setPlayerNetIdResolver([](RefId ref) { return ref == 0x14 ? NetId(9001) : NetId(0); }, [](NetId id) { return id == 9001 ? RefId(0x14) : RefId(0); });
    h.attacker = 0x14;
    CHECK(routeLocalHit(h).attackerNet == 9001);
    (void)last;
}

TEST(api, an_actor_nobody_owns_yet_is_neither_local_nor_remote) {
    FeedRig r(false);                              // a client that has not heard from the host
    r.eng.addNpc(0x100, 0x500, {1000, 1000, 0});
    settle(r);
    const OwnerRef o = ownerOf(0x100);
    CHECK(o.known && !o.mine && o.ownerSlot == kNoPlayer);
    CHECK(!isRemoteDriven(0x100));
    LocalHitInfo h;
    h.victim = 0x100;
    CHECK(routeLocalHit(h).route == HitRoute::NoOwner);
    bool called = false;
    setHitForwarder([&](const LocalHitInfo&, const HitRouting&) { called = true; });
    CHECK(!onLocalHit(h) && !called);
}

TEST(api, a_local_death_asks_the_ownership_rules) {
    FeedRig r;
    r.eng.addNpc(0x100, 0x500, {1000, 1000, 0});
    r.eng.addNpc(0x101, 0x510, {1200, 1000, 0}).baseUnique = true;
    settle(r);
    CHECK(onLocalDeath(0x999));                    // not an actor we sync: not our call
    CHECK(onLocalDeath(0x14));
    CHECK(!onLocalDeath(0x101));                   // a story NPC dies only by a host KillActor
    CHECK(!r.sync->isDead(r.id(0x101)));
    CHECK(onLocalDeath(0x100));                    // the owner's actor: allowed, and published
    CHECK(r.sync->isDead(r.id(0x100)));
    CHECK(r.died.size() == 1 && r.died[0].netId == r.id(0x100) && r.died[0].mine);
    CHECK(onLocalDeath(0x100));                    // asking again changes nothing
    CHECK(r.died.size() == 1);
}

TEST(api, a_client_that_does_not_own_the_actor_may_not_kill_it) {
    FeedRig r(false);
    r.eng.addNpc(0x100, 0x500, {1000, 1000, 0});
    settle(r);
    CHECK(!onLocalDeath(0x100));
    CHECK(!r.sync->isDead(r.id(0x100)) && r.died.empty());
}

TEST(api, listeners_are_called_in_order_and_can_leave_from_inside) {
    registry().reset();
    std::vector<int> order;
    ListenerId a = 0, b = 0, c = 0;
    a = addActorTrackedListener([&](const TrackedEvent&) { order.push_back(1); removeListener(a); });
    b = addActorTrackedListener([&](const TrackedEvent&) { order.push_back(2); removeListener(c); });
    c = addActorTrackedListener([&](const TrackedEvent&) { order.push_back(3); });
    CHECK(a && b && c && a != b && b != c);
    registry().emit(TrackedEvent{});
    CHECK((order == std::vector<int>{1, 2}));      // c was removed by b before its turn
    order.clear();
    registry().emit(TrackedEvent{});
    CHECK((order == std::vector<int>{2}));
    removeListener(b);
    order.clear();
    registry().emit(TrackedEvent{});
    CHECK(order.empty());
    CHECK(addActorDiedListener(nullptr) == 0 && addDriveListener(nullptr) == 0);
    int died = 0, lost = 0;
    addActorDiedListener([&](const DiedEvent&) { ++died; });
    addActorLostListener([&](const LostEvent&) { ++lost; });
    registry().emit(DiedEvent{});
    registry().emit(LostEvent{});
    registry().emit(LostEvent{});
    CHECK(died == 1 && lost == 2);
    registry().reset();
}

TEST(api, drive_listeners_see_the_interpolated_state) {
    FeedRig r;
    r.eng.addNpc(0x100, 0x500, {1000, 1000, 0});
    r.startSession();
    r.run(300);
    int seen = 0;
    bool weapon = false;
    addDriveListener([&](const DriveEvent& e) { ++seen; if (e.out) weapon = e.out->anim.weaponDrawn; });
    live::InterpOut o;
    o.state = live::InterpState::Interpolating;
    o.cell = 1; o.pos = {1000, 1000, 0}; o.anim.weaponDrawn = true;
    r.port.drive(r.id(0x100), o);
    CHECK(seen == 1 && weapon);
}

TEST(api, player_poses_effects_and_configuration_reach_their_owners) {
    FeedRig r;
    r.eng.addNpc(0x100, 0x500, {1000, 1000, 0});
    settle(r);
    // a second player far away in the same cell: the host still owns the actor (it is nearer), and the call does not break anything
    PlayerPose p;
    p.present = true; p.cell = 1; p.pos = {90000, 0, 0};
    setPlayerPose(1, p);
    // a KillActor effect reaches ActorSync through the API
    onEffect(fnvcoop::KillActor{r.eng.refForm(0x100)});
    CHECK(r.sync->isDead(r.id(0x100)) && r.eng.a[0x100].life == 2);
    // configuration
    Config c;
    c.scanMs = 1234; c.maxLinesPerFrame = 7;
    configure(c);
    CHECK(config().scanMs == 1234 && config().maxLinesPerFrame == 7);
    // the counters are one merged view
    const PortStats s = portStats();
    CHECK(s.scans >= 1 && s.trackedNew == 1 && s.kills >= 1 && s.consoleLines >= 1);
    // no session: calls are harmless
    r.endSession();
    onEffect(fnvcoop::KillActor{r.eng.refForm(0x100)});
    setPlayerPose(1, p);
    CHECK(!ownerOf(0x100).known);
    registry().reset();
    CHECK(portStats().scans == 0);
    beforeSave();
}
