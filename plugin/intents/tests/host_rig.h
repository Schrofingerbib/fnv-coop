// Shared rig for the intents tests: a host (IntentHost over a FakeEngine + a real CaptureHub), a fake Runtime::runAsGuest that forces the txn
// origin the way the real one does, and loopback helpers that carry datagrams between a host and any number of IntentClients.
#pragma once
#include <functional>
#include <memory>
#include <set>
#include <tuple>

#include "intents/intent_client.h"
#include "intents/intent_host.h"
#include "testing/fake_engine.h"

namespace nvcoop::intents::testing {

inline FormRef R(uint32_t id, const char* plugin = "falloutnv.esm") { return FormRef(plugin, id); }

inline Config testConfig() {
    Config c;
    c.enable = true;
    c.rateBurst = 1000;       // the rate tests set their own
    c.ratePerSec = 1000.f;
    return c;
}

struct HostRig {
    struct Commit { PlayerId origin; fnvcoop::EffectTxn txn; };

    nvhooks::CaptureHub hub;
    FakeEngine eng;
    std::vector<Commit> commits;
    PlayerId forced = kNoPlayer;
    int windows = 0;
    std::vector<std::pair<PlayerId, Bytes>> sent;
    std::set<PlayerId> joined{1, 2};
    std::map<PlayerId, RefId> puppets{{1, 0x1001}, {2, 0x1002}};
    std::map<PlayerId, RefId> ghosts;
    std::vector<std::tuple<PlayerId, ActorMode, RefId>> modes;                              // HostDeps::actorMode reports (the module logs them)
    std::vector<std::pair<FormRef, PlayerId>> dialogues;
    std::function<bool(PlayerId, const FormRef&)> allowActivation;                         // plugin/companions question (empty = allow)
    std::vector<std::tuple<PlayerId, bool, FormRef>> interactions;                           // plugin/companions report: (slot, pickup, target)
    uint64_t now = 100000;
    Config cfg;
    std::unique_ptr<IntentHost> host;

    explicit HostRig(Config c = testConfig()) : cfg(std::move(c)) {
        hub.setEnabled(true);
        hub.addSink([this](fnvcoop::EffectTxn&& t) { commits.push_back({forced, std::move(t)}); });
        eng.hub = &hub;
        build();
    }

    void build(ContainerRedirect redirect = ContainerRedirect()) {
        HostDeps d;
        d.eng = &eng;
        d.nowMs = [this] { return now; };
        d.send = [this](PlayerId slot, Bytes b) { sent.emplace_back(slot, std::move(b)); return true; };
        d.runAsGuest = [this](PlayerId slot, const std::function<void()>& body) {
            PlayerId prev = forced;
            forced = slot;
            ++windows;
            body();
            forced = prev;
            return true;
        };
        d.puppetOf = [this](PlayerId s) { auto it = puppets.find(s); return it == puppets.end() ? RefId(0) : it->second; };
        d.ghostOf = [this](PlayerId s) { auto it = ghosts.find(s); return it == ghosts.end() ? RefId(0) : it->second; };
        d.actorMode = [this](PlayerId s, ActorMode m, RefId a) { modes.emplace_back(s, m, a); };
        d.joined = [this](PlayerId s) { return joined.count(s) != 0; };
        d.dialogueStart = [this](const FormRef& n, PlayerId s) { dialogues.emplace_back(n, s); };
        d.allowActivation = [this](PlayerId s, const FormRef& t) { return !allowActivation || allowActivation(s, t); };
        d.interacted = [this](PlayerId s, bool pickup, const FormRef& t) { interactions.emplace_back(s, pickup, t); };
        host.reset(new IntentHost(std::move(d), cfg, std::move(redirect)));
    }

    void add(uint32_t id, TargetClass cls, float x = 0, float y = 0, float z = 0) { eng.world[R(id)] = mkTarget(cls, id, x, y, z); }
    void item(uint32_t id, uint32_t baseId, int count = 1, float x = 0) {
        TargetInfo t = mkTarget(TargetClass::Item, id, x);
        t.base = R(baseId);
        t.count = count;
        eng.world[R(id)] = t;
    }

    static Intent mk(Kind k, const FormRef& target, uint32_t id, uint32_t session = 1) {
        Intent in;
        in.session = session;
        in.id = id;
        in.kind = k;
        in.target = target;
        return in;
    }
    Verdict run(PlayerId slot, Kind k, const FormRef& target, uint32_t id, uint32_t session = 1) { return host->handle(slot, mk(k, target, id, session)); }

    template <class E> size_t effects() const {
        size_t n = 0;
        for (const auto& c : commits) for (const auto& e : c.txn.effects) n += std::holds_alternative<E>(e);
        return n;
    }
    const Commit* lastCommit() const { return commits.empty() ? nullptr : &commits.back(); }
};

// Loopback between one host rig and any number of clients: pump() moves every queued datagram both ways until nothing is in flight.
struct Loop {
    HostRig& h;
    struct Guest {
        PlayerId slot;
        FakeEngine eng;
        std::vector<Bytes> toHost;
        std::vector<Bytes> fromHost;
        std::unique_ptr<IntentClient> client;
        bool ready = true;
        uint32_t session = 7;
        uint64_t now = 100000;
        bool menuOpen = false;
        std::vector<FormRef> dialogues;
    };
    std::vector<std::unique_ptr<Guest>> guests;

    explicit Loop(HostRig& rig) : h(rig) {}

    Guest& addGuest(PlayerId slot, const Config& cfg) {
        guests.emplace_back(new Guest());
        Guest& g = *guests.back();
        g.slot = slot;
        g.eng.world = h.eng.world;   // the guest sees the same references (copied; tests change both when they must)
        ClientDeps d;
        d.eng = &g.eng;
        Guest* gp = &g;
        d.sendToHost = [gp](Bytes b) { gp->toHost.push_back(std::move(b)); return true; };
        d.ready = [gp] { return gp->ready; };
        d.session = [gp] { return gp->session; };
        d.nowMs = [gp] { return gp->now; };
        d.startDialogue = [gp](const FormRef& n) { gp->dialogues.push_back(n); };
        d.containerMenuOpen = [gp] { return gp->menuOpen; };
        g.client.reset(new IntentClient(std::move(d), cfg));
        return g;
    }

    void pump() {
        for (int round = 0; round < 8; ++round) {
            bool any = false;
            for (auto& g : guests)
                while (!g->toHost.empty()) {
                    any = true;
                    Bytes b = std::move(g->toHost.front());
                    g->toHost.erase(g->toHost.begin());
                    h.host->onDatagram(g->slot, b.data(), b.size());
                }
            for (auto& s : h.sent) {
                for (auto& g : guests)
                    if (g->slot == s.first) { any = true; g->client->onDatagram(s.second.data(), s.second.size()); }
            }
            h.sent.clear();
            if (!any) break;
        }
    }
    void advance(uint64_t ms) {
        h.now += ms;
        for (auto& g : guests) g->now += ms;
    }
};

}  // namespace nvcoop::intents::testing
