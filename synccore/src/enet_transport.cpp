#include "fnvcoop/enet_transport.h"
#include <enet/enet.h>
#include <atomic>

namespace fnvcoop {

namespace {
std::atomic<int> g_enetUsers{0};
bool enetAcquire() {
    if (g_enetUsers.fetch_add(1) == 0 && enet_initialize() != 0) { g_enetUsers.fetch_sub(1); return false; }
    return true;
}
void enetRelease() {
    if (g_enetUsers.fetch_sub(1) == 1) enet_deinitialize();
}
constexpr size_t kChannels = 2;
}  // namespace

EnetTransport::~EnetTransport() {
    if (host_) {
        enet_host_destroy(host_);
        host_ = nullptr;
        enetRelease();
    }
}

bool EnetTransport::listen(uint16_t port, size_t maxPeers, bool loopbackOnly) {
    if (host_ || !enetAcquire()) return false;
    ENetAddress a;
    a.host = loopbackOnly ? ENET_HOST_TO_NET_32(0x7F000001) : ENET_HOST_ANY;
    a.port = port;
    host_ = enet_host_create(&a, maxPeers, kChannels, 0, 0);
    if (!host_) { enetRelease(); return false; }
    server_ = true;
    port_ = host_->address.port ? host_->address.port : port;
    return true;
}

bool EnetTransport::connect(const std::string& hostName, uint16_t port) {
    if (host_ || !enetAcquire()) return false;
    host_ = enet_host_create(nullptr, 1, kChannels, 0, 0);
    if (!host_) { enetRelease(); return false; }
    ENetAddress a;
    if (enet_address_set_host(&a, hostName.c_str()) != 0) {
        enet_host_destroy(host_); host_ = nullptr; enetRelease(); return false;
    }
    a.port = port;
    if (!enet_host_connect(host_, &a, kChannels, 0)) {
        enet_host_destroy(host_); host_ = nullptr; enetRelease(); return false;
    }
    server_ = false;
    return true;
}

void EnetTransport::disconnect() {
    if (!host_) return;
    for (size_t i = 0; i < host_->peerCount; ++i)
        if (host_->peers[i].state == ENET_PEER_STATE_CONNECTED) enet_peer_disconnect(&host_->peers[i], 0);
    enet_host_flush(host_);
}

PeerId EnetTransport::idOf(const _ENetPeer* p) const {
    return server_ ? static_cast<PeerId>(p - host_->peers) + 1 : 0;
}

_ENetPeer* EnetTransport::peerFor(PeerId id) const {
    if (!host_) return nullptr;
    if (!server_) return id == 0 && host_->peerCount ? &host_->peers[0] : nullptr;
    if (id == 0 || id > host_->peerCount) return nullptr;
    return &host_->peers[id - 1];
}

void EnetTransport::sendEx(PeerId to, Bytes data, Channel ch) {
    ENetPeer* p = peerFor(to);
    if (!p || p->state != ENET_PEER_STATE_CONNECTED) return;
    stats_.datagramsOut++;
    stats_.bytesOut += data.size();
    ENetPacket* pk = enet_packet_create(data.data(), data.size(),
                                        ch == Channel::Reliable ? ENET_PACKET_FLAG_RELIABLE : 0);
    if (!pk) return;
    if (enet_peer_send(p, ch == Channel::Reliable ? 0 : 1, pk) != 0) enet_packet_destroy(pk);
    enet_host_flush(host_);
}

void EnetTransport::service() {
    if (!host_) return;
    ENetEvent ev;
    while (enet_host_service(host_, &ev, 0) > 0) {
        switch (ev.type) {
            case ENET_EVENT_TYPE_CONNECT:
                ++connectedPeers_;
                events_.push_back({Event::Connected, idOf(ev.peer)});
                break;
            case ENET_EVENT_TYPE_DISCONNECT:
                if (connectedPeers_ > 0) --connectedPeers_;
                events_.push_back({Event::Disconnected, idOf(ev.peer)});
                break;
            case ENET_EVENT_TYPE_RECEIVE:
                stats_.datagramsIn++;
                stats_.bytesIn += ev.packet->dataLength;
                inbox_.emplace_back(idOf(ev.peer), Bytes(ev.packet->data, ev.packet->data + ev.packet->dataLength));
                enet_packet_destroy(ev.packet);
                break;
            default: break;
        }
    }
}

bool EnetTransport::poll(PeerId& from, Bytes& data) {
    if (inbox_.empty()) service();
    if (inbox_.empty()) return false;
    from = inbox_.front().first;
    data = std::move(inbox_.front().second);
    inbox_.pop_front();
    return true;
}

size_t EnetTransport::backlogBytes(PeerId to) const {
    ENetPeer* p = peerFor(to);
    return p ? static_cast<size_t>(p->totalWaitingData) + p->reliableDataInTransit : 0;
}

bool EnetTransport::nextEvent(Event& e) {
    if (events_.empty()) service();
    if (events_.empty()) return false;
    e = events_.front();
    events_.pop_front();
    return true;
}

}  // namespace fnvcoop
