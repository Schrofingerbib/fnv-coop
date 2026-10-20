// "em" fuzz target: adapter from the fuzzer's ITarget to T2a's fake engine (synccore/enginemodel, fnvcoop::engine::Cluster).
// Ops are translated into Machine intents; the observer feeds our apply / ownership logs; the model's own Cluster oracles are
// reported through nativeViolations() under our invariant names. Bug classes are T2a's EngineFaults / OwnershipFaults.
#include <algorithm>
#include <cmath>
#include <deque>
#include <set>
#include "fuzz.h"
#include "chaos/impair.h"
#include "fnvcoop/engine/cluster.h"
#ifdef FNVCOOP_FUZZ_FLIGHT
#include "flight/engine_observer.h"
#endif

namespace fnvcoop::fuzz {
namespace {
using namespace fnvcoop::engine;

struct FaultNames {
    const char* name;
    bool EngineFaults::*e;
    bool OwnershipFaults::*o;
};
const FaultNames kFaults[] = {
    {"no_dedupe", &EngineFaults::noDedupe, nullptr},
    {"hit_no_validation", &EngineFaults::hitNoValidation, nullptr},
    {"hit_by_non_owner", &EngineFaults::hitAppliedByNonOwner, nullptr},
    {"pickup_no_arbitration", &EngineFaults::pickupNoArbitration, nullptr},
    {"no_pickup_rollback", &EngineFaults::noPickupRollback, nullptr},
    {"spawn_no_arbitration", &EngineFaults::spawnNoArbitration, nullptr},
    {"loot_per_machine", &EngineFaults::lootPerMachine, nullptr},
    {"pick_per_machine", &EngineFaults::pickPerMachine, nullptr},
    {"container_no_lock", &EngineFaults::containerNoLock, nullptr},
    {"named_killable", &EngineFaults::namedActorKillable, nullptr},
    {"no_session_epoch", &EngineFaults::noSessionEpoch, nullptr},
    {"heal_disabled", &EngineFaults::healDisabled, nullptr},
    {"drop_no_idempotency", &EngineFaults::dropNoIdempotency, nullptr},
    {"owner_keeps_publishing", &EngineFaults::ownerKeepsPublishing, nullptr},
    {"join_no_ownership", &EngineFaults::joinNoOwnership, nullptr},
    {"own_no_epoch", nullptr, &OwnershipFaults::noEpochBump},
    {"own_stale_epoch", nullptr, &OwnershipFaults::acceptStaleEpoch},
    {"own_adopt_echo", nullptr, &OwnershipFaults::adoptEcho},
    {"own_transfer_no_ack", nullptr, &OwnershipFaults::transferNoAck},
    {"own_no_hysteresis", nullptr, &OwnershipFaults::noHysteresis},
    {"own_follower_ignored", nullptr, &OwnershipFaults::followerIgnored},
    {"own_accept_non_owner", nullptr, &OwnershipFaults::acceptNonOwner},
};

#ifdef FNVCOOP_FUZZ_FLIGHT
#define FL(call) do { if (fl_) fl_->call; } while (0)
#else
#define FL(call) do {} while (0)
#endif

class Em : public ITarget, public IEngineObserver {
public:
    Em(const Scenario& s, const EngineFaults& ef, const OwnershipFaults& of) : efScope_(ef), ofScope_(of) {
        ClusterConfig cc;
        cc.seed = s.seed;
        cc.players = s.machines;
        cc.link = s.link;
        cl_ = std::make_unique<Cluster>(cc);
        cl_->setObserver(this);
        if (!s.profile.empty()) {   // T5's impairment model (burst loss, reorder, jitter, duplicate, corrupt, bandwidth) replaces the LinkParams link
            profile_ = chaos::profile(s.profile);
            profile_.seed = s.seed;
            useProfile_ = true;
            installShaper();
        }
        link_ = s.link;
        n_ = s.machines;
    }

    // ---------------------------------------------------------- ITarget
    uint64_t now() const override { return cl_->now(); }
    int machines() const override { return n_; }
    bool connected(int m) const override { return cl_->connected(static_cast<PlayerId>(m)); }
    const std::vector<ApplyEvent>& applyLog() const override { return apply_; }
    const std::vector<OwnEvent>& ownLog() const override { return own_; }
    const std::vector<MsgTrace>& msgLog() const override { return msgs_; }
    uint64_t repairs(int m) const override { return connected(m) ? cl_->machine(static_cast<PlayerId>(m)).healCount() : 0; }
    uint64_t digest(int m, int cat) const override { return connected(m) ? cl_->machine(static_cast<PlayerId>(m)).digest(static_cast<Category>(cat)) : 0; }
    std::map<std::string, uint64_t> stats() const override {
        std::map<std::string, uint64_t> s = counters_;
        for (int m = 0; m < n_; ++m) {
            if (!connected(m)) continue;
            const MachineStats& st = cl_->machine(static_cast<PlayerId>(m)).stats();
            s["hits_applied"] += st.hitsApplied; s["pickups_won"] += st.pickupsWon; s["pickups_lost"] += st.pickupsLost; s["spawns"] += st.spawns;
            s["link_retransmits"] += st.linkRetransmits; s["link_duplicates"] += st.linkDuplicates; s["double_applied"] += st.doubleApplied;
            s["repairs_sent"] += st.repairsSent;
        }
        const OwnershipService* svc = cl_->host().service();
        if (svc) { s["grants"] = svc->stats().grants; s["transfers"] = svc->stats().transfers; s["releases"] = svc->stats().releases; }
        return s;
    }
    bool quiescent() const override { return frozen_ && cl_->now() >= lastBusy_ + 1500; }

    void heal() override {
        frozen_ = true;
        cl_->net().setShaper(nullptr);
        cl_->net().setLink(LinkParams());
        for (int m = 1; m < n_; ++m) if (!connected(m)) { cl_->join(static_cast<PlayerId>(m)); noteFresh(m); }
        freezeAi();
    }
    void markQuiescent() override { cl_->repairBaseline = cl_->totalRepairs(); }

    void advance(uint64_t toMs) override {
        while (cl_->now() < toMs) {
            cl_->step();
            if (frozen_) freezeAi();
            trackBusy();
            if (!frozen_) cl_->checkAlways();
        }
    }

    std::vector<Violation> nativeViolations() override {
        std::vector<Violation> out;
        cl_->checkAlways();
        cl_->checkQuiescent();
        for (auto& v : cl_->allViolations()) {
            const std::string& w = v.what;
            const char* inv = "model_oracle";
            if (w.find("stale update applied") != std::string::npos) inv = "stale_applied";
            else if (w.find("applied twice") != std::string::npos) inv = "double_apply";
            else if (w.find("two owners") != std::string::npos) inv = "multi_owner";
            else if (w.find("AI-driven") != std::string::npos) inv = "multi_owner";
            else if (w.find("duplicate") != std::string::npos) inv = "dup_thing";
            else if (w.find("repairs happened") != std::string::npos) inv = "repairs_after_quiescence";
            else if (w.find("item conservation") != std::string::npos) inv = "dup_thing";
            else if (w.find("differs") != std::string::npos || w.find("never synced") != std::string::npos) inv = "converge";
            bool dup = false;
            for (auto& o : out) if (o.invariant == inv && o.detail == w) dup = true;
            if (!dup && out.size() < 40) out.push_back({inv, w, v.tMs});
        }
        return out;
    }

    std::vector<Thing> view(int m) const override {
        std::vector<Thing> out;
        if (!connected(m)) return out;
        const Machine& mc = cl_->machine(static_cast<PlayerId>(m));
        const World& w = mc.world();
        for (PlayerId p = 0; p < kMaxSlots; ++p) {
            const PlayerState& ps = w.player(p);
            if (!ps.present) continue;
            Thing t; t.netId = 0x100 + p; t.type = 0; t.x = rnd(ps.pos.x); t.y = rnd(ps.pos.y); t.cell = static_cast<int>(ps.cell); t.hp = ps.health; t.dead = ps.dead;
            out.push_back(t);
        }
        for (auto& kv : w.actors()) {
            const ActorState& a = kv.second;
            Thing t; t.netId = a.id; t.type = 1; t.ident = (!a.dead && a.site) ? a.site : 0;
            t.x = rnd(a.pos.x); t.y = rnd(a.pos.y); t.cell = static_cast<int>(a.cell); t.hp = a.health; t.dead = a.dead;
            t.owner = a.dead ? -1 : static_cast<int>(mc.view().ownerOf(a.id));
            if (t.owner == static_cast<int>(kNoPlayer)) t.owner = -1;
            t.epoch = a.dead ? 0 : mc.view().epochOf(a.id);
            out.push_back(t);
        }
        for (auto& kv : w.items()) {
            const ItemState& it = kv.second;
            Thing t; t.netId = it.id; t.type = 2; t.ident = (!it.taken && it.origin) ? static_cast<uint32_t>(((it.origin >> 32) << 20) + (it.origin & 0xFFFFFu) + 1u) : 0;   // injective for src < 4096, reqId < 2^20 (an xor-fold collided across (src,req) pairs)
            t.x = rnd(it.pos.x); t.y = rnd(it.pos.y); t.cell = static_cast<int>(it.cell); t.holder = it.taken ? static_cast<int>(it.takenBy) : -1;
            out.push_back(t);
        }
        for (auto& kv : w.objects()) {
            const WorldObject& o = kv.second;
            Thing t; t.netId = o.id; t.type = 3; t.x = rnd(o.pos.x); t.y = rnd(o.pos.y); t.cell = static_cast<int>(o.cell); t.open = o.open;
            out.push_back(t);
        }
        std::sort(out.begin(), out.end(), [](const Thing& a, const Thing& b) { return a.netId < b.netId; });
        return out;
    }

    void apply(const Op& op) override {
        ++counters_[std::string("op_") + opName(op.kind)];
        int m = ((op.m % n_) + n_) % n_;
        switch (op.kind) {
            case OpKind::Join: if (m != 0 && !connected(m)) { cl_->join(static_cast<PlayerId>(m)); noteFresh(m); } return;
            case OpKind::Leave: if (m != 0 && connected(m)) { cl_->checkConservation = false; cl_->leave(static_cast<PlayerId>(m)); }   // a leaver takes their items along
                return;
            case OpKind::Save: lastSave_ = cl_->saveHost(); haveSave_ = true; return;
            case OpKind::Load:
                if (haveSave_) {
                    ++counters_["loads"];
                    OwnEvent o; o.tMs = cl_->now(); o.ord = ++ord_; o.kind = OwnEvent::WorldEpoch; o.epoch = static_cast<uint32_t>(counters_["loads"]) + 1; own_.push_back(o);
                    cl_->checkConservation = false;   // only the host's save is the world: clients keep what they carry
                    cl_->loadHost(lastSave_);
                }
                return;
            case OpKind::SetLink:
                link_.minLatencyMs = static_cast<uint32_t>(op.a); link_.maxLatencyMs = std::max<uint32_t>(static_cast<uint32_t>(op.b), link_.minLatencyMs);
                link_.loss = op.c / 1000.0; link_.duplicate = op.d / 1000.0;
                if (!frozen_ && !useProfile_) cl_->net().setLink(link_);   // a chaos profile owns the link
                return;
            case OpKind::Stale: {
                int from = ((op.a % n_) + n_) % n_;
                auto& ring = sent_[{from, m}];
                if (from == m || ring.empty() || !connected(m) || !connected(from)) return;
                const Bytes& env = ring[static_cast<size_t>(op.b) % ring.size()];
                cl_->inject(static_cast<PlayerId>(m), static_cast<PlayerId>(from), env, op.c == 1 ? 1 : 0);
                ++counters_["stale_injected"];
                return;
            }
            default: break;
        }
        if (!connected(m)) return;
        Machine& mc = cl_->machine(static_cast<PlayerId>(m));
        const World& w = mc.world();
        switch (op.kind) {
            case OpKind::Move: {
                CellId cell = w.cellAt(((op.c % 3) + 3) % 3, 0);
                if (!cell) return;
                Vec3 c = w.cellCenter(cell);
                mc.move(cell, {c.x + (op.a - 500) * 2.0f, c.y + (op.b - 500) * 2.0f, c.z});
                return;
            }
            case OpKind::Spawn: {
                if (w.sites().empty()) return;
                auto it = w.sites().begin();
                std::advance(it, static_cast<size_t>(((op.a % 5) + 5) % 5) % w.sites().size());
                mc.move(it->second.cell, it->second.pos);
                return;
            }
            case OpKind::Hit: {
                std::vector<NetId> ids;
                for (auto& kv : w.actors()) if (!kv.second.dead) ids.push_back(kv.first);
                if (ids.empty()) return;
                NetId v = ids[static_cast<size_t>(op.a) % ids.size()];
                mc.attackActor(v, std::max(1, op.b), Limb::Torso, w.actors().at(v).pos, 1500);
                return;
            }
            case OpKind::Pickup: {
                std::vector<NetId> ids;
                for (auto& kv : w.items()) if (!kv.second.taken) ids.push_back(kv.first);
                if (ids.empty()) return;
                mc.pickup(ids[static_cast<size_t>(op.a) % ids.size()]);
                return;
            }
            case OpKind::Drop: {
                const PlayerState& ps = w.player(static_cast<PlayerId>(m));
                if (ps.inventory.empty()) return;
                auto it = ps.inventory.begin();
                std::advance(it, static_cast<size_t>(op.a) % ps.inventory.size());
                FormRef base = it->first;
                mc.drop(base);
                return;
            }
            case OpKind::Activate: {
                if (w.objects().empty()) return;
                auto it = w.objects().begin();
                std::advance(it, static_cast<size_t>(((op.a % 4) + 4) % 4) % w.objects().size());
                if (it->second.kind == ObjKind::Door) mc.useDoor(it->first); else mc.activate(it->first);
                return;
            }
            default: return;
        }
    }

    // ---------------------------------------------------------- IEngineObserver
    void onDrop(PlayerId self, uint64_t t, PeerId from, const char* why) override { FL(onDrop(self, t, from, why)); }
    void onEffectApplied(PlayerId self, uint64_t t, uint64_t seq, const Effect& e) override { FL(onEffectApplied(self, t, seq, e)); }
    void onDigest(PlayerId self, uint64_t t, Category cat, uint64_t h) override { FL(onDigest(self, t, cat, h)); }
    void onRepair(PlayerId self, uint64_t t, PlayerId target, Category cat, size_t keys) override { FL(onRepair(self, t, target, cat, keys)); }
    void onSend(PlayerId self, uint64_t t, PeerId to, uint8_t ch, const Bytes& env) override {
        FL(onSend(self, t, to, ch, env));
        auto& ring = sent_[{static_cast<int>(self), static_cast<int>(to)}];
        ring.push_back(env);
        if (ring.size() > 32) ring.pop_front();
        if (keep_) { MsgTrace mt; mt.tMs = t; mt.machine = static_cast<int>(self); mt.out = true; mt.peer = static_cast<int>(to); mt.channel = ch; mt.raw = env; msgs_.push_back(std::move(mt)); }
    }
    void onRecv(PlayerId self, uint64_t t, PeerId from, uint8_t ch, const Bytes& env) override {
        FL(onRecv(self, t, from, ch, env));
        if (keep_) { MsgTrace mt; mt.tMs = t; mt.machine = static_cast<int>(self); mt.out = false; mt.peer = static_cast<int>(from); mt.channel = ch; mt.raw = env; msgs_.push_back(std::move(mt)); }
    }
    void onOwnership(PlayerId self, uint64_t t, const OwnMsg& m, ApplyResult r) override {
        FL(onOwnership(self, t, m, r));
        noteSession(self, t);
        if (r != ApplyResult::Applied) return;
        if (m.kind != OwnMsg::Grant && m.kind != OwnMsg::Commit && m.kind != OwnMsg::Release) return;
        if (self == 0) {
            OwnEvent o;
            o.tMs = t; o.ord = ++ord_; o.ref = static_cast<int>(m.netId); o.epoch = m.epoch; o.prevOwner = m.prevOwner == kNoPlayer ? -1 : static_cast<int>(m.prevOwner);
            o.owner = m.kind == OwnMsg::Release || m.owner == kNoPlayer ? -1 : static_cast<int>(m.owner);
            o.kind = m.kind == OwnMsg::Grant ? OwnEvent::Grant : m.kind == OwnMsg::Commit ? OwnEvent::Transfer : OwnEvent::Release;
            own_.push_back(o);
        }
        ApplyEvent e;
        e.machine = static_cast<int>(self); e.tMs = t; e.ord = ++ord_; e.ref = static_cast<int>(m.netId); e.epoch = m.epoch; e.origin = 0; e.kind = ApplyKind::Owner; e.why = "";
        apply_.push_back(e);
    }
    void onActorUpdate(PlayerId self, uint64_t t, NetId id, PlayerId from, OwnerEpoch epoch, UpdateVerdict v) override {
        FL(onActorUpdate(self, t, id, from, epoch, v));
        noteSession(self, t);
        ApplyEvent e;
        e.machine = static_cast<int>(self); e.tMs = t; e.ord = ++ord_; e.ref = static_cast<int>(id); e.epoch = epoch; e.origin = static_cast<int>(from);
        e.kind = ApplyKind::Pos; e.accepted = v == UpdateVerdict::Accept; e.why = toString(v);
        apply_.push_back(e);
        if (!e.accepted) ++counters_[std::string("reject_") + toString(v)];
    }

public:
    void keepMessages(bool on) {
        keep_ = on;
#ifdef FNVCOOP_FUZZ_FLIGHT
        if (on && !fl_) fl_.reset(new flight::EngineFlightObserver(16u << 20));
#endif
    }
    bool dumpFlight(const std::string& base, const std::vector<Violation>& vs) const override {
#ifdef FNVCOOP_FUZZ_FLIGHT
        if (!fl_) return false;
        for (int m = 0; m < n_; ++m) {
            fl_->machine(static_cast<PlayerId>(m));
            for (auto& v : vs) fl_->machine(static_cast<PlayerId>(m)).marker(flight::kMarkDesync, v.invariant + ": " + v.detail);
        }
        return fl_->dumpAll(base) > 0;
#else
        (void)base; (void)vs;
        return false;
#endif
    }

private:
    void installShaper() {
        cl_->net().setShaper([this](PeerId from, PeerId to, uint64_t nowMs, Bytes& data) {
            auto key = std::make_pair(static_cast<int>(from), static_cast<int>(to));
            auto it = imp_.find(key);
            if (it == imp_.end()) it = imp_.emplace(key, chaos::Impairment(profile_, std::to_string(from) + ">" + std::to_string(to))).first;
            chaos::Verdict v = it->second.decide(static_cast<double>(nowMs), static_cast<uint32_t>(data.size()));
            std::vector<uint32_t> delays;
            for (double d : v.delaysMs) delays.push_back(static_cast<uint32_t>(d));
            if (!delays.empty() && v.corrupt) it->second.flipBit(data);
            return delays;
        });
    }
    // A guest that adopts a new host session (after a host load) starts a new ownership table: forget what it applied in the old one.
    void noteSession(PlayerId self, uint64_t t) {
        if (!cl_ || !cl_->connected(self)) return;
        const uint32_t s = cl_->machine(self).sess();
        auto it = sessSeen_.find(self);
        if (it == sessSeen_.end()) { sessSeen_[self] = s; return; }
        if (it->second == s) return;
        it->second = s;
        ApplyEvent e; e.machine = static_cast<int>(self); e.tMs = t; e.ord = ++ord_; e.kind = ApplyKind::Reset; e.why = "new host session";
        apply_.push_back(e);
    }
    std::map<PlayerId, uint32_t> sessSeen_;
    // A joining machine is fresh (all client state lost): the oracles must forget what it applied before.
    void noteFresh(int m) {
        ApplyEvent e; e.machine = m; e.tMs = cl_->now(); e.ord = ++ord_; e.kind = ApplyKind::Reset; e.why = "fresh machine";
        apply_.push_back(e);
    }
    static int rnd(float f) { return static_cast<int>(std::lround(f)); }
    void freezeAi() { for (int m = 0; m < n_; ++m) if (connected(m)) cl_->machine(static_cast<PlayerId>(m)).setAiFrozen(true); }
    void trackBusy() {
        uint64_t k = cl_->net().inFlight() * 1000003ull;
        for (int m = 0; m < n_; ++m) {
            if (!connected(m)) { k = k * 31 + 7; continue; }
            const Machine& mc = cl_->machine(static_cast<PlayerId>(m));
            for (int c = 0; c < static_cast<int>(Category::Count_); ++c) k = k * 1099511628211ull + mc.digest(static_cast<Category>(c));
            k = k * 31 + mc.view().digest() + mc.outboxSize() * 13 + mc.unackedCount() * 17;
        }
        if (k != busyKey_ || !cl_->net().idle()) { lastBusy_ = cl_->now(); busyKey_ = k; }
    }

    EngineFaultScope efScope_;
    OwnershipFaultScope ofScope_;
    std::unique_ptr<Cluster> cl_;
    LinkParams link_;
    chaos::Params profile_;
    bool useProfile_ = false;
    std::map<std::pair<int, int>, chaos::Impairment> imp_;
#ifdef FNVCOOP_FUZZ_FLIGHT
    std::unique_ptr<flight::EngineFlightObserver> fl_;
#endif
    int n_ = 2;
    bool frozen_ = false, keep_ = false, haveSave_ = false;
    size_t lastSave_ = 0;
    uint64_t ord_ = 0, lastBusy_ = 0, busyKey_ = 0;
    std::map<std::pair<int, int>, std::deque<Bytes>> sent_;
    std::vector<ApplyEvent> apply_;
    std::vector<OwnEvent> own_;
    std::vector<MsgTrace> msgs_;
    std::map<std::string, uint64_t> counters_;
};
}  // namespace

extern bool g_keepMessages;

std::unique_ptr<ITarget> makeEm(const Scenario& s, std::string* err) {
    EngineFaults ef;
    OwnershipFaults of;
    for (auto& n : s.bugs) {
        bool found = false;
        for (auto& f : kFaults)
            if (n == f.name) { if (f.e) ef.*f.e = true; else of.*f.o = true; found = true; }
        if (!found) { if (err) *err = "unknown fault for target em: " + n; return nullptr; }
    }
    auto t = std::make_unique<Em>(s, ef, of);
    t->keepMessages(g_keepMessages);
    return t;
}
std::vector<std::string> emBugs() {
    std::vector<std::string> v;
    for (auto& f : kFaults) v.push_back(f.name);
    return v;
}

}  // namespace fnvcoop::fuzz
