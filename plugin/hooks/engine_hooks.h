// Game-side glue (x86 only, compiled against xNVSE headers): installs the prologue-checked detours and feeds the
// CaptureHub. NOTHING happens until Init() is called by plugin main (L1) -- including on the first load, so a build
// that links this library is inert by default.
//
// Threading: Init/Shutdown/hooks all run on the game main thread (script execution is single-threaded).
#pragma once
#include <functional>
#include <string>
#include <vector>

#include "capture_hub.h"
#include "hook_patch.h"
#include "suppress_policy.h"

namespace nvhooks {

// refID <-> (plugin name, local id). The default implementation reads the engine's load order
// (DataHandler at 0x11C3F2C) and form map (LookupFormByID 0x4839C0). L1 may inject its own.
struct FormResolver {
    virtual ~FormResolver() = default;
    virtual bool toRef(uint32_t refID, fnvcoop::FormRef* out) = 0;
    virtual uint32_t fromRef(const fnvcoop::FormRef& r) = 0;  // 0 = plugin not loaded
    virtual void* lookup(uint32_t refID) = 0;                 // TESForm*, or nullptr
};
FormResolver& defaultResolver();

struct HookReport {
    std::string name;
    PatchStatus status;
};
struct InitReport {
    std::vector<HookReport> hooks;
    int ok = 0;
    int failed = 0;
    bool allCritical() const;  // SetStage + TopicInfoRunResult + ScriptExecute installed
};

// Host: capture + play normally. Guest: all hooks installed, capture off, and LOCAL script-driven mutations of shared
// state are blocked (see below). Client: only the ScriptGate hook.
enum class Role { Host, Client, Guest };

// Installs the hooks. Host: capture hooks (SetStage, topic result, objective/quest/rep/karma/ref/item/AV, ...).
// Client: only the ScriptGate hook (used while applying remote effects). A hook whose prologue does not match the
// expected bytes is skipped (reported in InitReport), never forced.
InitReport Init(CaptureHub& hub, Role role, FormResolver* resolver = nullptr);
void Shutdown();  // restores every patched byte (only if still ours)

CaptureHub* hub();
FormResolver& resolver();

// While alive, Script::Execute (0x5AC1E0) returns true without running the script. Used by RemoteApplier so that
// replaying a quest stage runs its engine-native parts (journal text, quest flags, notifications) but NOT the
// result script, whose effects arrive as separate captured effects.
class ScriptGate {
public:
    ScriptGate();
    ~ScriptGate();
    ScriptGate(const ScriptGate&) = delete;
    ScriptGate& operator=(const ScriptGate&) = delete;
};
int scriptGateDepth();

// Non-host suppression (Role::Guest). A local script that mutates shared state is blocked when a script is running
// (Script::Execute depth > 0) and no host txn is being applied. INFO result scripts never run locally on a guest.
// Wrap the application of a host EffectTxn (RemoteApplier does) in ApplyScope to lift the block.
// This also covers NV:MP's local ghost-NPC kill: the death-event script still fires, but its SetStage/AddItem/... no-op.
class ApplyScope {
public:
    ApplyScope();
    ~ApplyScope();
    ApplyScope(const ApplyScope&) = delete;
    ApplyScope& operator=(const ApplyScope&) = delete;
};
// Suppressed (counters) is declared in suppress_policy.h.
// Rules: quest-owned scripts are skipped on guests (setGuestQuestGate to disable); INFO/stage result scripts never run;
// object/NPC scripts run but shared-state mutators (incl. faction rank, perks, script packages, combat, lock/unlock,
// script Activate, FailQuest/CompleteQuest) are dropped.
void setGuestQuestGate(bool on);
const Suppressed& suppressed();
bool applying();



// Script::Execute accounting (proof that replaying a txn ran no result script).
struct ScriptStats {
    uint64_t gated = 0;             // calls swallowed by a closed ScriptGate
    uint64_t ran = 0;               // calls that executed
    uint64_t ranWhileApplying = 0;  // subset of `ran` during a RemoteApplier::apply (must be 0 with the gate on)
    uint64_t applierLines = 0;      // console lines the applier itself executed (kill/resurrect/move); not counted above
};
const ScriptStats& scriptStats();
// Bracket the applier's own console-line executions so they are not mistaken for leaked result scripts.
class ApplierExecScope {
public:
    ApplierExecScope();
    ~ApplierExecScope();
    ApplierExecScope(const ApplierExecScope&) = delete;
    ApplierExecScope& operator=(const ApplierExecScope&) = delete;
};

// Host-authoritative quest vars / globals (L4 StateReplicator). Quest scripts only run on the host and write vars every
// frame, so these are NOT put into capture txns: the host polls each tick and feeds the replicator.
//   nvhooks::enableStatePolling(true);            // turns the per-scope var/global diff off (no duplicates in txns)
//   nvhooks::pollHostState(sink);                 // once per host tick: sink.scriptVar()/global() for every CHANGED value
// The first poll reports every value (baseline); later polls only changes. Cost is one pass over all quests/globals.
struct StateSink {
    virtual ~StateSink() = default;
    virtual void scriptVar(const fnvcoop::FormRef& quest, uint16_t index, double value) = 0;
    virtual void global(const fnvcoop::FormRef& global, float value) = 0;
};
void enableStatePolling(bool on);
size_t pollHostState(StateSink& sink);

// Guest-originated activation (TESObjectREFR::Activate 0x573170, thiscall(ref, activator, u8, u32, u32), ret 0x10).
// Installed on Role::Guest only. Called when the LOCAL PLAYER activates `target` outside any running script and not while
// applying a host txn. The observer (L10) decides: Proceed = engine activates locally (UI / animation; shared-state script
// effects are still dropped by the gate), Suppress = the engine call is skipped entirely. To send the intent to the host call
// ClientSession::submitAction(IntentKind::ActivateRef, target) from inside the observer.
enum class ActivateAction { Proceed, Suppress };
using ActivateObserver = std::function<ActivateAction(const fnvcoop::FormRef& target)>;
void setActivateObserver(ActivateObserver obs);

// Player control lock (v1 for the baseline transfer): DisablePlayerControls / EnablePlayerControls (0x5CAE30 / 0x5CAD20) with all
// seven flags, issued as console lines through the executor injected by the plugin (hooks_module sets it). Idempotent: only the
// transitions run. Unlock re-enables ALL flags (it cannot know what a cutscene had disabled before).
void setConsoleExec(std::function<bool(const std::string&, uint32_t refID)> f);  // refID 0 = no calling reference
void SetPlayerControlsLocked(bool locked);
bool playerControlsLocked();

// Host: let the rules engine defer a SetStage that the engine is about to perform (not ours, not while applying, not inside a
// StageGateBypass). `quest` is the engine refID (resolver().toRef() converts). Return false = the engine SetStage is skipped and
// the detour returns false. The owner re-issues the stage later under a StageGateBypass.
using StageGateFn = std::function<bool(const fnvcoop::FormRef& quest, uint32_t stage)>;
void setStageGate(StageGateFn f);
class StageGateBypass {
public:
    StageGateBypass();
    ~StageGateBypass();
    StageGateBypass(const StageGateBypass&) = delete;
    StageGateBypass& operator=(const StageGateBypass&) = delete;
};
// Local read of a quest's current stage (TESQuest+0x60) for scene-pass detection (e.g. VCG01 chargen finished). -1 if unknown.
int localQuestStage(const fnvcoop::FormRef& quest);
// Execute a console line through the injected executor (for the owner of a deferred stage: "setstage <hex> <n>").
bool execConsoleLine(const std::string& line);
// Same, with `refID` as the console's calling reference (so `KillActor`, `MoveTo ...`, `GetDead` act on that reference).
bool execConsoleLineOn(const std::string& line, uint32_t refID);

// Diagnostic: every ScriptLocal (id, value) of a quest's ScriptEventList exactly as pollHostState sees it (empty if the quest has none).
std::vector<std::pair<uint32_t, double>> questVarsDump(const fnvcoop::FormRef& quest);

// While alive, nvhooks::actingPlayer() returns `actorRefID` (a guest's ghost) instead of the host player 0x14.
class ActingPlayerScope {
public:
    explicit ActingPlayerScope(uint32_t actorRefID);
    ~ActingPlayerScope();
    ActingPlayerScope(const ActingPlayerScope&) = delete;
    ActingPlayerScope& operator=(const ActingPlayerScope&) = delete;
};
uint32_t actingPlayer();

// GuestLocalRun allow-list (rules.json): quest-owned SCRIPT forms that keep running on guests (achievements, rumble, DLC setup, fades).
// Unknown plugins/ids are ignored. Replaces the previous list.
void setGuestLocalRun(const std::vector<fnvcoop::FormRef>& scripts);

// Guest-originated script events. Every engine-raised script event (OnTrigger/OnTriggerEnter/OnTriggerLeave/OnAdd/OnHit/OnOpen/...)
// goes through MarkBaseExtraListScriptEvent(object form, target's BaseExtraList, mask) @0x5AC750 (cdecl). `mask` is one
// ScriptEventList::kEvent_* bit; the target reference is recovered from its embedded extraDataList (+0x44) when the list belongs to a
// reference. Installed on Role::Guest only; called only when the LOCAL PLAYER is the event's object, outside scripts and outside applies.
// Proceed = the event is raised locally as usual; Suppress = it is not raised (the host runs the block via an Intent).
enum class EventAction { Proceed, Suppress };
using EventObserver = std::function<EventAction(uint32_t mask, const fnvcoop::FormRef& target)>;
void setEventObserver(EventObserver obs);
constexpr uint32_t kEvOnAdd = 0x1, kEvOnDrop = 0x4, kEvOnHit = 0x80, kEvOnOpen = 0x10000, kEvOnClose = 0x20000, kEvOnTrigger = 0x10000000,
                   kEvOnTriggerEnter = 0x20000000, kEvOnTriggerLeave = 0x40000000;

// Host-side guard for other lanes' mutation hooks: true = a local script on a guest is mutating shared state, drop it (counts as suppressed).
bool blockMutationNow();
// Non-player actor values (SetActorValue/ModActorValue/ForceActorValue/DamageActorValue on an actor that is not the player), reported after the
// original ran with the actor value before/after, only while capturing. kind: 0 set, 1 mod, 2 force, 3 damage. The observer turns it into
// effects (hub()->record). Unset = only counted in unmapped().avOnNonPlayer.
using NonPlayerAvObserver = std::function<void(const fnvcoop::FormRef& actor, uint32_t av, uint8_t kind, float before, float after)>;
void setNonPlayerAvObserver(NonPlayerAvObserver obs);

// Counters for effects the engine produced that have no Effect type yet (e.g. CompleteQuest, container AddItem on a
// non-player). Exposed so tests/telemetry can show gaps instead of silently dropping them.
struct Unmapped {
    uint64_t questCompleted = 0;
    uint64_t itemOnNonPlayer = 0;
    uint64_t avOnNonPlayer = 0;
    uint64_t questFailed = 0;
};
const Unmapped& unmapped();

}  // namespace nvhooks
