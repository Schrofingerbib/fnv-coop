# World time: clock, wait / sleep / travel consent, saves, world objects (FULL-SYNC-PLAN 3.6 and 3.9)

Owner: world-time (roadmap step 6). Code: `plugin/world_time/` (new) and the world-object part of `plugin/hooks_world/`.
Status: **code written and unit-tested; nothing here has run in a game.** Every claim below says which of the two it is.

## What it does

| Area | Rule | Where |
|---|---|---|
| Game time | The host's clock is the only clock. Beacons every 5 s and at once after a jump; clients snap when 2.5 game minutes off, hard on a jump. | `wt_clock.*`, `wt_wire.*`, `wt_session.*` |
| Weather, time scale | Already host-authoritative (`SetWeather`, `SetTimeScale` effects from L5 and the L12 poll). Unchanged. Weather replay is still unproven in game (see internal lane notes). | `plugin/hooks_world` |
| Wait / sleep | Party consent. The SleepWaitMenu is turned into a party question; a grant moves every clock once. | `wt_broker.*`, `wt_session.*`, `wt_glue.cpp` |
| Fast travel | Free by default (engine check: not in combat). `travel=party` or a story tether: party vote, then everybody goes. A `FastTravelLock` stays with the existing rules engine (`EnableFastTravel 0` everywhere). | `wt_broker.*`, `wt_session.*`, `wt_glue.cpp` |
| Saves | Only the host's save is the world. Clients: engine autosaves switched off (INI), scripted saves already dropped by L5, `RequestSave` from the host is acknowledged and ignored. Sidecars and the rejoin baseline already exist in synccore (`Runtime::saveSidecar` / `onGameLoaded`). | `wt_save.*`, `world_plan.cpp` |
| Doors, locks, destroyed, activators | `ObjectState` effects. L5 captured only SCRIPT commands (`Lock`, `UnLock`, `SetOpenState`, `SetDestroyed`). The new poll sees NATIVE changes (a player opening a door, lockpicking, a trap): host sends, clients heal after 1.5 s. | `wt_objects.*`, `world_hooks.cpp` (`objectsInPlayerCell`, `readObject`) |
| Ownership flags | New lane-local world effects `SetOwner` (SetOwnership / ClearOwnership), `SetCellOwner`, `SetCellPublic`, captured by four new command hooks, applied by console lines. | `world_effects.h`, `world_owner_addrs.h`, `world_hooks.cpp` |
| Enable / disable | Already `EnableRef` / `DisableRef` from the engine function hook (`TESObjectREFR::SetDisabled`), which covers script and native paths. | L5 |

## Files

| File | What |
|---|---|
| `wt_types.h` | channel byte 0x7E, `GameTime`, `ReqKind`, `Reason` |
| `wt_clock.*` | calendar math, `ClockAuthority` (host), `ClockFollower` (client), drift as hour-of-day difference plus whole days |
| `wt_wire.*` | the channel codec (7 message types, crc32, strict range checks); docs/protocol.md section 15 |
| `wt_broker.*` | `ConsentBroker`: the rules around the party question (combat, one at a time, cooldown, cap, re-check on grant, tether / party travel) |
| `wt_session.*` | `WorldTimeSession`: one machine's whole behaviour behind `IWorldTimeEngine` (wt_engine.h) and an injected send function |
| `wt_objects.*` | `ObjectWatcher`: host diff of native door / lock / destroyed changes, client heal |
| `wt_save.*` | `decideSave`, `SaveGuard` (INI lines for the engine's four autosave triggers) |
| `wt_glue.cpp` | x86 glue: module, ini, clock globals, SleepWaitMenu poll, pending-travel detour, object tick, bridge methods |
| `tests/test_worldtime.cpp`, `tests/wt_rig.h` | unit tests, a rig of 1..4 machines over a lossy network, a storm test |
| `tools/worldtime_proof.py` | in-game proof (not run) with its own `--selftest` |
| `run_tests.cmd` | one command: build, 57 twins, proof selftest; prints `ALL OK` |

## Run the tests

```
plugin\world_time\run_tests.cmd        # shared tree build\all; ALL OK or a non-zero exit
plugin\hooks_world\run_tests.cmd       # includes the ownership / ObjectState / AdvanceTime / RequestSave plans (twins 47..51)
```
Last run (this lane, 2026-10-21): world-time 63 tests, 469 checks, 0 failed, 57 of 57 twins caught; hooks_world 894 checks, 0 failed, 51 of 51 twins caught.
Twin numbers: world-time 101..169, hooks_world 1..51.

## Configuration (nvcoop.ini, everything OFF by default)

```
[worldtime]
enabled=1          ; the whole module. Environment override: NVCOOP_WORLDTIME=1|0
travel=free        ; free | party (set the same on every machine; the host's beacons tell clients)
restMinutes=60     ; what a wait / sleep costs when the menu is turned into a party question
objects=1          ; native door / lock / destroyed poll
objectsPerTick=40  ; refs examined every 10th frame
saves=1            ; engine autosaves off on clients and during a no-save story moment
```
Needs the coop module and the dialogue node (`[coop]` with dialogue enabled): the party question is `DialogueNode::raiseModal`. Without a node every
wait / sleep / party trip is refused with "That cannot be done right now." (the safe side).

## How it is wired (no edits to main.cpp or coop_module.cpp)

* `wt_glue.cpp` registers its own `NVCOOP_MODULE` and reads `nvcoop::coop::Runtime::active()` every frame; it registers channel 0x7E on the runtime's mux.
* The consent answer arrives through `DialogueNode::setMenuResume`: the glue installs a wrapper every frame that keeps ids it raised and forwards every other
  menu to `nvui::router()->onMenuResume` (L11 keeps working).
* `AdvanceTime` reaches a client through `nvworld::handlesEffect` -> `planEffect` -> `StepKind::AdvanceClock` -> `nvworld::setAdvanceClockHandler`.
* `ObjectState` and `RequestSave` are now applied by the world lane too (`handlesEffect`): same console forms as L5's RemoteApplier used (`lockCmd`,
  `unlockCmd`, `setOpenStateCmd`, `setDestroyedCmd`), plus `setObjectStateObserver` so the client book holds the host's word even for objects that are not
  loaded yet. The hub sink feeds the same observer on the host, so a script-captured change is never sent a second time by the poll.
* New hooks (host only, prologue-checked, skipped on mismatch): `Cmd_SetOwnership` 0x5B57E0, `Cmd_ClearOwnership` 0x5D6D10, `Cmd_SetCellOwnership`
  0x5D17F0, `Cmd_SetCellPublicFlag` 0x5C7660 (`world_owner_addrs.h`, bytes read from a reference exe, pinned by `tests/world_code/owner_prologues.txt`), and the
  pending-travel setter 0x93BE30 (bots_addrs.h PendingTravelSet) in the glue. `QueueUIMessage` (xNVSE) shows the refusals.

## Honest status: unit-tested only, not proven in game

* The clock is written by direct TESGlobal writes (0x35 year, 0x36 month, 0x37 day, 0x38 hour, 0x39 days passed). Whether the sky and scripts follow at once is
  unproven. The engine's own time-advance routine was not found statically (TODO-RE); the calendar uses a non-leap month table (the host's beacon corrects it).
* The SleepWaitMenu (menu id 0x3F4) is detected by polling the top menu id (`TopMenuId` pin) and closed with `CloseAllMenusCore`. The amount the player chose in
  the menu is not read (TODO-RE): the party question uses `restMinutes`. Wait and sleep are not told apart.
* The pending-travel detour assumes 0x93BE30 is only called with a travel object whose +0x30 holds the marker (`docs/tools/T6a-actions.md`, found statically).
  The re-issue after a grant builds the object the way `fastTravel()` in bot_engine.h does. Unproven: that the engine accepts it without the map menu.
* The four ownership commands follow the GECK parameter lists (`SetOwnership [owner]`, `ClearOwnership`, `SetCellOwnership cell owner`,
  `SetCellPublicFlag cell flag`); the proof exercises them. `SetINISetting "bSaveOnTravel:GamePlay" 0` and the other three INI names are from the game's
  INI, not read back yet (`worldtime.status.autosavesOff` only reports our intent).
* The object poll covers doors, containers and terminals of the PLAYER'S CELL (one cell in an exterior). Terminals: lock only; hacked / locked-out terminal
  state has no known engine accessor (TODO-RE). Activators and traps have no readable state beyond `RefScriptVar`, which already syncs their scripts.
* Weather replay is still the L12 open item (ForceWeather could not be driven in the test save).

## In-game proof (for the helper)

Two or three instances in one session, `[worldtime] enabled=1`, `[dev] bots=1`, `NVCOOP_TEST_HELPERS=1`:
```
python -I plugin/world_time/tools/worldtime_proof.py --instances 0 1 2 --door falloutnv.esm:XXXXXX [--party-travel falloutnv.esm:YYYYYY]
```
Steps: module/pins, clock follow and pull-back, wait by consent, refusal, native door (console `Activate`, no `SetOpenState`), autosave switches, party trip.
Manual extras: lockpick a chest on the host and read `world.query GetLocked` on a client; open a door on a client and watch it follow the host's answer; load a
save on the host and check `worldtime.clock` on the client.

## Scripted time skips: once per party event (G38, owner decision D7)
`ClockAuthority` (host): a forward leap of the host clock that no granted wait / sleep explains, of at least `once_min_hours` (12) game hours, with the size (within `once_tolerance_min`, 60 minutes) of one seen
`once_window_s` (120) seconds ago, is the same party event again (the Canyon door's +3 days when a second player walks through, Honest Hearts' +14 days): the host takes it back with `takeUndo` and sends no beacon,
so the clients never see it. Shorter leaps (a script that sets the hour) and skips of another size are never undone. ini `[worldtime] once_event=1|0 once_min_hours once_window_s once_tolerance_min`.
Twins 170, 171 (`wt_clock.cpp`). Unproven in game: that the engine's own scripts leave the clock where the host wrote it, and that a script's other effects of the repeat (items, quest stages) are idempotent.
