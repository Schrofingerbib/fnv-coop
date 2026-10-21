// Limb crippling, damage application and the death ledger (mutation twins 323..335).
#include "fake_world.h"

using namespace combat_test;

TEST(limbs, accumulated_damage_cripples_and_the_torso_never_does) {   // twins 323, 324
    CombatRules r = defaultRules();
    LimbState s;
    CHECK(applyLimbDamage(s, Limb::LeftArm, 15, r) == 0);
    CHECK(!s.isCrippled(Limb::LeftArm));
    const uint8_t fresh = applyLimbDamage(s, Limb::LeftArm, 15, r);   // 30 in total >= 25
    CHECK(fresh == LimbState::bit(Limb::LeftArm));
    CHECK(s.isCrippled(Limb::LeftArm));
    CHECK(applyLimbDamage(s, Limb::LeftArm, 15, r) == 0);   // already crippled: not "newly"
    CHECK(s.mask() == LimbState::bit(Limb::LeftArm));
    // a head needs 30
    LimbState h;
    CHECK(applyLimbDamage(h, Limb::Head, 29, r) == 0);
    CHECK(applyLimbDamage(h, Limb::Head, 1, r) == LimbState::bit(Limb::Head));
    // torso: any amount, never crippled
    LimbState t;
    CHECK(applyLimbDamage(t, Limb::Torso, 500, r) == 0);
    CHECK(t.mask() == 0);
    // non-positive damage does nothing
    LimbState z;
    CHECK(applyLimbDamage(z, Limb::RightLeg, 0, r) == 0 && applyLimbDamage(z, Limb::RightLeg, -4, r) == 0);
    CHECK(z.damage[static_cast<int>(Limb::RightLeg)] == 0);
}

TEST(limbs, restore_heals_only_what_is_named) {
    CombatRules r = defaultRules();
    LimbState s;
    applyLimbDamage(s, Limb::LeftLeg, 40, r);
    applyLimbDamage(s, Limb::RightArm, 40, r);
    s.restore(LimbState::bit(Limb::LeftLeg));
    CHECK(!s.isCrippled(Limb::LeftLeg));
    CHECK(s.isCrippled(Limb::RightArm));
    CHECK(applyLimbDamage(s, Limb::LeftLeg, 10, r) == 0);   // the damage counter restarted
}

TEST(limbs, a_published_mask_only_adds_crippled_limbs) {   // twin 325
    const uint8_t arm = LimbState::bit(Limb::LeftArm), leg = LimbState::bit(Limb::RightLeg);
    CHECK(mergeLimbMask(arm, leg) == (arm | leg));
    CHECK(mergeLimbMask(arm | leg, arm) == (arm | leg));   // an older, smaller mask arriving late changes nothing
    CHECK(mergeLimbMask(0, 0) == 0);
    CHECK(mergeLimbMask(arm, arm) == arm);   // idempotent
}

TEST(limbs, damage_application) {   // twins 326, 327, 328
    CombatRules r = defaultRules();
    VictimVitals v;
    v.health = 100; v.maxHealth = 100;
    HitEvent h;
    h.damage = 30; h.limb = Limb::Head;
    DamageOutcome o = applyHit(v, h, r);
    CHECK(o.applied && !o.died && o.healthBefore == 100 && o.healthAfter == 70);
    CHECK(o.newlyCrippled == LimbState::bit(Limb::Head));
    // exactly lethal: health 0 means dead
    VictimVitals exact;
    exact.health = 30;
    HitEvent lethal; lethal.damage = 30;
    DamageOutcome e = applyHit(exact, lethal, r);
    CHECK(e.died && exact.dead && exact.health == 0);
    // overkill clamps at zero
    VictimVitals over;
    over.health = 10;
    lethal.damage = 400;
    CHECK(applyHit(over, lethal, r).died && over.health == 0);
    // a dead victim takes nothing more
    DamageOutcome again = applyHit(over, lethal, r);
    CHECK(!again.applied && !again.died && over.health == 0);
    CHECK(over.limbs.mask() == 0);
    // story npc: lethal damage leaves 1 health, no death
    VictimVitals named;
    named.health = 20; named.named = true;
    lethal.damage = 50;
    DamageOutcome n = applyHit(named, lethal, r);
    CHECK(n.applied && !n.died && n.protectedFromDeath && named.health == 1 && !named.dead);
    VictimVitals ess;
    ess.health = 5; ess.essential = true;
    CHECK(!applyHit(ess, lethal, r).died && ess.health == 1);
}

namespace {
DeathDecl declOwner(uint64_t id, Entity who, PlayerId declarer, OwnerEpoch epoch) {
    DeathDecl d;
    d.deathId = id; d.who = who; d.source = DeathSource::Owner; d.declarer = declarer; d.epoch = epoch;
    d.killer = Entity::player(1); d.cell = 1; d.where = {1000, 0, 0};
    return d;
}
}  // namespace

TEST(death, only_the_owner_declares_and_only_once) {   // twins 329, 331, 332
    FakeWorld w = standardWorld();
    DeathLedger l;
    // actor 100 is owned by p0 at epoch 3
    DeathVerdict wrongSender = l.judge(declOwner(1, Entity::actor(100), 1, 3), 1, false, w);
    CHECK(!wrongSender.accept && wrongSender.reason == DeathReason::NotOwner);
    DeathVerdict stale = l.judge(declOwner(2, Entity::actor(100), 0, 2), 0, true, w);
    CHECK(!stale.accept && stale.reason == DeathReason::StaleEpoch);
    DeathVerdict ahead = l.judge(declOwner(3, Entity::actor(100), 0, 4), 0, true, w);
    CHECK(!ahead.accept && ahead.reason == DeathReason::FutureEpoch && ahead.resync);
    CHECK(!l.isDead(Entity::actor(100)));
    DeathVerdict ok = l.judge(declOwner(4, Entity::actor(100), 0, 3), 0, true, w);
    CHECK(ok.accept && ok.reason == DeathReason::Ok);
    CHECK(l.isDead(Entity::actor(100)));
    CHECK(l.record(Entity::actor(100))->where == (Vec3{1000, 0, 0}));
    // a second declaration (another id, same actor) is not a second death
    DeathVerdict dup = l.judge(declOwner(5, Entity::actor(100), 0, 3), 0, true, w);
    CHECK(!dup.accept && dup.reason == DeathReason::AlreadyDead);
    CHECK(l.deaths() == 1);
    DeathVerdict unknown = l.judge(declOwner(6, Entity::actor(555), 0, 1), 0, true, w);
    CHECK(!unknown.accept && unknown.reason == DeathReason::UnknownActor);
}

TEST(death, simultaneous_kills_by_two_declarers_resolve_to_one_death) {   // twins 329, 331
    FakeWorld w = standardWorld();
    DeathLedger l;
    // the owner and (a buggy or forged) second machine both declare in the same tick; whichever order they arrive, exactly one counts
    DeathVerdict a = l.judge(declOwner(10, Entity::actor(101), 1, 5), 1, false, w);
    DeathVerdict b = l.judge(declOwner(11, Entity::actor(101), 0, 5), 0, true, w);
    CHECK(a.accept);
    CHECK(!b.accept);
    DeathLedger l2;
    DeathVerdict c = l2.judge(declOwner(11, Entity::actor(101), 0, 5), 0, true, w);
    DeathVerdict d = l2.judge(declOwner(10, Entity::actor(101), 1, 5), 1, false, w);
    CHECK(!c.accept && d.accept);   // only the real owner's declaration is accepted, in any order
}

TEST(death, named_npcs_die_only_through_the_story_host) {   // twins 330, 334
    FakeWorld w = standardWorld();
    DeathLedger l;
    DeathVerdict byOwner = l.judge(declOwner(1, Entity::actor(102), 0, 2), 0, true, w);
    CHECK(!byOwner.accept && byOwner.reason == DeathReason::NamedNeedsStory);
    CHECK(!l.isDead(Entity::actor(102)));
    DeathDecl story = declOwner(2, Entity::actor(102), 1, 2);
    story.source = DeathSource::StoryHost;
    DeathVerdict fromGuest = l.judge(story, 1, false, w);   // a guest pretending to be the story
    CHECK(!fromGuest.accept && fromGuest.reason == DeathReason::StoryNeedsHost);
    story.declarer = 0; story.deathId = 3;
    DeathVerdict ok = l.judge(story, 0, true, w);
    CHECK(ok.accept);
    CHECK(l.isDead(Entity::actor(102)));
    // the story may also kill something this machine has not loaded
    DeathDecl far = declOwner(4, Entity::actor(7777), 0, 0);
    far.source = DeathSource::StoryHost;
    CHECK(l.judge(far, 0, true, w).accept);
}

TEST(death, a_replayed_declaration_cannot_kill_a_resurrected_actor) {   // twin 333
    FakeWorld w = standardWorld();
    DeathLedger l;
    DeathDecl d = declOwner(42, Entity::actor(100), 0, 3);
    CHECK(l.judge(d, 0, true, w).accept);
    l.resurrect(Entity::actor(100));   // scripted resurrection
    CHECK(!l.isDead(Entity::actor(100)));
    DeathVerdict replay = l.judge(d, 0, true, w);   // the old message arrives again (duplicate, stale echo)
    CHECK(!replay.accept && replay.reason == DeathReason::ReplayedDeclaration);
    CHECK(!l.isDead(Entity::actor(100)));
    // a new declaration with a new id can kill it again
    CHECK(l.judge(declOwner(43, Entity::actor(100), 0, 3), 0, true, w).accept);
}

TEST(death, players_declare_their_own_death) {   // twin 335
    FakeWorld w = standardWorld();
    DeathLedger l;
    DeathDecl d;
    d.deathId = 1; d.who = Entity::player(1); d.source = DeathSource::SelfPlayer; d.declarer = 1;
    DeathVerdict byOther = l.judge(d, 0, true, w);   // p0 declares p1 dead
    CHECK(!byOther.accept && byOther.reason == DeathReason::SelfOnly);
    CHECK(l.judge(d, 1, false, w).accept);
    CHECK(l.isDead(Entity::player(1)));
    l.resurrect(Entity::player(1));   // respawn: a new life
    DeathDecl again = d;
    again.deathId = 2;
    CHECK(l.judge(again, 1, false, w).accept);
}

TEST(death, a_corpse_the_ledger_never_heard_of_cannot_die_again) {   // twin 363
    FakeWorld w = standardWorld();
    w.f[Entity::actor(100)].alive = false;   // a world loaded with a corpse in it, or a late joiner
    DeathLedger l;
    DeathVerdict v = l.judge(declOwner(1, Entity::actor(100), 0, 3), 0, true, w);
    CHECK(!v.accept && v.reason == DeathReason::AlreadyDead);
    CHECK(!l.isDead(Entity::actor(100)));   // judged, not recorded: it was never declared here
    // markDead records a death that is already a fact; later declarations and replays of that id are refused
    DeathDecl d = declOwner(2, Entity::actor(101), 1, 5);
    l.markDead(d);
    CHECK(l.isDead(Entity::actor(101)));
    CHECK(l.judge(declOwner(3, Entity::actor(101), 1, 5), 1, false, w).reason == DeathReason::AlreadyDead);
    l.resurrect(Entity::actor(101));
    CHECK(l.judge(d, 1, false, w).reason == DeathReason::ReplayedDeclaration);
}

TEST(death, ledger_memory_is_bounded) {
    FakeWorld w = standardWorld();
    DeathLedger l(3);
    for (uint64_t i = 1; i <= 8; ++i) {
        DeathDecl d;
        d.deathId = i; d.who = Entity::actor(static_cast<uint32_t>(1000 + i)); d.source = DeathSource::StoryHost; d.declarer = 0;
        CHECK(l.judge(d, 0, true, w).accept);
    }
    CHECK(l.deaths() == 8);   // the deaths themselves are kept (a corpse stays dead); only the replay ids are bounded
}
