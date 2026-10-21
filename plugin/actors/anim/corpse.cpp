#include "corpse.h"

#include <cmath>
#include <cstring>

#include "fnvcoop_mut.h"

namespace nvcoop::actors::anim {

namespace {
float horiz(const Vec3& a, const Vec3& b) { return std::sqrt((a.x - b.x) * (a.x - b.x) + (a.y - b.y) * (a.y - b.y)); }
bool finite3(const Vec3& v) { return std::isfinite(v.x) && std::isfinite(v.y) && std::isfinite(v.z); }
}  // namespace

// Payload layout (23 bytes, little endian): kind(1) version(1) cell(4) x y z yaw (4 floats) flags(1: bit0 ragdoll, bit1 forced).
// Hand written (not fnvcoop::Archive) so the FIRST byte is the event kind: ActorSync hands the payload over opaque and the system dispatches on it.
namespace {
void put32(std::vector<uint8_t>& b, uint32_t v) { for (int i = 0; i < 4; ++i) b.push_back(static_cast<uint8_t>(v >> (8 * i))); }
void putF(std::vector<uint8_t>& b, float f) { uint32_t v; std::memcpy(&v, &f, 4); put32(b, v); }
uint32_t get32(const uint8_t* p) { return static_cast<uint32_t>(p[0]) | (static_cast<uint32_t>(p[1]) << 8) | (static_cast<uint32_t>(p[2]) << 16) | (static_cast<uint32_t>(p[3]) << 24); }
float getF(const uint8_t* p) { const uint32_t v = get32(p); float f; std::memcpy(&f, &v, 4); return f; }
constexpr size_t kCorpseBytes = 23;
}  // namespace

std::vector<uint8_t> encodeCorpse(const CorpsePose& p) {
    std::vector<uint8_t> b;
    b.reserve(kCorpseBytes);
    b.push_back(kAnimEventCorpse); b.push_back(kCorpseVersion);
    put32(b, p.cell);
    putF(b, p.pos.x); putF(b, p.pos.y); putF(b, p.pos.z); putF(b, p.yawRad);
    b.push_back(static_cast<uint8_t>((p.ragdoll ? 1 : 0) | (p.forced ? 2 : 0)));
    return b;
}

bool decodeCorpse(const uint8_t* data, size_t size, CorpsePose& out) {
    if (!data || size != kCorpseBytes) return false;
    if (data[0] != kAnimEventCorpse || data[1] != kCorpseVersion) return false;
    if (data[22] & 0xFC) return false;   // reserved flag bits
    CorpsePose p;
    p.cell = get32(data + 2);
    p.pos.x = getF(data + 6); p.pos.y = getF(data + 10); p.pos.z = getF(data + 14); p.yawRad = getF(data + 18);
    p.ragdoll = (data[22] & 1) != 0; p.forced = (data[22] & 2) != 0;
    if (!finite3(p.pos) || !std::isfinite(p.yawRad)) return false;
    out = p;
    return true;
}

void CorpseWatcher::begin(NetId id, uint32_t nowMs) {
    if (w_.count(id)) return;
    W w;
    w.since = nowMs; w.lastPoll = nowMs - cfg_.pollMs;
    w_[id] = w;
}

bool CorpseWatcher::due(NetId id, uint32_t nowMs) const {
    auto it = w_.find(id);
    return it != w_.end() && elapsed(nowMs, it->second.lastPoll) >= cfg_.pollMs;
}

std::vector<NetId> CorpseWatcher::ids() const {
    std::vector<NetId> v;
    for (auto& kv : w_) v.push_back(kv.first);
    return v;
}

bool CorpseWatcher::feed(NetId id, CellId cell, const Vec3& pos, float yawRad, bool ragdoll, uint32_t nowMs, CorpsePose* out) {
    auto it = w_.find(id);
    if (it == w_.end()) return false;
    W& w = it->second;
    w.lastPoll = nowMs;
    if (!w.have || w.cell != cell || horiz(w.anchor, pos) > cfg_.restUnits || std::fabs(w.anchor.z - pos.z) > cfg_.restUnits) {
        w.anchor = pos; w.anchorSince = nowMs; w.cell = cell; w.have = true;
    }
    w.last = pos; w.lastYaw = yawRad; w.ragdoll = ragdoll;
    const uint32_t age = elapsed(nowMs, w.since);
    const bool rested = elapsed(nowMs, w.anchorSince) >= cfg_.restMs && age >= cfg_.minRagdollMs;
    const bool timeout = age >= cfg_.maxWaitMs;
    bool report = rested || timeout;
    if (FNV_MUT(20)) report = true;                 // twin 20: the pose is taken the moment the actor dies, not where the body lands
    if (FNV_MUT(21)) report = false;                // twin 21: the owner never sends the resting place
    if (!report) return false;
    if (out) {
        out->cell = cell; out->pos = pos; out->yawRad = yawRad; out->ragdoll = ragdoll; out->forced = !rested;
    }
    if (!FNV_MUT(22)) w_.erase(it);                 // twin 22: keeps watching, so the same corpse is announced again and again
    return true;
}

bool CorpseWatcher::flush(NetId id, uint32_t nowMs, CorpsePose* out) {
    (void)nowMs;
    auto it = w_.find(id);
    if (it == w_.end()) return false;
    const W w = it->second;
    w_.erase(it);
    if (!w.have) return false;
    if (out) { out->cell = w.cell; out->pos = w.last; out->yawRad = w.lastYaw; out->ragdoll = w.ragdoll; out->forced = true; }
    return true;
}

}  // namespace nvcoop::actors::anim
