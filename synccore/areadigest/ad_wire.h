// Wire messages of the area digest module (first byte kChannelAreaDigest on the coop mux, always reliable-ordered).
//
// Datagram: [kChannelAreaDigest][Archive: version u8, src u8, body index u8, body]
//   client -> host : MDigest (my area digests + story digest), MItems (the items you asked for)
//   host -> client : MItemsReq (send me the items of these groups / these keys), MHeal (authoritative values for keys where you are wrong)
// Everything is idempotent: a replayed MDigest is rate limited, a replayed MItemsReq is answered again, a replayed MHeal finds nothing to change.
// Docs: docs/protocol.md section 15, docs/tools/area-desync.md.
#pragma once
#include <variant>
#include "areadigest/areadigest.h"

namespace fnvcoop::areadigest {

constexpr size_t kMaxCells = 2048, kMaxGroups = 4096, kMaxItems = 1200, kMaxKeys = 1200, kMaxReqGroups = 32, kMaxHealItems = 512;

struct WireGroup { CellId cell = 0; Cat cat = Cat::Actors; uint32_t hash = 0, count = 0; };
struct WireStory { bool has = false; uint64_t hash = 0, seq = 0; uint32_t count = 0, epoch = 0; };
struct MDigest { uint32_t round = 0; std::vector<CellId> cells; std::vector<WireGroup> groups; WireStory story; };
struct WireGroupId { CellId cell = 0; Cat cat = Cat::Actors; };
struct WireKey { Cat cat = Cat::Actors; Key key = 0; };
struct MItemsReq { uint32_t caseId = 0; std::vector<WireGroupId> groups; std::vector<WireKey> keys; };
struct MItems { uint32_t caseId = 0; bool truncated = false; std::vector<Item> items; };
struct WireHeal { Item item; uint32_t forVersion = 0; uint32_t epoch = 0; };
struct MHeal { uint32_t caseId = 0; std::vector<WireHeal> items; };

using Body = std::variant<MDigest, MItemsReq, MItems, MHeal>;

struct Envelope {
    PlayerId src = kNoPlayer;
    Body body;
};

const char* bodyName(const Body& b);
std::vector<uint8_t> encodeEnvelope(const Envelope& e);                       // full datagram, kChannelAreaDigest included; empty on failure
bool decodeEnvelope(const uint8_t* data, size_t size, Envelope& out);         // data = the full datagram; false on ANY malformed input (trailing bytes too)

}  // namespace fnvcoop::areadigest
