// ENet-backed ITransport (UDP). Channel 0 = reliable ordered, channel 1 = unreliable. Link with fnvcoop_enet.
// PeerId mapping: a client sees its server as peer 0; a server numbers clients by ENet peer slot + 1.
#pragma once
#include <deque>
#include <string>
#include <vector>
#include "fnvcoop/transport.h"

struct _ENetHost;
struct _ENetPeer;

namespace fnvcoop {

class EnetTransport : public ITransport {
public:
    struct Event { enum Type { Connected, Disconnected } type; PeerId peer; };

    EnetTransport() = default;
    ~EnetTransport() override;
    EnetTransport(const EnetTransport&) = delete;
    EnetTransport& operator=(const EnetTransport&) = delete;

    // Server: bind `port` (0 = any; see port()), up to maxPeers clients. bindAny=false binds 127.0.0.1 only.
    bool listen(uint16_t port, size_t maxPeers = 8, bool loopbackOnly = false);
    // Client: begin connecting to host:port (asynchronous; watch for the Connected event).
    bool connect(const std::string& host, uint16_t port);
    void disconnect();  // graceful for all peers

    void send(PeerId to, Bytes data) override { sendEx(to, std::move(data), Channel::Reliable); }
    void sendEx(PeerId to, Bytes data, Channel ch) override;
    bool poll(PeerId& from, Bytes& data) override;  // services the socket
    bool nextEvent(Event& e);
    size_t backlogBytes(PeerId to) const override;

    uint16_t port() const { return port_; }
    bool isServer() const { return server_; }
    bool connected() const { return connectedPeers_ > 0; }
    struct Stats { uint64_t datagramsIn = 0, datagramsOut = 0, bytesIn = 0, bytesOut = 0; };
    const Stats& stats() const { return stats_; }

private:
    void service();
    _ENetPeer* peerFor(PeerId id) const;
    PeerId idOf(const _ENetPeer* p) const;

    _ENetHost* host_ = nullptr;
    bool server_ = false;
    uint16_t port_ = 0;
    int connectedPeers_ = 0;
    std::deque<std::pair<PeerId, Bytes>> inbox_;
    std::deque<Event> events_;
    Stats stats_;
};

}  // namespace fnvcoop
