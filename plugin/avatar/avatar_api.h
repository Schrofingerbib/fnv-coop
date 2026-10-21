// The public face of the avatar layer for everybody who needs to know "which reference is the avatar of player N" (FULL-SYNC-PLAN 3.1,
// "the ghost-ref map used by the acting-player design"):
//
//   puppetRefOf(slot)       the engine reference of the puppet that stands in for remote player `slot` on THIS machine (0 = none right now)
//   slotOfPuppet(ref)       the player slot a puppet reference stands for (false = not one of ours / not usable)
//   actingRefOf(slot)       the reference that ACTS for a player on this machine: the local player's own reference (0x14) for the local
//                           slot, the puppet for a remote slot. Guest intents run on the host with this reference as the acting player.
//   slotOfActingRef(ref)    the inverse (0x14 -> local slot, puppet -> its slot)
//
// Every call answers from live puppets only: a puppet that was deleted, parked, lost its reference or is being rebuilt answers "none", never a
// stale id (a reused id would make a guest's action land on a stranger). Main thread only, like everything that touches the game.
//
// The directory behind these functions is registered by whoever owns the puppets (the avatar module registers the runtime's manager;
// tests register a ManagerDirectory or a fake). With none registered every call answers "none", so callers need no #ifdef.
#pragma once
#include <cstddef>
#include <functional>
#include <optional>
#include <utility>
#include <vector>

#include "avatar_types.h"

namespace nvcoop::avatar {

class PuppetManager;

constexpr RefId kLocalPlayerRefId = 0x14;

class IPuppetDirectory {
public:
    virtual ~IPuppetDirectory() = default;
    virtual Slot LocalSlot() const = 0;
    virtual std::vector<std::pair<Slot, RefId>> Live() const = 0;   // ascending by slot, usable puppets only
};

// A directory over a PuppetManager (the real one).
class ManagerDirectory : public IPuppetDirectory {
public:
    ManagerDirectory(const PuppetManager& pm, Slot localSlot) : pm_(pm), local_(localSlot) {}
    Slot LocalSlot() const override { return local_; }
    std::vector<std::pair<Slot, RefId>> Live() const override;

private:
    const PuppetManager& pm_;
    Slot local_;
};

// Registration. The directory must outlive its registration; pass nullptr to unregister.
void setPuppetDirectory(const IPuppetDirectory* d);
const IPuppetDirectory* puppetDirectory();

RefId puppetRefOf(Slot slot);
bool slotOfPuppet(RefId ref, Slot& slot);
std::optional<Slot> slotOfPuppet(RefId ref);
bool isPuppet(RefId ref);
size_t puppetCount();
void forEachPuppet(const std::function<void(Slot, RefId)>& fn);

RefId actingRefOf(Slot slot);
bool slotOfActingRef(RefId ref, Slot& slot);

}  // namespace nvcoop::avatar
