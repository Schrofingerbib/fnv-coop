// RollDetector: turns "the actor feed saw a created actor nobody announced" into rolls.
//
// The actor feed asks the created-id resolver about every dynamic (0xFF) actor it finds. One that no SpawnRef, ledger binding or host
// script capture explains was made by THIS machine's engine on its own: a random encounter, a marker's spawn, a leveled summon. The
// engine makes the members of one encounter within a frame or two of each other, so candidates that are close in space and time are one
// roll. Game-free.
#pragma once
#include <functional>
#include <vector>
#include "spawn/spawn_types.h"

namespace nvcoop::spawn {

class RollDetector {
public:
    explicit RollDetector(size_t cap = 256) : cap_(cap) {}
    // Idempotent: the same ref noted again keeps its first-seen time. Returns false when the list is full (the ref is asked again next scan).
    bool note(RefId ref, uint64_t nowMs);
    void forget(RefId ref);
    bool has(RefId ref) const;
    size_t size() const { return c_.size(); }
    void clear() { c_.clear(); }

    // Groups of settled candidates. A candidate is settled when it has been known for `settleMs`; a group is released when ALL its
    // members are settled (so the last actor of an encounter is not left behind in a second roll). `where` gives a ref's cell and
    // position; false = the ref is gone, it is dropped. Released refs leave the list.
    using Where = std::function<bool(RefId, CellId&, sp::Vec3&)>;
    std::vector<std::vector<RefId>> take(uint64_t nowMs, uint32_t settleMs, float radius, size_t maxGroup, const Where& where);

private:
    struct Cand { RefId ref = 0; uint64_t firstSeen = 0; };
    size_t cap_;
    std::vector<Cand> c_;
};

}  // namespace nvcoop::spawn
