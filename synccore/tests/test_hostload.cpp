// Host-load reverts, one effect at a time. A client changed some shared state; the host then loaded an older save that never held it.
// Both revert paths must bring the client's world back to the pristine value:
//   (1) the shadow-to-shadow diff (ShadowState::diffEffects), used by snapshot rejoin and rebase,
//   (2) the baseline path (diffBaseline + revertEffectFor), used by a join-in-progress against the client's own world.
// Each effect has a twin: with only that effect's pre-image/revert switched off (Mutations::preimageOff), the check must fail.
// Run: fnvcoop_tests --group hostload   (twins: --group twin, names hostload_*)   or  python -I tools/hostload/run_tests.py
#include <functional>
#include "fnvcoop/baseline.h"
#include "fnvcoop/mutations.h"
#include "fnvcoop/shadow.h"
#include "minitest.h"
#include "rejoin_rig.h"

using namespace fnvcoop;
using namespace rejoin;

namespace {
void applyAll(ShadowState& s, std::vector<Effect> effects) {
    EffectTxn t; t.seq = s.seq + 1; t.origin = 0; t.effects = std::move(effects);
    s.applyTxn(t);
}

enum Path { kDiff = 1, kBaseline = 2 };

// `change` is what the client did (with its pre-images); `pristine` tells whether a world is back to what the loaded save holds.
bool reverts(const std::vector<Effect>& change, const std::function<bool(const ShadowState&)>& pristine, int paths = kDiff | kBaseline) {
    ShadowState client;
    applyAll(client, change);
    if (pristine(client)) return false;   // the change must have done something, or the check proves nothing
    if (paths & kDiff) {
        std::vector<Effect> back;
        ShadowState::diffEffects(client, ShadowState(), 1, back);
        ShadowState w = client;
        EffectTxn t; t.seq = w.seq + 1; t.effects = back;
        w.applyTxn(t);
        if (!pristine(w)) return false;
    }
    if (paths & kBaseline) {
        BaselineDiff d = diffBaseline(ShadowState(), summarizeWorld(client, 1), SharePolicy(), 1, false, 0, false, {});
        ShadowState w = client;
        std::vector<Effect> fx;
        for (uint64_t k : d.revertKeys) { Effect e; if (revertEffectFor(client, k, e)) fx.push_back(e); }
        for (auto& e : d.effects) fx.push_back(e);
        EffectTxn t; t.seq = w.seq + 1; t.effects = fx;
        w.applyTxn(t);
        if (!pristine(w)) return false;
    }
    return true;
}

const FormRef kW0 = Q(0x40), kW1 = Q(0x41), kRef = Q(0x50), kMarker = Q(0x60), kCont = Q(0x70), kItem = Q(0x80), kItem2 = Q(0x81);

bool weatherReverts() {
    SetWeather sw{kW1, true}; sw.hasBefore = true; sw.before = kW0; sw.beforeForce = false;
    return reverts({sw}, [](const ShadowState& s) { return !s.weatherSet && !s.weatherForced; });
}
bool timeScaleReverts() {
    SetTimeScale ts{40.f}; ts.hasBefore = true; ts.before = 20.f;
    return reverts({ts}, [](const ShadowState& s) { return s.timeScale == 0; });
}
bool refVarReverts() {
    RefScriptVar rv{kRef, 2, 7.5}; rv.hasBefore = true; rv.before = 1.0;
    return reverts({rv}, [](const ShadowState& s) { return s.refVars.empty(); });
}
bool markerReverts() {
    SetMapMarker mm{kMarker, 3}; mm.hasBefore = true; mm.before = 1;
    return reverts({mm}, [](const ShadowState& s) { return s.mapMarkers.empty(); });
}
bool lockReverts() {
    SetLock lk{kRef, true, 60}; lk.hasBefore = true; lk.beforeLocked = false; lk.beforeLevel = 0;
    // the lock bit of ObjectState rides the same lock origin
    ObjectState os; os.ref = kMarker; os.mask = kObjLock; os.locked = true; os.level = 25; os.hasBefore = true; os.beforeLocked = true; os.beforeLevel = 80;
    return reverts({lk, os}, [](const ShadowState& s) {
        auto a = s.locks.find(kRef), b = s.locks.find(kMarker);
        return a != s.locks.end() && b != s.locks.end() && a->second == std::make_pair(false, 0) && b->second == std::make_pair(true, 80);
    });
}
bool objectOpenReverts() {
    ObjectState os; os.ref = kMarker; os.mask = kObjOpen; os.open = true;
    return reverts({os}, [](const ShadowState& s) { return s.objects.empty(); });
}
// An object that is open AND carries a sticky bit (destroyed): the sticky bit cannot be undone, the open bit can, on both revert paths.
bool objectOpenWithStickyBitReverts() {
    ObjectState os; os.ref = kMarker; os.mask = kObjOpen | kObjDestroyed; os.open = true; os.destroyed = true;
    return reverts({os}, [](const ShadowState& s) { auto it = s.objects.find(kMarker); return it == s.objects.end() || !it->second.open; });
}
bool refAddReverts() {
    return reverts({RefAddItem{kCont, kItem, 3}}, [](const ShadowState& s) { return s.refInv.empty(); });
}
bool refRemoveReverts() {
    return reverts({RefRemoveItem{kCont, kItem, 2}}, [](const ShadowState& s) { return s.refInv.empty(); });
}
bool removeAllReverts() {
    RemoveAllItems c; c.ref = kCont; c.hasBefore = true; c.items = {{kItem, 3}, {kItem2, 2}};
    return reverts({c, RefAddItem{kCont, kItem2, 4}}, [](const ShadowState& s) { return s.refInv.empty(); });
}
bool spawnReverts() {
    const uint32_t a = kCreatedRefPrefix | 5;
    return reverts({SpawnRef{a, Q(1), Q(2), 1, 2, 3, false}}, [](const ShadowState& s) { return s.spawned.empty() && s.despawned.empty(); });
}
bool despawnReverts() {
    const uint32_t a = kCreatedRefPrefix | 6;
    return reverts({SpawnRef{a, Q(1), Q(2), 1, 2, 3, false}, DespawnRef{a}}, [](const ShadowState& s) { return s.spawned.empty() && s.despawned.empty(); });
}

}  // namespace

TEST(hostload, set_weather_reverts) { CHECK(weatherReverts()); }
TEST(hostload, set_time_scale_reverts) { CHECK(timeScaleReverts()); }
TEST(hostload, ref_script_var_reverts) { CHECK(refVarReverts()); }
TEST(hostload, set_map_marker_reverts) { CHECK(markerReverts()); }
TEST(hostload, lock_and_object_state_lock_bit_revert) { CHECK(lockReverts()); }
TEST(hostload, object_state_open_reverts) { CHECK(objectOpenReverts()); }
TEST(hostload, object_state_open_reverts_even_when_a_sticky_bit_is_set) { CHECK(objectOpenWithStickyBitReverts()); }
TEST(hostload, ref_add_item_reverts) { CHECK(refAddReverts()); }
TEST(hostload, ref_remove_item_reverts) { CHECK(refRemoveReverts()); }
TEST(hostload, remove_all_items_reverts_with_later_adds) { CHECK(removeAllReverts()); }
TEST(hostload, spawn_ref_reverts) { CHECK(spawnReverts()); }
TEST(hostload, despawn_ref_tombstone_reverts) { CHECK(despawnReverts()); }

// A revert that is not asked for must not invent changes: an untouched client stays untouched on both paths.
TEST(hostload, untouched_client_gets_no_reverts) {
    ShadowState client;
    std::vector<Effect> back;
    ShadowState::diffEffects(client, ShadowState(), 1, back);
    CHECK(back.empty());
    BaselineDiff d = diffBaseline(ShadowState(), summarizeWorld(client, 1), SharePolicy(), 1, false, 0, false, {});
    CHECK(d.revertKeys.empty());
}

// The pre-image is the FIRST value a slot left; a second change keeps it, and changing back to it clears the slot (no revert needed).
TEST(hostload, first_preimage_wins_and_returning_to_it_clears_the_slot) {
    ShadowState s;
    SetWeather a{kW1, false}; a.hasBefore = true; a.before = kW0; a.beforeForce = false;
    SetWeather b{Q(0x42), true}; b.hasBefore = true; b.before = kW1; b.beforeForce = false;   // `before` of the second write is not the original
    applyAll(s, {a, b});
    CHECK(s.origins.size() == 1);
    CHECK(s.origins.begin()->second.f == kW0);
    SetWeather back{kW0, false}; back.hasBefore = true; back.before = Q(0x42); back.beforeForce = true;
    applyAll(s, {back});
    CHECK(!s.weatherSet && s.origins.empty());
    ShadowState t;
    SetTimeScale t1{30.f}; t1.hasBefore = true; t1.before = 20.f;
    SetTimeScale t2{35.f}; t2.hasBefore = true; t2.before = 30.f;
    applyAll(t, {t1, t2});
    CHECK(t.timeScale == 35.f && t.origins.size() == 1);
    SetTimeScale t3{20.f}; t3.hasBefore = true; t3.before = 35.f;
    applyAll(t, {t3});
    CHECK(t.timeScale == 0 && t.origins.empty());
}

// A client that learned of a clear from the host (snapshot diff, or baseline join) received it WITHOUT having been the capturer; the clear must still
// carry the contents it discarded, or a later host load that predates the clear can never be undone on that client.
bool clearPreTravels() {
    const FormRef c1 = kCont;
    ShadowState host;
    RemoveAllItems clr; clr.ref = c1; clr.hasBefore = true; clr.items = {{kItem, 3}, {kItem2, 2}};
    applyAll(host, {clr});
    auto learned = [&](bool viaBaseline) {
        ShadowState client;
        std::vector<Effect> fx;
        if (viaBaseline) fx = diffBaseline(host, summarizeWorld(client, 1), SharePolicy(), 1, false, 0, false, {}).effects;
        else ShadowState::diffEffects(client, host, 1, fx);
        EffectTxn t; t.seq = 1; t.effects = fx;
        client.applyTxn(t);
        return client;
    };
    for (bool viaBaseline : {false, true}) {
        ShadowState client = learned(viaBaseline);
        auto it = client.refInv.find(c1);
        if (it == client.refInv.end() || !it->second.cleared || !it->second.hasPre || it->second.pre.size() != 2) return false;
        // the host now loads an older save in which the container was never cleared: the client must be able to undo it
        std::vector<Effect> back;
        ShadowState::diffEffects(client, ShadowState(), 1, back);
        EffectTxn t; t.seq = client.seq + 1; t.effects = back;
        client.applyTxn(t);
        if (!client.refInv.empty()) return false;
    }
    return true;
}
TEST(hostload, a_clear_learned_from_the_host_carries_its_pre_image) { CHECK(clearPreTravels()); }

#define HOSTLOAD_TWIN(name, bit, fn) \
    TEST(twin, hostload_unit_##name) { MutationScope m([] { Mutations x; x.preimageOff = (bit); return x; }()); CHECK(!fn()); }
HOSTLOAD_TWIN(set_weather, kPiWeather, weatherReverts)
HOSTLOAD_TWIN(set_time_scale, kPiTimeScale, timeScaleReverts)
HOSTLOAD_TWIN(ref_script_var, kPiRefVar, refVarReverts)
HOSTLOAD_TWIN(set_map_marker, kPiMarker, markerReverts)
HOSTLOAD_TWIN(lock, kPiLock, lockReverts)
HOSTLOAD_TWIN(object_state_open, kPiObjectState, objectOpenReverts)
HOSTLOAD_TWIN(object_state_sticky_open, kPiStickyOpen, objectOpenWithStickyBitReverts)
HOSTLOAD_TWIN(ref_add_item, kPiRefInv, refAddReverts)
HOSTLOAD_TWIN(ref_remove_item, kPiRefInv, refRemoveReverts)
HOSTLOAD_TWIN(remove_all_items, kPiRefInv, removeAllReverts)
HOSTLOAD_TWIN(clear_pre_travels, kPiClearPre, clearPreTravels)
HOSTLOAD_TWIN(spawn_ref, kPiSpawn, spawnReverts)
HOSTLOAD_TWIN(despawn_ref, kPiSpawn, despawnReverts)
