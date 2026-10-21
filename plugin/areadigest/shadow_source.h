// Containers, objects and the story digest from the session's shared-world shadow (fnvcoop::ShadowState).
//
// What this is, honestly: the digest of what the SESSION believes about each container and object (opened / locked / destroyed state from
// ObjState + locks, contents deltas from RefInv), compared host against client. It finds a client whose session state drifted (a lost or
// mis-ordered txn, a rebase gone wrong) per reference and per cell, every 2 s. It does NOT read the real chest contents or door state from
// the engine: that reader does not exist yet (TODO-RE, see docs/tools/area-desync.md "Not covered"). The engine-level drift of those is still
// caught by the 10 s story digest of the real world dump (HostSession desync detection), which this module asks to run early.
// Both categories are `delegated`: a mismatch asks the story layer to repair (requestRepair), the values are never pushed from here.
#pragma once
#include <functional>
#include <map>
#include "areadigest/areadigest.h"

namespace nvcoop::areadigest {

// Stable 64-bit key of a reference: the same on every machine (plugin names compare lower-cased).
fnvcoop::areadigest::Key keyOfRef(const fnvcoop::FormRef& r);

using WorldGetter = std::function<const fnvcoop::ShadowState*()>;
// ref -> the cell it sits in; false = unknown (the item then lives in "cell 0", compared as one area).
using CellOfRef = std::function<bool(const fnvcoop::FormRef&, fnvcoop::areadigest::CellId&)>;

class ShadowRefSource : public fnvcoop::areadigest::IAreaSource {
public:
    ShadowRefSource(fnvcoop::areadigest::Cat cat, WorldGetter world, CellOfRef cellOf, const fnvcoop::areadigest::Config& cfg);

    fnvcoop::areadigest::Cat category() const override { return cat_; }
    void collect(uint64_t nowMs, std::vector<fnvcoop::areadigest::Item>& items, std::vector<fnvcoop::areadigest::CellId>& loadedCells) override;
    bool find(fnvcoop::areadigest::Key key, fnvcoop::areadigest::Item& out, uint64_t nowMs) override;
    fnvcoop::areadigest::HealResult heal(const fnvcoop::areadigest::Item&, const fnvcoop::areadigest::Diff&, uint64_t) override {
        return fnvcoop::areadigest::HealResult::Refused;   // delegated: the story layer repairs
    }
    bool delegated() const override { return true; }
    void setConfig(const fnvcoop::areadigest::Config& c) { tr_.setConfig(c); }

private:
    void build(std::vector<fnvcoop::areadigest::Item>& out) const;
    fnvcoop::areadigest::Cat cat_;
    WorldGetter world_;
    CellOfRef cellOf_;
    fnvcoop::areadigest::ChangeTracker tr_;
};

// The story digest source over a session: the shadow plus the applied seq and world epoch, and the repair hooks of the host session.
class ShadowStorySource : public fnvcoop::areadigest::IStorySource {
public:
    struct World {
        const fnvcoop::ShadowState* state = nullptr;
        uint64_t seq = 0;
        uint32_t epoch = 0;
    };
    std::function<bool(World&)> world;                       // false = no session state right now
    std::function<void(fnvcoop::PlayerId)> repair;           // host: HostSession::requestDigest(slot)
    std::function<uint64_t()> repairCount;                   // host: HostStats::desyncRepairs

    bool local(fnvcoop::areadigest::StoryDigest& out) override;
    void requestRepair(fnvcoop::PlayerId p) override { if (repair) repair(p); }
    uint64_t repairs() override { return repairCount ? repairCount() : 0; }
};

}  // namespace nvcoop::areadigest
