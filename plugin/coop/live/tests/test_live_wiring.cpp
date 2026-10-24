// Live-layer wiring tests (dll-wiring): ini switches, LiveHub lifecycle + channel routing, the avatar network service on a loopback of
// three machines, and the avatar wire codec. Game-free. Built once with FNVCOOP_RUNTIME_MUT; run once for real and once per mutation
// twin (FNVCOOP_MUT=<n>, tools/mut/run_twins.py) - every twin MUST make this binary fail.
//   1..4   switches        11..23 hub        31..33 wire        41..51 avatar net        61..64 [actors] ini
#include <cmath>
#include <cstdio>
#include <cstring>
#include <functional>
#include <new>
#include <stdexcept>
#include <map>
#include <memory>
#include <set>
#include <string>
#include <tuple>
#include <vector>

#include "avatar_net.h"
#include "avatar_runtime_side.h"
#include "avatar_wire.h"
#include "live_hub.h"
#include "live_switches.h"
#ifdef NVLIVE_HAVE_ACTOR_API
#include "live_actors_cfg.h"
#endif
#include "testing/fake_backend.h"

using namespace nvcoop::livewire;
namespace live = fnvcoop::live;
namespace av = nvcoop::avatar;

static int g_fail = 0, g_checks = 0;
#define CHECK(c)                                                       \
    do {                                                               \
        ++g_checks;                                                    \
        if (!(c)) {                                                    \
            ++g_fail;                                                  \
            std::printf("  FAIL %s:%d  %s\n", __FILE__, __LINE__, #c); \
        }                                                              \
    } while (0)
#define TEST(name) static void name()
static void run(const char* n, void (*f)()) {
    int before = g_fail;
    f();
    std::printf("[%s] %s\n", g_fail == before ? " ok " : "FAIL", n);
}

// =============================================================================================================================
// switches
// =============================================================================================================================
static IniGetter fakeIni(std::map<std::string, std::string> kv) {
    return [kv](const char* section, const char* key, const char* def) {
        auto it = kv.find(std::string(section) + "." + key);
        return it == kv.end() ? std::string(def) : it->second;
    };
}

TEST(switches_default_off) {
    LiveSwitches s = readLiveSwitches(fakeIni({}));
    CHECK(!s.actors && !s.avatars && !s.combat && !s.spawns && !s.social);
    CHECK(!s.any());
    CHECK(s.warnings.empty());
    CHECK(describeSwitches(s) == "actors=0 avatars=0 combat=0 spawns=0");
}

TEST(switches_parse_values) {
    for (const char* on : {"1", "true", "TRUE", " on ", "Yes", "yes"}) CHECK(parseSwitchValue(on));
    for (const char* off : {"0", "", "false", "off", "no", "2", "enabled", "maybe", "  "}) CHECK(!parseSwitchValue(off));
    LiveSwitches s = readLiveSwitches(fakeIni({{"live.actors", "1"}, {"live.avatars", "on"}}));
    CHECK(s.actors && s.avatars && !s.combat && !s.spawns);
    CHECK(describeSwitches(s) == "actors=1 avatars=1 combat=0 spawns=0");
    // a typo must not switch anything on
    s = readLiveSwitches(fakeIni({{"live.actors", "ofF"}, {"live.avatars", "disabled"}, {"live.combat", "none"}}));
    CHECK(!s.any());
}

TEST(switches_dependencies) {
    LiveSwitches s = readLiveSwitches(fakeIni({{"live.combat", "1"}, {"live.spawns", "1"}}));
    CHECK(!s.combat && !s.spawns && !s.actors);
    CHECK(s.warnings.find("combat=1 ignored") != std::string::npos);
    CHECK(s.warnings.find("spawns=1 ignored") != std::string::npos);
    s = readLiveSwitches(fakeIni({{"live.actors", "1"}, {"live.combat", "1"}, {"live.spawns", "1"}, {"live.avatars", "1"}}));
    CHECK(s.actors && s.combat && s.spawns && s.avatars);
    CHECK(s.warnings.empty());
    s = readLiveSwitches(fakeIni({{"live.avatars", "1"}, {"live.combat", "1"}}));   // avatars need nobody
    CHECK(s.avatars && !s.combat);
    LiveSwitches r;
    r.combat = true;
    r = resolveSwitches(r);
    CHECK(!r.combat);
}

// [live] social (plugin/companions): off by default, needs actors, never switched on by another lane's switch. Twins 65 / 66 (and 4).
TEST(switches_social) {
    LiveSwitches s = readLiveSwitches(fakeIni({{"live.actors", "1"}, {"live.avatars", "1"}, {"live.combat", "1"}, {"live.spawns", "1"}}));
    CHECK(!s.social);                                                      // every other lane on: still off
    CHECK(describeSwitches(s) == "actors=1 avatars=1 combat=1 spawns=1");  // the line other tools match is unchanged while it is off
    s = readLiveSwitches(fakeIni({{"live.actors", "1"}, {"live.social", "1"}}));
    CHECK(s.social && s.actors && s.warnings.empty() && s.any());
    CHECK(describeSwitches(s) == "actors=1 avatars=0 combat=0 spawns=0 social=1");
    s = readLiveSwitches(fakeIni({{"live.social", "1"}}));                  // companions are owned actors: nobody owns them without actors=1
    CHECK(!s.social && !s.any());
    CHECK(s.warnings.find("social=1 ignored") != std::string::npos);
    s = readLiveSwitches(fakeIni({{"live.actors", "1"}, {"live.social", "typo"}}));
    CHECK(!s.social);
    LiveSwitches r;
    r.social = true;
    r = resolveSwitches(r);
    CHECK(!r.social);
}

// =============================================================================================================================
// hub
// =============================================================================================================================
struct Rec : ILiveService {
    std::string nm;
    std::vector<uint8_t> ch;
    std::vector<std::string>* shared = nullptr;     // optional common log across services
    std::vector<std::string> log;
    std::vector<std::pair<PlayerId, Bytes>> got;
    ILiveLink* link = nullptr;
    Rec(std::string n, std::vector<uint8_t> c, std::vector<std::string>* sh = nullptr) : nm(std::move(n)), ch(std::move(c)), shared(sh) {}
    const char* name() const override { return nm.c_str(); }
    std::vector<uint8_t> channels() const override { return ch; }
    void note(const std::string& s) { log.push_back(s); if (shared) shared->push_back(nm + ":" + s); }
    void sessionBegin(const SessionInfo& i, ILiveLink& l) override { link = &l; note(std::string("begin:") + (i.isHost ? "host" : "client") + ":" + std::to_string(i.self)); }
    void sessionEnd() override { note("end"); link = nullptr; }
    void playerJoined(PlayerId p) override { note("join:" + std::to_string(p)); }
    void playerLeft(PlayerId p) override { note("leave:" + std::to_string(p)); }
    void hostLinkUp() override { note("up"); }
    void hostLinkDown() override { note("down"); }
    void receive(PlayerId from, const uint8_t* d, size_t n) override { got.push_back({from, Bytes(d, d + n)}); note("rx:" + std::to_string(from)); }
    void tick(uint64_t) override { note("tick"); }
    void idleTick(uint64_t) override { note("idle"); }
    void loadBegin() override { note("loadBegin"); }
    void loadEnd() override { note("loadEnd"); }
    void saved() override { note("saved"); }
    void gameExit() override { note("exit"); }
    size_t count(const std::string& s) const { size_t n = 0; for (auto& l : log) n += l == s ? 1 : 0; return n; }
    bool has(const std::string& s) const { return count(s) != 0; }
};

struct Net3 {
    fnvcoop::InMemoryNetwork net{7};
    std::map<fnvcoop::PeerId, std::unique_ptr<fnvcoop::MuxTransport>> mux;
    uint64_t now = 0;
    fnvcoop::MuxTransport* m(fnvcoop::PeerId id) {
        auto& p = mux[id];
        if (!p) p.reset(new fnvcoop::MuxTransport(*net.endpoint(id)));
        return p.get();
    }
    void pump() {   // deliver everything due and let each mux dispatch its registered channels
        net.advance(now);
        for (auto& kv : mux) { fnvcoop::PeerId f; fnvcoop::Bytes d; while (kv.second->poll(f, d)) {} }
    }
};

static SessionView hostView(fnvcoop::MuxTransport* mux, std::vector<ClientLink> clients, uint64_t now) {
    SessionView v;
    v.active = true; v.isHost = true; v.self = kHostPlayer; v.mux = mux; v.clients = std::move(clients); v.nowMs = now;
    return v;
}
static SessionView clientView(fnvcoop::MuxTransport* mux, PlayerId self, bool up, uint64_t now) {
    SessionView v;
    v.active = true; v.isHost = false; v.self = self; v.hostLinkUp = up; v.mux = mux; v.nowMs = now;
    return v;
}
static ClientLink cl(PlayerId slot, fnvcoop::PeerId peer, bool joined = true) { ClientLink c; c.slot = slot; c.peer = peer; c.joined = joined; return c; }

TEST(hub_disabled_service_is_never_called) {
    Net3 n;
    LiveHub hub;
    Rec on("on", {0xA1}), off("off", {0xA2});
    CHECK(hub.add(&on, true));
    CHECK(hub.add(&off, false));
    CHECK(hub.enabledCount() == 1 && hub.serviceCount() == 2);
    hub.onLoadBegin();
    hub.onLoadEnd();
    hub.frame(hostView(n.m(0), {cl(1, 1)}, 10));
    hub.frame(hostView(n.m(0), {cl(1, 1)}, 20));
    hub.onSaved();
    hub.onGameExit();
    CHECK(off.log.empty());
    CHECK(on.has("begin:host:0") && on.has("loadBegin") && on.has("loadEnd") && on.has("saved") && on.has("exit"));
    // a datagram on the disabled service's channel is not delivered anywhere
    hub.frame(hostView(n.m(0), {cl(1, 1)}, 30));
    n.m(1)->sendEx(0, {0xA2, 1, 2, 3}, fnvcoop::ITransport::Channel::Reliable);
    n.pump();
    CHECK(off.got.empty() && on.got.empty());
}

TEST(hub_session_lifecycle_host) {
    Net3 n;
    LiveHub hub;
    std::vector<std::string> order;
    Rec a("a", {0xA1}, &order), b("b", {0xA2}, &order);
    hub.add(&a, true);
    hub.add(&b, true);
    CHECK(!hub.inSession());
    hub.frame(hostView(n.m(0), {}, 10));
    CHECK(hub.inSession());
    CHECK(a.count("begin:host:0") == 1 && b.count("begin:host:0") == 1);
    CHECK(a.count("tick") == 1);
    hub.frame(hostView(n.m(0), {cl(1, 1)}, 20));
    hub.frame(hostView(n.m(0), {cl(1, 1)}, 30));
    hub.frame(hostView(n.m(0), {cl(1, 1), cl(2, 2)}, 40));
    CHECK(a.count("join:1") == 1 && a.count("join:2") == 1 && b.count("join:1") == 1);   // each exactly once
    hub.frame(hostView(n.m(0), {cl(2, 2)}, 50));
    CHECK(a.count("leave:1") == 1 && b.count("leave:1") == 1 && a.count("leave:2") == 0);
    hub.frame(hostView(n.m(0), {cl(1, 1, false), cl(2, 2)}, 60));   // not joined = not a player
    CHECK(a.count("join:1") == 1);
    order.clear();
    SessionView off = hostView(n.m(0), {}, 70);
    off.active = false;
    hub.frame(off);
    CHECK(!hub.inSession());
    CHECK(a.count("end") == 1 && b.count("end") == 1);
    CHECK(order.size() >= 2 && order[0] == "b:end" && order[1] == "a:end");   // services close in reverse order
    hub.frame(off);
    CHECK(a.count("end") == 1);
    CHECK(hub.stats().sessionsBegun == 1 && hub.stats().sessionsEnded == 1);
}

TEST(hub_existing_clients_are_announced_after_begin) {
    Net3 n;
    LiveHub hub;
    std::vector<std::string> order;
    Rec a("a", {0xA1}, &order);
    hub.add(&a, true);
    hub.frame(hostView(n.m(0), {cl(1, 1), cl(2, 2)}, 10));
    CHECK(order.size() >= 3);
    CHECK(order[0] == "a:begin:host:0");
    CHECK(a.count("join:1") == 1 && a.count("join:2") == 1);
    // and a client that connects to a host link that is already up hears hostLinkUp after the begin
    LiveHub chub;
    std::vector<std::string> corder;
    Rec c("c", {0xA1}, &corder);
    chub.add(&c, true);
    chub.frame(clientView(n.m(1), 1, true, 10));
    CHECK(corder.size() >= 2 && corder[0] == "c:begin:client:1" && corder[1] == "c:up");
}

TEST(hub_client_link_events) {
    Net3 n;
    LiveHub hub;
    Rec c("c", {0xA1});
    hub.add(&c, true);
    hub.frame(clientView(n.m(1), kNoPlayer, false, 10));   // not joined, no link: no session
    CHECK(!hub.inSession() && c.log.empty() == false);     // (idle ticks only)
    CHECK(!c.has("begin:client:255"));
    hub.frame(clientView(n.m(1), 1, true, 20));
    CHECK(hub.inSession() && c.count("up") == 1);
    hub.frame(clientView(n.m(1), 1, false, 30));   // rebaseline: the host link drops, the session stays
    CHECK(hub.inSession() && c.count("down") == 1);
    hub.frame(clientView(n.m(1), 1, true, 40));
    CHECK(c.count("up") == 2 && c.count("end") == 0);
    CHECK(c.count("join:1") == 0);   // joins are host-only
}

TEST(hub_new_mux_is_a_new_session_and_old_handlers_are_inert) {
    Net3 n;
    LiveHub hub;
    Rec a("a", {0xA1});
    hub.add(&a, true);
    hub.frame(hostView(n.m(0), {cl(1, 1)}, 10));
    CHECK(a.count("begin:host:0") == 1);
    // the runtime was rebuilt: a new mux object (peer 5) replaces the old one (peer 0)
    hub.frame(hostView(n.m(5), {cl(1, 1)}, 20));
    CHECK(a.count("end") == 1 && a.count("begin:host:0") == 2);
    CHECK(a.count("join:1") == 2);   // the new session re-announces the roster
    CHECK(hub.stats().sessionsBegun == 2);
    // a datagram still arriving on the OLD mux must not reach the service
    n.m(1)->sendEx(0, {0xA1, 9, 9}, fnvcoop::ITransport::Channel::Reliable);
    n.pump();
    CHECK(a.got.empty());
    CHECK(hub.stats().droppedSessionOver == 1);
    // the new mux does deliver
    n.m(1)->sendEx(5, {0xA1, 7}, fnvcoop::ITransport::Channel::Reliable);
    n.pump();
    CHECK(a.got.size() == 1 && a.got[0].first == 1);
}

TEST(hub_routing_and_channel_ownership) {
    Net3 n;
    LiveHub hub;
    Rec a("a", {0xA1}), b("b", {0xA2, 0xA3}), dup("dup", {0xA3});
    CHECK(hub.add(&a, true));
    CHECK(hub.add(&b, true));
    CHECK(!hub.add(&dup, true));   // 0xA3 is taken
    CHECK(!hub.add(&a, true));     // already registered
    CHECK(!hub.add(nullptr, true));
    hub.frame(hostView(n.m(0), {cl(1, 1), cl(2, 2)}, 10));
    n.m(1)->sendEx(0, {0xA1, 1}, fnvcoop::ITransport::Channel::Reliable);
    n.m(2)->sendEx(0, {0xA3, 2, 2}, fnvcoop::ITransport::Channel::Unreliable);
    n.m(2)->sendEx(0, {0xA2, 3}, fnvcoop::ITransport::Channel::Reliable);
    n.pump();
    CHECK(a.got.size() == 1 && a.got[0].first == 1 && a.got[0].second.size() == 2);
    CHECK(b.got.size() == 2 && b.got[0].first == 2);
    CHECK(dup.got.empty());
    // a peer that is not a joined client is ignored
    hub.frame(hostView(n.m(0), {cl(1, 1), cl(2, 2, false)}, 20));
    n.m(2)->sendEx(0, {0xA1, 4}, fnvcoop::ITransport::Channel::Reliable);
    n.m(7)->sendEx(0, {0xA1, 5}, fnvcoop::ITransport::Channel::Reliable);
    n.pump();
    CHECK(a.got.size() == 1);
    CHECK(hub.stats().droppedUnknownPeer == 2);
    // a client hears the host as slot 0
    LiveHub chub;
    Rec c("c", {0xA1});
    chub.add(&c, true);
    chub.frame(clientView(n.m(1), 1, true, 30));
    n.m(0)->sendEx(1, {0xA1, 6}, fnvcoop::ITransport::Channel::Reliable);
    n.pump();
    CHECK(c.got.size() == 1 && c.got[0].first == kHostPlayer);
}

TEST(hub_send_paths) {
    Net3 n;
    LiveHub hub, chub;
    Rec h("h", {0xA1}), c("c", {0xA1});
    hub.add(&h, true);
    chub.add(&c, true);
    hub.frame(hostView(n.m(0), {cl(1, 1), cl(2, 2, false)}, 10));
    chub.frame(clientView(n.m(1), 1, true, 10));
    CHECK(h.link && c.link);
    CHECK(h.link->isHost() && !c.link->isHost());
    CHECK(h.link->self() == kHostPlayer && c.link->self() == 1);
    CHECK(h.link->send(1, true, {0xA1, 1}));        // to a joined client
    CHECK(!h.link->send(2, true, {0xA1, 2}));       // slot 2 has not joined
    CHECK(!h.link->send(3, true, {0xA1, 3}));       // nobody
    CHECK(!h.link->send(1, true, {}));              // empty
    CHECK(c.link->send(kNoPlayer, false, {0xA1, 4}));   // a client always sends to the host
    n.pump();
    CHECK(c.got.size() == 1 && c.got[0].first == kHostPlayer);
    CHECK(h.got.size() == 1 && h.got[0].first == 1);
    CHECK(hub.stats().sendsOk == 1 && hub.stats().sendsFailed == 3);
    // after the session ended the link is dead
    ILiveLink* old = h.link;
    SessionView off = hostView(n.m(0), {}, 20);
    off.active = false;
    hub.frame(off);
    (void)old;   // (the service dropped it in sessionEnd; the hub's own link object is gone)
    CHECK(h.link == nullptr);
}

TEST(hub_loads_and_idle) {
    Net3 n;
    LiveHub hub;
    Rec a("a", {0xA1});
    hub.add(&a, true);
    // no session at all
    SessionView idle;
    idle.nowMs = 5;
    hub.onLoadBegin();
    hub.frame(idle);
    hub.frame(idle);
    hub.onLoadEnd();
    CHECK(a.count("loadBegin") == 1 && a.count("loadEnd") == 1);   // purges happen without a coop session too
    CHECK(a.count("idle") == 2 && a.count("tick") == 0);
    hub.frame(hostView(n.m(0), {}, 10));
    CHECK(a.count("tick") == 1 && a.count("idle") == 2);
    hub.onLoadBegin();
    hub.onLoadEnd();
    CHECK(a.count("loadBegin") == 2 && a.count("loadEnd") == 2);
    hub.onSaved();
    CHECK(a.count("saved") == 1);
    hub.onGameExit();
    CHECK(!hub.inSession() && a.count("end") == 1 && a.count("exit") == 1);
}

TEST(hub_client_reslot_is_a_new_session) {
    Net3 n;
    LiveHub hub;
    Rec c("c", {0xA1});
    hub.add(&c, true);
    hub.frame(clientView(n.m(1), 1, true, 10));
    hub.frame(clientView(n.m(1), 1, true, 20));
    CHECK(c.count("begin:client:1") == 1);
    hub.frame(clientView(n.m(1), 2, true, 30));   // the host gave this client another slot
    CHECK(c.count("end") == 1 && c.count("begin:client:2") == 1);
    CHECK(c.count("up") == 2);   // the new session hears the link state again
}

struct Boom : Rec {
    std::string when;
    Boom(std::string w) : Rec("boom", {0xA3}), when(std::move(w)) {}
    void sessionBegin(const SessionInfo& i, ILiveLink& l) override { Rec::sessionBegin(i, l); if (when == "begin") throw 5; }
    void tick(uint64_t n) override { Rec::tick(n); if (when == "tick") throw std::runtime_error("tick exploded"); }
    void receive(PlayerId f, const uint8_t* d, size_t n) override { Rec::receive(f, d, n); if (when == "receive") throw std::runtime_error("rx exploded"); }
    void playerJoined(PlayerId p) override { Rec::playerJoined(p); if (when == "join") throw std::bad_alloc(); }
    void loadEnd() override { Rec::loadEnd(); if (when == "load") throw std::runtime_error("load exploded"); }
};

TEST(hub_a_throwing_service_is_switched_off_and_the_others_go_on) {
    for (const char* when : {"begin", "tick", "receive", "join", "load"}) {
        Net3 n;
        LiveHub hub;
        Rec good("good", {0xA1});
        Boom bad(when);
        hub.add(&good, true);
        hub.add(&bad, true);
        bool escaped = false;
        auto guarded = [&](const std::function<void()>& f) {   // (under the twin the exception would otherwise end the test binary)
            try {
                f();
            } catch (...) {
                escaped = true;
            }
        };
        guarded([&] { hub.frame(hostView(n.m(0), {cl(1, 1)}, 10)); });
        n.m(1)->sendEx(0, {0xA3, 1}, fnvcoop::ITransport::Channel::Reliable);
        guarded([&] { n.pump(); });
        guarded([&] { hub.onLoadBegin(); });
        guarded([&] { hub.onLoadEnd(); });
        guarded([&] { hub.frame(hostView(n.m(0), {cl(1, 1)}, 20)); });
        guarded([&] { hub.frame(hostView(n.m(0), {cl(1, 1)}, 30)); });
        CHECK(!escaped);                                   // nothing unwinds into the game's frame
        CHECK(good.count("tick") == 3 && good.count("loadEnd") == 1);   // the other lane never noticed
        CHECK(hub.stats().serviceFaults == 1);
        CHECK(hub.enabledCount() == 1);                    // the faulty one is off now
        const size_t calls = bad.log.size();
        guarded([&] { hub.frame(hostView(n.m(0), {cl(1, 1)}, 40)); });
        guarded([&] { hub.onLoadEnd(); });
        CHECK(bad.log.size() == calls);                    // and is never called again
        std::string j = hub.statusJson();
        CHECK(j.find("\"fault\":\"boom: ") != std::string::npos && j.find("\"serviceFaults\":1") != std::string::npos);
        SessionView off = hostView(n.m(0), {}, 50);
        off.active = false;
        guarded([&] { hub.frame(off); });                  // the session ends cleanly
        CHECK(!hub.inSession());
    }
}

TEST(hub_status_json) {
    Net3 n;
    LiveHub hub;
    Rec a("alpha", {0xA1}), b("beta", {0xA2});
    hub.add(&a, true);
    hub.add(&b, false);
    hub.frame(hostView(n.m(0), {cl(1, 1)}, 10));
    std::string j = hub.statusJson();
    CHECK(j.find("\"inSession\":true") != std::string::npos);
    CHECK(j.find("\"role\":\"host\"") != std::string::npos);
    CHECK(j.find("{\"name\":\"alpha\",\"enabled\":true,\"sessionOpen\":true}") != std::string::npos);
    CHECK(j.find("{\"name\":\"beta\",\"enabled\":false,\"sessionOpen\":false}") != std::string::npos);
    CHECK(j.find("\"joins\":1") != std::string::npos);
}

#ifdef NVLIVE_HAVE_ACTOR_API
// =============================================================================================================================
// [actors] ini -> actors::engine::Config
// =============================================================================================================================
static IniGetter actorsIni(std::map<std::string, std::string> kv) {
    return [kv](const char* section, const char* key, const char* def) {
        if (std::string(section) != "actors") return std::string(def);
        auto it = kv.find(key);
        return it == kv.end() ? std::string(def) : it->second;
    };
}

TEST(actors_cfg_defaults_and_overrides) {
    using nvcoop::actors::engine::Config;
    Config d;
    std::string notes;
    Config c = readActorsConfig(actorsIni({}), d, &notes);
    CHECK(notes.empty());
    CHECK(c.enabled == d.enabled && c.scanMs == d.scanMs && c.maxScanActors == d.maxScanActors && c.maxLinesPerFrame == d.maxLinesPerFrame);
    CHECK(c.driveMinMs == d.driveMinMs && c.posEpsilonXY == d.posEpsilonXY && c.persistLedger == d.persistLedger && c.maxStandIns == d.maxStandIns);
    c = readActorsConfig(actorsIni({{"engine", "0"}, {"scanMs", "250"}, {"maxScanActors", "100"}, {"maxLinesPerFrame", "8"}, {"driveMinMs", "50"},
                                    {"posEpsilonXY", "0.5"}, {"posEpsilonZ", "1.5"}, {"yawEpsilonDeg", "2"}, {"pollDeathMs", "200"},
                                    {"restrainNonOwned", "0"}, {"stopCombatOnRestrain", "0"}, {"startCombatOnTakeover", "off"},
                                    {"persistLedger", "0"}, {"maxStandIns", "10"}, {"swapLeveled", "0"}, {"saveGraceMs", "500"}, {"deathGraceMs", "900"}}),
                         d, &notes);
    CHECK(!c.enabled && c.scanMs == 250 && c.maxScanActors == 100 && c.maxLinesPerFrame == 8 && c.driveMinMs == 50);
    CHECK(c.posEpsilonXY == 0.5f && c.posEpsilonZ == 1.5f && c.yawEpsilonDeg == 2.0f && c.pollDeathMs == 200);
    CHECK(!c.restrainNonOwned && !c.stopCombatOnRestrain && !c.startCombatOnTakeover && !c.persistLedger && !c.swapLeveled);
    CHECK(c.maxStandIns == 10 && c.saveGraceMs == 500 && c.deathGraceMs == 900);
    CHECK(notes.empty());
    c = readActorsConfig(actorsIni({{"restrain", "0"}}), d, nullptr);   // the older spelling of restrainNonOwned
    CHECK(!c.restrainNonOwned);
    // the base passed in is the starting point, not the compiled-in default
    Config base;
    base.scanMs = 777;
    base.persistLedger = false;
    c = readActorsConfig(actorsIni({{"maxStandIns", "5"}}), base, nullptr);
    CHECK(c.scanMs == 777 && !c.persistLedger && c.maxStandIns == 5);
}

TEST(actors_cfg_clamps_and_rejects_nonsense) {
    using nvcoop::actors::engine::Config;
    Config d;
    std::string notes;
    Config c = readActorsConfig(actorsIni({{"scanMs", "0"}, {"maxLinesPerFrame", "100000"}, {"maxScanActors", "-5"}, {"pollDeathMs", "1"},
                                           {"maxStandIns", "99999"}, {"posEpsilonXY", "-3"}, {"yawEpsilonDeg", "1000"}, {"driveMinMs", "-1"}}),
                                d, &notes);
    CHECK(c.scanMs == 50);
    CHECK(c.maxLinesPerFrame == 200);
    CHECK(c.maxScanActors == 16);
    CHECK(c.pollDeathMs == 16);
    CHECK(c.maxStandIns == 400);
    CHECK(c.posEpsilonXY == 0.0f);
    CHECK(c.yawEpsilonDeg == 90.0f);
    CHECK(c.driveMinMs == 0);
    CHECK(!notes.empty() && notes.find("scanMs") != std::string::npos);
    // text that is not a number keeps the default and says so
    notes.clear();
    c = readActorsConfig(actorsIni({{"scanMs", "fast"}, {"posEpsilonZ", "1.5x"}, {"saveGraceMs", ""}}), d, &notes);
    CHECK(c.scanMs == d.scanMs && c.posEpsilonZ == d.posEpsilonZ && c.saveGraceMs == d.saveGraceMs);
    CHECK(notes.find("scanMs=fast is not a number") != std::string::npos && notes.find("posEpsilonZ") != std::string::npos);
}
#endif

// =============================================================================================================================
// avatar wire
// =============================================================================================================================
TEST(wire_roster_roundtrip_and_truncation) {
    AvBytes b = encodeRoster({0, 1, 3});
    std::vector<uint8_t> s;
    CHECK(decodeRoster(b.data(), b.size(), s));
    CHECK(s == std::vector<uint8_t>({0, 1, 3}));
    for (size_t n = 0; n < b.size(); ++n) CHECK(!decodeRoster(b.data(), n, s));   // every truncation is rejected
    AvBytes extra = b;
    extra.push_back(0x55);
    CHECK(!decodeRoster(extra.data(), extra.size(), s));                          // trailing garbage is rejected
    AvBytes bad = encodeRoster({0, 9});
    CHECK(!decodeRoster(bad.data(), bad.size(), s));                              // slot outside the table
    AvBytes wrongCh = b;
    wrongCh[0] = 0xAC;
    CHECK(!decodeRoster(wrongCh.data(), wrongCh.size(), s));
}

static AvLook sampleLook(uint8_t slot) {
    AvLook l;
    l.slot = slot;
    l.appearance.race = 0x19;
    l.appearance.female = true;
    l.appearance.hair = 0x1234;
    l.appearance.eyes = 0x55;
    l.appearance.faceHash = 0xDEADBEEF;
    l.appearance.name = "Courier Six";
    l.equipment = {{0x1000, false}, {0x2000, true}, {0xFF000123, false}};
    return l;
}

TEST(wire_look_roundtrip_and_truncation) {
    AvLook l = sampleLook(2);
    AvBytes b = encodeLook(l);
    AvLook o;
    CHECK(decodeLook(b.data(), b.size(), o));
    CHECK(o == l);
    for (size_t n = 0; n < b.size(); ++n) CHECK(!decodeLook(b.data(), n, o));
    AvBytes extra = b;
    extra.push_back(0);
    CHECK(!decodeLook(extra.data(), extra.size(), o));
    AvLook bad = l;
    bad.slot = 8;   // outside the slot table
    AvBytes bb = encodeLook(bad);
    CHECK(!decodeLook(bb.data(), bb.size(), o));
    // an equipment count above the cap, with enough bytes behind it to be "valid" otherwise
    AvLook many = l;
    many.equipment.clear();
    AvBytes mb = encodeLook(many);
    mb[mb.size() - 1] = 200;   // count byte
    for (int i = 0; i < 200; ++i) { for (int k = 0; k < 4; ++k) mb.push_back(0); mb.push_back(0); }
    CHECK(!decodeLook(mb.data(), mb.size(), o));
    // empty look and a name at the limit round-trip
    AvLook e;
    e.slot = 0;
    e.appearance.name = std::string(48, 'x');
    AvBytes eb = encodeLook(e);
    CHECK(decodeLook(eb.data(), eb.size(), o) && o == e);
    // random garbage never crashes and is rejected unless it happens to be valid
    std::vector<uint8_t> sink;
    uint32_t x = 12345;
    for (int i = 0; i < 3000; ++i) {
        AvBytes g(1 + (x % 90));
        for (auto& c : g) { x = x * 1664525u + 1013904223u; c = static_cast<uint8_t>(x >> 24); }
        g[0] = kChannelAvatars;
        if (g.size() > 1) g[1] = (i & 1) ? kAvKindLook : kAvKindRoster;
        decodeLook(g.data(), g.size(), o);
        decodeRoster(g.data(), g.size(), sink);
    }
    CHECK(true);
}

TEST(wire_player_net_ids) {
    for (uint8_t s = 0; s < 8; ++s) {
        CHECK(isPlayerNetId(playerNetId(s)));
        CHECK(slotOfPlayerNetId(playerNetId(s)) == s);
    }
    CHECK(!isPlayerNetId(0x12345678));
    CHECK(!isPlayerNetId(0xFF000001));
}

// =============================================================================================================================
// avatar network service on a loopback of three machines
// =============================================================================================================================
namespace {
using nvcoop::avatar::engine::AvatarRuntime;
using nvcoop::avatar::engine::FakeGameBackend;
using nvcoop::avatar::engine::RuntimeConfig;
constexpr uint32_t kTemplate = 0x1002;
constexpr av::CellId kCell = FakeGameBackend::kExtA;

struct Node {
    fnvcoop::PeerId peer = 0;
    PlayerId slot = 0;
    bool host = false;
    bool present = true;      // the machine is connected to the session
    bool linkUp = true;
    fnvcoop::MuxTransport* mux = nullptr;
    FakeGameBackend be;
    std::unique_ptr<AvatarRuntime> rt;
    std::unique_ptr<RuntimeSide> side;
    std::unique_ptr<AvatarNet> an;
    LiveHub hub;
    uint32_t epoch = 1000;
    std::vector<std::tuple<PlayerId, bool, live::Vec3>> poses;
    float vx = 0;             // the local player walks along +x at this speed (units/s)

    Node(fnvcoop::PeerId p, PlayerId s, bool h, fnvcoop::MuxTransport* m) : peer(p), slot(s), host(h), mux(m) {
        be.knownBases = {kTemplate};
        be.vanishAfterFrames = 2;
        RuntimeConfig rc;
        rc.puppet.defaultTemplate = kTemplate;
        // localSlot stays at the ini default 0, exactly as the shipped module has it: the session start (AvatarNet::sessionBegin) must tell the runtime its slot
        rt.reset(new AvatarRuntime(be, rc));
        side.reset(new RuntimeSide(*rt));
        an.reset(new AvatarNet(*side, AvatarNetOptions{}, [this] { return epoch++; }));
        an->setPoseSink([this](PlayerId sl, bool pr, live::CellId, const live::Vec3& pos) { poses.emplace_back(sl, pr, pos); });
        hub.add(an.get(), true);
        be.Player().pos = {1000.0f * (slot + 1), 0, 0};
        be.Player().cell = kCell;
    }
    // actors that are not the player and not deleted (the puppets in this machine's world)
    int puppets() {
        std::vector<nvcoop::avatar::engine::ActorEntry> v;
        be.ListActors(kCell, v);
        int n = 0;
        for (auto& e : v) {
            auto* f = be.Get(e.ref);
            if (e.ref != nvcoop::avatar::engine::kPlayerRef && f && f->exists && !f->deleted) ++n;
        }
        return n;
    }
};

struct Rig3 {
    Net3 n;
    std::vector<std::unique_ptr<Node>> nodes;   // [0] host (peer 0), then clients (peer = slot)
    Rig3() {
        fnvcoop::LinkParams lp;
        lp.minLatencyMs = 20;
        lp.maxLatencyMs = 50;   // a decent connection: 20 to 50 ms one way, with reordering
        n.net.setLink(lp);
        nodes.emplace_back(new Node(0, 0, true, n.m(0)));
    }
    Node& host() { return *nodes[0]; }
    Node& addClient(PlayerId slot) {
        nodes.emplace_back(new Node(slot, slot, false, n.m(slot)));
        return *nodes.back();
    }
    Node* bySlot(PlayerId s) { for (auto& p : nodes) if (p->slot == s) return p.get(); return nullptr; }
    void step(uint64_t dtMs = 33) {
        n.now += dtMs;
        n.pump();
        std::vector<ClientLink> links;
        for (auto& p : nodes)
            if (!p->host) links.push_back(cl(p->slot, p->peer, p->present));
        for (auto& p : nodes) {
            if (p->vx != 0) p->be.Player().pos.x += p->vx * float(dtMs) / 1000.0f;
            SessionView v;
            if (p->host) {
                v = hostView(p->mux, links, n.now);
            } else {
                v = clientView(p->mux, p->slot, p->linkUp, n.now);
                v.active = p->present;
            }
            p->hub.frame(v);
            p->be.Frame();
        }
    }
    void run(uint64_t ms) { for (uint64_t t = 0; t < ms; t += 33) step(); }
};

float xOfPuppet(Node& viewer, PlayerId slot, bool* ok = nullptr) {
    av::RefId r = viewer.an->puppetRef(slot);
    const auto* f = r ? viewer.be.Get(r) : nullptr;
    if (ok) *ok = f != nullptr && f->exists;
    return f ? f->pos.x : -1e9f;
}
float playerX(Node& n) { return n.be.Player().pos.x; }
}  // namespace

TEST(avatars_three_machines_see_each_other) {
    Rig3 r;
    Node& h = r.host();
    Node& a = r.addClient(1);
    Node& b = r.addClient(2);
    a.present = true;
    b.present = false;   // B joins later
    a.vx = 150;
    h.vx = 0;
    r.run(2500);
    CHECK(h.an->roster() == std::set<PlayerId>({1}));
    CHECK(a.an->roster() == std::set<PlayerId>({0}));   // A sees the host (the roster lists the host and A; A filters itself)
    bool ok = false;
    float ax = xOfPuppet(h, 1, &ok);
    CHECK(ok);
    CHECK(std::abs(ax - playerX(a)) < 150.0f);          // within the interpolation delay of a 150 u/s walker
    float hx = xOfPuppet(a, 0, &ok);
    CHECK(ok && std::abs(hx - playerX(h)) < 30.0f);
    // B joins: everybody gets a puppet of everybody else, B included, through the host's relay
    b.present = true;
    b.vx = -100;
    r.run(3500);
    CHECK(h.an->roster() == std::set<PlayerId>({1, 2}));
    CHECK(a.an->roster() == std::set<PlayerId>({0, 2}));
    CHECK(b.an->roster() == std::set<PlayerId>({0, 1}));
    CHECK(h.puppets() == 2 && a.puppets() == 2 && b.puppets() == 2);
    float bxAtA = xOfPuppet(a, 2, &ok);
    CHECK(ok && std::abs(bxAtA - playerX(b)) < 150.0f);   // A sees B only through the host (relay)
    float axAtB = xOfPuppet(b, 1, &ok);
    CHECK(ok && std::abs(axAtB - playerX(a)) < 180.0f);
    CHECK(h.an->stats().forged == 0 && a.an->stats().forged == 0);
    CHECK(a.an->puppetRef(a.slot) == 0 && b.an->puppetRef(b.slot) == 0);
}

TEST(avatars_never_a_puppet_of_oneself) {
    Rig3 r;
    r.addClient(1);
    r.run(2000);
    CHECK(r.bySlot(1)->an->roster().count(1) == 0);
    CHECK(r.bySlot(1)->puppets() == 1);   // just the host's puppet
    CHECK(r.host().puppets() == 1);
    CHECK(r.bySlot(1)->an->puppetRef(1) == 0);
}

TEST(avatars_leave_removes_puppets_everywhere) {
    Rig3 r;
    Node& a = r.addClient(1);
    Node& b = r.addClient(2);
    a.vx = 50;
    b.vx = 50;
    r.run(3000);
    CHECK(r.host().puppets() == 2 && a.puppets() == 2);
    b.present = false;   // B quits
    r.run(1500);
    CHECK(r.host().an->roster() == std::set<PlayerId>({1}));
    CHECK(r.host().puppets() == 1);
    CHECK(a.an->roster() == std::set<PlayerId>({0}));
    CHECK(a.puppets() == 1);
    CHECK(b.puppets() == 0);   // B's own session ended: its puppets were taken down
    CHECK(b.an->roster().empty());
}

TEST(avatars_rejoin_follows_again) {
    Rig3 r;
    Node& a = r.addClient(1);
    a.vx = 100;
    r.run(2500);
    CHECK(r.host().puppets() == 1);
    a.present = false;
    r.run(1000);
    CHECK(r.host().puppets() == 0);
    a.vx = 0;
    a.be.Player().pos.x = 50000;   // reconnects somewhere else (same cell here)
    a.present = true;
    r.run(3000);
    bool ok = false;
    float x = xOfPuppet(r.host(), 1, &ok);
    CHECK(ok && std::abs(x - playerX(a)) < 300.0f);
}

TEST(avatars_forged_stream_is_rejected_at_the_host) {
    Rig3 r;
    Node& a = r.addClient(1);
    Node& b = r.addClient(2);
    b.vx = 30;
    r.run(2500);
    bool ok = false;
    float before = xOfPuppet(r.host(), 2, &ok);
    CHECK(ok);
    // client A publishes a snapshot that claims to be B's avatar, far away
    live::SnapshotSender evil;
    live::ActorSnapshot s;
    s.netId = playerNetId(2);
    s.ownerEpoch = 999999999u;   // an epoch that beats B's own
    s.seq = 1; s.tick = 5; s.cell = kCell;
    s.pos = {90000, 0, 0};
    evil.queue(0, s, 5);
    for (auto& d : evil.flush(0, 5)) {
        Bytes pk;
        pk.push_back(kChannelAvatars);
        pk.insert(pk.end(), d.begin(), d.end());
        a.mux->sendEx(0, pk, fnvcoop::ITransport::Channel::Unreliable);
    }
    r.run(900);
    CHECK(r.host().an->stats().forged >= 1);
    float after = xOfPuppet(r.host(), 2);
    CHECK(std::abs(after - playerX(b)) < 200.0f);     // still following the real B
    CHECK(std::abs(after - 90000.0f) > 1000.0f);
    CHECK(after >= before - 1.0f);
    // the same forgery with a Look message
    AvLook l = sampleLook(2);
    l.equipment = {{0x7777, false}};
    a.mux->sendEx(0, encodeLook(l), fnvcoop::ITransport::Channel::Reliable);
    r.run(300);
    CHECK(r.host().an->stats().looksRejected >= 1);
    const AvLook* real = r.host().an->lookOf(2);   // B's own (legitimate) look, if it has sent one: never the forged one
    bool forgedApplied = false;
    if (real)
        for (auto& e : real->equipment)
            if (e.form == 0x7777) forgedApplied = true;
    CHECK(!forgedApplied);
}

TEST(avatars_look_and_equipment_travel) {
    Rig3 r;
    Node& a = r.addClient(1);
    Node& b = r.addClient(2);
    a.be.Player().worn.insert(0x2000);
    a.be.Player().inv[0x2000] = 1;
    a.an->setLocalAppearanceReader([](av::Appearance& ap) { ap.race = 1; ap.name = "Alpha"; return true; });
    b.an->setLocalAppearanceReader([](av::Appearance& ap) { ap.race = 1; ap.name = "Beta"; return true; });
    r.run(5000);
    CHECK(r.host().an->hasLook(1) && r.host().an->hasLook(2));
    CHECK(b.an->hasLook(1));   // relayed through the host
    CHECK(r.host().an->stats().looksApplied >= 1 && b.an->stats().looksApplied >= 1);
    av::RefId ref = r.host().an->puppetRef(1);
    const auto* f = ref ? r.host().be.Get(ref) : nullptr;
    CHECK(f && f->worn.count(0x2000) == 1);
    av::RefId refAtB = b.an->puppetRef(1);
    const auto* fb = refAtB ? b.be.Get(refAtB) : nullptr;
    CHECK(fb && fb->worn.count(0x2000) == 1);
    // a change travels too
    a.be.Player().worn.erase(0x2000);
    a.be.Player().worn.insert(0x2001);
    a.be.Player().inv[0x2001] = 1;
    r.run(5000);
    f = r.host().be.Get(r.host().an->puppetRef(1));
    CHECK(f && f->worn.count(0x2001) == 1 && f->worn.count(0x2000) == 0);
}

TEST(avatars_unreadable_local_player_sends_nothing) {
    Rig3 r;
    Node& a = r.addClient(1);
    a.be.Player().exists = false;   // the player cannot be read (main menu / loading)
    r.run(1500);
    CHECK(a.an->stats().localSnapshots == 0);
    CHECK(r.host().an->stats().accepted == 0);
    a.be.Player().exists = true;
    r.run(2000);
    CHECK(a.an->stats().localSnapshots > 0 && r.host().an->stats().accepted > 0);
}

TEST(avatars_pose_sink_reports_remote_players) {
    Rig3 r;
    Node& a = r.addClient(1);
    a.vx = 40;
    r.run(2500);
    bool saw = false;
    for (auto& p : r.host().poses)
        if (std::get<0>(p) == 1 && std::get<1>(p)) saw = true;
    CHECK(saw);
    bool sawSelf = false, sawClientSelf = false;   // the local player's own place is reported too (the spawn service wants every slot)
    for (auto& p : r.host().poses)
        if (std::get<0>(p) == 0 && std::get<1>(p)) sawSelf = true;
    for (auto& p : a.poses)
        if (std::get<0>(p) == 1 && std::get<1>(p)) sawClientSelf = true;
    CHECK(sawSelf && sawClientSelf);
    size_t n = r.host().poses.size();
    a.present = false;
    r.run(800);
    bool gone = false;
    for (size_t i = n; i < r.host().poses.size(); ++i)
        if (std::get<0>(r.host().poses[i]) == 1 && !std::get<1>(r.host().poses[i])) gone = true;
    CHECK(gone);   // the ownership service is told the player is no longer there
}

TEST(avatars_load_purges_orphans_without_a_session) {
    Net3 n;
    Node h(0, 0, true, n.m(0));
    h.be.AddActor(kTemplate, kCell, nvcoop::avatar::Vec3{});   // a save carried puppets of an old session
    h.be.AddActor(kTemplate, kCell, nvcoop::avatar::Vec3{});
    CHECK(h.puppets() == 2);
    SessionView idle;
    idle.nowMs = 10;
    h.hub.onLoadBegin();
    h.hub.onLoadEnd();
    for (int i = 0; i < 6; ++i) { idle.nowMs += 33; h.hub.frame(idle); h.be.Frame(); }
    CHECK(h.puppets() == 0);
    // a second load purges again
    h.be.AddActor(kTemplate, kCell, nvcoop::avatar::Vec3{});
    h.hub.onLoadBegin();
    h.hub.onLoadEnd();
    for (int i = 0; i < 6; ++i) { idle.nowMs += 33; h.hub.frame(idle); h.be.Frame(); }
    CHECK(h.puppets() == 0);
}

TEST(avatars_load_inside_a_session_drops_and_respawns) {
    Rig3 r;
    Node& a = r.addClient(1);
    a.vx = 20;
    r.run(2500);
    CHECK(r.host().puppets() == 1);
    // the host loads a save: the world is torn down (the engine forgets its refs), the save also carried the puppet
    r.host().hub.onLoadBegin();
    r.host().be.Load();
    r.host().hub.onLoadEnd();
    r.run(3000);
    CHECK(r.host().puppets() == 1);   // exactly one puppet again: the carried one was purged, the real one respawned
    bool ok = false;
    float x = xOfPuppet(r.host(), 1, &ok);
    CHECK(ok && std::abs(x - playerX(a)) < 200.0f);
}

TEST(avatars_session_end_takes_puppets_down) {
    Rig3 r;
    Node& a = r.addClient(1);
    a.vx = 10;
    r.run(2500);
    CHECK(a.puppets() == 1);
    a.linkUp = false;
    r.run(600);
    CHECK(a.puppets() == 0);   // the roster is gone with the host link
    a.linkUp = true;
    r.run(2500);
    CHECK(a.puppets() == 1);   // and comes back with it
}

TEST(avatars_ghost_ref_map) {
    Rig3 r;
    r.addClient(1);
    r.run(3000);
    av::RefId ref = r.host().an->puppetRef(1);
    CHECK(ref != 0);
    PlayerId slot = 99;
    CHECK(r.host().an->slotOfPuppetRef(ref, slot) && slot == 1);
    CHECK(!r.host().an->slotOfPuppetRef(0, slot));
    CHECK(!r.host().an->slotOfPuppetRef(ref + 1000, slot));
    CHECK(r.host().an->puppetRef(5) == 0);
    std::string j = r.host().an->statusJson();
    CHECK(j.find("\"slot\":1") != std::string::npos);
}

int main() {
    std::setvbuf(stdout, nullptr, _IONBF, 0);
    run("switches_default_off", switches_default_off);
    run("switches_parse_values", switches_parse_values);
    run("switches_dependencies", switches_dependencies);
    run("switches_social", switches_social);
    run("hub_disabled_service_is_never_called", hub_disabled_service_is_never_called);
    run("hub_session_lifecycle_host", hub_session_lifecycle_host);
    run("hub_existing_clients_are_announced_after_begin", hub_existing_clients_are_announced_after_begin);
    run("hub_client_link_events", hub_client_link_events);
    run("hub_new_mux_is_a_new_session_and_old_handlers_are_inert", hub_new_mux_is_a_new_session_and_old_handlers_are_inert);
    run("hub_routing_and_channel_ownership", hub_routing_and_channel_ownership);
    run("hub_send_paths", hub_send_paths);
    run("hub_loads_and_idle", hub_loads_and_idle);
    run("hub_client_reslot_is_a_new_session", hub_client_reslot_is_a_new_session);
    run("hub_a_throwing_service_is_switched_off_and_the_others_go_on", hub_a_throwing_service_is_switched_off_and_the_others_go_on);
    run("hub_status_json", hub_status_json);
#ifdef NVLIVE_HAVE_ACTOR_API
    run("actors_cfg_defaults_and_overrides", actors_cfg_defaults_and_overrides);
    run("actors_cfg_clamps_and_rejects_nonsense", actors_cfg_clamps_and_rejects_nonsense);
#endif
    run("wire_roster_roundtrip_and_truncation", wire_roster_roundtrip_and_truncation);
    run("wire_look_roundtrip_and_truncation", wire_look_roundtrip_and_truncation);
    run("wire_player_net_ids", wire_player_net_ids);
    run("avatars_three_machines_see_each_other", avatars_three_machines_see_each_other);
    run("avatars_never_a_puppet_of_oneself", avatars_never_a_puppet_of_oneself);
    run("avatars_leave_removes_puppets_everywhere", avatars_leave_removes_puppets_everywhere);
    run("avatars_rejoin_follows_again", avatars_rejoin_follows_again);
    run("avatars_forged_stream_is_rejected_at_the_host", avatars_forged_stream_is_rejected_at_the_host);
    run("avatars_look_and_equipment_travel", avatars_look_and_equipment_travel);
    run("avatars_unreadable_local_player_sends_nothing", avatars_unreadable_local_player_sends_nothing);
    run("avatars_pose_sink_reports_remote_players", avatars_pose_sink_reports_remote_players);
    run("avatars_load_purges_orphans_without_a_session", avatars_load_purges_orphans_without_a_session);
    run("avatars_load_inside_a_session_drops_and_respawns", avatars_load_inside_a_session_drops_and_respawns);
    run("avatars_session_end_takes_puppets_down", avatars_session_end_takes_puppets_down);
    run("avatars_ghost_ref_map", avatars_ghost_ref_map);
    std::printf("%d checks, %d failed\n", g_checks, g_fail);
    return g_fail ? 1 : 0;
}
