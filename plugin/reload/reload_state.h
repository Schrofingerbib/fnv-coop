// Hot-reload state contract (T3b-state). Game-free: no NVSE, no Win32 in this header.
//
// A reloadable core DLL is unloaded and a new build is loaded into the same running game. Everything that must survive
// goes through this registry; everything that must NOT survive (threads, hooks, sockets) is stopped by a participant's
// `shutdown` and checked by leak probes before the loader is allowed to call FreeLibrary.
//
// Sequence (the loader drives it, see docs/tools/T3b-state.md):
//   old core : SaveAll(blob)        every participant writes its versioned state; a failure aborts the reload, the old core stays live
//   old core : ShutdownAll()        reverse order: stop threads, uninstall hooks, close or hand over sockets; then every leak probe is read
//   loader   : FreeLibrary(old), LoadLibrary(new)
//   new core : its modules run their normal OnLoad / DeferredInit paths (hooks reinstall), then RestoreAll(blob)
//
// Blob = what the loader keeps in memory between the two cores. It is plain bytes: no pointers, no vtables, nothing that
// points into the old DLL. Process-wide handles (a SOCKET, a HANDLE) may be stored as integers, they stay valid across the
// FreeLibrary as long as shutdown() did not close them.
#pragma once
#include <cstdint>
#include <functional>
#include <memory>
#include <string>
#include <vector>

namespace nvreload {

using Bytes = std::vector<uint8_t>;

// ---- little-endian byte writer / reader. The reader never throws and never reads out of range: after any short read
// ok() is false and every later get returns zero / empty.
class Writer {
public:
    void u8(uint8_t v) { b_.push_back(v); }
    void u16(uint16_t v) { put(v, 2); }
    void u32(uint32_t v) { put(v, 4); }
    void u64(uint64_t v) { put(v, 8); }
    void i64(int64_t v) { put(static_cast<uint64_t>(v), 8); }
    void f64(double v);
    void boolean(bool v) { u8(v ? 1 : 0); }
    void str(const std::string& s);
    void bytes(const Bytes& v);
    void raw(const void* p, size_t n);
    const Bytes& data() const { return b_; }
    Bytes take() { return std::move(b_); }

private:
    void put(uint64_t v, int n) {
        for (int i = 0; i < n; ++i) b_.push_back(static_cast<uint8_t>(v >> (8 * i)));
    }
    Bytes b_;
};

class Reader {
public:
    Reader(const uint8_t* p, size_t n) : p_(p), n_(n) {}
    explicit Reader(const Bytes& b) : p_(b.data()), n_(b.size()) {}
    uint8_t u8() { return static_cast<uint8_t>(get(1)); }
    uint16_t u16() { return static_cast<uint16_t>(get(2)); }
    uint32_t u32() { return static_cast<uint32_t>(get(4)); }
    uint64_t u64() { return get(8); }
    int64_t i64() { return static_cast<int64_t>(get(8)); }
    double f64();
    bool boolean() { return u8() != 0; }
    std::string str();
    Bytes bytes();
    bool ok() const { return ok_; }
    size_t remaining() const { return n_ - pos_; }
    bool atEnd() const { return pos_ == n_; }

private:
    uint64_t get(int n);
    const uint8_t* p_;
    size_t n_, pos_ = 0;
    bool ok_ = true;
};

uint32_t crc32(const void* data, size_t n);

// ---- participants ------------------------------------------------------------------------------------------------
// One per piece of state that must survive a reload (a module, a session, a ledger). Names are unique and stable across builds.
struct Participant {
    const char* name = "";
    uint32_t version = 1;  // bump whenever the saved layout changes
    int order = 100;       // save and restore ascending; shutdown descending. Hooks 10, sessions 50, UI 80.

    // Optional preflight, run before anything is saved or stopped. Return "" if the module can be unloaded, else the reason
    // (for example "a hook was chained over by another mod"). A refusal leaves the old core fully working.
    std::function<std::string()> canUnload;

    // onBeforeUnload: the old core is still fully alive. Write everything the new core needs. Return false to veto the reload.
    std::function<bool(Writer&)> save;
    // onAfterReload: the new core already ran its normal init. `savedVersion` may differ from `version`. Return false to
    // reject the blob (the participant then falls back to fresh()).
    std::function<bool(uint32_t savedVersion, Reader&)> restore;
    // Optional: convert an older layout. If set it is called instead of restore() when savedVersion != version.
    std::function<bool(uint32_t savedVersion, Reader&)> migrate;
    // Stop threads, uninstall hooks (bytes restored), close sockets or leave handed-over ones open. Runs after every save succeeded.
    std::function<void()> shutdown;
    // Optional: start from scratch (no blob, bad blob, version mismatch, restore rejected).
    std::function<void()> fresh;
};

struct Report {
    std::vector<std::string> notes;       // human-readable, one line each
    std::vector<std::string> failures;    // anything that makes the reload unsafe or lossy
    std::vector<std::string> leaks;       // leak probes that read non-zero after shutdown
    int restored = 0, freshStarts = 0, migrated = 0, dropped = 0;
    bool ok() const { return failures.empty() && leaks.empty(); }
    std::string text() const;
};

// A leak probe returns how many of something are still alive (installed hooks, running threads, open sockets).
// `baseline` is what is legitimately alive when the core is idle (for example hooks owned by the permanent loader).
struct LeakProbe {
    std::string name;
    std::function<int()> count;
    int baseline = 0;
};

class Registry {
public:
    // Registering twice under the same name replaces the earlier entry (a re-run of static init must not duplicate).
    void add(const Participant& p);
    void addLeakProbe(const LeakProbe& p);
    void clear();
    size_t size() const { return parts_.size(); }
    std::vector<std::string> names() const;
    uint32_t versionOf(const std::string& name) const;  // 0 = unknown

    // old core. false = the reload must be aborted and the old core kept (nothing was shut down).
    bool saveAll(Bytes* blob, Report* rep);
    // old core. Runs every shutdown (a throwing one does not stop the rest), then reads every leak probe.
    // Returns the number of leaks; the loader must NOT call FreeLibrary when it is non-zero.
    int shutdownAll(Report* rep);
    // new core. A missing, short or corrupt blob is never fatal: every participant just gets fresh().
    // Returns false only when the blob was unusable as a whole.
    bool restoreAll(const Bytes& blob, Report* rep);

    // For tests and the status RPC.
    static bool inspect(const Bytes& blob, std::vector<std::pair<std::string, uint32_t>>* entries);

private:
    std::vector<Participant> parts_;
    std::vector<LeakProbe> probes_;
};

// The process-wide registry used by the real modules (the plugin core has exactly one of these per loaded core DLL).
Registry& global();

struct Registrar {
    explicit Registrar(const Participant& p) { global().add(p); }
};
#define NVCOOP_RELOAD_STATE(ident, ...) \
    static const ::nvreload::Registrar nvreload_participant_##ident(::nvreload::Participant{__VA_ARGS__})

// ---- tracked threads ---------------------------------------------------------------------------------------------
// Every thread a core DLL starts must be a ManagedThread: the destructor asks it to stop and joins, and liveThreads()
// is the leak probe. A raw std::thread (or CreateThread) that outlives its DLL crashes the game the moment it runs.
int liveThreads();

class ManagedThread {
public:
    ManagedThread() = default;
    ~ManagedThread() { stopAndJoin(); }
    ManagedThread(const ManagedThread&) = delete;
    ManagedThread& operator=(const ManagedThread&) = delete;
    // body(stopRequested): must return soon after stopRequested() turns true.
    void start(const char* name, std::function<void(const std::function<bool()>& stopRequested)> body);
    void stopAndJoin();
    bool running() const;
    const std::string& name() const { return name_; }

private:
    struct State;
    std::shared_ptr<State> st_;
    std::string name_;
};

// Native ids of the threads started through ManagedThread right now (Windows thread ids; empty elsewhere). The reload drain
// ignores them: they are stopped and joined by their owners, everything else running our code must have left it.
std::vector<unsigned long> managedThreadIds();

// A thread a core module owns but does not start through ManagedThread (a library's worker, e.g. the co-op session's ThreadWorker,
// parked waiting for jobs inside our code). Its owner stops and joins it in its participant's shutdown, which runs after the drain,
// so the drain ignores it like a ManagedThread. Without this every reload with a co-op session running was refused by the drain
// (in game 2026-10-21: "thread ... is still inside the code that would be unloaded", the worker's wait on its queue).
void adoptThread(unsigned long tid);
void releaseThread(unsigned long tid);

// Probe helpers.
LeakProbe threadProbe();

}  // namespace nvreload
