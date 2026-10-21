#include "vats/vats_wire.h"

#include <cmath>

#include "fnvcoop/wire.h"
#include "../../tools/mut/fnvcoop_mut.h"

namespace nvcoop::vats {

namespace {

void ioMode(fnvcoop::Archive& a, Mode& m) {
    uint8_t v = static_cast<uint8_t>(m);
    a.u8(v);
    if (a.reading()) {
        if (v > 4) { a.fail(); return; }
        m = static_cast<Mode>(v);
    }
}

void ioLimb(fnvcoop::Archive& a, LimbId& l) {
    uint8_t v = static_cast<uint8_t>(static_cast<int8_t>(l));
    a.u8(v);
    if (a.reading()) {
        if (v == 255) { l = LimbId::None; return; }
        if (!validLimb(v)) { a.fail(); return; }
        l = static_cast<LimbId>(v);
    }
}

void ioFloat(fnvcoop::Archive& a, float& f) {
    a.f32(f);
    if (a.reading() && !std::isfinite(f)) a.fail();
}

void io(fnvcoop::Archive& a, MConfig& m) {
    a.varu32(m.epoch);
    uint8_t flags = 0;
    if (!a.reading()) {
        flags = static_cast<uint8_t>((m.s.realtime ? 1 : 0) | (m.s.partySlowMo ? 2 : 0) | (m.s.autoFire ? 4 : 0) | (m.s.killCam ? 8 : 0) |
                                     (m.s.guardFriendly ? 16 : 0) | (m.s.perks ? 32 : 0));
    }
    a.u8(flags);
    ioFloat(a, m.s.slowScale);
    a.varu32(m.s.slowMaxMs);
    a.varu32(m.s.slowCooldownMs);
    a.varu32(m.s.autoFireDelayMs);
    if (a.reading()) {
        if (flags & ~0x3Fu) { a.fail(); return; }
        m.s.realtime = flags & 1; m.s.partySlowMo = flags & 2; m.s.autoFire = flags & 4; m.s.killCam = flags & 8; m.s.guardFriendly = flags & 16; m.s.perks = flags & 32;
        if (!FNV_MUT(841)) m.s = sanitized(m.s);   // 841: a peer's numbers are used as received (no clamp)
    }
}

void io(fnvcoop::Archive& a, MPhase& m) {
    a.u8(m.player);
    ioMode(a, m.mode);
    ioLimb(a, m.limb);
    a.u8(m.queued);
    a.varu32(m.target);
    a.varu32(m.seq);
}

void io(fnvcoop::Archive& a, MAction& m) {
    a.u8(m.shooter);
    a.varu32(m.victim);
    ioLimb(a, m.limb);
    a.u8(m.queued);
    a.varu32(m.seq);
}

void io(fnvcoop::Archive& a, MSlowMo& m) {
    SlowMoGrant& g = m.g;
    a.varu32(g.epoch);
    a.varu32(g.seq);
    a.boolean(g.active);
    ioFloat(a, g.scale);
    a.varu32(g.remainingMs);
    a.u8(g.cause);
}

template <size_t I = 0>
bool readBody(fnvcoop::Archive& a, size_t idx, Body& out) {
    if constexpr (I == std::variant_size_v<Body>) {
        (void)a; (void)idx; (void)out;
        return false;
    } else {
        if (idx == I) {
            std::variant_alternative_t<I, Body> v{};
            io(a, v);
            if (!a.ok()) return false;
            out = std::move(v);
            return true;
        }
        return readBody<I + 1>(a, idx, out);
    }
}

}  // namespace

const char* bodyName(const Body& b) {
    static const char* names[] = {"Config", "Phase", "Action", "SlowMo"};
    return names[b.index()];
}

Kind kindOf(const Body& b) { return static_cast<Kind>(b.index() + 1); }

Bytes encodeEnvelope(const Envelope& e) {
    fnvcoop::Archive a = fnvcoop::Archive::writer();
    uint8_t src = e.src, dest = e.dest, hops = e.hops, idx = static_cast<uint8_t>(e.body.index());
    a.u8(src); a.u8(dest); a.u8(hops); a.u8(idx);
    Body copy = e.body;
    std::visit([&](auto& m) { io(a, m); }, copy);
    Bytes body = a.finish();
    Bytes out;
    out.reserve(body.size() + 2);
    out.push_back(kChannelVats);
    out.push_back(kVatsModuleWire);
    out.insert(out.end(), body.begin(), body.end());
    return out;
}

bool decodeEnvelope(const uint8_t* data, size_t size, Envelope& out) {
    if (!data || size < 3 || data[0] != kChannelVats || data[1] != kVatsModuleWire) return false;
    fnvcoop::Archive a = fnvcoop::Archive::reader(data + 2, size - 2);
    uint8_t idx = 0;
    a.u8(out.src); a.u8(out.dest); a.u8(out.hops); a.u8(idx);
    if (!a.ok() || idx >= std::variant_size_v<Body>) return false;
    if (!readBody(a, idx, out.body)) return false;
    if (FNV_MUT(842)) return a.ok();   // 842: trailing garbage after a valid message is accepted
    return a.ok() && a.atEnd();
}

}  // namespace nvcoop::vats
