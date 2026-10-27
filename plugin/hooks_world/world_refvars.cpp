#include "world_refvars.h"

#include <algorithm>

#include "fnvcoop_mut.h"

namespace nvworld {

void RefVarPoller::reset() {
    t_.clear();
    haveCell_ = false;
    lastCell_ = 0;
    cursor_ = 0;
    pinned_.clear();
    pending_.clear();
}

void RefVarPoller::visit(IRefVarSource& src, uint32_t ref, bool pinned, std::vector<RefVarChange>& out) {
    if (!ref) return;
    scratch_.clear();
    if (!src.readVars(ref, scratch_)) return;
    for (const auto& v : scratch_)
        if (t_.observe(ref, v.first, v.second, pinned && cfg_.reportFirstOfWatched)) out.push_back(RefVarChange{ref, v.first, v.second});
}

void RefVarPoller::cellChanged(IRefVarSource& src) {
    const uint32_t cell = src.playerCell();
    if (haveCell_ && cell == lastCell_) return;
    haveCell_ = true;
    lastCell_ = cell;
    cursor_ = 0;
    // a new cell: what was only read because the player stood in the old one re-baselines; the watched refs keep their last reported value
    t_.forgetAllExcept(pinned_);
}

void RefVarPoller::tick(uint32_t frame, IRefVarSource& src, std::vector<RefVarChange>& out) {
    // changes noted by afterScript go first (they are older than anything a read finds now)
    if (!pending_.empty()) {
        for (auto& c : pending_) out.push_back(c);
        pending_.clear();
    }
    const bool fast = cfg_.fastEvery && frame % cfg_.fastEvery == 0;
    const bool sweep = cfg_.sweepEvery && frame % cfg_.sweepEvery == 0;
    if (!fast && !sweep) return;

    std::vector<uint32_t> watched;
    src.watchedRefs(watched);
    pinned_.clear();
    pinned_.insert(watched.begin(), watched.end());

    if (fast && !watched.empty()) {
        ++fastReads_;
        t_.beginPoll(cfg_.fastBudget);
        for (uint32_t r : watched) visit(src, r, true, out);
    }
    if (sweep) {
        ++sweeps_;
        cellChanged(src);
        t_.beginPoll(cfg_.sweepBudget);
        std::vector<uint32_t> list;
        src.cellRefs(list);
        if (!FNV_MUT(56)) src.actorRefs(list);   // MUTANT 56: only the player's cell is read (an NPC in the next cell is never seen)
        // watched refs are read by the fast lane; with the fast lane off the sweep reads them
        if (!cfg_.fastEvery) list.insert(list.end(), watched.begin(), watched.end());
        std::unordered_set<uint32_t> seen;
        std::vector<uint32_t> uniq;
        uniq.reserve(list.size());
        for (uint32_t r : list)
            if (r && seen.insert(r).second && (cfg_.fastEvery == 0 || pinned_.count(r) == 0)) uniq.push_back(r);
        if (uniq.empty()) {
            cursor_ = 0;
            return;
        }
        if (cursor_ >= uniq.size() || FNV_MUT(57)) cursor_ = 0;   // MUTANT 57: a budget that ran out always starts over at the first ref (it starves the rest)
        size_t visited = 0;
        size_t i = cursor_;
        for (; visited < uniq.size(); ++visited, i = (i + 1) % uniq.size()) {
            visit(src, uniq[i], false, out);
            if (t_.reportedThisPoll() >= t_.budget()) break;
        }
        // the budget ran out at ref i: the next sweep starts after it, so a ref that changes on every sweep cannot starve the ones behind it
        cursor_ = visited < uniq.size() ? (i + 1) % uniq.size() : 0;
    }
}

size_t RefVarPoller::pollAll(IRefVarSource& src, std::vector<RefVarChange>& out) {
    const size_t before = out.size();
    std::vector<uint32_t> watched;
    src.watchedRefs(watched);
    pinned_.clear();
    pinned_.insert(watched.begin(), watched.end());
    cellChanged(src);
    t_.beginPoll(size_t(1) << 20);
    std::vector<uint32_t> list = watched;
    src.cellRefs(list);
    src.actorRefs(list);
    std::unordered_set<uint32_t> seen;
    for (uint32_t r : list)
        if (r && seen.insert(r).second) visit(src, r, pinned_.count(r) != 0, out);
    return out.size() - before;
}

void RefVarPoller::afterScript(uint32_t refID, IRefVarSource& src) {
    if (!refID || FNV_MUT(58)) return;   // MUTANT 58: the script-run observer is ignored (a flag set and cleared by two runs in one frame is lost)
    if (pending_.size() > 4096 || pinned_.count(refID) == 0) return;   // only watched refs: every script run of the cell would be too many
    scratch_.clear();
    if (!src.readVars(refID, scratch_)) return;
    t_.beginPoll(size_t(1) << 20);
    const bool pinned = true;
    for (const auto& v : scratch_)
        if (t_.observe(refID, v.first, v.second, pinned && cfg_.reportFirstOfWatched)) pending_.push_back(RefVarChange{refID, v.first, v.second});
}

std::vector<RefVarChange> RefVarPoller::takePending() {
    std::vector<RefVarChange> out;
    out.swap(pending_);
    return out;
}

}  // namespace nvworld
