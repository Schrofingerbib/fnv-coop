// Live IEngineApi for FalloutNV.exe 1.4.0.525 (x86, xNVSE headers). Declarations only: the implementation is engine_xnvse.cpp.
#pragma once
#include "fnvcoop_dlg/facts.h"

namespace fndlg {

struct IEngineApi;
// Creates the live engine API and registers the response loader (info_reader.h) that makes a default-constructed ResponseLayout work in
// the game. `report` receives every method that cannot do its job (once per kind).
IEngineApi* CreateXnvseEngine(void (*report)(const char*));
// The per-INFO player-fact table used by IEngineApi::collectFacts (null = no facts). Not owned.
void SetFactTable(const fnvcoop_dlg::FactTable* t);
// TESTopicInfo::GetResponses(info, 0) with the "never evict another line's chain" safety check; what the response loader runs.
bool LoadResponsesSafely(unsigned info);

}  // namespace fndlg
