// Test rig for plugin/world_time: 1..4 machines (host + clients), each with a fake engine (clock that runs at TimeScale, a message log, a
// travel log), over an in-memory network with latency, jitter, loss and duplication, plus a fake consent prompt layer the test answers.
#pragma once
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <deque>
#include <memory>
#include <string>
#include <vector>

#include "wt_session.h"

namespace wt_test {

using namespace nvwt;

struct FakeEngine : IWorldTimeEngine {
    GameTime t;
    float ts = 30.0f;
    bool paused = false;
    uint64_t lastMs = 0;
    std::vector<std::string> notes;
    std::vector<FormRef> traveled;
    int writes = 0;
    bool failWrite = false;
    bool knowsMarker = true;

    void advanceTo(uint64_t nowMs) {
        if (!paused && nowMs > lastMs) t = advanceBy(t, static_cast<double>(nowMs - lastMs) / 1000.0 * ts / 60.0);
        lastMs = nowMs;
    }
    bool readClock(GameTime* out) override { *out = t; return true; }
    bool writeClock(const GameTime& g) override {
        if (failWrite) return false;
        t = g;
        ++writes;
        return true;
    }
    float timeScale() override { return ts; }
    void notify(const std::string& s) override { notes.push_back(s); }
    bool startTravel(const FormRef& m) override {
        if (!knowsMarker) return false;
        traveled.push_back(m);
        return true;
    }
};

struct Pkt {
    uint64_t at, order;
    PlayerId from, to;
    Bytes data;
};
struct Eff {
    uint64_t at;
    PlayerId to;
    uint32_t minutes;
};
struct Prompt {
    uint32_t id;
    PlayerId initiator;
    ReqKind kind;
    std::string text;
    uint32_t timeoutMs;
};

struct NetOpts {
    uint32_t latencyMs = 40, jitterMs = 0;
    int lossPct = 0, dupPct = 0;
    uint64_t seed = 1;
};

inline GameTime startTime() {
    GameTime t;
    t.year = 2281; t.month = 10; t.day = 23; t.hour = 9.0; t.daysPassed = 0.0;
    return t;
}

struct Rig {
    struct M {
        PlayerId slot = 0;
        Role role = Role::Host;
        FakeEngine eng;
        std::unique_ptr<WorldTimeSession> s;
        std::vector<fnvcoop::Effect> recorded;
        bool joined = true;
    };
    uint64_t now = 100000;
    std::vector<std::unique_ptr<M>> m;
    std::vector<Pkt> q;
    std::vector<Eff> effs;
    uint64_t order = 0, rng;
    NetOpts net;
    uint32_t combat = 0;
    bool tether = false, locked = false, askFails = false;
    std::vector<Prompt> prompts;
    uint32_t nextPrompt = 100;
    uint64_t sent = 0, dropped = 0;

    uint64_t rnd() { rng = rng * 6364136223846793005ull + 1442695040888963407ull; return rng >> 33; }

    explicit Rig(int n, TravelMode travel = TravelMode::Free, NetOpts o = NetOpts(), SessionConfig base = SessionConfig()) : rng(o.seed * 7919 + 13), net(o) {
        base.broker.travel = travel;
        for (int i = 0; i < n; ++i) {
            auto mm = std::make_unique<M>();
            mm->slot = static_cast<PlayerId>(i);
            mm->role = i == 0 ? Role::Host : Role::Client;
            mm->eng.t = startTime();
            mm->eng.lastMs = now;
            M* raw = mm.get();
            SessionEnv env;
            env.engine = &mm->eng;
            env.party.joined = [this]() { return joinedMask(); };
            env.party.inCombat = [this]() { return combat; };
            env.party.tethered = [this]() { return tether; };
            env.party.travelLocked = [this]() { return locked; };
            if (i == 0) {
                env.send = [this](PlayerId to, Bytes d) {
                    if (to == kNoPlayer) { for (size_t k = 1; k < m.size(); ++k) if (m[k]->joined) push(0, static_cast<PlayerId>(k), d); }
                    else push(0, to, std::move(d));
                };
                env.ask = [this](PlayerId who, ReqKind k, const std::string& text, uint32_t timeoutMs) -> uint32_t {
                    if (askFails) return 0;
                    prompts.push_back(Prompt{nextPrompt, who, k, text, timeoutMs});
                    return nextPrompt++;
                };
                env.record = [this, raw](const fnvcoop::Effect& e) {
                    raw->recorded.push_back(e);
                    if (auto* a = std::get_if<fnvcoop::AdvanceTime>(&e))
                        for (size_t k = 1; k < m.size(); ++k) if (m[k]->joined) effs.push_back(Eff{now + net.latencyMs + (rnd() % (net.jitterMs + 1)), static_cast<PlayerId>(k), a->minutes});
                };
            } else {
                const PlayerId me = static_cast<PlayerId>(i);
                env.send = [this, me](PlayerId, Bytes d) { push(me, 0, std::move(d)); };
            }
            mm->s = std::make_unique<WorldTimeSession>(mm->role, mm->slot, base, env);
            m.push_back(std::move(mm));
        }
        for (int i = 1; i < n; ++i) m[0]->s->onPlayerJoined(static_cast<PlayerId>(i), now);
    }

    uint32_t joinedMask() const {
        uint32_t x = 1;
        for (size_t k = 1; k < m.size(); ++k) if (m[k]->joined) x |= 1u << k;
        return x;
    }
    M& host() { return *m[0]; }
    M& at(size_t i) { return *m[i]; }

    void push(PlayerId from, PlayerId to, Bytes d) {
        ++sent;
        if (static_cast<int>(rnd() % 100) < net.lossPct) { ++dropped; return; }
        const int copies = (static_cast<int>(rnd() % 100) < net.dupPct) ? 2 : 1;
        for (int c = 0; c < copies; ++c)
            q.push_back(Pkt{now + net.latencyMs + (rnd() % (net.jitterMs + 1)), ++order, from, to, d});
    }

    void step(uint64_t ms = 100) {
        now += ms;
        for (auto& mm : m) mm->eng.advanceTo(now);
        std::vector<Pkt> due;
        for (size_t i = 0; i < q.size();) {
            if (q[i].at <= now) { due.push_back(std::move(q[i])); q.erase(q.begin() + static_cast<std::ptrdiff_t>(i)); }
            else ++i;
        }
        std::sort(due.begin(), due.end(), [](const Pkt& a, const Pkt& b) { return a.at != b.at ? a.at < b.at : a.order < b.order; });
        for (auto& p : due) if (p.to < m.size() && m[p.to]->joined) m[p.to]->s->onPacket(p.from, p.data, now);
        for (size_t i = 0; i < effs.size();) {
            if (effs[i].at <= now) {
                if (effs[i].to < m.size() && m[effs[i].to]->joined) m[effs[i].to]->s->onAdvanceTimeEffect(effs[i].minutes, now);
                effs.erase(effs.begin() + static_cast<std::ptrdiff_t>(i));
            } else ++i;
        }
        for (auto& mm : m) if (mm->joined) mm->s->frame(now);
    }
    void run(uint64_t totalMs, uint64_t ms = 100) { for (uint64_t t = 0; t < totalMs; t += ms) step(ms); }

    bool answer(size_t promptIdx, bool confirmed, bool timedOut = false) {
        if (promptIdx >= prompts.size()) return false;
        return host().s->onPromptResult(prompts[promptIdx].id, confirmed, timedOut, now);
    }
    double driftToHost(size_t i) { return deltaMinutes(m[i]->eng.t, m[0]->eng.t); }
};

}  // namespace wt_test
