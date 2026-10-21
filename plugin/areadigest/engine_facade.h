// IActorFacade over the real actor port (plugin/actors/engine). x86 / dll only; game headers are NOT needed (actor_engine_api.h and
// engine_iface.h are game-free), but the symbols come from nvcoop_actor_engine_core, so only the dll build links this file.
// Status: written against the documented API, never run in a game.
#pragma once
#include <functional>
#include "areadigest/actor_source.h"

namespace nvcoop::areadigest {

// The singleton facade. Every call checks eng::actorEngine() (null = [live] actors=0 or not loaded yet) and answers "nothing" then.
IActorFacade* engineFacade();

// requestOwnerResync has no public entry in plugin/actors yet (ActorSync::requestResync is private): dll-wiring maps this hook to it. Unset:
// the facade answers false and the actor source counts the death heal as refused.
void setOwnerResyncHook(std::function<bool(uint32_t netId)> hook);

}  // namespace nvcoop::areadigest
