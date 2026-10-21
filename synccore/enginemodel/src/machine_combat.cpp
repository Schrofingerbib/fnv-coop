// Machine: combat-core glue. The rules live in synccore/combat (game-free); this file is the model's side of them:
// the world view the rules ask questions of, projectiles that fly and land, explosions, the death ledger, kill XP.
#include <algorithm>
#include <cmath>
#include "fnvcoop/engine/machine.h"

namespace fnvcoop::engine {

namespace {
constexpr int32_t kKillXp = 50;   // what a kill is worth in the model
constexpr float kBlastRadius = 300;

combat::Vec3 cv(const Vec3& v) { return {v.x, v.y, v.z}; }
Vec3 ev3(const combat::Vec3& v) { return {v.x, v.y, v.z}; }

// The rules' view of this machine's world: who exists, where, who owns them.
class ModelView : public combat::ICombatWorld {
public:
    explicit ModelView(const Machine& m) : m_(m) {}
    bool facts(const combat::Entity& e, combat::ActorFacts& out) const override {
        out = combat::ActorFacts{};
        const World& w = m_.world();
        if (e.isPlayer()) {
            if (e.id >= kMaxSlots) return false;
            const PlayerState& p = w.player(static_cast<PlayerId>(e.id));
            if (!p.present && e.id != m_.self()) return false;
            out.known = true; out.who = e; out.alive = !p.dead; out.cell = p.cell; out.pos = cv(p.pos);
            out.owner = static_cast<PlayerId>(e.id); out.epoch = 0;
            out.health = p.health; out.maxHealth = p.maxHealth;
            return true;
        }
        if (e.isActor()) {
            auto it = w.actors().find(e.id);
            if (it == w.actors().end()) return false;
            const ActorState& a = it->second;
            out.known = true; out.who = e; out.alive = !a.dead; out.named = a.named; out.cell = a.cell; out.pos = cv(a.pos);
            out.owner = m_.view().ownerOf(a.id); out.epoch = m_.view().epochOf(a.id);
            out.handingOff = m_.handingOff(a.id);
            out.health = a.health; out.maxHealth = a.maxHealth; out.limbMask = a.limbMask; out.recruiter = a.recruiter;
            if (a.dead && engineFaults().deathNotIdempotent) { out.alive = true; out.owner = m_.self(); }   // the fault: nothing remembers that it died or that its owner let go
            return true;
        }
        return false;
    }
    bool cellsAdjacent(combat::CellId a, combat::CellId b) const override { return m_.world().adjacent(a, b); }
    bool projectile(uint64_t id, combat::Entity& shooter) const override { return m_.projectiles().shooterOf(id, shooter); }
private:
    const Machine& m_;
};
}  // namespace

// ---- setup ------------------------------------------------------------------------------------

void Machine::initCombat() {
    rules_ = combat::defaultRules();
    rules_.maxWeaponRange = cfg_.maxWeaponRange;
    rules_.rangeSlack = cfg_.rangeSlack;
    rules_.impactSlack = cfg_.impactSlack;
    rules_.maxDamage = cfg_.maxDamage;
    rules_.maxHitAgeMs = cfg_.maxHitAgeMs;
    rules_.maxHitsPerSec = cfg_.maxHitsPerSec;
    rules_.requireTick = false;  // every hit the model produces is stamped with the host time; an unstamped one (a hand-made forgery) is judged on its other merits
    hits_ = combat::HitValidator(rules_);
    explosions_ = combat::ExplosionLedger(rules_);
    projectiles_ = combat::ProjectileRegistry(rules_);
    cview_ = std::make_unique<ModelView>(*this);
}

void Machine::resetCombat() {
    hits_.reset();
    deaths_.reset();
    projectiles_.reset();
    explosions_.reset();
    xpLedger_.reset();
    xpPaidFor_.clear();
    shots_.clear();
}

combat::HitEvent Machine::toHitEvent(const MHit& h) const {
    combat::HitEvent e;
    e.hitId = h.hitId;
    e.attacker = combat::Entity{h.attackerIsPlayer ? combat::EntityKind::Player : combat::EntityKind::Actor, h.attacker};
    e.victim = combat::Entity{h.victimIsPlayer ? combat::EntityKind::Player : combat::EntityKind::Actor, h.victim};
    e.damage = h.damage;
    e.limb = h.limb;
    e.impactPoint = cv(h.impact);
    e.projectile = h.projectile;
    e.explosionId = h.explosionId;
    e.firedAtTick = h.firedAt;
    e.attackerEpoch = h.attackerEpoch;
    e.claimedRange = h.weaponRange;
    return e;
}

MHit Machine::fromHitEvent(const combat::HitEvent& e) const {
    MHit h;
    h.hitId = e.hitId;
    h.attackerIsPlayer = e.attacker.isPlayer(); h.attacker = e.attacker.id;
    h.victimIsPlayer = e.victim.isPlayer(); h.victim = e.victim.id;
    h.damage = e.damage; h.limb = e.limb; h.impact = ev3(e.impactPoint); h.weaponRange = e.claimedRange;
    h.firedAt = e.firedAtTick; h.attackerEpoch = e.attackerEpoch; h.projectile = e.projectile; h.explosionId = e.explosionId;
    return h;
}

// ---- projectiles ------------------------------------------------------------------------------

bool Machine::fireProjectile(NetId victim, int32_t damage, uint32_t flightMs, bool explosive) {
    Action i; i.kind = ActionKind::Projectile; i.a = victim; i.n = damage; i.f = static_cast<float>(flightMs); i.flag = explosive;
    return doAction(i);
}

bool Machine::detonate(Vec3 center, float radius, int32_t damage) {
    Action i; i.kind = ActionKind::Explode; i.pos = center; i.f = radius; i.n = damage;
    return doAction(i);
}

bool Machine::doProjectile(const Action& i) {
    PlayerState& me = world_.player(cfg_.self);
    ActorState* v = world_.findActor(i.a);
    if (!v || v->dead || me.dead) return false;
    const uint32_t flight = std::max<uint32_t>(300, static_cast<uint32_t>(i.f));
    combat::ProjectileSpawn p;
    p.id = nextId();
    p.shooter = combat::Entity::player(cfg_.self);
    p.cell = me.cell;
    p.origin = cv(me.pos);
    const float dx = v->pos.x - me.pos.x, dy = v->pos.y - me.pos.y, dz = v->pos.z - me.pos.z;
    const float secs = static_cast<float>(flight) / 1000.0f;
    p.velocity = {dx / secs, dy / secs, dz / secs};
    p.firedAtTick = static_cast<uint32_t>(now_);
    p.lifetimeMs = flight + 2000;
    p.explosive = i.flag;
    const combat::ProjectileVerdict pv = projectiles_.onSpawn(p, cfg_.self, cfg_.self, now_, *cview_);
    if (!pv.accept) return false;
    ++stats_.shotsFired;
    MProjectile m;
    m.p = p; m.aim = v->id; m.damage = i.n; m.flightMs = flight;
    sendBody(m, kNoPlayer);
    Shot s;
    s.proj = p.id; s.victim = v->id; s.damage = i.n; s.explosive = i.flag; s.dueMs = now_ + flight; s.visual = false;
    shots_.push_back(s);
    return true;
}

bool Machine::onProjectile(const Envelope& e, const MProjectile& m) {
    const combat::ProjectileVerdict v = projectiles_.onSpawn(m.p, e.src, cfg_.self, now_, *cview_);
    if (!v.accept) {
        if (rejectLog_.size() < 64) rejectLog_.push_back(std::string("projectile from ") + std::to_string(e.src) + ": " + combat::toString(v.reason));
        return false;
    }
    ++stats_.shotsSeen;
    // Not ours: a copy for the eyes. It flies, lands, and is gone; it deals no damage on this machine (mayDealDamage(VisualOnly) == false).
    Shot s;
    s.proj = m.p.id; s.victim = m.aim; s.damage = m.damage; s.explosive = m.p.explosive; s.dueMs = now_ + m.flightMs;
    s.visual = !combat::mayDealDamage(v.role);
    if (s.visual) shots_.push_back(s);
    return true;
}

void Machine::stepShots() {
    for (size_t k = 0; k < shots_.size();) {
        if (now_ < shots_[k].dueMs) { ++k; continue; }
        const Shot s = shots_[k];
        shots_.erase(shots_.begin() + static_cast<std::ptrdiff_t>(k));
        projectiles_.remove(s.proj);
        ActorState* v = world_.findActor(s.victim);
        if (s.visual) {
            if (engineFaults().visualShotDamages && v && !v->dead) {
                // the fault: the copy that only shows the shot also applies its damage to our copy of the target
                if (view_.ownerOf(v->id) != cfg_.self) breach("visual_shot_damaged", "the visual copy of projectile " + std::to_string(s.proj) + " damaged actor " + std::to_string(v->id) + " on a machine that only shows it");
                v->health = std::max<int32_t>(1, v->health - s.damage);
            } else {
                ++stats_.visualShotsDealtNothing;
            }
            continue;
        }
        if (!v || v->dead || world_.player(cfg_.self).dead) continue;
        if (s.explosive) {
            combat::ExplosionEvent ev;
            ev.id = nextId();
            ev.source = combat::Entity::player(cfg_.self);
            ev.projectile = s.proj;
            ev.cell = v->cell; ev.pos = cv(v->pos); ev.radius = kBlastRadius; ev.damage = s.damage;
            ev.tick = ev.firedAtTick = static_cast<uint32_t>(now_);
            const combat::ExplosionVerdict ex = explosions_.judge(ev, cfg_.self, cfg_.self, *cview_);
            if (ex.accept && ex.authoritative) declareBlast(ev);
        } else {
            combat::HitEvent h;
            h.hitId = nextId();
            h.attacker = combat::Entity::player(cfg_.self);
            h.victim = combat::Entity::actor(v->id);
            h.damage = s.damage; h.limb = Limb::Torso; h.impactPoint = cv(v->pos); h.projectile = s.proj;
            h.firedAtTick = static_cast<uint32_t>(now_); h.claimedRange = 1500;
            PlayerId owner = view_.ownerOf(v->id);
            if (owner == kNoPlayer) owner = kHostPlayer;
            sendHit(fromHitEvent(h), owner);
        }
    }
}

// ---- explosions -------------------------------------------------------------------------------

bool Machine::doExplode(const Action& i) {
    PlayerState& me = world_.player(cfg_.self);
    if (me.dead) return false;
    combat::ExplosionEvent ev;
    ev.id = nextId();
    ev.source = combat::Entity::player(cfg_.self);
    ev.cell = me.cell; ev.pos = cv(i.pos); ev.radius = i.f; ev.damage = i.n;
    ev.tick = ev.firedAtTick = static_cast<uint32_t>(now_);
    const combat::ExplosionVerdict v = explosions_.judge(ev, cfg_.self, cfg_.self, *cview_);
    if (!v.accept || !v.authoritative) return false;
    declareBlast(ev);
    return true;
}

void Machine::declareBlast(const combat::ExplosionEvent& ev) {
    ++stats_.blastsDeclared;
    MExplosion m;
    m.e = ev;
    sendBody(m, kNoPlayer);
    if (engineFaults().blastRunsEverywhere) { runBlastLocally(ev); return; }   // the fault: no hits, every machine does its own damage
    std::vector<combat::BlastCandidate> cand;
    for (auto& kv : world_.actors()) {
        const ActorState& a = kv.second;
        if (a.dead || !a.enabled || a.cell != ev.cell) continue;
        cand.push_back({combat::Entity::actor(a.id), cv(a.pos), a.cell, 0});
    }
    for (uint8_t s = 0; s < kMaxSlots; ++s) {
        const PlayerState& p = world_.player(s);
        if (!p.present || p.dead || p.cell != ev.cell) continue;
        cand.push_back({combat::Entity::player(s), cv(p.pos), p.cell, 0});
    }
    for (const combat::HitEvent& h : combat::planExplosionHits(ev, cand, rules_)) {
        PlayerId owner = h.victim.isPlayer() ? static_cast<PlayerId>(h.victim.id) : view_.ownerOf(h.victim.id);
        if (owner == kNoPlayer) owner = kHostPlayer;
        ++stats_.blastHitsSent;
        sendHit(fromHitEvent(h), owner);
    }
}

bool Machine::onExplosion(const Envelope& e, const MExplosion& m) {
    const combat::ExplosionVerdict v = explosions_.judge(m.e, e.src, cfg_.self, *cview_);
    if (!v.accept) {
        if (rejectLog_.size() < 64) rejectLog_.push_back(std::string("explosion from ") + std::to_string(e.src) + ": " + combat::toString(v.reason));
        return false;
    }
    ++stats_.blastsSeen;   // the effect is shown; the damage arrives (if at all) as hits to the victims' owners
    if (engineFaults().blastRunsEverywhere) runBlastLocally(m.e);
    return true;
}

void Machine::runBlastLocally(const combat::ExplosionEvent& ev) {
    for (auto& kv : world_.actors()) {
        ActorState& a = kv.second;
        if (a.dead || !a.enabled || a.cell != ev.cell) continue;
        const float d = combat::distance(cv(a.pos), ev.pos);
        if (d > ev.radius) continue;
        const int32_t dmg = std::max<int32_t>(1, static_cast<int32_t>(std::lround(static_cast<float>(ev.damage) * (1.0f - d / ev.radius))));
        if (view_.ownerOf(a.id) != cfg_.self) breach("blast_applied_by_non_owner", "explosion " + std::to_string(ev.id) + " damaged actor " + std::to_string(a.id) + " owned by p" + std::to_string(view_.ownerOf(a.id)));
        a.health = std::max<int32_t>(1, a.health - dmg);
    }
}

// ---- deaths and kill XP -----------------------------------------------------------------------

void Machine::recordStoryDeath(NetId id, bool killed) {
    if (!id) return;
    if (!killed) { deaths_.resurrect(combat::Entity::actor(id)); xpPaidFor_.erase(id); return; }
    combat::DeathDecl d;
    d.deathId = nextId();
    d.who = combat::Entity::actor(id);
    d.source = combat::DeathSource::StoryHost;
    d.declarer = kHostPlayer;
    d.tick = static_cast<uint32_t>(now_);
    deaths_.markDead(d);   // already applied to the world by the effect: record it, do not judge it
}

void Machine::creditKill(const MActorEvent& ev) {
    combat::KillRecord k;
    k.deathId = ev.eventId;
    k.victim = combat::Entity::actor(ev.id);
    k.killer = combat::Entity{static_cast<combat::EntityKind>(ev.killerKind), ev.killerId};
    k.xp = kKillXp;
    if (k.killer.isActor()) {
        auto it = world_.actors().find(k.killer.id);
        if (it != world_.actors().end()) k.killerRecruiter = it->second.recruiter;
    }
    std::vector<PlayerId> present;
    for (uint8_t s = 0; s < kMaxSlots; ++s) if (world_.player(s).present) present.push_back(s);
    const std::vector<combat::XpAward> awards = xpLedger_.award(k, cfg_.xpMode, present, rules_);
    for (const combat::XpAward& a : awards) {
        if (a.slot != cfg_.self) continue;   // every machine earns for its own player only
        if (!xpPaidFor_.insert(ev.id).second) breach("kill_xp_twice", "actor " + std::to_string(ev.id) + " paid out experience a second time");
        xpEarned_ += a.xp;
        ++stats_.killsCredited;
    }
}

}  // namespace fnvcoop::engine
