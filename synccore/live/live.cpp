// Live layer core implementation. See live.h for the design.
#include "live.h"

#include <algorithm>
#include <cmath>
#include <cstring>

#include "fnvcoop/wire.h"   // crc32

namespace fnvcoop::live {

namespace {

constexpr double kTwoPi = 6.283185307179586476925286766559;
constexpr size_t kMaxAvPerEntry = 64;
constexpr uint8_t kMaskCell = 1, kMaskPos = 2, kMaskRot = 4, kMaskVel = 8, kMaskAnim = 16, kMaskAi = 32, kMaskAv = 64;
constexpr uint8_t kMaskAll = 0x7F;

struct Writer {
    Bytes& b;
    void u8(uint8_t v) { b.push_back(v); }
    void u16(uint16_t v) { b.push_back(static_cast<uint8_t>(v)); b.push_back(static_cast<uint8_t>(v >> 8)); }
    void varu(uint64_t v) {
        while (v >= 0x80) { b.push_back(static_cast<uint8_t>(v | 0x80)); v >>= 7; }
        b.push_back(static_cast<uint8_t>(v));
    }
    void vari(int64_t v) { varu((static_cast<uint64_t>(v) << 1) ^ static_cast<uint64_t>(v >> 63)); }
};

struct Reader {
    const uint8_t* p;
    const uint8_t* end;
    bool ok = true;
    uint8_t u8() { if (p >= end) { ok = false; return 0; } return *p++; }
    uint16_t u16() { uint16_t lo = u8(); uint16_t hi = u8(); return static_cast<uint16_t>(lo | (hi << 8)); }
    uint64_t varu() {
        uint64_t v = 0;
        for (int shift = 0; shift < 70; shift += 7) {
            if (p >= end) { ok = false; return 0; }
            uint8_t c = *p++;
            if (shift == 63 && c > 1) { ok = false; return 0; }
            v |= static_cast<uint64_t>(c & 0x7F) << shift;
            if (!(c & 0x80)) return v;
        }
        ok = false;
        return 0;
    }
    int64_t vari() { uint64_t u = varu(); return static_cast<int64_t>(u >> 1) ^ -static_cast<int64_t>(u & 1); }
    uint32_t varu32() { uint64_t v = varu(); if (v > 0xFFFFFFFFull) { ok = false; return 0; } return static_cast<uint32_t>(v); }
    int32_t vari32() { int64_t v = vari(); if (v < INT32_MIN || v > INT32_MAX) { ok = false; return 0; } return static_cast<int32_t>(v); }
};

int32_t qpos(float v, float step) {
    if (!std::isfinite(v)) return 0;
    double d = static_cast<double>(v) / step;
    d = std::min(d, 1073741823.0);
    d = std::max(d, -1073741823.0);
    return static_cast<int32_t>(std::llround(d));
}
uint16_t qang(float v) {
    if (!std::isfinite(v)) return 0;
    double a = std::fmod(static_cast<double>(v), kTwoPi);
    if (a < 0) a += kTwoPi;
    long long q = std::llround(a / kTwoPi * 65536.0);
    return static_cast<uint16_t>(q & 0xFFFF);
}
float dang(uint16_t q) {
    return static_cast<float>(static_cast<double>(q) * kTwoPi / 65536.0);
}

void appendCrc(Bytes& b) {
    uint32_t c = crc32(b.data(), b.size());
    for (int i = 0; i < 4; ++i) b.push_back(static_cast<uint8_t>(c >> (8 * i)));
}
bool checkCrc(const uint8_t* d, size_t n) {
    if (n < 5) return false;
    uint32_t want = 0;
    for (int i = 0; i < 4; ++i) want |= static_cast<uint32_t>(d[n - 4 + i]) << (8 * i);
    return crc32(d, n - 4) == want;
}

}  // namespace

float distance(const Vec3& a, const Vec3& b) {
    double dx = static_cast<double>(a.x) - b.x, dy = static_cast<double>(a.y) - b.y, dz = static_cast<double>(a.z) - b.z;
    return static_cast<float>(std::sqrt(dx * dx + dy * dy + dz * dz));
}

uint8_t packAnimFlags(const AnimState& a) {
    return static_cast<uint8_t>((a.weaponDrawn ? 1 : 0) | (a.sneak ? 2 : 0) | (a.jump ? 4 : 0) | (a.reload ? 8 : 0) | (a.aim ? 16 : 0));
}
uint8_t packAnimExt(const AnimState& a) {
    return static_cast<uint8_t>((a.dead ? 1 : 0) | (a.knocked ? 2 : 0) | (a.swim ? 4 : 0) | (a.talk ? 8 : 0) | (a.block ? 16 : 0) | (a.ragdoll ? 32 : 0));
}
bool animHasExt(const AnimState& a) { return a.action != 0 || a.actionSeq != 0 || packAnimExt(a) != 0; }
AnimState unpackAnim(uint8_t loco, uint8_t f, uint8_t action, uint8_t actionSeq, uint8_t ext) {
    AnimState a;
    a.locomotion = loco;
    a.weaponDrawn = f & 1; a.sneak = f & 2; a.jump = f & 4; a.reload = f & 8; a.aim = f & 16;
    a.action = action; a.actionSeq = actionSeq;
    a.dead = ext & 1; a.knocked = ext & 2; a.swim = ext & 4; a.talk = ext & 8; a.block = ext & 16; a.ragdoll = ext & 32;
    return a;
}

Channel channelFor(MsgKind k) {
    switch (k) {
        case MsgKind::SnapshotBatch:
        case MsgKind::SnapshotAck: return Channel::Unreliable;
        default: return Channel::Reliable;
    }
}
const char* toString(MsgKind k) {
    switch (k) {
        case MsgKind::SnapshotBatch: return "SnapshotBatch";
        case MsgKind::SnapshotAck: return "SnapshotAck";
        case MsgKind::Ownership: return "Ownership";
        case MsgKind::Event: return "Event";
    }
    return "?";
}
const char* toString(Verdict v) {
    switch (v) {
        case Verdict::Accepted: return "Accepted";
        case Verdict::StaleEpoch: return "StaleEpoch";
        case Verdict::StaleSeq: return "StaleSeq";
        case Verdict::WrongOwner: return "WrongOwner";
        case Verdict::NeedBaseline: return "NeedBaseline";
        case Verdict::Malformed: return "Malformed";
    }
    return "?";
}
const char* toString(InterpState s) {
    switch (s) {
        case InterpState::Empty: return "Empty";
        case InterpState::Interpolating: return "Interpolating";
        case InterpState::Extrapolating: return "Extrapolating";
        case InterpState::Holding: return "Holding";
        case InterpState::Smoothing: return "Smoothing";
        case InterpState::Snapped: return "Snapped";
    }
    return "?";
}
const char* toString(Tier t) {
    switch (t) {
        case Tier::None: return "None";
        case Tier::Far: return "Far";
        case Tier::Near: return "Near";
    }
    return "?";
}

// ---- quantization -----------------------------------------------------------------------------------------------
bool operator==(const QState& a, const QState& b) {
    return a.cell == b.cell && std::memcmp(a.pos, b.pos, sizeof a.pos) == 0 && std::memcmp(a.rot, b.rot, sizeof a.rot) == 0 &&
           std::memcmp(a.vel, b.vel, sizeof a.vel) == 0 && a.loco == b.loco && a.animFlags == b.animFlags &&
           a.action == b.action && a.actionSeq == b.actionSeq && a.animExt == b.animExt && a.target == b.target && a.package == b.package && a.alert == b.alert && a.av == b.av;
}

QState quantize(const ActorSnapshot& s) {
    QState q;
    q.cell = s.cell;
    q.pos[0] = qpos(s.pos.x, kPosStep); q.pos[1] = qpos(s.pos.y, kPosStep); q.pos[2] = qpos(s.pos.z, kPosStep);
    q.rot[0] = qang(s.rot.x); q.rot[1] = qang(s.rot.y); q.rot[2] = qang(s.rot.z);
    q.vel[0] = qpos(s.vel.x, kVelStep); q.vel[1] = qpos(s.vel.y, kVelStep); q.vel[2] = qpos(s.vel.z, kVelStep);
    q.loco = s.anim.locomotion;
    q.animFlags = packAnimFlags(s.anim);
    if (!liveFaults().dropAnimExt) { q.action = s.anim.action; q.actionSeq = s.anim.actionSeq; q.animExt = packAnimExt(s.anim); }
    q.target = s.ai.combatTarget; q.package = s.ai.package; q.alert = s.ai.alert;
    for (const auto& a : s.avDeltas) q.av[a.av] = a.value;
    return q;
}

ActorSnapshot dequantize(const QState& q, NetId id, OwnerEpoch epoch, uint32_t seq, uint32_t tick) {
    ActorSnapshot s;
    s.netId = id; s.ownerEpoch = epoch; s.seq = seq; s.tick = tick;
    s.cell = q.cell;
    s.pos = {q.pos[0] * kPosStep, q.pos[1] * kPosStep, q.pos[2] * kPosStep};
    s.rot = {dang(q.rot[0]), dang(q.rot[1]), dang(q.rot[2])};
    s.vel = {q.vel[0] * kVelStep, q.vel[1] * kVelStep, q.vel[2] * kVelStep};
    s.anim = unpackAnim(q.loco, q.animFlags, q.action, q.actionSeq, q.animExt);
    s.ai.combatTarget = q.target; s.ai.package = q.package; s.ai.alert = q.alert;
    for (const auto& kv : q.av) s.avDeltas.push_back({kv.first, kv.second});
    return s;
}

ActorSnapshot normalized(const ActorSnapshot& s) {
    return dequantize(quantize(s), s.netId, s.ownerEpoch, s.seq, s.tick);
}

uint32_t SeqAllocator::next(NetId id, OwnerEpoch epoch) {
    auto& e = st_[id];
    if (e.first != epoch || e.second == 0) { e.first = epoch; e.second = 0; }
    return ++e.second;
}

// ---- sender -----------------------------------------------------------------------------------------------------
bool SnapshotSender::queue(PlayerId peer, const ActorSnapshot& s, uint32_t nowMs) {
    (void)nowMs;
    if (s.seq == 0) return false;
    Peer& P = peers_[peer];
    auto it = P.tracks.find(s.netId);
    if (it == P.tracks.end()) {
        it = P.tracks.emplace(s.netId, Track{}).first;
        it->second.epoch = s.ownerEpoch;
    }
    Track& T = it->second;
    if (s.ownerEpoch != T.epoch) {
        if (!serialLess(T.epoch, s.ownerEpoch)) return false;   // older owner: never publish
        T = Track{};
        T.epoch = s.ownerEpoch;
    }
    if (T.lastQueuedSeq != 0 && !serialLess(T.lastQueuedSeq, s.seq)) return false;
    T.lastQueuedSeq = s.seq;
    ++stats_.queued;

    // AV values accumulate across snapshots so a value changed in a lost snapshot is still different from the baseline.
    if (!liveFaults().dropAvChanges) {
        for (const auto& a : s.avDeltas) T.avAcc[a.av] = a.value;
    }
    QState cur = quantize(s);
    cur.av = T.avAcc;
    if (cur.av.size() > kMaxAvPerEntry) {
        while (cur.av.size() > kMaxAvPerEntry) cur.av.erase(std::prev(cur.av.end()));
    }

    if (T.inflight.size() >= cfg_.maxUnacked) {
        T.haveAcked = false;
        T.inflight.clear();
        ++stats_.resyncs;
    }
    const QState* base = nullptr;
    uint32_t baseSeq = 0;
    if (liveFaults().deltaFromLatest && !T.inflight.empty()) {
        base = &T.inflight.back().q; baseSeq = T.inflight.back().seq;
    } else if (T.haveAcked && !T.forceFull) {
        base = &T.acked.q; baseSeq = T.acked.seq;
    }
    if (base && baseSeq == T.acked.seq && T.haveAcked && cfg_.idleHeartbeatMs > 0 && T.everSent && cur == T.acked.q &&
        static_cast<uint32_t>(s.tick - T.lastSentTick) < cfg_.idleHeartbeatMs) {
        ++stats_.skippedIdle;
        return false;
    }

    // Entry body.
    uint8_t mask = kMaskAll;
    QState zero;
    const QState& b = base ? *base : zero;
    if (base) {
        mask = 0;
        if (cur.cell != b.cell) mask |= kMaskCell;
        if (std::memcmp(cur.pos, b.pos, sizeof cur.pos) != 0) mask |= kMaskPos;
        if (std::memcmp(cur.rot, b.rot, sizeof cur.rot) != 0) mask |= kMaskRot;
        if (std::memcmp(cur.vel, b.vel, sizeof cur.vel) != 0) mask |= kMaskVel;
        const bool extChanged = cur.action != b.action || cur.actionSeq != b.actionSeq || cur.animExt != b.animExt;
        if (cur.loco != b.loco || cur.animFlags != b.animFlags || (extChanged && !liveFaults().animExtNoDelta)) mask |= kMaskAnim;
        if (cur.target != b.target || cur.package != b.package || cur.alert != b.alert) mask |= kMaskAi;
        for (const auto& kv : cur.av) { auto f = b.av.find(kv.first); if (f == b.av.end() || f->second != kv.second) { mask |= kMaskAv; break; } }
    }
    Pending pe;
    pe.id = s.netId; pe.epoch = s.ownerEpoch; pe.seq = s.seq; pe.tick = s.tick; pe.baseSeq = baseSeq;
    Writer w{pe.body};
    w.varu(s.netId); w.varu(s.ownerEpoch); w.varu(s.seq); w.varu(baseSeq); w.varu(s.tick);
    w.u8(mask);
    if (mask & kMaskCell) w.varu(cur.cell);
    if (mask & kMaskPos) for (int i = 0; i < 3; ++i) w.vari(static_cast<int64_t>(cur.pos[i]) - b.pos[i]);
    if (mask & kMaskRot) for (int i = 0; i < 3; ++i) w.u16(cur.rot[i]);
    if (mask & kMaskVel) for (int i = 0; i < 3; ++i) w.vari(static_cast<int64_t>(cur.vel[i]) - b.vel[i]);
    if (mask & kMaskAnim) {
        const bool ext = cur.action != 0 || cur.actionSeq != 0 || cur.animExt != 0;
        w.u8(cur.loco);
        w.u8(static_cast<uint8_t>(cur.animFlags | (ext ? kAnimFlagExt : 0)));
        if (ext) { w.u8(cur.action); w.u8(cur.actionSeq); w.u8(cur.animExt); }
    }
    if (mask & kMaskAi) { w.varu(cur.target); w.varu(cur.package); w.u8(cur.alert); }
    if (mask & kMaskAv) {
        std::vector<AvValue> ch;
        for (const auto& kv : cur.av) { auto f = b.av.find(kv.first); if (f == b.av.end() || f->second != kv.second) ch.push_back({kv.first, kv.second}); }
        w.varu(ch.size());
        for (const auto& c : ch) { w.u8(c.av); w.vari(c.value); }
    }
    P.pending.push_back(std::move(pe));
    T.inflight.push_back({s.seq, cur});
    T.lastSentTick = s.tick;
    T.everSent = true;
    if (baseSeq == 0) ++stats_.fullSent; else ++stats_.deltaSent;
    return true;
}

std::vector<Bytes> SnapshotSender::flush(PlayerId peer, uint32_t nowMs) {
    (void)nowMs;
    std::vector<Bytes> out;
    auto it = peers_.find(peer);
    if (it == peers_.end() || it->second.pending.empty()) return out;
    Peer& P = it->second;
    Bytes pkt;
    size_t count = 0;
    auto close = [&]() {
        if (count == 0) return;
        pkt[1] = static_cast<uint8_t>(count);
        appendCrc(pkt);
        stats_.bytes += pkt.size();
        ++stats_.packets;
        out.push_back(std::move(pkt));
        pkt.clear();
        count = 0;
    };
    for (const auto& pe : P.pending) {
        if (count > 0 && (pkt.size() + pe.body.size() + 4 > cfg_.maxPacketBytes || count >= 255)) close();
        if (count == 0) { pkt.push_back(static_cast<uint8_t>(MsgKind::SnapshotBatch)); pkt.push_back(0); }
        pkt.insert(pkt.end(), pe.body.begin(), pe.body.end());
        ++count;
    }
    close();
    P.pending.clear();
    return out;
}

bool SnapshotSender::onAckPacket(PlayerId peer, const uint8_t* data, size_t size) {
    if (!checkCrc(data, size)) return false;
    Reader r{data, data + size - 4};
    if (r.u8() != static_cast<uint8_t>(MsgKind::SnapshotAck)) return false;
    uint64_t n = r.varu();
    if (!r.ok || n > 4096) return false;
    struct A { NetId id; OwnerEpoch ep; uint32_t seq; };
    std::vector<A> acks;
    for (uint64_t i = 0; i < n && r.ok; ++i) { A a; a.id = r.varu32(); a.ep = r.varu32(); a.seq = r.varu32(); acks.push_back(a); }
    uint64_t m = r.ok ? r.varu() : 0;
    if (!r.ok || m > 4096) return false;
    std::vector<NetId> need;
    for (uint64_t i = 0; i < m && r.ok; ++i) need.push_back(r.varu32());
    if (!r.ok) return false;
    auto pit = peers_.find(peer);
    if (pit == peers_.end()) return true;
    for (const auto& a : acks) {
        auto t = pit->second.tracks.find(a.id);
        if (t == pit->second.tracks.end()) continue;
        Track& T = t->second;
        if (T.epoch != a.ep || liveFaults().ackIgnored) continue;
        auto e = std::find_if(T.inflight.begin(), T.inflight.end(), [&](const Entry& x) { return x.seq == a.seq; });
        if (e == T.inflight.end()) continue;
        if (!T.haveAcked || serialLess(T.acked.seq, a.seq)) {
            T.acked = *e;
            T.haveAcked = true;
            T.forceFull = false;
            ++stats_.acked;
        }
        while (!T.inflight.empty() && serialLE(T.inflight.front().seq, T.acked.seq)) T.inflight.pop_front();
    }
    for (NetId id : need) {
        auto t = pit->second.tracks.find(id);
        if (t == pit->second.tracks.end()) continue;
        t->second.haveAcked = false;
        t->second.forceFull = false;   // haveAcked == false already forces a full snapshot
        t->second.inflight.clear();
        ++stats_.resyncs;
    }
    return true;
}

void SnapshotSender::forgetPeer(PlayerId peer) { peers_.erase(peer); }
void SnapshotSender::forget(PlayerId peer, NetId id) {
    auto it = peers_.find(peer);
    if (it == peers_.end()) return;
    it->second.tracks.erase(id);
    auto& pend = it->second.pending;
    pend.erase(std::remove_if(pend.begin(), pend.end(), [&](const Pending& p) { return p.id == id; }), pend.end());
}
uint32_t SnapshotSender::ackedSeq(PlayerId peer, NetId id) const {
    auto it = peers_.find(peer);
    if (it == peers_.end()) return 0;
    auto t = it->second.tracks.find(id);
    if (t == it->second.tracks.end() || !t->second.haveAcked) return 0;
    return t->second.acked.seq;
}

// ---- receiver ---------------------------------------------------------------------------------------------------
void SnapshotReceiver::setAuthority(NetId id, OwnerEpoch epoch, PlayerId owner) {
    Auth& a = auth_[id];
    if (serialLess(epoch, a.epoch)) return;   // an older assignment never overwrites a newer one
    a.epoch = epoch; a.owner = owner;
}
void SnapshotReceiver::clearAuthority(NetId id) {
    auto it = auth_.find(id);
    if (it != auth_.end()) it->second.owner = kNoPlayer;   // keep the epoch floor
}
void SnapshotReceiver::forget(NetId id) { tracks_.erase(id); auth_.erase(id); }
OwnerEpoch SnapshotReceiver::epochOf(NetId id) const { auto it = tracks_.find(id); return it == tracks_.end() ? 0 : it->second.epoch; }
uint32_t SnapshotReceiver::lastSeqOf(NetId id) const { auto it = tracks_.find(id); return it == tracks_.end() ? 0 : it->second.lastSeq; }

std::vector<ReceivedSnapshot> SnapshotReceiver::receive(PlayerId from, const uint8_t* data, size_t size) {
    std::vector<ReceivedSnapshot> out;
    auto bad = [&]() { ReceivedSnapshot r; r.verdict = Verdict::Malformed; ++stats_.malformed; out.push_back(r); };
    if (!data || !checkCrc(data, size)) { bad(); return out; }
    Reader r{data, data + size - 4};
    if (r.u8() != static_cast<uint8_t>(MsgKind::SnapshotBatch)) { bad(); return out; }
    unsigned count = r.u8();
    for (unsigned n = 0; n < count; ++n) {
        NetId id = r.varu32();
        OwnerEpoch epoch = r.varu32();
        uint32_t seq = r.varu32(), baseSeq = r.varu32(), tick = r.varu32();
        uint8_t mask = r.u8();
        if (!r.ok || (mask & 0x80)) { bad(); return out; }
        CellId cell = 0;
        int64_t dpos[3] = {0, 0, 0}, dvel[3] = {0, 0, 0};
        uint16_t rot[3] = {0, 0, 0};
        uint8_t loco = 0, flags = 0, alert = 0, action = 0, actionSeq = 0, animExt = 0;
        uint32_t target = 0, package = 0;
        std::vector<AvValue> avs;
        if (mask & kMaskCell) cell = r.varu32();
        if (mask & kMaskPos) for (int i = 0; i < 3; ++i) dpos[i] = r.vari();
        if (mask & kMaskRot) for (int i = 0; i < 3; ++i) rot[i] = r.u16();
        if (mask & kMaskVel) for (int i = 0; i < 3; ++i) dvel[i] = r.vari();
        if (mask & kMaskAnim) {
            loco = r.u8(); flags = r.u8();
            if ((flags & kAnimFlagsReserved) && !liveFaults().acceptBadAnimExt) r.ok = false;
            if (flags & kAnimFlagExt) {
                action = r.u8(); actionSeq = r.u8(); animExt = r.u8();
                if (!liveFaults().acceptBadAnimExt) {
                    if (animExt & 0xC0) r.ok = false;                                  // reserved extension bits
                    if (action == 0 && actionSeq == 0 && animExt == 0) r.ok = false;   // an all-zero extension is never sent (canonical form)
                }
            }
            flags &= static_cast<uint8_t>(~kAnimFlagsReserved);   // (only reachable with the fault on: keeps the stored state canonical)
            flags &= static_cast<uint8_t>(~kAnimFlagExt);
        }
        if (mask & kMaskAi) { target = r.varu32(); package = r.varu32(); alert = r.u8(); }
        if (mask & kMaskAv) {
            uint64_t k = r.varu();
            if (k > kMaxAvPerEntry) r.ok = false;
            for (uint64_t i = 0; i < k && r.ok; ++i) { AvValue v; v.av = r.u8(); v.value = r.vari32(); avs.push_back(v); }
        }
        if (!r.ok || id == 0 || seq == 0 || (baseSeq == 0 && mask != kMaskAll)) { bad(); return out; }

        ReceivedSnapshot res;
        res.snap.netId = id; res.snap.ownerEpoch = epoch; res.snap.seq = seq; res.snap.tick = tick;
        auto verdict = [&](Verdict v) {
            res.verdict = v;
            switch (v) {
                case Verdict::StaleEpoch: ++stats_.staleEpoch; break;
                case Verdict::StaleSeq: ++stats_.staleSeq; break;
                case Verdict::WrongOwner: ++stats_.wrongOwner; break;
                case Verdict::NeedBaseline: ++stats_.needBaseline; break;
                default: break;
            }
            out.push_back(res);
        };

        // Authority.
        auto au = auth_.find(id);
        if (au != auth_.end() && !liveFaults().acceptStaleEpoch && serialLess(epoch, au->second.epoch)) { verdict(Verdict::StaleEpoch); continue; }
        if (au != auth_.end() && cfg_.checkSenderIsOwner && !liveFaults().acceptNonOwner && epoch == au->second.epoch &&
            au->second.owner != kNoPlayer && au->second.owner != from) { verdict(Verdict::WrongOwner); continue; }
        // A higher epoch than the recorded one is accepted provisionally only by a receiver that merely hears the host's relay (it may see
        // the new owner's stream a moment before the Commit). The host itself assigns every epoch and records it before the new owner can
        // publish, so a higher epoch from anyone but the recorded owner is a forgery or a bug: taking it would raise the track's epoch and lock
        // the real owner out ("stale") until its own epoch caught up.
        if (au != auth_.end() && cfg_.checkSenderIsOwner && !liveFaults().acceptNonOwner && serialLess(au->second.epoch, epoch) &&
            au->second.owner != kNoPlayer && au->second.owner != from) { verdict(Verdict::WrongOwner); continue; }
        auto tr = tracks_.find(id);
        if (tr != tracks_.end() && !liveFaults().acceptStaleEpoch && serialLess(epoch, tr->second.epoch)) { verdict(Verdict::StaleEpoch); continue; }
        if (tr == tracks_.end()) {
            if (tracks_.size() >= cfg_.maxActors) { bad(); return out; }
            tr = tracks_.emplace(id, Track{}).first; tr->second.epoch = epoch;
        }
        Track& T = tr->second;
        if (serialLess(T.epoch, epoch)) { T.epoch = epoch; T.lastSeq = 0; T.hist.clear(); }
        if (T.lastSeq != 0 && serialLE(seq, T.lastSeq) && !liveFaults().acceptStaleSeq) {
            // A duplicate of an applied snapshot is re-acked (the first ack may have been lost); nothing else.
            if (T.hist.count(seq)) { auto& h = acks_[from].highest[id]; if (h.second == 0 || serialLess(h.second, seq) || h.first != epoch) h = {epoch, seq}; }
            verdict(Verdict::StaleSeq);
            continue;
        }

        // Rebuild the state.
        QState base;
        if (baseSeq != 0) {
            auto h = T.hist.find(baseSeq);
            if (h == T.hist.end()) {
                if (!liveFaults().noResyncRequest) { acks_[from].needFull.insert(id); ++stats_.resyncRequests; }
                verdict(Verdict::NeedBaseline);
                continue;
            }
            base = h->second;
        }
        QState q = base;
        auto addChecked = [&](int64_t b, int64_t d, int32_t& dst) {
            int64_t v = b + d;
            if (v < INT32_MIN || v > INT32_MAX) { r.ok = false; return; }
            dst = static_cast<int32_t>(v);
        };
        if (mask & kMaskCell) q.cell = cell;
        if (mask & kMaskPos) for (int i = 0; i < 3; ++i) addChecked(base.pos[i], dpos[i], q.pos[i]);
        if (mask & kMaskRot) for (int i = 0; i < 3; ++i) q.rot[i] = rot[i];
        if (mask & kMaskVel) for (int i = 0; i < 3; ++i) addChecked(base.vel[i], dvel[i], q.vel[i]);
        if (mask & kMaskAnim) { q.loco = loco; q.animFlags = flags; q.action = action; q.actionSeq = actionSeq; q.animExt = animExt; }
        if (mask & kMaskAi) { q.target = target; q.package = package; q.alert = alert; }
        for (const auto& v : avs) q.av[v.av] = v.value;
        if (!r.ok) { bad(); return out; }

        T.lastSeq = seq;
        T.hist[seq] = q;
        while (T.hist.size() > cfg_.historyDepth) T.hist.erase(T.hist.begin());
        auto& h = acks_[from].highest[id];
        if (h.second == 0 || h.first != epoch || serialLess(h.second, seq)) h = {epoch, seq};
        ++stats_.accepted;
        res.verdict = Verdict::Accepted;
        res.snap = dequantize(q, id, epoch, seq, tick);
        res.snap.avDeltas = avs;
        for (const auto& kv : q.av) res.avAll.push_back({kv.first, kv.second});
        std::sort(res.snap.avDeltas.begin(), res.snap.avDeltas.end(), [](const AvValue& a, const AvValue& b) { return a.av < b.av; });
        out.push_back(std::move(res));
    }
    return out;
}

Bytes SnapshotReceiver::takeAcks(PlayerId to) {
    auto it = acks_.find(to);
    if (it == acks_.end() || (it->second.highest.empty() && it->second.needFull.empty())) return {};
    Bytes b;
    Writer w{b};
    w.u8(static_cast<uint8_t>(MsgKind::SnapshotAck));
    w.varu(it->second.highest.size());
    for (const auto& kv : it->second.highest) { w.varu(kv.first); w.varu(kv.second.first); w.varu(kv.second.second); }
    w.varu(it->second.needFull.size());
    for (NetId id : it->second.needFull) w.varu(id);
    appendCrc(b);
    it->second.highest.clear();
    it->second.needFull.clear();
    return b;
}

// ---- interpolation buffer ---------------------------------------------------------------------------------------
namespace {
float lerpf(float a, float b, float t) { return a + (b - a) * t; }
float lerpAngle(float a, float b, float t) {
    const float twoPi = static_cast<float>(kTwoPi);
    float d = std::fmod(b - a, twoPi);
    if (d > twoPi / 2) d -= twoPi;
    if (d < -twoPi / 2) d += twoPi;
    float r = a + d * t;
    r = std::fmod(r, twoPi);
    if (r < 0) r += twoPi;
    return r;
}
Vec3 lerp3(const Vec3& a, const Vec3& b, float t) { return {lerpf(a.x, b.x, t), lerpf(a.y, b.y, t), lerpf(a.z, b.z, t)}; }
}  // namespace

void InterpBuffer::reset() { snaps_.clear(); offsets_.clear(); haveShown_ = false; haveTarget_ = false; smoothing_ = false; corr_ = Vec3{}; intervalMs_ = 0; }

bool InterpBuffer::push(const ActorSnapshot& s, uint32_t arrivalMs) {
    if (!snaps_.empty() && !serialLess(snaps_.back().tick, s.tick)) return false;
    if (!snaps_.empty()) {
        double iv = static_cast<double>(static_cast<uint32_t>(s.tick - snaps_.back().tick));
        intervalMs_ = intervalMs_ == 0 ? iv : intervalMs_ * 0.8 + iv * 0.2;
    }
    Snap n{s.tick, s.cell, s.pos, s.rot, s.vel, s.anim, s.ai};
    snaps_.push_back(n);
    while (snaps_.size() > cfg_.maxSnapshots) snaps_.pop_front();
    offsets_.push_back(static_cast<int32_t>(arrivalMs - s.tick));
    while (offsets_.size() > cfg_.offsetWindow) offsets_.pop_front();
    return true;
}

InterpOut InterpBuffer::sample(uint32_t nowMs, const ActualPose* actual) {
    InterpOut o;
    if (snaps_.empty()) return o;

    // Clock offset: the smallest (arrival - tick) in the window, taken relative to the newest sample (wrap safe).
    int32_t ref = static_cast<int32_t>(offsets_.back());
    int32_t best = 0;
    for (int64_t v : offsets_) { int32_t d = static_cast<int32_t>(static_cast<int32_t>(v) - ref); if (d < best) best = d; }
    int32_t offset = ref + best;
    uint32_t delay = cfg_.delayMs;
    if (cfg_.adaptiveDelay && intervalMs_ > 0) {
        uint32_t want = static_cast<uint32_t>(intervalMs_ * 1.25 + 0.5);
        want = std::min(want, cfg_.maxAdaptiveDelayMs);
        delay = std::max(delay, want);
    }
    uint32_t rt = nowMs - static_cast<uint32_t>(offset) - delay;   // render time on the sender clock

    const Snap& newest = snaps_.back();
    const float snapUnits = cfg_.snapMetres * cfg_.unitsPerMetre;
    Vec3 tpos, trot, tvel;
    CellId tcell;
    AnimState tanim;
    AiState tai;
    InterpState st = InterpState::Interpolating;
    int32_t rel = static_cast<int32_t>(rt - newest.tick);
    if (rel >= 0 && !liveFaults().extrapolateOnly) {
        // Past the newest snapshot: dead-reckon, capped.
        uint32_t past = static_cast<uint32_t>(rel);
        uint32_t dt = past;
        if (!liveFaults().noExtrapolationCap && dt > cfg_.maxExtrapolateMs) dt = cfg_.maxExtrapolateMs;
        st = past == 0 ? InterpState::Interpolating : (past <= cfg_.maxExtrapolateMs || liveFaults().noExtrapolationCap ? InterpState::Extrapolating : InterpState::Holding);
        float sec = static_cast<float>(dt) / 1000.0f;
        tpos = {newest.pos.x + newest.vel.x * sec, newest.pos.y + newest.vel.y * sec, newest.pos.z + newest.vel.z * sec};
        trot = newest.rot; tvel = newest.vel; tcell = newest.cell; tanim = newest.anim; tai = newest.ai;
    } else if (liveFaults().extrapolateOnly) {
        // Mutant: always dead-reckon from the newest snapshot, even for render times inside the buffer.
        int32_t d = static_cast<int32_t>(rt - newest.tick);
        float sec = static_cast<float>(d) / 1000.0f;
        tpos = {newest.pos.x + newest.vel.x * sec, newest.pos.y + newest.vel.y * sec, newest.pos.z + newest.vel.z * sec};
        trot = newest.rot; tvel = newest.vel; tcell = newest.cell; tanim = newest.anim; tai = newest.ai;
    } else {
        // Find the first snapshot strictly after rt.
        size_t i = 0;
        while (i < snaps_.size() && serialLE(snaps_[i].tick, rt)) ++i;
        if (i == 0) {
            const Snap& a = snaps_.front();     // before the first snapshot: hold it
            st = InterpState::Holding;
            tpos = a.pos; trot = a.rot; tvel = a.vel; tcell = a.cell; tanim = a.anim; tai = a.ai;
        } else {
            const Snap& a = snaps_[i - 1];
            const Snap& b = snaps_[i];
            bool discontinuity = (a.cell != b.cell && !liveFaults().snapIgnoresCell) ||
                                 (!liveFaults().noSnap && distance(a.pos, b.pos) > snapUnits);
            if (discontinuity) {
                tpos = a.pos; trot = a.rot; tvel = a.vel; tcell = a.cell;   // hold until b's time, then jump
            } else {
                float span = static_cast<float>(static_cast<uint32_t>(b.tick - a.tick));
                float t = span > 0 ? static_cast<float>(static_cast<uint32_t>(rt - a.tick)) / span : 1.0f;
                t = std::min(1.0f, std::max(0.0f, t));
                tpos = lerp3(a.pos, b.pos, t);
                trot = {lerpAngle(a.rot.x, b.rot.x, t), lerpAngle(a.rot.y, b.rot.y, t), lerpAngle(a.rot.z, b.rot.z, t)};
                tvel = lerp3(a.vel, b.vel, t);
                tcell = t >= 1.0f ? b.cell : a.cell;
            }
            tanim = a.anim; tai = a.ai;
        }
    }

    // Correction against where the puppet actually is. The outstanding offset `corr_` lives in TARGET space (shown = target +
    // corr_), so easing it out never lags behind a moving target.
    uint32_t dtMs = haveShown_ ? std::min<uint32_t>(nowMs - lastSampleMs_, 1000u) : 0;
    lastSampleMs_ = nowMs;
    bool haveRef = actual != nullptr || haveShown_;
    CellId refCell = actual ? actual->cell : shownCell_;
    o.state = st;
    if (!haveRef) {
        shown_ = tpos; shownCell_ = tcell; haveShown_ = true; corr_ = Vec3{}; smoothing_ = false;
    } else {
        Vec3 dev = actual ? Vec3{actual->pos.x - shown_.x, actual->pos.y - shown_.y, actual->pos.z - shown_.z} : Vec3{};   // engine moved it
        // Full offset between where the puppet is and where it should be.
        Vec3 full = {shown_.x + dev.x - tpos.x, shown_.y + dev.y - tpos.y, shown_.z + dev.z - tpos.z};
        // A target that jumped by more than 2 m in one step (stream resumed after a gap) is a new error, not motion.
        float jump = haveTarget_ ? distance(tpos, tposPrev_) : 0.0f;
        const float smoothUnits = cfg_.smoothMetres * cfg_.unitsPerMetre;
        Vec3 c = (smoothing_ && jump <= smoothUnits) ? Vec3{corr_.x + dev.x, corr_.y + dev.y, corr_.z + dev.z} : full;
        float errUnits = distance(c, Vec3{});
        o.errorMetres = errUnits / cfg_.unitsPerMetre;
        bool otherCell = refCell != tcell && !liveFaults().snapIgnoresCell;
        if (otherCell || (!liveFaults().noSnap && errUnits > snapUnits)) {
            shown_ = tpos; shownCell_ = tcell; corr_ = Vec3{}; smoothing_ = false;
            o.snapped = true; o.state = InterpState::Snapped;
        } else if (!liveFaults().noSmooth &&
                   (errUnits > smoothUnits || (smoothing_ && errUnits > cfg_.smoothEndMetres * cfg_.unitsPerMetre))) {
            float k = std::exp(-static_cast<float>(dtMs) / static_cast<float>(cfg_.smoothMs));
            corr_ = {c.x * k, c.y * k, c.z * k};
            shown_ = {tpos.x + corr_.x, tpos.y + corr_.y, tpos.z + corr_.z};
            shownCell_ = tcell; smoothing_ = true; o.state = InterpState::Smoothing;
        } else {
            shown_ = tpos; shownCell_ = tcell; corr_ = Vec3{}; smoothing_ = false;
        }
        haveShown_ = true;
    }
    tposPrev_ = tpos;
    haveTarget_ = true;
    o.cell = shownCell_;
    o.pos = shown_;
    o.rot = trot; o.vel = tvel; o.anim = tanim; o.ai = tai;
    return o;
}

// ---- interest ---------------------------------------------------------------------------------------------------
void InterestManager::setObserver(const Observer& o) { obs_[o.player] = o; }
void InterestManager::removeObserver(PlayerId p) {
    obs_.erase(p);
    for (auto it = links_.begin(); it != links_.end();) { if (it->first.first == p) it = links_.erase(it); else ++it; }
}
void InterestManager::setActor(const InterestActor& a) { actors_[a.id] = a; }
void InterestManager::removeActor(NetId id) {
    actors_.erase(id);
    for (auto it = links_.begin(); it != links_.end();) { if (it->first.second == id) it = links_.erase(it); else ++it; }
}
uint32_t InterestManager::periodMs(Tier t) const {
    if (t == Tier::Near) return liveFaults().nearRateAsFar ? cfg_.farPeriodMs : cfg_.nearPeriodMs;
    if (t == Tier::Far) return cfg_.farPeriodMs;
    return 0;
}

Tier InterestManager::tierOf(PlayerId player, NetId id) {
    auto o = obs_.find(player);
    auto a = actors_.find(id);
    if (o == obs_.end() || a == actors_.end()) return Tier::None;
    if (a->second.owner == player) return Tier::None;
    Link& L = links_[{player, id}];
    Tier t = Tier::None;
    if (o->second.cell == a->second.cell) {
        float d = distance(o->second.pos, a->second.pos);
        float limit = L.tier == Tier::Near ? cfg_.nearRadius * cfg_.hysteresis : cfg_.nearRadius;
        t = d <= limit ? Tier::Near : Tier::Far;
    } else if (std::find(o->second.adjacent.begin(), o->second.adjacent.end(), a->second.cell) != o->second.adjacent.end()) {
        t = Tier::Far;
    }
    if (t == Tier::Far && liveFaults().noFarTier) t = Tier::None;
    if (t == Tier::None) L.sent = false;
    L.tier = t;
    return t;
}

std::vector<NetId> InterestManager::collect(PlayerId player, uint32_t nowMs) {
    std::vector<NetId> out;
    for (const auto& kv : actors_) {
        Tier t = tierOf(player, kv.first);
        if (t == Tier::None) continue;
        Link& L = links_[{player, kv.first}];
        uint32_t period = periodMs(t);
        if (L.sent) {
            uint32_t since = nowMs - L.lastSent;
            if (since < period) continue;
            // Keep the long-run rate exact when the caller polls on frame boundaries; resync after a long gap.
            L.lastSent = since < period * 2 ? L.lastSent + period : nowMs;
        } else {
            L.lastSent = nowMs; L.sent = true;
        }
        out.push_back(kv.first);
    }
    return out;
}

}  // namespace fnvcoop::live
