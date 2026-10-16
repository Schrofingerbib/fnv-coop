# NV:MP client.dll findings (v9.4, sha256 e2b5c251…)

Analysed with REA 6.5.0 + Ghidra 12.1.4. Image base 0x10000000, 13,176 functions,
6,549 strings, MS Detours sections (.detourc/.detourd). No PDB shipped.

## Launch chain
- ClientLauncher (C#, open) -> `nvmp_start.exe <ip> <port> <gameID> <jwt> <productID> "<mods>" "<name>"`
  (`src/ClientLauncher/ClientLauncher/MainWindow.xaml.cs:1228`; offline adds `-eos_disable`)
- nvmp_start.exe: CreateProcessA(FalloutNV.exe, suspended) -> VirtualAllocEx/WriteProcessMemory
  -> CreateRemoteThread(LoadLibrary "\nvmp\client.dll") -> ResumeThread

## FUN_100cc9e0 - NetEncounterManager "Encountered Actor"
Runs when the local player encounters an actor. Marks the actor **localized** (per-player,
NOT network-synced) if any of:
- it's a follower of the local player
- actor vtable +0x94 returns true -> "is quested" (quest-flagged ref)
- formID mod index == 0xFF -> "is scripted reference" (runtime-created)

=> Quest NPCs are deliberately excluded from sync. This is the main reason quests don't co-op.
   A quest-sync mod would need to flip this decision (sync quest actors from host) AND sync
   quest stages, or else quest actors stay per-player.

## FUN_10056040 - NetActorComp per-actor update
Applies networked life state to the local actor. If the networked state is dead (state 1/2) and
local isn't: logs "%s: auto-killing... [quest: %u, combat: %u]" and kills locally. Reverse case
logs "auto-resurrecting...". "quest" in the log = same vtable +0x94 flag.

## Game addresses called directly (FalloutNV.exe 1.4.0.525)
- 0x00EC43FB - called via guard_check_icall after form lookups (likely LookupFormByID-style thunk)
- 0x00410220 - called with 0x0E / 0x1D (looks like BaseExtraList::GetByType; 0x1D = ExtraFollower?)

## server.cfg keys (nvmp_storyserver.exe)
Hostname, Port, EnableConsole, EnableSaving, EnableVATS, StrictNVSE, Difficulty, IsHardcore,
EnablePartySystem, UseClientsideHitReg, AllowNPCGhosts, EnableWildEncounters, MainThreadDispatching

## Public source found (cloned into src/)
- ecaii/x-native - 2020 snapshot of NV:MP's own codebase ("NVMP-X"); PDB paths in 9.4 binaries are
  D:\x-native\build\app\app_client / app_server, so this IS the same project, early stage.
  lib_net (networkobject/networksynchronisation/networkvariable/networkactorobject), lib_game, app_server.
  app_client not included ("legacy project still closed source").
- clayne/NVMP-SDK (mirror of deleted NVMP/SDK, last 2025-09) - C# server SDK (NVMP.dll), NetActor/
  NetCharacter/NetReference factories, ExampleServer. Story/co-op servers have no scripting, so this only
  applies to dedicated servers.
- devmlew/NVMP-Companion - community RE of NV:MP 8.1: runtime byte patches to client.dll + server hook DLL.
  Confirms `[netref+0x1AE]` = locality byte (0 ghost / 1 local) - same offset seen in 9.4 FUN_10056040.

## Confirmed
- Actor vtable +0x94 = TESForm::IsQuestItem (xNVSE GameForms.h). Flag 0x400 is SHARED with
  kFlags_Persistent on refs, so "is quested" likely localizes every persistent ref = basically all named NPCs.
