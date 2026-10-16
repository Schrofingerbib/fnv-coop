// L12 world-state unit tests. Built once against the real sources and once per NVWORLD_MUT=<n> (each mutant MUST fail).
#include <cstdio>
#include <cstring>
#include <map>
#include <set>
#include <string>
#include <vector>

#include "effect_json.h"
#include "hook_patch.h"
#include "world_addrs.h"
#include "world_core.h"
#include "world_plan.h"

using namespace nvworld;
using fnvcoop::FormRef;

static int g_fail = 0, g_checks = 0;
#define CHECK(c)                                                       \
    do {                                                               \
        ++g_checks;                                                    \
        if (!(c)) {                                                    \
            ++g_fail;                                                  \
            std::printf("  FAIL %s:%d  %s\n", __FILE__, __LINE__, #c); \
        }                                                              \
    } while (0)
#define TEST(name) static void name()
static void run(const char* n, void (*f)()) {
    int before = g_fail;
    f();
    std::printf("[%s] %s\n", g_fail == before ? " ok " : "FAIL", n);
}

static FormRef esm(uint32_t id) { return FormRef("falloutnv.esm", id); }
static FormRef created(uint32_t id) { return FormRef(fnvcoop::createdRefPlugin(), 0xFF000000u | id); }

// ---------------------------------------------------------------------------------------------- fake engine resolver
struct FakeResolver : nvhooks::FormResolver {
    bool toRef(uint32_t id, FormRef* out) override {
        if ((id >> 24) == 0xFF || (id >> 24) != 0) return false;  // engine resolver cannot name created refs
        *out = esm(id & 0xFFFFFF);
        return true;
    }
    uint32_t fromRef(const FormRef& r) override { return r.plugin == "falloutnv.esm" ? r.localId : 0; }
    void* lookup(uint32_t id) override { return id ? reinterpret_cast<void*>(uintptr_t(id)) : nullptr; }
};

TEST(created_ref_map) {
    CreatedRefMap m;
    CHECK(m.local(created(1)) == 0);
    m.bind(created(1), 0x1000);
    CHECK(m.local(created(1)) == 0x1000);
    FormRef h;
    CHECK(m.hostOf(0x1000, &h) && h == created(1));
    m.bind(created(1), 0x2000);  // the host ref now maps to a different local object
    CHECK(m.local(created(1)) == 0x2000);
    CHECK(!m.hostOf(0x1000, &h));
    CHECK(m.hostOf(0x2000, &h));
    CHECK(m.size() == 1);
    CHECK(m.forget(created(1)));
    CHECK(!m.hostOf(0x2000, &h));
    CHECK(m.local(created(1)) == 0);
    CHECK(!m.forget(created(1)));
}

TEST(world_resolver_host) {
    FakeResolver inner;
    CreatedRefMap map;
    WorldResolver r(inner, map, true);
    FormRef f;
    CHECK(r.toRef(0xFF001234u, &f) && f == FormRef(fnvcoop::createdRefPlugin(), 0xFF001234u));
    CHECK(r.fromRef(f) == 0xFF001234u);
    CHECK(r.toRef(0x00104C79u, &f) && f == esm(0x104C79));
    CHECK(r.fromRef(esm(0x104C79)) == 0x104C79u);
}

TEST(world_resolver_client) {
    FakeResolver inner;
    CreatedRefMap map;
    WorldResolver r(inner, map, false);
    FormRef f;
    CHECK(r.fromRef(created(7)) == 0);  // not created here yet
    CHECK(!r.toRef(0xFF000055u, &f));
    map.bind(created(7), 0xFF000055u);
    CHECK(r.fromRef(created(7)) == 0xFF000055u);
    CHECK(r.toRef(0xFF000055u, &f) && f == created(7));  // local id back to the HOST's name
    CHECK(r.toRef(0x00104C79u, &f) && f == esm(0x104C79));
}

static fnvcoop::SpawnRef spawn(uint32_t id, uint32_t cell = 0x10) {
    fnvcoop::SpawnRef s;
    s.id = 0xFF000000u | id;
    s.base = esm(0x500 + id);
    s.cell = esm(cell);
    s.x = 1;
    s.y = 2;
    s.z = 3;
    return s;
}

TEST(spawn_ledger_create_and_despawn) {
    SpawnLedger l;
    CHECK(l.onSpawn(spawn(1), true) == SpawnAction::Create);
    l.onCreated(created(1), 0xFF000A00u);
    CHECK(l.onSpawn(spawn(1), true) == SpawnAction::Duplicate);  // replayed txn: never create twice
    auto d = l.onDespawn(fnvcoop::DespawnRef{0xFF000001u});
    CHECK(d.action == DespawnAction::DeleteLocal && d.localRefID == 0xFF000A00u);
    CHECK(l.map().size() == 0);
    CHECK(l.onDespawn(fnvcoop::DespawnRef{0xFF000001u}).action == DespawnAction::Unknown);  // second despawn is a no-op
}

TEST(spawn_ledger_queue) {
    SpawnLedger l;
    CHECK(l.onSpawn(spawn(1, 0x10), false) == SpawnAction::Queued);
    CHECK(l.onSpawn(spawn(2, 0x20), false) == SpawnAction::Queued);
    CHECK(l.onSpawn(spawn(1, 0x10), false) == SpawnAction::Duplicate);
    CHECK(l.pending() == 2);
    auto got = l.takeReady([](const fnvcoop::SpawnRef& s) { return s.cell == esm(0x10); });
    CHECK(got.size() == 1 && got[0].ref() == created(1));
    CHECK(l.pending() == 1);
    got = l.takeReady([](const fnvcoop::SpawnRef& s) { return s.cell == esm(0x10); });
    CHECK(got.empty());  // already taken: no second creation
    // despawn before the cell loaded: the queued spawn is cancelled, nothing is ever created
    CHECK(l.onDespawn(fnvcoop::DespawnRef{0xFF000002u}).action == DespawnAction::CancelledPending);
    CHECK(l.pending() == 0);
    got = l.takeReady([](const fnvcoop::SpawnRef&) { return true; });
    CHECK(got.empty());
}

TEST(ref_var_tracker) {
    RefVarTracker t;
    t.beginPoll(100);
    CHECK(!t.observe(0x100, 3, 0.0));  // baseline is silent
    CHECK(!t.observe(0x100, 3, 0.0));  // unchanged
    CHECK(t.observe(0x100, 3, 1.0));   // change
    CHECK(!t.observe(0x100, 3, 1.0));  // not repeated
    CHECK(t.observe(0x100, 3, 0.0));   // changes back
    CHECK(t.reportedThisPoll() == 2);
    CHECK(t.size() == 1);
}

TEST(ref_var_budget) {
    RefVarTracker t;
    t.beginPoll(2);
    for (uint16_t i = 0; i < 5; ++i) t.observe(0x200, i, 0);
    t.beginPoll(2);
    int sent = 0;
    for (uint16_t i = 0; i < 5; ++i) sent += t.observe(0x200, i, 1.0) ? 1 : 0;
    CHECK(sent == 2);                     // capped
    t.beginPoll(2);
    for (uint16_t i = 0; i < 5; ++i) sent += t.observe(0x200, i, 1.0) ? 1 : 0;
    CHECK(sent == 4);                     // the deferred ones are reported by the next poll, never lost
    t.beginPoll(2);
    for (uint16_t i = 0; i < 5; ++i) sent += t.observe(0x200, i, 1.0) ? 1 : 0;
    CHECK(sent == 5);
    t.beginPoll(2);
    for (uint16_t i = 0; i < 5; ++i) sent += t.observe(0x200, i, 1.0) ? 1 : 0;
    CHECK(sent == 5);
}

TEST(ref_var_forget) {
    RefVarTracker t;
    t.beginPoll(10);
    t.observe(0x300, 1, 5);
    t.observe(0x301, 1, 5);
    t.forgetRef(0x300);
    CHECK(t.size() == 1);
    CHECK(!t.observe(0x300, 1, 9));  // re-baselined after unload, the 9 is not reported
    CHECK(t.observe(0x300, 1, 10));
}

TEST(weather_time_sync) {
    WeatherTimeSync s;
    auto w1 = esm(0x10), w2 = esm(0x20);
    auto e = s.poll(w1, true, 1.0f);
    CHECK(e.size() == 2);  // first sight of both
    CHECK(std::get<fnvcoop::SetWeather>(e[0]).weather == w1);
    CHECK(std::get<fnvcoop::SetTimeScale>(e[1]).scale == 1.0f);
    CHECK(s.poll(w1, true, 1.0f).empty());  // unchanged: nothing
    e = s.poll(w2, true, 1.0f);
    CHECK(e.size() == 1 && std::get<fnvcoop::SetWeather>(e[0]).weather == w2);
    CHECK(s.poll(w2, false, 1.0f).empty());  // interior: no weather
    e = s.poll(w2, true, 30.0f);
    CHECK(e.size() == 1 && std::get<fnvcoop::SetTimeScale>(e[0]).scale == 30.0f);
    // a script already sent SetWeather(w1) through the capture hub: the next poll must not repeat it
    s.noteSent(fnvcoop::SetWeather{w1, true});
    CHECK(s.poll(w1, true, 30.0f).empty());
    s.noteSent(fnvcoop::SetTimeScale{5.0f});
    CHECK(s.poll(w1, true, 5.0f).empty());
}

TEST(weather_block_local) {
    CHECK(WeatherTimeSync::blockLocal(true, false));
    CHECK(!WeatherTimeSync::blockLocal(true, true));    // a host txn being applied may change it
    CHECK(!WeatherTimeSync::blockLocal(false, false));  // the host plays normally
}

TEST(av_effects) {
    auto a = esm(0x104C79);
    CHECK(!makeAvEffect(a, 16, AvCmdKind::Mod, 100, 100));  // nothing changed
    auto e = makeAvEffect(a, 16, AvCmdKind::Set, 100, 250);
    CHECK(e && e->mode == AvMode::Set && e->value == 250 && e->av == 16);
    e = makeAvEffect(a, 16, AvCmdKind::Force, 100, 250);
    CHECK(e && e->mode == AvMode::Set && e->value == 250);
    e = makeAvEffect(a, 16, AvCmdKind::Mod, 100, 125);
    CHECK(e && e->mode == AvMode::Mod && e->value == 25);
    e = makeAvEffect(a, 16, AvCmdKind::Damage, 100, 70);
    CHECK(e && e->mode == AvMode::Damage && e->value == 30);  // positive damage amount
    e = makeAvEffect(a, 16, AvCmdKind::Restore, 40, 100);
    CHECK(e && e->mode == AvMode::Restore && e->value == 60);
}

TEST(av_names_and_lines) {
    CHECK(std::strcmp(avName(16), "Health") == 0);
    CHECK(std::strcmp(avName(12), "ActionPoints") == 0);
    CHECK(std::strcmp(avName(0), "Aggression") == 0);
    CHECK(std::strcmp(avName(1), "Confidence") == 0);
    CHECK(std::strcmp(avName(5), "Strength") == 0);
    CHECK(std::strcmp(avName(41), "Guns") == 0);
    CHECK(std::strcmp(avName(45), "Unarmed") == 0);
    CHECK(std::strcmp(avName(999), "") == 0);
    SetActorValueRef x{esm(1), 16, 250, AvMode::Set};
    CHECK(avLine(x) == "SetActorValue Health 250.0000");
    x.mode = AvMode::Mod; x.value = -5;
    CHECK(avLine(x) == "ModActorValue Health -5.0000");
    x.mode = AvMode::Damage; x.value = 30;
    CHECK(avLine(x) == "DamageActorValue Health 30.0000");
    x.mode = AvMode::Restore; x.value = 60;
    CHECK(avLine(x) == "RestoreActorValue Health 60.0000");
    x.av = 999;
    CHECK(avLine(x).empty());
}

TEST(console_lines) {
    CHECK(markForDeleteLine() == "MarkForDelete");
    SetEssential e{esm(0x104C79), true, false};
    CHECK(setEssentialLine(e, 0x104C79) == "SetEssential 00104C79 1");
    e.essential = false;
    CHECK(setEssentialLine(e, 0x104C79) == "SetEssential 00104C79 0");
    e.refOnly = true; e.essential = true;
    CHECK(setEssentialLine(e, 0x104C79) == "SetActorRefEssential 1");
    CHECK(setRestrainedLine(true) == "SetRestrained 1");
    CHECK(setRestrainedLine(false) == "SetRestrained 0");
    CHECK(setRelationLine(Relation::Enemy, 0x11, 0x22) == "SetEnemy 00000011 00000022");
    CHECK(setRelationLine(Relation::Ally, 0x11, 0x22) == "SetAlly 00000011 00000022");
    CHECK(scriptPackageLine(true, 0xAB) == "AddScriptPackage 000000AB");
    CHECK(scriptPackageLine(false, 0xAB) == "RemoveScriptPackage 000000AB");
    CHECK(placeAtMeLine(0x1234, 3) == "PlaceAtMe 00001234 3");
    CHECK(setPosLine('X', 1.5f) == "SetPos X 1.5000");
}

TEST(world_log_coalesce) {
    WorldLog l;
    l.push(SetEssential{esm(1), true, false});
    l.push(SetEssential{esm(1), false, false});  // last write wins
    l.push(SetEssential{esm(2), true, false});
    l.push(SetActorValueRef{esm(1), 16, 50, AvMode::Set});
    l.push(SetActorValueRef{esm(1), 16, 75, AvMode::Set});
    l.push(SetActorValueRef{esm(1), 16, 5, AvMode::Damage});  // deltas are ordered and never coalesced
    l.push(SetActorValueRef{esm(1), 16, 5, AvMode::Damage});
    l.push(SetRestrained{esm(1), true});
    l.push(SetRestrained{esm(1), false});
    CHECK(l.size() == 6);
    auto v = l.drain();
    CHECK(v.size() == 6 && l.size() == 0);
    if (v.size() != 6) return;
    CHECK(std::get<SetEssential>(v[0]).essential == false);
    CHECK(std::get<SetActorValueRef>(v[2]).value == 75);
    CHECK(std::get<SetRestrained>(v[5]).on == false);
}

TEST(world_effect_json) {
    std::vector<WorldEffect> all = {
        SetActorValueRef{esm(0x104C79), 16, 12.5f, AvMode::Damage},
        SetEssential{esm(0x104C79), true, true},
        SetRestrained{esm(0x104C79), true},
        SetRelation{esm(0x11), esm(0x22), Relation::Ally},
        SetScriptPackage{esm(0x1), esm(0x2), false},
    };
    for (auto& e : all) {
        WorldEffect back;
        std::string err;
        std::string js = worldEffectToJson(e);
        CHECK(worldEffectFromJson(js, &back, &err));
        CHECK(back == e);
    }
    WorldEffect x;
    std::string err;
    CHECK(!worldEffectFromJson("{\"type\":\"Nope\"}", &x, &err));
    CHECK(!worldEffectFromJson("not json", &x, &err));
}


// ---------------------------------------------------------------------------------------------- apply planning
struct PlanFx {
    FakeResolver inner;
    CreatedRefMap* mapPtr = nullptr;
    SpawnLedger ledger;
    std::set<uint32_t> loadedCells;
    PlanEnv env;
    WorldResolver res;
    PlanFx() : res(inner, ledger.map(), false), env{res, ledger, nullptr} {
        env.cellReady = [this](const FormRef& c) { return loadedCells.count(c.localId) > 0; };
    }
};

TEST(plan_spawn) {
    PlanFx f;
    f.loadedCells.insert(0x10);
    auto p = planEffect(spawn(1, 0x10), f.env);
    CHECK(p.status == PlanStatus::Ok && p.steps.size() == 1);
    CHECK(p.steps[0].kind == StepKind::CreateSpawn && p.steps[0].baseID == 0x501 && p.steps[0].cellID == 0x10);
    f.ledger.onCreated(created(1), 0xFF000B00u);
    p = planEffect(spawn(1, 0x10), f.env);  // replayed
    CHECK(p.status == PlanStatus::Ok && p.steps.empty());
    // cell not loaded -> queued, nothing to run yet
    p = planEffect(spawn(2, 0x20), f.env);
    CHECK(p.status == PlanStatus::Queued && p.steps.empty());
    CHECK(planQueuedSpawns(f.env).empty());
    f.loadedCells.insert(0x20);
    auto q = planQueuedSpawns(f.env);
    CHECK(q.size() == 1 && q[0].steps.size() == 1 && q[0].steps[0].spawn.ref() == created(2));
    CHECK(planQueuedSpawns(f.env).empty());
}

TEST(plan_spawn_missing_base) {
    PlanFx f;
    f.loadedCells.insert(0x10);
    fnvcoop::SpawnRef s = spawn(3, 0x10);
    s.base = FormRef("nosuchmod.esp", 5);
    CHECK(planEffect(s, f.env).status == PlanStatus::FormMissing);
}

TEST(plan_despawn) {
    PlanFx f;
    f.loadedCells.insert(0x10);
    planEffect(spawn(1, 0x10), f.env);
    f.ledger.onCreated(created(1), 0xFF000C00u);
    auto p = planEffect(fnvcoop::DespawnRef{0xFF000001u}, f.env);
    CHECK(p.status == PlanStatus::Ok && p.steps.size() == 1);
    CHECK(p.steps[0].kind == StepKind::ConsoleOnRef && p.steps[0].refID == 0xFF000C00u && p.steps[0].line == "MarkForDelete");
    p = planEffect(fnvcoop::DespawnRef{0xFF000001u}, f.env);  // already gone
    CHECK(p.status == PlanStatus::Ok && p.steps.empty());
    planEffect(spawn(9, 0x99), f.env);  // queued
    p = planEffect(fnvcoop::DespawnRef{0xFF000009u}, f.env);
    CHECK(p.status == PlanStatus::Ok && p.steps.empty() && f.ledger.pending() == 0);
}

TEST(plan_ref_script_var) {
    PlanFx f;
    auto p = planEffect(fnvcoop::RefScriptVar{esm(0x777), 4, 2.5}, f.env);
    CHECK(p.status == PlanStatus::Ok && p.steps.size() == 1);
    CHECK(p.steps[0].kind == StepKind::SetRefVar && p.steps[0].refID == 0x777 && p.steps[0].varIndex == 4 && p.steps[0].varValue == 2.5);
    p = planEffect(fnvcoop::RefScriptVar{FormRef("nosuchmod.esp", 1), 4, 2.5}, f.env);
    CHECK(p.status == PlanStatus::FormMissing);
    // a var on a created ref goes through the host->local map
    f.ledger.onCreated(created(5), 0xFF000D00u);
    p = planEffect(fnvcoop::RefScriptVar{created(5), 1, 9}, f.env);
    CHECK(p.status == PlanStatus::Ok && p.steps.size() == 1 && p.steps[0].refID == 0xFF000D00u);
    CHECK(planEffect(fnvcoop::SetStage{esm(1), 10}, f.env).status == PlanStatus::Unsupported);
    CHECK(handlesEffect(fnvcoop::SpawnRef{}) && handlesEffect(fnvcoop::DespawnRef{}) && handlesEffect(fnvcoop::RefScriptVar{}));
    CHECK(!handlesEffect(fnvcoop::SetStage{}));
}

TEST(plan_world_effects) {
    PlanFx f;
    auto p = planWorldEffect(SetActorValueRef{esm(0x100), 16, 50, AvMode::Damage}, f.env);
    CHECK(p.status == PlanStatus::Ok && p.steps.size() == 1 && p.steps[0].refID == 0x100 && p.steps[0].line == "DamageActorValue Health 50.0000");
    p = planWorldEffect(SetActorValueRef{esm(0x100), 9999, 1, AvMode::Set}, f.env);
    CHECK(p.status == PlanStatus::Unsupported);
    p = planWorldEffect(SetEssential{esm(0x200), true, true}, f.env);  // ref-only: on the ref
    CHECK(p.steps.size() == 1 && p.steps[0].kind == StepKind::ConsoleOnRef && p.steps[0].refID == 0x200 && p.steps[0].line == "SetActorRefEssential 1");
    p = planWorldEffect(SetEssential{esm(0x200), false, false}, f.env);  // base form: global command
    CHECK(p.steps.size() == 1 && p.steps[0].kind == StepKind::ConsoleGlobal && p.steps[0].line == "SetEssential 00000200 0");
    p = planWorldEffect(SetRestrained{esm(0x300), true}, f.env);
    CHECK(p.steps.size() == 1 && p.steps[0].refID == 0x300 && p.steps[0].line == "SetRestrained 1");
    p = planWorldEffect(SetRelation{esm(0x11), esm(0x22), Relation::Enemy}, f.env);
    CHECK(p.steps.size() == 1 && p.steps[0].kind == StepKind::ConsoleGlobal && p.steps[0].line == "SetEnemy 00000011 00000022");
    p = planWorldEffect(SetRelation{esm(0x11), FormRef("nosuchmod.esp", 2), Relation::Enemy}, f.env);
    CHECK(p.status == PlanStatus::FormMissing);
    p = planWorldEffect(SetScriptPackage{esm(0x300), esm(0x400), true}, f.env);
    CHECK(p.steps.size() == 1 && p.steps[0].refID == 0x300 && p.steps[0].line == "AddScriptPackage 00000400");
}

// ---------------------------------------------------------------------------------------------- engine hook specs
// The prologue of every NEW engine function this lane detours is pinned here. The patcher refuses to write unless all
// `expectedLen` bytes match (HookRegistry rule 1), so a drifted spec = the hook is skipped, never forced.
struct FakeMem : nvhooks::MemoryOps {
    std::map<nvhooks::Addr, std::vector<uint8_t>> img;
    std::map<nvhooks::Addr, std::vector<uint8_t>> heap;
    nvhooks::Addr next = 0x20000000;
    bool read(nvhooks::Addr a, void* out, size_t n) override {
        for (auto& [b, v] : img)
            if (a >= b && a + n <= b + v.size()) { std::memcpy(out, &v[a - b], n); return true; }
        for (auto& [b, v] : heap)
            if (a >= b && a + n <= b + v.size()) { std::memcpy(out, &v[a - b], n); return true; }
        return false;
    }
    bool write(nvhooks::Addr a, const void* in, size_t n) override {
        for (auto& [b, v] : img)
            if (a >= b && a + n <= b + v.size()) { std::memcpy(&v[a - b], in, n); return true; }
        for (auto& [b, v] : heap)
            if (a >= b && a + n <= b + v.size()) { std::memcpy(&v[a - b], in, n); return true; }
        return false;
    }
    nvhooks::Addr allocExec(size_t n) override {
        nvhooks::Addr a = next;
        next += (nvhooks::Addr)((n + 15) & ~size_t(15)) + 16;
        heap[a] = std::vector<uint8_t>(n, 0xCC);
        return a;
    }
    void freeExec(nvhooks::Addr a, size_t) override { heap.erase(a); }
};

TEST(world_specs_pinned) {
    CHECK(world_addrs::kCount > 0);
    for (size_t i = 0; i < world_addrs::kCount; ++i) {
        const nvhooks::HookSpec& s = *world_addrs::kAll[i];
        std::string why;
        CHECK(nvhooks::specIsValid(s, &why));
        CHECK(s.expected[0] == 0x55 && s.expected[1] == 0x8B && s.expected[2] == 0xEC);
        CHECK(s.retBytes == 0);  // every world hook is a cdecl script-command handler
        FakeMem mem;
        mem.img[s.target] = std::vector<uint8_t>(s.expected, s.expected + s.expectedLen);
        mem.img[s.target].resize(64, 0x90);
        nvhooks::HookRegistry reg(mem);
        nvhooks::Addr tramp = 0;
        CHECK(reg.acquire(s, 0x30000000, &tramp) == nvhooks::PatchStatus::Ok);
        CHECK(reg.release(s) == nvhooks::PatchStatus::Restored);
        // one byte of drift anywhere in the pinned window: refuse, write nothing
        for (int b = 0; b < s.expectedLen; ++b) {
            FakeMem m2;
            m2.img[s.target] = std::vector<uint8_t>(s.expected, s.expected + s.expectedLen);
            m2.img[s.target].resize(64, 0x90);
            m2.img[s.target][b] ^= 0x01;
            auto before = m2.img[s.target];
            nvhooks::HookRegistry r2(m2);
            CHECK(r2.acquire(s, 0x30000000, &tramp) != nvhooks::PatchStatus::Ok);
            CHECK(m2.img[s.target] == before);
        }
    }
}


// The generated header must equal the recorded live-process dump byte for byte (the dump is the evidence; see internal lane notes).
struct DumpRow {
    std::string name;
    unsigned addr = 0;
    int stolen = 0;
    std::vector<uint8_t> bytes;
};
static std::vector<DumpRow> readDump() {
    std::vector<DumpRow> rows;
    FILE* f = std::fopen(NVW_DUMP_PATH, "r");
    if (!f) return rows;
    char line[1024];
    while (std::fgets(line, sizeof line, f)) {
        char name[128], hex[64];
        unsigned addr = 0;
        int stolen = 0;
        if (std::sscanf(line, "%127s 0x%x stolen=%d bytes16=%32s", name, &addr, &stolen, hex) != 4) continue;
        DumpRow r;
        r.name = name;
        r.addr = addr;
        r.stolen = stolen;
        for (int i = 0; i < 16; ++i) {
            unsigned b = 0;
            std::sscanf(hex + 2 * i, "%2x", &b);
            r.bytes.push_back((uint8_t)b);
        }
        rows.push_back(r);
    }
    std::fclose(f);
    return rows;
}

TEST(world_specs_match_dump) {
    auto rows = readDump();
    CHECK(rows.size() >= 10);
    auto find = [&](const char* n) -> const DumpRow* {
        for (auto& r : rows)
            if (r.name == n) return &r;
        return nullptr;
    };
    for (size_t i = 0; i < world_addrs::kCount; ++i) {
        nvhooks::HookSpec s = *world_addrs::kAll[i];
#if NVWORLD_MUT == 26
        s.expected[3] ^= 0x01;  // MUTANT: a spec that drifted from the evidence
#endif
        const DumpRow* r = find(s.name);
        CHECK(r != nullptr);
        if (!r) continue;
        CHECK(r->addr == s.target);
        CHECK(r->stolen == s.stolen);
        CHECK(std::memcmp(r->bytes.data(), s.expected, 16) == 0);
    }
    for (size_t i = 0; i < world_addrs::kCallCount; ++i) {
        const auto& c = *world_addrs::kCalls[i];
        const DumpRow* r = find(c.name);
        CHECK(r != nullptr);
        if (!r) continue;
        CHECK(r->addr == c.addr);
        CHECK(std::memcmp(r->bytes.data(), c.bytes, 16) == 0);
    }
    CHECK(world_addrs::kCount == 7);
    CHECK(world_addrs::kCallCount == 3);
}

int main() {
    std::setvbuf(stdout, nullptr, _IONBF, 0);
    run("created_ref_map", created_ref_map);
    run("world_resolver_host", world_resolver_host);
    run("world_resolver_client", world_resolver_client);
    run("spawn_ledger_create_and_despawn", spawn_ledger_create_and_despawn);
    run("spawn_ledger_queue", spawn_ledger_queue);
    run("ref_var_tracker", ref_var_tracker);
    run("ref_var_budget", ref_var_budget);
    run("ref_var_forget", ref_var_forget);
    run("weather_time_sync", weather_time_sync);
    run("weather_block_local", weather_block_local);
    run("av_effects", av_effects);
    run("av_names_and_lines", av_names_and_lines);
    run("console_lines", console_lines);
    run("world_log_coalesce", world_log_coalesce);
    run("world_effect_json", world_effect_json);
    run("plan_spawn", plan_spawn);
    run("plan_spawn_missing_base", plan_spawn_missing_base);
    run("plan_despawn", plan_despawn);
    run("plan_ref_script_var", plan_ref_script_var);
    run("plan_world_effects", plan_world_effects);
    run("world_specs_pinned", world_specs_pinned);
    run("world_specs_match_dump", world_specs_match_dump);
    std::printf("%d checks, %d failed\n", g_checks, g_fail);
    return g_fail ? 1 : 0;
}
