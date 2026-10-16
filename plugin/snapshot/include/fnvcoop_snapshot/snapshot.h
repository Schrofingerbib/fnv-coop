// Canonical world-state snapshot (see docs/snapshot-schema.md).
#pragma once
#include <string>
#include <vector>

namespace fnvcoop::snapshot {

struct SnapshotOptions {
	bool shared = true;      // emit the "shared" section
	bool perPlayer = true;   // emit the "perPlayer" section
	std::string playerId = "local"; // key under perPlayer for the local player ("host", "p1", ...)
	std::string instance;    // free-form label copied to meta.instance
	std::string timestamp;   // ISO-8601 UTC supplied by caller; empty => omitted (diffs ignore it anyway)
	int indent = -1;         // <0 compact, >=0 pretty
	// Extra refs to report in shared.refs, as "Plugin.esm:XXXXXX" (e.g. from quests.json).
	// Objective target refs of every displayed objective are always added automatically.
	std::vector<std::string> watchRefs;
	// Optional text file (one "Plugin.esm:XXXXXX" per line, '#' comments) merged into watchRefs. If empty,
	// the env var FNVCOOP_WATCHREFS is used when set. Build the file with tools/watchrefs.py
	// (host-owned refs from out/coverage/hostowned.txt + the M1 refs from out/questdata/m1_goodsprings.json).
	std::string watchRefsFile;
	// If non-empty, only quests whose EDID starts with one of these prefixes are emitted.
	// Leave empty for oracle use: a filtered dump cannot satisfy the empty-snapshot guard.
	std::vector<std::string> questEdidPrefixes;
};

// Entry point for L1's nvbridge `state` method. Must be called on the game main thread.
// Returns the JSON text of the snapshot. Never throws; on engine access failure returns
// a snapshot whose meta.error is set (and whose counts are 0, so the diff guard fails it).
std::string SnapshotJson(const SnapshotOptions& opts);

// Pure helpers (game-free; used by the dumper and unit tests).
std::string FormRefString(const std::string& pluginName, unsigned localId); // "Plugin.esm:00ABCD" (upper-case hex, 6 digits)
unsigned long long Fnv1a64(const std::string& s);
std::string Hex64(unsigned long long v);
// Parse a watch-ref list: trims, skips blanks/'#' comments and anything that is not "name.es[mp|l]:XXXXXX",
// upper-cases the hex id, de-duplicates and sorts. Returns empty if the file cannot be read.
std::vector<std::string> LoadWatchRefsFile(const std::string& path);
std::vector<std::string> ParseWatchRefs(const std::string& text);

} // namespace fnvcoop::snapshot
