#include "avatar_net.h"

#include <algorithm>
#include <ctime>

#include "fnvcoop_mut.h"   // FNV_MUT(n): twins 41..51 of the live wiring suite, 105..110 of the avatar suite (plugin/avatar/full)

namespace nvcoop::livewire {

namespace live = fnvcoop::live;

namespace {
uint32_t defaultEpoch() { return static_cast<uint32_t>(std::time(nullptr)); }
Bytes withChannel(const Bytes& liveDatagram) {
    Bytes b;
    b.reserve(liveDatagram.size() + 1);
    b.push_back(kChannelAvatars);
    b.insert(b.end(), liveDatagram.begin(), liveDatagram.end());
    return b;
}
live::ReceiverConfig receiverConfig() {
    live::ReceiverConfig c;
    c.checkSenderIsOwner = false;   // players have no ownership records; the netId-vs-sender check is done here
    c.maxActors = 64;
    return c;
}
bool sameAppearance(const nvcoop::avatar::Appearance& a, const nvcoop::avatar::Appearance& b) {
    return a.race == b.race && a.female == b.female && a.hair == b.hair && a.eyes == b.eyes && a.faceHash == b.faceHash &&
           a.hairColor == b.hairColor && a.height == b.height && a.weight == b.weight && a.faceCoeffs == b.faceCoeffs;
}
}  // namespace

AvatarNet::AvatarNet(IAvatarSide& side, AvatarNetOptions opt, std::function<uint32_t()> epochSeed)
    : side_(side), opt_(std::move(opt)), epochSeed_(epochSeed ? std::move(epochSeed) : std::function<uint32_t()>(defaultEpoch)),
      rate_(opt_.rate), sender_(opt_.sender), recv_(receiverConfig()) {}

AvatarNet::~AvatarNet() = default;

std::vector<PlayerId> AvatarNet::peers() const {
    std::vector<PlayerId> v;
    if (isHost_) v.assign(joined_.begin(), joined_.end());
    else if (hostUp_) v.push_back(kHostPlayer);
    return v;
}

void AvatarNet::sendTo(PlayerId peer, bool reliable, Bytes data) {
    if (link_) link_->send(peer, reliable, std::move(data));
}

void AvatarNet::broadcastReliable(const Bytes& data, PlayerId except) {
    if (!isHost_) return;
    for (PlayerId p : joined_)
        if (p != except) sendTo(p, true, data);
}

// ---------------------------------------------------------------------------------------------------------------------------------
// session
// ---------------------------------------------------------------------------------------------------------------------------------
void AvatarNet::sessionBegin(const SessionInfo& info, ILiveLink& link) {
    link_ = &link;
    inSession_ = true;
    isHost_ = info.isHost;
    self_ = info.self;
    hostUp_ = false;
    epoch_ = std::max<uint32_t>(1, epochSeed_());
    roster_.clear();
    joined_.clear();
    looks_.clear();
    lastSnap_.clear();
    reportedSnap_.clear();
    applied_.clear();
    rx_.clear();
    rate_.clear();
    haveLocalApp_ = false;
    lastAppRead_ = 0;
    sender_ = live::SnapshotSender(opt_.sender);
    recv_ = live::SnapshotReceiver(receiverConfig());
    localOut_.clear();
    haveEquip_ = false;
    lookSentValid_ = false;
    lastAck_ = lastLookCheck_ = lastPoseReport_ = 0;
    haveLocal_ = reportedLocalValid_ = false;
    if (!FNV_MUT(110) && self_ != kNoPlayer) side_.setLocalSlot(static_cast<nvcoop::avatar::Slot>(self_));   // MUTANT 110: the avatar layer is never told its slot (a client keeps the ini default 0 and refuses the host's puppet)
    side_.setLocalIdentity(playerNetId(self_), epoch_);
    side_.setSnapshotSink([this](const live::ActorSnapshot& s) { localOut_.push_back(s); });
    side_.setEquipSink([this](const std::vector<nvcoop::avatar::EquipEntry>& e) { equip_ = e; haveEquip_ = true; });
}

void AvatarNet::sessionEnd() {
    side_.setSnapshotSink(nullptr);
    side_.setEquipSink(nullptr);
    for (PlayerId s : std::vector<PlayerId>(roster_.begin(), roster_.end())) {
        side_.removeRemote(s);
        if (poseSink_) poseSink_(s, false, 0, live::Vec3());
    }
    if (poseSink_ && reportedLocalValid_) poseSink_(self_, false, 0, live::Vec3());
    reportedLocalValid_ = haveLocal_ = false;
    if (opt_.tickSide) side_.tick(static_cast<uint32_t>(nowMs_));   // let the removals reach the engine
    roster_.clear();
    joined_.clear();
    looks_.clear();
    lastSnap_.clear();
    reportedSnap_.clear();
    applied_.clear();
    rx_.clear();
    rate_.clear();
    localOut_.clear();
    inSession_ = false;
    hostUp_ = false;
    link_ = nullptr;
}

void AvatarNet::addPuppet(PlayerId slot) {
    if (slot >= fnvcoop::kMaxSlots) return;
    if (slot == self_) {   // the local player never gets a puppet
        if (FNV_MUT(48)) roster_.insert(slot);   // MUTANT 48: the local player is listed in the roster like everybody else
        return;
    }
    roster_.insert(slot);
    auto it = looks_.find(slot);
    const nvcoop::avatar::Appearance app = it != looks_.end() ? it->second.appearance : nvcoop::avatar::Appearance{};
    side_.addRemote(slot, app);
    applied_[slot] = app;
    if (it != looks_.end()) side_.onRemoteEquipment(slot, it->second.equipment);
}

void AvatarNet::removePuppet(PlayerId slot) {
    if (!FNV_MUT(43)) side_.removeRemote(slot);   // MUTANT 43: the puppet of a player who left stays in the world
    roster_.erase(slot);
    looks_.erase(slot);
    lastSnap_.erase(slot);
    reportedSnap_.erase(slot);
    applied_.erase(slot);
    rx_.erase(slot);
    rate_.removePlace(slot);
    recv_.forget(playerNetId(slot));
    sender_.forgetPeer(slot);
    if (poseSink_) poseSink_(slot, false, 0, live::Vec3());
}

void AvatarNet::sendRoster(PlayerId to) {
    if (!isHost_) return;
    std::vector<uint8_t> slots{kHostPlayer};
    for (PlayerId p : joined_) slots.push_back(p);
    Bytes b = encodeRoster(slots);
    ++stats_.rostersSent;
    if (to == kNoPlayer) broadcastReliable(b, kNoPlayer);
    else sendTo(to, true, b);
}

void AvatarNet::sendLook(const AvLook& l, PlayerId to, PlayerId skip) {
    Bytes b = encodeLook(l);
    ++stats_.looksSent;
    if (to == kNoPlayer) broadcastReliable(b, skip);
    else sendTo(to, true, b);
}

void AvatarNet::playerJoined(PlayerId slot) {
    if (!isHost_ || slot == self_ || slot >= fnvcoop::kMaxSlots) return;
    joined_.insert(slot);
    sender_.forgetPeer(slot);
    recv_.forget(playerNetId(slot));
    addPuppet(slot);
    sendRoster(kNoPlayer);
    // the newcomer learns what everybody looks like (the host's own and the others' relayed ones)
    for (const auto& kv : looks_)
        if (kv.first != slot) sendLook(kv.second, slot, kNoPlayer);
}

void AvatarNet::playerLeft(PlayerId slot) {
    if (!isHost_) return;
    joined_.erase(slot);
    removePuppet(slot);
    sendRoster(kNoPlayer);
}

void AvatarNet::hostLinkUp() {
    if (isHost_) return;
    hostUp_ = true;
    sender_.forgetPeer(kHostPlayer);
    lookSentValid_ = false;   // the host (maybe a new one) learns our look again
    sendTo(kHostPlayer, true, Bytes{kChannelAvatars, kAvKindResync});   // and we learn the roster and every look again
}

void AvatarNet::hostLinkDown() {
    if (isHost_) return;
    hostUp_ = false;
    for (PlayerId s : std::vector<PlayerId>(roster_.begin(), roster_.end())) removePuppet(s);   // the roster comes back with the link
    recv_ = live::SnapshotReceiver(receiverConfig());
}

void AvatarNet::applyRoster(const std::vector<uint8_t>& slots) {
    if (FNV_MUT(44)) return;   // MUTANT 44: the roster is ignored (a client never learns who exists)
    std::set<PlayerId> want;
    for (uint8_t s : slots) want.insert(s);
    for (PlayerId s : std::vector<PlayerId>(roster_.begin(), roster_.end()))
        if (!want.count(s)) removePuppet(s);
    for (PlayerId s : want)
        if (!roster_.count(s)) addPuppet(s);
    ++stats_.rostersApplied;
}

// ---------------------------------------------------------------------------------------------------------------------------------
// receive
// ---------------------------------------------------------------------------------------------------------------------------------
void AvatarNet::receive(PlayerId from, const uint8_t* data, size_t size) {
    if (!inSession_ || size < 2 || data[0] != kChannelAvatars) { ++stats_.badDatagrams; return; }
    ++stats_.received;
    const uint8_t kind = data[1];
    if (kind == static_cast<uint8_t>(live::MsgKind::SnapshotBatch)) {
        onSnapshotDatagram(from, data + 1, size - 1);
    } else if (kind == static_cast<uint8_t>(live::MsgKind::SnapshotAck)) {
        if (sender_.onAckPacket(from, data + 1, size - 1)) ++stats_.acksReceived;
        else ++stats_.badDatagrams;
    } else if (kind == kAvKindRoster) {
        std::vector<uint8_t> slots;
        if (isHost_ || from != kHostPlayer || !decodeRoster(data, size, slots)) { ++stats_.badDatagrams; return; }   // only the host sends rosters
        applyRoster(slots);
    } else if (kind == kAvKindResync) {
        if (!isHost_ || size != 2 || !joined_.count(from)) { ++stats_.badDatagrams; return; }
        if (FNV_MUT(51)) return;   // MUTANT 51: the host ignores a client's resync request (a returning link never gets the roster back)
        sendRoster(from);
        for (const auto& kv : looks_)
            if (kv.first != from) sendLook(kv.second, from, kNoPlayer);
        ++stats_.resyncsAnswered;
    } else if (kind == kAvKindLook || kind == kAvKindLook2) {
        onLook(from, data, size);
    } else {
        ++stats_.badDatagrams;
    }
}

void AvatarNet::onSnapshotDatagram(PlayerId from, const uint8_t* body, size_t size) {
    auto results = recv_.receive(from, body, size);
    for (const live::ReceivedSnapshot& r : results) {
        if (r.verdict != live::Verdict::Accepted) { ++stats_.rejected; continue; }
        const live::ActorSnapshot& s = r.snap;
        if (!isPlayerNetId(s.netId)) { ++stats_.rejected; continue; }
        const PlayerId slot = slotOfPlayerNetId(s.netId);
        if (isHost_ && slot != from && !FNV_MUT(42)) { ++stats_.forged; continue; }   // MUTANT 42: a client may publish another player's avatar
        if (slot == self_) { ++stats_.rejected; continue; }   // our own stream echoed back
        if (!isHost_ && from != kHostPlayer) { ++stats_.rejected; continue; }
        if (!roster_.count(slot)) { ++stats_.noRoster; continue; }
        side_.onRemoteSnapshot(slot, s, static_cast<uint32_t>(nowMs_));
        lastSnap_[slot] = s;
        rate_.setPlace(slot, s.cell, s.pos);
        ++stats_.accepted;
        ++rx_[slot];
        if (isHost_ && !FNV_MUT(41)) {   // MUTANT 41: the host does not relay (a client never sees the other clients)
            live::ActorSnapshot relay = s;
            relay.avDeltas = r.avAll;
            for (PlayerId c : joined_)
                if (c != slot) queueTo(c, slot, relay, nowMs_);
        }
    }
}

// Who our own stream is relayed to by the host: every other player we know of (the host itself included).
std::vector<PlayerId> AvatarNet::uplinkObservers() const {
    std::vector<PlayerId> v;
    for (PlayerId p : roster_)
        if (p != self_) v.push_back(p);
    if (!isHost_ && std::find(v.begin(), v.end(), kHostPlayer) == v.end()) v.push_back(kHostPlayer);
    return v;
}

// Queues one snapshot of `subject` for `peer` through the near / far rate. A snapshot that is not due yet is kept (latest wins) and
// released by releasePending() when its turn comes. `peer == kNoPlayer` is the uplink of the local stream (to the host).
void AvatarNet::queueTo(PlayerId peer, PlayerId subject, const live::ActorSnapshot& snap, uint64_t nowMs) {
    live::ActorSnapshot out;
    bool now = false;
    if (FNV_MUT(107)) {   // twin 107: the rate is ignored (everything goes at full rate to everybody)
        out = snap;
        now = true;
    } else if (peer == kNoPlayer) {
        const std::vector<PlayerId> obs = uplinkObservers();
        if (FNV_MUT(108)) {   // twin 108: the uplink follows the LOWEST tier of its observers (a near neighbour would see 5 or 1 Hz)
            nvcoop::avatar::StreamTier lo = nvcoop::avatar::StreamTier::Near;
            for (PlayerId o : obs) {
                nvcoop::avatar::StreamTier t = rate_.tierOf(o, subject);
                if (static_cast<uint8_t>(t) < static_cast<uint8_t>(lo)) lo = t;
            }
            now = rate_.offerAtTier(nvcoop::avatar::StreamRatePlanner::kUplink, subject, lo, snap, static_cast<uint32_t>(nowMs), out);
        } else {
            now = rate_.offerUplink(subject, obs, snap, static_cast<uint32_t>(nowMs), out);
        }
    } else {
        now = rate_.offer(peer, subject, snap, static_cast<uint32_t>(nowMs), out);
    }
    if (!now) {
        ++stats_.rateKept;
        return;
    }
    const PlayerId to = peer == kNoPlayer ? kHostPlayer : peer;
    if (sender_.queue(to, out, static_cast<uint32_t>(nowMs)) && subject != self_) ++stats_.relayed;
}

void AvatarNet::releasePending(uint64_t nowMs) {
    if (FNV_MUT(109)) return;   // twin 109: a snapshot held back by the rate is never released (a stopped player stays where the last sent one was)
    std::vector<nvcoop::avatar::StreamRatePlanner::Release> rel;
    rate_.poll(static_cast<uint32_t>(nowMs), rel);
    for (auto& r : rel) {
        const PlayerId to = r.observer == nvcoop::avatar::StreamRatePlanner::kUplink ? kHostPlayer : r.observer;
        if (!isHost_ && (to != kHostPlayer || !hostUp_)) continue;   // a client only talks to the host, and only while the link is up
        if (isHost_ && to != kHostPlayer && !joined_.count(to)) continue;   // the observer left meanwhile
        if (sender_.queue(to, r.snap, static_cast<uint32_t>(nowMs))) {
            ++stats_.rateReleased;
            if (r.subject != self_) ++stats_.relayed;
        }
    }
}

void AvatarNet::onLook(PlayerId from, const uint8_t* data, size_t size) {
    AvLook l;
    if (!decodeLook(data, size, l)) { ++stats_.looksRejected; return; }
    if (isHost_) {
        if (l.slot != from && !FNV_MUT(50)) { ++stats_.looksRejected; return; }   // MUTANT 50: a client may set another player's look
    } else {
        if (from != kHostPlayer || l.slot == self_) { ++stats_.looksRejected; return; }
    }
    auto old = looks_.find(l.slot);
    const bool newLook = old == looks_.end() || !(old->second == l);
    const bool oldHadLook = old != looks_.end();
    const nvcoop::avatar::Appearance oldApp = oldHadLook ? old->second.appearance : nvcoop::avatar::Appearance{};
    looks_[l.slot] = l;
    if (roster_.count(l.slot)) {
        // compare with what the PUPPET was built from, not with the previous look: the first look of a player arrives after the roster
        // created its puppet from the default appearance (a client learns who exists before it learns what they look like)
        auto ap = applied_.find(l.slot);
        bool appearanceChanged = ap == applied_.end() || !sameAppearance(ap->second, l.appearance);
        if (FNV_MUT(106)) appearanceChanged = oldHadLook && !sameAppearance(oldApp, l.appearance);   // MUTANT 106: the FIRST look of a player never reaches its puppet
        if (appearanceChanged || FNV_MUT(105)) {
            const bool templateChanged = ap == applied_.end() || ap->second.race != l.appearance.race || ap->second.female != l.appearance.female;
            // MUTANT 105: every look change rebuilds the puppet (it flashes; the interpolation state and equipment start over)
            if (FNV_MUT(105)) side_.addRemote(l.slot, l.appearance);
            else side_.setRemoteAppearance(l.slot, l.appearance);
            applied_[l.slot] = l.appearance;
            ++stats_.appearanceUpdates;
            if (templateChanged) ++stats_.puppetRespawnsForLook;
        }
        if (!FNV_MUT(45)) side_.onRemoteEquipment(l.slot, l.equipment);   // MUTANT 45: the equipment of other players is never applied
        ++stats_.looksApplied;
    }
    if (isHost_ && newLook) sendLook(l, kNoPlayer, from);
}

// ---------------------------------------------------------------------------------------------------------------------------------
// per frame
// ---------------------------------------------------------------------------------------------------------------------------------
void AvatarNet::checkLocalLook(uint64_t nowMs) {
    if (nowMs - lastLookCheck_ < opt_.lookCheckMs && lookSentValid_) return;
    lastLookCheck_ = nowMs;
    if (!(isHost_ || hostUp_)) return;
    AvLook l;
    l.slot = self_;
    bool haveApp = false;
    if (localAppearance_) {
        haveApp = localAppearance_(l.appearance);   // an injected reader is asked at every check
    } else if (haveLocalApp_ && nowMs - lastAppRead_ < opt_.appearancePollMs) {
        l.appearance = localApp_;
        haveApp = true;
    } else {
        nvcoop::avatar::Appearance a;
        lastAppRead_ = nowMs;
        if (side_.readLocalAppearance(a)) {
            localApp_ = a;
            haveLocalApp_ = true;
        }
        if (haveLocalApp_) {
            l.appearance = localApp_;
            haveApp = true;
        }
    }
    if (!haveEquip_ && !haveApp) return;
    l.equipment = equip_;
    if (lookSentValid_ && l == lastLookSent_) return;
    if (isHost_) {
        looks_[self_] = l;
        sendLook(l, kNoPlayer, kNoPlayer);
    } else {
        sendLook(l, kHostPlayer, kNoPlayer);
    }
    lastLookSent_ = l;
    lookSentValid_ = true;
}

void AvatarNet::flushStreams(uint64_t nowMs) {
    for (PlayerId p : peers()) {
        for (Bytes& d : sender_.flush(p, static_cast<uint32_t>(nowMs))) {
            ++stats_.sent;
            sendTo(p, false, withChannel(d));
        }
    }
}

void AvatarNet::sendAcks(uint64_t nowMs) {
    if (nowMs - lastAck_ < opt_.ackIntervalMs) return;
    lastAck_ = nowMs;
    for (PlayerId p : peers()) {
        Bytes a = recv_.takeAcks(p);
        if (a.empty()) continue;
        ++stats_.acksSent;
        sendTo(p, false, withChannel(a));
    }
}

void AvatarNet::reportPoses(uint64_t nowMs) {
    if (!poseSink_ || FNV_MUT(49)) return;   // MUTANT 49: the ownership service never learns where remote players are
    if (nowMs - lastPoseReport_ < opt_.poseReportMs) return;
    lastPoseReport_ = nowMs;
    for (const auto& kv : lastSnap_) {
        auto rep = reportedSnap_.find(kv.first);
        if (rep != reportedSnap_.end() && rep->second.cell == kv.second.cell && rep->second.pos == kv.second.pos) continue;
        reportedSnap_[kv.first] = kv.second;
        poseSink_(kv.first, true, kv.second.cell, kv.second.pos);
    }
    if (haveLocal_ && (!reportedLocalValid_ || reportedLocal_.cell != lastLocal_.cell || !(reportedLocal_.pos == lastLocal_.pos))) {
        reportedLocal_ = lastLocal_;
        reportedLocalValid_ = true;
        poseSink_(self_, true, lastLocal_.cell, lastLocal_.pos);
    }
}

void AvatarNet::tick(uint64_t nowMs) {
    nowMs_ = nowMs;
    if (!inSession_) return;
    if (opt_.tickSide) side_.tick(static_cast<uint32_t>(nowMs));   // publishes the local snapshot (sink), drives puppets
    const uint32_t id = playerNetId(self_);
    for (const live::ActorSnapshot& s : localOut_) {
        ++stats_.localSnapshots;
        live::ActorSnapshot snap = s;
        snap.netId = id;
        snap.ownerEpoch = epoch_;
        lastLocal_ = snap;
        haveLocal_ = true;
        rate_.setPlace(self_, snap.cell, snap.pos);
        if (isHost_) {
            for (PlayerId p : joined_) queueTo(p, self_, snap, nowMs);
        } else if (hostUp_) {
            queueTo(kNoPlayer, self_, snap, nowMs);   // the uplink: the host relays it onward at the tier each other player has
        }
    }
    localOut_.clear();
    releasePending(nowMs);
    checkLocalLook(nowMs);
    flushStreams(nowMs);
    sendAcks(nowMs);
    reportPoses(nowMs);
}

void AvatarNet::idleTick(uint64_t nowMs) {
    nowMs_ = nowMs;
    // no session: the avatar layer still has work after a load (orphan puppets from the save are purged by its next tick)
    if (opt_.tickSide && side_.purgePending()) {
        ++stats_.idleTicks;
        side_.tick(static_cast<uint32_t>(nowMs));
    }
}

// ---------------------------------------------------------------------------------------------------------------------------------
// game events
// ---------------------------------------------------------------------------------------------------------------------------------
void AvatarNet::loadBegin() { side_.onEvent(IAvatarSide::Event::LoadBegin); }

void AvatarNet::loadEnd() {
    if (FNV_MUT(47) && !inSession_) return;   // MUTANT 47: the post-load purge only happens inside a coop session (a puppet that a save carried stays)
    side_.onEvent(IAvatarSide::Event::LoadEnd);
}

void AvatarNet::gameExit() { side_.onEvent(IAvatarSide::Event::ExitToMenu); }

// ---------------------------------------------------------------------------------------------------------------------------------
// queries
// ---------------------------------------------------------------------------------------------------------------------------------
namespace {
const char* tierName(nvcoop::avatar::StreamTier t) {
    return t == nvcoop::avatar::StreamTier::Near ? "near" : (t == nvcoop::avatar::StreamTier::Far ? "far" : "out");
}
std::string lookJson(const AvLook& l) {
    const nvcoop::avatar::Appearance& a = l.appearance;
    std::string s = "{\"race\":" + std::to_string(a.race) + ",\"female\":" + (a.female ? "true" : "false") + ",\"hair\":" + std::to_string(a.hair) +
                    ",\"eyes\":" + std::to_string(a.eyes) + ",\"hairColor\":" + std::to_string(a.hairColor) + ",\"height\":" + std::to_string(a.height) +
                    ",\"weight\":" + std::to_string(a.weight) + ",\"faceCoeffs\":" + std::to_string(a.faceCoeffs.size()) + ",\"equip\":[";
    for (size_t i = 0; i < l.equipment.size(); ++i) {
        if (i) s += ",";
        s += "{\"form\":" + std::to_string(l.equipment[i].form) + ",\"weapon\":" + (l.equipment[i].weapon ? "true" : "false") + ",\"mods\":" +
             std::to_string(static_cast<int>(l.equipment[i].mods)) + "}";
    }
    return s + "]}";
}
}  // namespace

std::string AvatarNet::statusJson() const {
    std::string s = "{\"inSession\":";
    s += inSession_ ? "true" : "false";
    s += ",\"self\":" + std::to_string(static_cast<int>(self_)) + ",\"epoch\":" + std::to_string(epoch_) + ",\"roster\":[";
    bool first = true;
    for (PlayerId p : roster_) {
        if (!first) s += ",";
        first = false;
        auto rx = rx_.find(p);
        s += "{\"slot\":" + std::to_string(static_cast<int>(p)) + ",\"ref\":" + std::to_string(side_.refOf(p)) + ",\"look\":" + (looks_.count(p) ? "true" : "false") +
             ",\"tier\":\"" + tierName(rate_.tierOf(self_, p)) + "\",\"rx\":" + std::to_string(rx == rx_.end() ? 0ull : (unsigned long long)rx->second);
        auto lk = looks_.find(p);
        if (lk != looks_.end()) s += ",\"lookInfo\":" + lookJson(lk->second);
        s += "}";
    }
    s += "],\"selfLook\":" + (lookSentValid_ ? lookJson(lastLookSent_) : std::string("null"));
    s += ",\"sent\":" + std::to_string(stats_.sent) + ",\"received\":" + std::to_string(stats_.received) +
         ",\"accepted\":" + std::to_string(stats_.accepted) + ",\"rejected\":" + std::to_string(stats_.rejected) +
         ",\"forged\":" + std::to_string(stats_.forged) + ",\"noRoster\":" + std::to_string(stats_.noRoster) +
         ",\"relayed\":" + std::to_string(stats_.relayed) + ",\"looksApplied\":" + std::to_string(stats_.looksApplied) +
         ",\"rateKept\":" + std::to_string(stats_.rateKept) + ",\"rateReleased\":" + std::to_string(stats_.rateReleased) +
         ",\"appearanceUpdates\":" + std::to_string(stats_.appearanceUpdates) + ",\"puppetRespawnsForLook\":" + std::to_string(stats_.puppetRespawnsForLook) +
         ",\"localSnapshots\":" + std::to_string(stats_.localSnapshots) + "}";
    return s;
}

}  // namespace nvcoop::livewire
