// The public surface of plugin/vats for other code: the VATS state a bot or a test needs, and the few actions that make a VATS shot choosable.
//
// WHY THIS EXISTS (FULL-SYNC-PLAN 3.4, "bots must drive VATS end to end, including choosing a specific limb"): plugin/bots (Track B) cannot choose a
// limb because it has no field for the highlighted one. This header exposes it. Bots call it through the bridge methods this module registers
// (vats.state, vats.select_target, vats.select_limb, vats.fire, vats.cancel, vats.status; docs/tools/vats.md "How bots drive VATS") or, inside the DLL,
// through the functions below. Nothing here has run in a game.
//
// The first half is game-free (it works on any IVatsEngine and is unit-tested with a fake). The second half ("live") is implemented in
// vats_module.cpp and talks to the real engine; it is only declared when the module is part of nvcoop.dll (NVCOOP_WITH_VATS).
#pragma once
#include <string>
#include <vector>
#include "vats/vats_ports.h"

namespace nvcoop::vats {

struct LimbEntry {
    LimbId limb = LimbId::None;
    int index = 0;                 // position in the engine's part list = the order navigation walks
    bool highlighted = false;
};

struct VatsView {
    Mode mode = Mode::None;
    bool menuOpen = false;
    uint32_t targetRef = 0;        // the selected target (reference id), 0 = none
    int targetCount = 0;           // entries in the engine's target list
    std::vector<LimbEntry> limbs;  // the selected target's selectable parts
    LimbId highlighted = LimbId::None;
    LimbId remembered = LimbId::None;   // the engine's "last limb" memory (0x11A59F0): a second opinion on `highlighted`
    int queued = 0;                // VATS actions already queued
    int actionPoints = -1, maxActionPoints = -1;
};

VatsView readView(IVatsEngine& e);
std::string toJson(const VatsView& v);

struct ActionResult {
    bool ok = false;
    LimbId limb = LimbId::None;    // the highlighted limb after the call
    uint32_t targetRef = 0;        // the selected target after the call
    std::string reason;            // why not (ok == false)
};

// Highlight `want` on the selected target. Needs target / limb selection (mode 1..3) and a target that really has that part. The engine's own
// highlight state is written (VATSMenu +0x100 and 0x11A59F0) and read back; the result says what the engine holds afterwards.
ActionResult selectLimb(IVatsEngine& e, LimbId want);
// Make `ref` the selected target by cycling the engine's own "next target" action (at most `maxSteps` times); verifies after every step.
ActionResult selectTarget(IVatsEngine& e, uint32_t ref, int maxSteps = 16);
// Start the playback of what is queued (the real-time "fire"): refuses outside selection or with nothing queued.
ActionResult fire(IVatsEngine& e);
// Pop every queued action (xNVSE EndVATScam does the same) without starting the playback.
ActionResult cancelAll(IVatsEngine& e, int maxPops = 16);

// ---- live (implemented in vats_module.cpp) ---------------------------------------------------------------------------------------------
#ifdef NVCOOP_WITH_VATS
bool liveAvailable();                       // the module is built in, switched on ([vats] enabled=1) and initialised
VatsView liveView();
ActionResult liveSelectLimb(LimbId want);
ActionResult liveSelectTarget(uint32_t ref);
ActionResult liveFire();
std::string liveStatusJson();               // vats.status
// Combat integration (docs/tools/vats.md "Integration requests"): the attacker to credit for a hit. During the local player's own VATS playback an
// untracked temporary actor (the Mysterious Stranger) is the player; everything else is returned unchanged.
uint32_t liveAttackerFor(uint32_t attackerRef);
#endif

}  // namespace nvcoop::vats
