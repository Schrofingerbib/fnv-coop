// Co-op runtime (L10): glues synccore sessions, a transport, L5's CaptureHub and an engine-side world applier.
// Game-free: the engine is reached only through IWorldApplier (in-game: L5 RemoteApplier; tests: a fake world).
//
//   Host   : CaptureHub (outermost capture scope closes) -> HostSession::commit -> broadcast; tick() each frame.
//   Client : ClientSession -> IEffectSink -> IWorldApplier::apply (main thread); tick() each frame.
// Everything runs on the thread that calls frame() (the game main thread). No locks.
#pragma once
#include <cstdint>
#include <deque>
#include <functional>
#include <map>
#include <memory>
#include <set>
#include <string>
#include <vector>

#include "capture_hub.h"
#include "fnvcoop/baseline.h"
#include "fnvcoop/replicator.h"
#include "fnvcoop/rules.h"
#include "fnvcoop/session.h"
#include "fnvcoop/transport_util.h"
#include "fnvcoop/worker.h"
#include "fnvcoop_dlg/adapter.h"
#include "fnvcoop/enet_transport.h"
#include "udp_transport.h"

namespace nvcoop::coop {

enum class Role { Off, Host, Client };
const char* roleName(Role r);

struct Config {
    bool enable = false;            // [coop] enable=1 (default off: a stray nvcoop.dll must not open sockets)
    Role role = Role::Off;          // [coop] role=host|client|guest (guest == client)
    std::string listen = "127.0.0.1";  // host: bind address (use 0.0.0.0 to accept friends)
    std::string host = "127.0.0.1";    // client: host address
    uint16_t port = 27960;
    std::string transport = "enet";  // "enet" (default, L4) | "udp" (L10 stand-in, kept as a test double)
    uint64_t token = 0;             // client identity; 0 = derive from time/pid (set it in the ini for stable slots)
    bool captureEnabled = true;     // host: wire CaptureHub -> commit
    bool applyEnabled = true;       // client: wire sink -> world
    uint32_t maxPlayers = 4;
    bool forwardActivate = false;   // [coop] forward_activate=1: guest Activate -> submitAction to the host (suppresses the local call)
    bool clientLoadBlock = false;   // [coop] clientload=block: host policy, guests may not load saves while joined (default rejoin)
    std::string rulesFile;          // [coop] rules=<path to rules.json>: quest rules engine on the host (empty = off)
    uint8_t compareClasses = 0xDF;  // desync digest: entry classes the dump really covers (0..4 quest/stage/obj/vars/globals, 6 factions, 7 flags; 5 refs only with watch refs)
    uint32_t digestIntervalMs = 10000;   // client desync digest interval (0 = off)
    bool handshake = false;         // check build / rules / volatile / mod-list identity on join (the module turns it on; tests/fakes leave it off)
    bool useWorker = true;          // one background ThreadWorker for hashing / diffing (L4 setWorker)
    bool dialogue = false;          // [coop] dialogue=1: enable the 0xD1 dialogue channel
    bool baseline = false;          // [coop] baseline=1: join-in-progress via world baseline (needs an IWorldDumper)
    size_t logCapacity = 4096;
};

// Parses [coop] from ini text. Unknown keys ignored. Returns false (and *err) on a bad value.
bool ParseConfig(const std::string& iniText, Config* out, std::string* err);

struct ApplyResult {
    bool ok = false;
    std::string status;  // e.g. "Ok", "Unsupported", "FormMissing"
};

// Engine side of a client. Called on the main thread, inside the session's EchoGuard.
class IWorldApplier {
public:
    virtual ~IWorldApplier() = default;
    virtual ApplyResult apply(const fnvcoop::Effect& e, const fnvcoop::IEffectSink::Context& ctx) = 0;
    virtual void onJoined(fnvcoop::PlayerId /*slot*/, const fnvcoop::SharePolicy& /*policy*/) {}
};

// Engine dump of the shared world for the baseline join (L7 dumper in-game; fake world in tests). Main thread.
class IWorldDumper {
public:
    virtual ~IWorldDumper() = default;
    virtual bool dump(fnvcoop::ShadowState* out, uint64_t* contentHash, std::string* err) = 0;
};
// Host: run a requested ActivateRef/TriggerEnter/PickUp on the real engine. The runtime sets the origin of the
// resulting txn to `slot`; the executor opens its own CaptureHub scope so the effects are captured and committed.
using ActionExecutor = std::function<void(fnvcoop::PlayerId slot, fnvcoop::IntentKind kind, const fnvcoop::FormRef& ref)>;
struct DialogueHooks {
    fnvcoop_dlg::ResultRunner run;          // host: run the INFO result script under capture, return committed seq
    fnvcoop_dlg::DialogueNode::Sink sink;   // every node: engine glue (OpenMenu, ShowLines, ...)
};

using EdidResolver = std::function<bool(const std::string& edid, fnvcoop::FormRef& out)>;
using InfoQuestResolver = std::function<bool(const fnvcoop::FormRef& info, fnvcoop::FormRef& quest)>;
// Host: perform the REAL engine SetStage (capture open) when the rules engine releases a deferral.
using StageApplier = std::function<void(const fnvcoop::FormRef& quest, uint32_t stage)>;

struct LogEntry {
    std::string dir;  // "commit" (host), "apply" (client), "inject"
    uint64_t seq = 0;
    uint64_t atMs = 0;
    fnvcoop::PlayerId origin = 0;
    fnvcoop::Cause cause;
    std::vector<fnvcoop::Effect> effects;
    std::vector<std::string> results;  // client: per-effect apply status
};

class Runtime {
public:
    // hub may be null (client needs none; a host without it can still `inject`).
    Runtime(Config cfg, nvhooks::CaptureHub* hub, IWorldApplier* world, std::function<uint64_t()> nowMs);
    ~Runtime();

    // How the host receives outermost-scope txns. Default hub->addSink (slot 0 stays with L5's capture.end bridge).
    void setDumper(IWorldDumper* d) { dumper_ = d; }
    void setActionExecutor(ActionExecutor f) { executor_ = std::move(f); }
    // "Acting player" context (docs/protocol.md section 12): while the host runs a guest's interaction, the engine layer must resolve
    // `player` inside the target's script block to that guest. The hook is called (slot, true) before and (slot, false) after the executor.
    void setActingHook(std::function<void(fnvcoop::PlayerId slot, bool on)> f) { actingHook_ = std::move(f); }
    fnvcoop::PlayerId actingSlot() const { return actionOrigin_; }   // kNoPlayer outside an executor call
    void enableDialogue(DialogueHooks h) { dlgHooks_ = std::move(h); dlgEnabled_ = true; }  // before start()
    // Host with baseline=1: the exact dump handed to setWorldBaseline (null otherwise). Engine state drifts (timers),
    // so exports for tests must use THIS copy, not a fresh dump.
    const fnvcoop::ShadowState* usedBaseline(uint64_t* hash) const { if (hash) *hash = usedHash_; return usedBaseline_.get(); }
    fnvcoop_dlg::DialogueNode* dialogue() { return dlg_.get(); }  // null until the session is joined
    fnvcoop::MuxTransport* mux() { return mux_.get(); }
    // Client: ask the host to run an activation. Returns 0 (and does nothing) while syncing or not joined.
    uint32_t submitAction(fnvcoop::IntentKind kind, const fnvcoop::FormRef& ref);
    // ---- quest rules engine (L4): host side. Configure BEFORE start().
    bool setRules(const std::string& jsonText, std::string* err);    // or Config::rulesFile
    // Build identity for the join handshake (build number/hash/name, mod load order). rulesHash/volatileHash are filled by the runtime.
    void setHandshakeBase(const fnvcoop::HandshakeInfo& h) { hsBase_ = h; }
    // PartyWarp (rules engine): slot -> that player's ref. Default: slot 0 = the host player (0x14 of falloutnv.esm), guests via the ghost
    // resolver below (none -> the slot is skipped). Evaluated lazily on every use, so it stays correct across loads / rebases.
    void setPlayerRefResolver(std::function<bool(fnvcoop::PlayerId, fnvcoop::FormRef&)> f) { playerRef_ = std::move(f); }
    void setRulesFile(const std::string& path) { cfg_.rulesFile = path; }
    void setEdidResolver(EdidResolver f) { edid_ = std::move(f); }
    void setInfoQuestResolver(InfoQuestResolver f) { infoQuest_ = std::move(f); }
    void setStageApplier(StageApplier f) { stageApplier_ = std::move(f); }
    fnvcoop::RulesEngine* rules() { return rules_.get(); }
    // Host: coalescing channel for quest script vars / globals polled from the engine (L5 pollHostState). null on clients.
    fnvcoop::StateReplicator* replicator() { return replicator_.get(); }
    // Host: the engine wants to advance `quest` to `stage`. false => DEFERRED (skip the engine call; stageApplier fires on release).
    // Calls made by the stage applier itself, and everything while no rules engine is loaded, proceed.
    bool stageRequested(const fnvcoop::FormRef& quest, uint32_t stage, fnvcoop::PlayerId initiator = 0);
    // Local player is now in `cell` (host: tells the rules engine; client: ReportCell action). No-op while syncing.
    void reportCell(const fnvcoop::FormRef& cell);
    // Local player finished their per-player step of `quest` (chargen...): host -> rules engine, client -> ScenePass action.
    void scenePass(const fnvcoop::FormRef& quest);
    bool saveSuppressed() const;   // client: shared state says saving is blocked

    // ---- save / load (L4 round 6). Game-free: the module maps NVSE SaveGame/PreLoadGame/PostLoadGame onto these.
    // Host: session sidecar (shared world + epoch + token map). Client: its own view (empty if !canSaveState()).
    fnvcoop::Bytes saveSidecar();
    // PostLoad: `sidecar` = the file stored with the loaded save (null/empty if none). Host: loadSidecar, else reloadWorld from a
    // fresh engine dump -> epoch+1, clients rebase. Client: onLocalSaveLoaded (resume from its sidecar or re-baseline). Returns false
    // when nothing could be done (client policy Block, or host without dumper). `what` describes the path taken.
    bool onGameLoaded(const fnvcoop::Bytes* sidecar, std::string* what);
    // Client: may a load happen now? (false once joined under clientload=block; the engine layer must refuse it)
    bool clientLoadAllowed() const { return !client_ || client_->clientLoadAllowed(); }

    // Client: world baseline transfer in progress -> block local gameplay/intents.
    bool syncing() const { return client_ && client_->baselineActive(); }

    void setSinkInstaller(std::function<void(nvhooks::CaptureHub::Sink)> f) { installSink_ = std::move(f); }

    bool start(std::string* err);  // opens the transport and builds the session
    void stop();
    bool running() const { return running_; }
    void frame();                  // one tick on the main thread

    // coop.inject: host = commit as if captured; client = deliver as if received (applied through the sink path).
    bool inject(const std::string& txnJson, std::string* resultJson, std::string* err);
    std::string statusJson() const;
    std::string logJson(size_t maxEntries) const;

    // Observer for every log entry (peer tooling prints these).
    std::function<void(const LogEntry&)> onLog;

    const Config& config() const { return cfg_; }
    fnvcoop::HostSession* host() { return host_.get(); }
    fnvcoop::ClientSession* client() { return client_.get(); }
    UdpTransport* transport() { return udp_; }            // null unless transport=udp
    fnvcoop::EnetTransport* enet() { return enet_; }      // null unless transport=enet
    uint16_t boundPort() const { return enet_ ? enet_->port() : udp_ ? udp_->localPort() : 0; }
    const std::string& lastError() const { return lastError_; }

    struct Counters {
        uint64_t frames = 0, captured = 0, committed = 0, droppedEmpty = 0;
        uint64_t applied = 0, applyFailed = 0, injected = 0, actions = 0, rulesActions = 0, disconnects = 0, baselineEffects = 0, saves = 0, loads = 0, loadsBlocked = 0;
    };
    const Counters& counters() const { return counters_; }

private:
    class Sink;
    bool makeTransport(bool server, std::string* err);
    void pushLog(LogEntry e);
    void onCaptured(fnvcoop::EffectTxn&& t);
    void tickDialogue();

    Config cfg_;
    nvhooks::CaptureHub* hub_;
    IWorldApplier* world_;
    std::function<uint64_t()> now_;
    std::function<void(nvhooks::CaptureHub::Sink)> installSink_;

    std::unique_ptr<fnvcoop::ITransport> transport_;
    UdpTransport* udp_ = nullptr;
    fnvcoop::EnetTransport* enet_ = nullptr;
    fnvcoop::HandshakeInfo hsBase_;
    std::unique_ptr<fnvcoop::ThreadWorker> worker_;
    fnvcoop::RulesDb rulesDb_;
    bool haveRules_ = false;
    std::unique_ptr<fnvcoop::RulesEngine> rules_;
    std::unique_ptr<fnvcoop::StateReplicator> replicator_;
    EdidResolver edid_;
    std::function<bool(fnvcoop::PlayerId, fnvcoop::FormRef&)> playerRef_;
    InfoQuestResolver infoQuest_;
    StageApplier stageApplier_;
    bool applyingDeferred_ = false;
    std::unique_ptr<fnvcoop::MuxTransport> mux_;
    IWorldDumper* dumper_ = nullptr;
    std::unique_ptr<fnvcoop::ShadowState> usedBaseline_;
    uint64_t usedHash_ = 0;
    ActionExecutor executor_;
    std::function<void(fnvcoop::PlayerId, bool)> actingHook_;
    DialogueHooks dlgHooks_;
    bool dlgEnabled_ = false;
    std::unique_ptr<fnvcoop_dlg::DialogueNode> dlg_;
    std::set<fnvcoop::PlayerId> dlgJoined_;
    bool dlgWasLive_ = false;
    fnvcoop::PlayerId actionOrigin_ = fnvcoop::kNoPlayer;
    std::unique_ptr<fnvcoop::HostSession> host_;
    std::unique_ptr<Sink> sink_;
    std::unique_ptr<fnvcoop::ClientSession> client_;
    std::deque<LogEntry> log_;
    std::map<std::string, uint64_t> tally_;   // "<baseline|snapshot|live> <Effect>:<status>" -> count (survives log eviction)
    std::vector<std::string> firstErrors_;
    Counters counters_;
    std::string lastError_;
    bool running_ = false;
    uint64_t startMs_ = 0;
    bool captureOn_ = false, sinkInstalled_ = false;
    std::shared_ptr<bool> alive_ = std::make_shared<bool>(true);
};

}  // namespace nvcoop::coop
