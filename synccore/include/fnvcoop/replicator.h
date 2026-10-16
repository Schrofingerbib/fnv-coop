// StateReplicator: host-side coalescing channel for quest variables and globals.
//
// The host runs every quest script, and some write variables every frame (hundreds of writes per second). Committing
// each write would flood the log. Hooks call setScriptVar()/setGlobal() for every write; the replicator keeps only the
// LAST value per key, drops values equal to what replicas already have, and on flush() commits one txn of at most
// `maxEffectsPerFlush` effects, throttled by a token-bucket bandwidth budget (`maxEffectsPerSec`). Keys that did not
// fit stay pending (oldest first, so nothing starves) and always carry their newest value when finally sent.
#pragma once
#include <cstdint>
#include <deque>
#include <map>
#include <unordered_map>
#include "fnvcoop/session.h"

namespace fnvcoop {

class StateReplicator {
public:
    struct Options {
        size_t maxEffectsPerFlush = 48;   // per committed txn
        uint32_t minIntervalMs = 33;      // flush at most ~30x/s
        uint32_t maxEffectsPerSec = 600;  // bandwidth budget (token bucket)
    };
    struct Stats {
        uint64_t writes = 0, coalesced = 0, skippedUnchanged = 0, effectsCommitted = 0, txns = 0, deferredByBudget = 0, volatileDropped = 0;
    };

    explicit StateReplicator(HostSession& host) : StateReplicator(host, Options()) {}
    StateReplicator(HostSession& host, Options opt) : host_(host), opt_(opt), tokens_(double(opt.maxEffectsPerFlush)) {}

    void setScriptVar(const FormRef& owner, uint16_t index, double value);
    void setGlobal(const FormRef& global, float value);

    // Call every host tick. Returns the committed seq (0 if nothing was sent).
    uint64_t flush(uint64_t nowMs);
    size_t pending() const { return pending_.size(); }
    void clear() { pending_.clear(); order_.clear(); }   // host world was replaced: queued writes belong to the old world
    const Stats& stats() const { return stats_; }

private:
    struct Key {
        bool isGlobal = false;
        FormRef form;
        uint16_t index = 0;
        bool operator<(const Key& o) const {
            if (isGlobal != o.isGlobal) return isGlobal < o.isGlobal;
            if (form != o.form) return form < o.form;
            return index < o.index;
        }
    };
    static uint64_t hashOf(const Key& k);
    void put(const Key& k, double v);
    HostSession& host_;
    Options opt_;
    // Hot path (thousands of writes per frame): index by a precomputed 64-bit hash of the key, collisions resolved by probing +1.
    struct Entry { Key key; double value; };
    std::unordered_map<uint64_t, Entry> pending_;
    std::deque<uint64_t> order_;       // slot ids in first-write order
    double tokens_;
    uint64_t lastFlush_ = 0, lastRefill_ = 0;
    bool started_ = false;
    Stats stats_;
};

}  // namespace fnvcoop
