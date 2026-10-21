#include "spawn/spawn_runtime.h"

namespace nvcoop::spawn {

namespace { SpawnRuntime* g_active = nullptr; }
void setActive(SpawnRuntime* rt) { g_active = rt; }
SpawnRuntime* active() { return g_active; }

class SpawnRuntime::MuxNet : public ISpawnNet {
public:
    fnvcoop::MuxTransport* mux = nullptr;
    bool isHost = false;
    std::vector<ClientLink> clients;
    void send(PlayerId peer, Bytes data) override {
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

class SpawnRuntime::HostCommit : public ISpawnHost {
public:
    std::function<void(PlayerId, const fnvcoop::SpawnRef&)> fn;
    void commitSpawn(PlayerId origin, const fnvcoop::SpawnRef& s) override { if (fn) fn(origin, s); }
};

SpawnRuntime::SpawnRuntime(ISpawnEngine& eng, SyncConfig base) : eng_(eng), cfg_(base), alive_(std::make_shared<bool>(true)) {}

SpawnRuntime::~SpawnRuntime() {
    *alive_ = false;
    if (g_active == this) g_active = nullptr;
}

void SpawnRuntime::setIgnore(std::function<bool(RefId)> f) {
    ignore_ = std::move(f);
    if (sync_) sync_->setIgnore(ignore_);
}

PlayerId SpawnRuntime::slotOfPeer(fnvcoop::PeerId p) const {
    if (!isHost_) return kHostPlayer;
    for (auto& c : clients_) if (c.peer == p && c.joined) return c.slot;
    return kNoPlayer;
}

void SpawnRuntime::teardown() {
    sync_.reset();
    net_.reset();
    commit_.reset();
    mux_ = nullptr;
    linkUp_ = false;
    clients_.clear();
    known_.clear();
}

void SpawnRuntime::frame(const FrameInfo& f) {
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
        commit_.reset(new HostCommit());
        SyncConfig c = cfg_;
        c.self = self_; c.isHost = f.isHost;
        sync_.reset(new SpawnSync(c, eng_, *net_, f.isHost ? commit_.get() : nullptr));
        if (ignore_) sync_->setIgnore(ignore_);
        std::weak_ptr<bool> alive = alive_;
        f.mux->registerChannel(kChannelSpawn, [this, alive](fnvcoop::PeerId from, Bytes&& data) {
            auto a = alive.lock();
            if (!a || !*a || !sync_) return;
            const PlayerId slot = slotOfPeer(from);
            if (slot == kNoPlayer) { ++droppedUnknownPeer_; return; }
            sync_->receive(slot, data.data(), data.size());
        });
    }
    net_->clients = f.clients;
    commit_->fn = f.commit;
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
    sync_->tick(f.nowMs, f.gameMs);
}

}  // namespace nvcoop::spawn
