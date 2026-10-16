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
namespace dialog_menu {
constexpr Addr32 kSingletonPtr = 0x11D9510;
constexpr uint32_t kSize = 0x13C;
constexpr uint32_t kInfo078 = 0x078;       // TESTopicInfo* currently spoken
constexpr uint32_t kPartnerRef = 0x080;    // TESObjectREFR* the player talks to
constexpr uint32_t kInfos094 = 0x094;      // TESTopicInfo*[16] offered choices
constexpr uint32_t kInfos094Count = 16;
constexpr uint32_t kInfos0E4 = 0x0E4;      // TESTopicInfo*[8]
constexpr uint32_t kInfos0E4Count = 8;
constexpr uint32_t kInfos110 = 0x110;      // TESTopicInfo*[6]
constexpr uint32_t kInfos110Count = 6;
constexpr uint32_t kIsBarterShown = 0x138;
}  // namespace dialog_menu

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
constexpr uint32_t kUnk48 = 0x48;           // UNVERIFIED: candidate head of the response list (see probe)
constexpr uint8_t kFlagGoodbye = 0x01;      // UNVERIFIED bit mapping of flags1
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
