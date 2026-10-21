#include "actors/actor_runtime.h"
#include <algorithm>
#include <set>

namespace nvcoop::actors {

namespace { ActorRuntime* g_active = nullptr; }
void setActive(ActorRuntime* rt) { g_active = rt; }
ActorRuntime* active() { return g_active; }

class ActorRuntime::MuxNet : public IActorNet {
public:
    fnvcoop::MuxTransport* mux = nullptr;
    bool isHost = false;
    std::vector<ClientLink> clients;
    void send(PlayerId peer, bool reliable, Bytes data) override {
        if (!mux) return;
        fnvcoop::PeerId to = 0;   // a client's only peer is the host
        if (isHost) {
            bool found = false;
            for (auto& c : clients) if (c.slot == peer && c.joined) { to = c.peer; found = true; break; }
            if (!found) return;
        }
        mux->sendEx(to, std::move(data), reliable ? fnvcoop::ITransport::Channel::Reliable : fnvcoop::ITransport::Channel::Unreliable);
    }
};

ActorRuntime::ActorRuntime(IActorPort& port) : port_(port), alive_(std::make_shared<bool>(true)) {}

ActorRuntime::~ActorRuntime() {
    *alive_ = false;
    if (g_active == this) g_active = nullptr;
}

PlayerId ActorRuntime::slotOfPeer(fnvcoop::PeerId p) const {
    if (!isHost_) return kHostPlayer;
    for (auto& c : clients_) if (c.peer == p && c.joined) return c.slot;
    return kNoPlayer;
}

void ActorRuntime::teardown() {
    sync_.reset();
    net_.reset();
    mux_ = nullptr;
    linkUp_ = false;
    clients_.clear();
    known_.clear();
}

void ActorRuntime::frame(const FrameInfo& f) {
    if (!f.active || !f.mux || (!f.isHost && f.self == kNoPlayer && !f.hostLinkUp)) {
        if (sync_) teardown();
        return;
    }
    if (!sync_ || f.mux != mux_ || f.isHost != isHost_ || (!f.isHost && f.self != self_ && f.self != kNoPlayer)) {
        teardown();   // a new session (or a re-slotted client): start from nothing
        ++rebuilds_;
        mux_ = f.mux; isHost_ = f.isHost; self_ = f.isHost ? kHostPlayer : f.self;
        net_.reset(new MuxNet());
        net_->mux = f.mux; net_->isHost = f.isHost;
        SyncConfig c = cfg_;
        c.self = self_; c.isHost = f.isHost;
        sync_.reset(new ActorSync(c, port_, *net_));
        std::weak_ptr<bool> alive = alive_;
        f.mux->registerChannel(kChannelActors, [this, alive](fnvcoop::PeerId from, Bytes&& data) {
            auto a = alive.lock();
            if (!a || !*a || !sync_) return;
            const PlayerId slot = slotOfPeer(from);
            if (slot == kNoPlayer) { ++droppedUnknownPeer_; return; }
            sync_->receive(slot, data.data(), data.size());
        });
    }
    net_->clients = f.clients;
    clients_ = f.clients;
    if (isHost_) {
        std::set<PlayerId> now;
        for (auto& c : f.clients) if (c.joined && c.slot != kNoPlayer) now.insert(c.slot);
        for (PlayerId p : now) if (!known_.count(p)) sync_->playerJoined(p);
        for (PlayerId p : known_) if (!now.count(p)) sync_->playerLeft(p);
        known_ = now;
    } else {
        if (f.hostLinkUp && !linkUp_) sync_->hostConnected();
        if (!f.hostLinkUp && linkUp_) sync_->hostLost();
        linkUp_ = f.hostLinkUp;
    }
    sync_->tick(f.nowMs);
}

}  // namespace nvcoop::actors
