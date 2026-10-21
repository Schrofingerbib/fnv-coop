// AvatarRuntime: everything the player-avatar layer does per frame, game-free, on top of IGameBackend.
//
//   local player  --ReadLocalFrame--> LocalSampler (20 Hz) --> snapshot sink (the co-op layer sends it)
//                 --EquipWatcher---->                      --> equipment sink (reliable channel)
//   remote player <-- OnRemoteSnapshot(slot, snap, arrivalMs) --> live::InterpBuffer --> PuppetManager --> GamePuppetEngine --> game
//
// Plus the dev "loopback": a puppet of the LOCAL player driven by its own snapshots through the same interpolation buffer, shifted by
// a fixed offset. One game instance is enough to prove the whole puppet chain with the T7 comparison (tools/avatar/proof_puppet.py
// --loopback): the real player and its puppet are both in the same probe.
//
// The co-op layer (plugin/coop, owned by the dll-wiring agent) needs four calls: SetLocalIdentity, SetSnapshotSink / SetEquipSink,
// AddRemote / RemoveRemote and OnRemoteSnapshot / OnRemoteEquipment. docs/tools/avatar.md "Wiring" lists them.
#pragma once
#include <cstdint>
#include <functional>
#include <map>
#include <memory>
#include <optional>
#include <string>
#include <vector>

#include "../puppet_manager.h"
#include "game_backend.h"
#include "local_sampler.h"
#include "puppet_game_engine.h"

namespace nvcoop::avatar::engine {

struct RuntimeConfig {
    Config puppet;                          // localSlot, maxPuppets, templates, defaultTemplate, thresholds
    EngineOptions engine;                   // taggedBases is extended with every template base
    LocalSamplerConfig sampler;
    live::InterpConfig interp;
    uint32_t equipPollMs = 400;
    Slot loopbackSlot = 1;                  // the slot the loopback puppet uses (must differ from puppet.localSlot)
    Vec3 loopbackOffset{160.0f, 0.0f, 0.0f};   // world-space shift of the loopback puppet
    uint32_t saveHoldMs = 10000;            // puppets stay away this long after SaveBegin if nobody says SaveEnd (a save that never reported back)
};

enum class GameEvent : uint8_t { SaveBegin, SaveEnd, LoadBegin, LoadEnd, NewGame, ExitToMenu };

class AvatarRuntime {
public:
    AvatarRuntime(IGameBackend& be, RuntimeConfig cfg);
    AvatarRuntime(const AvatarRuntime&) = delete;
    AvatarRuntime& operator=(const AvatarRuntime&) = delete;

    using SnapshotSink = std::function<void(const live::ActorSnapshot&)>;
    using EquipSink = std::function<void(const std::vector<EquipEntry>&)>;
    void SetSnapshotSink(SnapshotSink s) { snapSink_ = std::move(s); }
    void SetEquipSink(EquipSink s) { equipSink_ = std::move(s); }
    void SetLog(std::function<void(const std::string&)> log);
    void SetLocalIdentity(live::NetId netId, live::OwnerEpoch epoch);

    // ---- remote players ---------------------------------------------------------------------------------------------
    bool AddRemote(Slot slot, const Appearance& a);
    void RemoveRemote(Slot slot);
    // A changed look of a known remote player: applied to the live puppet (a new puppet only when race or sex pick another template).
    // False when the slot is unknown.
    bool SetRemoteAppearance(Slot slot, const Appearance& a);
    // The local player's own look (race, sex, hair, eyes, body, face data) as the backend reads it. False when unreadable.
    bool ReadLocalAppearance(Appearance& out);
    void OnRemoteSnapshot(Slot slot, const live::ActorSnapshot& s, uint32_t arrivalMs);
    void OnRemoteEquipment(Slot slot, const std::vector<EquipEntry>& wanted);

    // ---- dev loopback -----------------------------------------------------------------------------------------------
    bool SetLoopback(bool on);
    bool loopback() const { return loopback_; }
    void SetLoopbackOffset(const Vec3& o) { cfg_.loopbackOffset = o; }

    // ---- per frame / events -----------------------------------------------------------------------------------------
    void Tick(uint32_t nowMs);
    void OnEvent(GameEvent e);

    // ---- ghost-ref map ----------------------------------------------------------------------------------------------
    RefId RefOf(Slot slot) const { return pm_.RefOf(slot); }
    std::optional<Slot> SlotOf(RefId ref) const { return pm_.SlotOf(ref); }

    // ---- introspection ----------------------------------------------------------------------------------------------
    PuppetManager& manager() { return pm_; }
    GamePuppetEngine& engine() { return eng_; }
    LocalSampler& sampler() { return sampler_; }
    const RuntimeConfig& config() const { return cfg_; }
    std::string StatusJson();   // avatar.status
    std::string LocalJson();    // avatar.local: the last local sample and what the engine said
    uint64_t remoteSnapshots() const { return remoteSnaps_; }
    uint64_t droppedRemote() const { return droppedRemote_; }
    uint64_t posesFed() const { return posesFed_; }

private:
    struct Remote {
        live::InterpBuffer buf;
        explicit Remote(const live::InterpConfig& c) : buf(c) {}
        bool everSampled = false;
    };

    void PublishLocal(uint32_t nowMs);
    void DriveRemotes(uint32_t nowMs);
    void Log(const std::string& s) const { if (log_) log_(s); }

    IGameBackend& be_;
    RuntimeConfig cfg_;
    GamePuppetEngine eng_;
    PuppetManager pm_;
    LocalSampler sampler_;
    EquipWatcher equip_;
    std::map<Slot, std::unique_ptr<Remote>> remotes_;
    SnapshotSink snapSink_;
    EquipSink equipSink_;
    std::function<void(const std::string&)> log_;
    bool loopback_ = false;
    bool loading_ = false;
    live::NetId netId_ = 0;
    uint64_t remoteSnaps_ = 0, droppedRemote_ = 0, posesFed_ = 0;
    uint32_t lastTick_ = 0;
    bool saveHeld_ = false;
    uint32_t saveHeldAt_ = 0;
    live::ActorSnapshot lastSnap_;
    bool haveSnap_ = false;
};

}  // namespace nvcoop::avatar::engine
