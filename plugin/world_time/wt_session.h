// One machine's world-time session (host or client). Game-free; the engine and the network are injected.
//
// Host   : ClockAuthority (beacons), ConsentBroker (wait / sleep / travel), the wire handlers for client requests, the granted skip.
// Client : ClockFollower (drift correction, AdvanceTime), the veto + request for wait / sleep, the travel decision, the verdicts.
// Both   : the local hooks (onLocalRest / onLocalTravel) return false when the native action must NOT happen now.
//
// Mutation twins 153..166 live in wt_session.cpp.
#pragma once
#include <functional>
#include <map>
#include <memory>

#include "fnvcoop/effect.h"
#include "wt_broker.h"
#include "wt_clock.h"
#include "wt_engine.h"
#include "wt_wire.h"

namespace nvwt {

struct SessionConfig {
    ClockAuthority::Config authority;
    ClockFollower::Config follower;
    BrokerConfig broker;
    uint32_t requestTimeoutMs = 60000;   // client: a request that got no verdict is dropped after this
    uint32_t statusEveryMs = 1000;       // client: combat report refresh
    uint32_t statusTtlMs = 5000;         // host: a combat report older than this no longer counts
    uint32_t helloRetryMs = 3000;        // client: asks for a beacon this often until one arrived
};

struct SessionEnv {
    IWorldTimeEngine* engine = nullptr;
    std::function<void(PlayerId to, Bytes data)> send;   // host: to a slot (kNoPlayer = every joined client); client: to the host
    PartyView party;                                      // host: the whole view; client: only tethered() is read
    ConsentBroker::AskFn ask;                             // host: raise the party question (L8 raiseModal in production)
    std::function<void(const fnvcoop::Effect&)> record;   // host: story effects (AdvanceTime) into the capture path
    std::function<bool()> localInCombat;                  // client: reported to the host about once a second (rest and party travel need it)
};

class WorldTimeSession {
public:
    WorldTimeSession(Role role, PlayerId self, SessionConfig cfg, SessionEnv env);

    void frame(uint64_t nowMs);
    void onPacket(PlayerId from, const Bytes& data, uint64_t nowMs);
    void onPlayerJoined(PlayerId slot, uint64_t nowMs);   // host
    void onPlayerLeft(PlayerId slot, uint64_t nowMs);     // host
    bool onPromptResult(uint32_t promptId, bool confirmed, bool timedOut, uint64_t nowMs);   // host: the consent layer answered
    void onAdvanceTimeEffect(uint32_t minutes, uint64_t nowMs);   // client: the AdvanceTime story effect arrived
    void onHostLoaded();                                           // host: a save was loaded, the clock is arbitrary now

    // The local player tries to wait / sleep / fast travel. true = let the game do it; false = vetoed (an answer follows).
    bool onLocalRest(ReqKind kind, uint32_t minutes, uint64_t nowMs);
    bool onLocalTravel(const FormRef& marker, uint64_t nowMs);

    void setTravelMode(TravelMode m);
    Role role() const { return role_; }
    PlayerId self() const { return self_; }
    ClockAuthority& authority() { return authority_; }
    ClockFollower& follower() { return follower_; }
    ConsentBroker& broker() { return broker_; }
    bool awaitingAnswer() const { return awaiting_.active; }
    uint32_t reportedCombat() const;   // host: slots whose client said "in combat" recently
    bool partyTravel() const { return role_ == Role::Host ? cfg_.broker.travel == TravelMode::Party : partyTravel_; }

    struct Stats {
        uint32_t beaconsSent = 0, beaconsRecv = 0, badPackets = 0, unexpected = 0, snaps = 0, advances = 0, skipsExecuted = 0,
                 requestsHandled = 0, duplicateRequests = 0, verdictsSent = 0, verdictsRecv = 0, travelsStarted = 0, helloSent = 0,
                 requestsTimedOut = 0, statusSent = 0, statusRecv = 0, scriptedRepeatsUndone = 0;
    };
    const Stats& stats() const { return stats_; }

private:
    static PartyView wrapParty(PartyView pv, WorldTimeSession* self);
    void hostPacket(PlayerId from, const Msg& m, uint64_t nowMs);
    void clientPacket(PlayerId from, const Msg& m, uint64_t nowMs);
    void hostRequest(PlayerId from, ReqKind kind, uint32_t minutes, const FormRef& marker, uint32_t extId, uint64_t nowMs);
    void onResolved(const Resolved& r);
    void executeSkip(uint32_t minutes);
    void sendTo(PlayerId to, const Msg& m);
    void broadcastBeacon(const Beacon& b);
    void sendVerdict(PlayerId to, uint32_t extId, ReqKind kind, bool granted, Reason why, uint32_t minutes, bool party);
    void clientAsk(const Msg& m, uint32_t reqId, ReqKind kind, const FormRef& marker, uint64_t nowMs);

    Role role_;
    PlayerId self_;
    SessionConfig cfg_;
    SessionEnv env_;
    ClockAuthority authority_;
    ClockFollower follower_;
    ConsentBroker broker_;
    uint64_t now_ = 0;

    // host
    uint32_t joinedClients_ = 0;
    std::map<PlayerId, uint32_t> lastReq_;       // highest client request id seen per slot (duplicates / reordering)
    std::map<uint32_t, uint32_t> extId_;         // broker request id -> the client's own request id
    struct Report { bool inCombat = false; uint64_t atMs = 0; };
    std::map<PlayerId, Report> reported_;
    // client
    bool partyTravel_ = false;
    uint32_t nextReq_ = 0;
    uint64_t lastHelloMs_ = 0;
    bool helloSent_ = false;
    bool lastSentCombat_ = false;
    uint64_t lastStatusMs_ = 0;
    bool statusSent_ = false;
    struct Awaiting {
        bool active = false;
        uint32_t reqId = 0;
        ReqKind kind = ReqKind::Wait;
        FormRef marker;
        uint64_t deadlineMs = 0;
    } awaiting_;
    Stats stats_;
};

}  // namespace nvwt
