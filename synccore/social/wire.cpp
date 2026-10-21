#include "social/wire.h"

#include "../../tools/mut/fnvcoop_mut.h"

namespace fnvcoop::social {

namespace {

void io(Archive& a, MCrime& m) { io(a, m.e); }
void io(Archive& a, MDetect& m) { io(a, m.d); }
void io(Archive& a, MBounty& m) {
    a.boolean(m.full);
    size_t n = m.u.size();
    if (!a.count(n, kMaxRosterEntries)) return;
    if (a.reading()) m.u.assign(n, {});
    for (auto& u : m.u) io(a, u);
}
void io(Archive& a, MAlarm& m) { io(a, m.o); }
void io(Archive& a, MHostility& m) { cc::io(a, m.e); }
void io(Archive& a, MKill& m) { io(a, m.r); }
void io(Archive& a, MRoster& m) {
    size_t n = m.all.size();
    if (!a.count(n, kMaxRosterEntries)) return;
    if (a.reading()) m.all.assign(n, {});
    for (auto& c : m.all) io(a, c);
}
void io(Archive& a, MCmd& m) { io(a, m.c); }
void io(Archive& a, MCmdNack& m) {
    a.u64(m.id);
    uint8_t r = static_cast<uint8_t>(m.reason);
    a.u8(r);
    if (a.reading()) {
        if (r >= static_cast<uint8_t>(CmdReason::Count_)) { a.fail(); return; }
        m.reason = static_cast<CmdReason>(r);
    }
}
void io(Archive& a, MState& m) { io(a, m.npc); io(a, m.st); }
void io(Archive& a, MInv& m) { io(a, m.d); }
void io(Archive& a, MInvFull& m) { io(a, m.f); }
void io(Archive& a, MInvAsk& m) { io(a, m.npc); }
void io(Archive& a, MSettings& m) { io(a, m.s); }
void io(Archive& a, MDigest& m) { a.u64(m.companions); a.u64(m.bounty); a.varu32(m.settingsRev); }
void io(Archive& a, MResync& m) { a.u8(m.what); }

template <size_t I = 0>
bool readBody(Archive& a, size_t idx, Body& out) {
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
    static const char* n[] = {"Crime", "Detect", "Bounty", "Alarm", "Hostility", "Kill", "Roster", "Cmd", "CmdNack", "State", "Inv", "InvFull", "InvAsk", "Settings", "Digest", "Resync"};
    return n[b.index()];
}
Kind kindOf(const Body& b) { return static_cast<Kind>(b.index() + 1); }

Bytes encodeEnvelope(const Envelope& e) {
    Archive a = Archive::writer();
    uint8_t src = e.src, dest = e.dest, idx = static_cast<uint8_t>(e.body.index());
    a.u8(src); a.u8(dest); a.u8(idx);
    Body copy = e.body;
    std::visit([&](auto& m) { io(a, m); }, copy);
    if (!a.ok()) return {};
    Bytes body = a.finish();
    Bytes out;
    out.reserve(body.size() + 2);
    out.push_back(kChannelSocial);
    out.push_back(kSocialWireVersion);
    out.insert(out.end(), body.begin(), body.end());
    return out;
}

bool decodeEnvelope(const uint8_t* data, size_t size, Envelope& out) {
    if (!data || size < 3) return false;
    if (data[0] != kChannelSocial && !FNV_MUT(880)) return false;          // BAD (880): a datagram of another channel is parsed
    if (data[1] != kSocialWireVersion && !FNV_MUT(881)) return false;      // BAD (881): any version is accepted
    Archive a = Archive::reader(data + 2, size - 2);
    uint8_t idx = 0;
    a.u8(out.src); a.u8(out.dest); a.u8(idx);
    if (!a.ok() || idx >= std::variant_size_v<Body>) return false;
    if (!readBody(a, idx, out.body)) return false;
    if ((out.src >= kMaxSlots || (out.dest >= kMaxSlots && out.dest != kNoPlayer)) && !FNV_MUT(883)) return false;   // BAD (883): a slot that cannot exist is accepted
    return a.ok() && (a.atEnd() || FNV_MUT(882));   // BAD (882): trailing bytes are accepted
}

}  // namespace fnvcoop::social
