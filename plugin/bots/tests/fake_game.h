// Synthetic game memory + call recorder implementing nvbots::IEngine for the game-free tests.
#pragma once
#include <map>
#include <string>
#include <vector>

#include "../bot_engine.h"

namespace nvbots_test {

using nvbots::Addr32;

struct FakeGame : nvbots::IEngine {
    std::map<Addr32, uint8_t> mem;
    std::vector<std::string> calls;
    uint32_t tick = 100000;
    Addr32 selectedItem = 0;
    uint32_t topId = 0;
    std::map<Addr32, bool> special;       // info -> special (flag 0x80, refused)
    std::map<Addr32, bool> speechCheck;   // info -> speech check / challenge (allowed)
    bool canTravel = true;
    Addr32 travelObj = 0x3B000000;
    std::map<Addr32, int> totals;         // form -> total count
    std::map<std::string, Addr32> forms;  // "Plugin.esm:HEX" -> form pointer
    std::map<std::string, uint32_t> formIds;
    Addr32 player = 0x40000000;
    int speakResult = 3;

    static constexpr Addr32 kDlg = 0x30000000, kLb = 0x31000000, kItems = 0x32000000, kInfos = 0x33000000, kMgr = 0x34000000,
                            kVmenu = 0x35000000, kData = 0x36000000, kCell = 0x37000000;

    void set32(Addr32 a, uint32_t v) { for (int i = 0; i < 4; ++i) mem[a + i] = uint8_t(v >> (8 * i)); }
    void set8(Addr32 a, uint8_t v) { mem[a] = v; }
    uint32_t get32(Addr32 a) { uint32_t v = 0; rd32(a, &v); return v; }

    bool rd32(Addr32 a, uint32_t* v) override {
        uint32_t r = 0;
        for (int i = 0; i < 4; ++i) {
            auto it = mem.find(a + i);
            if (it == mem.end()) { *v = 0; return false; }
            r |= uint32_t(it->second) << (8 * i);
        }
        *v = r;
        return true;
    }
    bool rd8(Addr32 a, uint8_t* v) override {
        auto it = mem.find(a);
        if (it == mem.end()) { *v = 0; return false; }
        *v = it->second;
        return true;
    }
    bool wr8(Addr32 a, uint8_t v) override { mem[a] = v; calls.push_back("wr8 " + hex(a) + "=" + std::to_string(v)); return true; }
    bool wr32(Addr32 a, uint32_t v) override { set32(a, v); calls.push_back("wr32 " + hex(a) + "=" + hex(v)); return true; }
    uint32_t nowTick() override { return tick; }

    // ---- dialogue: n choices with INFO form ids 0x1000+i
    int nItems = 0;
    void buildDialogue(int n, uint32_t state = 2) {
        nItems = n;
        set32(nvbots::eng::kDialogMenuPtr, kDlg);
        set32(kDlg + nvbots::eng::kDlgState, state);
        set32(kDlg + nvbots::eng::kDlgListBox, kLb);
        set32(kDlg + nvbots::eng::kDlgClickGate, 0);
        set32(kDlg + nvbots::eng::kDlgPending, 1);
        for (int i = 0; i < n; ++i) {
            const Addr32 item = kItems + i * 0x100, info = kInfos + i * 0x100;
            set32(item + nvbots::eng::kItemInfo, info);
            set8(item + nvbots::eng::kItemFlag, 7);
            set32(info + nvbots::eng::kFormId, 0x1000 + i);
        }
    }
    bool lbSelect(Addr32, int idx) override {
        calls.push_back("select " + std::to_string(idx));
        if (idx < 0 || idx >= nItems) return false;
        selectedItem = kItems + idx * 0x100;
        return true;
    }
    Addr32 lbSelected(Addr32) override { return selectedItem; }
    Addr32 itemInfo(Addr32 item) override { return get32(item + nvbots::eng::kItemInfo); }
    bool infoSpecial(Addr32 info) override { return special[info]; }
    bool infoSpeechCheck(Addr32 info) override { return speechCheck[info]; }
    void dlgItemClick(Addr32, Addr32 item) override { calls.push_back("click " + hex(item)); }
    void lbAfter(Addr32) override { calls.push_back("after"); }
    int dlgSpeak(Addr32, int flag) override { calls.push_back("speak " + std::to_string(flag)); return speakResult; }

    // ---- menus
    void buildMenus() { set32(nvbots::eng::kInterfaceMgrPtr, kMgr); set32(nvbots::eng::kVatsCamera + nvbots::eng::kVatsMode, 0); }
    uint32_t topMenuId(Addr32) override { return topId; }
    void termClick(Addr32, Addr32 tile) override { calls.push_back("termclick " + hex(tile)); }
    // terminal menu with entries named by `names`; entry i has tile 0x3A000000+i*0x10
    static constexpr Addr32 kTerm = 0x3A000000;
    void buildTerminal(const std::vector<std::string>& names, Addr32 ref) {
        set32(nvbots::eng::kInterfaceMgrPtr, kMgr);
        set32(nvbots::eng::kTerminalMenuPtr, kTerm);
        set32(kTerm + nvbots::eng::kTermRef, ref);
        Addr32 node = kTerm + 0x1000;
        set32(kTerm + nvbots::eng::kTermList + 4, node);
        for (size_t i = 0; i < names.size(); ++i) {
            const Addr32 item = kTerm + 0x2000 + Addr32(i) * 0x20, entry = kTerm + 0x3000 + Addr32(i) * 0x20, text = kTerm + 0x4000 + Addr32(i) * 0x40;
            set32(item, kTerm + 0x10 + Addr32(i) * 0x10); set32(item + 4, entry); set32(entry, text);
            for (size_t k = 0; k <= names[i].size(); ++k) set8(text + Addr32(k), k < names[i].size() ? (uint8_t)names[i][k] : 0);
            set32(node, item);
            const Addr32 next = i + 1 < names.size() ? node + 0x10 : 0;
            set32(node + 4, next);
            node = next;
        }
    }

    // ---- inventory: entries {type, delta}
    void buildInventory(const std::vector<std::pair<Addr32, int>>& entries) {
        set32(player + 0x1000, kData);                               // containerData(player) result lives here
        Addr32 node = kData + 0x100;
        set32(kData, node);
        for (size_t i = 0; i < entries.size(); ++i) {
            const Addr32 entry = kData + 0x1000 + Addr32(i) * 0x20;
            set32(entry, 0); set32(entry + 4, uint32_t(entries[i].second)); set32(entry + 8, entries[i].first);
            set32(node, entry);
            const Addr32 next = i + 1 < entries.size() ? node + 0x10 : 0;
            set32(node + 4, next);
            node = next;
        }
        if (entries.empty()) { set32(kData + 0x100, 0); set32(kData + 0x104, 0); }
    }
    Addr32 containerData(Addr32 ref) override { return ref == player ? kData : 0; }
    int itemTotal(Addr32, Addr32 form) override { return totals[form]; }

    // ---- fast travel
    bool canFastTravel(Addr32) override { calls.push_back("cantravel"); return canTravel; }
    Addr32 newTravelObj() override { calls.push_back("newtravel"); return travelObj; }
    void setPendingTravel(Addr32 p, Addr32 obj) override { calls.push_back("pending " + hex(obj)); set32(p + nvbots::addr::kPlayerPending, obj); }

    // ---- references
    bool resolveRef(const std::string& r, Addr32* out) override {
        if (r.empty()) { *out = player; return true; }
        auto it = forms.find(r);
        if (it == forms.end()) return false;
        *out = it->second;
        return true;
    }
    bool resolveForm(const std::string& f, Addr32* out) override { return resolveRef(f, out) && !f.empty(); }
    bool resolveFormId(const std::string& f, uint32_t* out) override {
        auto it = formIds.find(f);
        if (it == formIds.end()) return false;
        *out = it->second;
        return true;
    }
    std::string nameOfForm(Addr32 form) override { return "Form:" + hex(form); }

    static std::string hex(uint32_t v) { char b[16]; std::snprintf(b, sizeof b, "%X", v); return b; }
};

}  // namespace nvbots_test
