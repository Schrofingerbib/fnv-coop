// plugin/vats: shared types (FULL-SYNC-PLAN 3.4 VATS). Game-free: no xNVSE header, no Windows header.
//
// What VATS means in a shared world (the Fallout 76 model): the world cannot slow down for one player, so VATS runs in REAL TIME. The engine's own
// VATS machinery (target list, limb highlight, queue, playback, kill cam, hit chance from THIS player's skills and perks, AP use) is kept; we only take
// away what stops or slows the world: the menu pause, the global time-scale requests and the player / target time multipliers. See docs/tools/vats.md.
#pragma once
#include <cstdint>
#include <string>
#include "fnvcoop/types.h"

namespace nvcoop::vats {

using fnvcoop::PlayerId;
using fnvcoop::kHostPlayer;
using fnvcoop::kMaxSlots;
using fnvcoop::kNoPlayer;

// VATSCameraData (0x11F2250) +8. 0 none, 1 target select, 2 limb select / zoom, 3 unknown (limb navigation runs here, see docs/re/vats-realtime.md),
// 4 playback. "proven" for the numbers, "likely" for the names.
enum class Mode : uint8_t { None = 0, TargetSelect = 1, LimbSelect = 2, Mode3 = 3, Playback = 4 };
inline Mode modeFromEngine(uint32_t m) { return m <= 4 ? static_cast<Mode>(m) : Mode::None; }
inline bool isActive(Mode m) { return m != Mode::None; }
inline bool isSelecting(Mode m) { return m == Mode::TargetSelect || m == Mode::LimbSelect || m == Mode::Mode3; }
inline bool isPlayback(Mode m) { return m == Mode::Playback; }
const char* toString(Mode m);

// A limb is identified the way the engine's VATS menu identifies it: by the limb condition ActorValue code that 0x7F3BF0 returns for a body-part index
// (proven: 0x1A = torso is the default part, -1 = none). The same codes plugin/combat uses for its limb conditions.
enum class LimbId : int8_t {
    None = -1,
    Head = 25,       // PerceptionCondition
    Torso = 26,      // EnduranceCondition
    LeftArm = 27,    // LeftAttackCondition
    RightArm = 28,   // RightAttackCondition
    LeftLeg = 29,    // LeftMobilityCondition
    RightLeg = 30,   // RightMobilityCondition
    Brain = 31,      // BrainCondition (creatures)
};
constexpr unsigned kLimbMax = 8;               // a target never has more selectable parts than this (sanity cap for engine lists)
bool validLimb(int v);                         // 25..31
LimbId limbFromAv(int v);                      // None for anything else
const char* toString(LimbId l);                // "head", "torso", "left_arm", ...
LimbId limbFromName(const std::string& s);     // case-insensitive; accepts "head", "left arm", "left_arm", "leftarm", "25", ...; None if unknown
// plugin/combat's wire limb (synccore/combat Limb: Torso 0, Head 1, LeftArm 2, RightArm 3, LeftLeg 4, RightLeg 5); 255 = no such limb (Brain / None)
uint8_t combatLimbOf(LimbId l);

// Weapon classes of the four VATS playbooks (docs/tools/vats.md).
enum class WeaponClass : uint8_t { Gun = 0, Melee = 1, Explosive = 2, Energy = 3, Count_ };
const char* toString(WeaponClass c);
WeaponClass weaponClassFromName(const std::string& s, bool* ok = nullptr);

// Host-authoritative VATS settings. The host's values win on every machine (MConfig); a client's own nvcoop.ini only matters before it has joined.
struct Settings {
    bool realtime = true;               // real-time VATS in a co-op session (false = the engine's vanilla pausing VATS, NOT synced: for solo play and A/B tests)
    bool partySlowMo = false;           // "party slow-mo": any player entering VATS slows ALL players together for a short, capped window
    float slowScale = 0.35f;            // world time scale during the window (clamped 0.10 .. 0.90)
    uint32_t slowMaxMs = 3000;          // the window never lasts longer than this (clamped 500 .. 10000)
    uint32_t slowCooldownMs = 10000;    // after a window ended no new one starts before this has passed (clamped 0 .. 120000)
    bool autoFire = true;               // real time: a queued VATS action starts its playback by itself (the shot fires immediately)
    uint32_t autoFireDelayMs = 350;     // ... after this much time without a further queued action (clamped 0 .. 3000)
    bool killCam = true;                // keep the engine's VATS camera cuts and kill cam (they never pause the shared world); false shortens them
    bool guardFriendly = true;          // never leave a party member (another player's puppet) selected as a VATS target
    bool perks = true;                  // apply the real-time perk rework (vats_perks.h)
    friend bool operator==(const Settings& a, const Settings& b) {
        return a.realtime == b.realtime && a.partySlowMo == b.partySlowMo && a.slowScale == b.slowScale && a.slowMaxMs == b.slowMaxMs &&
               a.slowCooldownMs == b.slowCooldownMs && a.autoFire == b.autoFire && a.autoFireDelayMs == b.autoFireDelayMs && a.killCam == b.killCam &&
               a.guardFriendly == b.guardFriendly && a.perks == b.perks;
    }
    friend bool operator!=(const Settings& a, const Settings& b) { return !(a == b); }
};
Settings sanitized(Settings s);   // clamps every number into its documented range (applied to everything that comes from the ini or the wire)

}  // namespace nvcoop::vats
