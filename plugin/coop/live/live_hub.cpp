#include "live_hub.h"

#include <algorithm>
#include <exception>

#include "fnvcoop_mut.h"   // FNV_MUT(n): twins 11..22 of the live wiring suite (constant false in the shipped DLL)

namespace nvcoop::livewire {

class LiveHub::Link : public ILiveLink {
public:
    LiveHub* hub = nullptr;
    bool valid = true;
    bool send(PlayerId to, bool reliable, Bytes data) override {
        if (!valid || !hub || !hub->mux_ || data.empty()) { if (hub) ++hub->stats_.sendsFailed; return false; }
        fnvcoop::PeerId peer = 0;   // a client's only peer is the host
        if (hub->isHost_) {
            bool found = false;
            for (const auto& c : hub->clients_)
                if (c.slot == to && (c.joined || FNV_MUT(21))) { peer = c.peer; found = true; break; }   // MUTANT 21: unjoined slots are sendable
            if (!found) { ++hub->stats_.sendsFailed; return false; }
        }
        hub->mux_->sendEx(peer, std::move(data), reliable ? fnvcoop::ITransport::Channel::Reliable : fnvcoop::ITransport::Channel::Unreliable);
        ++hub->stats_.sendsOk;
        return true;
    }
    bool isHost() const override { return hub && hub->isHost_; }
    PlayerId self() const override { return hub ? (hub->isHost_ ? kHostPlayer : hub->self_) : kNoPlayer; }
};

// A bug in one lane must never take the game down or stop the others: an exception from a service switches that service off (counted, shown in
// coop.live) and the frame goes on. (C++ exceptions only; the lanes guard their raw engine reads with SEH themselves.)
void LiveHub::fault(Entry& e, const char* what) {
    ++stats_.serviceFaults;
    e.enabled = false;
    std::string msg = std::string(e.svc->name()) + ": " + (what ? what : "?");
    for (char& c : msg)   // it goes into coop.live as JSON text
        if (c == '"' || c == '\\') c = '\'';
        else if (static_cast<unsigned char>(c) < 0x20) c = ' ';
    e.fault = msg.substr(0, 200);
}

LiveHub::LiveHub() : liveSession_(std::make_shared<uint64_t>(0)) {}
LiveHub::~LiveHub() {
    if (inSession_) endSession();
    *liveSession_ = 0;
}

bool LiveHub::add(ILiveService* svc, bool enabled) {
    if (!svc) return false;
    for (const Entry& e : services_)
        if (e.svc == svc) return false;
    Entry n;
    n.svc = svc;
    n.enabled = enabled || FNV_MUT(11);   // MUTANT 11: a switched-off service is called anyway
    n.channels = svc->channels();
    if (!FNV_MUT(17)) {   // MUTANT 17: two services may claim the same channel byte (the second one's traffic goes to the first)
        for (const Entry& e : services_)
            for (uint8_t a : e.channels)
                for (uint8_t b : n.channels)
                    if (a == b) return false;
    }
    services_.push_back(n);
    return true;
}

size_t LiveHub::enabledCount() const {
    size_t n = 0;
    for (const Entry& e : services_) n += e.enabled ? 1 : 0;
    return n;
}

PlayerId LiveHub::slotOfPeer(fnvcoop::PeerId p) const {
    if (!isHost_) return kHostPlayer;
    for (const auto& c : clients_)
        if (c.peer == p && (c.joined || FNV_MUT(15))) return c.slot;   // MUTANT 15: datagrams from peers that have not joined are accepted
    return kNoPlayer;
}

void LiveHub::route(uint64_t sessionId, fnvcoop::PeerId from, Bytes&& data) {
    if (!FNV_MUT(18) && (*liveSession_ != sessionId || !inSession_)) {   // MUTANT 18: handlers of an ended session still deliver
        ++stats_.droppedSessionOver;
        return;
    }
    const PlayerId slot = slotOfPeer(from);
    if (slot == kNoPlayer) { ++stats_.droppedUnknownPeer; return; }
    if (data.empty()) { ++stats_.droppedNoService; return; }
    for (Entry& e : services_) {
        if (!e.enabled || !e.sessionOpen) continue;
        if (std::find(e.channels.begin(), e.channels.end(), data[0]) == e.channels.end()) continue;
        ++stats_.datagramsRouted;
        safe(e, [&] { e.svc->receive(slot, data.data(), data.size()); });
        return;
    }
    ++stats_.droppedNoService;
}

void LiveHub::beginSession(const SessionView& v) {
    ++sessionSeq_;
    ++stats_.sessionsBegun;
    *liveSession_ = sessionSeq_;
    inSession_ = true;
    mux_ = v.mux;
    isHost_ = v.isHost;
    self_ = v.isHost ? kHostPlayer : v.self;
    known_.clear();
    if (!FNV_MUT(20)) linkUp_ = false;   // MUTANT 20: a new session inherits the old link state (services never hear hostLinkUp again)
    clients_ = v.clients;
    link_.reset(new Link());
    link_->hub = this;
    const uint64_t id = sessionSeq_;
    std::weak_ptr<uint64_t> alive = liveSession_;
    SessionInfo info;
    info.isHost = isHost_;
    info.self = self_;
    for (Entry& e : services_) {
        if (!e.enabled) continue;
        for (uint8_t ch : e.channels) {
            mux_->registerChannel(ch, [this, id, alive](fnvcoop::PeerId from, Bytes&& d) {
                auto t = alive.lock();
                if (!t) return;   // the hub is gone
                route(id, from, std::move(d));
            });
        }
        e.sessionOpen = true;
        safe(e, [&] { e.svc->sessionBegin(info, *link_); });
        if (!e.enabled) e.sessionOpen = false;   // it threw: its session never opened
    }
}

void LiveHub::endSession() {
    for (auto it = services_.rbegin(); it != services_.rend(); ++it) {
        if (!it->sessionOpen) continue;
        it->sessionOpen = false;
        safe(*it, [&] { it->svc->sessionEnd(); });
    }
    if (link_) link_->valid = false;
    link_.reset();
    *liveSession_ = 0;
    inSession_ = false;
    ++stats_.sessionsEnded;
    mux_ = nullptr;
    known_.clear();
    clients_.clear();
}

void LiveHub::frame(const SessionView& v) {
    ++stats_.frames;
    const bool want = v.active && v.mux && (v.isHost || v.self != kNoPlayer || v.hostLinkUp);
    if (!want) {
        if (inSession_) endSession();
        for (Entry& e : services_) {
            if (!e.enabled) continue;
            if (FNV_MUT(22)) safe(e, [&] { e.svc->tick(v.nowMs); });   // MUTANT 22: services get the session tick with no session
            else safe(e, [&] { e.svc->idleTick(v.nowMs); });
        }
        return;
    }
    if (inSession_) {
        const bool reslotted = !v.isHost && !FNV_MUT(19) && v.self != kNoPlayer && v.self != self_;   // MUTANT 19: a re-slotted client keeps its old session
        if (FNV_MUT(12) ? false : (v.mux != mux_ || v.isHost != isHost_ || reslotted)) endSession();   // MUTANT 12: a new mux object is not a new session
    }
    if (!inSession_) beginSession(v);
    clients_ = v.clients;
    if (isHost_) {
        std::set<PlayerId> now;
        for (const auto& c : v.clients)
            if (c.joined && c.slot != kNoPlayer) now.insert(c.slot);
        for (PlayerId p : now)
            if (!known_.count(p)) {
                if (!FNV_MUT(13)) known_.insert(p);   // MUTANT 13: the roster is not remembered (the same join is announced every frame)
                ++stats_.joins;
                for (Entry& e : services_) if (e.enabled && e.sessionOpen) safe(e, [&] { e.svc->playerJoined(p); });
            }
        std::vector<PlayerId> gone;
        for (PlayerId p : known_)
            if (!now.count(p)) gone.push_back(p);
        for (PlayerId p : gone) {
            known_.erase(p);
            ++stats_.leaves;
            if (FNV_MUT(14)) continue;   // MUTANT 14: a leave is never announced
            for (auto it = services_.rbegin(); it != services_.rend(); ++it) if (it->enabled && it->sessionOpen) safe(*it, [&] { it->svc->playerLeft(p); });
        }
    } else {
        if (v.hostLinkUp && !linkUp_) { ++stats_.linkUps; for (Entry& e : services_) if (e.enabled && e.sessionOpen) safe(e, [&] { e.svc->hostLinkUp(); }); }
        if (!v.hostLinkUp && linkUp_) { ++stats_.linkDowns; for (Entry& e : services_) if (e.enabled && e.sessionOpen) safe(e, [&] { e.svc->hostLinkDown(); }); }
        linkUp_ = v.hostLinkUp;
    }
    for (Entry& e : services_)
        if (e.enabled && e.sessionOpen) safe(e, [&] { e.svc->tick(v.nowMs); });
}

void LiveHub::onLoadBegin() {
    ++stats_.loads;
    for (Entry& e : services_)
        if (e.enabled) safe(e, [&] { e.svc->loadBegin(); });
}

void LiveHub::onLoadEnd() {
    for (Entry& e : services_) {
        if (!e.enabled) continue;
        if (FNV_MUT(16) && !e.sessionOpen) continue;   // MUTANT 16: the post-load purge only runs inside a coop session (stand-ins / puppets survive a plain load)
        safe(e, [&] { e.svc->loadEnd(); });
    }
}

void LiveHub::onSaved() {
    for (Entry& e : services_)
        if (e.enabled) safe(e, [&] { e.svc->saved(); });
}

void LiveHub::onGameExit() {
    if (inSession_) endSession();
    for (Entry& e : services_)
        if (e.enabled) safe(e, [&] { e.svc->gameExit(); });
}

std::string LiveHub::statusJson() const {
    std::string s = "{\"inSession\":";
    s += inSession_ ? "true" : "false";
    s += ",\"role\":\"";
    s += !inSession_ ? "none" : isHost_ ? "host" : "client";
    s += "\",\"services\":[";
    bool first = true;
    for (const Entry& e : services_) {
        if (!first) s += ",";
        first = false;
        s += "{\"name\":\"";
        s += e.svc->name();
        s += "\",\"enabled\":";
        s += e.enabled ? "true" : "false";
        s += ",\"sessionOpen\":";
        s += e.sessionOpen ? "true" : "false";
        if (!e.fault.empty()) s += ",\"fault\":\"" + e.fault + "\"";
        s += "}";
    }
    s += "],\"frames\":" + std::to_string(stats_.frames) + ",\"sessions\":" + std::to_string(stats_.sessionsBegun) +
         ",\"joins\":" + std::to_string(stats_.joins) + ",\"leaves\":" + std::to_string(stats_.leaves) +
         ",\"routed\":" + std::to_string(stats_.datagramsRouted) + ",\"droppedUnknownPeer\":" + std::to_string(stats_.droppedUnknownPeer) +
         ",\"droppedNoService\":" + std::to_string(stats_.droppedNoService) + ",\"sendsOk\":" + std::to_string(stats_.sendsOk) +
         ",\"sendsFailed\":" + std::to_string(stats_.sendsFailed) + ",\"loads\":" + std::to_string(stats_.loads) + ",\"serviceFaults\":" + std::to_string(stats_.serviceFaults) + "}";
    return s;
}

}  // namespace nvcoop::livewire
