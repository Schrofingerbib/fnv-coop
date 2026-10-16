#include "world_core.h"

#include <cmath>
#include <cstdio>
#include <utility>

#include "common/minijson.h"
#include "effect_json.h"

namespace nvworld {

using fnvcoop::FormRef;
namespace json = nvcoop::json;

const char* worldEffectName(const WorldEffect& e) {
    switch (e.index()) {
        case 0: return "SetActorValueRef";
        case 1: return "SetEssential";
        case 2: return "SetRestrained";
        case 3: return "SetRelation";
        case 4: return "SetScriptPackage";
    }
    return "?";
}

// ------------------------------------------------------------------------------------------------ CreatedRefMap
void CreatedRefMap::bind(const FormRef& host, uint32_t localRefID) {
    auto it = toLocal_.find(host);
    if (it != toLocal_.end()) {
#if NVWORLD_MUT == 1
        return;  // MUTANT: first binding wins
#endif
        toHost_.erase(it->second);
    }
    toLocal_[host] = localRefID;
    toHost_[localRefID] = host;
}

uint32_t CreatedRefMap::local(const FormRef& host) const {
    auto it = toLocal_.find(host);
    return it == toLocal_.end() ? 0 : it->second;
}

bool CreatedRefMap::hostOf(uint32_t localRefID, FormRef* out) const {
    auto it = toHost_.find(localRefID);
    if (it == toHost_.end()) return false;
    if (out) *out = it->second;
    return true;
}

bool CreatedRefMap::forget(const FormRef& host) {
    auto it = toLocal_.find(host);
    if (it == toLocal_.end()) return false;
#if NVWORLD_MUT != 2
    toHost_.erase(it->second);
#endif
    toLocal_.erase(it);
    return true;
}

void CreatedRefMap::clear() {
    toLocal_.clear();
    toHost_.clear();
}

// ------------------------------------------------------------------------------------------------ WorldResolver
bool WorldResolver::toRef(uint32_t refID, FormRef* out) {
    if ((refID >> 24) != 0xFFu) return inner_.toRef(refID, out);
    if (host_) {
        *out = FormRef(fnvcoop::createdRefPlugin(), refID);
        return true;
    }
#if NVWORLD_MUT == 3
    return false;  // MUTANT: client never translates its local created refs back to host refs
#endif
    return map_.hostOf(refID, out);
}

uint32_t WorldResolver::fromRef(const FormRef& r) {
    if (r.plugin != fnvcoop::createdRefPlugin()) return inner_.fromRef(r);
    if (host_) return r.localId;
    return map_.local(r);
}

// ------------------------------------------------------------------------------------------------ SpawnLedger
SpawnAction SpawnLedger::onSpawn(const fnvcoop::SpawnRef& s, bool cellReady) {
#if NVWORLD_MUT != 4
    if (map_.local(s.ref())) return SpawnAction::Duplicate;
    for (const auto& p : pending_)
        if (p.id == s.id) return SpawnAction::Duplicate;
#endif
    if (cellReady) return SpawnAction::Create;
    pending_.push_back(s);
    return SpawnAction::Queued;
}

DespawnResult SpawnLedger::onDespawn(const fnvcoop::DespawnRef& d) {
    DespawnResult r;
#if NVWORLD_MUT != 5
    for (auto it = pending_.begin(); it != pending_.end(); ++it) {
        if (it->id == d.id) {
            pending_.erase(it);
            r.action = DespawnAction::CancelledPending;
            return r;
        }
    }
#endif
    uint32_t id = map_.local(d.ref());
    if (!id) return r;  // Unknown: never created here (or already gone)
    map_.forget(d.ref());
    r.action = DespawnAction::DeleteLocal;
    r.localRefID = id;
    return r;
}

std::vector<fnvcoop::SpawnRef> SpawnLedger::takeReady(const std::function<bool(const fnvcoop::SpawnRef&)>& ready) {
    std::vector<fnvcoop::SpawnRef> out, keep;
    for (auto& p : pending_) (ready(p) ? out : keep).push_back(p);
#if NVWORLD_MUT == 6
    return out;  // MUTANT: the queue is not drained, so the same spawn is created again on every flush
#endif
    pending_ = std::move(keep);
    return out;
}

// ------------------------------------------------------------------------------------------------ RefVarTracker
static uint64_t varKey(uint32_t ref, uint16_t idx) { return (uint64_t(ref) << 16) | idx; }

bool RefVarTracker::observe(uint32_t refID, uint16_t index, double value) {
    auto k = varKey(refID, index);
    auto it = last_.find(k);
    if (it == last_.end()) {
        last_[k] = value;
#if NVWORLD_MUT == 7
        return true;  // MUTANT: the baseline value is reported as a change
#endif
        return false;
    }
#if NVWORLD_MUT == 9
    ++reported_;
    it->second = value;
    return true;  // MUTANT: unchanged values are reported too
#endif
    if (it->second == value) return false;
#if NVWORLD_MUT != 8
    if (reported_ >= budget_) return false;  // deferred: the tracker keeps the old value, so a later poll reports it
#endif
    it->second = value;
    ++reported_;
    return true;
}

void RefVarTracker::forgetRef(uint32_t refID) {
#if NVWORLD_MUT == 10
    return;  // MUTANT: unloaded refs keep stale baselines
#endif
    for (auto it = last_.begin(); it != last_.end();) {
        if ((it->first >> 16) == refID) it = last_.erase(it);
        else ++it;
    }
}

// ------------------------------------------------------------------------------------------------ WeatherTimeSync
std::vector<fnvcoop::Effect> WeatherTimeSync::poll(const FormRef& weather, bool haveWeather, float timeScale) {
    std::vector<fnvcoop::Effect> out;
    if (haveWeather) {
#if NVWORLD_MUT == 11
        weather_ = weather;
        out.push_back(fnvcoop::SetWeather{weather, false});  // MUTANT: every poll re-sends the weather
#else
        if (!weather_ || !(*weather_ == weather)) {
            weather_ = weather;
            out.push_back(fnvcoop::SetWeather{weather, false});
        }
#endif
    }
    if (timeScale > 0 && (!scale_ || *scale_ != timeScale)) {
        scale_ = timeScale;
        out.push_back(fnvcoop::SetTimeScale{timeScale});
    }
    return out;
}

void WeatherTimeSync::noteSent(const fnvcoop::Effect& e) {
#if NVWORLD_MUT == 12
    return;  // MUTANT: script-driven changes are sent a second time by the next poll
#endif
    if (auto* w = std::get_if<fnvcoop::SetWeather>(&e)) weather_ = w->weather;
    else if (auto* t = std::get_if<fnvcoop::SetTimeScale>(&e)) scale_ = t->scale;
}

bool WeatherTimeSync::blockLocal(bool guest, bool applying) {
#if NVWORLD_MUT == 13
    return false;  // MUTANT: guests keep their own weather
#endif
    return guest && !applying;
}

// ------------------------------------------------------------------------------------------------ actor values
std::optional<SetActorValueRef> makeAvEffect(const FormRef& actor, uint32_t av, AvCmdKind kind, float before, float after) {
    SetActorValueRef e;
    e.actor = actor;
    e.av = av;
#if NVWORLD_MUT != 15
    if (before == after) return std::nullopt;
#endif
    switch (kind) {
        case AvCmdKind::Set:
        case AvCmdKind::Force:
            e.mode = AvMode::Set;
            e.value = after;  // absolute: idempotent on replay
            break;
        case AvCmdKind::Mod:
            e.mode = AvMode::Mod;
            e.value = after - before;
            break;
        case AvCmdKind::Damage:
            e.mode = AvMode::Damage;
#if NVWORLD_MUT == 14
            e.value = after - before;  // MUTANT: negative damage
#else
            e.value = before - after;
#endif
            break;
        case AvCmdKind::Restore:
            e.mode = AvMode::Restore;
            e.value = after - before;
            break;
    }
    return e;
}

const char* avName(uint32_t av) {
    // NVSE ActorValueCode order (FalloutNV.exe 1.4.0.525); console accepts these names.
    static const char* const kNames[] = {
        "Aggression", "Confidence", "Energy", "Responsibility", "Mood", "Strength", "Perception", "Endurance", "Charisma",
        "Intelligence", "Agility", "Luck", "ActionPoints", "CarryWeight", "CritChance", "HealRate", "Health", "MeleeDamage",
        "DamageResist", "PoisonResist", "RadResist", "SpeedMult", "Fatigue", "Karma", "XP", "PerceptionCondition",
        "EnduranceCondition", "LeftAttackCondition", "RightAttackCondition", "LeftMobilityCondition", "RightMobilityCondition",
        "BrainCondition", "Barter", "BigGuns", "EnergyWeapons", "Explosives", "Lockpick", "Medicine", "MeleeWeapons", "Repair",
        "Science", "Guns", "Sneak", "Speech", "Survival", "Unarmed"};
    return av < sizeof(kNames) / sizeof(kNames[0]) ? kNames[av] : "";
}

// ------------------------------------------------------------------------------------------------ console lines
static std::string hex(uint32_t v) {
    char b[16];
    std::snprintf(b, sizeof b, "%08X", v);
    return b;
}
static std::string num(float v) {
    char b[48];
    std::snprintf(b, sizeof b, "%.4f", v);
    return b;
}

std::string markForDeleteLine() { return "MarkForDelete"; }

std::string setEssentialLine(const SetEssential& e, uint32_t id) {
#if NVWORLD_MUT == 16
    bool v = !e.essential;  // MUTANT
#else
    bool v = e.essential;
#endif
    if (e.refOnly) return std::string("SetActorRefEssential ") + (v ? "1" : "0");
    return "SetEssential " + hex(id) + (v ? " 1" : " 0");
}

std::string setRestrainedLine(bool on) { return on ? "SetRestrained 1" : "SetRestrained 0"; }

std::string setRelationLine(Relation kind, uint32_t a, uint32_t b) {
#if NVWORLD_MUT == 20
    std::swap(a, b);  // MUTANT
#endif
    const char* cmd = kind == Relation::Ally ? "SetAlly " : "SetEnemy ";
    // Neutral: SetEnemy with flags 0 (engine clears the relation); enemy/ally set both directions' default flags.
    return cmd + hex(a) + " " + hex(b) + (kind == Relation::Neutral ? " 0" : "");
}

std::string scriptPackageLine(bool add, uint32_t packageID) {
    return std::string(add ? "AddScriptPackage " : "RemoveScriptPackage ") + hex(packageID);
}

std::string avLine(const SetActorValueRef& e) {
    const char* n = avName(e.av);
    if (!*n) return "";
    const char* cmd = nullptr;
    switch (e.mode) {
#if NVWORLD_MUT == 17
        case AvMode::Set: cmd = "ForceActorValue "; break;  // MUTANT: Set must change the base value, not the modified one
#else
        case AvMode::Set: cmd = "SetActorValue "; break;
#endif
        case AvMode::Mod: cmd = "ModActorValue "; break;
        case AvMode::Damage: cmd = "DamageActorValue "; break;
        case AvMode::Restore: cmd = "RestoreActorValue "; break;
    }
    return std::string(cmd) + n + " " + num(e.value);
}

std::string placeAtMeLine(uint32_t baseID, int count) {
#if NVWORLD_MUT == 21
    (void)count;
    return "PlaceAtMe " + hex(baseID);  // MUTANT: count dropped
#endif
    return "PlaceAtMe " + hex(baseID) + " " + std::to_string(count);
}

std::string setPosLine(char axis, float v) { return std::string("SetPos ") + axis + " " + num(v); }

// ------------------------------------------------------------------------------------------------ WorldLog
void WorldLog::push(const WorldEffect& e) {
#if NVWORLD_MUT != 18
    for (auto& q : q_) {
        if (q.index() != e.index()) continue;
        bool same = false;
        if (auto* a = std::get_if<SetActorValueRef>(&e)) {
            auto& b = std::get<SetActorValueRef>(q);
            same = a->mode == AvMode::Set && b.mode == AvMode::Set && a->actor == b.actor && a->av == b.av;
        } else if (auto* c = std::get_if<SetEssential>(&e)) {
            auto& d = std::get<SetEssential>(q);
            same = c->actor == d.actor && c->refOnly == d.refOnly;
        } else if (auto* f = std::get_if<SetRestrained>(&e)) {
            same = f->actor == std::get<SetRestrained>(q).actor;
        }
        if (same) {
            q = e;
            return;
        }
    }
#endif
    q_.push_back(e);
}

std::vector<WorldEffect> WorldLog::drain() {
    std::vector<WorldEffect> out;
    out.swap(q_);
    return out;
}

// ------------------------------------------------------------------------------------------------ JSON
static json::Value J(const FormRef& r) { return json::Value(nvhooks::refToStr(r)); }

std::string worldEffectToJson(const WorldEffect& e) {
    json::Value o = json::Value::object();
    o.set("type", worldEffectName(e));
    if (auto* x = std::get_if<SetActorValueRef>(&e)) {
        o.set("actor", J(x->actor)).set("av", (int64_t)x->av).set("value", (double)x->value).set("mode", (int64_t)x->mode);
    } else if (auto* y = std::get_if<SetEssential>(&e)) {
        o.set("actor", J(y->actor)).set("essential", y->essential);
#if NVWORLD_MUT != 19
        o.set("refOnly", y->refOnly);
#endif
    } else if (auto* z = std::get_if<SetRestrained>(&e)) {
        o.set("actor", J(z->actor)).set("on", z->on);
    } else if (auto* r = std::get_if<SetRelation>(&e)) {
        o.set("a", J(r->a)).set("b", J(r->b)).set("kind", (int64_t)r->kind);
    } else if (auto* p = std::get_if<SetScriptPackage>(&e)) {
        o.set("actor", J(p->actor)).set("package", J(p->package)).set("add", p->add);
    }
    return o.dump();
}

bool worldEffectFromJson(const std::string& text, WorldEffect* out, std::string* err) {
    json::Value v;
    std::string perr;
    if (!json::parse(text, v, perr)) {
        if (err) *err = perr;
        return false;
    }
    auto ref = [&](const char* k, FormRef* r) { return nvhooks::strToRef(v.get(k).asString(""), r); };
    std::string t = v.get("type").asString("");
    if (t == "SetActorValueRef") {
        SetActorValueRef x;
        if (!ref("actor", &x.actor)) { if (err) *err = "bad actor"; return false; }
        x.av = (uint32_t)v.get("av").asInt();
        x.value = (float)v.get("value").asNumber();
        x.mode = (AvMode)v.get("mode").asInt();
        *out = x;
    } else if (t == "SetEssential") {
        SetEssential x;
        if (!ref("actor", &x.actor)) { if (err) *err = "bad actor"; return false; }
        x.essential = v.get("essential").asBool();
        x.refOnly = v.get("refOnly").asBool();
        *out = x;
    } else if (t == "SetRestrained") {
        SetRestrained x;
        if (!ref("actor", &x.actor)) { if (err) *err = "bad actor"; return false; }
        x.on = v.get("on").asBool();
        *out = x;
    } else if (t == "SetRelation") {
        SetRelation x;
        if (!ref("a", &x.a) || !ref("b", &x.b)) { if (err) *err = "bad faction"; return false; }
        x.kind = (Relation)v.get("kind").asInt();
        *out = x;
    } else if (t == "SetScriptPackage") {
        SetScriptPackage x;
        if (!ref("actor", &x.actor) || !ref("package", &x.package)) { if (err) *err = "bad ref"; return false; }
        x.add = v.get("add").asBool(true);
        *out = x;
    } else {
        if (err) *err = "unknown world effect type: " + t;
        return false;
    }
    return true;
}

}  // namespace nvworld
