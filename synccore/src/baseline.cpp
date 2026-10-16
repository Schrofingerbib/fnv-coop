#include "fnvcoop/baseline.h"
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <map>
#include <sstream>
#include <unordered_map>
#include <unordered_set>
#include "fnvcoop/mutations.h"
#include "fnvcoop/wire.h"

namespace fnvcoop {

namespace {
// Allocation-free FNV-1a hashing of entry identity (key) and identity+value (val). The same helpers serve entries built from
// effects and the digest fast path that walks the maps directly, so both always agree.
struct Fnv {
    uint64_t h = 1469598103934665603ull;
    void b(uint8_t c) { h ^= c; h *= 1099511628211ull; }
    void u32(uint32_t v) { b(uint8_t(v)); b(uint8_t(v >> 8)); b(uint8_t(v >> 16)); b(uint8_t(v >> 24)); }
    void u64(uint64_t v) { u32(uint32_t(v)); u32(uint32_t(v >> 32)); }
    void str(const std::string& t) { for (char c : t) b(uint8_t(c)); b(0xFF); }
    void form(const FormRef& f) { str(f.plugin); u32(f.localId); }
};
struct KV { uint64_t key; uint32_t val; };
inline uint32_t fold(uint64_t h) { return static_cast<uint32_t>(h ^ (h >> 32)); }
inline KV kv(const Fnv& k, const Fnv& v) { return {k.h, fold(v.h)}; }

double quantize(double v) {
    double q = std::round(v * 1e4) / 1e4;
    if (!std::isfinite(q)) return v;
    return q == 0 ? 0.0 : q;   // folds -0.0 into 0.0
}
// Vars/globals are rounded to 1e-4 for hashing ONLY (engine float noise); the effect itself keeps the exact value.
inline uint64_t qbits(double v) {
    // Canonical representation first: the engine stores most script vars as 32-bit floats, so a value that went through a text
    // round trip (5.96) and the same value read as a float (5.9600000381...) must land in the same bucket.
    if (std::fabs(v) < 1e7) v = static_cast<double>(static_cast<float>(v));
    double q = quantize(v);
    if (std::fabs(q) < 1e14) return static_cast<uint64_t>(static_cast<int64_t>(std::llround(q * 1e4)));
    uint64_t b; std::memcpy(&b, &q, 8); return b;
}
inline uint32_t fbits(float f) { uint32_t b; std::memcpy(&b, &f, 4); return b; }

KV kvStage(const FormRef& q, uint32_t s) { Fnv k; k.b(1); k.form(q); Fnv v = k; v.u32(s); return kv(k, v); }
KV kvObjective(const FormRef& q, uint32_t o, uint8_t f) { Fnv k; k.b(2); k.form(q); k.u32(o); Fnv v = k; v.b(f); return kv(k, v); }
KV kvVar(const FormRef& ow, uint32_t i, double x) { Fnv k; k.b(3); k.form(ow); k.u32(i); Fnv v = k; v.u64(qbits(x)); return kv(k, v); }
KV kvGlobal(const FormRef& g, double x) { Fnv k; k.b(4); k.form(g); Fnv v = k; v.u64(qbits(x)); return kv(k, v); }
KV kvEnabled(const FormRef& r, bool on) { Fnv k; k.b(5); k.form(r); Fnv v = k; v.b(on); return kv(k, v); }
KV kvRunning(const FormRef& q, bool on) { Fnv k; k.b(6); k.form(q); Fnv v = k; v.b(on); return kv(k, v); }
KV kvCompleted(const FormRef& q) { Fnv k; k.b(7); k.form(q); return kv(k, k); }
KV kvAllObj(const FormRef& q) { Fnv k; k.b(8); k.form(q); return kv(k, k); }
KV kvKill(const FormRef& a) { Fnv k; k.b(9); k.form(a); return kv(k, k); }
KV kvPlaced(const FormRef& r, const FormRef& cell, float x, float y, float z) {
    Fnv k; k.b(10); k.form(r); Fnv v = k; v.form(cell); v.u32(fbits(x)); v.u32(fbits(y)); v.u32(fbits(z)); return kv(k, v);
}
KV kvLock(const FormRef& r, bool locked, int32_t level) { Fnv k; k.b(11); k.form(r); Fnv v = k; v.b(locked); v.u32(static_cast<uint32_t>(level)); return kv(k, v); }
KV kvTexture(const FormRef& r, uint8_t slot, const FormRef& tex) { Fnv k; k.b(12); k.form(r); k.b(slot); Fnv v = k; v.form(tex); return kv(k, v); }
KV kvCompanion(const FormRef& n, uint8_t owner, uint8_t kind) { Fnv k; k.b(13); k.form(n); Fnv v = k; v.b(owner); v.b(kind); return kv(k, v); }
KV kvTether(const FormRef& c, bool on) { Fnv k; k.b(14); k.form(c); Fnv v = k; v.b(on); return kv(k, v); }
KV kvFaction(const FormRef& a, const FormRef& f, int32_t rank) { Fnv k; k.b(15); k.form(a); k.form(f); Fnv v = k; v.u32(static_cast<uint32_t>(rank)); return kv(k, v); }
KV kvSayOnce(const FormRef& i) { Fnv k; k.b(16); k.form(i); return kv(k, k); }
KV kvSlideshow() { Fnv k; k.b(17); return kv(k, k); }
KV kvSave(bool on) { Fnv k; k.b(18); Fnv v = k; v.b(on); return kv(k, v); }
KV kvFailed(const FormRef& q) { Fnv k; k.b(20); k.form(q); return kv(k, k); }
KV kvObj(const FormRef& r, bool open, bool destroyed, bool activated) {
    Fnv k; k.b(21); k.form(r); Fnv v = k; v.b(open); v.b(destroyed); v.b(activated); return kv(k, v);
}
KV kvRefVar(const FormRef& r, uint32_t i, double x) { Fnv k; k.b(22); k.form(r); k.u32(i); Fnv v = k; v.u64(qbits(x)); return kv(k, v); }
KV kvTopic(const FormRef& t) { Fnv k; k.b(23); k.form(t); return kv(k, k); }
KV kvNote(const FormRef& t) { Fnv k; k.b(24); k.form(t); return kv(k, k); }
KV kvMarker(const FormRef& m, uint8_t f) { Fnv k; k.b(25); k.form(m); Fnv v = k; v.b(f); return kv(k, v); }
KV kvWeather(const FormRef& w, bool force) { Fnv k; k.b(26); Fnv v = k; v.form(w); v.b(force); return kv(k, v); }
KV kvTimeScale(float s) { Fnv k; k.b(27); Fnv v = k; v.u32(fbits(s)); return kv(k, v); }
KV kvTravel(const FormRef& s) { Fnv k; k.b(19); k.form(s); Fnv v = k; v.b(1); return kv(k, v); }

// Key/value hashes of an effect that is a baseline-style state entry.
bool kvOf(const Effect& e, KV& out) {
    bool ok = true;
    std::visit([&](const auto& x) {
        using E = std::decay_t<decltype(x)>;
        if constexpr (std::is_same_v<E, SetStage>) out = kvStage(x.quest, x.stage);
        else if constexpr (std::is_same_v<E, SetObjective>) out = kvObjective(x.quest, x.objective, x.flags);
        else if constexpr (std::is_same_v<E, SetScriptVar>) out = kvVar(x.owner, x.index, x.value);
        else if constexpr (std::is_same_v<E, SetGlobal>) out = kvGlobal(x.global, double(x.value));
        else if constexpr (std::is_same_v<E, EnableRef>) out = kvEnabled(x.ref, true);
        else if constexpr (std::is_same_v<E, DisableRef>) out = kvEnabled(x.ref, false);
        else if constexpr (std::is_same_v<E, StartQuest>) out = kvRunning(x.quest, true);
        else if constexpr (std::is_same_v<E, StopQuest>) out = kvRunning(x.quest, false);
        else if constexpr (std::is_same_v<E, CompleteQuest>) out = kvCompleted(x.quest);
        else if constexpr (std::is_same_v<E, FailQuest>) out = kvFailed(x.quest);
        else if constexpr (std::is_same_v<E, CompleteAllObjectives>) out = kvAllObj(x.quest);
        else if constexpr (std::is_same_v<E, KillActor>) out = kvKill(x.actor);
        else if constexpr (std::is_same_v<E, MoveRef>) out = kvPlaced(x.ref, x.cell, x.x, x.y, x.z);
        else if constexpr (std::is_same_v<E, SetLock>) out = kvLock(x.ref, x.locked, x.level);
        else if constexpr (std::is_same_v<E, SwapTexture>) out = kvTexture(x.ref, x.slot, x.texture);
        else if constexpr (std::is_same_v<E, CompanionOwner>) { if (x.active) out = kvCompanion(x.npc, x.owner, x.kind); else ok = false; }
        else if constexpr (std::is_same_v<E, ObjectState>) out = kvObj(x.ref, x.open, x.destroyed, x.activatedOnce);
        else if constexpr (std::is_same_v<E, RefScriptVar>) out = kvRefVar(x.ref, x.index, x.value);
        else if constexpr (std::is_same_v<E, AddTopic>) out = kvTopic(x.topic);
        else if constexpr (std::is_same_v<E, AddNote>) out = kvNote(x.note);
        else if constexpr (std::is_same_v<E, SetMapMarker>) out = kvMarker(x.marker, x.flags);
        else if constexpr (std::is_same_v<E, SetWeather>) out = kvWeather(x.weather, x.force);
        else if constexpr (std::is_same_v<E, SetTimeScale>) out = kvTimeScale(x.scale);
        else if constexpr (std::is_same_v<E, PartyTether>) out = kvTether(x.cell, x.on);
        else if constexpr (std::is_same_v<E, SetFactionRank>) out = kvFaction(x.actor, x.faction, x.rank);
        else if constexpr (std::is_same_v<E, SayOnceFlag>) out = kvSayOnce(x.info);
        else if constexpr (std::is_same_v<E, SlideshowEnd>) out = kvSlideshow();
        else if constexpr (std::is_same_v<E, SaveSuppress>) out = kvSave(x.on);
        else if constexpr (std::is_same_v<E, SetFastTravelLock>) out = kvTravel(x.scope);
        else ok = false;
    }, e);
    return ok;
}

void add(std::vector<BaselineEntry>& out, uint8_t cls, Effect e) {
    KV h;
    if (!kvOf(e, h)) return;
    if (volatileKeys().isVolatile(e)) return;
    BaselineEntry b;
    b.cls = cls;
    b.key = h.key;
    b.value = h.val;
    b.effect = std::move(e);
    out.push_back(std::move(b));
}
}  // namespace

bool isBaselineCovered(const Effect& e) {
    return std::holds_alternative<SetStage>(e) || std::holds_alternative<SetObjective>(e) || std::holds_alternative<SetScriptVar>(e) ||
           std::holds_alternative<SetGlobal>(e) || std::holds_alternative<EnableRef>(e) || std::holds_alternative<DisableRef>(e) ||
           std::holds_alternative<StartQuest>(e) || std::holds_alternative<StopQuest>(e) || std::holds_alternative<CompleteQuest>(e) ||
           std::holds_alternative<CompleteAllObjectives>(e) || std::holds_alternative<KillActor>(e) || std::holds_alternative<ResurrectActor>(e) ||
           std::holds_alternative<MoveRef>(e) || std::holds_alternative<SetLock>(e) || std::holds_alternative<SwapTexture>(e) ||
           std::holds_alternative<CompanionOwner>(e) || std::holds_alternative<PartyTether>(e) || std::holds_alternative<SetFactionRank>(e) ||
           std::holds_alternative<SayOnceFlag>(e) || std::holds_alternative<SlideshowEnd>(e) || std::holds_alternative<SaveSuppress>(e) ||
           std::holds_alternative<SetFastTravelLock>(e) || std::holds_alternative<FailQuest>(e) ||
           std::holds_alternative<ObjectState>(e) || std::holds_alternative<RefScriptVar>(e) || std::holds_alternative<AddTopic>(e) ||
           std::holds_alternative<AddNote>(e) || std::holds_alternative<SetMapMarker>(e) || std::holds_alternative<SetWeather>(e) ||
           std::holds_alternative<SetTimeScale>(e);
}

namespace {
uint8_t classOf(const Effect& e) {
    if (std::holds_alternative<StartQuest>(e) || std::holds_alternative<StopQuest>(e) || std::holds_alternative<CompleteQuest>(e) || std::holds_alternative<FailQuest>(e)) return 0;
    if (std::holds_alternative<SetStage>(e)) return 1;
    if (std::holds_alternative<SetObjective>(e) || std::holds_alternative<CompleteAllObjectives>(e)) return 2;
    if (std::holds_alternative<SetScriptVar>(e) || std::holds_alternative<RefScriptVar>(e)) return 3;
    if (std::holds_alternative<SetGlobal>(e)) return 4;
    if (std::holds_alternative<SetFactionRank>(e)) return 6;
    if (std::holds_alternative<SayOnceFlag>(e) || std::holds_alternative<SlideshowEnd>(e) || std::holds_alternative<SaveSuppress>(e) ||
        std::holds_alternative<SetFastTravelLock>(e)) return 7;
    return 5;
}
uint64_t mix(uint64_t x) {
    x += 0x9E3779B97F4A7C15ull;
    x = (x ^ (x >> 30)) * 0xBF58476D1CE4E5B9ull;
    x = (x ^ (x >> 27)) * 0x94D049BB133111EBull;
    return x ^ (x >> 31);
}
}  // namespace

bool entryOf(const Effect& e, BaselineEntry& out) {
    if (!isBaselineCovered(e)) return false;
    std::vector<BaselineEntry> v;
    add(v, classOf(e), e);
    if (v.empty()) return false;   // volatile
    out = std::move(v[0]);
    return true;
}

size_t shardOf(const BaselineEntry& e) { return static_cast<size_t>(e.cls) * 16 + static_cast<size_t>(e.key % 16); }

std::vector<uint32_t> shardHashes(const std::vector<BaselineEntry>& entries) {
    std::vector<uint64_t> acc(kSyncShards, 0);
    for (auto& e : entries) acc[shardOf(e)] += mix(e.key + (static_cast<uint64_t>(e.value) << 1) * 0x9E3779B97F4A7C15ull);
    std::vector<uint32_t> out(kSyncShards);
    for (size_t i = 0; i < kSyncShards; ++i) out[i] = static_cast<uint32_t>(acc[i] ^ (acc[i] >> 32));
    return out;
}

std::vector<uint32_t> shardHashesOf(const ShadowState& w) {
    if (mutations().slowHashing) return shardHashes(baselineEntries(w));   // TEST ONLY: the perf budget must catch this
    std::vector<uint64_t> acc(kSyncShards, 0);
    const bool vol = volatileKeys().size() != 0;
    auto put = [&](size_t cls, const KV& h) { acc[cls * 16 + static_cast<size_t>(h.key % 16)] += mix(h.key + (static_cast<uint64_t>(h.val) << 1) * 0x9E3779B97F4A7C15ull); };
    auto volEffect = [&](const Effect& e) { return vol && volatileKeys().isVolatile(e); };
    for (auto& [q, on] : w.questRunning) if (!volEffect(StartQuest{q})) put(0, kvRunning(q, on));
    for (auto& q : w.questCompleted) if (!w.questFailed.count(q)) put(0, kvCompleted(q));
    for (auto& q : w.questFailed) put(0, kvFailed(q));
    for (auto& [q, st] : w.stages) if (!volEffect(SetStage{q, 0})) put(1, kvStage(q, st));
    for (auto& [k, f] : w.objectives) if (!volEffect(SetObjective{k.first, 0, 0})) put(2, kvObjective(k.first, k.second, f));
    for (auto& q : w.allObjectivesDone) put(2, kvAllObj(q));
    for (auto& [k, val] : w.scriptVars) if (!(vol && volatileKeys().isVolatileVar(k.first, k.second))) put(3, kvVar(k.first, k.second, val));
    for (auto& [g, val] : w.globals) if (!(vol && volatileKeys().isVolatileGlobal(g))) put(4, kvGlobal(g, double(val)));
    for (auto& [r, on] : w.enabled) put(5, kvEnabled(r, on));
    for (auto& a : w.kills) put(5, kvKill(a));
    for (auto& [r, p] : w.placed) put(5, kvPlaced(r, p.cell, p.x, p.y, p.z));
    for (auto& [r, l] : w.locks) put(5, kvLock(r, l.first, l.second));
    for (auto& [k, t] : w.textures) put(5, kvTexture(k.first, k.second, t));
    for (auto& [n, o] : w.companionOwner) put(5, kvCompanion(n, o, w.companionKind.count(n) ? w.companionKind.at(n) : uint8_t(0)));
    for (auto& [r, o] : w.objects) put(5, kvObj(r, o.open, o.destroyed, o.activated));
    for (auto& [k, val] : w.refVars) put(3, kvRefVar(k.first, k.second, val));
    for (auto& t : w.topics) put(7, kvTopic(t));
    for (auto& t : w.notes) put(7, kvNote(t));
    for (auto& [m, f] : w.mapMarkers) put(7, kvMarker(m, f));
    if (w.weatherSet) put(7, kvWeather(w.weather, w.weatherForced));
    if (w.timeScale != 0) put(7, kvTimeScale(w.timeScale));
    for (auto& [c, on] : w.tethers) put(5, kvTether(c, on));
    for (auto& [k, r] : w.factionRank) put(6, kvFaction(k.first, k.second, r));
    for (auto& [s, on] : w.fastTravelLock) if (on) put(7, kvTravel(s));
    for (auto& i : w.sayOnce) put(7, kvSayOnce(i));
    if (w.slideshowEnded) put(7, kvSlideshow());
    put(7, kvSave(w.saveSuppress));
    std::vector<uint32_t> out(kSyncShards);
    for (size_t i = 0; i < kSyncShards; ++i) out[i] = static_cast<uint32_t>(acc[i] ^ (acc[i] >> 32));
    return out;
}

std::string explainEntry(const ShadowState& w, const Effect& probe) {
    std::ostringstream o;
    BaselineEntry pe;
    bool covered = entryOf(probe, pe);
    bool vol = volatileKeys().isVolatile(probe);
    if (!covered) {
        o << "probe " << describeEntry(probe) << ": " << (vol ? "VOLATILE (excluded) " : "") << "not a baseline entry";
        return o.str();
    }
    for (auto& e : baselineEntries(w)) {
        if (e.key != pe.key) continue;
        char b[96];
        std::snprintf(b, sizeof b, " key=%016llx value=%08x class=%d shard=%zu", (unsigned long long)e.key, e.value, int(e.cls), shardOf(e));
        o << "present: " << describeEntry(e.effect) << b;
        return o.str();
    }
    char b[96];
    std::snprintf(b, sizeof b, " key=%016llx class=%d shard=%zu", (unsigned long long)pe.key, int(pe.cls), shardOf(pe));
    o << "absent from the world" << b << (vol ? " (VOLATILE: excluded everywhere)" : "") << " (probe " << describeEntry(probe) << ")";
    return o.str();
}

std::string describeEntry(const Effect& e) {
    auto f = [](const FormRef& r) { char b[16]; std::snprintf(b, sizeof b, "%X", r.localId); return std::string(b) + "@" + r.plugin; };
    std::ostringstream o;
    o << effectName(e) << ' ';
    std::visit([&](const auto& x) {
        using T = std::decay_t<decltype(x)>;
        if constexpr (std::is_same_v<T, SetStage>) o << f(x.quest) << " = " << x.stage;
        else if constexpr (std::is_same_v<T, SetObjective>) o << f(x.quest) << '#' << x.objective << " flags " << int(x.flags);
        else if constexpr (std::is_same_v<T, SetScriptVar>) o << f(x.owner) << " var " << x.index << " = " << x.value;
        else if constexpr (std::is_same_v<T, SetGlobal>) o << f(x.global) << " = " << x.value;
        else if constexpr (std::is_same_v<T, EnableRef>) o << f(x.ref);
        else if constexpr (std::is_same_v<T, DisableRef>) o << f(x.ref);
        else if constexpr (std::is_same_v<T, StartQuest>) o << f(x.quest);
        else if constexpr (std::is_same_v<T, StopQuest>) o << f(x.quest);
        else if constexpr (std::is_same_v<T, CompleteQuest>) o << f(x.quest);
        else if constexpr (std::is_same_v<T, FailQuest>) o << f(x.quest);
        else if constexpr (std::is_same_v<T, CompleteAllObjectives>) o << f(x.quest);
        else if constexpr (std::is_same_v<T, KillActor>) o << f(x.actor);
        else if constexpr (std::is_same_v<T, MoveRef>) o << f(x.ref) << " -> " << f(x.cell) << " (" << x.x << ',' << x.y << ',' << x.z << ')';
        else if constexpr (std::is_same_v<T, SetLock>) o << f(x.ref) << (x.locked ? " locked " : " unlocked ") << x.level;
        else if constexpr (std::is_same_v<T, SwapTexture>) o << f(x.ref) << '#' << int(x.slot) << " = " << f(x.texture);
        else if constexpr (std::is_same_v<T, CompanionOwner>) o << f(x.npc) << " owner " << int(x.owner) << " kind " << int(x.kind);
        else if constexpr (std::is_same_v<T, ObjectState>) o << f(x.ref) << " open " << x.open << " destroyed " << x.destroyed << " activated " << x.activatedOnce;
        else if constexpr (std::is_same_v<T, RefScriptVar>) o << f(x.ref) << " var " << x.index << " = " << x.value;
        else if constexpr (std::is_same_v<T, AddTopic>) o << f(x.topic);
        else if constexpr (std::is_same_v<T, AddNote>) o << f(x.note);
        else if constexpr (std::is_same_v<T, SetMapMarker>) o << f(x.marker) << " flags " << int(x.flags);
        else if constexpr (std::is_same_v<T, SetWeather>) o << f(x.weather) << (x.force ? " forced" : "");
        else if constexpr (std::is_same_v<T, SetTimeScale>) o << x.scale;
        else if constexpr (std::is_same_v<T, PartyTether>) o << f(x.cell) << (x.on ? " on" : " off");
        else if constexpr (std::is_same_v<T, SetFactionRank>) o << f(x.actor) << " in " << f(x.faction) << " rank " << x.rank;
        else if constexpr (std::is_same_v<T, SayOnceFlag>) o << f(x.info);
        else if constexpr (std::is_same_v<T, SaveSuppress>) o << (x.on ? "on" : "off");
        else if constexpr (std::is_same_v<T, SetFastTravelLock>) o << f(x.scope) << (x.on ? " on" : " off");
    }, e);
    return o.str();
}

std::vector<BaselineEntry> baselineEntries(const ShadowState& w) {
    std::vector<BaselineEntry> v;
    for (auto& [q, on] : w.questRunning) add(v, 0, on ? Effect(StartQuest{q}) : Effect(StopQuest{q}));
    for (auto& q : w.questCompleted) if (!w.questFailed.count(q)) add(v, 0, CompleteQuest{q});
    for (auto& q : w.questFailed) add(v, 0, FailQuest{q});
    for (auto& [q, s] : w.stages) add(v, 1, SetStage{q, s});
    for (auto& [k, f] : w.objectives) add(v, 2, SetObjective{k.first, k.second, f});
    for (auto& q : w.allObjectivesDone) add(v, 2, CompleteAllObjectives{q});
    for (auto& [k, val] : w.scriptVars) add(v, 3, SetScriptVar{k.first, k.second, val});
    for (auto& [g, val] : w.globals) add(v, 4, SetGlobal{g, val});
    for (auto& [r, on] : w.enabled) add(v, 5, on ? Effect(EnableRef{r}) : Effect(DisableRef{r}));
    for (auto& a : w.kills) add(v, 5, KillActor{a});
    for (auto& [r, p] : w.placed) add(v, 5, MoveRef{r, p.cell, p.x, p.y, p.z, false});
    for (auto& [r, l] : w.locks) add(v, 5, SetLock{r, l.first, l.second});
    for (auto& [k, t] : w.textures) add(v, 5, SwapTexture{k.first, t, k.second});
    for (auto& [n, o] : w.companionOwner) add(v, 5, CompanionOwner{n, o, true, w.companionKind.count(n) ? w.companionKind.at(n) : uint8_t(0)});
    for (auto& [r, o] : w.objects) {
        ObjectState e; e.ref = r; e.mask = kObjOpen | kObjDestroyed | kObjActivated; e.open = o.open; e.destroyed = o.destroyed; e.activatedOnce = o.activated;
        add(v, 5, e);
    }
    for (auto& [k, val] : w.refVars) add(v, 3, RefScriptVar{k.first, k.second, val});
    for (auto& t : w.topics) add(v, 7, AddTopic{t});
    for (auto& t : w.notes) add(v, 7, AddNote{t});
    for (auto& [m, f] : w.mapMarkers) add(v, 7, SetMapMarker{m, f});
    if (w.weatherSet) add(v, 7, SetWeather{w.weather, w.weatherForced});
    if (w.timeScale != 0) add(v, 7, SetTimeScale{w.timeScale});
    for (auto& [c, on] : w.tethers) add(v, 5, PartyTether{c, on});
    for (auto& [k, r] : w.factionRank) add(v, 6, SetFactionRank{k.first, k.second, r});
    for (auto& [s, on] : w.fastTravelLock) if (on) add(v, 7, SetFastTravelLock{s, true});
    for (auto& i : w.sayOnce) add(v, 7, SayOnceFlag{i});
    if (w.slideshowEnded) add(v, 7, SlideshowEnd{});
    add(v, 7, SaveSuppress{w.saveSuppress});
    std::stable_sort(v.begin(), v.end(), [](const BaselineEntry& a, const BaselineEntry& b) { return a.cls < b.cls; });
    return v;
}

BaselineSummary summarizeWorld(const ShadowState& w, uint64_t contentHash) {
    BaselineSummary s;
    s.contentHash = contentHash;
    for (auto& e : baselineEntries(w)) s.entries.emplace_back(e.key, e.value);
    s.karma = w.ledger[0].karma;
    for (auto& [f, r] : w.ledger[0].rep) s.rep.push_back({f, r.first, r.second});
    return s;
}

BaselineDiff diffBaseline(const ShadowState& host, const BaselineSummary& client, const SharePolicy& policy,
                          PlayerId joiner, bool syncKarma, int64_t hostKarma, bool syncRep,
                          const std::vector<RepAbs>& hostRep) {
    BaselineDiff d;
    std::unordered_map<uint64_t, uint32_t> cl;
    cl.reserve(client.entries.size() * 2);
    for (auto& e : client.entries) cl.emplace(e.first, e.second);
    std::unordered_set<uint64_t> hostKeys;
    hostKeys.reserve(client.entries.size() * 2 + 64);
    EffectTxn t;
    t.origin = joiner;
    for (auto& e : baselineEntries(host)) {
        hostKeys.insert(e.key);
        auto it = cl.find(e.key);
        if (it != cl.end() && it->second == e.value) { ++d.keptByClient; continue; }
        t.effects.push_back(e.effect);
    }
    for (auto& [k, v] : cl) if (!hostKeys.count(k)) d.revertKeys.push_back(k);
    std::sort(d.revertKeys.begin(), d.revertKeys.end());   // deterministic order
    auto clampTo32 = [](int64_t v) { return static_cast<int32_t>(std::clamp<int64_t>(v, INT32_MIN + 1, INT32_MAX)); };
    auto deltaChunks = [&](int64_t delta, auto&& emit) {
        while (delta != 0) { int32_t c = clampTo32(delta); emit(c); delta -= c; }
    };
    if (syncKarma)
        deltaChunks(hostKarma - client.karma, [&](int32_t c) { t.effects.push_back(ModKarma{c, Recipient::slot(joiner)}); });
    if (syncRep) {
        std::map<FormRef, std::pair<int64_t, int64_t>> have, want;
        for (auto& r : client.rep) have[r.faction] = {r.fame, r.infamy};
        for (auto& r : hostRep) want[r.faction] = {r.fame, r.infamy};
        std::vector<FormRef> all;
        for (auto& [f, v] : have) all.push_back(f);
        for (auto& [f, v] : want) all.push_back(f);
        std::sort(all.begin(), all.end());
        all.erase(std::unique(all.begin(), all.end()), all.end());
        for (auto& f : all) {
            auto h = have.count(f) ? have[f] : std::pair<int64_t, int64_t>{0, 0};
            auto w = want.count(f) ? want[f] : std::pair<int64_t, int64_t>{0, 0};
            int64_t df = w.first - h.first, di = w.second - h.second;
            while (df != 0 || di != 0) {
                int32_t cf = clampTo32(df), ci = clampTo32(di);
                t.effects.push_back(ModReputation{f, cf, ci, Recipient::slot(joiner)});
                df -= cf; di -= ci;
            }
        }
    }
    normalizeTxn(t, policy);  // drops categories the policy does not share
    d.effects = std::move(t.effects);
    return d;
}

bool revertEffectFor(const ShadowState& clientWorld, uint64_t key, Effect& out) {
    for (auto& e : baselineEntries(clientWorld)) {
        if (e.key != key) continue;
        if (auto* k = std::get_if<KillActor>(&e.effect)) { out = ResurrectActor{k->actor}; return true; }
        if (auto* l = std::get_if<SetFastTravelLock>(&e.effect)) { out = SetFastTravelLock{l->scope, false}; return true; }
        if (auto* c = std::get_if<CompanionOwner>(&e.effect)) { out = CompanionOwner{c->npc, c->owner, false, c->kind}; return true; }
        if (auto* n = std::get_if<AddNote>(&e.effect)) { out = RemoveNote{n->note}; return true; }
        if (auto* o = std::get_if<ObjectState>(&e.effect)) {   // sticky bits (destroyed / activated once) cannot be undone
            if (o->destroyed || o->activatedOnce) return false;
            ObjectState r; r.ref = o->ref; r.mask = kObjOpen; r.open = false; out = r; return true;
        }
        return false;
    }
    return false;
}

uint64_t contentHashOf(const std::vector<std::string>& names) {
    uint64_t h = 1469598103934665603ull;
    for (auto& n : names) {
        for (char c : n) { h ^= static_cast<uint8_t>(c); h *= 1099511628211ull; }
        h ^= 0xFF; h *= 1099511628211ull;
    }
    return h;
}

}  // namespace fnvcoop
