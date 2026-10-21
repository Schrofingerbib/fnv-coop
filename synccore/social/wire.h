// social/wire: the datagrams of the social channel (first byte 0x5C on the coop MuxTransport, docs/protocol.md section 18). Game-free.
//
//   [0x5C kChannelSocial][kSocialWireVersion][Archive: u8 src, u8 dest, u8 kind index, body]
//
// Star topology like the combat channel: a client sends to the host only; the host judges, then relays or answers. Always reliable-ordered.
// `src` is the AUTHOR (the host overwrites it with the transport peer's slot on a client's datagram, so a client speaks only for itself).
// `dest` = kNoPlayer means "everyone except src".
#pragma once
#include <cstdint>
#include <variant>
#include <vector>
#include "fnvcoop/transport.h"
#include "social/companions.h"
#include "social/crime.h"
#include "social/rewards.h"

namespace fnvcoop::social {

constexpr uint8_t kChannelSocial = 0x5C;
constexpr uint8_t kSocialWireVersion = 1;
constexpr size_t kMaxRosterEntries = 256;

enum class Kind : uint8_t {
    Crime = 1, Detect, Bounty, Alarm, Hostility, Kill, Roster, Cmd, CmdNack, State, Inv, InvFull, InvAsk, Settings, Digest, Resync, Count_
};

struct MCrime { CrimeEvent e; };                                    // anybody -> host
struct MDetect { Detection d; };                                    // an NPC's owner -> host
struct MBounty { std::vector<BountyUpdate> u; bool full = false; }; // host -> all (full = replace the table)
struct MAlarm { AlarmOrder o; };                                    // host -> all
struct MHostility { cc::HostilityEffect e; };                       // host -> all
struct MKill { KillReport r; };                                     // anybody -> host
struct MRoster { std::vector<Companion> all; };                     // host -> all: the companion table (join / resync / change)
struct MCmd { CompanionCmd c; };                                    // the owner -> host
struct MCmdNack { uint64_t id = 0; CmdReason reason = CmdReason::Ok; };   // host -> the issuer
struct MState { FormRef npc; CompanionState st; };                  // host -> all
struct MInv { InvDelta d; };                                        // the owner -> host; the host relays accepted ones to everyone else
struct MInvFull { InvFull f; };                                     // the owner -> host -> all; also the answer to an ask
struct MInvAsk { FormRef npc; };                                    // anybody -> host: send me the full table of this companion
struct MSettings { SocialSettings s; };                             // host -> all
struct MDigest { uint64_t companions = 0, bounty = 0; uint32_t settingsRev = 0; };   // host -> all, every few seconds
struct MResync { uint8_t what = 0; };                               // client -> host: 0 = everything

using Body = std::variant<MCrime, MDetect, MBounty, MAlarm, MHostility, MKill, MRoster, MCmd, MCmdNack, MState, MInv, MInvFull, MInvAsk, MSettings, MDigest, MResync>;

struct Envelope {
    PlayerId src = kNoPlayer;
    PlayerId dest = kNoPlayer;
    Body body;
};

const char* bodyName(const Body& b);
Kind kindOf(const Body& b);
Bytes encodeEnvelope(const Envelope& e);                                // the whole datagram, channel and version included; empty if a value cannot be encoded
bool decodeEnvelope(const uint8_t* data, size_t size, Envelope& out);  // false on anything malformed, wrong channel / version, trailing bytes

}  // namespace fnvcoop::social
