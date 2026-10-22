// Fake "engine" for game-free co-op tests: a world model that both applies effects (client side) and performs
// local mutations while recording them into an nvhooks::CaptureHub (host side), exactly like L5's engine hooks do.
#pragma once
#include <map>
#include <set>
#include <string>
#include <vector>

#include "common/minijson.h"
#include "coop_runtime.h"
#include "effect_json.h"

namespace nvcoop::coop::test {

class FakeWorld : public IWorldApplier, public IWorldDumper {
public:
    // IWorldApplier (client side): perform the effect on the local world. Never records into the hub.
    ApplyResult apply(const fnvcoop::Effect& e, const fnvcoop::IEffectSink::Context& ctx) override {
        ++effectsApplied;
        if (ctx.baseline) ++baselineApplied;
        else if (ctx.seq == 0) untaggedSnapshot.push_back(fnvcoop::effectName(e));   // snapshot-diff effects outside the baseline
        if (!mutate(e, ctx.baseline)) { ++unsupported; return {false, "Unsupported"}; }
        return {true, "Ok"};
    }
    void onJoined(fnvcoop::PlayerId slot, const fnvcoop::SharePolicy&) override { joinedSlot = static_cast<int>(slot); }

    // IWorldDumper: shared state as a ShadowState (complete key space: every seeded quest/global, zeros included).
    bool dump(fnvcoop::ShadowState* out, uint64_t* hash, std::string* err) override {
        (void)err;
        fnvcoop::ShadowState st;
        auto ref = [](const std::string& k) { fnvcoop::FormRef r; nvhooks::strToRef(k, &r); return r; };
        for (auto& [k, v] : stages) st.stages[ref(k)] = v;
        for (auto& [k, v] : objectives) {
            size_t h = k.rfind('#');
            st.objectives[{ref(k.substr(0, h)), (uint32_t)std::stoul(k.substr(h + 1))}] = (uint8_t)v;
        }
        for (auto& [k, v] : globals) st.globals[ref(k)] = v;
        for (auto& [k, v] : questRunning) st.questRunning[ref(k)] = v;
        for (auto& [k, v] : enabled) st.enabled[ref(k)] = v;
        st.ledger[0].karma = karma;
        *out = st;
        *hash = 0xC0FFEEull;
        return true;
    }
    // Deterministic complete key space shared by host and client worlds.
    void seed(int quests) {
        for (int i = 0; i < quests; ++i) {
            char b[64];
            std::snprintf(b, sizeof b, "falloutnv.esm:%06X", 0x200 + i);
            stages[b] = 0;
            questRunning[b] = false;
            objectives[std::string(b) + "#0"] = 0;
            std::snprintf(b, sizeof b, "falloutnv.esm:%06X", 0x300 + i);
            globals[b] = 0;
        }
    }
    // Host side: a guest asked for an activation. Runs on the fake engine under a capture scope. The AddItem is
    // player-targeted at the REQUESTING guest, so it is recorded but not applied to the host's own inventory.
    void executeAction(nvhooks::CaptureHub& hub, fnvcoop::PlayerId slot, fnvcoop::IntentKind kind, const fnvcoop::FormRef& ref) {
        using namespace fnvcoop;
        nvhooks::ScopedCapture sc(hub, Cause{CauseKind::Script, ref, static_cast<int32_t>(kind)});
        (void)slot;
        if (kind == IntentKind::PickUp) {
            hub.record(AddItem{ref, 1, Recipient::origin()});
        } else {
            hostMutate(hub, EnableRef{ref});
            hub.record(AddItem{nvhooks::makeRef("FalloutNV.esm", 0xF), 5, Recipient::origin()});
        }
    }

    // Host side: a script run mutates the world AND the hook records it (record order == engine order).
    void hostMutate(nvhooks::CaptureHub& hub, const fnvcoop::Effect& e) {
        mutate(e);
        hub.record(e);
    }
    // Runs a nested script description: {"cause":..,"effects":[ effect | {"scope":{cause,effects}} ]}
    void runScope(nvhooks::CaptureHub& hub, const nvcoop::json::Value& v) {
        fnvcoop::EffectTxn head;
        std::string err;
        // cause/origin only; effects handled one by one so nested scopes can interleave
        nvcoop::json::Value hdr = nvcoop::json::Value::object();
        hdr.set("cause", v.get("cause"));
        nvhooks::txnFromJson(hdr, &head, &err);
        nvhooks::ScopedCapture sc(hub, head.cause);
        for (const auto& item : v.get("effects").items()) {
            if (item.get("scope").isObject()) { runScope(hub, item.get("scope")); continue; }
            fnvcoop::Effect e;
            if (nvhooks::effectFromJson(item, &e, &err)) hostMutate(hub, e);
        }
    }

    nvcoop::json::Value dump() const {
        using nvcoop::json::Value;
        auto obj = [](const std::map<std::string, double>& m) {
            Value o = Value::object();
            for (auto& [k, v] : m) o.set(k, v);
            return o;
        };
        Value o = Value::object();
        std::map<std::string, double> st, gl, items, run;
        for (auto& [k, v] : stages) st[k] = v;
        for (auto& [k, v] : globals) gl[k] = v;
        for (auto& [k, v] : inventory) items[k] = static_cast<double>(v);
        for (auto& [k, v] : questRunning) run[k] = v ? 1 : 0;
        std::map<std::string, double> en, obj2, rep;
        for (auto& [k, v] : enabled) en[k] = v ? 1 : 0;
        for (auto& [k, v] : objectives) obj2[k] = v;
        for (auto& [k, v] : reputation) rep[k] = static_cast<double>(v);
        std::map<std::string, double> rv, comp, spawn, npc;
        for (auto& [k, v] : refVars) rv[k] = v;
        for (auto& [k, v] : companions) comp[k] = v;
        for (auto& k : spawned) spawn[k.first] = 1;
        for (auto& [k, v] : worldState) npc[k] = static_cast<double>(std::hash<std::string>()(v) % 1000003);
        o.set("refVars", obj(rv)).set("companions", obj(comp)).set("spawned", obj(spawn)).set("npcState", obj(npc))
            .set("slideEnds", (int64_t)slideEnds).set("forcedEvents", (int64_t)forcedEvents);
        o.set("stages", obj(st)).set("globals", obj(gl)).set("items", obj(items)).set("questRunning", obj(run))
            .set("enabled", obj(en)).set("objectives", obj(obj2)).set("reputation", obj(rep)).set("karma", (int64_t)karma)
            .set("xp", (int64_t)xp);
        return o;
    }

    std::map<std::string, uint32_t> stages;
    std::map<std::string, float> globals;
    std::map<std::string, int64_t> inventory;
    std::map<std::string, bool> questRunning, enabled;
    std::map<std::string, int> objectives;     // "quest#obj" -> flags
    std::map<std::string, int64_t> reputation; // "faction/fame|infamy"
    int64_t karma = 0, xp = 0;
    // L12 / L8 / L11 effect surface: created refs (host ref -> this machine's own local ref), ref script vars, companions, events
    std::map<std::string, uint32_t> spawned;       // host ref string -> local refID (differs per machine on purpose)
    std::set<std::string> despawned;
    std::map<std::string, double> refVars;         // "<ref>#<index>" -> value
    std::map<std::string, double> questVars;       // "<quest>#<index>" -> value (SetScriptVar on a quest; not part of dump())
    std::map<std::string, int> companions;         // npc -> owner slot (absent = free)
    std::map<std::string, std::string> worldState; // key -> lane-local world effect json (NPC AV, relations, ...)
    uint64_t dupSpawns = 0, unresolvedRefOps = 0, worldApplied = 0, slideEnds = 0, forcedEvents = 0;
    uint32_t nextLocalRef = 0x00100000;
    // Lane-local world effect (WorldEffectApplier): same json twice must not be applied twice by the runtime.
    std::string applyWorldJson(const std::string& json) {
        nvcoop::json::Value v;
        std::string err;
        if (!nvcoop::json::parse(json, v, err)) return "BadWorldEffect";
        ++worldApplied;
        worldState[v.get("type").asString() + ":" + v.get("actor").asString() + ":" + v.get("a").asString() + v.get("b").asString() + ":" + v.get("av").dump()] = json;
        return "Ok";
    }
    std::vector<std::string> untaggedSnapshot;
    uint64_t effectsApplied = 0, unsupported = 0, baselineApplied = 0;
    int joinedSlot = -1;

private:
    static std::string s(const fnvcoop::FormRef& r) { return nvhooks::refToStr(r); }
    bool mutate(const fnvcoop::Effect& e, bool force = false) {
        using namespace fnvcoop;
        if (auto* x = std::get_if<SetStage>(&e)) { stages[s(x->quest)] = x->stage; return true; }
        if (auto* x = std::get_if<SetObjective>(&e)) {
            int& f = objectives[s(x->quest) + "#" + std::to_string(x->objective)];
            if (force) f = x->flags; else f |= x->flags;   // baseline: force-set the full flag byte
            return true;
        }
        if (auto* x = std::get_if<SetGlobal>(&e)) { globals[s(x->global)] = x->value; return true; }
        if (auto* x = std::get_if<EnableRef>(&e)) { enabled[s(x->ref)] = true; return true; }
        if (auto* x = std::get_if<DisableRef>(&e)) { enabled[s(x->ref)] = false; return true; }
        if (auto* x = std::get_if<AddItem>(&e)) { inventory[s(x->item)] += x->count; return true; }
        if (auto* x = std::get_if<RemoveItem>(&e)) { inventory[s(x->item)] -= x->count; return true; }
        if (auto* x = std::get_if<ModActorValue>(&e)) { if (x->av == kAvExperience) xp += x->delta; return true; }
        if (auto* x = std::get_if<ModReputation>(&e)) {
            reputation[s(x->faction) + "/fame"] += x->fame;
            reputation[s(x->faction) + "/infamy"] += x->infamy;
            return true;
        }
        if (auto* x = std::get_if<ModKarma>(&e)) { karma += x->delta; return true; }
        if (auto* x = std::get_if<StartQuest>(&e)) { questRunning[s(x->quest)] = true; return true; }
        if (auto* x = std::get_if<StopQuest>(&e)) { questRunning[s(x->quest)] = false; return true; }
        if (auto* x = std::get_if<SpawnRef>(&e)) {
            const std::string k = s(x->ref());
            if (spawned.count(k) || despawned.count(k)) { ++dupSpawns; return true; }   // replayed / late-join: never create twice
            spawned[k] = nextLocalRef++;
            return true;
        }
        if (auto* x = std::get_if<DespawnRef>(&e)) {
            const std::string k = s(x->ref());
            spawned.erase(k);
            despawned.insert(k);
            return true;
        }
        if (auto* x = std::get_if<SetScriptVar>(&e)) { questVars[s(x->owner) + "#" + std::to_string(x->index)] = x->value; return true; }
        if (auto* x = std::get_if<RefScriptVar>(&e)) {
            if (isCreatedRef(x->ref) && !spawned.count(s(x->ref))) { ++unresolvedRefOps; return false; }
            refVars[s(x->ref) + "#" + std::to_string(x->index)] = x->value;
            return true;
        }
        if (auto* x = std::get_if<CompanionOwner>(&e)) {
            if (x->active) companions[s(x->npc)] = x->owner; else companions.erase(s(x->npc));
            return true;
        }
        if (std::get_if<ForcedDialogue>(&e)) { ++forcedEvents; return true; }
        if (std::get_if<SlideshowEnd>(&e)) { ++slideEnds; return true; }
        return false;
    }
};

}  // namespace nvcoop::coop::test
