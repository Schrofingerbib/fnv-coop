// World-baseline dumper: reads the loaded game world into an L4 ShadowState (internal lane notes "World baseline").
// Needs synccore/include on the include path (header-only use of fnvcoop/shadow.h; no synccore library link).
#pragma once
#include <cstdint>
#include <map>
#include <set>
#include <string>
#include <vector>

#include "fnvcoop/shadow.h"

namespace fnvcoop::snapshot {

// ---- game-free layer (unit tested; what the engine reader fills) -----------------------------------------------
struct BaselineRef { std::string plugin; uint32_t local = 0; };

struct BaselineQuestIn {
	BaselineRef ref;
	uint32_t stage = 0;
	bool running = false, completed = false;
	std::map<uint32_t, uint8_t> objectives;   // objective id -> bit0 displayed | bit1 completed
	std::map<uint16_t, double> vars;          // script variable index -> value
	// Per-field exclusions from the empirical volatile-keys file (never the whole quest). Vars are filtered by the
	// engine reader before they reach this struct.
	bool excludeRunning = false, excludeStage = false, excludeObjectives = false;
};
struct BaselineGlobalIn { BaselineRef ref; float value = 0; bool volatileClock = false; };
struct BaselineRefIn {
	BaselineRef ref;
	bool enabled = true;
	bool isActor = false, alive = true;
	bool hasLock = false, locked = false; int32_t lockLevel = 0;
	std::map<uint16_t, double> vars;          // attached-script variables (empty if none)
};
struct BaselineFactionIn { BaselineRef faction; int32_t rank = -1; };   // the player's rank; -1 = not a member
struct BaselineRepIn { BaselineRef faction; double fame = 0, infamy = 0; };

struct BaselineInput {
	std::vector<std::string> plugins;         // load order, as the game reports them
	std::vector<BaselineQuestIn> quests;
	std::vector<BaselineGlobalIn> globals;
	std::vector<BaselineRefIn> refs;          // watched refs
	std::vector<BaselineFactionIn> factionRanks;
	std::vector<BaselineRepIn> reputations;
	std::vector<BaselineRef> saidOnceInfos;   // INFOs whose "said once" flag is set
	BaselineRef player{"FalloutNV.esm", 0x14};
	double karma = 0;
	bool includeVolatileGlobals = false;      // GameHour/GameDay... are per-machine clocks: excluded by default
};

// Same value as fnvcoop::contentHashOf(lower-cased plugin names) (cross-checked in tests).
uint64_t BaselineContentHash(const std::vector<std::string>& plugins);

// Fills *out (cleared first) and *hash. Returns false with *err on a vacuous input (no plugins / no quests).
bool BuildShadowFromBaseline(const BaselineInput& in, fnvcoop::ShadowState* out, uint64_t* hash, std::string* err);

// ---- engine layer (needs the game; main thread only; call after a save is loaded) ------------------------------
struct BaselineDumpOptions {
	std::vector<std::string> watchRefs;       // "Plugin.esm:XXXXXX"; merged with watchRefsFile / env FNVCOOP_WATCHREFS
	std::string watchRefsFile;
	bool includeVolatileGlobals = false;
	// Data-driven volatility (docs/snapshot-schema.md section 11): text file produced by tools/volatile_probe.py
	// (default path: env FNVCOOP_VOLATILE_KEYS; if neither is set nothing but the clock globals is excluded).
	// A named file that is missing or malformed makes the dump FAIL (never silently unfiltered).
	std::string volatileKeysFile;
};

// What the last dump excluded (for logging). Also returned by BaselineDumpOptions-based Ex via LastBaselineStats().
struct BaselineDumpStats {
	size_t excludedVars = 0, excludedFields = 0, excludedGlobals = 0, excludedClockGlobals = 0;
	size_t volatileFileEntries = 0;
	std::string summary;   // one line, e.g. "volatile keys: file=... entries=24 excluded vars=22 fields=1 globals=0 clock-globals=6"
};
const BaselineDumpStats& LastBaselineStats();

// Exact signature requested by L10 (usable as a plain function pointer). Uses default options (watch refs from
// env FNVCOOP_WATCHREFS if set). Never throws; engine faults (SEH) return false with *err set.
// Loads the volatile-keys file into the shared list (fnvcoop::volatileKeys()) and resolves its variable names against the loaded game, WITHOUT
// dumping the world. A host without a world baseline never dumps, so its list stayed empty: the poll committed per-machine vars (bFixedEDE,
// iSpawnMonsters ...) that the client's dump leaves out, and the digest differed on them forever. Idempotent; call again until it returns true
// (false = a name is still unresolved, or the file cannot be read; *summary says which). Main thread, after a save is loaded.
bool PrepareVolatileKeys(const BaselineDumpOptions& opts, std::string* summary);
bool DumpBaselineShadow(fnvcoop::ShadowState* out, uint64_t* contentHash, std::string* err);
bool DumpBaselineShadowEx(fnvcoop::ShadowState* out, uint64_t* contentHash, std::string* err,
                          const BaselineDumpOptions& opts);

}  // namespace fnvcoop::snapshot
