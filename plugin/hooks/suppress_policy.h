// Guest suppression policy (game-free, unit-tested). The engine detours ask this object what to do.
//
// Rules (coordinator decision, internal lane notes round 4):
//  1. Quest-owned scripts (Script::info.type == 1: every block type, GameMode/MenuMode/OnTrigger...) never run on a
//     guest. The host replicates their quest vars / globals.
//  2. INFO result scripts and stage result scripts never run on a guest.
//  3. Any OTHER script (object / NPC / magic effect) still runs on a guest, but a shared-state mutator it calls is
//     dropped (blockMutation()).
//  Everything is lifted while a host EffectTxn is being applied (applying > 0).
#pragma once
#include <cstdint>
#include <functional>
#include <unordered_set>

namespace nvhooks {

struct Suppressed {
    uint64_t mutations = 0;      // blocked primitive / command calls made by a running local script
    uint64_t resultScripts = 0;  // blocked TESTopicInfo::RunResultScript calls
    uint64_t questScripts = 0;   // skipped quest-owned Script::Execute calls
};

constexpr uint16_t kScriptTypeObject = 0;
constexpr uint16_t kScriptTypeQuest = 1;
constexpr uint16_t kScriptTypeMagic = 0x100;

class SuppressPolicy {
public:
    void setGuest(bool g) { guest_ = g; }
    void setQuestGate(bool on) { questGate_ = on; }
    bool guest() const { return guest_; }
    bool questGate() const { return questGate_; }

    void beginApply() { ++applying_; }
    void endApply() { if (applying_ > 0) --applying_; }
    bool applying() const { return applying_ > 0; }

    // Script::Execute bracket. skipScript() is evaluated BEFORE enterScript().
    bool skipScript(uint16_t scriptType) { return skipScript(scriptType, 0); }
    // `scriptRefID` lets the GuestLocalRun allow-list (per-machine quest scripts: achievements, rumble, DLC setup, fades) keep specific
    // quest scripts running on guests.
    bool skipScript(uint16_t scriptType, uint32_t scriptRefID);
    void setGuestLocalRun(std::unordered_set<uint32_t> ids) { localRun_ = std::move(ids); }
    // Quest script variables of a GuestLocalRun quest are per-machine state: never captured, polled or sent (and never shared divergence).
    bool sharesQuestVars(uint32_t questScriptRefID) const;
    size_t guestLocalRunSize() const { return localRun_.size(); }
    bool isGuestLocalRun(uint32_t scriptRefID) const { return scriptRefID && localRun_.count(scriptRefID); }
    void enterScript() { ++depth_; }
    void leaveScript() { if (depth_ > 0) --depth_; }
    // A GuestLocalRun script is per-machine by definition: while one runs, its mutations (SetStage, StartQuest, AddItem, ...) are NOT
    // blocked on a guest (the host drops the same script's txns, so each machine applies them itself).
    void enterLocalScript() { ++depth_; ++localDepth_; }
    void leaveLocalScript() { if (depth_ > 0) --depth_; if (localDepth_ > 0) --localDepth_; }
    int localScriptDepth() const { return localDepth_; }
    int scriptDepth() const { return depth_; }

    bool blockMutation();      // shared-state mutator called from a running script
    bool blockResultScript();  // INFO / stage result script entry

    const Suppressed& stats() const { return stats_; }
    void resetStats() { stats_ = {}; }

private:
    bool guest_ = false;
    bool questGate_ = true;
    int applying_ = 0;
    int depth_ = 0;
    int localDepth_ = 0;
    Suppressed stats_;
    std::unordered_set<uint32_t> localRun_;
};

// Host-side deferral of engine SetStage calls the rules engine wants to hold back (e.g. the VCG01 chargen barrier).
// allow() is asked at the top of the SetStage detour. Calls made by our own applier (applying) or by the owner of the
// gate re-issuing a deferred stage (StageGateBypass alive) always pass.
class StageGate {
public:
    using Fn = std::function<bool(uint32_t quest, uint32_t stage)>;  // quest = engine refID
    void set(Fn f) { fn_ = std::move(f); }
    void beginBypass() { ++bypass_; }
    void endBypass() { if (bypass_ > 0) --bypass_; }
    bool allow(uint32_t quest, uint32_t stage, bool applying);
    uint64_t deferred() const { return deferred_; }

private:
    Fn fn_;
    int bypass_ = 0;
    uint64_t deferred_ = 0;
};

// "Acting player": while the host runs a block FOR a guest, which reference stands for `player` in the host-side helpers that consult it
// (activator selection for Activate, player-ref condition evaluators, inventory/perk facts). Stack-like; default = the host's player 0x14.
// NOT a global ThePlayer swap: script `player` operands are cached form pointers and the engine assumes a PlayerCharacter there.
class ActingContext {
public:
    static constexpr uint32_t kHostPlayer = 0x14;
    uint32_t current() const { return depth_ ? stack_[depth_ - 1] : kHostPlayer; }
#ifdef NVHOOKS_MUTANT
    void push(uint32_t refID) { if (depth_ < 8) stack_[depth_++] = refID; }  // mutant: a null ghost id becomes ref 0
#else
    void push(uint32_t refID) { if (depth_ < 8) stack_[depth_++] = refID ? refID : kHostPlayer; }
#endif
    void pop() { if (depth_) --depth_; }
    int depth() const { return depth_; }
    // Party slot of the acting player: 0 for the host's own player, else the slot registered for that guest's ghost (unknown ghost -> 0).
    void registerSlot(uint32_t ghostRefID, uint8_t slot) {
        for (int i = 0; i < nSlots_; ++i) if (ghost_[i] == ghostRefID) { slot_[i] = slot; return; }
        if (nSlots_ < 8) { ghost_[nSlots_] = ghostRefID; slot_[nSlots_++] = slot; }
    }
    void clearSlots() { nSlots_ = 0; }
    uint8_t slot() const {
        uint32_t c = current();
        for (int i = 0; i < nSlots_; ++i) if (ghost_[i] == c) return slot_[i];
        return 0;
    }

private:
    uint32_t ghost_[8] = {};
    uint8_t slot_[8] = {};
    int nSlots_ = 0;
    uint32_t stack_[8] = {};
    int depth_ = 0;
};

}  // namespace nvhooks
