// Test helpers for synccore/spawn.
#pragma once
#include <string>
#include <vector>
#include "minitest.h"
#include "spawn/spawn.h"
#include "spawn/spawn_wire.h"

namespace spt {

using namespace fnvcoop;
using namespace fnvcoop::spawn;

inline FormRef F(uint32_t id, const char* plugin = "falloutnv.esm") { return FormRef(plugin, id); }
inline Vec3 V(float x = 0, float y = 0, float z = 0) { Vec3 v; v.x = x; v.y = y; v.z = z; return v; }

inline SiteDef mkDef(SiteKind k, uint32_t anchor, CellId cell, bool interior = false, Vec3 pos = Vec3(), uint32_t respawnHours = 0) {
    SiteDef d;
    d.kind = k; d.anchor = F(anchor); d.cellForm = F(0x100000 + cell); d.cell = cell; d.pos = pos; d.interior = interior; d.respawnHours = respawnHours;
    d.id = siteIdOf(k, d.anchor, cell);
    return d;
}

inline Member placed(NetId id, uint32_t base, uint32_t handle = 0) {
    Member m;
    m.netId = id; m.created = false; m.base = F(base); m.handle = handle ? handle : id;
    return m;
}
// created member: a client report leaves netId 0 (the host issues it); a host report carries the host engine's own id
inline Member created(uint32_t base, uint32_t handle, NetId hostId = 0, Vec3 pos = Vec3()) {
    Member m;
    m.netId = hostId; m.created = true; m.base = F(base); m.handle = handle; m.pos = pos;
    return m;
}

inline RollReport mkReport(const SiteDef& d, PlayerId roller, OwnerEpoch epoch, uint32_t rollId, std::vector<Member> ms, uint32_t pick = 0) {
    RollReport r;
    r.def = d; r.roller = roller; r.epoch = epoch; r.rollId = rollId; r.members = std::move(ms);
    if (pick) r.pick = F(pick);
    return r;
}

inline PlayerLoc loc(CellId cell, std::vector<CellId> adj = {}, Vec3 pos = Vec3()) {
    PlayerLoc l;
    l.present = true; l.cell = cell; l.adjacent = std::move(adj); l.pos = pos;
    l.adjacentKnown = true;       // the pose says exactly what is loaded: a roll's claim cannot add to it
    return l;
}

// a pose without adjacency data (what the avatar layer's pose sink gives): the roll's proof of a loaded cell counts
inline PlayerLoc locBlind(CellId cell) {
    PlayerLoc l;
    l.present = true; l.cell = cell;
    return l;
}

// a registry with a clock the tests move by hand
struct Reg {
    SiteRegistry r;
    SiteRegistry::Out out;
    uint64_t wall = 10000;
    explicit Reg(Config c = {}) : r(c) {}
    void join(PlayerId p) { r.playerJoined(p); }
    void tick(uint64_t dtMs = 0) { wall += dtMs; r.tick(wall, out); }
    // roll and return the (single) verdict for the sender
    VerdictMsg roll(const RollReport& rep, PlayerId from) {
        out.clear();
        r.onRoll(rep, from, wall, out);
        for (auto& v : out.verdicts) if (v.to == from) return v.msg;
        VerdictMsg none;
        none.verdict = Verdict::Count_;
        return none;
    }
    VerdictMsg roll(const RollReport& rep) { return roll(rep, rep.roller); }
};

inline const char* vn(Verdict v) { return toString(v); }

}  // namespace spt
