// Save policy (FULL-SYNC-PLAN 3.9). Game-free.
//
//   * Only the HOST's save is the world. A client keeps its own save as a character sidecar (character + delivered reward ids): that part
//     lives in synccore (ClientSession / Runtime::saveSidecar) and is not repeated here.
//   * Autosaves are suppressed on clients. The engine autosaves on its own (interior / exterior switch, fast travel, wait, rest): those four
//     triggers are INI settings ([GamePlay] bSaveOnInteriorExteriorSwitch, bSaveOnTravel, bSaveOnWait, bSaveOnRest), so the glue turns them
//     off with the console command SetINISetting while this machine is a joined client, and also on every machine while a quest holds
//     SaveSuppress. Scripted saves (Autosave / ForceSave / SystemSave commands) are already dropped on clients by L5's guest policy; the
//     RequestSave effect from the host is acknowledged and ignored on clients (plugin/hooks_world world_plan).
//   * decideSave() is the one table for "may this save happen" (manual saves are always the player's choice, except while a story moment
//     forbids saving or the world baseline is still arriving).
// Mutation twins 146..152 live in wt_save.cpp.
#pragma once
#include <string>
#include <vector>

#include "wt_types.h"

namespace nvwt {

enum class SaveKind : uint8_t { Auto, Quick, Manual, System, Force, Unknown };
enum class SaveVerdict : uint8_t { Allow, DenyQuiet, DenyNotify };

struct SaveContext {
    bool inSession = false;       // a co-op session is running on this machine
    Role role = Role::Host;
    bool joined = false;          // client: joined to the host
    bool saveSuppressed = false;  // a SaveSuppress effect holds (story moment that must not be saved)
    bool syncing = false;         // client: the world baseline is still arriving
};

SaveVerdict decideSave(SaveKind kind, const SaveContext& c);
const char* saveKindName(SaveKind k);

// Should the ENGINE's own autosave triggers be switched off right now?
bool engineAutosavesOff(const SaveContext& c);

class SaveGuard {
public:
    // Console lines to run now (empty when nothing changes). The first call only acts when autosaves have to go off.
    std::vector<std::string> update(const SaveContext& c);
    // The INI values that were in force before we touched anything (read them with GetINISetting; default: all on).
    void setOriginal(bool saveOnTravel, bool saveOnWait, bool saveOnRest, bool saveOnInteriorExteriorSwitch);
    bool autosavesOff() const { return off_; }
    // Lines that put the INI back (shutdown, leaving the session).
    std::vector<std::string> restore();

    static const char* const* iniNames(size_t* n);   // "bSaveOnTravel:GamePlay" ...
    static std::string lineFor(const char* name, bool on);

private:
    bool off_ = false;
    bool orig_[4] = {true, true, true, true};
};

}  // namespace nvwt
