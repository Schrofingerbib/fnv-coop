// Apply a remote Effect to the local world WITHOUT running result scripts.
//
//   Effect            | Engine call(s) used (addresses: docs/engine-addresses.md)                       | Conf
//   ------------------+----------------------------------------------------------------------------------+-----
//   SetStage          | ScriptGate + TESQuest::SetStage 0x60D510. Runs the engine-native part of the     | V
//                     | stage (auto SetRunning, currentStage, isDone, journal text, quest completed/failed
//                     | flags, HUD notice, MarkModified) but Script::Execute 0x5AC1E0 is gated off, so the
//                     | stage's result script never runs; its effects arrive as separate captured effects.
//   SetObjective      | TESQuest::GetObjective 0x60C8E0 -> BGSQuestObjective::SetState 0x5EC5D0(flags)    | V
//                     | (flags: bit0 displayed, bit1 completed -- same encoding as Effect::SetObjective)
//   SetGlobal         | TESGlobal float at +0x24 (form type 6; getter 0x526AC0 is a plain field read)      | M
//   EnableRef/Disable | TESObjectREFR::SetDisabled 0x5650D0(bool)                                         | M
//   AddItem/RemoveItem| TESObjectREFR::AddItem / RemoveItem virtuals (xNVSE GameObjects.h) on the player  | S
//   ModActorValue     | Actor vtable +0x3A8 (ModActorValue: avCode, float delta, 0). XP pseudo-AV 0x10000   | M
//                     | uses Player vtable +0x488 (RewardXP(int)), exactly as Cmd_RewardXP does           |
//   ModReputation     | read TESReputation +0x44/+0x48, TESReputation::Set 0x615580(type, newValue)       | V
//                     | (type 1 = fame/+0x44, else infamy/+0x48; Set also MarkModified's the form)
//   ModKarma          | Player::RewardKarma 0x94FD30(int)                                                  | M
//   StartQuest/Stop   | TESQuest::SetRunning 0x60C9C0(true/false)                                          | V
//   MoveRef           | cell == marker REFR: console `MoveTo <marker> x y z` run ON <ref> via setExecLine (x/y/z = offsets) | M
//   KillActor         | console `KillActor` run ON <actor>                                      | M
//   ResurrectActor    | console `ResurrectActor 0` run ON <actor>                               | M
//
// Every apply runs under an EchoGuard (so the hooks do not re-capture it) and a ScriptGate.
#pragma once
#include <cstdint>
#include <functional>
#include <string>

#include "capture_hub.h"
#include "engine_hooks.h"
#include "fnvcoop/effect.h"

namespace nvhooks {

enum class ApplyStatus { Ok, Unsupported, FormMissing, NotForMe, EngineRejected };
const char* applyStatusName(ApplyStatus s);

class RemoteApplier {
public:
    RemoteApplier(CaptureHub& hub, FormResolver& res) : hub_(hub), res_(res) {}
    // Applies one effect (caller decides recipient filtering via applyTxn or by itself).
    ApplyStatus apply(const fnvcoop::Effect& e);
    // Applies all effects in order. localSlot filters player-targeted effects by Recipient. Returns count applied.
    size_t applyTxn(const fnvcoop::EffectTxn& t, fnvcoop::PlayerId localSlot);
    std::string lastError() const { return err_; }
    // Runs a console command with a calling reference (kill / resurrect / MoveTo). Injected by the plugin (NVSE console interface);
    // returns false on failure. Without it those effects stay Unsupported.
    void setExecLine(std::function<bool(const std::string&, uint32_t)> f) { exec_ = std::move(f); }
    // Baseline/join apply (L4 ctx.baseline): FORCE-SET semantics, no result scripts, no HUD notices where avoidable.
    //   SetStage     : stage may go DOWN (currentStage byte written directly; the engine SetStage only ever raises it)
    //   SetObjective : status byte forced to exactly `flags` (clear+set)
    //   SetGlobal / SetScriptVar : overwrite (same as apply)
    //   everything else: same as apply() (deltas stay deltas; ResurrectActor/KillActor/MoveRef remain Unsupported)
    ApplyStatus applyForce(const fnvcoop::Effect& e);
    // TEST ONLY (mutation twin): apply without closing the ScriptGate, so result scripts run again on the replica.
    void setUseGate(bool on) { useGate_ = on; }

private:
    bool forMe(const fnvcoop::Recipient& r, fnvcoop::PlayerId origin, fnvcoop::PlayerId me) const;
    CaptureHub& hub_;
    FormResolver& res_;
    std::string err_;
    bool useGate_ = true;
    std::function<bool(const std::string&, uint32_t)> exec_;  // (console command, calling reference refID)
};

}  // namespace nvhooks
