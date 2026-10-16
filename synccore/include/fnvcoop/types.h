// fnvcoop core shared types. Game-free, C++17.
#pragma once
#include <cstdint>
#include <string>
#include <tuple>

namespace fnvcoop {

// Player slot. 0 is always the host. Slots index per-player ledgers and Recipient masks.
using PlayerId = uint8_t;
constexpr uint8_t kMaxSlots = 8;  // hard cap (mask is one byte)
constexpr PlayerId kHostPlayer = 0;
constexpr PlayerId kNoPlayer = 0xFF;

// A form on the wire: (plugin file name, plugin-local id). Never a raw load-order FormID.
// Plugin names are compared byte-exact; producers should lower-case them (see FormRef::make).
struct FormRef {
    std::string plugin;
    uint32_t localId = 0;

    FormRef() = default;
    FormRef(std::string p, uint32_t id) : plugin(std::move(p)), localId(id) {}
    static FormRef make(std::string plugin, uint32_t id);  // lower-cases the plugin name

    friend bool operator==(const FormRef& a, const FormRef& b) { return a.localId == b.localId && a.plugin == b.plugin; }
    friend bool operator!=(const FormRef& a, const FormRef& b) { return !(a == b); }
    friend bool operator<(const FormRef& a, const FormRef& b) {
        return std::tie(a.plugin, a.localId) < std::tie(b.plugin, b.localId);
    }
};

enum class CauseKind : uint8_t {
    Unknown = 0,
    QuestResult = 1,     // quest stage result script (host-run)
    DialogueResult = 2,  // topic/info result script
    Console = 3,
    Loot = 4,    // pickup / container
    Combat = 5,
    Script = 6,  // other script (object/effect script)
    Intent = 7,  // client-reported local action
    Count_
};

struct Cause {
    CauseKind kind = CauseKind::Unknown;
    FormRef source;   // quest / info / script form that produced the effect (may be empty)
    int32_t aux = 0;  // e.g. stage number
    friend bool operator==(const Cause& a, const Cause& b) {
        return a.kind == b.kind && a.source == b.source && a.aux == b.aux;
    }
};

// Who a player-targeted effect applies to.
//  Default -> host policy decides at commit time; after commit it is always Mask.
struct Recipient {
    enum Mode : uint8_t { Default = 0, Origin = 1, All = 2, Mask = 3 };
    Mode mode = Default;
    uint8_t mask = 0;  // bit i = slot i (valid when mode == Mask)

    static Recipient dflt() { return {Default, 0}; }
    static Recipient origin() { return {Origin, 0}; }
    static Recipient all() { return {All, 0}; }
    static Recipient slots(uint8_t m) { return {Mask, m}; }
    static Recipient slot(PlayerId p) { return {Mask, static_cast<uint8_t>(p < 8 ? 1u << p : 0u)}; }
    bool includes(PlayerId p) const { return mode == Mask && p < 8 && ((mask >> p) & 1u); }
    friend bool operator==(const Recipient& a, const Recipient& b) { return a.mode == b.mode && a.mask == b.mask; }
};

}  // namespace fnvcoop
