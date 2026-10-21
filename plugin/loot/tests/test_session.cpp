// LootSync session handling: the host's own player, spoofed senders, leaving, link loss, loads, the host sidecar, status. Twins 146, 149, 152, 153,
// 156, 157 are guarded here.
#include "rig.h"

using namespace nvl_test;

namespace {
void share(Cluster& c, NetId net, const Items& items) { for (auto& mc : c.m) mc->eng.add(net, items); }
const lc::ContainerRec* tableOf(Cluster& c, NetId net) { return c.host().sync->host()->container(net); }
}  // namespace

TEST(session, the_host_machines_own_player_goes_through_the_same_table_in_process) {
    Cluster c(2);
    share(c, 0x100, I({{1, 10}}));
    Machine& h = c.host();
    h.eng.player = I({{50, 1}});
    h.eng.openMenuOn(0x100);
    c.run(200);
    CHECK(h.sync->client()->holdsLock(0x100));        // twin 149: the host's own answers must reach its own client
    CHECK(h.eng.take(0x100, 1, 3));
    c.run(200);
    CHECK(count(tableOf(c, 0x100)->items, 1) == 7);
    h.eng.menu = 0;
    c.run(400);
    CHECK(tableOf(c, 0x100)->holder == lc::kNoPlayer);
    CHECK(count(c.machine(1).eng.conts[0x100].items, 1) == 7);
    CHECK(h.sync->isHost());
    CHECK(!c.machine(1).sync->isHost());
}

TEST(session, a_guest_cannot_speak_for_another_slot) {
    Cluster c(3);
    share(c, 0x100, I({{1, 10}}));
    // guest 1 sends an Open that CLAIMS to come from slot 2
    lc::ReqOpen o;
    o.txn = 1; o.cont = 0x100; o.hasAdopt = true;
    o.adopt.id = 0x100; o.adopt.items = I({{1, 10}});
    lc::Envelope e;
    e.src = 2;
    e.body = o;
    std::vector<uint8_t> bytes = lc::encodeEnvelope(e);
    REQUIRE(!bytes.empty());
    c.host().sync->receive(1, bytes.data(), bytes.size());   // the transport says: this came from peer 1
    REQUIRE(tableOf(c, 0x100));
    CHECK(tableOf(c, 0x100)->holder == 1);                   // twin 146: the claimed sender would have made it 2
    CHECK(c.host().sync->stats().spoofed == 1);
    // a guest only listens to the host
    lc::Envelope back;
    back.src = 0;
    back.body = lc::MLock{0x100, 99, 2};
    std::vector<uint8_t> b2 = lc::encodeEnvelope(back);
    c.machine(2).sync->receive(1, b2.data(), b2.size());     // from peer 1, not the host
    CHECK(c.machine(2).sync->stats().spoofed == 1);
    // garbage and wrong-direction traffic are counted, not applied
    const uint8_t junk[5] = {lc::kChannelLoot, 1, 2, 3, 4};
    c.host().sync->receive(1, junk, sizeof junk);
    CHECK(c.host().sync->stats().undecodable == 1);
    lc::Envelope wrong;
    wrong.src = 1;
    wrong.body = lc::MLock{0x100, 5, kNoPlayer};
    std::vector<uint8_t> b3 = lc::encodeEnvelope(wrong);
    c.host().sync->receive(1, b3.data(), b3.size());          // a host-to-client message sent TO the host
    CHECK(c.host().sync->stats().wrongDirection == 1);
    CHECK(tableOf(c, 0x100)->holder == 1);
}

TEST(session, a_guest_that_leaves_frees_its_containers_and_clutter) {
    Cluster c(3);
    share(c, 0x100, I({{1, 10}}));
    Machine& g = c.machine(1);
    g.eng.openMenuOn(0x100);
    c.run(300);
    REQUIRE(tableOf(c, 0x100));
    CHECK(tableOf(c, 0x100)->holder == 1);
    const RefId ref = g.eng.addWorld(F(5), 1);
    g.sync->onDrop(kPlayerRef, ref);
    c.run(300);
    const NetId item = c.host().sync->host()->items().begin()->first;
    CHECK(c.host().sync->host()->clutterOwner(item) == 1);
    c.host().sync->playerLeft(1);                              // twin 157
    c.run(100);
    CHECK(tableOf(c, 0x100)->holder == lc::kNoPlayer);
    CHECK(c.host().sync->host()->clutterOwner(item) == lc::kNoPlayer);
    // somebody else can use it at once
    c.machine(2).eng.openMenuOn(0x100);
    c.run(300);
    CHECK(c.machine(2).sync->client()->holdsLock(0x100));
}

TEST(session, losing_the_host_abandons_what_was_in_flight_and_regaining_it_fetches_the_table) {
    Cluster c(2);
    share(c, 0x100, I({{1, 10}}));
    Machine& g = c.machine(1);
    g.eng.openMenuOn(0x100);
    c.run(300);
    g.eng.take(0x100, 1, 2);
    g.cut = true;
    c.run(100);
    CHECK(g.sync->client()->pendingCount() >= 1);
    g.sync->hostLinkDown();                                   // twin 156
    CHECK(g.sync->client()->pendingCount() == 0);
    CHECK(!g.sync->client()->linkUp());
    g.cut = false;
    const uint64_t tablesBefore = g.sync->client()->stats().tablesApplied;
    g.sync->hostLinkUp();
    c.run(500);
    CHECK(g.sync->client()->linkUp());
    CHECK(g.sync->client()->stats().tablesApplied == tablesBefore + 1);     // the table came back
}

TEST(session, a_load_forgets_the_view_and_the_scheduled_work_and_fetches_the_table_again) {
    Cluster c(3);
    share(c, 0x100, I({{1, 10}}));
    Machine& g = c.machine(1);
    g.eng.openMenuOn(0x100);
    c.run(300);
    g.eng.menu = 0;
    c.run(400);
    REQUIRE(g.sync->client()->container(0x100));
    c.machine(2).eng.cellLoaded = false;
    const RefId ref = g.eng.addWorld(F(5), 1);
    g.sync->onDrop(kPlayerRef, ref);
    c.run(300);
    CHECK(c.machine(2).sync->stats().createsDeferred >= 1);
    c.machine(2).cut = true;                                  // (no table can arrive while the old world is being torn down)
    c.machine(2).sync->loadBegin();                           // twin 152: the view would survive; the deferred creation belongs to the world that is gone
    CHECK(c.machine(2).sync->client()->itemCount() == 0);
    CHECK(c.machine(2).sync->client()->containerCount() == 0);
    c.machine(2).eng.cellLoaded = true;
    c.run(600);
    CHECK(c.machine(2).eng.aliveWorld() == 0);                // not created from the old list (the table is fetched after loadEnd)
    c.machine(2).cut = false;
    c.machine(2).sync->loadEnd();
    c.run(600);
    CHECK(c.machine(2).sync->client()->itemCount() == 1);
    CHECK(c.machine(2).eng.aliveWorld() == 1);
}

TEST(session, the_end_of_a_session_clears_its_scheduled_work) {
    Cluster c(2);
    c.machine(1).eng.cellLoaded = false;
    const RefId ref = c.host().eng.addWorld(F(5), 1);
    c.host().sync->onDrop(kPlayerRef, ref);
    c.run(400);
    CHECK(c.machine(1).sync->stats().createsDeferred >= 1);
    c.machine(1).sync->sessionEnd();
    CHECK(!c.machine(1).sync->inSession());
    c.machine(1).eng.cellLoaded = true;
    // a new session: the old deferred list must not create anything in it
    ll::SessionInfo si;
    si.isHost = false; si.self = 1;
    c.machine(1).sync->sessionBegin(si, c.machine(1).link);
    c.run(600);
    CHECK(c.machine(1).eng.aliveWorld() == 0);
}

TEST(session, the_host_table_survives_a_save_and_a_reload_including_the_id_counter) {
    Cluster c(2);
    share(c, 0x100, I({{1, 10}, {2, 2}}));
    Machine& g = c.machine(1);
    g.eng.openMenuOn(0x100);
    c.run(300);
    g.eng.take(0x100, 1, 4);
    g.eng.menu = 0;
    c.run(500);
    const RefId ref = g.eng.addWorld(F(5), 1);
    g.sync->onDrop(kPlayerRef, ref);
    c.run(300);
    const NetId firstItem = c.host().sync->host()->items().begin()->first;
    const std::vector<uint8_t> bytes = c.host().sync->saveState();
    REQUIRE(!bytes.empty());

    // a new game process: the sidecar is loaded before the session starts
    FakeEngine eng2;
    LootSync fresh(eng2, Cluster::defaultCfg());
    CHECK(fresh.loadState(bytes));
    struct NullLink : ll::ILiveLink {
        bool send(PlayerId, bool, fnvcoop::Bytes) override { return true; }
        bool isHost() const override { return true; }
        PlayerId self() const override { return 0; }
    } link;
    ll::SessionInfo si;
    si.isHost = true; si.self = 0;
    fresh.sessionBegin(si, link);
    REQUIRE(fresh.host());
    REQUIRE(fresh.host()->container(0x100));
    CHECK(fresh.host()->container(0x100)->items == I({{1, 6}, {2, 2}}));
    CHECK(fresh.host()->item(firstItem) != nullptr);
    CHECK(fresh.host()->nextIssuedId() > firstItem);
    // garbage does not load
    LootSync other(eng2, Cluster::defaultCfg());
    other.sessionBegin(si, link);
    CHECK(!other.loadState(std::vector<uint8_t>{1, 2, 3}));
}

TEST(session, the_status_answers_with_the_numbers_a_bridge_client_needs) {
    Cluster c(2);
    share(c, 0x100, I({{1, 10}}));
    c.machine(1).eng.openMenuOn(0x100);
    c.run(300);
    const std::string s = c.machine(1).sync->statusJson();
    CHECK(s.find("\"enabled\":true") != std::string::npos);
    CHECK(s.find("\"session\":true") != std::string::npos);
    CHECK(s.find("\"openContainer\":256") != std::string::npos);
    CHECK(s.find("\"digest\":\"") != std::string::npos);
    const std::string h = c.host().sync->statusJson();
    CHECK(h.find("\"host\":true") != std::string::npos);
    CHECK(c.machine(1).sync->client()->digest() == c.host().sync->host()->digest());
}
