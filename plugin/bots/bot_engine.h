// Engine-facing logic of the bot methods bot.menu / bot.say / bot.inventory / bot.vats (T6a), game-free.
// Every engine access goes through IEngine (raw memory + a few thiscall wrappers), so the logic is unit-tested on a synthetic memory
// image with a call recorder (tests/test_bots.cpp). The live IEngine is in bots_module.cpp (SEH-guarded reads, address pins checked).
// Addresses and layouts come from docs/re/dialogmenu-menus-vats-inventory.md (static analysis only: NOTHING here has run in the game).
// Each method is defensive: it checks a state gate and refuses with a reason instead of poking the engine when a gate fails.
#pragma once
#include <cstdint>
#include <mutex>
#include <string>
#include <vector>

#include "bots_addrs.h"
#include "common/minijson.h"

#ifndef NVBOTS_MUTANT
#define NVBOTS_MUTANT 0
#endif
// Extra mutants for this file (13..22), every one must make tests/test_bots.cpp fail:
//  13 dialogue pick ignores the ready state    14 pick matches the wrong list index        15 pick skips the special-topic refusal
//  16 menu id map has 0x3F1/0x3F0 swapped      17 VATS wins nothing over the menu id       18 inventory counts deltas, not totals
//  19 vats job never gives up on a bad target  20 pick leaves the click gate untouched     21 consent buttons swapped
//  22 vats limb request silently accepted     23 terminal entry index off by one     24 terminal pick ignores the menu id gate
//  25 terminal pick is not recorded
//  26 speech-check INFOs refused again   27 drop log ignores the base form    28 drop log ignores the time window    29 fast travel skips the may-travel gate
//  30 fast travel skips the busy-menu gate  31 fast travel writes the marker at the wrong offset  32 refs list includes disabled refs
//  35 always-run state ignores gamepad mode  36 always-run state reads player+0x652 (the auto-move byte)

namespace nvbots {

using nvcoop::json::Value;
using Addr32 = uint32_t;

namespace eng {  // addresses and offsets (see the RE note)
constexpr Addr32 kDialogMenuPtr = 0x11D9510, kInfoSpoken = 0x11D9518, kClockObj = 0x11F6394;
constexpr uint32_t kDlgState = 0x28, kDlgListBox = 0x70, kDlgPartner = 0x80, kDlgClickGate = 0x10C, kDlgPending = 0x104;
constexpr uint32_t kItemInfo = 0x18, kItemFlag = 0x25, kFormId = 0x0C;
constexpr Addr32 kInterfaceMgrPtr = 0x11D8A80, kVatsCamera = 0x11F2250, kVatsMenuPtr = 0x11DB0D4;
constexpr uint32_t kVatsMode = 8, kVatsSelValid = 0x118, kVatsSelRef = 0x128;
constexpr int kDlgReady = 2;
constexpr Addr32 kTerminalMenuPtr = 0x11D9334;   // *(TerminalMenu**), set while the terminal menu (id 0x421) is open
constexpr uint32_t kTermList = 0x68, kTermTerminal = 0xA8, kTermRef = 0xAC;
// Always-run (the code that uses each one is pinned in bots_addrs.h kRunPins):
constexpr uint32_t kPlayerAlwaysRun = 0x651;      // PlayerCharacter byte, flipped on the AlwaysRun (10) press edge
constexpr Addr32 kPadConnected = 0x11D8A84;       // byte: an XInput pad answered this frame
constexpr Addr32 kDisable360Value = 0x11D8C50;    // value byte of Setting bDisable360Controller:Interface (object 0x11D8C4C, +4)
// Prologue pins for the engine functions the live engine calls (verified by tools/bots/re/verify_pins.py; thiscall unless noted)
}  // namespace eng

struct IEngine {
    virtual ~IEngine() = default;
    virtual bool rd32(Addr32 a, uint32_t* v) = 0;
    virtual bool rd8(Addr32 a, uint8_t* v) = 0;
    virtual bool wr8(Addr32 a, uint8_t v) = 0;
    virtual bool wr32(Addr32 a, uint32_t v) = 0;
    virtual uint32_t nowTick() = 0;                                  // the game's millisecond clock (0x825C00 on 0x11F6394)
    // DialogMenu helpers
    virtual bool lbSelect(Addr32 lb, int idx) = 0;                   // 0x83E430
    virtual Addr32 lbSelected(Addr32 lb) = 0;                        // 0x83E4C0
    virtual Addr32 itemInfo(Addr32 item) = 0;                        // 0x9611E0 (item -> TESTopicInfo*)
    virtual bool infoSpecial(Addr32 info) = 0;                       // 0x7627C0 (flag 0x80): the click path takes another branch (item select 0x83E850, speak flag 1): refused
    virtual bool infoSpeechCheck(Addr32 info) = 0;                   // 0x7627E0 || 0x762820: speech-check / challenge INFO; HandleClick only plays a UI sound for these, so the plain path is the same
    virtual void dlgItemClick(Addr32 menu, Addr32 item) = 0;         // 0x762860
    virtual void lbAfter(Addr32 lb) = 0;                             // 0x83EBB0
    virtual int dlgSpeak(Addr32 menu, int flag) = 0;                 // 0x762FF0 -> next state
    // menus / inventory
    virtual uint32_t topMenuId(Addr32 mgr) = 0;                      // 0x714F00
    virtual void termClick(Addr32 menu, Addr32 tile) = 0;            // 0x757F70 thiscall(menu, -1, tile), ret 8
    virtual Addr32 containerData(Addr32 ref) = 0;                    // 0x4BF220 (creates the data if missing)
    virtual int itemTotal(Addr32 data, Addr32 form) = 0;             // 0x4C8F30
    // genuine fast travel (the map menu's deferred worker 0x798A00, replicated)
    virtual bool canFastTravel(Addr32 player) = 0;                   // 0x93D660 (the engine's own may-travel check, shows its refusal message)
    virtual Addr32 newTravelObj() = 0;                               // heap 0x34 + ctor 0x456540, 0 on failure
    virtual void setPendingTravel(Addr32 player, Addr32 obj) = 0;    // 0x93BE30
    // references
    virtual bool resolveRef(const std::string& ref, Addr32* out) = 0;    // "" = the player
    virtual bool resolveForm(const std::string& form, Addr32* out) = 0;
    virtual bool resolveFormId(const std::string& form, uint32_t* refId) = 0;   // runtime form id of "Plugin.esm:HEX"
    virtual std::string nameOfForm(Addr32 form) = 0;                 // "Plugin.esm:HEX"
};

inline const char* menuKindOf(uint32_t id) {
    switch (id) {
        case 0x3E9: return "message";
        case 0x3EA: case 0x3EB: case 0x3FF: case 0x40B: case 0x425: return "pipboy";
        case 0x3EC: return "hud";
        case 0x3EF: return "loading";
#if NVBOTS_MUTANT == 16
        case 0x3F0: return "dialogue";
        case 0x3F1: return "container";
#else
        case 0x3F0: return "container";
        case 0x3F1: return "dialogue";
#endif
        case 0x3F5: return "pause";
        case 0x3F6: return "lockpick";
        case 0x41D: return "barter";
        case 0x41F: return "hacking";
        case 0x420: return "vats";
        case 0x421: return "terminal";
        default: return id ? "other" : "none";
    }
}

// ---- bot.menu ----
inline bool menuInfo(IEngine& e, Value* out, std::string* err) {
    uint32_t mgr = 0, mode = 0;
    if (!e.rd32(eng::kInterfaceMgrPtr, &mgr) || !mgr) { *err = "interface manager not ready"; return false; }
    e.rd32(eng::kVatsCamera + eng::kVatsMode, &mode);
    const uint32_t id = e.topMenuId(mgr);
    const char* kind = menuKindOf(id);
#if NVBOTS_MUTANT == 17
    (void)mode;
#else
    if (mode != 0) kind = "vats";
#endif
    *out = Value::object();
    out->set("kind", kind).set("id", (int64_t)id).set("vatsMode", (int64_t)mode);
    uint32_t dlg = 0;
    if (e.rd32(eng::kDialogMenuPtr, &dlg) && dlg) {
        uint32_t st = 0;
        e.rd32(dlg + eng::kDlgState, &st);
        out->set("dialogState", (int64_t)st).set("dialogReady", st == (uint32_t)eng::kDlgReady);
    }
    return true;
}

// ---- always-run state for bot.move ----
// What the player-controls update (PlayerCharacter::vf2F8, run decision at 0x941A2B) does with the Run control (9):
//   keyboard mode, always-run on:  Run up = run speed, Run down = walk     (so holding Run for "run" made the player walk)
//   keyboard mode, always-run off: Run down = run speed, Run up = walk
//   gamepad mode (a pad answers and bDisable360Controller is 0): the toggle is ignored, Run down = run speed
// Returns 1 when the engine treats always-run as on, 0 when off (or ignored), -1 when the bytes cannot be read.
inline int alwaysRunState(IEngine& e) {
    Addr32 player = 0;
    uint8_t on = 0, pad = 0, padOff = 0;
    if (!e.resolveRef("", &player) || !player) return -1;
#if NVBOTS_MUTANT == 36
    if (!e.rd8(player + eng::kPlayerAlwaysRun + 1, &on)) return -1;
#else
    if (!e.rd8(player + eng::kPlayerAlwaysRun, &on)) return -1;
#endif
    if (!e.rd8(eng::kPadConnected, &pad) || !e.rd8(eng::kDisable360Value, &padOff)) return -1;
#if NVBOTS_MUTANT != 35
    if (pad && !padOff) return 0;
#endif
    return on ? 1 : 0;
}

// ---- bot.say ----
struct Choice { int idx; Addr32 item, info; uint32_t formId; };

inline bool dialogChoices(IEngine& e, std::vector<Choice>* out, std::string* err) {
    uint32_t dlg = 0, lb = 0;
    if (!e.rd32(eng::kDialogMenuPtr, &dlg) || !dlg) { *err = "no dialogue open"; return false; }
    if (!e.rd32(dlg + eng::kDlgListBox, &lb) || !lb) { *err = "dialogue has no choice list"; return false; }
    for (int i = 0; i < 64 && e.lbSelect(lb, i); ++i) {   // moves the selection, exactly like a click would
        Choice c{i, e.lbSelected(lb), 0, 0};
        if (!c.item) break;
        uint32_t info = 0;
        if (e.rd32(c.item + eng::kItemInfo, &info) && info) { c.info = info; e.rd32(info + eng::kFormId, &c.formId); }
        out->push_back(c);
    }
    return true;
}

// Replicates the plain-topic path of DialogMenu::HandleClick (0x7624F0) for the choice whose INFO form id is `wantFormId`:
// select, clear item flag, item click (0x762860), list after-click (0x83EBB0), clear +0x104, record the spoken INFO, speak (0x762FF0).
inline bool dialogPick(IEngine& e, uint32_t wantFormId, Value* out, std::string* err) {
    uint32_t dlg = 0, lb = 0, state = 0;
    if (!e.rd32(eng::kDialogMenuPtr, &dlg) || !dlg) { *err = "no dialogue open"; return false; }
    e.rd32(dlg + eng::kDlgState, &state);
#if NVBOTS_MUTANT != 13
    if (state != (uint32_t)eng::kDlgReady) { *err = "dialogue not ready (state " + std::to_string(state) + ", need 2)"; return false; }
#endif
    uint32_t gate = 0;
    e.rd32(dlg + eng::kDlgClickGate, &gate);
#if NVBOTS_MUTANT != 20
    if (gate) {
        if (e.nowTick() - gate < 500) { *err = "click gate closed (a click happened < 500 ms ago)"; return false; }
        e.wr32(dlg + eng::kDlgClickGate, 0);
    }
#endif
    std::vector<Choice> cs;
    if (!dialogChoices(e, &cs, err)) return false;
    const Choice* hit = nullptr;
    for (const Choice& c : cs)
        if (c.formId == wantFormId) { hit = &c; break; }
    if (!hit) {
        Value ids = Value::array();
        for (const Choice& c : cs) ids.push((int64_t)c.formId);
        *err = "INFO not offered in this dialogue";
        out->set("offered", ids);
        return false;
    }
#if NVBOTS_MUTANT == 15
#else
    if (e.infoSpecial(hit->info)) { *err = "special INFO (flag 0x80): the replicated click path does not cover it"; return false; }
#endif
#if NVBOTS_MUTANT == 26
    if (e.infoSpeechCheck(hit->info)) { *err = "special INFO (speech check): refused"; return false; }
#endif
    const bool speechCheck = e.infoSpeechCheck(hit->info);
    e.rd32(dlg + eng::kDlgListBox, &lb);
#if NVBOTS_MUTANT == 14
    e.lbSelect(lb, hit->idx + 1);
#else
    e.lbSelect(lb, hit->idx);
#endif
    const Addr32 item = e.lbSelected(lb);
    if (item != hit->item) { *err = "selection did not land on the requested choice"; return false; }
    e.wr8(item + eng::kItemFlag, 0);
    e.dlgItemClick(dlg, item);
    e.lbAfter(lb);
    e.wr8(dlg + eng::kDlgPending, 0);
    e.wr32(eng::kInfoSpoken, hit->info);
    const int next = e.dlgSpeak(dlg, 0);
    e.wr32(dlg + eng::kDlgState, (uint32_t)next);
    out->set("said", true).set("index", (int64_t)hit->idx).set("nextState", (int64_t)next).set("speechCheck", speechCheck);
    return true;
}

// ---- bot.inventory ----
// Totals (base container + entry delta, equipped included) through 0x4C8F30. Explicit `items` are always answered; the entry list
// adds every item the reference has an ExtraContainerChanges entry for. Base-container-only items are NOT enumerated (unknown
// layout), so the reply says partial:true unless explicit items were requested.
inline bool inventoryInfo(IEngine& e, const std::string& ref, const std::vector<std::string>& wanted, Value* out, std::string* err) {
    Addr32 r = 0;
    if (!e.resolveRef(ref, &r) || !r) { *err = "reference not loaded"; return false; }
    const Addr32 data = e.containerData(r);
    if (!data) { *err = "no container data"; return false; }
    Value items = Value::array();
    auto add = [&](Addr32 form, int count) {
        Value it = Value::object();
        it.set("item", e.nameOfForm(form)).set("count", (int64_t)count);
        items.push(it);
    };
    for (const std::string& w : wanted) {
        Addr32 f = 0;
        if (!e.resolveForm(w, &f)) { *err = "unknown item " + w; return false; }
        add(f, e.itemTotal(data, f));
    }
    if (wanted.empty()) {
        uint32_t list = 0, node = 0;
        e.rd32(data, &list);                       // EntryDataList*, its head node is embedded {data, next}
        node = list;
        for (int guard = 0; node && guard < 4096; ++guard) {
            uint32_t entry = 0, next = 0, type = 0;
            int32_t delta = 0;
            if (!e.rd32(node, &entry) || !e.rd32(node + 4, &next)) break;
            if (entry && e.rd32(entry + 8, &type) && type) {
                uint32_t d = 0;
                e.rd32(entry + 4, &d);
                delta = (int32_t)d;
#if NVBOTS_MUTANT == 18
                if (delta) add(type, delta);
#else
                const int total = e.itemTotal(data, type);
                if (total) add(type, total);
#endif
            }
            node = next;
        }
    }
    out->set("items", items).set("partial", wanted.empty());
    return true;
}

// ---- dev.terminal / dev.terminalState ----
// TerminalMenu (id 0x421): ListBox at menu+0x68 (head node at lb+4), node {data,next}, data = item {Tile*@0, TerminalEntry*@4},
// entry text String {char*@0}. Picking calls the click handler 0x757F70(menu, -1, tile). The engine keeps NO per-entry "used" flag,
// so dev.terminalState reports what THIS bot picked (recorded by BotRpc), not what a human clicked earlier.
struct TermEntry { int idx; Addr32 tile; Addr32 entry; std::string text; };

inline bool terminalEntries(IEngine& e, const std::string& ref, std::vector<TermEntry>* out, Addr32* menuOut, std::string* err) {
    uint32_t mgr = 0, menu = 0, top = 0;
    if (!e.rd32(eng::kInterfaceMgrPtr, &mgr) || !mgr) { *err = "interface manager not ready"; return false; }
    top = e.topMenuId(mgr);
#if NVBOTS_MUTANT != 24
    if (top != 0x421) { *err = "terminal menu is not open (top menu id " + std::to_string(top) + "); activate the terminal first"; return false; }
#endif
    if (!e.rd32(eng::kTerminalMenuPtr, &menu) || !menu) { *err = "terminal menu object missing"; return false; }
    Addr32 want = 0, shown = 0;
    if (!e.resolveRef(ref, &want) || !want) { *err = "terminal reference not loaded"; return false; }
    if (!e.rd32(menu + eng::kTermRef, &shown) || shown != want) { *err = "the open terminal is a different reference"; return false; }
    uint32_t node = 0;
    e.rd32(menu + eng::kTermList + 4, &node);
    for (int i = 0; node && i < 64; ++i) {
        uint32_t item = 0, next = 0, tile = 0, entry = 0, text = 0;
        if (!e.rd32(node, &item) || !e.rd32(node + 4, &next)) break;
        if (item) {
            e.rd32(item, &tile);
            e.rd32(item + 4, &entry);
            TermEntry t{(int)out->size(), tile, entry, ""};
            if (entry && e.rd32(entry, &text) && text) {
                for (int k = 0; k < 120; ++k) {
                    uint8_t ch = 0;
                    if (!e.rd8(text + k, &ch) || !ch) break;
                    t.text.push_back((char)ch);
                }
            }
            out->push_back(t);
        }
        node = next;
    }
    *menuOut = menu;
    return true;
}

inline bool terminalPick(IEngine& e, const std::string& ref, int entry, Value* out, std::string* err) {
    std::vector<TermEntry> es;
    Addr32 menu = 0;
    if (!terminalEntries(e, ref, &es, &menu, err)) return false;
    Value names = Value::array();
    for (const TermEntry& t : es) names.push(t.text);
    out->set("entries", names);
    if (entry < 0 || entry >= (int)es.size()) { *err = "entry index out of range (" + std::to_string(es.size()) + " entries shown)"; return false; }
#if NVBOTS_MUTANT == 23
    const TermEntry& pick = es[(size_t)(entry + 1 < (int)es.size() ? entry + 1 : entry)];
#else
    const TermEntry& pick = es[(size_t)entry];
#endif
    if (!pick.tile) { *err = "entry has no tile"; return false; }
    e.termClick(menu, pick.tile);
    out->set("used", pick.text).set("index", (int64_t)pick.idx);
    return true;
}

// ---- dev.countRefs {base, cell?} / dev.refsOf {base, cell?} ----
// Walks the references of base form `base` in a cell (default: the player's cell) through TESObjectCELL::objectList (cell+0xAC, an
// embedded tList head {data,next}); skips deleted/disabled refs. Offsets: ref->baseForm +0x20, ref->parentCell +0x40, form flags +0x08
// (disabled 0x800, deleted 0x20), refID +0x0C: from xNVSE headers, unverified at runtime.
inline bool walkRefs(IEngine& e, const std::string& base, const std::string& cell, std::vector<Addr32>* refs, std::string* err) {
    uint32_t baseId = 0, cellAddr = 0;
    if (!e.resolveFormId(base, &baseId)) { *err = "unknown base form " + base; return false; }
    if (cell.empty()) {
        Addr32 player = 0;
        if (!e.resolveRef("", &player) || !player || !e.rd32(player + 0x40, &cellAddr) || !cellAddr) { *err = "player cell unavailable"; return false; }
    } else if (!e.resolveForm(cell, &cellAddr) || !cellAddr) {
        *err = "cell not loaded";
        return false;
    }
    uint32_t node = cellAddr + 0xAC;
    for (int guard = 0; node && guard < 100000; ++guard) {
        uint32_t ref = 0, next = 0;
        if (!e.rd32(node, &ref) || !e.rd32(node + 4, &next)) break;
        if (ref) {
            uint32_t bf = 0, id = 0, flags = 0;
#if NVBOTS_MUTANT == 32
            const uint32_t skip = 0x20;
#else
            const uint32_t skip = 0x820;
#endif
            if (e.rd32(ref + 0x20, &bf) && bf && e.rd32(bf + eng::kFormId, &id) && id == baseId && e.rd32(ref + 0x08, &flags) && !(flags & skip))
                refs->push_back(ref);
        }
        node = next;
    }
    return true;
}

inline bool countRefs(IEngine& e, const std::string& base, const std::string& cell, Value* out, std::string* err) {
    std::vector<Addr32> refs;
    if (!walkRefs(e, base, cell, &refs, err)) return false;
    *out = Value::object();
    out->set("count", (int64_t)refs.size());
    return true;
}

// The references themselves ("Plugin.esm:HEX"), capped at 512: the fallback for finding what a Drop created when the OnDrop event is unavailable
// (list before, drop, list after, take the difference).
inline bool refsOf(IEngine& e, const std::string& base, const std::string& cell, Value* out, std::string* err) {
    std::vector<Addr32> refs;
    if (!walkRefs(e, base, cell, &refs, err)) return false;
    Value arr = Value::array();
    for (size_t i = 0; i < refs.size() && i < 512; ++i) arr.push(e.nameOfForm(refs[i]));
    *out = Value::object();
    out->set("refs", arr).set("count", (int64_t)refs.size());
    return true;
}

// ---- bot.fasttravel {marker} ----
// The engine's own map-menu path, minus the clicks: gate on the busy menus and on the engine's may-travel check (0x93D660), then
// do what the deferred worker 0x798A00 does after the player answers Yes: travel object (0x34 bytes) with the marker at +0x30, handed to
// the player (0x93BE30). The main loop moves the player on a later frame, so success here means "queued"; the caller waits for the cell change.
inline bool fastTravel(IEngine& e, const std::string& marker, Value* out, std::string* err) {
    Addr32 mk = 0, player = 0;
    if (!e.resolveRef(marker, &mk) || !mk || marker.empty()) { *err = "marker not loaded"; return false; }
    if (!e.resolveRef("", &player) || !player) { *err = "player not loaded"; return false; }
    uint32_t mgr = 0, menuKindId = 0, vatsMode = 0;
    if (e.rd32(eng::kInterfaceMgrPtr, &mgr) && mgr) menuKindId = e.topMenuId(mgr);
    e.rd32(eng::kVatsCamera + eng::kVatsMode, &vatsMode);
#if NVBOTS_MUTANT != 30
    if (vatsMode != 0 || menuKindId == 0x3EF || menuKindId == 0x3F1 || menuKindId == 0x420 || menuKindId == 0x3F0 || menuKindId == 0x421 || menuKindId == 0x41D) {
        *err = "busy: a loading, dialogue, VATS, container, terminal or barter menu is open";
        return false;
    }
#endif
    uint32_t pending = 0;
    if (e.rd32(player + addr::kPlayerPending, &pending) && pending) { *err = "busy: a travel or teleport is already pending"; return false; }
#if NVBOTS_MUTANT != 29
    if (!e.canFastTravel(player)) { *err = "the engine refuses fast travel right now (combat, enemies near, overencumbered, in air or blocked here)"; return false; }
#endif
    const Addr32 obj = e.newTravelObj();
    if (!obj) { *err = "could not allocate the travel object"; return false; }
#if NVBOTS_MUTANT == 31
    e.wr32(obj + 0x2C, mk);
#else
    e.wr32(obj + addr::kTravelObjMarker, mk);
#endif
    e.setPendingTravel(player, obj);
    out->set("genuine", true).set("queued", true);
    return true;
}

// ---- bot.drops: what the engine's OnDrop event reported ----
// xNVSE's event manager calls a native handler with (dropper, droppedRef) when something is dropped, from Cell and world code (the same
// point the OnDrop script event fires). The handler records strings only, so this class is game-free. Correlate by base form plus a time window.
struct DropRec { int64_t seq; std::string ref, base, dropper; uint64_t atMs; };
class DropLog {
public:
    void record(const std::string& ref, const std::string& base, const std::string& dropper, uint64_t nowMs) {
        std::lock_guard<std::mutex> lk(mu_);
        recs_.push_back({++seq_, ref, base, dropper, nowMs});
        if (recs_.size() > 256) recs_.erase(recs_.begin());
    }
    int64_t seq() {
        std::lock_guard<std::mutex> lk(mu_);
        return seq_;
    }
    // drops newer than `since`, of `base` when given, no older than windowMs
    Value query(int64_t since, const std::string& base, uint64_t nowMs, uint64_t windowMs) {
        std::lock_guard<std::mutex> lk(mu_);
        Value arr = Value::array();
        for (const DropRec& r : recs_) {
            if (r.seq <= since) continue;
#if NVBOTS_MUTANT != 27
            if (!base.empty() && r.base != base) continue;
#endif
#if NVBOTS_MUTANT != 28
            if (nowMs > r.atMs && nowMs - r.atMs > windowMs) continue;
#endif
            Value v = Value::object();
            v.set("seq", r.seq).set("ref", r.ref).set("base", r.base).set("dropper", r.dropper);
            arr.push(v);
        }
        return arr;
    }

private:
    std::mutex mu_;
    std::vector<DropRec> recs_;
    int64_t seq_ = 0;
};

// ---- bot.vats / test.vats.hit : a tick-driven job that drives VATS through the input driver ----
// Control sequence (UNVERIFIED in game): tap Vats -> wait mode != 0 -> cycle targets with Right until the selected ref is the wanted
// one -> tap Activate (accept) -> wait for playback (mode 4) -> done when mode returns to 0. Limb choice is not implemented.
class VatsJob {
public:
    enum Phase { kIdle, kEnter, kTarget, kAccept, kPlayback, kDone, kFailed };
    bool start(IEngine& e, const std::string& ref, const std::string& limb, uint64_t now, std::string* err) {
#if NVBOTS_MUTANT != 22
        if (!limb.empty()) { *err = "unsupported: limb selection in VATS is not implemented"; return false; }
#else
        (void)limb;
#endif
        if (phase_ == kEnter || phase_ == kTarget || phase_ == kAccept || phase_ == kPlayback) { *err = "a VATS job is already running"; return false; }
        if (!e.resolveRef(ref, &want_) || !want_) { *err = "target not loaded"; return false; }
        uint32_t mode = 0;
        e.rd32(eng::kVatsCamera + eng::kVatsMode, &mode);
        if (mode != 0) { *err = "VATS is already active"; return false; }
        phase_ = kEnter; reason_.clear(); t0_ = tPhase_ = now; cycles_ = 0; sawPlayback_ = false; tapped_ = false;
        return true;
    }
    // Called every frame while running. `tap(ctrl)` presses a control through the input driver.
    template <class Tap>
    void tick(IEngine& e, uint64_t now, Tap tap) {
        if (phase_ == kIdle || phase_ == kDone || phase_ == kFailed) return;
        uint32_t mode = 0;
        e.rd32(eng::kVatsCamera + eng::kVatsMode, &mode);
        auto fail = [&](const char* why) { phase_ = kFailed; reason_ = why; };
        if (now - t0_ > 25000) return fail("timeout");
        switch (phase_) {
            case kEnter:
                if (!tapped_) { tap(16); tapped_ = true; tPhase_ = now; }
                else if (mode != 0) { phase_ = kTarget; tPhase_ = now; }
                else if (now - tPhase_ > 3000) fail("vats_not_entered (no weapon, no target in view, or no AP)");
                break;
            case kTarget: {
                if (mode == 4) { phase_ = kPlayback; break; }
                if (now - tPhase_ < 500) break;
                if (selected(e) == want_) { tap(5); phase_ = kAccept; tPhase_ = now; break; }
                if (cycles_ >= 12
#if NVBOTS_MUTANT == 19
                    && false
#endif
                    ) return fail("target_not_selectable (cycled all targets)");
                tap(3); ++cycles_; tPhase_ = now;
                break;
            }
            case kAccept:
                if (mode == 4) { phase_ = kPlayback; sawPlayback_ = true; tPhase_ = now; }
                else if (now - tPhase_ > 3000) fail("accept_had_no_effect");
                break;
            case kPlayback:
                if (mode == 4) sawPlayback_ = true;
                if (mode == 0) { if (sawPlayback_) phase_ = kDone; else fail("left_vats_without_playback"); }
                break;
            default: break;
        }
    }
    Phase phase() const { return phase_; }
    const std::string& reason() const { return reason_; }
    bool running() const { return phase_ == kEnter || phase_ == kTarget || phase_ == kAccept || phase_ == kPlayback; }
    static const char* name(Phase p) {
        static const char* const n[] = {"idle", "enter", "target", "accept", "playback", "done", "failed"};
        return n[p];
    }

private:
    Addr32 selected(IEngine& e) {
        uint32_t menu = 0, ref = 0;
        uint8_t ok = 0;
        if (!e.rd32(eng::kVatsMenuPtr, &menu) || !menu) return 0;
        e.rd8(menu + eng::kVatsSelValid, &ok);
        if (!ok) return 0;
        e.rd32(menu + eng::kVatsSelRef, &ref);
        return ref;
    }
    Phase phase_ = kIdle;
    Addr32 want_ = 0;
    std::string reason_;
    uint64_t t0_ = 0, tPhase_ = 0;
    int cycles_ = 0;
    bool sawPlayback_ = false, tapped_ = false;
};

}  // namespace nvbots
