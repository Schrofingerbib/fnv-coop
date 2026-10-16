#include "udp_transport.h"

#include <winsock2.h>
#include <ws2tcpip.h>
#include <mstcpip.h>

#ifndef SIO_UDP_CONNRESET
#define SIO_UDP_CONNRESET _WSAIOW(IOC_VENDOR, 12)
#endif

#include <algorithm>
#include <cstring>

namespace nvcoop::coop {
using fnvcoop::Bytes;
using fnvcoop::PeerId;

namespace {
constexpr uint8_t kMagic = 0xFD;
constexpr size_t kHeader = 9;
constexpr auto kReassemblyTtl = std::chrono::seconds(5);

struct WsaInit {
    bool ok = false;
    WsaInit() { WSADATA d; ok = WSAStartup(MAKEWORD(2, 2), &d) == 0; }
    ~WsaInit() { if (ok) WSACleanup(); }
};
WsaInit& wsa() { static WsaInit w; return w; }

std::string wsaErr(const char* what) { return std::string(what) + " failed: WSA " + std::to_string(WSAGetLastError()); }

bool resolve(const std::string& host, uint16_t port, sockaddr_in* out, std::string* err) {
    std::memset(out, 0, sizeof *out);
    out->sin_family = AF_INET;
    out->sin_port = htons(port);
    if (inet_pton(AF_INET, host.c_str(), &out->sin_addr) == 1) return true;
    addrinfo hints{}, *res = nullptr;
    hints.ai_family = AF_INET;
    hints.ai_socktype = SOCK_DGRAM;
    if (getaddrinfo(host.c_str(), nullptr, &hints, &res) != 0 || !res) {
        if (err) *err = "cannot resolve '" + host + "'";
        return false;
    }
    out->sin_addr = reinterpret_cast<sockaddr_in*>(res->ai_addr)->sin_addr;
    freeaddrinfo(res);
    return true;
}

bool openSocket(uintptr_t* s, const sockaddr_in& bindTo, uint16_t* localPort, std::string* err) {
    if (!wsa().ok) { if (err) *err = "WSAStartup failed"; return false; }
    SOCKET sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (sock == INVALID_SOCKET) { if (err) *err = wsaErr("socket"); return false; }
    u_long nb = 1;
    ioctlsocket(sock, FIONBIO, &nb);
    BOOL off = FALSE;  // do not surface ICMP port-unreachable as WSAECONNRESET on recvfrom
    DWORD ret = 0;
    WSAIoctl(sock, SIO_UDP_CONNRESET, &off, sizeof off, nullptr, 0, &ret, nullptr, nullptr);
    int buf = 1 << 20;
    setsockopt(sock, SOL_SOCKET, SO_RCVBUF, reinterpret_cast<const char*>(&buf), sizeof buf);
    if (bind(sock, reinterpret_cast<const sockaddr*>(&bindTo), sizeof bindTo) != 0) {
        if (err) *err = wsaErr("bind");
        closesocket(sock);
        return false;
    }
    sockaddr_in got{};
    int len = sizeof got;
    getsockname(sock, reinterpret_cast<sockaddr*>(&got), &len);
    *localPort = ntohs(got.sin_port);
    *s = static_cast<uintptr_t>(sock);
    return true;
}

void put16(uint8_t* p, uint16_t v) { p[0] = uint8_t(v); p[1] = uint8_t(v >> 8); }
void put32(uint8_t* p, uint32_t v) { for (int i = 0; i < 4; ++i) p[i] = uint8_t(v >> (8 * i)); }
uint16_t get16(const uint8_t* p) { return uint16_t(p[0] | (p[1] << 8)); }
uint32_t get32(const uint8_t* p) { return uint32_t(p[0]) | (uint32_t(p[1]) << 8) | (uint32_t(p[2]) << 16) | (uint32_t(p[3]) << 24); }
}  // namespace

UdpTransport::~UdpTransport() {
    if (sock_ != ~uintptr_t(0)) closesocket(static_cast<SOCKET>(sock_));
}

std::unique_ptr<UdpTransport> UdpTransport::Server(const std::string& bindAddr, uint16_t port, std::string* err) {
    sockaddr_in a{};
    if (!resolve(bindAddr, port, &a, err)) return nullptr;
    std::unique_ptr<UdpTransport> t(new UdpTransport());
    if (!openSocket(&t->sock_, a, &t->localPort_, err)) return nullptr;
    t->server_ = true;
    return t;
}

std::unique_ptr<UdpTransport> UdpTransport::Client(const std::string& host, uint16_t port, std::string* err) {
    sockaddr_in to{}, any{};
    if (!resolve(host, port, &to, err)) return nullptr;
    any.sin_family = AF_INET;
    any.sin_addr.s_addr = htonl(INADDR_ANY);
    std::unique_ptr<UdpTransport> t(new UdpTransport());
    if (!openSocket(&t->sock_, any, &t->localPort_, err)) return nullptr;
    t->server_ = false;
    t->serverAddr_ = {to.sin_addr.s_addr, to.sin_port};  // network byte order kept as-is (only compared)
    t->peers_[t->serverAddr_] = 0;
    t->addrs_[0] = t->serverAddr_;
    return t;
}

PeerId UdpTransport::peerFor(const Addr& a, bool create) {
    auto it = peers_.find(a);
    if (it != peers_.end()) return it->second;
    if (!create || !server_ || peers_.size() >= kMaxPeers) return ~PeerId(0);
    PeerId id = nextPeer_++;
    peers_[a] = id;
    addrs_[id] = a;
    return id;
}

void UdpTransport::send(PeerId to, Bytes data) {
    auto it = addrs_.find(to);
    if (it == addrs_.end() || data.size() > kMaxMessage) { ++stats_.sendErrors; return; }
    sockaddr_in dst{};
    dst.sin_family = AF_INET;
    dst.sin_addr.s_addr = it->second.ip;
    dst.sin_port = it->second.port;
    size_t count = std::max<size_t>(1, (data.size() + kFragment - 1) / kFragment);
    uint32_t id = nextMsg_++;
    ++stats_.messagesSent;
    Bytes dgram;
    for (size_t i = 0; i < count; ++i) {
        size_t off = i * kFragment, n = std::min(kFragment, data.size() - off);
        dgram.assign(kHeader + n, 0);
        dgram[0] = kMagic;
        put32(&dgram[1], id);
        put16(&dgram[5], static_cast<uint16_t>(i));
        put16(&dgram[7], static_cast<uint16_t>(count));
        if (n) std::memcpy(&dgram[kHeader], data.data() + off, n);
        if (dropEvery_ && ++dropCounter_ % dropEvery_ == 0) continue;
        int r = sendto(static_cast<SOCKET>(sock_), reinterpret_cast<const char*>(dgram.data()), static_cast<int>(dgram.size()), 0,
                       reinterpret_cast<const sockaddr*>(&dst), sizeof dst);
        if (r < 0) ++stats_.sendErrors; else ++stats_.datagramsSent;
    }
}

void UdpTransport::drainSocket() {
    uint8_t buf[2048];
    for (int guard = 0; guard < 4096; ++guard) {
        sockaddr_in from{};
        int flen = sizeof from;
        int n = recvfrom(static_cast<SOCKET>(sock_), reinterpret_cast<char*>(buf), sizeof buf, 0, reinterpret_cast<sockaddr*>(&from), &flen);
        if (n < 0) {
            int e = WSAGetLastError();
            if (e == WSAEWOULDBLOCK) break;
            if (e == WSAECONNRESET || e == WSAEMSGSIZE) continue;
            break;
        }
        ++stats_.datagramsRecv;
        if (n < static_cast<int>(kHeader) || buf[0] != kMagic) { ++stats_.badDatagrams; continue; }
        uint32_t id = get32(buf + 1);
        uint16_t idx = get16(buf + 5), count = get16(buf + 7);
        size_t plen = static_cast<size_t>(n) - kHeader;
        if (count == 0 || idx >= count || plen > kFragment || size_t(count) * kFragment > kMaxMessage + kFragment) { ++stats_.badDatagrams; continue; }
        Addr a{from.sin_addr.s_addr, from.sin_port};
        PeerId peer = peerFor(a, true);
        if (peer == ~PeerId(0)) { ++stats_.badDatagrams; continue; }
        if (count == 1) {
            inbox_.emplace_back(peer, Bytes(buf + kHeader, buf + n));
            ++stats_.messagesRecv;
            continue;
        }
        Partial& p = partial_[{peer, id}];
        if (p.count == 0) { p.count = count; p.parts.resize(count); p.first = std::chrono::steady_clock::now(); }
        if (p.count != count) { ++stats_.badDatagrams; continue; }
        if (p.parts[idx].empty()) {
            p.parts[idx].assign(buf + kHeader, buf + n);
            if (p.parts[idx].empty()) p.parts[idx].push_back(0);  // cannot happen (non-last frags are full); keep "got" accurate
            ++p.got;
        }
        if (p.got == p.count) {
            Bytes whole;
            for (auto& part : p.parts) whole.insert(whole.end(), part.begin(), part.end());
            partial_.erase({peer, id});
            inbox_.emplace_back(peer, std::move(whole));
            ++stats_.messagesRecv;
        }
    }
}

void UdpTransport::expire() {
    if (partial_.empty()) return;
    auto now = std::chrono::steady_clock::now();
    for (auto it = partial_.begin(); it != partial_.end();) {
        if (now - it->second.first > kReassemblyTtl) { it = partial_.erase(it); ++stats_.expiredReassemblies; }
        else ++it;
    }
}

bool UdpTransport::poll(PeerId& from, Bytes& data) {
    if (inbox_.empty()) { drainSocket(); expire(); }
    if (inbox_.empty()) return false;
    from = inbox_.front().first;
    data = std::move(inbox_.front().second);
    inbox_.pop_front();
    return true;
}

}  // namespace nvcoop::coop
