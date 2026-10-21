#include "vats/vats_runtime.h"

#include "../../tools/mut/fnvcoop_mut.h"

namespace nvcoop::vats {

class VatsRuntime::MuxNet : public IVatsNet {
public:
    fnvcoop::MuxTransport* mux = nullptr;
    bool isHost = false;
    std::vector<ClientLink> clients;
    void send(PlayerId peer, Bytes data) override {
        if (!mux) return;
        if (!isHost) {   // a client's only peer is the host
            mux->sendEx(0, std::move(data), fnvcoop::ITransport::Channel::Reliable);
            return;
        }
        if (peer == kNoPlayer) {
            for (auto& c : clients) if (c.joined) mux->sendEx(c.peer, data, fnvcoop::ITransport::Channel::Reliable);
            return;
        }
        for (auto& c : clients) {
            if (c.slot == peer && c.joined) { mux->sendEx(c.peer, std::move(data), fnvcoop::ITransport::Channel::Reliable); return; }
        }
    }
};

VatsRuntime::VatsRuntime(IVatsEngine& engine, const Config& baseCfg) : engine_(engine), base_(baseCfg), alive_(std::make_shared<bool>(true)) {}

VatsRuntime::~VatsRuntime() {
    *alive_ = false;
    teardown();
}

PlayerId VatsRuntime::slotOfPeer(fnvcoop::PeerId p) const {
    if (!isHost_) return kHostPlayer;
    for (auto& c : clients_) if (c.peer == p && c.joined) return c.slot;
    return kNoPlayer;
}

void VatsRuntime::teardown() {
    if (sync_) sync_->shutdown();   // the time scale and the camera settings go back to normal
    sync_.reset();
    net_.reset();
    mux_ = nullptr;
    linkUp_ = false;
    clients_.clear();
    known_.clear();
}

void VatsRuntime::frame(const FrameInfo& f) {
    nowMs_ = f.nowMs;
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
        Config c = base_;
        c.self = self_; c.isHost = f.isHost;
        c.epoch = ++epoch_;
        sync_.reset(new VatsSync(c, engine_, *net_));
        std::weak_ptr<bool> alive = alive_;
        f.mux->registerChannel(kChannelVats, [this, alive](fnvcoop::PeerId from, Bytes&& data) {
            auto a = alive.lock();
            if (!a || !*a || !sync_) return;
            const PlayerId slot = slotOfPeer(from);
            if (slot == kNoPlayer) { ++droppedUnknownPeer_; return; }
            sync_->receive(slot, data.data(), data.size(), nowMs_);
        });
    }
    net_->clients = f.clients;
    clients_ = f.clients;
    if (isHost_) {
        std::set<PlayerId> now;
        for (auto& c : f.clients) if (c.joined && c.slot != kNoPlayer) now.insert(c.slot);
        for (PlayerId p : now) if (!known_.count(p)) sync_->playerJoined(p, f.nowMs);
        for (PlayerId p : known_) if (!now.count(p)) sync_->playerLeft(p, f.nowMs);
        known_ = now;
    } else {
        if (!f.hostLinkUp && linkUp_) sync_->hostLost();
        linkUp_ = f.hostLinkUp;
    }
    sync_->tick(f.nowMs);
}

}  // namespace nvcoop::vats
