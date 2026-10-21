#include "areadigest/area_runtime.h"

#include "fnvcoop_mut.h"

namespace nvcoop::areadigest {

using fnvcoop::PlayerId;
using fnvcoop::kHostPlayer;
using fnvcoop::kNoPlayer;
using fnvcoop::areadigest::AreaSync;
using fnvcoop::areadigest::IAreaNet;
using fnvcoop::areadigest::kChannelAreaDigest;

namespace {
AreaRuntime* g_active = nullptr;
}
void setActive(AreaRuntime* rt) { g_active = rt; }
AreaRuntime* active() { return g_active; }

class AreaRuntime::MuxNet : public IAreaNet {
public:
    fnvcoop::MuxTransport* mux = nullptr;
    bool isHost = false;
    std::vector<ClientLink> clients;
    void send(PlayerId peer, std::vector<uint8_t> data) override {
        if (!mux) return;
        fnvcoop::PeerId to = 0;   // a client's only peer is the host
        if (isHost) {
            bool found = false;
            for (auto& c : clients) if (c.slot == peer && c.joined) { to = c.peer; found = true; break; }
            if (!found) return;
        }
        mux->sendEx(to, std::move(data), fnvcoop::ITransport::Channel::Reliable);
    }
};

AreaRuntime::AreaRuntime(fnvcoop::areadigest::Config base) : cfg_(base), alive_(std::make_shared<bool>(true)) {}

AreaRuntime::~AreaRuntime() {
    *alive_ = false;
    if (g_active == this) g_active = nullptr;
}

void AreaRuntime::setSources(std::vector<fnvcoop::areadigest::IAreaSource*> sources) {
    sources_ = std::move(sources);
    if (sync_) for (auto* s : sources_) sync_->addSource(s);
}
void AreaRuntime::setStory(fnvcoop::areadigest::IStorySource* story) { story_ = story; }
void AreaRuntime::setObserver(fnvcoop::areadigest::IAreaObserver* obs) {
    obs_ = obs;
    if (sync_) sync_->setObserver(obs);
}

PlayerId AreaRuntime::slotOfPeer(fnvcoop::PeerId p) const {
    if (!isHost_) return kHostPlayer;
    for (auto& c : clients_) if (c.peer == p && c.joined) return c.slot;
    return kNoPlayer;
}

void AreaRuntime::teardown() {
    sync_.reset();
    net_.reset();
    mux_ = nullptr;
    linkUp_ = false;
    clients_.clear();
    known_.clear();
}

void AreaRuntime::frame(const FrameInfo& f) {
    if (!f.active || !f.mux || (!f.isHost && f.self == kNoPlayer && !f.hostLinkUp)) {
        if (sync_) teardown();
        return;
    }
    const bool newMux = FNV_MUT(77) ? false : f.mux != mux_;   // twin 77: a new session (new mux) keeps the old AreaSync and its dead channel handler
    if (!sync_ || newMux || f.isHost != isHost_ || (!f.isHost && f.self != self_ && f.self != kNoPlayer)) {
        teardown();   // a new session (or a re-slotted client): start from nothing
        ++rebuilds_;
        mux_ = f.mux; isHost_ = f.isHost; self_ = f.isHost ? kHostPlayer : f.self;
        net_.reset(new MuxNet());
        net_->mux = f.mux; net_->isHost = f.isHost;
        fnvcoop::areadigest::Config c = cfg_;
        c.self = self_; c.isHost = f.isHost;
        sync_.reset(new AreaSync(c, *net_, story_));
        for (auto* s : sources_) sync_->addSource(s);
        sync_->setObserver(obs_);
        std::weak_ptr<bool> alive = alive_;
        f.mux->registerChannel(kChannelAreaDigest, [this, alive](fnvcoop::PeerId from, fnvcoop::Bytes&& data) {
            auto a = alive.lock();
            if (!a || !*a || !sync_) return;
            const PlayerId slot = slotOfPeer(from);
            if (slot == kNoPlayer && !FNV_MUT(76)) { ++droppedUnknownPeer_; return; }   // twin 76: a datagram from an unknown peer is taken as the host's
            sync_->receive(slot == kNoPlayer ? kHostPlayer : slot, data.data(), data.size());
        });
    }
    net_->clients = f.clients;
    clients_ = f.clients;
    sync_->tick(f.nowMs);   // sets the clock first: joins below and the datagrams of this frame see the right time
    if (isHost_) {
        std::set<PlayerId> now;
        for (auto& c : f.clients) if (c.joined && c.slot != kNoPlayer) now.insert(c.slot);
        if (!FNV_MUT(79)) {   // twin 79: joins and leaves never reach AreaSync (no peer is ever compared)
            for (PlayerId p : now) if (!known_.count(p)) sync_->playerJoined(p);
            for (PlayerId p : known_) if (!now.count(p)) sync_->playerLeft(p);
        }
        known_ = now;
    } else {
        const bool up = FNV_MUT(78) ? true : f.hostLinkUp;   // twin 78: the host link state is ignored
        if (up && !linkUp_) sync_->hostConnected();
        if (!up && linkUp_) sync_->hostLost();
        linkUp_ = up;
    }
}

}  // namespace nvcoop::areadigest
