// Wire messages of plugin/vats (docs/protocol.md section 15). One Envelope per datagram, reliable-ordered (ENet channel 0).
//
// Datagram: [kChannelVats 0xB7][kVatsModuleWire][Archive: u8 src, u8 dest, u8 hops, u8 kind-1, body]
// Star topology like the combat and actor modules: a client sends to the host only; the host relays what the others need to see.
//   MConfig   host  -> everyone   the host's VATS settings (the host's values win on every machine); repeated every few seconds and on a join
//   MPhase    anyone -> everyone  a player's VATS mode changed (also the trigger for the party slow-mo); a heartbeat while the mode is active
//   MAction   anyone -> everyone  a VATS action was queued (target, limb): what the others' logs and the in-game proof read; the hit itself is combat's MHit
//   MSlowMo   host  -> everyone   the party slow-mo grant (vats_slowmo.h)
#pragma once
#include <variant>
#include "vats/vats_ports.h"
#include "vats/vats_slowmo.h"

namespace nvcoop::vats {

enum class Kind : uint8_t { Config = 1, Phase, Action, SlowMo, Count_ };

struct MConfig { uint32_t epoch = 0; Settings s; };
struct MPhase { PlayerId player = kNoPlayer; Mode mode = Mode::None; LimbId limb = LimbId::None; uint8_t queued = 0; uint32_t target = 0; uint32_t seq = 0; };
struct MAction { PlayerId shooter = kNoPlayer; uint32_t victim = 0; LimbId limb = LimbId::None; uint8_t queued = 0; uint32_t seq = 0; };
struct MSlowMo { SlowMoGrant g; };

using Body = std::variant<MConfig, MPhase, MAction, MSlowMo>;
constexpr uint8_t kMaxHops = 3;

struct Envelope {
    PlayerId src = kNoPlayer;     // author of the message (the host overwrites a client's value with the transport peer's slot)
    PlayerId dest = kNoPlayer;    // kNoPlayer = everyone except src
    uint8_t hops = 0;
    Body body;
};

const char* bodyName(const Body& b);
Kind kindOf(const Body& b);
Bytes encodeEnvelope(const Envelope& e);                                    // the whole datagram, channel and version included
bool decodeEnvelope(const uint8_t* data, size_t size, Envelope& out);      // data = the whole datagram; false on any malformed input

}  // namespace nvcoop::vats
