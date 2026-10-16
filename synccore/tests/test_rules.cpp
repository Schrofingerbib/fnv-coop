// Quest rules engine: rules.json loading, barrier (VCG01 chargen), recipients, controls, tether, reductions, sim + twins.
#include <cstdio>
#include <memory>
#include "fnvcoop/rules.h"
#include "minitest.h"

using namespace fnvcoop;

#ifndef FNVCOOP_RULES_JSON
#define FNVCOOP_RULES_JSON ""
#endif

namespace {
uint32_t h32(const std::string& s) {
    uint32_t h = 2166136261u;
    for (char c : s) { h ^= uint8_t(c); h *= 16777619u; }
    return h;
}
bool resolveEdid(const std::string& edid, FormRef& out) { out = FormRef("falloutnv.esm", h32(edid)); return true; }
FormRef QF(const char* edid) { FormRef f; resolveEdid(edid, f); return f; }

struct Rec : IEffectSink {
    std::vector<Effect> live;
    void apply(const Effect& e, const Context&) override { live.push_back(e); }
    size_t count(size_t idx) const { size_t n = 0; for (auto& e : live) n += e.index() == idx; return n; }
};

struct Rig {
    InMemoryNetwork net{5};
    HostSession host;
    std::vector<std::unique_ptr<Rec>> sinks;
    std::vector<std::unique_ptr<EffectCapture>> caps;
    std::vector<std::unique_ptr<ClientSession>> cs;
    uint64_t now = 0;
    explicit Rig(SharePolicy p = {}) : host(*net.endpoint(0), p) {}
    ClientSession& add(uint64_t token) {
        sinks.push_back(std::make_unique<Rec>());
        caps.push_back(std::make_unique<EffectCapture>());
        cs.push_back(std::make_unique<ClientSession>(*net.endpoint(PeerId(cs.size() + 1)), 0, token, *sinks.back(), caps.back().get()));
        return *cs.back();
    }
    void connectAll() { for (auto& c : cs) c->connect(now, c->lastSession() + 1); }
    template <class F> void run(uint32_t ms, F&& each) {
        for (uint32_t t = 0; t < ms; t += 5) {
            now += 5; net.advance(now); host.tick(now);
            for (auto& c : cs) c->tick(now);
            each(now);
            net.advance(now);
        }
    }
    void run(uint32_t ms) { run(ms, [](uint64_t) {}); }
};

const char* kMini = R"({"schema":1,"quests":{
 "VCG01":{"tier":"T4","questline":"Main: Prologue","curated":true,"rules":[
   {"kind":"SetPlayerControls","params":{"scope":"all","release":"host-effect","timeout_s":120,"masks":["1 1 1 1 0 0 1"]},"origin":"curated"},
   {"kind":"RewardAll","params":{},"origin":"curated"},
   {"kind":"PerPlayerScene","params":{},"origin":"curated"},
   {"kind":"Barrier","params":{"step":"all-players-pass"},"origin":"curated"},
   {"kind":"QuestItemDup","params":{"recipient":"all"},"origin":"curated"},
   {"kind":"SaveSuppress","params":{},"origin":"curated"}]},
 "VMS03":{"tier":"T3","questline":"Side: Goodsprings","curated":false,"rules":[
   {"kind":"ShareKarmaRep","params":{},"origin":"derived"},
   {"kind":"RemoveItemPerOwner","params":{},"origin":"derived"},
   {"kind":"PartyAwareCond","params":{"funcs":{"GetInCell":"entry:all-in,exit:any-in","IsKiller":"any-party-member","GetDistance":"min-over-party","GetAV":"initiator","Odd":"mystery"}},"origin":"derived"},
   {"kind":"CompanionOwner","params":{"owner":"recruiter"},"origin":"derived"},
   {"kind":"SlideshowEnd","params":{"run":"host-once","lock":"all","release":"together"},"origin":"curated"}]},
 "VMS01":{"tier":"T4","questline":"Side: Goodsprings","curated":false,"rules":[
   {"kind":"PartyTether","params":{"cells":["GSDocMitchellHouse","GSCemetery"],"gather":"auto-warp"},"origin":"derived"}]},
 "Travel":{"tier":"T3","questline":"x","curated":false,"rules":[
   {"kind":"FastTravelLock","params":{"scope":"party"},"origin":"derived"},
   {"kind":"SetPlayerControls","params":{"scope":"all","release":"host-effect","timeout_s":120,"masks":[]},"origin":"derived"},
   {"kind":"PartyTether","params":{"cells":["GSCemetery"],"gather":"wait"},"origin":"derived"}]},
 "Talk":{"tier":"T3","questline":"x","curated":false,"rules":[{"kind":"InitiatorDialogue","params":{},"origin":"derived"},{"kind":"RewardAll","params":{},"origin":"derived"}]},
 "Warp":{"tier":"T4","questline":"x","curated":false,"rules":[{"kind":"PartyWarp","params":{"markers":["M1","M2"],"mode":"slot-offset","absent":"hold"},"origin":"derived"}]},
 "Plain":{"tier":"T1","questline":"x","curated":false,"rules":[]},
 "Dbg":{"tier":"T0","questline":"x","curated":true,"rules":[{"kind":"ExcludeFromGate","params":{},"origin":"curated"},{"kind":"FastTravelLock","params":{"scope":"party"},"origin":"derived"}]}
}})";

Cause quest(const char* edid, CauseKind k = CauseKind::QuestResult) { Cause c; c.kind = k; c.source = QF(edid); return c; }
}  // namespace

TEST(rules, loads_the_real_rules_json_and_is_strict) {
    RulesDb db;
    std::string err;
    if (std::string(FNVCOOP_RULES_JSON).empty() || !db.loadFile(FNVCOOP_RULES_JSON, &err)) {
        CHECK_MSG(false, "cannot load real rules.json: " + err);
        return;
    }
    CHECK(db.quests().size() > 100);
    const QuestRules* vcg01 = db.find("VCG01");
    REQUIRE(vcg01 != nullptr);
    CHECK(vcg01->find("Barrier") != nullptr);
    CHECK(vcg01->find("Barrier")->scalars.at("step") == "all-players-pass");
    CHECK(vcg01->find("SetPlayerControls")->lists.at("masks").size() == 3);
    CHECK(db.find("VMQTops")->find("PartyAwareCond")->funcs.at("getincell") == "entry:all-in,exit:any-in");
    auto counts = db.kindCounts();
    CHECK(counts["Barrier"] >= 2);
    CHECK(counts["RewardAll"] > 50);
    std::printf("    rules.json: %zu quests, %zu kinds\n", db.quests().size(), counts.size());
    // strictness: bad input is rejected and keeps the previous content
    for (const char* bad : {"", "{", "[]", "{\"schema\":2,\"quests\":{}}", "{\"schema\":1}", "{\"schema\":1,\"quests\":{\"A\":{\"rules\":[{\"params\":{}}]}}}",
                            "{\"schema\":1,\"quests\":{}} trailing", "{\"schema\":1,\"quests\":{\"A\":{\"rules\":[{\"kind\":\"X\",\"params\":{\"a\":{\"b\":1}}}]}}}"}) {
        std::string e;
        CHECK_MSG(!db.load(bad, &e), bad);
        CHECK(!e.empty());
    }
    CHECK(db.quests().size() > 100);
}

TEST(rules, json_fuzz_never_crashes) {
    RulesDb real;
    std::string err;
    if (std::string(FNVCOOP_RULES_JSON).empty() || !real.loadFile(FNVCOOP_RULES_JSON, &err)) return;
    std::string text = kMini;
    Rng r(77);
    size_t ok = 0;
    for (int i = 0; i < 3000; ++i) {
        std::string t = text;
        for (uint32_t k = 0, n = r.range(1, 4); k < n; ++k) {
            size_t pos = r.u32() % t.size();
            switch (r.range(0, 2)) {
                case 0: t[pos] = char(r.u32()); break;
                case 1: t.erase(pos, r.range(1, 8)); break;
                default: t.insert(pos, 1, "{}[]\",:0e-"[r.u32() % 10]); break;
            }
        }
        RulesDb d;
        if (d.load(t)) ++ok;
    }
    CHECK(ok < 3000);
}

TEST(rules, recipients_follow_the_quest_rules_even_when_global_policy_says_otherwise) {
    RulesDb db; REQUIRE(db.load(kMini));
    SharePolicy p; p.questRewardsToAll = false; p.karma = false; p.reputation = false;   // global defaults would give everything to the origin
    Rig r(p);
    auto& a = r.add(1); (void)a;
    RulesEngine eng(r.host, db, resolveEdid);
    r.connectAll(); r.run(300);
    // VCG01: RewardAll + QuestItemDup -> items and XP to everyone
    r.host.commit(1, quest("VCG01"), {AddItem{QF("Item"), 1, Recipient::dflt()}, ModActorValue{kAvExperience, 100, Recipient::dflt()}});
    // an unrelated quest keeps the global policy (origin only)
    r.host.commit(1, quest("Plain"), {AddItem{QF("Item"), 1, Recipient::dflt()}});
    // VMS03: ShareKarmaRep wins over policy.karma=false; hand-in removes from the handing player only
    r.host.commit(1, quest("VMS03"), {ModKarma{-5, Recipient::dflt()}, RemoveItem{QF("Item"), 1, Recipient::dflt()}, CompanionOwner{QF("Rex"), 0}});
    const auto& led0 = r.host.state().ledger[0];
    const auto& led1 = r.host.state().ledger[1];
    CHECK(led0.items.at(QF("Item")) == 1);        // VCG01 reward reached the host slot too
    CHECK(led1.items.at(QF("Item")) == 1 + 1 - 1);// +1 VCG01, +1 Plain (origin = slot 1), -1 hand-in
    CHECK(led0.karma == -5 && led1.karma == -5);
    CHECK(r.host.state().companionOwner.at(QF("Rex")) == 1);   // recruiter = the txn origin
    CHECK(led0.av.at(kAvExperience) == 100);
    CHECK(eng.stats().recipientRewrites >= 4);
    CHECK(eng.excludedFromGate(QF("Dbg")));
    CHECK(!eng.excludedFromGate(QF("VCG01")));
    CHECK(eng.unsupportedKinds().empty());   // every kind in the test data is handled
}

TEST(rules, controls_scope_all_save_suppress_tie_and_timeout) {
    RulesDb db; REQUIRE(db.load(kMini));
    Rig r;
    r.add(1);
    RulesEngine::Options o; o.controlTimeoutMs = 1000;
    RulesEngine eng(r.host, db, resolveEdid, o);
    r.connectAll(); r.run(300);
    uint64_t t0 = r.now;
    eng.tick(t0);
    r.host.commit(0, quest("VCG01", CauseKind::Script), {SetPlayerControls{0b1011, false, Recipient::dflt()}});   // lock for... whom? scope all
    CHECK(r.host.state().ledger[1].controlsOff == 0b1011);   // the client is locked too
    CHECK(r.host.state().saveSuppress);                      // saving blocked while the scene runs
    // the script forgets to release: the engine does it after the timeout
    eng.tick(t0 + 500);
    CHECK(r.host.state().ledger[1].controlsOff != 0);
    eng.tick(t0 + 1500);
    CHECK(r.host.state().ledger[1].controlsOff == 0);
    CHECK(!r.host.state().saveSuppress);
    CHECK(eng.stats().controlTimeouts == 1);
    // normal release by the script
    r.host.commit(0, quest("VCG01", CauseKind::Script), {SetPlayerControls{0b1, false, Recipient::dflt()}});
    CHECK(r.host.state().saveSuppress);
    r.host.commit(0, quest("VCG01", CauseKind::Script), {SetPlayerControls{0b1, true, Recipient::dflt()}});
    CHECK(!r.host.state().saveSuppress);
    eng.tick(t0 + 10000);
    CHECK(eng.stats().controlTimeouts == 1);
}

TEST(rules, party_aware_condition_reductions) {
    RulesDb db; REQUIRE(db.load(kMini));
    Rig r;
    RulesEngine eng(r.host, db, resolveEdid);
    FormRef q = QF("VMS03");
    std::vector<PartyValue> v = {{0, 0}, {1, 1}, {2, 0}};
    CHECK(eng.reduceCondition(q, "IsKiller", v, 0) == 1.0);                        // any party member
    CHECK(eng.reduceCondition(q, "GetAV", {{0, 50}, {1, 70}}, 1) == 70.0);         // initiator's value
    CHECK(eng.reduceCondition(q, "GetDistance", {{0, 900}, {1, 40}, {2, 300}}, 0) == 40.0);   // closest member
    CHECK(eng.reduceCondition(q, "GetInCell", {{0, 1}, {1, 1}}, 0, CondPhase::Entry) == 1.0);
    CHECK(eng.reduceCondition(q, "GetInCell", {{0, 1}, {1, 0}}, 0, CondPhase::Entry) == 0.0);   // entry needs everyone
    CHECK(eng.reduceCondition(q, "GetInCell", {{0, 1}, {1, 0}}, 0, CondPhase::Exit) == 1.0);    // exit needs only one still inside
    CHECK(eng.reduceCondition(q, "GetInCell", {{0, 0}, {1, 0}}, 0, CondPhase::Exit) == 0.0);
    CHECK(eng.reduceCondition(q, "getincell", {{0, 1}, {1, 1}}, 0) == 1.0);        // case-insensitive
    // unknown function / reduction / quest fall back to the initiator (counted, never a crash)
    CHECK(eng.reduceCondition(q, "Odd", {{0, 3}, {1, 4}}, 1) == 4.0);
    CHECK(eng.stats().unknownReductions == 1);
    CHECK(eng.reduceCondition(q, "Unlisted", {{0, 3}, {1, 4}}, 0) == 3.0);
    CHECK(eng.reduceCondition(QF("Nope"), "GetAV", {{0, 3}, {1, 4}}, 1) == 4.0);
    CHECK(eng.reduceCondition(q, "GetAV", {}, 0) == 0.0);
}

TEST(rules, vcg01_chargen_barrier_advances_only_after_every_player_passed) {
    RulesDb db; REQUIRE(db.load(kMini));
    Rig r;
    auto& a = r.add(1); auto& b = r.add(2);
    RulesEngine eng(r.host, db, resolveEdid);
    std::vector<std::pair<FormRef, uint32_t>> applied;
    eng.applyStage = [&](const FormRef& q, uint32_t s) { applied.emplace_back(q, s); };
    r.host.onAction = [&](PlayerId slot, IntentKind k, const FormRef& ref, uint32_t, uint32_t) { eng.handleAction(slot, k, ref); };
    r.connectAll(); r.run(400);
    FormRef vcg01 = QF("VCG01");
    CHECK(eng.onStageRequested(vcg01, 30, 0) == StageDecision::Deferred);
    CHECK(eng.onStageRequested(vcg01, 30, 0) == StageDecision::Deferred);       // asking again does not duplicate
    CHECK(eng.pendingDeferrals() == 1);
    eng.onPlayerPassed(0, vcg01);                                                // host's own chargen
    a.submitAction(IntentKind::ScenePass, vcg01);
    r.run(300);
    CHECK(applied.empty());                                                      // client b is still creating their character
    a.submitAction(IntentKind::ScenePass, vcg01);                                // duplicate report is harmless
    r.run(200);
    CHECK(applied.empty());
    b.submitAction(IntentKind::ScenePass, vcg01);
    r.run(400);
    REQUIRE(applied.size() == 1);
    CHECK((applied[0].first == vcg01 && applied[0].second == 30));
    CHECK(eng.pendingDeferrals() == 0);
    CHECK(r.host.state().barriers.at(RulesEngine::barrierId(vcg01, 30)).released);
    for (auto& s : r.sinks) CHECK(s->count(23) == 1);                            // BarrierRelease reached every client exactly once
    CHECK(a.state() == r.host.state());
    // a stage with no rules goes straight through
    CHECK(eng.onStageRequested(QF("Plain"), 5, 0) == StageDecision::Proceed);
    // a second chargen-like barrier on another stage is independent
    CHECK(eng.onStageRequested(vcg01, 40, 0) == StageDecision::Deferred);
    CHECK(!r.host.state().barriers.count(RulesEngine::barrierId(vcg01, 40)) || !r.host.state().barriers.at(RulesEngine::barrierId(vcg01, 40)).released);
}

TEST(rules, barrier_early_passes_leavers_and_timeout) {
    RulesDb db; REQUIRE(db.load(kMini));
    {   // a player finishes chargen BEFORE the host asks for the stage
        Rig r; auto& a = r.add(1);
        RulesEngine eng(r.host, db, resolveEdid);
        int n = 0; eng.applyStage = [&](const FormRef&, uint32_t) { ++n; };
        r.host.onAction = [&](PlayerId s, IntentKind k, const FormRef& ref, uint32_t, uint32_t) { eng.handleAction(s, k, ref); };
        r.connectAll(); r.run(300);
        a.submitAction(IntentKind::ScenePass, QF("VCG01"));
        r.run(200);
        eng.onPlayerPassed(0, QF("VCG01"));
        CHECK(n == 0);
        CHECK(eng.onStageRequested(QF("VCG01"), 30, 0) == StageDecision::Deferred);
        CHECK(n == 1);                                                           // both had already passed: releases immediately
    }
    {   // a player leaves mid-chargen: the others are not held hostage
        Rig r; auto& a = r.add(1); auto& b = r.add(2);
        RulesEngine eng(r.host, db, resolveEdid);
        int n = 0; eng.applyStage = [&](const FormRef&, uint32_t) { ++n; };
        r.host.onAction = [&](PlayerId s, IntentKind k, const FormRef& ref, uint32_t, uint32_t) { eng.handleAction(s, k, ref); };
        r.connectAll(); r.run(300);
        eng.onStageRequested(QF("VCG01"), 30, 0);
        eng.onPlayerPassed(0, QF("VCG01"));
        a.submitAction(IntentKind::ScenePass, QF("VCG01"));
        r.run(300);
        CHECK(n == 0);
        b.disconnect(); r.net.setConnected(2, false);
        r.host.peerDisconnected(2);           // the transport reported the disconnect: b no longer holds the party
        eng.tick(r.now + 10);
        CHECK(n == 1);
        CHECK(eng.stats().barrierTimeouts == 0);
        CHECK(r.host.state().barriers.at(RulesEngine::barrierId(QF("VCG01"), 30)).released);
    }
    {   // a player who stays connected but never finishes: the timeout releases the party
        Rig r; r.add(1);
        RulesEngine eng(r.host, db, resolveEdid);
        int n = 0; eng.applyStage = [&](const FormRef&, uint32_t) { ++n; };
        r.connectAll(); r.run(300);
        eng.onStageRequested(QF("VCG01"), 30, 0);
        eng.onPlayerPassed(0, QF("VCG01"));
        eng.tick(r.now + 1000);
        CHECK(n == 0);
        eng.tick(r.now + 200000);
        CHECK(n == 1);
        CHECK(eng.stats().barrierTimeouts == 1);
        CHECK(r.host.state().barriers.at(RulesEngine::barrierId(QF("VCG01"), 30)).released);
    }
}

TEST(rules, party_tether_holds_the_stage_until_everyone_is_in_the_cells) {
    RulesDb db; REQUIRE(db.load(kMini));
    Rig r; auto& a = r.add(1);
    RulesEngine eng(r.host, db, resolveEdid);
    int n = 0; eng.applyStage = [&](const FormRef&, uint32_t) { ++n; };
    r.host.onAction = [&](PlayerId s, IntentKind k, const FormRef& ref, uint32_t, uint32_t) { eng.handleAction(s, k, ref); };
    r.connectAll(); r.run(300);
    FormRef vms01 = QF("VMS01");
    eng.onCellReported(0, QF("GSDocMitchellHouse"));
    CHECK(eng.onStageRequested(vms01, 10, 0) == StageDecision::Deferred);       // client not reported in a tether cell yet
    auto anyTether = [&] { for (auto& kv : r.host.state().tethers) if (kv.second) return true; return false; };
    CHECK(anyTether());                                                          // clients were told to gather
    a.submitAction(IntentKind::ReportCell, QF("GSCemetery"));                    // the client arrives in the OTHER tether cell: fine
    r.run(300);
    CHECK(n == 1);
    CHECK(!anyTether());
    // when everybody is already there the stage proceeds immediately
    CHECK(eng.onStageRequested(vms01, 20, 0) == StageDecision::Proceed);
}

namespace {
// Shared by the real test and its rulesDisabled twin.
bool extendedRulesHold() {
    RulesDb db; db.load(kMini);
    Rig r; auto& a = r.add(1); auto& b = r.add(2); (void)a; (void)b;
    RulesEngine::Options o;
    o.playerRef = [](PlayerId s, FormRef& f) { f = FormRef("falloutnv.esm", 0xF000 + s); return true; };
    RulesEngine eng(r.host, db, resolveEdid, o);
    r.connectAll(); r.run(400);
    bool ok = true;
    // FastTravelLock: set while the quest's scene (control lock) runs, cleared on release
    r.host.commit(0, quest("Travel", CauseKind::Script), {SetPlayerControls{1, false, Recipient::dflt()}});
    ok &= !r.host.state().fastTravelLock.empty();
    r.host.commit(0, quest("Travel", CauseKind::Script), {SetPlayerControls{1, true, Recipient::dflt()}});
    ok &= r.host.state().fastTravelLock.empty();
    // ... and while a tether deferral is pending
    eng.onCellReported(0, QF("Elsewhere"));
    ok &= eng.onStageRequested(QF("Travel"), 5, 0) == StageDecision::Deferred;
    ok &= !r.host.state().fastTravelLock.empty();
    eng.onCellReported(0, QF("GSCemetery")); eng.onCellReported(1, QF("GSCemetery")); eng.onCellReported(2, QF("GSCemetery"));
    ok &= r.host.state().fastTravelLock.empty();
    // InitiatorDialogue: while player 2 talks, player 1's dialogue results of the quest are dropped
    eng.onDialogueStart(2);
    ok &= r.host.commit(1, quest("Talk", CauseKind::DialogueResult), {SetStage{QF("Talk"), 7}}) == 0;
    ok &= r.host.commit(2, quest("Talk", CauseKind::DialogueResult), {SetStage{QF("Talk"), 8}}) != 0;
    ok &= r.host.state().stages.at(QF("Talk")) == 8;
    eng.onDialogueEnd();
    ok &= r.host.commit(1, quest("Talk", CauseKind::DialogueResult), {SetStage{QF("Talk"), 9}}) != 0;
    // PartyWarp: the script moves the player; every present party member follows with slot offsets, in one txn
    Cause wc = quest("Warp", CauseKind::Script);
    r.host.commit(0, wc, {MoveRef{FormRef("falloutnv.esm", 0xF000), QF("Cell"), 100.f, 5.f, 0.f, false}});
    for (unsigned s = 0; s < 3; ++s) {
        auto it = r.host.state().placed.find(FormRef("falloutnv.esm", 0xF000 + s));
        ok &= it != r.host.state().placed.end() && it->second.x == 100.f + 64.f * float(s) && it->second.cell == QF("Cell");
    }
    return ok;
}
}  // namespace

TEST(rules, fast_travel_lock_initiator_dialogue_and_party_warp) {
    CHECK(extendedRulesHold());
    // an absent player is held where they are
    RulesDb db; db.load(kMini);
    Rig r; r.add(1); r.add(2);
    RulesEngine::Options o;
    o.playerRef = [](PlayerId s, FormRef& f) { f = FormRef("falloutnv.esm", 0xF000 + s); return true; };
    RulesEngine eng(r.host, db, resolveEdid, o);
    r.connectAll(); r.run(400);
    r.host.peerDisconnected(2);
    r.host.commit(0, quest("Warp", CauseKind::Script), {MoveRef{FormRef("falloutnv.esm", 0xF000), QF("Cell"), 0.f, 0.f, 0.f, false}});
    CHECK(r.host.state().placed.count(FormRef("falloutnv.esm", 0xF001)) == 1);
    CHECK(r.host.state().placed.count(FormRef("falloutnv.esm", 0xF002)) == 0);
    CHECK(r.host.state().placed.at(FormRef("falloutnv.esm", 0xF001)).x == 64.f);
    CHECK(eng.stats().warpsExpanded == 1);
    // a MoveRef of some other ref is left alone
    r.host.commit(0, quest("Warp", CauseKind::Script), {MoveRef{QF("Crate"), QF("Cell"), 1.f, 2.f, 3.f, false}});
    CHECK(r.host.state().placed.count(QF("Crate")) == 1);
    CHECK(eng.stats().warpsExpanded == 1);
}

// ---- simulation: chargen barrier under loss, reordering, duplicates and disconnects -------------------------------

namespace {
struct BarrierSimResult { bool ok = true; std::string why; int released = 0; };

BarrierSimResult runBarrierSim(uint64_t seed) {
    BarrierSimResult res;
    RulesDb db;
    db.load(kMini);
    Rng rng(seed);
    int nClients = int(rng.range(1, 3));
    Rig r;
    LinkParams lp; lp.minLatencyMs = rng.range(0, 20); lp.maxLatencyMs = lp.minLatencyMs + rng.range(0, 100);
    lp.loss = rng.range(0, 30) / 100.0; lp.duplicate = rng.range(0, 20) / 100.0; lp.corrupt = rng.range(0, 5) / 100.0;
    r.net.setLink(lp);
    for (int i = 0; i < nClients; ++i) r.add(uint64_t(100 + i));
    RulesEngine::Options opt; opt.barrierTimeoutMs = 60000;
    RulesEngine eng(r.host, db, resolveEdid, opt);
    FormRef vcg01 = QF("VCG01");
    int applied = 0;
    std::set<PlayerId> passedAtRelease;
    uint8_t joinedAtRelease = 0;
    eng.applyStage = [&](const FormRef&, uint32_t) {
        ++applied;
        joinedAtRelease = 1;
        for (auto& c : r.host.clients()) if (c.joined) joinedAtRelease = uint8_t(joinedAtRelease | (1u << c.slot));
    };
    r.host.onAction = [&](PlayerId s, IntentKind k, const FormRef& ref, uint32_t, uint32_t) { eng.handleAction(s, k, ref); };
    r.connectAll();
    // calm phase so everyone is in
    r.net.setLink(LinkParams());
    r.run(400);
    r.net.setLink(lp);
    std::vector<uint64_t> passAt(static_cast<size_t>(nClients), 0);
    for (auto& t : passAt) t = r.now + rng.range(100, 3000);
    uint64_t hostPassAt = r.now + rng.range(100, 3000), requestAt = r.now + rng.range(0, 1500);
    bool requested = false, hostPassed = false;
    std::vector<bool> passedSent(size_t(nClients), false);
    // Invariant monitor: the stage may only be applied after every player that is joined at that moment has passed.
    std::set<int> reportedByClient;
    r.run(8000, [&](uint64_t now) {
        if (!requested && now >= requestAt) { requested = true; eng.onStageRequested(vcg01, 30, 0); }
        if (!hostPassed && now >= hostPassAt) { hostPassed = true; eng.onPlayerPassed(0, vcg01); }
        for (int i = 0; i < nClients; ++i)
            if (!passedSent[size_t(i)] && now >= passAt[size_t(i)] && r.cs[size_t(i)]->joined()) {
                passedSent[size_t(i)] = true;
                r.cs[size_t(i)]->submitAction(IntentKind::ScenePass, vcg01);
            }
        eng.tick(now);
        if (applied == 1 && res.released == 0) {
            res.released = 1;
            // at the moment of release every player (host + all clients that were joined) must already have passed
            bool everyoneHostSaw = hostPassed;
            for (int i = 0; i < nClients; ++i) everyoneHostSaw = everyoneHostSaw && passedSent[size_t(i)];
            if (!everyoneHostSaw && res.ok) { res.ok = false; res.why = "stage applied before all players passed (seed " + std::to_string(seed) + ")"; }
        }
    });
    r.net.setLink(LinkParams());
    r.run(4000, [&](uint64_t now) { eng.tick(now); });
    if (res.ok && applied != 1) { res.ok = false; res.why = "stage applied " + std::to_string(applied) + " times (seed " + std::to_string(seed) + ")"; }
    if (res.ok) for (auto& c : r.cs) if (!(c->state() == r.host.state())) { res.ok = false; res.why = "client shadow diverged (seed " + std::to_string(seed) + ")"; }
    if (res.ok && !r.host.state().barriers.at(RulesEngine::barrierId(vcg01, 30)).released) { res.ok = false; res.why = "barrier never released"; }
    return res;
}

struct SimAgg { int seeds = 0, failed = 0; std::string first; };
SimAgg runBarrierSeeds(int n) {
    SimAgg a;
    for (int i = 1; i <= n; ++i) {
        BarrierSimResult r = runBarrierSim(uint64_t(i));
        ++a.seeds;
        if (!r.ok) { ++a.failed; if (a.first.empty()) a.first = r.why; }
    }
    return a;
}
}  // namespace

TEST(rules, barrier_sim_300_seeds_lossy_link_converges_and_releases_exactly_once) {
    SimAgg a = runBarrierSeeds(300);
    std::printf("    barrier sim: %d seeds, %d failed %s\n", a.seeds, a.failed, a.first.c_str());
    CHECK(a.failed == 0);
}

TEST(twin, rules_barrier_release_early) {
    Mutations m; m.barrierReleaseEarly = true;
    MutationScope s(m);
    SimAgg a = runBarrierSeeds(100);
    std::printf("    twin barrier_release_early: caught on %d/%d seeds e.g. %s\n", a.failed, a.seeds, a.first.c_str());
    CHECK(a.failed > 0);
}

TEST(twin, rules_extended_disabled) {
    Mutations m; m.rulesDisabled = true;
    MutationScope s(m);
    CHECK(!extendedRulesHold());
}

TEST(twin, rules_disabled) {
    Mutations m; m.rulesDisabled = true;
    MutationScope s(m);
    SimAgg a = runBarrierSeeds(50);   // stage is never deferred: the harness sees "applied 0 times" (the host plugin would have advanced itself)
    std::printf("    twin rules_disabled: caught on %d/%d seeds\n", a.failed, a.seeds);
    CHECK(a.failed > 0);
    // and recipients are no longer rewritten
    RulesDb db; db.load(kMini);
    SharePolicy p; p.questRewardsToAll = false;
    Rig r(p); r.add(1);
    RulesEngine eng(r.host, db, resolveEdid);
    r.connectAll(); r.run(300);
    r.host.commit(1, quest("VCG01"), {AddItem{QF("Item"), 1, Recipient::dflt()}});
    CHECK(r.host.state().ledger[0].items.count(QF("Item")) == 0);   // reward did NOT reach everyone: the rule check would catch this
}
