#include "vats/vats_api.h"

#include <cstdio>

#include "../../tools/mut/fnvcoop_mut.h"

namespace nvcoop::vats {

namespace {

const TargetInfo* findTarget(const std::vector<TargetInfo>& ts, uint32_t ref) {
    if (!ref) return nullptr;
    for (const TargetInfo& t : ts)
        if (t.actorRef == ref) return &t;
    return nullptr;
}

ActionResult fail(const VatsView& v, std::string why) {
    ActionResult r;
    r.ok = false;
    r.limb = v.highlighted;
    r.targetRef = v.targetRef;
    r.reason = std::move(why);
    return r;
}

ActionResult okResult(IVatsEngine& e) {
    const VatsView v = readView(e);
    ActionResult r;
    r.ok = true;
    r.limb = v.highlighted;
    r.targetRef = v.targetRef;
    return r;
}

std::string availableLimbs(const VatsView& v) {
    std::string s;
    for (const LimbEntry& l : v.limbs) {
        if (!s.empty()) s += ",";
        s += toString(l.limb);
    }
    return s.empty() ? std::string("none") : s;
}

}  // namespace

VatsView readView(IVatsEngine& e) {
    VatsView v;
    v.mode = e.mode();
    v.menuOpen = e.vatsMenuOpen();
    v.targetRef = e.selectedTargetRef();
    v.queued = e.queuedCount();
    v.actionPoints = e.actionPoints();
    v.maxActionPoints = e.maxActionPoints();
    v.remembered = e.rememberedLimb();
    if (!isActive(v.mode)) return v;                       // the engine lists are stale outside VATS: report nothing
    const std::vector<TargetInfo> ts = e.targets();
    v.targetCount = static_cast<int>(ts.size());
    const TargetInfo* t = findTarget(ts, v.targetRef);
    if (!t) return v;
    const uint32_t hl = e.highlightedPartRec();
    int idx = 0;
    for (const PartInfo& p : t->parts) {
        if (p.limb == LimbId::None) { ++idx; continue; }   // part 14 / unknown: not selectable
        LimbEntry le;
        le.limb = p.limb;
        le.index = idx++;
        le.highlighted = hl != 0 && p.rec == hl;
        if (le.highlighted) v.highlighted = p.limb;
        v.limbs.push_back(le);
    }
    return v;
}

std::string toJson(const VatsView& v) {
    std::string s = "{\"mode\":" + std::to_string(static_cast<int>(v.mode)) + ",\"modeName\":\"" + toString(v.mode) + "\",\"menuOpen\":" +
                    (v.menuOpen ? "true" : "false") + ",\"target\":" + std::to_string(v.targetRef) + ",\"targetCount\":" + std::to_string(v.targetCount) +
                    ",\"queued\":" + std::to_string(v.queued) + ",\"ap\":" + std::to_string(v.actionPoints) + ",\"maxAp\":" + std::to_string(v.maxActionPoints) +
                    ",\"limb\":\"" + toString(v.highlighted) + "\",\"limbAv\":" + std::to_string(static_cast<int>(v.highlighted)) +
                    ",\"rememberedLimb\":\"" + toString(v.remembered) + "\",\"limbs\":[";
    for (size_t i = 0; i < v.limbs.size(); ++i) {
        if (i) s += ",";
        s += std::string("{\"limb\":\"") + toString(v.limbs[i].limb) + "\",\"av\":" + std::to_string(static_cast<int>(v.limbs[i].limb)) +
             ",\"index\":" + std::to_string(v.limbs[i].index) + ",\"highlighted\":" + (v.limbs[i].highlighted ? "true" : "false") + "}";
    }
    s += "]}";
    return s;
}

ActionResult selectLimb(IVatsEngine& e, LimbId want) {
    VatsView v = readView(e);
    if (want == LimbId::None || !validLimb(static_cast<int>(want))) return fail(v, "unknown limb");
    if (!FNV_MUT(853) && !isSelecting(v.mode)) return fail(v, std::string("VATS is not in target / limb selection (mode ") + toString(v.mode) + ")");
    if (!v.targetRef) return fail(v, "no selected target");
    const std::vector<TargetInfo> ts = e.targets();
    const TargetInfo* t = findTarget(ts, v.targetRef);
    if (!t) return fail(v, "the selected target is not in the engine's target list");
    const PartInfo* part = nullptr;
    for (const PartInfo& p : t->parts)
        if (p.limb == want) { part = &p; break; }
    if (!part && FNV_MUT(851) && !t->parts.empty()) part = &t->parts.front();   // 851: a limb the target does not have is "selected" anyway (first part)
    if (!part) return fail(v, std::string("the target has no selectable ") + toString(want) + " (available: " + availableLimbs(v) + ")");
    if (v.highlighted == want && !FNV_MUT(855)) return okResult(e);           // 855: always writes, even when already highlighted
    if (!e.commitHighlight(part->rec, part->limb)) return fail(v, "the engine refused the highlight change");
    if (FNV_MUT(852)) return okResult(e);                                      // 852: no read-back (an engine that reverts the write is not noticed)
    const VatsView after = readView(e);
    if (after.highlighted != want) return fail(after, std::string("the engine did not keep the highlight (holds ") + toString(after.highlighted) + ")");
    return okResult(e);
}

ActionResult selectTarget(IVatsEngine& e, uint32_t ref, int maxSteps) {
    VatsView v = readView(e);
    if (!ref) return fail(v, "no target given");
    if (!isSelecting(v.mode)) return fail(v, std::string("VATS is not in target / limb selection (mode ") + toString(v.mode) + ")");
    const std::vector<TargetInfo> ts = e.targets();
    if (!findTarget(ts, ref)) return fail(v, "not in the engine's VATS target list");
    if (maxSteps < 1) maxSteps = 1;
    for (int i = 0; i < maxSteps; ++i) {
        if (e.selectedTargetRef() == ref) return okResult(e);
        const uint32_t before = e.selectedTargetRef();
        if (!e.cycleTarget(true)) return fail(readView(e), "the engine refused the target change");
        if (e.selectedTargetRef() == before && !FNV_MUT(854)) return fail(readView(e), "the selected target did not change after cycling");
    }
    if (e.selectedTargetRef() == ref) return okResult(e);
    return fail(readView(e), "target not reached within the step limit");
}

ActionResult fire(IVatsEngine& e) {
    const VatsView v = readView(e);
    if (!FNV_MUT(856) && !isSelecting(v.mode)) return fail(v, std::string("nothing to fire: VATS mode is ") + toString(v.mode));
    if (v.queued <= 0 && !FNV_MUT(857)) return fail(v, "no VATS action is queued");
    if (!e.accept()) return fail(v, "the engine refused the accept");
    return okResult(e);
}

ActionResult cancelAll(IVatsEngine& e, int maxPops) {
    VatsView v = readView(e);
    int n = 0;
    while (e.queuedCount() > 0 && n < maxPops) {
        if (!e.unclickLast()) return fail(readView(e), "the engine refused to pop a queued action");
        ++n;
    }
    v = readView(e);
    if (v.queued > 0) return fail(v, "queued actions remain");
    return okResult(e);
}

}  // namespace nvcoop::vats
