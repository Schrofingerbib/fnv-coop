// Live IEngineApi for FalloutNV.exe 1.4.0.525 (x86) built against xNVSE headers (L8 skeleton).
//
// STATUS: compiles; NOT loaded in a game yet. Each method is tagged:
//   [live]  implemented from xNVSE/JIP-documented addresses and layouts
//   [stub]  needs an L5-verified address/hook; reports itself through unsupported() so the first in-game run
//           lists exactly what is missing (never silently pretends to work)
// Subtitle strategy for spectators: v1 uses xNVSE's QueueUIMessage (HUD message queue, address 0x7052F0 in the
// xNVSE source) which is available today; the vanilla Subtitles tile (HUDMainMenu::subtitlesArr, JIP GameUI.h
// 0x198) is the better target once an add-subtitle routine is verified.
#include <windows.h>
#include <cstdio>
#include <string>

#include "nvse/GameAPI.h"
#include "nvse/GameData.h"
#include "nvse/GameForms.h"

#include "glue.h"
#include "ui_hooks.h"   // L11 hooks_ui: nvui::ops (real message boxes, vanilla menus, CloseAllMenus core, StartConversation)

namespace fndlg {
namespace {

// ReadProcessMemory on ourselves: a bad pointer returns false instead of faulting the game.
struct ProcessMemory : IMemory {
    bool read(Addr32 a, void* out, size_t n) const override {
        SIZE_T got = 0;
        return a >= 0x10000 && ReadProcessMemory(GetCurrentProcess(), reinterpret_cast<LPCVOID>(uintptr_t(a)), out, n, &got) && got == n;
    }
};

std::string PluginName(UInt32 formId) {
    DataHandler* dh = DataHandler::Get();
    UInt32 idx = formId >> 24;
    if (dh && idx < dh->modList.loadedModCount && dh->modList.loadedMods[idx]) {
        std::string s = dh->modList.loadedMods[idx]->name;
        for (auto& c : s) c = char(tolower(static_cast<unsigned char>(c)));
        return s;
    }
    return std::string();
}

// (plugin file, local id) -> runtime refID through the load order; 0 if the plugin is not loaded
uint32_t RuntimeRefId(const FormRef& r) {
    DataHandler* dh = DataHandler::Get();
    if (!dh) return 0;
    for (UInt32 i = 0; i < dh->modList.loadedModCount && i < 0xFF; ++i) {
        ModInfo* mi = dh->modList.loadedMods[i];
        if (!mi) continue;
        std::string n = mi->name;
        for (auto& c : n) c = char(tolower(static_cast<unsigned char>(c)));
        if (n == r.plugin) return (uint32_t(i) << 24) | (r.localId & 0x00FFFFFF);
    }
    return 0;
}

}  // namespace

class XnvseEngine : public IEngineApi {
public:
    using Report = void (*)(const char*);
    explicit XnvseEngine(Report r = nullptr) : report_(r) {}

    // [live] process memory, DialogMenu singleton (*(DialogMenu**)0x11D9510)
    const IMemory& memory() override { return mem_; }
    Addr32 dialogMenu() override {
        Addr32 p = 0;
        return readU32(mem_, dialog_menu::kSingletonPtr, p) ? p : 0;
    }

    // [live] runtime FormID -> (plugin file, plugin-local id); dynamic (0xFF) forms have no stable name
    bool formRefOf(Addr32 form, FormRef& out) override {
        uint32_t id = 0;
        if (!form || !readU32(mem_, form + 0x0C, id)) return false;  // TESForm::refID at 0x0C
        std::string plugin = PluginName(id);
        if (plugin.empty()) return false;
        out = FormRef(plugin, id & 0x00FFFFFF);
        return true;
    }
    bool infoRefOf(Addr32 info, FormRef& out) override { return formRefOf(info, out); }

    // [stub] TESTopicInfo -> parent TESTopic. Vanilla has no back pointer (JIP adds one at +0x50 only when its
    // patch is active). Candidates: DialogMenu current topic via vtbl method, or the topic's info array search.
    bool topicOf(Addr32, FormRef&, std::string&) override { unsupported("topicOf: TESTopicInfo->TESTopic lookup"); return false; }

    // [stub] voice file naming/locating (needs confirmation that "<topic>_<infoid>_<n>.ogg" is right and which
    // BSA/loose path the engine resolves)
    std::string voicePath(Addr32, uint32_t, Addr32) override { unsupported("voicePath"); return std::string(); }

    // [stub] facts: Actor::GetActorValue (0x66EF50, L5 verified) for the AVs the INFO conditions reference,
    // ContainerChanges item counts, perks. Needs the per-INFO form list (from L3 data / SCRO refs).
    std::vector<fnvcoop_dlg::Fact> collectFacts(Addr32) override { unsupported("collectFacts"); return {}; }

    // [live] speech/skill classification from the INFO's own fields
    CheckKind classifyCheck(Addr32, const InfoFlags& f) override {
        if (f.speechChallenge) return CheckKind::Speech;
        if (f.actorValueOrPerk) return CheckKind::Skill;
        return CheckKind::None;
    }

    // [stub] replay the deferred Activate: needs L5's RefActivate trampoline (TESObjectREFR::Activate 0x573170)
    void resumeTalk(const FormRef&) override { unsupported("resumeTalk: call original RefActivate via trampoline"); }

    // [live] HUD message through xNVSE QueueUIMessage
    void talkDenied(DenyReason why, PlayerId owner) override {
        char buf[128];
        std::snprintf(buf, sizeof buf, why == DenyReason::Busy ? "Busy talking to player %d." : "Cannot talk right now (%d).",
                      why == DenyReason::Busy ? int(owner) : int(why));
        QueueUIMessage(buf, 0, nullptr, nullptr, 2.0f, false);
    }

    // [stub] re-run the engine's own topic-list build (DialogMenu refresh) after the end-script round trip
    void rebuildTopicList() override { unsupported("rebuildTopicList"); }
    // [stub] close the DialogMenu without local end scripts. Candidates: the CloseAllMenus routine near 0x715177
    // (JIP GameUI.h comment) or Menu::HandleClick on the goodbye tile; both unverified.
    void forceCloseDialog() override { if (!nvui::ops::forceCloseDialog()) unsupported("forceCloseDialog: no DialogMenu open"); }   // [live via L11] CloseAllMenus core, END script never runs locally
    // [stub] EnablePlayerControls/DisablePlayerControls equivalents (OSGlobals control flags); L5 control capture
    void setControls(uint8_t, bool) override { unsupported("setControls"); }

    // [live] spectator banner/topics/subtitle through the HUD message queue
    void banner(const FormRef&, PlayerId initiator) override {
        char buf[96];
        std::snprintf(buf, sizeof buf, "Player %d is talking...", int(initiator));
        QueueUIMessage(buf, 2, nullptr, nullptr, 2.0f, false);
    }
    void showTopics(const std::vector<fnvcoop_dlg::TopicEntry>&) override {}  // v1: no topic overlay
    void showSubtitle(const FormRef&, const std::string& text, float seconds) override {
        std::string t = text.substr(0, 0x1FF);
        QueueUIMessage(t.c_str(), 2, nullptr, nullptr, seconds, false);
    }
    // [stub] play a voice file on the NPC puppet (Say-like, without touching conversation state)
    void playVoice(const FormRef&, const std::string&) override { unsupported("playVoice"); }
    void hideOverlay() override {}

    // [stub] hearing range / line of sight of the NPC from the local player (distance to the puppet ref); conservative: audible
    bool npcAudible(const FormRef&) override { return true; }
    // [stub] native StartConversation (NVSE Cmd_StartConversation) with the player as target, executed on THIS machine only; the
    // script command that triggered it on the host is vetoed there unless the host player is the actor
    void startForcedConversation(const FormRef& npc, const FormRef& topic) override {   // [live via L11] <npc>.StartConversation player <topic>
        uint32_t ref = RuntimeRefId(npc);
        if (!ref || !nvui::ops::openDialogMenu(ref, topic)) unsupported("startForcedConversation: npc ref not loaded");
    }

    // [stub] remove a choice from the live DialogMenu list (infos094[] entry + tile); needs the topic-list tile API
    void hideTopic(Addr32) override { unsupported("hideTopic"); }
    // [stub] the host's ShowMessageBox/ShowBarterMenu/... interception (L5) forwards the request here; opening it
    // locally means calling the same engine function the script command would have called, on THIS machine
    void openMenu(const fnvcoop_dlg::MenuRequest& rq) override { if (!nvui::ops::openMenu(rq)) unsupported("openMenu: kind not supported by hooks_ui"); }   // [live via L11]
    void closeMenu(uint32_t menu) override { nvui::ops::closeMenu(menu); }                                                                      // [live via L11]
    // [live] read-only notice through the HUD message queue
    void menuNotice(const std::string& text, bool closed) override {
        if (text.empty()) return;
        std::string t = (closed ? "Chose: " : "") + text.substr(0, 0x1F0);
        QueueUIMessage(t.c_str(), 2, nullptr, nullptr, 2.0f, false);
    }

    void unsupported(const char* what) override {
        if (report_) report_(what);
        ++unsupportedCalls_;
    }
    unsigned unsupportedCalls() const { return unsupportedCalls_; }

private:
    ProcessMemory mem_;
    Report report_;
    unsigned unsupportedCalls_ = 0;
};

// Factory for the nvcoop module code (L1's NVCOOP_MODULE) to instantiate.
IEngineApi* CreateXnvseEngine(void (*report)(const char*)) { return new XnvseEngine(report); }

}  // namespace fndlg
