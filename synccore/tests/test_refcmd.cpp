// RefCommand (story-capture): the generic "script command on a reference" effect. Table, wire, merge (state vs event), recipients, baseline,
// digest, sidecar encoding, and a host -> clients session. Twins: refCmdKeepsFirst, refCmdAllState (Mutations), plus the checks that a wire
// decoder rejects an unknown op.
#include <set>
#include <string>

#include "fnvcoop/baseline.h"
#include "fnvcoop/mutations.h"
#include "fnvcoop/refop.h"
#include "fnvcoop/session.h"
#include "fnvcoop/shadow.h"
#include "fnvcoop/wire.h"
#include "minitest.h"

using namespace fnvcoop;

namespace {
FormRef Q(uint32_t i) { return FormRef("falloutnv.esm", i); }

RefCommand cmd(uint16_t op, FormRef ref = FormRef(), FormRef a = FormRef(), int32_t i = 0) {
    RefCommand c;
    c.op = op;
    c.ref = std::move(ref);
    c.a = std::move(a);
    c.i = i;
    c.to = Recipient::slots(SharePolicy().allMask());   // what normalizeTxn leaves after commit
    return c;
}

std::vector<Effect> fold(ShadowState& s, PlayerId origin, std::vector<Effect> effects, PlayerId me = 1) {
    EffectTxn t;
    t.seq = s.seq + 1;
    t.origin = origin;
    t.effects = std::move(effects);
    std::vector<Effect> out;
    s.applyTxn(t, me, &out);
    return out;
}

bool joinerRebuilds(const ShadowState& host) {
    ShadowState j;
    std::vector<Effect> d;
    ShadowState::diffEffects(ShadowState(), host, 1, d);
    EffectTxn t;
    t.seq = 1;
    t.effects = d;
    j.applyTxn(t);
    j.seq = host.seq;
    return j == host;
}

// the two rules the twins break (the twin passes when the rule FAILS under its mutation, like every other twin of the suite)
bool laterCommandWinsPerKey() {
    ShadowState s;
    fold(s, 0, {cmd(kOpSetGhost, Q(7), FormRef(), 1)});
    fold(s, 0, {cmd(kOpSetGhost, Q(7), FormRef(), 0)});
    return s.refOps.at(refOpKeyOf(cmd(kOpSetGhost, Q(7)))).i == 0;
}
// a command on the player left at Default follows the reward rule: the party for a quest result, the acting player otherwise
bool playerCommandFollowsTheRewardRule() {
    SharePolicy p;
    p.maxPlayers = 4;
    auto onPlayer = [&](CauseKind k) {
        EffectTxn u;
        u.origin = 3;
        u.cause.kind = k;
        RefCommand c = cmd(kOpAddSpell, Q(0x14), Q(9));
        c.to = Recipient::dflt();
        u.effects = {c};
        normalizeTxn(u, p);
        return std::get<RefCommand>(u.effects[0]).to;
    };
    return onPlayer(CauseKind::QuestResult).mask == p.allMask() && onPlayer(CauseKind::Script) == Recipient::slot(3);
}
bool eventsLeaveNoState() {
    ShadowState s;
    fold(s, 0, {cmd(kOpStartCombat, Q(8), Q(0x14))});
    return s.refOps.empty();
}
}  // namespace

TEST(refcmd, op_table_is_sound) {
    std::set<std::string> names;
    CHECK(sizeof(kRefOps) / sizeof(kRefOps[0]) == kOpCount_);
    for (uint16_t id = 1; id < kOpCount_; ++id) {
        const RefOpInfo* i = refOpInfo(id);
        REQUIRE(i != nullptr);
        CHECK(i->id == id);
        CHECK(i->name && *i->name);
        CHECK(names.insert(i->name).second);                       // names are unique
        CHECK(i->group >= 1 && i->group <= id);                    // a group is its first member's id
        CHECK(kRefOps[i->group].group == i->group);                // ... and that member is the lone head of the group
        if (i->kind == kRefOpState && i->group == id) continue;
        if (i->group != id) CHECK(i->kind == kRefOpState && kRefOps[i->group].key == i->key);   // group members agree on the key
        CHECK(i->recipient <= kRcpAll);
    }
    CHECK(!refOpKnown(kOpNone));
    CHECK(!refOpKnown(kOpCount_));
    CHECK(!refOpKnown(60000));
    CHECK(refOpIsState(kOpAddSpell) && refOpIsState(kOpRemoveSpell) && !refOpIsState(kOpStartCombat) && !refOpIsState(0));
    CHECK(refOpInfo(kOpRemoveSpell)->group == kOpAddSpell);
    CHECK(refOpInfo(kOpRemoveSpell)->group == refOpInfo(kOpAddSpell)->group && refOpInfo(kOpAddSpell)->key == (kKeyRef | kKeyA));
    // the id of an op is on the wire and in sidecars: pin a few so a renumbering is a loud failure
    CHECK(kOpEvaluatePackage == 1 && kOpAddSpell == 23 && kOpActivate == 47 && kOpSetCellImageSpace == 71 && kOpModFactionRank == 74);
}

TEST(refcmd, wire_round_trip_and_unknown_ops_are_rejected) {
    RefCommand c = cmd(kOpSetLevel, Q(0x14), Q(2), 7);
    c.b = Q(3); c.j = -2; c.k = 99; c.l = 4; c.f = 1.25f; c.s = "IdleWave"; c.to = Recipient::slot(2);
    EffectTxn t;
    t.seq = 5;
    t.effects = {c, cmd(kOpEvaluatePackage, Q(9))};
    Archive w = Archive::writer();
    io(w, t);
    std::vector<uint8_t> bytes = w.finish();
    Archive r = Archive::reader(bytes.data(), bytes.size());
    EffectTxn back;
    io(r, back);
    REQUIRE(r.ok());
    CHECK(back == t);
    CHECK(std::get<RefCommand>(back.effects[0]).s == "IdleWave");   // the text argument travels
    CHECK(std::string(effectName(back.effects[0])) == "RefCommand");
    // a forged op id never decodes (0 = none, and one past the table)
    for (uint16_t bad : {uint16_t(0), uint16_t(kOpCount_), uint16_t(40000)}) {
        EffectTxn f = t;
        std::get<RefCommand>(f.effects[0]).op = bad;
        Archive fw = Archive::writer();
        io(fw, f);
        std::vector<uint8_t> fb = fw.finish();
        Archive fr = Archive::reader(fb.data(), fb.size());
        EffectTxn out;
        io(fr, out);
        CHECK(!fr.ok());
    }
    // a truncated command never decodes
    Archive tr = Archive::reader(bytes.data(), bytes.size() - 3);
    EffectTxn cut;
    io(tr, cut);
    CHECK(!tr.ok());
}

TEST(refcmd, state_ops_keep_the_last_command_per_key_and_events_keep_nothing) {
    ShadowState s;
    // AddSpell / RemoveSpell share a group: the later of the two per (ref, spell) wins; another spell is another key
    CHECK(fold(s, 0, {cmd(kOpAddSpell, Q(1), Q(50))}).size() == 1);
    CHECK(fold(s, 0, {cmd(kOpAddSpell, Q(1), Q(50))}).empty());                      // same command again: no change
    CHECK(fold(s, 0, {cmd(kOpAddSpell, Q(1), Q(51))}).size() == 1);
    CHECK(s.refOps.size() == 2);
    CHECK(fold(s, 0, {cmd(kOpRemoveSpell, Q(1), Q(50))}).size() == 1);
    CHECK(s.refOps.size() == 2);
    CHECK(s.refOps.at(refOpKeyOf(cmd(kOpAddSpell, Q(1), Q(50)))).op == kOpRemoveSpell);
    // a keyed-by-ref flag: a later value replaces the earlier one (host order wins)
    CHECK(fold(s, 0, {cmd(kOpSetGhost, Q(7), FormRef(), 1)}).size() == 1);
    CHECK(fold(s, 1, {cmd(kOpSetGhost, Q(7), FormRef(), 0)}).size() == 1);
    CHECK(s.refOps.at(refOpKeyOf(cmd(kOpSetGhost, Q(7)))).i == 0);
    CHECK(laterCommandWinsPerKey() && eventsLeaveNoState());
    // events are delivered every time and never stored
    const size_t stored = s.refOps.size();
    CHECK(fold(s, 0, {cmd(kOpStartCombat, Q(8), Q(0x14)), cmd(kOpStartCombat, Q(8), Q(0x14)), cmd(kOpEvaluatePackage, Q(8))}).size() == 3);
    CHECK(s.refOps.size() == stored);
    // an unknown op changes nothing and is not delivered
    RefCommand junk = cmd(kOpCount_ + 3, Q(1));
    CHECK(fold(s, 0, {junk}).empty() && s.refOps.size() == stored);
}

TEST(refcmd, recipients_narrow_who_performs_an_event) {
    ShadowState s;
    RefCommand lvl = cmd(kOpSetLevel, FormRef(), FormRef(), 5);
    lvl.to = Recipient::slot(2);
    CHECK(fold(s, 0, {lvl}, 2).size() == 1);     // addressed slot performs it
    CHECK(fold(s, 0, {lvl}, 1).empty());         // another slot does not
    CHECK(fold(s, 0, {lvl}, 0).empty());
    // the policy resolves Origin / Default at commit time
    EffectTxn t;
    t.origin = 2;
    RefCommand o = cmd(kOpAddSPECIALPoints, FormRef(), FormRef(), 1);
    o.to = Recipient::origin();
    RefCommand d = cmd(kOpStartCombat, Q(3), Q(0x14));
    d.to = Recipient::all();   // what the capture leaves on an order to an actor
    t.effects = {o, d};
    SharePolicy p;
    p.maxPlayers = 4;
    CHECK(normalizeTxn(t, p) == 0);
    CHECK(std::get<RefCommand>(t.effects[0]).to == Recipient::slot(2));      // the acting player only
    CHECK(std::get<RefCommand>(t.effects[1]).to.mask == p.allMask());        // an AI order goes to everyone; the applier decides who runs it
    CHECK(playerCommandFollowsTheRewardRule());
}

TEST(refcmd, a_conditioned_item_for_the_player_is_a_reward_like_additem) {
    SharePolicy p;
    p.maxPlayers = 4;
    auto routed = [&](CauseKind k, Recipient to) {
        EffectTxn t;
        t.origin = 2;
        t.cause.kind = k;
        RefCommand c = cmd(kOpAddItemHealthPercent, Q(0x14), Q(9), 1);
        c.to = to;
        t.effects = {c};
        normalizeTxn(t, p);
        return std::get<RefCommand>(t.effects[0]).to;
    };
    CHECK(routed(CauseKind::QuestResult, Recipient::dflt()).mask == p.allMask());          // a quest reward goes to the whole party
    CHECK(routed(CauseKind::Script, Recipient::dflt()) == Recipient::slot(2));             // a vending machine pays the one who used it
    CHECK(routed(CauseKind::Script, Recipient::all()).mask == p.allMask());                // a container's stock (addressed to All by the capture) is shared
}

TEST(refcmd, state_reaches_a_joiner_through_the_diff_the_baseline_and_the_digest) {
    ShadowState host;
    fold(host, 0, {cmd(kOpSetCombatStyle, Q(10), Q(11)), cmd(kOpSetUnconscious, Q(12), FormRef(), 1), cmd(kOpAddFormToFormList, FormRef(), Q(20))});
    RefCommand both = cmd(kOpAddFormToFormList, FormRef(), Q(20));
    both.b = Q(21);
    fold(host, 0, {both, cmd(kOpStartCombat, Q(1), Q(2))});   // the event is not part of the state
    CHECK(host.refOps.size() == 4);
    CHECK(joinerRebuilds(host));
    // baseline: an empty client gets exactly the state commands
    ShadowState client;
    BaselineDiff d = diffBaseline(host, summarizeWorld(client, 1), SharePolicy(), 1, false, 0, false, {});
    size_t n = 0;
    for (auto& e : d.effects) n += std::holds_alternative<RefCommand>(e);
    CHECK(n == 4);
    EffectTxn t;
    t.seq = 1;
    t.effects = d.effects;
    client.applyTxn(t);
    CHECK(client.refOps == host.refOps);
    CHECK(shardHashesOf(client) == shardHashesOf(host));
    CHECK(shardHashesOf(host) == shardHashes(baselineEntries(host)));
    // a different value for the same key changes the digest and is repaired by the next baseline
    fold(client, 0, {cmd(kOpSetUnconscious, Q(12), FormRef(), 0)});
    CHECK(shardHashesOf(client) != shardHashesOf(host));
    BaselineDiff d2 = diffBaseline(host, summarizeWorld(client, 1), SharePolicy(), 1, false, 0, false, {});
    REQUIRE(d2.effects.size() == 1);
    CHECK(std::get<RefCommand>(d2.effects[0]).i == 1);
    // events are not in the baseline
    BaselineEntry be;
    CHECK(!entryOf(cmd(kOpStartCombat, Q(1), Q(2)), be));
    CHECK(entryOf(cmd(kOpSetGhost, Q(1), FormRef(), 1), be));
}

TEST(refcmd, shadow_state_survives_the_sidecar_encoding) {
    ShadowState s;
    fold(s, 0, {cmd(kOpSetCombatStyle, Q(10), Q(11)), cmd(kOpSetScale, Q(12), FormRef(), 0)});
    RefCommand sc = cmd(kOpSetActorAlpha, Q(5));
    sc.f = 0.5f;
    fold(s, 0, {sc});
    Archive w = Archive::writer();
    io(w, s);
    std::vector<uint8_t> bytes = w.finish();
    Archive r = Archive::reader(bytes.data(), bytes.size());
    ShadowState back;
    io(r, back);
    CHECK(r.ok() && r.atEnd());
    CHECK(back == s);
    CHECK(back.refOps.size() == 3);
    CHECK(s.describeDifference(ShadowState()) != "");
}

TEST(refcmd, host_commits_reach_clients_once_and_a_late_joiner_gets_only_the_state) {
    InMemoryNetwork net(3);
    HostSession host(*net.endpoint(0), SharePolicy());
    struct Sink : IEffectSink {
        std::vector<RefCommand> got;
        std::vector<bool> catchUp;
        void apply(const Effect& e, const Context& c) override {
            if (auto* x = std::get_if<RefCommand>(&e)) { got.push_back(*x); catchUp.push_back(c.catchUp || c.baseline || c.fromSnapshot); }
        }
        void onJoined(PlayerId, const SharePolicy&) override {}
    };
    Sink s1, s2;
    EffectCapture c1, c2;
    ClientSession a(*net.endpoint(1), 0, 11, s1, &c1);
    uint64_t now = 0;
    auto run = [&](uint32_t ms) {
        for (uint32_t t = 0; t < ms; t += 5) { now += 5; net.advance(now); host.tick(now); a.tick(now); if (!s2.got.empty() || true) {} net.advance(now); }
    };
    a.connect(now, 1);
    run(150);
    REQUIRE(a.joined());
    Cause cause;
    cause.kind = CauseKind::QuestResult;
    cause.source = Q(1);
    host.commit(0, cause, {cmd(kOpAddSpell, Q(1), Q(50)), cmd(kOpStartCombat, Q(2), Q(0x14))});
    run(200);
    CHECK(s1.got.size() == 2);                                   // both performed once, live
    CHECK(a.state() == host.state());
    // a second client joins late: the state command arrives, the event does not fire again
    ClientSession b(*net.endpoint(2), 0, 22, s2, &c2);
    b.connect(now, 1);
    for (uint32_t t = 0; t < 300; t += 5) { now += 5; net.advance(now); host.tick(now); a.tick(now); b.tick(now); net.advance(now); }
    REQUIRE(b.joined());
    CHECK(b.state() == host.state());
    size_t liveEvents = 0, stateCmds = 0;
    for (size_t i = 0; i < s2.got.size(); ++i) {
        if (s2.got[i].op == kOpStartCombat && !s2.catchUp[i]) ++liveEvents;
        if (s2.got[i].op == kOpAddSpell) ++stateCmds;
    }
    CHECK(liveEvents == 0);
    CHECK(stateCmds >= 1);
}

TEST(twin, refcmd_keeps_first) {
    MutationScope m([] { Mutations x; x.refCmdKeepsFirst = true; return x; }());
    CHECK(!laterCommandWinsPerKey());   // the first value stayed, host order no longer wins
}

TEST(twin, refcmd_player_shared) {
    MutationScope m([] { Mutations x; x.refCmdPlayerShared = true; return x; }());
    CHECK(!playerCommandFollowsTheRewardRule());   // a script-driven spell on the player would reach the whole party
}

TEST(twin, refcmd_events_become_state) {
    MutationScope m([] { Mutations x; x.refCmdAllState = true; return x; }());
    CHECK(!eventsLeaveNoState());       // the event was remembered, so a late joiner would replay it
}
