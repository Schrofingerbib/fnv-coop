// AreaRuntime: owns the AreaSync of the running co-op session and plugs it into the coop runtime's MuxTransport (first byte kChannelAreaDigest).
// Game-free, the same shape as plugin/spawn SpawnRuntime: the glue (or a test) calls frame() every frame with the facts it knows; this class
// builds / drops the AreaSync when the session starts / ends, maps slots to transport peers, forwards datagrams and reports joins / leaves.
#pragma once
#include <memory>
#include <set>
#include <vector>
#include "areadigest/areadigest.h"
#include "fnvcoop/transport_util.h"

namespace nvcoop::areadigest {

struct ClientLink { fnvcoop::PlayerId slot = fnvcoop::kNoPlayer; fnvcoop::PeerId peer = 0; bool joined = false; };

struct FrameInfo {
    bool active = false;                          // the coop session is running
    bool isHost = false;
    fnvcoop::PlayerId self = fnvcoop::kNoPlayer;  // client: slot assigned by the host (kNoPlayer until joined)
    bool hostLinkUp = false;                      // client: joined and not rebaselining
    std::vector<ClientLink> clients;              // host: clients known to the session
    fnvcoop::MuxTransport* mux = nullptr;         // the session's mux; a new pointer = a new session
    uint64_t nowMs = 0;
};

class AreaRuntime {
public:
    explicit AreaRuntime(fnvcoop::areadigest::Config base = {});
    ~AreaRuntime();
    AreaRuntime(const AreaRuntime&) = delete;
    AreaRuntime& operator=(const AreaRuntime&) = delete;

    // Sources, story hook and observer survive session rebuilds. They are not owned. Set them before the first frame().
    void setSources(std::vector<fnvcoop::areadigest::IAreaSource*> sources);
    void setStory(fnvcoop::areadigest::IStorySource* story);
    void setObserver(fnvcoop::areadigest::IAreaObserver* obs);

    void frame(const FrameInfo& f);
    fnvcoop::areadigest::AreaSync* sync() { return sync_.get(); }
    const fnvcoop::areadigest::AreaSync* sync() const { return sync_.get(); }
    fnvcoop::areadigest::Config& config() { return cfg_; }       // set before the session starts
    uint64_t rebuilds() const { return rebuilds_; }
    uint64_t droppedUnknownPeer() const { return droppedUnknownPeer_; }

private:
    class MuxNet;
    void teardown();
    fnvcoop::PlayerId slotOfPeer(fnvcoop::PeerId p) const;

    fnvcoop::areadigest::Config cfg_;
    std::vector<fnvcoop::areadigest::IAreaSource*> sources_;
    fnvcoop::areadigest::IStorySource* story_ = nullptr;
    fnvcoop::areadigest::IAreaObserver* obs_ = nullptr;
    std::shared_ptr<bool> alive_;
    std::unique_ptr<MuxNet> net_;
    std::unique_ptr<fnvcoop::areadigest::AreaSync> sync_;
    fnvcoop::MuxTransport* mux_ = nullptr;
    bool isHost_ = false;
    fnvcoop::PlayerId self_ = fnvcoop::kNoPlayer;
    bool linkUp_ = false;
    std::vector<ClientLink> clients_;
    std::set<fnvcoop::PlayerId> known_;           // host: clients announced to AreaSync as joined
    uint64_t rebuilds_ = 0, droppedUnknownPeer_ = 0;
};

// The one runtime the bridge method (areadigest.status) looks at; set by the glue while a session runs.
void setActive(AreaRuntime* rt);
AreaRuntime* active();

}  // namespace nvcoop::areadigest
