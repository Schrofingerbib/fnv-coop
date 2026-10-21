// Test rig for plugin/loot (game-free): a fake engine per machine (ILootEngine), an in-memory link that stands in for the live hub (ILiveLink),
// and a Cluster that advances a virtual clock. Machine 0 is the host. Everything crosses the real wire encoder.
#pragma once
#include <algorithm>
#include <deque>
#include <functional>
#include <map>
#include <memory>
#include <set>
#include <string>
#include <vector>
#include "loot/loot_sync.h"
#include "loot/loot_wire.h"
#include "minitest.h"

namespace nvl_test {

using namespace nvcoop::loot;
namespace ll = nvcoop::livewire;

inline FormRef F(uint32_t id, const char* plugin = "falloutnv.esm") { return FormRef(plugin, id); }
inline lc::Vec3 V(float x = 0, float y = 0, float z = 0) { lc::Vec3 v; v.x = x; v.y = y; v.z = z; return v; }
inline Items I(std::initializer_list<std::pair<uint32_t, int32_t>> l) {
    Items it;
    for (auto& p : l) it[F(p.first)] = p.second;
    return it;
}
inline int32_t count(const Items& i, uint32_t base) { auto f = i.find(F(base)); return f == i.end() ? 0 : f->second; }

class FakeEngine : public ILootEngine {
public:
    struct Cont { RefId ref = 0; lc::ContainerKind kind = lc::ContainerKind::Container; lc::CellId cell = 7; lc::Vec3 pos; Items items; bool exact = true; bool loaded = true; };
    struct WItem { RefId ref = 0; lc::WorldItem it; NetId net = 0; bool alive = true; };

    bool ready = true;
    std::map<NetId, Cont> conts;
    Items player;
    RefId menu = 0;
    int closeCalls = 0;
    bool closeWorks = true;
    std::vector<std::pair<lc::Notice, NetId>> notices;
    std::map<RefId, WItem> world;
    RefId nextRef = 0x1000;
    bool cellLoaded = true;
    int createCalls = 0;
    std::map<NetId, std::pair<lc::Vec3, lc::Vec3>> poses;      // what readPose answers (the owner's engine)
    std::map<NetId, std::pair<lc::Vec3, bool>> shown;          // what setPose put on screen
    std::set<FormRef> stuck;                                   // setContents cannot remove these
    int adjustCalls = 0;
    int setContentsCalls = 0;
    bool readsFail = false;

    Cont& add(NetId net, Items items, lc::ContainerKind kind = lc::ContainerKind::Container, lc::CellId cell = 7) {
        Cont c;
        c.ref = 0x500 + net; c.kind = kind; c.cell = cell; c.items = std::move(items);
        return conts[net] = c;
    }
    Cont* byRef(RefId r) { for (auto& kv : conts) if (kv.second.ref == r) return &kv.second; return nullptr; }
    NetId netOfRef(RefId r) { for (auto& kv : conts) if (kv.second.ref == r) return kv.first; return 0; }
    void openMenuOn(NetId net) { menu = conts.at(net).ref; }
    bool take(NetId net, uint32_t base, int32_t n) {
        Cont& c = conts.at(net);
        if (menu != c.ref || count(c.items, base) < n) return false;
        c.items[F(base)] -= n;
        if (c.items[F(base)] == 0) c.items.erase(F(base));
        player[F(base)] += n;
        return true;
    }
    bool put(NetId net, uint32_t base, int32_t n) {
        Cont& c = conts.at(net);
        if (menu != c.ref || count(player, base) < n) return false;
        player[F(base)] -= n;
        if (player[F(base)] == 0) player.erase(F(base));
        c.items[F(base)] += n;
        return true;
    }
    RefId addWorld(const FormRef& base, int32_t n, NetId net = 0, lc::CellId cell = 7, lc::Vec3 pos = V(1, 2, 3)) {
        WItem w;
        w.ref = nextRef++; w.net = net;
        w.it.base = base; w.it.count = n; w.it.cell = cell; w.it.pos = pos; w.it.id = net;
        world[w.ref] = w;
        return w.ref;
    }
    int aliveWorld() const { int n = 0; for (auto& kv : world) if (kv.second.alive) ++n; return n; }
    WItem* worldByNet(NetId net) { for (auto& kv : world) if (kv.second.alive && kv.second.net == net) return &kv.second; return nullptr; }

    // ---- ILootEngine ----
    bool worldReady() override { return ready; }
    uint32_t worldEpoch() override { return 1; }
    bool describeContainer(RefId ref, ContainerDesc& out) override {
        Cont* c = byRef(ref);
        if (!c || !c->loaded) return false;
        out.net = netOfRef(ref); out.kind = c->kind; out.cell = c->cell; out.pos = c->pos;
        return true;
    }
    RefId refOfContainer(NetId net) override { auto it = conts.find(net); return it != conts.end() && it->second.loaded ? it->second.ref : 0; }
    bool readContents(NetId net, Items& out, bool& exact) override {
        auto it = conts.find(net);
        if (readsFail || it == conts.end() || !it->second.loaded) return false;
        out = it->second.items;
        exact = it->second.exact;
        return true;
    }
    bool setContents(NetId net, const Items& want) override {
        ++setContentsCalls;
        auto it = conts.find(net);
        if (it == conts.end()) return false;
        Items next = want;
        bool ok = true;
        for (const FormRef& s : stuck) {   // an item the engine will not take out
            auto have = it->second.items.find(s);
            if (have != it->second.items.end() && count(next, s.localId) < have->second) { next[s] = have->second; ok = false; }
        }
        it->second.items = next;
        return ok;
    }
    RefId openMenuRef() override { return menu; }
    bool closeMenu() override { ++closeCalls; if (closeWorks) menu = 0; return closeWorks; }
    bool adjustPlayer(const FormRef& base, int32_t delta) override {
        ++adjustCalls;
        int32_t& c = player[base];
        bool ok = true;
        if (delta < 0 && c < -delta) { c = 0; ok = false; } else c += delta;
        if (c <= 0) player.erase(base);
        return ok;
    }
    void notify(lc::Notice n, NetId id) override { notices.emplace_back(n, id); }
    void loadedContainers(std::vector<NetId>& out, size_t max) override {
        out.clear();
        for (auto& kv : conts) if (kv.second.loaded && out.size() < max) out.push_back(kv.first);
    }
    bool describeItem(RefId ref, ItemDesc& out) override {
        auto it = world.find(ref);
        if (it == world.end() || !it->second.alive) return false;
        out.item = it->second.it;
        out.net = it->second.net;
        out.boundDrop = isIssuedNet(it->second.net);
        out.item.id = out.net;
        return true;
    }
    static bool isIssuedNet(NetId n) { return lc::isIssuedItemId(n); }
    RefId createItem(const lc::WorldItem& it) override {
        ++createCalls;
        if (!cellLoaded) return 0;
        if (WItem* have = worldByNet(it.id)) return have->ref;
        WItem w;
        w.ref = nextRef++; w.net = it.id; w.it = it;
        world[w.ref] = w;
        return w.ref;
    }
    void bindItem(RefId ref, NetId net) override { auto it = world.find(ref); if (it != world.end()) it->second.net = net; }
    bool removeItem(NetId net) override {
        bool any = false;
        for (auto& kv : world) if (kv.second.alive && kv.second.net == net) { kv.second.alive = false; any = true; }
        return any;
    }
    bool readPose(NetId net, lc::Vec3& pos, lc::Vec3& rot) override {
        auto it = poses.find(net);
        if (it == poses.end()) return false;
        pos = it->second.first; rot = it->second.second;
        return true;
    }
    bool setPose(NetId net, const lc::Vec3& pos, const lc::Vec3&, bool moving) override { shown[net] = {pos, moving}; return true; }
};

struct Cluster;

struct Link : public ll::ILiveLink {
    Cluster* cl = nullptr;
    PlayerId from = 0;
    bool host = false;
    bool send(PlayerId to, bool reliable, fnvcoop::Bytes data) override;
    bool isHost() const override { return host; }
    PlayerId self() const override { return from; }
};

struct Machine {
    PlayerId slot = 0;
    FakeEngine eng;
    std::unique_ptr<LootSync> sync;
    Link link;
    bool cut = false;
};

struct Datagram { uint64_t at = 0, seq = 0; PlayerId from = 0, to = 0; std::vector<uint8_t> bytes; bool unreliable = false; };

struct Cluster {
    std::vector<std::unique_ptr<Machine>> m;
    std::vector<Datagram> q;
    uint64_t now = 1000, seq = 0;
    uint32_t latency = 20;
    uint64_t sent = 0, delivered = 0;
    SyncConfig cfg;

    explicit Cluster(int players, SyncConfig c = defaultCfg()) : cfg(c) {
        for (int i = 0; i < players; ++i) addMachine();
    }
    static SyncConfig defaultCfg() {
        SyncConfig c;
        c.enabled = true;
        c.pollMs = 20;
        c.corpseDelayMs = 300;
        c.sweepEveryMs = 100;
        c.auditEveryMs = 100;
        c.clutterEveryMs = 20;
        c.createRetryMs = 200;
        return c;
    }
    Machine& addMachine() {
        auto mc = std::make_unique<Machine>();
        mc->slot = static_cast<PlayerId>(m.size());
        mc->sync = std::make_unique<LootSync>(mc->eng, cfg);
        mc->link.cl = this;
        mc->link.from = mc->slot;
        mc->link.host = mc->slot == 0;
        Machine& me = *mc;
        m.push_back(std::move(mc));
        ll::SessionInfo si;
        si.isHost = me.slot == 0;
        si.self = me.slot;
        me.sync->sessionBegin(si, me.link);
        if (me.slot != 0) {
            m[0]->sync->playerJoined(me.slot);
            me.sync->hostLinkUp();
        }
        return me;
    }
    Machine& host() { return *m[0]; }
    Machine& machine(size_t i) { return *m[i]; }

    void post(PlayerId from, PlayerId to, std::vector<uint8_t> bytes, bool unrel) {
        const PlayerId guest = from == 0 ? to : from;
        if (guest < m.size() && m[guest]->cut) return;
        ++sent;
        Datagram d;
        d.at = now + (guest == 0 ? 0 : latency); d.seq = ++seq; d.from = from; d.to = to; d.bytes = std::move(bytes); d.unreliable = unrel;
        q.push_back(std::move(d));
    }
    void deliverDue() {
        for (;;) {
            size_t best = q.size();
            for (size_t i = 0; i < q.size(); ++i)
                if (q[i].at <= now && (best == q.size() || q[i].at < q[best].at || (q[i].at == q[best].at && q[i].seq < q[best].seq))) best = i;
            if (best == q.size()) return;
            Datagram d = std::move(q[best]);
            q.erase(q.begin() + static_cast<long>(best));
            ++delivered;
            m[d.to]->sync->receive(d.from, d.bytes.data(), d.bytes.size());
        }
    }
    void step(uint32_t dt = 10) {
        now += dt;
        deliverDue();
        for (auto& mc : m) mc->sync->tick(now);
        deliverDue();
    }
    void run(uint32_t ms, uint32_t dt = 10) { for (uint32_t t = 0; t < ms; t += dt) step(dt); }
    bool idle() const {
        if (!q.empty()) return false;
        for (auto& mc : m) if (mc->sync->client() && mc->sync->client()->pendingCount()) return false;
        return true;
    }
    void settle(uint32_t ms = 3000) { run(ms); for (int i = 0; i < 500 && !idle(); ++i) step(10); }
};

inline bool Link::send(PlayerId to, bool reliable, fnvcoop::Bytes data) {
    cl->post(from, to, std::move(data), !reliable);
    return true;
}

}  // namespace nvl_test
