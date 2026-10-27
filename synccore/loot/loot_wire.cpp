#include "loot/loot_wire.h"
#include "fnvcoop/wire.h"
#include "fnvcoop_mut.h"

namespace fnvcoop::loot {

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

void ioPlayer(Archive& a, PlayerId& p) {
    uint8_t v = p;
    a.u8(v);
    if (a.reading()) {
        if (v >= kMaxSlots && v != kNoPlayer) { a.fail(); return; }
        p = v;
    }
}

}  // namespace

void io(Archive& a, Vec3& v) { a.f32(v.x); a.f32(v.y); a.f32(v.z); }

void io(Archive& a, Items& v) {
    size_t n = v.size();
    // twin 120: the stack count is not bounded on read (a hostile length is accepted)
    if (!a.count(n, FNV_MUT(120) ? static_cast<size_t>(1u << 22) : kMaxStacks)) return;
    if (a.reading()) {
        v.clear();
        for (size_t i = 0; i < n; ++i) {
            FormRef f;
            int32_t c = 0;
            fnvcoop::io(a, f);
            a.vari32(c);
            if (!a.ok()) return;
            if (c < 0 || c > kMaxCount) { a.fail(); return; }
            v[f] = c;
        }
    } else {
        for (auto& kv : v) {
            FormRef f = kv.first;
            int32_t c = kv.second;
            fnvcoop::io(a, f);
            a.vari32(c);
        }
    }
}

void io(Archive& a, ContainerRec& v) {
    a.varu32(v.id);
    ioEnum(a, v.kind, static_cast<uint8_t>(ContainerKind::Count_));
    a.varu32(v.cell);
    io(a, v.pos);
    io(a, v.items);
    a.varu32(v.rev);
    ioPlayer(a, v.holder);
    a.varu32(v.lockSeq);
}

void io(Archive& a, WorldItem& v) {
    a.varu32(v.id);
    fnvcoop::io(a, v.base);
    a.vari32(v.count);
    a.varu32(v.meta);
    a.varu32(v.cell);
    io(a, v.pos);
    io(a, v.rot);
    ioPlayer(a, v.dropper);
    a.varu32(v.dropTxn);
    if (a.reading() && (v.count < 0 || v.count > kMaxCount)) a.fail();
}

void io(Archive& a, Result& v) {
    a.varu32(v.txn);
    ioEnum(a, v.op, static_cast<uint8_t>(OpKind::Count_));
    ioEnum(a, v.status, static_cast<uint8_t>(Status::Count_));
    a.varu32(v.id);
    a.varu32(v.rev);
    ioPlayer(a, v.other);
    fnvcoop::io(a, v.base);
    a.vari32(v.count);
    a.varu32(v.newId);
    a.boolean(v.hasRec);
    if (v.hasRec) io(a, v.rec);
}

namespace {

void ioBody(Archive& a, ReqRegister& m) { a.varu32(m.txn); io(a, m.rec); }
void ioBody(Archive& a, ReqOpen& m) { a.varu32(m.txn); a.varu32(m.cont); a.boolean(m.hasAdopt); if (m.hasAdopt) io(a, m.adopt); }
void ioBody(Archive& a, ReqMove& m) { a.varu32(m.txn); a.varu32(m.cont); fnvcoop::io(a, m.base); a.vari32(m.count); a.boolean(m.put); }
void ioBody(Archive& a, ReqClose& m) { a.varu32(m.txn); a.varu32(m.cont); a.u64(m.digest); }
void ioBody(Archive& a, ReqHold& m) { a.varu32(m.cont); }
void ioBody(Archive& a, ReqDrop& m) {
    a.varu32(m.txn); fnvcoop::io(a, m.base); a.vari32(m.count); a.varu32(m.meta); a.varu32(m.cell); io(a, m.pos); io(a, m.rot);
}
void ioBody(Archive& a, ReqClaim& m) { a.varu32(m.txn); a.varu32(m.item); a.boolean(m.hasAdopt); if (m.hasAdopt) { io(a, m.adopt); fnvcoop::io(a, m.ref); } }
void ioBody(Archive& a, ReqResync& m) { a.varu32(m.cont); a.boolean(m.all); }
void ioBody(Archive& a, ReqDigest& m) { a.u64(m.digest); a.varu32(m.containers); a.varu32(m.items); }
void ioBody(Archive& a, ReqTouch& m) { a.varu32(m.net); io(a, m.pos); io(a, m.rot); }
void ioBody(Archive& a, Result& m) { io(a, m); }
void ioBody(Archive& a, MContainer& m) { io(a, m.rec); }
void ioBody(Archive& a, MDelta& m) { a.varu32(m.cont); a.varu32(m.rev); fnvcoop::io(a, m.base); a.vari32(m.newCount); if (a.reading() && (m.newCount < 0 || m.newCount > kMaxCount)) a.fail(); }
void ioBody(Archive& a, MLock& m) { a.varu32(m.cont); a.varu32(m.lockSeq); ioPlayer(a, m.holder); }
void ioBody(Archive& a, MItemNew& m) { io(a, m.item); }
void ioBody(Archive& a, MItemGone& m) { a.varu32(m.item); ioPlayer(a, m.by); a.varu32(m.txn); fnvcoop::io(a, m.ref); }
void ioBody(Archive& a, MContainerGone& m) { a.varu32(m.cont); }
void ioBody(Archive& a, MTableBegin& m) { a.varu32(m.seq); }
void ioBody(Archive& a, MTableEnd& m) { a.varu32(m.seq); a.varu32(m.containers); a.varu32(m.items); }
void ioBody(Archive& a, MClutter& m) { a.varu32(m.net); a.varu32(m.epoch); a.varu32(m.seq); io(a, m.pos); io(a, m.rot); a.boolean(m.moving); }
void ioBody(Archive& a, MClutterOwn& m) { a.varu32(m.net); ioPlayer(a, m.owner); a.varu32(m.epoch); io(a, m.pos); io(a, m.rot); }

template <size_t I = 0>
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

std::vector<uint8_t> encodeEnvelope(const Envelope& e) {
    Archive a = Archive::writer();
    uint8_t ver = kLootWireVersion, src = e.src, idx = static_cast<uint8_t>(e.body.index());
    a.u8(ver); a.u8(src); a.u8(idx);
    Body copy = e.body;
    std::visit([&](auto& m) { ioBody(a, m); }, copy);
    std::vector<uint8_t> body = a.finish();
    if (body.empty() || !a.ok()) return {};
    std::vector<uint8_t> out;
    out.reserve(body.size() + 1);
    out.push_back(kChannelLoot);
    out.insert(out.end(), body.begin(), body.end());
    return out;
}

bool decodeEnvelope(const uint8_t* data, size_t size, Envelope& out) {
    if (!data || size < 2 || data[0] != kChannelLoot) return false;
    Archive a = Archive::reader(data + 1, size - 1);
    uint8_t ver = 0, idx = 0, src = 0;
    a.u8(ver);
    if (!a.ok() || ver != kLootWireVersion) return false;
    a.u8(src); a.u8(idx);
    if (!a.ok() || idx >= std::variant_size_v<Body>) return false;
    if (src >= kMaxSlots && src != kNoPlayer) return false;
    Envelope e;
    e.src = src;
    if (!readBody(a, idx, e.body)) return false;
    if (!a.ok()) return false;
    if (!FNV_MUT(121) && !a.atEnd()) return false;   // twin 121: trailing garbage is accepted
    out = std::move(e);
    return true;
}

}  // namespace fnvcoop::loot
