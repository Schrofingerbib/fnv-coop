// VatsSync: the session logic of real-time VATS on one machine (host or client). Game-free; the engine is IVatsEngine, the network IVatsNet.
//
// Per frame it: reads the engine's VATS mode and tells the others when it changes (MPhase), notices queued actions (MAction, the perk ledger), starts the
// playback by itself in real time (auto fire), keeps a party member from being a target (guard), runs the host's party slow-mo arbiter, mirrors the
// host's grant on every machine and applies the world time scale, repeats the host's settings. The hooks in vats_module.cpp ask it for the
// PolicyInput (policyInput()) and for the Stranger credit (attackerFor()).
#pragma once
#include <memory>
#include <set>
#include <string>
#include "vats/vats_perks.h"
#include "vats/vats_policy.h"
#include "vats/vats_ports.h"
#include "vats/vats_slowmo.h"
#include "vats/vats_wire.h"

namespace nvcoop::vats {

struct Config {
    Settings settings;               // this machine's ini values. The host's are authoritative; a client uses its own only until the host's MConfig arrives
    PerkSettings perks;
    PlayerId self = kNoPlayer;
    bool isHost = false;
    uint32_t epoch = 1;              // host: the session epoch of its slow-mo grants
    uint32_t configRepeatMs = 5000;  // host: MConfig is repeated this often
    uint32_t phaseHeartbeatMs = 1000;
    uint32_t guardCooldownMs = 250;
};

struct Stats {
    uint64_t phasesSent = 0, phasesReceived = 0, actionsQueued = 0, actionsReceived = 0, configsSent = 0, configsApplied = 0, slowMoSent = 0, slowMoApplied = 0;
    uint64_t autoFires = 0, autoFireRefused = 0, guardSkips = 0, guardBlocked = 0, guardCancelled = 0;
    uint64_t scaleSets = 0, scaleReasserts = 0, scaleFailed = 0, cameraChanges = 0;
    uint64_t grsRefunds = 0, grsApGiven = 0, grsNoPerk = 0, vatsKillsRemote = 0, vatsKillsNative = 0;
    uint64_t undecodable = 0, spoofed = 0, rejected = 0, relayed = 0, hopLimit = 0;
};

class VatsSync {
public:
    VatsSync(const Config& cfg, IVatsEngine& engine, IVatsNet& net);
    ~VatsSync();
    VatsSync(const VatsSync&) = delete;
    VatsSync& operator=(const VatsSync&) = delete;

    PlayerId self() const { return cfg_.self; }
    bool isHost() const { return cfg_.isHost; }

    // ---- session ----
    void playerJoined(PlayerId slot, uint64_t nowMs);   // host
    void playerLeft(PlayerId slot, uint64_t nowMs);     // host
    void hostLost();                                    // client: back to the local settings, slow-mo cleared
    void shutdown();                                    // the session ends: the time scale and the camera settings go back to normal
    // Host only: change the settings at run time (bridge vats.set); every client gets them at once. false = refused (a client, or nothing changed).
    bool setSettings(const Settings& s, uint64_t nowMs);

    // ---- network ----
    void receive(PlayerId from, const uint8_t* data, size_t size, uint64_t nowMs);

    // ---- main thread, every frame ----
    void tick(uint64_t nowMs);

    // ---- events ----
    void onActorDied(uint32_t ref, bool decidedHere, bool fromNetwork, uint64_t nowMs);

    // ---- hook queries (cheap, main thread) ----
    PolicyInput policyInput() const;
    uint32_t attackerFor(uint32_t attackerRef, uint64_t nowMs) const;
    const Settings& settings() const { return settings_; }
    bool partySlowActive() const { return follower_.active(); }
    float partyScale() const { return follower_.scale(); }
    Mode localMode() const { return lastMode_; }
    const Stats& stats() const { return stats_; }
    const SlowMoStats& slowMoStats() const { return arbiter_.stats(); }
    Mode modeOf(PlayerId p) const { return p < kMaxSlots ? remoteModes_[p] : Mode::None; }
    std::string statusJson(uint64_t nowMs) const;

private:
    void send(PlayerId dest, const Envelope& e);
    void broadcastHostOnly(const Body& b);                 // host -> every joined client
    void sendConfig(PlayerId dest);
    void sendSlowMo(PlayerId dest, uint64_t nowMs);
    void announcePhase(Mode m, uint64_t nowMs);
    void handleConfig(const MConfig& m);
    void applySlowMoToEngine(uint64_t nowMs);
    void autoFire(uint64_t nowMs);
    void guard(uint64_t nowMs);
    void onQueuedChanged(int queued, uint64_t nowMs);
    Settings effectiveSlowSettings() const;

    Config cfg_;
    IVatsEngine& engine_;
    IVatsNet& net_;
    Settings settings_;                 // the settings in force on this machine
    SlowMoArbiter arbiter_;             // host only
    SlowMoFollower follower_;           // everyone
    VatsLedger ledger_;
    std::set<PlayerId> joined_;
    Mode remoteModes_[kMaxSlots] = {};
    Mode lastMode_ = Mode::None;
    uint32_t phaseSeq_ = 0, actionSeq_ = 0;
    uint64_t lastPhaseMs_ = 0, lastConfigMs_ = 0, lastSlowMoMs_ = 0, lastGuardMs_ = 0, lastReassertMs_ = 0, queuedAtMs_ = 0;
    int lastQueued_ = 0;
    bool acceptSent_ = false, started_ = false;
    bool partyTargetQueued_ = false;
    float appliedScale_ = 1.0f;
    bool cameraShortened_ = false;
    Stats stats_;
};

}  // namespace nvcoop::vats
