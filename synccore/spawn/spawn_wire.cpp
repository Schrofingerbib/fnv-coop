#include "spawn/spawn_wire.h"
#include "fnvcoop/wire.h"
#include "fnvcoop_mut.h"

namespace fnvcoop::spawn {

namespace {

template <class E>
void ioEnum(Archive& a, E& e, uint8_t limit) {
    uint8_t v = static_cast<uint8_t>(e);
    a.u8(v);
    if (a.reading()) {
        if (v >= limit) { a.fail(); return; }
        e = static_cast<E>(v);
    }
}

void ioMembers(Archive& a, std::vector<Member>& v) {
    size_t n = v.size();
    // twin 19: the member count is not bounded on read (the decoder accepts a hostile length)
    if (!a.count(n, FNV_MUT(19) ? static_cast<size_t>(1u << 20) : kMaxMembers)) return;
    if (a.reading()) v.assign(n, Member{});
    for (auto& m : v) io(a, m);
}

template <size_t I = 0>
bool readBody(Archive& a, size_t idx, Body& out);

void ioBody(Archive& a, MRoll& m) { io(a, m.r); }
void ioBody(Archive& a, MVerdict& m) { io(a, m.v); }
void ioBody(Archive& a, MSite& m) { io(a, m.s); }
void ioBody(Archive& a, MOwn& m) { fnvcoop::io(a, m.m); }
void ioBody(Archive& a, MDigest& m) { a.u64(m.digest); a.varu32(m.sites); }
void ioBody(Archive& a, MResync& m) { a.u8(m.all); }

template <size_t I>
bool readBody(Archive& a, size_t idx, Body& out) {
    if constexpr (I == std::variant_size_v<Body>) {
        (void)a; (void)idx; (void)out;
        return false;
    } else {
        if (idx == I) {
            std::variant_alternative_t<I, Body> v{};
            ioBody(a, v);
            if (!a.ok()) return false;
            out = std::move(v);
            return true;
        }
        return readBody<I + 1>(a, idx, out);
    }
}

}  // namespace

void io(Archive& a, Vec3& v) { a.f32(v.x); a.f32(v.y); a.f32(v.z); }

void io(Archive& a, SiteDef& v) {
    a.varu32(v.id);
    ioEnum(a, v.kind, static_cast<uint8_t>(SiteKind::Count_));
    fnvcoop::io(a, v.anchor);
    fnvcoop::io(a, v.cellForm);
    a.varu32(v.cell);
    io(a, v.pos);
    a.boolean(v.interior);
    a.varu32(v.respawnHours);
}

void io(Archive& a, Member& v) {
    a.varu32(v.netId);
    a.boolean(v.created);
    a.boolean(v.dead);
    a.varu32(v.handle);
    fnvcoop::io(a, v.base);
    a.varu32(v.cell);
    io(a, v.pos);
}

void io(Archive& a, RollReport& v) {
    io(a, v.def);
    a.u8(v.roller);
    a.varu32(v.epoch);
    a.varu32(v.rollId);
    a.boolean(v.none);
    fnvcoop::io(a, v.pick);
    ioMembers(a, v.members);
}

void io(Archive& a, VerdictMsg& v) {
    a.varu32(v.site);
    a.varu32(v.rollId);
    ioEnum(a, v.verdict, static_cast<uint8_t>(Verdict::Count_));
    a.varu32(v.epoch);
    a.u8(v.owner);
    a.varu32(v.generation);
    a.boolean(v.none);
    fnvcoop::io(a, v.pick);
    ioMembers(a, v.members);
}

void io(Archive& a, SiteMsg& v) {
    io(a, v.def);
    ioEnum(a, v.state, 4);
    a.varu32(v.generation);
    a.varu32(v.rev);
    a.boolean(v.none);
    fnvcoop::io(a, v.pick);
    a.varu(v.clearedAtGame);
    a.varu(v.respawnAtGame);
    ioMembers(a, v.members);
}

const char* bodyName(const Body& b) {
    static const char* names[] = {"Roll", "Verdict", "Site", "Own", "Digest", "Resync"};
    return names[b.index()];
}

std::vector<uint8_t> encodeEnvelope(const Envelope& e) {
    Archive a = Archive::writer();
    uint8_t ver = kSpawnWireVersion, src = e.src, idx = static_cast<uint8_t>(e.body.index());
    a.u8(ver); a.u8(src); a.u8(idx);
    Body copy = e.body;
    std::visit([&](auto& m) { ioBody(a, m); }, copy);
    std::vector<uint8_t> body = a.finish();
    if (body.empty()) return {};   // a field failed to encode (too long a string): nothing to send
    std::vector<uint8_t> out;
    out.reserve(body.size() + 1);
    out.push_back(kChannelSpawn);
    out.insert(out.end(), body.begin(), body.end());
    return out;
}

bool decodeEnvelope(const uint8_t* data, size_t size, Envelope& out) {
    if (!data || size < 2 || data[0] != kChannelSpawn) return false;
    Archive a = Archive::reader(data + 1, size - 1);
    uint8_t ver = 0, idx = 0, src = 0;
    a.u8(ver);
    if (!a.ok() || ver != kSpawnWireVersion) return false;
    a.u8(src); a.u8(idx);
    if (!a.ok() || idx >= std::variant_size_v<Body>) return false;
    Envelope e;
    e.src = src;
    if (!readBody(a, idx, e.body)) return false;
    if (!a.ok()) return false;
    if (!FNV_MUT(19) && !a.atEnd()) return false;   // twin 19: trailing garbage is accepted
    out = std::move(e);
    return true;
}

}  // namespace fnvcoop::spawn
