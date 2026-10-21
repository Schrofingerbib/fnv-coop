#include "spawn/engine_adapter.h"

namespace nvcoop::spawn {

bool EngineAdapter::describe(RefId ref, RefInfo& out) {
    out = RefInfo{};
    actors::engine::ActorRaw raw;
    if (!ref || !eng_.read(ref, raw) || !raw.valid || raw.isPlayer || raw.deleted) return false;
    out.valid = true;
    out.ref = ref;
    out.dead = raw.dead();
    out.disabled = raw.disabled;
    out.created = raw.created();
    out.cell = raw.cell;
    out.pos = {raw.pos.x, raw.pos.y, raw.pos.z};
    eng_.toFormRef(raw.baseId, out.base);                // empty for a created form; the rolled bases of vanilla actors always resolve
    if (raw.cell) {
        eng_.toFormRef(raw.cell, out.cellForm);
        actors::engine::CellInfo ci;
        if (eng_.cellInfo(raw.cell, ci) && ci.known) out.interior = !ci.exterior;
    }
    if (!out.created) {
        eng_.toFormRef(ref, out.wireRef);
        if (h_.placedNetId && !out.wireRef.plugin.empty()) out.netId = h_.placedNetId(ref, out.wireRef);
    }
    if (h_.extras) h_.extras(ref, out.leveledFrom, out.encounterZone);
    return true;
}

RefId EngineAdapter::refOfNet(NetId id) { return h_.refOfNet ? h_.refOfNet(id) : 0; }

bool EngineAdapter::setHeld(RefId ref, bool hold) { return ref && eng_.setDisabled(ref, hold); }

bool EngineAdapter::deleteRef(RefId ref) { return ref && eng_.markForDelete(ref); }

bool EngineAdapter::rebase(RefId ref, const FormRef& base) {
    if (!ref) return false;
    eng_.setDisabled(ref, false);                        // never leave it hidden, whatever happens below
    return h_.rebase ? h_.rebase(ref, base) : false;     // TODO-RE: no verified base swap; see the header
}

bool EngineAdapter::kill(RefId ref) { return ref && eng_.killActor(ref); }

void EngineAdapter::bindCreated(RefId ref, NetId id) { if (h_.bindCreated) h_.bindCreated(ref, id); }
bool EngineAdapter::isCreatedBound(NetId id) { return h_.isCreatedBound && h_.isCreatedBound(id); }
bool EngineAdapter::netOfCreated(RefId ref, NetId& id) { return h_.netOfCreated && h_.netOfCreated(ref, id); }
bool EngineAdapter::createFromSpawnRef(const fnvcoop::SpawnRef& s) { return h_.createFromSpawnRef && h_.createFromSpawnRef(s); }

}  // namespace nvcoop::spawn
