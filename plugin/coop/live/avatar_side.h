// IAvatarSide: what AvatarNet (the avatar network service) needs from the local avatar layer. The real implementation wraps
// avatar::engine::AvatarRuntime (plugin/avatar/engine: local sampler, equipment watcher, interpolation buffers, PuppetManager over
// GamePuppetEngine over the game backend), see avatar_runtime_side.h. Game-free.
#pragma once
#include <cstdint>
#include <functional>
#include <string>
#include <vector>

#include "avatar_types.h"
#include "live/live.h"

namespace nvcoop::livewire {

class IAvatarSide {
public:
    virtual ~IAvatarSide() = default;
    using SnapshotSink = std::function<void(const fnvcoop::live::ActorSnapshot&)>;
    using EquipSink = std::function<void(const std::vector<nvcoop::avatar::EquipEntry>&)>;

    // ---- local player -> network ----
    virtual void setSnapshotSink(SnapshotSink s) = 0;        // 20 Hz, from tick(); null = drop
    virtual void setEquipSink(EquipSink s) = 0;              // once at the start and after every stable change
    virtual void setLocalIdentity(fnvcoop::live::NetId netId, fnvcoop::live::OwnerEpoch epoch) = 0;
    // This machine's slot in the session. The avatar layer's own default (slot 0) is right for the host only; a client that is never told
    // refuses the host's puppet as "the local slot". Called at every session start, before any addRemote.
    virtual void setLocalSlot(nvcoop::avatar::Slot /*slot*/) {}

    // ---- network -> remote players ----
    virtual bool addRemote(nvcoop::avatar::Slot slot, const nvcoop::avatar::Appearance& a) = 0;   // replaces an existing puppet
    virtual void removeRemote(nvcoop::avatar::Slot slot) = 0;
    // A look change of a known player (same race / sex = updated on the live puppet, no respawn). The default replaces the puppet.
    virtual bool setRemoteAppearance(nvcoop::avatar::Slot slot, const nvcoop::avatar::Appearance& a) { return addRemote(slot, a); }
    // The local player's own look, read from the game. Default: unknown.
    virtual bool readLocalAppearance(nvcoop::avatar::Appearance& /*out*/) { return false; }
    virtual void onRemoteSnapshot(nvcoop::avatar::Slot slot, const fnvcoop::live::ActorSnapshot& s, uint32_t arrivalMs) = 0;
    virtual void onRemoteEquipment(nvcoop::avatar::Slot slot, const std::vector<nvcoop::avatar::EquipEntry>& wanted) = 0;

    // ---- per frame / game events ----
    virtual void tick(uint32_t nowMs) = 0;
    enum class Event : uint8_t { LoadBegin, LoadEnd, ExitToMenu, SaveBegin, SaveEnd };
    virtual void onEvent(Event e) = 0;
    virtual bool purgePending() const = 0;                    // a load asked for an orphan purge that the next tick performs

    // ---- ghost-ref map ----
    virtual nvcoop::avatar::RefId refOf(nvcoop::avatar::Slot slot) const = 0;
    virtual bool slotOf(nvcoop::avatar::RefId ref, nvcoop::avatar::Slot& slot) const = 0;
    virtual std::string statusJson() = 0;
};

}  // namespace nvcoop::livewire
