// LootClient rules, driven directly with hand-made host answers. Twins 110, 111, 118, 122..125, 130 (client side), 131, 132, 134, 136.
#include "th.h"

using namespace lt;

namespace {

struct ClientRig {
    std::vector<Body> sent;
    LootClient c;
    uint64_t now = 5000;
    explicit ClientRig(ClientConfig cfg = [] { ClientConfig k; k.self = 1; return k; }())
        : c(cfg, [this](const Body& b) { sent.push_back(b); }) {
        c.hostLinkUp(now);
        sent.clear();
    }
    template <class T> const T* last() const {
        for (size_t i = sent.size(); i-- > 0;) if (auto* p = std::get_if<T>(&sent[i])) return p;
        return nullptr;
    }
    template <class T> int countSent() const {
        int n = 0;
        for (const auto& b : sent) if (std::holds_alternative<T>(b)) ++n;
        return n;
    }
    void feed(const Body& b) { c.onMessage(b, now); }
    std::vector<Action> acts() { return c.takeActions(); }
    void open(NetId id, const Items& items, ContainerKind k = ContainerKind::Container) { c.opened(id, k, 7, V(1, 1, 1), items, now); }
    Result grant(uint32_t txn, NetId id, const Items& items, uint32_t rev = 1) {
        Result r;
        r.txn = txn; r.op = OpKind::Open; r.id = id; r.rev = rev; r.hasRec = true;
        r.rec = rec(id, items);
        r.rec.rev = rev; r.rec.holder = 1; r.rec.lockSeq = 1;
        return r;
    }
};

Result refuse(uint32_t txn, OpKind op, Status s, NetId id, const Items* authoritative = nullptr) {
    Result r;
    r.txn = txn; r.op = op; r.status = s; r.id = id;
    if (authoritative) { r.hasRec = true; r.rec = rec(id, *authoritative); r.rec.rev = 5; }
    return r;
}

}  // namespace

TEST(client, opening_an_unregistered_container_offers_its_contents_and_the_grant_replaces_a_different_roll) {
    ClientRig r;
    r.open(0x100, I({{1, 2}}));
    const ReqOpen* o = r.last<ReqOpen>();
    REQUIRE(o);
    CHECK(o->hasAdopt);
    CHECK(o->adopt.items == I({{1, 2}}));
    CHECK(o->txn == 1);
    CHECK(r.c.pendingCount() == 1);
    r.feed(r.grant(1, 0x100, I({{1, 5}, {9, 1}})));
    auto a = r.acts();
    const Action* set = findAction(a, Action::ContainerSet, 0x100);
    REQUIRE(set);
    CHECK(set->items == I({{1, 5}, {9, 1}}));
    CHECK(r.c.holdsLock(0x100));
    CHECK(r.c.pendingCount() == 0);

    // a known container is opened without the offer
    ClientRig k;
    k.feed(MContainer{rec(0x200, I({{3, 3}}))});
    k.acts();
    k.open(0x200, I({{3, 3}}));
    CHECK(!k.last<ReqOpen>()->hasAdopt);
    // and when the grant carries what the engine already shows, nothing is replaced
    k.feed(k.grant(1, 0x200, I({{3, 3}})));
    CHECK(!hasAction(k.acts(), Action::ContainerSet));
}

TEST(client, busy_closes_the_menu_and_tells_the_player) {
    ClientRig r;
    r.open(0x100, I({{1, 2}}));
    Result b;
    b.txn = 1; b.op = OpKind::Open; b.status = Status::Busy; b.id = 0x100; b.other = 2;
    r.feed(b);
    auto a = r.acts();
    CHECK(hasAction(a, Action::CloseMenu));
    const Action* n = findAction(a, Action::Notify);
    REQUIRE(n);
    CHECK(n->notice == Notice::InUse);
    CHECK(!r.c.holdsLock(0x100));
    CHECK(!r.c.isOpen(0x100));
    CHECK(r.c.stats().busy == 1);
}

TEST(client, what_the_player_takes_and_puts_is_read_from_the_engine_copy) {
    ClientRig r;
    r.open(0x100, I({{1, 5}}));
    r.feed(r.grant(1, 0x100, I({{1, 5}})));
    r.acts();
    r.sent.clear();
    r.c.observed(0x100, I({{1, 5}}), r.now);
    CHECK(r.sent.empty());                                // nothing changed
    r.c.observed(0x100, I({{1, 3}}), r.now);              // took 2
    REQUIRE(r.last<ReqMove>());
    CHECK(!r.last<ReqMove>()->put);
    CHECK(r.last<ReqMove>()->count == 2);
    CHECK(r.last<ReqMove>()->base == F(1));
    r.c.observed(0x100, I({{1, 3}, {4, 6}}), r.now);      // put 6 of something else
    CHECK(r.last<ReqMove>()->put);
    CHECK(r.last<ReqMove>()->count == 6);
    r.c.observed(0x100, I({{1, 3}, {4, 6}}), r.now);      // same look again: no new request
    CHECK(r.countSent<ReqMove>() == 2);
    r.c.observed(0x100, I({{4, 1}}), r.now);              // took all of 1, 5 of 4: two requests
    CHECK(r.countSent<ReqMove>() == 4);
    CHECK(r.c.pendingFor(0x100) == 4);
    // txns are consecutive and unique
    std::set<uint32_t> txns;
    for (auto& b : r.sent) if (auto* m = std::get_if<ReqMove>(&b)) txns.insert(m->txn);
    CHECK(txns.size() == 4);
    // a container that is not open produces no requests
    r.c.observed(0x999, I({{4, 1}}), r.now);
    CHECK(r.countSent<ReqMove>() == 4);
}

TEST(client, a_refused_take_takes_the_items_back_from_the_player_and_heals_the_container) {
    ClientRig r;
    r.open(0x100, I({{1, 5}}));
    r.feed(r.grant(1, 0x100, I({{1, 5}})));
    r.acts();
    r.c.observed(0x100, I({{1, 2}}), r.now);   // took 3
    const uint32_t txn = r.last<ReqMove>()->txn;
    Items auth = I({{1, 1}});
    r.feed(refuse(txn, OpKind::Take, Status::Insufficient, 0x100, &auth));
    auto a = r.acts();
    const Action* adj = findAction(a, Action::PlayerAdjust);
    REQUIRE(adj);
    CHECK(adj->base == F(1));
    CHECK(adj->delta == -3);
    const Action* set = findAction(a, Action::ContainerSet, 0x100);
    REQUIRE(set);
    CHECK(set->items == I({{1, 1}}));
    CHECK(hasAction(a, Action::Notify));
    CHECK(r.c.stats().rollbacks == 1);
    // the same refusal arriving twice (a duplicated datagram) rolls back once
    r.feed(refuse(txn, OpKind::Take, Status::Insufficient, 0x100, &auth));
    CHECK(r.acts().empty());
    CHECK(r.c.stats().duplicateResults == 1);
}

TEST(client, a_refused_put_gives_the_items_back_to_the_player) {
    ClientRig r;
    r.open(0x100, I({{1, 5}}));
    r.feed(r.grant(1, 0x100, I({{1, 5}})));
    r.acts();
    r.c.observed(0x100, I({{1, 5}, {7, 4}}), r.now);   // put 4
    const uint32_t txn = r.last<ReqMove>()->txn;
    Items auth = I({{1, 5}});
    r.feed(refuse(txn, OpKind::Put, Status::NotHolder, 0x100, &auth));
    auto a = r.acts();
    const Action* adj = findAction(a, Action::PlayerAdjust);
    REQUIRE(adj);
    CHECK(adj->base == F(7));
    CHECK(adj->delta == 4);
    const Action* set = findAction(a, Action::ContainerSet, 0x100);
    REQUIRE(set);
    CHECK(set->items == I({{1, 5}}));   // the 4 are gone from the engine copy again
}

TEST(client, a_confirmed_move_costs_nothing_more) {
    ClientRig r;
    r.open(0x100, I({{1, 5}}));
    r.feed(r.grant(1, 0x100, I({{1, 5}})));
    r.acts();
    r.c.observed(0x100, I({{1, 4}}), r.now);
    Result ok;
    ok.txn = r.last<ReqMove>()->txn; ok.op = OpKind::Take; ok.id = 0x100; ok.count = 1;
    r.feed(ok);
    CHECK(r.acts().empty());
    CHECK(r.c.pendingCount() == 0);
}

TEST(client, replacing_contents_keeps_the_players_unconfirmed_moves) {
    // the grant arrives AFTER the player already took something: the host will apply that take next, so the engine copy must show
    // (shared contents - the take), not the shared contents (which would hand the player a duplicate)
    ClientRig r;
    r.open(0x100, I({{1, 5}}));
    r.c.observed(0x100, I({{1, 3}}), r.now);   // took 2 before the answer came
    r.feed(r.grant(1, 0x100, I({{1, 5}, {9, 1}})));
    auto a = r.acts();
    const Action* set = findAction(a, Action::ContainerSet, 0x100);
    REQUIRE(set);
    CHECK(set->items == I({{1, 3}, {9, 1}}));
}

TEST(client, an_unanswered_request_is_resent_with_the_same_txn_and_finally_rolled_back) {
    ClientConfig cfg;
    cfg.self = 1;
    cfg.resendMs = 1000;
    cfg.giveUpMs = 10000;
    ClientRig r(cfg);
    r.open(0x100, I({{1, 5}}));
    r.feed(r.grant(1, 0x100, I({{1, 5}})));
    r.acts();
    r.c.observed(0x100, I({{1, 2}}), r.now);
    const uint32_t txn = r.last<ReqMove>()->txn;
    r.sent.clear();
    r.now += 500; r.c.tick(r.now);
    CHECK(r.sent.empty());
    r.now += 600; r.c.tick(r.now);
    REQUIRE(r.countSent<ReqMove>() == 1);
    CHECK(r.last<ReqMove>()->txn == txn);
    CHECK(r.c.stats().resent == 1);
    r.now += 1100; r.c.tick(r.now);
    CHECK(r.countSent<ReqMove>() == 2);
    // the host never answers: after giveUpMs the move is abandoned like a refusal, and the container is asked for
    r.now += 9000; r.c.tick(r.now);
    auto a = r.acts();
    const Action* adj = findAction(a, Action::PlayerAdjust);
    REQUIRE(adj);
    CHECK(adj->delta == -3);
    CHECK(r.c.stats().gaveUp >= 1);
    CHECK(r.countSent<ReqResync>() >= 1);
    CHECK(r.c.pendingCount() == 0);
}

TEST(client, a_close_waits_until_every_move_is_answered_and_sends_the_digest_of_the_engine_copy) {
    ClientRig r;
    r.open(0x100, I({{1, 5}}));
    r.feed(r.grant(1, 0x100, I({{1, 5}})));
    r.acts();
    r.c.closed(0x100, I({{1, 3}}), r.now);   // took 2 and closed at once
    CHECK(!hasAction(r.acts(), Action::ReadyToClose));   // the take is unanswered
    Result ok;
    ok.txn = r.last<ReqMove>()->txn; ok.op = OpKind::Take; ok.id = 0x100; ok.count = 2;
    r.feed(ok);
    auto a = r.acts();
    REQUIRE(hasAction(a, Action::ReadyToClose));
    r.sent.clear();
    r.c.closeNow(0x100, I({{1, 3}}), r.now);
    REQUIRE(r.last<ReqClose>());
    CHECK(r.last<ReqClose>()->digest == digestOf(I({{1, 3}})));
    Result cok;
    cok.txn = r.last<ReqClose>()->txn; cok.op = OpKind::Close; cok.id = 0x100;
    r.feed(cok);
    CHECK(r.c.pendingCount() == 0);
    CHECK(!r.c.isOpen(0x100));
    // a digest mismatch replaces the engine copy with the shared contents
    ClientRig m;
    m.open(0x100, I({{1, 5}}));
    m.feed(m.grant(1, 0x100, I({{1, 5}})));
    m.acts();
    m.c.closed(0x100, I({{1, 5}}), m.now);
    m.acts();
    m.c.closeNow(0x100, I({{1, 5}}), m.now);
    Result bad = refuse(m.last<ReqClose>()->txn, OpKind::Close, Status::DigestMismatch, 0x100);
    Items auth = I({{1, 4}});
    bad.hasRec = true; bad.rec = rec(0x100, auth); bad.rec.rev = 9;
    m.feed(bad);
    auto b = m.acts();
    const Action* set = findAction(b, Action::ContainerSet, 0x100);
    REQUIRE(set);
    CHECK(set->items == auth);
    CHECK(m.c.stats().closeMismatch == 1);
}

TEST(client, a_held_container_keeps_its_lock_alive_and_a_quiet_table_sends_a_digest) {
    ClientConfig cfg;
    cfg.self = 1;
    cfg.holdEveryMs = 10000;
    cfg.digestEveryMs = 2000;
    ClientRig r(cfg);
    r.open(0x100, I({{1, 5}}));
    r.feed(r.grant(1, 0x100, I({{1, 5}})));
    r.acts();
    r.sent.clear();
    r.now += 9000; r.c.tick(r.now);
    CHECK(r.countSent<ReqHold>() == 0);
    r.now += 1500; r.c.tick(r.now);
    CHECK(r.countSent<ReqHold>() == 1);
    CHECK(r.countSent<ReqDigest>() >= 1);
    CHECK(r.last<ReqDigest>()->digest == r.c.digest());
    // not while something is unanswered
    r.c.observed(0x100, I({{1, 4}}), r.now);
    r.sent.clear();
    r.now += 3000; r.c.tick(r.now);
    CHECK(r.countSent<ReqDigest>() == 0);
}

TEST(client, deltas_apply_in_order_stale_ones_are_ignored_and_a_gap_asks_for_the_record) {
    ClientRig r;
    ContainerRec c = rec(0x100, I({{1, 5}}));
    c.rev = 3;
    r.feed(MContainer{c});
    r.acts();
    r.feed(MDelta{0x100, 4, F(1), 4});
    CHECK(count(r.c.container(0x100)->items, 1) == 4);
    CHECK(hasAction(r.acts(), Action::ContainerSet));      // eager: the engine copy of a container nobody here has open follows
    r.feed(MDelta{0x100, 4, F(1), 1});                      // a duplicate / old one
    CHECK(count(r.c.container(0x100)->items, 1) == 4);
    r.feed(MDelta{0x100, 3, F(1), 9});
    CHECK(count(r.c.container(0x100)->items, 1) == 4);
    CHECK(r.c.stats().deltasStale == 2);
    r.sent.clear();
    r.feed(MDelta{0x100, 6, F(1), 0});                      // revision 5 was lost
    CHECK(count(r.c.container(0x100)->items, 1) == 4);
    REQUIRE(r.countSent<ReqResync>() == 1);
    CHECK(r.last<ReqResync>()->cont == 0x100);
    // asking again right away is throttled
    r.feed(MDelta{0x100, 7, F(1), 0});
    CHECK(r.countSent<ReqResync>() == 1);
    // the record that answers it
    ContainerRec c2 = rec(0x100, I({{1, 2}}));
    c2.rev = 7;
    r.feed(MContainer{c2});
    CHECK(count(r.c.container(0x100)->items, 1) == 2);
    r.feed(MContainer{c});                                  // an older full record changes nothing
    CHECK(count(r.c.container(0x100)->items, 1) == 2);
    // a delta for a container never seen: ask
    r.sent.clear();
    r.feed(MDelta{0x300, 2, F(1), 1});
    CHECK(r.countSent<ReqResync>() == 1);
}

TEST(client, an_open_container_is_not_rewritten_under_the_players_hands) {
    ClientRig r;
    r.open(0x100, I({{1, 5}}));
    r.feed(r.grant(1, 0x100, I({{1, 5}})));
    r.acts();
    ContainerRec c = rec(0x100, I({{1, 9}}));
    c.rev = 10; c.holder = 1; c.lockSeq = 1;
    r.feed(MContainer{c});
    CHECK(!hasAction(r.acts(), Action::ContainerSet));
}

TEST(client, a_table_transfer_prunes_what_the_host_no_longer_has) {
    ClientRig r;
    r.feed(MContainer{rec(0x100, I({{1, 5}}))});
    r.feed(MContainer{rec(0x101, I({{1, 5}}))});
    WorldItem w;
    w.id = kItemIdBase + 1; w.base = F(2); w.count = 1;
    r.feed(MItemNew{w});
    r.acts();
    CHECK(r.c.containerCount() == 2);
    CHECK(r.c.itemCount() == 1);
    r.feed(MTableBegin{1});
    r.feed(MContainer{rec(0x100, I({{1, 5}}))});
    r.feed(MTableEnd{1, 1, 0});
    CHECK(r.c.container(0x100) != nullptr);
    CHECK(r.c.container(0x101) == nullptr);
    CHECK(r.c.item(kItemIdBase + 1) == nullptr);
    auto a = r.acts();
    CHECK(countActions(a, Action::RemoveItem) == 1);        // the world ref of an item that no longer exists is removed
    CHECK(!hasAction(a, Action::ContainerSet));             // a table never rewrites engine containers (they are replaced at open)
    CHECK(r.c.stats().tablesApplied == 1);
    // a record the host forgot
    r.feed(MContainerGone{0x100});
    CHECK(r.c.container(0x100) == nullptr);
}

TEST(client, losing_the_lock_while_the_menu_is_open_closes_it) {
    ClientRig r;
    r.open(0x100, I({{1, 5}}));
    r.feed(r.grant(1, 0x100, I({{1, 5}})));
    r.acts();
    r.feed(MLock{0x100, 2, 2});
    auto a = r.acts();
    CHECK(hasAction(a, Action::CloseMenu));
    CHECK(findAction(a, Action::Notify)->notice == Notice::InUse);
    CHECK(r.c.container(0x100)->holder == 2);
    // an older lock message does not bring it back
    r.feed(MLock{0x100, 1, 1});
    CHECK(r.c.container(0x100)->holder == 2);
}

TEST(client, a_resolved_container_is_offered_and_a_second_roll_adopts_the_shared_contents) {
    ClientRig r;
    r.c.resolved(0x100, ContainerKind::Corpse, 7, V(), I({{1, 5}}), r.now);
    const ReqRegister* q = r.last<ReqRegister>();
    REQUIRE(q);
    CHECK(q->rec.kind == ContainerKind::Corpse);
    CHECK(q->rec.items == I({{1, 5}}));
    Result ok;
    ok.txn = q->txn; ok.op = OpKind::Register; ok.id = 0x100; ok.rev = 1;
    r.feed(ok);
    REQUIRE(r.c.container(0x100));
    CHECK(r.c.container(0x100)->items == I({{1, 5}}));
    CHECK(r.acts().empty());

    ClientRig s;
    s.c.resolved(0x100, ContainerKind::Container, 7, V(), I({{1, 2}}), s.now);
    Result no;
    no.txn = s.last<ReqRegister>()->txn; no.op = OpKind::Register; no.id = 0x100; no.status = Status::AlreadyResolved;
    no.hasRec = true; no.rec = rec(0x100, I({{1, 5}, {2, 1}})); no.rec.rev = 4;
    s.feed(no);
    const auto sacts = s.acts();
    const Action* set = findAction(sacts, Action::ContainerSet, 0x100);
    REQUIRE(set);
    CHECK(set->items == I({{1, 5}, {2, 1}}));
}

TEST(client, a_drop_binds_the_players_own_ref_once_whichever_message_arrives_first) {
    for (int order = 0; order < 2; ++order) {
        ClientRig r;
        r.c.dropped(0xAA, F(5), 3, 0, 7, V(1, 2, 3), V(), r.now);
        const ReqDrop* d = r.last<ReqDrop>();
        REQUIRE(d);
        WorldItem w;
        w.id = kItemIdBase + 7; w.base = F(5); w.count = 3; w.dropper = 1; w.dropTxn = d->txn; w.cell = 7;
        Result res;
        res.txn = d->txn; res.op = OpKind::Drop; res.id = w.id; res.newId = w.id;
        if (order == 0) { r.feed(res); r.feed(MItemNew{w}); }
        else { r.feed(MItemNew{w}); r.feed(res); }
        auto a = r.acts();
        CHECK(countActions(a, Action::BindDrop) == 1);
        CHECK(countActions(a, Action::CreateItem) == 0);       // our own ref exists; a second one would double the item on screen
        const Action* b = findAction(a, Action::BindDrop);
        REQUIRE(b);
        CHECK(b->ref == 0xAA);
        CHECK(b->id == w.id);
        REQUIRE(r.c.item(w.id));
        CHECK(r.c.item(w.id)->count == 3);
    }
    // somebody else's drop is created here
    ClientRig r;
    WorldItem w;
    w.id = kItemIdBase + 8; w.base = F(5); w.count = 2; w.dropper = 2; w.dropTxn = 1;
    r.feed(MItemNew{w});
    auto a = r.acts();
    CHECK(countActions(a, Action::CreateItem) == 1);
    CHECK(findAction(a, Action::CreateItem)->item.base == F(5));
    // a refused drop gives the item back
    ClientRig q;
    q.c.dropped(0xBB, F(5), 3, 0, 7, V(), V(), q.now);
    q.feed(refuse(q.last<ReqDrop>()->txn, OpKind::Drop, Status::BadRequest, 0));
    auto b = q.acts();
    REQUIRE(findAction(b, Action::PlayerAdjust));
    CHECK(findAction(b, Action::PlayerAdjust)->delta == 3);
}

TEST(client, a_pickup_is_a_claim_a_win_changes_nothing_and_a_loss_is_rolled_back) {
    ClientRig r;
    r.c.pickedUp(0x11, 0x4000, F(9), 2, 0, 7, V(), r.now);
    const ReqClaim* q = r.last<ReqClaim>();
    REQUIRE(q);
    CHECK(q->hasAdopt);                                          // a placed item: the host learns it from the claim
    CHECK(q->adopt.base == F(9));
    Result win;
    win.txn = q->txn; win.op = OpKind::Claim; win.id = 0x4000; win.count = 2; win.base = F(9);
    r.feed(win);
    CHECK(r.acts().empty());
    CHECK(r.c.stats().claimsWon == 1);

    // an issued id needs no adopt
    r.c.pickedUp(0x12, kItemIdBase + 3, F(9), 2, 0, 7, V(), r.now);
    CHECK(!r.last<ReqClaim>()->hasAdopt);
    // the stack the host knows is bigger than the one this engine gave the player
    Result win2;
    win2.txn = r.last<ReqClaim>()->txn; win2.op = OpKind::Claim; win2.id = kItemIdBase + 3; win2.count = 5; win2.base = F(9);
    r.feed(win2);
    auto a2 = r.acts();
    REQUIRE(findAction(a2, Action::PlayerAdjust));
    CHECK(findAction(a2, Action::PlayerAdjust)->delta == 3);

    // lost
    r.c.pickedUp(0x13, 0x4001, F(9), 2, 0, 7, V(), r.now);
    Result lost = refuse(r.last<ReqClaim>()->txn, OpKind::Claim, Status::AlreadyTaken, 0x4001);
    lost.other = 2;
    r.feed(lost);
    auto a = r.acts();
    const Action* adj = findAction(a, Action::PlayerAdjust);
    REQUIRE(adj);
    CHECK(adj->base == F(9));
    CHECK(adj->delta == -2);
    const Action* n = findAction(a, Action::Notify);
    REQUIRE(n);
    CHECK(n->notice == Notice::AlreadyTaken);
    CHECK(r.c.stats().claimsLost == 1);
}

TEST(client, an_item_somebody_else_took_is_removed_here_and_a_late_creation_is_ignored) {
    ClientRig r;
    WorldItem w;
    w.id = kItemIdBase + 2; w.base = F(5); w.count = 2; w.dropper = 2; w.dropTxn = 1;
    r.feed(MItemNew{w});
    r.acts();
    r.feed(MItemGone{w.id, 2, 3});
    auto a = r.acts();
    CHECK(countActions(a, Action::RemoveItem) == 1);
    CHECK(r.c.item(w.id) == nullptr);
    r.feed(MItemNew{w});                                         // the creation arrives after the removal (reordered / replayed)
    CHECK(r.c.item(w.id) == nullptr);
    CHECK(r.acts().empty());
    // our own claim: the engine already removed the ref
    WorldItem w2 = w;
    w2.id = kItemIdBase + 3;
    r.feed(MItemNew{w2});
    r.acts();
    r.feed(MItemGone{w2.id, 1, 4});
    CHECK(!hasAction(r.acts(), Action::RemoveItem));
}

TEST(client, a_placed_item_somebody_else_took_is_removed_by_its_world_ref) {
    ClientRig r;
    const FormRef ref = F(0x842, "nvcooplab.esp");
    r.feed(MItemGone{0x4000, 0, 3, ref});                        // the host took a Stimpak off the floor; this machine never touched it
    auto a = r.acts();
    REQUIRE(countActions(a, Action::RemoveItem) == 1);
    const Action* rm = findAction(a, Action::RemoveItem);
    CHECK(rm->id == 0x4000);
    CHECK(rm->placed == ref);                                    // twin 173: the engine can only find a placed item from this
    CHECK(r.c.stats().removedPlaced == 1);
    // a dropped item has no ref and is found by its id
    r.feed(MItemGone{kItemIdBase + 9, 0, 4});
    auto b = r.acts();
    REQUIRE(findAction(b, Action::RemoveItem));
    CHECK(findAction(b, Action::RemoveItem)->placed.plugin.empty());
}

TEST(client, one_pickup_is_one_claim_even_when_the_engine_reports_it_twice) {
    ClientRig r;
    const FormRef ref = F(0x842, "nvcooplab.esp");
    r.c.pickedUp(0x11, 0x4000, F(9), 1, 0, 7, V(), r.now, ref);
    r.c.pickedUp(0x11, 0x4000, F(9), 1, 0, 7, V(), r.now, ref);   // twin 172: a second claim loses at the host and takes the item back from the winner
    CHECK(r.countSent<ReqClaim>() == 1);
    CHECK(r.c.stats().claimsDuplicate == 1);
    CHECK(r.last<ReqClaim>()->ref == ref);
    CHECK(r.last<ReqClaim>()->hasAdopt);
    Result win;
    win.txn = r.last<ReqClaim>()->txn; win.op = OpKind::Claim; win.id = 0x4000; win.count = 1; win.base = F(9);
    r.feed(win);
    CHECK(r.acts().empty());                                      // nothing taken back
    // an issued id (a dropped item) is guarded the same way
    r.sent.clear();
    r.c.pickedUp(0x12, kItemIdBase + 3, F(9), 1, 0, 7, V(), r.now);
    r.c.pickedUp(0x12, kItemIdBase + 3, F(9), 1, 0, 7, V(), r.now);
    CHECK(r.countSent<ReqClaim>() == 1);
}

TEST(client, a_pickup_of_an_own_drop_that_has_no_id_yet_waits_for_the_id) {
    ClientRig r;
    r.c.dropped(0xAA, F(5), 3, 0, 7, V(1, 2, 3), V(), r.now);
    const uint32_t dropTxn = r.last<ReqDrop>()->txn;
    r.sent.clear();
    r.c.pickedUp(0xAA, 0, F(5), 3, 0, 7, V(1, 2, 3), r.now);    // the player grabbed it straight back: no net id yet
    CHECK(r.countSent<ReqClaim>() == 0);
    WorldItem w;
    w.id = kItemIdBase + 11; w.base = F(5); w.count = 3; w.dropper = 1; w.dropTxn = dropTxn; w.cell = 7;
    Result res;
    res.txn = dropTxn; res.op = OpKind::Drop; res.id = w.id; res.newId = w.id;
    r.feed(res);
    r.feed(MItemNew{w});
    REQUIRE(r.last<ReqClaim>());
    CHECK(r.last<ReqClaim>()->item == w.id);
}

TEST(client, with_the_link_down_a_request_fails_at_once_and_rolls_back) {
    ClientRig r;
    r.open(0x100, I({{1, 5}}));
    r.feed(r.grant(1, 0x100, I({{1, 5}})));
    r.acts();
    r.c.hostLinkDown();
    r.sent.clear();
    r.c.observed(0x100, I({{1, 2}}), r.now);
    CHECK(r.sent.empty());                                       // nothing can be sent
    auto a = r.acts();
    REQUIRE(findAction(a, Action::PlayerAdjust));
    CHECK(findAction(a, Action::PlayerAdjust)->delta == -3);
    r.c.hostLinkUp(r.now);
    CHECK(r.countSent<ReqResync>() == 1);
    CHECK(r.c.pendingCount() == 0);
}

TEST(client, a_host_to_client_message_that_is_really_a_request_is_ignored) {
    ClientRig r;
    r.feed(ReqOpen{});
    r.feed(ReqDigest{});
    CHECK(r.c.stats().wrongDirection == 2);
}

TEST(client, clutter_is_sent_only_while_it_moves_with_one_rest_snapshot_and_then_silence) {
    ClientConfig cfg;
    cfg.self = 1;
    ClientRig r(cfg);
    const NetId net = kItemIdBase + 1;
    r.feed(MClutterOwn{net, 1, 1, V(), V()});
    auto a = r.acts();
    REQUIRE(findAction(a, Action::ClutterOwner));
    CHECK(findAction(a, Action::ClutterOwner)->flag);
    CHECK(r.c.owns(net));
    r.sent.clear();
    auto sample = [&](float x) { return std::vector<LootClient::Sample>{{net, V(x, 0, 0), V()}}; };
    r.c.clutterSamples(sample(0), r.now);                         // first look: sent
    CHECK(r.countSent<MClutter>() == 1);
    CHECK(r.last<MClutter>()->moving);
    CHECK(r.last<MClutter>()->seq == 1);
    r.now += 20; r.c.clutterSamples(sample(30), r.now);           // moved but inside the minimum interval
    CHECK(r.countSent<MClutter>() == 1);
    r.now += 60; r.c.clutterSamples(sample(60), r.now);
    CHECK(r.countSent<MClutter>() == 2);
    CHECK(r.last<MClutter>()->seq == 2);
    r.now += 100; r.c.clutterSamples(sample(60.5f), r.now);       // noise below posEps
    CHECK(r.countSent<MClutter>() == 2);
    r.now += 300; r.c.clutterSamples(sample(60.5f), r.now);
    r.now += 200; r.c.clutterSamples(sample(60.5f), r.now);       // quiet for restMs: ONE final snapshot, not moving
    REQUIRE(r.countSent<MClutter>() == 3);
    CHECK(!r.last<MClutter>()->moving);
    CHECK(r.last<MClutter>()->pos == V(60.5f, 0, 0));
    for (int i = 0; i < 100; ++i) { r.now += 100; r.c.clutterSamples(sample(60.5f), r.now); }
    CHECK(r.countSent<MClutter>() == 3);                          // resting costs nothing
    r.now += 100; r.c.clutterSamples(sample(90), r.now);          // pushed again
    CHECK(r.countSent<MClutter>() == 4);
    CHECK(r.last<MClutter>()->moving);
    CHECK(r.last<MClutter>()->seq == 4);
    // an object this machine does not own is never published
    r.sent.clear();
    r.c.clutterSamples({{net + 1, V(5, 5, 5), V()}}, r.now);
    CHECK(r.sent.empty());
    // somebody else takes over at a newer epoch: we stop
    r.feed(MClutterOwn{net, 2, 2, V(), V()});
    auto b = r.acts();
    REQUIRE(findAction(b, Action::ClutterOwner));
    CHECK(!findAction(b, Action::ClutterOwner)->flag);
    r.now += 100; r.c.clutterSamples(sample(200), r.now);
    CHECK(r.sent.empty());
}

TEST(client, received_clutter_poses_are_ordered_and_stale_ones_dropped) {
    ClientRig r;
    const NetId net = kItemIdBase + 1;
    WorldItem w;
    w.id = net; w.base = F(5); w.count = 1; w.dropper = 2;
    r.feed(MItemNew{w});
    r.feed(MClutterOwn{net, 2, 1, V(), V()});
    r.acts();
    r.feed(MClutter{net, 1, 5, V(10, 0, 0), V(), true});
    auto a = r.acts();
    const Action* p = findAction(a, Action::SetPose);
    REQUIRE(p);
    CHECK(p->pos == V(10, 0, 0));
    CHECK(p->flag);
    CHECK(r.c.item(net)->pos == V(10, 0, 0));
    r.feed(MClutter{net, 1, 4, V(1, 0, 0), V(), true});           // older seq
    r.feed(MClutter{net, 1, 5, V(1, 0, 0), V(), true});           // same seq
    CHECK(r.acts().empty());
    r.feed(MClutter{net, 1, 6, V(12, 0, 0), V(), false});
    CHECK(countActions(r.acts(), Action::SetPose) == 1);
    r.feed(MClutterOwn{net, 3, 2, V(), V()});                     // new owner, new epoch: its seq restarts
    r.feed(MClutter{net, 2, 1, V(20, 0, 0), V(), true});
    CHECK(countActions(r.acts(), Action::SetPose) == 1);
    r.feed(MClutter{net, 1, 99, V(0, 0, 0), V(), true});          // the old owner's late datagram
    CHECK(r.acts().empty());
    CHECK(r.c.stats().clutterStale == 3);
}

TEST(client, touching_a_physics_object_asks_once_for_ownership) {
    ClientRig r;
    const NetId net = kItemIdBase + 4;
    r.c.touch(net, V(1, 2, 3), V(), r.now);
    CHECK(r.countSent<ReqTouch>() == 1);
    r.now += 100;
    r.c.touch(net, V(1, 2, 3), V(), r.now);
    CHECK(r.countSent<ReqTouch>() == 1);                          // throttled
    r.now += 400;
    r.c.touch(net, V(1, 2, 3), V(), r.now);
    CHECK(r.countSent<ReqTouch>() == 2);
    r.feed(MClutterOwn{net, 1, 1, V(), V()});
    r.sent.clear();
    r.now += 1000;
    r.c.touch(net, V(), V(), r.now);
    CHECK(r.sent.empty());                                        // already ours
}

TEST(client, a_menu_less_transfer_may_start_only_when_nothing_of_that_container_is_in_flight) {
    ClientRig r;
    CHECK(r.c.transferIdle(0x100));                 // unknown container: nothing in flight
    r.open(0x100, I({{1, 2}}));
    CHECK(!r.c.transferIdle(0x100));                // opened, the answer is pending
    CHECK(r.c.transferIdle(0x101));                 // other containers are not affected
    const ReqOpen* o = r.last<ReqOpen>();
    REQUIRE(o);
    r.feed(r.grant(o->txn, 0x100, I({{1, 2}})));
    CHECK(!r.c.transferIdle(0x100));                // open and held
    r.c.closed(0x100, I({{1, 2}}), r.now);
    CHECK(!r.c.transferIdle(0x100));                // closing: the close request is not answered yet
}

TEST(client, a_tombstone_in_a_join_table_removes_the_placed_item_once_however_often_the_table_comes) {
    ClientRig r;
    const FormRef ref = F(0x842, "nvcooplab.esp");
    r.feed(MTableBegin{1});
    r.feed(MItemGone{0x4000, 2, 0, ref});
    r.feed(MTableEnd{1, 0, 0});
    auto a = r.acts();
    REQUIRE(countActions(a, Action::RemoveItem) == 1);
    CHECK(findAction(a, Action::RemoveItem)->placed == ref);
    // a heal / resync pushes the table again: the engine is not asked a second time
    r.feed(MTableBegin{2});
    r.feed(MItemGone{0x4000, 2, 0, ref});                        // twin 183
    r.feed(MTableEnd{2, 0, 0});
    CHECK(countActions(r.acts(), Action::RemoveItem) == 0);
    // our own old pickup in the table: nothing to remove
    ClientRig self;
    self.feed(MTableBegin{1});
    self.feed(MItemGone{0x4001, 1, 0, ref});
    self.feed(MTableEnd{1, 0, 0});
    CHECK(countActions(self.acts(), Action::RemoveItem) == 0);
}
