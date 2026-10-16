#include "coop_runtime.h"

#include <algorithm>
#include <chrono>
#include <cstdlib>
#include <random>
#include <sstream>

#include "common/minijson.h"
#include "effect_json.h"

// Mutation twins (tests build the runtime with these to prove the integration tests can fail):
//   COOP_MUTANT=1 : client sink is not wired (received effects never reach the world)
//   COOP_MUTANT=2 : host capture hook not wired (captured txns are never committed)
//   COOP_MUTANT=3 : host ignores guest actions (onAction never executes)
//   COOP_MUTANT=4 : client skips setBaselineWorld (joins without a baseline transfer)
#ifndef COOP_MUTANT
#define COOP_MUTANT 0
#endif

namespace nvcoop::coop {
using nvcoop::json::Value;
using namespace fnvcoop;

const char* roleName(Role r) { return r == Role::Host ? "host" : r == Role::Client ? "client" : "off"; }

namespace {
std::string trim(const std::string& s) {
    size_t b = s.find_first_not_of(" \t\r\n"), e = s.find_last_not_of(" \t\r\n");
    return b == std::string::npos ? std::string() : s.substr(b, e - b + 1);
}
std::string lower(std::string s) {
    for (char& c : s) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    return s;
}
bool parseBool(const std::string& v, bool* out) {
    std::string l = lower(v);
    if (l == "1" || l == "true" || l == "yes" || l == "on") { *out = true; return true; }
    if (l == "0" || l == "false" || l == "no" || l == "off") { *out = false; return true; }
    return false;
}
bool parseU64(const std::string& v, uint64_t* out) {
    if (v.empty()) return false;
    char* end = nullptr;
    unsigned long long n = std::strtoull(v.c_str(), &end, 0);
    if (*end) return false;
    *out = n;
    return true;
}
uint64_t randomToken() {
    std::random_device rd;
    uint64_t t = (uint64_t(rd()) << 32) ^ rd();
    return t ? t : 1;
}
uint32_t sessionId() {
    uint64_t ms = std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::system_clock::now().time_since_epoch()).count();
    uint32_t s = static_cast<uint32_t>(ms / 10);  // strictly increasing across reconnects >10 ms apart
    return s ? s : 1;
}
}  // namespace

bool ParseConfig(const std::string& text, Config* out, std::string* err) {
    Config c = *out;
    std::istringstream in(text);
    std::string line, section;
    int n = 0;
    auto bad = [&](const std::string& m) { if (err) *err = "line " + std::to_string(n) + ": " + m; return false; };
    while (std::getline(in, line)) {
        ++n;
        line = trim(line);
        if (line.empty() || line[0] == ';' || line[0] == '#') continue;
        if (line[0] == '[') { size_t e = line.find(']'); section = lower(e == std::string::npos ? line.substr(1) : line.substr(1, e - 1)); continue; }
        if (section != "coop") continue;
        size_t eq = line.find('=');
        if (eq == std::string::npos) continue;
        std::string k = lower(trim(line.substr(0, eq))), v = trim(line.substr(eq + 1));
        uint64_t u = 0;
        if (k == "enable") { if (!parseBool(v, &c.enable)) return bad("enable must be 0/1"); }
        else if (k == "role") {
            std::string r = lower(v);
            if (r == "host") c.role = Role::Host;
            else if (r == "client" || r == "guest") c.role = Role::Client;
            else if (r == "off" || r.empty()) c.role = Role::Off;
            else return bad("role must be host|client|guest|off");
        }
        else if (k == "listen") c.listen = v;
        else if (k == "host" || k == "hostaddress") c.host = v;
        else if (k == "port") { if (!parseU64(v, &u) || u == 0 || u > 65535) return bad("port out of range"); c.port = static_cast<uint16_t>(u); }
        else if (k == "transport") { std::string t = lower(v); if (t != "enet" && t != "udp") return bad("transport must be enet|udp"); c.transport = t; }
        else if (k == "forward_activate") { if (!parseBool(v, &c.forwardActivate)) return bad("forward_activate must be 0/1"); }
        else if (k == "clientload") { std::string t = lower(v); if (t != "rejoin" && t != "block") return bad("clientload must be rejoin|block"); c.clientLoadBlock = t == "block"; }
        else if (k == "rules") c.rulesFile = v;
        else if (k == "handshake") { if (!parseBool(v, &c.handshake)) return bad("handshake must be 0/1"); }
        else if (k == "worker") { if (!parseBool(v, &c.useWorker)) return bad("worker must be 0/1"); }
        else if (k == "dialogue") { if (!parseBool(v, &c.dialogue)) return bad("dialogue must be 0/1"); }
        else if (k == "baseline") { if (!parseBool(v, &c.baseline)) return bad("baseline must be 0/1"); }
        else if (k == "token") { if (!parseU64(v, &u)) return bad("token must be a number"); c.token = u; }
        else if (k == "capture") { if (!parseBool(v, &c.captureEnabled)) return bad("capture must be 0/1"); }
        else if (k == "apply") { if (!parseBool(v, &c.applyEnabled)) return bad("apply must be 0/1"); }
        else if (k == "maxplayers") { if (!parseU64(v, &u) || u < 1 || u > kMaxSlots) return bad("maxPlayers 1..8"); c.maxPlayers = static_cast<uint32_t>(u); }
    }
    *out = c;
    return true;
}

// ---- client sink ----------------------------------------------------------------------------------------------
class Runtime::Sink : public IEffectSink {
public:
    explicit Sink(Runtime& rt) : rt_(rt) {}
    void apply(const Effect& e, const Context& ctx) override {
        if (!open_ || cur_.seq != ctx.seq || ctx.index == 0) {
            flush();
            cur_ = LogEntry();
            cur_.dir = ctx.repair ? "repair" : ctx.baseline ? "baseline" : "apply";
            cur_.seq = ctx.seq;
            cur_.origin = ctx.origin;
            if (ctx.cause) cur_.cause = *ctx.cause;
            cur_.atMs = rt_.now_();
            open_ = true;
        }
        cur_.effects.push_back(e);
        ApplyResult r;
#if COOP_MUTANT == 1
        r.status = "MutantNotWired";
#else
        if (rt_.cfg_.applyEnabled && rt_.world_) r = rt_.world_->apply(e, ctx);
        else r.status = "ApplyDisabled";
#endif
        if (ctx.baseline) ++rt_.counters_.baselineEffects;
        {
            std::string key = std::string(ctx.repair ? "repair " : ctx.baseline ? "baseline " : ctx.seq == 0 ? "snapshot " : "live ") + effectName(e) + ":" + r.status.substr(0, r.status.find(':'));
            ++rt_.tally_[key];
            if (!r.ok && rt_.firstErrors_.size() < 12 && r.status.find("Unsupported") == std::string::npos) {
                FormRef q;
                rt_.firstErrors_.push_back(key + " | " + r.status + " | " + effectName(e));
            }
        }
        if (r.ok) ++rt_.counters_.applied; else ++rt_.counters_.applyFailed;
        cur_.results.push_back(r.status);
    }
    void onJoined(PlayerId slot, const SharePolicy& p) override {
        if (rt_.world_) rt_.world_->onJoined(slot, p);
    }
    void onBaselineComplete(const BaselineResult& r) override {
        flush();
        LogEntry le;
        le.dir = "baselineDone";
        le.atMs = rt_.now_();
        le.results.push_back("effects=" + std::to_string(r.effectsApplied) + " reverted=" + std::to_string(r.reverted) +
                             " unrevertable=" + std::to_string(r.unrevertable) + " kept=" + std::to_string(r.entriesKept));
        rt_.pushLog(std::move(le));
    }
    void flush() {
        if (open_) { rt_.pushLog(std::move(cur_)); open_ = false; }
    }

private:
    Runtime& rt_;
    LogEntry cur_;
    bool open_ = false;
};

// ---- runtime --------------------------------------------------------------------------------------------------
Runtime::Runtime(Config cfg, nvhooks::CaptureHub* hub, IWorldApplier* world, std::function<uint64_t()> nowMs)
    : cfg_(std::move(cfg)), hub_(hub), world_(world), now_(std::move(nowMs)) {
    installSink_ = [hub](nvhooks::CaptureHub::Sink s) { if (hub) hub->addSink(std::move(s)); };  // L5 keeps slot 0 (bridge capture.end)
}

Runtime::~Runtime() { stop(); }

void Runtime::pushLog(LogEntry e) {
    if (onLog) onLog(e);
    log_.push_back(std::move(e));
    while (log_.size() > cfg_.logCapacity) log_.pop_front();
}

void Runtime::onCaptured(EffectTxn&& t) {
    ++counters_.captured;
    if (!captureOn_) return;
    // the rules engine keys on the OWNING QUEST: an INFO result script's cause.source is the INFO, map it to its quest
    if (t.cause.kind == CauseKind::DialogueResult && infoQuest_) {
        FormRef q;
        if (infoQuest_(t.cause.source, q)) t.cause.source = q;
    }
#if COOP_MUTANT == 2
    (void)t;
    return;
#else
    if (!host_) return;
    uint64_t seq = host_->commit(actionOrigin_ != kNoPlayer ? actionOrigin_ : t.origin, t.cause, std::move(t.effects), t.flags, t.originEpoch, t.originIntent);
    if (seq) ++counters_.committed; else ++counters_.droppedEmpty;
#endif
}

bool Runtime::makeTransport(bool server, std::string* err) {
    if (cfg_.transport == "udp") {
        auto t = server ? UdpTransport::Server(cfg_.listen, cfg_.port, err) : UdpTransport::Client(cfg_.host, cfg_.port, err);
        if (!t) return false;
        udp_ = t.get();
        transport_ = std::move(t);
        return true;
    }
    auto t = std::make_unique<fnvcoop::EnetTransport>();
    // ClientSession keeps sending Hello until Welcome, so it is fine that the ENet connect completes asynchronously.
    bool ok = server ? t->listen(cfg_.port, 8, cfg_.listen == "127.0.0.1" || cfg_.listen == "localhost") : t->connect(cfg_.host, cfg_.port);
    if (!ok) { if (err) *err = "enet " + std::string(server ? "listen" : "connect") + " failed"; return false; }
    enet_ = t.get();
    transport_ = std::move(t);
    return true;
}

bool Runtime::start(std::string* err) {
    if (running_) return true;
    std::string e;
    startMs_ = now_();
    if (cfg_.role == Role::Host) {
        if (!makeTransport(true, &e)) { lastError_ = "host transport: " + e; if (err) *err = lastError_; return false; }
        mux_.reset(new MuxTransport(*transport_));
        SharePolicy pol;
        pol.maxPlayers = static_cast<uint8_t>(cfg_.maxPlayers);
        if (cfg_.clientLoadBlock) pol.clientLoad = SharePolicy::ClientLoad::Block;
        host_.reset(new HostSession(*mux_, pol));
        host_->onCommit = [this](const EffectTxn& t) {
            LogEntry le;
            le.dir = "commit";
            le.seq = t.seq;
            le.origin = t.origin;
            le.cause = t.cause;
            le.effects = t.effects;
            le.atMs = now_();
            pushLog(std::move(le));
        };
        if (cfg_.baseline) {
            if (!dumper_) { lastError_ = "baseline=1 but no world dumper"; if (err) *err = lastError_; return false; }
            ShadowState b;
            uint64_t hash = 0;
            std::string derr;
            if (!dumper_->dump(&b, &hash, &derr)) { lastError_ = "baseline dump: " + derr; if (err) *err = lastError_; return false; }
            host_->setWorldBaseline(b, hash);
            usedBaseline_.reset(new ShadowState(b));
            usedHash_ = hash;
        }
        {
            HostSession::DesyncOptions dopt;
            dopt.compareClasses = cfg_.compareClasses;
            host_->setDesyncOptions(dopt);
        }
        replicator_.reset(new StateReplicator(*host_));
        if (!haveRules_ && !cfg_.rulesFile.empty()) {
            std::string rerr;
            if (!rulesDb_.loadFile(cfg_.rulesFile, &rerr)) { lastError_ = "rules: " + rerr; if (err) *err = lastError_; return false; }
            haveRules_ = true;
        }
        if (haveRules_) {
            EdidResolver res = edid_ ? edid_ : EdidResolver([](const std::string&, FormRef&) { return false; });
            RulesEngine::Options ropt;
            ropt.playerRef = [this](PlayerId slot, FormRef& out) {
                if (playerRef_) return playerRef_(slot, out);
                if (slot == kHostPlayer) { out = FormRef("falloutnv.esm", 0x14); return true; }   // the host's own player ref
                return false;                                                                    // guests: needs a ghost ref (L6)
            };
            rules_.reset(new RulesEngine(*host_, rulesDb_, res, ropt));
            rules_->applyStage = [this](const FormRef& q, uint32_t st) {
                if (!stageApplier_) return;
                applyingDeferred_ = true;
                stageApplier_(q, st);
                applyingDeferred_ = false;
            };
        }
        if (cfg_.useWorker) { worker_.reset(new ThreadWorker()); host_->setWorker(worker_.get()); }
        if (cfg_.handshake) {
            HandshakeInfo hs = hsBase_;
            if (haveRules_) hs.rulesHash = rulesDb_.textHash();
            hs.volatileHash = volatileKeys().contentHash();
            host_->setHandshake(hs);
        }
        host_->onAction = [this](PlayerId slot, IntentKind kind, const FormRef& ref, uint32_t, uint32_t) {
            ++counters_.actions;
            if (rules_ && rules_->handleAction(slot, kind, ref)) { ++counters_.rulesActions; return; }
#if COOP_MUTANT == 3
            (void)slot; (void)kind; (void)ref;   // twin: host ignores guest actions
#else
            if (!executor_) return;
            actionOrigin_ = slot;
            if (actingHook_) actingHook_(slot, true);
            executor_(slot, kind, ref);
            if (actingHook_) actingHook_(slot, false);
            actionOrigin_ = kNoPlayer;
#endif
        };
        if (hub_ && cfg_.captureEnabled) {
            if (!sinkInstalled_) {
                sinkInstalled_ = true;  // sinks cannot be removed from the hub: install once, gate with captureOn_
                std::weak_ptr<bool> alive = alive_;  // the hub outlives us in-game; never call into a dead Runtime
                installSink_([this, alive](EffectTxn&& t) { if (!alive.expired()) onCaptured(std::move(t)); });
            }
            hub_->setEnabled(true);
            captureOn_ = true;
        }
    } else if (cfg_.role == Role::Client) {
        if (!makeTransport(false, &e)) { lastError_ = "client transport: " + e; if (err) *err = lastError_; return false; }
        if (!cfg_.token) cfg_.token = randomToken();
        mux_.reset(new MuxTransport(*transport_));
        sink_.reset(new Sink(*this));
        client_.reset(new ClientSession(*mux_, 0, cfg_.token, *sink_, hub_ ? &hub_->echo() : nullptr));
#if COOP_MUTANT != 4   // twin 4: client never hands its own world to the session -> no baseline transfer
        if (cfg_.baseline) {
            if (!dumper_) { lastError_ = "baseline=1 but no world dumper"; if (err) *err = lastError_; return false; }
            ShadowState c;
            uint64_t hash = 0;
            std::string derr;
            if (!dumper_->dump(&c, &hash, &derr)) { lastError_ = "baseline dump: " + derr; if (err) *err = lastError_; return false; }
            client_->setBaselineWorld(c, hash);
        }
#endif
#if COOP_MUTANT != 4
        if (dumper_) {   // Rebase / loading a save without a usable sidecar re-dumps the CURRENT world
            IWorldDumper* d = dumper_;
            client_->setBaselineProvider([d](ShadowState& w, uint64_t& h) { std::string e; return d->dump(&w, &h, &e); });
        }
#endif
        client_->setDigestInterval(cfg_.digestIntervalMs);
        if (cfg_.useWorker) { worker_.reset(new ThreadWorker()); client_->setWorker(worker_.get()); }
        if (cfg_.handshake) {
            HandshakeInfo hs = hsBase_;
            if (!cfg_.rulesFile.empty() && !haveRules_) {   // the host compares rules.json text hashes: hash ours the same way
                std::string rerr;
                if (rulesDb_.loadFile(cfg_.rulesFile, &rerr)) haveRules_ = true;
                else { lastError_ = "rules: " + rerr; if (err) *err = lastError_; return false; }
            }
            if (haveRules_) hs.rulesHash = rulesDb_.textHash();
            hs.volatileHash = volatileKeys().contentHash();
            client_->setHandshake(hs);
        }
        client_->connect(now_(), sessionId());
    } else {
        lastError_ = "role is off";
        if (err) *err = lastError_;
        return false;
    }
    if (dlgEnabled_ && mux_) {
        mux_->registerChannel(kChannelDialogue, [this](PeerId from, Bytes&& d) {
            if (dlg_) dlg_->onPacket(from, d, static_cast<uint32_t>(now_()));
        });
    }
    running_ = true;
    return true;
}

void Runtime::stop() {
    if (!running_) return;
    if (enet_) enet_->disconnect();   // graceful: peers see Disconnected now instead of a timeout
    if (cfg_.role == Role::Host) captureOn_ = false;
    rules_.reset();
    replicator_.reset();
    client_.reset();
    host_.reset();
    worker_.reset();   // joins the thread after queued jobs finished (sessions are gone: no result will be drained)
    dlg_.reset();
    dlgJoined_.clear();
    dlgWasLive_ = false;
    client_.reset();
    sink_.reset();
    host_.reset();
    mux_.reset();
    transport_.reset();
    udp_ = nullptr;
    enet_ = nullptr;
    running_ = false;
}

Bytes Runtime::saveSidecar() {
    Bytes b;
    if (host_) b = host_->saveSidecar();
    else if (client_ && client_->canSaveState()) b = client_->saveState();
    if (!b.empty()) ++counters_.saves;
    LogEntry le;
    le.dir = "save";
    le.atMs = now_();
    le.results.push_back("sidecar bytes=" + std::to_string(b.size()));
    pushLog(std::move(le));
    return b;
}

bool Runtime::onGameLoaded(const Bytes* sidecar, std::string* what) {
    std::string w;
    bool ok = false;
    if (!running_) { w = "runtime not running"; }
    else if (!cfg_.baseline) { w = "save/load handling is off (baseline=0)"; }
    else if (host_) {
        if (sidecar && !sidecar->empty() && host_->loadSidecar(*sidecar)) { ok = true; w = "host: sidecar restored"; }
        else if (dumper_) {
            ShadowState st;
            uint64_t hash = 0;
            std::string derr;
            if (dumper_->dump(&st, &hash, &derr) && host_->reloadWorld(st, hash)) { ok = true; w = "host: reloaded world from a fresh engine dump"; }
            else w = "host: reload failed: " + derr;
        } else w = "host: no sidecar and no dumper";
        if (ok) { usedBaseline_.reset(new ShadowState(host_->world())); }
        if (ok) w += " (epoch " + std::to_string(host_->epoch()) + ")";
    } else if (client_) {
        if (!client_->clientLoadAllowed()) { w = "client: load refused by host policy (clientload=block)"; ++counters_.loadsBlocked; }
        else if (client_->onLocalSaveLoaded(now_(), sidecar && !sidecar->empty() ? sidecar : nullptr)) {
            ok = true;
            w = sidecar && !sidecar->empty() ? "client: resumed from sidecar or re-baselining" : "client: re-baselining (no sidecar)";
        } else { w = "client: onLocalSaveLoaded refused"; ++counters_.loadsBlocked; }
    }
    if (ok && replicator_) replicator_->clear();   // queued writes belong to the replaced world
    if (ok) ++counters_.loads;
    LogEntry le;
    le.dir = "load";
    le.atMs = now_();
    le.results.push_back(w);
    pushLog(std::move(le));
    if (what) *what = w;
    return ok;
}

bool Runtime::setRules(const std::string& jsonText, std::string* err) {
    if (!rulesDb_.load(jsonText, err)) return false;
    haveRules_ = true;
    return true;
}

bool Runtime::stageRequested(const FormRef& quest, uint32_t stage, PlayerId initiator) {
    if (!rules_ || applyingDeferred_) return true;
    return rules_->onStageRequested(quest, stage, initiator) == StageDecision::Proceed;
}

void Runtime::reportCell(const FormRef& cell) {
    if (rules_) rules_->onCellReported(kHostPlayer, cell);
    else if (client_ && client_->joined() && !client_->baselineActive()) client_->submitAction(IntentKind::ReportCell, cell);
}

void Runtime::scenePass(const FormRef& quest) {
    if (rules_) rules_->onPlayerPassed(kHostPlayer, quest);
    else if (client_ && client_->joined() && !client_->baselineActive()) client_->submitAction(IntentKind::ScenePass, quest);
}

bool Runtime::saveSuppressed() const { return client_ && client_->state().saveSuppress; }

uint32_t Runtime::submitAction(IntentKind kind, const FormRef& ref) {
    if (!client_ || !client_->joined() || client_->baselineActive()) return 0;
    return client_->submitAction(kind, ref);
}

void Runtime::tickDialogue() {
    if (!dlgEnabled_) return;
    uint32_t now = static_cast<uint32_t>(now_());
    if (host_) {
        if (!dlg_) {
            fnvcoop_dlg::DialogueNode::Config c;
            c.self = kHostPlayer;
            c.isHost = true;
            c.appliedSeq = [this]() -> uint64_t { return host_ ? host_->log().head() : 0; };
            c.peerOfSlot = [this](PlayerId p) {
                for (auto& cv : host_->clients()) if (cv.slot == p) return cv.peer;
                return PeerId(~0u);
            };
            c.slotOfPeer = [this](PeerId peer) {
                for (auto& cv : host_->clients()) if (cv.peer == peer && cv.joined) return cv.slot;
                return kNoPlayer;
            };
            dlg_.reset(new fnvcoop_dlg::DialogueNode(mux_.get(), c, dlgHooks_.run, dlgHooks_.sink));
        }
        std::set<PlayerId> joinedNow;
        for (auto& cv : host_->clients()) if (cv.joined) joinedNow.insert(cv.slot);
        for (PlayerId p : joinedNow) if (!dlgJoined_.count(p)) dlg_->playerJoined(p, now);
        for (PlayerId p : dlgJoined_) if (!joinedNow.count(p)) dlg_->playerLeft(p, now);
        dlgJoined_ = joinedNow;
    } else if (client_ && client_->joined() && !client_->baselineActive()) {
        if (!dlg_) {
            fnvcoop_dlg::DialogueNode::Config c;
            c.self = client_->slot();
            c.isHost = false;
            c.hostPeer = 0;
            c.appliedSeq = [this]() -> uint64_t { return client_ ? client_->applied() : 0; };
            dlg_.reset(new fnvcoop_dlg::DialogueNode(mux_.get(), c, nullptr, dlgHooks_.sink));
        }
    }
    if (dlg_ && client_) {   // the story session (re)joined or finished a (re)baseline: drop any stale conversation role
        bool live = client_->joined() && !client_->baselineActive();
        if (live && !dlgWasLive_) dlg_->resync(now);
        dlgWasLive_ = live;
    }
    if (dlg_) dlg_->tick(now);
}

void Runtime::frame() {
    if (!running_) return;
    ++counters_.frames;
    uint64_t t = now_();
    if (host_) {
        host_->tick(t);
        if (enet_) {
            fnvcoop::EnetTransport::Event ev;
            while (enet_->nextEvent(ev)) {
                if (ev.type == fnvcoop::EnetTransport::Event::Disconnected) { ++counters_.disconnects; host_->peerDisconnected(ev.peer); }
            }
        }
        if (rules_) rules_->tick(t);
        if (replicator_) replicator_->flush(t);
    }
    if (client_) {
        client_->tick(t);
        if (sink_) sink_->flush();
    }
    tickDialogue();
}

bool Runtime::inject(const std::string& txnJson, std::string* resultJson, std::string* err) {
    Value p;
    std::string perr;
    if (!nvcoop::json::parse(txnJson, p, perr)) { if (err) *err = "bad json: " + perr; return false; }
    const Value& v = p.get("txn").isObject() ? p.get("txn") : p;
    EffectTxn txn;
    if (!nvhooks::txnFromJson(v, &txn, &perr)) { if (err) *err = "bad txn: " + perr; return false; }
    if (!running_) { if (err) *err = "coop runtime not running"; return false; }
    Value o = Value::object();
    ++counters_.injected;
    if (host_) {
        uint64_t seq = host_->commit(txn.origin, txn.cause, std::move(txn.effects));
        o.set("committed", seq != 0).set("seq", (int64_t)seq);
    } else {
        // Deliver as if received: same sink + applier path as a real txn, under the echo guard.
        Sink sink(*this);
        EchoGuard guard(hub_ ? &hub_->echo() : nullptr);
        IEffectSink::Context ctx;
        ctx.origin = txn.origin;
        ctx.cause = &txn.cause;
        size_t idx = 0;
        uint64_t ok0 = counters_.applied, bad0 = counters_.applyFailed;
        for (const Effect& e : txn.effects) { ctx.index = idx++; sink.apply(e, ctx); }
        sink.flush();
        // flush() logged it as "apply"; relabel as inject
        if (!log_.empty()) log_.back().dir = "inject";
        o.set("effects", (int64_t)txn.effects.size()).set("applied", (int64_t)(counters_.applied - ok0))
            .set("failed", (int64_t)(counters_.applyFailed - bad0));
    }
    if (resultJson) *resultJson = o.dump();
    return true;
}

static Value entryToJson(const LogEntry& le) {
    EffectTxn t;
    t.origin = le.origin;
    t.cause = le.cause;
    t.effects = le.effects;
    Value o = nvhooks::txnToJson(t);
    o.set("dir", le.dir).set("seq", (int64_t)le.seq).set("atMs", (int64_t)le.atMs);
    if (!le.results.empty()) {
        Value r = Value::array();
        for (auto& s : le.results) r.push(s);
        o.set("results", r);
    }
    return o;
}

std::string Runtime::logJson(size_t maxEntries) const {
    Value a = Value::array();
    size_t start = log_.size() > maxEntries ? log_.size() - maxEntries : 0;
    for (size_t i = start; i < log_.size(); ++i) a.push(entryToJson(log_[i]));
    Value o = Value::object();
    o.set("count", (int64_t)log_.size()).set("txns", a);
    return o.dump();
}

std::string Runtime::statusJson() const {
    Value o = Value::object();
    o.set("role", roleName(cfg_.role)).set("enabled", cfg_.enable).set("running", running_).set("mutant", (int64_t)COOP_MUTANT)
        .set("port", (int64_t)boundPort()).set("error", lastError_);
    Value c = Value::object();
    c.set("frames", (int64_t)counters_.frames).set("captured", (int64_t)counters_.captured).set("committed", (int64_t)counters_.committed)
        .set("droppedEmpty", (int64_t)counters_.droppedEmpty).set("applied", (int64_t)counters_.applied)
        .set("applyFailed", (int64_t)counters_.applyFailed).set("injected", (int64_t)counters_.injected)
        .set("saves", (int64_t)counters_.saves).set("loads", (int64_t)counters_.loads).set("loadsBlocked", (int64_t)counters_.loadsBlocked)
        .set("actions", (int64_t)counters_.actions).set("baselineEffects", (int64_t)counters_.baselineEffects);
    o.set("counters", c);
    if (udp_) {
        const auto& s = udp_->stats();
        Value t = Value::object();
        t.set("kind", "udp").set("datagramsSent", (int64_t)s.datagramsSent).set("datagramsRecv", (int64_t)s.datagramsRecv)
            .set("messagesSent", (int64_t)s.messagesSent).set("messagesRecv", (int64_t)s.messagesRecv)
            .set("bad", (int64_t)s.badDatagrams).set("sendErrors", (int64_t)s.sendErrors);
        o.set("transport", t);
    } else if (enet_) {
        const auto& s = enet_->stats();
        Value t = Value::object();
        t.set("kind", "enet").set("datagramsIn", (int64_t)s.datagramsIn).set("datagramsOut", (int64_t)s.datagramsOut)
            .set("bytesIn", (int64_t)s.bytesIn).set("bytesOut", (int64_t)s.bytesOut).set("connected", enet_->connected());
        o.set("transport", t);
    }
    if (hub_) {
        const auto& s = hub_->stats();
        Value h = Value::object();
        h.set("enabled", hub_->enabled()).set("recorded", (int64_t)s.recorded).set("txns", (int64_t)s.txns)
            .set("droppedNoScope", (int64_t)s.droppedNoScope).set("droppedEcho", (int64_t)s.droppedEcho)
            .set("emptyScopes", (int64_t)s.emptyScopes);
        o.set("hub", h);
    }
    if (host_) {
        const auto& s = host_->stats();
        Value peers = Value::array();
        int joined = 0;
        for (auto& cv : host_->clients()) {
            Value p = Value::object();
            p.set("token", std::to_string(cv.token)).set("slot", (int64_t)cv.slot).set("joined", cv.joined).set("acked", (int64_t)cv.acked);
            if (cv.joined) ++joined;
            peers.push(p);
        }
        Value h = Value::object();
        h.set("lastSeq", (int64_t)host_->log().head()).set("peers", peers).set("connected", joined).set("allSynced", host_->allSynced())
            .set("commits", (int64_t)s.commits).set("droppedByPolicy", (int64_t)s.droppedByPolicy)
            .set("epoch", (int64_t)host_->epoch()).set("welcomes", (int64_t)s.welcomes).set("snapshots", (int64_t)s.snapshots).set("replays", (int64_t)s.replays)
            .set("retransmitBatches", (int64_t)s.retransmitBatches).set("badMessages", (int64_t)s.badMessages);
        if (replicator_) {
            const auto& rp = replicator_->stats();
            Value r = Value::object();
            r.set("writes", (int64_t)rp.writes).set("coalesced", (int64_t)rp.coalesced).set("skippedUnchanged", (int64_t)rp.skippedUnchanged)
                .set("effectsCommitted", (int64_t)rp.effectsCommitted).set("txns", (int64_t)rp.txns).set("deferredByBudget", (int64_t)rp.deferredByBudget)
                .set("volatileDropped", (int64_t)rp.volatileDropped).set("pending", (int64_t)replicator_->pending());
            o.set("replicator", r);
        }
        {
            Value dj;
            std::string derr;
            if (nvcoop::json::parse(host_->desyncsJson(), dj, derr)) o.set("desyncs", dj);
        }
        o.set("host", h);
        o.set("lastSeq", (int64_t)host_->log().head()).set("connected", joined);
    }
    if (client_) {
        const auto& s = client_->stats();
        Value c2 = Value::object();
        c2.set("joined", client_->joined()).set("rejected", client_->rejected()).set("slot", (int64_t)client_->slot())
            .set("applied", (int64_t)client_->applied()).set("txnsApplied", (int64_t)s.txnsApplied)
            .set("duplicatesDropped", (int64_t)s.duplicatesDropped).set("snapshots", (int64_t)s.snapshots)
            .set("replays", (int64_t)s.replays).set("token", std::to_string(cfg_.token))
            .set("worldEpoch", (int64_t)client_->worldEpoch()).set("rebases", (int64_t)s.rebases).set("staleEpochDropped", (int64_t)s.staleEpochDropped);
        c2.set("rejectText", client_->rejectText()).set("syncing", client_->baselineActive()).set("baselineDone", client_->baselineDone())
            .set("rejectReason", (int64_t)client_->rejectReason())
            .set("baselineEffects", (int64_t)client_->baselineResult().effectsApplied)
            .set("baselineKept", (int64_t)client_->baselineResult().entriesKept).set("baselineReverted", (int64_t)client_->baselineResult().reverted)
            .set("baselineUnrevertable", (int64_t)client_->baselineResult().unrevertable).set("baselineChunks", (int64_t)client_->baselineResult().chunks);
        o.set("client", c2);
        o.set("lastSeq", (int64_t)client_->applied()).set("connected", client_->joined() ? 1 : 0);
    }
    {
        Value ty = Value::object();
        for (auto& [k, n] : tally_) ty.set(k, (int64_t)n);
        o.set("effectTally", ty);
        Value fe = Value::array();
        for (auto& x : firstErrors_) fe.push(x);
        o.set("firstErrors", fe);
    }
    if (rules_) {
        const auto& rs = rules_->stats();
        Value r = Value::object();
        r.set("quests", (int64_t)rulesDb_.quests().size()).set("pendingDeferrals", (int64_t)rules_->pendingDeferrals())
            .set("stagesDeferred", (int64_t)rs.stagesDeferred).set("stagesReleased", (int64_t)rs.stagesReleased)
            .set("barrierTimeouts", (int64_t)rs.barrierTimeouts).set("arrivals", (int64_t)rs.arrivals)
            .set("recipientRewrites", (int64_t)rs.recipientRewrites).set("rulesActions", (int64_t)counters_.rulesActions);
        Value uk = Value::array();
        for (auto& k : rules_->unsupportedKinds()) uk.push(k);
        r.set("unsupportedKinds", uk);
        o.set("rules", r);
    }
    if (client_) {
        o.set("saveSuppress", client_->state().saveSuppress);
        if (client_->slot() < kMaxSlots) o.set("controlsOff", (int64_t)client_->state().ledger[client_->slot()].controlsOff);
    }
    o.set("volatileUnresolved", (int64_t)volatileKeys().unresolvedNames());
    o.set("logEntries", (int64_t)log_.size());
    return o.dump();
}

}  // namespace nvcoop::coop
