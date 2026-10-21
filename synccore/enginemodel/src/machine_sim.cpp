#include <cstdio>
#include <cstdlib>
// Machine: NPC AI for owned actors, snapshots, combat (hits, damage, death), poses, vitals.
#include <algorithm>
#include <cmath>
#include "fnvcoop/engine/machine.h"

namespace fnvcoop::engine {

namespace {
constexpr uint64_t kTagHit = 1ull << 56, kTagEvent = 2ull << 56;
float clampf(float v, float lo, float hi) { return v < lo ? lo : (v > hi ? hi : v); }
}  // namespace

// ---- pose / vitals ---------------------------------------------------------------------------

void Machine::refreshAi(NetId id) {
    ActorState* a = world_.findActor(id);
    if (a) a->aiOn = drivesAi(*a);
}

bool Machine::drivesAi(const ActorState& a) const {
    return view_.ownerOf(a.id) == cfg_.self && !handingOff_.count(a.id) && !awaitOld_.count(a.id) && !a.dead && a.enabled;
}

void Machine::releaseAiHold(NetId id) {
    if (!awaitOld_.erase(id)) return;
    lastMoveMs_[id] = now_; settledAt_[id] = 0;   // our copy may be newer than what guests hold: announce it once it settles
    refreshAi(id);
}

void Machine::releaseHoldsFrom(PlayerId prev) {
    std::vector<NetId> ids;
    for (auto& kv : awaitOld_) if (kv.second.first == prev) ids.push_back(kv.first);
    for (NetId id : ids) releaseAiHold(id);
}

void Machine::breach(const std::string& name, const std::string& detail) {
    ++stats_.breaches;
    if (breaches_.size() < 32) breaches_.push_back(name + ": machine " + std::to_string(cfg_.self) + " at " + std::to_string(now_) + " ms: " + detail);
}

void Machine::refreshAiFlags() {
    for (auto& kv : world_.actors()) refreshAi(kv.first);
}

void Machine::publishPose(bool force) {
    if (!synced_) return;
    bool settle = false;
    if (!force && !poseDirty_) {   // stopped: one reliable copy of the final pose, so a lost datagram cannot leave others behind
        if (!poseUnsettled_ || now_ < lastPoseChangeMs_ + cfg_.settleMs) return;
        settle = true;
    } else if (!force && now_ < nextPoseMs_) {
        return;
    }
    nextPoseMs_ = now_ + cfg_.poseMs;
    if (!settle) { poseDirty_ = false; poseUnsettled_ = true; lastPoseChangeMs_ = now_; }
    else poseUnsettled_ = false;
    PlayerState& p = world_.player(cfg_.self);
    MPose m;
    m.settle = settle;
    m.slot = cfg_.self; m.present = true; m.cell = p.cell; m.pos = p.pos; m.yaw = p.yaw;
    m.seq = seqBase() + ++poseCounter_;
    p.poseSeq = m.seq;
    sendBody(m, kNoPlayer);
}

bool Machine::onPose(const Envelope& e, const MPose& m) {
    if (m.slot == cfg_.self || m.slot >= kMaxSlots) return false;
    if (isHost() && (e.src != m.slot || !links_.count(m.slot))) return false;
    PlayerState& p = world_.player(m.slot);
    if (m.seq <= p.poseSeq) return false;
    p.slot = m.slot; p.present = m.present; p.poseSeq = m.seq;
    if (!m.present) releaseHoldsFrom(m.slot);
    if (m.present) { p.cell = m.cell; p.pos = m.pos; p.yaw = m.yaw; }
    return true;
}

bool Machine::onVitals(const Envelope& e, const MVitals& m) {
    if (m.slot == cfg_.self || m.slot >= kMaxSlots) return false;
    if (isHost() && e.src != m.slot) return false;
    PlayerState& p = world_.player(m.slot);
    if (m.seq <= p.vitalsSeq) return false;
    p.vitalsSeq = m.seq; p.health = m.health; p.dead = m.dead;
    return true;
}

// ---- owner side: AI and snapshots ------------------------------------------------------------

void Machine::aiStep(ActorState& a, uint32_t dtMs) {
    lastOwnedEpoch_[a.id] = view_.epochOf(a.id);
    const float dts = static_cast<float>(dtMs) / 1000.0f;
    bool moved = false;
    auto cellIt = world_.cells().find(a.cell);
    auto clampToCell = [&](Vec3& p) {
        if (cellIt == world_.cells().end() || cellIt->second.interior) return;
        const float x0 = static_cast<float>(cellIt->second.gx) * kCellSize, y0 = static_cast<float>(cellIt->second.gy) * kCellSize;
        p.x = clampf(p.x, x0 + 50.0f, x0 + kCellSize - 50.0f);
        p.y = clampf(p.y, y0 + 50.0f, y0 + kCellSize - 50.0f);
    };
    auto moveToward = [&](const Vec3& target, float speed, float stopAt) {
        const float d = distance2d(a.pos, target);
        if (d <= stopAt || d < 1e-3f) return;
        const float step = std::min(d - stopAt, speed * dts);
        a.pos.x += (target.x - a.pos.x) / d * step;
        a.pos.y += (target.y - a.pos.y) / d * step;
        a.yaw = std::atan2(target.y - a.pos.y, target.x - a.pos.x);
        clampToCell(a.pos);
        moved = moved || step > 0;
    };

    if (aiFrozen_) {
        // no decisions
    } else if (a.recruiter != kNoPlayer && a.recruiter < kMaxSlots && world_.player(a.recruiter).present) {
        // follower: goes wherever the recruiter goes
        const PlayerState& r = world_.player(a.recruiter);
        if (r.cell != a.cell) { a.cell = r.cell; a.pos = r.pos; a.pos.x += 80; moved = true; cellIt = world_.cells().find(a.cell); }
        else moveToward(r.pos, cfg_.npcSpeed, 250.0f);
    } else if (a.hostile) {
        const PlayerState* tgt = nullptr;
        float best = cfg_.aggroRange;
        for (uint8_t s = 0; s < kMaxSlots; ++s) {
            const PlayerState& p = world_.player(s);
            if (!p.present || p.dead || p.cell != a.cell) continue;
            const float d = distance2d(p.pos, a.pos);
            if (d < best) { best = d; tgt = &p; }
        }
        if (tgt) {
            if (best > cfg_.meleeRange) moveToward(tgt->pos, cfg_.npcSpeed, cfg_.meleeRange * 0.8f);
            else if (now_ >= lastAttackMs_[a.id] + cfg_.npcAttackMs) {
                lastAttackMs_[a.id] = now_;
                MHit h;
                h.hitId = nextId(); h.attackerIsPlayer = false; h.attacker = a.id; h.victimIsPlayer = true; h.victim = tgt->slot;
                h.damage = cfg_.npcDamage; h.weaponRange = cfg_.meleeRange;   // (a melee swing has no impact point to check)
                h.firedAt = static_cast<uint32_t>(now_); h.attackerEpoch = view_.epochOf(a.id);
                sendHit(h, tgt->slot);
            }
        }
    }
    if (!moved && !aiFrozen_) {
        const bool engaged = a.hostile && lastAttackMs_.count(a.id) && now_ < lastAttackMs_[a.id] + 500;
        Wander& w = wander_[a.id];
        if (now_ >= w.nextMs) {
            w.nextMs = now_ + 1000 + rng_.range(0, 2000);
            if (rng_.chance(0.5)) { w.dx = w.dy = 0; }
            else { const float ang = static_cast<float>(rng_.uni()) * 6.2831853f; w.dx = std::cos(ang); w.dy = std::sin(ang); }
        }
        if ((w.dx != 0 || w.dy != 0) && dtMs > 0 && !engaged) {
            a.pos.x += w.dx * cfg_.wanderSpeed * dts;
            a.pos.y += w.dy * cfg_.wanderSpeed * dts;
            a.yaw = std::atan2(w.dy, w.dx);
            clampToCell(a.pos);
            moved = true;
        }
    }

    if (moved) {
        lastMoveMs_[a.id] = now_;
        bool near = false;
        for (uint8_t s = 0; s < kMaxSlots && !near; ++s) near = world_.player(s).present && world_.player(s).cell == a.cell;
        const uint32_t interval = near ? cfg_.snapshotMs : cfg_.farSnapshotMs;
        if (now_ >= lastPublishMs_[a.id] + interval) publishActor(a, false, false);
    } else if (lastMoveMs_.count(a.id) && now_ >= lastMoveMs_[a.id] + cfg_.settleMs && settledAt_[a.id] <= lastMoveMs_[a.id]) {
        settledAt_[a.id] = now_;
        publishActor(a, true, false);
    }
}

void Machine::publishActor(ActorState& a, bool settle, bool final) {
    OwnerEpoch epoch = 0;
    if (!view_.mayPublish(a.id, epoch)) return;
    lastPublishMs_[a.id] = now_;
    MActorSnap s;
    s.id = a.id; s.epoch = epoch; s.seq = ++a.snapSeq; s.cell = a.cell; s.pos = a.pos; s.yaw = a.yaw;
    s.health = a.health; s.dead = a.dead; s.settle = settle; s.final = final;
    sendBody(s, kNoPlayer);
}

bool Machine::onActorSnap(const Envelope& e, const MActorSnap& m) {
    ActorState* a = world_.findActor(m.id);
    if (!a) { ++stats_.snapshotsDropped; return false; }
    const UpdateVerdict v = view_.check(m.id, e.src, m.epoch);
    if (obs_) obs_->onActorUpdate(cfg_.self, now_, m.id, e.src, m.epoch, v);
    if (v != UpdateVerdict::Accept) {
        ++stats_.snapshotsDropped;
        if (v == UpdateVerdict::StaleEpoch && m.final) {   // the old owner's last word, after a forced commit: its AI has stopped, ours may start
            auto w = awaitOld_.find(m.id);
            if (w != awaitOld_.end() && w->second.first == e.src) releaseAiHold(m.id);
            else if (isHost() && !engineFaults().staleFinalNotForwarded) {
                // The new owner is another guest: only the host sees this message (the broadcast of a stale state is not relayed), and without
                // it the new owner would keep its AI off until the safety timeout. Pass it to the new owner alone; it releases only for the
                // previous owner it is waiting for, everybody else ignores a stale epoch as before.
                const PlayerId owner = view_.ownerOf(m.id);
                if (owner != cfg_.self && owner != e.src && owner != kNoPlayer && owner < kMaxSlots) {
                    Envelope fwd = e;
                    fwd.dest = owner;
                    relay(fwd);
                }
            }
        }
        if (rejectLog_.size() < 64) rejectLog_.push_back(std::string("snap ") + std::to_string(m.id) + " from " + std::to_string(e.src) + ": " + toString(v));
        if (v == UpdateVerdict::FutureEpoch && isHost()) {   // the sender is ahead of us or we are behind: tell it the truth
            OwnMsg cur;
            if (svc_->current(m.id, cur)) { ++stats_.resyncs; sendBody(MOwn{cur}, e.src); }
        }
        return false;
    }
    if (!m.final && m.seq <= a->snapSeq) { ++stats_.snapshotsDropped; return false; }
    a->cell = m.cell; a->pos = m.pos; a->yaw = m.yaw; a->health = m.health;
    a->snapSeq = std::max(a->snapSeq, m.seq);
    ++stats_.snapshotsApplied;
    if (m.final) {
        ++stats_.finalSnaps;
        if (isHost()) ackAfterRelay_.push_back(m.id);   // the host acks (and may commit) only after relaying this state onward
        else maybeAck(m.id);
    }
    return true;
}

// ---- loot and death --------------------------------------------------------------------------

LootList Machine::rollLoot(Rng& r) const {
    LootList l;
    const int n = static_cast<int>(r.range(0, 2));
    for (int i = 0; i < n; ++i) {
        FormRef f("falloutnv.esm", 0x200001u + r.range(0, 2));
        const int32_t c = static_cast<int32_t>(r.range(1, 3));
        bool merged = false;
        for (auto& e : l) if (e.first == f) { e.second += c; merged = true; }
        if (!merged) l.push_back({f, c});
    }
    return l;
}

void Machine::makeCorpse(ActorState& a, const LootList& loot) {
    ContainerState& c = world_.containers()[a.id];
    const PlayerId holder = c.inUseBy;
    c = ContainerState{};
    c.id = a.id; c.cell = a.cell; c.pos = a.pos; c.corpse = true; c.inUseBy = holder;
    for (auto& e : loot) c.items[e.first] += e.second;
}

void Machine::applyDamage(ActorState& a, const combat::HitEvent& h) {
    // Only the owner changes an actor's health (the caller has judged the hit); anything else is a bug the oracle must see.
    if (view_.ownerOf(a.id) != cfg_.self) breach("damage_applied_by_non_owner", "actor " + std::to_string(a.id) + " damaged on a machine that does not own it");
    combat::VictimVitals v;
    v.health = a.health; v.maxHealth = a.maxHealth;
    v.dead = a.dead && !engineFaults().deathNotIdempotent;   // (the fault: a corpse can be hit and die again)
    v.named = a.named && !engineFaults().namedActorKillable;  // essential: only the story may kill it
    v.limbs.crippled = a.limbMask;
    for (unsigned i = 0; i < combat::kLimbCount; ++i) v.limbs.damage[i] = a.limbDmg[i];
    const bool wasDead = a.dead;
    const combat::DamageOutcome o = combat::applyHit(v, h, rules_);
    if (!o.applied) return;
    a.health = v.health; a.limbMask = v.limbs.crippled;
    for (unsigned i = 0; i < combat::kLimbCount; ++i) a.limbDmg[i] = v.limbs.damage[i];
    if (o.newlyCrippled) ++stats_.limbsCrippled;
    const bool kill = o.died;
    if (kill && a.named) breach("story_npc_killed", "named actor " + std::to_string(a.id) + " was killed in combat");
    if (kill && wasDead) breach("death_declared_twice", "actor " + std::to_string(a.id) + " was declared dead a second time");
    OwnerEpoch epoch = 0;
    view_.mayPublish(a.id, epoch);
    MActorEvent ev;
    ev.id = a.id; ev.epoch = epoch; ev.eventId = nextId(); ev.health = a.health;
    ev.limbMask = engineFaults().limbNotSynced ? 0 : a.limbMask;   // (the fault: the crippled limbs stay a secret of the owner)
    for (unsigned i = 0; i < combat::kLimbCount; ++i) ev.limbDmg[i] = a.limbDmg[i];
    // What the owner publishes is what it knows; a digest repair that later patches the difference would hide the bug from every convergence check.
    if (ev.limbMask != a.limbMask) breach("limbs_not_published", "actor " + std::to_string(a.id) + " is crippled (mask " + std::to_string(a.limbMask) + ") but the event says " + std::to_string(ev.limbMask));
    ev.seq = ++a.snapSeq; ev.cell = a.cell; ev.pos = a.pos; ev.yaw = a.yaw;
    ev.killerKind = static_cast<uint8_t>(h.attacker.kind); ev.killerId = h.attacker.id;
    if (kill) {
        combat::DeathDecl d;
        d.deathId = ev.eventId; d.who = combat::Entity::actor(a.id); d.source = combat::DeathSource::Owner; d.declarer = cfg_.self; d.epoch = epoch;
        d.killer = h.attacker; d.limbMask = a.limbMask; d.cell = a.cell; d.where = {a.pos.x, a.pos.y, a.pos.z}; d.tick = static_cast<uint32_t>(now_);
        const combat::DeathVerdict dv = deaths_.judge(d, cfg_.self, isHost(), *cview_);
        if (dv.accept || engineFaults().deathNotIdempotent) {
            ++stats_.deathsDeclared;
            a.health = 0; a.dead = true;
            ev.kind = ActorEventKind::Death; ev.dead = true; ev.health = 0;
            ev.loot = rollLoot(rng_);
            makeCorpse(a, ev.loot);
            creditKill(ev);
        } else {
            ++stats_.deathsRefused;
            a.health = std::max<int32_t>(a.health, 1);
            ev.health = a.health;
        }
    }
    applied_.insert(kTagEvent | ev.eventId);
    sendBody(ev, kNoPlayer);
    if (ev.dead && isHost()) onActorDiedHost(a.id);
}

bool Machine::onActorEvent(const Envelope& e, const MActorEvent& m) {
    ActorState* a = world_.findActor(m.id);
    if (!a) return false;
    const UpdateVerdict v = view_.check(m.id, e.src, m.epoch);
    if (obs_) obs_->onActorUpdate(cfg_.self, now_, m.id, e.src, m.epoch, v);
    if (v != UpdateVerdict::Accept) {
        if (rejectLog_.size() < 64) rejectLog_.push_back(std::string("event ") + std::to_string(m.id) + " from " + std::to_string(e.src) + ": " + toString(v));
        if (v == UpdateVerdict::FutureEpoch && isHost()) {
            OwnMsg cur;
            if (svc_->current(m.id, cur)) { ++stats_.resyncs; sendBody(MOwn{cur}, e.src); }
        }
        return false;
    }
    if (!applyOnce(kTagEvent | m.eventId)) { ++stats_.eventsDuplicate; return false; }
    if (m.dead) {   // a death is declared once: the ledger refuses a second declaration, a replay, and a story NPC killed by combat
        combat::DeathDecl d;
        d.deathId = m.eventId; d.who = combat::Entity::actor(m.id); d.source = combat::DeathSource::Owner; d.declarer = e.src; d.epoch = m.epoch;
        d.killer = combat::Entity{static_cast<combat::EntityKind>(m.killerKind <= 2 ? m.killerKind : 0), m.killerId};
        d.limbMask = m.limbMask; d.cell = m.cell; d.where = {m.pos.x, m.pos.y, m.pos.z};
        const combat::DeathVerdict dv = deaths_.judge(d, e.src, e.src == kHostPlayer, *cview_);
        if (!dv.accept && !engineFaults().deathNotIdempotent) {
            ++stats_.deathsRefused;
            if (rejectLog_.size() < 64) rejectLog_.push_back(std::string("death of ") + std::to_string(m.id) + " from " + std::to_string(e.src) + ": " + combat::toString(dv.reason));
            return false;
        }
    }
    if (m.dead && a->dead) breach("death_declared_twice", "actor " + std::to_string(m.id) + " was declared dead a second time (event from p" + std::to_string(e.src) + ")");   // only reachable with the fault
    a->health = m.health; a->limbMask = m.limbMask;
    for (unsigned i = 0; i < combat::kLimbCount; ++i) a->limbDmg[i] = m.limbDmg[i];
    if (m.seq > a->snapSeq) { a->snapSeq = m.seq; a->cell = m.cell; a->pos = m.pos; a->yaw = m.yaw; }
    ++stats_.eventsApplied;
    if (m.dead) {
        a->dead = true; a->health = 0;
        LootList loot = m.loot;
        if (engineFaults().lootPerMachine) { loot = rollLoot(rng_); breach("loot_rolled_again", "corpse " + std::to_string(a->id) + " loot rolled on a machine that is not its owner"); }
        makeCorpse(*a, loot);
        creditKill(m);
        if (isHost()) diedAfterRelay_.push_back(a->id);   // untrack only after the event itself went out (ordering: event, then Release)
    }
    return true;
}

// ---- hits ------------------------------------------------------------------------------------

void Machine::sendHit(const MHit& h, PlayerId dest) {
    if (dest == cfg_.self) {
        Envelope e;
        e.sess = sess_; e.src = cfg_.self; e.dest = cfg_.self; e.body = h;
        onHit(e, h);
        return;
    }
    sendBody(h, dest);
}

void Machine::onHit(const Envelope& e, MHit h) {
    if (isHost() && e.dest != cfg_.self) {
        // routing: the host knows the current owner, so a hit aimed at a stale owner is redirected
        if (h.victimIsPlayer) { relay(e); ++stats_.hitsForwarded; return; }
        const PlayerId owner = view_.ownerOf(h.victim);
        if (owner != kNoPlayer && owner != cfg_.self) {
            Envelope f = e;
            f.dest = owner;
            relay(f);
            ++stats_.hitsForwarded;
            return;
        }
    }
    if (!applyOnce(kTagHit | h.hitId)) { ++stats_.hitDuplicates; return; }
    if (h.victimIsPlayer) { applyHitToSelf(e, h); return; }
    ActorState* a = world_.findActor(h.victim);
    if (!a) return;
    const PlayerId owner = view_.ownerOf(h.victim);
    PlayerId judgeAs = cfg_.self;
    if (owner != cfg_.self && engineFaults().hitAppliedByNonOwner) {
        breach("non_owner_hit_applied", "hit on actor " + std::to_string(h.victim) + " applied by a machine that is not its owner (owner p" + std::to_string(owner) + ")");
        judgeAs = owner;   // the fault: this machine takes the hit as if it owned the victim
    }
    const combat::HitEvent he = toHitEvent(h);
    combat::HitVerdict v;
    if (engineFaults().hitNoValidation) {
        // the fault: nothing is checked. Run the real checks on a copy anyway so the oracle can see a forged hit take effect.
        combat::HitValidator probe = hits_;
        const combat::HitVerdict real = probe.judge(he, e.src, judgeAs, now_, *cview_);
        if (!real.apply) breach("forged_hit_applied", "hit on actor " + std::to_string(h.victim) + " from " + std::to_string(e.src) + " applied although it failed validation (" + real.detail + ")");
        v.apply = true;
    } else {
        v = hits_.judge(he, e.src, judgeAs, now_, *cview_);
    }
    if (!v.apply) {
        ++stats_.hitsRejected;
        if (rejectLog_.size() < 64) rejectLog_.push_back("hit on " + std::to_string(h.victim) + ": " + combat::toString(v.reason));
        return;
    }
    ++stats_.hitsApplied;
    applyDamage(*a, he);
}

void Machine::applyHitToSelf(const Envelope& e, const MHit& h) {
    PlayerState& me = world_.player(cfg_.self);
    if (h.victim != cfg_.self || me.dead) return;
    if (!engineFaults().hitNoValidation) {
        // the player's own machine applies the damage the NPC's owner (or the shooter's machine) reports, after the same checks
        const combat::HitVerdict v = hits_.judge(toHitEvent(h), e.src, cfg_.self, now_, *cview_);
        if (!v.apply) {
            ++stats_.hitsRejected;
            if (rejectLog_.size() < 64) rejectLog_.push_back("hit on player " + std::to_string(h.victim) + ": " + combat::toString(v.reason));
            return;
        }
    }
    me.health -= h.damage;
    if (me.health <= 0) { me.health = 0; me.dead = true; }
    ++stats_.hitsApplied;
    MVitals v;
    v.slot = cfg_.self; v.health = me.health; v.dead = me.dead; v.seq = seqBase() + ++vitalsCounter_;
    me.vitalsSeq = v.seq;
    sendBody(v, kNoPlayer);
}

}  // namespace fnvcoop::engine
