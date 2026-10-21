// World-time channel (first byte 0x7E): clock beacons and the wait / sleep / fast-travel requests. Game-free.
//
//   byte 0  0x7E            kChannelWorldTime
//   byte 1  kWireVersion    bumped on any incompatible change here (the story wire version is not involved)
//   byte 2  MsgType
//   ...     payload (little endian; strings are u8 length + bytes, at most 96)
//   last 4  crc32 (IEEE) of every byte before it
//
// Host -> clients: Beacon, Verdict, TravelGo.   Clients -> host: SkipRequest, TravelRequest, Hello, Status.
// Every decoder rejects: short, wrong magic / version, bad crc, unknown type, out-of-range fields, trailing bytes.
// Mutation twins 110..114 live in wt_wire.cpp.
#pragma once
#include <variant>

#include "wt_clock.h"
#include "wt_types.h"

namespace nvwt {

struct SkipRequest {
    uint32_t reqId = 0;
    ReqKind kind = ReqKind::Wait;   // Wait or Sleep
    uint32_t minutes = 0;
    bool operator==(const SkipRequest& o) const { return reqId == o.reqId && kind == o.kind && minutes == o.minutes; }
};
struct TravelRequest {
    uint32_t reqId = 0;
    FormRef marker;   // the map marker (or any reference) the player wants to travel to
    bool operator==(const TravelRequest& o) const { return reqId == o.reqId && marker == o.marker; }
};
struct Verdict {
    uint32_t reqId = 0;
    ReqKind kind = ReqKind::Wait;
    bool granted = false;
    Reason reason = Reason::None;
    uint32_t minutes = 0;
    bool party = false;   // travel: everybody goes (a TravelGo follows)
    bool operator==(const Verdict& o) const {
        return reqId == o.reqId && kind == o.kind && granted == o.granted && reason == o.reason && minutes == o.minutes && party == o.party;
    }
};
struct TravelGo {
    uint32_t reqId = 0;
    FormRef marker;
    PlayerId mover = 0;   // who asked
    bool party = false;   // true: every machine travels; false: only `mover`
    bool operator==(const TravelGo& o) const { return reqId == o.reqId && marker == o.marker && mover == o.mover && party == o.party; }
};
struct Status {   // client -> host about once a second: what the host cannot see from its own machine
    bool inCombat = false;
    bool operator==(const Status& o) const { return inCombat == o.inCombat; }
};
struct Hello {   // a client that just joined (or just lost its clock) asks for a beacon now
    uint8_t wireVersion = kWireVersion;
    bool operator==(const Hello& o) const { return wireVersion == o.wireVersion; }
};

using Msg = std::variant<Beacon, SkipRequest, TravelRequest, Verdict, TravelGo, Hello, Status>;
enum class MsgType : uint8_t { Beacon = 1, SkipRequest = 2, TravelRequest = 3, Verdict = 4, TravelGo = 5, Hello = 6, Status = 7 };

enum class DecodeError : uint8_t { Ok, TooShort, BadMagic, BadVersion, BadCrc, BadType, Malformed };
const char* decodeErrorName(DecodeError e);

Bytes encode(const Msg& m);
DecodeError decode(const uint8_t* data, size_t size, Msg* out);
inline DecodeError decode(const Bytes& b, Msg* out) { return decode(b.data(), b.size(), out); }

uint32_t crc32Ieee(const uint8_t* data, size_t size);

}  // namespace nvwt
