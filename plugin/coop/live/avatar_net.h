// AvatarNet: the network service for player avatars (FULL-SYNC-PLAN 3.1). A live-hub service on channel kChannelAvatars.
//
//   every machine : takes the local player's 20 Hz snapshots from the avatar layer (IAvatarSide: sampler + equipment watcher), publishes
//                   them as ActorSnapshots of netId playerNetId(self) with the synccore/live delta codec, and hands the other players'
//                   snapshots to the avatar layer, which interpolates them and drives one restrained puppet per remote slot.
//   client        : sends its stream to the host only; hears the host (which relays everybody else) and the host's Roster.
//   host          : accepts a client's stream only when the snapshot is about THAT client's own slot (no forged avatars), relays it to the
//                   other clients, sends the Roster (who exists) and relays Look messages (appearance + equipment).
//   look          : appearance and what the player wears travel reliably when they change, and again to every newcomer / returning link.
//
// Game-free: the engine side is behind IAvatarSide (real: RuntimeSide over plugin/avatar/engine AvatarRuntime; tests: the same runtime on a
// FakeGameBackend). Unit-tested with a loopback of 3 machines (tests/test_live_wiring.cpp).
#pragma once
#include <functional>
#include <map>
#include <memory>
#include <set>
#include <string>
#include <vector>

#include "avatar_side.h"
#include "avatar_types.h"
#include "avatar_wire.h"
#include "stream_rate.h"
#include "live/live.h"
#include "live_hub.h"

namespace nvcoop::livewire {

struct AvatarNetOptions {
    uint32_t ackIntervalMs = 50;
    uint32_t lookCheckMs = 500;            // how often the local look is compared with the one last sent
    uint32_t poseReportMs = 200;           // how often remote players' places go to the ownership service (when they changed)
    fnvcoop::live::SenderConfig sender;
    nvcoop::avatar::StreamRateConfig rate;  // 20 Hz near / 5 Hz far / 1 Hz elsewhere (stream_rate.h); rate.enabled = false sends everything at full rate
    uint32_t appearancePollMs = 2000;      // how often the local look (race, sex, hair, body, face) is read from the game
    bool tickSide = true;                  // the hub's tick drives the avatar layer (false: something else ticks it)
};

struct AvatarNetStats {
    uint64_t sent = 0, received = 0, accepted = 0, rejected = 0, forged = 0, noRoster = 0, relayed = 0, acksSent = 0, acksReceived = 0;
    uint64_t looksSent = 0, looksApplied = 0, looksRejected = 0, rostersSent = 0, rostersApplied = 0, badDatagrams = 0;
    uint64_t localSnapshots = 0, resyncsAnswered = 0, idleTicks = 0;
    uint64_t rateKept = 0, rateReleased = 0;          // snapshots held back by the near / far rate and released when their turn came
    uint64_t appearanceUpdates = 0, puppetRespawnsForLook = 0;
};

class AvatarNet : public ILiveService {
public:
    AvatarNet(IAvatarSide& side, AvatarNetOptions opt = {}, std::function<uint32_t()> epochSeed = nullptr);
    ~AvatarNet() override;

    // Where a player is (cell + position of its newest snapshot; the LOCAL player's own slot too, from its published snapshots): the ownership and
    // spawn services want every player's place. present=false when the player is gone.
    using PoseSink = std::function<void(PlayerId slot, bool present, fnvcoop::live::CellId cell, const fnvcoop::live::Vec3& pos)>;
    void setPoseSink(PoseSink s) { poseSink_ = std::move(s); }
    void setLog(std::function<void(const std::string&)> log) { log_ = std::move(log); }
    // Race / sex of the local player (for the template NPC of its puppets on the other machines). Optional; without it the default template.
    // Without it the look is read through IAvatarSide::readLocalAppearance (the game).
    void setLocalAppearanceReader(std::function<bool(nvcoop::avatar::Appearance&)> f) { localAppearance_ = std::move(f); }

    // ---- ILiveService ----
    const char* name() const override { return "avatars"; }
    std::vector<uint8_t> channels() const override { return {kChannelAvatars}; }
    void sessionBegin(const SessionInfo& info, ILiveLink& link) override;
    void sessionEnd() override;
    void playerJoined(PlayerId slot) override;
    void playerLeft(PlayerId slot) override;
    void hostLinkUp() override;
    void hostLinkDown() override;
    void receive(PlayerId from, const uint8_t* data, size_t size) override;
    void tick(uint64_t nowMs) override;
    void idleTick(uint64_t nowMs) override;
    void loadBegin() override;
    void loadEnd() override;
    void gameExit() override;

    // ---- queries (glue, tests, bridge) ----
    nvcoop::avatar::RefId puppetRef(PlayerId slot) const { return side_.refOf(slot); }   // 0 = none (the ghost-ref map of the acting-player design)
    bool slotOfPuppetRef(nvcoop::avatar::RefId ref, PlayerId& slot) const { return ref && side_.slotOf(ref, slot); }
    const std::set<PlayerId>& roster() const { return roster_; }
    const AvatarNetStats& stats() const { return stats_; }
    const nvcoop::avatar::StreamRatePlanner& ratePlanner() const { return rate_; }
    // Tier the stream of `subject` currently has for `observer` (diagnostics, tests).
    nvcoop::avatar::StreamTier tierOf(PlayerId observer, PlayerId subject) { return rate_.tierOf(observer, subject); }
    bool hasLook(PlayerId slot) const { return looks_.count(slot) != 0; }
    const AvLook* lookOf(PlayerId slot) const { auto it = looks_.find(slot); return it == looks_.end() ? nullptr : &it->second; }
    bool inSession() const { return inSession_; }
    PlayerId self() const { return self_; }
    std::string statusJson() const;

private:
    void log(const std::string& s) const { if (log_) log_(s); }
    void addPuppet(PlayerId slot);
    void removePuppet(PlayerId slot);
    void applyRoster(const std::vector<uint8_t>& slots);
    void sendTo(PlayerId peer, bool reliable, Bytes data);
    void broadcastReliable(const Bytes& data, PlayerId except);
    void sendRoster(PlayerId to /* kNoPlayer = everyone */);
    void sendLook(const AvLook& l, PlayerId to /* kNoPlayer = everyone except `skip` */, PlayerId skip);
    void flushStreams(uint64_t nowMs);
    void sendAcks(uint64_t nowMs);
    void reportPoses(uint64_t nowMs);
    void checkLocalLook(uint64_t nowMs);
    void onSnapshotDatagram(PlayerId from, const uint8_t* body, size_t size);
    void onLook(PlayerId from, const uint8_t* data, size_t size);
    void queueTo(PlayerId peer, PlayerId subject, const fnvcoop::live::ActorSnapshot& snap, uint64_t nowMs);
    void releasePending(uint64_t nowMs);
    std::vector<PlayerId> uplinkObservers() const;
    std::vector<PlayerId> peers() const;   // who we exchange streams with: host = joined clients, client = the host

    IAvatarSide& side_;
    AvatarNetOptions opt_;
    std::function<uint32_t()> epochSeed_;
    std::function<void(const std::string&)> log_;
    std::function<bool(nvcoop::avatar::Appearance&)> localAppearance_;
    PoseSink poseSink_;

    // session state
    ILiveLink* link_ = nullptr;
    bool inSession_ = false, isHost_ = false, hostUp_ = false;
    PlayerId self_ = kNoPlayer;
    uint32_t epoch_ = 1;
    uint64_t nowMs_ = 0;
    std::set<PlayerId> roster_;                          // remote slots that have a player (puppet or about to)
    std::set<PlayerId> joined_;                          // host: joined clients
    std::map<PlayerId, AvLook> looks_;
    std::map<PlayerId, fnvcoop::live::ActorSnapshot> lastSnap_;   // newest accepted snapshot per remote slot (place for the ownership service)
    std::map<PlayerId, fnvcoop::live::ActorSnapshot> reportedSnap_;
    mutable nvcoop::avatar::StreamRatePlanner rate_;   // tierOf() keeps the hysteresis state, so reading a tier in a const query is fine
    std::map<PlayerId, uint64_t> rx_;                  // accepted snapshots per remote slot (statusJson: the in-game proof turns the difference into a rate)
    std::map<PlayerId, nvcoop::avatar::Appearance> applied_;   // the look each puppet was last built / updated with
    nvcoop::avatar::Appearance localApp_;                       // the cached local look
    bool haveLocalApp_ = false;
    uint64_t lastAppRead_ = 0;
    fnvcoop::live::SnapshotSender sender_;
    fnvcoop::live::SnapshotReceiver recv_;
    std::vector<fnvcoop::live::ActorSnapshot> localOut_;   // snapshots the side produced during this tick
    bool haveEquip_ = false;
    std::vector<nvcoop::avatar::EquipEntry> equip_;        // the local player's worn items as last reported by the side
    bool lookSentValid_ = false;
    AvLook lastLookSent_;
    uint64_t lastAck_ = 0, lastLookCheck_ = 0, lastPoseReport_ = 0;
    fnvcoop::live::ActorSnapshot lastLocal_, reportedLocal_;   // the local player's newest published place, and the one the pose sink last heard
    bool haveLocal_ = false, reportedLocalValid_ = false;
    AvatarNetStats stats_;
};

}  // namespace nvcoop::livewire
