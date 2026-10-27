// TEST-ONLY fault switches for the dialogue mutation twins. All false in production.
// Each flag re-introduces one classic bug; the matching scenario test must then fail.
#pragma once
namespace fnvcoop_dlg {
struct Mutations {
    bool noNpcLock = false;          // a second player may start a conversation with an NPC already in use
    bool anyonePicks = false;        // host accepts Pick/LineDone/EndReq from non-initiators
    bool replayDuplicatePick = false;// a re-delivered Pick re-runs the begin script (double rewards)
    bool dropEndOnClose = false;     // closing mid-line (disconnect/NPC death) discards the pending end script
    bool leakLockOnClose = false;    // NPC lock and control lock are not released on close
    bool spectatorStaleLines = false;// client applies Lines with an old step (out-of-order / duplicate delivery)
    bool noTopicGate = false;        // initiator builds the next topic list before the end-script result arrived
    bool skipControlRelease = false; // control lock lease is not released when the conversation closes
    bool noJoinCatchUp = false;      // a player who joins mid-conversation is not told about it (stays Idle, ignores Lines/Closed)
    bool ignoreResultLink = false;   // TopicsReady fires on Result without waiting for the result txn to be applied locally
    bool noResync = false;
    // --- round 3 (companions, menus)
    bool capIsPartyWide = false;      // follower cap is the old shared "2 total" instead of per recruiting player
    bool anyoneDismisses = false;     // a non-owner may dismiss someone else's companion
    bool keepCompanionsOfLeaver = false; // companions of a departed owner stay owned by the ghost slot
    bool noOwnerRewrite = false;      // conditions read the shared aggregate (everybody sees everybody's party)
    bool nonOwnerCommands = false;    // host accepts owner-only companion topics from non-owners
    bool spectatorsSeeCommands = false; // owner-only picks/offers are still broadcast to spectators
    bool noPreempt = false;           // the owner cannot take over their companion from a non-owner conversation
    bool menuOnHost = false;          // menu requests open on the host player instead of the origin player
    bool anyoneRepliesMenu = false;   // any player may answer someone else's menu
    bool resumeMenuTwice = false;     // a duplicate MenuReply resumes the script again
    bool noMenuDefault = false;       // timeout / disconnect never resumes the waiting script
    bool anyMenuChoice = false;       // out-of-range button indices are accepted
    bool forcedStartToAll = false;    // ForcedStart (open the real menu) goes to every player instead of the target only
    bool forcedNoPreempt = false;     // a scripted conversation does not displace an existing one (it silently never starts)
    bool forcedAlwaysHost = false;    // the acting player of a party scene is always the host
    bool forcedNoDedupe = false;      // a re-delivered ForcedLines is shown again
    bool skipConsent = false;         // party-consent prompts resume the script on the origin's answer alone
    bool menuToSpectators = false;    // spectators receive the interactive MenuRequest (can answer it)           // a rejoining client keeps its stale Initiator/Spectator role
    // --- round 5 (campaign fix wave 1, P3: guest dialogue end to end)
    bool dlgReadWrongInfo = false;    // the response reader trusts the engine cache even when it holds another INFO's chain
    bool sayOnceNeverShared = false;  // watching a line never marks the INFO said (player B can repeat player A's Speech success)
    bool sayOnceAllShared = false;    // trainer / Speech-XP lines are shared too (only the first player gets them)
    bool sayOnceKeepHostMark = false; // the host keeps the said-mark its RunResultScript made for a guest's per-player line
    bool pollNoPick = false;          // the menu poller never reports the chosen INFO (the host never learns the pick)
    bool pollPickEveryFrame = false;  // the menu poller re-reports the spoken INFO every frame (duplicate picks)
    bool pollNoLineDone = false;      // the menu poller never reports the end of a line (the gate waits for the timeout)
    bool pollLeaveAfterGoodbye = false; // closing the menu after a finished goodbye line is also reported as the player leaving
    bool nonOwnerPicksCommand = false;// the glue lets a non-owner send an owner-only companion command
    bool noCompanionCanon = false;    // a companion known under several forms (base, placed refs) is tracked per form: ownership is lost
    bool forcedHostReplays = false;   // the host replays a scripted line its own engine already played (double subtitles and voice)
    // --- G08 choice consent (D3: party vote, the host breaks ties; points of no return need everybody)
    bool voteIsUnanimous = false;     // a party vote needs every player to agree (one "not yet" kills a story fork)
    bool voteNoHostTieBreak = false;  // a tied vote is declined instead of the host deciding
    bool voteTimeoutApproves = false; // the consent window running out approves the choice
    bool noChoiceConsent = false;     // a fork INFO runs its scripts at the pick, before the party was asked
    bool denyKeepsSaid = false;       // a denied fork leaves the INFO marked as said for the initiator (the choice is gone for good)
    bool actorAsksParty = false;      // a personal decision (Caesar's surgery, a trooper's treatment) is put to a vote too
    bool hostTalkSilent = false;      // the host player's own conversation is not shown to the other players
};
inline Mutations& mutations() {
    static Mutations m;
    return m;
}
struct MutationScope {
    Mutations saved;
    explicit MutationScope(const Mutations& m) : saved(mutations()) { mutations() = m; }
    ~MutationScope() { mutations() = saved; }
};
}  // namespace fnvcoop_dlg
