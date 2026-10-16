#include "fnvcoop/transport_util.h"

namespace fnvcoop {

bool MuxTransport::poll(PeerId& from, Bytes& data) {
    while (inner_.poll(from, data)) {
        if (data.empty()) { ++unroutable_; continue; }
        auto it = handlers_.find(data[0]);
        if (it == handlers_.end()) return true;
        it->second(from, std::move(data));
    }
    return false;
}

void FaultyTransport::enqueue(PeerId to, Bytes data, Channel ch) {
    uint64_t lat = link_.minLatencyMs;
    if (link_.maxLatencyMs > link_.minLatencyMs) lat = rng_.range(link_.minLatencyMs, link_.maxLatencyMs);
    q_.push(Msg{now_ + lat, order_++, to, std::move(data), ch});
}

void FaultyTransport::sendEx(PeerId to, Bytes data, Channel ch) {
    ++stats_.sent;
    if (link_.loss > 0 && rng_.chance(link_.loss)) { ++stats_.lost; return; }
    if (link_.corrupt > 0 && !data.empty() && rng_.chance(link_.corrupt)) {
        data[rng_.u32() % data.size()] ^= static_cast<uint8_t>(1u << (rng_.u32() % 8));
        ++stats_.corrupted;
    }
    if (link_.duplicate > 0 && rng_.chance(link_.duplicate)) { enqueue(to, data, ch); ++stats_.duplicated; }
    enqueue(to, std::move(data), ch);
}

void FaultyTransport::pump(uint64_t nowMs) {
    now_ = nowMs;
    while (!q_.empty() && q_.top().at <= now_) {
        Msg m = q_.top();
        q_.pop();
        inner_.sendEx(m.to, std::move(m.data), m.ch);
    }
}

}  // namespace fnvcoop
