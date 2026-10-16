// Engine glue (L8): translates between the game's dialogue lifecycle and fnvcoop_dlg::DialogueNode.
//
// IEngineApi is everything the glue needs from the running game. plugin/dialogue/engine/engine_xnvse.cpp implements
// it against xNVSE headers (x86 only); engine_test.cpp implements it with a mock, so all the logic here is tested
// natively. Methods the live implementation cannot do yet (addresses unverified by L5) are stubs that report
// themselves via IEngineApi::unsupported() so the first live run tells us exactly what is missing.
#pragma once
#include <string>
#include <vector>
#include "fnvcoop_dlg/adapter.h"
#include "info_reader.h"

namespace fndlg {

using fnvcoop_dlg::CheckKind;
using fnvcoop_dlg::DenyReason;
using fnvcoop_dlg::Event;
using fnvcoop_dlg::FormRef;
using fnvcoop_dlg::PlayerId;

struct IEngineApi {
    virtual ~IEngineApi() = default;
    virtual const IMemory& memory() = 0;
    virtual Addr32 dialogMenu() = 0;                                        // *(Addr32*)0x11D9510, 0 if closed
    virtual bool formRefOf(Addr32 form, FormRef& out) = 0;                  // runtime form -> (plugin, local id)
    virtual bool topicOf(Addr32 info, FormRef& topic, std::string& topicEdid) = 0;
    virtual bool infoRefOf(Addr32 info, FormRef& out) = 0;
    virtual std::string voicePath(Addr32 info, uint32_t responseNumber, Addr32 speakerRef) = 0;  // "" if unknown
    virtual std::vector<fnvcoop_dlg::Fact> collectFacts(Addr32 info) = 0;   // player facts the scripts/conditions read
    virtual CheckKind classifyCheck(Addr32 info, const InfoFlags& f) = 0;

    // --- initiator side
    virtual void resumeTalk(const FormRef& npc) = 0;                        // continue the deferred vanilla talk
    virtual void talkDenied(DenyReason why, PlayerId owner) = 0;            // "X is busy talking to <owner>"
    virtual void rebuildTopicList() = 0;                                    // after End result: build next list
    virtual void forceCloseDialog() = 0;                                    // close DialogMenu, no local end scripts
    virtual void setControls(uint8_t mask, bool locked) = 0;

    // --- spectator side
    virtual void banner(const FormRef& npc, PlayerId initiator) = 0;
    virtual void showTopics(const std::vector<fnvcoop_dlg::TopicEntry>& t) = 0;
    virtual void showSubtitle(const FormRef& npc, const std::string& text, float seconds) = 0;
    virtual void playVoice(const FormRef& npc, const std::string& path) = 0;
    virtual void hideOverlay() = 0;

    // --- forced dialogue (scripted SayTo / StartConversation)
    virtual bool npcAudible(const FormRef& npc) = 0;                         // within hearing/line-of-sight of this player
    virtual void startForcedConversation(const FormRef& npc, const FormRef& topic) = 0;  // native StartConversation on THIS machine

    // --- companions: remove a command topic from the real DialogMenu for a non-owner
    virtual void hideTopic(Addr32 info) = 0;

    // --- menus raised by host-run scripts: opened on the origin player only; everyone else gets a notice
    virtual void openMenu(const fnvcoop_dlg::MenuRequest& rq) = 0;          // ShowMessageBox / barter / wheel / terminal on THIS machine
    virtual void closeMenu(uint32_t menu) = 0;                              // forced close (timeout)
    virtual void menuNotice(const std::string& text, bool closed) = 0;      // read-only notice / its dismissal

    virtual void unsupported(const char* what) = 0;                         // live stubs report here
};

class EngineGlue {
public:
    EngineGlue(IEngineApi& api, ResponseLayout layout = {}) : api_(api), layout_(layout) {}
    void attach(fnvcoop_dlg::DialogueNode* n) { node_ = n; }
    fnvcoop_dlg::DialogueNode::Sink sink() { return [this](const Event& e) { onEvent(e); }; }

    void setLayout(ResponseLayout l) { layout_ = l; }
    const ResponseLayout& layout() const { return layout_; }

    // ---- engine -> glue (L5 hooks call these on the main thread)
    // TESObjectREFR::Activate toward an NPC resulted in "talk". Returns true if vanilla must be VETOED/deferred.
    bool onTalkRequested(const FormRef& npc, uint32_t now) {
        auto& c = node_->client();
        if (c.role() == fnvcoop_dlg::ClientDialogue::Role::Initiator || c.role() == fnvcoop_dlg::ClientDialogue::Role::Requesting)
            return false;  // already in/awaiting a conversation: let vanilla handle repeated activation (menu re-entry)
        pendingNpc_ = npc;
        node_->requestStart(npc, now);
        return true;
    }
    // DialogMenu finished building its topic/choice list.
    void onTopicsBuilt(uint32_t now) {
        MenuView mv;
        if (!api_.dialogMenu() || !readMenu(api_.memory(), api_.dialogMenu(), mv)) return;
        std::vector<fnvcoop_dlg::TopicEntry> t;
        const bool companion = node_->companions().isCompanion(pendingNpc_);
        const PlayerId owner = node_->companions().ownerOf(pendingNpc_);
        for (Addr32 info : mv.offered) {
            fnvcoop_dlg::TopicEntry e;
            std::string edid;
            InfoFlags f;
            if (!api_.topicOf(info, e.topic, edid)) continue;
            // Companion commands are owner-only: a non-owner never sees them (host also rejects the pick).
            if (companion && owner != node_->self() && node_->ownerOnlyTopics().count(e.topic)) { api_.hideTopic(info); continue; }
            if (readInfoFlags(api_.memory(), info, f)) {
                e.check = uint8_t(api_.classifyCheck(info, f));
                e.flags = uint8_t(f.saidOnce ? 1 : 0);
            }
            t.push_back(e);
        }
        node_->offer(std::move(t), now);
    }
    // The initiator selected an INFO; the engine is about to speak it.
    bool onInfoChosen(Addr32 info, uint32_t now) {
        FormRef topic, infoRef;
        std::string edid;
        InfoFlags f;
        std::vector<fnvcoop_dlg::Line> lines;
        MenuView mv;
        if (!api_.topicOf(info, topic, edid) || !api_.infoRefOf(info, infoRef) || !readInfoFlags(api_.memory(), info, f))
            return false;
        if (layout_.headOffset < 0) { api_.unsupported("response list offset unknown (run probeResponseOffset)"); return false; }
        if (!readLines(api_.memory(), info, layout_, lines)) return false;
        if (api_.dialogMenu() && readMenu(api_.memory(), api_.dialogMenu(), mv)) {
            for (uint32_t i = 0; i < lines.size(); ++i) lines[i].voice = api_.voicePath(info, i + 1, mv.partnerRef);
        }
        node_->pick(topic, infoRef, -1, f.goodbye, std::move(lines), api_.collectFacts(info), now);
        return true;
    }
    void onLineFinished(uint32_t now) { node_->lineDone(now); }
    void onMenuClosedByPlayer(uint32_t now) { node_->leave(now); }
    void onFrame(uint32_t now) { node_->tick(now); }
    // The origin player answered (button index) or closed (-1) a menu opened by openMenu().
    void onMenuAnswered(uint32_t menu, int32_t choice, uint32_t now) { node_->menuReply(menu, choice, now); }

    // ---- node -> engine
    void onEvent(const Event& e) {
        using K = Event::Kind;
        switch (e.kind) {
            case K::OpenMenu: api_.resumeTalk(pendingNpc_); break;
            case K::Denied: api_.talkDenied(e.deny, e.owner); break;
            case K::SpectatorOpened: {
                const auto& o = std::get<fnvcoop_dlg::Opened>(e.msg);
                npcOfSpectated_ = o.npc;
                api_.banner(o.npc, o.initiator);
                break;
            }
            case K::SpectatorOffer: api_.showTopics(std::get<fnvcoop_dlg::Offer>(e.msg).topics); break;
            case K::ShowLines: {
                const auto& l = std::get<fnvcoop_dlg::Lines>(e.msg);
                for (const auto& ln : l.lines) {
                    api_.showSubtitle(npcOfSpectated_, ln.text, subtitleSeconds(ln.text));
                    if (!ln.voice.empty()) api_.playVoice(npcOfSpectated_, ln.voice);
                }
                break;
            }
            case K::TopicsReady: api_.rebuildTopicList(); break;
            case K::ControlLock: {
                const auto& c = std::get<fnvcoop_dlg::Control>(e.msg);
                api_.setControls(c.lockMask, c.locked);
                break;
            }
            case K::CloseMenu:
                // Initiator: the engine closes the menu itself after a goodbye line; every other reason is forced.
                if (e.wasInitiator && e.reason != fnvcoop_dlg::CloseReason::Goodbye) api_.forceCloseDialog();
                if (!e.wasInitiator) api_.hideOverlay();
                break;
            case K::MenuShow: api_.openMenu(std::get<fnvcoop_dlg::MenuRequest>(e.msg)); break;
            case K::MenuNoticeShow: {
                const auto& n = std::get<fnvcoop_dlg::MenuNotice>(e.msg);
                api_.menuNotice("Player " + std::to_string(int(n.origin)) + ": " + n.text, false);
                break;
            }
            case K::MenuEnd: {
                const auto& c = std::get<fnvcoop_dlg::MenuClosed>(e.msg);
                if (e.timedOut) api_.closeMenu(c.menu);
                api_.menuNotice(c.label, true);
                break;
            }
            case K::ForcedSayShow: {
                const auto& f = std::get<fnvcoop_dlg::ForcedLines>(e.msg);
                // The target and cutscene viewers always get it; everybody else only if they could hear the NPC.
                const bool mine = f.target == node_->self() || f.scene;
                if (!mine && !api_.npcAudible(f.npc)) break;
                for (const auto& ln : f.lines) {
                    api_.showSubtitle(f.npc, ln.text, subtitleSeconds(ln.text));
                    if (!ln.voice.empty()) api_.playVoice(f.npc, ln.voice);
                }
                break;
            }
            case K::ForcedOpen: {
                const auto& fs = std::get<fnvcoop_dlg::ForcedStart>(e.msg);
                pendingNpc_ = fs.npc;                                         // companion/topic filtering needs the NPC
                api_.startForcedConversation(fs.npc, fs.topic);
                break;
            }
            case K::Send: break;
        }
    }

    static float subtitleSeconds(const std::string& text) {  // reading time floor 2 s, ~15 chars/s
        float s = float(text.size()) / 15.0f;
        return s < 2.0f ? 2.0f : s;
    }

private:
    IEngineApi& api_;
    ResponseLayout layout_;
    fnvcoop_dlg::DialogueNode* node_ = nullptr;
    FormRef pendingNpc_, npcOfSpectated_;
};

}  // namespace fndlg
