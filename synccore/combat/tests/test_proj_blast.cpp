// Projectiles (visual-only copies) and explosions (damage through each victim's owner): mutation twins 336..350.
#include "fake_world.h"

using namespace combat_test;

namespace {
ProjectileSpawn shot(uint64_t counter, Entity shooter = Entity::player(1), OwnerEpoch epoch = 0) {
    ProjectileSpawn p;
    p.id = (static_cast<uint64_t>(shooter.isPlayer() ? shooter.id : 1) << 48) | counter;
    p.shooter = shooter; p.shooterEpoch = epoch;
    p.weapon = rifle(); p.ammo = FormRef("falloutnv.esm", 0x2000); p.projectileBase = FormRef("falloutnv.esm", 0x3000);
    p.cell = 1; p.origin = {500, 0, 0}; p.velocity = {3000, 0, 0};
    p.firedAtTick = 10000; p.lifetimeMs = 5000;
    return p;
}
}  // namespace

TEST(projectile, the_shooters_machine_runs_the_real_one_everyone_else_a_copy) {   // twin 337
    FakeWorld w = standardWorld();
    ProjectileRegistry onShooter, onOther;
    ProjectileVerdict real = onShooter.onSpawn(shot(1), 1, 1, kNow, w);   // p1 fires on machine 1
    CHECK(real.accept && real.role == ProjectileRole::Authoritative && mayDealDamage(real.role));
    ProjectileVerdict copy = onOther.onSpawn(shot(1), 1, 0, kNow, w);     // machine 0 receives it from p1
    CHECK(copy.accept);
    CHECK(copy.role == ProjectileRole::VisualOnly);
    CHECK(!mayDealDamage(copy.role));
    CHECK(onOther.live(shot(1).id) && onOther.roleOf(shot(1).id) == ProjectileRole::VisualOnly);
    Entity who;
    CHECK(onOther.shooterOf(shot(1).id, who) && who == Entity::player(1));
    // an npc shot fired by its owner
    ProjectileRegistry onOwner;
    ProjectileSpawn npcShot = shot(2, Entity::actor(100), 3);
    npcShot.origin = {1000, 0, 0};
    CHECK(onOwner.onSpawn(npcShot, 0, 0, kNow, w).role == ProjectileRole::Authoritative);   // raider 100 is owned by machine 0
}

TEST(projectile, only_the_shooters_owner_may_spawn_it) {   // twin 336
    FakeWorld w = standardWorld();
    ProjectileRegistry r;
    ProjectileVerdict forged = r.onSpawn(shot(1), 0, 0, kNow, w);   // machine 0 spawns a projectile of player 1
    CHECK(!forged.accept && forged.reason == ProjectileReason::NotShooterOwner);
    CHECK(!r.live(shot(1).id));
    ProjectileSpawn ghost = shot(2, Entity::actor(9999));
    CHECK(r.onSpawn(ghost, 1, 0, kNow, w).reason == ProjectileReason::UnknownShooter);
}

TEST(projectile, spawn_is_idempotent) {   // twin 338
    FakeWorld w = standardWorld();
    ProjectileRegistry r;
    CHECK(r.onSpawn(shot(1), 1, 0, kNow, w).accept);
    ProjectileVerdict again = r.onSpawn(shot(1), 1, 0, kNow, w);
    CHECK(!again.accept && again.reason == ProjectileReason::Duplicate);
    CHECK(r.liveCount() == 1);
}

TEST(projectile, epochs) {   // twin 341
    FakeWorld w = standardWorld();
    ProjectileRegistry r;
    // raider 101 is owned by p1 at epoch 5
    CHECK(r.onSpawn(shot(1, Entity::actor(101), 4), 1, 0, kNow, w).reason == ProjectileReason::StaleEpoch);
    ProjectileVerdict ahead = r.onSpawn(shot(2, Entity::actor(101), 6), 1, 0, kNow, w);
    CHECK(!ahead.accept && ahead.reason == ProjectileReason::FutureEpoch && ahead.resync);
    ProjectileSpawn ok = shot(3, Entity::actor(101), 5);
    ok.origin = {1500, 0, 0};
    CHECK(r.onSpawn(ok, 1, 0, kNow, w).accept);
}

TEST(projectile, speed_and_origin_are_bounded) {   // twins 339, 340
    FakeWorld w = standardWorld();
    ProjectileRegistry r;
    ProjectileSpawn fast = shot(1);
    fast.velocity = {1e6f, 0, 0};
    CHECK(r.onSpawn(fast, 1, 0, kNow, w).reason == ProjectileReason::SpeedOutOfBounds);
    ProjectileSpawn far = shot(2);
    far.origin = {9000, 0, 0};   // p1 is at 500
    CHECK(r.onSpawn(far, 1, 0, kNow, w).reason == ProjectileReason::OriginTooFar);
    CHECK(r.onSpawn(shot(3), 1, 0, kNow, w).accept);
}

TEST(projectile, rate_and_lifetime) {   // twin 342
    FakeWorld w = standardWorld();
    ProjectileRegistry r;
    int ok = 0;
    for (uint64_t i = 1; i <= 60; ++i) if (r.onSpawn(shot(i), 1, 0, kNow, w).accept) ++ok;
    CHECK_MSG(ok == 40, "accepted " + std::to_string(ok));
    CHECK(r.onSpawn(shot(100), 1, 0, kNow + 1500, w).accept);   // the second is over
    ProjectileSpawn old = shot(200);
    old.firedAtTick = 1000;
    CHECK(r.onSpawn(old, 1, 0, kNow + 1500, w).reason == ProjectileReason::Expired);
    // expiry drops live projectiles
    r.expire(kNow + 100000);
    CHECK(r.liveCount() == 0);
}

// ---------------------------------------------------------------------------------------------- explosions
namespace {
ExplosionEvent blast(uint64_t counter, Entity source = Entity::player(1)) {
    ExplosionEvent e;
    e.id = (static_cast<uint64_t>(source.isPlayer() ? source.id : 1) << 48) | counter;
    e.source = source;
    e.weapon = rifle(); e.explosionBase = FormRef("falloutnv.esm", 0x4000);
    e.cell = 1; e.pos = {1000, 0, 0}; e.radius = 300; e.damage = 100; e.tick = 10000;
    return e;
}
}  // namespace

TEST(explosion, only_the_sources_owner_declares_and_it_plans_the_damage) {   // twins 343, 344
    FakeWorld w = standardWorld();
    ExplosionLedger onSource, onOther;
    ExplosionVerdict mine = onSource.judge(blast(1), 1, 1, w);
    CHECK(mine.accept && mine.authoritative);
    ExplosionVerdict seen = onOther.judge(blast(1), 1, 0, w);   // machine 0 hears about it: visual only
    CHECK(seen.accept);
    CHECK(!seen.authoritative);
    ExplosionLedger l;
    ExplosionVerdict forged = l.judge(blast(2), 0, 0, w);   // machine 0 declares player 1's blast
    CHECK(!forged.accept && forged.reason == ExplosionReason::NotSourceOwner);
    // a scripted blast (no source) belongs to the host
    ExplosionEvent scripted = blast(3, Entity());
    CHECK(l.judge(scripted, 0, 0, w).accept);
    scripted.id = 4;
    CHECK(!l.judge(scripted, 1, 1, w).accept);
}

TEST(explosion, declared_once_and_bounded) {   // twins 349, 350
    FakeWorld w = standardWorld();
    ExplosionLedger l;
    CHECK(l.judge(blast(1), 1, 0, w).accept);
    ExplosionVerdict dup = l.judge(blast(1), 1, 0, w);
    CHECK(!dup.accept && dup.reason == ExplosionReason::Duplicate);
    ExplosionEvent huge = blast(2);
    huge.radius = 100000;
    CHECK(l.judge(huge, 1, 0, w).reason == ExplosionReason::RadiusOutOfBounds);
    huge.radius = 0; huge.id = 3;
    CHECK(l.judge(huge, 1, 0, w).reason == ExplosionReason::RadiusOutOfBounds);
    ExplosionEvent strong = blast(4);
    strong.damage = 100000;
    CHECK(l.judge(strong, 1, 0, w).reason == ExplosionReason::DamageOutOfBounds);
    // npc source epochs
    ExplosionEvent npc = blast(5, Entity::actor(101));
    npc.sourceEpoch = 4;
    CHECK(l.judge(npc, 1, 0, w).reason == ExplosionReason::StaleEpoch);
    npc.id = 6; npc.sourceEpoch = 9;
    ExplosionVerdict ahead = l.judge(npc, 1, 0, w);
    CHECK(!ahead.accept && ahead.resync);
    npc.id = 7; npc.sourceEpoch = 5;
    CHECK(l.judge(npc, 1, 0, w).accept);
}

TEST(explosion, plan_hits_in_radius_with_falloff) {   // twins 345, 346, 348
    CombatRules r = defaultRules();
    ExplosionEvent e = blast(1);
    std::vector<BlastCandidate> c;
    c.push_back({Entity::actor(100), {1050, 0, 0}, 1, 0});     // 50 away
    c.push_back({Entity::actor(101), {1200, 0, 0}, 1, 0});     // 200 away
    c.push_back({Entity::player(0), {5000, 0, 0}, 1, 0});      // far outside
    c.push_back({Entity::actor(102), {1010, 0, 0}, 2, 0});     // in range but another cell
    c.push_back({Entity::actor(103), {1290, 0, 0}, 1, 0});     // just inside (290)
    c.push_back({Entity::actor(104), {1500, 0, 0}, 1, 250});   // outside by 200, but 250 units wide: its body reaches in
    std::vector<HitEvent> hits = planExplosionHits(e, c, r);
    CHECK_MSG(hits.size() == 4, "hits " + std::to_string(hits.size()));
    auto find = [&](Entity v) -> const HitEvent* { for (auto& h : hits) if (h.victim == v) return &h; return nullptr; };
    CHECK(find(Entity::actor(100)) && find(Entity::actor(101)) && find(Entity::actor(103)) && find(Entity::actor(104)));
    CHECK(!find(Entity::player(0)) && !find(Entity::actor(102)));
    if (find(Entity::actor(100)) && find(Entity::actor(101)) && find(Entity::actor(103))) {
        CHECK(find(Entity::actor(100))->damage == 83);   // 100 * (1 - 50/300)
        CHECK(find(Entity::actor(101))->damage == 33);   // 100 * (1 - 200/300)
        CHECK(find(Entity::actor(103))->damage >= 1 && find(Entity::actor(103))->damage < 10);   // the edge of the blast barely hurts
        CHECK(find(Entity::actor(100))->damage > find(Entity::actor(101))->damage);
        const HitEvent& h = *find(Entity::actor(100));
        CHECK(h.attacker == Entity::player(1) && h.explosionId == e.id && h.impactPoint == e.pos && h.claimedRange == e.radius && h.firedAtTick == 10000);
    }
    // no falloff when the rules say so
    r.explosionFalloff = 0;
    for (auto& h : planExplosionHits(e, c, r)) CHECK(h.damage == 100);
    // nothing to plan for an empty or harmless blast
    ExplosionEvent harmless = e;
    harmless.damage = 0;
    CHECK(planExplosionHits(harmless, c, defaultRules()).empty());
}

TEST(explosion, hit_ids_are_per_victim_and_stable) {   // twin 347
    CombatRules r = defaultRules();
    ExplosionEvent e = blast(1);
    std::vector<BlastCandidate> c;
    c.push_back({Entity::actor(100), {1050, 0, 0}, 1, 0});
    c.push_back({Entity::actor(101), {1100, 0, 0}, 1, 0});
    c.push_back({Entity::player(0), {1000, 50, 0}, 1, 0});
    std::vector<HitEvent> a = planExplosionHits(e, c, r), b = planExplosionHits(e, c, r);
    REQUIRE(a.size() == 3 && b.size() == 3);
    std::set<uint64_t> ids;
    for (size_t i = 0; i < a.size(); ++i) {
        CHECK(a[i].hitId == b[i].hitId);   // planning twice gives the same ids
        ids.insert(a[i].hitId);
    }
    CHECK(ids.size() == 3);   // one id per victim, so no victim's hit is mistaken for another's replay
    // an actor and a player with the same number are different victims
    CHECK(explosionHitId(1, Entity::actor(0)) != explosionHitId(1, Entity::player(0)));
    CHECK(explosionHitId(1, Entity::actor(5)) != explosionHitId(2, Entity::actor(5)));
}

TEST(explosion, damage_goes_through_each_victims_owner) {
    FakeWorld w = standardWorld();
    w.f[Entity::actor(100)].pos = {1050, 0, 0};   // owned by p0
    w.f[Entity::actor(101)].pos = {1200, 0, 0};   // owned by p1
    ExplosionEvent e = blast(1);                  // thrown by player 1
    e.damage = 70;                                // the rifle's own cap is 80
    ExplosionLedger ledger;
    ExplosionVerdict v = ledger.judge(e, 1, 1, w);   // judged on machine 1, the source's owner
    REQUIRE(v.accept && v.authoritative);
    std::vector<BlastCandidate> c;
    c.push_back({Entity::actor(100), w.f[Entity::actor(100)].pos, 1, 0});
    c.push_back({Entity::actor(101), w.f[Entity::actor(101)].pos, 1, 0});
    c.push_back({Entity::player(1), {500, 0, 0}, 1, 0});   // the thrower stands 500 away: outside the radius
    std::vector<HitEvent> hits = planExplosionHits(e, c, defaultRules());
    REQUIRE(hits.size() == 2);
    HitValidator atP0, atP1;
    int appliedAtP0 = 0, appliedAtP1 = 0, forwarded = 0;
    for (const HitEvent& h : hits) {
        // the thrower's machine (p1) delivers each hit to every machine; only the victim's owner applies it
        HitVerdict r0 = atP0.judge(h, 1, 0, kNow, w), r1 = atP1.judge(h, 1, 1, kNow, w);
        if (r0.apply) ++appliedAtP0;
        if (r1.apply) ++appliedAtP1;
        if (!r0.apply && r0.reason == HitReason::NotVictimOwner && r0.forwardTo == 1) ++forwarded;
        if (!r1.apply && r1.reason == HitReason::NotVictimOwner && r1.forwardTo == 0) ++forwarded;
    }
    CHECK(appliedAtP0 == 1 && appliedAtP1 == 1 && forwarded == 2);   // each hit applied exactly once, on the owner
    // a replay of the whole explosion (same ids) applies nothing new
    for (const HitEvent& h : planExplosionHits(e, c, defaultRules())) {
        CHECK(!atP0.judge(h, 1, 0, kNow, w).apply);
        CHECK(!atP1.judge(h, 1, 1, kNow, w).apply);
    }
}
