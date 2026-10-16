// Transport decorators: first-byte multiplexer (channel registry) and a seeded fault injector.
#pragma once
#include <functional>
#include <map>
#include <queue>
#include "fnvcoop/transport.h"

namespace fnvcoop {

// ---- Channel registry (first byte of every datagram) -------------------------------------------------------
// 0xFC  story/session envelope (this library: Hello/Welcome/TxnBatch/Ack/Intent/IntentAck/AppData)
// 0xD1  dialogue adapter (plugin/dialogue, L8)
// Everything else is reserved; new users claim a byte here. Datagrams whose first byte has a registered handler are
// consumed by that handler; all others (including 0xFC) are returned from poll() to the session.
constexpr uint8_t kChannelStory = 0xFC;
constexpr uint8_t kChannelDialogue = 0xD1;

class MuxTransport : public ITransport {
public:
    using Handler = std::function<void(PeerId from, Bytes&& data)>;
    explicit MuxTransport(ITransport& inner) : inner_(inner) {}
    void registerChannel(uint8_t firstByte, Handler h) { handlers_[firstByte] = std::move(h); }
    void send(PeerId to, Bytes data) override { inner_.send(to, std::move(data)); }
    void sendEx(PeerId to, Bytes data, Channel ch) override { inner_.sendEx(to, std::move(data), ch); }
    bool poll(PeerId& from, Bytes& data) override;
    uint64_t unroutable() const { return unroutable_; }  // empty datagrams dropped

private:
    ITransport& inner_;
    std::map<uint8_t, Handler> handlers_;
    uint64_t unroutable_ = 0;
};

// Wraps any transport and applies LinkParams (latency, jitter/reorder, loss, duplicate, bit-flip) on the SEND side
// with a seeded RNG. Wrap both ends to fault both directions. Call pump(nowMs) every tick.
class FaultyTransport : public ITransport {
public:
    FaultyTransport(ITransport& inner, const LinkParams& p, uint64_t seed) : inner_(inner), link_(p), rng_(seed) {}
    void setLink(const LinkParams& p) { link_ = p; }
    void send(PeerId to, Bytes data) override { sendEx(to, std::move(data), Channel::Reliable); }
    void sendEx(PeerId to, Bytes data, Channel ch) override;
    bool poll(PeerId& from, Bytes& data) override { return inner_.poll(from, data); }
    void pump(uint64_t nowMs);
    size_t inFlight() const { return q_.size(); }
    struct Stats { uint64_t sent = 0, lost = 0, duplicated = 0, corrupted = 0; };
    const Stats& stats() const { return stats_; }

private:
    struct Msg {
        uint64_t at, order; PeerId to; Bytes data; Channel ch;
        bool operator>(const Msg& o) const { return at != o.at ? at > o.at : order > o.order; }
    };
    void enqueue(PeerId to, Bytes data, Channel ch);
    ITransport& inner_;
    LinkParams link_;
    Rng rng_;
    uint64_t now_ = 0, order_ = 0;
    std::priority_queue<Msg, std::vector<Msg>, std::greater<Msg>> q_;
    Stats stats_;
};

}  // namespace fnvcoop
