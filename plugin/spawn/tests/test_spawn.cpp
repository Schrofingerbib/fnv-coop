// plugin/spawn tests (game-free). A Rig of 1..4 machines (host + clients), each with a fake engine, over an in-memory network with
// latency, drops, duplicates and cuts. Groups: spawn (scenarios), detect (roll detector), rpc, storm (random traffic with oracles).
// The FNV_MUT twins 41..59 of plugin/spawn and 1..27 of synccore/spawn must each fail this file.
#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include "common/minijson.h"
#include "minitest.h"
#include "rig.h"
#include "fnvcoop/transport_util.h"
#include "spawn/spawn_rpc.h"
#include "spawn/spawn_runtime.h"

using namespace nvs_test;

namespace {

std::unique_ptr<Rig> mk(int n, NetOpts o = NetOpts(), SyncConfig c = SyncConfig()) {
    auto r = std::make_unique<Rig>(n, c, o);
    r->connectAll();
    return r;
}
void everyoneIn(Rig& r, CellId c) { for (size_t i = 0; i < r.m.size(); ++i) r.setLoc(static_cast<int>(i), c); }
const sp::SiteRegistry& reg(Rig& r) { return *r.host().sync->registry(); }

int aliveVisible(Rig& r, int i) {
    int n = 0;
    for (auto& kv : r.at(i).eng.refs) if (kv.second.created && r.at(i).eng.visible(kv.second) && !kv.second.dead) ++n;
    return n;
}
std::vector<uint32_t> basesVisible(Rig& r, int i, bool aliveOnly = true) {
    std::vector<uint32_t> v;
    for (auto& kv : r.at(i).eng.refs)
        if (kv.second.created && r.at(i).eng.visible(kv.second) && (!aliveOnly || !kv.second.dead)) v.push_back(kv.second.base.localId);
    std::sort(v.begin(), v.end());
    return v;
}
std::set<NetId> idsVisible(Rig& r, int i, bool aliveOnly = true) {
    std::set<NetId> s;
    for (auto& kv : r.at(i).eng.refs)
        if (kv.second.created && r.at(i).eng.visible(kv.second) && (!aliveOnly || !kv.second.dead)) s.insert(r.at(i).eng.netOf(kv.second));
    return s;
}
SiteId ambient(CellId c, float x, float y) { return sp::ambientSiteId(c, x, y); }
bool isAnyBody(const Msg& m, size_t index) {
    if (m.effect) return false;
    sp::Envelope e;
    return sp::decodeEnvelope(m.data.data(), m.data.size(), e) && e.body.index() == index;
}
constexpr size_t kBodyRoll = 0, kBodyVerdict = 1, kBodySite = 2;

}  // namespace

// ---- one encounter, every machine rolled it ----------------------------------------------------------------------------------------
TEST(spawn, an_encounter_every_machine_rolled_appears_once) {
    auto r = mk(3);
    everyoneIn(*r, 5);
    r->run(200);
    auto h = r->engineEncounter(0, 5, V(100, 100), {0x77, 0x78});
    r->engineEncounter(1, 5, V(110, 100), {0x80, 0x81, 0x82});
    r->engineEncounter(2, 5, V(95, 105), {0x90});
    r->step(20);
    for (int i = 0; i < 3; ++i) CHECK(r->visibleUnbound(i) == 0);          // the feed hid every unannounced actor at once (twin 41)
    r->run(3000);
    for (int i = 0; i < 3; ++i) {
        CHECK(r->visibleCreated(i) == 2);
        CHECK(r->visibleUnbound(i) == 0);
        CHECK(r->visibleCanonical(i, h[0]) == 1 && r->visibleCanonical(i, h[1]) == 1);   // the host's ref ids are the net ids
        CHECK((basesVisible(*r, i) == std::vector<uint32_t>{0x77, 0x78}));                // the owner's result everywhere
        CHECK(r->at(i).sync->pendingRolls() == 0 && r->at(i).sync->candidates() == 0);
        CHECK(r->at(i).sync->digest() == reg(*r).digest());
    }
    CHECK(reg(*r).stats().accepted == 1);
    CHECK(reg(*r).stats().rolls == 3);
    CHECK(r->host().log->committed.size() == 2);                              // twin 42
    CHECK(r->at(1).eng.deletes == 3 && r->at(2).eng.deletes == 1);            // the other machines' own rolls were turned off
    CHECK(r->at(1).eng.madeFromSpawnRef == 2 && r->at(2).eng.madeFromSpawnRef == 2);
    CHECK(r->at(0).sync->stats().bound == 2);                                 // twin 43
}

TEST(spawn, a_client_owned_roll_reaches_everyone_whichever_message_comes_first) {
    for (uint32_t extra : {0u, 400u}) {                                       // 0: the SpawnRef beats the verdict; 400: the verdict beats it
        NetOpts o;
        o.effectExtraMs = extra;
        auto r = mk(3, o);
        r->setLoc(0, 9); r->setLoc(1, 5); r->setLoc(2, 5);
        r->run(200);
        r->engineEncounter(1, 5, V(100, 100), {0x77, 0x78});
        r->engineEncounter(2, 5, V(105, 100), {0x90, 0x91});
        r->run(3000);
        const SiteId site = ambient(5, 100, 100);
        CHECK(reg(*r).ownerOf(site) == 1);
        CHECK(reg(*r).stats().accepted == 1 && reg(*r).stats().idsIssued == 2);
        REQUIRE(r->host().log->committed.size() == 2);
        for (auto& s : r->host().log->committed) CHECK(sp::isIssuedId(s.id));
        const std::set<NetId> ids = idsVisible(*r, 1);
        CHECK(ids.size() == 2);
        for (int i = 1; i <= 2; ++i) {
            CHECK(aliveVisible(*r, i) == 2);
            CHECK(r->visibleUnbound(i) == 0);
            CHECK((basesVisible(*r, i) == std::vector<uint32_t>{0x77, 0x78}));
            CHECK(idsVisible(*r, i) == ids);
            for (NetId id : ids) CHECK(r->visibleCanonical(i, id) == 1);
        }
        CHECK(r->at(0).eng.queued.size() == 2);                               // the host has not loaded the cell: its copies wait
        CHECK(aliveVisible(*r, 0) == 0);
        r->setLoc(0, 5);                                                      // the host walks in: the queued copies appear
        r->run(300);
        CHECK(aliveVisible(*r, 0) == 2);
        CHECK(idsVisible(*r, 0) == ids);
        if (extra == 0) CHECK(r->at(1).sync->trackerStats().droppedBound == 2);            // twin 14
        else CHECK(r->at(1).sync->trackerStats().adoptedBound == 2 && r->at(1).eng.madeFromSpawnRef == 0);
        CHECK(r->at(1).sync->stats().bound + r->at(1).sync->stats().dropped >= 2);
    }
}

TEST(spawn, a_leveled_placed_ref_shows_the_owners_pick_everywhere) {
    auto r = mk(3);
    everyoneIn(*r, 5);
    r->addPlacedEverywhere(0x5000, 0x500, 0x1234, 5, V(0, 0), {0x11, 0x22, 0x33});
    r->run(100);
    for (int i = 0; i < 3; ++i) r->at(i).sync->onPlacedLoaded(0x5000);
    for (int i = 1; i <= 2; ++i) CHECK(r->at(i).eng.refs[0x5000].disabled);   // hidden while the host decides
    for (int s = 0; s < 150; ++s) {
        r->step(20);
        for (int i = 1; i <= 2; ++i) {
            const FRef& ref = r->at(i).eng.refs[0x5000];
            CHECK(ref.disabled || ref.base == F(0x11));                       // never visible with this machine's own, different roll
        }
    }
    for (int i = 0; i < 3; ++i) {
        const FRef& ref = r->at(i).eng.refs[0x5000];
        CHECK(ref.base == F(0x11) && !ref.disabled);
        CHECK(r->at(i).sync->waitingPlaced() == 0 && r->at(i).sync->pendingRolls() == 0);
    }
    CHECK(r->at(1).eng.rebases == 1 && r->at(2).eng.rebases == 1 && r->at(0).eng.rebases == 0);   // twin 45, 50
    CHECK(reg(*r).stats().accepted == 1);
    CHECK(r->host().log->committed.empty());                                  // placed refs need no SpawnRef
}

TEST(spawn, a_placed_ref_waits_hidden_for_an_owner_that_has_not_rolled_yet) {
    auto r = mk(3);
    everyoneIn(*r, 5);
    r->addPlacedEverywhere(0x5000, 0x500, 0x1234, 5, V(0, 0), {0x11, 0x22, 0x33});
    r->run(100);
    r->at(1).sync->onPlacedLoaded(0x5000);                                    // the clients load the cell first ...
    r->at(2).sync->onPlacedLoaded(0x5000);
    r->run(1500);
    for (int i = 1; i <= 2; ++i) {
        CHECK(r->at(i).eng.refs[0x5000].disabled);                            // ... the host said "not yours" but has no result yet: wait, hidden
        CHECK(r->at(i).sync->waitingPlaced() == 1);
    }
    r->at(0).sync->onPlacedLoaded(0x5000);                                    // the owner rolls
    r->run(500);
    for (int i = 0; i < 3; ++i) {
        const FRef& ref = r->at(i).eng.refs[0x5000];
        CHECK(!ref.disabled && ref.base == F(0x11));                          // twin 50: the record never releases the waiting ref
        CHECK(r->at(i).sync->waitingPlaced() == 0);
    }
}

TEST(spawn, a_placed_ref_nobody_rolls_for_is_shown_after_the_timeout) {
    auto r = mk(2);
    everyoneIn(*r, 5);
    r->addPlacedEverywhere(0x5000, 0x500, 0x1234, 5, V(0, 0), {0x11, 0x22});
    r->at(1).sync->onPlacedLoaded(0x5000);
    r->run(14000);                                                            // the host's engine never loads that ref
    CHECK(!r->at(1).eng.refs[0x5000].disabled && r->at(1).sync->waitingPlaced() == 0);   // twin 26: hidden for good
}

TEST(spawn, a_player_who_leaves_hands_their_sites_to_the_next_player) {
    auto r = mk(3);
    r->setLoc(0, 9); r->setLoc(1, 5); r->setLoc(2, 5);
    r->run(200);
    r->engineEncounter(1, 5, V(100, 100), {0x77});
    r->run(2500);
    const SiteId site = ambient(5, 100, 100);
    CHECK(reg(*r).ownerOf(site) == 1);
    r->disconnect(1);
    r->run(600);
    CHECK(reg(*r).ownerOf(site) == 2);                                        // twin 57
    CHECK(r->at(2).sync->siteInfo(site).owner == 2 && r->at(2).sync->siteInfo(site).mine);
    CHECK(reg(*r).stateOf(site) == sp::SiteState::Spawned);                   // the encounter outlives its first owner
}

TEST(spawn, a_placed_ref_stays_what_it_is_when_the_engine_cannot_rebase) {
    auto r = mk(2);
    everyoneIn(*r, 5);
    r->at(1).eng.rebaseOk = false;
    r->addPlacedEverywhere(0x5000, 0x500, 0x1234, 5, V(0, 0), {0x11, 0x22});
    for (int i = 0; i < 2; ++i) r->at(i).sync->onPlacedLoaded(0x5000);
    r->run(1500);
    CHECK(r->at(1).eng.refs[0x5000].base == F(0x22));                         // unsupported: counted, but never left hidden
    CHECK(!r->at(1).eng.refs[0x5000].disabled);
    CHECK(r->at(1).sync->stats().rebaseFailed >= 1);
}

TEST(spawn, a_cleared_interior_stays_cleared_even_when_a_client_resets_it) {
    auto r = mk(2);
    for (int i = 0; i < 2; ++i) r->at(i).eng.interiors.insert(5);
    everyoneIn(*r, 5);
    r->addPlacedEverywhere(0x5000, 0x500, 0x1234, 5, V(0, 0), {0x11, 0x22});
    r->run(100);
    for (int i = 0; i < 2; ++i) r->at(i).sync->onPlacedLoaded(0x5000);
    r->run(1000);
    const SiteId site = sp::siteIdOf(sp::SiteKind::Placed, F(0x1234), 5);
    CHECK(reg(*r).stateOf(site) == sp::SiteState::Spawned);
    // the party kills it: the death reaches every machine and the host's site layer
    r->killNet(0x500);
    r->run(500);
    for (int i = 0; i < 2; ++i) {
        CHECK(r->at(i).sync->siteInfo(site).state == sp::SiteState::Cleared);
        CHECK(r->at(i).sync->siteInfo(site).interior);
        CHECK(r->at(i).eng.refs[0x5000].dead);
    }
    const uint64_t rolls = reg(*r).stats().rolls;
    // client 1's engine resets the cell on its own: the actor is alive again, with a fresh roll
    r->at(1).eng.refs[0x5000].dead = false;
    r->at(1).eng.refs[0x5000].base = F(0x44);
    r->at(1).sync->onPlacedLoaded(0x5000);                                    // the feed reports it again: dead again at once
    CHECK(r->at(1).eng.refs[0x5000].dead);
    // a reset the feed does NOT report is caught by the periodic sweep
    r->at(1).eng.refs[0x5000].dead = false;
    r->run(3500);
    CHECK(r->at(1).eng.refs[0x5000].dead);                                    // twin 59
    CHECK(reg(*r).stats().rolls == rolls);                                    // answered locally: no round trip, no respawn
    CHECK(reg(*r).stateOf(site) == sp::SiteState::Cleared);
    CHECK(r->at(0).eng.refs[0x5000].dead);
}

TEST(spawn, an_exterior_encounter_respawns_after_its_game_time_and_only_for_the_owner) {
    auto r = mk(3);
    everyoneIn(*r, 5);
    r->run(200);
    auto h = r->engineEncounter(0, 5, V(100, 100), {0x77, 0x78});
    r->run(2500);
    r->killNet(h[0]);
    r->killNet(h[1]);
    r->run(300);
    const SiteId site = ambient(5, 100, 100);
    CHECK(reg(*r).stateOf(site) == sp::SiteState::Cleared);
    for (int i = 0; i < 3; ++i) CHECK(r->at(i).sync->siteInfo(site).state == sp::SiteState::Cleared);
    const uint64_t rolls = reg(*r).stats().rolls;
    // client 1's engine resets its cell and rolls the encounter again: not due, thrown away without asking
    r->engineEncounter(1, 5, V(100, 100), {0x50, 0x51});
    r->run(2500);
    CHECK(r->at(1).sync->stats().rollsSuppressed == 1);
    CHECK(reg(*r).stats().rolls == rolls);
    for (int i = 0; i < 3; ++i) CHECK(aliveVisible(*r, i) == 0);              // only the two bodies remain
    // game time passes: the host marks the site respawnable ...
    r->gameMs = 73ull * 3600 * 1000;
    r->run(600);
    CHECK(reg(*r).stateOf(site) == sp::SiteState::Respawnable);
    for (int i = 0; i < 3; ++i) CHECK(r->at(i).sync->siteInfo(site).state == sp::SiteState::Respawnable);
    // ... and every machine's engine respawns it with its own roll
    auto h2 = r->engineEncounter(0, 5, V(100, 100), {0x61, 0x62});
    r->engineEncounter(1, 5, V(100, 100), {0x71});
    r->engineEncounter(2, 5, V(100, 100), {0x81, 0x82, 0x83});
    r->run(3000);
    for (int i = 0; i < 3; ++i) {
        CHECK(aliveVisible(*r, i) == 2);
        CHECK((basesVisible(*r, i) == std::vector<uint32_t>{0x61, 0x62}));
        CHECK(r->visibleCanonical(i, h2[0]) == 1 && r->visibleCanonical(i, h2[1]) == 1);
        CHECK(r->visibleUnbound(i) == 0);
        CHECK(basesVisible(*r, i, false).size() == 4);                        // the two old bodies are still there
    }
    CHECK(reg(*r).stats().accepted == 2);
    CHECK(r->at(0).sync->siteInfo(site).generation == 2);
}

TEST(spawn, a_roll_made_while_ownership_moves_does_not_become_a_second_encounter) {
    NetOpts o;
    o.latencyMs = 200;
    auto r = mk(2, o);
    r->setLoc(0, 9); r->setLoc(1, 5);
    r->run(500);
    auto e1 = r->engineEncounter(1, 5, V(100, 100), {0x77});
    r->run(3000);
    const SiteId site = ambient(5, 100, 100);
    CHECK(reg(*r).ownerOf(site) == 1);
    CHECK(reg(*r).stats().accepted == 1);
    (void)e1;
    for (NetId id : idsVisible(*r, 1)) r->killNet(id);
    r->run(500);
    r->gameMs = 73ull * 3600 * 1000;
    r->run(600);
    r->setLoc(0, 5);                                                          // the host walks into the cell: the site changes hands
    r->run(700);
    CHECK(reg(*r).ownerOf(site) == 0);
    CHECK(r->at(1).sync->siteInfo(site).owner == 0);
    r->engineEncounter(1, 5, V(100, 100), {0x88});                            // client 1's engine respawns it
    r->engineEncounter(0, 5, V(100, 100), {0x99, 0x9A});                      // so does the host's
    r->run(3000);
    CHECK(reg(*r).stats().accepted == 2);
    for (int i = 0; i < 2; ++i) {
        CHECK(aliveVisible(*r, i) == 2);
        CHECK((basesVisible(*r, i) == std::vector<uint32_t>{0x99, 0x9A}));
        CHECK(r->visibleUnbound(i) == 0);
    }
}

TEST(spawn, a_lost_report_or_a_lost_verdict_is_repeated_and_spawns_once) {
    {   // the first report never arrives
        auto r = mk(2);
        r->setLoc(0, 9); r->setLoc(1, 5);
        r->run(200);
        bool dropped = false;
        r->net.dropIf = [&](const Msg& m) { if (!dropped && isAnyBody(m, kBodyRoll)) { dropped = true; return true; } return false; };
        r->engineEncounter(1, 5, V(100, 100), {0x77, 0x78});
        r->run(6000);
        CHECK(dropped);
        CHECK(r->at(1).sync->trackerStats().resent >= 1);                     // twin 44
        CHECK(reg(*r).stats().accepted == 1);
        CHECK(aliveVisible(*r, 1) == 2 && r->visibleUnbound(1) == 0);
        CHECK(r->host().log->committed.size() == 2);
    }
    {   // the verdict never arrives: the report is repeated, the host answers from its cache and spawns nothing more
        auto r = mk(2);
        r->setLoc(0, 9); r->setLoc(1, 5);
        r->run(200);
        bool dropped = false;
        r->net.dropIf = [&](const Msg& m) { if (!dropped && isAnyBody(m, kBodyVerdict)) { dropped = true; return true; } return false; };
        r->engineEncounter(1, 5, V(100, 100), {0x77, 0x78});
        r->run(6000);
        CHECK(dropped);
        CHECK(reg(*r).stats().accepted == 1 && reg(*r).stats().duplicates >= 1);
        CHECK(r->host().log->committed.size() == 2);
        CHECK(aliveVisible(*r, 1) == 2 && r->visibleUnbound(1) == 0);
    }
}

TEST(spawn, a_roll_nobody_answers_is_given_up_without_leaving_a_duplicate) {
    auto r = mk(2);
    r->setLoc(0, 9); r->setLoc(1, 5);
    r->run(200);
    r->net.dropIf = [](const Msg& m) { return !m.effect && m.from == 1; };    // the host hears nothing from client 1
    r->engineEncounter(1, 5, V(100, 100), {0x77, 0x78});
    r->run(15000);
    CHECK(r->at(1).sync->pendingRolls() == 0);
    CHECK(r->at(1).sync->trackerStats().gaveUp == 1);                         // twin: the roll must not stay held forever
    CHECK(aliveVisible(*r, 1) == 0);                                          // turned off, not duplicated
    CHECK(reg(*r).stats().rolls == 0);
}

TEST(spawn, a_late_joiner_gets_the_table_and_adopts_a_spawned_site_without_asking) {
    auto r = std::make_unique<Rig>(3);
    r->host().sync->playerJoined(1);
    r->at(1).sync->hostConnected();
    r->setLoc(0, 5); r->setLoc(1, 5);
    r->run(200);
    auto h = r->engineEncounter(0, 5, V(100, 100), {0x77, 0x78});
    r->run(2500);
    r->connect(2);
    r->setLoc(2, 5);
    for (auto& s : r->host().log->committed) r->at(2).eng.createFromSpawnRef(s);   // the story baseline brings the spawns
    r->run(800);
    CHECK(r->at(2).sync->digest() == reg(*r).digest());
    CHECK(r->at(2).sync->siteInfo(ambient(5, 100, 100)).state == sp::SiteState::Spawned);
    const uint64_t rolls = reg(*r).stats().rolls;
    r->engineEncounter(2, 5, V(100, 100), {0xAA});                            // its engine rolls the same encounter on its own
    r->run(2500);
    CHECK(r->at(2).sync->stats().rollsAdopted == 1);
    CHECK(reg(*r).stats().rolls == rolls);
    CHECK(aliveVisible(*r, 2) == 2);
    CHECK((basesVisible(*r, 2) == std::vector<uint32_t>{0x77, 0x78}));
    CHECK(r->visibleCanonical(2, h[0]) == 1);
}

TEST(spawn, a_diverged_table_is_healed_by_the_digest) {
    auto r = mk(3);
    everyoneIn(*r, 5);
    r->run(200);
    auto h = r->engineEncounter(0, 5, V(100, 100), {0x77, 0x78});
    r->run(2500);
    bool drop = true;
    r->net.dropIf = [&](const Msg& m) { return drop && !m.effect && m.to == 2 && isAnyBody(m, kBodySite); };
    r->killNet(h[0]);
    r->killNet(h[1]);                                                         // the site is cleared; machine 2 misses the record
    r->run(500);
    drop = false;
    CHECK(r->at(2).sync->digest() != reg(*r).digest());
    r->run(8000);                                                             // digest every 2 s; two mismatches in a row -> the whole table
    CHECK(r->at(2).sync->digest() == reg(*r).digest());                       // twin 47
    CHECK(r->host().sync->stats().healsSent >= 1);
    CHECK(r->at(2).sync->siteInfo(ambient(5, 100, 100)).state == sp::SiteState::Cleared);
}

TEST(spawn, a_join_burst_that_arrived_before_the_channel_was_ready_is_asked_for_again) {
    auto r = std::make_unique<Rig>(3);
    r->host().sync->playerJoined(1);
    r->at(1).sync->hostConnected();
    r->setLoc(0, 5); r->setLoc(1, 5);
    r->run(200);
    r->engineEncounter(0, 5, V(100, 100), {0x77, 0x78});
    r->run(2500);
    int burst = 0;
    r->net.dropIf = [&](const Msg& m) { if (m.from == 0 && m.to == 2 && !m.effect && burst < 2) { ++burst; return true; } return false; };
    r->connect(2);                                                            // the host's table is lost on its way
    r->run(800);
    CHECK(burst == 2);
    CHECK(r->at(2).sync->digest() == reg(*r).digest());                       // twin 48: nothing asks again, only the 4 s heal would fix it
    CHECK(r->host().sync->stats().resyncsAnswered >= 1);
}

TEST(spawn, forged_messages_are_not_believed) {
    auto r = mk(3);
    r->setLoc(0, 9); r->setLoc(1, 5); r->setLoc(2, 5);
    r->run(200);
    r->net.dropIf = [](const Msg& m) { return !m.effect && m.from == 0 && m.to == 1; };   // client 1 never hears the host
    r->engineEncounter(1, 5, V(100, 100), {0x77});
    r->run(700);
    REQUIRE(r->at(1).sync->pendingRolls() == 1);
    sp::VerdictMsg v;
    v.site = ambient(5, 100, 100); v.rollId = 1; v.verdict = sp::Verdict::Accept; v.epoch = 1; v.owner = 1;
    sp::Member m;
    m.netId = 0xFFC0FFFFu; m.created = true; m.handle = 0; m.base = F(0x77);
    v.members.push_back(m);
    sp::Envelope e;
    e.src = 2;
    sp::MVerdict mv;
    mv.v = v;
    e.body = mv;
    Bytes b = sp::encodeEnvelope(e);
    r->at(1).sync->receive(2, b.data(), b.size());                            // a verdict from another client
    CHECK(r->at(1).sync->pendingRolls() == 1);                                // twin 49
    e.src = 0;
    b = sp::encodeEnvelope(e);
    r->at(1).sync->receive(2, b.data(), b.size());                            // a verdict that says it is from the host but came from slot 2
    CHECK(r->at(1).sync->pendingRolls() == 1);
    CHECK(r->at(1).sync->stats().spoofed >= 2);
    // the host: a roll that claims another client's name, and a client sending a site record
    sp::RollReport rep;
    rep.def.id = 77; rep.def.kind = sp::SiteKind::Ambient; rep.def.cell = 5; rep.roller = 1; rep.rollId = 5;
    rep.members.push_back(m);
    sp::Envelope re;
    re.src = 1;
    sp::MRoll mr;
    mr.r = rep;
    re.body = mr;
    b = sp::encodeEnvelope(re);
    const uint64_t rolls = reg(*r).stats().rolls;
    r->host().sync->receive(2, b.data(), b.size());
    CHECK(r->host().sync->stats().spoofed >= 1 && reg(*r).stats().rolls == rolls);
    sp::Envelope se;
    se.src = 2;
    sp::MSite ms;
    ms.s.def = rep.def;
    se.body = ms;
    b = sp::encodeEnvelope(se);
    r->host().sync->receive(2, b.data(), b.size());
    CHECK(r->host().sync->stats().wrongDirection >= 1);
    // garbage
    const uint8_t junk[] = {kChannelSpawn, 1, 2, 3};
    r->host().sync->receive(1, junk, sizeof junk);
    CHECK(r->host().sync->stats().undecodable >= 1);
}

TEST(spawn, losing_the_host_releases_held_rolls_and_the_table_comes_back_on_reconnect) {
    auto r = mk(2);
    r->setLoc(0, 9); r->setLoc(1, 5);
    r->run(200);
    r->net.dropIf = [](const Msg& m) { return !m.effect && m.from == 0 && m.to == 1; };
    auto e = r->engineEncounter(1, 5, V(100, 100), {0x77, 0x78});
    r->run(700);
    REQUIRE(r->at(1).sync->pendingRolls() == 1);
    CHECK(r->at(1).eng.refs[e[0]].disabled);
    r->disconnect(1);
    CHECK(r->at(1).sync->pendingRolls() == 0);
    CHECK(!r->at(1).eng.refs[e[0]].disabled && !r->at(1).eng.refs[e[1]].disabled);   // offline: what the engine made stands
    CHECK(r->at(1).sync->view().siteCount() == 0);
    r->net.dropIf = nullptr;
    r->reconnect(1);
    r->setLoc(1, 5);
    r->run(4000);
    // the actors are rolled again now that the host is back: accepted exactly once
    CHECK(reg(*r).stats().accepted == 1);
    CHECK(aliveVisible(*r, 1) == 2 && r->visibleUnbound(1) == 0);
    CHECK(r->at(1).sync->digest() == reg(*r).digest());
}

TEST(spawn, a_host_script_spawn_is_known_without_a_roll_and_clears_the_site) {
    auto r = mk(2);
    everyoneIn(*r, 5);
    r->run(200);
    sp::SiteDef d;
    d.kind = sp::SiteKind::Script; d.anchor = F(0x7777); d.cell = 5; d.cellForm = F(0x100005);
    d.id = sp::siteIdOf(sp::SiteKind::Script, d.anchor, 5);
    // the feed sees the actor a frame before the capture hub announces it: hidden for that frame, shown once announced
    RefId a = r->host().eng.addCreated(F(0x80), 5, V(1, 2, 3));
    r->step(20);
    CHECK(r->host().eng.refs[a].disabled);
    fnvcoop::SpawnRef s;
    s.id = a; s.base = F(0x80); s.cell = d.cellForm; s.x = 1; s.y = 2; s.z = 3;
    r->host().sync->onHostSpawnEffect(d, s);
    r->step(20);
    CHECK(!r->host().eng.refs[a].disabled);
    CHECK(r->host().tracked.count(a) == 1 && r->host().tracked[a] == a);      // identity id, tracked at once
    CHECK(r->host().sync->stats().rollsLocal == 0);
    CHECK(r->host().log->committed.empty());                                  // the capture hub already committed the effect itself
    CHECK(r->at(0).sync->siteInfo(d.id).state == sp::SiteState::Spawned);
    r->run(300);
    CHECK(r->at(1).sync->siteInfo(d.id).state == sp::SiteState::Spawned);
    r->killNet(a);
    r->run(300);
    CHECK(r->at(1).sync->siteInfo(d.id).state == sp::SiteState::Cleared);
}

TEST(spawn, the_host_state_survives_a_save_and_never_reuses_an_id) {
    auto r = mk(2);
    r->setLoc(0, 9); r->setLoc(1, 5);
    r->run(200);
    r->engineEncounter(1, 5, V(100, 100), {0x77, 0x78});
    r->run(2500);
    const Bytes b = r->host().sync->saveState();
    REQUIRE(!b.empty());
    CHECK(r->at(1).sync->saveState().empty());                                // clients have nothing to save
    auto r2 = std::make_unique<Rig>(1);
    CHECK(r2->host().sync->loadState(b));
    CHECK(reg(*r2).digest() == reg(*r).digest());
    CHECK(r2->host().sync->siteInfo(ambient(5, 100, 100)).state == sp::SiteState::Spawned);
    CHECK(reg(*r2).issuer().peek() >= reg(*r).issuer().peek());
    CHECK(!r2->host().sync->loadState(Bytes()));
    Bytes cut = b;
    cut.resize(cut.size() / 2);
    CHECK(!r2->host().sync->loadState(cut));
}

TEST(spawn, a_new_world_forgets_the_old_bindings) {
    auto r = mk(2);
    everyoneIn(*r, 5);
    r->run(200);
    auto h = r->engineEncounter(0, 5, V(100, 100), {0x77});
    r->run(2500);
    NetId id = 0;
    CHECK(r->host().sync->boundNet(h[0], id) && id == h[0]);
    r->host().sync->purge();
    CHECK(!r->host().sync->boundNet(h[0], id));                               // twin 55
    r->at(1).sync->purge();
    CHECK(r->at(1).sync->view().siteCount() == 0);
}

// ---- roll detector --------------------------------------------------------------------------------------------------------------
TEST(detect, actors_that_appear_close_in_space_and_time_are_one_roll) {
    auto r = mk(1);
    r->setLoc(0, 5);
    r->run(100);
    r->engineEncounter(0, 5, V(100, 100), {0x77});
    r->run(100);
    r->engineEncounter(0, 5, V(130, 100), {0x78});
    r->run(100);
    r->engineEncounter(0, 5, V(160, 100), {0x79});
    r->run(3000);
    CHECK(r->host().sync->stats().detectedGroups == 1);                       // twin 52: released while still arriving
    sp::SiteMsg m;
    REQUIRE(r->host().sync->view().find(ambient(5, 100, 100), m));
    CHECK(m.members.size() == 3);
}

TEST(detect, refs_in_different_cells_are_different_rolls) {
    auto r = mk(1);
    r->setLoc(0, 5);
    r->host().eng.loadCell(6);
    r->run(100);
    r->engineEncounter(0, 5, V(100, 100), {0x77, 0x78});
    r->engineEncounter(0, 6, V(100, 100), {0x80, 0x81});
    r->run(3000);
    CHECK(r->host().sync->stats().detectedGroups == 2);                       // twin 51
    CHECK(reg(*r).siteCount() == 2);
}

TEST(detect, an_unannounced_actor_is_not_tracked_until_the_host_says_so) {
    NetOpts o;
    o.effectExtraMs = 400;                                                    // the verdict comes first, so the roller keeps its own actor
    auto r = mk(2, o);
    r->setLoc(0, 9); r->setLoc(1, 5);
    r->run(100);
    auto e = r->engineEncounter(1, 5, V(100, 100), {0x77});
    r->run(200);
    CHECK(r->at(1).tracked.empty());                                          // twin 53: tracked at once under an id nobody else has
    CHECK(r->at(1).sync->candidates() == 1);
    r->run(3000);
    CHECK(r->at(1).tracked.count(e[0]) == 1);
    CHECK(sp::isIssuedId(r->at(1).tracked[e[0]]));
}

TEST(detect, puppets_and_stand_ins_are_never_rolls) {
    auto r = mk(2);
    r->setLoc(0, 9); r->setLoc(1, 5);
    r->run(100);
    auto e = r->engineEncounter(1, 5, V(100, 100), {0x77});
    r->at(1).ignoreRefs.insert(e[0]);
    r->run(3000);
    CHECK(r->at(1).sync->candidates() == 0);                                  // twin 54
    CHECK(!r->at(1).eng.refs[e[0]].disabled && reg(*r).stats().rolls == 0);
}

TEST(detect, nothing_is_rolled_while_the_host_is_unreachable) {
    auto r = std::make_unique<Rig>(2);   // never connected
    r->setLoc(1, 5);
    r->run(100);
    auto e = r->engineEncounter(1, 5, V(100, 100), {0x77});
    r->run(2000);
    CHECK(r->at(1).sync->candidates() == 0 && !r->at(1).eng.refs[e[0]].disabled);   // offline: the engine's own result stands
}

// ---- rpc ------------------------------------------------------------------------------------------------------------------------
TEST(rpc, coop_spawn_reports_the_table_and_a_site) {
    auto r = mk(2);
    r->setLoc(0, 9); r->setLoc(1, 5);
    r->run(200);
    r->engineEncounter(1, 5, V(100, 100), {0x77, 0x78});
    r->run(2500);
    std::string out;
    REQUIRE(nvcoop::spawn::spawnRpc(r->at(1).sync.get(), "", out));
    nvcoop::json::Value v;
    std::string err;
    REQUIRE(nvcoop::json::parse(out, v, err));
    CHECK(v.get("self").asInt() == 1 && !v.get("host").asBool(true) && v.get("sites").asInt() == 1);
    CHECK(v.get("pending").asInt() == 0 && v.get("tracker").get("accepted").asInt() == 1);
    REQUIRE(nvcoop::spawn::spawnRpc(r->host().sync.get(), "{\"list\":true}", out));
    REQUIRE(nvcoop::json::parse(out, v, err));
    CHECK(v.get("total").asInt() == 1);
    REQUIRE(v.get("sites").items().size() == 1);
    CHECK(v.get("sites").items()[0].get("state").asString("") == "Spawned" && v.get("sites").items()[0].get("owner").asInt() == 1);
    char q[64];
    std::snprintf(q, sizeof q, "{\"site\":%u}", ambient(5, 100, 100));
    REQUIRE(nvcoop::spawn::spawnRpc(r->at(1).sync.get(), q, out));
    REQUIRE(nvcoop::json::parse(out, v, err));
    CHECK(v.get("known").asBool(false) && v.get("members").asInt() == 2 && v.get("mine").asBool(false));
    REQUIRE(nvcoop::spawn::spawnRpc(r->host().sync.get(), "{\"site\":\"0xdeadbeef\"}", out));
    REQUIRE(nvcoop::json::parse(out, v, err));
    CHECK(!v.get("known").asBool(true));
    CHECK(!nvcoop::spawn::spawnRpc(r->host().sync.get(), "{\"site\":\"zzz\"}", out));
    CHECK(!nvcoop::spawn::spawnRpc(r->host().sync.get(), "{not json", out));
    REQUIRE(nvcoop::spawn::spawnRpc(nullptr, "", out));
    CHECK(out.find("unsupported") != std::string::npos);
}

// ---- SpawnRuntime over the real MuxTransport ---------------------------------------------------------------------------------------
TEST(runtime, a_session_over_the_real_mux) {
    fnvcoop::InMemoryNetwork net(3);
    FakeEngine hostEng, cliEng;
    fnvcoop::MuxTransport hostMux(*net.endpoint(0)), cliMux(*net.endpoint(1));
    SpawnRuntime hostRt(hostEng), cliRt(cliEng);
    std::vector<fnvcoop::SpawnRef> committed;
    std::map<RefId, NetId> cliTracked;
    uint64_t now = 1000;
    bool joined = true;
    auto frame = [&](uint64_t dt) {
        now += dt;
        net.advance(now);
        fnvcoop::PeerId from;
        Bytes d;
        while (hostMux.poll(from, d)) {}
        while (cliMux.poll(from, d)) {}
        FrameInfo hf;
        hf.active = true; hf.isHost = true; hf.self = 0; hf.mux = &hostMux; hf.nowMs = now;
        if (joined) { ClientLink l; l.slot = 1; l.peer = 1; l.joined = true; hf.clients.push_back(l); }
        hf.commit = [&](PlayerId, const fnvcoop::SpawnRef& sr) { committed.push_back(sr); cliEng.createFromSpawnRef(sr); };   // the story log, instantly
        hostRt.frame(hf);
        FrameInfo cf;
        cf.active = true; cf.isHost = false; cf.self = 1; cf.hostLinkUp = joined; cf.mux = &cliMux; cf.nowMs = now;
        cliRt.frame(cf);
        if (cliRt.sync())
            for (auto& kv : cliEng.refs) {
                if (!kv.second.created || kv.second.deleted || cliTracked.count(kv.first) || !cliEng.loaded.count(kv.second.cell)) continue;
                NetId id = 0; FormRef w;
                if (cliRt.sync()->resolveCreated(kv.first, id, w)) cliTracked[kv.first] = id;
            }
    };
    frame(10);
    REQUIRE(hostRt.sync() && cliRt.sync());
    frame(50);
    cliEng.loadCell(5);
    auto a = cliEng.addCreated(F(0x77), 5, V(100, 100));
    cliEng.addCreated(F(0x78), 5, V(130, 100));
    (void)a;
    for (int i = 0; i < 300; ++i) frame(10);
    REQUIRE(hostRt.sync()->registry());
    CHECK(hostRt.sync()->registry()->stats().accepted == 1);
    CHECK(committed.size() == 2 && sp::isIssuedId(committed[0].id));
    int visible = 0;
    for (auto& kv : cliEng.refs) if (kv.second.created && cliEng.visible(kv.second) && cliTracked.count(kv.first)) ++visible;
    CHECK(visible == 2);                                                      // tracked, canonical, shown
    CHECK(hostEng.queued.size() == 2);                                        // the host has not loaded cell 5: its copies wait
    CHECK(cliRt.sync()->digest() == hostRt.sync()->digest());
    // a datagram from a peer that is not a joined client is dropped
    net.endpoint(9)->send(0, Bytes{kChannelSpawn, 1, 2, 3});
    frame(30);
    CHECK(hostRt.droppedUnknownPeer() >= 1);
    // the link drops: the client forgets the table
    joined = false;
    for (int i = 0; i < 20; ++i) frame(10);
    REQUIRE(cliRt.sync() != nullptr);
    CHECK(cliRt.sync()->view().siteCount() == 0);
    // the session ends
    FrameInfo off;
    hostRt.frame(off);
    CHECK(hostRt.sync() == nullptr);
    CHECK(hostRt.rebuilds() == 1);
}

// ---- storm ----------------------------------------------------------------------------------------------------------------------
namespace {

struct Storm {
    std::unique_ptr<Rig> r;
    fnvcoop::Rng rng;
    int n = 3;
    std::map<int, CellId> where;                 // slot -> cell (absent = disconnected)
    std::vector<RefId> placed;                   // placed leveled refs (the same ids on every machine)
    std::map<RefId, CellId> placedCell;
    std::set<int> cut;
    std::vector<std::string> problems;
    explicit Storm(uint64_t seed) : rng(seed) {}

    void fail(const std::string& s) { if (problems.size() < 12) problems.push_back(s); }

    void build(int machines) {
        n = machines;
        NetOpts o;
        o.latencyMs = rng.range(15, 120);
        o.jitterMs = rng.range(0, 40);
        o.effectExtraMs = rng.chance(0.5) ? rng.range(0, 400) : 0;
        o.dup = rng.chance(0.5) ? 0.04 : 0.0;
        r = std::make_unique<Rig>(n, SyncConfig(), o);
        r->net.rng.reseed(rng.next());
        r->connectAll();
        for (int i = 0; i < n; ++i) r->at(i).eng.interiors.insert(3);
        for (int k = 0; k < 4; ++k) {
            const RefId ref = 0x5000 + static_cast<RefId>(k);
            const CellId cell = static_cast<CellId>(1 + k % 3);
            std::vector<uint32_t> bases;
            for (int i = 0; i < n; ++i) bases.push_back(0x100 + rng.range(0, 50));
            r->addPlacedEverywhere(ref, 0x500 + static_cast<NetId>(k), 0x2000 + static_cast<uint32_t>(k), cell, V(300.0f * k, 50), bases);
            placed.push_back(ref);
            placedCell[ref] = cell;
        }
        for (int i = 0; i < n; ++i) { where[i] = static_cast<CellId>(rng.range(1, 3)); r->setLoc(i, where[i]); }
        for (int i = 0; i < n; ++i) enterRolls(i);
    }

    // a machine that has just loaded a cell: its engine rolls everything in it (placed leveled refs, maybe an encounter)
    void enterRolls(int i) {
        const CellId c = where[i];
        for (RefId p : placed) if (placedCell[p] == c) r->at(i).sync->onPlacedLoaded(p);
        if (rng.chance(0.5)) {
            const float x = 100.0f + 2000.0f * static_cast<float>(rng.range(0, 2));
            std::vector<uint32_t> bases;
            for (uint32_t k = 0, cnt = rng.range(1, 3); k < cnt; ++k) bases.push_back(0x200 + rng.range(0, 90));
            r->engineEncounter(i, c, V(x, 100), bases);
        }
    }

    void op() {
        const uint32_t k = rng.range(0, 99);
        const int i = static_cast<int>(rng.range(0, static_cast<uint32_t>(n - 1)));
        if (k < 18) {                                                         // a player walks to another cell
            if (cut.count(i)) return;
            const CellId c = static_cast<CellId>(rng.range(1, 3));
            if (c == where[i]) return;
            where[i] = c;
            r->setLoc(i, c);
            enterRolls(i);
        } else if (k < 34) {                                                  // the whole cell resets on every machine in it: everybody rolls again
            const CellId c = static_cast<CellId>(rng.range(1, 3));
            for (int j = 0; j < n; ++j) {
                if (cut.count(j) || where[j] != c) continue;
                for (RefId p : placed) if (placedCell[p] == c) {
                    FRef& f = r->at(j).eng.refs[p];
                    f.dead = false; f.base = F(0x100 + rng.range(0, 50));
                }
                enterRolls(j);
            }
        } else if (k < 52) {                                                  // the party kills something
            std::vector<NetId> alive;
            for (auto& kv : r->host().eng.refs) {
                const FRef& f = kv.second;
                if (f.deleted || f.dead || !r->host().eng.loaded.count(f.cell)) continue;
                const NetId id = r->host().eng.netOf(f);
                if (id) alive.push_back(id);
            }
            if (!alive.empty()) r->killNet(alive[rng.range(0, static_cast<uint32_t>(alive.size() - 1))]);
        } else if (k < 62) {                                                  // game time passes
            r->gameMs += static_cast<uint64_t>(rng.range(0, 90)) * 3600ull * 1000ull;
        } else if (k < 68) {                                                  // the host's engine host-places a scripted actor
            if (!where.count(0)) return;
            sp::SiteDef d;
            d.kind = sp::SiteKind::Script; d.anchor = F(0x7000 + rng.range(0, 2)); d.cell = where[0]; d.cellForm = F(0x100000 + where[0]);
            d.id = sp::siteIdOf(sp::SiteKind::Script, d.anchor, d.cell);
            const RefId a = r->host().eng.addCreated(F(0x300 + rng.range(0, 9)), where[0], V(50, 50));
            fnvcoop::SpawnRef s;
            s.id = a; s.base = r->host().eng.refs[a].base; s.cell = d.cellForm; s.x = 50; s.y = 50;
            r->host().sync->onHostSpawnEffect(d, s);
            // the capture hub also commits the effect itself (the baseline / clients create their copies from it)
            r->host().log->committed.push_back(s);
            for (int j = 1; j < n; ++j) if (!cut.count(j)) { Msg e; e.from = 0; e.to = j; e.effect = true; e.sp = s; r->net.push(e); }
        } else if (k < 72) {                                                  // a client's link drops, and comes back later
            if (i == 0 || cut.count(i)) return;
            r->disconnect(i);
            cut.insert(i);
        } else if (k < 78) {
            if (cut.empty()) return;
            const int j = *cut.begin();
            cut.erase(j);
            r->reconnect(j);
            for (auto& s : r->host().log->committed) r->at(j).eng.createFromSpawnRef(s);   // the story baseline brings the spawns back
            r->setLoc(j, where[j]);
            enterRolls(j);
        }
        r->run(rng.range(20, 700));
    }

    // after everything calmed down: the same picture on every machine
    void check() {
        for (int j : std::set<int>(cut)) { cut.erase(j); r->reconnect(j); for (auto& s : r->host().log->committed) r->at(j).eng.createFromSpawnRef(s); r->setLoc(j, where[j]); }
        r->net.dropIf = nullptr;
        r->run(14000);
        const sp::SiteRegistry& rg = reg(*r);
        for (int i = 0; i < n; ++i) {
            Machine& mc = r->at(i);
            if (mc.sync->digest() != rg.digest()) fail("digest differs on " + std::to_string(i));
            if (mc.sync->pendingRolls() != 0) fail("pending rolls on " + std::to_string(i));
            if (mc.sync->waitingPlaced() != 0) fail("placed refs still waiting on " + std::to_string(i));
            if (mc.sync->candidates() != 0) fail("candidates left on " + std::to_string(i));
            for (auto& kv : mc.eng.refs)
                if (kv.second.created && mc.eng.visible(kv.second) && !mc.tracked.count(kv.first)) fail("visible untracked created actor " + std::to_string(kv.first) + " on " + std::to_string(i));
            std::map<NetId, int> seen;
            for (auto& kv : mc.eng.refs) {
                const FRef& f = kv.second;
                if (!f.created || !mc.eng.visible(f)) continue;
                auto t = mc.tracked.find(kv.first);
                if (t != mc.tracked.end() && ++seen[t->second] > 1) fail("net id " + std::to_string(t->second) + " twice on " + std::to_string(i));
            }
        }
        // every actor of every site exists once, in the right state, on every machine that has its cell loaded
        for (SiteId sid : r->at(0).sync->view().ids()) {
            sp::SiteMsg m;
            if (!rg.siteMsg(sid, m)) continue;
            for (const auto& mem : m.members) {
                for (int i = 0; i < n; ++i) {
                    Machine& mc = r->at(i);
                    if (mem.created) {
                        const CellId cell = m.def.cell;
                        if (!mc.eng.loaded.count(cell)) continue;
                        int count = 0, deadCount = 0;
                        for (auto& kv : mc.eng.refs) {
                            const FRef& f = kv.second;
                            auto t = mc.tracked.find(kv.first);
                            if (!f.created || f.deleted || t == mc.tracked.end() || t->second != mem.netId) continue;
                            ++count; if (f.dead) ++deadCount;
                        }
                        if (count != 1) fail("member " + std::to_string(mem.netId) + " exists " + std::to_string(count) + "x on " + std::to_string(i));
                        if (count == 1 && (deadCount == 1) != mem.dead) fail("member " + std::to_string(mem.netId) + " has the wrong life state on " + std::to_string(i));
                    } else {
                        const RefId ref = mc.eng.refOfNet(mem.netId);
                        if (!ref) continue;
                        const FRef& f = mc.eng.refs[ref];
                        if (f.disabled) fail("placed member hidden on " + std::to_string(i));
                        // a respawnable site has no say over its placed refs until the owner's next roll counts
                        if (mem.dead && !f.dead && m.state != sp::SiteState::Respawnable) fail("placed member should be dead on " + std::to_string(i));
                        if (!mem.dead && m.state == sp::SiteState::Spawned && f.base != mem.base) fail("placed member has the wrong base on " + std::to_string(i));
                    }
                }
            }
        }
    }
};

}  // namespace

TEST(storm, random_traffic_converges_with_one_copy_of_everything) {
    int checked = 0;
    uint64_t accepted = 0, refused = 0, suppressed = 0, adopted = 0, heals = 0, resent = 0, scripted = 0, rebased = 0, respawns = 0;
    const char* more = std::getenv("SPAWN_STORM_SEEDS");   // soak: SPAWN_STORM_SEEDS=300 runs 300 seeds
    const uint64_t seeds = more && *more ? static_cast<uint64_t>(std::atoi(more)) : 14;
    for (uint64_t seed = 1; seed <= seeds; ++seed) {
        Storm s(seed);
        s.build(2 + static_cast<int>(seed % 3));
        for (int step = 0; step < 90; ++step) s.op();
        s.check();
        ++checked;
        const sp::RegistryStats& rs = reg(*s.r).stats();
        accepted += rs.accepted;
        refused += rs.notOwner + rs.alreadySpawned + rs.notDue + rs.clearedPermanent + rs.staleEpoch;
        respawns += rs.sitesRespawnable;
        heals += s.r->host().sync->stats().healsSent;
        for (int i = 0; i < s.n; ++i) {
            suppressed += s.r->at(i).sync->stats().rollsSuppressed;
            adopted += s.r->at(i).sync->stats().rollsAdopted;
            resent += s.r->at(i).sync->trackerStats().resent;
            rebased += s.r->at(i).eng.rebases;
        }
        scripted += s.r->host().sync->stats().spawnCommits;
        for (const auto& p : s.problems) std::printf("    seed %llu: %s\n", static_cast<unsigned long long>(seed), p.c_str());
        CHECK(s.problems.empty());
    }
    CHECK(checked == static_cast<int>(seeds));
    if (seeds == 14) {   // the storm really exercised the paths it is supposed to judge
        CHECK(accepted > 30 && refused > 30);
        CHECK(suppressed + adopted > 5);
        CHECK(respawns > 3);
        CHECK(rebased > 5);
        CHECK(scripted > 10);
    }
    (void)heals; (void)resent;
}
