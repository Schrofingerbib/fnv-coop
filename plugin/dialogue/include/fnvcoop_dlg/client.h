// Client-side conversation view (L8). Game-free, header-only. One instance per game process.
//
// Role Initiator: the local DialogMenu is real; this class only gates it (start grant, end-script round trip) and
// produces the messages to send. Role Spectator: purely a sink for Lines/Result/Closed; never evaluates conditions.
// All outputs are Events; the engine glue (L5 hooks + overlay) turns them into UI/engine calls.
#pragma once
#include <functional>
#include <set>
#include <vector>
#include "fnvcoop_dlg/messages.h"
#include "fnvcoop_dlg/mutations.h"

namespace fnvcoop_dlg {

struct Event {
    enum Kind : uint8_t {
        Send,            // msg -> host
        OpenMenu,        // initiator: grant received, open the real DialogMenu now
        Denied,          // initiator: start refused (reason); show "busy" and do not open the menu
        SpectatorOpened, // spectator: show conversation banner (npc, initiator)
        SpectatorOffer,  // spectator: read-only topic list
        ShowLines,       // spectator: subtitle + voice via the NPC puppet
        TopicsReady,     // initiator: end-script round trip done (or timed out) -> build next topic list
        ControlLock,     // initiator: apply/release control mask
        CloseMenu,       // force-close DialogMenu / overlay (reason)
        MenuShow,        // origin: open this menu locally (msg = MenuRequest), answer with menuReply()
        MenuNoticeShow,  // others: read-only "player X is using a menu" notice (msg = MenuNotice)
        ForcedSayShow,   // a scripted line (SayTo/Say) from the host: play it on the NPC puppet (msg = ForcedLines)
        ForcedOpen,      // target only: start the forced conversation natively now (msg = ForcedStart)
        MenuEnd,         // everyone: menu finished (msg = MenuClosed); origin closes its UI if timedOut
    } kind = Send;
    Msg msg;
    CloseReason reason = CloseReason::Goodbye;
    DenyReason deny = DenyReason::Busy;
    bool timedOut = false;  // TopicsReady fired because the gate timed out
    PlayerId owner = fnvcoop::kNoPlayer;  // Denied(Busy): who holds the NPC
    bool wasInitiator = false;            // CloseMenu: the local player was the initiator (real DialogMenu is open)
};

class ClientDialogue {
public:
    struct Config { uint32_t gateTimeoutMs = 1500; uint32_t heartbeatMs = 15000; };
    // Story-channel link: returns the highest EffectTxn seq applied locally. Result{txnSeq} travels on 0xD1 and the txn on 0xFC, so the
    // initiator's next topic list (conditions read quest state!) must wait until the txn is in. Unset = no waiting.
    using AppliedProvider = std::function<uint64_t()>;
    void setAppliedProvider(AppliedProvider p) { applied_ = std::move(p); }
    enum class Role : uint8_t { Idle, Requesting, Initiator, Spectator };

    ClientDialogue(PlayerId self, Config cfg = {}) : self_(self), cfg_(cfg) {}

    Role role() const { return role_; }
    ConvId conv() const { return conv_; }
    uint32_t step() const { return step_; }
    bool topicsReady() const {
        return role_ == Role::Initiator && (mutations().noTopicGate || (!awaitEnd_ && !awaitBegin_ && holdSeq_ == 0));
    }
    bool spectatorVisible() const { return role_ == Role::Spectator && visible_; }
    const std::vector<Lines>& history() const { return history_; }
    const std::vector<TopicEntry>& spectatorTopics() const { return topics_; }
    uint32_t sendCount() const { return sends_; }

    // Activation hook: intercept "talk to NPC", do NOT open the menu until OpenMenu.
    std::vector<Event> requestStart(const FormRef& npc) {
        std::vector<Event> e;
        if (role_ == Role::Spectator) reset();  // a spectator is free to walk away and talk to someone else
        if (role_ != Role::Idle) return e;
        role_ = Role::Requesting; npc_ = npc; reqId_ = ++nextReq_;
        send(e, StartReq{reqId_, npc});
        return e;
    }
    std::vector<Event> offer(std::vector<TopicEntry> t) {
        std::vector<Event> e;
        if (role_ == Role::Initiator) send(e, Offer{conv_, step_ + 1, std::move(t)});
        return e;
    }
    // Initiator picked a topic/INFO in the real menu; lines are the already-resolved responses.
    std::vector<Event> pick(const FormRef& topic, const FormRef& info, int32_t choice, bool goodbye,
                            std::vector<Line> lines, std::vector<Fact> facts) {
        std::vector<Event> e;
        if (role_ != Role::Initiator || !topicsReady()) return e;
        ++step_; awaitBegin_ = true; awaitEnd_ = true; goodbye_ = goodbye;
        Pick p; p.conv = conv_; p.step = step_; p.topic = topic; p.info = info; p.choice = choice;
        p.goodbye = goodbye; p.lines = std::move(lines); p.facts = std::move(facts);
        send(e, p);
        return e;
    }
    // Voice/subtitle of the current line finished playing locally.
    std::vector<Event> lineDone(uint32_t now) {
        std::vector<Event> e;
        if (role_ != Role::Initiator || step_ == 0 || lineDoneSent_) return e;
        lineDoneSent_ = true; gateStart_ = now;
        send(e, LineDone{conv_, step_});
        return e;
    }
    std::vector<Event> leave(CloseReason r = CloseReason::InitiatorLeft) {  // initiator Esc / walked away
        std::vector<Event> e;
        if (role_ == Role::Initiator) { send(e, EndReq{conv_, r}); }
        else if (role_ == Role::Spectator) visible_ = false;  // spectators may just hide the overlay
        return e;
    }
    // Origin answered a host-raised menu (button index, or -1 = cancelled / closed a service menu).
    std::vector<Event> menuReply(uint32_t menu, int32_t choice) {
        std::vector<Event> e;
        if (!pendingMenus_.count(menu)) return e;
        send(e, MenuReply{menu, choice});
        return e;
    }
    bool menuPending(uint32_t menu) const { return pendingMenus_.count(menu) != 0; }

    // The story session (re)joined: whatever conversation this client believed in is gone on the host (it closes on Disconnect and
    // the Closed message could not reach us). Clears the role and tells the engine to close its menu / overlay.
    std::vector<Event> sessionReset() {
        std::vector<Event> e;
        if (mutations().noResync) return e;
        pendingMenus_.clear();
        if (role_ == Role::Initiator || role_ == Role::Spectator) {
            Event c = ev(Event::CloseMenu); c.reason = CloseReason::Disconnect; c.wasInitiator = role_ == Role::Initiator; e.push_back(c);
        }
        reset();
        return e;
    }
    void reset() { holdSeq_ = 0; beginSeq_ = 0; role_ = Role::Idle; conv_ = 0; step_ = 0; awaitBegin_ = awaitEnd_ = lineDoneSent_ = false; history_.clear(); topics_.clear(); visible_ = true; }

    std::vector<Event> tick(uint32_t now) {
        std::vector<Event> e;
        if (role_ == Role::Initiator) {
            if (awaitEnd_ && lineDoneSent_ && now - gateStart_ >= cfg_.gateTimeoutMs) {
                awaitEnd_ = false; awaitBegin_ = false;
                Event t; t.kind = Event::TopicsReady; t.timedOut = true; e.push_back(t);
            }
            if (holdSeq_ != 0) {
                const bool ok = !applied_ || applied_() >= holdSeq_;
                if (ok || now - gateStart_ >= cfg_.gateTimeoutMs) {
                    holdSeq_ = 0;
                    Event t = ev(Event::TopicsReady); t.timedOut = !ok; e.push_back(t);
                }
            }
            if (now - lastSend_ >= cfg_.heartbeatMs) send(e, Heartbeat{conv_}, now);
        }
        return e;
    }

    std::vector<Event> onMessage(const Msg& m, uint32_t now = 0) {
        std::vector<Event> e;
        std::visit([&](const auto& x) { handle(x, now, e); }, m);
        return e;
    }

private:
    void send(std::vector<Event>& e, Msg m, uint32_t now = 0) {
        Event ev; ev.kind = Event::Send; ev.msg = std::move(m); e.push_back(std::move(ev)); ++sends_; lastSend_ = now;
    }
    static Event ev(Event::Kind k) { Event e; e.kind = k; return e; }

    void handle(const Opened& m, uint32_t, std::vector<Event>& e) {
        if (role_ == Role::Requesting && m.reqId == reqId_ && m.initiator == self_) {
            role_ = Role::Initiator; conv_ = m.conv; step_ = 0; awaitBegin_ = awaitEnd_ = lineDoneSent_ = false;
            e.push_back(ev(Event::OpenMenu));
        } else if (m.initiator != self_ && role_ == Role::Idle) {
            role_ = Role::Spectator; conv_ = m.conv; step_ = 0; npc_ = m.npc; visible_ = true; history_.clear(); topics_.clear();
            Event s = ev(Event::SpectatorOpened); s.msg = m; e.push_back(s);
        }
    }
    void handle(const StartDenied& m, uint32_t, std::vector<Event>& e) {
        if (role_ == Role::Requesting && m.reqId == reqId_) {
            role_ = Role::Idle;
            Event d = ev(Event::Denied); d.deny = m.reason; d.owner = m.owner; e.push_back(d);
        }
    }
    void handle(const Offer& m, uint32_t, std::vector<Event>& e) {
        if (role_ != Role::Spectator || m.conv != conv_) return;
        topics_ = m.topics;
        Event s = ev(Event::SpectatorOffer); s.msg = m; e.push_back(s);
    }
    void handle(const Lines& m, uint32_t, std::vector<Event>& e) {
        if (role_ != Role::Spectator || m.conv != conv_) return;
        if (m.step <= step_ && !mutations().spectatorStaleLines) return;  // stale or duplicate
        step_ = m.step; history_.push_back(m);
        Event s = ev(Event::ShowLines); s.msg = m; e.push_back(s);
    }
    void handle(const Result& m, uint32_t, std::vector<Event>& e) {
        if (role_ != Role::Initiator || m.conv != conv_ || m.step != step_) return;
        if (m.phase == Phase::Begin) { awaitBegin_ = false; beginSeq_ = m.txnSeq; }
        else if (awaitEnd_) {
            awaitEnd_ = false; lineDoneSent_ = false;
            if (goodbye_) return;
            const uint64_t need = m.txnSeq > beginSeq_ ? m.txnSeq : beginSeq_;
            if (!mutations().ignoreResultLink && applied_ && need != 0 && applied_() < need) holdSeq_ = need;   // released by tick()
            else e.push_back(ev(Event::TopicsReady));
        }
    }
    void handle(const MenuRequest& m, uint32_t, std::vector<Event>& e) {
        if (m.origin != self_ && !m.consent) return;         // never open someone else's menu (consent prompts go to everybody else)
        pendingMenus_.insert(m.menu);
        Event x = ev(Event::MenuShow); x.msg = m; e.push_back(x);
    }
    void handle(const ForcedLines& m, uint32_t, std::vector<Event>& e) {
        if (!mutations().forcedNoDedupe) {
            if (seenForced_.count(m.id)) return;               // duplicate delivery
            seenForced_.insert(m.id);
            if (seenForced_.size() > 256) seenForced_.erase(seenForced_.begin());
        }
        Event x = ev(Event::ForcedSayShow); x.msg = m; e.push_back(x);
    }
    void handle(const ForcedStart& m, uint32_t, std::vector<Event>& e) {
        if (seenForcedStart_.count(m.id)) return;
        seenForcedStart_.insert(m.id);
        // the host already displaced whatever we were doing; become the initiator of the forced conversation
        reset();
        role_ = Role::Initiator; conv_ = m.conv; npc_ = m.npc;
        Event x = ev(Event::ForcedOpen); x.msg = m; e.push_back(x);
    }
    void handle(const MenuNotice& m, uint32_t, std::vector<Event>& e) {
        if (m.origin == self_) return;
        Event x = ev(Event::MenuNoticeShow); x.msg = m; e.push_back(x);
    }
    void handle(const MenuClosed& m, uint32_t, std::vector<Event>& e) {
        pendingMenus_.erase(m.menu);
        Event x = ev(Event::MenuEnd); x.msg = m; x.timedOut = m.timedOut; e.push_back(x);
    }
    void handle(const Control& m, uint32_t, std::vector<Event>& e) {
        if (role_ != Role::Initiator || m.conv != conv_) return;
        Event c = ev(Event::ControlLock); c.msg = m; e.push_back(c);
    }
    void handle(const Closed& m, uint32_t, std::vector<Event>& e) {
        if (m.conv != conv_ || (role_ != Role::Initiator && role_ != Role::Spectator)) return;
        Event c = ev(Event::CloseMenu); c.reason = m.reason; c.wasInitiator = role_ == Role::Initiator; e.push_back(c);
        reset();
    }
    template <class T> void handle(const T&, uint32_t, std::vector<Event>&) {}

    PlayerId self_;
    Config cfg_;
    Role role_ = Role::Idle;
    ConvId conv_ = 0;
    AppliedProvider applied_;
    uint64_t holdSeq_ = 0, beginSeq_ = 0;   // TopicsReady waits for this story seq (0 = not holding)
    uint32_t step_ = 0, reqId_ = 0, nextReq_ = 0, gateStart_ = 0, lastSend_ = 0, sends_ = 0;
    bool awaitBegin_ = false, awaitEnd_ = false, lineDoneSent_ = false, goodbye_ = false, visible_ = true;
    FormRef npc_;
    std::set<uint32_t> pendingMenus_, seenForced_, seenForcedStart_;
    std::vector<Lines> history_;
    std::vector<TopicEntry> topics_;
};

}  // namespace fnvcoop_dlg
