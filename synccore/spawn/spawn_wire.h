// Wire messages of the spawn module (first byte kChannelSpawn on the coop mux, always reliable-ordered).
//
// Datagram: [kChannelSpawn][Archive: version u8, src u8, body index u8, body]
//   client -> host : MRoll (a roll my engine made), MDigest (my table digest), MResync (send me the whole table)
//   host -> client : MVerdict (to the roller), MSite (a site record changed / the join table), MOwn (site ownership)
// Everything is idempotent: a replay changes nothing (rev for MSite, epoch for MOwn, rollId for MRoll).
#pragma once
#include <variant>
#include "spawn/spawn.h"

namespace fnvcoop::spawn {

struct MRoll { RollReport r; };
struct MVerdict { VerdictMsg v; };
struct MSite { SiteMsg s; };
struct MOwn { fnvcoop::OwnMsg m; };
struct MDigest { uint64_t digest = 0; uint32_t sites = 0; };
struct MResync { uint8_t all = 1; };

using Body = std::variant<MRoll, MVerdict, MSite, MOwn, MDigest, MResync>;

struct Envelope {
    PlayerId src = kNoPlayer;
    Body body;
};

const char* bodyName(const Body& b);
std::vector<uint8_t> encodeEnvelope(const Envelope& e);                       // full datagram, kChannelSpawn included
bool decodeEnvelope(const uint8_t* data, size_t size, Envelope& out);         // data = the full datagram; false on ANY malformed input

void io(fnvcoop::Archive& a, Vec3& v);
void io(fnvcoop::Archive& a, SiteDef& v);
void io(fnvcoop::Archive& a, Member& v);
void io(fnvcoop::Archive& a, RollReport& v);
void io(fnvcoop::Archive& a, VerdictMsg& v);
void io(fnvcoop::Archive& a, SiteMsg& v);

}  // namespace fnvcoop::spawn
