// LiveHub: the one place that knows the coop session and the game's load / save events, and tells every live-layer service about them.
// Game-free and unit-tested (tests/test_live_wiring.cpp); coop_module.cpp feeds it a SessionView every frame and forwards NVSE messages.
//
//   * A SERVICE is one live-layer part (actors, avatars, combat, spawns). It declares the mux channel bytes it owns, gets a send link when
//     a session begins, and is told about joins, leaves, host-link changes, loads and datagrams. A disabled service (ini switch off) is
//     never called at all.
//   * A SESSION begins when the coop runtime is running with a mux (a client also needs its slot or an up host link) and ends when it
//     stops, when the mux object changes (a new session) or when a client is re-slotted.
//   * Datagrams: the hub registers each service's channel bytes on the session's MuxTransport and routes them by sender slot. Datagrams from
//     peers that are not joined are dropped and counted. Handlers of an old session stay registered on the old mux but are inert.
//   * Loads happen with or without a session: loadBegin / loadEnd always reach the enabled services (puppets and stand-ins must be purged
//     when a save loads even if nobody is connected).
#pragma once
#include <cstdint>
#include <functional>
#include <memory>
#include <set>
#include <string>
#include <vector>

#include <exception>

#include "fnvcoop/transport.h"
#include "fnvcoop/transport_util.h"
#include "fnvcoop/types.h"
#include "fnvcoop_mut.h"   // FNV_MUT(n) (hub twins 11..23, constant false in the shipped DLL)

namespace nvcoop::livewire {

using fnvcoop::Bytes;
using fnvcoop::PlayerId;
using fnvcoop::kHostPlayer;
using fnvcoop::kNoPlayer;

struct ClientLink { PlayerId slot = kNoPlayer; fnvcoop::PeerId peer = 0; bool joined = false; };

// Everything the hub needs to know about the coop session this frame (built by coop_module.cpp from fnvcoop::Runtime).
struct SessionView {
    bool active = false;                    // the coop runtime is running
    bool isHost = false;
    PlayerId self = kNoPlayer;              // client: slot assigned by the host (kNoPlayer until joined); host: ignored (kHostPlayer)
    bool hostLinkUp = false;                // client: joined and not rebaselining
    std::vector<ClientLink> clients;        // host: the clients the session knows
    fnvcoop::MuxTransport* mux = nullptr;   // a new pointer = a new session
    uint64_t nowMs = 0;
};

struct SessionInfo { bool isHost = false; PlayerId self = kHostPlayer; };

// How a service sends. Client: `to` is ignored (the only peer is the host). Host: `to` is a joined client's slot.
// Returns false when the datagram could not be sent (no such joined slot, session over).
class ILiveLink {
public:
    virtual ~ILiveLink() = default;
    virtual bool send(PlayerId to, bool reliable, Bytes data) = 0;
    virtual bool isHost() const = 0;
    virtual PlayerId self() const = 0;
};

class ILiveService {
public:
    virtual ~ILiveService() = default;
    virtual const char* name() const = 0;
    // First bytes of the datagrams this service owns (every datagram it sends must start with one of them).
    virtual std::vector<uint8_t> channels() const { return {}; }

    virtual void sessionBegin(const SessionInfo&, ILiveLink&) {}   // the link stays valid until sessionEnd() returns
    virtual void sessionEnd() {}
    virtual void playerJoined(PlayerId) {}      // host: a client joined (also called for clients already joined when the session begins)
    virtual void playerLeft(PlayerId) {}        // host: a client left
    virtual void hostLinkUp() {}                // client: the link to the host is up (also right after sessionBegin if it already is)
    virtual void hostLinkDown() {}              // client: the link dropped (rebaseline, host gone)
    virtual void receive(PlayerId /*from*/, const uint8_t* /*data*/, size_t /*size*/) {}
    virtual void tick(uint64_t /*nowMs*/) {}    // once per frame, only while a session is up
    virtual void idleTick(uint64_t /*nowMs*/) {}   // once per frame while NO session is up (work that must go on without one: purging puppets after a load)

    virtual void loadBegin() {}                 // a save is about to load: the world is torn down (drop refs, nothing to call the engine for)
    virtual void loadEnd() {}                   // the loaded world is up: purge what must not survive a load, respawn what should
    virtual void saved() {}                     // NVSE SaveGame message (fires after the save was written)
    virtual void gameExit() {}                  // exit to menu / quit
};

struct HubStats {
    uint64_t frames = 0, sessionsBegun = 0, sessionsEnded = 0, joins = 0, leaves = 0, linkUps = 0, linkDowns = 0;
    uint64_t datagramsRouted = 0, droppedUnknownPeer = 0, droppedNoService = 0, droppedSessionOver = 0;
    uint64_t sendsOk = 0, sendsFailed = 0, loads = 0, serviceFaults = 0;
};

class LiveHub {
public:
    LiveHub();
    ~LiveHub();
    LiveHub(const LiveHub&) = delete;
    LiveHub& operator=(const LiveHub&) = delete;

    // Registers a service (the hub does not own it). Returns false when a channel byte it declares is already taken by another service
    // or the service is already registered. A disabled service is remembered for the status report and never called.
    bool add(ILiveService* svc, bool enabled);

    void frame(const SessionView& v);            // once per game frame, after the coop runtime ticked
    void onLoadBegin();                          // NVSE PreLoadGame
    void onLoadEnd();                            // NVSE PostLoadGame (successful)
    void onSaved();                              // NVSE SaveGame
    void onGameExit();                           // NVSE ExitToMainMenu / ExitGame

    bool inSession() const { return inSession_; }
    const HubStats& stats() const { return stats_; }
    size_t serviceCount() const { return services_.size(); }
    size_t enabledCount() const;
    std::string statusJson() const;

private:
    class Link;
    struct Entry { ILiveService* svc = nullptr; bool enabled = false; bool sessionOpen = false; std::vector<uint8_t> channels; std::string fault; };
    void fault(Entry& e, const char* what);
    template <class F> void safe(Entry& e, F&& f) {
        if (FNV_MUT(23)) { f(); return; }   // MUTANT 23: a throwing service is not contained (it would unwind into the game's frame)
        try {
            f();
        } catch (const std::exception& x) {
            fault(e, x.what());
        } catch (...) {
            fault(e, "unknown exception");
        }
    }
    void endSession();
    void beginSession(const SessionView& v);
    PlayerId slotOfPeer(fnvcoop::PeerId p) const;
    void route(uint64_t sessionId, fnvcoop::PeerId from, Bytes&& data);

    std::vector<Entry> services_;
    std::shared_ptr<uint64_t> liveSession_;      // id of the running session as seen by mux handlers (0 = none)
    uint64_t sessionSeq_ = 0;
    std::unique_ptr<Link> link_;
    bool inSession_ = false;
    fnvcoop::MuxTransport* mux_ = nullptr;
    bool isHost_ = false;
    PlayerId self_ = kNoPlayer;
    bool linkUp_ = false;
    std::vector<ClientLink> clients_;
    std::set<PlayerId> known_;                   // host: slots announced to the services as joined
    HubStats stats_;
};

}  // namespace nvcoop::livewire
