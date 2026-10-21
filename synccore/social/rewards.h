// social/rewards: game-free KILL REWARDS (FULL-SYNC-PLAN 3.8): kill XP and karma, paid ONCE, to the right players, by host setting.
// C++17. Mutation twins 861..899 (FNV_MUT).
//
// Why this exists. When a kill is resolved by the victim's owner (docs/tools/combat-hooks.md), the engine that natively hands out XP, karma and
// reputation only sees the owner's puppet as the killer, never the real player. So the host settles every death ONCE here and delivers the result as
// ordinary committed effects (ModActorValue experience / ModKarma / ModReputation with an explicit recipient mask: the story channel already makes
// them idempotent, replays them to a rejoiner and survives a lossy link).
//
// The rules:
//   * Kill XP goes to the KILLING BLOW (a companion's kill credits its recruiter; an NPC or the environment pays nobody) or is split by PARTY SHARE
//     among the players present (host setting `xpMode`; `scope` says who counts as present). The split uses synccore/combat attributeKillXp.
//   * One death settles once, however many machines report it (the ledger remembers death ids; a second report with another killer changes nothing).
//   * Whatever the engine ALREADY paid natively (the killer's own machine killed a victim it simulates: `nativeMask`) is not paid again to that
//     slot; every other player still gets their shared part.
//   * Karma follows the victim's karma class through a table (OURS, placeholders: the game's own numbers are TODO-RE, see docs/tools/companions-crime.md)
//     and is shared or personal by SharePolicy. Killing a hostile victim never costs karma (self defence); it still pays for killing evil.
//   * Reputation for a murder comes from the crime path (crime.h infamy per witnessed crime), not from here, so it cannot be paid twice.
//   * The host's SocialSettings are the only ones that count: clients mirror them (rev ordered).
#pragma once
#include <cstdint>
#include <set>
#include <string>
#include <vector>
#include "combat/combat.h"
#include "fnvcoop/effect.h"
#include "fnvcoop/types.h"
#include "fnvcoop/wire.h"
#include "social/companions.h"
#include "social/crime.h"

namespace fnvcoop::social {

enum class KarmaClass : uint8_t { Unknown = 0, VeryEvil, Evil, Neutral, Good, VeryGood, Count_ };
const char* toString(KarmaClass k);
// Our own table (karma change per kill by victim class). NOT the game's values; the engine's are TODO-RE (GMSTs fKarmaMod*, docs/02-engine-re.md).
struct KarmaTable {
    int32_t delta[static_cast<size_t>(KarmaClass::Count_)] = {0, 2, 1, -1, -3, -5};
};

enum class XpScope : uint8_t { AllConnected = 0, SameCell = 1, Near = 2 };   // who counts as "present" for a party share

struct SocialSettings {
    uint32_t rev = 0;                   // host raises it with every change; clients ignore an older one
    cc::XpMode xpMode = cc::XpMode::KillingBlow;
    XpScope scope = XpScope::AllConnected;
    float nearRadius = 6000;            // XpScope::Near, game units
    bool shareKarma = true;             // SharePolicy::karma
    bool karmaOnKills = true;           // host switch: kills move karma at all
    bool crime = true;                  // host switch: the crime / alarm / bounty system
    bool friendlyFire = false;          // party-vs-party damage (players and their companions)
    ForeignPolicy foreignCompanions = ForeignPolicy::Deny;   // may a player open somebody else's companion wheel?
    FollowerCaps caps;
    KarmaTable karma;
    friend bool operator==(const SocialSettings& a, const SocialSettings& b);
};
void io(Archive& a, SocialSettings& s);

struct VictimFacts {
    cc::Entity who;
    KarmaClass karma = KarmaClass::Unknown;
    FormRef faction;                    // crime faction (empty = none)
    int32_t xp = 0;                     // the engine's kill XP for it (0 = unknown / worthless: nothing is paid)
    bool hostileToParty = false;        // it was fighting the party
    bool named = false;
};
struct KillReport {
    uint64_t deathId = 0;               // DeathDecl::deathId: the idempotency key
    VictimFacts victim;
    cc::Entity killer;                  // a player (slot), an NPC / companion (net id), or none (environment)
    PlayerId killerRecruiter = kNoPlayer;   // killer is a companion: the player it follows
    std::vector<PlayerId> contributors; // players who damaged the victim (informational; PartyShare splits among `present`)
    uint8_t nativeMask = 0;             // slots whose engine already paid XP and karma natively for this kill
    uint32_t cell = 0;
    cc::Vec3 where;
    PlayerId reporter = kNoPlayer;
};
void io(Archive& a, KillReport& r);

struct PlayerPlace {
    PlayerId slot = kNoPlayer;
    bool connected = true;
    uint32_t cell = 0;
    cc::Vec3 pos;
};
// The players that count as present for a kill at (cell, where): per scope. Always sorted, no duplicates, only connected slots.
std::vector<PlayerId> presentPlayers(XpScope scope, const std::vector<PlayerPlace>& players, uint32_t cell, const cc::Vec3& where, float nearRadius);

struct XpGrant { PlayerId slot = kNoPlayer; int32_t xp = 0; };
struct KillRewards {
    bool settled = false;               // false: nothing to do (already settled, or nothing earned)
    std::vector<XpGrant> xp;            // to deliver (natively paid slots already removed)
    int32_t karma = 0;                  // the karma change of this kill (0 = none)
    PlayerId responsible = kNoPlayer;   // the player answerable for the kill (killer or recruiter)
    uint8_t karmaTo = 0;                // slot mask that gets `karma` (natively paid slots removed)
    int32_t xpNative = 0;               // XP the engine already paid (informational)
};

class RewardLedger {
public:
    explicit RewardLedger(size_t remember = 8192) : cap_(remember) {}
    // `present` = presentPlayers(...) at the kill; `connectedMask` = every connected slot (karma sharing).
    KillRewards settle(const KillReport& k, const SocialSettings& s, const std::vector<PlayerId>& present, uint8_t connectedMask,
                       const cc::CombatRules& rules = cc::defaultRules());
    bool seen(uint64_t deathId) const { return ids_.count(deathId) != 0; }
    void reset() { ids_.clear(); order_.clear(); }
    size_t settledCount() const { return ids_.size(); }
private:
    bool remember(uint64_t id);
    size_t cap_;
    std::set<uint64_t> ids_;
    std::vector<uint64_t> order_;
};

// ---- effects ---------------------------------------------------------------------------------------------------------------------------
// The committed effects for a settlement: one ModActorValue(experience) per XP grant (Recipient::slot) and one ModKarma for the shared mask.
// Slots in `localMask` (the host's own slot) are NOT included: the host's engine is the source of its own effects and applies them directly.
std::vector<Effect> rewardEffects(const KillRewards& r, uint8_t localMask);
// The ModReputation effects of a crime decision: infamy to `sharedMask` when reputation is shared (else to the criminal), natively paid slots and
// the local slot removed.
std::vector<Effect> reputationEffects(const std::vector<RepChange>& rep, PlayerId criminal, bool shared, uint8_t connectedMask, uint8_t nativeMask, uint8_t localMask);

}  // namespace fnvcoop::social
