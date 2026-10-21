#include "avatar_api.h"

#include "avatar_faults.h"
#include "puppet_manager.h"

namespace nvcoop::avatar {

namespace {
const IPuppetDirectory* g_dir = nullptr;
}

std::vector<std::pair<Slot, RefId>> ManagerDirectory::Live() const { return pm_.LivePuppets(); }

void setPuppetDirectory(const IPuppetDirectory* d) { g_dir = d; }
const IPuppetDirectory* puppetDirectory() { return g_dir; }

RefId puppetRefOf(Slot slot) {
    if (!g_dir) return 0;
    if (slot == g_dir->LocalSlot() && !avatarFaults().apiLocalGetsPuppet) return 0;   // the local player has no puppet; its own reference is not a puppet
    for (const auto& kv : g_dir->Live())
        if (kv.first == slot) return kv.second;
    return 0;
}

bool slotOfPuppet(RefId ref, Slot& slot) {
    if (!g_dir || !ref) return false;
    if (ref == kLocalPlayerRefId && !avatarFaults().apiPlayerIsPuppet) return false;
    for (const auto& kv : g_dir->Live())
        if (kv.second == ref) {
            slot = kv.first;
            return true;
        }
    return false;
}

std::optional<Slot> slotOfPuppet(RefId ref) {
    Slot s = kNoSlot;
    if (!slotOfPuppet(ref, s)) return std::nullopt;
    return s;
}

bool isPuppet(RefId ref) {
    Slot s;
    return slotOfPuppet(ref, s);
}

size_t puppetCount() { return g_dir ? g_dir->Live().size() : 0; }

void forEachPuppet(const std::function<void(Slot, RefId)>& fn) {
    if (!g_dir || !fn) return;
    for (const auto& kv : g_dir->Live()) fn(kv.first, kv.second);
}

RefId actingRefOf(Slot slot) {
    if (!g_dir) return 0;
    if (slot == g_dir->LocalSlot()) return avatarFaults().apiActingNeedsPuppet ? 0 : kLocalPlayerRefId;
    return puppetRefOf(slot);
}

bool slotOfActingRef(RefId ref, Slot& slot) {
    if (!g_dir || !ref) return false;
    if (ref == kLocalPlayerRefId) {
        slot = g_dir->LocalSlot();
        return true;
    }
    return slotOfPuppet(ref, slot);
}

}  // namespace nvcoop::avatar
