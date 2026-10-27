// L11 hooks_ui: the production Bridge (game-free). Connects UiRouter to L8's DialogueNode (menus, point-of-no-return consent, forced
// dialogue, condition views) and to the slideshow host. L10 owns the DialogueNode; it calls
//     nvui::DialogueBridgeConfig c; c.node = rt.dialogue(); c.nowMs = ...; c.consent = loadConsentMessages(ifstream(consent_messages.txt)); ...
//     router = nvui::MakeRouter(nvui::makeDialogueBridge(c));  nvui::attachMenuResume(*c.node, *router);
// Everything here runs on the game main thread.
#pragma once
#include <functional>
#include <map>
#include <memory>

#include "fnvcoop_dlg/adapter.h"
#include "fnvcoop_dlg/consent.h"
#include "slideshow.h"
#include "ui_core.h"

namespace nvui {

struct DialogueBridgeConfig {
    fnvcoop_dlg::DialogueNode* node = nullptr;                          // host node (guests never raise anything)
    std::function<uint32_t()> nowMs;                                     // same clock the node's tick() gets
    std::map<FormRef, fnvcoop_dlg::ConsentMessage> consent;              // plugin/dialogue/data/consent_messages.txt
    std::function<PlayerId(uint32_t actorRefID)> slotOfActor;            // 0x14 -> host, guest ghost refs -> their slot
    std::function<bool(uint32_t questRefID)> watchesQuest;               // optional exact prefilter; default: any variable rule exists
    std::function<uint64_t()> lastCommittedSeq;                          // optional: story seq of the INFO's captured effects (ForcedSay ordering)
    SlideHost* slides = nullptr;                                         // optional: ending slideshow broadcaster
};

inline Bridge makeDialogueBridge(const DialogueBridgeConfig& c) {
    Bridge b;
    auto consentMap = std::make_shared<std::map<FormRef, fnvcoop_dlg::ConsentMessage>>(c.consent);
    fnvcoop_dlg::DialogueNode* node = c.node;
    auto now = c.nowMs;

    b.slotOfActor = c.slotOfActor;
    b.raiseMenu = [node, now](PlayerId origin, MenuKind kind, const std::string& text, std::vector<std::string> buttons, int32_t def,
                              const FormRef& subject) { return node->raiseMenu(origin, kind, text, std::move(buttons), def, subject, now()); };
    b.raiseModal = [node, now](PlayerId origin, const std::string& text, std::vector<std::string> buttons, int32_t def, const ConsentRule& rule) {
        fnvcoop_dlg::HostDialogue::ModalOptions o;
        o.partyConsent = true;
        o.confirmButton = rule.confirmButton;
        o.declineChoice = rule.declineChoice;
        o.timeoutMs = rule.timeoutMs;
        o.mode = static_cast<fnvcoop_dlg::HostDialogue::ConsentMode>(rule.mode);
        o.anyButton = rule.anyButton;
        return node->raiseModal(origin, text, std::move(buttons), def, o, now());
    };
    b.consentFor = [consentMap](const FormRef& msg) -> std::optional<ConsentRule> {
        auto it = consentMap->find(msg);
        if (it == consentMap->end() || it->second.isInfo || mut(15)) return std::nullopt;  // MUTANT 15: the consent table is ignored
        ConsentRule r;
        r.confirmButton = it->second.confirmButton;
        r.declineChoice = it->second.declineChoice;
        r.timeoutMs = it->second.timeoutMs;
        r.mode = it->second.mode;
        r.anyButton = it->second.anyButton;
        return r;
    };
    // fork INFOs (Joshua or Daniel, Elijah's offer): the dialogue host holds the pick until the party answered
    if (node && node->host() && !mut(25)) {   // MUTANT 25: the fork INFOs of the consent table are never handed to the dialogue host
        std::map<FormRef, fnvcoop_dlg::HostDialogue::ChoiceRule> rules;
        for (const auto& kv : c.consent) {
            if (!kv.second.isInfo) continue;
            fnvcoop_dlg::HostDialogue::ChoiceRule r;
            r.mode = static_cast<fnvcoop_dlg::HostDialogue::ConsentMode>(kv.second.mode);
            r.timeoutMs = kv.second.timeoutMs;
            rules[kv.first] = r;
        }
        if (!rules.empty()) node->host()->setChoiceRules(std::move(rules));
    }

    b.watchesQuest = c.watchesQuest ? c.watchesQuest : [node](uint32_t) { return node->companions().ruleCount() != 0; };
    b.viewVar = [node](const FormRef& quest, uint32_t idx, PlayerId slot, int32_t real) { return node->viewQuestVarFor(quest, idx, slot, real); };

    auto seq = c.lastCommittedSeq;
    b.forcedSay = [node, now, seq](const ForcedSayArgs& a) {
        node->forcedSay(a.npc, a.topic, a.info, a.lines, a.target, a.scene, seq ? seq() : 0, now());
    };
    b.forceConversation = [node, now](const FormRef& npc, const FormRef& topic, PlayerId target) { return node->forceConversation(npc, topic, target, now()); };

    if (c.slides && !mut(16)) {  // MUTANT 16: the slide host is not connected
        SlideHost* s = c.slides;
        b.slideSwap = [s, now](const FormRef& ref, const std::string& o, const std::string& n) { s->onSwap(ref, o, n, now()); };
        if (!mut(20)) b.nextStamp = [s]() { return s->clock().next(); };  // MUTANT 20: scene narration is not stamped (no order against slides)
    }
    return b;
}

// Menu answers (and timeouts) reach the script that is waiting for them.
inline void attachMenuResume(fnvcoop_dlg::DialogueNode& node, UiRouter& router) {
    node.setMenuResume([&router](uint32_t menu, int32_t choice, bool timedOut) { router.onMenuResume(menu, choice, timedOut); });
}

}  // namespace nvui
