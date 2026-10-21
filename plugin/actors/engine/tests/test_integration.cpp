// Two or three machines, each with its own fake engine, EnginePort, ActorFeed and a REAL ActorSync, joined by the lossy in-memory net of
// plugin/actors/tests/rig.h. Checks the things only the whole chain can show: one AI per actor, smooth copies, handoff without a restart,
// stand-ins, death once, one body per leveled pick, clean-up. Twin 52 (stand-ins survive a world change) is caught here.
#include <memory>
#include <string>

#include "feed_rig.h"
#include "minitest.h"
#include "rig.h"

using namespace nvae_test;

namespace {

struct Mach {
    int slot = 0;
    FakeEngine eng;
    MemStore store;
    Ledger led;
    EnginePort port;
    std::unique_ptr<nva_test::Endpoint> ep;
    std::unique_ptr<ActorSync> sync;
    ActorFeed feed;
    uint64_t gen = 0;
    std::vector<DiedEvent> died;

    Mach(int s, nva_test::SimNet& net, const Config& c)
        : slot(s), led(&store), port(eng, led, c), feed(eng, port, led, [this] { return sync.get(); }, [this] { return gen; }) {
        feed.setConfig(c);
        ep.reset(new nva_test::Endpoint(net, s));
        SyncConfig sc;
        sc.self = static_cast<PlayerId>(s);
        sc.isHost = s == 0;
        sc.adjacent = [this](CellId a, CellId b) { return feed.adjacent(a, b); };
        sync.reset(new ActorSync(sc, port, *ep));
        gen = 1;
    }
};

struct World {
    nva_test::SimNet net;
    std::vector<std::unique_ptr<Mach>> m;
    uint64_t now = 1000;
    std::vector<std::string> errors;
    int cur = -1;
    std::vector<DiedEvent> diedAll;
    std::vector<std::pair<int, DiedEvent>> diedBy;

    explicit World(int n, Config c = rigConfig()) {
        registry().reset();
        net.now = now;
        for (int i = 0; i < n; ++i) m.push_back(std::make_unique<Mach>(i, net, c));
        for (int i = 1; i < n; ++i) { m[0]->sync->playerJoined(static_cast<PlayerId>(i)); m[static_cast<size_t>(i)]->sync->hostConnected(); }
        registry().addDied([this](const DiedEvent& e) { diedBy.push_back({cur, e}); });
    }
    ~World() { registry().reset(); }
    Mach& at(int i) { return *m[static_cast<size_t>(i)]; }
    void select(int i) {
        cur = i;
        Mach& x = at(i);
        wireApi(x.port, x.feed, x.eng, [&x] { return x.sync.get(); });
    }
    // The same placed NPC on every machine in `mask` (bit i = machine i); the actor walks (vx, vy) wherever its AI runs.
    void npc(RefId ref, live::Vec3 pos, uint32_t mask = ~0u, float vx = 0, float vy = 0, uint32_t baseLocal = 0x500, bool named = false) {
        for (int i = 0; i < static_cast<int>(m.size()); ++i) {
            at(i).eng.form("falloutnv.esm", baseLocal);        // the base form is in every machine's data, whether the ref is loaded there or not
            if (!((mask >> i) & 1u)) continue;
            FakeActor& f = at(i).eng.addNpc(ref, baseLocal, pos, 1, vx, vy);
            f.baseUnique = named;
        }
    }
    void player(int i, live::Vec3 pos, CellId cell = 1) {
        if (!at(i).eng.cells.count(cell)) at(i).eng.cells[cell] = FakeEngine::Cell{};
        at(i).eng.a[0x14].pos = pos;
        at(i).eng.a[0x14].cell = cell;
    }
    // native AI running: not restrained, alive, in the world
    static bool aiRuns(const FakeActor& f) { return !f.restrained && !f.scriptRestrained && f.life == 0 && !f.disabled && !f.deleted && !f.player; }
    void check() {
        std::set<RefId> refs;
        for (auto& mc : m) for (auto& kv : mc->eng.a) if (!kv.second.player && (kv.first >> 24) != 0xFF) refs.insert(kv.first);
        for (RefId r : refs) {
            int n = 0;
            for (auto& mc : m) { auto it = mc->eng.a.find(r); if (it != mc->eng.a.end() && aiRuns(it->second)) ++n; }
            if (n > 1) errors.push_back("actor " + std::to_string(r) + " runs its AI on " + std::to_string(n) + " machines at t=" + std::to_string(now));
        }
    }
    void step(uint32_t dt = 20) {
        now += dt;
        net.deliver(now, [&](const nva_test::NetMsg& msg) {
            select(msg.to);
            if (at(msg.to).sync) at(msg.to).sync->receive(static_cast<PlayerId>(msg.from), msg.data.data(), msg.data.size());
        });
        // the avatar layer: every machine hears where the other players are (each machine's own player is read by its feed)
        for (int i = 0; i < static_cast<int>(m.size()); ++i) {
            const FakeActor& pl = at(i).eng.a[0x14];
            PlayerPose pose;
            pose.present = true; pose.cell = pl.cell; pose.pos = pl.pos;
            for (int j = 0; j < static_cast<int>(m.size()); ++j) {
                if (j == i) continue;
                select(j);
                setPlayerPose(static_cast<PlayerId>(i), pose);
            }
        }
        for (int i = 0; i < static_cast<int>(m.size()); ++i) {
            select(i);
            at(i).feed.tick(now);
            if (at(i).sync) at(i).sync->tick(now);
            at(i).eng.simulate(dt);
        }
        check();
    }
    void run(uint32_t ms, uint32_t dt = 20) { for (uint32_t t = 0; t < ms; t += dt) step(dt); }
    NetId id(RefId ref) { return deriveNetId(at(0).eng.refForm(ref)); }
    // how many machines run the native AI of a placed ref, and which
    int aiMachine(RefId ref) {
        int who = -1;
        for (int i = 0; i < static_cast<int>(m.size()); ++i) { auto it = at(i).eng.a.find(ref); if (it != at(i).eng.a.end() && aiRuns(it->second)) who = i; }
        return who;
    }
};

float dx(World& w, RefId ref) { return std::fabs(w.at(0).eng.a[ref].pos.x - w.at(1).eng.a[ref].pos.x); }

}  // namespace

TEST(integration, one_owner_runs_the_ai_and_the_other_copy_follows_smoothly) {
    World w(2);
    w.npc(0x100, {1000, 1000, 0}, ~0u, 60, 0);
    w.player(0, {0, 0, 0});
    w.player(1, {3000, 0, 0});
    w.run(2500);
    CHECK_MSG(w.errors.empty(), w.errors.empty() ? "" : w.errors[0]);
    CHECK(w.aiMachine(0x100) == 0);
    CHECK(w.at(1).eng.a[0x100].restrained);
    CHECK(w.at(0).eng.a[0x100].pos.x > 1100);                                           // it walked
    CHECK_MSG(dx(w, 0x100) < 60, "copy lags " + std::to_string(dx(w, 0x100)) + " units");
    CHECK(w.at(1).eng.count("setpos", 0x100) > 20);                                     // driven from snapshots
    CHECK(w.at(1).port.stats().drives > 20);
    // the copy was moved in small steps, never teleported across the map
    CHECK(w.at(0).sync->stats().published > 8 && w.at(1).sync->stats().published == 0);
}

TEST(integration, a_handoff_moves_the_ai_without_a_restart_or_a_second_brain) {
    World w(2);
    w.npc(0x100, {1000, 1000, 0}, ~0u, 50, 0);
    w.player(0, {0, 0, 0});
    w.player(1, {3000, 0, 0});
    w.run(2000);
    CHECK(w.aiMachine(0x100) == 0);
    const float xBefore = w.at(0).eng.a[0x100].pos.x;
    // the host walks into another cell; the client stays: the client now owns the actor
    w.at(0).eng.cells[9] = FakeEngine::Cell{};
    w.player(0, {0, 0, 0}, 9);
    w.run(3500);
    CHECK_MSG(w.errors.empty(), w.errors.empty() ? "" : w.errors[0]);
    CHECK(w.at(0).sync->ownerOf(w.id(0x100)).owner == 1 && w.at(1).sync->ownerOf(w.id(0x100)).owner == 1);
    CHECK(w.aiMachine(0x100) == 1);
    CHECK(w.at(0).eng.a[0x100].restrained);
    // the actor kept walking from where the old owner left it: no jump back, no recreation
    CHECK(w.at(1).eng.a[0x100].pos.x > xBefore);
    CHECK(w.at(1).eng.countOp("placeatme") == 0 && w.at(0).eng.countOp("placeatme") == 0);
    CHECK(w.at(1).eng.a[0x100].life == 0 && !w.at(1).eng.a[0x100].deleted);
    CHECK(std::fabs(w.at(0).eng.a[0x100].pos.x - w.at(1).eng.a[0x100].pos.x) < 80);
    // the restrain flag moved with the AI: the ledgers agree
    CHECK(w.at(0).led.has(LedgerKind::Restrained, 0x100) && !w.at(1).led.has(LedgerKind::Restrained, 0x100));
}

TEST(integration, ownership_flipping_back_and_forth_never_runs_two_brains) {
    World w(3);
    w.npc(0x100, {1000, 1000, 0}, ~0u, 30, 10);
    w.npc(0x101, {1500, 800, 0}, ~0u, -20, 0);
    w.at(0).eng.cells[9] = FakeEngine::Cell{};
    w.at(1).eng.cells[9] = FakeEngine::Cell{};
    w.at(2).eng.cells[9] = FakeEngine::Cell{};
    w.player(0, {0, 0, 0});
    w.player(1, {3000, 0, 0});
    w.player(2, {0, 3000, 0});
    w.run(1500);
    for (int round = 0; round < 6; ++round) {
        w.player(0, {0, 0, 0}, round % 2 ? 1 : 9);
        w.player(1, {3000, 0, 0}, round % 3 == 0 ? 9 : 1);
        w.run(2200);
    }
    w.player(0, {0, 0, 0}, 1);
    w.player(1, {3000, 0, 0}, 1);
    w.run(6000);
    CHECK_MSG(w.errors.empty(), w.errors.empty() ? "" : w.errors[0]);
    for (RefId ref : {0x100, 0x101}) {
        const int who = w.aiMachine(ref);
        CHECK_MSG(who >= 0, "actor " + std::to_string(ref) + " has no brain at the end");
        for (int i = 0; i < 3; ++i) {
            const OwnerInfo o = w.at(i).sync->ownerOf(w.id(ref));
            CHECK(o.owner == w.at(0).sync->ownerOf(w.id(ref)).owner && o.epoch == w.at(0).sync->ownerOf(w.id(ref)).epoch);
        }
        for (int i = 1; i < 3; ++i) CHECK_MSG(std::fabs(w.at(0).eng.a[ref].pos.x - w.at(i).eng.a[ref].pos.x) < 150, "actor " + std::to_string(ref) + " diverged on " + std::to_string(i));
    }
}

TEST(integration, an_actor_that_is_not_loaded_here_gets_a_stand_in_until_the_real_one_loads) {
    World w(2);
    w.npc(0x100, {1000, 1000, 0}, 1u, 40, 0);                     // only the host has it loaded
    w.player(0, {0, 0, 0});
    w.player(1, {1500, 0, 0});
    w.run(2500);
    CHECK_MSG(w.errors.empty(), w.errors.empty() ? "" : w.errors[0]);
    Mach& c = w.at(1);
    RefId clone = 0;
    for (auto& kv : c.eng.a) if ((kv.first >> 24) == 0xFF) clone = kv.first;
    CHECK(clone != 0);
    CHECK(c.eng.a[clone].restrained && !c.eng.a[clone].deleted);
    CHECK_MSG(std::fabs(c.eng.a[clone].pos.x - w.at(0).eng.a[0x100].pos.x) < 80, "clone x=" + std::to_string(c.eng.a[clone].pos.x));
    CHECK(c.led.has(LedgerKind::StandIn, clone) && c.port.standInCount() == 1);
    w.select(1);
    CHECK(isRemoteDriven(clone) && isStandIn(clone) && ownerOf(clone).known && ownerOf(clone).ownerSlot == 0);
    // a hit on the clone is the owner's business
    int forwarded = 0;
    setHitForwarder([&](const LocalHitInfo&, const HitRouting& rt) { if (rt.route == HitRoute::ForwardToOwner && rt.victim.ownerSlot == 0) ++forwarded; });
    LocalHitInfo h;
    h.attacker = 0x14; h.victim = clone; h.damage = 20;
    CHECK(onLocalHit(h) && forwarded == 1);
    // the real actor loads on the client (its cell came into range): the clone goes, only one body is left
    c.eng.addNpc(0x100, 0x500, {1100, 1000, 0}, 1, 40, 0);
    w.run(1500);
    CHECK(c.eng.a[clone].deleted && c.port.standInCount() == 0 && !c.led.has(LedgerKind::StandIn, clone));
    CHECK(c.eng.a[0x100].restrained && w.aiMachine(0x100) == 0);
    CHECK(w.errors.empty());
    CHECK_MSG(std::fabs(c.eng.a[0x100].pos.x - w.at(0).eng.a[0x100].pos.x) < 80, "after the swap the real copy follows");
    w.select(1);
    CHECK(!isStandIn(0x100) && isRemoteDriven(0x100) && ownerOf(0x100).ownerSlot == 0);
}

TEST(integration, a_death_happens_once_everywhere) {
    World w(2);
    w.npc(0x100, {1000, 1000, 0});
    w.npc(0x101, {1200, 1000, 0});
    w.player(0, {0, 0, 0});
    w.player(1, {3000, 0, 0});
    w.run(2000);
    CHECK(w.aiMachine(0x100) == 0);
    w.at(0).eng.a[0x100].life = 2;                                    // the owner's engine kills it
    w.at(0).eng.a[0x100].av[kAvHealth] = 0;
    w.run(3000);
    CHECK(w.at(0).eng.a[0x100].life == 2 && w.at(1).eng.a[0x100].life == 2);
    CHECK(w.at(0).eng.count("resurrect", 0x100) == 0 && w.at(1).eng.count("resurrect", 0x100) == 0);
    int hostDeaths = 0, clientDeaths = 0;
    for (auto& d : w.diedBy) {
        if (d.second.netId != w.id(0x100)) continue;
        if (d.first == 0) ++hostDeaths; else ++clientDeaths;
    }
    CHECK_MSG(hostDeaths == 1 && clientDeaths == 1, "deaths host=" + std::to_string(hostDeaths) + " client=" + std::to_string(clientDeaths));
    CHECK(w.at(1).eng.count("kill", 0x100) >= 1);                      // the client's copy was killed by the network message
    CHECK(w.at(0).eng.a[0x101].life == 0 && w.at(1).eng.a[0x101].life == 0);
    // a local kill on the non-owner (a stray explosion) is undone
    w.at(1).eng.a[0x101].life = 2;
    w.run(3000);
    CHECK(w.at(1).eng.a[0x101].life == 0 && w.at(1).eng.count("resurrect", 0x101) >= 1);
}

TEST(integration, the_owners_pick_decides_which_body_everyone_has) {
    World w(2);
    w.npc(0x100, {1000, 1000, 0}, 1u, 0, 0, 0x500);                    // host rolled body 0x500
    w.at(1).eng.addNpc(0x100, 0x501, {1000, 1000, 0});                 // the client's own roll differs
    w.at(1).eng.form("falloutnv.esm", 0x500);
    w.player(0, {0, 0, 0});
    w.player(1, {3000, 0, 0});
    w.run(3500);
    CHECK_MSG(w.errors.empty(), w.errors.empty() ? "" : w.errors[0]);
    Mach& c = w.at(1);
    const RefId body = c.port.activeRef(w.id(0x100));
    CHECK(body != 0x100 && body != 0);
    CHECK(c.eng.a[body].base == c.eng.form("falloutnv.esm", 0x500));
    CHECK(c.eng.a[0x100].disabled && c.led.has(LedgerKind::Disabled, 0x100));
    CHECK(c.eng.a[body].restrained);
    w.at(0).eng.a[0x100].vx = 50;
    w.run(2000);
    CHECK_MSG(std::fabs(c.eng.a[body].pos.x - w.at(0).eng.a[0x100].pos.x) < 60, "the replacement body follows the owner");
    CHECK(std::fabs(c.eng.a[0x100].pos.x - 1000) < 1);                  // the disabled original stays put
    // leaving the session restores the original
    c.sync.reset();
    ++c.gen;
    w.run(100);
    CHECK(!c.eng.a[0x100].disabled && c.eng.a[body].deleted && !c.eng.a[0x100].restrained && c.led.empty());
}

TEST(integration, a_loaded_game_rebuilds_the_stand_ins_instead_of_trusting_the_old_flags) {
    World w(2);
    w.npc(0x100, {1000, 1000, 0}, 1u, 0, 0);
    w.player(0, {0, 0, 0});
    w.player(1, {1500, 0, 0});
    w.run(2500);
    Mach& c = w.at(1);
    CHECK(c.port.standInCount() == 1);
    RefId first = 0;
    for (auto& kv : c.eng.a) if ((kv.first >> 24) == 0xFF) first = kv.first;
    CHECK(first != 0);
    c.eng.epoch = 2;                       // the client loads a save: everything of the old world is gone
    w.run(2500);
    CHECK_MSG(c.port.standInCount() == 1, "stand-ins after the load: " + std::to_string(c.port.standInCount()));
    RefId second = 0;
    for (auto& kv : c.eng.a) if ((kv.first >> 24) == 0xFF && !kv.second.deleted) second = kv.first;
    CHECK(second != 0 && second != first);
    CHECK(c.eng.a[first].deleted);         // the old clone, found by the ledger, is gone
}

TEST(integration, a_dropped_link_and_a_rejoin_leave_one_brain_and_no_leaks) {
    World w(2);
    w.npc(0x100, {1000, 1000, 0}, ~0u, 40, 0);
    w.player(0, {0, 0, 0});
    w.player(1, {3000, 0, 0});
    w.run(2000);
    w.net.down.insert(1);
    w.at(1).sync->hostLost();
    w.run(1500);
    w.net.down.erase(1);
    w.at(0).sync->playerLeft(1);
    w.at(0).sync->playerJoined(1);
    w.at(1).sync->hostConnected();
    w.run(4000);
    CHECK_MSG(w.errors.empty(), w.errors.empty() ? "" : w.errors[0]);
    CHECK(w.aiMachine(0x100) == 0 && w.at(1).eng.a[0x100].restrained);
    CHECK(std::fabs(w.at(0).eng.a[0x100].pos.x - w.at(1).eng.a[0x100].pos.x) < 100);
    CHECK(w.at(1).port.standInCount() == 0 && w.at(0).port.standInCount() == 0);
}

TEST(integration, the_new_owner_takes_the_fight_up_again_against_the_same_target) {
    World w(2);
    w.npc(0x100, {1000, 1000, 0}, ~0u, 30, 0);
    w.npc(0x200, {1300, 1000, 0}, ~0u, 0, 0);
    w.player(0, {0, 0, 0});
    w.player(1, {3000, 0, 0});
    w.run(2000);
    CHECK(w.aiMachine(0x100) == 0);
    w.at(0).eng.a[0x100].target = 0x200;               // on the host, 0x100 is fighting 0x200
    w.run(1000);                                       // the target travels in the snapshots
    w.at(0).eng.cells[9] = FakeEngine::Cell{};
    w.player(0, {0, 0, 0}, 9);                         // the host leaves: the client takes over
    w.run(4000);
    CHECK_MSG(w.errors.empty(), w.errors.empty() ? "" : w.errors[0]);
    CHECK(w.aiMachine(0x100) == 1);
    CHECK_MSG(w.at(1).eng.did("startcombat:256:" + std::to_string(0x200)), "the new owner did not resume the fight");
    CHECK(w.at(1).eng.a[0x100].target == 0x200);
    CHECK(w.at(1).port.stats().combatRestarted >= 1);
}
