// L12 world-state core (game-free, unit-tested). Everything here is plain C++ over synccore types; the engine glue lives
// in world_hooks.cpp / world_apply.cpp and only feeds this code.
//
// NVWORLD_MUT=<n> compiles a deliberately broken variant (mutation twin n); the test binary must fail for every n.
#pragma once
#include <cstdint>
#include <functional>
#include <map>
#include <optional>
#include <string>
#include <unordered_map>
#include <vector>

#include "engine_hooks.h"  // nvhooks::FormResolver (declaration only; game-free)
#include "fnvcoop/effect.h"
#include "world_effects.h"

#ifndef NVWORLD_MUT
#define NVWORLD_MUT 0
#endif

namespace nvworld {

// ---------------------------------------------------------------------------------------------------------------------
// 1. Created refs: host ref (FormRef "fnvcoop.created":0xFFxxxxxx) <-> this machine's local refID
// ---------------------------------------------------------------------------------------------------------------------
class CreatedRefMap {
public:
    void bind(const fnvcoop::FormRef& host, uint32_t localRefID);  // rebinding replaces both directions
    uint32_t local(const fnvcoop::FormRef& host) const;            // 0 = unknown
    bool hostOf(uint32_t localRefID, fnvcoop::FormRef* out) const;
    bool forget(const fnvcoop::FormRef& host);                     // true if it was bound
    size_t size() const { return toLocal_.size(); }
    void clear();

private:
    std::map<fnvcoop::FormRef, uint32_t> toLocal_;
    std::unordered_map<uint32_t, fnvcoop::FormRef> toHost_;
};

// FormResolver decorator: the engine resolver cannot name created (0xFF) refs. On the host a created ref keeps the engine id
// (identity); on a client the map translates in both directions. Everything else is delegated.
class WorldResolver : public nvhooks::FormResolver {
public:
    WorldResolver(nvhooks::FormResolver& inner, CreatedRefMap& map, bool hostSide) : inner_(inner), map_(map), host_(hostSide) {}
    bool toRef(uint32_t refID, fnvcoop::FormRef* out) override;
    uint32_t fromRef(const fnvcoop::FormRef& r) override;
    void* lookup(uint32_t refID) override { return inner_.lookup(refID); }

private:
    nvhooks::FormResolver& inner_;
    CreatedRefMap& map_;
    bool host_;
};

// ---------------------------------------------------------------------------------------------------------------------
// 2. Spawn ledger: SpawnRef / DespawnRef bookkeeping on a client
// ---------------------------------------------------------------------------------------------------------------------
enum class SpawnAction { Create, Queued, Duplicate };
enum class DespawnAction { CancelledPending, DeleteLocal, Unknown };
struct DespawnResult {
    DespawnAction action = DespawnAction::Unknown;
    uint32_t localRefID = 0;  // DeleteLocal: the created ref to MarkForDelete (already forgotten by the ledger)
};

class SpawnLedger {
public:
    // cellReady: the spawn's cell is loaded here, so the object can be created now. Otherwise it is queued.
    // Duplicate: this host ref is already bound or queued (a replayed/late-join txn) -- never create twice.
    // (DespawnRef only ever names created refs: synccore's DespawnRef carries just the 0xFF id.)
    SpawnAction onSpawn(const fnvcoop::SpawnRef& s, bool cellReady);
    // The glue created the local object for `host`.
    void onCreated(const fnvcoop::FormRef& host, uint32_t localRefID) { map_.bind(host, localRefID); }
    DespawnResult onDespawn(const fnvcoop::DespawnRef& d);
    // Queued spawns for which `ready(spawn)` is true, in arrival order; they are removed from the queue.
    std::vector<fnvcoop::SpawnRef> takeReady(const std::function<bool(const fnvcoop::SpawnRef&)>& ready);
    size_t pending() const { return pending_.size(); }
    CreatedRefMap& map() { return map_; }
    const CreatedRefMap& map() const { return map_; }

private:
    CreatedRefMap map_;
    std::vector<fnvcoop::SpawnRef> pending_;
};

// ---------------------------------------------------------------------------------------------------------------------
// 3. Ref script variables (set ref.var to x): change tracking with a per-poll budget
// ---------------------------------------------------------------------------------------------------------------------
class RefVarTracker {
public:
    // First sight of (ref,index) records the baseline and returns false (a ref's initial values come from the baseline
    // transfer, not from the change stream). Later calls return true when the value differs from the last REPORTED one
    // and the poll budget is not exhausted; a deferred change is reported by a later poll.
    bool observe(uint32_t refID, uint16_t index, double value);
    void beginPoll(size_t budget) { budget_ = budget; reported_ = 0; }
    size_t reportedThisPoll() const { return reported_; }
    void forgetRef(uint32_t refID);  // cell unloaded: the next sighting re-baselines
    size_t size() const { return last_.size(); }
    void clear() { last_.clear(); }

private:
    std::unordered_map<uint64_t, double> last_;
    size_t budget_ = 0, reported_ = 0;
};

// ---------------------------------------------------------------------------------------------------------------------
// 4. Weather / time-scale: host observes, emits only genuine, not-yet-sent changes
// ---------------------------------------------------------------------------------------------------------------------
class WeatherTimeSync {
public:
    // Host poll. weatherRefID 0 = unknown (interior): no effect. Returns effects to record (host order).
    std::vector<fnvcoop::Effect> poll(const fnvcoop::FormRef& weather, bool haveWeather, float timeScale);
    // A SetWeather/SetTimeScale already went out through the capture hub (script-driven): do not re-send it on the next poll.
    void noteSent(const fnvcoop::Effect& e);
    // Client side: should a LOCAL weather/time change be blocked? Only a host txn being applied may change them.
    static bool blockLocal(bool guest, bool applying);

private:
    std::optional<fnvcoop::FormRef> weather_;
    std::optional<float> scale_;
};

// ---------------------------------------------------------------------------------------------------------------------
// 5. Non-player actor values
// ---------------------------------------------------------------------------------------------------------------------
enum class AvCmdKind : uint8_t { Set = 0, Mod = 1, Force = 2, Damage = 3, Restore = 4 };
// before/after = the actor's current AV value around the command. Returns nullopt when nothing changed.
std::optional<SetActorValueRef> makeAvEffect(const fnvcoop::FormRef& actor, uint32_t av, AvCmdKind kind, float before, float after);
// Console name of an actor value code ("" if unknown).
const char* avName(uint32_t av);

// ---------------------------------------------------------------------------------------------------------------------
// 6. Console lines for applying world effects (run with the target as the calling reference where noted)
// ---------------------------------------------------------------------------------------------------------------------
std::string markForDeleteLine();                                     // on the ref
std::string setEssentialLine(const SetEssential& e, uint32_t baseOrRefID);  // refOnly: on the ref; else SetEssential <base> n
std::string setRestrainedLine(bool on);                              // on the ref
std::string setRelationLine(Relation kind, uint32_t factionA, uint32_t factionB);
std::string scriptPackageLine(bool add, uint32_t packageID);         // on the actor
std::string avLine(const SetActorValueRef& e);                       // on the actor; empty if the AV has no console name
std::string placeAtMeLine(uint32_t baseID, int count);               // on a reference in the target cell
std::string setPosLine(char axis, float v);

// ---------------------------------------------------------------------------------------------------------------------
// 7. WorldLog: lane-local effect buffer for the effects L4 has no type for yet (host drains, clients apply)
// ---------------------------------------------------------------------------------------------------------------------
class WorldLog {
public:
    void push(const WorldEffect& e);       // coalesces: a second write to the same (actor, av, Set) / essential / restrained replaces the first
    std::vector<WorldEffect> drain();
    size_t size() const { return q_.size(); }

private:
    std::vector<WorldEffect> q_;
};

// JSON for WorldEffect: {"type":"SetEssential","actor":"falloutnv.esm:104C79","essential":true,"refOnly":false}
std::string worldEffectToJson(const WorldEffect& e);
bool worldEffectFromJson(const std::string& json, WorldEffect* out, std::string* err);

}  // namespace nvworld
