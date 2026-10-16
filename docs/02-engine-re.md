# Fallout NV engine reverse-engineering resources (1.4.0.525)

Method: shallow-cloned xNVSE/NVSE, jazzisparis/JIP-LN-NVSE, Demorome/ShowOff-NVSE, and read their source. Addresses below come from those sources and are for FalloutNV.exe 1.4.0.525 (the static, non-ASLR 32-bit image, base 0x400000). Items marked UNVERIFIED are not confirmed in code.

## 1. Primary sources

| Resource | URL | Covers |
|---|---|---|
| xNVSE (core) | https://github.com/xNVSE/NVSE | Plugin API (`nvse/nvse/PluginAPI.h`), class layouts (`GameForms.h`, `GameObjects.h`, `GameScript.h`, `GameAPI.h`, `GameData.h`), `SafeWrite.h` (SafeWrite8/16/32, WriteRelCall, WriteRelJump), `Hooks_*.cpp` (Dialog, Gameplay, SaveLoad, Script, Other), `EventManager.h` (script event dispatch, with LoadGame/SaveGame/PostLoadGame/ExitGame/DeleteGame/QQQ events). Discord: https://discord.gg/EebN93s (linked from the README). |
| JIP LN NVSE | https://github.com/jazzisparis/JIP-LN-NVSE | The most address-dense public source. `internal/hooks.h` has about 60 hooks, including SetQuestStage, RunResultScript, ScriptRunner, and PCCellChange. `internal/class_vtbls.h` has every vtable address. `internal/settings_enum.h` has GMST addresses. `nvse/Game*.h` has updated struct layouts. |
| ShowOff NVSE | https://github.com/Demorome/ShowOff-NVSE | xNVSE-era plugin with engine tweaks and its own `nvse/` header fork, which is a good template for a modern plugin. |
| kNVSE | https://github.com/korri123/kNVSE | Animation hooks (not quest-relevant). |
| lStewieAl small plugins | https://github.com/lStewieAl (e.g. falloutNVConsolePasteSupport) | Console hooks. The repo "Stewie's Tweaks" itself is not public on GitHub (search found only the INI repo ModdingLinked/Stewie-Tweaks-INIs). |
| hNVSE | https://github.com/DuncanWasHere/hNVSE | Misc engine features (not read). |
| NV:MP SDK | https://github.com/NVMP/SDK (mirror clayne/NVMP-SDK) | NV:MP's C# SDK source for `NVMP.dll`, docs at https://nvmp.github.io/SDK/. It is important because it shows what NV:MP exposes natively. The client.dll itself is closed. |
| GECK wiki | https://geckwiki.com, https://geck.uesp.net/wiki/SetStage | Script function semantics. |

Not found: public repos for "NVHR", "New Vegas Reloaded", "NVTF", "Yvileapsis". I did not search for them in depth. Treat them as unlocated.

## 2. Symbol / database sources

- **No public FalloutNV.exe IDB/Ghidra project or PDB for retail 1.4.0.525 was found.** The community knowledge is in NVSE/JIP headers (names and struct layouts, no PDB).
- **Xbox 360 dev-kit prototype builds (2025):** Games' Past recovered New Vegas builds dated about 21 Jul 2010 and 22 Aug 2010 (plus April 2010 fragments), reportedly with intact PDBs. Press says they were shared via MEGA, and modders were using them to reverse-engineer cut content. Sources: https://ingamenews.com/2025/11/fallout-new-vegas-dev-builds-surface.html, https://frvr.com/blog/fallout-new-vegas-modders-are-restoring-cut-content-and-beta-ideas-following-xbox-360-dev-kit-discovery, https://winfuture.de/news,155280.html. Caveats: these are PPC/Xbox 360 binaries, not x86. Names and class layouts transfer by matching, but addresses do not. Locating the download and checking legal and ToS status is a to-do. I did not find a direct link.
- **Fallout 3 debug executables with PDBs:** a Dec 2025 Patreon post (https://www.patreon.com/DaggerfallTeam/posts/oblivion-reverse-145293425) says FO3 debug EXEs and PDBs give a full symbol map, and mentions a "completed Fallout 3/NV IDA" used for Oblivion work. This is self-reported and UNVERIFIED. No public download was located. FO3 and NV share most Gamebryo-derived code, so FO3 symbols can be function-matched into an NV IDB (bindiff / Diaphora / signature matching).
- Ask in the xNVSE Discord whether a shared NV IDB exists. Several plugin authors keep private ones (UNVERIFIED).

## 3. Functions and addresses of interest (FalloutNV.exe 1.4.0.525)

| Item | Address | Notes | Source |
|---|---|---|---|
| `TESQuest::SetStage(UInt8 stageID)` | 0x60D510 (thiscall) | Returns bool. This is the function the scripted `SetStage` and JIP `SetStageAlt` call. | JIP `nvse/GameForms.h` ~L4118 |
| SetQuestStage hook point (JIP) | 0x60F4A9 (jmp to 0x60F4C6) | Inside the stage-item processing. `ecx` is the stage info (`[ecx]` is the stage byte, `[ecx+1]` is isDone), `eax` is the quest, and the flag test `[eax+0x3C] & 8` checks allow-repeat. It is the place to intercept or replicate stage changes. | JIP `internal/hooks.h` L2161-2237, L5127 |
| TESQuest layout | size 0x6C: flags 0x3C, stages list 0x44, objectives/vars list 0x4C, conditions 0x54, scriptEventList 0x5C, **currentStage 0x60**, editorName 0x64 | GetStage reads `currentStage`. The StageInfo node is {stage, isDone, logEntries list}. | NVSE `GameForms.h` L3756-3800 |
| QuestStageItem | `GameScript.h` L183 | Per-log-entry data (result script, text, conditions). | NVSE |
| `TESTopicInfo::RunResultScript(onEnd, actor)` | 0x61F170 (thiscall) | Runs the INFO result script. Begin and End are `onEnd` = 0/1. | JIP `GameForms.h` ~L1397 |
| `TESTopicInfo::GetResultScript(onEnd)` | 0x61EB60 | | JIP |
| RunResultScript hook | 0x61F184 | JIP dialog-topic event hook. `[ebp-0xC]` is the INFO, `[ebp+0xC]` is the actor, and the INFO's parentTopic is at +0x50. | JIP `hooks.h` L2242-2277, L5128 |
| Topic selection `GetTopicInfo(topic, result, actor, target, ..)` | 0x61A7D0 (cdecl) | Chooses the TESTopicInfo that would be said. | JIP `internal/jip_core.h` L114 |
| TESTopicInfo load/init | 0x61CD16 (InitTopicInfoHook call), 0x61E086 (LoadTopicInfoHook) | JIP adds a `parentTopic` field at 0x50. | JIP `patches_game.h` L5224 |
| TESTopicInfo layout | `GameForms.h` L1363: quest at 0x48, speechChallenge 0x44, actorValueOrPerk 0x40 | | JIP |
| DialogMenu singleton | `*(DialogMenu**)0x11D9510`, size 0x13C, vtbl 0x107257C | `infos094[16]`, `infos0E4[8]`, `infos110[6]` hold the TESTopicInfo choice arrays. | JIP `GameUI.h` ~L1015-1045, `class_vtbls.h` |
| Extra vtbls | ExtraSayToTopicInfo 0x1015F48, ExtraSayTopicInfoOnceADay 0x1015F3C, TESTopic 0x104D19C, TESTopicInfo 0x104D5B4, TESQuest 0x104AC44, TESReputation 0x104BA24, BGSQuestObjective 0x1047088 | Useful as RTTI-less class anchors. | JIP `class_vtbls.h` |
| `Script::Execute` | 0x5AC1E0 (thiscall: thisObj, eventList, containingObj, arg3) | Base script-runner entry. | NVSE `GameScript.h` L19, JIP L90 |
| `Script::Run` (script context) | 0x5AC400 | | NVSE L137 |
| Script runner hook sites (JIP) | 0x5E0EC9 (call), 0x5E1716 (jmp, eval event block) | ScriptRunner. | JIP `hooks.h` L5129 |
| `Console_Print` | 0x71D0A0 (cdecl, printf-style) | Console output. | NVSE `GameAPI.h` L18 |
| Console-open flag global | 0x11DEA2E | | NVSE `Hooks_Gameplay.cpp` L39 |
| ToggleConsole hook | 0x70E5FE | Console command entry should be near here. UNVERIFIED. | JIP `patches_game.h` |
| Condition::Evaluate | 0x681600; ConditionList::Evaluate 0x680C60 | Used for dialogue and quest conditions. | JIP `GameForms.h` |
| Script event mask hooks (OnHit, OnDeath, ...) | 0x5AC763 (MarkScriptEvent), 0x5AC782 (MergeEventMask), 0x5AC78A (OnHit) | | JIP `hooks.h` |
| Add pipboy note | hook at 0x966AF9 | | JIP |
| Activate / DoActivate | 0x573170 (TESObjectREFR::Activate), hook 0x573512 | | NVSE/JIP |
| PC cell change / fast travel | hooks 0x94CB35 / 0x93BF22 | Entry for per-cell sync. | JIP |
| LocationDiscover | 0x77955D | | JIP |
| DamageActorValue hook | 0x66EE72; `Actor::GetActorValue` is 0x66EF50 | AV change interception. | JIP |
| StartCombat / SetCombatTarget | 0x9001C6 / 0x986C79 | | JIP |
| Save/load singletons | `BGSSaveLoadGame` 0x11DDF38 (g_BGSSaveLoadGame), `TESSaveLoadGame` 0x11DE45C; MarkModified thiscall 0x84A690; save filename 0x84FF90; save path 0x84FF30 | NVSE exposes save and load events via `EventManager`. | JIP `GameAPI.h`, `GameForms.h` |
| Extra save/load hooks | 0x42C328 (load), 0x427F30 (save) | Vtable slot overwrites for per-form extra data. | JIP `patches_game.h` L5297 |
| Reputation | TESReputation layout: maxRep 0x40, positiveRep 0x44, negativeRep 0x48, size 0x50. GMSTs: fReputationBump* 0x11CBC88 and nearby, fReputationThreshold* 0x11CBC70/0x11CBD68 | No public address for an "AdvanceReputation" function was found. NVSE exposes `ModReputation`-type script commands. Hook points would need to be found in the Cmd handler. | JIP `GameForms.h` ~L1579, `settings_enum.h` |
| Karma | GMSTs fKarmaMod* (0x11CD164 etc.), fDefaultKarma 0x11C5074. Karma is the player's AV (kActorVal_Karma) read via `GetActorValue`. | | JIP `settings_enum.h` |
| TESGlobal | `GameForms.h` L1512 (JIP), a plain float in the form. | Writing `->data` directly is the standard approach. | JIP |
| Chargen quest fix | 0x7D333B | Shows the early chargen flow. | JIP |

Not found as published addresses: HUD quest-objective display (use `BGSQuestObjective` list plus `TESQuest` flags), "quest completed" notification (GMSTs sQuestUpdatedText 0x11D4504 / sQuestAddedText 0x11D282C identify the display code by xref), DialogMenu "start conversation" function, and GetStage's command handler (it can be found by xref from the CommandTable entry, whose address NVSE exposes). Recommended approach: search xrefs of the GMST/vtbl addresses above in IDA.

## 4. Hooking libraries and patterns

- NVSE's `SafeWrite.h`: `SafeWrite8/16/32`, `WriteRelJump`, `WriteRelCall`, `WritePushRetRelJump`. This is the dominant convention. JIP wraps it in `HOOK_INIT_JUMP/CALL` with naked-asm trampolines and enable/disable ref-counting (`HOOK_INC/HOOK_DEC`), which is a good model for lazy hooks.
- xNVSE has a `ThisCall<>`, `CdeclCall<>`, `StdCall<>` set of helpers for calling by raw address (JIP has its own `__forceinline` versions).
- The commonly used external libs are MinHook and Microsoft Detours. Detours is mentioned in the NVSE `Hooks_*` code for DirectInput only (UNVERIFIED). Nothing in the repos I read uses them for gameplay hooks.
- Plugin event API: `PluginAPI.h` provides the message bus (`kMessage_PostLoad`, `PostLoadGame`, `MainGameLoop`, etc.), `NVSEEventManagerInterface` (register custom and built-in events), the `NVSEScriptInterface` (CallFunction), and `NVSEDataInterface` for cross-plugin calls. Check the xNVSE README/DEVELOPMENT.md for the current version.
- An xNVSE plugin template exists at `nvse/nvse_plugin_example` inside the xNVSE repo, and a CMake template at https://github.com/clayne/nvse_plugin_cmake_template.

## 5. Relevant implications for the co-op design

1. Quest stage sync: hook 0x60D510 `TESQuest::SetStage` (or the JIP site 0x60F4A9), broadcast {quest formID, stage}, and apply on the remote side by calling 0x60D510 with a re-entrancy guard. Since result scripts run per stage item, applying remotely will re-run the stage result scripts (NPC spawns, item grants), so decide who is authoritative.
2. Dialogue: hook 0x61F170 (RunResultScript) for INFO selections, and 0x61A7D0 for topic choice. Both are keyed by TESTopicInfo form ID, which is stable across clients using the same load order.
3. State-save: `currentStage` at quest+0x60 and the stage `isDone` flags are readable directly for periodic reconciliation.
4. Reputation and karma are best synced by reading TESReputation +0x44/+0x48 and the Karma actor value.

## 6. Communities

- xNVSE Discord: https://discord.gg/EebN93s
- Viva New Vegas / ModdingLinked / Nexus forums (NVSE dev threads), the GECK wiki (https://geckwiki.com), and the NVMP GitHub / docs (https://nvmp.github.io/SDK/).
- The Games' Past community around the Xbox 360 prototype PDBs (YouTube / Twitter, Oct 2025+) for symbols.

## 7. Gaps / to-do

- Find the actual Games' Past PDB distribution link and judge usability (PPC build).
- Locate FO3 debug-build PDBs (not found publicly).
- Confirm console command-execution function address (look near 0x71D0A0 and 0x70E5FE in IDA).
- Check clayne/NVMP-SDK `Documentation` and `NVMP.dll` interfaces for hookable native seams before patching `client.dll`.
