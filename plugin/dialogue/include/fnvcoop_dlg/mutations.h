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
