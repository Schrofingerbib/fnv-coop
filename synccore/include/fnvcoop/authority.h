// Script authority by machine role (coordinator decision, L3 guest_suppression_report):
//  * QUEST scripts (owner is a TESQuest, incl. per-frame GameMode/MenuMode blocks) run ONLY on the host. Guests never
//    execute them; quest vars and globals reach guests as host-authoritative SetScriptVar/SetGlobal (StateReplicator).
//  * OBJECT/NPC scripts (OnActivate, OnTriggerEnter, OnDeath...) still run on guests for local presentation, but their
//    shared-state mutations are blocked by EffectCapture (guest default-deny). When the TRIGGER came from a guest
//    player, the guest sends an action intent (ClientSession::submitAction) and the host runs the real thing.
#pragma once
#include "fnvcoop/mutations.h"

namespace fnvcoop {
enum class Role : unsigned char { Host, Guest };
// L5's Script::Execute gate calls this with the script's owner kind.
inline bool questScriptsMayRun(Role r) { return r == Role::Host || mutations().guestRunsQuestScripts; }
}  // namespace fnvcoop
