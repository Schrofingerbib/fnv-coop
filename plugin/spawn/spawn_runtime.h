// SpawnRuntime: owns the SpawnSync of the running co-op session and plugs it into the coop runtime's MuxTransport (first byte kChannelSpawn).
// Game-free: the module (or a test) calls frame() every frame with the facts it knows; this class builds / drops the SpawnSync when the
// session starts / ends, maps slots to transport peers, forwards datagrams and reports joins / leaves.
#pragma once
#include <functional>
#include <memory>
#include <set>
#include <vector>
#include "fnvcoop/transport_util.h"
#include "spawn/spawn_sync.h"

namespace nvcoop::spawn {

struct ClientLink { PlayerId slot = kNoPlayer; fnvcoop::PeerId peer = 0; bool joined = false; };

struct FrameInfo {
    bool active = false;                  // the coop session is running
    bool isHost = false;
    PlayerId self = kNoPlayer;            // client: slot assigned by the host (kNoPlayer until joined)
    bool hostLinkUp = false;              // client: joined and not rebaselining
    std::vector<ClientLink> clients;      // host: clients known to the session
    fnvcoop::MuxTransport* mux = nullptr; // the session's mux; a new pointer = a new session
    uint64_t nowMs = 0;                   // wall clock (ownership, retries)
    uint64_t gameMs = 0;                  // game time (respawn timers; host authoritative)
    std::function<void(PlayerId origin, const fnvcoop::SpawnRef&)> commit;   // host: record a SpawnRef effect in the story log
};

class SpawnRuntime {
public:
    SpawnRuntime(ISpawnEngine& eng, SyncConfig base = {});
    ~SpawnRuntime();
    SpawnRuntime(const SpawnRuntime&) = delete;
    SpawnRuntime& operator=(const SpawnRuntime&) = delete;

    void frame(const FrameInfo& f);
    SpawnSync* sync() { return sync_.get(); }
    const SpawnSync* sync() const { return sync_.get(); }
    SyncConfig& config() { return cfg_; }          // set before the session starts
    void setIgnore(std::function<bool(RefId)> f);  // survives session rebuilds
    uint64_t rebuilds() const { return rebuilds_; }
    uint64_t droppedUnknownPeer() const { return droppedUnknownPeer_; }

private:
    class MuxNet;
    class HostCommit;
    void teardown();
    PlayerId slotOfPeer(fnvcoop::PeerId p) const;

    ISpawnEngine& eng_;
    SyncConfig cfg_;
    std::function<bool(RefId)> ignore_;
    std::shared_ptr<bool> alive_;
    std::unique_ptr<MuxNet> net_;
    std::unique_ptr<HostCommit> commit_;
    std::unique_ptr<SpawnSync> sync_;
    fnvcoop::MuxTransport* mux_ = nullptr;
    bool isHost_ = false;
    PlayerId self_ = kNoPlayer;
    bool linkUp_ = false;
    std::vector<ClientLink> clients_;
    std::set<PlayerId> known_;                  // host: clients announced to SpawnSync as joined
    uint64_t rebuilds_ = 0, droppedUnknownPeer_ = 0;
};

// The one runtime the bridge method (coop.spawn) looks at; set by the glue while a session runs.
void setActive(SpawnRuntime* rt);
SpawnRuntime* active();

}  // namespace nvcoop::spawn
