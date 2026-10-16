// L12 detours + host polls. x86 / MSVC only. Style follows plugin/hooks/engine_hooks.cpp (L5): every detour is a plain cdecl
// script-command handler, pinned by prologue bytes in world_addrs.h (a mismatch skips the hook, never forces it).
#include "world_hooks.h"

#include <Windows.h>

#include <algorithm>
#include <cmath>
#include <cstring>
#include <set>

#include "GameAPI.h"
#include "GameData.h"
#include "GameExtraData.h"
#include "GameForms.h"
#include "GameObjects.h"
#include "effect_json.h"
#include "engine_addrs.h"
#include "world_addrs.h"

using namespace fnvcoop;

namespace nvworld {
namespace {

struct Win32Memory : nvhooks::MemoryOps {
    bool read(nvhooks::Addr a, void* out, size_t n) override { return peekMemory(a, out, n); }
    bool write(nvhooks::Addr a, const void* in, size_t n) override {
        DWORD old = 0;
        void* p = reinterpret_cast<void*>(a);
        if (!VirtualProtect(p, n, PAGE_EXECUTE_READWRITE, &old)) return false;
        std::memcpy(p, in, n);
        FlushInstructionCache(GetCurrentProcess(), p, n);
        DWORD tmp;
        VirtualProtect(p, n, old, &tmp);
        return true;
    }
    nvhooks::Addr allocExec(size_t n) override {
        return reinterpret_cast<nvhooks::Addr>(VirtualAlloc(nullptr, n, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE));
    }
    void freeExec(nvhooks::Addr a, size_t) override { VirtualFree(reinterpret_cast<void*>(a), 0, MEM_RELEASE); }
};

nvhooks::CaptureHub* g_hub = nullptr;
nvhooks::Role g_role = nvhooks::Role::Host;
Win32Memory g_mem;
std::unique_ptr<nvhooks::HookRegistry> g_reg;
SpawnLedger g_ledger;
WorldLog g_log;
WeatherTimeSync g_weather;
RefVarTracker g_vars;
std::function<void(const WorldEffect&)> g_sink;
std::vector<FormRef> g_watched;

constexpr nvhooks::Addr kThePlayerPtr = 0x11DEA3C;
constexpr uint32_t kPlayerRefID = 0x14;
constexpr uint32_t kTimeScaleGlobal = 0x3A;   // FalloutNV.esm TimeScale

inline void* thePlayer() { return *reinterpret_cast<void**>(kThePlayerPtr); }
inline bool capturing() { return g_hub && g_hub->active(); }
inline uint32_t refIdOf(const void* f) { return f ? static_cast<const TESForm*>(f)->refID : 0; }
inline bool isPlayer(const void* r) { return r && refIdOf(r) == kPlayerRefID; }

// L5's exported suppression state is not available yet (requested: nvhooks::blockMutationNow). Until then guest suppression of
// these commands is off and only host-side capture is live. Tracked in internal lane notes "open items".
bool blockedNow() { return false; }

bool formRef(const void* form, FormRef* out) {
    uint32_t id = refIdOf(form);
    if (!id) return false;
    if ((id >> 24) == 0xFFu) {  // created ref: the host's engine id IS the wire id
        *out = FormRef(createdRefPlugin(), id);
        return true;
    }
    return nvhooks::resolver().toRef(id, out);
}

template <class R, class... A>
inline R tc(nvhooks::Addr fn, void* self, A... a) {
    return reinterpret_cast<R(__thiscall*)(void*, A...)>(fn)(self, a...);
}
float getAV(void* actor, uint32_t av) {
    if (!actor) return 0;
    return tc<float, uint32_t>(nvhooks::addr::ActorGetActorValue.target, reinterpret_cast<char*>(actor) + 0xA4, av);
}

using Fn_Cmd = bool(__cdecl*)(void*, void*, void*, void*, void*, void*, double*, UInt32*);
using Fn_Extract = bool(__cdecl*)(void*, void*, UInt32*, void*, void*, void*, void*, ...);
Fn_Cmd o_MarkForDelete, o_SetEssential, o_SetActorRefEssential, o_SetRestrained, o_SetEnemy, o_SetAlly, o_RestoreAV;

#define CMD_ARGS void* pi, void* sd, void* thisObj, void* contObj, void* script, void* ev, double* res, UInt32* opc
#define CMD_FWD(o) o(pi, sd, thisObj, contObj, script, ev, res, opc)

template <class... Out>
bool parseArgs(CMD_ARGS, Out*... outs) {
    UInt32 off = *opc;  // ExtractArgs advances the offset: work on a copy so the real call sees the original
    return reinterpret_cast<Fn_Extract>(nvhooks::addr::ExtractArgs.target)(pi, sd, &off, thisObj, contObj, script, ev, outs...);
}

void emitLocal(const WorldEffect& e) {
    g_log.push(e);
    if (g_sink) g_sink(e);
}

bool __cdecl h_MarkForDelete(CMD_ARGS) {
    if (blockedNow()) return true;
    FormRef rr;
    uint32_t id = refIdOf(thisObj);
    bool track = capturing() && thisObj && !isPlayer(thisObj) && formRef(thisObj, &rr);
    bool ok = CMD_FWD(o_MarkForDelete);
    if (ok && track) {
        // synccore's DespawnRef names created refs only. An esm reference marked for delete is gone for good, which for the
        // other players is the same as disabled: send DisableRef (idempotent, safe on a replay).
        if ((id >> 24) == 0xFFu) g_hub->record(DespawnRef{id});
        else g_hub->record(DisableRef{rr});
    }
    return ok;
}

bool __cdecl h_SetEssential(CMD_ARGS) {
    if (blockedNow()) return true;
    FormRef ar;
    TESForm* base = nullptr;
    int v = 0;
    bool parsed = capturing() && parseArgs(pi, sd, thisObj, contObj, script, ev, res, opc, &base, &v);
    bool ok = CMD_FWD(o_SetEssential);
    if (ok && parsed && base && formRef(base, &ar)) emitLocal(SetEssential{ar, v > 0, false});
    return ok;
}

bool __cdecl h_SetActorRefEssential(CMD_ARGS) {
    if (blockedNow()) return true;
    FormRef ar;
    int v = 0;
    bool track = capturing() && thisObj && formRef(thisObj, &ar);
    bool parsed = track && parseArgs(pi, sd, thisObj, contObj, script, ev, res, opc, &v);
    bool ok = CMD_FWD(o_SetActorRefEssential);
    if (ok && parsed) emitLocal(SetEssential{ar, v > 0, true});
    return ok;
}

bool __cdecl h_SetRestrained(CMD_ARGS) {
    if (blockedNow()) return true;
    FormRef ar;
    int v = 0;
    bool track = capturing() && thisObj && formRef(thisObj, &ar);
    bool parsed = track && parseArgs(pi, sd, thisObj, contObj, script, ev, res, opc, &v);
    bool ok = CMD_FWD(o_SetRestrained);
    if (ok && parsed) emitLocal(SetRestrained{ar, v != 0});
    return ok;
}

bool relationCmd(Fn_Cmd o, Relation kind, CMD_ARGS) {
    if (blockedNow()) return true;
    FormRef fa, fb;
    TESForm *a = nullptr, *b = nullptr;
    int flags1 = 0, flags2 = 0;  // two optional ints follow the factions
    bool parsed = capturing() && parseArgs(pi, sd, thisObj, contObj, script, ev, res, opc, &a, &b, &flags1, &flags2);
    bool ok = CMD_FWD(o);
    if (ok && parsed && a && b && formRef(a, &fa) && formRef(b, &fb)) emitLocal(SetRelation{fa, fb, kind});
    return ok;
}
bool __cdecl h_SetEnemy(CMD_ARGS) { return relationCmd(o_SetEnemy, Relation::Enemy, pi, sd, thisObj, contObj, script, ev, res, opc); }
bool __cdecl h_SetAlly(CMD_ARGS) { return relationCmd(o_SetAlly, Relation::Ally, pi, sd, thisObj, contObj, script, ev, res, opc); }

bool __cdecl h_RestoreAV(CMD_ARGS) {
    if (blockedNow()) return true;
    FormRef ar;
    UInt32 av = 0xFFFFFFFF;
    float val = 0;
    bool track = capturing() && thisObj && !isPlayer(thisObj) && formRef(thisObj, &ar);
    bool parsed = track && parseArgs(pi, sd, thisObj, contObj, script, ev, res, opc, &av, &val);
    float before = parsed ? getAV(thisObj, av) : 0;
    bool ok = CMD_FWD(o_RestoreAV);
    if (ok && parsed) recordNonPlayerAv(ar, av, AvCmdKind::Restore, before, getAV(thisObj, av));
    return ok;
}

struct Entry {
    const nvhooks::HookSpec* spec;
    void* detour;
    void* origOut;
};
#define HK(spec, det, orig) {&world_addrs::spec, reinterpret_cast<void*>(&det), reinterpret_cast<void*>(&orig)}
const Entry kEntries[] = {
    HK(Cmd_MarkForDelete, h_MarkForDelete, o_MarkForDelete),
    HK(Cmd_SetEssential, h_SetEssential, o_SetEssential),
    HK(Cmd_SetActorRefEssential, h_SetActorRefEssential, o_SetActorRefEssential),
    HK(Cmd_SetRestrained, h_SetRestrained, o_SetRestrained),
    HK(Cmd_SetEnemy, h_SetEnemy, o_SetEnemy),
    HK(Cmd_SetAlly, h_SetAlly, o_SetAlly),
    HK(Cmd_RestoreActorValue, h_RestoreAV, o_RestoreAV),
};

// ------------------------------------------------------------------ polls
// Current weather through the engine's own accessor chain (the one Cmd_GetIsCurrentWeather's evaluator 0x59EBB0 uses):
// obj = 0x46DD00() ; weather = thiscall 0x44EDB0(obj). Both are re-verified against their recorded prologue bytes first.
bool callsVerified() {
    static int state = 0;  // 0 unknown, 1 ok, -1 bad
    if (state) return state > 0;
    state = 1;
    for (size_t i = 0; i < world_addrs::kCallCount; ++i) {
        const world_addrs::CallSpec& c = *world_addrs::kCalls[i];
        unsigned char now[16] = {};
        if (!peekMemory(c.addr, now, 16) || std::memcmp(now, c.bytes, 16) != 0) state = -1;
    }
    return state > 0;
}

void* skyWeather() {
    if (!callsVerified()) return nullptr;
    void* obj = reinterpret_cast<void*(__cdecl*)()>(world_addrs::Call_SkySingleton.addr)();
    if (!obj) return nullptr;
    void* w = reinterpret_cast<void*(__thiscall*)(void*)>(world_addrs::Call_SkyCurrentWeather.addr)(obj);
    if (!w || static_cast<const TESForm*>(w)->typeID != kFormType_TESWeather) return nullptr;
    return w;
}

float timeScale() {
    uint32_t id = nvhooks::resolver().fromRef(FormRef("falloutnv.esm", kTimeScaleGlobal));
    TESGlobal* g = static_cast<TESGlobal*>(id ? nvhooks::resolver().lookup(id) : nullptr);
    return g ? g->data : 0;
}

template <class F>
void forRefVars(TESObjectREFR* r, F f) {
    if (!r) return;
    BSExtraData* xd = r->extraDataList.GetByType(kExtraData_Script);
    ScriptEventList* el = xd ? static_cast<ExtraScript*>(xd)->eventList : nullptr;
    if (!el || !el->m_vars) return;
    for (auto v = el->m_vars->Begin(); !v.End(); ++v)
        if (v.Get() && v.Get()->id <= 0xFFFFu) f(static_cast<uint16_t>(v.Get()->id), v.Get()->data);
}

TESObjectCELL* playerCell() {
    TESObjectREFR* p = static_cast<TESObjectREFR*>(thePlayer());
    return p ? p->parentCell : nullptr;
}

size_t scanRefVars(std::vector<RefScriptVar>* out, size_t budget) {
    g_vars.beginPoll(budget);
    static TESObjectCELL* lastCell = nullptr;
    TESObjectCELL* cell = playerCell();
    if (cell != lastCell) {  // new cell: everything is re-baselined on first sight
        g_vars.clear();
        lastCell = cell;
    }
    size_t n = 0;
    auto visit = [&](TESObjectREFR* r) {
        if (!r || !r->refID) return;
        FormRef rr;
        bool haveRef = false, got = false;
        forRefVars(r, [&](uint16_t idx, double val) {
            if (!g_vars.observe(r->refID, idx, val)) return;
            if (!haveRef) {
                got = formRef(r, &rr);
                haveRef = true;
            }
            if (got) {
                out->push_back(RefScriptVar{rr, idx, val});
                ++n;
            }
        });
    };
    if (cell)
        for (auto it = cell->objectList.Begin(); !it.End(); ++it) visit(it.Get());
    for (const FormRef& w : g_watched) {
        uint32_t id = nvhooks::resolver().fromRef(w);
        TESObjectREFR* r = static_cast<TESObjectREFR*>(id ? nvhooks::resolver().lookup(id) : nullptr);
        if (r && r->parentCell != cell) visit(r);  // refs of the player's cell were already visited
    }
    return n;
}

}  // namespace

// ------------------------------------------------------------------ public API
nvhooks::FormResolver& makeResolver(bool hostSide) {
    static std::unique_ptr<WorldResolver> r;
    r.reset(new WorldResolver(nvhooks::defaultResolver(), g_ledger.map(), hostSide));
    return *r;
}
SpawnLedger& ledger() { return g_ledger; }

InitReport Init(nvhooks::CaptureHub& hub, nvhooks::Role role) {
    InitReport rep;
    g_hub = &hub;
    g_role = role;
    if (!g_reg) g_reg = std::make_unique<nvhooks::HookRegistry>(g_mem);
    if (role == nvhooks::Role::Client) return rep;  // gate-only role: no capture hooks
    for (const Entry& e : kEntries) {
        nvhooks::Addr tramp = 0;
        nvhooks::PatchStatus st = g_reg->acquire(*e.spec, reinterpret_cast<nvhooks::Addr>(e.detour), &tramp);
        if (nvhooks::installed(st)) {
            *reinterpret_cast<void**>(e.origOut) = reinterpret_cast<void*>(tramp);
            ++rep.ok;
        } else {
            ++rep.failed;
        }
        rep.hooks.push_back({e.spec->name, st});
    }
    // Script-driven SetWeather/SetTimeScale that already went out through a txn must not be re-sent by the next poll.
    hub.addSink([](EffectTxn&& t) {
        for (const Effect& e : t.effects) g_weather.noteSent(e);
    });
    return rep;
}

void Shutdown() {
    g_reg.reset();
    g_hub = nullptr;
}

std::vector<WorldEffect> drainWorldEffects() { return g_log.drain(); }
void setWorldEffectSink(std::function<void(const WorldEffect&)> sink) { g_sink = std::move(sink); }

void recordNonPlayerAv(const FormRef& actor, uint32_t av, AvCmdKind kind, float before, float after) {
    if (!capturing()) return;
    if (auto e = makeAvEffect(actor, av, kind, before, after)) emitLocal(*e);
}

void watchRef(const FormRef& r) {
    if (std::find(g_watched.begin(), g_watched.end(), r) == g_watched.end()) g_watched.push_back(r);
}
void clearWatched() { g_watched.clear(); }

size_t pollRefVarsNow(std::vector<RefScriptVar>* out) {
    std::vector<RefScriptVar> tmp;
    if (!out) out = &tmp;
    return scanRefVars(out, 1u << 20);
}

void hostTick() {
    static uint32_t frame = 0;
    if (!g_hub || !g_hub->enabled() || g_role != nvhooks::Role::Host) return;
    if (++frame % 15 != 0) return;
    if (g_hub->depth() != 0) return;  // never inside a script scope

    std::vector<RefScriptVar> vars;
    scanRefVars(&vars, 64);
    FormRef wr;
    void* w = skyWeather();
    bool haveW = w && formRef(w, &wr);
    std::vector<Effect> fx = g_weather.poll(wr, haveW, timeScale());
    for (auto& v : vars) fx.push_back(v);
    if (fx.empty()) return;
    Cause c;
    c.kind = CauseKind::Script;
    nvhooks::ScopedCapture sc(*g_hub, c);
    for (const Effect& e : fx) g_hub->record(e);
}

bool probeRef(uint32_t refID, RefProbe* out) {
    TESObjectREFR* r = static_cast<TESObjectREFR*>(refID ? nvhooks::resolver().lookup(refID) : nullptr);
    RefProbe p;
    if (r) {
        p.exists = true;
        p.refID = r->refID;
        p.baseID = refIdOf(r->baseForm);
        p.cellID = refIdOf(r->parentCell);
        p.flags = r->flags;
        p.x = r->posX;
        p.y = r->posY;
        p.z = r->posZ;
        BSExtraData* xd = r->extraDataList.GetByType(kExtraData_Script);
        ScriptEventList* el = xd ? static_cast<ExtraScript*>(xd)->eventList : nullptr;
        if (el && el->m_vars) {
            p.nvars = 0;
            for (auto v = el->m_vars->Begin(); !v.End(); ++v) ++p.nvars;
        }
    }
    *out = p;
    return p.exists;
}

std::vector<ScriptedRef> scriptedRefsInPlayerCell(size_t max) {
    std::vector<ScriptedRef> out;
    TESObjectCELL* cell = playerCell();
    if (!cell) return out;
    for (auto it = cell->objectList.Begin(); !it.End() && out.size() < max; ++it) {
        TESObjectREFR* r = it.Get();
        if (!r || !r->refID) continue;
        RefProbe p;
        probeRef(r->refID, &p);
        if (p.nvars <= 0) continue;
        ScriptedRef s;
        s.refID = r->refID;
        s.baseID = p.baseID;
        s.nvars = p.nvars;
        FormRef fr;
        if (formRef(r, &fr)) s.ref = nvhooks::refToStr(fr);
        out.push_back(s);
    }
    return out;
}

bool getRefVar(uint32_t refID, uint16_t index, double* out) {
    TESObjectREFR* r = static_cast<TESObjectREFR*>(refID ? nvhooks::resolver().lookup(refID) : nullptr);
    bool found = false;
    if (r)
        forRefVars(r, [&](uint16_t idx, double v) {
            if (idx == index) { *out = v; found = true; }
        });
    return found;
}

bool setRefVarDirect(uint32_t refID, uint16_t index, double value) {
    TESObjectREFR* r = static_cast<TESObjectREFR*>(refID ? nvhooks::resolver().lookup(refID) : nullptr);
    BSExtraData* xd = r ? r->extraDataList.GetByType(kExtraData_Script) : nullptr;
    ScriptEventList* el = xd ? static_cast<ExtraScript*>(xd)->eventList : nullptr;
    if (!el || !el->m_vars) return false;
    for (auto it = el->m_vars->Begin(); !it.End(); ++it)
        if (it.Get() && it.Get()->id == index) {
            it.Get()->data = value;
            return true;
        }
    return false;
}

bool currentWeather(FormRef* out) {
    void* w = skyWeather();
    return w && formRef(w, out);
}
float currentTimeScale() { return timeScale(); }
uint32_t playerCellID() { return refIdOf(playerCell()); }

bool queryCondition(const std::string& fn, uint32_t refID, uint32_t p1, double* out) {
    // evaluator pointers: 4th field of the command table entry (0x118E8E0, stride 0x28); signature cdecl(thisObj, p1, p2, double* result)
    struct Q { const char* name; uint32_t addr; };
    static const Q kQ[] = {{"GetRestrained", 0x59FF90}, {"IsEssential", 0x5A3980}, {"GetActorValue", 0x59C4F0}, {"GetFactionRelation", 0x5A46D0}};
    for (const Q& q : kQ) {
        if (fn != q.name) continue;
        unsigned char pro[3] = {};
        if (!peekMemory(q.addr, pro, 3) || pro[0] != 0x55 || pro[1] != 0x8B || pro[2] != 0xEC) return false;
        void* ref = refID ? nvhooks::resolver().lookup(refID) : nullptr;
        if (!ref) return false;
        double r = 0;
        bool ok = reinterpret_cast<bool(__cdecl*)(void*, void*, void*, double*)>(q.addr)(ref, reinterpret_cast<void*>(static_cast<uintptr_t>(p1)), nullptr, &r);
        *out = r;
        return ok;
    }
    return false;
}

std::vector<ActorRef> actorsInPlayerCell(size_t max) {
    std::vector<ActorRef> out;
    TESObjectCELL* cell = playerCell();
    if (!cell) return out;
    for (auto it = cell->objectList.Begin(); !it.End() && out.size() < max; ++it) {
        TESObjectREFR* r = it.Get();
        if (!r || !r->refID || r->refID == kPlayerRefID || r->typeID != kFormType_Character) continue;
        ActorRef a;
        a.refID = r->refID;
        a.baseID = refIdOf(r->baseForm);
        FormRef fr;
        if (formRef(r, &fr)) a.ref = nvhooks::refToStr(fr);
        out.push_back(a);
    }
    return out;
}

// ------------------------------------------------------------------ client apply
namespace {
bool cellIsReady(const FormRef& cellRef) {
    uint32_t id = nvhooks::resolver().fromRef(cellRef);
    TESObjectCELL* c = static_cast<TESObjectCELL*>(id ? nvhooks::resolver().lookup(id) : nullptr);
    if (!c) return false;
    return c == playerCell() || c->cellState == TESObjectCELL::kCellLoadState_Attached;
}
}  // namespace

nvhooks::ApplyStatus WorldApplier::run(const Plan& p) {
    using nvhooks::ApplyStatus;
    err_ = p.error;
    switch (p.status) {
        case PlanStatus::FormMissing: return ApplyStatus::FormMissing;
        case PlanStatus::Unsupported: return ApplyStatus::Unsupported;
        case PlanStatus::Queued: return ApplyStatus::Ok;  // accepted; created when the cell loads
        case PlanStatus::Ok: break;
    }
    for (const Step& s : p.steps) {
        switch (s.kind) {
            case StepKind::ConsoleOnRef:
            case StepKind::ConsoleGlobal: {
                if (!exec_) { err_ = "no console executor"; return ApplyStatus::Unsupported; }
                nvhooks::ApplierExecScope scope;
                if (!exec_(s.line, s.kind == StepKind::ConsoleOnRef ? s.refID : 0)) {
                    err_ = "console command failed: " + s.line;
                    return ApplyStatus::EngineRejected;
                }
                break;
            }
            case StepKind::SetRefVar: {
                TESObjectREFR* r = static_cast<TESObjectREFR*>(res_.lookup(s.refID));
                bool done = false;
                BSExtraData* xd = r ? r->extraDataList.GetByType(kExtraData_Script) : nullptr;
                ScriptEventList* el = xd ? static_cast<ExtraScript*>(xd)->eventList : nullptr;
                if (el && el->m_vars)
                    for (auto it = el->m_vars->Begin(); !it.End(); ++it)
                        if (it.Get() && it.Get()->id == s.varIndex) {
                            it.Get()->data = s.varValue;
                            done = true;
                            break;
                        }
                if (!done) { err_ = "ref has no such script variable (script not instantiated here?)"; return ApplyStatus::EngineRejected; }
                break;
            }
            case StepKind::CreateSpawn: {
                TESObjectCELL* cell = static_cast<TESObjectCELL*>(s.cellID ? res_.lookup(s.cellID) : nullptr);
                void* player = thePlayer();
                TESObjectREFR* anchor = nullptr;
                if (cell && cell == playerCell()) anchor = static_cast<TESObjectREFR*>(player);
                else if (cell)
                    for (auto it = cell->objectList.Begin(); !it.End() && !anchor; ++it)
                        if (it.Get() && it.Get()->refID) anchor = it.Get();
                if (!anchor) anchor = static_cast<TESObjectREFR*>(player);
                if (!anchor) { err_ = "no anchor reference to place from"; return ApplyStatus::EngineRejected; }
                if (!callsVerified()) { err_ = "engine PlaceAtMe worker prologue differs; refusing to call it"; return ApplyStatus::EngineRejected; }
                TESForm* base = static_cast<TESForm*>(res_.lookup(s.baseID));
                if (!base) { err_ = "base form missing"; return ApplyStatus::FormMissing; }
                // Cmd_PlaceAtMe's own worker: cdecl(ref, form, count, distance, direction, health) -> first placed ref
                TESObjectREFR* made = reinterpret_cast<TESObjectREFR*(__cdecl*)(TESObjectREFR*, TESForm*, UInt32, UInt32, UInt32, float)>(
                    world_addrs::Call_PlaceAtMe.addr)(anchor, base, 1, 0, 0, 1.0f);
                if (!made) { err_ = "PlaceAtMe produced no reference"; return ApplyStatus::EngineRejected; }
                const char axes[3] = {'X', 'Y', 'Z'};
                const float v[3] = {s.spawn.x, s.spawn.y, s.spawn.z};
                for (int i = 0; i < 3 && exec_; ++i) {
                    nvhooks::ApplierExecScope scope;
                    exec_(setPosLine(axes[i], v[i]), made->refID);
                }
                g_ledger.onCreated(s.spawn.ref(), made->refID);
                break;
            }
        }
    }
    return ApplyStatus::Ok;
}

nvhooks::ApplyStatus WorldApplier::apply(const Effect& e) {
    fnvcoop::EchoGuard echo(&hub_.echo());
    nvhooks::ApplyScope applyScope;
    nvhooks::ScriptGate gate;
    PlanEnv env{res_, g_ledger, cellIsReady};
    return run(planEffect(e, env));
}

nvhooks::ApplyStatus WorldApplier::applyWorld(const WorldEffect& e) {
    fnvcoop::EchoGuard echo(&hub_.echo());
    nvhooks::ApplyScope applyScope;
    PlanEnv env{res_, g_ledger, cellIsReady};
    return run(planWorldEffect(e, env));
}

size_t WorldApplier::flushQueued() {
    fnvcoop::EchoGuard echo(&hub_.echo());
    nvhooks::ApplyScope applyScope;
    nvhooks::ScriptGate gate;
    PlanEnv env{res_, g_ledger, cellIsReady};
    size_t n = 0;
    for (const Plan& p : planQueuedSpawns(env))
        if (run(p) == nvhooks::ApplyStatus::Ok) ++n;
    return n;
}

}  // namespace nvworld
