// Quest rules engine (host side, game-free).
//
// Input: docs/quest-policies/rules.json (L9; kinds documented in tools/coverage/rules.py), keyed by quest EDID.
// Output: the session-level effects synccore already has (BarrierArrive/Release, PartyTether, SetPlayerControls,
// SaveSuppress, recipient rewrites for rewards / quest items / hand-ins / karma-rep / companion ownership).
//
// How the host plugin uses it
//   RulesEngine eng(host, db, resolver);              // resolver maps quest/cell EDIDs to FormRefs
//   eng.applyStage = [](quest, stage){ /* real SetStage on the host, capture open */ };
//   host.onAction  = [&](slot, kind, ref, ...){ eng.handleAction(slot, kind, ref); ... };   // ScenePass / ReportCell
//   hook for "a quest wants to advance": if (eng.onStageRequested(q, stage, initiator) == Deferred) skip the engine call now;
//       the engine calls applyStage(q, stage) when the barrier / tether releases.
//   every host frame: eng.tick(nowMs).
// Rule kinds handled: Barrier, PartyTether, SetPlayerControls (scope all + timeout + SaveSuppress tie), SaveSuppress,
// RewardAll, QuestItemDup, RemoveItemPerOwner, ShareKarmaRep, CompanionOwner, SlideshowEnd (host once), PartyAwareCond
// (reduceCondition), ExcludeFromGate (excludedFromGate). Kinds with no session effect yet (PartyWarp needs engine refs,
// FastTravelLock, PerPlayerScene, InitiatorDialogue) are loaded, counted, and reported via unsupportedKinds().
#pragma once
#include <functional>
#include <map>
#include <set>
#include <string>
#include <vector>
#include "fnvcoop/session.h"

namespace fnvcoop {

struct Rule {
    std::string kind;
    std::string origin;                                             // "curated" | "derived"
    std::map<std::string, std::string> scalars;                     // params with scalar values (as text)
    std::map<std::string, std::vector<std::string>> lists;          // params with array values (cells, markers, masks)
    std::map<std::string, std::string> funcs;                       // PartyAwareCond: function -> reduction
};

struct QuestRules {
    std::string edid, tier, questline;
    bool curated = false;
    std::vector<Rule> rules;
    const Rule* find(const std::string& kind) const {
        for (auto& r : rules) if (r.kind == kind) return &r;
        return nullptr;
    }
};

class RulesDb {
public:
    // Strict: malformed JSON, wrong schema or a rule without a kind fails the whole load (nothing is kept).
    bool load(const std::string& jsonText, std::string* err = nullptr);
    bool loadFile(const std::string& path, std::string* err = nullptr);
    const QuestRules* find(const std::string& edid) const;
    uint64_t textHash() const { return textHash_; }   // hash of the exact rules.json text that was loaded (handshake)
    const std::map<std::string, QuestRules>& quests() const { return quests_; }
    std::map<std::string, size_t> kindCounts() const;
private:
    std::map<std::string, QuestRules> quests_;
    uint64_t textHash_ = 0;
};

enum class StageDecision { Proceed, Deferred };
enum class CondPhase { Entry, Exit };
struct PartyValue { PlayerId slot = 0; double value = 0; };

class RulesEngine {
public:
    struct Options {
        uint32_t barrierTimeoutMs = 120000, controlTimeoutMs = 120000;
        // PartyWarp: maps a slot to its player ref (host player = slot 0). Without it PartyWarp rules are inactive.
        std::function<bool(PlayerId slot, FormRef& playerRef)> playerRef;
        float warpSpacing = 64.f;   // slot offset along x between warped party members
    };
    struct Stats {
        uint64_t stagesDeferred = 0, stagesReleased = 0, barrierTimeouts = 0, arrivals = 0, tetherGathers = 0, controlTimeouts = 0,
                 recipientRewrites = 0, locksTracked = 0, unknownReductions = 0, ignoredPasses = 0, dialogueRejected = 0,
                 warpsExpanded = 0, travelLockChanges = 0, guestLocalDropped = 0;
    };
    using Resolver = std::function<bool(const std::string& edid, FormRef& out)>;

    RulesEngine(HostSession& host, const RulesDb& db, Resolver resolve);
    RulesEngine(HostSession& host, const RulesDb& db, Resolver resolve, Options opt);
    ~RulesEngine();
    RulesEngine(const RulesEngine&) = delete;
    RulesEngine& operator=(const RulesEngine&) = delete;

    // The host plugin performs the real SetStage when a deferral releases (it runs the stage's result scripts, captured as usual).
    std::function<void(const FormRef& quest, uint32_t stage)> applyStage;

    // A quest wants to advance to `stage` on the host. Deferred => do not perform it now; applyStage fires on release.
    StageDecision onStageRequested(const FormRef& quest, uint32_t stage, PlayerId initiator);
    // A player finished their per-player step of `quest` (chargen etc.). Slot 0 = the host player.
    void onPlayerPassed(PlayerId slot, const FormRef& quest);
    // A player is now in `cell` (clients report via IntentKind::ReportCell; the host reports itself).
    void onCellReported(PlayerId slot, const FormRef& cell);
    // Routes ScenePass / ReportCell action requests from HostSession::onAction. True if consumed.
    bool handleAction(PlayerId slot, IntentKind kind, const FormRef& ref);
    void tick(uint64_t nowMs);

    // PartyAwareCond: reduce one condition function over the party. Reductions: initiator, any-party-member, any,
    // min-over-party, "entry:all-in,exit:any-in". Unknown / missing => initiator (counted).
    double reduceCondition(const FormRef& quest, const std::string& func, const std::vector<PartyValue>& values,
                           PlayerId initiator, CondPhase phase = CondPhase::Entry);

    // InitiatorDialogue: while a dialogue is running only its initiator's result txns (cause DialogueResult) of a rule-bearing quest are
    // committed; other players' attempts are dropped. Everyone still receives the initiator's results (broadcast).
    void onDialogueStart(PlayerId initiator) { dialogueInitiator_ = initiator; }
    void onDialogueEnd() { dialogueInitiator_ = kNoPlayer; }

    // GuestLocalRun (allow-list): quest scripts that run locally on EVERY machine and are never captured / replicated. L5 feeds this into
    // SuppressPolicy::skipScript (guests must NOT suppress these scripts) and the host drops any txn they produce.
    bool guestLocalRun(const FormRef& quest) const;
    std::vector<FormRef> guestLocalQuests() const;
    // Script EDIDs named by GuestLocalRun rules (param "scripts"), for object/effect scripts that are not quest scripts.
    std::vector<std::string> guestLocalScripts() const;

    bool excludedFromGate(const FormRef& quest) const;
    const QuestRules* rulesFor(const FormRef& quest) const;
    size_t pendingDeferrals() const { return pending_.size(); }
    const Stats& stats() const { return stats_; }
    const std::set<std::string>& unsupportedKinds() const { return unsupported_; }
    // Barrier id used in BarrierArrive/BarrierRelease for (quest, stage).
    static FormRef barrierId(const FormRef& quest, uint32_t stage);

private:
    struct Pending {
        FormRef quest; uint32_t stage = 0; PlayerId initiator = 0;
        bool tether = false; FormRef barrier; uint64_t since = 0; std::set<PlayerId> passed; std::set<FormRef> cells;
    };
    struct Lock { uint32_t mask = 0; uint64_t since = 0; };
    void filter(EffectTxn& t);
    uint8_t joinedMask() const;
    bool tetherSatisfied(const std::set<FormRef>& cells) const;
    void release(size_t index, bool timedOut);
    void checkPending();
    bool desiredTravelLock(const FormRef& quest) const;
    void syncTravelLock(const FormRef& quest);

    HostSession& host_;
    const RulesDb& db_;
    Options opt_;
    std::map<FormRef, const QuestRules*> byForm_;
    std::map<FormRef, std::string> cellsByEdid_;
    std::vector<Pending> pending_;
    std::map<FormRef, std::set<PlayerId>> earlyPasses_;
    std::map<PlayerId, FormRef> cellOf_;
    std::map<FormRef, Lock> locks_;
    std::set<FormRef> travelLocked_;
    PlayerId dialogueInitiator_ = kNoPlayer;
    std::set<std::string> unsupported_;
    Resolver resolve_;
    uint64_t now_ = 0;
    Stats stats_;
};

}  // namespace fnvcoop
