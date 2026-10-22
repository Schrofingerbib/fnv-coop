// Host side of the guest intents (FULL-SYNC-PLAN 3.7). Game-free: the engine, the network and the runtime are injected.
//
// A guest's intent arrives (or a legacy story-channel ActivateRef / TriggerEnter / PickUp does), the host
//   1. checks the sender is a joined player, the id is new (exactly-once per session), the rate, that the kind is switched on;
//   2. resolves the target (loaded? what is it?), picks the ACTING PLAYER: the guest's puppet, else NV:MP's ghost, else (policy permitting)
//      the host's own player as a stand-in, and checks the puppet is near enough;
//   3. runs the interaction inside Runtime::runAsGuest (origin forced to the guest) + an acting scope (capture scope, `player` = the puppet);
//   4. answers with a Verdict. Everything the interaction changed travels as an ordinary txn with the guest as origin.
//
// Shared state of its own, all bounded: item claims (first wins), container locks (one user, leased), trigger cooldowns, per-player id windows.
#pragma once
#include <deque>
#include <functional>
#include <map>
#include <string>
#include <vector>

#include "fnvcoop/protocol.h"
#include "intents/container_redirect.h"
#include "intents/intent_engine.h"
#include "intents/intent_policy.h"
#include "intents/intent_wire.h"

namespace nvcoop::intents {

constexpr RefId kHostPlayerRef = 0x14;

// Who acts for a guest. Told to HostDeps::actorMode when it changes for a slot (the module logs it; a stand-in is the host's own player).
enum class ActorMode : uint8_t { Puppet, Ghost, StandIn };

struct HostDeps {
    IIntentEngine* eng = nullptr;
    std::function<bool(PlayerId slot, Bytes data)> send;                              // to one joined guest, reliable
    std::function<bool(PlayerId slot, const std::function<void()>& body)> runAsGuest; // Runtime::runAsGuest
    std::function<RefId(PlayerId slot)> puppetOf;                                     // avatar layer: the puppet of that player (0 = none)
    std::function<RefId(PlayerId slot)> ghostOf;                                      // NV:MP ghost (0 = none)
    std::function<void(PlayerId slot, ActorMode mode, RefId actor)> actorMode;         // optional: the acting player for a slot changed (puppet / ghost / stand-in)
    std::function<bool(PlayerId slot)> joined;                                        // is that slot a joined player right now
    std::function<uint64_t()> nowMs;
    std::function<void(const FormRef& npc, PlayerId slot)> dialogueStart;             // optional: tell the 0xD1 adapter
    // plugin/companions (nvcoop::social::glueAllowActivation / glueGuestInteraction; the module wires them under NVCOOP_WITH_COMPANIONS). Both optional.
    //   allowActivation  asked BEFORE an Activate / DialogueStart runs; false = refuse it (Result::Denied): somebody else's companion
    //   interacted       told AFTER a guest's Activate / PickUp / ContainerOpen ran (Result::Executed): theft, trespass and break-in are judged from it
    std::function<bool(PlayerId slot, const FormRef& target)> allowActivation;
    std::function<void(PlayerId slot, bool pickup, const FormRef& target)> interacted;
};

struct HostStats {
    uint64_t received = 0, badDatagrams = 0, legacy = 0, verdictsSent = 0, verdictsFailed = 0;
    uint64_t executed = 0, duplicates = 0;
    uint64_t rejected[static_cast<size_t>(Result::Count_)] = {};
    uint64_t byKind[static_cast<size_t>(Kind::Count_)] = {};          // executed per kind
    uint64_t actedAsPuppet = 0, actedAsGhost = 0, actedAsStandIn = 0;
    uint64_t reachChecked = 0, reachUnchecked = 0, scriptsSkipped = 0;
    uint64_t claimsWon = 0, claimsLost = 0, lockGrants = 0, lockBusy = 0, lockExpired = 0, lockReleased = 0, redirects = 0;
    uint64_t activationsRefused = 0, interactionsReported = 0;   // plugin/companions hooks (HostDeps::allowActivation / interacted)
};

class IntentHost {
public:
    IntentHost(HostDeps deps, Config cfg, ContainerRedirect redirect = ContainerRedirect());

    // A datagram from `from` (a transport peer already mapped to a slot). Malformed input is counted and dropped.
    void onDatagram(PlayerId from, const uint8_t* data, size_t size);
    // The whole pipeline for one decoded intent. Sends the verdict (when `send` is set) and returns it.
    Verdict handle(PlayerId slot, const Intent& in);
    // Story-channel intents (ClientSession::submitAction): same checks and execution, no id window, no verdict.
    Result runLegacy(PlayerId slot, fnvcoop::IntentKind kind, const FormRef& ref);

    void tick();                       // lock leases, bounded tables
    void playerLeft(PlayerId slot);    // releases the player's container locks and forgets their id window
    void worldReset();                 // the host loaded a save: claims, locks, cooldowns belong to the old world

    struct LockInfo { FormRef ref; PlayerId holder; uint64_t sinceMs; };
    std::vector<LockInfo> locks() const;
    bool claimed(const FormRef& item) const { return claims_.count(item) != 0; }
    size_t claimCount() const { return claims_.size(); }
    const HostStats& stats() const { return stats_; }
    const Config& config() const { return cfg_; }
    std::string statusJson() const;

private:
    struct Actor { RefId ref = 0; bool puppet = false, ghost = false, standIn = false; };
    struct SlotState {
        uint32_t session = 0;
        uint32_t lastId = 0;
        std::deque<Verdict> recent;     // last verdicts, repeated for a duplicate id
        double tokens = 0;
        uint64_t refilledAt = 0;
        bool init = false;
    };
    Verdict decide(PlayerId slot, const Intent& in, bool legacy);
    Verdict dispatch(PlayerId slot, const Intent& in, const TargetInfo& t);
    bool rateOk(SlotState& s, uint64_t now);
    bool pickActor(PlayerId slot, Kind k, TargetClass cls, Actor* out);
public:
    // Reports the acting player of a slot to HostDeps::actorMode when the mode differs from the last one reported for it.
    void noteActor(PlayerId slot, ActorMode mode, RefId actor);
private:
    Result checkReach(const Actor& a, const TargetInfo& t, Kind k);
    Result runScoped(PlayerId slot, const Actor& a, Kind k, const FormRef& target, const std::function<Result(IActingScope&)>& fn);
    Verdict doActivate(PlayerId slot, const Intent& in, const TargetInfo& t);
    Verdict doPickUp(PlayerId slot, const Intent& in, const TargetInfo& t);
    Verdict doContainerOpen(PlayerId slot, const Intent& in, const TargetInfo& t);
    Verdict doContainerClose(PlayerId slot, const Intent& in);
    Verdict doEvent(PlayerId slot, const Intent& in, const TargetInfo& t, uint32_t mask);
    Verdict doTerminal(PlayerId slot, const Intent& in, const TargetInfo& t);
    uint64_t now() const { return deps_.nowMs ? deps_.nowMs() : 0; }
    Verdict verdictOf(const Intent& in, Result r) const;
    void count(const Intent& in, Result r);

    HostDeps deps_;
    Config cfg_;
    ContainerRedirect redirect_;
    std::map<PlayerId, SlotState> slots_;
    std::map<PlayerId, ActorMode> lastMode_;   // last acting-player mode reported per slot
    struct Claim { PlayerId slot; uint64_t atMs; };
    std::map<FormRef, Claim> claims_;
    std::deque<FormRef> claimOrder_;
    struct Lock { PlayerId slot; uint64_t sinceMs, refreshedMs; };
    std::map<FormRef, Lock> locks_;
    std::map<std::pair<PlayerId, FormRef>, uint64_t> lastTrigger_;
    HostStats stats_;
};

}  // namespace nvcoop::intents
