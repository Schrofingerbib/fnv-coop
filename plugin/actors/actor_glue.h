// What coop_module.cpp calls (only when built with NVCOOP_WITH_ACTORS). Everything behind it is game-free and tested; the engine adapter
// (IActorPort over the real actor refs) is plugged in with setGamePort() by the hooks lane. Until then the port is a no-op: ownership is
// decided, replicated and answered by coop.owner for every actor registered with ActorSync::track(), but no engine call is made.
#pragma once
#include <functional>
#include <string>
#include "actors/actor_runtime.h"

namespace nvcoop::coop { class Runtime; }

namespace nvcoop::actors {

void setGamePort(IActorPort* port);                          // before the session starts; null = back to the no-op port
// Called once on every new ActorRuntime, before its first session starts, with the SyncConfig it will use (plugin/actors/engine sets `adjacent`).
void setSyncConfigurer(std::function<void(SyncConfig&)> f);
uint64_t glueSessionGeneration();                            // ActorRuntime::rebuilds() of the current runtime (0 = none); changes with every new ActorSync
bool glueRunning();                                          // an ActorSync exists (the coop session is up)
ActorSync* glueSync();                                       // for the engine glue: track() / onLocalDeath() / setPlayerPose() ...
void glueFrame(nvcoop::coop::Runtime* rt);                   // once per frame, after Runtime::frame(); null = the session is gone
bool glueOwnerRpc(const std::string& paramsJson, std::string& out);   // coop.owner

}  // namespace nvcoop::actors
