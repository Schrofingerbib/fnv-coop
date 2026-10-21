// Test rig for plugin/spawn (game-free): one fake engine per machine (ISpawnEngine), an in-memory network with latency, cuts, drops and
// duplicates (ISpawnNet), the story log's SpawnRef broadcast (ISpawnHost) and a Rig that advances a virtual clock. Machine 0 is the host.
#pragma once
#include <algorithm>
#include <cmath>
#include <functional>
#include <map>
#include <memory>
#include <set>
#include <sstream>
#include <string>
#include <vector>
#include "fnvcoop/rng.h"
#include "spawn/spawn_sync.h"

namespace nvs_test {

using namespace nvcoop::spawn;
namespace sp = fnvcoop::spawn;

inline FormRef F(uint32_t id, const char* plugin = "falloutnv.esm") { return FormRef(plugin, id); }
inline sp::Vec3 V(float x = 0, float y = 0, float z = 0) { sp::Vec3 v; v.x = x; v.y = y; v.z = z; return v; }

struct FRef {
    RefId id = 0;
    bool created = false;
    FormRef base, leveledFrom, zone, wire;
    NetId placedNet = 0;
    CellId cell = 0;
    sp::Vec3 pos;
    bool dead = false, disabled = false, deleted = false;
    int baseSets = 0;
};

class FakeEngine : public ISpawnEngine {
public:
    std::map<RefId, FRef> refs;
    std::set<CellId> loaded, interiors;
    std::map<FormRef, CellId> cellOfForm;
    std::map<NetId, RefId> ledger;                 // created refs bound to a net id (by adoption or by a SpawnRef)
    std::vector<fnvcoop::SpawnRef> queued;         // SpawnRefs waiting for their cell
    RefId nextRef = 0xFF000001u;
    bool rebaseOk = true;
    const std::set<NetId>* deadNets = nullptr;     // actors the party already killed: a copy made later (queued SpawnRef) is a corpse
    uint64_t holds = 0, shows = 0, deletes = 0, kills = 0, rebases = 0, rebaseCalls = 0, madeFromSpawnRef = 0;

    CellId cellId(CellId c) { cellOfForm[F(0x100000 + c)] = c; return c; }

    RefId addCreated(const FormRef& base, CellId cell, sp::Vec3 pos, const FormRef& zone = FormRef()) {
        FRef r;
        r.id = nextRef++; r.created = true; r.base = base; r.cell = cellId(cell); r.pos = pos; r.zone = zone;
        refs[r.id] = r;
        return r.id;
    }
    RefId addPlaced(RefId id, NetId net, const FormRef& wire, const FormRef& base, const FormRef& leveledFrom, CellId cell, sp::Vec3 pos) {
        FRef r;
        r.id = id; r.created = false; r.placedNet = net; r.wire = wire; r.base = base; r.leveledFrom = leveledFrom; r.cell = cellId(cell); r.pos = pos;
        refs[id] = r;
        return id;
    }
    void loadCell(CellId c) {
        cellId(c);
        loaded.insert(c);
        std::vector<fnvcoop::SpawnRef> q;
        q.swap(queued);
        for (auto& s : q) createFromSpawnRef(s);
    }
    void unloadCell(CellId c) { loaded.erase(c); }
    bool visible(const FRef& r) const { return !r.deleted && !r.disabled && loaded.count(r.cell) != 0; }
    NetId netOf(const FRef& r) const {
        if (!r.created) return r.placedNet;
        for (auto& kv : ledger) if (kv.second == r.id) return kv.first;
        return 0;
    }

    // ---- ISpawnEngine ----
    bool describe(RefId ref, RefInfo& out) override {
        out = RefInfo{};
        auto it = refs.find(ref);
        if (it == refs.end() || it->second.deleted || !loaded.count(it->second.cell)) return false;
        const FRef& r = it->second;
        out.valid = true; out.ref = ref; out.dead = r.dead; out.disabled = r.disabled; out.created = r.created;
        out.base = r.base; out.leveledFrom = r.leveledFrom; out.encounterZone = r.zone; out.wireRef = r.wire; out.netId = r.placedNet;
        out.cell = r.cell; out.cellForm = F(0x100000 + r.cell); out.pos = r.pos; out.interior = interiors.count(r.cell) != 0;
        return true;
    }
    RefId refOfNet(NetId id) override {
        auto l = ledger.find(id);
        if (l != ledger.end()) { auto it = refs.find(l->second); if (it != refs.end() && !it->second.deleted) return l->second; }
        for (auto& kv : refs) if (!kv.second.created && kv.second.placedNet == id && !kv.second.deleted && loaded.count(kv.second.cell)) return kv.first;
        return 0;
    }
    bool setHeld(RefId ref, bool hold) override {
        auto it = refs.find(ref);
        if (it == refs.end() || it->second.deleted) return false;
        it->second.disabled = hold;
        hold ? ++holds : ++shows;
        return true;
    }
    bool deleteRef(RefId ref) override {
        auto it = refs.find(ref);
        if (it == refs.end() || !it->second.created) return false;   // a placed ref can never be deleted
        it->second.deleted = true;
        for (auto l = ledger.begin(); l != ledger.end();) { if (l->second == ref) l = ledger.erase(l); else ++l; }
        ++deletes;
        return true;
    }
    bool rebase(RefId ref, const FormRef& base) override {
        ++rebaseCalls;
        auto it = refs.find(ref);
        if (it == refs.end() || it->second.deleted) return false;
        it->second.disabled = false;
        if (!rebaseOk) return false;
        if (it->second.base != base) { it->second.base = base; ++it->second.baseSets; ++rebases; }
        return true;
    }
    bool kill(RefId ref) override {
        auto it = refs.find(ref);
        if (it == refs.end() || it->second.deleted) return false;
        if (!it->second.dead) { it->second.dead = true; ++kills; }
        return true;
    }
    void bindCreated(RefId ref, NetId id) override {
        ledger[id] = ref;
        auto it = refs.find(ref);
        if (it != refs.end() && deadNets && deadNets->count(id)) it->second.dead = true;   // the actor layer keeps deaths per net id and applies them when it starts tracking
    }
    bool isCreatedBound(NetId id) override {
        if (ledger.count(id)) return true;
        for (auto& q : queued) if (q.id == id) return true;
        return false;
    }
    bool netOfCreated(RefId ref, NetId& id) override {
        for (auto& kv : ledger) if (kv.second == ref) { id = kv.first; return true; }
        return false;
    }
    bool createFromSpawnRef(const fnvcoop::SpawnRef& s) override {
        if (ledger.count(s.id)) return true;
        for (auto& q : queued) if (q.id == s.id) return true;
        auto c = cellOfForm.find(s.cell);
        const CellId cell = c == cellOfForm.end() ? 0 : c->second;
        if (!loaded.count(cell)) { queued.push_back(s); return true; }
        FRef r;
        r.id = nextRef++; r.created = true; r.base = s.base; r.cell = cell; r.pos = V(s.x, s.y, s.z);
        r.dead = deadNets && deadNets->count(s.id) != 0;
        refs[r.id] = r;
        ledger[s.id] = r.id;
        ++madeFromSpawnRef;
        return true;
    }
};

struct NetOpts {
    uint32_t latencyMs = 25;
    uint32_t effectExtraMs = 0;          // the story-log SpawnRef travels this much slower than the spawn channel (0 = same speed, sent first)
    double dup = 0;                      // chance a datagram is delivered twice
    uint32_t jitterMs = 0;               // random extra latency, order per link kept
};

struct Msg {
    uint64_t at = 0, order = 0;
    int from = 0, to = 0;
    bool effect = false;
    Bytes data;
    fnvcoop::SpawnRef sp;
    PlayerId origin = 0;
};

class SimNet {
public:
    NetOpts o;
    fnvcoop::Rng rng{11};
    uint64_t now = 0, order = 0, sent = 0, lost = 0;
    std::vector<Msg> q;
    std::map<std::pair<int, int>, uint64_t> lastAt;
    std::set<int> down;
    std::function<bool(const Msg&)> dropIf;
    std::function<void(const Msg&)> onSend;
    void push(Msg m, bool keepOrder = true) {
        if (down.count(m.from) || down.count(m.to)) { ++lost; return; }
        if (dropIf && dropIf(m)) { ++lost; return; }
        m.order = order++;
        uint64_t at = now + o.latencyMs + (m.effect ? o.effectExtraMs : 0u) + (o.jitterMs ? rng.range(0, o.jitterMs) : 0u);
        if (keepOrder) { uint64_t& last = lastAt[{m.from, m.effect ? m.to + 1000 : m.to}]; if (at < last) at = last; last = at; }
        m.at = at;
        ++sent;
        if (onSend) onSend(m);
        q.push_back(m);
        if (o.dup > 0 && rng.chance(o.dup)) { Msg c = m; c.order = order++; c.at = at + 5; q.push_back(c); }
    }
    template <class F> void deliver(uint64_t t, F&& f) {
        now = t;
        for (;;) {
            size_t best = q.size();
            for (size_t i = 0; i < q.size(); ++i)
                if (q[i].at <= t && (best == q.size() || q[i].at < q[best].at || (q[i].at == q[best].at && q[i].order < q[best].order))) best = i;
            if (best == q.size()) break;
            Msg m = std::move(q[best]);
            q.erase(q.begin() + static_cast<long>(best));
            if (down.count(m.from) || down.count(m.to)) { ++lost; continue; }
            f(m);
        }
    }
};

class Endpoint : public ISpawnNet {
public:
    Endpoint(SimNet& n, int self) : net_(n), self_(self) {}
    void send(PlayerId peer, Bytes data) override {
        Msg m;
        m.from = self_; m.to = peer; m.data = std::move(data);
        net_.push(std::move(m));
    }
private:
    SimNet& net_;
    int self_;
};

class HostLog : public ISpawnHost {
public:
    std::function<void(PlayerId, const fnvcoop::SpawnRef&)> fn;
    std::vector<fnvcoop::SpawnRef> committed;
    void commitSpawn(PlayerId origin, const fnvcoop::SpawnRef& s) override { committed.push_back(s); if (fn) fn(origin, s); }
};

struct Machine {
    int slot = 0;
    FakeEngine eng;
    std::unique_ptr<Endpoint> ep;
    std::unique_ptr<HostLog> log;
    std::unique_ptr<SpawnSync> sync;
    std::map<RefId, NetId> tracked;          // what the actor feed would track (resolveCreated said yes)
    std::set<RefId> ignoreRefs;
};

class Rig {
public:
    SimNet net;
    std::vector<std::unique_ptr<Machine>> m;
    uint64_t now = 1000, gameMs = 0;
    SyncConfig base;
    std::set<NetId> deadNets;
    std::map<int, sp::PlayerLoc> locs;

    explicit Rig(int n, SyncConfig cfg = {}, NetOpts opts = {}) : base(cfg) {
        net.o = opts;
        net.now = now;
        for (int i = 0; i < n; ++i) {
            auto mc = std::make_unique<Machine>();
            mc->slot = i;
            SyncConfig c = base;
            c.self = static_cast<PlayerId>(i);
            c.isHost = i == 0;
            mc->ep.reset(new Endpoint(net, i));
            if (i == 0) {
                mc->log.reset(new HostLog());
                mc->log->fn = [this](PlayerId origin, const fnvcoop::SpawnRef& s) {
                    for (size_t k = 1; k < m.size(); ++k) {   // the story log broadcast: every client applies the SpawnRef effect
                        Msg e;
                        e.from = 0; e.to = static_cast<int>(k); e.effect = true; e.sp = s; e.origin = origin;
                        net.push(std::move(e));
                    }
                };
            }
            mc->eng.deadNets = &deadNets;
            mc->sync.reset(new SpawnSync(c, mc->eng, *mc->ep, mc->log.get()));
            Machine* raw = mc.get();
            mc->sync->setIgnore([raw](RefId r) { return raw->ignoreRefs.count(r) != 0; });
            m.push_back(std::move(mc));
        }
    }
    Machine& at(int i) { return *m[static_cast<size_t>(i)]; }
    Machine& host() { return *m[0]; }

    void connectAll() {
        for (size_t i = 1; i < m.size(); ++i) connect(static_cast<int>(i));
    }
    void connect(int i) {
        host().sync->playerJoined(static_cast<PlayerId>(i));
        at(i).sync->hostConnected();
    }
    void disconnect(int i) {
        net.down.insert(i);
        host().sync->playerLeft(static_cast<PlayerId>(i));
        at(i).sync->hostLost();
        locs.erase(i);
    }
    void reconnect(int i) {
        net.down.erase(i);
        connect(i);
    }
    // where a player is: loads the cell on its machine and tells the host (presence for site ownership)
    void setLoc(int i, CellId cell, std::vector<CellId> adjacent = {}, sp::Vec3 pos = sp::Vec3()) {
        sp::PlayerLoc l;
        l.present = true; l.cell = cell; l.adjacent = std::move(adjacent); l.pos = pos;
        locs[i] = l;
        at(i).eng.loadCell(cell);
        for (CellId a : l.adjacent) at(i).eng.loadCell(a);
        host().sync->setPlayerLoc(static_cast<PlayerId>(i), l);
        at(i).sync->onSiteCellLoaded(cell);
    }

    void scan(Machine& mc) {
        for (auto& kv : mc.eng.refs) {
            FRef& r = kv.second;
            if (!r.created) continue;
            if (r.deleted) { mc.tracked.erase(kv.first); continue; }
            if (!mc.eng.loaded.count(r.cell) || mc.tracked.count(kv.first)) continue;
            NetId id = 0; FormRef wire;
            if (mc.sync->resolveCreated(kv.first, id, wire)) mc.tracked[kv.first] = id;
        }
    }

    void step(uint32_t dt) {
        now += dt;
        net.deliver(now, [this](const Msg& msg) {
            Machine& to = at(msg.to);
            if (msg.effect) { to.eng.createFromSpawnRef(msg.sp); return; }
            to.sync->receive(static_cast<PlayerId>(msg.from), msg.data.data(), msg.data.size());
        });
        for (auto& mc : m) {
            if (net.down.count(mc->slot)) continue;
            mc->sync->tick(now, gameMs);
            scan(*mc);
        }
    }
    void run(uint64_t ms, uint32_t stepMs = 20) {
        const uint64_t end = now + ms;
        while (now < end) step(stepMs);
    }

    // the party killed actor `id`: the death reaches every engine and the host's site layer
    void killNet(NetId id) {
        deadNets.insert(id);
        for (auto& mc : m) {
            const RefId ref = mc->eng.refOfNet(id);
            if (ref) mc->eng.refs[ref].dead = true;
        }
        host().sync->onMemberDied(id);
    }

    // ---- what an engine does on its own ----
    // the machine's engine rolls an encounter: n created actors with the given bases near pos, in `cell`
    std::vector<RefId> engineEncounter(int i, CellId cell, sp::Vec3 pos, const std::vector<uint32_t>& bases, const FormRef& zone = FormRef()) {
        std::vector<RefId> out;
        for (size_t k = 0; k < bases.size(); ++k) out.push_back(at(i).eng.addCreated(F(bases[k]), cell, V(pos.x + 30.0f * static_cast<float>(k), pos.y, pos.z), zone));
        return out;
    }
    // a placed leveled ref that every machine has; each engine rolled its own base
    void addPlacedEverywhere(RefId ref, NetId netId, uint32_t wireId, CellId cell, sp::Vec3 pos, const std::vector<uint32_t>& basePerMachine) {
        for (size_t i = 0; i < m.size(); ++i) {
            const uint32_t b = basePerMachine[i % basePerMachine.size()];
            at(static_cast<int>(i)).eng.addPlaced(ref, netId, F(wireId), F(b), F(0x7000), cell, pos);
        }
    }

    // ---- views for oracles ----
    int visibleCanonical(int i, NetId id) {
        int n = 0;
        for (auto& kv : at(i).eng.refs) {
            const FRef& r = kv.second;
            if (!r.created || !at(i).eng.visible(r)) continue;
            if (at(i).eng.netOf(r) == id) ++n;
        }
        return n;
    }
    int visibleUnbound(int i) {
        int n = 0;
        for (auto& kv : at(i).eng.refs) {
            const FRef& r = kv.second;
            if (r.created && at(i).eng.visible(r) && at(i).eng.netOf(r) == 0 && !at(i).ignoreRefs.count(r.id)) ++n;
        }
        return n;
    }
    int visibleCreated(int i) {
        int n = 0;
        for (auto& kv : at(i).eng.refs) if (kv.second.created && at(i).eng.visible(kv.second) && !at(i).ignoreRefs.count(kv.second.id)) ++n;
        return n;
    }
};

}  // namespace nvs_test
