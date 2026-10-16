// StoryLog: ordered, append-only transaction log held by the host (canonical story history).
// Late join / rejoin = replay from a seq, or snapshot of the head ShadowState when the log was compacted.
#pragma once
#include <cstdint>
#include <deque>
#include <memory>
#include <vector>
#include "fnvcoop/shadow.h"

namespace fnvcoop {

class StoryLog {
public:
    // Assigns seq = head()+1, folds into the head state, stores the txn. Returns the assigned seq.
    uint64_t append(EffectTxn txn);

    // Seed an EMPTY log with a non-empty starting world (join-in-progress sessions: the host's live state at session
    // start). Returns false if txns were already appended.
    bool setInitialState(const ShadowState& s);

    uint64_t head() const { return head_.seq; }
    uint64_t firstSeq() const { return base_.seq + 1; }  // seq of the oldest retained txn (head()+1 if empty)
    size_t size() const { return txns_.size(); }
    bool canReplayFrom(uint64_t afterSeq) const { return afterSeq >= base_.seq && afterSeq <= head_.seq; }

    const EffectTxn* get(uint64_t seq) const;
    // Up to maxCount txns with seq > afterSeq, in order.
    std::vector<EffectTxn> range(uint64_t afterSeq, size_t maxCount) const;

    const ShadowState& headState() const { return head_; }
    // The state exactly as of `seq` (base + retained txns up to seq). False if seq is below the retained base or above head.
    bool stateAt(uint64_t seq, ShadowState& out) const;
    const ShadowState& baseState() const { return base_; }

    // Drop all but the newest `keep` txns (folded into the base state). Replay below the new base is impossible.
    void compact(size_t keep);

    // Persistence of the whole log (base + retained txns). load() rebuilds the head by replay; false on bad data.
    std::vector<uint8_t> save() const;
    bool load(const uint8_t* data, size_t size);

private:
    static constexpr uint64_t kCheckpointEvery = 128;   // checkpoint spacing: bounds stateAt() replay
    static constexpr size_t kMaxCheckpoints = 8;
    std::vector<std::pair<uint64_t, std::shared_ptr<const ShadowState>>> checkpoints_;
    ShadowState base_;  // state just before txns_.front()
    ShadowState head_;
    std::deque<EffectTxn> txns_;
};

}  // namespace fnvcoop
