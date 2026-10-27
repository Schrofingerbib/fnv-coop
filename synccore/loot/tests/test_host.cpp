// LootHost rules, driven directly (no clients, no network). Twins 101..119, 126..129, 133, 135 are guarded here (and in the model tests).
#include "th.h"

using namespace lt;

namespace {

HostRig rig3() {
    HostRig r;
    r.join(1);
    r.join(2);
    return r;
}

}  // namespace

TEST(host, first_resolver_wins_and_the_loser_adopts_the_shared_contents) {
    HostRig r = rig3();
    auto c = rec(0x100, I({{1, 5}, {2, 1}}));
    r.req(1, regReq(1, c));
    const Result* a = r.resultFor(1);
    REQUIRE(a);
    CHECK(a->status == Status::Ok);
    CHECK(r.countTo<MContainer>(0) == 1);   // everybody else (not the sender) is told about the record
    CHECK(r.countTo<MContainer>(2) == 1);
    CHECK(r.countTo<MContainer>(1) == 0);

    // a second machine rolled something else: it is told to take the shared contents, the table does not change
    auto other = rec(0x100, I({{1, 2}, {9, 9}}));
    r.req(2, regReq(1, other));
    const Result* b = r.resultFor(2);
    REQUIRE(b);
    CHECK(b->status == Status::AlreadyResolved);
    CHECK(b->hasRec);
    CHECK(b->rec.items == c.items);
    REQUIRE(r.host.container(0x100));
    CHECK(r.host.container(0x100)->items == c.items);

    // the same contents again is simply Ok
    r.req(2, regReq(2, c));
    CHECK(r.resultFor(2)->status == Status::Ok);
    CHECK(r.host.stats().registered == 1);
    CHECK(r.host.stats().alreadyResolved == 1);
}

TEST(host, a_malformed_or_foreign_register_is_refused) {
    HostRig r = rig3();
    auto bad = rec(0, I({{1, 1}}));
    r.req(1, regReq(1, bad));
    CHECK(r.resultFor(1)->status == Status::BadRequest);
    auto issued = rec(kItemIdBase + 3, I({{1, 1}}));   // the id range of dropped items
    r.req(1, regReq(2, issued));
    CHECK(r.resultFor(1)->status == Status::BadRequest);
    auto emptyPlugin = rec(5, {});
    emptyPlugin.items[FormRef("", 3)] = 1;
    r.req(1, regReq(3, emptyPlugin));
    CHECK(r.resultFor(1)->status == Status::BadRequest);
    r.req(1, regReq(0, rec(6, {})));   // txn 0 is not a transaction
    CHECK(r.resultFor(1)->status == Status::BadRequest);
    CHECK(r.host.containerCount() == 0);
    // somebody who never joined is ignored
    r.req(5, regReq(1, rec(7, {})));
    CHECK(r.last.empty());
    CHECK(r.host.stats().wrongDirection >= 1);
    // a host-to-client message sent as a request is ignored too
    r.req(1, MLock{1, 1, 1});
    CHECK(r.last.empty());
}

TEST(host, one_lock_per_container_and_busy_names_the_holder) {
    HostRig r = rig3();
    r.req(1, openAdopt(1, rec(0x100, I({{1, 5}}))));
    const Result* g = r.resultFor(1);
    REQUIRE(g);
    CHECK(g->status == Status::Ok);
    CHECK(g->rec.holder == 1);
    CHECK(g->rec.items == I({{1, 5}}));
    CHECK(r.countTo<MLock>(2) == 1);     // the others learn who has it
    CHECK(r.countTo<MLock>(1) == 0);

    r.req(2, openReq(1, 0x100));
    const Result* b = r.resultFor(2);
    REQUIRE(b);
    CHECK(b->status == Status::Busy);
    CHECK(b->other == 1);
    CHECK(r.host.container(0x100)->holder == 1);

    // the holder opening again (a resent request with a new txn, a re-open) is fine
    r.req(1, openReq(2, 0x100));
    CHECK(r.resultFor(1)->status == Status::Ok);

    // close releases; now the other player gets it
    r.req(1, closeReq(3, 0x100, I({{1, 5}})));
    CHECK(r.resultFor(1)->status == Status::Ok);
    CHECK(r.host.container(0x100)->holder == kNoPlayer);
    CHECK(r.countTo<MLock>(2) == 1);
    r.req(2, openReq(2, 0x100));
    CHECK(r.resultFor(2)->status == Status::Ok);
    CHECK(r.host.container(0x100)->holder == 2);
}

TEST(host, an_unknown_container_without_contents_is_unknown) {
    HostRig r = rig3();
    r.req(1, openReq(1, 0x555));
    CHECK(r.resultFor(1)->status == Status::Unknown);
    r.req(1, moveReq(2, 0x555, 1, 1));
    CHECK(r.resultFor(1)->status == Status::Unknown);
}

TEST(host, take_and_put_need_the_lock_and_the_stock) {
    HostRig r = rig3();
    r.req(1, openAdopt(1, rec(0x100, I({{1, 5}, {2, 2}}))));
    // not the holder
    r.req(2, moveReq(1, 0x100, 1, 1));
    CHECK(r.resultFor(2)->status == Status::NotHolder);
    CHECK(r.resultFor(2)->hasRec);
    CHECK(r.host.container(0x100)->items == I({{1, 5}, {2, 2}}));
    // the holder takes
    r.req(1, moveReq(2, 0x100, 1, 3));
    CHECK(r.resultFor(1)->status == Status::Ok);
    CHECK(r.host.container(0x100)->items == I({{1, 2}, {2, 2}}));
    CHECK(r.countTo<MDelta>(2) == 1);
    CHECK(r.countTo<MDelta>(0) == 1);
    CHECK(r.countTo<MDelta>(1) == 0);       // the mover applied it itself
    // more than there is
    r.req(1, moveReq(3, 0x100, 1, 3));
    CHECK(r.resultFor(1)->status == Status::Insufficient);
    CHECK(r.resultFor(1)->hasRec);
    CHECK(r.host.container(0x100)->items == I({{1, 2}, {2, 2}}));
    // something the container never had
    r.req(1, moveReq(4, 0x100, 77, 1));
    CHECK(r.resultFor(1)->status == Status::Insufficient);
    // take everything of a stack: the stack disappears
    r.req(1, moveReq(5, 0x100, 2, 2));
    CHECK(r.resultFor(1)->status == Status::Ok);
    CHECK(r.host.container(0x100)->items.count(F(2)) == 0);
    // put
    r.req(1, moveReq(6, 0x100, 9, 4, true));
    CHECK(r.resultFor(1)->status == Status::Ok);
    CHECK(count(r.host.container(0x100)->items, 9) == 4);
    // nonsense
    r.req(1, moveReq(7, 0x100, 9, 0));
    CHECK(r.resultFor(1)->status == Status::BadRequest);
    r.req(1, moveReq(8, 0x100, 9, -3));
    CHECK(r.resultFor(1)->status == Status::BadRequest);
    r.req(1, moveReq(9, 0x100, 9, kMaxCount, true));
    CHECK(r.resultFor(1)->status == Status::BadRequest);
    CHECK(r.host.container(0x100)->rev == 4);   // register(1) + take + take + put
}

TEST(host, a_replayed_transaction_returns_the_same_answer_and_changes_nothing) {
    HostRig r = rig3();
    r.req(1, openAdopt(1, rec(0x100, I({{1, 5}}))));
    r.req(1, moveReq(2, 0x100, 1, 2));
    const uint32_t rev = r.host.container(0x100)->rev;
    for (int i = 0; i < 3; ++i) {
        r.req(1, moveReq(2, 0x100, 1, 2));
        const Result* a = r.resultFor(1);
        REQUIRE(a);
        CHECK(a->status == Status::Ok);
        CHECK(a->txn == 2);
        CHECK(r.countAll<MDelta>() == 0);   // no second broadcast
    }
    CHECK(count(r.host.container(0x100)->items, 1) == 3);
    CHECK(r.host.container(0x100)->rev == rev);
    CHECK(r.host.stats().replays == 3);
    // a replayed REFUSAL is replayed too, even though the state would allow it now
    r.req(1, moveReq(3, 0x100, 1, 9));
    CHECK(r.resultFor(1)->status == Status::Insufficient);
    r.req(1, moveReq(4, 0x100, 1, 10, true));
    r.req(1, moveReq(3, 0x100, 1, 9));
    CHECK(r.resultFor(1)->status == Status::Insufficient);
}

TEST(host, a_replayed_open_shows_the_container_as_it_is_now_not_as_it_was) {
    HostRig r = rig3();
    r.req(1, openAdopt(1, rec(0x100, I({{1, 5}}))));
    r.req(1, moveReq(2, 0x100, 1, 2));
    r.req(1, openAdopt(1, rec(0x100, I({{1, 5}}))));   // the first answer was lost: the opener asks again with the same txn
    const Result* a = r.resultFor(1);
    REQUIRE(a);
    REQUIRE(a->hasRec);
    CHECK(a->rec.items == I({{1, 3}}));
    CHECK(a->rev == r.host.container(0x100)->rev);
}

TEST(host, a_transaction_older_than_the_window_is_stale_not_reapplied) {
    HostConfig c;
    c.ledgerWindow = 4;
    HostRig r(c);
    r.join(1);
    r.req(1, openAdopt(1, rec(0x100, I({{1, 50}}))));
    for (uint32_t t = 2; t < 12; ++t) r.req(1, moveReq(t, 0x100, 1, 1));
    CHECK(count(r.host.container(0x100)->items, 1) == 40);
    r.req(1, moveReq(3, 0x100, 1, 1));   // long forgotten
    CHECK(r.resultFor(1)->status == Status::Stale);
    CHECK(count(r.host.container(0x100)->items, 1) == 40);
}

TEST(host, a_lock_runs_out_when_the_holder_goes_quiet) {
    HostConfig c;
    c.lockLeaseMs = 5000;
    HostRig r(c);
    r.join(1); r.join(2);
    r.req(1, openAdopt(1, rec(0x100, I({{1, 5}}))));
    r.now += 4000;
    r.req(2, openReq(1, 0x100));
    CHECK(r.resultFor(2)->status == Status::Busy);
    r.req(1, ReqHold{0x100});          // keep-alive refreshes the lease
    r.now += 4000;
    r.req(2, openReq(2, 0x100));
    CHECK(r.resultFor(2)->status == Status::Busy);
    r.now += 6000;
    LootHost::Out o;
    r.host.tick(r.now, o);             // the host's own clock releases it
    CHECK(r.host.container(0x100)->holder == kNoPlayer);
    bool told = false;
    for (auto& x : o) if (x.to == 1 && std::holds_alternative<MLock>(x.body)) told = true;
    CHECK(told);                       // the old holder learns it lost the lock
    r.req(2, openReq(3, 0x100));
    CHECK(r.resultFor(2)->status == Status::Ok);
    // the old holder's late take is refused
    r.req(1, moveReq(2, 0x100, 1, 1));
    CHECK(r.resultFor(1)->status == Status::NotHolder);
    CHECK(count(r.host.container(0x100)->items, 1) == 5);
}

TEST(host, an_open_request_takes_over_an_expired_lock_without_waiting_for_tick) {
    HostConfig c;
    c.lockLeaseMs = 1000;
    HostRig r(c);
    r.join(1); r.join(2);
    r.req(1, openAdopt(1, rec(0x100, I({{1, 5}}))));
    r.now += 2000;
    r.req(2, openReq(1, 0x100));
    CHECK(r.resultFor(2)->status == Status::Ok);
    CHECK(r.host.container(0x100)->holder == 2);
}

TEST(host, a_player_who_leaves_releases_its_locks) {
    HostRig r = rig3();
    r.req(1, openAdopt(1, rec(0x100, I({{1, 5}}))));
    r.req(1, openAdopt(2, rec(0x101, I({{1, 5}}))));
    LootHost::Out o;
    r.host.playerLeft(1, r.now, o);
    CHECK(r.host.container(0x100)->holder == kNoPlayer);
    CHECK(r.host.container(0x101)->holder == kNoPlayer);
    int locks = 0;
    for (auto& x : o) if (x.to == 2 && std::holds_alternative<MLock>(x.body)) ++locks;
    CHECK(locks == 2);
    CHECK(!r.host.isJoined(1));
    CHECK(r.host.stats().locksReleasedOnLeave == 2);
    r.req(2, openReq(1, 0x100));
    CHECK(r.resultFor(2)->status == Status::Ok);
}

TEST(host, close_compares_the_digest_heals_a_mismatch_and_still_releases) {
    HostRig r = rig3();
    r.req(1, openAdopt(1, rec(0x100, I({{1, 5}}))));
    r.req(1, moveReq(2, 0x100, 1, 2));
    // the closer's copy says something else (a lost op, a bug): the shared contents win
    r.req(1, closeReq(3, 0x100, I({{1, 4}})));
    const Result* a = r.resultFor(1);
    REQUIRE(a);
    CHECK(a->status == Status::DigestMismatch);
    CHECK(a->hasRec);
    CHECK(a->rec.items == I({{1, 3}}));
    CHECK(r.host.container(0x100)->holder == kNoPlayer);
    CHECK(r.host.stats().closeMismatch == 1);
    // a matching close is Ok
    r.req(2, openReq(1, 0x100));
    r.req(2, closeReq(2, 0x100, I({{1, 3}})));
    CHECK(r.resultFor(2)->status == Status::Ok);
    // closing something you do not hold: NotHolder with the state to heal from
    r.req(2, closeReq(3, 0x100, I({{1, 3}})));
    CHECK(r.resultFor(2)->status == Status::NotHolder);
    CHECK(r.resultFor(2)->hasRec);
}

TEST(host, a_drop_gets_one_issued_id_even_when_the_request_is_repeated) {
    HostRig r = rig3();
    r.req(1, dropReq(1, 0x33, 5));
    const Result* a = r.resultFor(1);
    REQUIRE(a);
    CHECK(a->status == Status::Ok);
    CHECK(isIssuedItemId(a->newId));
    const NetId id = a->newId;
    CHECK(r.countTo<MItemNew>(1) == 1);     // the dropper hears too: it binds its own ref
    CHECK(r.countTo<MItemNew>(2) == 1);
    CHECK(r.countTo<MItemNew>(0) == 1);
    REQUIRE(r.host.item(id));
    CHECK(r.host.item(id)->dropper == 1);
    CHECK(r.host.item(id)->dropTxn == 1);
    CHECK(r.host.item(id)->count == 5);
    r.req(1, dropReq(1, 0x33, 5));          // resent
    CHECK(r.resultFor(1)->newId == id);
    CHECK(r.host.itemCount() == 1);
    CHECK(r.countAll<MItemNew>() == 0);
    // ids are never reused
    r.req(2, dropReq(1, 0x33, 1));
    CHECK(r.resultFor(2)->newId != id);
    CHECK(r.host.itemCount() == 2);
    // a bad drop
    r.req(1, dropReq(9, 0x33, 0));
    CHECK(r.resultFor(1)->status == Status::BadRequest);
}

TEST(host, the_first_claim_wins_and_a_late_one_is_told_who_got_it) {
    HostRig r = rig3();
    r.req(1, dropReq(1, 0x33, 5));
    const NetId id = r.resultFor(1)->newId;
    r.req(2, claimReq(1, id));
    const Result* a = r.resultFor(2);
    REQUIRE(a);
    CHECK(a->status == Status::Ok);
    CHECK(a->count == 5);
    CHECK(a->base == F(0x33));
    CHECK(r.host.item(id) == nullptr);
    CHECK(r.countTo<MItemGone>(1) == 1);    // the others are told (a loser with a pending claim learns here)
    CHECK(r.countTo<MItemGone>(0) == 1);
    CHECK(r.countTo<MItemGone>(2) == 0);
    // the dropper arrives second
    r.req(1, claimReq(2, id));
    const Result* b = r.resultFor(1);
    REQUIRE(b);
    CHECK(b->status == Status::AlreadyTaken);
    CHECK(b->other == 2);
    // the winner's request again (resent): the same answer, nothing else happens
    r.req(2, claimReq(1, id));
    CHECK(r.resultFor(2)->status == Status::Ok);
    CHECK(r.countAll<MItemGone>() == 0);
    // an id nobody issued
    r.req(1, claimReq(3, kItemIdBase + 9999));
    CHECK(r.resultFor(1)->status == Status::Unknown);
    // an unissued id with a made-up adopt record is not believed either
    ReqClaim fake = claimPlaced(4, kItemIdBase + 9998, 0x33, 3);
    r.req(1, fake);
    CHECK(r.resultFor(1)->status == Status::Unknown);
}

TEST(host, a_placed_item_is_learned_at_its_first_claim_and_stays_taken) {
    HostRig r = rig3();
    r.req(1, claimPlaced(1, 0x4000, 0x77, 2));
    CHECK(r.resultFor(1)->status == Status::Ok);
    CHECK(r.resultFor(1)->count == 2);
    PlayerId by = 99;
    CHECK(r.host.placedTaken(0x4000, &by));
    CHECK(by == 1);
    CHECK(r.countTo<MItemGone>(2) == 1);
    r.req(2, claimPlaced(1, 0x4000, 0x77, 2));
    CHECK(r.resultFor(2)->status == Status::AlreadyTaken);
    CHECK(r.resultFor(2)->other == 1);
    // without the adopt record a placed item the host never heard of is just unknown
    r.req(2, claimReq(2, 0x4001));
    CHECK(r.resultFor(2)->status == Status::Unknown);
    // survives a host save / load
    auto bytes = r.host.save();
    LootHost h2;
    REQUIRE(h2.load(bytes));
    CHECK(h2.placedTaken(0x4000));
}

static const MItemGone* goneTo(const HostRig& r, PlayerId to) {
    for (const auto& o : r.last) if (o.to == to) if (auto* g = std::get_if<MItemGone>(&o.body)) return g;
    return nullptr;
}

TEST(host, a_placed_items_gone_message_names_its_world_ref_so_the_other_copies_can_find_it) {
    // The net id of a placed item is a hash: a machine that never touched the item cannot turn it back into a ref. The message carries the ref.
    HostRig r = rig3();
    r.req(1, claimPlaced(1, 0x4000, 0x77, 1));
    REQUIRE(goneTo(r, 2));
    CHECK(goneTo(r, 2)->ref == F(0x842, "nvcooplab.esp"));   // twin 170
    CHECK(goneTo(r, 2)->item == 0x4000);
    REQUIRE(goneTo(r, 0));
    CHECK(goneTo(r, 0)->ref == F(0x842, "nvcooplab.esp"));
    CHECK(goneTo(r, 1) == nullptr);                          // the claimant's engine removed its own copy
}

TEST(host, a_race_for_a_placed_item_gives_it_to_exactly_one_player_whoever_arrives_first) {
    for (int order = 0; order < 2; ++order) {
        HostRig r = rig3();
        const PlayerId first = order == 0 ? 1 : 2, second = order == 0 ? 2 : 1;
        r.req(first, claimPlaced(1, 0x4000, 0x77, 1));
        CHECK(r.resultFor(first)->status == Status::Ok);
        r.req(second, claimPlaced(1, 0x4000, 0x77, 1));
        CHECK(r.resultFor(second)->status == Status::AlreadyTaken);
        CHECK(r.resultFor(second)->other == first);
        CHECK(r.host.stats().claimsWon == 1);
        CHECK(r.host.stats().claimsLost == 1);
        CHECK(r.countAll<MItemGone>() == 0);                 // told once, at the win; the loser removed its own copy and is not asked again
    }
}

TEST(host, the_winners_own_second_claim_of_the_same_item_is_not_a_loss) {
    // One pickup that reaches the host twice (two event paths, a resend under a new txn) must not take the item back from the player who won it.
    HostRig r = rig3();
    r.req(1, claimPlaced(1, 0x4000, 0x77, 2));
    CHECK(r.resultFor(1)->status == Status::Ok);
    r.req(1, claimPlaced(2, 0x4000, 0x77, 2));
    CHECK(r.resultFor(1)->status == Status::Ok);             // twin 171
    CHECK(r.host.stats().claimsRepeated == 1);
    CHECK(r.host.stats().claimsWon == 1);
    CHECK(r.countAll<MItemGone>() == 0);
    // a dropped item as well
    r.req(1, dropReq(3, 0x33, 5));
    const NetId id = r.resultFor(1)->newId;
    r.req(2, claimReq(1, id));
    CHECK(r.resultFor(2)->status == Status::Ok);
    r.req(2, claimReq(2, id));
    CHECK(r.resultFor(2)->status == Status::Ok);             // twin 171
    r.req(1, claimReq(4, id));
    CHECK(r.resultFor(1)->status == Status::AlreadyTaken);   // somebody else still loses
}

TEST(host, save_and_load_keep_contents_items_tombstones_and_never_reuse_an_issued_id) {
    HostRig r = rig3();
    r.req(1, openAdopt(1, rec(0x100, I({{1, 5}, {2, 7}}), ContainerKind::Corpse)));
    r.req(1, dropReq(2, 0x33, 4));
    const NetId first = r.resultFor(1)->newId;
    r.req(2, dropReq(1, 0x34, 1));
    const NetId second = r.resultFor(2)->newId;
    r.req(2, claimReq(2, second));          // taken: gone from the table, but its id is spent
    auto bytes = r.host.save();
    LootHost h2;
    REQUIRE(h2.load(bytes));
    REQUIRE(h2.container(0x100));
    CHECK(h2.container(0x100)->items == I({{1, 5}, {2, 7}}));
    CHECK(h2.container(0x100)->kind == ContainerKind::Corpse);
    CHECK(h2.container(0x100)->holder == kNoPlayer);   // locks are transient
    REQUIRE(h2.item(first));
    CHECK(h2.item(first)->count == 4);
    CHECK(h2.item(second) == nullptr);
    CHECK(h2.nextIssuedId() > second);
    LootHost::Out o;
    h2.playerJoined(1, o);
    h2.onRequest(1, dropReq(1, 0x35, 1), 10, o);
    const Result* res = nullptr;
    for (auto& x : o) if (auto* rr = std::get_if<Result>(&x.body)) res = rr;
    REQUIRE(res);
    CHECK(res->newId > second);
    // garbage does not load and leaves the host alone
    std::vector<uint8_t> junk = bytes;
    junk.resize(junk.size() / 2);
    LootHost h3;
    CHECK(!h3.load(junk));
    CHECK(h3.containerCount() == 0);
}

TEST(host, a_scripts_change_to_an_unheld_container_is_shared_but_never_over_a_holder) {
    HostRig r = rig3();
    r.req(1, regReq(1, rec(0x100, I({{1, 5}}))));
    LootHost::Out o;
    CHECK(r.host.externalSet(0x100, I({{1, 5}, {8, 100}}), r.now, o));
    CHECK(r.host.container(0x100)->items == I({{1, 5}, {8, 100}}));
    int rec1 = 0;
    for (auto& x : o) if (std::holds_alternative<MContainer>(x.body)) ++rec1;
    CHECK(rec1 == 3);                       // every joined slot, host included
    // identical contents: no news
    o.clear();
    CHECK(r.host.externalSet(0x100, I({{1, 5}, {8, 100}}), r.now, o));
    CHECK(o.empty());
    // held: refused, nothing changes
    r.req(2, openReq(1, 0x100));
    o.clear();
    CHECK(!r.host.externalSet(0x100, I({{1, 1}}), r.now, o));
    CHECK(o.empty());
    CHECK(count(r.host.container(0x100)->items, 8) == 100);
    CHECK(!r.host.externalSet(0x999, I({{1, 1}}), r.now, o));
}

TEST(host, a_cell_reset_forgets_unheld_containers_so_the_next_resolve_starts_over) {
    HostRig r = rig3();
    r.req(1, regReq(1, rec(0x100, I({{1, 5}}), ContainerKind::Container, 7)));
    r.req(1, regReq(2, rec(0x101, I({{1, 5}}), ContainerKind::Container, 8)));
    r.req(2, openReq(1, 0x100));
    LootHost::Out o;
    r.host.resetCell(7, o);
    CHECK(r.host.container(0x100) != nullptr);   // held: stays
    r.req(2, closeReq(2, 0x100, I({{1, 5}})));
    o.clear();
    r.host.resetCell(7, o);
    CHECK(r.host.container(0x100) == nullptr);
    CHECK(r.host.container(0x101) != nullptr);
    int gone = 0;
    for (auto& x : o) if (std::holds_alternative<MContainerGone>(x.body)) ++gone;
    CHECK(gone == 3);
    r.req(2, regReq(3, rec(0x100, I({{4, 1}}))));
    CHECK(r.resultFor(2)->status == Status::Ok);
    CHECK(r.host.container(0x100)->items == I({{4, 1}}));
}

TEST(host, a_join_gets_the_whole_table_and_a_digest_mismatch_pushes_it_again_rate_limited) {
    HostRig r;
    r.join(1);
    r.req(1, regReq(1, rec(0x100, I({{1, 5}}))));
    r.req(1, dropReq(2, 0x33, 1));
    r.join(2);
    int begin = 0, end = 0, cont = 0, item = 0;
    for (auto& x : r.last) {
        if (x.to != 2) continue;
        if (std::holds_alternative<MTableBegin>(x.body)) ++begin;
        if (std::holds_alternative<MTableEnd>(x.body)) ++end;
        if (std::holds_alternative<MContainer>(x.body)) ++cont;
        if (std::holds_alternative<MItemNew>(x.body)) ++item;
    }
    CHECK(begin == 1);
    CHECK(end == 1);
    CHECK(cont == 1);
    CHECK(item == 1);

    r.req(2, ReqDigest{r.host.digest(), 1, 1});
    CHECK(r.last.empty());                              // equal: nothing to do
    r.req(2, ReqDigest{r.host.digest() ^ 1, 1, 1});
    CHECK(r.countTo<MContainer>(2) == 1);               // different: the table again
    r.now += 100;
    r.req(2, ReqDigest{r.host.digest() ^ 1, 1, 1});
    CHECK(r.last.empty());                              // but not more than once per healMinMs
    r.now += 5000;
    r.req(2, ReqDigest{r.host.digest() ^ 1, 1, 1});
    CHECK(r.countTo<MContainer>(2) == 1);
    CHECK(r.host.stats().healsSent == 2);

    // a single container on request, and "forgotten" for one the host does not have
    r.req(2, ReqResync{0x100, false});
    CHECK(r.countTo<MContainer>(2) == 1);
    r.req(2, ReqResync{0x555, false});
    CHECK(r.countTo<MContainerGone>(2) == 1);
}

TEST(host, a_digest_depends_on_contents_and_ground_items_not_on_locks_or_revisions) {
    HostRig a = rig3(), b = rig3();
    a.req(1, regReq(1, rec(0x100, I({{1, 5}}))));
    b.req(1, regReq(1, rec(0x100, I({{1, 5}}))));
    CHECK(a.host.digest() == b.host.digest());
    a.req(1, openReq(2, 0x100));
    a.req(1, moveReq(3, 0x100, 1, 1));
    a.req(1, moveReq(4, 0x100, 1, 1, true));            // net no change, revision moved
    CHECK(a.host.digest() == b.host.digest());
    a.req(1, moveReq(5, 0x100, 1, 1));
    CHECK(a.host.digest() != b.host.digest());
    b.req(1, dropReq(2, 5, 1));
    CHECK(b.host.digest() != a.host.digest());
}

TEST(host, clutter_has_one_owner_per_epoch_and_only_the_owner_is_relayed) {
    HostRig r = rig3();
    r.req(1, dropReq(1, 0x33, 1));
    const NetId id = r.resultFor(1)->newId;
    uint32_t ep = 0;
    CHECK(r.host.clutterOwner(id, &ep) == 1);
    CHECK(ep == 1);
    CHECK(r.countTo<MClutterOwn>(2) == 1);

    MClutter m;
    m.net = id; m.epoch = 1; m.seq = 1; m.pos = V(10, 0, 0); m.moving = true;
    r.req(1, m);
    CHECK(r.countTo<MClutter>(2) == 1);
    CHECK(r.countTo<MClutter>(0) == 1);
    CHECK(r.countTo<MClutter>(1) == 0);                 // never back to the sender
    CHECK(r.host.item(id)->pos == V(10, 0, 0));         // a late joiner places it where it last was
    // not the owner
    m.seq = 2;
    r.req(2, m);
    CHECK(r.last.empty());
    // the owner repeating or going back in sequence
    m.seq = 1;
    r.req(1, m);
    CHECK(r.last.empty());
    // the wrong epoch
    m.seq = 3; m.epoch = 0;
    r.req(1, m);
    CHECK(r.last.empty());
    m.epoch = 5;
    r.req(1, m);
    CHECK(r.last.empty());
    CHECK(r.host.stats().clutterRejected >= 4);
    // an object nobody tracks
    m.net = 0x7777; m.epoch = 1; m.seq = 1;
    r.req(1, m);
    CHECK(r.last.empty());
}

TEST(host, a_second_mover_cannot_take_clutter_from_a_moving_owner_but_can_from_a_resting_one) {
    HostRig r = rig3();
    r.req(1, dropReq(1, 0x33, 1));
    const NetId id = r.resultFor(1)->newId;
    MClutter m;
    m.net = id; m.epoch = 1; m.seq = 1; m.pos = V(3, 0, 0); m.moving = true;
    r.req(1, m);
    r.now += 100;
    r.req(2, ReqTouch{id, V(), V()});
    CHECK(r.host.clutterOwner(id) == 1);                // still moving: denied
    CHECK(r.countTo<MClutterOwn>(2) == 1);              // and the asker is told who owns it
    CHECK(r.host.stats().clutterDenied == 1);
    // the owner says it came to rest
    m.seq = 2; m.moving = false;
    r.req(1, m);
    r.now += 50;
    r.req(2, ReqTouch{id, V(), V()});
    uint32_t ep = 0;
    CHECK(r.host.clutterOwner(id, &ep) == 2);
    CHECK(ep == 2);
    // the old owner's snapshots at the old epoch are dead now
    m.seq = 3; m.epoch = 1; m.moving = true;
    r.req(1, m);
    CHECK(r.last.empty());
    // a silent owner loses it after restMs even if the last word was "moving"
    MClutter m2;
    m2.net = id; m2.epoch = 2; m2.seq = 1; m2.moving = true;
    r.req(2, m2);
    r.now += 100;
    r.req(1, ReqTouch{id, V(), V()});
    CHECK(r.host.clutterOwner(id) == 2);
    r.now += 1000;
    r.req(1, ReqTouch{id, V(), V()});
    CHECK(r.host.clutterOwner(id) == 1);
    // an owner who leaves drops the object (nobody owns it) with a new epoch
    LootHost::Out o;
    r.host.playerLeft(1, r.now, o);
    CHECK(r.host.clutterOwner(id) == kNoPlayer);
    bool told = false;
    for (auto& x : o) if (x.to == 2 && std::holds_alternative<MClutterOwn>(x.body)) told = true;
    CHECK(told);
}

static int goneInTable(const HostRig& r, PlayerId to, NetId item, FormRef* ref = nullptr) {
    int n = 0;
    for (const auto& o : r.last)
        if (o.to == to)
            if (auto* g = std::get_if<MItemGone>(&o.body))
                if (g->item == item) { ++n; if (ref) *ref = g->ref; }
    return n;
}

TEST(host, a_client_that_joins_after_a_pickup_is_told_the_placed_item_is_gone) {
    // The pickup was told to the players who were there. A player who joins afterwards gets the table: the tombstone (with the ref) must be in it.
    HostRig r = rig3();
    r.req(1, claimPlaced(1, 0x4000, 0x77, 2));
    r.join(3);
    FormRef ref;
    CHECK(goneInTable(r, 3, 0x4000, &ref) == 1);                 // twin 181
    CHECK(ref == F(0x842, "nvcooplab.esp"));                     // twin 180: the ref came along
    // it sits between TableBegin and TableEnd of that table
    int begin = -1, gone = -1, end = -1, i = 0;
    for (const auto& o : r.last) {
        if (o.to == 3) {
            if (std::holds_alternative<MTableBegin>(o.body)) begin = i;
            if (std::holds_alternative<MItemGone>(o.body)) gone = i;
            if (std::holds_alternative<MTableEnd>(o.body)) end = i;
        }
        ++i;
    }
    CHECK(begin >= 0 && gone > begin && end > gone);
    // the counts the client checks the table against are not about tombstones
    for (const auto& o : r.last)
        if (o.to == 3)
            if (auto* e = std::get_if<MTableEnd>(&o.body)) CHECK(e->items == 0);
}

TEST(host, the_join_table_tombstones_survive_a_host_save_and_are_bounded) {
    HostConfig cfg;
    cfg.maxTableTombstones = 2;
    HostRig r(cfg);
    r.join(1);
    r.join(2);
    for (uint32_t i = 0; i < 4; ++i) {
        ReqClaim c = claimPlaced(i + 1, 0x4000 + i, 0x77, 1);
        c.ref = F(0x900 + i, "nvcooplab.esp");
        r.req(1, c);
    }
    r.join(3);
    int n = 0;
    for (const auto& o : r.last) if (o.to == 3 && std::holds_alternative<MItemGone>(o.body)) ++n;
    CHECK(n == 2);                                               // twin 182: unbounded
    // a restarted host knows the refs again
    auto bytes = r.host.save();
    HostRig r2(cfg);
    REQUIRE(r2.host.load(bytes));
    r2.join(2);
    r2.join(1);
    FormRef ref;
    CHECK(goneInTable(r2, 1, 0x4000, &ref) == 1);
    CHECK(ref == F(0x900, "nvcooplab.esp"));
    // a tombstone without a ref (an old save) cannot be told to anybody, but still blocks a second pickup
    ReqClaim bare = claimPlaced(9, 0x5000, 0x77, 1);
    bare.ref = FormRef();
    r2.req(2, bare);
    r2.join(3);
    CHECK(goneInTable(r2, 3, 0x5000) == 0);
    CHECK(r2.host.placedTaken(0x5000));
}
