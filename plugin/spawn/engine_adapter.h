// EngineAdapter: ISpawnEngine on top of the actor port's IActorEngine (plugin/actors/engine/engine_iface.h) plus a handful of hooks for what
// that interface does not have (the created-ref ledger of plugin/hooks_world, the xNVSE extra-data reads, a leveled rebase).
// Game-free: every engine access goes through IActorEngine (production: ScriptActorEngine over RealActorEngine, tests: a fake).
//
// Honest status: unit-tested against a fake. Nothing here has run in a game. The engine-facing assumptions, each marked where it matters:
//   * hold      = console Disable / Enable on the ref (IActorEngine::setDisabled). A save written while a roll is held contains the actor
//                 disabled; the window is one round trip (tens of ms) and Show re-enables it.
//   * delete    = MarkForDelete (IActorEngine::markForDelete), the same call the world lane uses for DespawnRef.
//   * kill      = IActorEngine::killActor (the console Kill).
//   * rebase    = NO verified call changes the base form of a placed ref. TODO-RE: the hook `rebase` is where the actor port's
//                 leveled-pick swap (EnginePort::setBase, replace with a clone of the pick) plugs in. Without it a placed ref keeps the
//                 base this machine's engine rolled and the failure is counted (Stats::rebaseFailed, shown by coop.spawn); the ref is
//                 still enabled so it is never left hidden.
//   * leveled   = TODO-RE verify: xNVSE ExtraLeveledCreature (extra 0x2E, third_party/NVSE GameExtraData.h) has {baseForm, form}; which of
//                 the two is the original leveled list and which the rolled actor base is NOT verified against the exe. The hook
//                 `extras` supplies both leveledFrom and the encounter zone (ExtraEncounterZone, extra 0x74); until it is wired no placed
//                 ref is recognised as leveled and only created (encounter / marker) actors are arbitrated.
#pragma once
#include <functional>
#include "actors/engine/engine_iface.h"
#include "spawn/spawn_types.h"

namespace nvcoop::spawn {

struct AdapterHooks {
    std::function<NetId(RefId ref, const FormRef& wire)> placedNetId;       // plugin/actors deriveNetId(wire) for placed refs
    std::function<RefId(NetId)> refOfNet;                                   // actors::engine::refOfNet (placed, stand-in); created refs are found through the ledger
    std::function<void(RefId, NetId)> bindCreated;                          // nvworld::ledger().onCreated(createdRef(id), ref)
    std::function<bool(NetId)> isCreatedBound;                              // nvworld::ledger().map() knows the host ref (or the spawn is queued)
    std::function<bool(RefId, NetId&)> netOfCreated;                        // reverse lookup in the ledger map
    std::function<bool(const fnvcoop::SpawnRef&)> createFromSpawnRef;       // WorldApplier::apply(SpawnRef) (creates now or queues until the cell loads)
    std::function<bool(RefId, const FormRef&)> rebase;                      // TODO-RE: leveled base swap (actor port). Absent = unsupported
    std::function<void(RefId, FormRef& leveledFrom, FormRef& encounterZone)> extras;   // xNVSE extra data reads (x86 glue). Absent = none known
};

class EngineAdapter : public ISpawnEngine {
public:
    EngineAdapter(actors::engine::IActorEngine& eng, AdapterHooks hooks) : eng_(eng), h_(std::move(hooks)) {}
    void setHooks(AdapterHooks h) { h_ = std::move(h); }

    bool describe(RefId ref, RefInfo& out) override;
    RefId refOfNet(NetId id) override;
    bool setHeld(RefId ref, bool hold) override;
    bool deleteRef(RefId ref) override;
    bool rebase(RefId ref, const FormRef& base) override;
    bool kill(RefId ref) override;
    void bindCreated(RefId ref, NetId id) override;
    bool isCreatedBound(NetId id) override;
    bool netOfCreated(RefId ref, NetId& id) override;
    bool createFromSpawnRef(const fnvcoop::SpawnRef& s) override;

private:
    actors::engine::IActorEngine& eng_;
    AdapterHooks h_;
};

}  // namespace nvcoop::spawn
