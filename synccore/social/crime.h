// social/crime: game-free CRIME, ALARMS, BOUNTIES and FACTION HOSTILITY (FULL-SYNC-PLAN 3.4 "Crime, alarms and faction hostility").
// C++17, no engine, no network: time and facts are passed in. Mutation twins 831..879 (FNV_MUT).
//
// What the engine has, so what we model (FalloutNV.exe 1.4.0.525, read from the exe's command table):
//   New Vegas has NO crime gold. Its crime state is per faction FLAGS: SetPCFactionAttack / SetPCFactionMurder <faction> <0|1>,
//   SetPCEnemyOfFaction <faction> <0|1>, SetEnemy / SetAlly <factionA> <factionB>, plus the alarms SendAssaultAlarm / SendTrespassAlarm and per-actor
//   StartCombat <target>. So our "bounty" is a CO-OP WANTED SCORE per faction (host-side, shared by the whole party: the party is one Courier):
//   crimes add points, points flip the faction's engine flags (attack at once, murder on a witnessed murder, enemy at `hostileAtBounty` or on a murder),
//   forgiving clears the score and the flags. The numbers are ours (CrimeRules), not read from the game.
//
// The rules (each guarded by a unit test and a twin):
//   * HOST-AUTHORITATIVE. Anybody reports a CrimeEvent; only the host decides (CrimeJudge); every machine applies the host's result once
//     (BountyBook revisions, HostilityBook ids, AlarmOrder ids).
//   * A crime counts only when WITNESSED. Detection by ANY player's machine counts: the owner of each NPC reports "this NPC sees player P"
//     (DetectionBook); a crime is witnessed by an NPC of the faction that cares, in the same cell, seen within the window. A surviving victim
//     is a witness of its own assault.
//   * A report is believed only about the reporter itself (a client speaks for itself); the host may report for a guest (stealing / trespass by a
//     guest is judged on the host from the guest's intent: classifyInteraction).
//   * A burst of the same crime (an automatic weapon: 30 hits) is ONE crime inside `dedupeMs`.
//   * Attacking something already hostile, the party's own faction, and companions' friendly fire are not crimes. A companion's crime is its recruiter's.
//   * An alarm is one order per (faction, cell); machines alert the NPCs THEY own (StartCombat <criminal>), so no NPC reacts twice.
#pragma once
#include <cstdint>
#include <map>
#include <set>
#include <string>
#include <tuple>
#include <vector>
#include "combat/combat.h"
#include "fnvcoop/types.h"
#include "fnvcoop/wire.h"

namespace fnvcoop::social {

namespace cc = fnvcoop::combat;

enum class CrimeKind : uint8_t { Assault = 0, Murder, Theft, Pickpocket, Trespass, BreakIn, Vandalism, Count_ };
constexpr size_t kCrimeKinds = static_cast<size_t>(CrimeKind::Count_);
const char* toString(CrimeKind k);
// The faction the party belongs to (falloutnv.esm 0x1BF3E "PlayerFaction"): crimes against it are not crimes, hostility is declared FROM it.
FormRef playerFaction();

struct CrimeRules {
    int32_t bounty[kCrimeKinds] = {25, 100, 15, 30, 5, 20, 10};   // points per witnessed crime: assault, murder, theft, pickpocket, trespass, break-in, vandalism
    int32_t infamy[kCrimeKinds] = {0, 2, 0, 1, 0, 0, 0};          // reputation: infamy with the faction per witnessed crime (ModReputation)
    int32_t hostileAtBounty = 100;     // score at which the faction turns hostile (SetEnemy); a witnessed murder does it at once. 0 = only murder does
    int32_t maxBounty = 1000;
    uint32_t witnessWindowMs = 6000;   // a detection older than this does not witness anything
    uint32_t dedupeMs = 3000;          // the same (criminal, kind, faction) inside this window is one crime
    uint32_t alarmMs = 30000;          // how long an alarm order stays valid for a late joiner
    float alarmRadius = 4000;          // game units around where the crime happened
    bool victimWitnesses = true;       // a surviving person who was assaulted / pickpocketed saw it
    bool companionCrimesCount = true;  // a companion's crime is charged to its recruiter
    int32_t decayPerMinute = 0;        // points forgiven per minute without a new crime (0 = never)
    size_t remember = 8192;            // event ids kept for idempotency
};
CrimeRules defaultCrimeRules();

// ---- reports -----------------------------------------------------------------------------------------------------------------------------
struct CrimeEvent {
    uint64_t id = 0;                // (reporter slot << 48 | counter): idempotent
    CrimeKind kind = CrimeKind::Assault;
    PlayerId criminal = kNoPlayer;  // the player answerable (a companion's recruiter)
    PlayerId reporter = kNoPlayer;
    bool byCompanion = false;
    cc::Entity victim;              // Assault / Murder / Pickpocket: the NPC; none for theft and trespass
    FormRef faction;                // the crime faction that cares: the victim's, or the faction that owns the stolen / trespassed thing
    uint32_t cell = 0;
    cc::Vec3 where;
    int32_t value = 0;              // Theft: caps value (informational; the table gives the points)
    bool victimHostile = false;     // the victim was already fighting the party: not a crime
    bool victimDead = false;        // Murder: it died
    uint8_t nativeMask = 0;         // slots whose engine already applied this crime's reputation natively (the criminal's own machine killed it)
    uint32_t tick = 0;
};
void io(Archive& a, CrimeEvent& e);

struct Detection {                  // "NPC `observer` (of crime faction `faction`) sees player `target`" (published by the observer's OWNER)
    cc::Entity observer;
    FormRef faction;
    PlayerId target = kNoPlayer;
    uint32_t cell = 0;
    bool seen = true;               // false = lost sight
};
void io(Archive& a, Detection& d);

class DetectionBook {
public:
    explicit DetectionBook(uint32_t windowMs = 6000) : window_(windowMs) {}
    void setWindow(uint32_t ms) { window_ = ms; }
    void report(const Detection& d, uint64_t nowMs);           // seen = refresh; lost = clear that observer's entry
    // Fresh detections of `target` in `cell`.
    std::vector<Detection> witnesses(PlayerId target, uint32_t cell, uint64_t nowMs) const;
    void forget(const cc::Entity& observer);                   // the NPC died / left
    void expire(uint64_t nowMs);
    size_t size() const { return seen_.size(); }
    void clear() { seen_.clear(); }
private:
    struct Rec { Detection d; uint64_t atMs = 0; };
    uint32_t window_;
    std::map<std::pair<cc::Entity, PlayerId>, Rec> seen_;
};

// ---- bounty (wanted score) ---------------------------------------------------------------------------------------------------------------
struct FactionStanding {
    int32_t bounty = 0;
    bool attack = false;            // SetPCFactionAttack
    bool murder = false;            // SetPCFactionMurder
    bool enemy = false;             // SetPCEnemyOfFaction
    uint32_t rev = 0;
    bool clean() const { return bounty == 0 && !attack && !murder && !enemy; }
    friend bool operator==(const FactionStanding& a, const FactionStanding& b) {
        return a.bounty == b.bounty && a.attack == b.attack && a.murder == b.murder && a.enemy == b.enemy && a.rev == b.rev;
    }
};
struct BountyUpdate {
    FormRef faction;
    FactionStanding st;
    friend bool operator==(const BountyUpdate& a, const BountyUpdate& b) { return a.faction == b.faction && a.st == b.st; }
};
void io(Archive& a, FactionStanding& s);
void io(Archive& a, BountyUpdate& u);

class BountyBook {
public:
    FactionStanding get(const FormRef& f) const;
    // Host: adds points and raises flags; returns the update to broadcast (rev raised). `enemy` only ever turns ON here.
    BountyUpdate add(const FormRef& f, int32_t points, bool attack, bool murder, bool enemy, int32_t maxBounty);
    // Host: pardon. Returns the update (all clear, rev raised), or an update equal to the current when it was already clean.
    BountyUpdate forgive(const FormRef& f);
    // Host: decay by `points` (never below 0). Returns true and fills `u` when something changed.
    bool decay(const FormRef& f, int32_t points, BountyUpdate& u);
    // Every machine: applies the host's update when its rev is HIGHER. false = stale / duplicate.
    bool apply(const BountyUpdate& u);
    std::vector<BountyUpdate> roster() const;
    void replace(const std::vector<BountyUpdate>& all);
    uint64_t digest() const;
    size_t size() const { return m_.size(); }
    void clear() { m_.clear(); }
private:
    std::map<FormRef, FactionStanding> m_;
};

// ---- the judge (host) ---------------------------------------------------------------------------------------------------------------------
enum class CrimeReason : uint8_t { Ok = 0, Duplicate, BadReporter, NotACrime, NoFaction, OwnFaction, Burst, Unwitnessed, Count_ };
const char* toString(CrimeReason r);

struct RepChange { FormRef faction; int32_t fame = 0, infamy = 0; };
struct AlarmOrder {
    uint64_t id = 0;                // derived from the event id: a replay is the same order
    FormRef faction;
    uint32_t cell = 0;
    cc::Vec3 where;
    PlayerId criminal = kNoPlayer;
    uint64_t untilMs = 0;           // host clock
    friend bool operator==(const AlarmOrder& a, const AlarmOrder& b) {
        return a.id == b.id && a.faction == b.faction && a.cell == b.cell && a.where == b.where && a.criminal == b.criminal && a.untilMs == b.untilMs;
    }
};
void io(Archive& a, AlarmOrder& o);

struct CrimeDecision {
    CrimeReason reason = CrimeReason::Ok;
    bool witnessed = false;
    PlayerId blamed = kNoPlayer;
    uint8_t nativeMask = 0;         // copied from the event: slots that must not get the reputation again
    std::vector<BountyUpdate> bounty;
    std::vector<AlarmOrder> alarms;
    std::vector<cc::HostilityEffect> hostility;
    std::vector<RepChange> reputation;
    bool counted() const { return reason == CrimeReason::Ok; }
};

class CrimeJudge {
public:
    explicit CrimeJudge(CrimeRules r = defaultCrimeRules(), PlayerId self = kHostPlayer) : rules_(r), self_(self) {}
    const CrimeRules& rules() const { return rules_; }
    void setRules(const CrimeRules& r) { rules_ = r; }
    void setSelf(PlayerId s) { self_ = s; }
    // Host only (anything else returns NotACrime / empty). `sender` = the machine the report came from. `det` = the detections the host holds.
    CrimeDecision judge(const CrimeEvent& e, PlayerId sender, uint64_t nowMs, const DetectionBook& det);
    // Pardon a faction (quest, bribe, time): clears the score and the flags and turns a hostile pair back into allies.
    CrimeDecision forgive(const FormRef& faction, PlayerId cause);
    // Time forgives: call about once a second on the host.
    std::vector<BountyUpdate> tick(uint64_t nowMs);
    BountyBook& bounty() { return bounty_; }
    const BountyBook& bounty() const { return bounty_; }
    cc::HostilityBook& hostility() { return hostility_; }
    const cc::HostilityBook& hostility() const { return hostility_; }
    uint64_t counted() const { return counted_; }
    void reset();
private:
    bool remember(uint64_t id);
    CrimeRules rules_;
    PlayerId self_;
    BountyBook bounty_;
    cc::HostilityBook hostility_;
    std::set<uint64_t> ids_;
    std::vector<uint64_t> order_;
    std::map<std::tuple<PlayerId, uint8_t, FormRef>, uint64_t> lastAt_;   // (criminal, kind, faction) -> last counted time
    std::map<FormRef, uint64_t> lastCrimeAt_;
    std::map<std::pair<FormRef, uint32_t>, uint64_t> alarmUntil_;   // (faction, cell) -> the alarm in force ends at (host clock)
    uint64_t lastTickMs_ = 0, decayAcc_ = 0, counted_ = 0;
};

// ---- interactions of a guest, judged on the host --------------------------------------------------------------------------------------
enum class Interaction : uint8_t { OpenContainer = 0, TakeItem, PickLock, HackTerminal, UseBed, EnterOwnedCell, Pickpocket, Destroy, Count_ };
const char* toString(Interaction i);
struct Ownership {                  // who owns the thing, as the engine reports it (ExtraOwnership of the ref, else of its cell)
    bool owned = false;
    bool ownerIsFaction = false;    // owner is a faction (a base's faction) rather than an NPC
    FormRef ownerForm;              // the NPC or the faction
    FormRef crimeFaction;           // the faction that cares: the owner's faction (an NPC owner's own crime faction)
    int32_t value = 0;              // caps value of what is taken
    bool publicPlace = false;       // a public cell / an unlocked shared thing: no trespass
};
struct InteractionFacts {
    Interaction what = Interaction::OpenContainer;
    Ownership own;
    bool locked = false;            // the thing is locked (opening it by lockpick / hack is a break-in)
    bool ownerPresentAndFriendly = false;   // the owner gave permission (dialogue, "ok to take")
};
// Turns a guest's interaction into a crime, or nothing (nobody's thing, a public place, the party's own faction, permission given).
// `criminalFactions` = the factions the criminal belongs to (at least the party faction). `id` is the event id to use.
bool classifyInteraction(const InteractionFacts& f, PlayerId criminal, PlayerId reporter, uint64_t id, uint32_t cell, const cc::Vec3& where,
                         const std::set<FormRef>& criminalFactions, CrimeEvent& out, const cc::Entity& victim = cc::Entity());

}  // namespace fnvcoop::social
