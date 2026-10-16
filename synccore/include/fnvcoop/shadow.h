// ShadowState: the deterministic, game-free model of "story state" derived from the txn stream.
// Host and every client fold the same txns in seq order and must end with identical ShadowStates.
// Merge rules: stage = max, objective flags = OR, kills = union (Resurrect removes), globals = host wins
// (host order, last write), enable/disable/move/quest-running = host order, per-slot ledgers accumulate deltas.
#pragma once
#include <array>
#include <map>
#include <set>
#include <string>
#include <vector>
#include "fnvcoop/effect.h"

namespace fnvcoop {

class Archive;

struct Placement {
    FormRef cell;
    float x = 0, y = 0, z = 0;
    bool operator==(const Placement& o) const { return cell == o.cell && x == o.x && y == o.y && z == o.z; }
};

// Net deltas granted to one player slot.
struct Ledger {
    std::map<FormRef, int64_t> items;
    std::map<uint32_t, int64_t> av;
    int64_t karma = 0;
    std::map<FormRef, std::pair<int64_t, int64_t>> rep;  // faction -> (fame, infamy)
    std::map<FormRef, int32_t> perks;                    // perk -> rank (0 erased)
    FormRef activeQuest; bool hasActive = false;         // ForceActiveQuest (per player)
    uint32_t controlsOff = 0;                            // disabled player-control bits
    bool empty() const { return items.empty() && av.empty() && karma == 0 && rep.empty() && perks.empty() && controlsOff == 0 && !hasActive; }
    bool operator==(const Ledger& o) const {
        return items == o.items && av == o.av && karma == o.karma && rep == o.rep && perks == o.perks &&
               controlsOff == o.controlsOff && hasActive == o.hasActive && (!hasActive || activeQuest == o.activeQuest);
    }
};

struct SpawnInfo {
    FormRef base, cell;
    float x = 0, y = 0, z = 0;
    bool leveled = false;
    bool operator==(const SpawnInfo& o) const { return base == o.base && cell == o.cell && x == o.x && y == o.y && z == o.z && leveled == o.leveled; }
};
struct ObjState {
    bool open = false, destroyed = false, activated = false;
    bool isDefault() const { return !open && !destroyed && !activated; }
    bool operator==(const ObjState& o) const { return open == o.open && destroyed == o.destroyed && activated == o.activated; }
};
struct RefInv {
    bool cleared = false;                       // RemoveAllItems happened
    std::map<FormRef, int64_t> delta;           // net AddItem - RemoveItem since then
    bool operator==(const RefInv& o) const { return cleared == o.cleared && delta == o.delta; }
};

struct BarrierState {
    uint8_t arrived = 0, required = 0;
    bool released = false;
    bool operator==(const BarrierState& o) const { return arrived == o.arrived && required == o.required && released == o.released; }
};

class ShadowState {
public:
    uint64_t seq = 0;  // last txn folded in
    std::map<FormRef, uint32_t> stages;
    std::map<std::pair<FormRef, uint32_t>, uint8_t> objectives;
    std::map<FormRef, float> globals;
    std::map<FormRef, bool> enabled;
    std::set<FormRef> kills;
    std::map<FormRef, bool> questRunning;
    std::map<FormRef, Placement> placed;
    std::array<Ledger, kMaxSlots> ledger;
    // round 2
    std::map<std::pair<FormRef, uint16_t>, double> scriptVars;
    std::map<FormRef, bool> tethers;
    bool slideshowEnded = false, saveSuppress = false;
    std::map<FormRef, uint8_t> companionOwner;
    std::map<FormRef, BarrierState> barriers;
    std::set<FormRef> sayOnce, questCompleted, allObjectivesDone;
    std::set<FormRef> questFailed;   // subset of questCompleted (a failed quest is also completed)
    std::map<FormRef, std::pair<bool, int32_t>> locks;
    std::map<std::pair<FormRef, FormRef>, int32_t> factionRank;
    std::map<std::pair<FormRef, uint8_t>, FormRef> textures;
    std::map<FormRef, bool> fastTravelLock;   // scope -> locked
    // round 3
    std::map<FormRef, SpawnInfo> spawned;     // live created refs
    std::set<FormRef> despawned;              // tombstones (a replayed SpawnRef must not resurrect)
    uint32_t createdCounter = 0;              // highest created-ref number allocated so far
    std::map<FormRef, ObjState> objects;      // non-default object states only (lock lives in `locks`)
    std::map<std::pair<FormRef, uint16_t>, double> refVars;
    std::map<FormRef, RefInv> refInv;
    std::set<FormRef> topics, notes;
    std::map<FormRef, uint8_t> mapMarkers;
    std::map<FormRef, uint8_t> companionKind;
    FormRef weather; bool weatherSet = false, weatherForced = false;
    float timeScale = 0;

    // Fold one committed txn. `me` = the local slot (kNoPlayer for the host's own bookkeeping);
    // `out` (optional) receives the post-merge effects the local world must actually perform:
    // shared effects only when they change state, player effects only when `me` is a recipient.
    // The txn seq must be exactly seq+1 for callers that care; this function only records it.
    void applyTxn(const EffectTxn& txn, PlayerId me = kNoPlayer, std::vector<Effect>* out = nullptr);

    // Effects that move a world currently equal to `from` into `to` for slot `me`.
    static void diffEffects(const ShadowState& from, const ShadowState& to, PlayerId me, std::vector<Effect>& out);

    bool operator==(const ShadowState& o) const;
    bool operator!=(const ShadowState& o) const { return !(*this == o); }
    std::string describeDifference(const ShadowState& o) const;  // first differing section (debug aid)

private:
    void applyEffect(const Effect& e, PlayerId origin, PlayerId me, std::vector<Effect>* out);
};

void io(Archive& a, ShadowState& s);

}  // namespace fnvcoop
