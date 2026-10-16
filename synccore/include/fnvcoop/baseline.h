// World baseline: joining a game that is already in progress.
//
// The StoryLog only covers transactions since the session started. A friend joining a host that is 20 hours into a
// save has a world from THEIR OWN save, so a full shared-state transfer is needed first:
//
//   host engine dump  --> ShadowState (shared maps; ledger[0].karma/rep = host's ABSOLUTE karma/reputation)
//   client engine dump--> ShadowState (same layout, from the client's own save)
//   client sends a BaselineSummary (hash per entry, ~12 bytes each); host replies with only the entries that differ
//   (plus "revert" keys for state only the client has, e.g. a kill), chunked, in class order:
//     0 quest run/complete state -> 1 stages -> 2 objectives -> 3 script vars -> 4 globals -> 5 refs/world ->
//     6 factions -> 7 flags (say-once, slideshow, save suppress) -> 8 absolute karma/reputation as deltas
//   The client applies the chunks through IEffectSink with Context::baseline == true (scripts gated, EchoGuard held),
//   then the session continues with live txns that were buffered meanwhile.
//
// Contract for the engine side (L7 dumper / L5 applier / L10 runtime): see internal lane notes "World baseline".
#pragma once
#include <cstdint>
#include <string>
#include <utility>
#include <vector>
#include "fnvcoop/policy.h"
#include "fnvcoop/shadow.h"

namespace fnvcoop {

struct RepAbs {
    FormRef faction;
    int64_t fame = 0, infamy = 0;
    bool operator==(const RepAbs& o) const { return faction == o.faction && fame == o.fame && infamy == o.infamy; }
};

struct BaselineEntry {
    Effect effect;      // the effect that establishes this entry on a world
    uint8_t cls = 0;    // ordering class (see header comment)
    uint64_t key = 0;   // hash of the entry's identity (which thing)
    uint32_t value = 0; // hash of identity + value
};

// What a joining client tells the host about its own world.
struct BaselineSummary {
    uint64_t contentHash = 0;                            // hash of the plugin load order; must match the host's
    std::vector<std::pair<uint64_t, uint32_t>> entries;  // (key, value) hashes
    int64_t karma = 0;
    std::vector<RepAbs> rep;
};

struct BaselineDiff {
    std::vector<Effect> effects;         // in class order
    std::vector<uint64_t> revertKeys;    // entries only the client has (client resolves them from its own world)
    size_t keptByClient = 0;             // identical entries not sent
};

// True for effect kinds the baseline transfer already establishes (state the host's baselineEntries() enumerates).
// After a baseline join only the REST of the snapshot diff is applied: per-slot ledger deltas (items, AV, karma/rep,
// perks, controls) and shared state the baseline does not carry (barrier releases).
bool isBaselineCovered(const Effect& e);

// Canonical, ordered entry list for a world (shared state only; ledgers are ignored).
std::vector<BaselineEntry> baselineEntries(const ShadowState& world);
BaselineSummary summarizeWorld(const ShadowState& world, uint64_t contentHash);

// Host side: effects that bring a world matching `client` to `hostWorld` (as of the join snapshot).
// hostKarma/hostRep = absolute values to give THIS joiner (already includes the session's deltas for its slot);
// sync flags select which absolute categories to sync. Unshared categories are dropped via the policy.
BaselineDiff diffBaseline(const ShadowState& hostWorld, const BaselineSummary& client, const SharePolicy& policy,
                          PlayerId joiner, bool syncKarma, int64_t hostKarma, bool syncRep,
                          const std::vector<RepAbs>& hostRep);

// Client side: resolve a revert key against the client's own world (kills -> ResurrectActor). Returns false if the
// entry kind has no safe inverse (counted as "unrevertable" by the session).
bool revertEffectFor(const ShadowState& clientWorld, uint64_t key, Effect& out);

// ---- runtime desync detection helpers ----
constexpr size_t kSyncShards = 128;   // 8 entry classes x 16 key buckets
// Entry (key/value hash/class) for an effect that is a baseline-style state entry. False => the effect is not world state
// (items, events, ...), key/value are meaningless.
bool entryOf(const Effect& e, BaselineEntry& out);
size_t shardOf(const BaselineEntry& e);   // class*16 + key%16
// Order-independent per-shard hashes of an entry list (volatile keys never appear in entry lists).
std::vector<uint32_t> shardHashes(const std::vector<BaselineEntry>& entries);
// Shard hashes straight from the maps (no per-entry objects or allocations): what digests use. Same result as shardHashes(baselineEntries(w)).
std::vector<uint32_t> shardHashesOf(const ShadowState& w);

// Compact human-readable description of an entry effect ("SetStage 0x1234@falloutnv.esm = 40").
std::string describeEntry(const Effect& e);

// Diagnostics: how a state entry looks to the baseline / digest machinery. `probe` is any effect that names the entry
// (e.g. SetScriptVar{owner, index, 0}); the value in the probe is ignored. Reports presence in `w`, the stored effect, key and value
// hash, class/shard and whether the volatile list excludes it.
std::string explainEntry(const ShadowState& w, const Effect& probe);

// 64-bit FNV-1a of the plugin list, convenience for callers.
uint64_t contentHashOf(const std::vector<std::string>& pluginNames);

}  // namespace fnvcoop
