// What the intents module needs from the game. Game-free interface: engine/game_engine.cpp implements it against the xNVSE headers and the
// verified addresses (x86 only), testing/fake_engine.h implements it for the unit tests. Every method is called on the main thread and must
// never throw; a method that has no verified engine path returns false / reports `supported = false` instead of guessing.
#pragma once
#include <memory>
#include <string>
#include <vector>

#include "fnvcoop/effect.h"
#include "intents/intent_types.h"

namespace nvcoop::intents {

// What the target is, as far as intents care. The game implementation classifies by form type and flags.
enum class TargetClass : uint8_t {
    Unknown = 0,
    Door,         // TESObjectDOOR
    Activator,    // TESObjectACTI (levers, buttons, switches, scripted objects, destructibles)
    Container,    // TESObjectCONT, and a dead actor's body (you loot it)
    Terminal,     // BGSTerminal
    Npc,          // a living humanoid actor: you talk to it
    Creature,     // a living creature: nothing to activate
    Item,         // a world item you can pick up (weapon, armor, ammo, misc, ingredient, book, note, key, ...)
    Trigger,      // an enable/trigger volume or other scripted marker
    Furniture,    // TESFurniture (beds, workbenches, chairs)
    Static,       // anything else without gameplay
    Count_
};
const char* className(TargetClass c);
// Activating it by an NPC / puppet would be harmless; activating it as the HOST'S OWN PLAYER would pop a menu or start a conversation on the
// host's screen. The stand-in policy (no puppet for this guest) only allows the first group.
bool opensPlayerMenu(TargetClass c);

struct Vec3 { float x = 0, y = 0, z = 0; };
float distance(const Vec3& a, const Vec3& b);

struct TargetInfo {
    RefId ref = 0;
    TargetClass cls = TargetClass::Unknown;
    bool loaded = false;      // the reference exists on this machine and is in a loaded cell
    bool disabled = false;    // initially or script disabled (an item that was picked up is disabled / deleted)
    bool dead = false;        // actors
    bool inCombat = false;    // actors
    bool hasPos = false;
    Vec3 pos;
    uint32_t cell = 0;
    FormRef base;             // Item: the base form that goes into an inventory (empty = unknown)
    int32_t count = 1;        // Item: stack size of the world reference
};

// ScriptEventList::kEvent_* (xNVSE GameAPI.h). These are the masks MarkBaseExtraListScriptEvent @0x5AC750 takes.
namespace ev {
constexpr uint32_t OnAdd = 0x1, OnHit = 0x80, OnOpen = 0x10000, OnClose = 0x20000, OnTrigger = 0x10000000, OnTriggerEnter = 0x20000000,
                   OnTriggerLeave = 0x40000000;
}

// One open "the host runs this as the guest" window: the acting-player context (a guest's puppet is `player` for every script block started
// inside) plus a capture scope whose cause is {Intent, source = target, aux = kind}. Destroying it closes the capture scope, which emits the
// txn; the runtime has set the txn origin to the guest slot for the duration (Runtime::runAsGuest).
class IActingScope {
public:
    virtual ~IActingScope() = default;
    // Put an effect into the open capture scope the way an engine hook would (false = the hub refused it: scope muted or disabled).
    virtual bool record(const fnvcoop::Effect& e) = 0;
};

class IIntentEngine {
public:
    virtual ~IIntentEngine() = default;

    // ---- shared ----
    virtual bool describe(const FormRef& target, TargetInfo& out) = 0;   // false: the form cannot be resolved at all (plugin not loaded)
    virtual bool position(RefId ref, Vec3& pos, uint32_t* cell) = 0;     // a puppet / ghost / any ref

    // ---- host: run things as the guest. All of them must be called with an IActingScope open. ----
    virtual std::unique_ptr<IActingScope> beginActing(RefId actor, PlayerId slot, const Cause& cause) = 0;
    // TESObjectREFR::Activate with `actor` as activator (console: `Activate <actor> 1` on a calling reference).
    virtual bool activate(RefId actor, const FormRef& target) = 0;
    // MarkBaseExtraListScriptEvent(actor, target.extraDataList, mask): raises the script event exactly like the engine would.
    virtual bool raiseEvent(RefId actor, const FormRef& target, uint32_t mask) = 0;
    // Runs the result script of terminal entry `path` (indices from the top menu down) with `actor` acting. supported = false: no verified path.
    virtual bool runTerminalEntry(RefId actor, const FormRef& terminal, const std::vector<uint8_t>& path, bool* supported) = 0;
    // TESTopicInfo::RunResultScript(info, onEnd, npc) with `actor` as the acting player (docs/protocol.md section 10: a guest's conversation runs its
    // INFO result scripts on the host). Unlike the calls above it opens NO capture scope of its own: the engine's hook on that function does, with
    // the DialogueResult cause the rules engine maps to the owning quest. supported = false: the hook is not installed (nothing captures, so nothing runs).
    virtual bool runInfoResult(RefId actor, PlayerId slot, const FormRef& info, const FormRef& npc, bool onEnd, bool* supported) = 0;
    // Disables the reference (a picked-up item leaves the world). The engine's own hook records the DisableRef effect into the open scope.
    virtual bool disableRef(const FormRef& target) = 0;

    // ---- guest: UI feedback for a rejected intent ----
    virtual void closeMenus() = 0;                       // container / terminal / lockpick menus opened by the local activation
    virtual void notify(const std::string& text) = 0;    // a HUD message
    virtual bool openContainer(const FormRef& container) = 0;   // the player opens THIS container (redirect); must not re-enter the observers
};

}  // namespace nvcoop::intents
