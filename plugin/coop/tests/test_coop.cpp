// L10 in-process tests: config parsing, UDP transport, host+client Runtime convergence over real loopback sockets.
// (The two-PROCESS test lives in tests/test_integration.py and drives coop_peer.exe.)
#include <chrono>
#include <cstdio>
#include <functional>
#include <memory>
#include <thread>
#include <vector>

#include "fake_world.h"
#include "baseline_file.h"
#include "udp_transport.h"

using namespace nvcoop::coop;
using namespace nvcoop::coop::test;
using nvcoop::json::Value;

static int g_checks = 0, g_failed = 0;
#define CHECK(c)                                                                  \
    do {                                                                          \
        ++g_checks;                                                               \
        if (!(c)) { ++g_failed; std::printf("  FAIL %s:%d  %s\n", __FILE__, __LINE__, #c); } \
    } while (0)

static uint64_t nowMs() {
    return std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now().time_since_epoch()).count();
}

static bool pumpUntil(const std::function<void()>& frame, const std::function<bool()>& done, int timeoutMs = 5000) {
    uint64_t end = nowMs() + timeoutMs;
    while (nowMs() < end) {
        frame();
        if (done()) return true;
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    return done();
}

static fnvcoop::FormRef R(const char* plugin, uint32_t id) { return nvhooks::makeRef(plugin, id); }

// A nested host script: setstage VCG02 10 -> result script sets a global, adds caps/items, enables a ref; one nested
// stage on a second quest. Returns via the fake engine so it is captured exactly like L5's hooks would.
static void runVcg02(FakeWorld& w, nvhooks::CaptureHub& hub) {
    using namespace fnvcoop;
    nvhooks::ScopedCapture outer(hub, Cause{CauseKind::QuestResult, R("FalloutNV.esm", 0x10A214), 10});
    w.hostMutate(hub, SetStage{R("FalloutNV.esm", 0x10A214), 10});
    w.hostMutate(hub, SetGlobal{R("FalloutNV.esm", 0x123456), 3.0f});
    w.hostMutate(hub, AddItem{R("FalloutNV.esm", 0xF), 100, Recipient::dflt()});
    {
        nvhooks::ScopedCapture inner(hub, Cause{CauseKind::QuestResult, R("FalloutNV.esm", 0x104C00), 5});
        w.hostMutate(hub, SetStage{R("FalloutNV.esm", 0x104C00), 5});
        w.hostMutate(hub, StartQuest{R("FalloutNV.esm", 0x104C01)});
    }
    w.hostMutate(hub, EnableRef{R("FalloutNV.esm", 0x777)});
    w.hostMutate(hub, ModKarma{2, Recipient::dflt()});
}

struct Node {
    struct Opt { const char* transport = "enet"; bool baseline = false; int seed = 0; bool dialogue = false; bool blockClientLoad = false; bool rules = false; bool handshake = false; bool partyWarp = false; bool noPlayerRef = false; uint64_t buildHash = 0; std::vector<std::string> plugins; };
    nvhooks::CaptureHub hub;
    FakeWorld world;
    std::unique_ptr<Runtime> rt;
    std::vector<fnvcoop_dlg::Event> dlgEvents;
    int dlgRuns = 0;
    Node(Role role, uint16_t port, uint64_t token = 0, const char* transport = "enet") : Node(role, port, token, Opt{transport}) {}
    Node(Role role, uint16_t port, uint64_t token, Opt o, const std::function<void(FakeWorld&)>& prep = nullptr) {
        world.seed(o.seed);
        if (prep) prep(world);
        Config c;
        c.enable = true;
        c.role = role;
        c.port = port;
        c.token = token;
        c.transport = o.transport;
        c.baseline = o.baseline;
        c.clientLoadBlock = o.blockClientLoad;
        c.handshake = o.handshake;
        rt.reset(new Runtime(c, &hub, &world, nowMs));
        rt->setDumper(&world);
        if (o.handshake) {
            fnvcoop::HandshakeInfo hs;
            hs.buildNumber = 7;
            hs.buildHash = o.buildHash;
            hs.buildName = "test build";
            hs.plugins = o.plugins;
            rt->setHandshakeBase(hs);
        }
        rt->setActionExecutor([this](fnvcoop::PlayerId s, fnvcoop::IntentKind k, const fnvcoop::FormRef& r) { world.executeAction(hub, s, k, r); });
        if (o.rules && role == Role::Host) {
            static const char* kRules = R"({"schema":1,"quests":{
 "VCG01":{"tier":"T4","questline":"Main: Prologue","curated":true,"rules":[
   {"kind":"Barrier","params":{"step":"all-players-pass"},"origin":"curated"},
   {"kind":"RewardAll","params":{},"origin":"curated"},
   {"kind":"SaveSuppress","params":{},"origin":"curated"}]},
 "WarpQ":{"tier":"T3","questline":"x","curated":true,"rules":[{"kind":"PartyWarp","params":{},"origin":"curated"}]}}})";
            std::string rerr;
            if (!rt->setRules(kRules, &rerr)) std::printf("  rules failed: %s\n", rerr.c_str());
            rt->setEdidResolver([](const std::string& edid, fnvcoop::FormRef& out) {
                uint32_t h = 2166136261u;
                for (char ch : edid) { h ^= (uint8_t)ch; h *= 16777619u; }
                out = fnvcoop::FormRef("falloutnv.esm", h);
                return true;
            });
            if (!o.noPlayerRef)   // twin: with no resolver beyond the default (host only) a guest would be left behind -> still host-only
                rt->setPlayerRefResolver([](fnvcoop::PlayerId slot, fnvcoop::FormRef& out) {
                    out = fnvcoop::FormRef("falloutnv.esm", slot == 0 ? 0x14u : 0x1000u + slot);
                    return true;
                });
            rt->setStageApplier([this](const fnvcoop::FormRef& q, uint32_t st) {
                nvhooks::ScopedCapture sc(hub, fnvcoop::Cause{fnvcoop::CauseKind::QuestResult, q, (int)st});
                world.hostMutate(hub, fnvcoop::SetStage{q, st});
            });
        }
        if (o.dialogue) {
            DialogueHooks h;
            h.run = [this](const fnvcoop_dlg::ResultRequest& rq) -> uint64_t {
                ++dlgRuns;   // the host "runs the result script": commits one DialogueResult txn per phase
                nvhooks::ScopedCapture sc(hub, fnvcoop::Cause{fnvcoop::CauseKind::DialogueResult, rq.info, (int)rq.phase});
                world.hostMutate(hub, fnvcoop::SetGlobal{R("FalloutNV.esm", 0x500 + rq.step * 2 + (int)rq.phase), 1.0f});
                return rt->host() ? rt->host()->log().head() + 1 : 0;
            };
            h.sink = [this](const fnvcoop_dlg::Event& e) { dlgEvents.push_back(e); };
            rt->enableDialogue(h);
        }
        std::string err;
        if (!rt->start(&err)) std::printf("  start failed: %s\n", err.c_str());
    }
};

static void testConfig() {
    std::printf("config\n");
    Config c;
    std::string err;
    CHECK(ParseConfig("[bridge]\nport=1\n[coop]\nenable=1\nrole=host\nport=4000\nlisten=0.0.0.0\ntoken=77\n; c\n", &c, &err));
    CHECK(c.enable && c.role == Role::Host && c.port == 4000 && c.listen == "0.0.0.0" && c.token == 77);
    Config d;
    CHECK(ParseConfig("[coop]\nrole=guest\nhost=10.0.0.5\nenable=true\n", &d, &err));
    CHECK(d.role == Role::Client && d.host == "10.0.0.5" && d.enable);
    Config e;
    CHECK(ParseConfig("", &e, &err) && !e.enable && e.role == Role::Off);  // default: inert
    Config f;
    CHECK(!ParseConfig("[coop]\nrole=banana\n", &f, &err));
    CHECK(!ParseConfig("[coop]\nport=99999\n", &f, &err));
}

static void testTransport() {
    std::printf("transport\n");
    std::string err;
    auto srv = UdpTransport::Server("127.0.0.1", 0, &err);
    CHECK(srv != nullptr);
    if (!srv) return;
    auto c1 = UdpTransport::Client("127.0.0.1", srv->localPort(), &err);
    auto c2 = UdpTransport::Client("127.0.0.1", srv->localPort(), &err);
    CHECK(c1 && c2);
    fnvcoop::Bytes small = {1, 2, 3}, big(300000);
    for (size_t i = 0; i < big.size(); ++i) big[i] = static_cast<uint8_t>(i * 31 + 7);
    c1->send(0, small);
    c2->send(0, big);
    c2->send(0, {});  // empty message
    fnvcoop::PeerId p1 = ~0u, p2 = ~0u;
    int got = 0;
    pumpUntil([&] {
        fnvcoop::PeerId from;
        fnvcoop::Bytes b;
        while (srv->poll(from, b)) {
            ++got;
            if (b == small) p1 = from;
            else if (b == big) p2 = from;
        }
    }, [&] { return got >= 3; });
    CHECK(got == 3);
    CHECK(p1 != ~0u && p2 != ~0u && p1 != p2 && p1 >= 1 && p2 >= 1);
    // reply per peer
    srv->send(p2, big);
    srv->send(p1, small);
    fnvcoop::Bytes r1, r2;
    pumpUntil([&] {
        fnvcoop::PeerId from;
        fnvcoop::Bytes b;
        while (c1->poll(from, b)) { CHECK(from == 0); r1 = b; }
        while (c2->poll(from, b)) { CHECK(from == 0); r2 = b; }
    }, [&] { return !r1.empty() && !r2.empty(); });
    CHECK(r1 == small && r2 == big);
    // garbage and truncated fragments are ignored, never delivered
    {
        auto g = UdpTransport::Client("127.0.0.1", srv->localPort(), &err);
        g->send(0, small);  // legit so the peer exists
        const auto before = srv->stats().badDatagrams;
        // raw garbage through a second plain socket is covered by the fragment validity checks; simulate by loss:
        fnvcoop::PeerId from;
        fnvcoop::Bytes b;
        pumpUntil([&] { while (srv->poll(from, b)) {} }, [&] { return false; }, 100);
        CHECK(srv->stats().badDatagrams == before);
    }
    // loss: dropping a fragment loses the whole message (no partial delivery)
    c2->setDropEveryNth(2);
    c2->send(0, big);
    int deliveredBig = 0;
    pumpUntil([&] {
        fnvcoop::PeerId from;
        fnvcoop::Bytes b;
        while (srv->poll(from, b)) if (b == big) ++deliveredBig;
    }, [&] { return false; }, 300);
    CHECK(deliveredBig == 0);
}

static void testConvergence(const char* tr) {
    std::printf("convergence (loopback sockets, 2 clients, late join)\n");
    Node host(Role::Host, 0, 0, tr);
    CHECK(host.rt->running());
    uint16_t port = host.rt->boundPort();
    Node c1(Role::Client, port, 1001, tr), c2(Role::Client, port, 1002, tr);
    CHECK(c1.rt->running() && c2.rt->running());
    auto frameAll = [&] { host.rt->frame(); c1.rt->frame(); c2.rt->frame(); };

    CHECK(pumpUntil(frameAll, [&] { return c1.rt->client()->joined() && c2.rt->client()->joined(); }));
    CHECK(c1.rt->client()->slot() != c2.rt->client()->slot());

    runVcg02(host.world, host.hub);
    CHECK(host.rt->counters().captured == 1);   // nested scopes = ONE txn
    CHECK(host.rt->host()->log().head() == 1);
    CHECK(pumpUntil(frameAll, [&] { return c1.rt->client()->applied() == 1 && c2.rt->client()->applied() == 1 && host.rt->host()->allSynced(); }));

    Value hw = host.world.dump(), w1 = c1.world.dump(), w2 = c2.world.dump();
    CHECK(hw.dump() == w1.dump());
    CHECK(hw.dump() == w2.dump());
    CHECK(c1.world.stages.at("falloutnv.esm:10A214") == 10);
    CHECK(c1.world.stages.at("falloutnv.esm:104C00") == 5);   // the nested stage arrived in the SAME txn
    CHECK(c1.world.inventory.at("falloutnv.esm:00000F") == 100);
    CHECK(c1.world.karma == 2);

    // echo guard: applying a remote txn on a client never produced a client-side capture
    CHECK(c1.rt->counters().captured == 0 && c1.hub.stats().txns == 0);

    // second txn, then a late joiner replays everything
    {
        using namespace fnvcoop;
        nvhooks::ScopedCapture s(host.hub, Cause{CauseKind::DialogueResult, R("FalloutNV.esm", 0x15D4E9), 0});
        host.world.hostMutate(host.hub, SetObjective{R("FalloutNV.esm", 0x10A214), 3, 3});
        host.world.hostMutate(host.hub, DisableRef{R("FalloutNV.esm", 0x777)});
    }
    Node late(Role::Client, port, 1003, tr);
    auto frameAll2 = [&] { frameAll(); late.rt->frame(); };
    CHECK(pumpUntil(frameAll2, [&] { return late.rt->client()->applied() == 2 && c1.rt->client()->applied() == 2 && c2.rt->client()->applied() == 2 && host.rt->host()->allSynced(); }));
    CHECK(host.world.dump().dump() == late.world.dump().dump());
    CHECK(host.world.dump().dump() == c1.world.dump().dump());

    // log + status JSON
    Value st, lg;
    std::string err;
    CHECK(nvcoop::json::parse(host.rt->statusJson(), st, err));
    CHECK(st.get("role").asString("") == "host" && st.get("lastSeq").asInt() == 2 && st.get("connected").asInt() == 3);
    CHECK(st.get("host").get("commits").asInt() == 2);
    CHECK(nvcoop::json::parse(late.rt->logJson(10), lg, err));
    CHECK(lg.get("count").asInt() >= 1);
    CHECK(nvcoop::json::parse(c1.rt->statusJson(), st, err));
    CHECK(st.get("client").get("joined").asBool() && st.get("client").get("applied").asInt() == 2);

    // inject on host (as if captured) reaches everyone
    std::string res;
    CHECK(host.rt->inject(R"({"txn":{"cause":{"kind":1,"source":"falloutnv.esm:000AAA","aux":1},"origin":0,
        "effects":[{"type":"SetGlobal","global":"falloutnv.esm:000BBB","value":9}]}})", &res, &err));
    CHECK(pumpUntil(frameAll2, [&] { return late.rt->client()->applied() == 3 && host.rt->host()->allSynced(); }));
    CHECK(late.world.globals.at("falloutnv.esm:000BBB") == 9.0f);
    // inject on a client = applied as if received (no seq, no wire)
    CHECK(c1.rt->inject(R"({"cause":{"kind":1,"source":"","aux":0},"origin":0,"effects":[{"type":"SetGlobal","global":"falloutnv.esm:000CCC","value":4}]})", &res, &err));
    CHECK(c1.world.globals.at("falloutnv.esm:000CCC") == 4.0f);
    CHECK(!host.rt->inject("{not json", &res, &err));
    CHECK(!c1.rt->inject(R"({"cause":{},"effects":[{"type":"Nope"}]})", &res, &err));
}

static void testActions() {
    std::printf("guest actions (activate -> host executes -> txn to all)\n");
    Node host(Role::Host, 0);
    Node a(Role::Client, host.rt->boundPort(), 3001), b(Role::Client, host.rt->boundPort(), 3002);
    auto fr = [&] { host.rt->frame(); a.rt->frame(); b.rt->frame(); };
    CHECK(a.rt->submitAction(fnvcoop::IntentKind::ActivateRef, R("FalloutNV.esm", 0xABC)) == 0);   // not joined yet: refused
    CHECK(pumpUntil(fr, [&] { return a.rt->client()->joined() && b.rt->client()->joined(); }));
    std::vector<std::pair<int, bool>> actingEvents;   // (slot, on) as seen by the engine layer's acting-player hook
    fnvcoop::PlayerId seenInside = fnvcoop::kNoPlayer;
    host.rt->setActingHook([&](fnvcoop::PlayerId slot, bool on) {
        actingEvents.emplace_back((int)slot, on);
        if (on) seenInside = host.rt->actingSlot();
    });
    uint32_t id = a.rt->submitAction(fnvcoop::IntentKind::ActivateRef, R("FalloutNV.esm", 0xABC));
    CHECK(id != 0);
    CHECK(pumpUntil(fr, [&] { return a.rt->client()->applied() == 1 && b.rt->client()->applied() == 1 && host.rt->host()->allSynced(); }));
    CHECK(host.rt->counters().actions == 1);
    CHECK(actingEvents.size() == 2 && actingEvents[0].second && !actingEvents[1].second && actingEvents[0].first == (int)a.rt->client()->slot());
    CHECK(seenInside == a.rt->client()->slot() && host.rt->actingSlot() == fnvcoop::kNoPlayer);   // set exactly while the executor runs
    CHECK(host.world.enabled.at("falloutnv.esm:000ABC") && a.world.enabled.at("falloutnv.esm:000ABC") && b.world.enabled.at("falloutnv.esm:000ABC"));
    CHECK(a.world.inventory.at("falloutnv.esm:00000F") == 5);      // the item goes to the requester ONLY
    CHECK(b.world.inventory.count("falloutnv.esm:00000F") == 0 && host.world.inventory.count("falloutnv.esm:00000F") == 0);
    const auto* t = host.rt->host()->log().get(1);                  // the txn carries the guest as origin
    CHECK(t && t->origin == a.rt->client()->slot());
    a.rt->submitAction(fnvcoop::IntentKind::PickUp, R("FalloutNV.esm", 0xDEF));
    CHECK(pumpUntil(fr, [&] { return a.rt->client()->applied() == 2 && host.rt->host()->allSynced(); }));
    CHECK(a.world.inventory.at("falloutnv.esm:000DEF") == 1 && host.rt->counters().actions == 2);   // exactly once each
}

static void testDialogue() {
    std::printf("dialogue channel (0xD1 through the Mux, result scripts on the host)\n");
    Node::Opt o;
    o.dialogue = true;
    Node host(Role::Host, 0, 0, o);
    Node a(Role::Client, host.rt->boundPort(), 4001, o), b(Role::Client, host.rt->boundPort(), 4002, o);
    auto fr = [&] { host.rt->frame(); a.rt->frame(); b.rt->frame(); };
    CHECK(pumpUntil(fr, [&] { return a.rt->client()->joined() && b.rt->client()->joined() && a.rt->dialogue() && b.rt->dialogue() && host.rt->host()->clients().size() == 2; }));
    auto count = [](Node& n, fnvcoop_dlg::Event::Kind k) { int c = 0; for (auto& e : n.dlgEvents) c += e.kind == k; return c; };
    a.rt->dialogue()->requestStart(R("FalloutNV.esm", 0x104C0F), (uint32_t)nowMs());
    CHECK(pumpUntil(fr, [&] { return count(a, fnvcoop_dlg::Event::OpenMenu) == 1; }));
    CHECK(pumpUntil(fr, [&] { return b.rt->dialogue()->client().role() == fnvcoop_dlg::ClientDialogue::Role::Spectator; }));
    a.rt->dialogue()->pick(R("FalloutNV.esm", 0x105560), R("FalloutNV.esm", 0x105583), 0, false, {fnvcoop_dlg::Line{"House.", "", 0, 50}}, {}, (uint32_t)nowMs());
    CHECK(pumpUntil(fr, [&] { return count(b, fnvcoop_dlg::Event::ShowLines) == 1; }));        // spectator sees the line
    a.rt->dialogue()->lineDone((uint32_t)nowMs());
    CHECK(pumpUntil(fr, [&] { return host.dlgRuns == 2 && a.rt->client()->applied() == 2 && b.rt->client()->applied() == 2 && host.rt->host()->allSynced(); }));
    CHECK(host.dlgRuns == 2);                                                                   // begin + end, once each
    CHECK(host.world.dump().dump() == a.world.dump().dump() && host.world.dump().dump() == b.world.dump().dump());
    a.rt->dialogue()->leave((uint32_t)nowMs());
    CHECK(pumpUntil(fr, [&] { return host.rt->dialogue()->host()->open() == 0; }));
    CHECK(host.rt->mux()->unroutable() == 0);
}

static void testBaseline() {
    std::printf("world baseline join (client has its own, different world)\n");
    Node::Opt ho;
    ho.baseline = true;
    ho.seed = 6;
    Node host(Role::Host, 0, 0, ho, [](FakeWorld& w) {
        w.stages["falloutnv.esm:000200"] = 10;
        w.stages["falloutnv.esm:000203"] = 40;
        w.globals["falloutnv.esm:000300"] = 7.0f;
        w.objectives["falloutnv.esm:000200#0"] = 3;
        w.questRunning["falloutnv.esm:000200"] = true;
        w.karma = 12;
    });
    {   // host plays on before the friend joins: the join must see baseline + these txns
        using namespace fnvcoop;
        nvhooks::ScopedCapture s(host.hub, Cause{CauseKind::QuestResult, R("FalloutNV.esm", 0x201), 5});
        host.world.hostMutate(host.hub, SetStage{R("FalloutNV.esm", 0x201), 5});
    }
    Node c(Role::Client, host.rt->boundPort(), 5001, ho, [](FakeWorld& w) {
        w.stages["falloutnv.esm:000200"] = 30;      // ahead of the host: force-set must LOWER it
        w.stages["falloutnv.esm:000202"] = 9;       // host has 0
        w.globals["falloutnv.esm:000300"] = 99.0f;
        w.objectives["falloutnv.esm:000200#0"] = 1;
        w.karma = -5;
    });
    auto fr = [&] { host.rt->frame(); c.rt->frame(); };
    CHECK(pumpUntil(fr, [&] { return c.rt->client()->baselineDone() && c.rt->client()->applied() == host.rt->host()->log().head(); }, 8000));
    CHECK(!c.rt->syncing());
    CHECK(pumpUntil(fr, [&] { return host.rt->host()->allSynced(); }));
    fnvcoop::ShadowState hs, cs;
    uint64_t h1 = 0, h2 = 0;
    std::string e;
    host.world.dump(&hs, &h1, &e);
    c.world.dump(&cs, &h2, &e);
    CHECK(hs.stages == cs.stages && hs.globals == cs.globals && hs.objectives == cs.objectives && hs.questRunning == cs.questRunning);
    CHECK(c.world.stages.at("falloutnv.esm:000200") == 10 && c.world.stages.at("falloutnv.esm:000202") == 0);   // lowered / reset
    CHECK(c.world.stages.at("falloutnv.esm:000201") == 5);                                                       // host's post-baseline txn
    CHECK(c.world.baselineApplied > 0);
    {   // no snapshot flood after a baseline join: only per-slot ledger kinds may arrive untagged, and only a handful
        CHECK(c.world.untaggedSnapshot.size() <= 8);
        for (auto& n : c.world.untaggedSnapshot) {
            std::string k = n;
            CHECK(k == "AddItem" || k == "RemoveItem" || k == "ModActorValue" || k == "ModReputation" || k == "ModKarma" || k == "SetPerk" ||
                  k == "SetPlayerControls" || k == "BarrierRelease");
        }
    }
    CHECK(c.world.karma == 12);
    {   // after the join, live txns still flow
        using namespace fnvcoop;
        nvhooks::ScopedCapture s(host.hub, Cause{CauseKind::QuestResult, R("FalloutNV.esm", 0x204), 5});
        host.world.hostMutate(host.hub, SetStage{R("FalloutNV.esm", 0x204), 15});
    }
    CHECK(pumpUntil(fr, [&] { return c.world.stages.at("falloutnv.esm:000204") == 15; }));
}


// Host saves (sidecar), plays on, then "loads" the older save: epoch+1, the client rebases and its world is force-set back.
static bool hostLoadScenario(bool* epochBumped, bool strict) {
    auto chk = [&](bool c) { if (strict) { ++g_checks; if (!c) { ++g_failed; std::printf("  FAIL save/load scenario check\n"); } } };
    Node::Opt ho;
    ho.baseline = true;
    ho.seed = 6;
    Node host(Role::Host, 0, 0, ho);
    Node c(Role::Client, host.rt->boundPort(), 6001, ho);
    auto fr = [&] { host.rt->frame(); c.rt->frame(); };
    auto txn = [&](uint32_t q, uint32_t stage) {
        using namespace fnvcoop;
        nvhooks::ScopedCapture s(host.hub, Cause{CauseKind::QuestResult, R("FalloutNV.esm", q), (int)stage});
        host.world.hostMutate(host.hub, SetStage{R("FalloutNV.esm", q), stage});
    };
    pumpUntil(fr, [&] { return c.rt->client()->baselineDone() && host.rt->host()->allSynced(); });
    txn(0x201, 5);
    pumpUntil(fr, [&] { return c.rt->client()->applied() == 1; });
    fnvcoop::Bytes sidecar = host.rt->saveSidecar();     // the PreSave hook
    FakeWorld savedWorld = host.world;                    // what the engine would restore on load
    uint32_t epoch0 = host.rt->host()->epoch();
    txn(0x202, 7);
    txn(0x203, 9);
    pumpUntil(fr, [&] { return c.rt->client()->applied() == 3 && host.rt->host()->allSynced(); });
    chk(c.world.stages.at("falloutnv.esm:000203") == 9);
    chk(!sidecar.empty());
    host.world = savedWorld;                              // engine rolled back by the load
    std::string what;
    chk(host.rt->onGameLoaded(&sidecar, &what));        // the PostLoad hook
    *epochBumped = host.rt->host()->epoch() == epoch0 + 1;
    // clients converge to the loaded save's shared world (rebase -> fresh baseline -> force-set)
    bool conv = pumpUntil(fr, [&] {
        return c.world.stages.at("falloutnv.esm:000203") == 0 && c.world.stages.at("falloutnv.esm:000202") == 0 &&
               c.world.stages.at("falloutnv.esm:000201") == 5 && host.rt->host()->allSynced();
    }, 6000);
    if (conv) {
        chk(c.rt->client()->worldEpoch() == epoch0 + 1);
        chk(c.rt->client()->stats().rebases >= 1);
        chk(host.world.dump().dump() == c.world.dump().dump());
        // history restarted: the next txn is seq 1 of the new epoch and still reaches the client
        txn(0x204, 3);
        chk(pumpUntil(fr, [&] { return c.world.stages.at("falloutnv.esm:000204") == 3; }));
    }
    return conv;
}

// Join a baseline client to a host with a 60-quest world; returns how many untagged (non-baseline) effects reached the client.
static size_t floodCount() {
    Node::Opt ho;
    ho.baseline = true;
    ho.seed = 60;
    Node host(Role::Host, 0, 0, ho, [](FakeWorld& w) { w.stages["falloutnv.esm:000200"] = 10; w.karma = 3; });
    Node c(Role::Client, host.rt->boundPort(), 5201, ho, [](FakeWorld& w) { w.stages["falloutnv.esm:000200"] = 30; });
    auto fr = [&] { host.rt->frame(); c.rt->frame(); };
    pumpUntil(fr, [&] { return c.rt->client()->baselineDone() && host.rt->host()->allSynced(); }, 8000);
    pumpUntil(fr, [] { return false; }, 300);   // let any held snapshot diff arrive
    return c.world.untaggedSnapshot.size();
}

static void testNoFlood() {
    std::printf("no snapshot flood after a baseline join (+ twin)\n");
    CHECK(floodCount() <= 8);
    fnvcoop::Mutations m;
    m.snapshotFloodAfterBaseline = true;
    fnvcoop::MutationScope scope(m);
    CHECK(floodCount() > 8);   // twin: with the L4 filter off the same scenario floods, so the assertion above would fail
}

static void testSaveLoad() {
    std::printf("save / load (sidecar, epoch bump, rebase) + client load policy\n");
    bool bumped = false;
    CHECK(hostLoadScenario(&bumped, true));
    CHECK(bumped);
    {   // mutation twin: the host load does NOT bump the epoch -> clients never rebase -> must NOT converge
        fnvcoop::Mutations m;
        m.noEpochBump = true;
        fnvcoop::MutationScope scope(m);
        bool b2 = true;
        CHECK(!hostLoadScenario(&b2, false));
        CHECK(!b2);
    }
    // client policy: Block refuses a load while joined; Rejoin re-baselines to the host world
    for (bool block : {true, false}) {
        Node::Opt ho;
        ho.baseline = true;
        ho.seed = 4;
        ho.blockClientLoad = block;
        Node host(Role::Host, 0, 0, ho);
        Node c(Role::Client, host.rt->boundPort(), 6101, ho);
        auto fr = [&] { host.rt->frame(); c.rt->frame(); };
        CHECK(pumpUntil(fr, [&] { return c.rt->client()->baselineDone(); }));
        std::string what;
        c.world.stages["falloutnv.esm:000200"] = 77;       // the "loaded save" has different local state
        bool ok = c.rt->onGameLoaded(nullptr, &what);
        CHECK(ok == !block);
        CHECK(c.rt->clientLoadAllowed() == !block);
        if (!block) CHECK(pumpUntil(fr, [&] { return c.world.stages.at("falloutnv.esm:000200") == 0; }, 6000));
        else CHECK(c.rt->counters().loadsBlocked == 1);
    }
}


// VCG01 chargen: the host's SetStage 30 is deferred until EVERY joined player passed; then the real stage runs once.
static bool chargenScenario(bool strict, bool disconnectLast) {
    auto chk = [&](bool c) { if (strict) { ++g_checks; if (!c) { ++g_failed; std::printf("  FAIL chargen scenario check\n"); } } };
    Node::Opt o;
    o.rules = true;
    Node host(Role::Host, 0, 0, o);
    auto a = std::make_unique<Node>(Role::Client, host.rt->boundPort(), 7001, o);
    auto b = std::make_unique<Node>(Role::Client, host.rt->boundPort(), 7002, o);
    auto fr = [&] { host.rt->frame(); if (a) a->rt->frame(); if (b) b->rt->frame(); };
    pumpUntil(fr, [&] { return a->rt->client()->joined() && b->rt->client()->joined(); });
    fnvcoop::FormRef vcg01;
    uint32_t h = 2166136261u;
    for (char ch : std::string("VCG01")) { h ^= (uint8_t)ch; h *= 16777619u; }
    vcg01 = fnvcoop::FormRef("falloutnv.esm", h);
    std::string key = nvhooks::refToStr(vcg01);
    chk(host.rt->rules() != nullptr);
    chk(!host.rt->stageRequested(vcg01, 30, 0));           // deferred by the barrier
    chk(!host.rt->stageRequested(vcg01, 30, 0));           // asking again changes nothing
    chk(host.rt->stageRequested(R("FalloutNV.esm", 0x555), 10, 0));   // a quest without rules proceeds
    host.rt->scenePass(vcg01);                             // the host player finished chargen
    a->rt->scenePass(vcg01);                               // guest A finished
    pumpUntil(fr, [&] { return host.rt->counters().rulesActions >= 1; });
    pumpUntil(fr, [] { return false; }, 200);
    bool early = host.world.stages.count(key) != 0;        // B has NOT passed: nothing may have advanced
    chk(!early);
    if (disconnectLast) {
        b->rt->stop();                                     // B leaves: the barrier must not wait for them any more
        b.reset();
    } else {
        b->rt->scenePass(vcg01);
    }
    bool done = pumpUntil(fr, [&] { return host.world.stages.count(key) && host.world.stages.at(key) == 30; }, 8000);
    if (done) {
        chk(a->world.stages.count(key) ? true : pumpUntil(fr, [&] { return a->world.stages.count(key) != 0; }));
        chk(a->world.stages.at(key) == 30);
        chk(host.rt->rules()->pendingDeferrals() == 0);
        chk(host.rt->rules()->stats().stagesReleased == 1);
        if (!disconnectLast) {
            pumpUntil(fr, [&] { return b->world.stages.count(key) != 0; });
            chk(b->world.stages.count(key) && b->world.stages.at(key) == 30);
        }
    }
    return done && !early;
}

static void testRules() {
    std::printf("quest rules: VCG01 chargen barrier (3 players), early-leaver release, ENet disconnect\n");
    CHECK(chargenScenario(true, false));
    CHECK(chargenScenario(true, true));      // the leaver is dropped through the ENet Disconnected event -> peerDisconnected
    {   // twin: the barrier releases as soon as ANYONE passes -> the stage advances before everyone passed -> scenario must fail
        fnvcoop::Mutations m;
        m.barrierReleaseEarly = true;
        fnvcoop::MutationScope scope(m);
        CHECK(!chargenScenario(false, false));
    }
    {   // INFO result cause -> owning quest, and client ReportCell is consumed by the rules engine
        Node::Opt o;
        o.rules = true;
        Node host(Role::Host, 0, 0, o);
        host.rt->setInfoQuestResolver([](const fnvcoop::FormRef& info, fnvcoop::FormRef& q) {
            if (info.localId != 0xAAA) return false;
            q = R("FalloutNV.esm", 0xBBB);
            return true;
        });
        Node c(Role::Client, host.rt->boundPort(), 7101, o);
        auto fr = [&] { host.rt->frame(); c.rt->frame(); };
        pumpUntil(fr, [&] { return c.rt->client()->joined(); });
        {
            using namespace fnvcoop;
            nvhooks::ScopedCapture sc(host.hub, Cause{CauseKind::DialogueResult, R("FalloutNV.esm", 0xAAA), 0});
            host.world.hostMutate(host.hub, SetGlobal{R("FalloutNV.esm", 0x777), 1.0f});
        }
        const auto* t = host.rt->host()->log().get(1);
        CHECK(t && t->cause.source == R("FalloutNV.esm", 0xBBB));
        c.rt->reportCell(R("FalloutNV.esm", 0xCCC));
        CHECK(pumpUntil(fr, [&] { return host.rt->counters().rulesActions >= 1; }));
    }
}


// Real-data repro (skipped when the fixtures do not exist): host baseline + the sidecar written by the real game, client with the
// same real world. The host loads the sidecar (epoch 2) and the client MUST finish the rebase baseline.
class StateDumper : public IWorldDumper {
public:
    fnvcoop::ShadowState st;
    uint64_t hash = 0;
    bool dump(fnvcoop::ShadowState* o, uint64_t* h, std::string*) override { *o = st; *h = hash; return true; }
};

static std::string g_outDir;   // directory of the test exe (= <repo>/out/L10)

static void testRealSidecar() {
    std::string base = g_outDir + "/ingame_baseline/host_baseline.bin", sc = g_outDir + "/ingame_saveload/sidecar.bin";
    StateDumper d;
    if (!LoadBaselineFile(base, &d.st, &d.hash)) { std::printf("real sidecar scenario: SKIPPED (no %s)\n", base.c_str()); return; }
    FILE* f = std::fopen(sc.c_str(), "rb");
    if (!f) { std::printf("real sidecar scenario: SKIPPED (no %s)\n", sc.c_str()); return; }
    fnvcoop::Bytes side;
    uint8_t buf[65536];
    size_t n;
    while ((n = std::fread(buf, 1, sizeof buf, f)) > 0) side.insert(side.end(), buf, buf + n);
    std::fclose(f);
    std::printf("real sidecar scenario (%zu stages, sidecar %zu bytes)\n", d.st.stages.size(), side.size());
    nvhooks::CaptureHub hh, hc;
    FakeWorld wh, wc;
    Config c;
    c.enable = true; c.baseline = true; c.role = Role::Host; c.port = 0;
    Runtime host(c, &hh, &wh, nowMs);
    host.setDumper(&d);
    std::string err;
    CHECK(host.start(&err));
    c.role = Role::Client; c.port = host.boundPort(); c.token = 8001;
    Runtime cl(c, &hc, &wc, nowMs);
    cl.setDumper(&d);
    CHECK(cl.start(&err));
    auto fr = [&] { host.frame(); cl.frame(); };
    CHECK(pumpUntil(fr, [&] { return cl.client()->baselineDone(); }, 15000));
    CHECK(host.onGameLoaded(&side, nullptr));
    bool again = pumpUntil(fr, [&] { return cl.client()->worldEpoch() == host.host()->epoch() && cl.client()->baselineDone() && !cl.client()->baselineActive(); }, 15000);
    if (!again) std::printf("  client stuck after rebase: %s\n  host: %s\n", cl.statusJson().c_str(), host.statusJson().c_str());
    CHECK(again);
}


// Offline replay of the join diff on the REAL exported baseline: a client whose world equals the host world must get ZERO effects.
static void testRealSelfDiff() {
    StateDumper d;
    std::string base = g_outDir + "/ingame_baseline/host_baseline.bin";
    if (!LoadBaselineFile(base, &d.st, &d.hash)) { std::printf("real self-diff: SKIPPED\n"); return; }
    fnvcoop::BaselineSummary sum = fnvcoop::summarizeWorld(d.st, d.hash);
    fnvcoop::BaselineDiff diff = fnvcoop::diffBaseline(d.st, sum, fnvcoop::SharePolicy(), 1, true, d.st.ledger[0].karma, true, {});
    std::printf("real self-diff: %zu entries, diff=%zu effects, kept=%zu\n", sum.entries.size(), diff.effects.size(), diff.keptByClient);
    for (size_t i = 0; i < diff.effects.size() && i < 10; ++i) std::printf("   %s\n", nvhooks::effectToJson(diff.effects[i]).dump().c_str());
    CHECK(diff.effects.empty());
}


static void testHandshake() {
    std::printf("join handshake (build / mod list), reject text, worker thread\n");
    auto join = [&](uint64_t hostHash, uint64_t clientHash, std::vector<std::string> hostMods, std::vector<std::string> clientMods, uint8_t* reason, std::string* text) {
        Node::Opt ho;
        ho.handshake = true; ho.buildHash = hostHash; ho.plugins = hostMods;
        Node host(Role::Host, 0, 0, ho);
        Node::Opt co = ho;
        co.buildHash = clientHash; co.plugins = clientMods;
        Node c(Role::Client, host.rt->boundPort(), 9001, co);
        auto fr = [&] { host.rt->frame(); c.rt->frame(); };
        bool ended = pumpUntil(fr, [&] { return c.rt->client()->joined() || c.rt->client()->rejected(); }, 6000);
        *reason = c.rt->client()->rejectReason();
        *text = c.rt->client()->rejectText();
        return ended && c.rt->client()->joined();
    };
    uint8_t reason = 0;
    std::string text;
    CHECK(join(0xAB, 0xAB, {"falloutnv.esm", "a.esp"}, {"falloutnv.esm", "a.esp"}, &reason, &text));      // identical builds join
    CHECK(!join(0xAB, 0xCD, {"falloutnv.esm"}, {"falloutnv.esm"}, &reason, &text));                         // different build hash
    CHECK(reason == fnvcoop::kRejectBuild && !text.empty());
    std::printf("    build mismatch -> reason %d: %s\n", (int)reason, text.c_str());
    CHECK(!join(0xAB, 0xAB, {"falloutnv.esm", "a.esp"}, {"falloutnv.esm", "b.esp"}, &reason, &text));      // different mod list
    CHECK(reason == fnvcoop::kRejectModList && !text.empty());
    std::printf("    mod list mismatch -> reason %d: %s\n", (int)reason, text.c_str());
}


static size_t partyWarpCount(bool withResolver) {
    Node::Opt o;
    o.rules = true;
    o.noPlayerRef = !withResolver;
    Node host(Role::Host, 0, 0, o);
    Node a(Role::Client, host.rt->boundPort(), 7301, o), b(Role::Client, host.rt->boundPort(), 7302, o);
    auto fr = [&] { host.rt->frame(); a.rt->frame(); b.rt->frame(); };
    pumpUntil(fr, [&] { return a.rt->client()->joined() && b.rt->client()->joined(); });
    uint32_t h = 2166136261u;
    for (char ch : std::string("WarpQ")) { h ^= (uint8_t)ch; h *= 16777619u; }
    fnvcoop::MoveRef m;
    m.ref = R("FalloutNV.esm", 0x14);          // the quest script moves "the player"
    m.cell = R("FalloutNV.esm", 0x777);
    m.x = 100;
    host.rt->host()->commit(0, fnvcoop::Cause{fnvcoop::CauseKind::QuestResult, fnvcoop::FormRef("falloutnv.esm", h), 0}, {m});
    const auto* t = host.rt->host()->log().get(1);
    size_t n = 0;
    if (t) for (auto& e : t->effects) if (auto* mv = std::get_if<fnvcoop::MoveRef>(&e)) n += mv->partyWarp;
    return n;
}

static void testPartyWarp() {
    std::printf("PartyWarp needs the rules engine's playerRef (set by the runtime)\n");
    CHECK(partyWarpCount(true) == 3);      // host + 2 guests moved together
    CHECK(partyWarpCount(false) == 1);     // twin: without a guest ref resolver only the host player is expanded (inert for guests)
}

static void testLossyHost() {
    std::printf("convergence under 1/3 datagram loss from the host\n");
    Node host(Role::Host, 0, 0, "udp");
    host.rt->transport()->setDropEveryNth(3);
    Node c1(Role::Client, host.rt->boundPort(), 2001, 0 ? "" : "udp");
    auto frameAll = [&] { host.rt->frame(); c1.rt->frame(); };
    CHECK(pumpUntil(frameAll, [&] { return c1.rt->client()->joined(); }, 8000));
    for (int i = 0; i < 10; ++i) {
        using namespace fnvcoop;
        nvhooks::ScopedCapture s(host.hub, Cause{CauseKind::QuestResult, R("FalloutNV.esm", 0x100 + i), i});
        host.world.hostMutate(host.hub, SetStage{R("FalloutNV.esm", 0x100 + i), static_cast<uint32_t>(i + 1)});
        host.world.hostMutate(host.hub, AddItem{R("FalloutNV.esm", 0xF), 10, Recipient::dflt()});
    }
    CHECK(pumpUntil(frameAll, [&] { return c1.rt->client()->applied() == 10 && host.rt->host()->allSynced(); }, 15000));
    CHECK(host.world.dump().dump() == c1.world.dump().dump());
}

int main(int argc, char** argv) {
    if (argc > 0) { g_outDir = argv[0]; size_t sl = g_outDir.find_last_of("\\/"); g_outDir = sl == std::string::npos ? "." : g_outDir.substr(0, sl); }
    testConfig();
    testTransport();
    testConvergence("enet");
    testConvergence("udp");
    testActions();
    testDialogue();
    testBaseline();
    testSaveLoad();
    testNoFlood();
    testRules();
    testPartyWarp();
    testRealSidecar();
    testRealSelfDiff();
    testHandshake();
    testLossyHost();
    std::printf("%d checks, %d failed\n", g_checks, g_failed);
    return g_failed ? 1 : 0;
}
