// Combat scenarios on the fake engine (combat-core, FULL-SYNC-PLAN 3.4): limbs, deaths, projectiles, explosions, kill XP, forged hits.
// Each is shared by a real test (test_combat.cpp) and a mutation twin (test_twins.cpp). A failed SETUP says "setup" in `why`, so a twin that is
// only "caught" because the scenario could not even start is not mistaken for a caught bug.
#include <algorithm>
#include <cmath>
#include <map>
#include "scenarios.h"

namespace fnvcoop::testing {

using namespace engine;

namespace {

void note(std::string* why, const std::string& s) { if (why && why->size() < 900) *why += s + "; "; }

struct Check {
    bool ok = true;
    std::string* why;
    explicit Check(std::string* w) : why(w) {}
    void expect(bool cond, const std::string& what) { if (!cond) { ok = false; note(why, what); } }
};

bool finish(Cluster& c, Check& k, bool allowRepairs = false) {
    const bool quiet = c.quiesce(30000);
    if (!allowRepairs && c.totalRepairs() > 0) {
        std::string log;
        const auto& rl = c.host().repairLog();
        for (size_t i = 0; i < rl.size() && i < 3; ++i) log += " [" + rl[i] + "]";
        k.expect(false, "digest repairs were needed (" + std::to_string(c.totalRepairs()) + "): something diverged and was patched:" + log);
    }
    c.checkAlways();
    c.checkQuiescent();
    k.expect(quiet, "cluster never became quiet");
    for (auto& v : c.allViolations()) { k.ok = false; note(k.why, v.what); }
    return k.ok;
}

Vec3 beside(const Vec3& p, float dx = 100) { Vec3 v = p; v.x += dx; return v; }

// A living actor in an exterior cell. hostile / named select the kind; cell 0 = any cell.
const ActorState* pickActor(const World& w, bool hostile, bool named, CellId cell = 0) {
    for (auto& kv : w.actors()) {
        const ActorState& a = kv.second;
        if (a.dead || !a.enabled || a.hostile != hostile || a.named != named) continue;
        if (cell && a.cell != cell) continue;
        auto it = w.cells().find(a.cell);
        if (it == w.cells().end() || it->second.interior) continue;
        return &a;
    }
    return nullptr;
}

// Both players walk to the actor's cell and stand next to it; the cluster runs until the poses have spread.
bool gather(Cluster& c, const ActorState& a, std::string* why) {
    const CellId cell = a.cell;
    if (!c.host().world().cellKnown(cell)) { note(why, "setup: unknown cell"); return false; }
    c.machine(0).move(cell, c.host().world().cellCenter(cell), 0);
    for (int p = 1; p < c.players(); ++p) c.machine(static_cast<PlayerId>(p)).move(cell, beside(a.pos, 60.0f + 40.0f * static_cast<float>(p)), 0);
    c.run(1500);
    return true;
}

// Strikes `id` from machine `m`: stand beside it (where it is on this machine), let the pose travel, hit.
bool strike(Cluster& c, PlayerId m, NetId id, int32_t damage, Limb limb, uint32_t settleMs = 150) {
    Machine& mc = c.machine(m);
    const ActorState* a = mc.world().findActor(id);
    if (!a || a->dead) return false;
    if (mc.world().player(m).dead) { Action r; r.kind = ActionKind::Revive; mc.doAction(r); }
    mc.move(a->cell, beside(a->pos, 90), 0);
    c.run(settleMs);
    a = mc.world().findActor(id);
    if (!a || a->dead) return false;
    mc.attackActor(id, damage, limb, a->pos, 1500);
    c.run(120);
    return true;
}

uint64_t sum(Cluster& c, uint64_t MachineStats::*f) {
    uint64_t n = 0;
    for (int p = 0; p < c.players(); ++p) if (c.connected(static_cast<PlayerId>(p))) n += c.machine(static_cast<PlayerId>(p)).stats().*f;
    return n;
}

bool sameOnEveryMachine(Cluster& c, NetId id, Check& k, const char* what) {
    const ActorState* h = c.host().world().findActor(id);
    if (!h) { k.expect(false, std::string(what) + ": actor missing on the host"); return false; }
    for (int p = 1; p < c.players(); ++p) {
        const ActorState* o = c.machine(static_cast<PlayerId>(p)).world().findActor(id);
        if (!o) { k.expect(false, std::string(what) + ": actor missing on machine " + std::to_string(p)); continue; }
        k.expect(o->health == h->health && o->dead == h->dead && o->limbMask == h->limbMask, std::string(what) + ": machine " + std::to_string(p) + " disagrees with the host (health " +
                 std::to_string(o->health) + " vs " + std::to_string(h->health) + ", limbs " + std::to_string(o->limbMask) + " vs " + std::to_string(h->limbMask) + ")");
    }
    return k.ok;
}

// A death event as the owner would send it, injected by hand.
void injectDeath(Cluster& c, PlayerId to, PlayerId owner, NetId id, uint64_t eventId, const LootList& loot) {
    MActorEvent ev;
    ev.id = id; ev.epoch = c.machine(to).view().epochOf(id); ev.eventId = eventId; ev.kind = ActorEventKind::Death; ev.dead = true; ev.health = 0;
    ev.loot = loot; ev.seq = 1u << 24; ev.cell = c.machine(to).world().findActor(id)->cell; ev.pos = c.machine(to).world().findActor(id)->pos;
    Envelope e;
    e.sess = c.machine(to).sess(); e.src = owner; e.dest = to; e.body = ev;
    c.inject(to, owner, encode(e));
}

const FormRef kLootA("falloutnv.esm", 0x200001u), kLootB("falloutnv.esm", 0x200003u);

}  // namespace

// ------------------------------------------------------------------------------------------------ limbs
// Crippling is accumulated limb damage; it survives a change of owner, and every machine ends up with the same crippled limbs.
bool limbsSync(uint64_t seed, std::string* why) {
    Check k(why);
    Cluster c(baseConfig(seed, 2));
    c.run(1000);
    const CellId near1 = c.host().world().cellAt(0, 0), far9 = c.host().world().cellAt(2, 2);
    const ActorState* a = pickActor(c.host().world(), true, false, near1);
    if (!a) a = pickActor(c.host().world(), false, false, near1);
    if (!a) { note(why, "setup: no actor in the start cell"); return false; }
    const NetId id = a->id;
    if (!gather(c, *a, why)) return false;
    for (int i = 0; i < 3; ++i) if (!strike(c, 1, id, 12, Limb::RightArm)) { note(why, "setup: the actor died early"); return false; }
    strike(c, 1, id, 10, Limb::Head);   // 10 of the 30 a head needs
    strike(c, 1, id, 10, Limb::Torso);  // the torso is never crippled
    c.run(600);
    const ActorState* h = c.host().world().findActor(id);
    k.expect(h && !h->dead, "setup: the actor should still be alive (health " + std::to_string(h ? h->health : -1) + ")");
    k.expect(h && (h->limbMask & combat::LimbState::bit(Limb::RightArm)), "the arm took 36 damage and is not crippled on the host");
    k.expect(h && !(h->limbMask & combat::LimbState::bit(Limb::Torso)), "the torso was crippled");
    k.expect(h && !(h->limbMask & combat::LimbState::bit(Limb::Head)), "the head was crippled by 10 damage");
    sameOnEveryMachine(c, id, k, "before the handoff");
    // the host walks to the far corner: the actor changes owner, and the new owner continues the counters
    c.machine(0).move(far9, c.host().world().cellCenter(far9), 0);
    c.run(4500);
    const PlayerId owner = c.machine(1).view().ownerOf(id);
    k.expect(owner == 1, "setup: the actor did not move to machine 1 (owner p" + std::to_string(owner) + ")");
    strike(c, 1, id, 25, Limb::Head);   // 10 + 25 = 35 >= 30 only if the new owner knew about the first 10
    strike(c, 1, id, 5, Limb::RightArm);
    c.run(800);
    h = c.host().world().findActor(id);
    k.expect(h && !h->dead, "setup: the actor died during the second round");
    k.expect(h && (h->limbMask & combat::LimbState::bit(Limb::Head)), "the head was not crippled after the handoff: the new owner lost the limb damage counters");
    k.expect(h && h->limbDmg[static_cast<int>(Limb::Head)] == 35 && h->limbDmg[static_cast<int>(Limb::RightArm)] == 41, "the limb damage counters are wrong on the host");
    sameOnEveryMachine(c, id, k, "after the handoff");
    return finish(c, k);
}

// ------------------------------------------------------------------------------------------------ death
// Two lethal hits at the same instant: the actor dies once, with one corpse and one set of loot, and the late hit is simply refused.
bool deathHappensOnce(uint64_t seed, std::string* why) {
    Check k(why);
    Cluster c(baseConfig(seed, 2));
    c.run(1000);
    const ActorState* a = pickActor(c.host().world(), true, false);
    if (!a) { note(why, "setup: no hostile actor"); return false; }
    const NetId id = a->id;
    if (!gather(c, *a, why)) return false;
    for (int p = 0; p < 2; ++p) {   // both players get next to it, poses spread
        Machine& mc = c.machine(static_cast<PlayerId>(p));
        const ActorState* cur = mc.world().findActor(id);
        mc.move(cur->cell, beside(cur->pos, 80.0f + 30.0f * static_cast<float>(p)), 0);
    }
    c.run(500);
    for (int p = 0; p < 2; ++p) {   // the same virtual instant
        Machine& mc = c.machine(static_cast<PlayerId>(p));
        const ActorState* cur = mc.world().findActor(id);
        mc.attackActor(id, 400, Limb::Torso, cur->pos, 1500);
    }
    c.run(1500);
    const ActorState* h = c.host().world().findActor(id);
    k.expect(h && h->dead, "setup: the actor survived two lethal hits");
    k.expect(sum(c, &MachineStats::deathsDeclared) == 1, "the actor was declared dead " + std::to_string(sum(c, &MachineStats::deathsDeclared)) + " times");
    k.expect(sum(c, &MachineStats::hitsApplied) >= 1, "setup: no hit was applied");
    const ContainerState* corpse = c.host().world().containers().count(id) ? &c.host().world().containers().at(id) : nullptr;
    k.expect(corpse && corpse->corpse, "no corpse on the host");
    sameOnEveryMachine(c, id, k, "after the double kill");
    return finish(c, k, true);
}

// The story host kills an actor while its owner kills it in combat: one death, everyone agrees.
bool storyKillRace(uint64_t seed, std::string* why) {
    Check k(why);
    Cluster c(baseConfig(seed, 2));
    c.run(1000);
    const ActorState* a = pickActor(c.host().world(), true, false);
    if (!a) { note(why, "setup: no hostile actor"); return false; }
    const NetId id = a->id;
    if (!gather(c, *a, why)) return false;
    const ActorState* cur = c.machine(1).world().findActor(id);
    c.machine(1).move(cur->cell, beside(cur->pos, 80), 0);
    c.run(500);
    cur = c.machine(1).world().findActor(id);
    c.machine(1).attackActor(id, 400, Limb::Torso, cur->pos, 1500);
    c.machine(0).killByStory(id);
    c.run(2500);
    for (int p = 0; p < 2; ++p) {
        const ActorState* o = c.machine(static_cast<PlayerId>(p)).world().findActor(id);
        k.expect(o && o->dead, "actor not dead on machine " + std::to_string(p));
    }
    k.expect(sum(c, &MachineStats::deathsDeclared) <= 1, "the actor was declared dead twice");
    sameOnEveryMachine(c, id, k, "after the race");
    return finish(c, k, true);
}

// ------------------------------------------------------------------------------------------------ projectiles and explosions
// A projectile is real only on its shooter's machine. The other machine shows a copy and must not hurt anyone with it.
bool projectileVisualOnly(uint64_t seed, std::string* why) {
    Check k(why);
    Cluster c(baseConfig(seed, 2));
    c.run(1000);
    const ActorState* a = pickActor(c.host().world(), true, false);
    if (!a) { note(why, "setup: no hostile actor"); return false; }
    const NetId id = a->id;
    if (!gather(c, *a, why)) return false;
    Machine& shooter = c.machine(1);
    const ActorState* cur = shooter.world().findActor(id);
    shooter.move(cur->cell, beside(cur->pos, 90), 0);
    c.run(400);
    const int32_t hp0 = c.host().world().findActor(id)->health;
    k.expect(shooter.fireProjectile(id, 30, 600, false), "setup: the projectile could not be fired");
    c.run(1600);
    k.expect(shooter.stats().shotsFired == 1, "shooter fired " + std::to_string(shooter.stats().shotsFired));
    k.expect(c.host().stats().shotsSeen >= 1, "the host never saw the projectile");
    k.expect(c.host().stats().visualShotsDealtNothing >= 1, "the host's copy of the projectile did not land as a visual");
    const ActorState* h = c.host().world().findActor(id);
    k.expect(h && h->health == hp0 - 30, "the actor lost " + std::to_string(h ? hp0 - h->health : -1) + " instead of 30 (the projectile hurt it more than once, or not at all)");
    // now an explosive one: one blast, one hit
    const int32_t hp1 = h ? h->health : 0;
    cur = shooter.world().findActor(id);
    if (cur && !cur->dead) shooter.move(cur->cell, beside(cur->pos, 90), 0);
    c.run(300);
    k.expect(shooter.fireProjectile(id, 40, 600, true), "setup: the explosive projectile could not be fired");
    c.run(1800);
    h = c.host().world().findActor(id);
    k.expect(shooter.stats().blastsDeclared == 1 && c.host().stats().blastsSeen >= 1, "the blast was not declared once and seen by the host");
    k.expect(h && h->health <= hp1 - 30, "the blast did not hurt the actor enough (health " + std::to_string(h ? h->health : -1) + " from " + std::to_string(hp1) + ")");
    sameOnEveryMachine(c, id, k, "after the shots");
    return finish(c, k);
}

// An explosion hurts every actor in range once, through each victim's own owner.
bool blastViaOwners(uint64_t seed, std::string* why) {
    Check k(why);
    Cluster c(baseConfig(seed, 3));
    c.run(1000);
    // a cell with at least two living actors
    CellId cell = 0;
    std::map<CellId, std::vector<NetId>> byCell;
    for (auto& kv : c.host().world().actors()) {
        const ActorState& a = kv.second;
        auto it = c.host().world().cells().find(a.cell);
        if (a.dead || !a.enabled || a.named || it == c.host().world().cells().end() || it->second.interior) continue;
        byCell[a.cell].push_back(a.id);
    }
    for (auto& kv : byCell) if (kv.second.size() >= 2) { cell = kv.first; break; }
    if (!cell) { note(why, "setup: no cell with two actors"); return false; }
    const std::vector<NetId> ids = byCell[cell];
    const Vec3 center = c.host().world().cellCenter(cell);
    // players split: the host in the cell, machine 2 next to it, machine 1 far away (so owners differ between actors)
    c.machine(0).move(cell, center, 0);
    c.machine(2).move(cell, beside(center, 200), 0);
    c.machine(1).move(cell, beside(center, 500), 0);
    c.run(3000);
    c.machine(0).setAiFrozen(true); c.machine(1).setAiFrozen(true); c.machine(2).setAiFrozen(true);   // nobody moves while the numbers are checked
    c.run(800);
    const ActorState* t = c.machine(1).world().findActor(ids[0]);
    if (!t || t->dead) { note(why, "setup: target gone"); return false; }
    const Vec3 at = t->pos;
    // who is in range, and how much each should lose
    struct Victim { NetId id; int32_t hp; float d; };
    std::vector<Victim> victims;
    for (NetId id : ids) {
        const ActorState* a = c.host().world().findActor(id);
        const float d = distance(a->pos, at);
        if (!a->dead && d <= 300.0f) victims.push_back({id, a->health, d});
    }
    k.expect(!victims.empty(), "setup: nobody in range");
    k.expect(c.machine(1).detonate(at, 300.0f, 60), "setup: the explosion could not be declared");
    c.run(2500);
    k.expect(c.machine(1).stats().blastsDeclared == 1 && c.host().stats().blastsSeen >= 1 && c.machine(2).stats().blastsSeen >= 1, "the blast was not declared once and seen by the others");
    for (const Victim& v : victims) {
        const ActorState* h = c.host().world().findActor(v.id);
        const int32_t want = std::max<int32_t>(1, static_cast<int32_t>(std::lround(60.0f * (1.0f - v.d / 300.0f))));
        k.expect(h && v.hp - h->health >= 1, "actor " + std::to_string(v.id) + " in range took no damage");
        k.expect(h && (v.hp - h->health == want || h->dead || std::abs((v.hp - h->health) - want) <= 3), "actor " + std::to_string(v.id) + " took " + std::to_string(h ? v.hp - h->health : -1) + " instead of about " + std::to_string(want));
        sameOnEveryMachine(c, v.id, k, "after the blast");
    }
    return finish(c, k);
}

// ------------------------------------------------------------------------------------------------ kill XP
bool killXpOnce(uint64_t seed, std::string* why) {
    Check k(why);
    for (int mode = 0; mode < 2; ++mode) {
        ClusterConfig cfg = baseConfig(seed, 2);
        cfg.machine.xpMode = mode == 0 ? combat::XpMode::KillingBlow : combat::XpMode::PartyShare;
        Cluster c(cfg);
        c.run(1000);
        const ActorState* a = pickActor(c.host().world(), true, false);
        if (!a) { note(why, "setup: no hostile actor"); return false; }
        const NetId id = a->id;
        if (!gather(c, *a, why)) return false;
        strike(c, 1, id, 400, Limb::Torso);
        c.run(1500);
        const ActorState* h = c.host().world().findActor(id);
        if (!h || !h->dead) { note(why, "setup: the actor survived"); return false; }
        const int32_t x0 = c.machine(0).xpEarned(), x1 = c.machine(1).xpEarned();
        if (mode == 0) k.expect(x0 == 0 && x1 == 50, "killing blow: xp host " + std::to_string(x0) + ", killer " + std::to_string(x1));
        else k.expect(x0 == 25 && x1 == 25, "party share: xp host " + std::to_string(x0) + ", killer " + std::to_string(x1));
        // the death arriving again (a second declaration) pays nothing
        injectDeath(c, 0, 1, id, 0xDEAD1001ull, {});
        injectDeath(c, 1, 0, id, 0xDEAD1002ull, {});
        c.run(600);
        k.expect(c.machine(0).xpEarned() == x0 && c.machine(1).xpEarned() == x1, "a second death declaration paid experience again");
        finish(c, k, true);
    }
    return k.ok;
}

// ------------------------------------------------------------------------------------------------ forged combat messages
bool forgedCombatRejected(uint64_t seed, std::string* why) {
    Check k(why);
    ClusterConfig cc = baseConfig(seed, 2);
    cc.machine.maxHitAgeMs = 3000;   // the production default: a hit stamped 20 s ago is stale
    Cluster c(cc);
    c.run(1500);
    const ActorState* a = pickActor(c.host().world(), true, false);
    if (!a) { note(why, "setup: no hostile actor"); return false; }
    const NetId id = a->id;
    if (!gather(c, *a, why)) return false;
    c.machine(0).setAiFrozen(true); c.machine(1).setAiFrozen(true);   // the raider stays put
    c.run(300);
    const PlayerId owner = c.host().view().ownerOf(id);
    if (owner != 0) { note(why, "setup: the host should own the raider (owner p" + std::to_string(owner) + ")"); return false; }
    const ActorState* cur = c.host().world().findActor(id);
    const int32_t hp = cur->health;
    const uint32_t nowMs = static_cast<uint32_t>(c.now());
    auto inject = [&](PlayerId to, PlayerId from, PlayerId src, const MHit& h) {
        Envelope e;
        e.sess = c.machine(to).sess(); e.src = src; e.dest = to; e.body = h;
        c.inject(to, from, encode(e));
    };
    MHit good;
    good.attackerIsPlayer = true; good.attacker = 1; good.victim = id; good.damage = 10; good.weaponRange = 1500; good.impact = cur->pos; good.firedAt = nowMs;
    // stale: stamped 20 seconds ago
    MHit stale = good; stale.hitId = 0xCC0001; stale.firedAt = nowMs > 20000 ? nowMs - 20000 : 1;
    // from the future
    MHit future = good; future.hitId = 0xCC0002; future.firedAt = nowMs + 30000;
    // wrong sender: claims to be player 0 but comes from 1
    MHit spoof = good; spoof.hitId = 0xCC0003; spoof.attacker = 0;
    // an npc attacker the sender does not own
    MHit npc = good; npc.hitId = 0xCC0004; npc.attackerIsPlayer = false; npc.attacker = id; npc.attackerEpoch = 1;
    // impact point on the other side of the map
    MHit far = good; far.hitId = 0xCC0005; far.impact = {900000, 0, 0};
    for (const MHit* h : {&stale, &future, &spoof, &npc, &far}) inject(owner, 1, 1, *h);
    c.run(500);
    k.expect(c.machine(owner).world().findActor(id)->health == hp, "a forged hit changed the actor");
    k.expect(c.machine(owner).stats().hitsRejected >= 5, "only " + std::to_string(c.machine(owner).stats().hitsRejected) + " of 5 forged hits were rejected on the owner");
    // player 1 claims the host's own raider hit player 0
    MHit npcOnHost;
    npcOnHost.hitId = 0xCC0006; npcOnHost.attackerIsPlayer = false; npcOnHost.attacker = id; npcOnHost.victimIsPlayer = true; npcOnHost.victim = 0;
    npcOnHost.damage = 10; npcOnHost.weaponRange = 150; npcOnHost.firedAt = nowMs; npcOnHost.attackerEpoch = c.host().view().epochOf(id);
    const int32_t php = c.host().world().player(0).health;
    inject(0, 1, 1, npcOnHost);
    c.run(300);
    k.expect(c.host().world().player(0).health == php, "player 0 took damage from a hit its raider never made");
    k.expect(c.host().stats().hitsRejected >= 6, "only " + std::to_string(c.host().stats().hitsRejected) + " of 6 forged hits were rejected");
    // the real thing still works
    strike(c, 1, id, 10, Limb::Torso);
    c.run(500);
    const ActorState* after = c.host().world().findActor(id);
    k.expect(after && (after->health == hp - 10 || after->dead), "the legitimate hit did not land (health " + std::to_string(after ? after->health : -1) + " from " + std::to_string(hp) + ")");
    return finish(c, k);
}

}  // namespace fnvcoop::testing
