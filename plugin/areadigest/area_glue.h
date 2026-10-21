// What coop_module.cpp (dll-wiring) calls, only when built with NVCOOP_WITH_AREADIGEST. Everything behind it is game-free and tested except the
// engine facade (engine_facade.cpp), which has never run in a game. The module (areadigest_module.cpp) reads nvcoop.ini [areadigest]
// (area_config.h), registers the bridge methods and does nothing at all unless enabled=1 (OFF by default).
//
// Wiring (dll-wiring), in coop_module.cpp next to the actors / combat / spawn glue:
//     #ifdef NVCOOP_WITH_AREADIGEST
//     #include "areadigest/area_glue.h"
//     #endif
//     ...once per frame, right after nvcoop::actors::glueFrame(g_rt.get()) (the session has polled the mux by then):
//     #ifdef NVCOOP_WITH_AREADIGEST
//         nvcoop::areadigest::glueFrame(g_rt.get());
//     #endif
// Optional, when the lanes offer them:
//     nvcoop::areadigest::setOwnerResyncHook([](uint32_t netId){ return /* ActorSync::requestResync of that actor */ true; });   (engine_facade.h)
//     nvcoop::areadigest::setCellOfRef(...)        a reference's cell, so containers / objects are compared per cell instead of as one area
#pragma once
#include <string>
#include "areadigest/area_config.h"
#include "areadigest/shadow_source.h"

namespace nvcoop::coop { class Runtime; }

namespace nvcoop::areadigest {

void glueConfigure(const IniGetter& get);       // module onLoad: reads [areadigest]
bool glueEnabled();
const AreaSettings& glueSettings();
void glueFrame(nvcoop::coop::Runtime* rt);      // once per frame after Runtime::frame(); null = the session is gone. No-op unless enabled
bool glueRunning();                             // an AreaSync exists (the coop session is up)
std::string glueStatusJson();                   // areadigest.status
bool glueExportFlight(std::string& out);        // areadigest.flight: write the heal counters into the flight recorder now
void glueWorldLoaded();                         // PostLoadGame / NewGame: the world is a different one, start the session part over
void glueShutdown();                            // unload / game exit
void setCellOfRef(CellOfRef f);

}  // namespace nvcoop::areadigest
