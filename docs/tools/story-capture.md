# Story capture: the remaining script commands (roadmap step 8, story gaps)

Owner: story-capture. Code: the capture side of `plugin/hooks/` (engine_hooks.cpp, apply_remote.cpp, ref_ops.*, move_queue.h, story_*.h), the `RefCommand` effect in
`synccore/`, and the coverage evidence in `tools/coverage/`. Status 2026-10-21: **code written and unit-tested game-free; nothing here has run in a game.** The new
hooks are **off by default** (`nvcoop.ini [hooks] story_capture=0`); the gate turns them on.

## Result

Script calls synced, game wide (`python -I tools/coverage/build.py`, `out/coverage/backlog.md`, live source scan):

| | supported | partial | missing | local (not counted) | synced |
|---|---|---|---|---|---|
| before | 51 840 | 21 201 | 4 752 | 4 178 | 66.6 % |
| after | 77 631 | 13 | 0 | 4 327 | 99.98 % (rounds to 100.0) |

What is left: `UseWeapon` (13 calls), which has nine parameters (four forms, five integers), more than a `RefCommand` has slots. It needs either a second command
shape or a packed layout; it is the only open script function of the census.

How the number is made, honestly:
* **Real code** (new in this round): the `RefCommand` effect and its 77 hooked commands (below), `AddItemHealthPercent`, `ModFactionRank`, `PlayGroup`, `PlayIdle`,
  `SetCriticalStage`, `SetActorFullName`, `ForceActiveQuest` capture, parked cross-cell `MoveRef`, and a fix for the console-line effects (below).
* **Stale evidence replaced by source checks.** The hand-written census text (`questlines/systems/census.py`) still said "player only", "NOT hooked", "needs the injected
  console executor" for things the earlier rounds had built (container and NPC items, NPC actor values, `PlaceAtMe`, the object-state commands, `SetAngle`, essential /
  restrained / enemy / ally, scripted saves, forced dialogue, `SetPlayerTeammate`, `EnableFastTravel` ...). `tools/coverage/evidence.json` now lists, per script function,
  the Effect types and the source tokens (file + regex) that must be present; `live_status.py` counts a function as synced only while every token is found, so removing a
  hook drops the function back to partial. `tools/coverage/tests/test_evidence.py` checks that every shipped token matches the current source and that every op row has an
  entry. The static census tables are untouched (their "48.9 %" is kept as the baseline column).
* **Two classifications, with the reason written down** (`status: local` in evidence.json): `SwapTexture` (the C-Finder weapon screen, a texture on the weapon of whoever holds
  it, driven by that weapon's own script on that machine; the world-shared use, the ending slideshow, has its own channel) and `unparsed-token` (not a function: statement heads
  the census parser could not attribute). They leave the denominator.
* **Owned by another lane and counted through its tokens**: forced dialogue (`Say`, `SayTo`, `StartConversation`: L11 hooks_ui), NPC state (`SetEssential`, `SetRestrained`,
  `SetEnemy`, `SetAlly`, `IgnoreCrime`, faction flags, script packages, non-player actor values, `MarkForDelete`: L12 hooks_world, as `WorldEffect`s), and the four ownership
  commands (`SetOwnership`, `ClearOwnership`, `SetCellOwnership`, `SetCellPublicFlag`: the world-time lane). If one of those lanes removes its code, the evidence check
  fails and the function shows as partial again.
* `ReleaseWeatherOverride` (11 calls): its handler starts with a `call`, so it cannot be detoured at the entry. The L12 host tick already polls the current weather and records
  `SetWeather` when it changes, which is how a released override reaches the clients; the evidence entry names that poll. Unproven in a game.

## The RefCommand effect

One new Effect (tag 49, wire v6, docs/protocol.md section 21) instead of 75 new types: `{op, ref, a, b, i, j, k, l, f, s, to}`. The op ids and merge rules are in
`synccore/include/fnvcoop/refop.h`; the engine side (which handler, how `ExtractArgs` returns the arguments, who may perform it) is `plugin/hooks/ref_ops.{h,cpp}`.

* **Event or state.** An event is delivered once and remembered nowhere. A state op keeps the last command per key in the `ShadowState`, so a baseline, a late joiner and a
  sidecar carry it (class 5, digest segment 12). Keys: `SetGhost` per ref, `AddSpell` / `RemoveSpell` per (ref, spell), `AddFormToFormList` per (list, form), `SetQuestDelay` per quest ...
* **Who performs it.** Recipients narrow player-targeted calls (`Origin` for the character-build calls, `All` for achievements and challenges and for every command on
  something other than the player, and the reward rule of `AddItem` for a command on the player: the party for a quest or dialogue result, the acting player otherwise). **Actor AI orders** (`EvaluatePackage`, `StartCombat`, `StopCombat`, `ResetAI`, `StopLook`, `Look`, `SetAlert`, `StopCombatAlarmOnActor`,
  `ForceFlee`, `PushActorAway`, `ResetHealth`, `SetPackDuration`, `PickIdle`, `SetVATSTarget`, `FireWeapon`, `PlayGroup`, `PlayIdle`) are performed only by the machine that runs the actor's AI
  (`RemoteApplier::setDefaultRemoteDrivenQuery`, wired in `story_owner_gate.cpp` to `nvcoop::actors::engine::isRemoteDriven` when the actors engine port is in the build); the
  others answer `NotForMe` and see the result in the actor snapshots. Without the ownership layer every machine performs every order. `Activate` is performed only on
  activators and doors (never containers, terminals, talking activators or actors, which open a menu or a conversation on a machine that did not ask).
* **Capture.** `storyCmd` in engine_hooks.cpp is one detour per command (a template instance per op). It decodes the `ExtractArgs` outputs before the original runs (a command may
  delete what it was given), records after the original succeeded, and drops nothing silently: a command whose calling reference or form has no stable name is counted in
  `unmapped().storyDropped`. On a guest the same hooks block a local script's mutation like every other shared-state mutator. `StartCombat`, `StopCombat`, `Activate` and
  `ModFactionRank` already had a detour (block on a guest); theirs now calls `storyCmd` (`kRoSharedHook`).
* **Apply.** `RemoteApplier::applyRefCommand` builds the engine's own console line (`buildRefCommandLine`: hex form ids, trailing default arguments left out, skills and animation
  groups and critical stages printed as the engine's names, text arguments restricted to one plain word so a script cannot append a second command) and runs it on the calling
  reference through the console executor.
* **What the table is pinned to.** Every handler address and prologue is in `story_addrs.h`, generated from `tests/story_prologues_dump.txt` (rizin on a reference exe, the same method
  as `tools/re/pro.py`; the execute address of each command is the `execute` column of the engine command table). `data/story_cmd_params.json` holds, per command, the engine's
  own parameter list (ParamInfo type id and optional flag), `needsParent`, and the handler address; `test_story.cpp` checks every op row (argument letters, optional counts, slots,
  calling-reference rule, handler) against it, so a wrong layout fails in seconds. `story_animgroups.h` holds the engine's 245 animation group names (`g_animGroupInfos`,
  0x11977D8, matches xNVSE's compiler loop and the xNVSE enum table in tools/animcheck) and the five critical stage names (`g_criticalStageNames`, 0x119BBB0; entry 0 is the
  GECK's "None", the other four are read from the image). Regenerate: `python -I plugin/hooks/tools/gen_story_addrs.py`, `python -I plugin/hooks/tools/gen_story_params.py`.
* A hook whose prologue differs at run time is skipped (reported in `hooks.status`), never forced. `ReleaseWeatherOverride` was left out for the reason above.

The 77 ops: actor AI orders (above); actor flags `SetUnconscious`, `SetGhost`, `SetIgnoreFriendlyHits`, `SetCombatStyle`, `SetForceSneak`, `SetActorsAI`, `SetDisposition`; spells `AddSpell`,
`RemoveSpell`, `Dispel`, `DispelAllSpells`, `CastImmediateOnSelf`, `PlayMagicShaderVisuals`, `StopMagicShaderVisuals`; inventory `EquipItem`, `UnequipItem`, `ResetInventory`, `DropMe`, `Drop`,
`RemoveMe`, `AddItemHealthPercent`, `AddItemToLeveledList`, `AddFormToFormList`; world `SetActorAlpha`, `SetScale`, `SetItemValue`, `ClearDestruction`, `Rotate`, `SetActorFullName`,
`SetCellImageSpace`; quest meta `SetQuestDelay`, `ResetQuest`, `SetQuestObject`, `AutoDisplayObjectives`, `SetCriticalStage`; `Activate`, `ModFactionRank`; character build
`SetPlayerTagSkill`, `AddSPECIALPoints`, `SetSPECIALPoints`, `SetTagSkills`, `AddTagSkills`, `SetLevel`, `ResetXP`, `AgeRace`, `SexChange`, `SetPCCanUsePowerArmor`, `SetPCSleepHours`, `WakeUpPC`,
`SetHardcore`, `SetInChargen`, `OpenTeammateContainer`, `AddCardToPlayer`; challenges `AddAchievement`, `IncrementScriptedChallenge`, `UnlockChallenge`, `RemoveRecurringFromChallenge`; scene
helpers `SetTalkingActivatorActor`, `AlwaysShowActorSubtitles`, `StartRadioConversation`.

## Other fixes in the capture / apply side

* **Console-line effects had no executor in the coop path.** `coop_module.cpp` creates its own `RemoteApplier` and never calls `setExecLine`, so `KillActor`, `ResurrectActor`,
  `MoveRef`, `SetFactionRank`, `SetPerk`, `ObjectState`, `RefAddItem`, `RefRemoveItem`, `RemoveAllItems`, journal and weather effects, `SetTimeScale`, `RefAngle` and `CompanionOwner` would
  all have answered `Unsupported` in a real session. `RemoteApplier` now falls back to the process-wide executor (`nvhooks::setConsoleExec`, set by the hooks module) when it has
  none of its own (`pickExec`, test + twins 9 and 10). coop_module.cpp itself was not edited.
* **Cross-cell `MoveRef` no longer fails when the destination cell is not loaded.** It is parked (`move_queue.h`: newest move per reference wins, gives up after 120 tries) and retried about
  once a second from the frame callback (`RemoteApplier::tickAll`). A reference whose cell is away is not simulated here; it ends where the host left it when the cell loads. Twins 7 and 8.
* **`ForceActiveQuest`** was an Effect type with a policy rule and no capture or apply; it has both now (`h_ForceActiveQuest`, console `ForceActiveQuest`).
* **`AddItemHealthPercent`** (166 calls) was not hooked and the `AddItem` effect has no condition field; it is a `RefCommand` op, so the condition arrives.
* `RemoteApplier::applyTxn` honours the recipient of a `RefCommand`.

## Run it

```
plugin\hooks\run_story_tests.cmd                          # story unit tests (3 800+ checks) + 11 runtime twins; prints ALL OK
python -I plugin/hooks/tools/run_story_checks.py          # generated tables vs generators, prologue dump vs op table, proof checker self-test + injected defects, twin
synccore: build\all\synccore\fnvcoop_tests.exe --group refcmd   (ctest: unit_refcmd, twin_refcmd_keeps_first, twin_refcmd_events_become_state, twin_refcmd_player_shared)
python -I tools/coverage/run_tests.py                     # includes test_evidence.py
python -I tools/coverage/build.py                         # out/coverage/backlog.md, summary.md: the percentages above
```
All are in `tools/test_all.py` (the synccore ones through `synccore\run_tests.bat`). Mutation twins: `plugin/hooks/ref_ops.cpp` and `move_queue.h` twins 1..11 (calling reference dropped, AI
orders everywhere, Activate on anything, trailing defaults printed, wrong recipient, skill off by one, old move kept, never gives up, process-wide executor always wins, no fallback executor,
free text on the console line), `Mutations::refCmdKeepsFirst`, `refCmdAllState` and `refCmdPlayerShared` in synccore.

## Wire and version

`kWireVersion` and `kProtocolVersion` 5 -> 6 (one bump for this round), host sidecar 3 -> 4, client sidecar 2 -> 3, because the ShadowState encoding gained the RefCommand state section.
The flight golden traces (`synccore/flight/golden/*.nvfr`) were regenerated: the only differences are the version byte and the message checksums.

## What the gate needs to do

1. Set `[hooks] story_capture=1` in `nvcoop.ini` on every machine (default 0). `hooks.status` then lists the extra hooks; every one must say installed (a prologue mismatch is reported there).
2. `python -I plugin/hooks/tools/proof_story_capture.py --host 0 --guest 1` with two running instances (written, never run). `--selftest` is in the gate already.
3. Check `unmapped().storyDropped` stays near zero in a long session (commands whose reference or form has no stable name are dropped, not guessed).

## Not proven, and why

* Everything above is unit-tested only. The `ExtractArgs` output convention (one pointer per parameter, a `char[256]` buffer for a text parameter, an index for animation group and critical
  stage) comes from the engine tables, the xNVSE sources and xNVSE's own command code; nothing has run in the game.
* Console behaviour of a few commands is assumed from the GECK syntax: optional trailing parameters that are left out, `Rotate`'s axis letter, `SetActorFullName` taking a Message form id.
* Actor AI orders rely on the ownership layer (`isRemoteDriven`); until that layer runs in a game, every machine performs them (the safe default is "perform").
* `PlayGroup` / `PlayIdle` on a remote-driven actor are left to the owner and show through the snapshots' `action` field; that path is the enemy-anim lane's.
* State commands carry no pre-image: after a host load, a state command a client had issued and the host's older save did not is not taken back.

## Open items

* `UseWeapon` (13 calls, nine parameters): needs a second RefCommand shape. Not started.
* `ReleaseWeatherOverride` has no handler hook (see above); it relies on the weather poll.
* The static census tables and `census.json` were not regenerated; the live scan with `evidence.json` is the number to read. `questlines/systems/run_tests.py` already failed before this work
  (`m_bad_kind`, empty `patch.add`) and is not in the gate.
