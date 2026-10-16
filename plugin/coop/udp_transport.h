// UDP implementation of fnvcoop::ITransport (L10). Stand-in until L4's ENet transport lands; also the
// transport the in-game single-instance test uses (host in the game, fake client in a separate process).
//
//  * Server mode: bind(addr, port); every distinct sender (ip:port) gets a PeerId >= 1 on first valid datagram.
//  * Client mode: connect(host, port); the server is PeerId 0.
//  * Datagram = [0xFD][msgId u32 LE][idx u16 LE][count u16 LE][payload <= kFragment]. Messages larger than one
//    fragment are split and reassembled; a message with a lost fragment is dropped (the session retransmits).
//  * Non-blocking; poll() drains the socket. Main-thread only, no threads inside.
// Windows (Winsock2) only.
#pragma once
#include <chrono>
#include <cstdint>
#include <deque>
#include <map>
#include <memory>
#include <string>
#include <utility>
#include <vector>

#include "fnvcoop/transport.h"

namespace nvcoop::coop {

class UdpTransport : public fnvcoop::ITransport {
public:
    static constexpr size_t kFragment = 1100;     // payload bytes per datagram (safe under typical MTU)
    static constexpr size_t kMaxMessage = 1 << 20;
    static constexpr size_t kMaxPeers = 32;

    ~UdpTransport() override;
    UdpTransport(const UdpTransport&) = delete;
    UdpTransport& operator=(const UdpTransport&) = delete;

    // port 0 = ephemeral (see localPort()). nullptr + *err on failure.
    static std::unique_ptr<UdpTransport> Server(const std::string& bindAddr, uint16_t port, std::string* err);
    static std::unique_ptr<UdpTransport> Client(const std::string& host, uint16_t port, std::string* err);

    void send(fnvcoop::PeerId to, fnvcoop::Bytes data) override;
    bool poll(fnvcoop::PeerId& from, fnvcoop::Bytes& data) override;

    uint16_t localPort() const { return localPort_; }
    bool isServer() const { return server_; }
    size_t peerCount() const { return peers_.size(); }

    struct Stats {
        uint64_t datagramsSent = 0, datagramsRecv = 0, messagesSent = 0, messagesRecv = 0;
        uint64_t badDatagrams = 0, expiredReassemblies = 0, sendErrors = 0;
    };
    const Stats& stats() const { return stats_; }

    // Test hook: drop every N-th outgoing datagram (0 = off) to exercise loss handling.
    void setDropEveryNth(unsigned n) { dropEvery_ = n; }

private:
    UdpTransport() = default;
    struct Addr { uint32_t ip = 0; uint16_t port = 0; bool operator<(const Addr& o) const { return ip != o.ip ? ip < o.ip : port < o.port; } };
    struct Partial {
        uint16_t count = 0, got = 0;
        std::vector<fnvcoop::Bytes> parts;
        std::chrono::steady_clock::time_point first;
    };
    void drainSocket();
    void expire();
    fnvcoop::PeerId peerFor(const Addr& a, bool create);

    uintptr_t sock_ = ~uintptr_t(0);
    bool server_ = false;
    uint16_t localPort_ = 0;
    Addr serverAddr_;
    uint32_t nextMsg_ = 1;
    unsigned dropEvery_ = 0, dropCounter_ = 0;
    std::map<Addr, fnvcoop::PeerId> peers_;
    std::map<fnvcoop::PeerId, Addr> addrs_;
    fnvcoop::PeerId nextPeer_ = 1;
    std::map<std::pair<fnvcoop::PeerId, uint32_t>, Partial> partial_;
    std::deque<std::pair<fnvcoop::PeerId, fnvcoop::Bytes>> inbox_;
    Stats stats_;
};

}  // namespace nvcoop::coop
