# Avatar: player puppets (plugin/avatar), Milestone 2

Owner: avatar agent. **Everything here is game-free and unit tested against a fake game. Nothing has run in the game.**
The puppet layer follows FULL-SYNC-PLAN section 3.1: a restrained actor made from a template NPC, driven every frame from
the interpolated snapshot of the remote player (`synccore/live`), with an equipment correction loop, never saved, purged on load,
and a slot -> puppet ref map (the "ghost-ref map" of the acting-player design, `plugin/coop` `bindPlayerRefs`).

## Run

- `plugin\avatar\run_tests.cmd` (one command, ends in `avatar: ALL OK`). It builds standalone in `%TEMP%\nvav_build` (Ninja, 2 jobs,
  takes `out\locks\build.lock` only while compiling) and then runs:
  1. `python -I tools/avatar/check_groups.py` (+ its `--mutant`): `avatar_groups.h` equals the generated xNVSE group table.
  2. `avatar_tests`: the suite, then the whole suite once per runtime mutation twin (25 twins, each must fail at least one check).
     `--no-twins`, `--twin NAME`, `--list-twins`, `--verbose` for debugging.
  3. `avatar_live_tests`: the driver on top of the REAL `synccore/live` (sender delta codec, lossy jittery link, receiver, InterpBuffer),
     judged by game-free versions of the T7 detectors (slide, skate, T-pose proxy, float, jitter, rubber band, silence). 4 twins incl. two live-layer faults.
  4. `python -I tools/avatar/proof_puppet.py --selftest`: the in-game proof's analysis and its assertions on synthetic T7 streams,
     with 5 injected-defect twins (never touches a game).
- Added to the parent build with one line (`plugin/CMakeLists.txt` sub-directory list). The targets are `EXCLUDE_FROM_ALL`:
  the canonical `nvcoop.dll` build does not compile or link the puppet layer yet (see "Not wired").

## Files

| File | What |
|---|---|
| `avatar_types.h` | value types (`PuppetPose`, `Anim`, `Appearance`, `Config`, `Stats`), all numeric thresholds in `Config` (first guesses) |
| `loco.h` | locomotion codes carried in `live::AnimState::locomotion`, direction maths, sender-side `CaptureLoco` |
| `avatar_groups.h` | the animation group ids the driver asks for + `GroupName` (script name); checked against the xNVSE table |
| `anim_planner.*` | owner `AnimState` + velocity -> group (speed matching, jump, reload, draw/holster, aim pitch) |
| `equip_corrector.*` | per-puppet equipment correction loop (own item first, extras off, weapon last, backoff, flap guard) |
| `puppet_manager.*` | the driver: lifecycle, per-frame pose/anim/restraint/equipment, park, cross-cell, save/load, ref map |
| `puppet_engine.h` | `IPuppetEngine`: every engine call, nothing else touches the game |
| `script_engine.*` | `ScriptPuppetEngine`: `IPuppetEngine` on script-command text (`IScriptRunner` + `IEngineReads` supplied by plugin glue) |
| `live_bridge.h` | `live::InterpOut` -> `PuppetPose`, and the sender-side `MakeLocalSnapshot` |
| `testing/fake_engine.h` | the fake game (3D loads late, rebuild drops restraint, menus reset animation, AI strips/re-equips, id reuse, orphans in saves) |
| `avatar_faults.h` | runtime mutation switches |
| `tools/avatar/proof_puppet.py` | the in-game proof (WRITTEN, NOT RUN), `tools/avatar/check_groups.py` |

## Puppet traps -> test -> twin

Sources: VaultMP list (`docs/research/vaultmp-actor-sync.md` sections 2.2 to 4), FULL-SYNC-PLAN 3.1 ("restrained actors refusing
commands, animation re-assert after menus, speed matching, cell borders vs transitions"), live layer rules (snap above 10 m, smooth above 2 m).

| # | Trap | Handler | Test (`tests/test_avatar.cpp`) | Twin |
|---|---|---|---|---|
| 1 | commands to a ref without 3D are refused / lost | `Loading3D` phase; nothing but a 3D check until `Has3D` | `spawn_waits_for_3d` | `noWait3D` |
| 2 | AI takes a loose actor over | `SetRestrained` first, then re-checked every 250 ms and re-asserted | `restraint_reasserted`, order check in #1 | `noRestrainReassert` |
| 3 | menus reset animation | replay on the first Tick after a menu closed, nothing sent while one is open | `anim_after_menu` | `noMenuReassert` |
| 4 | a menu leaves the puppet near, not at, its place | forced pose on menu close | `post_menu_snap` | `noPostMenuSnap` |
| 5 | engine forgets the group without any event | `CurrentGroup` mismatch (300 ms) or heartbeat when unreadable; one-shot groups excluded | `anim_heartbeat` | `noAnimHeartbeat` |
| 6 | slide: owner code says idle but speed says walk (also walk/run mismatch) | speed matching with 150 ms debounce and run/walk hysteresis; a moving code with zero speed (wall) is kept | `speed_matching_planner`, `no_slide`, `planner_groups` | `trustLocoCode` |
| 7 | remote stops sending: run in place | `frozen` (dry buffer) or no pose for 3 s means idle at once | `frozen_and_stale` | `frozenKeepsRunning` |
| 8 | jump needs start / loop / land | `JumpStart`, `JumpLoop`, `JumpLand` (+ forward variants) | `jump_sequence`, planner jump test | `noJumpLand` |
| 9 | drawing a weapon the puppet does not hold | planner sees `weaponDrawn` only once the weapon is equipped | `weapon_draw_after_equip` | `weaponDrawBeforeEquip` |
| 10 | equip needs ownership; default outfit is worn; slot replacement | own item first, extras off, weapon last | `equipment_converges` | `noEquipAddFirst` |
| 11 | engine refuses equipment calls forever | exponential backoff, give up after 6, cleared when the wanted set changes | `equipment_backoff_giveup` | `noEquipBackoff` |
| 12 | AI puts the default outfit back on (STR-style fight) | after 3 reverts, `RemoveItem` instead of `UnequipItem` | `equipment_flap_guard` | `noEquipFlapGuard` |
| 13 | puppet of the local player | refused | `local_slot_and_limits` | `allowLocalSlot` |
| 14 | reconnect / double join duplicates | one puppet per slot, the old ref is deleted | `duplicate_slot`, `remove_player` | `duplicateSlotSpawns` |
| 15 | ref gone (cell unload) or id reused by something else | `IsValid` before every drive; respawn; never delete a foreign ref | `stale_and_reused_refs` | `noStaleRefCheck` |
| 16 | remote's cell is not loaded here: puppet floats in the void | delete after 500 ms grace, respawn when the cell loads | `park_when_cell_unloaded` | `neverPark` |
| 17 | cell borders vs transitions | exterior neighbours: `SetPos`; anything else: `MoveToCell` then wait for the rebuilt 3D and re-apply restraint, pose, animation | `cross_cell_move`, `exterior_border` | `crossCellSetPos` |
| 18 | 3D rebuilt in place (cell reload) drops animation | `Generation3D` change forces pose + animation + restraint check | `rebuild_3d_reasserts` | `no3DGenReassert` |
| 19 | never saved | despawn on `OnSaveBegin`, no respawn until `OnSaveEnd` | `save_despawns` | `saveKeepsPuppets`, `noSaveWait` |
| 20 | purged on load, orphans in an old save | `OnLoadBegin` drops refs without touching the world; `FindTaggedPuppets` purge after load, on the first Tick of a session, and when `WorldEpoch` changes without an event | `load_purges_orphans`, `first_tick_purge` | `noLoadPurge` |
| 21 | heading wrap and pitch | yaw wrapped to (-pi, pi], no SetAngle storm at the seam, pitch/roll never sent | `yaw_handling` | `noYawNormalize`, `usesPitchAsYaw` |
| 22 | too many engine calls per frame | `Config::maxEngineCalls`, round-robin start so nobody starves | `call_budget` | `ignoreBudget` |
| 23 | stale ghost-ref map | `RefOf`/`SlotOf` only for live refs | `ref_map` | `staleMapEntries` |
| 24 | spawn fails / 3D never arrives | backoff 0.5 s doubling to 8 s, `Failed` after 8 attempts, 3D timeout 5 s replaces the ref | `spawn_retry` | (covered by #1, counts) |
| 25 | snap and physics push | snapped sample forces SetPos + animation; drift > 2 m is corrected and counted | `snap_and_drift` | |
| 26 | script text must be exact | `ScriptPuppetEngine` pins every line | `script_backend`, `manager_on_script_backend` | |

Live integration (`avatar_live_tests`): a scripted 24 s owner (stand, run 300 u/s, sneak-walk, strafe, draw/aim, jump, 28 m teleport, 2 s silence)
through the real codec, 0% and 10% loss, 3 seeds each. Asserted: no slide, no skate, no T-pose proxy, no float, no jitter, exactly one big jump (the teleport),
max step < 12 units, legs stop within 800 ms of silence, exactly one spawn, no refused or foreign commands.
Twins: `frozenKeepsRunning`, `noWait3D`, live `noSnap`, live `extrapolateOnly`.

Observation for live-core (found by the integration test, not fixed here): with 25 ms of latency jitter on the link, `InterpBuffer` output advances
in lumps: single frames of 14 to 16 units where the nominal step at 300 u/s is 4.8, about every 100 ms (seen at t=2.3 s in all seeds, with and without loss).
The cause looks like the clock-offset min filter re-basing the shown time. It is below the 150-unit T7 rubber-band threshold, so the integration test only
bounds it at 20 units; it would show as slight stutter when running. `AVATAR_DEBUG=1 avatar_live_tests.exe` prints the frames around it.

Mutation twins here are runtime switches (`avatar_faults.h`, `--twin NAME`), the same idea as `synccore/live` `LiveFaults`, not yet the repo-wide
`FNV_MUT(n)` / `FNVCOOP_MUT` mechanism of `tools/mut`; converting is mechanical if the build-speed work wants one convention.

## What the driver needs from the live layer and the plugin

- Per remote player: one `live::InterpBuffer`; each frame `pm.SetPose(slot, FromInterp(buf.sample(now, actual)))` then `pm.Tick(now)`.
  `pm.ActualPose(slot, cell, pos)` feeds `sample()`'s `ActualPose`.
- Local player, 20 Hz: `MakeLocalSnapshot(netId, epoch, seq, tick, cell, pos, rot, vel, anim, &LocalRead)`. `rot = (pitch, yaw, roll)`, yaw = REFR rotZ.
  `anim` flags (weapon drawn, sneak, jump, reload, aim) come from the T7 reads (`weapon.out`, `weapon.aiming`, `move_flags` bit 9, ...), `LocalRead` from the mover flags.
- Equipment changes: a reliable event with the full wanted set -> `pm.SetEquipment(slot, set)`. Not part of the snapshot.
- Game events: `kMessage_SaveGame` -> `OnSaveBegin`, then `OnSaveEnd` when the save finished; `kMessage_PreLoadGame` -> `OnLoadBegin`; `kMessage_PostLoadGame` -> `OnLoadEnd`
  (names from `third_party/NVSE/nvse/nvse/PluginAPI.h`). `WorldEpoch()` is the backstop if one is missed.

## Bridge contract for the in-game proof (implemented by the real engine, see "Real engine" below)

`tools/avatar/proof_puppet.py` needs, on the machine that shows the puppet, `avatar.status` (puppets, stats, `tagged`, `engine`, `backend`, `local`),
`anim.probe` with `refs` accepting the puppet ref (exists), and on the machine whose real player is copied the bot input driver (`[dev] bots=1`).
Exit code 3 means "not wired / not ready" (nothing judged), 1 = an assertion failed, 0 = all asserted.

## Not wired before the engine existed (done: plugin/avatar/engine, next section)

The original list was: an `IScriptRunner` / `IEngineReads`, the module with `avatar.status`, the NVSE message hooks, `bindPlayerRefs`, a template NPC
base. The engine round replaced the first two by a `GamePuppetEngine` over an `IGameBackend` (below), added the module and the bridge methods, and
made the template configurable (`template=player` needs no ESP). `bindPlayerRefs` and the network side live in plugin/coop/live, which calls `avatar_glue.h`.

## Real engine (plugin/avatar/engine), round of 2026-10-21

**Everything below is unit tested on a fake game (`FakeGameBackend`) and compiles into nvcoop.dll. NOTHING of it has run in the game.**
The fake models what we expect the engine to do and is strict about console grammar; the first in-game run is `proof_puppet.py --loopback`.

Run: `plugin\avatar\engine\run_tests.cmd` (one command, ends in `avatar engine: ALL OK`). It builds `avatar_engine_tests` in the shared tree
(`tools/build/build.py`), runs the suite once for real and once per runtime mutation twin (`FNV_MUT`, 63 twins: 1-34, 40-56, 60-71, each must fail
at least one check), builds nvcoop.dll (the game side must compile), then `tools/avatar/verify_engine_pins.py` (+ `--selftest`) and
`tools/avatar/proof_puppet.py --selftest`. Registered in `tools/test_all.py` as "M2 avatar engine".

| File | What |
|---|---|
| `game_backend.h` | `IGameBackend`: the raw primitives (ref / cell / animation / equipment reads, `RunLine` on a calling ref, condition evaluators). The only door to the game |
| `puppet_game_engine.*` | `GamePuppetEngine : IPuppetEngine`. All policy: guards, spawn, per-axis writes, 3D tracking, `CurrentGroup`, cost accounting |
| `local_sampler.*` | `LocalSampler` (local player -> `live::ActorSnapshot` at 20 Hz), `EquipWatcher` (worn items -> reliable event), `ReadLocalFrame` |
| `avatar_runtime.*` | `AvatarRuntime`: sampler + watcher + `InterpBuffer` per remote + `PuppetManager` + dev loopback + `StatusJson` |
| `avatar_glue.h` | what plugin/coop calls (sinks, identity, `AddRemote`, `OnRemoteSnapshot`, ghost-ref map, `PreSave` / `PostSave`) |
| `engine_pins.h` | every engine address / offset used, prologue bytes pinned, with its source |
| `win/game_backend_win.*` | `WinBackend`: SEH-guarded reads, pinned calls, the console. x86 + xNVSE headers, nvcoop.dll only |
| `win/avatar_module.cpp` | module `m2_avatar`: ini, lazy runtime creation, bridge methods, NVSE messages, the glue functions |
| `testing/fake_backend.h`, `tests/test_engine.cpp` | the fake game and 59 tests (540 checks) |
| `tools/avatar/verify_engine_pins.py` | proves `engine_pins.h` against a reference exe (24 checks, 6 twins) |

### What the real engine does (each line has a test and, mostly, a twin)

- **Guard in front of every write**: a reference is touched only when it is an actor, not the player, not deleted / disabled, its base is one of the
  template bases AND its id is a created reference (`0xFFxxxxxx`). A placed NPC that happens to share the template base (template = a vanilla NPC) is
  never ours, so the load-time purge cannot delete the real one. A recorded base per spawned ref catches a reused id that now belongs to something else.
- **Spawn finds its own ref** (a console `PlaceAtMe` returns nothing): list the anchor cell's actors before / after. Anchor = the local player when the
  target cell is the player's cell, else a placed non-actor ref in that cell (one with a loaded 3D preferred). Reused ids are handled (a deleted ref
  that is still listed does not hide the new object). The new ref is positioned in the same frame (SetPos / SetAngle, best effort). It must pass the
  guard or it is deleted again at once. **Circuit breaker**: at most 8 spawns in 10 s, because a driver that keeps losing its refs would otherwise flood
  the world with actors.
- **SetPos / SetAngle compare with where the ref really is** and send one console line per axis that differs by more than 0.25 units: a standing puppet
  costs no line, a runner about one per frame (Y only), a diagonal runner two. Nothing is sent while the ref has no 3D.
- **`CurrentGroup`** counts a group as playing when ANY AnimData slot holds it (the slot layout is not proven); a reset (menu, rebuilt 3D) empties every
  slot and the driver re-issues the group. Unreadable = -1 (the driver's heartbeat).
- **3D generation** from the scene object's identity, plus "lost then regained". A rebuilt 3D forgets what we believed about the restraint, so the
  fallback of `IsRestrained` (used when the `GetRestrained` evaluator is unavailable) can never claim a restraint that the rebuild dropped.
- **Puppets stay hittable** (`MakeNonCombatant` = `SetIgnoreFriendlyHits 1`, no `SetGhost` unless `GamePuppetEngine` is built with `ghostPuppets`): the
  combat layer (plugin/combat) maps a puppet to its player slot through `SlotOf` and needs the engine's hit event on it, to forward the damage to the
  remote player and neutralise it locally. A ghost would let enemy shots pass through it. UNVERIFIED which of the two the engine really does.
- **Weapon draw = `SetAlert`** (no script command draws a weapon; VaultMP drives its puppets this way). `IsWeaponDrawn` reports the engine's own
  `IsWeaponOut`, never the request, so `proof_puppet.py` sees it if SetAlert does not do it.
- **Appearance is honest**: race and sex come from the template choice; face / hair / eyes cannot be set from the console (command table), so
  `ApplyAppearance` returns false and counts `appearanceApproximate` instead of pretending.
- **Cost accounting**: every console line is timed with QueryPerformanceCounter; `avatar.status engine` shows p50 / p95 / max microseconds. This answers
  "what does a script line cost" (was a TODO-RE).
- **Local player**: position and rotation from the reference ((pitch, yaw, roll) = (rotX, rotZ, rotY)), finite-difference velocity (smoothed, restarted by a
  teleport above 350 units, a cell change, a gap, clamped to 2200 u/s), locomotion from the speed (mover flags only when the walk / run bit is set),
  jump / reload from the running animation groups (227..244 / 173..199), aim from the process, weapon from `IsWeaponOut`, sneak from `IsSneaking`.
  The schedule advances by one period so 16.7 ms frames still give 20 Hz (a naive "now + period" gives 15 Hz; found by a test).
  Equipment is polled and published only when stable (250 ms), sorted, weapons flagged.
- **Hook layer interplay** (a real trap found by reading plugin/hooks): puppet console lines run inside `Script::Execute`. On the host the capture
  hub would record `Disable` / `AddItem` / `MoveToMarker` on a puppet as story effects and replicate them; on a guest the suppression gate would drop
  them. `WinBackend::RunLine` therefore holds `CaptureHub::pushMute`, `nvhooks::ApplyScope` and `ApplierExecScope` around every line.

### Engine facts used (engine_pins.h; `python -I tools/avatar/verify_engine_pins.py` re-proves them)

| Fact | Source | Status |
|---|---|---|
| `LookupFormByID` 0x4839C0 (cdecl) | engine-addresses.md | verified, prologue pinned, same bytes as plugin/hooks |
| `MenuMode` without argument 0x702360 (cdecl bool) | docs/re/dialogmenu-menus-vats-inventory.md | verified; also used by the `menuOpen` of the driver |
| evaluators `GetRestrained` 0x59FF90, `IsWeaponOut` 0x5A0550, `IsSneaking` 0x5A2F60, `GetDead` 0x59D840, `GetItemCount` 0x59D8E0, `GetEquipped` 0x59DA90 | eval column of command_table.json | address = table, prologue pinned; calling pattern as `nvworld::queryCondition` |
| `TESObjectREFR::IsDisabled` 0x565090 | engine_addrs.h (conf M) | disassembled: ExtraData 0x92 flags & 8 |
| ref fields (flags, base, cell, pos, rot, renderState -> niNode +0x14, NiAVObject world translate +0x8C) | xNVSE headers, animprobe | header offsets; +0x8C verified by animprobe's static check |
| process vtable slots 0x6E / 0xF9 / 0x101 / 0x103 / 0x115, ActorMover slot 8 | tools/animcheck/verify_engine.py | verified statically; a slot is called only when the vtable lies in .rdata and the target in .text |
| AnimData sequences at +0xE0.. , group id at animGroup(+0x68)+8 | animprobe | NOT proven (runtime guarded by the BSAnimGroupSequence vtable) |
| worn items: ExtraContainerChanges (0x15) -> Data -> objList -> EntryData{extendData, count, type}; ExtraWorn 0x16 / WornLeft 0x17 | xNVSE GameExtraData.h | header layout, SEH guarded, unproven in a run |
| loaded cells: `TESWorldSpace::cellMap` (+0x30) of the player's worldspace, cellState == Attached | xNVSE GameForms.h | header layout, unproven in a run |

### Wiring (done in plugin/coop/live against `avatar_glue.h`; the module needs no other change in main.cpp)

`nvcoop.ini [avatar]`: `enabled=1` (default 0), `template=player` (default: the local player's own base form, no ESP; UNVERIFIED that `PlaceAtMe` of it
works) or a hex NPC_ form id, `templates=RACE:m|f:BASE,...`, `localSlot`, `maxPuppets`, `loopback=1` (start the dev loopback), `loopbackOffset=160,0,0`.
Bridge (all on the main thread): `avatar.status`, `avatar.local`, `avatar.loopback {on, offset?}`, `avatar.add {slot, race?, female?}`,
`avatar.remove {slot}`, `avatar.feed {slot, x, y, z, yaw, vx, vy, vz, loco, cell?}` (one synthetic remote snapshot: a remote player without a network),
`avatar.presave` / `avatar.postsave`.
The runtime is created on the first frame in which the player exists in a cell; calls made before are remembered and replayed.

### In-game proof (written, NOT run)

`python -I tools/avatar/proof_puppet.py --loopback` with ONE game instance (`[avatar] enabled=1`, `[dev] animprobe=1 bots=1`, 15 m of free floor):
the loopback puppet copies the real player through the same interpolation buffer and driver a remote player gets, `anim.probe` reports both actors in
one reply, the proof splits it into two machines (removing the offset again) and runs the unchanged T7 analysis (`avatar.*` counters) plus the engine
counters of `avatar.status` (no console exceptions, no refused foreign write, no spawn without a new ref, p95 line cost, 3D drift) and the backend pins.

### Known limits of this round (each is also in the TODO-RE list)

- `kMessage_SaveGame` arrives AFTER the .fos is written, so the module cannot despawn puppets before an engine-started save (autosave, quicksave).
  The guarantee is the purge on load (orphans are found by template base + created id in every attached cell). `glue::PreSave()` / `PostSave()` and the
  bridge `avatar.presave` / `avatar.postsave` exist for saves that tools start; a missing `PostSave` times out after 10 s. A pre-save detour of
  `BGSSaveLoadGame::SaveGame` (0x8503B0) was NOT written: a hook on the save path is a risk to the owner's saves.
- Face, hair and eyes of a remote player are not applied (no console command): the look is the template's. With `template=player` every puppet looks like
  the LOCAL player, which is right for the loopback and wrong for remote players; a real build needs one template NPC per race / sex in an ESP
  (`templates=`), and a FaceGen transfer is future RE.
- `SetAlert` as the weapon-draw is a hypothesis; the Equip / Unequip groups carry the pose either way.

## TODO-RE (unknown engine facts, all unmeasured; the driver treats each as an ordinary refusal)

- Whether `SetPos` on a restrained actor and `PlayGroup` on an AI-less actor are accepted without VaultMP-style patches (`research/vaultmp-actor-sync.md` section 3 lists
  the FO3 sites; the NV equivalents would be found from the command handlers `Cmd_SetPos 0x5C05F0`, `Cmd_PlayGroup 0x5C0DF0`, `Cmd_SetRestrained 0x5D0920` in
  `plugin/hooks/tools/re/command_table.json`, early-out branches not read yet).
- Exact meaning of `PlayGroup`'s flag, `EquipItem`'s two flags, the sign/direction of `SetAngle Z`, and whether `PlayGroup` by name resolves the weapon-type variant.
- Sneak: whether `SetForceSneak` changes which sequence `PlayGroup Forward` plays.
- A way to draw/holster a weapon (no script command exists; `IsWeaponOut` 0x5CB660 reads it). `GamePuppetEngine::SetWeaponDrawn` uses `SetAlert` (hypothesis, proof asserts `avatar.wrong_weapon == 0`); `ScriptPuppetEngine` still returns false. The process `SetWeaponOut` virtual (neighbour of slot 0x115) is unknown.
- Appearance (face, hair): no console command exists; `GamePuppetEngine::ApplyAppearance` reports "approximate" and the template NPC's look is used.
- Cost of a script line per call (`Script::Run` 0x5AC400 compiles every line): the engine now MEASURES it (`avatar.status engine.costP50Us / costP95Us / costMaxUs`, bounded by `proof_puppet.py`). If 3 puppets at 60 fps are too slow, replace the hot calls with direct member calls after RE; `IGameBackend` does not change.
- `MoveToMarker 0x5CCF50` accepting a non-marker anchor ref; whether `kMessage_SaveGame` fires before the engine serialises refs (if after, only the load-time purge protects saves).
- `SetPos` across exterior cells of one worldspace moving the ref's parent cell (`SameSpace` assumes yes).
- Whether the engine uses the xNVSE group numbering (the T7 first in-game check measures this).
- Dead/ragdoll puppets, VATS, knock-down and cutscene animations are not handled at all in Milestone 2.

## Round of 2026-10-22: avatars finished in code (roadmap step 3)

**Everything below is unit tested on a fake game / a simulated network and compiles into nvcoop.dll. NOTHING of it has run in the game.**
Run: `plugin\avatar\full\run_tests.cmd` (one command, ends in `avatar full: ALL OK`; registered in `tools/test_all.py` as "M2 avatar full"). It builds in the
shared tree, runs the new 3-machine suite once for real and once per runtime twin (101..109), re-runs the suites that share the changed code (`avatar_tests`
with 36 twins, `avatar_engine_tests` with 69 twins, dll-wiring's `nvcoop_live_tests` with 34 twins) and `tools/avatar/proof_avatars.py --selftest`.

| Piece | Where | What it does |
|---|---|---|
| Rates 20 Hz near / 5 Hz far / 1 Hz elsewhere | `plugin/avatar/stream_rate.h` (planner), `plugin/coop/live/avatar_net.*` (use) | Every machine publishes its 20 Hz snapshots; the host relays; each (observer, subject) pair has a tier (same cell within 2800 units = near, 1.25x hysteresis; same cell beyond or adjacent = far; else out). A snapshot that is not due is kept and released later (latest wins), so a stopped player's last place is never lost. A client's uplink to the host takes the HIGHEST tier any other player has for it. Measured in the simulation over 6 s: near 120 snapshots, far 31, out 3 to 6. |
| Appearance sync | `avatar_types.h` (Appearance: hair colour, height, weight, 130 face coefficients; EquipEntry: weapon mod flags), `plugin/coop/live/avatar_wire.*` (Look v2, kind 0x43), `win/game_backend_win.cpp` (reads) | The local look is read from the game every 2 s (race, sex, hair, eyes, hair colour, height, weight, name, FaceGen coefficients when the guarded read validates) and sent when it changes, once to every newcomer. The first look of a player now reaches its puppet (it used to be compared with an empty previous look and dropped). |
| Applying a look | `puppet_game_engine.cpp`, `puppet_manager.cpp` (`SetAppearance`), `equip_corrector.cpp` | Same template (race, sex): updated in place, no respawn. Height: `SetScale ratio` on the puppet (the template height is taken into account). Another race or sex: a new puppet from the other template, equipment kept. Weapon mods: after the weapon is worn, one `EnableWeaponMod <weapon> <slot>` per write, verified by reading the `ExtraWeaponModFlags` back; an engine that cannot tell is asked once per slot; a refusing engine is given up on after 6 tries. |
| Hit-target puppets | `puppet_manager.cpp` (health check), `puppet_engine.h` (`IsDead`, `ResetHealth`), `puppet_hits.*` (router) | A puppet is hittable by design (the combat layer needs the hit on it). It is topped up with `ResetHealth` every 2 s; one that died anyway (burst, explosion that skipped the gate) is deleted and respawned after 1 s. `PuppetHitRouter` says "is this victim a puppet and which player does it stand for" (avatar API) and hands the hit to a port; the production port is the combat layer's gate (`glueGate`, `win/avatar_module.cpp`), which sends the HitEvent to the real player's machine and answers Clamp. No combat layer: the stand-in is clamped and nothing is forwarded (counted). |
| The ghost-ref map | `plugin/avatar/avatar_api.h/.cpp` | `puppetRefOf(slot)`, `slotOfPuppet(ref)`, `actingRefOf(slot)` (the local slot answers the player's own ref 0x14, a remote slot its puppet), `slotOfActingRef(ref)`, `isPuppet`, `puppetCount`, `forEachPuppet`. Registered by the avatar module as a `ManagerDirectory` over the runtime's manager; answers live puppets only. dll-wiring's `GhostOf(slot)` and `setPlayerRefMap` can call these instead of `avatar_glue.h`. |
| Lifecycle | tests (`full/tests/test_full.cpp`) plus two real fixes | join, leave, rejoin, interior (parked), exterior neighbour cell (SetPos only, same object), another worldspace (parked), host load (orphans purged, one puppet per player), client load, link loss. Fixed on the way: equipment that arrived before the runtime existed was dropped (now remembered and replayed); a look change used to respawn the puppet. |
| Bridge | `win/avatar_module.cpp` | new: `avatar.api`, `avatar.look`, `avatar.puppetlook {slot}`, `avatar.hit {slot, damage, limb?, attackerRef?}`; `coop.live` -> `avatars` now carries per slot `tier`, `rx` (accepted snapshots, the proof turns the difference into a rate), `lookInfo`, and `selfLook`. |
| In-game proof | `tools/avatar/proof_avatars.py` (WRITTEN, NOT RUN) | Two instances: wiring, look (sent equals stored, puppet gear and weapon mods read back), rates (15..24 Hz near, 3.5..7.5 Hz after a 60 m teleport, near again), lifecycle (interior park, come back, client load, no orphan), hits (router forwarded, the client's combat counters). `--selftest` runs it against a scripted fake game with 16 injected defects (all caught). |

### New traps, test, twin

| # | Trap | Handler | Test | Twin |
|---|---|---|---|---|
| 27 | a far player at full rate floods the link; a near one at 5 Hz stutters | `StreamRatePlanner` tiers + hysteresis | `rates_near_far_and_out`, `planner_tiers_hysteresis_and_pending` | `107` (rate ignored) |
| 28 | a relay would show a near neighbour at the far rate | uplink = highest tier of the observers | `uplink_follows_the_highest_tier` | `108` |
| 29 | the last position is stuck behind the rate gate when the stream stops | kept snapshot released by `poll` | `last_position_is_never_lost_behind_the_rate` | `109` |
| 30 | the first look of a player never reached its puppet | compare with what the puppet was BUILT from | `look_travels_and_applies_in_place` | `106` |
| 31 | every look change rebuilt the puppet | in place unless race or sex change | same test, `appearance_update` | `105`, `appearanceUpdateRespawns` |
| 32 | a weapon mod change is not a changed look | `AvLook ==` and `SameSet` include the mods | same test, `weapon_mods_applied` | `103`, `modsIgnoredInSet` |
| 33 | mods never applied / retried forever when refused | corrector mod step with give-up | `weapon_mods_*` (3 tests) | `noWeaponMods`, `modsRetryForever` |
| 34 | junk look data on the wire | reserved bits, mod flags, face cap rejected | `wire_look_v2_roundtrip_and_rejections` | `101`, `102`, `104` |
| 35 | a puppet dies of a stray hit and stays a corpse | `IsDead` poll, `ResetHealth` top-up | `puppet_hit_target_health`, `stack_hit_target_*` | `noPuppetHeal`, `noPuppetDeathRespawn`, engine `85` |
| 36 | the API repeats a lie (local slot or the real player as a puppet) | filtered in `avatar_api.cpp` | `api_map` | `apiLocalGetsPuppet`, `apiPlayerIsPuppet`, `apiActingNeedsPuppet` |
| 37 | a hit on a puppet kills the stand-in, or is forwarded without damage | `PuppetHitRouter` | `hit_router`, `hits_on_a_puppet_reach_the_real_player` | `puppetHitProceeds`, `hitsForwardEmpty` |
| 38 | body height of the remote player | `SetScale` ratio on the puppet | `appearance_height_is_matched_with_setscale` | engine `80` |

### Engine facts found this round (static, from the command table and the CommandInfo structs of a reference exe)

| Fact | Source | Status |
|---|---|---|
| `EnableWeaponMod` (opcode 0x1CB, handler 0x5DDAE0): needs a calling reference, parameters (Weapon, Mod Slot optional), help text "Mods the current weapon with the slot" | CommandInfo at 0x1190898 | the semantics (1-based slot, acts on the worn weapon of the calling actor) are a HYPOTHESIS; the proof reads the flags back |
| `ResetHealth` (0x1150, handler 0x5C6B60): needs a calling reference, no parameters | CommandInfo | used by the health top-up; unmeasured |
| `SetScale` (0x113C): calling reference, one float | CommandInfo | used for the height; unmeasured |
| `ModifyFaceGen` (0x1C4: String, optional Integer x2), `HairTint` (0x14C: 3 integers) take NO calling reference | CommandInfo | NOT used: they could change the LOCAL player. `MatchFaceGeometry` (0x11DF: Actor, Value) copies a face between two actors on one machine, which needs the remote player's NPC here |
| `ExtraWeaponModFlags` = extra data type 0x8D, flags byte at +0x0C | xNVSE GameExtraData.h | read from the WORN instance's extra data list (`ReadEquippedRaw`), guarded |
| `TESNPC::faceGenData[3]` (`values`, `useOffset`, `count`, `size`) | xNVSE GameForms.h comments | layout NOT proven: two readings are tried, one counts only if every float is finite and plausible; the numbers are sent and logged, nothing applies them |

### Still not done (honest)

- **Face, hair style and eyes of a remote player are not applied.** The data (hair, eyes, hair colour, face coefficients) travels, the puppet shows the template's face. A real
  fix needs a clone of the remote player's NPC base on every machine (or a FaceGen write path); `MatchFaceGeometry` is the nearest console tool and is not enough. The weight has no
  command at all. Both are reported through `appearanceApproximate`, never faked.
- **Hits on a puppet** are forwarded only through the combat layer (`[live] combat=1`). The router exists so the answer is the same everywhere; without combat nobody carries the report.
- Everything under "TODO-RE" above, plus: whether `EnableWeaponMod` and `ResetHealth` do what the command table suggests, whether `SetScale` on a restrained actor is accepted,
  whether the FaceGen reading validates, whether a puppet shot by an enemy plays a hit reaction that breaks the pose for a moment (the re-assert logic should catch it).

## Honest status

- Unit tested on a fake game and on the real live layer: yes (see Run). Proven in the game: **nothing**. The fake models the engine as we expect it to behave; every row in the traps
  table is a hypothesis about the engine until `proof_puppet.py` has run once.
- Thresholds (speed bands 20/190/230 u/s, debounces, parking grace, heartbeats) are guesses to tune from T7 counters.

- Engine round (2026-10-21): the real `IPuppetEngine`, the local-player sampler, the runtime with the dev loopback and the Windows backend are written and
  unit tested on a fake game (59 tests, 540 checks, 63 twins). The game side compiles into nvcoop.dll. Every pinned address is proven against the
  reference exe (24 checks); what the engine DOES when called that way (PlaceAtMe of the template, SetPos on a restrained actor, SetAlert drawing a weapon,
  the group numbering, the cost of a line, the cell map walk, the worn-item walk) is unknown until `proof_puppet.py --loopback` has run once.
