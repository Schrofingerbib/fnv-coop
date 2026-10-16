#include "fnvcoop/transport.h"
#include <algorithm>
#include <deque>

namespace fnvcoop {

class InMemoryNetwork::Endpoint : public ITransport {
public:
    Endpoint(InMemoryNetwork& n, PeerId id) : net_(n), id_(id) {}
    void send(PeerId to, Bytes data) override { net_.sendFrom(id_, to, std::move(data)); }
    size_t backlogBytes(PeerId to) const override { return static_cast<size_t>(net_.queuedBytes(id_, to)); }
    bool poll(PeerId& from, Bytes& data) override {
        if (inbox.empty()) return false;
        from = inbox.front().first;
        data = std::move(inbox.front().second);
        inbox.pop_front();
        return true;
    }
    std::deque<std::pair<PeerId, Bytes>> inbox;
private:
    InMemoryNetwork& net_;
    PeerId id_;
};

InMemoryNetwork::InMemoryNetwork(uint64_t seed) : rng_(seed) {}
InMemoryNetwork::~InMemoryNetwork() = default;

ITransport* InMemoryNetwork::endpoint(PeerId id) {
    auto& p = eps_[id];
    if (!p) { p = std::make_unique<Endpoint>(*this, id); up_[id] = true; }
    return p.get();
}

uint64_t InMemoryNetwork::queuedBytes(PeerId from, PeerId to) const {
    auto it = queued_.find({from, to});
    return it == queued_.end() ? 0 : it->second;
}

bool InMemoryNetwork::connected(PeerId id) const {
    auto it = up_.find(id);
    return it != up_.end() && it->second;
}

void InMemoryNetwork::setConnected(PeerId id, bool up) {
    up_[id] = up;
    if (up) return;
    // Drop everything queued to/from the peer, including the unread inbox.
    std::vector<Msg> keep;
    while (!pending_.empty()) {
        Msg m = pending_.top();
        pending_.pop();
        if (m.from == id || m.to == id) { ++stats_.droppedDown; queued_[{m.from, m.to}] -= std::min<uint64_t>(queued_[{m.from, m.to}], m.data.size()); }
        else keep.push_back(std::move(m));
    }
    for (auto& m : keep) pending_.push(std::move(m));
    auto it = eps_.find(id);
    if (it != eps_.end()) it->second->inbox.clear();
}

void InMemoryNetwork::enqueue(PeerId from, PeerId to, Bytes data) {
    uint64_t lat = link_.minLatencyMs;
    if (link_.maxLatencyMs > link_.minLatencyMs) lat = rng_.range(link_.minLatencyMs, link_.maxLatencyMs);
    uint64_t at = now_ + lat;
    if (bandwidth_) {   // serialization delay on a shared pipe: big messages block what is behind them
        uint64_t start = std::max<uint64_t>(now_, busyUntil_[{from, to}]);
        uint64_t ser = (uint64_t(data.size()) * 1000 + bandwidth_ - 1) / bandwidth_;
        busyUntil_[{from, to}] = start + ser;
        at = start + ser + lat;
    }
    queued_[{from, to}] += data.size();
    pending_.push(Msg{at, order_++, from, to, std::move(data)});
}

void InMemoryNetwork::sendFrom(PeerId from, PeerId to, Bytes data) {
    ++stats_.sent;
    stats_.bytesSent += data.size();
    if (!connected(from) || !connected(to)) { ++stats_.droppedDown; return; }
    sentBytes_[{from, to}] += data.size();   // offered load, before any loss
    if (link_.loss > 0 && rng_.chance(link_.loss)) { ++stats_.lost; return; }
    if (link_.corrupt > 0 && !data.empty() && rng_.chance(link_.corrupt)) {
        data[rng_.u32() % data.size()] ^= static_cast<uint8_t>(1u << (rng_.u32() % 8));
        ++stats_.corrupted;
    }
    if (link_.duplicate > 0 && rng_.chance(link_.duplicate)) { enqueue(from, to, data); ++stats_.duplicated; }
    enqueue(from, to, std::move(data));
}

void InMemoryNetwork::advance(uint64_t nowMs) {
    now_ = nowMs;
    while (!pending_.empty() && pending_.top().at <= now_) {
        Msg m = pending_.top();
        pending_.pop();
        queued_[{m.from, m.to}] -= std::min<uint64_t>(queued_[{m.from, m.to}], m.data.size());
        if (!connected(m.to) || !connected(m.from)) { ++stats_.droppedDown; continue; }
        auto it = eps_.find(m.to);
        if (it == eps_.end()) continue;
        it->second->inbox.emplace_back(m.from, std::move(m.data));
        ++stats_.delivered;
    }
}

bool InMemoryNetwork::idle() const {
    if (!pending_.empty()) return false;
    for (auto& [id, ep] : eps_) if (!ep->inbox.empty()) return false;
    return true;
}

}  // namespace fnvcoop
