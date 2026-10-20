// Core-side reload contract entry points (T3b-state). The core's NvCoreApi::before_unload and the end of its init()
// call these; see docs/tools/T3b-state.md. The implementation lives in plugin/reload/reload_glue.cpp.
#pragma once
#include <string>

#include "loader/loader_abi.h"

namespace nvcoop::reload {

// before_unload: preflight, save every participant into one blob (api->blob_put), shut everything down in reverse order,
// then read the leak probes. true = safe to FreeLibrary. false = the reload must not proceed:
//   * a preflight refusal or a failing save: nothing was stopped, the core keeps running as before;
//   * a leak after shutdown: hooks and threads are already stopped, so the game is safe, but this core is degraded and
//     the loader must keep it mapped (never FreeLibrary).
bool BeforeUnload(const NvLoaderApi* api);

// End of the new core's init(): restore every participant from the blob (api->is_reload), or let them start fresh.
// A damaged, old or missing blob is not an error; it only means the participants start fresh. Always returns true.
bool AfterLoad(const NvLoaderApi* api);

// JSON for the "reload.state" bridge method: participants and versions, probes, last report.
std::string StatusJson();

}  // namespace nvcoop::reload
