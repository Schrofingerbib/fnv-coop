// LootSync against a fake engine and an in-memory link: the container watcher, the actions, corpses, drops, pickups, the host sweep, the audit,
// clutter. Twins 141..148, 150, 151, 154, 155 are guarded here.
#include "rig.h"

using namespace nvl_test;

namespace {

// the same non-leveled container on every engine
void share(Cluster& c, NetId net, const Items& items, lc::ContainerKind k = lc::ContainerKind::Container) {
    for (auto& mc : c.m) mc->eng.add(net, items, k);
}
const lc::ContainerRec* tableOf(Cluster& c, NetId net) { return c.host().sync->host()->container(net); }

}  // namespace

TEST(sync, a_guest_taking_and_putting_is_read_from_the_engine_and_reaches_the_table_and_the_other_engines) {
    Cluster c(3);
    share(c, 0x100, I({{1, 10}, {2, 3}}));
    Machine& g = c.machine(1);
    g.eng.player = I({{50, 2}});
    g.eng.openMenuOn(0x100);
    c.run(300);
    REQUIRE(g.sync->client()->holdsLock(0x100));
    REQUIRE(tableOf(c, 0x100));
    CHECK(tableOf(c, 0x100)->holder == 1);
    CHECK(g.eng.take(0x100, 1, 4));
    CHECK(g.eng.put(0x100, 50, 1));
    c.run(300);
    CHECK(count(tableOf(c, 0x100)->items, 1) == 6);
    CHECK(count(tableOf(c, 0x100)->items, 50) == 1);
    g.eng.menu = 0;                                // the player leaves the menu
    c.run(500);
    CHECK(tableOf(c, 0x100)->holder == lc::kNoPlayer);
    CHECK(count(g.eng.player, 1) == 4);
    // the other machines' engine copies follow without anybody opening anything
    CHECK(count(c.machine(2).eng.conts[0x100].items, 1) == 6);
    CHECK(count(c.host().eng.conts[0x100].items, 50) == 1);
    CHECK(c.host().sync->host()->stats().closeMismatch == 0);
}

TEST(sync, a_second_player_is_refused_the_menu_is_closed_and_nothing_is_closed_on_the_host_afterwards) {
    Cluster c(3);
    share(c, 0x100, I({{1, 10}}));
    Machine& a = c.machine(1);
    Machine& b = c.machine(2);
    a.eng.openMenuOn(0x100);
    c.run(300);
    b.eng.openMenuOn(0x100);
    c.run(300);
    CHECK(b.eng.closeCalls == 1);                  // the engine was asked to close the menu
    CHECK(b.eng.menu == 0);
    bool told = false;
    for (auto& n : b.eng.notices) if (n.first == lc::Notice::InUse) told = true;
    CHECK(told);
    CHECK(tableOf(c, 0x100)->holder == 1);
    CHECK(b.sync->client()->stats().closes == 0);  // twin 144: the menu WE closed is not reported as the player closing it
    // the host's own player is held back the same way
    c.host().eng.openMenuOn(0x100);
    c.run(300);
    CHECK(c.host().eng.closeCalls == 1);
    CHECK(tableOf(c, 0x100)->holder == 1);
}

TEST(sync, a_move_the_host_refuses_is_taken_back_from_the_players_inventory) {
    SyncConfig cfg = Cluster::defaultCfg();
    cfg.menuless = false;                           // the menu watcher on its own (the menu-less screening would also catch a move the watcher missed: twin 141)
    Cluster c(3, cfg);
    share(c, 0x100, I({{1, 10}}));
    Machine& a = c.machine(1);
    Machine& b = c.machine(2);
    a.eng.openMenuOn(0x100);
    c.run(300);
    b.eng.player = I({{50, 1}});
    b.eng.openMenuOn(0x100);
    c.run(25);                                      // opened, request in flight
    CHECK(b.eng.take(0x100, 1, 3));                 // the player does not know yet
    CHECK(count(b.eng.player, 1) == 3);
    c.run(400);
    CHECK(count(b.eng.player, 1) == 0);             // taken back
    CHECK(b.eng.adjustCalls >= 1);
    CHECK(count(tableOf(c, 0x100)->items, 1) == 10);
    CHECK(count(b.eng.conts[0x100].items, 1) == 10);   // and the container shows the real contents
}

TEST(sync, the_container_is_read_before_a_message_that_replaces_its_contents_is_handled) {
    // the grant carries different contents (this engine rolled another list) and arrives after the player already took something
    SyncConfig cfg = Cluster::defaultCfg();
    cfg.pollMs = 500;
    Cluster c(1, cfg);
    c.host().eng.add(0x100, I({{1, 10}}));
    c.host().eng.openMenuOn(0x100);
    c.run(100);
    c.host().eng.menu = 0;
    c.run(300);
    REQUIRE(tableOf(c, 0x100));
    Machine& g = c.addMachine();
    g.eng.add(0x100, I({{1, 10}, {9, 5}}));         // its own roll
    g.eng.player = I({{50, 1}});
    c.settle();
    g.eng.openMenuOn(0x100);
    c.step(10);                                     // opened, the request is on its way
    CHECK(g.eng.take(0x100, 1, 4));                 // taken before any answer, before any poll
    c.run(120);
    c.settle();
    CHECK(count(g.eng.player, 1) == 4);
    CHECK(count(tableOf(c, 0x100)->items, 1) == 6);   // the take reached the host; the replaced contents did not hand the items back
    CHECK(tableOf(c, 0x100)->items.count(F(9)) == 0);
    CHECK(count(g.eng.conts[0x100].items, 9) == 0);
}

TEST(sync, a_correction_the_engine_could_only_half_apply_is_not_mistaken_for_a_player_move) {
    Cluster c(1);
    c.host().eng.add(0x100, I({{1, 10}}));
    c.host().eng.openMenuOn(0x100);
    c.run(100);
    c.host().eng.menu = 0;
    c.run(300);
    Machine& g = c.addMachine();
    g.eng.add(0x100, I({{1, 10}, {7, 1}}));         // an item that is not in the shared contents ...
    g.eng.stuck.insert(F(7));                       // ... and that the engine will not remove
    c.settle();
    g.eng.openMenuOn(0x100);
    c.run(600);
    CHECK(count(g.eng.conts[0x100].items, 7) == 1); // it is still there
    CHECK(tableOf(c, 0x100)->items.count(F(7)) == 0);   // but it was not put into the shared container as if the player had done it
    g.eng.menu = 0;
    c.run(600);
    CHECK(tableOf(c, 0x100)->items.count(F(7)) == 0);
}

TEST(sync, a_corpse_is_registered_by_its_owner_a_moment_after_death_with_the_real_items) {
    Cluster c(2);
    share(c, 0x2000, I({{20, 1}, {21, 40}}), lc::ContainerKind::Corpse);
    Machine& g = c.machine(1);
    c.run(30);                                      // the service has seen a clock tick
    g.sync->onActorDied(0x2000, g.eng.conts[0x2000].ref);
    c.run(100);
    CHECK(tableOf(c, 0x2000) == nullptr);           // the inventory has not settled yet
    c.run(600);
    REQUIRE(tableOf(c, 0x2000));
    CHECK(tableOf(c, 0x2000)->kind == lc::ContainerKind::Corpse);
    CHECK(tableOf(c, 0x2000)->items == I({{20, 1}, {21, 40}}));
    CHECK(g.sync->stats().corpsesResolved == 1);
}

TEST(sync, a_partial_read_is_never_offered_as_the_shared_contents_and_is_tried_again) {
    Cluster c(2);
    share(c, 0x100, I({{1, 5}}));
    Machine& g = c.machine(1);
    g.eng.conts[0x100].exact = false;               // an unresolved leveled list is still in it
    g.sync->onContainerLoaded(g.eng.conts[0x100].ref);
    c.run(4000);
    CHECK(tableOf(c, 0x100) == nullptr);
    CHECK(g.sync->stats().resolveRetries >= 1);
    CHECK(g.sync->stats().containersResolved == 0);
    g.eng.conts[0x100].exact = true;
    g.sync->onContainerLoaded(g.eng.conts[0x100].ref);
    c.run(600);
    REQUIRE(tableOf(c, 0x100));
    CHECK(tableOf(c, 0x100)->items == I({{1, 5}}));
}

TEST(sync, only_the_cell_owner_resolves_right_away_the_others_wait_for_its_real_items) {
    Cluster c(3);
    share(c, 0x100, I({{1, 5}}));
    c.machine(2).eng.conts[0x100].items = I({{1, 5}, {9, 9}});   // machine 2 rolled something else
    c.machine(1).sync->setCellOwnerHook([](lc::CellId) { return true; });
    c.machine(2).sync->setCellOwnerHook([](lc::CellId) { return false; });
    c.machine(2).sync->onContainerLoaded(c.machine(2).eng.conts[0x100].ref);
    c.machine(1).sync->onContainerLoaded(c.machine(1).eng.conts[0x100].ref);
    c.run(600);
    REQUIRE(tableOf(c, 0x100));
    CHECK(tableOf(c, 0x100)->items == I({{1, 5}}));   // the owner's, although machine 2 asked first
    c.run(4000);
    CHECK(c.machine(2).eng.conts[0x100].items == I({{1, 5}}));   // and machine 2's roll was replaced
}

TEST(sync, a_drop_becomes_a_world_item_everywhere_and_only_the_players_own_drop_counts) {
    Cluster c(3);
    Machine& a = c.machine(1);
    const RefId ref = a.eng.addWorld(F(5), 3);
    a.sync->onDrop(kPlayerRef, ref);
    c.run(400);
    REQUIRE(c.host().sync->host()->itemCount() == 1);
    const NetId net = c.host().sync->host()->items().begin()->first;
    CHECK(lc::isIssuedItemId(net));
    CHECK(a.eng.world[ref].net == net);              // the dropper's own ref is bound to the issued id
    CHECK(a.eng.aliveWorld() == 1);                  // not created a second time
    CHECK(c.host().eng.worldByNet(net) != nullptr);
    CHECK(c.machine(2).eng.worldByNet(net) != nullptr);
    CHECK(c.machine(2).eng.worldByNet(net)->it.count == 3);
    // somebody else's drop (an NPC) is not a player drop (twin 150)
    const RefId npcRef = c.machine(2).eng.addWorld(F(6), 1);
    c.machine(2).sync->onDrop(0x77, npcRef);
    c.run(300);
    CHECK(c.host().sync->host()->itemCount() == 1);
    CHECK(c.machine(2).sync->stats().dropsIgnored == 1);
}

TEST(sync, a_pickup_is_a_claim_the_other_machines_lose_the_ref_and_an_npc_pickup_claims_nothing) {
    Cluster c(3);
    Machine& a = c.machine(1);
    Machine& b = c.machine(2);
    const RefId ref = a.eng.addWorld(F(5), 3);
    a.sync->onDrop(kPlayerRef, ref);
    c.run(400);
    const NetId net = c.host().sync->host()->items().begin()->first;
    RefId bref = b.eng.worldByNet(net)->ref;
    // an NPC picks it up on machine b: no claim (twin 151)
    b.sync->onPickup(0x77, bref);
    c.run(300);
    CHECK(c.host().sync->host()->itemCount() == 1);
    CHECK(b.sync->stats().pickupsIgnored == 1);
    // the player does
    b.sync->onPickup(kPlayerRef, bref);
    b.eng.player[F(5)] += 3;
    b.eng.world[bref].alive = false;
    c.run(400);
    CHECK(c.host().sync->host()->itemCount() == 0);
    CHECK(a.eng.worldByNet(net) == nullptr);          // the dropper's ref went away
    CHECK(c.host().eng.worldByNet(net) == nullptr);
    CHECK(count(b.eng.player, 5) == 3);
}

// ---- placed items: persistent refs of a plugin, the same ref on every copy (the lab's loot room: a Stimpak on the floor) ----
namespace {
const FormRef kPlacedRef("nvcooplab.esp", 0x842);
// a placed item on every machine; returns the ref of machine i in refs[i]
std::vector<RefId> placeEverywhere(Cluster& c, uint32_t base = 5, int32_t n = 1) {
    std::vector<RefId> refs;
    for (auto& mc : c.m) refs.push_back(mc->eng.addPlaced(F(base), n, kPlacedRef));
    return refs;
}
// what the engine does when the player activates a world item: the item goes into the inventory, the ref is gone (the claim is made at the same instant)
void takeAsPlayer(Machine& m, RefId ref) {
    ItemDesc d;
    REQUIRE(m.eng.describeItem(ref, d));
    m.sync->onPickup(kPlayerRef, ref);
    m.eng.player[d.item.base] += d.item.count;
    m.eng.world[ref].alive = false;
}
}  // namespace

TEST(sync, a_placed_item_the_host_picks_up_is_gone_on_every_copy_exactly_once) {
    // The lab failure: "loose item 1 is gone on both copies: host False, client True". The host's engine removed its ref; the guest's copy stayed
    // because it could not find a placed ref from its hashed net id.
    Cluster c(3);
    auto refs = placeEverywhere(c);
    takeAsPlayer(c.host(), refs[0]);
    c.run(600);
    CHECK(c.machine(1).eng.world[refs[1]].alive == false);   // twins 170 173 174 175
    CHECK(c.machine(2).eng.world[refs[2]].alive == false);
    CHECK(count(c.host().eng.player, 5) == 1);
    CHECK(count(c.machine(1).eng.player, 5) == 0);
    CHECK(count(c.machine(2).eng.player, 5) == 0);
    CHECK(c.machine(1).sync->stats().removesDone == 1);      // exactly once
    CHECK(c.machine(2).sync->stats().removesDone == 1);
    CHECK(c.machine(1).sync->stats().removesPlaced == 1);
    CHECK(c.host().sync->stats().removesRun == 0);           // the taker's own engine did it
    CHECK(c.host().sync->host()->stats().claimsWon == 1);
    CHECK(c.host().sync->host()->stats().placedClaims == 1);
    c.settle(4000);                                          // nothing more happens: no repeat removal, no rollback
    CHECK(c.machine(1).sync->stats().removesRun == 1);
    CHECK(c.machine(2).sync->stats().removesRun == 1);
    CHECK(c.host().sync->stats().rollbacksApplied == 0);
}

TEST(sync, a_placed_item_a_guest_picks_up_is_gone_on_the_host_and_the_other_guest) {
    Cluster c(3);
    auto refs = placeEverywhere(c, 5, 2);
    takeAsPlayer(c.machine(2), refs[2]);
    c.run(600);
    CHECK(c.host().eng.world[refs[0]].alive == false);
    CHECK(c.machine(1).eng.world[refs[1]].alive == false);
    CHECK(count(c.machine(2).eng.player, 5) == 2);           // the winner keeps its stack
    CHECK(c.machine(2).sync->stats().removesRun == 0);
    CHECK(c.host().sync->stats().removesDone == 1);
    CHECK(c.machine(1).sync->stats().removesDone == 1);
}

TEST(sync, two_players_picking_up_the_same_placed_item_at_once_leave_it_with_exactly_one) {
    for (int order = 0; order < 2; ++order) {
        Cluster c(2);
        auto refs = placeEverywhere(c);
        if (order == 0) { takeAsPlayer(c.host(), refs[0]); takeAsPlayer(c.machine(1), refs[1]); }
        else { takeAsPlayer(c.machine(1), refs[1]); takeAsPlayer(c.host(), refs[0]); }
        c.settle(3000);
        const int got = count(c.host().eng.player, 5) + count(c.machine(1).eng.player, 5);
        CHECK(got == 1);                                     // one winner, the loser's item was taken back
        CHECK(c.host().eng.aliveWorld() == 0);
        CHECK(c.machine(1).eng.aliveWorld() == 0);
        bool told = false;
        for (auto& m : c.m) for (auto& n : m->eng.notices) if (n.first == lc::Notice::AlreadyTaken) told = true;
        CHECK(told);
        CHECK(c.host().sync->host()->stats().claimsWon == 1);
        CHECK(c.host().sync->host()->stats().claimsLost == 1);
    }
}

TEST(sync, the_same_pickup_reported_twice_by_the_engine_is_one_claim_and_the_item_stays_with_the_player) {
    Cluster c(2);
    auto refs = placeEverywhere(c);
    Machine& g = c.machine(1);
    ItemDesc d;
    REQUIRE(g.eng.describeItem(refs[1], d));
    g.sync->onPickup(kPlayerRef, refs[1]);
    g.sync->onPickup(kPlayerRef, refs[1]);                   // a second event for the same activation
    g.eng.player[d.item.base] += 1;
    g.eng.world[refs[1]].alive = false;
    c.settle(3000);
    CHECK(count(g.eng.player, 5) == 1);
    CHECK(g.sync->client()->stats().claims == 1);
    CHECK(g.sync->client()->stats().claimsDuplicate == 1);
    CHECK(c.host().eng.aliveWorld() == 0);
}

TEST(sync, a_placed_item_that_is_not_loaded_here_is_a_counted_miss_not_a_crash_and_a_repeat_is_harmless) {
    Cluster c(3);
    auto refs = placeEverywhere(c);
    c.machine(2).eng.world.clear();                          // its cell is not loaded on machine 2
    takeAsPlayer(c.host(), refs[0]);
    c.run(600);
    CHECK(c.machine(2).sync->stats().removesGone == 1);
    CHECK(c.machine(2).sync->stats().actionsFailed == 0);
    CHECK(c.machine(1).eng.world[refs[1]].alive == false);
}

TEST(sync, a_client_that_joins_after_a_placed_item_was_picked_up_does_not_see_the_item) {
    // The gap: the pickup was told to the players who were there; the table pushed to a late joiner left the tombstone out.
    Cluster c(2);
    auto refs = placeEverywhere(c);
    takeAsPlayer(c.host(), refs[0]);
    c.run(600);
    Machine& late = c.addMachine();
    const RefId lref = late.eng.addPlaced(F(5), 1, kPlacedRef);   // the joiner's own copy of the persistent ref: it is still on its floor
    c.run(600);
    CHECK(late.eng.world[lref].alive == false);                   // twins 180 181
    CHECK(late.sync->stats().removesDone == 1);
    CHECK(count(late.eng.player, 5) == 0);                        // nobody was given the item again
    c.settle(3000);
    CHECK(late.sync->stats().removesRun == 1);                    // the tombstone is not acted on a second time
}

TEST(sync, a_taken_placed_item_in_a_cell_that_loads_later_is_removed_when_the_cell_loads) {
    Cluster c(2);
    auto refs = placeEverywhere(c);
    takeAsPlayer(c.host(), refs[0]);
    c.run(600);
    Machine& late = c.addMachine();                               // joins while its player is elsewhere: no ref of that cell is here
    c.run(600);
    CHECK(late.sync->stats().removesDeferred == 1);               // twin 176
    const RefId lref = late.eng.addPlaced(F(5), 1, kPlacedRef);   // the cell loads
    c.run(5000);
    CHECK(late.eng.world[lref].alive == false);
    CHECK(late.sync->stats().removesDone == 1);
    CHECK(late.sync->stats().removesRetried >= 1);
    const uint64_t calls = late.sync->stats().removesRetried;
    c.run(5000);
    CHECK(late.sync->stats().removesRetried == calls);            // done: nobody asks again
}

TEST(sync, a_placed_item_whose_ref_never_shows_up_is_given_up_after_a_bounded_number_of_tries) {
    SyncConfig cfg = Cluster::defaultCfg();
    cfg.removeRetryMs = 100;
    cfg.maxRemoveTries = 4;
    Cluster c(2, cfg);
    auto refs = placeEverywhere(c);
    takeAsPlayer(c.host(), refs[0]);
    c.run(600);
    Machine& late = c.addMachine();
    c.run(3000);
    CHECK(late.sync->stats().removesDeferred == 1);
    CHECK(late.sync->stats().removesRetried == 4);                // twin 177
    CHECK(late.sync->stats().removesDropped == 1);
}

// ---- G10 / P4 interface: a picked-up NOTE goes to the host's journal, not into an inventory ----
TEST(sync, a_note_a_guest_picks_up_is_added_to_the_hosts_journal_once) {
    Cluster c(3);
    for (auto& m : c.m) m->eng.notes.insert(F(0x77));         // base 0x77 is a BGSNote everywhere
    auto refs = placeEverywhere(c, 0x77, 1);
    takeAsPlayer(c.machine(1), refs[1]);                       // the guest picks up the Jessup note: its own engine added it to its own journal
    c.run(600);
    REQUIRE(c.host().eng.notesAdded.size() == 1);              // twin 178
    CHECK(c.host().eng.notesAdded[0] == F(0x77));
    CHECK(c.host().sync->stats().notesGranted == 1);
    CHECK(c.machine(1).eng.notesAdded.empty());                // only the host's journal is written by this path; the AddNote effect carries it to the others
    CHECK(c.machine(2).eng.notesAdded.empty());
    CHECK(c.host().eng.aliveWorld() == 0 && c.machine(1).eng.aliveWorld() == 0 && c.machine(2).eng.aliveWorld() == 0);   // the note object is gone everywhere
}

TEST(sync, the_host_picking_up_a_note_records_it_and_a_repeated_claim_grants_nothing_more) {
    Cluster c(2);
    for (auto& m : c.m) m->eng.notes.insert(F(0x77));
    auto refs = placeEverywhere(c, 0x77, 1);
    takeAsPlayer(c.host(), refs[0]);
    c.run(600);
    CHECK(c.host().eng.notesAdded.size() == 1);                // the shared AddNote effect has to be recorded for the other copies
    // the same pickup reaches the host again under a new transaction (a second event path): answered Ok, granted once
    c.host().sync->client()->pickedUp(refs[0], NetId(0x20000000u + kPlacedRef.localId), F(0x77), 1, 0, 7, lc::Vec3(), c.now + 10, kPlacedRef);
    c.settle(3000);
    CHECK(c.host().eng.notesAdded.size() == 1);                // twin 179
    CHECK(c.host().sync->stats().notesGranted == 1);
}

TEST(sync, a_picked_up_item_that_is_not_a_note_is_never_added_as_a_note) {
    Cluster c(2);
    c.host().eng.notes.insert(F(0x77));
    auto refs = placeEverywhere(c, 5, 2);                      // a Stimpak
    takeAsPlayer(c.machine(1), refs[1]);
    c.run(600);
    CHECK(c.host().eng.notesAdded.empty());                    // twin 180
    CHECK(c.host().sync->stats().notesGranted == 0);
}

TEST(sync, a_pickup_that_loses_the_race_is_taken_back_from_the_loser) {
    Cluster c(3);
    Machine& a = c.machine(1);
    Machine& b = c.machine(2);
    const RefId ref = a.eng.addWorld(F(5), 2);
    a.sync->onDrop(kPlayerRef, ref);
    c.run(400);
    const NetId net = c.host().sync->host()->items().begin()->first;
    const RefId aref = a.eng.worldByNet(net)->ref, bref = b.eng.worldByNet(net)->ref;
    // both grab it in the same instant
    a.sync->onPickup(kPlayerRef, aref); a.eng.player[F(5)] += 2; a.eng.world[aref].alive = false;
    b.sync->onPickup(kPlayerRef, bref); b.eng.player[F(5)] += 2; b.eng.world[bref].alive = false;
    c.run(500);
    CHECK(count(a.eng.player, 5) + count(b.eng.player, 5) == 2);   // exactly one kept it
    bool told = false;
    for (auto& m : {&a, &b}) for (auto& n : m->eng.notices) if (n.first == lc::Notice::AlreadyTaken) told = true;
    CHECK(told);
}

TEST(sync, a_remote_drop_whose_cell_is_not_loaded_here_is_created_when_it_is) {
    Cluster c(3);
    c.machine(2).eng.cellLoaded = false;
    Machine& a = c.machine(1);
    const RefId ref = a.eng.addWorld(F(5), 1);
    a.sync->onDrop(kPlayerRef, ref);
    c.run(600);
    CHECK(c.machine(2).eng.aliveWorld() == 0);
    CHECK(c.machine(2).sync->stats().createsDeferred >= 1);
    c.machine(2).eng.cellLoaded = true;
    c.run(800);
    CHECK(c.machine(2).eng.aliveWorld() == 1);
}

TEST(sync, a_scripts_change_to_an_unheld_container_on_the_host_reaches_the_table_but_never_over_a_holder) {
    Cluster c(3);
    share(c, 0x100, I({{1, 10}}));
    Machine& g = c.machine(1);
    g.eng.openMenuOn(0x100);
    c.run(300);
    g.eng.menu = 0;
    c.run(400);
    // a quest script adds an item to the host's copy
    c.host().eng.conts[0x100].items[F(99)] = 1;
    c.run(900);
    CHECK(count(tableOf(c, 0x100)->items, 99) == 1);
    CHECK(count(c.machine(2).eng.conts[0x100].items, 99) == 1);
    CHECK(count(g.eng.conts[0x100].items, 99) == 1);
    CHECK(c.host().sync->stats().sweepsChanged >= 1);
    // while the guest holds it, the sweep leaves the table alone
    g.eng.openMenuOn(0x100);
    c.run(300);
    c.host().eng.conts[0x100].items[F(98)] = 1;
    c.run(900);
    CHECK(count(tableOf(c, 0x100)->items, 98) == 0);
    CHECK(count(c.host().eng.conts[0x100].items, 98) == 1);   // the script's item is not wiped from the host's engine either
    g.eng.menu = 0;                                            // the holder leaves: the script's change is carried into the table now
    c.run(1200);
    CHECK(count(tableOf(c, 0x100)->items, 98) == 1);
}

TEST(sync, an_engine_copy_that_drifted_while_nobody_looked_is_corrected) {
    Cluster c(2);
    share(c, 0x100, I({{1, 10}}));
    Machine& g = c.machine(1);
    g.eng.openMenuOn(0x100);
    c.run(300);
    g.eng.menu = 0;
    c.run(400);
    g.eng.conts[0x100].items[F(1)] = 3;             // something outside the menu changed it (a cell reset, a mod)
    c.run(900);
    CHECK(count(g.eng.conts[0x100].items, 1) == 10);
}

TEST(sync, owned_clutter_is_sampled_and_shown_on_the_other_machines) {
    Cluster c(3);
    Machine& a = c.machine(1);
    const RefId ref = a.eng.addWorld(F(5), 1, 0, 7, V(0, 0, 0));
    a.sync->onDrop(kPlayerRef, ref);
    c.run(300);
    const NetId net = c.host().sync->host()->items().begin()->first;
    for (int i = 1; i <= 20; ++i) {
        a.eng.poses[net] = {V(static_cast<float>(i) * 10, 0, 0), V()};
        c.run(70);
    }
    c.run(800);
    CHECK(c.machine(2).eng.shown.count(net) == 1);
    CHECK(c.machine(2).eng.shown[net].first.x == 200);
    CHECK(!c.machine(2).eng.shown[net].second);      // the last pose is the resting one
    CHECK(c.host().eng.shown[net].first.x == 200);
}

TEST(sync, a_disabled_service_does_nothing_at_all) {
    SyncConfig cfg = Cluster::defaultCfg();
    cfg.enabled = false;
    Cluster c(2, cfg);
    share(c, 0x100, I({{1, 10}}));
    c.machine(1).eng.openMenuOn(0x100);
    c.machine(1).sync->onDrop(kPlayerRef, c.machine(1).eng.addWorld(F(5), 1));
    c.run(500);
    CHECK(!c.machine(1).sync->inSession());
    CHECK(c.sent == 0);
    CHECK(c.machine(1).sync->stats().ticks == 0);
}
