// VatsRuntime: owns the VatsSync of the running co-op session and plugs it into the coop runtime's MuxTransport (first byte kChannelVats).
// Game-free, the same shape as plugin/combat CombatRuntime: the module calls frame() every frame with the facts it knows; this class builds / drops the
// VatsSync when the session starts / ends, maps slots to transport peers, forwards datagrams and reports joins / leaves.
#pragma once
#include <memory>
#include <set>
#include <vector>
#include "fnvcoop/transport_util.h"
#include "vats/vats_sync.h"

namespace nvcoop::vats {

struct ClientLink { PlayerId slot = kNoPlayer; fnvcoop::PeerId peer = 0; bool joined = false; };

struct FrameInfo {
    bool active = false;                  // the coop session is running
    bool isHost = false;
    PlayerId self = kNoPlayer;            // client: slot assigned by the host (kNoPlayer until joined)
    bool hostLinkUp = false;              // client: joined and not rebaselining
    std::vector<ClientLink> clients;      // host: clients known to the session
    fnvcoop::MuxTransport* mux = nullptr; // the session's mux; a new pointer = a new session
    uint64_t nowMs = 0;
};

class VatsRuntime {
public:
    VatsRuntime(IVatsEngine& engine, const Config& baseCfg);
    ~VatsRuntime();
    VatsRuntime(const VatsRuntime&) = delete;
    VatsRuntime& operator=(const VatsRuntime&) = delete;

    void frame(const FrameInfo& f);
    VatsSync* sync() { return sync_.get(); }
    const VatsSync* sync() const { return sync_.get(); }
    uint64_t rebuilds() const { return rebuilds_; }
    uint64_t droppedUnknownPeer() const { return droppedUnknownPeer_; }
    Config& baseConfig() { return base_; }    // settings / perk settings for the NEXT session

private:
    class MuxNet;
    void teardown();
    PlayerId slotOfPeer(fnvcoop::PeerId p) const;

    IVatsEngine& engine_;
    Config base_;
    std::shared_ptr<bool> alive_;
    std::unique_ptr<MuxNet> net_;
    std::unique_ptr<VatsSync> sync_;
    fnvcoop::MuxTransport* mux_ = nullptr;
    bool isHost_ = false;
    PlayerId self_ = kNoPlayer;
    bool linkUp_ = false;
    uint64_t nowMs_ = 0;
    std::vector<ClientLink> clients_;
    std::set<PlayerId> known_;
    uint32_t epoch_ = 0;
    uint64_t rebuilds_ = 0, droppedUnknownPeer_ = 0;
};

}  // namespace nvcoop::vats
