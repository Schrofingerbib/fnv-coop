// Test helpers for synccore/loot: a small fake-world model (no relation to synccore/enginemodel, which another lane owns).
//
//   Machine  one player's machine: a LootClient, the local player's inventory, this engine's copy of each container, the world refs it can
//            see, and the glue a real plugin would have (poll an open container every step, execute the client's actions, answer
//            ReadyToClose).
//   World    a LootHost, N machines (slot 0 is the host's own), and a datagram network between them. EVERYTHING goes through the real wire
//            encoder / decoder. The network has latency, jitter, loss, duplication and per-slot cuts.
//   totals() counts every item that exists: the players' inventories, the host's containers, items on the ground. A transfer must
//            never change it (nothing duplicated, nothing lost).
#pragma once
#include <algorithm>
#include <deque>
#include <functional>
#include <map>
#include <memory>
#include <set>
#include <string>
#include <vector>
#include "fnvcoop/rng.h"
#include "loot/loot.h"
#include "loot/loot_wire.h"
#include "minitest.h"

namespace lt {

using namespace fnvcoop;
using namespace fnvcoop::loot;

inline FormRef F(uint32_t id, const char* plugin = "falloutnv.esm") { return FormRef(plugin, id); }
inline Vec3 V(float x = 0, float y = 0, float z = 0) { Vec3 v; v.x = x; v.y = y; v.z = z; return v; }
inline Items I(std::initializer_list<std::pair<uint32_t, int32_t>> l) {
    Items it;
    for (auto& p : l) it[F(p.first)] = p.second;
    return it;
}
inline int32_t count(const Items& i, uint32_t base) { auto f = i.find(F(base)); return f == i.end() ? 0 : f->second; }

inline bool hasAction(const std::vector<Action>& v, Action::Kind k) {
    for (const auto& a : v) if (a.kind == k) return true;
    return false;
}
inline const Action* findAction(const std::vector<Action>& v, Action::Kind k, NetId id = 0) {
    for (const auto& a : v) if (a.kind == k && (id == 0 || a.id == id)) return &a;
    return nullptr;
}
inline int countActions(const std::vector<Action>& v, Action::Kind k) {
    int n = 0;
    for (const auto& a : v) if (a.kind == k) ++n;
    return n;
}

// ---------------------------------------------------------------------------------------------------------------------------- host helper
// Drives a LootHost directly (no clients): collects the output per destination.
struct HostRig {
    LootHost host;
    uint64_t now = 100000;
    LootHost::Out last;
    explicit HostRig(HostConfig c = {}) : host(c) {}
    void join(PlayerId p) { LootHost::Out o; host.playerJoined(p, o); last = o; }
    LootHost::Out& req(PlayerId from, const Body& b) { last.clear(); host.onRequest(from, b, now, last); return last; }
    const Result* resultFor(PlayerId to) const {
        for (const auto& o : last) if (o.to == to) if (auto* r = std::get_if<Result>(&o.body)) return r;
        return nullptr;
    }
    template <class T> int countTo(PlayerId to) const {
        int n = 0;
        for (const auto& o : last) if (o.to == to && std::holds_alternative<T>(o.body)) ++n;
        return n;
    }
    template <class T> int countAll() const {
        int n = 0;
        for (const auto& o : last) if (std::holds_alternative<T>(o.body)) ++n;
        return n;
    }
};

inline ContainerRec rec(NetId id, Items items, ContainerKind k = ContainerKind::Container, CellId cell = 7) {
    ContainerRec r;
    r.id = id; r.kind = k; r.cell = cell; r.items = std::move(items);
    return r;
}
inline ReqRegister regReq(uint32_t txn, const ContainerRec& r) { ReqRegister q; q.txn = txn; q.rec = r; return q; }
inline ReqOpen openReq(uint32_t txn, NetId c) { ReqOpen q; q.txn = txn; q.cont = c; return q; }
inline ReqOpen openAdopt(uint32_t txn, const ContainerRec& r) { ReqOpen q; q.txn = txn; q.cont = r.id; q.hasAdopt = true; q.adopt = r; return q; }
inline ReqMove moveReq(uint32_t txn, NetId c, uint32_t base, int32_t n, bool put = false) {
    ReqMove q; q.txn = txn; q.cont = c; q.base = F(base); q.count = n; q.put = put; return q;
}
inline ReqClose closeReq(uint32_t txn, NetId c, const Items& items) { ReqClose q; q.txn = txn; q.cont = c; q.digest = digestOf(items); return q; }
inline ReqDrop dropReq(uint32_t txn, uint32_t base, int32_t n = 1) {
    ReqDrop q; q.txn = txn; q.base = F(base); q.count = n; q.cell = 7; q.pos = V(1, 2, 3); return q;
}
inline ReqClaim claimReq(uint32_t txn, NetId item) { ReqClaim q; q.txn = txn; q.item = item; return q; }
inline ReqClaim claimPlaced(uint32_t txn, NetId item, uint32_t base, int32_t n = 1) {
    ReqClaim q; q.txn = txn; q.item = item; q.hasAdopt = true;
    q.adopt.id = item; q.adopt.base = F(base); q.adopt.count = n; q.adopt.cell = 7;
    q.ref = F(0x842, "nvcooplab.esp");   // the placed world ref itself (a plugin's persistent ref)
    return q;
}

// ---------------------------------------------------------------------------------------------------------------------------- the model
struct Machine {
    PlayerId slot = 0;
    std::unique_ptr<LootClient> c;
    Items player;                                  // the local player's inventory
    std::map<NetId, Items> eng;                    // this engine's copy of each container it knows
    std::set<NetId> menu;                          // open container menus
    struct Ref { uint32_t id = 0; FormRef base; int32_t count = 0; NetId net = 0; Vec3 pos; bool alive = true; };
    std::map<uint32_t, Ref> refs;                  // world refs of items
    uint32_t nextRef = 1;
    std::vector<Notice> notices;
    std::map<NetId, Vec3> shown;                   // clutter poses shown
    std::set<NetId> clutterOwned;
    uint64_t short_ = 0;                           // rollbacks that found less than they had to take back
    uint64_t actionsRun = 0;
    uint64_t nextAudit = 0;
    bool cut = false;

    bool has(uint32_t base, int32_t n) const { return count(player, base) >= n; }
    Ref* refOfNet(NetId net) {
        for (auto& kv : refs) if (kv.second.alive && kv.second.net == net) return &kv.second;
        return nullptr;
    }
    int aliveRefs() const { int n = 0; for (auto& kv : refs) if (kv.second.alive) ++n; return n; }
};

struct NetCfg {
    uint32_t latency = 20, jitter = 0;
    double loss = 0, dup = 0;
};

struct Datagram { uint64_t at = 0; uint64_t seq = 0; PlayerId from = 0, to = 0; std::vector<uint8_t> bytes; };

constexpr PlayerId kToHost = 0xFE;

struct World {
    HostConfig hc;
    ClientConfig cc;
    LootHost host;
    std::vector<std::unique_ptr<Machine>> m;
    std::vector<Datagram> q;
    Rng rng;
    NetCfg net;
    uint64_t now = 1000;
    uint64_t seq = 0;
    uint64_t sent = 0, dropped = 0, delivered = 0, undecodable = 0;
    std::set<size_t> kindsSeen;                    // body indexes that crossed the wire
    std::vector<std::string> log;
    bool trace = false;
    void note(const std::string& s) { if (trace) log.push_back(std::to_string(now) + " " + s); }

    explicit World(int players, uint64_t seed = 1, HostConfig h = {}, ClientConfig c = {}) : hc(h), cc(c), host(h), rng(seed) {
        for (int i = 0; i < players; ++i) addMachine();
    }

    Machine& machine(size_t i) { return *m[i]; }
    Machine& addMachine() {
        auto mc = std::make_unique<Machine>();
        mc->slot = static_cast<PlayerId>(m.size());
        ClientConfig c2 = cc;
        c2.self = mc->slot;
        Machine* raw = mc.get();
        mc->c = std::make_unique<LootClient>(c2, [this, raw](const Body& b) { toHost(raw->slot, b); });
        m.push_back(std::move(mc));
        Machine& me = *m.back();
        // the host's own player is joined from the start; the others join through playerJoined
        if (me.slot != kHostPlayer) {
            LootHost::Out o;
            host.playerJoined(me.slot, o);
            flush(o);
        }
        me.c->hostLinkUp(now);
        return me;
    }

    // ---- network ----
    uint32_t latencyOf(PlayerId slot) const { return slot == kHostPlayer ? 0 : net.latency; }
    void post(PlayerId from, PlayerId to, const Body& b) {
        Envelope e;
        e.src = from == kToHost ? kHostPlayer : from;
        e.body = b;
        std::vector<uint8_t> bytes = encodeEnvelope(e);
        REQUIRE(!bytes.empty());
        kindsSeen.insert(b.index());
        const PlayerId slot = (to == kToHost) ? from : to;
        if (slot < m.size() && m[slot]->cut) { ++dropped; return; }
        ++sent;
        const bool unrel = isUnreliable(b);
        if (net.loss > 0 && rng.chance(unrel ? std::max(net.loss, 0.05) : net.loss)) { ++dropped; return; }
        auto push = [&](uint64_t extra) {
            Datagram d;
            d.from = from; d.to = to; d.bytes = bytes; d.seq = ++seq;
            d.at = now + latencyOf(slot) + (net.jitter && slot != kHostPlayer ? rng.range(0, net.jitter) : 0) + extra;
            q.push_back(std::move(d));
        };
        push(0);
        if (net.dup > 0 && rng.chance(net.dup)) push(rng.range(0, 30));
    }
    void toHost(PlayerId from, const Body& b) { post(from, kToHost, b); }
    void flush(LootHost::Out& o) {
        for (auto& x : o) post(kHostPlayer, x.to, x.body);   // every host output goes to a client (slot 0 included)
        o.clear();
    }
    void deliverDue() {
        for (;;) {
            size_t best = q.size();
            for (size_t i = 0; i < q.size(); ++i)
                if (q[i].at <= now && (best == q.size() || q[i].at < q[best].at || (q[i].at == q[best].at && q[i].seq < q[best].seq))) best = i;
            if (best == q.size()) return;
            Datagram d = std::move(q[best]);
            q.erase(q.begin() + static_cast<long>(best));
            Envelope e;
            if (!decodeEnvelope(d.bytes.data(), d.bytes.size(), e)) { ++undecodable; continue; }
            ++delivered;
            if (trace) {
                std::string what = bodyName(e.body);
                if (auto* r = std::get_if<Result>(&e.body)) what += std::string(" op=") + toString(r->op) + " st=" + toString(r->status) + " txn=" + std::to_string(r->txn) + " id=" + std::to_string(r->id);
                if (auto* r = std::get_if<ReqMove>(&e.body)) what += " txn=" + std::to_string(r->txn) + " c=" + std::to_string(r->cont) + " b=" + std::to_string(r->base.localId) + " n=" + std::to_string(r->count) + (r->put ? " put" : " take");
                if (auto* r = std::get_if<ReqOpen>(&e.body)) what += " txn=" + std::to_string(r->txn) + " c=" + std::to_string(r->cont);
                if (auto* r = std::get_if<ReqClose>(&e.body)) what += " txn=" + std::to_string(r->txn) + " c=" + std::to_string(r->cont);
                if (auto* r = std::get_if<ReqClaim>(&e.body)) what += " txn=" + std::to_string(r->txn) + " item=" + std::to_string(r->item);
                if (auto* r = std::get_if<MContainer>(&e.body)) what += " c=" + std::to_string(r->rec.id) + " rev=" + std::to_string(r->rec.rev) + " {" + describe(r->rec.items) + "}";
                if (auto* r = std::get_if<MDelta>(&e.body)) what += " c=" + std::to_string(r->cont) + " rev=" + std::to_string(r->rev);
                if (auto* r = std::get_if<ReqDrop>(&e.body)) what += " txn=" + std::to_string(r->txn) + " b=" + std::to_string(r->base.localId);
                note("deliver " + std::to_string(d.from) + "->" + (d.to == kToHost ? std::string("host") : std::to_string(d.to)) + " " + what);
            }
            if (d.to == kToHost) {
                LootHost::Out o;
                host.onRequest(d.from, e.body, now, o);
                flush(o);
            } else if (d.to < m.size()) {
                poll(*m[d.to]);
                m[d.to]->c->onMessage(e.body, now);
                glue(*m[d.to]);   // the correction (if any) is applied before the next message can be handled
            }
        }
    }

    // ---- the glue a plugin would have, per machine ----
    void exec(Machine& me, const Action& a) {
        ++me.actionsRun;
        switch (a.kind) {
        case Action::PlayerAdjust: {
            note("m" + std::to_string(me.slot) + " PlayerAdjust b=" + std::to_string(a.base.localId) + " d=" + std::to_string(a.delta));
            int32_t& c = me.player[a.base];
            if (a.delta < 0 && c < -a.delta) { me.short_ += static_cast<uint64_t>(-a.delta - c); c = 0; }
            else c += a.delta;
            if (c <= 0) me.player.erase(a.base);
            break;
        }
        case Action::ContainerSet:
            note("m" + std::to_string(me.slot) + " ContainerSet c=" + std::to_string(a.id) + " {" + describe(a.items) + "}");
            me.eng[a.id] = a.items;
            me.c->rebaseline(a.id, a.items);
            break;
        case Action::CloseMenu: me.menu.erase(a.id); break;
        case Action::Notify: me.notices.push_back(a.notice); break;
        case Action::BindDrop: {
            auto it = me.refs.find(a.ref);
            if (it != me.refs.end()) it->second.net = a.id;
            break;
        }
        case Action::CreateItem: {
            if (Machine::Ref* have = me.refOfNet(a.id)) { (void)have; break; }
            Machine::Ref r;
            r.id = (static_cast<uint32_t>(me.slot) << 24) | me.nextRef++;
            r.base = a.item.base; r.count = a.item.count; r.net = a.id; r.pos = a.item.pos;
            me.refs[r.id] = r;
            break;
        }
        case Action::RemoveItem:
            if (Machine::Ref* r = me.refOfNet(a.id)) r->alive = false;
            break;
        case Action::SetPose: me.shown[a.id] = a.pos; break;
        case Action::ClutterOwner:
            if (a.flag) me.clutterOwned.insert(a.id); else me.clutterOwned.erase(a.id);
            break;
        case Action::ReadyToClose:
            note("m" + std::to_string(me.slot) + " ReadyToClose c=" + std::to_string(a.id) + " engine {" + describe(me.eng[a.id]) + "} host {" + (host.container(a.id) ? describe(host.container(a.id)->items) : std::string("-")) + "}");
            me.c->closeNow(a.id, me.eng[a.id], now);
            break;
        }
    }
    // what the player did since the last look. It must be read BEFORE a message from the host is handled (that may produce a correction of the
    // engine copy) and after every executed action, or a correction would swallow a change nobody has looked at yet.
    void poll(Machine& me) {
        for (NetId cont : std::vector<NetId>(me.menu.begin(), me.menu.end())) me.c->observed(cont, me.eng[cont], now);
    }
    void glue(Machine& me) {
        for (int guard = 0; guard < 8; ++guard) {
            std::vector<Action> acts = me.c->takeActions();
            if (acts.empty()) break;
            for (const Action& a : acts) exec(me, a);
            poll(me);
        }
        poll(me);
        if (now >= me.nextAudit) {   // the plugin audits its loaded containers round-robin; the model does all of them twice a second
            me.nextAudit = now + 500;
            for (auto& kv : me.eng) if (!me.menu.count(kv.first)) me.c->audit(kv.first, kv.second, now);
        }
        me.c->tick(now);
    }
    void step(uint32_t dt = 10) {
        now += dt;
        deliverDue();
        for (auto& mc : m) glue(*mc);
        LootHost::Out o;
        host.tick(now, o);
        flush(o);
        deliverDue();
        for (auto& mc : m) glue(*mc);
    }
    void run(uint32_t ms, uint32_t dt = 10) { for (uint32_t t = 0; t < ms; t += dt) step(dt); }
    bool idle() {
        if (!q.empty()) return false;
        for (auto& mc : m) if (mc->c->pendingCount() != 0) return false;
        return true;
    }
    void settle(uint32_t maxMs = 30000) {
        uint32_t t = 0;
        while (t < maxMs && !idle()) { step(10); t += 10; }
        run(3000);   // digests, resyncs, heals
        for (uint32_t i = 0; i < 4000 && !idle(); i += 10) step(10);
    }

    // ---- what a player can do ----
    void giveEngine(NetId cont, const Items& items) { for (auto& mc : m) mc->eng[cont] = items; }   // the same contents on every engine
    void open(size_t who, NetId cont, ContainerKind k = ContainerKind::Container) {
        Machine& me = *m[who];
        note("m" + std::to_string(who) + " OPEN c=" + std::to_string(cont) + " {" + describe(me.eng[cont]) + "}");
        me.menu.insert(cont);
        me.c->opened(cont, k, 7, V(1, 1, 1), me.eng[cont], now);
    }
    bool take(size_t who, NetId cont, uint32_t base, int32_t n) {
        Machine& me = *m[who];
        if (!me.menu.count(cont)) return false;
        int32_t& c = me.eng[cont][F(base)];
        if (c < n) { if (c == 0) me.eng[cont].erase(F(base)); return false; }
        c -= n;
        if (c == 0) me.eng[cont].erase(F(base));
        me.player[F(base)] += n;
        note("m" + std::to_string(who) + " TAKE c=" + std::to_string(cont) + " b=" + std::to_string(base) + " n=" + std::to_string(n));
        return true;
    }
    bool put(size_t who, NetId cont, uint32_t base, int32_t n) {
        Machine& me = *m[who];
        if (!me.menu.count(cont) || !me.has(base, n)) return false;
        me.player[F(base)] -= n;
        if (me.player[F(base)] <= 0) me.player.erase(F(base));
        me.eng[cont][F(base)] += n;
        note("m" + std::to_string(who) + " PUT c=" + std::to_string(cont) + " b=" + std::to_string(base) + " n=" + std::to_string(n));
        return true;
    }
    void close(size_t who, NetId cont) {
        Machine& me = *m[who];
        if (!me.menu.count(cont)) return;
        note("m" + std::to_string(who) + " CLOSE c=" + std::to_string(cont));
        me.menu.erase(cont);
        me.c->closed(cont, me.eng[cont], now);
    }
    uint32_t dropItem(size_t who, uint32_t base, int32_t n, Vec3 pos = V(5, 5, 0)) {
        Machine& me = *m[who];
        if (!me.has(base, n)) return 0;
        me.player[F(base)] -= n;
        if (me.player[F(base)] <= 0) me.player.erase(F(base));
        Machine::Ref r;
        r.id = (static_cast<uint32_t>(me.slot) << 24) | me.nextRef++;
        r.base = F(base); r.count = n; r.pos = pos;
        me.refs[r.id] = r;
        note("m" + std::to_string(who) + " DROP b=" + std::to_string(base) + " n=" + std::to_string(n));
        me.c->dropped(r.id, r.base, n, 0, 7, pos, V(), now);
        return r.id;
    }
    // a placed item every machine has a ref for (same net id)
    void placeItem(NetId id, uint32_t base, int32_t n, Vec3 pos = V(9, 9, 0)) {
        for (auto& mc : m) {
            Machine::Ref r;
            r.id = (static_cast<uint32_t>(mc->slot) << 24) | mc->nextRef++;
            r.base = F(base); r.count = n; r.net = id; r.pos = pos;
            mc->refs[r.id] = r;
        }
        placed.push_back({id, F(base), n});
    }
    bool pickup(size_t who, uint32_t ref) {
        Machine& me = *m[who];
        auto it = me.refs.find(ref);
        if (it == me.refs.end() || !it->second.alive) return false;
        Machine::Ref& r = it->second;
        r.alive = false;
        note("m" + std::to_string(who) + " PICKUP b=" + std::to_string(r.base.localId) + " n=" + std::to_string(r.count) + " net=" + std::to_string(r.net));
        me.player[r.base] += r.count;
        me.c->pickedUp(r.id, r.net, r.base, r.count, 0, 7, r.pos, now);
        return true;
    }
    uint32_t refFor(size_t who, NetId id) {
        Machine::Ref* r = m[who]->refOfNet(id);
        return r ? r->id : 0;
    }

    struct Placed { NetId net; FormRef base; int32_t count; };
    std::vector<Placed> placed;
    std::map<FormRef, int64_t> initial;            // set by snapshotInitial()

    // ---- accounting ----
    std::map<FormRef, int64_t> totals() const {
        std::map<FormRef, int64_t> t;
        for (auto& mc : m) for (auto& kv : mc->player) t[kv.first] += kv.second;
        for (auto& kv : host.containers()) for (auto& it : kv.second.items) t[it.first] += it.second;
        for (auto& kv : host.items()) t[kv.second.base] += kv.second.count;
        for (auto& p : placed) if (!host.placedTaken(p.net)) t[p.base] += p.count;
        for (auto it = t.begin(); it != t.end();) { if (it->second == 0) it = t.erase(it); else ++it; }
        return t;
    }
    void snapshotInitial() { initial = totals(); }
    std::string diffTotals() const {
        std::string s;
        auto t = totals();
        std::set<FormRef> keys;
        for (auto& kv : t) keys.insert(kv.first);
        for (auto& kv : initial) keys.insert(kv.first);
        for (auto& k : keys) {
            int64_t a = initial.count(k) ? initial.at(k) : 0, b = t.count(k) ? t.at(k) : 0;
            if (a != b) s += " " + std::to_string(k.localId) + ":" + std::to_string(a) + "->" + std::to_string(b);
        }
        return s;
    }
    // every client's view equals the host's table (the heal worked)
    bool viewsEqualHost() const {
        for (auto& mc : m) if (mc->c->digest() != host.digest()) return false;
        return true;
    }
    // for containers nobody has open: the engine copy equals the host's contents, wherever the machine has one
    std::string engineCopiesDiffer() const {
        for (auto& mc : m) {
            for (auto& kv : mc->eng) {
                if (mc->menu.count(kv.first)) continue;
                const ContainerRec* h = host.container(kv.first);
                if (!h) continue;
                if (digestOf(kv.second) != digestOf(h->items))
                    return "machine " + std::to_string(mc->slot) + " container " + std::to_string(kv.first) + ": engine {" + describe(kv.second) + "} host {" + describe(h->items) + "}";
            }
        }
        return "";
    }
};

}  // namespace lt
