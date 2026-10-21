// Whole-system tests of synccore/loot on the fake-world model (tests/th.h): real wire bytes, a host, 2 to 4 machines, optimistic local
// transfers, rollbacks. Every scenario ends with the same audit: nothing duplicated, nothing lost, every machine's view equals the host's.
#include <cstdlib>
#include "th.h"

using namespace lt;

namespace {

void givePlayers(World& w, Items stuff = I({{50, 3}, {51, 2}})) {
    for (auto& mc : w.m) mc->player = stuff;
}

// the common audit after a scenario has settled
void audit(World& w, const char* what, bool strict = true) {
    w.settle();
    uint64_t shorts = 0;
    for (auto& mc : w.m) shorts += mc->short_;
    const std::string diff = w.diffTotals();
    if (strict) CHECK_MSG(diff.empty(), std::string(what) + ": totals changed:" + diff);
    else CHECK_MSG(diff.empty() || shorts > 0, std::string(what) + ": totals changed without a short rollback:" + diff);
    CHECK_MSG(w.viewsEqualHost(), std::string(what) + ": a view differs from the host");
    const std::string e = w.engineCopiesDiffer();
    CHECK_MSG(e.empty(), std::string(what) + ": " + e);
    for (auto& mc : w.m) CHECK_MSG(mc->c->pendingCount() == 0, std::string(what) + ": pending ops left");
    for (auto& kv : w.host.containers()) CHECK_MSG(kv.second.holder == kNoPlayer || w.m[kv.second.holder]->menu.count(kv.first), std::string(what) + ": a lock nobody uses");
    // world refs: one alive ref per host item on every machine, none for taken items, never two for one id
    for (auto& mc : w.m) {
        std::map<NetId, int> alive;
        for (auto& kv : mc->refs) if (kv.second.alive && kv.second.net) ++alive[kv.second.net];
        for (auto& kv : alive) CHECK_MSG(kv.second == 1, std::string(what) + ": two refs for one item on machine " + std::to_string(mc->slot));
        for (auto& kv : w.host.items()) CHECK_MSG(alive.count(kv.first) == 1, std::string(what) + ": a dropped item has no ref on machine " + std::to_string(mc->slot));
        for (auto& kv : alive) {
            if (isIssuedItemId(kv.first)) CHECK_MSG(w.host.item(kv.first) != nullptr, std::string(what) + ": a ref for an item that is gone");
            else CHECK_MSG(!w.host.placedTaken(kv.first), std::string(what) + ": a ref for a taken placed item");
        }
    }
}

}  // namespace

TEST(model, a_container_is_shared_one_holder_at_a_time_and_every_item_stays_accounted_for) {
    World w(3);
    givePlayers(w);
    w.giveEngine(0x100, I({{1, 10}, {2, 3}}));
    w.open(1, 0x100);
    w.run(200);
    CHECK(w.m[1]->c->holdsLock(0x100));
    w.snapshotInitial();                         // the host knows the contents now (adopted from the first opener)
    REQUIRE(w.host.container(0x100));
    CHECK(w.host.container(0x100)->items == I({{1, 10}, {2, 3}}));

    // a second player is refused while the first one is shopping
    w.open(2, 0x100);
    w.run(200);
    CHECK(!w.m[2]->menu.count(0x100));           // its menu was closed
    CHECK(std::find(w.m[2]->notices.begin(), w.m[2]->notices.end(), Notice::InUse) != w.m[2]->notices.end());

    CHECK(w.take(1, 0x100, 1, 4));
    CHECK(w.put(1, 0x100, 50, 1));
    w.run(300);
    CHECK(count(w.host.container(0x100)->items, 1) == 6);
    CHECK(count(w.host.container(0x100)->items, 50) == 1);
    w.close(1, 0x100);
    w.run(300);
    CHECK(w.host.container(0x100)->holder == kNoPlayer);

    // now the other one: its engine copy shows what the first one left
    CHECK(count(w.m[2]->eng[0x100], 1) == 6);
    w.open(2, 0x100);
    w.run(200);
    CHECK(w.m[2]->c->holdsLock(0x100));
    CHECK(w.take(2, 0x100, 1, 6));
    w.close(2, 0x100);
    audit(w, "shared container");
    CHECK(count(w.m[1]->player, 1) == 4);
    CHECK(count(w.m[2]->player, 1) == 6);
    CHECK(count(w.host.container(0x100)->items, 1) == 0);
    CHECK(w.host.stats().closeMismatch == 0);
}

TEST(model, the_host_players_own_container_use_obeys_the_same_lock) {
    World w(2);
    givePlayers(w);
    w.giveEngine(0x100, I({{1, 10}}));
    w.open(1, 0x100);
    w.run(100);
    w.snapshotInitial();
    w.open(0, 0x100);                             // the host's own player
    w.run(100);
    CHECK(!w.m[0]->menu.count(0x100));
    CHECK(w.take(1, 0x100, 1, 10));
    w.close(1, 0x100);
    w.run(200);
    w.open(0, 0x100);
    w.run(100);
    CHECK(w.m[0]->c->holdsLock(0x100));
    CHECK(w.put(0, 0x100, 50, 2));
    w.close(0, 0x100);
    audit(w, "host player");
    CHECK(count(w.host.container(0x100)->items, 50) == 2);
}

TEST(model, leveled_loot_is_resolved_once_and_everybody_else_gets_the_real_items) {
    World w(3);
    givePlayers(w);
    // each engine rolled its own contents for the same container
    w.m[0]->eng[0x100] = I({{1, 5}});
    w.m[1]->eng[0x100] = I({{1, 2}, {7, 9}});
    w.m[2]->eng[0x100] = I({{3, 4}});
    // the owner of the cell (machine 1) resolves it at cell load
    w.m[1]->c->resolved(0x100, ContainerKind::Container, 7, V(), w.m[1]->eng[0x100], w.now);
    // machine 2 has the cell loaded too and resolves it in the same instant: it is told to adopt the shared contents
    w.m[2]->c->resolved(0x100, ContainerKind::Container, 7, V(), w.m[2]->eng[0x100], w.now);
    w.run(400);
    REQUIRE(w.host.container(0x100));
    CHECK(w.host.container(0x100)->items == I({{1, 2}, {7, 9}}));
    CHECK(w.m[2]->eng[0x100] == I({{1, 2}, {7, 9}}));           // its roll was replaced
    CHECK(w.host.stats().alreadyResolved == 1);
    w.snapshotInitial();
    // machine 0 never resolved anything: opening it shows the shared contents, not its own roll
    w.open(0, 0x100);
    w.run(200);
    CHECK(w.m[0]->eng[0x100] == I({{1, 2}, {7, 9}}));
    CHECK(w.take(0, 0x100, 7, 9));
    CHECK(!w.take(0, 0x100, 3, 1));                              // the item only machine 2's roll had does not exist
    w.close(0, 0x100);
    audit(w, "leveled");
    CHECK(count(w.m[0]->player, 7) == 9);
}

TEST(model, a_container_opened_first_by_a_guest_is_adopted_and_the_hosts_own_roll_gives_way) {
    World w(2);
    givePlayers(w);
    w.m[0]->eng[0x100] = I({{1, 5}});
    w.m[1]->eng[0x100] = I({{9, 1}});
    w.open(1, 0x100);
    w.run(200);
    w.snapshotInitial();
    CHECK(w.host.container(0x100)->items == I({{9, 1}}));
    w.close(1, 0x100);
    w.run(200);
    w.open(0, 0x100);
    w.run(200);
    CHECK(w.m[0]->eng[0x100] == I({{9, 1}}));
    w.close(0, 0x100);
    audit(w, "adopt");
}

TEST(model, a_corpse_is_registered_by_its_owner_and_looted_by_somebody_else) {
    World w(3);
    givePlayers(w);
    const NetId corpse = 0x2000;
    w.m[1]->eng[corpse] = I({{20, 1}, {21, 40}});
    w.m[1]->c->resolved(corpse, ContainerKind::Corpse, 7, V(5, 5, 0), w.m[1]->eng[corpse], w.now);
    w.run(300);
    w.snapshotInitial();
    CHECK(w.host.container(corpse)->kind == ContainerKind::Corpse);
    w.open(2, corpse);
    w.run(200);
    CHECK(w.m[2]->eng[corpse] == I({{20, 1}, {21, 40}}));
    CHECK(w.take(2, corpse, 21, 40));
    CHECK(w.take(2, corpse, 20, 1));
    w.close(2, corpse);
    audit(w, "corpse");
    CHECK(w.host.container(corpse)->items.empty());
    CHECK(count(w.m[2]->player, 21) == 40);
}

TEST(model, two_players_grabbing_the_same_placed_item_at_once_get_it_once) {
    World w(3);
    givePlayers(w);
    w.placeItem(0x4000, 77, 1);
    w.snapshotInitial();
    const uint32_t r1 = w.refFor(1, 0x4000), r2 = w.refFor(2, 0x4000);
    REQUIRE(r1);
    REQUIRE(r2);
    CHECK(w.pickup(1, r1));
    CHECK(w.pickup(2, r2));                      // before any answer: both machines handed the item out optimistically
    CHECK(count(w.m[1]->player, 77) == 1);
    CHECK(count(w.m[2]->player, 77) == 1);
    audit(w, "placed race");
    const int total = count(w.m[1]->player, 77) + count(w.m[2]->player, 77) + count(w.m[0]->player, 77);
    CHECK(total == 1);
    // the loser was told and nothing is left on the ground anywhere
    const int told = static_cast<int>(std::count(w.m[1]->notices.begin(), w.m[1]->notices.end(), Notice::AlreadyTaken) +
                                      std::count(w.m[2]->notices.begin(), w.m[2]->notices.end(), Notice::AlreadyTaken));
    CHECK(told == 1);
    for (auto& mc : w.m) CHECK(mc->aliveRefs() == 0);
    CHECK(w.host.placedTaken(0x4000));
}

TEST(model, the_race_for_a_dropped_item_has_one_winner_whoever_asks_first) {
    World w(3);
    givePlayers(w);
    w.snapshotInitial();
    const uint32_t ref = w.dropItem(1, 50, 2);
    REQUIRE(ref);
    w.run(300);
    // everybody has a ref now, the dropper's is bound to the issued id
    REQUIRE(w.m[1]->refs.count(ref));
    const NetId net = w.m[1]->refs[ref].net;
    REQUIRE(isIssuedItemId(net));
    REQUIRE(w.host.item(net) != nullptr);
    CHECK(w.m[0]->refOfNet(net) != nullptr);
    CHECK(w.m[2]->refOfNet(net) != nullptr);
    CHECK(w.m[1]->aliveRefs() == 1);             // not duplicated for the dropper
    CHECK(w.host.item(net)->pos == V(5, 5, 0));
    // the dropper changes its mind in the same instant the other player grabs it
    CHECK(w.pickup(1, ref));
    CHECK(w.pickup(2, w.refFor(2, net)));
    audit(w, "drop race");
    CHECK(count(w.m[0]->player, 50) + count(w.m[1]->player, 50) + count(w.m[2]->player, 50) == 9);   // three players with three each: one pickup won
}

TEST(model, a_pickup_by_another_player_removes_the_ref_everywhere_else) {
    World w(3);
    givePlayers(w);
    w.snapshotInitial();
    const uint32_t ref = w.dropItem(1, 51, 1);
    w.run(300);
    REQUIRE(ref);
    const NetId net = w.m[1]->refs[ref].net;
    CHECK(w.pickup(2, w.refFor(2, net)));
    audit(w, "pickup");
    CHECK(count(w.m[2]->player, 51) == 3);
    CHECK(count(w.m[1]->player, 51) == 1);
    CHECK(w.m[1]->aliveRefs() == 0);
    CHECK(w.m[0]->aliveRefs() == 0);
    CHECK(w.host.itemCount() == 0);
}

TEST(model, a_dropped_item_is_created_exactly_once_on_a_link_that_loses_and_duplicates) {
    World w(3, 5);
    w.net.latency = 40;
    w.net.loss = 0.2;
    w.net.dup = 0.3;
    givePlayers(w, I({{50, 30}}));
    w.snapshotInitial();
    for (int i = 0; i < 12; ++i) {
        w.dropItem(1 + (i & 1), 50, 1, V(static_cast<float>(i), 0, 0));
        w.run(200);
    }
    w.net.loss = 0; w.net.dup = 0;                  // the audit runs on a healthy link: the digest has to heal what the loss broke
    audit(w, "lossy drops");
    CHECK(w.host.itemCount() == 12);
    CHECK(w.m[1]->c->stats().resent + w.m[2]->c->stats().resent > 0);   // the loss really happened
}

TEST(model, an_unanswered_take_is_resent_and_applied_once) {
    World w(2, 3);
    givePlayers(w);
    w.giveEngine(0x100, I({{1, 10}}));
    w.open(1, 0x100);
    w.run(200);
    w.snapshotInitial();
    w.m[1]->cut = true;                          // the link dies right as the player takes
    CHECK(w.take(1, 0x100, 1, 3));
    w.run(500);
    CHECK(count(w.host.container(0x100)->items, 1) == 10);
    w.m[1]->cut = false;
    w.net.dup = 1.0;                             // and when it comes back every datagram arrives twice
    w.run(4000);
    CHECK(count(w.host.container(0x100)->items, 1) == 7);
    w.net.dup = 0;
    w.close(1, 0x100);
    audit(w, "resend");
    CHECK(w.host.stats().replays + w.m[1]->c->stats().resent >= 1);
}

TEST(model, a_player_whose_link_was_cut_converges_through_the_digest) {
    World w(3, 4);
    givePlayers(w);
    w.giveEngine(0x100, I({{1, 10}}));
    w.giveEngine(0x101, I({{2, 10}}));
    w.open(1, 0x100);
    w.run(200);
    w.close(1, 0x100);
    w.run(200);
    w.snapshotInitial();
    w.m[2]->cut = true;
    w.open(1, 0x100);
    w.run(200);
    CHECK(w.take(1, 0x100, 1, 5));
    w.close(1, 0x100);
    w.run(1000);
    CHECK(w.m[2]->c->container(0x100)->items != w.host.container(0x100)->items);   // the cut one missed it
    w.m[2]->cut = false;
    w.settle();
    audit(w, "cut link");
    CHECK(w.m[2]->eng[0x100] == I({{1, 5}}));
}

TEST(model, a_holder_who_lost_its_lock_is_rolled_back_and_its_menu_closed) {
    HostConfig hc;
    hc.lockLeaseMs = 3000;
    World w(3, 7, hc);
    givePlayers(w);
    w.giveEngine(0x100, I({{1, 10}}));
    w.open(1, 0x100);
    w.run(200);
    w.snapshotInitial();
    w.m[1]->cut = true;                           // machine 1 goes dark with its menu open
    w.run(5000);
    w.open(2, 0x100);                             // the lease is over: the other player gets it
    w.run(300);
    CHECK(w.m[2]->c->holdsLock(0x100));
    CHECK(w.take(1, 0x100, 1, 6));                // machine 1 does not know and keeps taking
    w.m[1]->cut = false;
    w.run(6000);
    CHECK(!w.m[1]->menu.count(0x100));            // its menu was closed
    CHECK(count(w.m[1]->player, 1) == 0);         // and the items it took went back
    CHECK(count(w.host.container(0x100)->items, 1) == 10);
    w.close(2, 0x100);
    audit(w, "lease");
}

TEST(model, a_script_changing_a_container_nobody_holds_reaches_every_engine) {
    World w(3);
    givePlayers(w);
    w.giveEngine(0x100, I({{1, 10}}));
    w.open(1, 0x100);
    w.run(200);
    w.close(1, 0x100);
    w.run(300);
    w.snapshotInitial();
    LootHost::Out o;
    CHECK(w.host.externalSet(0x100, I({{1, 10}, {99, 1}}), w.now, o));
    w.flush(o);
    w.initial[F(99)] += 1;
    w.run(300);
    CHECK(w.m[2]->eng[0x100] == I({{1, 10}, {99, 1}}));
    CHECK(w.m[0]->eng[0x100] == I({{1, 10}, {99, 1}}));
    // while somebody holds it the script's change is refused (the holder's transfer must not be undone)
    w.open(2, 0x100);
    w.run(200);
    CHECK(!w.host.externalSet(0x100, I({{1, 1}}), w.now, o));
    CHECK(w.take(2, 0x100, 99, 1));
    w.close(2, 0x100);
    audit(w, "script change");
}

TEST(model, a_machine_that_joins_late_sees_the_containers_and_items_and_opens_them_correctly) {
    World w(2);
    givePlayers(w);
    w.giveEngine(0x100, I({{1, 10}}));
    w.open(1, 0x100);
    w.run(200);
    CHECK(w.take(1, 0x100, 1, 3));
    w.close(1, 0x100);
    w.dropItem(1, 50, 1);
    w.run(500);
    Machine& late = w.addMachine();
    late.player = I({{50, 3}, {51, 2}});
    w.snapshotInitial();
    w.settle();
    CHECK(late.c->container(0x100) != nullptr);
    CHECK(late.c->itemCount() == 1);
    CHECK(late.aliveRefs() == 1);                 // the table made a world ref for the item that was dropped before it joined
    late.eng[0x100] = I({{1, 10}});               // the late machine's own engine rolled something else
    w.open(2, 0x100);
    w.run(300);
    CHECK(late.eng[0x100] == I({{1, 7}}));
    w.close(2, 0x100);
    audit(w, "late join");
}

TEST(model, nothing_about_a_players_inventory_is_ever_on_the_wire) {
    World w(3);
    givePlayers(w);
    w.giveEngine(0x100, I({{1, 10}}));
    w.open(1, 0x100);
    w.run(200);
    w.take(1, 0x100, 1, 2);
    w.put(1, 0x100, 50, 2);
    w.close(1, 0x100);
    w.dropItem(2, 51, 1);
    w.run(1000);
    // the only messages that exist are the ones in Body; none of them is a player-inventory message
    for (size_t k : w.kindsSeen) {
        const std::string name = [&] {
            Body b;
            switch (k) {
            case 0: b = ReqRegister{}; break; case 1: b = ReqOpen{}; break; case 2: b = ReqMove{}; break; case 3: b = ReqClose{}; break;
            case 4: b = ReqHold{}; break; case 5: b = ReqDrop{}; break; case 6: b = ReqClaim{}; break; case 7: b = ReqResync{}; break;
            case 8: b = ReqDigest{}; break; case 9: b = ReqTouch{}; break; case 10: b = Result{}; break; case 11: b = MContainer{}; break;
            case 12: b = MDelta{}; break; case 13: b = MLock{}; break; case 14: b = MItemNew{}; break; case 15: b = MItemGone{}; break;
            case 16: b = MContainerGone{}; break; case 17: b = MTableBegin{}; break; case 18: b = MTableEnd{}; break;
            case 19: b = MClutter{}; break; default: b = MClutterOwn{}; break;
            }
            return std::string(bodyName(b));
        }();
        CHECK(name.find("Inventory") == std::string::npos);
        CHECK(name.find("Player") == std::string::npos);
    }
    // and what each player owns differs per machine: the host never learned the guests' items
    CHECK(count(w.m[0]->player, 50) == 3);
    CHECK(count(w.m[1]->player, 50) == 1);
}

TEST(model, physics_clutter_follows_its_mover_and_costs_nothing_at_rest) {
    World w(3);
    givePlayers(w);
    w.snapshotInitial();
    const uint32_t ref = w.dropItem(1, 50, 1, V(0, 0, 0));
    w.run(300);
    const NetId net = w.m[1]->refs[ref].net;
    REQUIRE(isIssuedItemId(net));
    REQUIRE(w.host.item(net) != nullptr);
    CHECK(w.m[1]->clutterOwned.count(net) == 1);
    // machine 1 owns the dropped item and pushes it around
    const uint64_t before = w.m[1]->c->stats().clutterSent;
    for (int i = 1; i <= 30; ++i) {
        w.m[1]->c->clutterSamples({{net, V(static_cast<float>(i) * 10, 0, 0), V()}}, w.now);
        w.run(70);
    }
    w.run(1000);   // comes to rest: one final snapshot
    w.m[1]->c->clutterSamples({{net, V(300, 0, 0), V()}}, w.now);
    w.run(500);
    const uint64_t sentMoving = w.m[1]->c->stats().clutterSent - before;
    CHECK(sentMoving >= 20);
    CHECK(sentMoving <= 33);
    CHECK(w.m[2]->shown[net].x == 300);          // the final resting pose arrived
    CHECK(w.m[0]->shown[net].x == 300);
    CHECK(w.host.item(net)->pos.x == 300);
    // a long rest: not one more datagram
    const uint64_t sentBefore = w.sent;
    for (int i = 0; i < 200; ++i) {
        w.m[1]->c->clutterSamples({{net, V(300, 0, 0), V()}}, w.now);
        w.run(100);
    }
    const uint64_t chatter = w.sent - sentBefore;
    // (digests are the only background traffic: one per 2 s per machine at most, over 20 s)
    CHECK(chatter <= 3 * 10 + 6);
    CHECK(w.m[1]->c->stats().clutterSent == before + sentMoving);   // not one clutter datagram while it rests
    // machine 2 shoves it: after the owner is idle it takes over and its pushes are what everyone sees
    w.m[2]->c->touch(net, V(300, 0, 0), V(), w.now);
    w.run(300);
    CHECK(w.host.clutterOwner(net) == 2);
    CHECK(w.m[2]->clutterOwned.count(net) == 1);
    CHECK(w.m[1]->clutterOwned.count(net) == 0);
    w.m[2]->c->clutterSamples({{net, V(500, 0, 0), V()}}, w.now);
    w.run(300);
    CHECK(w.m[1]->shown[net].x == 500);
    // the old owner can no longer move it on everybody else's screen
    w.m[1]->c->clutterSamples({{net, V(0, 0, 0), V()}}, w.now);
    w.run(300);
    CHECK(w.m[0]->shown[net].x == 500);
}

// ---------------------------------------------------------------------------------------------------------------------- fuzz
namespace {

struct FuzzCfg {
    int players = 3;
    uint64_t seed = 1;
    uint32_t latency = 20, jitter = 0;
    double loss = 0, dup = 0;
    bool strict = true;     // reliable ordered network, players wait for their answers; no cuts
    bool leveled = false;   // every engine rolled its own contents
    int seconds = 40;
    bool noCuts = false;
};

std::string fuzzOnce(const FuzzCfg& f) {
    HostConfig hc;
    hc.lockLeaseMs = 20000;
    ClientConfig cc;
    World w(f.players, f.seed, hc, cc);
    w.trace = std::getenv("LOOT_FUZZ_TRACE") != nullptr;
    w.net.latency = f.latency; w.net.jitter = f.jitter; w.net.loss = f.loss; w.net.dup = f.dup;
    Rng rng(f.seed * 7919 + 13);
    givePlayers(w, I({{50, 3}, {51, 2}}));
    const std::vector<NetId> conts = {0x100, 0x101, 0x102};
    const Items base[3] = {I({{1, 6}, {2, 4}, {3, 2}}), I({{4, 5}}), I({{1, 3}, {5, 9}})};
    for (size_t i = 0; i < conts.size(); ++i) {
        for (auto& mc : w.m) {
            mc->eng[conts[i]] = base[i];
            if (f.leveled && mc->slot != 1) mc->eng[conts[i]][F(100 + mc->slot)] = 1 + static_cast<int32_t>(mc->slot);   // a different roll
        }
    }
    // the cell owner (machine 1, or 0 when alone) resolves two of the three at load; the third is adopted at its first open
    w.m[w.m.size() > 1 ? 1 : 0]->c->resolved(conts[0], ContainerKind::Container, 7, V(), w.m[w.m.size() > 1 ? 1 : 0]->eng[conts[0]], w.now);
    w.m[w.m.size() > 1 ? 1 : 0]->c->resolved(conts[1], ContainerKind::Corpse, 7, V(), w.m[w.m.size() > 1 ? 1 : 0]->eng[conts[1]], w.now);
    w.placeItem(0x4000, 90, 1); w.placeItem(0x4001, 91, 2); w.placeItem(0x4002, 92, 1);
    w.run(500);
    w.snapshotInitial();
    // the host's table is the truth for the initial totals of resolved containers; the unresolved third one is counted when adopted
    // (it is not in the host table yet, so add it by hand and remove it again once the host has it)
    std::map<NetId, bool> counted;
    for (size_t i = 0; i < conts.size(); ++i) counted[conts[i]] = w.host.container(conts[i]) != nullptr;

    std::vector<uint64_t> nextAct(w.m.size(), w.now + 300);
    std::vector<uint64_t> cutUntil(w.m.size(), 0);
    uint64_t nextScript = w.now + 3000, nextCut = w.now + 4000;
    const uint64_t end = w.now + static_cast<uint64_t>(f.seconds) * 1000;
    while (w.now < end) {
        w.step(10);
        // adopted containers become part of the accounting the moment the host has them
        for (size_t i = 0; i < conts.size(); ++i) {
            if (!counted[conts[i]] && w.host.container(conts[i])) {
                counted[conts[i]] = true;
                for (auto& kv : w.host.container(conts[i])->items) w.initial[kv.first] += kv.second;
            }
        }
        for (size_t who = 0; who < w.m.size(); ++who) {
            Machine& me = *w.m[who];
            if (!f.strict && cutUntil[who] && w.now >= cutUntil[who]) { me.cut = false; cutUntil[who] = 0; }
            if (w.now < nextAct[who]) continue;
            if (f.strict && me.c->pendingCount() != 0) continue;
            nextAct[who] = w.now + 250 + rng.range(0, 250);
            const uint32_t r = rng.u32() % 100;
            if (!me.menu.empty()) {
                const NetId c = *me.menu.begin();
                if (r < 40) {
                    Items& it = me.eng[c];
                    if (!it.empty()) {
                        auto pick = it.begin();
                        std::advance(pick, rng.range(0, static_cast<uint32_t>(it.size() - 1)));
                        const int32_t n = std::min<int32_t>(static_cast<int32_t>(rng.range(1, 3)), pick->second);
                        w.take(who, c, pick->first.localId, n);
                    }
                } else if (r < 62) {
                    if (!me.player.empty()) {
                        auto pick = me.player.begin();
                        std::advance(pick, rng.range(0, static_cast<uint32_t>(me.player.size() - 1)));
                        w.put(who, c, pick->first.localId, 1);
                    }
                } else if (r < 88) {
                    w.close(who, c);
                }
            } else if (r < 28) {
                w.open(who, conts[rng.range(0, static_cast<uint32_t>(conts.size() - 1))]);
            } else if (r < 42) {
                if (!me.player.empty()) {
                    auto pick = me.player.begin();
                    std::advance(pick, rng.range(0, static_cast<uint32_t>(me.player.size() - 1)));
                    w.dropItem(who, pick->first.localId, 1, V(static_cast<float>(rng.range(0, 50)), 0, 0));
                }
            } else if (r < 70) {
                std::vector<uint32_t> alive;
                for (auto& kv : me.refs) if (kv.second.alive && kv.second.net) alive.push_back(kv.first);
                if (!alive.empty()) w.pickup(who, alive[rng.range(0, static_cast<uint32_t>(alive.size() - 1))]);
            }
        }
        if (w.now >= nextScript) {
            nextScript = w.now + 2500 + rng.range(0, 3000);
            const NetId c = conts[rng.range(0, static_cast<uint32_t>(conts.size() - 1))];
            if (const ContainerRec* h = w.host.container(c)) {
                Items want = h->items;
                want[F(6)] += 1;
                LootHost::Out o;
                if (w.host.externalSet(c, want, w.now, o)) {
                    w.flush(o);
                    w.initial[F(6)] += 1;
                }
            }
        }
        if (!f.strict && !f.noCuts && w.now >= nextCut && w.m.size() > 1) {
            nextCut = w.now + 4000 + rng.range(0, 4000);
            const size_t who = 1 + rng.range(0, static_cast<uint32_t>(w.m.size() - 2));
            w.m[who]->cut = true;
            cutUntil[who] = w.now + 800 + rng.range(0, 1500);
        }
    }
    for (auto& mc : w.m) { mc->cut = false; }
    for (size_t who = 0; who < w.m.size(); ++who) {
        std::vector<NetId> open(w.m[who]->menu.begin(), w.m[who]->menu.end());
        for (NetId c : open) w.close(who, c);
    }
    w.net.loss = 0; w.net.dup = 0; w.net.jitter = 0;
    w.settle(60000);
    w.run(8000);
    w.settle(60000);

    if (std::getenv("LOOT_FUZZ_VERBOSE")) {
        std::printf("  seed %llu: host digest %016llx containers %zu items %zu now %llu\n", (unsigned long long)f.seed, (unsigned long long)w.host.digest(),
                    w.host.containerCount(), w.host.itemCount(), (unsigned long long)w.now);
        for (auto& mc : w.m) {
            const auto& st = mc->c->stats();
            std::printf("    m%d digest %016llx cont %zu items %zu pending %zu digestsSent %llu gaps %llu resyncs %llu resent %llu gaveUp %llu tables %llu\n", mc->slot,
                        (unsigned long long)mc->c->digest(), mc->c->containerCount(), mc->c->itemCount(), mc->c->pendingCount(), (unsigned long long)st.digestsSent,
                        (unsigned long long)st.gaps, (unsigned long long)st.resyncsAsked, (unsigned long long)st.resent, (unsigned long long)st.gaveUp,
                        (unsigned long long)st.tablesApplied);
        }
        for (auto& mc : w.m)
            for (auto& kv : mc->eng) {
                const ContainerRec* vr = mc->c->container(kv.first);
                const ContainerRec* hr = w.host.container(kv.first);
                std::printf("    m%d c=%u engine {%s} view rev %u {%s} host rev %u {%s} local: %s\n", mc->slot, kv.first, describe(kv.second).c_str(), vr ? vr->rev : 0,
                            vr ? describe(vr->items).c_str() : "-", hr ? hr->rev : 0, hr ? describe(hr->items).c_str() : "-", mc->c->debugLocal(kv.first).c_str());
            }
        const auto& hs = w.host.stats();
        std::printf("    host: digestsSeen %llu mismatch %llu heals %llu stale %llu replays %llu\n", (unsigned long long)hs.digestsSeen, (unsigned long long)hs.digestMismatch,
                    (unsigned long long)hs.healsSent, (unsigned long long)hs.stale, (unsigned long long)hs.replays);
    }
    std::string bad;
    uint64_t shorts = 0;
    for (auto& mc : w.m) shorts += mc->short_;
    const std::string diff = w.diffTotals();
    if (!diff.empty() && (f.strict || shorts == 0)) bad += " totals changed:" + diff;
    if (!w.viewsEqualHost()) bad += " views differ from the host;";
    const std::string e = w.engineCopiesDiffer();
    if (!e.empty()) bad += " " + e + ";";
    for (auto& mc : w.m) if (mc->c->pendingCount()) bad += " pending left;";
    for (auto& kv : w.host.containers()) if (kv.second.holder != kNoPlayer) bad += " a lock is still held;";
    for (auto& mc : w.m) {
        std::map<NetId, int> alive;
        for (auto& kv : mc->refs) if (kv.second.alive && kv.second.net) ++alive[kv.second.net];
        for (auto& kv : alive) {
            if (kv.second != 1) bad += " duplicate ref;";
            if (isIssuedItemId(kv.first) && !w.host.item(kv.first)) bad += " ref of a gone item;";
            if (!isIssuedItemId(kv.first) && w.host.placedTaken(kv.first)) bad += " ref of a taken placed item;";
        }
        for (auto& kv : w.host.items()) if (!alive.count(kv.first)) bad += " item without ref on " + std::to_string(mc->slot) + ";";
    }
    if (w.trace && !bad.empty()) { const char* filt = std::getenv("LOOT_FUZZ_TRACE"); for (auto& l : w.log) if (!*filt || l.find(filt) != std::string::npos) std::printf("      %s\n", l.c_str()); }
    if (f.strict && w.host.stats().closeMismatch != 0) bad += " close digest mismatch " + std::to_string(w.host.stats().closeMismatch) + ";";
    if (f.strict && shorts != 0) bad += " a rollback found less than it had to take back;";
    return bad;
}

int seedsFromEnv(int dflt) {
    const char* e = std::getenv("LOOT_FUZZ_SEEDS");
    return e && *e ? std::atoi(e) : dflt;
}

}  // namespace

TEST(fuzz, strict_sessions_on_a_reliable_ordered_link_lose_and_duplicate_nothing) {
    const int seeds = seedsFromEnv(14);
    for (int s = 1; s <= seeds; ++s) {
        FuzzCfg f;
        f.seed = static_cast<uint64_t>(s);
        f.players = 2 + s % 3;
        const std::string bad = fuzzOnce(f);
        CHECK_MSG(bad.empty(), "seed " + std::to_string(s) + " players " + std::to_string(f.players) + ":" + bad);
    }
}

TEST(fuzz, sessions_with_every_engine_rolling_its_own_loot_still_end_with_one_truth) {
    const int seeds = seedsFromEnv(14);
    for (int s = 100; s < 100 + seeds / 2 + 1; ++s) {
        FuzzCfg f;
        f.seed = static_cast<uint64_t>(s);
        f.players = 2 + s % 3;
        f.leveled = true;
        const std::string bad = fuzzOnce(f);
        CHECK_MSG(bad.empty(), "seed " + std::to_string(s) + ":" + bad);
    }
}

TEST(fuzz, lossy_duplicating_cut_links_converge_and_never_create_items_from_nothing) {
    const int seeds = seedsFromEnv(14);
    for (int s = 200; s < 200 + seeds; ++s) {
        if (const char* only = std::getenv("LOOT_FUZZ_SEED_ONLY")) { if (s != std::atoi(only)) continue; }
        FuzzCfg f;
        f.seed = static_cast<uint64_t>(s);
        f.players = 2 + s % 3;
        f.strict = false;
        f.loss = 0.08;
        f.dup = 0.1;
        f.latency = 40;
        f.jitter = 0;
        if (const char* e = std::getenv("LOOT_FUZZ_LOSS")) f.loss = std::atof(e);      // debugging aids: isolate which fault matters
        if (const char* e = std::getenv("LOOT_FUZZ_DUP")) f.dup = std::atof(e);
        if (const char* e = std::getenv("LOOT_FUZZ_NOCUT")) f.noCuts = std::atoi(e) != 0;
        const std::string bad = fuzzOnce(f);
        CHECK_MSG(bad.empty(), "seed " + std::to_string(s) + ":" + bad);
    }
}
