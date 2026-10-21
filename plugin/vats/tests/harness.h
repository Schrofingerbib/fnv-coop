// Test harness for plugin/vats: a scripted fake VATS engine and a cluster of machines (star topology, machine 0 is the host) on an in-memory network.
#pragma once
#include <algorithm>
#include <cstdio>
#include <deque>
#include <functional>
#include <map>
#include <memory>
#include <set>
#include <string>
#include "minitest.h"
#include "vats/vats_sync.h"

namespace vtest {
using namespace nvcoop::vats;

// A target with the usual humanoid parts. Part records are fake addresses: ref * 16 + index.
inline TargetInfo humanoid(uint32_t ref, bool withHead = true) {
    TargetInfo t;
    t.actorRef = ref;
    t.rec = ref * 256;
    t.kind = 0;
    struct P { LimbId l; int body; };
    const P ps[] = {{LimbId::Torso, 0}, {LimbId::Head, 1}, {LimbId::LeftArm, 3}, {LimbId::RightArm, 5}, {LimbId::LeftLeg, 7}, {LimbId::RightLeg, 9}};
    int i = 0;
    for (const P& p : ps) {
        if (!withHead && p.l == LimbId::Head) continue;
        PartInfo pi;
        pi.rec = ref * 16 + static_cast<uint32_t>(i++);
        pi.bodyIndex = p.body;
        pi.limb = p.l;
        t.parts.push_back(pi);
    }
    return t;
}

struct FakeEngine : IVatsEngine {
    // ---- state ----
    Mode m = Mode::None;
    bool menuOpen = false;
    uint32_t selected = 0, hl = 0;
    std::vector<TargetInfo> tg;
    LimbId remembered = LimbId::None;
    int queued = 0;
    float scaleCur = 1.0f, scaleTgt = 1.0f;
    uint32_t player = 0x14;
    std::map<uint32_t, uint32_t> netIds;
    int ap = 60, maxAp = 100;
    std::set<std::string> perks;
    std::set<uint32_t> party;
    // ---- knobs ----
    bool refuseCommit = false, revertCommit = false, refuseAccept = false, cycleNoMove = false, refuseSetScale = false, refuseUnclick = false;
    bool acceptStartsPlayback = true;
    // ---- recordings ----
    std::vector<std::pair<float, bool>> scaleCalls;
    int accepts = 0, unclicks = 0, cycles = 0, commits = 0, apRestoredTotal = 0, cameraCalls = 0;
    bool cameraShort = false;

    // ---- helpers ----
    void enter(const std::vector<TargetInfo>& targets, uint32_t sel, Mode mode = Mode::TargetSelect) {
        tg = targets;
        m = mode;
        menuOpen = true;
        select(sel);
    }
    void select(uint32_t ref) {
        selected = ref;
        hl = 0;
        for (const TargetInfo& t : tg)
            if (t.actorRef == ref) {
                for (const PartInfo& p : t.parts) if (p.limb == LimbId::Torso) hl = p.rec;
                if (!hl && !t.parts.empty()) hl = t.parts.front().rec;
                remembered = LimbId::Torso;
            }
    }
    void leave() { m = Mode::None; menuOpen = false; selected = 0; hl = 0; queued = 0; tg.clear(); }
    void queueAction() { ++queued; }

    // ---- IVatsEngine ----
    Mode mode() override { return m; }
    bool vatsMenuOpen() override { return menuOpen; }
    uint32_t selectedTargetRef() override { return selected; }
    std::vector<TargetInfo> targets() override { return tg; }
    uint32_t highlightedPartRec() override { return hl; }
    LimbId rememberedLimb() override { return remembered; }
    int queuedCount() override { return queued; }
    float timeScaleCurrent() override { return scaleCur; }
    float timeScaleTarget() override { return scaleTgt; }
    uint32_t localPlayerRef() override { return player; }
    uint32_t netIdOfRef(uint32_t ref) override { auto it = netIds.find(ref); return it == netIds.end() ? 0 : it->second; }
    int actionPoints() override { return ap; }
    int maxActionPoints() override { return maxAp; }
    bool playerHasPerk(const std::string& id) override { return perks.count(id) != 0; }
    bool isPartyRef(uint32_t ref) override { return party.count(ref) != 0; }
    bool commitHighlight(uint32_t partRec, LimbId limb) override {
        ++commits;
        if (refuseCommit) return false;
        if (revertCommit) return true;
        hl = partRec;
        remembered = limb;
        return true;
    }
    bool cycleTarget(bool forward) override {
        ++cycles;
        if (cycleNoMove) return true;
        if (tg.size() < 2) return false;
        size_t i = 0;
        for (; i < tg.size(); ++i) if (tg[i].actorRef == selected) break;
        i = forward ? (i + 1) % tg.size() : (i + tg.size() - 1) % tg.size();
        select(tg[i].actorRef);
        return true;
    }
    bool accept() override {
        ++accepts;
        if (refuseAccept) return false;
        if (acceptStartsPlayback) m = Mode::Playback;
        return true;
    }
    bool unclickLast() override {
        ++unclicks;
        if (refuseUnclick) return false;
        if (queued > 0) --queued;
        return true;
    }
    bool setTimeScale(float scale, bool immediate) override {
        scaleCalls.push_back({scale, immediate});
        if (refuseSetScale) return false;
        scaleTgt = scale;
        scaleCur = scale;
        return true;
    }
    bool restoreActionPoints(int amount) override {
        apRestoredTotal += amount;
        ap = std::min(maxAp, ap + amount);
        return true;
    }
    bool applyCameraTimes(bool shortened) override { ++cameraCalls; cameraShort = shortened; return true; }
};

// An in-memory star network: delivery is explicit (pump), so a test controls ordering and loss.
struct Wire {
    struct Msg { PlayerId from, to; Bytes data; };
    std::deque<Msg> q;
    std::vector<Msg> log;
    bool drop = false;
};

struct Node;
struct NodeNet : IVatsNet {
    Wire* wire = nullptr;
    PlayerId me = kNoPlayer;
    bool host = false;
    std::vector<PlayerId> clients;   // host only: joined slots
    void send(PlayerId peer, Bytes data) override {
        if (wire->drop) return;
        if (!host) { wire->q.push_back({me, kHostPlayer, std::move(data)}); wire->log.push_back(wire->q.back()); return; }
        if (peer == kNoPlayer) {
            for (PlayerId c : clients) { wire->q.push_back({me, c, data}); wire->log.push_back(wire->q.back()); }
            return;
        }
        wire->q.push_back({me, peer, std::move(data)});
        wire->log.push_back(wire->q.back());
    }
};

struct Node {
    FakeEngine eng;
    NodeNet net;
    std::unique_ptr<VatsSync> sync;
};

struct Cluster {
    Wire wire;
    std::vector<std::unique_ptr<Node>> nodes;
    uint64_t now = 10000;

    explicit Cluster(int n, Settings hostSettings = Settings{}, const PerkSettings& perks = PerkSettings{}, const std::function<void(int, Config&)>& tweak = nullptr) {
        for (int i = 0; i < n; ++i) {
            auto nd = std::make_unique<Node>();
            nd->net.wire = &wire;
            nd->net.me = static_cast<PlayerId>(i);
            nd->net.host = i == 0;
            Config c;
            c.settings = i == 0 ? hostSettings : Settings{};   // a client starts with DEFAULT settings, the host's arrive over the wire
            c.perks = perks;
            c.self = static_cast<PlayerId>(i);
            c.isHost = i == 0;
            c.epoch = 7;
            if (tweak) tweak(i, c);
            nd->sync.reset(new VatsSync(c, nd->eng, nd->net));
            nodes.push_back(std::move(nd));
        }
        for (int i = 1; i < n; ++i) {
            nodes[0]->net.clients.push_back(static_cast<PlayerId>(i));
            nodes[0]->sync->playerJoined(static_cast<PlayerId>(i), now);
        }
        pump();
    }
    Node& host() { return *nodes[0]; }
    Node& at(int i) { return *nodes[static_cast<size_t>(i)]; }
    void pump() {
        for (int guard = 0; guard < 1000 && !wire.q.empty(); ++guard) {
            Wire::Msg m = std::move(wire.q.front());
            wire.q.pop_front();
            nodes[m.to]->sync->receive(m.from, m.data.data(), m.data.size(), now);
        }
    }
    void tickAll() { for (auto& n : nodes) n->sync->tick(now); pump(); }
    void advance(uint64_t ms, uint64_t step = 50) {
        for (uint64_t t = 0; t < ms; t += step) { now += step; tickAll(); }
    }
    size_t sentOfKind(Kind k) const {
        size_t n = 0;
        for (const auto& m : wire.log) {
            Envelope e;
            if (decodeEnvelope(m.data.data(), m.data.size(), e) && kindOf(e.body) == k) ++n;
        }
        return n;
    }
};

}  // namespace vtest
