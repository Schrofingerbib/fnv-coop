// Two machines load a prepared save pair, each restoring its OWN sidecar (PROOF-PLAN section 4 item 4: "client lastSeq ahead of host after
// a prepared-save load"). The client's sidecar is newer than the host's: it was written after the host's save, in the epoch the host
// reached by loading its own save once (the prepare tool's reload check). A fresh host that loads the prepared save reaches that same epoch
// number again, with a different history, so the client's (epoch, lastSeq) cursor looked resumable:
//  - cursor ahead of the host's head: snapshot diff from the client's foreign shadow, which rewinds per-player state (the reward the
//    client's save holds is taken away again);
//  - cursor behind the head: txns replayed on top of a shadow from another timeline (the shared world diverges).
// Fixed: the client's Hello carries a hash of its shadow at the cursor (Hello::cursorCheck); the host resumes it only if its own state at
// that seq hashes the same (and the cursor is not past its head). Otherwise the client is rebased (fresh dump, baseline rejoin) and adopts
// the host's sequence; per-player state stays what the client's save holds. Twin: trustForeignCursor.
#include <memory>
#include "fnvcoop/session.h"
#include "minitest.h"

using namespace fnvcoop;

namespace {
FormRef Q(uint32_t i) { return FormRef("falloutnv.esm", i); }
const FormRef kCaps("falloutnv.esm", 0xF);

ShadowState startWorld() {
    ShadowState s;
    for (int i = 0; i < 40; ++i) {
        s.stages[Q(0x1000 + i)] = uint32_t(i % 7);
        s.globals[Q(0x3000 + i)] = float(i);
        s.questRunning[Q(0x1000 + i)] = (i % 3) == 0;
    }
    return s;
}

// One machine's game: the shared world plus this player's inventory.
struct Engine : IEffectSink {
    ShadowState w;
    std::map<FormRef, int64_t> inv;
    void apply(const Effect& e, const Context& c) override {
        if (auto* a = std::get_if<AddItem>(&e)) { inv[a->item] += a->count; return; }
        if (auto* r = std::get_if<RemoveItem>(&e)) { inv[r->item] -= r->count; return; }
        if (std::holds_alternative<ModKarma>(e) || std::holds_alternative<ModReputation>(e) || std::holds_alternative<ModActorValue>(e)) return;
        if (c.baseline) {
            std::visit([&](const auto& x) {
                using T = std::decay_t<decltype(x)>;
                if constexpr (std::is_same_v<T, SetStage>) w.stages[x.quest] = x.stage;
                else if constexpr (std::is_same_v<T, SetGlobal>) w.globals[x.global] = x.value;
                else { EffectTxn t; t.effects = {Effect(x)}; w.applyTxn(t); }
            }, e);
        } else {
            EffectTxn t; t.effects = {e}; w.applyTxn(t);
        }
    }
};

struct Machine {
    Engine eng;
    EffectCapture cap;
    std::unique_ptr<ClientSession> cs;
    void make(ITransport& t, uint64_t token) {
        cs = std::make_unique<ClientSession>(t, 0, token, eng, &cap);
        cs->setBaselineProvider([this](ShadowState& out, uint64_t& h) { out = eng.w; h = 7; return true; });
        cs->setDigestInterval(0);   // the session cursor alone must get it right (a digest repair would hide a wrong resume)
    }
};

struct Run {
    InMemoryNetwork net;
    HostSession host;
    Machine cl;
    uint64_t now = 0;
    explicit Run(uint64_t seed) : net(seed), host(*net.endpoint(0)) {}
    void tick(uint32_t ms) {
        for (uint32_t t = 0; t < ms; t += 5) { now += 5; net.advance(now); host.tick(now); cl.cs->tick(now); net.advance(now); }
    }
    bool live() { return cl.cs->joined() && !cl.cs->baselineActive() && cl.cs->applied() == host.log().head(); }
    bool settle() { for (int i = 0; i < 400 && !live(); ++i) tick(25); tick(100); return live(); }
    void commitStage(uint32_t q, uint32_t st) { Cause c; c.kind = CauseKind::QuestResult; c.source = Q(q); host.commit(0, c, {SetStage{Q(q), st}}); }
};

struct Save { Bytes side; ShadowState world; std::map<FormRef, int64_t> inv; uint32_t epoch = 0; uint64_t seq = 0; };

struct Outcome {
    bool prepOk = false, live = false, worldMatches = false;
    uint32_t sidecarEpoch = 0, hostEpoch = 0;
    uint64_t sidecarSeq = 0, applied = 0, head = 0, refused = 0;
    int64_t caps = 0, capsInSave = 0;
};

// behind=false: the host commits nothing after its load (the client's cursor is AHEAD of the head);
// behind=true: the host commits 8 txns before the client loads (the cursor is behind the head, but from another timeline).
Outcome preparedLoad(bool behind, bool trustForeign) {
    Mutations m;
    m.trustForeignCursor = trustForeign;
    MutationScope scope(m);
    Outcome o;
    Save hostSave, clientSave;
    {   // ---- run 1: prepare_save. Both copies in a session, the host saves, reload-checks its save, the client saves later.
        Run r(31);
        r.host.setWorldBaseline(startWorld(), 7);
        r.cl.make(*r.net.endpoint(1), 4242);
        r.cl.eng.w = startWorld();
        r.cl.cs->setBaselineWorld(r.cl.eng.w, 7);
        r.cl.cs->connect(r.now, 100);
        if (!r.settle()) return o;
        for (uint32_t i = 0; i < 3; ++i) r.commitStage(0x1000 + i, 20 + i);
        if (!r.settle()) return o;
        hostSave.side = r.host.saveSidecar();
        hostSave.world = r.host.world();
        if (!r.host.loadSidecar(hostSave.side)) return o;   // the reload check: epoch 1 -> 2, the client rebases
        if (!r.settle()) return o;
        Cause reward; reward.kind = CauseKind::QuestResult; reward.source = Q(0x1005);
        r.host.commit(0, reward, {SetStage{Q(0x1005), 50}, AddItem{kCaps, 50, Recipient::slot(r.cl.cs->slot())}});
        r.commitStage(0x1006, 60);
        r.commitStage(0x1007, 70);
        if (!r.settle()) return o;
        if (!r.cl.cs->canSaveState()) return o;
        clientSave.side = r.cl.cs->saveState();   // newer than the host's save
        clientSave.world = r.cl.eng.w;
        clientSave.inv = r.cl.eng.inv;
        clientSave.epoch = r.cl.cs->worldEpoch();
        clientSave.seq = r.cl.cs->applied();
    }
    o.sidecarEpoch = clientSave.epoch;
    o.sidecarSeq = clientSave.seq;
    o.capsInSave = clientSave.inv.count(kCaps) ? clientSave.inv.at(kCaps) : 0;
    o.prepOk = o.capsInSave == 50 && o.sidecarSeq > 0;
    if (!o.prepOk) return o;
    {   // ---- run 2: fresh processes. The host starts from a dump, the client joins, then each loads its prepared save.
        Run r(57);
        r.host.setWorldBaseline(hostSave.world, 7);
        r.cl.make(*r.net.endpoint(1), 4242);
        r.cl.eng.w = startWorld();
        r.cl.cs->setBaselineWorld(r.cl.eng.w, 7);
        r.cl.cs->connect(r.now, 900);
        if (!r.settle()) return o;
        if (!r.host.loadSidecar(hostSave.side)) return o;   // host first: epoch 1 -> 2, the number the client's sidecar was saved in
        r.tick(200);
        if (behind) { for (uint32_t i = 0; i < 8; ++i) r.commitStage(0x1010 + i, 80 + i); r.tick(100); }
        r.cl.eng.w = clientSave.world;                       // then the client loads its own (newer) prepared save
        r.cl.eng.inv = clientSave.inv;
        r.cl.cs->onLocalSaveLoaded(r.now, &clientSave.side);
        o.live = r.settle();
        o.hostEpoch = r.host.epoch();
        o.applied = r.cl.cs->applied();
        o.head = r.host.log().head();
        o.refused = r.host.stats().resumesRefused;
        o.worldMatches = shardHashes(baselineEntries(r.cl.eng.w)) == shardHashes(baselineEntries(r.host.world()));
        o.caps = r.cl.eng.inv.count(kCaps) ? r.cl.eng.inv.at(kCaps) : 0;
    }
    return o;
}

std::string why(const Outcome& o) {
    return "sidecar epoch " + std::to_string(o.sidecarEpoch) + " seq " + std::to_string(o.sidecarSeq) + ", host epoch " + std::to_string(o.hostEpoch) +
           " head " + std::to_string(o.head) + ", client applied " + std::to_string(o.applied) + ", caps " + std::to_string(o.caps) + " (save " +
           std::to_string(o.capsInSave) + "), world " + (o.worldMatches ? "matches" : "differs") + ", refused " + std::to_string(o.refused);
}

bool good(const Outcome& o) { return o.prepOk && o.live && o.worldMatches && o.applied == o.head && o.caps == o.capsInSave; }
}  // namespace

TEST(preparedload, client_sidecar_newer_than_the_hosts_adopts_the_host_sequence) {
    Outcome o = preparedLoad(false, false);
    CHECK_MSG(o.prepOk, why(o));
    REQUIRE(o.prepOk);
    CHECK_MSG(o.sidecarEpoch == o.hostEpoch && o.sidecarSeq > o.head, why(o));   // the collision this test is about
    CHECK_MSG(good(o), why(o));
    CHECK_MSG(o.refused >= 1, why(o));
}

TEST(preparedload, client_sidecar_of_another_timeline_behind_the_head_is_not_replayed) {
    Outcome o = preparedLoad(true, false);
    CHECK_MSG(o.prepOk, why(o));
    REQUIRE(o.prepOk);
    CHECK_MSG(o.sidecarEpoch == o.hostEpoch && o.sidecarSeq <= o.head, why(o));
    CHECK_MSG(good(o), why(o));
    CHECK_MSG(o.refused >= 1, why(o));
}

// Twin: the host trusts any cursor of its epoch again. Both shapes must then go wrong.
TEST(twin, prepared_load_trusts_foreign_cursor) {
    Outcome ahead = preparedLoad(false, true), behind = preparedLoad(true, true);
    REQUIRE(ahead.prepOk && behind.prepOk);
    CHECK_MSG(!good(ahead), why(ahead));
    CHECK_MSG(!good(behind), why(behind));
}
