// plugin/world_time: shared small types. Game-free (no engine headers). See docs/tools/world-time.md.
//
// This module owns FULL-SYNC-PLAN 3.6 / 3.9 on the runtime side: game time + weather authority, wait / sleep and fast-travel consent,
// save policy, and the poll that turns NATIVE door / lock / destroyed changes into ObjectState effects.
#pragma once
#include <cstdint>
#include <string>
#include <vector>

#include "fnvcoop/types.h"

namespace nvwt {

using fnvcoop::FormRef;
using fnvcoop::PlayerId;
using fnvcoop::kHostPlayer;
using fnvcoop::kNoPlayer;
using Bytes = std::vector<uint8_t>;

// First byte of every datagram of this module (docs/protocol.md section 2). Own version byte, independent of the story wire version.
constexpr uint8_t kChannelWorldTime = 0x7E;
constexpr uint8_t kWireVersion = 1;

enum class Role : uint8_t { Host, Client };

// What a player asked the party for.
enum class ReqKind : uint8_t { Wait = 0, Sleep = 1, Travel = 2 };
// Why a request was refused (also the byte on the wire).
enum class Reason : uint8_t {
    None = 0,
    Combat = 1,     // a party member is fighting (rest) / the traveller is fighting (travel)
    Busy = 2,       // another question of the same party is still open
    Cooldown = 3,   // the party said no a moment ago
    TooLong = 4,    // more minutes than the configured cap
    Locked = 5,     // a quest holds a fast-travel lock
    Timeout = 6,    // nobody answered in time
    Declined = 7,   // somebody said no
    Left = 8,       // the asker left the session
    Invalid = 9,    // malformed or impossible request
    NotJoined = 10, // the asker is not a joined player
    PartyChanged = 11,  // somebody joined after the question was asked
    Max = 11,
};
const char* reasonText(Reason r);   // short player-facing text

inline uint32_t bit(PlayerId p) { return p < 8 ? (1u << p) : 0u; }

// The five engine globals that make up the game clock (FalloutNV.esm GameYear 0x35, GameMonth 0x36, GameDay 0x37, GameHour 0x38,
// GameDaysPassed 0x39; TimeScale 0x3A is already used by plugin/hooks_world). Only the HOST's values are authoritative.
struct GameTime {
    int32_t year = 2281;
    int32_t month = 10;     // 1..12
    int32_t day = 23;       // 1..31
    double hour = 9.0;      // 0 <= hour < 24
    double daysPassed = 0;  // GameDaysPassed
    bool operator==(const GameTime& o) const {
        return year == o.year && month == o.month && day == o.day && hour == o.hour && daysPassed == o.daysPassed;
    }
    bool operator!=(const GameTime& o) const { return !(*this == o); }
};

}  // namespace nvwt
