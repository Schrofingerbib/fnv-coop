// Unit tests for the hook patcher (fake memory) and the capture hub. Exit code != 0 on any failure.
// Built twice: against the real sources, and against NVHOOKS_MUTANT sources (the latter MUST fail).
#include <cstdio>
#include <cstring>
#include <map>
#include <vector>

#include "../capture_hub.h"
#include "../effect_json.h"
#include "../engine_addrs.h"
#include "../hook_patch.h"
#include "../apply_lines.h"
#include "../change_tracker.h"
#include "../inventory_diff.h"
#include "../suppress_policy.h"

using namespace nvhooks;

static int g_fail = 0, g_checks = 0;
#define CHECK(c)                                                                  \
    do {                                                                          \
        ++g_checks;                                                               \
        if (!(c)) {                                                               \
            ++g_fail;                                                             \
            std::printf("  FAIL %s:%d  %s\n", __FILE__, __LINE__, #c);            \
        }                                                                         \
    } while (0)
#define TEST(name) static void name()
static void run(const char* n, void (*f)()) {
    int before = g_fail;
    f();
    std::printf("[%s] %s\n", g_fail == before ? " ok " : "FAIL", n);
}

// ---- fake game memory: one "image" region at 0x00400000 + a bump allocator region for trampolines ----
struct FakeMemory : MemoryOps {
    static constexpr Addr kImage = 0x00400000, kImageSize = 0x1000, kHeap = 0x20000000;
    std::vector<uint8_t> image = std::vector<uint8_t>(kImageSize, 0xCC);
    std::map<Addr, std::vector<uint8_t>> heap;
    Addr next = kHeap;
    int writes = 0;          // total successful writes
    int imageWrites = 0;     // writes into the image (game code)
    bool failAlloc = false, failTrampWrite = false, failImageWrite = false;
    int freed = 0;

    uint8_t* at(Addr a, size_t n) {
        if (a >= kImage && a + n <= kImage + kImageSize) return &image[a - kImage];
        for (auto& [b, v] : heap)
            if (a >= b && a + n <= b + v.size()) return &v[a - b];
        return nullptr;
    }
    bool read(Addr a, void* out, size_t n) override {
        uint8_t* p = at(a, n);
        if (!p) return false;
        std::memcpy(out, p, n);
        return true;
    }
    bool write(Addr a, const void* in, size_t n) override {
        bool inImage = a >= kImage && a < kImage + kImageSize;
        if (inImage && failImageWrite) return false;
        if (!inImage && failTrampWrite) return false;
        uint8_t* p = at(a, n);
        if (!p) return false;
        std::memcpy(p, in, n);
        ++writes;
        if (inImage) ++imageWrites;
        return true;
    }
    Addr allocExec(size_t n) override {
        if (failAlloc) return 0;
        Addr a = next;
        next += static_cast<Addr>((n + 15) & ~size_t(15));
        heap[a] = std::vector<uint8_t>(n, 0);
        return a;
    }
    void freeExec(Addr a, size_t) override {
        heap.erase(a);
        ++freed;
    }
    // Lay a function (the spec's expected bytes) down at spec.target.
    void place(const HookSpec& s) { std::memcpy(&image[s.target - kImage], s.expected, s.expectedLen); }
    std::vector<uint8_t> bytes(Addr a, size_t n) {
        std::vector<uint8_t> v(n);
        read(a, v.data(), n);
        return v;
    }
};

static HookSpec makeSpec(Addr target = 0x00400100, uint8_t stolen = 6) {
    HookSpec s{"fake::Fn", target, {0x55, 0x8B, 0xEC, 0x83, 0xEC, 0x08, 0x89, 0x4D, 0xF8, 0x8B, 0x4D, 0xF8, 0xE8, 0x11, 0x22, 0x33}, 16, stolen};
    return s;
}
static uint32_t rel32At(const std::vector<uint8_t>& v, size_t off) {
    uint32_t r;
    std::memcpy(&r, &v[off], 4);
    return r;
}

TEST(install_patches_entry_and_builds_trampoline) {
    FakeMemory m;
    HookSpec s = makeSpec();
    m.place(s);
    HookRegistry reg(m);
    Addr tramp = 0, detour = 0x30000000;
    CHECK(reg.acquire(s, detour, &tramp) == PatchStatus::Ok);
    CHECK(tramp != 0);
    auto code = m.bytes(s.target, 16);
    CHECK(code[0] == 0xE9);
    CHECK(s.target + 5 + rel32At(code, 1) == detour);
    CHECK(code[5] == 0x90);                                   // stolen=6: one NOP of padding
    CHECK(std::memcmp(&code[6], &s.expected[6], 10) == 0);    // bytes beyond `stolen` untouched
    auto t = m.bytes(tramp, 11);
    CHECK(std::memcmp(t.data(), s.expected, 6) == 0);          // stolen bytes first
    CHECK(t[6] == 0xE9);
    CHECK(tramp + 6 + 5 + rel32At(t, 7) == s.target + 6);      // jumps back to the unstolen remainder
    CHECK(m.imageWrites == 1);
}

TEST(wrong_prologue_refuses_and_writes_nothing) {
    FakeMemory m;
    HookSpec s = makeSpec();
    m.place(s);
    m.image[s.target - FakeMemory::kImage + 13] ^= 0xFF;  // different call displacement => different exe build
    auto before = m.image;
    HookRegistry reg(m);
    Addr tramp = 0xDEAD;
    CHECK(reg.acquire(s, 0x30000000, &tramp) == PatchStatus::PrologueMismatch);
    CHECK(m.writes == 0);
    CHECK(m.image == before);
    CHECK(m.heap.empty());          // no trampoline leaked
    CHECK(reg.installedCount() == 0);
    CHECK(tramp == 0xDEAD);         // output untouched on failure
}

TEST(unrelated_bytes_refuse) {
    FakeMemory m;  // image is all 0xCC: nothing like a prologue
    HookSpec s = makeSpec();
    HookRegistry reg(m);
    CHECK(reg.acquire(s, 0x30000000, nullptr) == PatchStatus::PrologueMismatch);
    CHECK(m.writes == 0);
}

TEST(foreign_detour_is_reported_not_overwritten) {
    FakeMemory m;
    HookSpec s = makeSpec();
    m.place(s);
    m.image[s.target - FakeMemory::kImage] = 0xE9;  // someone else already detoured the entry
    HookRegistry reg(m);
    CHECK(reg.acquire(s, 0x30000000, nullptr) == PatchStatus::AlreadyHooked);
    CHECK(m.writes == 0);
}

TEST(bad_specs_are_rejected) {
    FakeMemory m;
    HookRegistry reg(m);
    HookSpec a = makeSpec(0x00400100, 4);  // too few bytes for a jmp rel32
    m.place(a);
    CHECK(reg.acquire(a, 1, nullptr) == PatchStatus::BadSpec);
    HookSpec b = makeSpec();
    b.expected[0] = 0x8B;  // not a standard frame entry: stolen bytes could hold a rel32 branch
    CHECK(reg.acquire(b, 1, nullptr) == PatchStatus::BadSpec);
    HookSpec c = makeSpec();
    c.expectedLen = 5;  // signature shorter than the stolen region
    CHECK(reg.acquire(c, 1, nullptr) == PatchStatus::BadSpec);
    CHECK(m.writes == 0);
}

TEST(refcount_install_release_restore) {
    FakeMemory m;
    HookSpec s = makeSpec();
    m.place(s);
    auto pristine = m.image;
    HookRegistry reg(m);
    Addr t1 = 0, t2 = 0;
    CHECK(reg.acquire(s, 0x30000000, &t1) == PatchStatus::Ok);
    CHECK(reg.acquire(s, 0x30000000, &t2) == PatchStatus::AlreadyInstalled);
    CHECK(t1 == t2);
    CHECK(reg.refCount(s) == 2);
    CHECK(m.imageWrites == 1);  // second acquire does not re-patch
    CHECK(reg.release(s) == PatchStatus::Released);
    CHECK(m.image != pristine);  // still hooked for the remaining user
    CHECK(m.bytes(s.target, 1)[0] == 0xE9);
    CHECK(reg.release(s) == PatchStatus::Restored);
    CHECK(m.image == pristine);
    CHECK(m.freed == 1);
    CHECK(reg.release(s) == PatchStatus::NotInstalled);
}

TEST(different_detour_for_same_target_refused) {
    FakeMemory m;
    HookSpec s = makeSpec();
    m.place(s);
    HookRegistry reg(m);
    CHECK(reg.acquire(s, 0x30000000, nullptr) == PatchStatus::Ok);
    CHECK(reg.acquire(s, 0x30000040, nullptr) == PatchStatus::BadSpec);
    CHECK(reg.refCount(s) == 1);
}

TEST(tampered_code_not_restored) {
    FakeMemory m;
    HookSpec s = makeSpec();
    m.place(s);
    HookRegistry reg(m);
    CHECK(reg.acquire(s, 0x30000000, nullptr) == PatchStatus::Ok);
    m.image[s.target - FakeMemory::kImage + 1] ^= 0x55;  // another hook chained over ours
    auto after = m.image;
    CHECK(reg.release(s) == PatchStatus::Tampered);
    CHECK(m.image == after);
    CHECK(m.freed == 0);  // their jump may still target our trampoline
}

TEST(alloc_or_write_failure_leaves_game_code_untouched) {
    {
        FakeMemory m;
        HookSpec s = makeSpec();
        m.place(s);
        auto p = m.image;
        m.failAlloc = true;
        HookRegistry reg(m);
        CHECK(reg.acquire(s, 1, nullptr) == PatchStatus::AllocFailed);
        CHECK(m.image == p);
    }
    {
        FakeMemory m;
        HookSpec s = makeSpec();
        m.place(s);
        auto p = m.image;
        m.failTrampWrite = true;
        HookRegistry reg(m);
        CHECK(reg.acquire(s, 1, nullptr) == PatchStatus::WriteFailed);
        CHECK(m.image == p);
        CHECK(m.heap.empty());
    }
    {
        FakeMemory m;
        HookSpec s = makeSpec();
        m.place(s);
        m.failImageWrite = true;
        HookRegistry reg(m);
        CHECK(reg.acquire(s, 1, nullptr) == PatchStatus::WriteFailed);
        CHECK(m.heap.empty());  // trampoline freed again
    }
}

TEST(registry_destructor_restores_everything) {
    FakeMemory m;
    HookSpec a = makeSpec(0x00400100), b = makeSpec(0x00400200, 5);
    m.place(a);
    m.place(b);
    auto pristine = m.image;
    {
        HookRegistry reg(m);
        CHECK(reg.acquire(a, 1, nullptr) == PatchStatus::Ok);
        CHECK(reg.acquire(b, 2, nullptr) == PatchStatus::Ok);
        CHECK(m.image != pristine);
    }
    CHECK(m.image == pristine);
}

TEST(engine_address_table_is_sane) {
    std::map<Addr, const char*> seen;
    for (const HookSpec* s : addr::kAll) {
        std::string why;
        CHECK(specIsValid(*s, &why));
        CHECK(s->target >= 0x401000 && s->target < 0xFDE58B);  // inside .text of 1.4.0.525
        CHECK(seen.emplace(s->target, s->name).second);         // no duplicate targets
    }
    CHECK(addr::SetStage.target == 0x60D510);
    CHECK(addr::kAll[0] != nullptr);
}

TEST(engine_table_signature_roundtrip_on_fake_image) {
    // Every table entry must install cleanly on an image containing exactly its signature, and refuse on 1 flipped bit.
    for (const HookSpec* s : addr::kAll) {
        FakeMemory m;
        HookSpec t = *s;
        // remap target into the fake image
        t.target = FakeMemory::kImage + 0x100 + (s->target & 0x3FF);
        m.place(t);
        HookRegistry reg(m);
        CHECK(reg.acquire(t, 0x30000000, nullptr) == PatchStatus::Ok);
        reg.release(t);
        m.place(t);
        m.image[t.target - FakeMemory::kImage + s->expectedLen - 1] ^= 1;
        CHECK(reg.acquire(t, 0x30000000, nullptr) == PatchStatus::PrologueMismatch);
    }
}

// ---------------------------------------------------------------- capture hub
using namespace fnvcoop;
static Effect stage(uint32_t id, uint32_t st) { return SetStage{FormRef("fallout3.esm", id), st}; }
static Cause qcause(uint32_t id, int st) {
    Cause c;
    c.kind = CauseKind::QuestResult;
    c.source = FormRef("falloutnv.esm", id);
    c.aux = st;
    return c;
}

TEST(no_scope_no_record) {
    CaptureHub h;
    h.setEnabled(true);
    std::vector<EffectTxn> out;
    h.setSink([&](EffectTxn&& t) { out.push_back(std::move(t)); });
    CHECK(!h.record(stage(1, 10)));
    CHECK(h.stats().droppedNoScope == 1);
    h.begin(qcause(1, 10));
    h.end();
    CHECK(out.empty());  // the ambient mutation must not leak into a later scope
    CHECK(h.stats().emptyScopes == 1);
}

TEST(scope_emits_one_txn_with_cause_and_order) {
    CaptureHub h;
    h.setEnabled(true);
    std::vector<EffectTxn> out;
    h.setSink([&](EffectTxn&& t) { out.push_back(std::move(t)); });
    {
        ScopedCapture sc(h, qcause(0x104C, 30));
        CHECK(h.active());
        CHECK(h.record(stage(0x104C, 30)));
        CHECK(h.record(AddItem{FormRef("falloutnv.esm", 0xF), 100, Recipient::dflt()}));
        CHECK(h.record(ModKarma{5, Recipient::dflt()}));
        CHECK(out.empty());  // nothing emitted until the scope closes
    }
    CHECK(out.size() == 1);
    CHECK(out[0].cause.kind == CauseKind::QuestResult && out[0].cause.aux == 30);
    CHECK(out[0].effects.size() == 3);
    CHECK(std::holds_alternative<SetStage>(out[0].effects[0]));
    CHECK(std::holds_alternative<AddItem>(out[0].effects[1]));
    CHECK(std::holds_alternative<ModKarma>(out[0].effects[2]));
    CHECK(!h.active());
}

TEST(nested_scopes_merge_into_outermost_txn) {
    CaptureHub h;
    h.setEnabled(true);
    std::vector<EffectTxn> out;
    h.setSink([&](EffectTxn&& t) { out.push_back(std::move(t)); });
    Cause topic;
    topic.kind = CauseKind::DialogueResult;
    topic.source = FormRef("falloutnv.esm", 0x77);
    h.begin(topic);
    h.record(EnableRef{FormRef("falloutnv.esm", 1)});
    h.begin(qcause(0x104C, 40));  // topic result -> SetStage -> stage item result script
    h.record(stage(0x104C, 40));
    h.record(DisableRef{FormRef("falloutnv.esm", 2)});
    h.end();
    CHECK(out.empty());
    h.record(StartQuest{FormRef("falloutnv.esm", 3)});
    h.end();
    CHECK(out.size() == 1);
    CHECK(out[0].cause.kind == CauseKind::DialogueResult);
    CHECK(out[0].effects.size() == 4);
    CHECK(std::holds_alternative<EnableRef>(out[0].effects[0]));
    CHECK(std::holds_alternative<SetStage>(out[0].effects[1]));
    CHECK(std::holds_alternative<DisableRef>(out[0].effects[2]));
    CHECK(std::holds_alternative<StartQuest>(out[0].effects[3]));
}

TEST(echo_guard_blocks_capture_of_remote_apply) {
    CaptureHub h;
    h.setEnabled(true);
    std::vector<EffectTxn> out;
    h.setSink([&](EffectTxn&& t) { out.push_back(std::move(t)); });
    h.begin(qcause(1, 1));
    {
        fnvcoop::EchoGuard g(&h.echo());
        CHECK(!h.record(stage(1, 5)));  // remote apply re-enters the hooks: must not be captured
    }
    CHECK(h.record(stage(1, 6)));
    h.end();
    CHECK(out.size() == 1 && out[0].effects.size() == 1);
    CHECK(std::get<SetStage>(out[0].effects[0]).stage == 6);
    CHECK(h.stats().droppedEcho == 1);
}

TEST(disabled_hub_is_inert) {
    CaptureHub h;  // clients: disabled by default
    std::vector<EffectTxn> out;
    h.setSink([&](EffectTxn&& t) { out.push_back(std::move(t)); });
    h.begin(qcause(1, 1));
    CHECK(!h.active());
    CHECK(!h.record(stage(1, 1)));
    h.end();
    CHECK(out.empty() && h.stats().recorded == 0);
}

TEST(unbalanced_end_is_ignored) {
    CaptureHub h;
    h.setEnabled(true);
    h.end();
    CHECK(h.depth() == 0);
}

TEST(scope_observers_run_on_outermost_only_and_can_record) {
    CaptureHub h;
    h.setEnabled(true);
    std::vector<EffectTxn> out;
    h.setSink([&](EffectTxn&& t) { out.push_back(std::move(t)); });
    int begins = 0, ends = 0;
    h.setScopeObservers([&] { ++begins; },
                        [&] {
                            ++ends;
                            h.record(SetGlobal{FormRef("falloutnv.esm", 0x99), 3.0f});  // e.g. diffed TESGlobal
                        });
    h.begin(qcause(1, 1));
    h.begin(qcause(1, 2));
    h.end();
    CHECK(begins == 1 && ends == 0);  // nested scope: observers silent
    h.record(stage(1, 2));
    h.end();
    CHECK(begins == 1 && ends == 1);
    CHECK(out.size() == 1 && out[0].effects.size() == 2);
    CHECK(std::holds_alternative<SetGlobal>(out[0].effects[1]));  // observer effect lands inside the txn, after the others
}

TEST(script_vars_become_setscriptvar_effects) {
    CaptureHub h;
    h.setEnabled(true);
    std::vector<EffectTxn> out;
    h.setSink([&](EffectTxn&& t) { out.push_back(std::move(t)); });
    CHECK(!h.recordScriptVar({FormRef("falloutnv.esm", 5), 2, 1.0}));  // no scope
    CHECK(!h.recordScriptVar({FormRef("falloutnv.esm", 5), 70000, 1.0}));  // index does not fit uint16
    h.begin(qcause(1, 1));
    CHECK(h.recordScriptVar({FormRef("falloutnv.esm", 5), 2, 7.0}));
    {
        fnvcoop::EchoGuard g(&h.echo());
        CHECK(!h.recordScriptVar({FormRef("falloutnv.esm", 5), 3, 9.0}));
    }
    h.end();
    CHECK(out.size() == 1 && out[0].effects.size() == 1);
    auto* v = std::get_if<SetScriptVar>(&out[0].effects[0]);
    CHECK(v && v->index == 2 && v->value == 7.0 && v->owner == FormRef("falloutnv.esm", 5));
}

TEST(multiple_sinks_all_see_every_txn) {
    CaptureHub h;
    h.setEnabled(true);
    int a = 0, b = 0, aEff = 0, bEff = 0;
    h.setSink([&](EffectTxn&& t) { ++a; aEff += (int)t.effects.size(); });
    h.addSink([&](EffectTxn&& t) { ++b; bEff += (int)t.effects.size(); });
    h.setSink([&](EffectTxn&& t) { ++a; aEff += (int)t.effects.size(); });  // replaces slot 0 only
    h.begin(qcause(1, 1));
    h.record(stage(1, 3));
    h.end();
    CHECK(a == 1 && b == 1 && aEff == 1 && bEff == 1);
}

TEST(effect_json_roundtrip_every_type) {
    FormRef q("falloutnv.esm", 0x10A214);
    std::vector<Effect> all = {SetStage{q, 10}, SetObjective{q, 20, 3}, SetGlobal{q, 2.5f}, EnableRef{q}, DisableRef{q},
        AddItem{q, 5, Recipient::all()}, RemoveItem{q, 1, Recipient::slots(3)}, ModActorValue{24, 100, Recipient::origin()},
        ModReputation{q, 2, -1, Recipient::dflt()}, ModKarma{-5, Recipient::dflt()}, StartQuest{q}, StopQuest{q},
        MoveRef{q, FormRef("falloutnv.esm", 0x99), 1.5f, -2.f, 3.f}, KillActor{q}, ResurrectActor{q},
        SetScriptVar{q, 7, 3.25}, CompleteQuest{q}, CompleteAllObjectives{q}, SetLock{q, true, 50},
        SetFactionRank{q, FormRef("falloutnv.esm", 0x42), -1}, SetPerk{q, 2, Recipient::all()}, FailQuest{q}, SetPlayerControls{0x0B, false, Recipient::origin()},
        RefAddItem{q, q, 3}, RefRemoveItem{q, q, 1}, RemoveAllItems{q}, RefScriptVar{q, 4, 2.5}, AddTopic{q}, AddNote{q}, RemoveNote{q},
        RequestSave{kSaveForce}, SetWeather{q, true}, SetTimeScale{30.f}, DespawnRef{0xFF000001u}, CompanionOwner{q, 0xFF}, SetMapMarker{q, kMarkerVisible | kMarkerTravel}};
    {
        ObjectState os;
        os.ref = q; os.mask = kObjLock | kObjOpen; os.locked = true; os.open = true; os.level = 50;
        all.push_back(os);
        SpawnRef sp;
        sp.id = 0xFF000042u; sp.base = q; sp.cell = FormRef("falloutnv.esm", 0x99); sp.x = 1; sp.y = 2; sp.z = 3; sp.leveled = true;
        all.push_back(sp);
    }
    EffectTxn t;
    t.cause = qcause(0x10A214, 10);
    t.effects = all;
    std::string text = txnToJson(t).dump();
    nvcoop::json::Value v;
    std::string err;
    CHECK(nvcoop::json::parse(text, v, err));
    EffectTxn back;
    CHECK(txnFromJson(v, &back, &err));
    CHECK(back.effects.size() == all.size());
    CHECK(back.effects == all);
    for (size_t i = 0; i < all.size() && i < back.effects.size(); ++i)
        if (!(all[i] == back.effects[i])) std::printf("    mismatch at effect #%zu (variant index %zu)\n", i, all[i].index());
    CHECK(back.cause == t.cause);
    CHECK(text.find("\"quest\":\"falloutnv.esm:10A214\"") != std::string::npos);  // oracle dict form
    nvcoop::json::Value bad = nvcoop::json::Value::object();
    bad.set("type", "Nope");
    Effect e;
    CHECK(!effectFromJson(bad, &e, &err));
}

TEST(suppress_host_never_blocks) {
    SuppressPolicy p;  // host / client: guest=false
    p.enterScript();
    CHECK(!p.blockMutation());
    CHECK(!p.blockResultScript());
    CHECK(!p.skipScript(kScriptTypeQuest));
    CHECK(p.stats().mutations == 0 && p.stats().questScripts == 0);
}

TEST(suppress_guest_blocks_script_mutations_only_inside_scripts) {
    SuppressPolicy p;
    p.setGuest(true);
    CHECK(!p.blockMutation());  // console / load path: no script running -> allowed
    p.enterScript();
    CHECK(p.blockMutation());   // OnDeath script of a locally killed ghost NPC tries SetStage/AddItem/Lock...
    p.leaveScript();
    CHECK(!p.blockMutation());
    CHECK(p.stats().mutations == 1);
}

TEST(suppress_guest_skips_quest_scripts_but_not_object_scripts) {
    SuppressPolicy p;
    p.setGuest(true);
    CHECK(p.skipScript(kScriptTypeQuest));
    CHECK(!p.skipScript(kScriptTypeObject));
    CHECK(!p.skipScript(kScriptTypeMagic));
    CHECK(p.stats().questScripts == 1);
    p.setQuestGate(false);
    CHECK(!p.skipScript(kScriptTypeQuest));
}

TEST(suppress_guest_result_scripts_never_run_locally) {
    SuppressPolicy p;
    p.setGuest(true);
    CHECK(p.blockResultScript());
    CHECK(p.stats().resultScripts == 1);
}

TEST(suppress_lifted_while_applying_host_txn) {
    SuppressPolicy p;
    p.setGuest(true);
    p.enterScript();
    p.beginApply();
    CHECK(!p.blockMutation());
    CHECK(!p.blockResultScript());
    CHECK(!p.skipScript(kScriptTypeQuest));
    p.beginApply();  // nested apply
    p.endApply();
    CHECK(p.applying());
    p.endApply();
    CHECK(!p.applying());
    CHECK(p.blockMutation());  // and back on afterwards
    p.endApply();              // unbalanced end is harmless
    CHECK(!p.applying());
}

TEST(change_tracker_reports_first_sight_and_changes_only) {
    ChangeTracker t;
    uint64_t k = ChangeTracker::varKey(0x10A214, 3), g = ChangeTracker::globalKey(0x35);
    CHECK(t.update(k, 1.0));    // baseline
    CHECK(!t.update(k, 1.0));   // unchanged -> silent
    CHECK(t.update(k, 2.0));
    CHECK(!t.update(k, 2.0));
    CHECK(t.update(g, 5.0));    // global namespace is separate from vars
    CHECK(k != g && ChangeTracker::varKey(1, 2) != ChangeTracker::varKey(2, 1));
    CHECK(t.size() == 2);
    t.clear();
    CHECK(t.update(k, 2.0));
}

TEST(apply_lines_pin_command_names_and_parameter_order) {
    CHECK(killCmd() == "KillActor");
    CHECK(resurrectCmd(false) == "ResurrectActor 0");
    CHECK(resurrectCmd(true) == "ResurrectActor 1");
    CHECK(moveToMarkerCmd(0x32, 1.5f, -2.f, 0.f) == "MoveTo 00000032 1.5000 -2.0000 0.0000");
    CHECK(setPosCmd('Z', 12.25f) == "SetPos Z 12.2500");
    CHECK(setFactionRankCmd(0x104C22, -1) == "SetFactionRank 00104C22 -1");
    CHECK(removeFromFactionCmd(0x104C22) == "RemoveFromFaction 00104C22");
    CHECK(addPerkCmd(0x1234) == "AddPerk 00001234" && removePerkCmd(0x1234) == "RemovePerk 00001234");
    CHECK(lockCmd(-1) == "Lock" && lockCmd(50) == "Lock 50" && unlockCmd() == "UnLock");
    CHECK(setOpenStateCmd(true) == "SetOpenState 1" && setDestroyedCmd(true) == "SetDestroyed 1");
    CHECK(addNoteCmd(0x77) == "AddNote 00000077" && removeNoteCmd(0x77) == "RemoveNote 00000077" && addTopicCmd(0x9) == "AddTopic 00000009");
    CHECK(setWeatherCmd(0x5, false) == "SetWeather 00000005" && setWeatherCmd(0x5, true) == "ForceWeather 00000005");
    CHECK(setTimeScaleCmd(30.f) == "SetGlobalTimeMultiplier 30.0000");
    CHECK(showMapCmd(0x55, false) == "ShowMap 00000055" && showMapCmd(0x55, true) == "ShowMap 00000055 1");
    CHECK(addItemCmd(0xF, 100) == "AddItem 0000000F 100" && removeItemCmd(0xF, 3) == "RemoveItem 0000000F 3" && removeAllItemsCmd() == "RemoveAllItems");
    CHECK(saveCmd(0) == "Autosave" && saveCmd(1) == "SystemSave" && saveCmd(2) == "ForceSave");
    CHECK(isReferenceFormType(0x3A) && isReferenceFormType(0x3B) && isReferenceFormType(0x3C));  // player (0x14) is an ACHR
    CHECK(!isReferenceFormType(0x39) && !isReferenceFormType(0x06));
}

TEST(engine_table_pins_stack_bytes_of_hooked_functions) {
    // Same numbers the x86 glue static_asserts against its detour signatures (engine_hooks.cpp, PIN_F / PIN_C).
    CHECK(addr::ScriptExecute.retBytes == 16);
    CHECK(addr::SetStage.retBytes == 4);
    CHECK(addr::TopicInfoRunResult.retBytes == 8);
    CHECK(addr::ObjectiveSetState.retBytes == 4);
    CHECK(addr::RepAdd.retBytes == 8 && addr::RepSet.retBytes == 8);
    CHECK(addr::PlayerRewardKarma.retBytes == 4);
    CHECK(addr::RefSetDisabled.retBytes == 4);
    CHECK(addr::RefActivate.retBytes == 16);
    CHECK(addr::MarkScriptEvent.retBytes == 0);
    CHECK(addr::ActorSetTeammate.retBytes == 4);
    CHECK(addr::QuestCompleteAllObjectives.retBytes == 4);
    CHECK(addr::PlayerSetControlsDisabled.retBytes == 8);
    CHECK(addr::Cmd_AddItem.retBytes == 0 && addr::Cmd_KillActor.retBytes == 0);
    CHECK(addr::ConsolePrint.retBytes == 8);  // ConsoleManager::Print(this, fmt, va_list): the function we once mis-hooked as cdecl
}

TEST(stage_gate_defers_unless_applying_or_bypassed) {
    StageGate g;
    int asked = 0;
    CHECK(g.allow(1, 5, false));  // no gate installed
    g.set([&](uint32_t, uint32_t s) { ++asked; return s != 20; });  // defer stage 20 (barrier)
    CHECK(g.allow(1, 10, false));
    CHECK(!g.allow(1, 20, false));
    CHECK(g.deferred() == 1);
    CHECK(g.allow(1, 20, true));   // our own remote apply is never gated
    g.beginBypass();
    CHECK(g.allow(1, 20, false));  // the owner re-issuing the deferred stage
    g.beginBypass();
    g.endBypass();
    CHECK(g.allow(1, 20, false));  // nested bypass
    g.endBypass();
    CHECK(!g.allow(1, 20, false)); // and gated again afterwards
    g.endBypass();                 // unbalanced end is harmless
    CHECK(asked == 3);             // bypass/applying paths do not even consult the callback
}

TEST(stale_scope_is_reaped_at_frame_boundary) {
    CaptureHub h;
    h.setEnabled(true);
    std::vector<EffectTxn> out;
    h.setSink([&](EffectTxn&& t) { out.push_back(std::move(t)); });
    h.begin(qcause(0x100, 36));
    h.begin(qcause(0x100, 37));  // nested; neither end() ever runs (destructor skipped by an engine longjmp/SEH unwind)
    CHECK(h.record(stage(0x100, 36)));
    CHECK(h.record(StartQuest{FormRef("falloutnv.esm", 0x100)}));
    CHECK(out.empty());  // the VCG00-36 symptom: recorded 2, emitted 0
    CHECK(h.reapStale() == 2);
    CHECK(out.size() == 1 && out[0].effects.size() == 2);
    CHECK(!out.empty() && out[0].cause.aux == 36);  // outermost cause
    CHECK(h.depth() == 0 && !h.active());
    CHECK(h.stats().staleScopes == 1);
    // subsequent captures work again
    h.begin(qcause(0x200, 5));
    h.record(stage(0x200, 5));
    h.end();
    CHECK(out.size() == 2);
    CHECK(h.reapStale() == 0);  // nothing open: no-op
}

TEST(consecutive_identical_state_effects_collapse_but_quantities_do_not) {
    CaptureHub h;
    h.setEnabled(true);
    std::vector<EffectTxn> out;
    h.setSink([&](EffectTxn&& t) { out.push_back(std::move(t)); });
    FormRef r("falloutnv.esm", 0x101C9E);
    h.begin(qcause(1, 1));
    h.record(EnableRef{r});
    h.record(EnableRef{r});   // second primitive of the same Enable command
    h.record(DisableRef{r});  // a real state change afterwards is kept
    h.record(DisableRef{r});
    h.record(AddItem{r, 1, Recipient::dflt()});
    h.record(AddItem{r, 1, Recipient::dflt()});  // two real pickups: never collapsed
    h.end();
    CHECK(out.size() == 1 && out[0].effects.size() == 4);
    CHECK(h.stats().collapsed == 2);
    // a new scope starts clean: the same effect in the next txn is recorded again
    h.begin(qcause(1, 2));
    h.record(DisableRef{r});
    h.end();
    CHECK(out.size() == 2 && out[1].effects.size() == 1);
}

TEST(failed_quest_txn_carries_fail_only_not_complete) {
    CaptureHub h;
    h.setEnabled(true);
    std::vector<EffectTxn> out;
    h.setSink([&](EffectTxn&& t) { out.push_back(std::move(t)); });
    FormRef q("falloutnv.esm", 0x15D912), other("falloutnv.esm", 0x104EAE);
    h.begin(qcause(0x15D912, 255));
    h.record(StopQuest{q});
    h.record(CompleteQuest{q});  // QuestStageItem::RunResult: SetCompleted(1) ...
    h.record(FailQuest{q});      // ... then SetFailed(1)
    h.record(CompleteQuest{other});  // a different quest that merely completed keeps its CompleteQuest
    h.end();
    CHECK(out.size() == 1 && out[0].effects.size() == 3);
    int fails = 0, completes = 0;
    for (auto& e : out[0].effects) {
        fails += std::holds_alternative<FailQuest>(e);
        if (auto* c = std::get_if<CompleteQuest>(&e)) { ++completes; CHECK(c->quest == other); }
    }
    CHECK(fails == 1 && completes == 1);
}

TEST(inventory_delta_reports_resolved_items_not_the_list) {
    // VCG02 stage 40: the script adds LEVELED LIST 0x96C06; the player actually gained 0x7EA24 (+1) - that is what must be captured.
    Inventory before{{0xF, 100}, {0x1111, 1}};
    Inventory after{{0xF, 100}, {0x1111, 1}, {0x7EA24, 1}};
    auto g = inventoryGained(before, after);
    CHECK(g.size() == 1 && g[0].first == 0x7EA24 && g[0].second == 1);
    CHECK(!isResolvingItemType(0x28));                       // plain item: record the command's own item
    CHECK(isResolvingItemType(0x34) && isResolvingItemType(0x55));  // leveled list, form list
    // removal sets (RemoveAllTypedItems): exact removed counts
    Inventory after2{{0xF, 90}, {0x1111, 0}};
    auto l = inventoryLost(before, after2);
    CHECK(l.size() == 2);
    CHECK(inventoryGained(before, before).empty() && inventoryLost(before, before).empty());
    // stacked count increases are reported as the delta, not the total
    Inventory more{{0xF, 130}, {0x1111, 1}};
    auto g2 = inventoryGained(before, more);
    CHECK(g2.size() == 1 && g2[0].first == 0xF && g2[0].second == 30);
}

TEST(weak_script_scope_captures_ambient_mutations_with_script_cause) {
    CaptureHub h;
    h.setEnabled(true);
    std::vector<EffectTxn> out;
    h.setSink([&](EffectTxn&& t) { out.push_back(std::move(t)); });
    h.setWeakCauseResolver([](uint32_t id) {
        Cause c;
        c.kind = CauseKind::Script;
        c.source = FormRef("falloutnv.esm", id);
        return c;
    });
    FormRef q("falloutnv.esm", 0x104EAE);
    // quest GameMode block / object script: no explicit scope open -> implicit scope instead of droppedNoScope
    CHECK(h.beginWeak(0x1234));
    CHECK(h.active() && h.depth() == 1);
    CHECK(h.record(SetObjective{q, 70, 2}));
    h.endWeak();
    CHECK(out.size() == 1 && out[0].cause.kind == CauseKind::Script && out[0].cause.source.localId == 0x1234);
    CHECK(h.stats().droppedNoScope == 0 && h.stats().weakTxns == 1);
    // an empty script run costs nothing: no txn, not even an "empty scope"
    CHECK(h.beginWeak(0x1));
    h.endWeak();
    CHECK(out.size() == 1 && h.stats().emptyScopes == 0);
    // never opens inside another scope
    h.begin(qcause(1, 1));
    CHECK(!h.beginWeak(0x2));
    h.end();
}

TEST(explicit_scope_inside_weak_scope_keeps_its_own_cause) {
    CaptureHub h;
    h.setEnabled(true);
    std::vector<EffectTxn> out;
    h.setSink([&](EffectTxn&& t) { out.push_back(std::move(t)); });
    h.setWeakCauseResolver([](uint32_t id) {
        Cause c;
        c.kind = CauseKind::Script;
        c.source = FormRef("falloutnv.esm", id);
        return c;
    });
    FormRef r("falloutnv.esm", 0x777);
    // object script (elevator): player.MoveTo, then SetStage of a rule-bearing quest, then more
    CHECK(h.beginWeak(0xABC));
    h.record(MoveRef{FormRef("falloutnv.esm", 0x14), r, 0, 0, 0});
    h.begin(qcause(0x15D912, 10));
    h.record(stage(0x15D912, 10));
    h.end();
    h.record(EnableRef{r});
    h.endWeak();
    CHECK(out.size() == 3);
    CHECK(out.size() == 3 && out[0].cause.kind == CauseKind::Script && std::holds_alternative<MoveRef>(out[0].effects[0]));
    CHECK(out.size() == 3 && out[1].cause.kind == CauseKind::QuestResult && std::holds_alternative<SetStage>(out[1].effects[0]));
    CHECK(out.size() == 3 && out[2].cause.kind == CauseKind::Script && std::holds_alternative<EnableRef>(out[2].effects[0]));
    CHECK(h.depth() == 0);
}

TEST(acting_context_is_a_stack_defaulting_to_the_host_player) {
    ActingContext a;
    CHECK(a.current() == 0x14);
    a.push(0xFF001234);
    CHECK(a.current() == 0xFF001234);
    a.push(0);  // unknown ghost -> falls back to the host player
    CHECK(a.current() == 0x14);
    a.pop();
    CHECK(a.current() == 0xFF001234);
    a.pop();
    a.pop();  // unbalanced pop is harmless
    CHECK(a.current() == 0x14 && a.depth() == 0);
}

TEST(guest_local_run_allow_list_keeps_selected_quest_scripts_running) {
    SuppressPolicy p;
    p.setGuest(true);
    p.setGuestLocalRun({0x500, 0x600});
    CHECK(!p.skipScript(kScriptTypeQuest, 0x500));  // allow-listed (achievement / rumble / DLC setup script)
    CHECK(p.skipScript(kScriptTypeQuest, 0x700));   // any other quest script is still skipped
    CHECK(p.skipScript(kScriptTypeQuest, 0));       // unknown script id: skipped
    CHECK(p.stats().questScripts == 2);
    p.setGuestLocalRun({});
    CHECK(p.skipScript(kScriptTypeQuest, 0x500));   // list replaced
}

int main() {
    run("install_patches_entry_and_builds_trampoline", install_patches_entry_and_builds_trampoline);
    run("wrong_prologue_refuses_and_writes_nothing", wrong_prologue_refuses_and_writes_nothing);
    run("unrelated_bytes_refuse", unrelated_bytes_refuse);
    run("foreign_detour_is_reported_not_overwritten", foreign_detour_is_reported_not_overwritten);
    run("bad_specs_are_rejected", bad_specs_are_rejected);
    run("refcount_install_release_restore", refcount_install_release_restore);
    run("different_detour_for_same_target_refused", different_detour_for_same_target_refused);
    run("tampered_code_not_restored", tampered_code_not_restored);
    run("alloc_or_write_failure_leaves_game_code_untouched", alloc_or_write_failure_leaves_game_code_untouched);
    run("registry_destructor_restores_everything", registry_destructor_restores_everything);
    run("engine_address_table_is_sane", engine_address_table_is_sane);
    run("engine_table_signature_roundtrip_on_fake_image", engine_table_signature_roundtrip_on_fake_image);
    run("no_scope_no_record", no_scope_no_record);
    run("scope_emits_one_txn_with_cause_and_order", scope_emits_one_txn_with_cause_and_order);
    run("nested_scopes_merge_into_outermost_txn", nested_scopes_merge_into_outermost_txn);
    run("echo_guard_blocks_capture_of_remote_apply", echo_guard_blocks_capture_of_remote_apply);
    run("disabled_hub_is_inert", disabled_hub_is_inert);
    run("unbalanced_end_is_ignored", unbalanced_end_is_ignored);
    run("guest_local_run_allow_list_keeps_selected_quest_scripts_running", guest_local_run_allow_list_keeps_selected_quest_scripts_running);
    run("acting_context_is_a_stack_defaulting_to_the_host_player", acting_context_is_a_stack_defaulting_to_the_host_player);
    run("weak_script_scope_captures_ambient_mutations_with_script_cause", weak_script_scope_captures_ambient_mutations_with_script_cause);
    run("explicit_scope_inside_weak_scope_keeps_its_own_cause", explicit_scope_inside_weak_scope_keeps_its_own_cause);
    run("inventory_delta_reports_resolved_items_not_the_list", inventory_delta_reports_resolved_items_not_the_list);
    run("failed_quest_txn_carries_fail_only_not_complete", failed_quest_txn_carries_fail_only_not_complete);
    run("consecutive_identical_state_effects_collapse_but_quantities_do_not", consecutive_identical_state_effects_collapse_but_quantities_do_not);
    run("stale_scope_is_reaped_at_frame_boundary", stale_scope_is_reaped_at_frame_boundary);
    run("stage_gate_defers_unless_applying_or_bypassed", stage_gate_defers_unless_applying_or_bypassed);
    run("engine_table_pins_stack_bytes_of_hooked_functions", engine_table_pins_stack_bytes_of_hooked_functions);
    run("apply_lines_pin_command_names_and_parameter_order", apply_lines_pin_command_names_and_parameter_order);
    run("change_tracker_reports_first_sight_and_changes_only", change_tracker_reports_first_sight_and_changes_only);
    run("suppress_host_never_blocks", suppress_host_never_blocks);
    run("suppress_guest_blocks_script_mutations_only_inside_scripts", suppress_guest_blocks_script_mutations_only_inside_scripts);
    run("suppress_guest_skips_quest_scripts_but_not_object_scripts", suppress_guest_skips_quest_scripts_but_not_object_scripts);
    run("suppress_guest_result_scripts_never_run_locally", suppress_guest_result_scripts_never_run_locally);
    run("suppress_lifted_while_applying_host_txn", suppress_lifted_while_applying_host_txn);
    run("effect_json_roundtrip_every_type", effect_json_roundtrip_every_type);
    run("script_vars_become_setscriptvar_effects", script_vars_become_setscriptvar_effects);
    run("multiple_sinks_all_see_every_txn", multiple_sinks_all_see_every_txn);
    run("scope_observers_run_on_outermost_only_and_can_record", scope_observers_run_on_outermost_only_and_can_record);
    std::printf("%d checks, %d failed\n", g_checks, g_fail);
    return g_fail ? 1 : 0;
}
