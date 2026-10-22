#include "intents/intent_service.h"

#include <sstream>

#include "fnvcoop_mut.h"

namespace nvcoop::intents {

IntentService::IntentService(ServiceDeps deps, Config cfg, ContainerRedirect redirect)
    : deps_(std::move(deps)), cfg_(std::move(cfg)), redirect_(std::move(redirect)) {}

IntentService::~IntentService() {
    *live_ = 0;
    if (up_ && isHost_ && rt_ && nvcoop::coop::Runtime::active() == rt_) rt_->setDialogueRunner(nullptr);   // the runner captured `this`
}

PlayerId IntentService::slotOfPeer(fnvcoop::PeerId peer) const {
    if (!rt_ || !rt_->host()) return kNoPlayer;
    for (const auto& cv : rt_->host()->clients())
        if (cv.peer == peer && cv.joined) return cv.slot;
    return kNoPlayer;
}

void IntentService::end(bool runtimeAlive) {
    if (!up_) return;
    if (runtimeAlive && isHost_ && rt_) rt_->setDialogueRunner(nullptr);   // back to whatever coop_module installed
    *live_ = 0;   // handlers registered on the old mux become no-ops
    host_.reset();
    client_.reset();
    joined_.clear();
    rt_ = nullptr;
    mux_ = nullptr;
    up_ = false;
}

void IntentService::begin(nvcoop::coop::Runtime* rt, fnvcoop::MuxTransport* mux, bool isHost) {
    rt_ = rt;
    mux_ = mux;
    isHost_ = isHost;
    up_ = true;
    ++sessions_;
    *live_ = ++gen_;
    const uint64_t gen = gen_;
    std::shared_ptr<uint64_t> live = live_;
    if (isHost) {
        HostDeps hd;
        hd.eng = deps_.eng;
        hd.nowMs = deps_.nowMs;
        hd.puppetOf = deps_.puppetOf;
        hd.ghostOf = deps_.ghostOf;
        hd.actorMode = deps_.actorMode;
        hd.dialogueStart = deps_.hostDialogue;
        hd.allowActivation = deps_.allowActivation;
        hd.interacted = deps_.interacted;
        hd.joined = [this](PlayerId slot) {   // asked live: a datagram can arrive in the same frame as the join
            if (!rt_ || !rt_->host()) return false;
            for (const auto& cv : rt_->host()->clients())
                if (cv.slot == slot && cv.joined) return true;
            return false;
        };
        hd.send = [this](PlayerId slot, Bytes data) {
            if (!rt_ || !rt_->host() || !mux_) return false;
            for (const auto& cv : rt_->host()->clients())
                if (cv.slot == slot && cv.joined) { mux_->send(cv.peer, std::move(data)); return true; }
            return false;
        };
        hd.runAsGuest = [this](PlayerId slot, const std::function<void()>& body) { return rt_ && rt_->runAsGuest(slot, body); };
        host_.reset(new IntentHost(std::move(hd), cfg_, redirect_));
        for (const auto& cv : rt->host()->clients())
            if (cv.joined) joined_.insert(cv.slot);
        // The older story-channel intents (ClientSession::submitAction -> HostSession::onAction -> this executor) get the same checks and the
        // same acting-player context. Taking the executor over is the point: coop_module's own is the v1 stand-in without a puppet.
        rt->setActionExecutor([this](PlayerId slot, fnvcoop::IntentKind kind, const FormRef& ref) {
            if (host_) host_->runLegacy(slot, kind, ref);
        });
        if (cfg_.dialogueResults)
            rt->setDialogueRunner([this](const fnvcoop_dlg::ResultRequest& rq) { return runDialogueResult(rq); });
    } else {
        ClientDeps cd;
        cd.eng = deps_.eng;
        cd.nowMs = deps_.nowMs;
        cd.startDialogue = deps_.startDialogue;
        cd.containerMenuOpen = deps_.containerMenuOpen;
        cd.sendToHost = [this](Bytes data) {
            if (!mux_) return false;
            mux_->send(0, std::move(data));   // a client's only peer is the host
            return true;
        };
        cd.ready = [this] { return rt_ && rt_->client() && rt_->client()->joined() && !rt_->syncing(); };
        cd.session = [this] { return rt_ && rt_->client() ? rt_->client()->lastSession() : 0u; };
        client_.reset(new IntentClient(std::move(cd), cfg_));
    }
    mux->registerChannel(kChannelIntents, [this, live, gen](fnvcoop::PeerId from, Bytes&& data) {
        if (*live != gen || !up_) return;
        if (isHost_) {
            PlayerId slot = slotOfPeer(from);
            if (slot == kNoPlayer || !host_) return;   // not a joined player: dropped
            host_->onDatagram(slot, data.data(), data.size());
        } else if (client_) {
            client_->onDatagram(data.data(), data.size());
        }
    });
}

void IntentService::frame(nvcoop::coop::Runtime* rt) {
    if (!cfg_.enable) return;
    fnvcoop::MuxTransport* mux = rt && rt->running() ? rt->mux() : nullptr;
    if (!mux) { end(rt != nullptr && rt == rt_); return; }
    const bool isHost = rt->host() != nullptr;
    if (!isHost && !rt->client()) { end(rt == rt_); return; }
    if (!up_ || mux != mux_ || rt != rt_ || isHost != isHost_) {
        end(rt == rt_);
        begin(rt, mux, isHost);
    }
    if (isHost_) {
        std::set<PlayerId> now;
        for (const auto& cv : rt_->host()->clients())
            if (cv.joined) now.insert(cv.slot);
        for (PlayerId s : joined_)
            if (!now.count(s)) host_->playerLeft(s);
        joined_ = std::move(now);
        host_->tick();
    } else {
        client_->tick();
    }
}

uint64_t IntentService::runDialogueResult(const fnvcoop_dlg::ResultRequest& rq) {
    if (!up_ || !isHost_ || !rt_ || !rt_->host() || !deps_.eng) return 0;
    const uint64_t head0 = rt_->host()->log().head();
    const PlayerId slot = rq.initiator;
    RefId actor = kHostPlayerRef;
    if (slot != kHostPlayer) {
        RefId p = deps_.puppetOf ? deps_.puppetOf(slot) : 0;
        ActorMode mode = ActorMode::Puppet;
        if (!p && deps_.ghostOf) { p = deps_.ghostOf(slot); mode = ActorMode::Ghost; }
        if (p) actor = p;
        else if (!cfg_.standIn || cfg_.requirePuppet) { ++dlgStats_.refused; return 0; }   // no stand-in allowed: the script does not run for a stranger
        else mode = ActorMode::StandIn;
        if (host_) host_->noteActor(slot, mode, actor);
    } else {
        ++dlgStats_.asHost;
    }
    bool supported = true, ok = false;
    auto body = [&] { ok = deps_.eng->runInfoResult(actor, slot, rq.info, rq.npc, rq.phase == fnvcoop_dlg::Phase::End, &supported); };
    const bool ran = FNV_MUT(326) ? (body(), true) : rt_->runAsGuest(slot, body);   // MUTANT 326: no guest window: the host is the origin, the initiator is neither paid nor charged
    ++dlgStats_.ran;
    if (!ran || !supported) ++dlgStats_.unsupported;
    else if (!ok) ++dlgStats_.failed;
    const uint64_t head1 = rt_->host()->log().head();
    if (head1 > head0) { ++dlgStats_.committed; return head1; }
    return 0;
}

Obs IntentService::onActivate(const FormRef& target) { return client_ ? client_->onActivate(target) : Obs::Proceed; }
Obs IntentService::onEvent(uint32_t mask, const FormRef& target) { return client_ ? client_->onEvent(mask, target) : Obs::Proceed; }
uint32_t IntentService::submit(Kind kind, const FormRef& target, const FormRef* aux, int32_t value, std::vector<uint8_t> path) {
    return client_ ? client_->submit(kind, target, aux, value, std::move(path)) : 0u;
}
void IntentService::onWorldLoaded() { if (host_) host_->worldReset(); }

std::string IntentService::statusJson() const {
    std::ostringstream o;
    o << "{\"enabled\":" << (cfg_.enable ? "true" : "false") << ",\"up\":" << (up_ ? "true" : "false") << ",\"sessions\":" << sessions_ << ",\"dialogueResults\":{\"ran\":" << dlgStats_.ran << ",\"committed\":" << dlgStats_.committed << ",\"unsupported\":" << dlgStats_.unsupported
      << ",\"failed\":" << dlgStats_.failed << ",\"refused\":" << dlgStats_.refused << "},\"detail\":"
      << (host_ ? host_->statusJson() : client_ ? client_->statusJson() : std::string("null")) << "}";
    return o.str();
}

// ---- SaveGate --------------------------------------------------------------------------------------------------------------------------

bool SaveGate::allow(bool engineAllows) {
    ++stats_.asked;
    if (!engineAllows) return false;
    if (FNV_MUT(324)) return engineAllows;   // MUTANT 324: the flag is read but never enforced
    if (d_.suppressed && d_.suppressed()) {
        ++stats_.blocked;
        const uint64_t t = d_.nowMs ? d_.nowMs() : 0;
        if (d_.notify && (!noticed_ || t - lastNoticeMs_ >= 5000)) {
            noticed_ = true;
            lastNoticeMs_ = t;
            ++stats_.notices;
            d_.notify("You cannot save right now.");
        }
        return false;
    }
    return true;
}

bool saveSuppressedBy(nvcoop::coop::Runtime* rt) {
    if (!rt || !rt->running()) return false;
    if (auto* c = rt->client()) return c->state().saveSuppress;
    if (auto* h = rt->host()) return h->world().saveSuppress;
    return false;
}

}  // namespace nvcoop::intents
