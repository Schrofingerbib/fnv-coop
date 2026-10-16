// L11 hooks_ui x86 glue. Addresses/conventions: internal lane notes (prologues pinned in
// tests/test_ui.cpp). Every command-handler detour is cdecl (COMMAND_ARGS: paramInfo, scriptData, thisObj, containingObj, script,
// eventList, double* result, UInt32* opcodeOffsetPtr); a static_assert ties each one to `ret` (retBytes == 0).
#include "ui_hooks.h"

#include <Windows.h>

#include <cstdio>
#include <cstring>
#include <deque>
#include <memory>

#include "common/nvcoop_api.h"
#include "engine_hooks.h"
#include "ui_addrs.h"

namespace nvui {
namespace {

using nvhooks::PatchStatus;

// ------------------------------------------------------------------ memory
struct Win32Memory : nvhooks::MemoryOps {
    bool read(nvhooks::Addr a, void* out, size_t n) override {
        __try {
            std::memcpy(out, reinterpret_cast<const void*>(a), n);
            return true;
        } __except (EXCEPTION_EXECUTE_HANDLER) {
            return false;
        }
    }
    bool write(nvhooks::Addr a, const void* in, size_t n) override {
        DWORD old = 0;
        void* p = reinterpret_cast<void*>(a);
        if (!VirtualProtect(p, n, PAGE_EXECUTE_READWRITE, &old)) return false;
        std::memcpy(p, in, n);
        FlushInstructionCache(GetCurrentProcess(), p, n);
        DWORD tmp;
        VirtualProtect(p, n, old, &tmp);
        return true;
    }
    nvhooks::Addr allocExec(size_t n) override {
        return reinterpret_cast<nvhooks::Addr>(VirtualAlloc(nullptr, n, MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE));
    }
    void freeExec(nvhooks::Addr a, size_t) override { VirtualFree(reinterpret_cast<void*>(a), 0, MEM_RELEASE); }
};

Win32Memory g_mem;
std::unique_ptr<nvhooks::HookRegistry> g_reg;
UiRouter* g_router = nullptr;
bool g_host = false;
Counters g_cnt;
InfoReader g_infoReader;
std::vector<std::string> g_installed;

bool safeRead(uint32_t a, void* out, size_t n) {
    __try {
        std::memcpy(out, reinterpret_cast<const void*>(a), n);
        return true;
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        return false;
    }
}
uint32_t rd32(uint32_t a) {
    uint32_t v = 0;
    safeRead(a, &v, 4);
    return v;
}
uint32_t refIdOf(const void* form) { return form ? rd32(reinterpret_cast<uint32_t>(form) + 0x0C) : 0; }
uint8_t typeOf(const void* form) {
    uint8_t t = 0;
    if (form) safeRead(reinterpret_cast<uint32_t>(form) + 0x04, &t, 1);
    return t;
}
size_t safeCopy(const char* p, char* buf, size_t cap) {
    size_t n = 0;
    __try {
        while (n + 1 < cap && p[n]) {
            buf[n] = p[n];
            ++n;
        }
    } __except (EXCEPTION_EXECUTE_HANDLER) {
    }
    return n;
}
std::string safeStr(const char* p) {
    if (!p) return std::string();
    static char buf[0x4000];
    const size_t n = safeCopy(p, buf, sizeof buf);
    return std::string(buf, n);
}

constexpr uint32_t kQuestFormType = 0x47;

using ExtractArgsFn = bool(__cdecl*)(void*, void*, uint32_t*, void*, void*, void*, void*, ...);
ExtractArgsFn extractArgs() { return reinterpret_cast<ExtractArgsFn>(addr::ExtractArgs.addr); }

bool toRef(uint32_t refID, FormRef& out) { return refID && nvhooks::resolver().toRef(refID, &out); }

// ------------------------------------------------------------------ engine seam for the router
struct GameEngine : IUiEngine {
    uint64_t nowMs() override { return GetTickCount64(); }
    void setMessageBoxResult(uint32_t scriptRefID, int32_t button) override {
        // what ShowMessageBox_Callback (0x5B4A70) does: remember the button; GetButtonPressed (0x5B4A80) then returns it if the
        // stored script refID is the caller's, and clears both
        *reinterpret_cast<uint32_t*>(addr::MessageBoxScriptRefID) = scriptRefID;
        *reinterpret_cast<uint8_t*>(addr::MessageBoxButton) = uint8_t(button & 0xFF);
    }
    bool refOf(uint32_t refID, FormRef& out) override { return toRef(refID, out); }
    void log(const std::string& s) override { nvcoop::Log("[ui] %s", s.c_str()); }
};
GameEngine g_engine;

// ------------------------------------------------------------------ command handler detours (all cdecl COMMAND_ARGS)
#define CMD_ARGS void *paramInfo, void *scriptData, void *thisObj, void *contObj, void *script, void *eventList, double *result, uint32_t *opcodeOff
#define CMD_PASS paramInfo, scriptData, thisObj, contObj, script, eventList, result, opcodeOff
using Cmd = bool(__cdecl*)(CMD_ARGS);

// ---- ShowMessage + the box itself ----
Cmd o_ShowMessage;
int g_inShowMessage = 0;
void* g_showThisObj = nullptr;
FormRef g_showMessageForm;

bool __cdecl h_ShowMessage(CMD_ARGS) {
    ++g_cnt.seenShowMessage;
    if (!g_host || !g_router) return o_ShowMessage(CMD_PASS);
    // identify the MESG form (consent rules); same extraction the vanilla handler does (message form + one more out slot)
    g_showMessageForm = FormRef();
    {
        void* msg = nullptr;
        uint32_t dummy = 0;
        uint32_t off = opcodeOff ? *opcodeOff : 0;
        if (extractArgs()(paramInfo, scriptData, &off, thisObj, contObj, script, eventList, &msg, &dummy)) toRef(refIdOf(msg), g_showMessageForm);
    }
    g_showThisObj = thisObj;
    ++g_inShowMessage;
    struct Leave {
        ~Leave() { --g_inShowMessage; }
    } leave;
    return o_ShowMessage(CMD_PASS);
}

using Fn_ShowMessageBox = bool(__cdecl*)(const char*, uint32_t, uint32_t, void*, uint32_t, uint32_t, float, float, const char*, const char*,
                                          const char*, const char*, const char*, const char*, const char*, const char*, const char*,
                                          const char*, const char*);
Fn_ShowMessageBox o_ShowMessageBox;

// Variadic in the engine (buttons, NULL terminated). Reading b0..b9 is safe because we only dereference up to the first NULL; the
// caller pushed at least that many.
bool __cdecl h_ShowMessageBox(const char* msg, uint32_t u1, uint32_t u2, void* cb, uint32_t u4, uint32_t u5, float f6, float f7, const char* b0,
                              const char* b1, const char* b2, const char* b3, const char* b4, const char* b5, const char* b6,
                              const char* b7, const char* b8, const char* b9) {
    if (g_inShowMessage > 0 && g_host && g_router) {
        ++g_cnt.seenBoxes;
        MessageBoxCall c;
        c.scriptRefID = rd32(addr::MessageBoxScriptRefID);
        c.thisObj = refIdOf(g_showThisObj);
        c.message = g_showMessageForm;
        c.text = safeStr(msg);
        const char* bs[10] = {b0, b1, b2, b3, b4, b5, b6, b7, b8, b9};
        for (int i = 0; i < 10 && bs[i]; ++i) c.buttons.push_back(safeStr(bs[i]));
        if (g_router->onMessageBox(c) == Route::Suppressed) return true;
    }
    return o_ShowMessageBox(msg, u1, u2, cb, u4, u5, f6, f7, b0, b1, b2, b3, b4, b5, b6, b7, b8, b9, nullptr);
}

// ---- service menus ----
#define SERVICE_HOOK(NAME, KIND)                                                                              \
    Cmd o_##NAME;                                                                                             \
    bool __cdecl h_##NAME(CMD_ARGS) {                                                                         \
        ++g_cnt.seenService;                                                                                  \
        if (g_host && g_router) {                                                                             \
            ServiceMenuCall c;                                                                                \
            c.kind = KIND;                                                                                    \
            c.thisObj = refIdOf(thisObj);                                                                     \
            c.scriptKey = refIdOf(script);                                                                    \
            if (g_router->onServiceMenu(c) == Route::Suppressed) return true;                                 \
        }                                                                                                     \
        return o_##NAME(CMD_PASS);                                                                            \
    }
SERVICE_HOOK(Barter, MenuKind::Barter)
SERVICE_HOOK(Repair, MenuKind::Repair)
SERVICE_HOOK(Recipe, MenuKind::Recipe)
SERVICE_HOOK(Caravan, MenuKind::Caravan)
SERVICE_HOOK(Slot, MenuKind::Other)
SERVICE_HOOK(BlackJack, MenuKind::Other)
SERVICE_HOOK(Roulette, MenuKind::Other)

// ---- GetQuestVariable / GetScriptVariable condition functions ----
using Fn_Cond = bool(__cdecl*)(void*, void*, uint32_t, double*);
Fn_Cond o_Cond;
bool __cdecl h_Cond(void* thisObj, void* form, uint32_t idx, double* result) {
    const bool ok = o_Cond(thisObj, form, idx, result);
    if (ok && form && g_router && result && typeOf(form) == kQuestFormType) {
        ++g_cnt.seenCond;
        *result = g_router->viewQuestVar(refIdOf(form), idx, *result);
    }
    return ok;
}

// ---- forced dialogue ----
int g_forcedDepth = 0;
uint32_t g_lastInfo = 0;
using Fn_GetTopicInfo = int(__cdecl*)(int, uint8_t*, void*, void*, char, int, int);
Fn_GetTopicInfo o_GetTopicInfo;
int __cdecl h_GetTopicInfo(int topic, uint8_t* flag, void* speaker, void* target, char p5, int p6, int p7) {
    int info = o_GetTopicInfo(topic, flag, speaker, target, p5, p6, p7);
    if (g_forcedDepth > 0 && info) g_lastInfo = uint32_t(info);
    return info;
}

void recordForcedEffect(const ForcedCall& c, const ForcedOutcome& o) {
    nvhooks::CaptureHub* hub = nvhooks::hub();
    if (!hub || !hub->active() || !o.captured || c.kind == ForcedCall::Kind::Say) return;
    fnvcoop::ForcedDialogue fd;
    toRef(c.speaker, fd.npc);
    fd.topic = c.topic;
    fd.initiator = (o.target < 8) ? o.target : 0;
    hub->record(fd);
}

Cmd o_Say, o_SayTo, o_StartConv;

bool __cdecl h_Say(CMD_ARGS) {
    ++g_cnt.seenForced;
    if (!g_host || !g_router) return o_Say(CMD_PASS);
    void* topic = nullptr;
    int32_t a = 0, b = 0, d = 0;
    void* base = nullptr;
    uint32_t off = opcodeOff ? *opcodeOff : 0;
    const bool got = extractArgs()(paramInfo, scriptData, &off, thisObj, contObj, script, eventList, &topic, &a, &base, &b, &d);
    g_lastInfo = 0;
    ++g_forcedDepth;
    const bool r = o_Say(CMD_PASS);
    --g_forcedDepth;
    if (got && topic) {
        ForcedCall c;
        c.kind = ForcedCall::Kind::Say;
        c.speaker = refIdOf(thisObj);
        toRef(refIdOf(topic), c.topic);
        if (g_lastInfo) {
            toRef(refIdOf(reinterpret_cast<void*>(g_lastInfo)), c.info);
            if (g_infoReader) c.lines = g_infoReader(g_lastInfo);
        }
        recordForcedEffect(c, g_router->onForced(c));
    }
    return r;
}

bool __cdecl h_SayTo(CMD_ARGS) {
    ++g_cnt.seenForced;
    if (!g_host || !g_router) return o_SayTo(CMD_PASS);
    void* actor = nullptr;
    void* topic = nullptr;
    int32_t a = 0, b = 0;
    uint32_t off = opcodeOff ? *opcodeOff : 0;
    const bool got = extractArgs()(paramInfo, scriptData, &off, thisObj, contObj, script, eventList, &actor, &topic, &a, &b);
    g_lastInfo = 0;
    ++g_forcedDepth;
    const bool r = o_SayTo(CMD_PASS);
    --g_forcedDepth;
    if (got && topic) {
        ForcedCall c;
        c.kind = ForcedCall::Kind::SayTo;
        c.speaker = refIdOf(thisObj);
        c.target = refIdOf(actor);
        toRef(refIdOf(topic), c.topic);
        if (g_lastInfo) {
            toRef(refIdOf(reinterpret_cast<void*>(g_lastInfo)), c.info);
            if (g_infoReader) c.lines = g_infoReader(g_lastInfo);
        }
        recordForcedEffect(c, g_router->onForced(c));
    }
    return r;
}

bool __cdecl h_StartConversation(CMD_ARGS) {
    ++g_cnt.seenForced;
    if (!g_host || !g_router) return o_StartConv(CMD_PASS);
    void* target = nullptr;
    void* topic = nullptr;
    void* r1 = nullptr;
    void* r2 = nullptr;
    int32_t a = 0, b = 0;
    uint32_t off = opcodeOff ? *opcodeOff : 0;
    if (extractArgs()(paramInfo, scriptData, &off, thisObj, contObj, script, eventList, &target, &topic, &r1, &r2, &a, &b) && topic) {
        ForcedCall c;
        c.kind = ForcedCall::Kind::StartConversation;
        c.speaker = refIdOf(thisObj);
        c.target = refIdOf(target);
        toRef(refIdOf(topic), c.topic);
        const ForcedOutcome o = g_router->onForced(c);
        recordForcedEffect(c, o);
        if (o.vetoNative) return true;  // the actor's own machine opens the DialogMenu; the host does not
    }
    return o_StartConv(CMD_PASS);
}

// ---- ending slideshow: SwapTexture ref, oldPath, newPath ----
Cmd o_Swap;
bool __cdecl h_SwapTexture(CMD_ARGS) {
    ++g_cnt.seenSwap;
    if (g_host && g_router) {
        void* ref = nullptr;
        char oldTex[512] = {}, newTex[512] = {};
        uint32_t off = opcodeOff ? *opcodeOff : 0;
        if (extractArgs()(paramInfo, scriptData, &off, thisObj, contObj, script, eventList, &ref, oldTex, newTex))
            g_router->onSwapTexture(refIdOf(ref), oldTex, newTex);
    }
    return o_Swap(CMD_PASS);
}

// ------------------------------------------------------------------ calling-convention pins
#define PIN_CDECL(spec) static_assert(addr::spec.retBytes == 0, #spec " is not a plain `ret` (cdecl) function")
PIN_CDECL(ShowMessage);
PIN_CDECL(ShowMessageBox);
PIN_CDECL(CondEvalVar);
PIN_CDECL(CmdSay);
PIN_CDECL(CmdSayTo);
PIN_CDECL(CmdStartConversation);
PIN_CDECL(CmdSwapTexture);
PIN_CDECL(GetTopicInfo);
PIN_CDECL(CmdShowBarterMenu);
PIN_CDECL(CmdShowRepairMenu);
PIN_CDECL(CmdShowRecipeMenu);
PIN_CDECL(CmdShowCaravanMenu);
PIN_CDECL(CmdShowSlotMachine);
PIN_CDECL(CmdShowBlackJack);
PIN_CDECL(CmdShowRoulette);

struct Entry {
    const nvhooks::HookSpec* spec;
    void* detour;
    void* original;  // address of the `o_*` pointer
    bool hostOnly;
};
#define E(spec, det, orig, hostOnly) {&addr::spec, reinterpret_cast<void*>(&det), reinterpret_cast<void*>(&orig), hostOnly}
const Entry kEntries[] = {
    E(CondEvalVar, h_Cond, o_Cond, false),
    E(ShowMessage, h_ShowMessage, o_ShowMessage, true),
    E(ShowMessageBox, h_ShowMessageBox, o_ShowMessageBox, true),
    E(CmdSay, h_Say, o_Say, true),
    E(CmdSayTo, h_SayTo, o_SayTo, true),
    E(CmdStartConversation, h_StartConversation, o_StartConv, true),
    E(CmdSwapTexture, h_SwapTexture, o_Swap, true),
    E(GetTopicInfo, h_GetTopicInfo, o_GetTopicInfo, true),
    E(CmdShowBarterMenu, h_Barter, o_Barter, true),
    E(CmdShowRepairMenu, h_Repair, o_Repair, true),
    E(CmdShowRecipeMenu, h_Recipe, o_Recipe, true),
    E(CmdShowCaravanMenu, h_Caravan, o_Caravan, true),
    E(CmdShowSlotMachine, h_Slot, o_Slot, true),
    E(CmdShowBlackJack, h_BlackJack, o_BlackJack, true),
    E(CmdShowRoulette, h_Roulette, o_Roulette, true),
};

}  // namespace

// ------------------------------------------------------------------ public
UiRouter* MakeRouter(Bridge b) {
    static std::unique_ptr<UiRouter> r;
    r = std::make_unique<UiRouter>(g_engine, std::move(b));
    return r.get();
}

InitReport Init(UiRouter& router, bool host) {
    InitReport rep;
    g_router = &router;
    g_host = host;
    router.setHost(host);
    if (!g_reg) g_reg = std::make_unique<nvhooks::HookRegistry>(g_mem);
    for (const Entry& e : kEntries) {
        if (e.hostOnly && !host) continue;
        nvhooks::Addr tramp = 0;
        PatchStatus st = g_reg->acquire(*e.spec, reinterpret_cast<nvhooks::Addr>(e.detour), &tramp);
        rep.hooks.push_back({e.spec->name, st});
        if (nvhooks::installed(st)) {
            *reinterpret_cast<nvhooks::Addr*>(e.original) = tramp;
            g_installed.push_back(e.spec->name);
            ++rep.ok;
        } else {
            ++rep.failed;
            nvcoop::Log("[ui] hook %s skipped: %s", e.spec->name, nvhooks::statusName(st));
        }
    }
    return rep;
}

void Shutdown() {
    g_reg.reset();
    g_installed.clear();
    g_router = nullptr;
}
UiRouter* router() { return g_router; }
void setInfoReader(InfoReader f) { g_infoReader = std::move(f); }
const Counters& counters() { return g_cnt; }
bool installedByName(const std::string& n) {
    for (auto& s : g_installed)
        if (s == n) return true;
    return false;
}

// ------------------------------------------------------------------ origin side ops
namespace ops {
namespace {
struct QueuedBox {
    uint32_t id;
    std::string text;
    std::vector<std::string> buttons;
};
std::deque<QueuedBox> g_queue;
uint32_t g_showing = 0;
Answer g_answer;

void __cdecl boxCallback() {
    const int b = reinterpret_cast<int(__cdecl*)()>(addr::MessageBoxLastButton.addr)();
    const uint32_t id = g_showing;
    g_showing = 0;
    if (id && g_answer) g_answer(id, b);
}

bool showNow(const QueuedBox& q) {
    const char* b[10] = {};
    std::vector<std::string> btn = q.buttons;
    if (btn.empty()) btn.push_back("Ok");
    for (size_t i = 0; i < btn.size() && i < 10; ++i) b[i] = btn[i].c_str();
    Fn_ShowMessageBox fn = o_ShowMessageBox ? o_ShowMessageBox : reinterpret_cast<Fn_ShowMessageBox>(0x703E80);
    g_showing = q.id;
    // same arguments as the vanilla ShowMessage command (xNVSE Cmd_MessageBoxEx): unk5 = 0x17, own callback
    const bool ok = fn(q.text.c_str(), 0, 0, reinterpret_cast<void*>(&boxCallback), 0, 0x17, 0.f, 0.f, b[0], b[1], b[2], b[3], b[4], b[5], b[6], b[7],
                       b[8], b[9], nullptr);
    if (!ok) g_showing = 0;
    return ok;
}
}  // namespace

void setAnswerSink(Answer a) { g_answer = std::move(a); }

bool openMenu(const fnvcoop_dlg::MenuRequest& rq) {
    if (rq.kind == MenuKind::MessageBox || rq.kind == MenuKind::Terminal) {
        g_queue.push_back({rq.menu, rq.text, rq.buttons});
        pump();
        return true;
    }
    // service menus: run the vanilla command on the subject; the menu has no result, the "answer" is that it was shown
    const char* cmd = rq.kind == MenuKind::Barter ? "ShowBarterMenu" : rq.kind == MenuKind::Repair ? "ShowRepairMenu"
                    : rq.kind == MenuKind::Recipe ? "ShowRecipeMenu" : rq.kind == MenuKind::Caravan ? "ShowCaravanMenu" : nullptr;
    if (!cmd) {
        nvcoop::Log("[ui] openMenu: kind %d not supported", int(rq.kind));
        if (g_answer) g_answer(rq.menu, -1);
        return false;
    }
    const uint32_t ref = nvhooks::resolver().fromRef(rq.subject);
    const bool ok = ref && nvhooks::execConsoleLineOn(cmd, ref);
    if (g_answer) g_answer(rq.menu, ok ? 0 : -1);
    return ok;
}

void pump() {
    if (g_showing || g_queue.empty()) return;
    QueuedBox q = std::move(g_queue.front());
    g_queue.pop_front();
    if (!showNow(q) && g_answer) g_answer(q.id, -1);
}

void closeMenu(uint32_t menu) {
    for (auto it = g_queue.begin(); it != g_queue.end(); ++it)
        if (it->id == menu) {
            g_queue.erase(it);
            return;
        }
    if (g_showing == menu) {
        g_showing = 0;  // the host already took its default: do not answer
        reinterpret_cast<void(__cdecl*)()>(addr::CloseAllMenusCore.addr)();
    }
}

bool dialogOpen() { return rd32(addr::DialogMenuPtr) != 0; }

bool forceCloseDialog() {
    if (!dialogOpen()) return false;
    // the END result script of the open INFO must not run on this machine: it only runs when *0x11D9518 != 0
    *reinterpret_cast<uint32_t*>(addr::DialogMenuInfoSpoken) = 0;
    reinterpret_cast<void(__cdecl*)()>(addr::CloseAllMenusCore.addr)();
    return true;
}

bool openDialogMenu(uint32_t speakerRefID, const FormRef& topic) {
    const uint32_t t = nvhooks::resolver().fromRef(topic);
    if (!speakerRefID || !t) return false;
    char line[64];
    std::snprintf(line, sizeof line, "StartConversation player %08X", t);
    return nvhooks::execConsoleLineOn(line, speakerRefID);
}

bool swapTexture(const FormRef& ref, const std::string& oldTex, const std::string& newTex) {
    const uint32_t id = nvhooks::resolver().fromRef(ref);
    if (!id) return false;
    char line[1200];
    std::snprintf(line, sizeof line, "SwapTexture %08X \"%s\" \"%s\"", id, oldTex.c_str(), newTex.c_str());
    return nvhooks::execConsoleLine(line);
}

size_t queuedBoxes() { return g_queue.size() + (g_showing ? 1 : 0); }
}  // namespace ops

}  // namespace nvui
