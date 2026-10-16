// Shared rig for the rejoin / late-join / dialogue-over-session tests: one HostSession and N ClientSessions on a lossy story
// network, plus an optional second (reliable, ordered) network carrying the 0xD1 dialogue channel.
#pragma once
#include <map>
#include <memory>
#include "fnvcoop/rules.h"
#include "fnvcoop/session.h"
#include "fnvcoop_dlg/adapter.h"

namespace rejoin {
using namespace fnvcoop;
namespace dlg = fnvcoop_dlg;

inline uint32_t h32(const std::string& s) {
    uint32_t h = 2166136261u;
    for (char c : s) { h ^= uint8_t(c); h *= 16777619u; }
    return h;
}
inline bool resolveEdid(const std::string& edid, FormRef& out) { out = FormRef("falloutnv.esm", h32(edid)); return true; }
inline FormRef QF(const char* edid) { FormRef f; resolveEdid(edid, f); return f; }
inline FormRef Q(uint32_t i) { return FormRef("falloutnv.esm", i); }

inline ShadowState world(int n, int salt = 0) {
    ShadowState s;
    for (int i = 0; i < n; ++i) {
        s.stages[Q(0x1000 + i)] = uint32_t((i * 7 + salt) % 100);
        s.scriptVars[{Q(0x1000 + i), uint16_t(i % 4)}] = double((i + salt) % 50);
        s.globals[Q(0x3000 + i)] = float(i + salt);
    }
    return s;
}

struct Rec : IEffectSink {
    std::map<uint32_t, int> items;     // live AddItem deliveries per item id
    std::vector<uint8_t> tags;
    std::vector<std::string> trail;   // debugging: where each AddItem came from
    PlayerId slot = kNoPlayer;
    void apply(const Effect& e, const Context& c) override {
        if (c.baseline) return;
        tags.push_back(uint8_t(e.index()));
        if (auto* a = std::get_if<AddItem>(&e)) { items[a->item.localId] += a->count; trail.push_back(std::to_string(a->item.localId) + "@seq" + std::to_string(c.seq) + "/ep" + std::to_string(c.epoch) + (c.catchUp ? "/catchup" : "") + (c.fromSnapshot ? "/snap" : "")); }
    }
    void onJoined(PlayerId s, const SharePolicy&) override { slot = s; }
    int count(size_t tag) const { int n = 0; for (auto t : tags) n += t == tag; return n; }
};

struct Rig {
    InMemoryNetwork story, dnet;
    HostSession host;
    struct C {
        Rec sink; EffectCapture cap; ShadowState dump; PeerId peer = 0; PlayerId slot = 0; bool dlgIn = false;
        std::unique_ptr<ClientSession> cs;
        std::unique_ptr<dlg::DialogueNode> dn;
        std::vector<dlg::Event> ev;
        std::vector<uint64_t> appliedAtTopics;     // cs->applied() whenever TopicsReady fired
    };
    std::vector<std::unique_ptr<C>> cl;
    std::unique_ptr<dlg::DialogueNode> hostDn;
    std::vector<dlg::Event> hostEv;
    RulesEngine* engine = nullptr;
    uint64_t now = 0;
    uint32_t storyLatMin = 20, storyLatMax = 60;
    std::vector<std::pair<uint32_t, uint64_t>> rewardSeq;   // (item, seq)
    std::vector<uint64_t> dlgRuns;                          // seq returned by the dialogue ResultRunner, in call order
    std::vector<std::pair<dlg::ConvId, dlg::Phase>> dlgRunKeys;
    bool withDialogue = false;

    explicit Rig(uint64_t seed, bool dialogue = false, uint32_t dlgLatency = 40)
        : story(seed * 7 + 1), dnet(seed * 7 + 2), host(*story.endpoint(0)), withDialogue(dialogue) {
        LinkParams lp; lp.minLatencyMs = storyLatMin; lp.maxLatencyMs = storyLatMax; lp.loss = 0.03; lp.duplicate = 0.01;
        story.setLink(lp);
        LinkParams dp; dp.minLatencyMs = dp.maxLatencyMs = dlgLatency; dp.duplicate = 0.02;   // reliable + ordered, like ENet channel 0
        dnet.setLink(dp);
        host.setWorldBaseline(world(20), 5);
        if (dialogue) {
            dlg::DialogueNode::Config c; c.self = 0; c.isHost = true;
            c.peerOfSlot = [](PlayerId p) { return PeerId(p); };
            c.slotOfPeer = [](PeerId p) { return PlayerId(p); };
            hostDn.reset(new dlg::DialogueNode(dnet.endpoint(0), c, [this](const dlg::ResultRequest& rq) {
                Cause cs; cs.kind = CauseKind::DialogueResult; cs.source = rq.info;
                uint64_t seq = host.commit(rq.initiator, cs, {AddItem{Q(0x5000 + rq.step * 2 + (rq.phase == dlg::Phase::End)), 1, Recipient::all()}});
                dlgRuns.push_back(seq); dlgRunKeys.push_back({rq.conv, rq.phase});
                return seq;
            }, [this](const dlg::Event& e) { hostEv.push_back(e); }));
        }
    }
    C& addClient(uint64_t token) {
        auto c = std::make_unique<C>();
        c->peer = PeerId(cl.size() + 1);
        c->slot = PlayerId(cl.size() + 1);
        c->dump = world(20, int(token) * 3);
        C* raw = c.get();
        c->cs = std::make_unique<ClientSession>(*story.endpoint(c->peer), 0, token, c->sink, &c->cap);
        c->cs->setBaselineProvider([raw](ShadowState& out, uint64_t& h) { out = raw->dump; h = 5; return true; });
        if (withDialogue) {
            dlg::DialogueNode::Config dc; dc.self = c->slot; dc.isHost = false; dc.hostPeer = 0;
            dc.appliedSeq = [raw] { return raw->cs->applied(); };
            c->dn.reset(new dlg::DialogueNode(dnet.endpoint(c->peer), dc, nullptr, [this, raw](const dlg::Event& e) {
                raw->ev.push_back(e);
                if (e.kind == dlg::Event::TopicsReady) raw->appliedAtTopics.push_back(raw->cs->applied());
            }));
        }
        cl.push_back(std::move(c));
        return *cl.back();
    }
    void setStoryLatency(uint32_t lo, uint32_t hi, double loss = 0.03) {
        LinkParams lp; lp.minLatencyMs = lo; lp.maxLatencyMs = hi; lp.loss = loss; lp.duplicate = 0.01;
        story.setLink(lp);
    }
    ShadowState hostWorld() const { return host.world(); }
    void join(size_t i, const ShadowState* dump = nullptr) {
        C& c = *cl[i];
        if (dump) c.dump = *dump;
        c.cs->setBaselineWorld(c.dump, 5);
        c.cs->connect(now, c.cs->lastSession() + 1);
    }
    void joinOnly(size_t i) { join(i); }
    void joinAll() { for (size_t i = 0; i < cl.size(); ++i) join(i); }
    // transport lost the peer (ENet Disconnected): story and dialogue both notice
    void drop(size_t i) {
        C& c = *cl[i];
        story.setConnected(c.peer, false);
        dnet.setConnected(c.peer, false);
        host.peerDisconnected(c.peer);
        if (hostDn && c.dlgIn) { c.dlgIn = false; hostDn->playerLeft(c.slot, uint32_t(now)); }
        c.cs->disconnect();
    }
    void rejoin(size_t i, const ShadowState& dump) {
        C& c = *cl[i];
        story.setConnected(c.peer, true);
        dnet.setConnected(c.peer, true);
        join(i, &dump);
    }
    // same process, link came back: plain resume (the session keeps its state; a stale epoch is fixed by the host's Rebase)
    void reconnect(size_t i) {
        C& c = *cl[i];
        story.setConnected(c.peer, true);
        dnet.setConnected(c.peer, true);
        c.cs->connect(now, c.cs->lastSession() + 1);
    }
    void step() {
        now += 5;
        story.advance(now); dnet.advance(now);
        host.tick(now);
        if (engine) engine->tick(now);
        for (auto& c : cl) c->cs->tick(now);
        if (withDialogue) {
            for (auto& c : cl) if (c->cs->joined() && !c->dlgIn) { c->dlgIn = true; hostDn->playerJoined(c->slot); }
            PeerId from; Bytes b;
            while (dnet.endpoint(0)->poll(from, b)) hostDn->onPacket(from, b, uint32_t(now));
            for (auto& c : cl) while (dnet.endpoint(c->peer)->poll(from, b)) c->dn->onPacket(from, b, uint32_t(now));
            hostDn->tick(uint32_t(now));
            for (auto& c : cl) c->dn->tick(uint32_t(now));
        }
        story.advance(now);
    }
    void run(uint32_t ms) { for (uint32_t t = 0; t < ms; t += 5) step(); }
    bool allLive() {
        for (auto& c : cl) {
            if (!story.connected(c->peer)) continue;   // dropped clients do not count
            if (!c->cs->joined() || c->cs->baselineActive() || c->cs->applied() != host.log().head()) return false;
        }
        return true;
    }
    uint64_t reward(uint32_t item) {
        Cause cs; cs.kind = CauseKind::QuestResult; cs.source = Q(1);
        uint64_t seq = host.commit(0, cs, {AddItem{Q(item), 1, Recipient::all()}});
        rewardSeq.push_back({item, seq});
        return seq;
    }
};

}  // namespace rejoin
