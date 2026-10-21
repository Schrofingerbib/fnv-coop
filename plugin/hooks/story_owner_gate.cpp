// Story-capture: tells the RefCommand applier which actors this machine does NOT run, so an AI order (StartCombat, EvaluatePackage, ...) is performed
// only by the machine that owns the actor (FULL-SYNC-PLAN 3.2). Compiled into nvcoop.dll only when the actors engine port is part of the build
// (plugin/CMakeLists.txt adds this file under `if(TARGET nvcoop_actor_engine_core)`); without it every machine performs every order.
// No switch of its own: the query is only consulted for RefCommand effects, which exist only when [hooks] story_capture is on.
#include <cstdint>

#include "actors/engine/actor_engine_api.h"
#include "apply_remote.h"
#include "common/nvcoop_api.h"

namespace {
void OnLoad(void*) {
    nvhooks::RemoteApplier::setDefaultRemoteDrivenQuery([](uint32_t ref) { return nvcoop::actors::engine::isRemoteDriven(ref); });
}
}  // namespace

NVCOOP_MODULE(story_owner_gate, "story_owner_gate", OnLoad, nullptr, nullptr);
