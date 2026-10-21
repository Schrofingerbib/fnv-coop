// What coop_module.cpp / the engine glue call (only when built with NVCOOP_WITH_SPAWN). Everything behind it is game-free and tested; the
// engine adapter (ISpawnEngine) is plugged in with setEngine(). Until then the engine is a no-op: nothing is ever rolled or held, the
// session still mirrors the site table, and coop.spawn answers.
//
// Wiring checklist for dll-wiring (nothing here is wired by this lane):
//   1. at startup:  spawn::setEngine(&adapter)   with an EngineAdapter over the actor port's IActorEngine + hooks (engine_adapter.h)
//                   spawn::setGameClock([]{ return game ms })   (host: GameDaysPassed * 24 * 3600 * 1000; default = wall clock, which is wrong
//                   for Wait / Sleep but never stalls respawn)
//                   actors::engine::setCreatedIdResolver(spawn::createdIdResolver)   so the actor feed asks us who a created actor is
//                   spawn::setIgnore([](RefId r){ return puppet / stand-in })   avatar puppets and the port's stand-ins are never rolls
//   2. every frame, after the coop runtime ticked and after glueFrame of plugin/actors:   spawn::glueFrame(rt.get())
//   3. engine events:  actors::engine::addActorTrackedListener -> spawn::onPlacedLoaded(ev.ref) (not for stand-ins)
//                      actors::engine::addActorDiedListener    -> spawn::onActorDied(ev.netId)   (host acts, clients ignore)
//                      the host's cell-reset detection         -> spawn::onCellReset(cell)       (TODO-RE: no verified engine hook; unused)
//                      the host's PlaceAtMe capture            -> spawn::onHostSpawnEffect(def, spawnRef)  (def.kind = Script, anchor = the script form)
//                      cell attach                             -> spawn::onSiteCellLoaded(cell)
//                      PostLoadGame / NewGame / main menu      -> spawn::onWorldLoaded()
//                      the avatar layer's pose sink            -> spawn::setPlayerPose(slot, present, cell, pos)   (every slot incl. the local one)
//   4. bridge method:  coop.spawn -> spawn::glueRpc
//   5. host save sidecar: spawn::saveState() / loadState() carry the site table with the save (see docs/tools/spawn-arb.md)
#pragma once
#include <functional>
#include <string>
#include "spawn/spawn_runtime.h"

namespace nvcoop::coop { class Runtime; }

namespace nvcoop::spawn {

void setEngine(ISpawnEngine* eng);                           // before the session starts; null = back to the no-op engine
void setGameClock(std::function<uint64_t()> gameMs);         // game time in ms (host authoritative); null = wall clock
void setIgnore(std::function<bool(RefId)> f);
SyncConfig& config();                                        // tuning, read when a session starts

bool glueRunning();
SpawnSync* glueSync();
void glueFrame(nvcoop::coop::Runtime* rt);                   // once per frame; null = the session is gone
bool glueRpc(const std::string& paramsJson, std::string& out);

// engine event entry points: safe to call when nothing runs (they do nothing)
bool createdIdResolver(RefId ref, NetId& netId, fnvcoop::FormRef& wireRef);   // actors::engine::CreatedIdResolver
void onPlacedLoaded(RefId ref);
void onActorDied(NetId id);
void onCellReset(CellId cell);
void onHostSpawnEffect(const sp::SiteDef& def, const fnvcoop::SpawnRef& s);
void onSiteCellLoaded(CellId cell);
void onWorldLoaded();
// Where every player is (the avatar layer's pose sink, for the local player too). Without it ownership falls back to the proof each roll
// carries that its sender has the cell loaded, which works but is blind to a player walking away.
void setPlayerPose(PlayerId slot, bool present, CellId cell, const sp::Vec3& pos);

// host save sidecar (the site table + the id counter)
fnvcoop::Bytes saveState();
bool loadState(const fnvcoop::Bytes& b);

}  // namespace nvcoop::spawn
