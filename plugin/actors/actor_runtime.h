// ActorRuntime: owns the ActorSync of the running co-op session and plugs it into the coop runtime's MuxTransport (first byte kChannelActors).
// Game-free: coop_module.cpp (or a test) calls frame() every frame with the facts it knows; this class builds / drops the ActorSync when the
// session starts / ends, maps slots to transport peers, forwards datagrams, and reports joins / leaves.
#pragma once
#include <functional>
#include <memory>
#include <set>
#include <vector>
#include "actors/actor_sync.h"
#include "fnvcoop/transport_util.h"

namespace nvcoop::actors {

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

class ActorRuntime {
public:
    explicit ActorRuntime(IActorPort& port);
    ~ActorRuntime();
    ActorRuntime(const ActorRuntime&) = delete;
    ActorRuntime& operator=(const ActorRuntime&) = delete;

    void frame(const FrameInfo& f);
    ActorSync* sync() { return sync_.get(); }
    const ActorSync* sync() const { return sync_.get(); }
    SyncConfig& config() { return cfg_; }          // set before the session starts (interp / interest / ownership tuning)
    uint64_t rebuilds() const { return rebuilds_; }
    uint64_t droppedUnknownPeer() const { return droppedUnknownPeer_; }

private:
    class MuxNet;
    void teardown();
    PlayerId slotOfPeer(fnvcoop::PeerId p) const;

    IActorPort& port_;
    SyncConfig cfg_;
    std::shared_ptr<bool> alive_;
    std::unique_ptr<MuxNet> net_;
    std::unique_ptr<ActorSync> sync_;
    fnvcoop::MuxTransport* mux_ = nullptr;
    bool isHost_ = false;
    PlayerId self_ = kNoPlayer;
    bool linkUp_ = false;
    std::vector<ClientLink> clients_;
    std::set<PlayerId> known_;                  // host: clients announced to ActorSync as joined
    uint64_t rebuilds_ = 0, droppedUnknownPeer_ = 0;
};

// The one runtime the bridge methods (coop.owner) look at; set by the module while a session runs.
void setActive(ActorRuntime* rt);
ActorRuntime* active();

}  // namespace nvcoop::actors
