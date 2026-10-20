// Co-op fuzzer (T2b): scenario generator, invariant oracles, replay, shrink.
// The fuzzer drives an ITarget (a game-free multi-machine world over the lossy in-memory network) with a list of
// independent timed ops, then checks the oracles on what the target logged. Two targets exist:
//   "mini"  - synccore/fuzz/mini_target.cpp, a small self-contained reference world + protocol with injectable bugs.
//   "em"    - adapter to T2a's synccore/enginemodel (see target_em.cpp; unavailable until T2a ships its model).
#pragma once
#include <cstdint>
#include <map>
#include <memory>
#include <string>
#include <vector>
#include "fnvcoop/transport.h"

namespace fnvcoop::fuzz {

// ------------------------------------------------------------------ scenario
enum class OpKind : uint8_t { Move, Hit, Pickup, Drop, Spawn, Activate, Join, Leave, Save, Load, SetLink, Stale, Count };
const char* opName(OpKind k);
bool opFromName(const std::string& s, OpKind& out);

// Every op carries its own absolute time and explicit small ints. Refs are slot numbers that the target maps onto
// whatever exists at that moment (modulo), so removing other ops never makes an op invalid.
//   Move    m, a=x(0..1000) b=y(0..1000) c=cell(0..2)
//   Hit     m, a=victim slot (modulo live actors), b=damage
//   Pickup  m, a=item slot (modulo ground items)
//   Drop    m, a=held item slot, b=x c=y d=cell
//   Spawn   m, a=site (0..4)
//   Activate m, a=door slot
//   Join/Leave m (machine 0, the host, never leaves)
//   Save/Load  host save point / load the latest save
//   SetLink a=minLatencyMs b=maxLatencyMs c=loss permille d=duplicate permille
//   Stale   m=receiver, a=sender, b=which recorded message, c=0 old-content replay (fresh packet) / 1 raw duplicate
struct Op {
    uint32_t t = 0;
    OpKind kind = OpKind::Move;
    int m = 0, a = 0, b = 0, c = 0, d = 0;
};

struct Scenario {
    uint64_t seed = 1;
    int machines = 3;                 // 2..4, machine 0 is the host
    uint32_t durationMs = 20000;      // chaos phase length (virtual)
    LinkParams link;                  // initial chaos link
    std::vector<std::string> bugs;    // injected bug classes (empty = clean)
    std::string profile;              // T5 chaos profile name (lan good_wifi bad_wifi mobile_tether transatlantic hell); empty = plain LinkParams
    std::string target = "mini";     // which target the scenario was made for
    std::string expect;               // regression fixtures: the invariant that must break while the bug is injected
    std::vector<Op> ops;              // sorted by t
};

Scenario generate(uint64_t seed, int machines, uint32_t durationMs);
std::string toJson(const Scenario& s);
bool fromJson(const std::string& text, Scenario& out, std::string* err = nullptr);

// ------------------------------------------------------------------ observations (what oracles read)
enum class ApplyKind : uint8_t { Pos, PlayerPos, Hp, Owner, Holder, Door, Spawn, Remove, Reset, Intent };
const char* applyKindName(ApplyKind k);
struct ApplyEvent {
    int machine = 0;
    uint64_t tMs = 0;
    uint64_t ord = 0;         // global order of occurrence, shared with OwnEvent (tMs is too coarse inside one millisecond)
    uint64_t idem = 0;        // identity of the message being applied; the same id accepted twice on one machine = applied twice
    int ref = -1;             // netId of the thing, or -1
    uint32_t epoch = 0;       // ownership epoch the message carried (0 = n/a)
    uint32_t seq = 0;         // sequence inside (ref, epoch) for ordered streams (0 = n/a)
    int origin = -1;          // machine that produced the content
    ApplyKind kind = ApplyKind::Pos;
    bool accepted = true;
    uint32_t worldEpoch = 0;  // world (save/load) epoch the message was made under
    const char* why = "";     // reject reason / note
};
struct OwnEvent {            // ownership service log
    uint64_t tMs = 0;
    uint64_t ord = 0;
    enum Kind : uint8_t { Grant, Transfer, Release, WorldEpoch } kind = Grant;
    int ref = -1;
    int owner = -1;           // new owner (-1 on release)
    int prevOwner = -1;
    uint32_t epoch = 0;       // ownership epoch (WorldEpoch: the new world epoch)
};
struct Thing {               // one actor / item / player / door as a machine sees it
    uint32_t netId = 0;
    uint8_t type = 0;         // 0 player 1 actor 2 item 3 door
    uint32_t ident = 0;       // identity independent of netId (spawn site slot / original item); 0 = none
    int x = 0, y = 0, cell = 0, hp = 0;
    bool dead = false;
    int owner = -1;
    uint32_t epoch = 0;
    int holder = -1;          // items: machine holding it, -1 on the ground
    bool open = false;
    bool operator==(const Thing& o) const;
    std::string str() const;
};
struct MsgTrace {            // every packet entering/leaving a machine (for the flight-format trace)
    uint64_t tMs = 0;
    int machine = 0;
    bool out = false;
    int peer = 0;
    uint8_t channel = 0;
    Bytes raw;
};
struct Violation {
    std::string invariant;   // converge, double_apply, multi_owner, dup_thing, stale_applied, echo_adopted, repairs_after_quiescence, not_quiescent
    std::string detail;
    uint64_t tMs = 0;
};
constexpr int kDigestCats = 4;   // story, actors, containers, objects

class ITarget {
public:
    virtual ~ITarget() = default;
    virtual void apply(const Op& op) = 0;          // run one op "now"
    virtual void advance(uint64_t toMs) = 0;       // run the virtual clock up to toMs
    virtual uint64_t now() const = 0;
    virtual void heal() = 0;                       // perfect link, everyone reconnected, activity frozen
    virtual int machines() const = 0;
    virtual bool connected(int m) const = 0;
    virtual bool quiescent() const = 0;            // nothing in flight, everyone caught up, activity frozen
    virtual std::vector<Thing> view(int m) const = 0;
    virtual uint64_t digest(int m, int cat) const = 0;
    virtual uint64_t repairs(int m) const = 0;     // heal / resync actions the machine performed so far
    virtual const std::vector<ApplyEvent>& applyLog() const = 0;
    virtual const std::vector<OwnEvent>& ownLog() const = 0;
    virtual const std::vector<MsgTrace>& msgLog() const = 0;
    virtual std::map<std::string, uint64_t> stats() const = 0;
    // Optional: the target's own invariant checks (e.g. T2a's Cluster oracles), mapped to our invariant names.
    // markQuiescent() is called once when quiescence was reached; nativeViolations() at the end of the run.
    virtual void markQuiescent() {}
    virtual std::vector<Violation> nativeViolations() { return {}; }
    // Optional: write <base>.m<slot>.nvfr from the target's own recorder (T1a EngineFlightObserver). False = unsupported, the generic
    // writer in runner.cpp is used instead.
    virtual bool dumpFlight(const std::string& /*base*/, const std::vector<Violation>& /*violations*/) const { return false; }
};
// target: "mini" or "em". Returns null and sets *err when unknown bugs are requested / the target is unavailable.
std::unique_ptr<ITarget> makeTarget(const std::string& target, const Scenario& s, std::string* err = nullptr);
std::vector<std::string> knownBugs(const std::string& target);
void setKeepMessages(bool on);   // record raw packets in msgLog (needed for traces; off by default, it is large)

// ------------------------------------------------------------------ oracles
struct OracleOptions {
    bool checkRepairs = true;
};
// Log-based checks (valid at any time).
void checkLogs(const ITarget& t, std::vector<Violation>& out);
// State checks (only meaningful when the target is quiescent).
void checkConverged(const ITarget& t, std::vector<Violation>& out);

// ------------------------------------------------------------------ run / shrink
struct RunResult {
    bool ok = true;
    std::vector<Violation> violations;
    std::map<std::string, uint64_t> stats;
    uint64_t virtualMs = 0;
    uint64_t opsRun = 0;
};
struct RunOptions {
    std::string profile;              // T5 chaos profile name (lan good_wifi bad_wifi mobile_tether transatlantic hell); empty = plain LinkParams
    std::string target = "mini";
    uint32_t quiesceCapMs = 60000;     // longest wait for quiescence after healing
    uint32_t afterQuiesceMs = 10000;   // repairs must stay flat this long
    std::string traceDir;              // non-empty: on failure write simfuzz-trace JSON + .nvfr per machine here
    std::string traceName = "fail";
};
RunResult run(const Scenario& s, const RunOptions& opt);
// Delta-debug the ops (then link, machines) down to a smaller scenario that still violates `invariant`.
Scenario shrink(const Scenario& s, const RunOptions& opt, const std::string& invariant, int* runs = nullptr);
bool writeTraces(const ITarget& t, const Scenario& s, const RunResult& r, const std::string& dir, const std::string& name);

std::string fnvHex(const std::string& s);

}  // namespace fnvcoop::fuzz
