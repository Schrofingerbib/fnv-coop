// Raw 32-bit layouts of the game structures dialogue needs, as byte offsets (L8).
//
// Why offsets and not the xNVSE structs: this header is game-free, so layout reading is unit-tested natively on a
// synthetic memory image (engine_test.cpp). All pointers are 32-bit game addresses (FalloutNV.exe is x86).
// Provenance: offsets come from JIP-LN-NVSE nvse/GameUI.h (DialogMenu) or xNVSE GameForms.h (TESTopicInfo,
// TESTopicInfoResponse). Items marked UNVERIFIED have not been confirmed against a live game yet (see the live
// verification checklist in internal lane notes).
#pragma once
#include <cstdint>
#include <cstring>
#include <string>
#include <vector>

namespace fndlg {

using Addr32 = uint32_t;

// Memory access abstraction: process memory in the game, a byte vector in tests.
struct IMemory {
    virtual ~IMemory() = default;
    virtual bool read(Addr32 a, void* out, size_t n) const = 0;
};

inline bool readU32(const IMemory& m, Addr32 a, uint32_t& v) { return m.read(a, &v, 4); }
inline bool readU8(const IMemory& m, Addr32 a, uint8_t& v) { return m.read(a, &v, 1); }

// ---- DialogMenu (singleton pointer at *(Addr32*)0x11D9510, size 0x13C, vtbl 0x107257C)
// Layout re-verified against a reference exe 1.4.0.525 (docs/re/dialogmenu-menus-vats-inventory.md, plugin/dialogue/tests/test_engine_pins.py):
// the xNVSE/JIP header offsets +0x78 / +0x94 / +0xE4 / +0x110 are WRONG (never read by the menu code, +0x94 is inside a char buffer).
// The offered choices live in a ListBox; the spoken INFO is a global.
namespace dialog_menu {
constexpr Addr32 kSingletonPtr = 0x11D9510;
constexpr Addr32 kSpokenInfoGlobal = 0x11D9518;  // TESTopicInfo* being spoken (the END result script runs only while it is non-zero)
constexpr uint32_t kSize = 0x13C;
constexpr uint32_t kState = 0x28;          // u32; 0 opening, 1 line playing, 2 idle (waiting for a pick), 3 line queued, 4 closing, 5 barter
constexpr uint32_t kListBox = 0x70;        // ListBox* of the offered topics (see list_box)
constexpr uint32_t kPartnerRef = 0x80;     // TESObjectREFR* the player talks to
constexpr uint32_t kIsBarterShown = 0x138; // likely
constexpr uint32_t kStateOpening = 0, kStatePlaying = 1, kStateIdle = 2, kStateQueued = 3, kStateClosing = 4, kStateBarter = 5;
}  // namespace dialog_menu

// ---- the choice ListBox: lb+0 selected node, lb+4 head node pointer; node {item* @0, next @4}; item (0x2C bytes, ctor 0x83DB00)
namespace list_box {
constexpr uint32_t kSelectedNode = 0, kHead = 4;
constexpr uint32_t kNodeItem = 0, kNodeNext = 4;
constexpr uint32_t kItemInfo = 0x18;       // TESTopicInfo* (written after construction; one best INFO per topic)
constexpr uint32_t kItemTopic = 0x1C;      // TESTopic* (constructor argument, stored verbatim)
constexpr uint32_t kItemSize = 0x2C;
constexpr uint32_t kMaxNodes = 64;         // walk guard (the menu shows at most a few dozen topics)
}  // namespace list_box

// ---- INFO response chain. The INFO object has NO list head field: TESTopicInfo::GetResponses loads the chain lazily from the ESM
// (offset TESTopicInfo+0x4C) into either a caller cell or the engine's single-entry cache, and the engine speaks from that cache.
//   0x61E780  thiscall(TESTopicInfo* info, Cell* out /* 0 = use the cache */) -> Cell*, ret 4
//   *0x11CBF3C = the INFO whose chain sits in the cache; 0x11CC07C = the cache Cell: dword 0 = head TESTopicInfoResponse*, chained by +0x28
// (nodes are appended with 0x61CC60 / 0x61CC90 which write cell[0] or tail+0x28; node ctor 0x616CE0 zeroes +0x20/+0x24/+0x28).
namespace response_cache {
constexpr Addr32 kLoaderFn = 0x61E780;
constexpr Addr32 kCachedInfoGlobal = 0x11CBF3C;
constexpr Addr32 kCell = 0x11CC07C;
}  // namespace response_cache

// ---- TESTopicInfo (size 0x50) -- xNVSE GameForms.h
namespace topic_info {
constexpr uint32_t kSize = 0x50;
constexpr uint32_t kSaidOnce = 0x22;
constexpr uint32_t kType = 0x23;
constexpr uint32_t kNextSpeaker = 0x24;
constexpr uint32_t kFlags1 = 0x25;
constexpr uint32_t kFlags2 = 0x26;
constexpr uint32_t kPrompt = 0x28;          // String {char* data; u16 len; u16 cap}
constexpr uint32_t kAddTopics = 0x30;
constexpr uint32_t kRelated = 0x38;
constexpr uint32_t kSpeaker = 0x3C;
constexpr uint32_t kActorValueOrPerk = 0x40;
constexpr uint32_t kSpeechChallenge = 0x44;
constexpr uint32_t kUnk48 = 0x48;           // zeroed by the constructor and never read as a list head: NOT the response chain (see response_cache)
constexpr uint32_t kConditions = 0x18;      // tList<Condition*> {data @0x18, next @0x1C}
constexpr uint32_t kModInfoFileOffset = 0x4C; // where GetResponses reads the chain from
// flags1 = first ENAM byte: 0x01 goodbye, 0x04 say once, 0x10 info refusal (ESM format; the engine confirms 0x10: 0x61F260 tests
// flags1 & 0x10, and TESTopicInfo::MarkSaid 0x61F220 (thiscall(info), plain ret) sets [info+0x22] = 1 and calls vtbl+0x48(0x80000000)
// (form "modified" flag for the save) unless that bit is set; RunResultScript 0x61F170 calls MarkSaid after every result script.
constexpr uint8_t kFlagGoodbye = 0x01;
constexpr uint8_t kFlagSayOnce = 0x04;
constexpr uint8_t kFlagInfoRefusal = 0x10;
constexpr Addr32 kMarkSaidFn = 0x61F220;
}  // namespace topic_info

// ---- TESTopicInfoResponse (xNVSE GameForms.h)
namespace response {
constexpr uint32_t kEmotionType = 0x00;
constexpr uint32_t kEmotionValue = 0x04;    // init 0x32
constexpr uint32_t kResponseNumber = 0x0C;
constexpr uint32_t kSound = 0x10;
constexpr uint32_t kFlags = 0x14;           // init 1
constexpr uint32_t kText = 0x18;            // String
constexpr uint32_t kNext = 0x28;
constexpr uint32_t kSize = 0x2C;
}  // namespace response

inline bool readString(const IMemory& m, Addr32 stringAddr, std::string& out, uint32_t maxLen = 1024) {
    uint32_t data = 0;
    uint16_t len = 0;
    if (!readU32(m, stringAddr, data) || !m.read(stringAddr + 4, &len, 2)) return false;
    out.clear();
    if (!data) return true;
    if (len > maxLen) return false;
    out.resize(len);
    return len == 0 || m.read(data, &out[0], len);
}

}  // namespace fndlg
