// Guest dialogue module (L8, gap G05): the live EngineGlue behind the 0xD1 DialogueNode. x86 / xNVSE only (compiled into nvcoop.dll by
// plugin/hooks_ui/CMakeLists.txt next to the hooks_ui game glue).
//
// plugin/coop/coop_module.cpp (wave 2, P6) wires it with five calls, nothing else:
//
//   1. start-up, before Runtime::start():
//        fndlg::GuestDialogueConfig gc;  gc.dataDir = DllDir();  gc.report = [](const char* s) { nvcoop::Log("dialogue: %s", s); };
//        fndlg::GuestDialogue::instance().init(gc);
//   2. DialogueHooks h;  h.sink = [](const fnvcoop_dlg::Event& e) { fndlg::GuestDialogue::instance().sink()(e); ...existing PushEvent... };
//      (h.run stays the IntentService runner: runDialogueResult)
//   3. UiWiring w;  w.infoReader = fndlg::GuestDialogue::instance().infoReader();     // replaces fndlg::makeInfoReader(mem, &layout)
//   4. every frame, after Runtime::frame():  fndlg::GuestDialogue::instance().frame(g_rt->dialogue(), nowMs);
//   5. the guest's talk request (IntentClient deps.startDialogue and the activate observer):
//        if (fndlg::GuestDialogue::instance().onTalkRequested(npc, nowMs)) -> suppress the vanilla activation
//      and set [intents] dialogue=1 once a two-copy run passed.
#pragma once
#include <functional>
#include <memory>
#include <string>
#include <vector>
#include "fnvcoop_dlg/adapter.h"

namespace fndlg {

struct GuestDialogueConfig {
    std::string dataDir;                      // "<dir>nvcoop_<table>.txt" overrides the table embedded in the DLL (empty = embedded only)
    bool broadcastHostTalk = true;            // the host player's own conversations are shown to the other players
    void (*report)(const char*) = nullptr;    // load notes and every engine call that could not be done (nvcoop::Log)
};

class GuestDialogue {
public:
    static GuestDialogue& instance();
    ~GuestDialogue();

    bool init(const GuestDialogueConfig& c);   // once; false = the module stays inert (frame / onTalkRequested do nothing)
    bool ready() const;
    // DialogueHooks::sink: forwards the node's events (OpenMenu, ShowLines, CloseMenu, ...) to the engine glue.
    fnvcoop_dlg::DialogueNode::Sink sink();
    // UiWiring::infoReader: INFO address -> its response lines (the engine's own loader behind a cache check).
    std::function<std::vector<fnvcoop_dlg::Line>(uint32_t)> infoReader();
    // Per frame. `node` = Runtime::dialogue() (null outside a joined session): attaches the glue, applies the data tables to a new node, polls the DialogMenu.
    void frame(fnvcoop_dlg::DialogueNode* node, uint32_t nowMs);
    // The local player activated an NPC. true = the vanilla talk is deferred until the host grants the conversation.
    bool onTalkRequested(const fnvcoop_dlg::FormRef& npc, uint32_t nowMs);
    std::string statusJson() const;

private:
    GuestDialogue();
    struct Impl;
    static void probeResponseLayout(Impl& s);
    std::unique_ptr<Impl> p_;
};

}  // namespace fndlg
