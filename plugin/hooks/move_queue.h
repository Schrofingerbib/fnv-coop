// Moves that could not be performed yet (story-capture, docs/tools/story-capture.md). A MoveRef to a cell this machine has not loaded has no
// MoveTo anchor to run against; instead of failing it is parked here and retried until the cell has loaded (or it gives up). The newest move of a
// reference replaces an older parked one, so a reference that was moved twice while its cell was away ends where the host left it.
// Game-free; RemoteApplier drives it from the frame callback.
#pragma once
#include <cstdint>
#include <functional>
#include <string>
#include <vector>

#include "fnvcoop/effect.h"
#include "fnvcoop_mut.h"

namespace nvhooks {

class MoveQueue {
public:
    void add(const fnvcoop::MoveRef& m) {
        for (Item& it : items_) {
            if (it.move.ref == m.ref) {
                if (!FNV_MUT(7)) { it.move = m; it.attempts = 0; }   // twin 7: the OLD move stays (a ref is left where it was first sent)
                return;
            }
        }
        items_.push_back({m, 0});
    }
    // Calls `tryMove` for every parked move in the order they were first parked. True = done (removed). A move that fails `maxAttempts` times is
    // dropped (the cell never loaded here). Returns how many were done.
    size_t run(const std::function<bool(const fnvcoop::MoveRef&)>& tryMove, uint32_t maxAttempts = 120) {
        size_t done = 0;
        std::vector<Item> keep;
        for (Item& it : items_) {
            if (tryMove(it.move)) { ++done; continue; }
            if (++it.attempts < maxAttempts || FNV_MUT(8)) keep.push_back(it);   // twin 8: never gives up
        }
        items_ = std::move(keep);
        return done;
    }
    size_t size() const { return items_.size(); }
    void clear() { items_.clear(); }
    bool holds(const fnvcoop::FormRef& ref) const {
        for (const Item& it : items_) if (it.move.ref == ref) return true;
        return false;
    }

private:
    struct Item {
        fnvcoop::MoveRef move;
        uint32_t attempts = 0;
    };
    std::vector<Item> items_;
};

// The console executor a RemoteApplier uses: its own when one was set, else the process-wide one (nvhooks::setConsoleExec, installed by the hooks
// module). coop_module.cpp builds its RemoteApplier without an executor of its own, so without this fallback every console-line effect (kill,
// resurrect, move, ref inventory, object state, faction, perks ...) would answer Unsupported in a real session.
using ExecFn = std::function<bool(const std::string&, uint32_t)>;
inline ExecFn pickExec(const ExecFn& own, const ExecFn& global) {
    if (FNV_MUT(10)) return own;          // twin 10: no fallback (an applier without an executor of its own cannot run any console line)
    if (own && !FNV_MUT(9)) return own;   // twin 9: the process-wide executor always wins (a test / world-lane executor is ignored)
    return global ? global : own;
}

}  // namespace nvhooks
