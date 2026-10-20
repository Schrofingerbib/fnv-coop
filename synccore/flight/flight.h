// Flight recorder (T1a): a compact binary trace of everything one machine sent, received, captured and applied.
// Format: docs/tools/flight-format.md (NVFR v1). Game-free, C++17. The ring lives in memory; it is written to a
// .nvfr file on desync, crash, shutdown or an explicit dump.
//
// Hot-path cost: one mutex lock, one memcpy of the datagram (at most maxRawBytes), no allocation, no decoding.
// Decoding (session, epoch, seq of a message) happens at dump time.
#pragma once
#include <atomic>
#include <cstdint>
#include <functional>
#include <mutex>
#include <string>
#include <utility>
#include <vector>
#include "fnvcoop/session.h"
#include "fnvcoop/transport.h"

namespace fnvcoop::flight {

constexpr uint16_t kFormatVersion = 1;
constexpr size_t kHeaderSize = 64;
constexpr size_t kRecordHeaderSize = 16;
constexpr size_t kNetPrefix = 36;   // bytes of a Net payload before the raw datagram

enum RecType : uint8_t { kMeta = 1, kNetOut = 2, kNetIn = 3, kTxnCaptured = 4, kTxnApplied = 5, kOwnership = 6, kDigest = 7, kMarker = 8 };
enum RecFlag : uint8_t { kFlagTruncated = 1, kFlagDerived = 2, kFlagDetail = 4 };   // kFlagDetail: Digest record carrying per-key (key, value hash) entries only, no shards   // kFlagDerived: Digest record built from a SyncDigest message at dump time
enum DumpReason : uint8_t { kDumpManual = 0, kDumpDesync = 1, kDumpCrash = 2, kDumpShutdown = 3, kDumpTest = 4 };
enum Role : uint8_t { kRoleHost = 0, kRoleClient = 1, kRoleSim = 2 };
enum OwnershipOp : uint8_t { kOwnGrant = 0, kOwnTransfer = 1, kOwnRelease = 2, kOwnRejectStale = 3, kOwnRejectEcho = 4, kOwnRejectNonOwner = 5 };
enum DigestCategory : uint8_t { kCatStory = 0, kCatActors = 1, kCatContainers = 2, kCatObjects = 3, kCatOther = 4, kCatWorld = 4 };   // 4: the real engine world dumped at a desync (bucketed + per-key detail)
enum MarkerKind : uint16_t { kMarkNote = 0, kMarkDesync = 1, kMarkJoin = 2, kMarkLeave = 3, kMarkTestStep = 4, kMarkSave = 5, kMarkLoad = 6, kMarkRebase = 7 };
constexpr uint8_t kNoSlot = 0xFF;

// TEST ONLY: each flag re-introduces one bug that the recorder's tests must catch (mutation twins).
struct FlightMutations {
    bool dropNewest = false;       // a full ring drops the NEW record instead of the oldest
    bool noNetIn = false;          // received datagrams are not recorded
    bool zeroSeq = false;          // decoded session / epoch / seq fields are left zero
    bool noTruncFlag = false;      // a cut datagram is not flagged
    bool noDroppedCount = false;   // the header says nothing was dropped
    bool noAppliedFlags = false;   // appliedFlags (repair / baseline / catchUp) are always 0
    bool noDerivedDigest = false;  // SyncDigest messages do not yield a Digest record
    bool bucketsIgnoreValue = false;   // bucket hashes cover keys only: a changed value is invisible
    bool detailDropsKey = false;       // digestDetail loses the last entry
};
inline FlightMutations& flightMutations() { static FlightMutations m; return m; }

struct Config {
    size_t ringBytes = 16u << 20;     // oldest records are overwritten when full
    size_t maxRawBytes = 48000;       // datagram bytes kept per Net record (the rest is cut, flagged)
    uint8_t slot = kNoSlot;
    uint8_t role = kRoleSim;
    std::string label = "nvcoop";     // goes into the Meta record and the dump file name
    std::string dir;                  // default folder for dumpToDir(), e.g. <instance>/nvcoop_flight
    uint32_t autoDumpMinIntervalMs = 30000;   // onDesync() dumps at most this often ...
    uint32_t autoDumpMax = 8;                 // ... and at most this many files per run
};

struct OwnershipEvent {
    uint8_t op = kOwnGrant;
    uint64_t actor = 0;
    uint32_t epoch = 0;
    uint8_t fromOwner = kNoSlot, toOwner = kNoSlot;
    uint8_t reason = 0;
    uint64_t txnId = 0;
};

struct DigestEvent {
    uint8_t category = kCatStory;
    uint32_t epoch = 0;
    uint64_t seq = 0;
    uint64_t hash = 0;
    uint32_t itemCount = 0;
    std::vector<uint32_t> shards;                        // optional
    std::vector<std::pair<uint64_t, uint32_t>> keys;     // optional per-key detail (at most kMaxDigestKeys are kept)
};
constexpr size_t kMaxDigestKeys = 4000;     // per Digest record; digestDetail() splits bigger lists over several records
constexpr size_t kDigestBuckets = 256;      // one-level Merkle: bucket = key & 0xFF

// Builds a digest with 256 order-independent bucket hashes over (key, value-hash) entries. `hash` covers all buckets and the count.
// Two machines whose entry lists differ in one value get exactly one differing bucket.
DigestEvent bucketedDigest(uint8_t category, uint32_t epoch, uint64_t seq, const std::vector<std::pair<uint64_t, uint32_t>>& entries);
// Same result straight from a ShadowState (all state entries, no per-entry objects): what the plugin records every quiet point.
DigestEvent bucketedDigestOf(uint8_t category, uint32_t epoch, uint64_t seq, const ShadowState& world);

class FlightRecorder {
public:
    explicit FlightRecorder(Config cfg = Config());

    // ---- clock and frame: real by default; a simulator injects its virtual clock ----
    void setClockSource(std::function<uint64_t()> nowUs);    // microseconds, monotonic; recorder time = value - value at this call
    void setFrame(uint32_t frame) { frame_.store(frame, std::memory_order_relaxed); }
    uint32_t frame() const { return frame_.load(std::memory_order_relaxed); }
    void setIdentity(uint8_t slot, uint8_t role);
    void setSession(uint32_t session, uint32_t epoch);       // for the file header
    void setWallStart(uint64_t unixMs) { wallStartMs_ = unixMs; }   // tests: makes the file deterministic
    void setEnabled(bool on) { enabled_.store(on, std::memory_order_relaxed); }
    bool enabled() const { return enabled_.load(std::memory_order_relaxed); }

    // ---- recording ----
    void meta(const std::string& key, const std::string& value);   // goes into the header Meta record of the next dump
    void netOut(PeerId to, const Bytes& data, uint8_t channel = 0);
    void netIn(PeerId from, const Bytes& data);
    // Effects the local hooks captured and the session took. kind: 0 host commit input, 1 client intent.
    void captured(uint8_t kind, uint32_t intentId, uint64_t seq, const std::vector<Effect>& effects, CauseKind cause, uint32_t epoch);
    void applied(const Effect& e, const IEffectSink::Context& ctx);
    void ownership(const OwnershipEvent& e);
    void digest(const DigestEvent& d, uint8_t flags = 0);
    // Every entry of the state a bucketed digest was taken from, as Digest records flagged kFlagDetail (same category, epoch, seq).
    void digestDetail(uint8_t category, uint32_t epoch, uint64_t seq, const std::vector<std::pair<uint64_t, uint32_t>>& entries);
    void marker(uint16_t kind, const std::string& text);

    // ---- dumping ----
    // Writes the ring to `path`. Returns false on I/O failure. Never throws.
    bool dump(const std::string& path, uint8_t reason = kDumpManual);
    // Writes <cfg.dir>/<label>-<reason>-<unixms>.nvfr; returns the path or "" on failure (or when dir is empty).
    std::string dumpToDir(uint8_t reason = kDumpManual);
    // A desync was detected: marks the ring and, if a dir is configured, dumps (rate limited). Returns the path or "".
    std::string onDesync(const std::string& what);
    // Crash path: does not wait for the lock if another thread holds it. Best effort.
    std::string dumpCrash();

    struct Stats {
        uint64_t records = 0, dropped = 0, bytesRing = 0, dumps = 0, dumpFailures = 0, autoDumpsSkipped = 0;
    };
    Stats stats() const;
    const Config& config() const { return cfg_; }

private:
    void push(uint8_t type, uint8_t flags, const uint8_t* a, size_t an, const uint8_t* b = nullptr, size_t bn = 0);
    uint64_t nowUs() const;
    bool dumpImpl(const std::string& path, uint8_t reason, bool lock);

    Config cfg_;
    std::function<uint64_t()> clock_;
    uint64_t clock0_ = 0;
    uint64_t wallStartMs_ = 0;
    std::atomic<uint32_t> frame_{0};
    std::atomic<bool> enabled_{true};
    uint32_t session_ = 0, epoch_ = 0;
    std::vector<std::pair<std::string, std::string>> meta_;

    mutable std::mutex mu_;
    std::vector<uint8_t> ring_;
    size_t head_ = 0, used_ = 0;          // oldest entry offset, bytes in use
    uint64_t count_ = 0, dropped_ = 0, dumps_ = 0, dumpFailures_ = 0, autoSkipped_ = 0;
    uint64_t lastAutoDumpMs_ = 0;
    uint32_t autoDumps_ = 0;
    std::atomic<uint32_t> fileSeq_{0};
};

// ---- observers: drop-in wrappers so synccore itself needs no changes ----

// Wraps a transport; every datagram sent and polled is recorded. Wrap the transport BELOW the MuxTransport so all
// channels are seen.
class RecordingTransport : public ITransport {
public:
    RecordingTransport(ITransport& inner, FlightRecorder& rec) : inner_(inner), rec_(rec) {}
    void send(PeerId to, Bytes data) override { rec_.netOut(to, data, 0); inner_.send(to, std::move(data)); }
    void sendEx(PeerId to, Bytes data, Channel ch) override { rec_.netOut(to, data, static_cast<uint8_t>(ch)); inner_.sendEx(to, std::move(data), ch); }
    bool poll(PeerId& from, Bytes& data) override { if (!inner_.poll(from, data)) return false; rec_.netIn(from, data); return true; }
    size_t backlogBytes(PeerId to) const override { return inner_.backlogBytes(to); }
private:
    ITransport& inner_;
    FlightRecorder& rec_;
};

// Wraps a sink; every effect applied to the local world is recorded before the inner sink runs.
class RecordingSink : public IEffectSink {
public:
    RecordingSink(IEffectSink& inner, FlightRecorder& rec) : inner_(inner), rec_(rec) {}
    void apply(const Effect& e, const Context& ctx) override { rec_.applied(e, ctx); inner_.apply(e, ctx); }
    void onJoined(PlayerId slot, const SharePolicy& p) override { rec_.marker(kMarkJoin, "slot " + std::to_string(slot)); inner_.onJoined(slot, p); }
    void onBaselineComplete(const BaselineResult& r) override { inner_.onBaselineComplete(r); }
private:
    IEffectSink& inner_;
    FlightRecorder& rec_;
};

// ---- reader (tests and the plugin's own tools; the Python reader is tools/flight/) ----

struct TraceHeader {
    uint16_t version = 0, headerSize = 0;
    uint8_t slot = 0, role = 0, reason = 0;
    uint32_t wireVersion = 0;
    uint64_t wallStartMs = 0, dumpTUs = 0, dropped = 0, recordCount = 0;
    uint32_t sessionId = 0, epoch = 0;
};
struct TraceRecord {
    uint8_t type = 0, flags = 0;
    uint32_t frame = 0;
    uint64_t tUs = 0;
    std::vector<uint8_t> payload;
};
struct Trace { TraceHeader header; std::vector<TraceRecord> records; };
bool readTrace(const std::string& path, Trace& out, std::string* err = nullptr);
bool parseTrace(const std::vector<uint8_t>& bytes, Trace& out, std::string* err = nullptr);

struct NetInfo {
    uint32_t peer = 0; uint8_t channel = 0, mux = 0, msgType = 0, decode = 0;
    uint32_t session = 0, epoch = 0; uint64_t seqLo = 0, seqHi = 0; uint32_t rawLen = 0;
    std::vector<uint8_t> raw;
};
bool parseNet(const TraceRecord& r, NetInfo& out);
bool parseDigest(const TraceRecord& r, DigestEvent& out);
bool parseOwnership(const TraceRecord& r, OwnershipEvent& out);

}  // namespace fnvcoop::flight
