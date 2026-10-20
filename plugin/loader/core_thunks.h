// Core DLL internals shared by core_thunks.cpp and core_entry.cpp (split build only).
#pragma once
#include "loader_abi.h"

namespace nvcoop::loader_core {
// Called by NvCore_Init. Flushes registrations made during static initialisation.
void SetApi(const NvLoaderApi* api);
// The loader api of the running core (nullptr before NvCore_Init).
const NvLoaderApi* Api();
}  // namespace nvcoop::loader_core
