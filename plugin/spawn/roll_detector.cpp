#include "spawn/roll_detector.h"
#include <algorithm>
#include <cmath>
#include "fnvcoop_mut.h"

// Mutation twins (plugin/spawn uses 41..60): 51 groups ignore the cell, 52 a group is released before it settled.
namespace nvcoop::spawn {

bool RollDetector::note(RefId ref, uint64_t nowMs) {
    for (const auto& c : c_) if (c.ref == ref) return true;
    if (c_.size() >= cap_) return false;
    c_.push_back({ref, nowMs});
    return true;
}

void RollDetector::forget(RefId ref) {
    c_.erase(std::remove_if(c_.begin(), c_.end(), [&](const Cand& c) { return c.ref == ref; }), c_.end());
}

bool RollDetector::has(RefId ref) const {
    for (const auto& c : c_) if (c.ref == ref) return true;
    return false;
}

std::vector<std::vector<RefId>> RollDetector::take(uint64_t nowMs, uint32_t settleMs, float radius, size_t maxGroup, const Where& where) {
    std::vector<std::vector<RefId>> out;
    if (c_.empty()) return out;
    struct G { CellId cell = 0; sp::Vec3 center; std::vector<RefId> refs; uint64_t newest = 0; };
    std::vector<G> groups;
    std::vector<Cand> gone;
    for (const auto& c : c_) {
        CellId cell = 0;
        sp::Vec3 pos;
        if (!where(c.ref, cell, pos)) { gone.push_back(c); continue; }
        G* home = nullptr;
        for (auto& g : groups) {
            if (g.refs.size() >= maxGroup) continue;
            if (g.cell != cell && !FNV_MUT(51)) continue;       // twin 51: refs from different cells become one roll
            const float dx = g.center.x - pos.x, dy = g.center.y - pos.y;
            if (std::sqrt(dx * dx + dy * dy) <= radius) { home = &g; break; }
        }
        if (!home) { groups.emplace_back(); home = &groups.back(); home->cell = cell; home->center = pos; }
        home->refs.push_back(c.ref);
        home->newest = std::max(home->newest, c.firstSeen);
    }
    std::vector<RefId> taken;
    for (auto& g : groups) {
        const bool settled = nowMs >= g.newest + settleMs;
        if (!settled && !FNV_MUT(52)) continue;                  // twin 52: released while the encounter is still arriving
        out.push_back(g.refs);
        for (RefId r : g.refs) taken.push_back(r);
    }
    for (const auto& c : gone) taken.push_back(c.ref);
    c_.erase(std::remove_if(c_.begin(), c_.end(), [&](const Cand& c) { return std::find(taken.begin(), taken.end(), c.ref) != taken.end(); }), c_.end());
    return out;
}

}  // namespace nvcoop::spawn
