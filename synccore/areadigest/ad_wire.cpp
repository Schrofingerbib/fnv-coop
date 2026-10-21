#include "areadigest/ad_wire.h"
#include "fnvcoop/wire.h"
#include "fnvcoop_mut.h"

namespace fnvcoop::areadigest {

namespace {

void ioCat(Archive& a, Cat& c, bool allowStory) {
    uint8_t v = static_cast<uint8_t>(c);
    a.u8(v);
    if (a.reading()) {
        const uint8_t lo = allowStory ? 0 : 1;
        if (v < lo || v >= static_cast<uint8_t>(Cat::Count_)) { a.fail(); return; }
        c = static_cast<Cat>(v);
    }
}

void io(Archive& a, Item& it) {
    a.varu(it.key);
    ioCat(a, it.cat, false);
    a.varu32(it.cell);
    a.boolean(it.alive);
    a.f32(it.hp);
    a.f32(it.pos.x); a.f32(it.pos.y); a.f32(it.pos.z);
    a.varu32(it.state);
    a.varu32(it.settledMs);
    a.varu32(it.version);
}

template <size_t I = 0>
bool readBody(Archive& a, size_t idx, Body& out);

void ioBody(Archive& a, MDigest& m) {
    a.varu32(m.round);
    size_t n = m.cells.size();
    if (!a.count(n, kMaxCells)) return;
    if (a.reading()) m.cells.assign(n, 0);
    for (auto& c : m.cells) a.varu32(c);
    n = m.groups.size();
    // twin 31: the group count is not bounded on read (a hostile length is accepted)
    if (!a.count(n, FNV_MUT(31) ? static_cast<size_t>(1u << 22) : kMaxGroups)) return;
    if (a.reading()) m.groups.assign(n, WireGroup{});
    for (auto& g : m.groups) { a.varu32(g.cell); ioCat(a, g.cat, false); a.u32(g.hash); a.varu32(g.count); }
    a.boolean(m.story.has);
    if (m.story.has) { a.u64(m.story.hash); a.varu(m.story.seq); a.varu32(m.story.count); a.varu32(m.story.epoch); }
}

void ioBody(Archive& a, MItemsReq& m) {
    a.varu32(m.caseId);
    size_t n = m.groups.size();
    if (!a.count(n, kMaxReqGroups)) return;
    if (a.reading()) m.groups.assign(n, WireGroupId{});
    for (auto& g : m.groups) { a.varu32(g.cell); ioCat(a, g.cat, false); }
    n = m.keys.size();
    if (!a.count(n, kMaxKeys)) return;
    if (a.reading()) m.keys.assign(n, WireKey{});
    for (auto& k : m.keys) { ioCat(a, k.cat, false); a.varu(k.key); }
}

void ioBody(Archive& a, MItems& m) {
    a.varu32(m.caseId);
    a.boolean(m.truncated);
    size_t n = m.items.size();
    if (!a.count(n, kMaxItems)) return;
    if (a.reading()) m.items.assign(n, Item{});
    for (auto& it : m.items) io(a, it);
}

void ioBody(Archive& a, MHeal& m) {
    a.varu32(m.caseId);
    size_t n = m.items.size();
    if (!a.count(n, kMaxHealItems)) return;
    if (a.reading()) m.items.assign(n, WireHeal{});
    for (auto& h : m.items) { io(a, h.item); a.varu32(h.forVersion); a.varu32(h.epoch); }
}

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

const char* bodyName(const Body& b) {
    static const char* names[] = {"Digest", "ItemsReq", "Items", "Heal"};
    return names[b.index()];
}

std::vector<uint8_t> encodeEnvelope(const Envelope& e) {
    Archive a = Archive::writer();
    uint8_t ver = kAreaDigestWireVersion, src = e.src, idx = static_cast<uint8_t>(e.body.index());
    a.u8(ver); a.u8(src); a.u8(idx);
    Body copy = e.body;
    std::visit([&](auto& m) { ioBody(a, m); }, copy);
    std::vector<uint8_t> body = a.finish();
    if (body.empty()) return {};   // a field failed to encode (non-finite float, over-long list): nothing to send
    std::vector<uint8_t> out;
    out.reserve(body.size() + 1);
    out.push_back(kChannelAreaDigest);
    out.insert(out.end(), body.begin(), body.end());
    return out;
}

bool decodeEnvelope(const uint8_t* data, size_t size, Envelope& out) {
    if (!data || size < 2 || data[0] != kChannelAreaDigest) return false;
    Archive a = Archive::reader(data + 1, size - 1);
    uint8_t ver = 0, idx = 0, src = 0;
    a.u8(ver);
    if (!a.ok() || ver != kAreaDigestWireVersion) return false;
    a.u8(src); a.u8(idx);
    if (!a.ok() || idx >= std::variant_size_v<Body>) return false;
    Envelope e;
    e.src = src;
    if (!readBody(a, idx, e.body)) return false;
    if (!a.ok()) return false;
    if (!FNV_MUT(32) && !a.atEnd()) return false;   // twin 32: trailing garbage is accepted
    out = std::move(e);
    return true;
}

}  // namespace fnvcoop::areadigest
