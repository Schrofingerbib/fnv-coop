// A fake game for the NPC animation layer: actors with a movement sequence, a one-shot slot, a weapon flag, a ragdoll that slides to rest, and
// switchable engine traps (commands ignored or refused, animation lost on menu close or 3D rebuild, a skeleton that starts in its bind pose).
// Game-free; time is advanced by the test (advance()).
#pragma once
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <map>
#include <string>
#include <vector>

#include "anim_groups.h"
#include "npc_anim_engine.h"

namespace anim_test {

using namespace nvcoop::actors::anim;

struct FakeNpc {
    bool exists = true, has3D = true, creature = false;
    uint32_t gen3D = 1;
    // animation state
    int moveGroup = NpcReads::kNoSequence;   // a fresh skeleton is in its bind pose until a group plays
    bool hold = false;
    uint32_t nonHoldEnds = 0;
    int actionGroup = -1;
    uint32_t actionStart = 0, actionEnd = 0;   // actionEnd 0 = until replaced (held actions)
    bool weaponOut = false, aiming = false, sneak = false, dead = false, knocked = false, ragdoll = false;
    bool inAir = false, swimming = false, talking = false;
    float rate = 1.0f;
    CellId cell = 1;
    Vec3 pos;
    float yaw = 0;
    // ragdoll
    Vec3 slideFrom, restPos;
    uint32_t slideStart = 0, slideMs = 1500;
    bool sliding = false;
    // traps
    int ignoreWeaponCalls = 0;     // the first n SetWeaponDrawn calls return Ok but do nothing
    int ignoreKillCalls = 0;
    bool refuseAll = false;
    bool hideMoveGroup = false;    // reads cannot tell the movement group
    bool rateSupported = false, knockSupported = false, weaponSupported = true, aimSupported = true;
    // counters
    int playsAfterDeath = 0, kills = 0, placeCalls = 0, releaseCalls = 0, knockCalls = 0, getUpCalls = 0;
    bool placedWhileSliding = false;   // PlaceCorpse was called while the ragdoll was still moving (it carries on to its own rest place)
    std::vector<std::string> log;   // "play:<group>:<hold>@<ms>", "weapon:1@<ms>", "sneak:1", "kill", "place", "release", "rate:<f>", "aim:1", "knock", "getup"
};

class FakeNpcEngine : public INpcAnimEngine {
public:
    std::map<NetId, FakeNpc> a;
    uint32_t now = 1000;
    uint32_t worldEpoch = 1;
    bool menuOpen = false, worldReady = true;
    bool resetOnMenuClose = true;
    uint64_t totalCommands = 0;

    FakeNpc& add(NetId id, Vec3 pos = {}) {
        FakeNpc& n = a[id];
        n.pos = pos;
        return n;
    }
    void advance(uint32_t dtMs) {
        now += dtMs;
        for (auto& kv : a) {
            FakeNpc& n = kv.second;
            if (n.nonHoldEnds && static_cast<int32_t>(now - n.nonHoldEnds) >= 0 && !n.hold) { n.moveGroup = kGroupIdle; n.nonHoldEnds = 0; }
            if (n.actionGroup >= 0 && n.actionEnd && static_cast<int32_t>(now - n.actionEnd) >= 0) n.actionGroup = -1;
            if (n.sliding) {
                const float t = std::min(1.0f, static_cast<float>(now - n.slideStart) / static_cast<float>(n.slideMs));
                n.pos.x = n.slideFrom.x + (n.restPos.x - n.slideFrom.x) * t;
                n.pos.y = n.slideFrom.y + (n.restPos.y - n.slideFrom.y) * t;
                n.pos.z = n.slideFrom.z + (n.restPos.z - n.slideFrom.z) * t;
                if (t >= 1.0f) { n.sliding = false; n.ragdoll = false; }
            }
        }
    }
    void closeMenu() {
        menuOpen = false;
        if (resetOnMenuClose)
            for (auto& kv : a) { kv.second.moveGroup = kGroupIdle; kv.second.hold = false; kv.second.weaponOut = kv.second.weaponOut; }
    }
    void rebuild3D(NetId id) { FakeNpc& n = a[id]; ++n.gen3D; n.moveGroup = NpcReads::kNoSequence; n.hold = false; n.actionGroup = -1; }
    void startDeath(NetId id, Vec3 rest, uint32_t slideMs = 1500) {
        FakeNpc& n = a[id];
        n.dead = true; n.ragdoll = true; n.sliding = true; n.slideFrom = n.pos; n.restPos = rest; n.slideStart = now; n.slideMs = slideMs;
        n.moveGroup = NpcReads::kNoSequence; n.actionGroup = -1;
    }
    int count(NetId id, const std::string& prefix) const {
        auto it = a.find(id);
        if (it == a.end()) return 0;
        int c = 0;
        for (auto& l : it->second.log) if (l.compare(0, prefix.size(), prefix) == 0) ++c;
        return c;
    }
    int plays(NetId id, int group) const { return count(id, "play:" + std::to_string(group) + ":"); }

    // ---- INpcAnimEngine ----
    bool WorldReady() override { return worldReady; }
    uint32_t WorldEpoch() override { return worldEpoch; }
    bool MenuOpen() override { return menuOpen; }
    bool Read(NetId id, NpcReads& out) override {
        auto it = a.find(id);
        if (it == a.end() || !it->second.exists) return false;
        const FakeNpc& n = it->second;
        out = NpcReads{};
        out.valid = true; out.has3D = n.has3D; out.dead = n.dead; out.creature = n.creature; out.knocked = n.knocked; out.ragdoll = n.ragdoll;
        out.swimming = n.swimming; out.sneaking = n.sneak; out.weaponOut = n.weaponOut; out.aiming = n.aiming; out.inAir = n.inAir; out.talking = n.talking;
        out.moveGroup = n.hideMoveGroup ? NpcReads::kUnknownGroup : n.moveGroup;
        if (n.actionGroup >= 0) { out.actionGroup = n.actionGroup; out.actionPlayMs = static_cast<int>(now - n.actionStart); }
        return true;
    }
    uint32_t Generation3D(NetId id) override { auto it = a.find(id); return it == a.end() ? 0 : it->second.gen3D; }
    bool GetPose(NetId id, CellId& cell, Vec3& pos, float& yawRad) override {
        auto it = a.find(id);
        if (it == a.end() || !it->second.exists) return false;
        cell = it->second.cell; pos = it->second.pos; yawRad = it->second.yaw;
        return true;
    }
    Result PlayGroup(NetId id, int g, bool holdFlag) override {
        ++totalCommands;
        FakeNpc* n = target(id);
        if (!n) return Result::Refused;
        n->log.push_back("play:" + std::to_string(g) + ":" + (holdFlag ? "1" : "0") + "@" + std::to_string(now));
        if (n->dead) { ++n->playsAfterDeath; return Result::Ok; }
        if (isActionGroup(g)) {
            n->actionGroup = g; n->actionStart = now;
            n->actionEnd = isHeldAction(g) ? 0 : now + nominalActionMs(g);
            return Result::Ok;
        }
        if (n->actionGroup >= 0 && n->actionEnd == 0) n->actionGroup = -1;   // a movement group ends a held action
        n->moveGroup = g; n->hold = holdFlag; n->nonHoldEnds = holdFlag ? 0 : now + 450;
        return Result::Ok;
    }
    Result ReleaseHold(NetId id) override {
        ++totalCommands;
        FakeNpc* n = target(id);
        if (!n) return Result::Refused;
        ++n->releaseCalls; n->log.push_back("release");
        n->hold = false; n->moveGroup = kGroupIdle;
        return Result::Ok;
    }
    Result SetGroupRate(NetId id, float r) override {
        FakeNpc* n = target(id);
        if (!n) return Result::Refused;
        if (!n->rateSupported) return Result::Unsupported;
        ++totalCommands;
        n->rate = r; n->log.push_back("rate:" + std::to_string(r));
        return Result::Ok;
    }
    Result SetSneak(NetId id, bool on) override {
        ++totalCommands;
        FakeNpc* n = target(id);
        if (!n) return Result::Refused;
        n->sneak = on; n->log.push_back(std::string("sneak:") + (on ? "1" : "0"));
        return Result::Ok;
    }
    Result SetWeaponDrawn(NetId id, bool on) override {
        FakeNpc* n = target(id);
        if (!n) return Result::Refused;
        if (!n->weaponSupported) return Result::Unsupported;
        ++totalCommands;
        n->log.push_back(std::string("weapon:") + (on ? "1" : "0") + "@" + std::to_string(now));
        if (n->ignoreWeaponCalls > 0) { --n->ignoreWeaponCalls; return Result::Ok; }
        n->weaponOut = on;
        return Result::Ok;
    }
    Result SetAiming(NetId id, bool on) override {
        FakeNpc* n = target(id);
        if (!n) return Result::Refused;
        if (!n->aimSupported) return Result::Unsupported;
        ++totalCommands;
        n->aiming = on; n->log.push_back(std::string("aim:") + (on ? "1" : "0"));
        return Result::Ok;
    }
    Result KnockDown(NetId id, float, float) override {
        FakeNpc* n = target(id);
        if (!n) return Result::Refused;
        if (!n->knockSupported) return Result::Unsupported;
        ++totalCommands;
        ++n->knockCalls; n->knocked = true; n->log.push_back("knock");
        return Result::Ok;
    }
    Result GetUp(NetId id) override {
        FakeNpc* n = target(id);
        if (!n) return Result::Refused;
        if (!n->knockSupported) return Result::Unsupported;
        ++totalCommands;
        ++n->getUpCalls; n->knocked = false; n->log.push_back("getup");
        return Result::Ok;
    }
    Result Kill(NetId id) override {
        ++totalCommands;
        FakeNpc* n = target(id);
        if (!n) return Result::Refused;
        ++n->kills; n->log.push_back("kill");
        if (n->ignoreKillCalls > 0) { --n->ignoreKillCalls; return Result::Ok; }
        startDeath(id, n->pos);
        return Result::Ok;
    }
    Result PlaceCorpse(NetId id, const Vec3& pos, float yawRad) override {
        ++totalCommands;
        FakeNpc* n = target(id);
        if (!n) return Result::Refused;
        ++n->placeCalls; n->log.push_back("place"); if (n->sliding) n->placedWhileSliding = true;
        n->pos = pos; n->yaw = yawRad;
        return Result::Ok;
    }

private:
    FakeNpc* target(NetId id) {
        auto it = a.find(id);
        if (it == a.end() || !it->second.exists || !it->second.has3D || it->second.refuseAll) return nullptr;
        return &it->second;
    }
};

}  // namespace anim_test
