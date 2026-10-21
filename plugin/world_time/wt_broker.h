// Party consent for wait / sleep and fast travel (host side). Game-free.
//
// The question itself is asked through the existing consent machinery (internal lane notes, L8 DialogueNode::raiseModal with
// ModalOptions::partyConsent: the asker confirms, every other player answers "Ready" / "Not yet", any no or a timeout declines). This
// class owns the RULES around that question:
//   * rest (wait / sleep): needs every joined player; refused while anybody is fighting; one question at a time; a no starts a cooldown;
//     a minute cap; consent is re-checked when it arrives (a player who joined or a fight that started after the question was asked).
//   * fast travel: free by default (out of combat only, and not while a quest holds a FastTravelLock). With TravelMode::Party, or while a
//     story tether holds the party, the trip needs the same consent and then the WHOLE party goes (TravelGo.party).
//   * alone in the session: nobody to ask, granted at once.
// request() answers Granted / Denied at once, or Asked: then `resolve` fires exactly once later (never for a Granted / Denied answer).
// Mutation twins 115..129 live in wt_broker.cpp.
#pragma once
#include <functional>
#include <map>
#include <string>

#include "wt_types.h"

namespace nvwt {

struct PartyView {
    std::function<uint32_t()> joined;       // bit per joined slot, the host player included
    std::function<uint32_t()> inCombat;     // bit per slot whose player is fighting
    std::function<bool()> tethered;         // a PartyTether (a story moment) holds the party together
    std::function<bool()> travelLocked;     // some quest holds a FastTravelLock
};

enum class TravelMode : uint8_t { Free = 0, Party = 1 };

struct BrokerConfig {
    uint32_t timeoutMs = 45000;       // handed to the prompt layer
    uint32_t graceMs = 5000;          // if the prompt layer never answers, the request is resolved as Timeout this long after timeoutMs
    uint32_t cooldownMs = 8000;       // after a no / timeout the same kind of question is refused for this long
    uint32_t maxMinutes = 12 * 60;    // wait / sleep cap
    TravelMode travel = TravelMode::Free;
};

struct Resolved {
    uint32_t reqId = 0;
    ReqKind kind = ReqKind::Wait;
    PlayerId initiator = 0;
    uint32_t minutes = 0;
    FormRef marker;
    bool granted = false;
    Reason reason = Reason::None;
    bool party = false;   // travel: the whole party goes
};

struct Outcome {
    enum Kind : uint8_t { Granted, Asked, Denied } kind = Denied;
    Reason reason = Reason::None;
    uint32_t reqId = 0;
    bool party = false;
};

class ConsentBroker {
public:
    // ask(initiator, kind, text, timeoutMs) -> prompt id (0 = the prompt could not be raised: the request is refused)
    using AskFn = std::function<uint32_t(PlayerId initiator, ReqKind kind, const std::string& text, uint32_t timeoutMs)>;
    using ResolveFn = std::function<void(const Resolved&)>;
    ConsentBroker(BrokerConfig cfg, PartyView party, AskFn ask, ResolveFn resolve)
        : cfg_(cfg), party_(std::move(party)), ask_(std::move(ask)), resolve_(std::move(resolve)) {}

    Outcome request(PlayerId initiator, ReqKind kind, uint32_t minutes, const FormRef& marker, uint64_t nowMs);
    // The prompt layer answered (or timed out). Returns true when the prompt belonged to this broker.
    bool onPromptResult(uint32_t promptId, bool confirmed, bool timedOut, uint64_t nowMs);
    void onPlayerLeft(PlayerId slot, uint64_t nowMs);
    void tick(uint64_t nowMs);

    bool busy() const { return !pending_.empty(); }
    const BrokerConfig& config() const { return cfg_; }
    void setTravelMode(TravelMode m) { cfg_.travel = m; }
    struct Stats { uint32_t asked = 0, granted = 0, denied = 0, resolvedTwice = 0, lateAnswers = 0; };
    const Stats& stats() const { return stats_; }

    static std::string describe(ReqKind kind, uint32_t minutes);

private:
    struct Pending {
        uint32_t reqId = 0, promptId = 0;
        ReqKind kind = ReqKind::Wait;
        PlayerId initiator = 0;
        uint32_t minutes = 0, joinedAtAsk = 0;
        FormRef marker;
        bool party = false;
        uint64_t askedMs = 0;
    };
    Reason recheck(const Pending& p) const;
    void finish(Pending p, bool granted, Reason why, uint64_t nowMs);
    Reason baseDenial(PlayerId initiator, ReqKind kind, uint32_t minutes, uint64_t nowMs) const;

    BrokerConfig cfg_;
    PartyView party_;
    AskFn ask_;
    ResolveFn resolve_;
    std::map<uint32_t, Pending> pending_;   // by reqId (at most one in practice)
    std::map<uint32_t, uint64_t> cancelledPrompts_;   // promptId -> when: answers that arrive late are ignored
    uint32_t nextReq_ = 0;
    uint64_t lastDeniedMs_[3] = {0, 0, 0};
    bool everDenied_[3] = {false, false, false};
    Stats stats_;
};

}  // namespace nvwt
