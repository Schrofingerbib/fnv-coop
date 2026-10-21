// Equipment correction loop for one puppet: makes the set of equipped items equal the owner's published set, one engine
// write per interval, without ever fighting the engine forever.
//
// Traps handled (VaultMP list, docs/research/vaultmp-actor-sync.md section 2.5 / 4, and the plan):
//   * the puppet must OWN an item before it can wear it (AddItem first);
//   * the template NPC's default outfit is worn at spawn and must be taken off; the actor's AI may put it back on:
//     when an item keeps coming back (flap), it is removed from the inventory instead of unequipped again;
//   * "the game always removes the equipped item first": equipping into an occupied slot unequips the old item, so
//     extras are handled before missing items, and weapons are equipped after everything else;
//   * failures back off exponentially and stop after equipMaxFails for that item (until the wanted set changes);
//   * the weapon may only be drawn once it is equipped (WeaponReady());
//   * weapon modifications (EquipEntry::mods) are put on AFTER the weapon is worn, one slot per write, verified by reading the
//     flags back (IPuppetEngine::WeaponMods); an engine that cannot tell is asked once per slot, an engine that refuses is
//     given up on after equipMaxFails writes (the mods are cosmetic and must never keep the loop busy forever).
#pragma once
#include <cstdint>
#include <map>
#include <set>
#include <utility>
#include <vector>

#include "avatar_types.h"
#include "puppet_engine.h"

namespace nvcoop::avatar {

class EquipCorrector {
public:
    explicit EquipCorrector(const Config* cfg = nullptr) : cfg_(cfg) {}   // SetConfig before the first Tick
    void SetConfig(const Config& cfg) { cfg_ = &cfg; }
    void SetDesired(const std::vector<EquipEntry>& want);   // no-op when the set is unchanged
    // Does at most one engine write. Returns the number of writes made (0 or 1).
    int Tick(IPuppetEngine& e, RefId ref, uint32_t nowMs, Stats& stats);
    void Reset();                   // new ref (respawn): forget observations, keep the wanted set

    bool Converged() const { return converged_; }
    bool WeaponReady() const { return weaponsReady_; }
    const std::set<FormId>& GaveUp() const { return gaveUp_; }
    // Weapon mods: true once every wanted mod is on (or could not be verified and was asked for once, or was given up on).
    bool ModsConverged() const { return modsConverged_; }
    const std::vector<EquipEntry>& Desired() const { return want_; }

private:
    enum class Kind : uint8_t { None, Equip, Unequip };
    struct Pending {
        Kind kind = Kind::None;
        FormId item = 0;
    };
    const Config* cfg_ = nullptr;
    std::vector<EquipEntry> want_;
    uint32_t nextAt_ = 0;
    bool converged_ = false, weaponsReady_ = true, modsConverged_ = true;
    using ModKey = std::pair<FormId, int>;             // (weapon form, slot 1..3)
    std::map<ModKey, unsigned> modFails_;              // consecutive failures per key
    std::set<ModKey> modGaveUp_, modAsked_;            // given up on / already asked once when the engine cannot tell us the flags
    Pending last_;
    std::map<FormId, unsigned> fails_;
    std::map<FormId, std::vector<uint32_t>> undone_;   // times the engine reverted our write
    std::set<FormId> gaveUp_;
};

}  // namespace nvcoop::avatar
