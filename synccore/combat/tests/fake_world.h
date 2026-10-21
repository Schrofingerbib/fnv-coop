// A tiny scripted world for the combat tests: players 0 and 1, a hostile raider (100, owned by p0, epoch 3), a raider owned by p1 (101, epoch 5),
// a named story npc (102, owned by p0). Cell 1 and cell 2 are adjacent, cell 9 is far away.
#pragma once
#include <map>
#include "combat.h"
#include "minitest.h"

namespace combat_test {
using namespace fnvcoop;
using namespace fnvcoop::combat;

struct FakeWorld : ICombatWorld {
    std::map<Entity, ActorFacts> f;
    std::map<FormRef, WeaponInfo> weapons;
    std::map<uint64_t, Entity> projectiles;
    bool los = true;

    bool facts(const Entity& e, ActorFacts& out) const override {
        auto it = f.find(e);
        if (it == f.end()) { out = ActorFacts{}; return false; }
        out = it->second;
        return true;
    }
    bool cellsAdjacent(CellId a, CellId b) const override { return a == b || (a < 9 && b < 9 && (a > b ? a - b : b - a) <= 1); }
    const WeaponInfo* weapon(const FormRef& id) const override { auto it = weapons.find(id); return it == weapons.end() ? nullptr : &it->second; }
    bool projectile(uint64_t id, Entity& shooter) const override { auto it = projectiles.find(id); if (it == projectiles.end()) return false; shooter = it->second; return true; }
    bool lineOfFire(const Vec3&, const Vec3&) const override { return los; }

    ActorFacts& add(Entity e, PlayerId owner, OwnerEpoch epoch, CellId cell, Vec3 pos, int32_t hp = 100) {
        ActorFacts a;
        a.known = true; a.who = e; a.alive = true; a.cell = cell; a.pos = pos; a.owner = owner; a.epoch = epoch; a.health = hp; a.maxHealth = hp;
        return f[e] = a;
    }
};

inline FormRef rifle() { return FormRef("falloutnv.esm", 0x1000); }
inline FormRef shotgun() { return FormRef("falloutnv.esm", 0x1001); }

inline FakeWorld standardWorld() {
    FakeWorld w;
    w.add(Entity::player(0), 0, 0, 1, {0, 0, 0});
    w.add(Entity::player(1), 1, 0, 1, {500, 0, 0});
    w.add(Entity::actor(100), 0, 3, 1, {1000, 0, 0});
    w.add(Entity::actor(101), 1, 5, 1, {1500, 0, 0});
    ActorFacts& n = w.add(Entity::actor(102), 0, 2, 1, {1200, 200, 0});
    n.named = true;
    WeaponInfo r; r.range = 3000; r.maxDamage = 80; r.minIntervalMs = 200;
    w.weapons[rifle()] = r;
    WeaponInfo s; s.range = 800; s.maxDamage = 30; s.minIntervalMs = 800; s.pellets = 8;
    w.weapons[shotgun()] = s;
    return w;
}

// player 1 shoots actor 100 (owned by p0): a hit that is fine in the standard world.
inline HitEvent goodHit(uint64_t id = 1) {
    HitEvent h;
    h.hitId = id;
    h.attacker = Entity::player(1);
    h.victim = Entity::actor(100);
    h.damage = 20;
    h.limb = Limb::Torso;
    h.impactPoint = {1000, 0, 0};
    h.weapon = rifle();
    h.firedAtTick = 10000;
    return h;
}

constexpr uint64_t kNow = 10050;

}  // namespace combat_test
