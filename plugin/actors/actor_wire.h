// Wire messages of the actor module. Snapshot streams (delta encoded, unreliable) are live-core's SnapshotBatch / SnapshotAck bytes carried
// verbatim; everything else is one Envelope per datagram, always reliable-ordered.
//
// Datagram layout: [kChannelActors][sub][payload]
//   sub 0  SubBatch     payload = live::SnapshotSender::flush() bytes     (unreliable)
//   sub 1  SubAck       payload = live::SnapshotReceiver::takeAcks() bytes (unreliable)
//   sub 2  SubEnvelope  payload = fnvcoop::Archive of an Envelope          (reliable)
#pragma once
#include <variant>
#include "actors/actor_types.h"

namespace fnvcoop { class Archive; }

namespace nvcoop::actors {

constexpr uint8_t kActorWireVersion = 2;   // 2: ActorSnapshot carries the live schema v2 animation extension (synccore/live/live.h), + MAnimEvent
enum Sub : uint8_t { SubBatch = 0, SubAck = 1, SubEnvelope = 2 };

struct MOwn { fnvcoop::OwnMsg m; };                                   // host -> all; client -> host only for Ack
struct MTrack { ActorMeta meta; };                                    // client -> host: "I have this actor loaded"
// Full (non-delta) state, reliable: the old owner's last state in a handoff (final) or the one reliable copy of a settled actor.
struct MSnapFull { live::ActorSnapshot s; bool final = false; bool settle = false; };
struct MDeath { NetId id = 0; OwnerEpoch epoch = 0; uint64_t eventId = 0; CellId cell = 0; live::Vec3 pos; };   // owner's death declaration
struct MPick { NetId id = 0; FormRef pick; };                         // host -> all: the one leveled pick of this actor
struct MPickReport { NetId id = 0; FormRef base; };                   // owner -> host: the base form the engine rolled
struct MResync { NetId id = 0; };                                     // client -> host: "my view is behind, tell me the owner"

// Opaque reliable event from the actor's owner to everyone (enemy-anim: the settled corpse pose). ActorSync only judges the sender (owner,
// current epoch), de-duplicates by eventId and hands `payload` to the registered AnimEventHandler; it never looks inside.
struct MAnimEvent { NetId id = 0; OwnerEpoch epoch = 0; uint64_t eventId = 0; Bytes payload; };

using Body = std::variant<MOwn, MTrack, MSnapFull, MDeath, MPick, MPickReport, MResync, MAnimEvent>;   // append only: the index is on the wire

struct Envelope {
    PlayerId src = kNoPlayer;
    PlayerId dest = kNoPlayer;   // kNoPlayer = everyone except src (a client's broadcast is relayed by the host)
    Body body;
};

const char* bodyName(const Body& b);
Bytes encodeEnvelope(const Envelope& e);                                    // full datagram, kChannelActors and sub included
bool decodeEnvelope(const uint8_t* payload, size_t size, Envelope& out);    // payload = after the sub byte; false on any malformed input
Bytes wrapLive(Sub sub, const Bytes& liveBytes);                            // full datagram around a live-core packet

void io(fnvcoop::Archive& a, live::ActorSnapshot& s);
void io(fnvcoop::Archive& a, ActorMeta& m);

}  // namespace nvcoop::actors
