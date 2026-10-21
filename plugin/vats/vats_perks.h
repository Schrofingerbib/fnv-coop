// The VATS perk rework for real time, per player. Game-free.
//
// What stays native, and why: every machine runs the engine for its OWN player, and real-time VATS keeps the engine's VATS queue and playback, so the
// shooter's skills and perks are applied by the shooter's own engine exactly as in the single-player game: hit chance (skills, perks, range), critical
// chance (Four Leaf Clover, Ninja ...), critical and damage multipliers (Better Criticals ...), AP cost and AP pool (Action Boy). The result travels as
// the damage, the critical flag and the limb of the hit that plugin/combat forwards to the victim's owner.
//
// What does NOT stay native, and is done here:
//   * Grim Reaper's Sprint: the engine refunds AP when ITS hit code kills the target during VATS. When the victim belongs to another machine the death
//     is decided there (docs/tools/combat-hooks.md), the shooter's engine never runs that code, and the AP would be lost. VatsLedger recognises the
//     kill (a VATS action was queued at that victim, the death arrived during / right after the playback, the engine did not decide it locally) and
//     applies the refund once.
//   * Mysterious Stranger: the Stranger is a temporary local actor that shoots for the player. A hit from an actor no machine tracks would be refused
//     by the victim's owner (unknown attacker); attackerFor() credits it to the player, only during the player's own playback.
#pragma once
#include <cstdint>
#include <set>
#include <map>
#include <string>
#include "vats/vats_types.h"

namespace nvcoop::vats {

struct PerkSettings {
    bool grsEnabled = true;
    int grsApRestore = 20;                 // AP given back per VATS kill. PLACEHOLDER for the vanilla number: compare with the perk text in game, then set grs_ap
    bool grsRestoreAll = false;            // true: refill to the maximum instead
    std::string grsEditorId = "GrimReapersSprint";   // the perk's editor id as xNVSE's GetFormByID resolves it (UNVERIFIED name, ini: grs_edid)
    uint32_t killWindowMs = 4000;          // a death counts as a VATS kill up to this long after the playback ended
    uint32_t actionTtlMs = 20000;          // a queued action is forgotten after this long
};

enum class PerkTreatment : uint8_t { Native, Glue };
struct PerkInfo { const char* title; PerkTreatment how; const char* note; };
const PerkInfo* perkTable(size_t* count);   // documentation, also reported by vats.status

struct VatsKill { uint32_t victimRef = 0; LimbId limb = LimbId::None; };

class VatsLedger {
public:
    explicit VatsLedger(const PerkSettings& s = PerkSettings{}) : s_(s) {}
    void setSettings(const PerkSettings& s) { s_ = s; }
    // The local player queued a VATS action at `targetRef` (limb as highlighted at that moment).
    void onActionQueued(uint32_t targetRef, LimbId limb, uint64_t nowMs);
    void onModeChanged(Mode from, Mode to, uint64_t nowMs);
    // An actor died on this machine's view of the world (actor port DiedEvent). Returns true if it is a VATS kill of the local player that the
    // engine did NOT already settle itself (then `out` is filled and the perk rule runs once for it).
    bool onActorDied(uint32_t victimRef, bool decidedHere, bool fromNetwork, uint64_t nowMs, VatsKill* out);
    bool playbackRecently(uint64_t nowMs) const;   // playback running, or ended less than killWindowMs ago
    size_t pending() const { return actions_.size(); }
    uint64_t kills() const { return kills_; }
    uint64_t nativeKills() const { return nativeKills_; }
    void clear();

private:
    struct Action { LimbId limb; uint64_t atMs; uint64_t playbackSeq; };
    PerkSettings s_;
    std::map<uint32_t, Action> actions_;
    bool playback_ = false;
    uint64_t playbackEndMs_ = 0, playbackSeq_ = 0;
    uint64_t kills_ = 0, nativeKills_ = 0;
};

// Pure rule of the Stranger credit: should a hit by `attackerRef` be credited to the local player?
struct AttackerFacts {
    uint32_t attackerRef = 0;
    uint32_t localPlayerRef = 0;
    bool attackerTracked = false;      // a synced actor (actor port network id) or a known player puppet
    bool attackerIsParty = false;      // another player's puppet or a party companion
    bool localPlayback = false;        // the LOCAL player's VATS playback is running (or ended within the grace)
};
uint32_t attackerFor(const AttackerFacts& f);

// AP to give back for one VATS kill (0 = none): never negative, never above the missing amount.
int grimReaperRefund(const PerkSettings& s, bool hasPerk, int ap, int maxAp);

}  // namespace nvcoop::vats
