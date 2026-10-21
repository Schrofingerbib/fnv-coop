#include "intents/intent_client.h"

#include <sstream>

#include "fnvcoop_mut.h"

namespace nvcoop::intents {

namespace {

constexpr size_t kMaxPending = 64;
constexpr uint64_t kNeverSawMenuMs = 3000;   // the menu never showed up (the activation failed locally): release the lock anyway

std::string explain(Kind k, Result r, PlayerId holder) {
    switch (r) {
        case Result::Busy: return holder == kNoPlayer ? "Someone else is using that." : "Player " + std::to_string(static_cast<int>(holder) + 1) + " is using that.";
        case Result::Taken: return "Somebody else got there first.";
        case Result::OutOfRange: return k == Kind::PickUp ? "You are too far away to take that." : "You are too far away.";
        case Result::NotLoaded: return "The host cannot see that yet.";
        case Result::Denied: return "You cannot use that.";
        case Result::NoPuppet: return "The host could not act for you yet. Try again.";
        case Result::RateLimited: return "Slow down.";
        default: return "That did not work.";
    }
}

}  // namespace

IntentClient::IntentClient(ClientDeps deps, Config cfg) : deps_(std::move(deps)), cfg_(std::move(cfg)) {}

void IntentClient::syncSession() {
    const uint32_t s = deps_.session ? deps_.session() : 0;
    if (s == session_) return;
    session_ = s;
    nextId_ = 1;
    if (!FNV_MUT(322)) pending_.clear();   // MUTANT 322: intents of the old session stay pending forever
    recent_.clear();
    watching_ = false;
}

Obs IntentClient::finish(const GuestDecision& d, const FormRef& target) {
    if (d.viaDialogue) {
        if (deps_.startDialogue) { deps_.startDialogue(target); ++stats_.dialogueStarts; }
        ++stats_.proceeded;
        return Obs::Proceed;
    }
    if (!d.forward) { ++stats_.proceeded; return Obs::Proceed; }
    const uint64_t t = now();
    const auto key = std::make_pair(d.kind, target);
    auto it = recent_.find(key);
    if (it != recent_.end() && cfg_.coalesceMs > 0 && t - it->second < cfg_.coalesceMs && !FNV_MUT(321)) {   // MUTANT 321: a double click sends two intents
        ++stats_.coalesced;
        if (d.suppressLocal) { ++stats_.suppressed; return Obs::Suppress; }
        ++stats_.proceeded;
        return Obs::Proceed;
    }
    if (submit(d.kind, target) == 0) { ++stats_.proceeded; return Obs::Proceed; }   // could not forward: the engine call goes on as before
    if (recent_.size() > 256) recent_.clear();
    recent_[key] = t;
    if (d.suppressLocal) { ++stats_.suppressed; return Obs::Suppress; }
    ++stats_.proceeded;
    return Obs::Proceed;
}

Obs IntentClient::onActivate(const FormRef& target) {
    ++stats_.observedActivate;
    if (inObserver_) { ++stats_.proceeded; return Obs::Proceed; }
    TargetInfo t;
    if (!deps_.eng->describe(target, t)) { ++stats_.proceeded; return Obs::Proceed; }
    return finish(decideActivate(cfg_, t), target);
}

Obs IntentClient::onEvent(uint32_t mask, const FormRef& target) {
    ++stats_.observedEvent;
    if (inObserver_) { ++stats_.proceeded; return Obs::Proceed; }
    TargetInfo t;
    if (!deps_.eng->describe(target, t)) { ++stats_.proceeded; return Obs::Proceed; }
    return finish(decideEvent(cfg_, mask, t), target);
}

uint32_t IntentClient::submit(Kind kind, const FormRef& target, const FormRef* aux, int32_t value, std::vector<uint8_t> path) {
    syncSession();
    if (!deps_.ready || !deps_.ready()) { ++stats_.notReady; return 0; }
    if (pending_.size() >= kMaxPending) { ++stats_.pendingCapped; return 0; }
    Intent in;
    in.session = session_;
    in.id = nextId_;
    in.kind = kind;
    in.target = target;
    if (aux) { in.aux = *aux; in.hasAux = true; }
    in.value = value;
    in.path = std::move(path);
    if (in.path.size() > kMaxPath) in.path.resize(kMaxPath);
    if (!deps_.sendToHost || !deps_.sendToHost(encode(in))) { ++stats_.sendFailed; return 0; }
    ++nextId_;
    ++stats_.forwarded;
    ++stats_.byKind[static_cast<size_t>(kind)];
    pending_[in.id] = Pending{in, now()};
    return in.id;
}

void IntentClient::onDatagram(const uint8_t* data, size_t size) {
    Verdict v;
    if (decode(data, size, nullptr, &v) != WireType::Verdict) { ++stats_.badDatagrams; return; }
    syncSession();
    auto it = pending_.find(v.id);
    if (v.session != session_ || it == pending_.end()) { ++stats_.unknownVerdicts; return; }
    Pending p = std::move(it->second);
    pending_.erase(it);
    ++stats_.verdicts;
    if (v.result != Result::Executed) ++stats_.rejected[static_cast<size_t>(v.result)];
    onVerdict(v, p);
}

void IntentClient::onVerdict(const Verdict& v, const Pending& p) {
    const Kind k = p.in.kind;
    if (k == Kind::ContainerOpen) {
        if (v.result == Result::Executed) {
            if (v.hasRedirect) {
                deps_.eng->closeMenus();   // the shared storage opened locally; the player's own is the one to show
                inObserver_ = true;
                deps_.eng->openContainer(v.redirect);
                inObserver_ = false;
                ++stats_.redirectsOpened;
            } else if (deps_.containerMenuOpen) {
                watching_ = true;
                sawOpen_ = false;
                watched_ = p.in.target;
                watchSince_ = now();
            }
        } else if (v.result != Result::Duplicate) {
            if (!(v.result == Result::Busy && FNV_MUT(319))) deps_.eng->closeMenus();   // MUTANT 319: a busy container stays open on the guest
            deps_.eng->notify(explain(k, v.result, v.holder));
        }
        return;
    }
    if (k == Kind::PickUp && v.result != Result::Executed && v.result != Result::Duplicate) deps_.eng->notify(explain(k, v.result, v.holder));
}

void IntentClient::tick() {
    syncSession();
    const uint64_t t = now();
    if (!FNV_MUT(323)) {   // MUTANT 323: a verdict that never comes keeps the intent pending for ever
        for (auto it = pending_.begin(); it != pending_.end();) {
            if (t - it->second.sentMs > cfg_.pendingTimeoutMs) { ++stats_.timeouts; it = pending_.erase(it); }
            else ++it;
        }
    }
    if (watching_ && deps_.containerMenuOpen && !FNV_MUT(320)) {   // MUTANT 320: the lock is never released (the chest stays "in use" for the lease)
        const bool open = deps_.containerMenuOpen();
        if (open) sawOpen_ = true;
        else if (sawOpen_ || t - watchSince_ > kNeverSawMenuMs) {
            watching_ = false;
            if (submit(Kind::ContainerClose, watched_) != 0) ++stats_.closesSent;
        }
    }
}

std::string IntentClient::statusJson() const {
    std::ostringstream o;
    o << "{\"role\":\"guest\",\"session\":" << session_ << ",\"pending\":" << pending_.size() << ",\"observedActivate\":" << stats_.observedActivate
      << ",\"observedEvent\":" << stats_.observedEvent << ",\"forwarded\":" << stats_.forwarded << ",\"suppressed\":" << stats_.suppressed
      << ",\"proceeded\":" << stats_.proceeded << ",\"coalesced\":" << stats_.coalesced << ",\"notReady\":" << stats_.notReady
      << ",\"verdicts\":" << stats_.verdicts << ",\"unknownVerdicts\":" << stats_.unknownVerdicts << ",\"timeouts\":" << stats_.timeouts
      << ",\"sendFailed\":" << stats_.sendFailed << ",\"redirectsOpened\":" << stats_.redirectsOpened << ",\"closesSent\":" << stats_.closesSent
      << ",\"watching\":" << (watching_ ? "true" : "false") << ",\"rejected\":{";
    bool first = true;
    for (size_t r = 1; r < static_cast<size_t>(Result::Count_); ++r) {
        if (!stats_.rejected[r]) continue;
        o << (first ? "" : ",") << '"' << resultName(static_cast<Result>(r)) << "\":" << stats_.rejected[r];
        first = false;
    }
    o << "}}";
    return o.str();
}

}  // namespace nvcoop::intents
