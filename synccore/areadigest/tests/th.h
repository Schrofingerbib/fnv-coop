// Test rig for synccore/areadigest: fake sources, a fake story layer, a message bus with latency / loss / hooks, and a machine set
// (one host, 1..3 clients) that runs the real AreaSync on every machine.
#pragma once
#include <cmath>
#include <functional>
#include <map>
#include <memory>
#include <set>
#include <string>
#include <vector>
#include "areadigest/ad_wire.h"
#include "areadigest/areadigest.h"
#include "fnvcoop/rng.h"
#include "fnvcoop/shadow.h"
#include "minitest.h"

namespace adt {

using namespace fnvcoop;
using namespace fnvcoop::areadigest;

constexpr float kU = 70.0f;   // game units per metre
inline Vec3 V(float x = 0, float y = 0, float z = 0) { Vec3 v; v.x = x; v.y = y; v.z = z; return v; }

// ---- fake sources -------------------------------------------------------------------------------------------------------------
struct RawActor {
    CellId cell = 1;
    bool alive = true;
    float hp = 100;
    Vec3 pos;
};

// The actors of one machine. `own` is THIS machine's view of who owns what.
class FakeActors : public IAreaSource {
public:
    explicit FakeActors(const Config& c = Config()) : cfg(c), tr(c) {}
    Config cfg;
    ChangeTracker tr;
    std::map<Key, RawActor> a;
    std::set<CellId> cells;
    std::map<Key, OwnerView> own;
    HealResult result = HealResult::Applied;      // what heal() answers
    bool stuck = false;                           // heal() says Applied but changes nothing (an engine that keeps reverting)
    std::vector<std::string> healLog;
    std::map<Key, int> healsByKey;
    Diff lastPlan;
    int heals = 0;

    Cat category() const override { return Cat::Actors; }
    static Item toItem(Key k, const RawActor& r) {
        Item it;
        it.key = k; it.cat = Cat::Actors; it.cell = r.cell; it.alive = r.alive; it.hp = r.hp; it.pos = r.pos;
        return it;
    }
    void collect(uint64_t now, std::vector<Item>& out, std::vector<CellId>& loaded) override {
        std::vector<Item> mine;
        for (auto& kv : a) mine.push_back(toItem(kv.first, kv.second));
        tr.update(mine, now);
        for (auto& it : mine) out.push_back(it);
        for (CellId c : cells) loaded.push_back(c);
    }
    bool find(Key k, Item& out, uint64_t now) override {
        auto i = a.find(k);
        if (i == a.end()) return false;
        out = toItem(k, i->second);
        tr.refresh(out, now);
        return true;
    }
    HealResult heal(const Item& auth, const Diff& plan, uint64_t) override {
        lastPlan = plan;
        healLog.push_back(describe(auth));
        ++heals;
        ++healsByKey[auth.key];
        auto i = a.find(auth.key);
        if (i == a.end()) return HealResult::Missing;
        if (result != HealResult::Applied) return result;
        if (stuck) return HealResult::Applied;
        RawActor& r = i->second;
        if (plan.alive) r.alive = auth.alive;
        if (plan.hp) r.hp = auth.hp;
        if (plan.cell) r.cell = auth.cell;
        if (plan.pos != RubberMode::None || plan.cell) r.pos = auth.pos;
        return HealResult::Applied;
    }
    bool owner(Key k, OwnerView& v) override {
        auto i = own.find(k);
        if (i == own.end()) return false;
        v = i->second;
        return true;
    }
};

// Containers or objects of one machine.
class FakeState : public IAreaSource {
public:
    FakeState(Cat c, bool deleg = false, const Config& cf = Config()) : cat(c), deleg_(deleg), tr(cf) {}
    Cat cat;
    bool deleg_;
    ChangeTracker tr;
    struct R { CellId cell = 1; bool alive = true; uint32_t state = 0; };
    std::map<Key, R> a;
    std::set<CellId> cells;
    std::vector<std::string> healLog;
    int heals = 0;
    Cat category() const override { return cat; }
    bool delegated() const override { return deleg_; }
    Item toItem(Key k, const R& r) const {
        Item it;
        it.key = k; it.cat = cat; it.cell = r.cell; it.alive = r.alive; it.state = r.state;
        return it;
    }
    void collect(uint64_t now, std::vector<Item>& out, std::vector<CellId>& loaded) override {
        std::vector<Item> mine;
        for (auto& kv : a) mine.push_back(toItem(kv.first, kv.second));
        tr.update(mine, now);
        for (auto& it : mine) out.push_back(it);
        for (CellId c : cells) loaded.push_back(c);
    }
    bool find(Key k, Item& out, uint64_t now) override {
        auto i = a.find(k);
        if (i == a.end()) return false;
        out = toItem(k, i->second);
        tr.refresh(out, now);
        return true;
    }
    HealResult heal(const Item& auth, const Diff& plan, uint64_t) override {
        ++heals;
        healLog.push_back(describe(auth));
        auto i = a.find(auth.key);
        if (i == a.end()) return HealResult::Missing;
        if (plan.state) i->second.state = auth.state;
        if (plan.alive) i->second.alive = auth.alive;
        if (plan.cell) i->second.cell = auth.cell;
        return HealResult::Applied;
    }
};

class FakeStory : public IStorySource {
public:
    ShadowState st;
    uint64_t seq = 0;
    uint32_t epoch = 1;
    uint64_t repairCount = 0;
    bool available = true;
    std::vector<PlayerId> repairRequests;
    bool local(StoryDigest& d) override {
        if (!available) return false;
        d = storyDigestOf(st, seq, epoch);
        return true;
    }
    void requestRepair(PlayerId p) override { repairRequests.push_back(p); }
    uint64_t repairs() override { return repairCount; }
    void commit(uint32_t quest, uint32_t stage) {
        EffectTxn t;
        t.seq = ++seq;
        t.effects.push_back(SetStage{FormRef("falloutnv.esm", quest), stage});
        st.applyTxn(t);
    }
};

// ---- the bus -------------------------------------------------------------------------------------------------------------------
struct Msg {
    uint64_t at = 0, order = 0;
    PlayerId from = 0, to = 0;
    std::vector<uint8_t> data;
};

class Bus {
public:
    uint64_t now = 10000;
    uint64_t latency = 20, jitter = 0;
    double loss = 0, dup = 0;
    std::vector<Msg> q;
    uint64_t order = 0, sent = 0, lost = 0, delivered = 0;
    Rng rng{12345};
    bool cut[8][8] = {};                                              // cut[from][to]: everything is dropped
    std::function<bool(Msg&)> hook;                                    // before delivery; false = drop it
    std::function<bool(const Msg&)> sendFilter;                        // at send time; false = drop it

    void push(PlayerId from, PlayerId to, std::vector<uint8_t> d) {
        ++sent;
        Msg m;
        m.from = from; m.to = to; m.data = std::move(d);
        if (sendFilter && !sendFilter(m)) { ++lost; return; }
        if (from < 8 && to < 8 && cut[from][to]) { ++lost; return; }
        if (loss > 0 && rng.chance(loss)) { ++lost; return; }
        m.at = now + latency + (jitter ? rng.next() % (jitter + 1) : 0);
        m.order = ++order;
        q.push_back(m);
        if (dup > 0 && rng.chance(dup)) { m.at += 5; m.order = ++order; q.push_back(m); }
    }
};

struct Port : IAreaNet {
    Bus* bus = nullptr;
    PlayerId from = 0;
    void send(PlayerId to, std::vector<uint8_t> d) override { bus->push(from, to, std::move(d)); }
};

// One machine: its own copy of the world and the real AreaSync on top.
struct M {
    PlayerId slot = 0;
    Port port;
    FakeActors actors;
    FakeState cont{Cat::Containers}, obj{Cat::Objects};
    FakeStory story;
    std::unique_ptr<AreaSync> sync;
    M(PlayerId s, Bus& bus, const Config& base, bool delegContainers, bool delegObjects, bool withStory)
        : slot(s), actors(base), cont(Cat::Containers, delegContainers, base), obj(Cat::Objects, delegObjects, base) {
        port.bus = &bus;
        port.from = s;
        Config c = base;
        c.self = s;
        c.isHost = s == kHostPlayer;
        sync.reset(new AreaSync(c, port, withStory ? &story : nullptr));
        sync->addSource(&actors);
        sync->addSource(&cont);
        sync->addSource(&obj);
    }
};

struct Rig {
    Bus bus;
    Config base;
    std::vector<std::unique_ptr<M>> m;
    std::vector<uint64_t> deliveredBytes;

    explicit Rig(int machines = 2, Config c = Config(), bool withStory = true, bool delegContainers = false, bool delegObjects = false) : base(c) {
        for (int i = 0; i < machines; ++i) m.emplace_back(new M(static_cast<PlayerId>(i), bus, base, delegContainers, delegObjects, withStory));
        for (int i = 1; i < machines; ++i) {
            m[0]->sync->playerJoined(static_cast<PlayerId>(i));
            m[i]->sync->hostConnected();
        }
        for (auto& x : m) x->sync->tick(bus.now);
    }
    M& host() { return *m[0]; }
    M& cl(int i) { return *m[static_cast<size_t>(i)]; }
    size_t n() const { return m.size(); }

    void step(uint64_t ms) {
        bus.now += ms;
        for (;;) {
            size_t best = bus.q.size();
            for (size_t i = 0; i < bus.q.size(); ++i) {
                if (bus.q[i].at > bus.now) continue;
                if (best == bus.q.size() || bus.q[i].at < bus.q[best].at || (bus.q[i].at == bus.q[best].at && bus.q[i].order < bus.q[best].order)) best = i;
            }
            if (best == bus.q.size()) break;
            Msg msg = std::move(bus.q[best]);
            bus.q.erase(bus.q.begin() + static_cast<std::ptrdiff_t>(best));
            if (bus.hook && !bus.hook(msg)) { ++bus.lost; continue; }
            if (msg.to < m.size()) {
                ++bus.delivered;
                m[msg.to]->sync->tick(bus.now);   // sources and the clock know the delivery time
                m[msg.to]->sync->receive(msg.from, msg.data.data(), msg.data.size());
            }
        }
        for (auto& x : m) x->sync->tick(bus.now);
    }
    void run(uint64_t ms, uint64_t stepMs = 50) {
        for (uint64_t t = 0; t < ms; t += stepMs) step(stepMs);
    }

    // ---- world building -------------------------------------------------------------------------------------------------------
    // The same actor on every machine, owned by `owner` on every machine's view.
    void addActor(Key k, CellId cell, Vec3 pos, PlayerId owner, float hp = 100, uint32_t epoch = 1) {
        for (auto& x : m) {
            RawActor r;
            r.cell = cell; r.pos = pos; r.hp = hp;
            x->actors.a[k] = r;
            x->actors.cells.insert(cell);
            OwnerView v;
            v.known = true; v.owner = owner; v.epoch = epoch;
            x->actors.own[k] = v;
        }
    }
    void addState(Cat c, Key k, CellId cell, uint32_t state) {
        for (auto& x : m) {
            FakeState& s = c == Cat::Containers ? x->cont : x->obj;
            FakeState::R r;
            r.cell = cell; r.state = state;
            s.a[k] = r;
            s.cells.insert(cell);
        }
    }
    // The normal sync path: the owner's change reaches everybody (as the snapshot / event layer would).
    void setHp(Key k, float hp) { for (auto& x : m) x->actors.a[k].hp = hp; }
    void setPos(Key k, Vec3 p) { for (auto& x : m) x->actors.a[k].pos = p; }
    void setAlive(Key k, bool alive) { for (auto& x : m) x->actors.a[k].alive = alive; }
    void setStateEverywhere(Cat c, Key k, uint32_t st) { for (auto& x : m) (c == Cat::Containers ? x->cont : x->obj).a[k].state = st; }

    // ---- reading ---------------------------------------------------------------------------------------------------------------
    uint64_t healsApplied(int machine, Cat c) const { return m[static_cast<size_t>(machine)]->sync->counters().cat[static_cast<size_t>(c)].healsApplied; }
    uint64_t healsAll() const {
        uint64_t n = 0;
        for (auto& x : m) n += x->sync->healsTotalAll();
        return n;
    }
    uint64_t sumDecided() const {
        uint64_t n = 0;
        for (auto& x : m) for (size_t c = 0; c < kCats; ++c) n += x->sync->counters().cat[c].healsDecided;
        return n;
    }
};

}  // namespace adt
