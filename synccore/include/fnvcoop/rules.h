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
// (reduceCondition), ExcludeFromGate (excludedFromGate), PerPlayerScene (effects go to the acting player; run=every-machine +
// scope=local-machine makes the quest guest-local), PerPlayerVars (per-slot copies of listed quest vars, host side), ExclusiveStages
// (first committed outcome wins), ReadyCheck (party consent before an INFO's end script), wait/sleep consent (onTimeSkipRequested),
// per-stage `stages` params, PartyTether anchor+radius, max/sum-over-party reductions, the slideshow control hold
// (Barrier before=slideshow / SlideshowEnd lock=all). Kinds with no session effect (ShareMapMarkers, LocalQuestScript...) are loaded,
// counted, and reported via unsupportedKinds().
#pragma once
#include <functional>
#include <map>
#include <set>
#include <string>
#include <vector>
#include "fnvcoop/session.h"

namespace fnvcoop {

constexpr uint32_t kAllControls = 0xFFFFFFFFu;   // SetPlayerControls mask meaning "every control" (unknown bits are ignored by the applier)

struct Rule {
    std::string kind;
    std::string origin;                                             // "curated" | "derived"
    std::map<std::string, std::string> scalars;                     // params with scalar values (as text)
    std::map<std::string, std::vector<std::string>> lists;          // params with array values (cells, markers, masks)
    std::map<std::string, std::string> funcs;                       // PartyAwareCond: function -> reduction
    // Per-stage params: `stages` [n, ...] limits the rule to those stages (absent / empty = every stage). Validated numeric at load.
    bool appliesToStage(uint32_t stage) const;
    std::string scalar(const std::string& key, const std::string& dflt = std::string()) const {
        auto it = scalars.find(key);
        return it == scalars.end() ? dflt : it->second;
    }
    double number(const std::string& key, double dflt) const;
};

struct QuestRules {
    std::string edid, tier, questline;
    bool curated = false;
    std::vector<Rule> rules;
    const Rule* find(const std::string& kind) const {
        for (auto& r : rules) if (r.kind == kind) return &r;
        return nullptr;
    }
    // First rule of `kind` that applies to `stage` (a rule without a `stages` list applies to every stage).
    const Rule* findFor(const std::string& kind, uint32_t stage) const {
        for (auto& r : rules) if (r.kind == kind && r.appliesToStage(stage)) return &r;
        return nullptr;
    }
};

class RulesDb {
public:
    // Strict: malformed JSON, wrong schema or a rule without a kind fails the whole load (nothing is kept). Known kinds are validated:
    // a PartyTether needs a non-empty `cells` list or `anchor`+`radius`; `stages` lists must be numbers; ExclusiveStages needs >= 2 stages;
    // ReadyCheck needs `infos`; PerPlayerVars needs `vars`; Barrier.before may only be `slideshow`.
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
        uint32_t slideshowHoldMs = 600000;   // failsafe for the control lock held until SlideshowEnd
        uint32_t consentTimeoutMs = 60000;   // wait / sleep consent window
    };
    struct Stats {
        uint64_t stagesDeferred = 0, stagesReleased = 0, barrierTimeouts = 0, arrivals = 0, tetherGathers = 0, controlTimeouts = 0,
                 recipientRewrites = 0, locksTracked = 0, unknownReductions = 0, ignoredPasses = 0, dialogueRejected = 0,
                 warpsExpanded = 0, travelLockChanges = 0, guestLocalDropped = 0, exclusiveRejected = 0, readyChecks = 0, readyTimeouts = 0,
                 slideshowHolds = 0, perPlayerRouted = 0, timeSkipsAsked = 0, timeSkipsGranted = 0, timeSkipsDenied = 0;
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
    // ExclusiveStages, asked BEFORE anything runs: true when `stage` is an alternative outcome and a different one of the same set was committed first (or is the
    // quest's present stage). filter() would drop that SetStage from the txn anyway, but only after its result scripts ran on the host; a guest's stage
    // request (plugin/intents Kind::SetStage) is refused up front with this. Read-only.
    bool stageExcluded(const FormRef& quest, uint32_t stage) const;
    // A player finished their per-player step of `quest` (chargen etc.). Slot 0 = the host player.
    void onPlayerPassed(PlayerId slot, const FormRef& quest);
    // A player is now in `cell` (clients report via IntentKind::ReportCell; the host reports itself).
    void onCellReported(PlayerId slot, const FormRef& cell);
    // A player is now within (near=true) / outside the radius of a PartyTether `anchor` ref (IntentKind::ReportNear / ReportFar).
    void onNearReported(PlayerId slot, const FormRef& anchor, bool near);
    // ReadyCheck: the INFO `info` of `quest` wants to run its end script (irreversible step). Deferred => do not run it now; every joined
    // player must confirm (IntentKind::ScenePass with ref = info). onReadyResolved(quest, info, proceed) fires on release: proceed=true when
    // everyone confirmed (or on_timeout=proceed), false = cancelled.
    StageDecision onReadyRequested(const FormRef& quest, const FormRef& info, PlayerId initiator);
    std::function<void(const FormRef& quest, const FormRef& info, bool proceed)> onReadyResolved;
    // Wait / sleep consent: the time skip only happens when every joined player agrees (ScenePass with ref = timeSkipId()), otherwise it is
    // cancelled at the deadline. applyTimeSkip(minutes) fires on grant (the plugin performs it and captures AdvanceTime as usual).
    StageDecision onTimeSkipRequested(PlayerId initiator, uint32_t minutes);
    std::function<void(uint32_t minutes)> applyTimeSkip;
    std::function<void(uint32_t minutes)> timeSkipDenied;
    static FormRef timeSkipId();
    // PerPlayerVars: per-slot copies of quest vars listed by a PerPlayerVars rule. The host plugin loads the acting player's copy into the
    // quest var before running a script / INFO for that player and stores the result back afterwards. Host side only (clients never run
    // quest scripts); saveVars / loadVars persist it with the host save.
    bool isPerPlayerVar(const FormRef& quest, const std::string& var) const;
    void writePlayerVar(const FormRef& quest, const std::string& var, PlayerId slot, double value);
    bool readPlayerVar(const FormRef& quest, const std::string& var, PlayerId slot, double& out) const;
    std::vector<std::pair<std::string, double>> playerVarsOf(const FormRef& quest, PlayerId slot) const;
    // Host-side rules state worth keeping across a host restart: the per-slot var copies and the ExclusiveStages winners.
    Bytes saveState() const;
    bool loadState(const Bytes& b);
    // Routes ScenePass / ReportCell / ReportNear / ReportFar action requests from HostSession::onAction. True if consumed.
    bool handleAction(PlayerId slot, IntentKind kind, const FormRef& ref);
    void tick(uint64_t nowMs);

    // PartyAwareCond: reduce one condition function over the party. Reductions: initiator, any-party-member, any,
    // min-over-party, max-over-party, sum-over-party (collection quests pool what the party carries), "entry:all-in,exit:any-in".
    // Unknown / missing => initiator (counted).
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
        FormRef anchor; bool hasAnchor = false;                       // tether anchor ref (+ radius, checked by the clients' near reports)
        enum Kind : uint8_t { Stage, Ready, TimeSkip } kind = Stage;  // what is waiting
        FormRef info; uint32_t timeoutMs = 0; bool proceedOnTimeout = false; uint32_t minutes = 0;
        bool slideshowAfter = false;                                  // Barrier before=slideshow: hold the controls once released
    };
    struct Lock { uint32_t mask = 0; uint64_t since = 0; };
    void filter(EffectTxn& t);
    uint8_t joinedMask() const;
    bool tetherSatisfied(const std::set<FormRef>& cells, const FormRef& anchor, bool hasAnchor) const;
    void startVote(Pending p);
    bool askerIsAlone(PlayerId asker) const;
    void holdForSlideshow(const FormRef& quest);
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
    std::map<PlayerId, std::set<FormRef>> nearOf_;     // anchors each slot reported being within the radius of
    std::map<FormRef, uint32_t> exclusiveWinner_;      // quest -> the ExclusiveStages outcome that was committed first
    std::set<FormRef> readyInfos_;                     // every INFO a ReadyCheck rule names (a consent for an unasked check is dropped)
    std::map<FormRef, uint64_t> slideshowHold_;        // quest -> when its control hold began (released by SlideshowEnd or the failsafe)
    std::map<std::tuple<FormRef, std::string, PlayerId>, double> playerVars_;
    std::map<FormRef, Lock> locks_;
    std::set<FormRef> travelLocked_;
    PlayerId dialogueInitiator_ = kNoPlayer;
    std::set<std::string> unsupported_;
    Resolver resolve_;
    uint64_t now_ = 0;
    Stats stats_;
};

}  // namespace fnvcoop
