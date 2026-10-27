// Ref script variables (`set Ref.var to x`): which refs the host reads, how often, and what counts as a change. Game-free and unit-tested;
// the engine glue (world_hooks.cpp) only implements IRefVarSource.
//
// Why a poll and not a hook per write: the script interpreter's variable store has no verified single write point (docs/engine-addresses.md
// lists none), so a write is seen by reading the ref's script event list. Three things decide what the poll can see:
//   * WHERE it looks: the refs of the player's cell, every loaded actor (the process lists reach neighbouring cells), and the WATCHED refs
//     (the generated list of refs other scripts write to by name, the test watches, the live spawns) wherever the player is.
//   * HOW OFTEN: watched refs every `fastEvery` frames (default 2), the sweep every `sweepEvery` (15). A flag that is set and cleared again
//     inside one interval is lost; `afterScript` closes that for refs whose own script just ran (the script-run observer reads the ref the
//     moment its script ends, so a write made by two runs in one frame is two values).
//   * WHAT it reports: the change from the last REPORTED value (see RefVarTracker), so a flip made while the ref was out of sight is sent when
//     the ref is read again.
#pragma once
#include <cstdint>
#include <unordered_set>
#include <utility>
#include <vector>

#include "world_core.h"

namespace nvworld {

struct RefVarChange {
    uint32_t refID = 0;
    uint16_t index = 0;
    double value = 0;
};

class IRefVarSource {
public:
    virtual ~IRefVarSource() = default;
    virtual uint32_t playerCell() = 0;                          // 0 = unknown
    virtual void cellRefs(std::vector<uint32_t>& out) = 0;      // refs in the player's cell
    virtual void actorRefs(std::vector<uint32_t>& out) = 0;     // loaded actors, any cell the engine keeps in a process list
    virtual void watchedRefs(std::vector<uint32_t>& out) = 0;   // refs polled wherever the player is (engine ids that resolve now)
    virtual bool readVars(uint32_t refID, std::vector<std::pair<uint16_t, double>>& out) = 0;  // false: the ref (or its script) is not here now
};

struct RefVarPollCfg {
    uint32_t fastEvery = 2;      // frames between reads of the watched refs (0 = never)
    uint32_t sweepEvery = 15;    // frames between sweeps of the player's cell + the loaded actors
    size_t fastBudget = 64;      // changes reported per fast read
    size_t sweepBudget = 64;     // changes reported per sweep
    bool reportFirstOfWatched = true;
};

class RefVarPoller {
public:
    explicit RefVarPoller(RefVarPollCfg cfg = RefVarPollCfg()) : cfg_(cfg) {}
    void setConfig(const RefVarPollCfg& c) { cfg_ = c; }
    const RefVarPollCfg& config() const { return cfg_; }

    // Once per frame. Appends the changes that are due this frame.
    void tick(uint32_t frame, IRefVarSource& src, std::vector<RefVarChange>& out);
    // Everything, now, no budget (diagnostics and tests).
    size_t pollAll(IRefVarSource& src, std::vector<RefVarChange>& out);
    // The script of `refID` just ended: read its variables now. The changes wait in the pending list until the next tick (the caller may be
    // inside a capture scope); `takePending` hands them out.
    void afterScript(uint32_t refID, IRefVarSource& src);
    std::vector<RefVarChange> takePending();
    size_t pending() const { return pending_.size(); }

    void reset();
    RefVarTracker& tracker() { return t_; }
    uint64_t sweeps() const { return sweeps_; }
    uint64_t fastReads() const { return fastReads_; }

private:
    void visit(IRefVarSource& src, uint32_t ref, bool pinned, std::vector<RefVarChange>& out);
    void cellChanged(IRefVarSource& src);

    RefVarPollCfg cfg_;
    RefVarTracker t_;
    uint32_t lastCell_ = 0;
    bool haveCell_ = false;
    size_t cursor_ = 0;                       // round-robin start of the sweep list (a budget that ran out resumes here)
    std::unordered_set<uint32_t> pinned_;     // the watched refs of the last read
    std::vector<RefVarChange> pending_;
    std::vector<std::pair<uint16_t, double>> scratch_;
    uint64_t sweeps_ = 0, fastReads_ = 0;
};

}  // namespace nvworld
