// SocialRuntime: owns the SocialSync of the running co-op session and plugs it into the coop runtime's MuxTransport (first byte kChannelSocial).
// Game-free, the same shape as plugin/combat CombatRuntime: the glue (or a test) calls frame() every frame with the facts it knows; this class builds /
// drops the SocialSync when the session starts / ends, maps slots to transport peers, forwards datagrams and reports joins / leaves.
#pragma once
#include <functional>
#include <memory>
#include <set>
#include <vector>
#include "companions/social_sync.h"
#include "fnvcoop/transport_util.h"

namespace nvcoop::social {

struct ClientLink { PlayerId slot = kNoPlayer; fnvcoop::PeerId peer = 0; bool joined = false; };

struct FrameInfo {
    bool active = false;                  // the coop session is running
    bool isHost = false;
    PlayerId self = kNoPlayer;            // client: slot assigned by the host (kNoPlayer until joined)
    bool hostLinkUp = false;              // client: joined and not rebaselining
    std::vector<ClientLink> clients;      // host: clients known to the session
    fnvcoop::MuxTransport* mux = nullptr; // the session's mux; a new pointer = a new session
    uint64_t nowMs = 0;
    // host: commit effects through the story session (HostSession::commit). null = nothing can be committed (the commits count as failed).
    std::function<uint64_t(const std::vector<Effect>&, const char*)> commit;
};

class SocialRuntime {
public:
    SocialRuntime(ISocialWorld& world, ISocialEngine& engine);
    ~SocialRuntime();
    SocialRuntime(const SocialRuntime&) = delete;
    SocialRuntime& operator=(const SocialRuntime&) = delete;

    void frame(const FrameInfo& f);
    SocialSync* sync() { return sync_.get(); }
    const SocialSync* sync() const { return sync_.get(); }
    Config& config() { return cfg_; }       // set before the session starts
    uint64_t rebuilds() const { return rebuilds_; }
    uint64_t droppedUnknownPeer() const { return droppedUnknownPeer_; }

private:
    class MuxNet;
    class FnCommitter;
    void teardown();
    PlayerId slotOfPeer(fnvcoop::PeerId p) const;

    ISocialWorld& world_;
    ISocialEngine& engine_;
    Config cfg_;
    std::shared_ptr<bool> alive_;
    std::unique_ptr<MuxNet> net_;
    std::unique_ptr<FnCommitter> commit_;
    std::unique_ptr<SocialSync> sync_;
    fnvcoop::MuxTransport* mux_ = nullptr;
    bool isHost_ = false;
    PlayerId self_ = kNoPlayer;
    bool linkUp_ = false;
    std::vector<ClientLink> clients_;
    std::set<PlayerId> known_;
    uint64_t rebuilds_ = 0, droppedUnknownPeer_ = 0;
};

}  // namespace nvcoop::social
