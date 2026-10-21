// Corpse resting place (enemy-anim). A body lands wherever its ragdoll takes it, which differs per machine (physics is not synced). Only the
// OWNER's machine decides where the corpse rests: it watches the dead actor until the body stops, then sends the final pose once, reliably
// (an ActorSync MAnimEvent). Everyone else lets their own ragdoll play (visual only) and, once it has settled, moves the body to the owner's
// resting place if it ended up further away than a small tolerance.
#pragma once
#include <map>

#include "npc_anim_config.h"
#include "npc_anim_engine.h"

namespace nvcoop::actors::anim {

constexpr uint8_t kAnimEventCorpse = 1;   // first byte of an MAnimEvent payload
constexpr uint8_t kCorpseVersion = 1;

struct CorpsePose {
    CellId cell = 0;
    Vec3 pos;
    float yawRad = 0;
    bool ragdoll = false;     // the body was a ragdoll (vs. a plain death animation) when it stopped
    bool forced = false;      // reported because the wait ran out or the body vanished, not because it came to rest
};

// Payload of an MAnimEvent. Decode returns false on anything malformed (wrong kind or version, non-finite numbers, trailing bytes).
std::vector<uint8_t> encodeCorpse(const CorpsePose& p);
bool decodeCorpse(const uint8_t* data, size_t size, CorpsePose& out);
inline bool decodeCorpse(const std::vector<uint8_t>& b, CorpsePose& out) { return decodeCorpse(b.data(), b.size(), out); }

class CorpseWatcher {
public:
    explicit CorpseWatcher(const CorpseWatchConfig& cfg = {}) : cfg_(cfg) {}
    void begin(NetId id, uint32_t nowMs);
    void end(NetId id) { w_.erase(id); }
    bool watching(NetId id) const { return w_.count(id) != 0; }
    size_t count() const { return w_.size(); }
    // Feed the body's current place. Returns true exactly once per watched actor, when it has been at rest for restMs (after minRagdollMs)
    // or the wait ran out; `out` is then the pose to publish and the actor is no longer watched.
    bool feed(NetId id, CellId cell, const Vec3& pos, float yawRad, bool ragdoll, uint32_t nowMs, CorpsePose* out);
    // The body can no longer be read (unloaded, deleted): publish the last place it was seen at, if any.
    bool flush(NetId id, uint32_t nowMs, CorpsePose* out);
    uint32_t pollMs() const { return cfg_.pollMs; }
    bool due(NetId id, uint32_t nowMs) const;
    std::vector<NetId> ids() const;

private:
    struct W {
        uint32_t since = 0;
        uint32_t lastPoll = 0;
        bool have = false;
        CellId cell = 0;
        Vec3 anchor;              // where the body was when it last moved more than restUnits
        uint32_t anchorSince = 0;
        Vec3 last;
        float lastYaw = 0;
        bool ragdoll = false;
    };
    CorpseWatchConfig cfg_;
    std::map<NetId, W> w_;
};

}  // namespace nvcoop::actors::anim
