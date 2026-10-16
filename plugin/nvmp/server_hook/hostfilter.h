// Game-free policy logic for the NV:MP story-server ownership hook.
// Everything here is plain C++17 (no Windows, no game structs) so it is unit-testable in a fake harness.
//
// Policy ("host owns flagged refs"):
//   * An encounter message for a FormID in the host-owned set that comes from a non-host sender is dropped,
//     so the first-come ownership rule can only ever hand the object to the host.
//   * While the host is connected, the server's hot-swap owner reselection is suppressed for host-owned refs
//     (otherwise the closest guest would take over when the host leaves PVS).
//   * Fail open: if the host cannot be identified, nothing is dropped.
#pragma once
#include <cstddef>
#include <cstdint>
#include <memory>
#include <mutex>
#include <string>
#include <string_view>
#include <unordered_set>
#include <vector>

namespace nvmp::hook {

// ---- host-owned FormID set -------------------------------------------------------------------
// The server's active plugin order (index = FormID high byte). Names are matched case-insensitively.
struct LoadOrder {
    std::vector<std::string> plugins;  // as written, in load order
    std::string source;                // where it came from (for the log)
    bool guessed = false;              // true when synthesised (file times / canonical default), not read from a list
    int IndexOf(std::string_view name) const;  // -1 if absent
};
// plugins.txt / loadorder.txt format: one plugin per line, '#' comments, optional '*' prefix; only *.esm/*.esp/*.esl kept.
// FalloutNV.esm is forced to index 0 (the server requires it at the top). Duplicates are dropped.
LoadOrder ParseLoadOrder(std::string_view text, std::string source);
// What tools/questdata/loadorder.json says (observed in game; FNV sorts ESMs by file time): FalloutNV, DeadMoney,
// HonestHearts, OldWorldBlues, LonesomeRoad, TribalPack, MercenaryPack, ClassicPack, CaravanPack, GunRunnersArsenal.
LoadOrder CanonicalAssumedOrder();

struct HostOwnedSet {
    std::unordered_set<uint32_t> ids;
    size_t lines = 0;       // non-empty, non-comment lines
    size_t rejected = 0;    // lines that failed to parse
    size_t named = 0;       // "Plugin.esm:LOCALID" lines resolved against the load order
    size_t remapped = 0;    // plain-hex lines re-resolved through the file's own "# load order:" header
    size_t unresolved = 0;  // dropped: plugin not in the server's load order, or no load order available for a named line
    size_t badIndex = 0;    // plain-hex lines whose mod index is >= number of loaded plugins (kept, but warned)
    std::vector<std::string> errors;    // first few parse errors, "line N: text"
    std::vector<std::string> warnings;  // load-order problems; the DLL logs each one loudly
    bool Contains(uint32_t id) const { return ids.find(id) != ids.end(); }
};

// Line forms (blank lines and lines starting with # or ; are ignored; trailing "# ..." / "; ..." comments allowed):
//   Plugin.esm:104C6D     plugin name + local id: resolved to the runtime FormID with `actual` (preferred form)
//   00104C6D / 0x...      runtime FormID. If the file header has "# load order: 00=A.esm, 01=B.esm, ..." and `actual`
//                         is given, the id is re-resolved by plugin name (robust against a different order);
//                         otherwise it is used as-is and only checked against the number of loaded plugins.
// FormID 0 is rejected. `actual` may be null (tests / no list): named lines then count as unresolved.
HostOwnedSet ParseHostOwned(std::string_view text, const LoadOrder* actual = nullptr);

// ---- config (server_hook.cfg: key=value, # comments) -----------------------------------------
enum class HostMode { Auto, Loopback, First };
struct HookConfig {
    bool enabled = true;           // master switch; false => never drop, never block
    bool blockHandover = true;     // suppress owner reselection for host-owned refs while host present
    bool dropActors = true;        // apply the encounter filter to encounter type 0 (actor)
    bool dropReferences = true;    // types 1 (reference), 2 (door), 3 (container)
    HostMode mode = HostMode::Auto;
    std::vector<std::string> hostAddresses;  // extra addresses that count as the host (besides loopback)
    bool logAll = false;           // also log decisions for unflagged forms (very chatty)
    unsigned pollMs = 1000;        // hostowned.txt change-poll interval
};
HookConfig ParseConfig(std::string_view text, std::vector<std::string>* warnings = nullptr);

// ---- verdicts ---------------------------------------------------------------------------------
enum class EncounterVerdict {
    AllowDisabled,     // hook disabled by config
    AllowNotFlagged,   // FormID not in the host-owned set
    AllowTypeSkipped,  // encounter type not covered by dropActors/dropReferences
    AllowHostSender,   // flagged, but the sender is the host
    AllowHostUnknown,  // flagged, but the host could not be identified (fail open)
    DropGuest,         // flagged, sender is a guest: drop
};
const char* ToString(EncounterVerdict v);
inline bool IsDrop(EncounterVerdict v) { return v == EncounterVerdict::DropGuest; }

enum class SenderKind { Unknown, Host, Guest };

EncounterVerdict DecideEncounter(const HostOwnedSet& set, const HookConfig& cfg, uint32_t formId, uint32_t type,
                                 SenderKind sender);

enum class HandoverVerdict { AllowDisabled, AllowNotFlagged, AllowHostAbsent, Block };
const char* ToString(HandoverVerdict v);
inline bool IsBlock(HandoverVerdict v) { return v == HandoverVerdict::Block; }
HandoverVerdict DecideHandover(const HostOwnedSet& set, const HookConfig& cfg, uint32_t formId, bool hostPresent);

// ---- peers / host identification ---------------------------------------------------------------
bool IsLoopbackAddress(std::string_view addr);

struct PeerInfo {
    uintptr_t ptr = 0;     // connection object pointer as seen by the connect handler
    uint32_t key0 = 0;     // candidate identity dwords read from the object (offsets +4 and +0x10)
    uint32_t key1 = 0;
    std::string addr;
    bool loopback = false;  // loopback or listed in HookConfig::hostAddresses
};

// A sender as seen by the encounter handler: the connection pointer and its identity dword (+4).
struct SenderRef {
    uintptr_t ptr = 0;
    uint32_t key = 0;
};

class PeerTable {
public:
    void Add(const PeerInfo& p);                 // replaces an entry with the same pointer
    void Remove(uintptr_t ptr);
    // Look the sender up by connection pointer first, then by identity dword. nullptr if unknown.
    const PeerInfo* Find(const SenderRef& s) const;
    size_t size() const { return peers_.size(); }
    bool HasLoopback() const {
        for (const auto& e : peers_) if (e.loopback) return true;
        return false;
    }
private:
    std::vector<PeerInfo> peers_;
};

struct HostResolution {
    SenderKind kind = SenderKind::Unknown;
    const char* reason = "";
};

// firstCharKey: identity dword of the first connected character (0 = unknown/none).
// Auto: table lookup (loopback flag) if the sender is a known peer; the first-character fallback is used only
//       while NO loopback peer is known at all (once a host is identified, an unknown sender stays Unknown => fail open).
// Loopback: table lookup only. First: first-character only.
HostResolution ResolveSender(const PeerTable& peers, const SenderRef& sender, uint32_t firstCharKey, HostMode mode);

// True when the host is still connected: a character exists whose identity dword matches a loopback peer
// (Auto/Loopback) or equals firstCharKey (First/Auto fallback).
bool IsHostPresent(const PeerTable& peers, const std::vector<uint32_t>& connectedCharKeys, uint32_t firstCharKey,
                   HostMode mode);

// ---- hot-reloaded store ------------------------------------------------------------------------
// Readers call Get() (cheap shared_ptr copy under a short lock). PollOnce() is called from a background
// thread; it reloads when the file's size/mtime stamp changed. Never called on the game thread.
class HostOwnedStore {
public:
    HostOwnedStore();
    std::shared_ptr<const HostOwnedSet> Get() const;
    // Returns true if a reload happened. `stamp` is any cheap change token for the file (mtime/size hash);
    // `text` is only evaluated lazily via the callback so unchanged files are not read.
    template <class ReadFn>
    bool PollOnce(uint64_t stamp, ReadFn&& read) {
        if (stamp == lastStamp_) return false;
        lastStamp_ = stamp;
        std::shared_ptr<const LoadOrder> lo;
        { std::lock_guard<std::mutex> g(mu_); lo = order_; }
        Set(ParseHostOwned(read(), lo.get()));
        return true;
    }
    // The order used to resolve entries. Takes effect on the next (re)load; call ForceReload() to apply now.
    void SetLoadOrder(LoadOrder lo) {
        auto p = std::make_shared<const LoadOrder>(std::move(lo));
        std::lock_guard<std::mutex> g(mu_);
        order_ = std::move(p);
    }
    std::shared_ptr<const LoadOrder> GetLoadOrder() const { std::lock_guard<std::mutex> g(mu_); return order_; }
    void ForceReload() { lastStamp_ = ~0ull; }
    void Set(HostOwnedSet s);
    uint64_t reloads() const { return reloads_; }
private:
    mutable std::mutex mu_;
    std::shared_ptr<const HostOwnedSet> cur_;
    std::shared_ptr<const LoadOrder> order_;
    uint64_t lastStamp_ = ~0ull;
    uint64_t reloads_ = 0;
};

}  // namespace nvmp::hook
