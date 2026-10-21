// Puppets as hit targets (FULL-SYNC-PLAN 3.1 + 3.4). A puppet is the local stand-in of a REMOTE player; when something on THIS machine hits it
// (an enemy this machine owns, a stray explosion) the damage must not be taken by the stand-in: it is reported to the REAL player's machine,
// which owns that player's health and applies it (synccore/combat HitValidator -> applyHit; plugin/combat CombatSync is the production path).
//
// This class is the avatar side of that: it decides "is the victim one of our puppets, and which player does it stand for" (avatar_api.h), and
// hands the hit to a port. The production port calls the combat layer's gate (plugin/combat glueGate, win/avatar_module.cpp), the tests'
// port builds a combat::HitEvent and runs it through a second machine's HitValidator. The answer tells the hit gate what the LOCAL engine may
// do with the damage: a puppet never dies of a prediction (Clamp keeps it above zero health), and a hit that could not be forwarded is
// clamped as well (better a puppet that shrugs it off than a stand-in that falls over while the real player was never hurt).
//
// Game-free: RefIds and plain numbers only, no combat types in this header.
#pragma once
#include <cstdint>
#include <string>

#include "avatar_api.h"
#include "avatar_types.h"

namespace nvcoop::avatar {

struct PuppetHit {
    RefId victimRef = 0;      // the reference that was hit (a puppet if the hit matters here)
    RefId attackerRef = 0;    // the attacker on THIS machine (0 = environment / unknown)
    float healthDmg = 0, limbDmg = 0;
    int32_t hitLocation = -1; // engine body part index, -1 = unknown
    Vec3 impact;
    FormId weapon = 0, ammo = 0;
    uint64_t projectileKey = 0, explosionKey = 0;
    bool melee = false, critical = false, vats = false;
};

enum class HitAction : uint8_t {
    Proceed,      // not ours (or nothing to do): the engine applies the hit as computed
    Clamp,        // the engine plays the hit but the puppet keeps at least 1 health
    Neutralize,   // the engine applies nothing (visuals only)
};

enum class HitRoute : uint8_t {
    NotAPuppet,   // the victim is not a live puppet: not our business
    Ignored,      // a puppet, but the hit carries no damage
    Forwarded,    // reported to the real player's machine
    NoPort,       // a puppet, but nothing can carry the report (combat layer off): clamped locally
    PortRefused,  // the port could not forward it: clamped locally
};

struct HitOutcome {
    HitRoute route = HitRoute::NotAPuppet;
    HitAction action = HitAction::Proceed;
    Slot victimSlot = kNoSlot;
};

class IPuppetHitPort {
public:
    virtual ~IPuppetHitPort() = default;
    // Report `hit` (victim = the puppet of `victimSlot`) to that player's machine. true = accepted for delivery; `action` is what the local
    // engine may do with the damage (default Clamp).
    virtual bool Forward(Slot victimSlot, const PuppetHit& hit, HitAction& action) = 0;
};

struct PuppetHitStats {
    uint64_t hits = 0, notPuppet = 0, ignored = 0, forwarded = 0, noPort = 0, refused = 0, clamped = 0, neutralized = 0;
};

class PuppetHitRouter {
public:
    void SetPort(IPuppetHitPort* p) { port_ = p; }
    HitOutcome OnHit(const PuppetHit& h);
    const PuppetHitStats& stats() const { return stats_; }
    std::string StatusJson() const;

private:
    IPuppetHitPort* port_ = nullptr;
    PuppetHitStats stats_;
};

}  // namespace nvcoop::avatar
