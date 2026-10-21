// Wire format of the avatar channel (first byte kChannelAvatars on the coop MuxTransport). Game-free.
//
//   [kChannelAvatars][live datagram]            kind 1 = SnapshotBatch, kind 2 = SnapshotAck (synccore/live codec, CRC protected)
//   [kChannelAvatars][0x40][n][slot * n]        Roster : host -> clients, reliable. Every slot that has a player right now (host included).
//   [kChannelAvatars][0x41][slot][look][equip]  Look   : owner -> host -> others, reliable. Appearance + what the player wears. (v1, still decoded)
//   [kChannelAvatars][0x43][ver=2][slot][flags][race][hair][eyes][face][hairColor?][height,weight?][faceCoeffs?][name][equip + mods]
//                                               Look v2: the same message with body data (height / weight / hair colour), FaceGen coefficients and the
//                                               weapon modification flags of the held weapon. encodeLook always writes this one.
//   [kChannelAvatars][0x42]                     Resync : client -> host, reliable. Answered with the Roster and every known Look for that client.
//
// Roster and Look are decoded defensively: a truncated or oversized message is rejected as a whole, never half applied.
#pragma once
#include <cstdint>
#include <string>
#include <vector>

#include "avatar_types.h"

namespace nvcoop::livewire {

constexpr uint8_t kChannelAvatars = 0xAD;   // 0xAC actors, 0xFC story, 0xD1 dialogue, 0xE5 slides
constexpr uint8_t kAvKindRoster = 0x40;
constexpr uint8_t kAvKindLook = 0x41;
constexpr uint8_t kAvKindLook2 = 0x43;    // Look v2 (kAvatarWireVersion = 2 inside the message); 0x44 and up are free
constexpr uint8_t kAvatarWireVersion = 2;
constexpr uint8_t kAvKindResync = 0x42;   // client -> host, reliable: "my host link came back, send me the roster and every look again" (2 bytes)
constexpr size_t kMaxLookEquip = 64;
constexpr size_t kMaxLookName = 48;

using AvBytes = std::vector<uint8_t>;

struct AvLook {
    uint8_t slot = 0xFF;
    nvcoop::avatar::Appearance appearance;
    std::vector<nvcoop::avatar::EquipEntry> equipment;
    friend bool operator==(const AvLook& a, const AvLook& b);
};

AvBytes encodeRoster(const std::vector<uint8_t>& slots);
bool decodeRoster(const uint8_t* data, size_t size, std::vector<uint8_t>& slots);   // `data` starts at the channel byte

AvBytes encodeLook(const AvLook& l);
bool decodeLook(const uint8_t* data, size_t size, AvLook& out);                     // `data` starts at the channel byte

// NetId of a player's avatar stream: never in the derived-actor space (top byte 0xFE, low byte = slot).
inline uint32_t playerNetId(uint8_t slot) { return 0xFE000000u | slot; }
inline bool isPlayerNetId(uint32_t id) { return (id >> 8) == 0xFE0000u; }
inline uint8_t slotOfPlayerNetId(uint32_t id) { return static_cast<uint8_t>(id & 0xFF); }

}  // namespace nvcoop::livewire
