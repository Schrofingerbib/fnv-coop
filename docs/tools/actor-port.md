# actor-port: the real IActorPort and the engine feed (plugin/actors/engine)

Status 2026-10-21: written and unit-tested game-free (fake engine, two- and three-machine chains with a real `ActorSync`). It compiles into
`nvcoop.dll`. **Nothing here has run in a game.** Every engine fact is either pinned from the exe/xNVSE headers (listed below) or marked
TODO-RE with the fallback that is used instead.

Run it: `plugin\actors\engine\run_tests.cmd` (shared tree, prints `ALL OK`, 64 runtime twins) and
`python -I tools/actors/run_tests.py` (offline self-test of the in-game proof, with twins). Both are in `tools/test_all.py`.

## What it is

| Piece | File | Job |
|---|---|---|
| Public API | `actor_engine_api.h/.cpp`, `registry.h` | what other lanes include: `ownerOf`, `isRemoteDriven`, `routeLocalHit` / `onLocalHit`, `onLocalDeath`, listeners (tracked / died / lost / drive), `setAnimSampler`, `setRefExclusion`, `setPlayerPose`, `onEffect`, `beforeSave`, `configure`, `portStats` |
| Engine interface | `engine_iface.h` | `IActorEngine` (reads + writes by ref id) and the small `IEngineHost` a real engine must provide |
| Script backend | `script_engine.h/.cpp` | `ScriptActorEngine`: every write is one console line run on the actor (`SetPos`, `SetAngle`, `MoveTo`, `SetRestrained`, `StopCombat`, `StartCombat`, `EvaluatePackage`, `SetActorRefEssential`, `ForceActorValue`, `KillActor`, `ResurrectActor`, `Disable`/`Enable`, `MarkForDelete`). Text pinned by tests |
| The port | `engine_port.h/.cpp` | `EnginePort : IActorPort` |
| The feed | `actor_feed.h/.cpp` | scan, death poll, player pose, sessions, world changes, saves; calls `ActorSync::track / onActorLoaded / onActorUnloaded / onLocalDeath / purgeStandIns / setPlayerPose / onNativeBase` |
| Ledger | `ledger.h/.cpp` | persisted list of what the port changed in the world (restrained, clones, disabled originals, essential flags) |
| Real engine | `engine_real.h/.cpp` (x86 only) | `RealEngineHost`: xNVSE layouts + SEH-guarded reads |
| Module | `engine_module.cpp` (direct source of `nvcoop.dll`) | builds the objects, `setGamePort`, ini, bridge methods, xNVSE ref events, host effect sink |
| Tests | `tests/` | fake engine, port rig, feed rig, 2 to 3 machine chain |
| Proof | `tools/actors/proof_actor_port.py` | in-game proof on one game copy (never run) |

Glue edits outside the folder (additive): `plugin/actors/actor_glue.h/actors_glue.cpp` gained `setSyncConfigurer` (the module sets
`SyncConfig::adjacent`) and `glueSessionGeneration`; `plugin/actors/CMakeLists.txt` adds the subdirectory; `tools/test_all.py` has two suites.

## The AI switch: `SetRestrained`, and why

ActorSync wants "AI off" on every actor this machine does not own, "AI on" on the owner, and never a recreation of the actor.

* **Chosen: `SetRestrained 1` (+ `StopCombat` first when the actor is in combat).** It is the only per-actor switch in the vanilla command
  table (`TAI` is global, `SetUnconscious` / `SetGhost` / paralysis change the actor's state, not just its brain). VaultMP and NV:MP use it for
  the same reason. The owner side is `SetRestrained 0` + `EvaluatePackage`, then `StartCombat <last target>` when the old owner's snapshots
  carried a combat target that resolves to a ref here.
* **What the port will not do:** change a restraint it did not make. It reads `GetRestrained` first; an NPC a quest or script restrained is
  left alone in both directions (`aiRef == 0`), and an actor the port restrained earlier (ledger) is recognised after a reload of its cell.
* **Lines run through `ApplyScope` and `ApplierExecScope`** (plugin/hooks): on a guest the hooks block shared-state mutators called while a
  script runs, and the console runs lines through `Script::Execute`; without the scope `SetRestrained`, `KillActor`, `StopCombat` would be dropped.
* **VaultMP traps** (docs/research/vaultmp-actor-sync.md) and what is done about each:
  1. restrained actors refuse some commands (FO3 needed code patches): every write returns bool, a refusal is counted (`consoleFailed`), never throws;
     the proof checks that no line was refused. Which commands a restrained NV actor refuses is **unknown**.
  2. commands run off the main thread: all calls are main-thread only (frame callback).
  3. no interpolation: `drive()` takes the `InterpBuffer` output and `pose()` reports where the ref really is so the buffer can ease physics pushes.
  4. native NPCs were never neutralised in VaultMP: here the feed freezes every tracked non-owned actor at track time (ActorSync turns the AI off
     until it knows the owner), which costs one console line per actor on a cell load (two more for the owner when it takes over).
  5. animation re-assert: not this lane (plugin/actors/anim).
* **Save safety:** a save made while an NPC is restrained would keep it frozen in single player, and the host's save IS the world. Two layers:
  the ledger file (`nvcoop_actors_ledger.txt` next to the dll) lists every ref the port restrained, cloned, disabled or made essential; and the
  same list is written into the save's `.nvse` cosave (record `ACLG`) and merged back when that save is loaded, so a save from an earlier run or an
  earlier session is cleaned too. On the next start, on every game load, and at the end of a session the port undoes the entries whose refs are
  loaded; entries for refs that are not loaded stay until they are. `engine::beforeSave()` additionally removes clones before a save you start yourself.
  (Unverified: that NVSE calls the save callback for a save made from the menu, and that `ResolveRefID` accepts every id we store.)

## Inventory

An owner's AI uses its items (a ganger drinks a Stimpak mid-fight); a replicated AddItem effect lands on every machine by itself, the AI's own use does not.
`ActorSync` therefore reads the inventory totals of every actor it owns (`IActorPort::sampleInventory`, `invPollMs` 500, four actors per tick; the engine
calls are plugin/loot's `readContents`, wired through `actors::engine::setInventoryAccess`, so they exist whether or not the loot service is on) and
publishes a list that changed and then stayed the same for `invSettleMs` (1 s) as `MInventory` (reliable, owner and epoch judged by the host like an
anim event). The first read is a silent baseline. Everybody else makes its copy match with `AddItem` / `RemoveItem` (`applyInventory`), retried up to
five times, written when the actor loads. The owner's last use before a death or a handoff is flushed at once, a joining client is sent the lists
already published, and a corpse's contents belong to the loot service from the death on. An actor with an unresolved leveled list is not synced
(the reader says no).

## Driving

`drive()` only records the wanted pose (and tells the drive listeners). `flush()` applies it once per frame:
* per actor at most one application per `driveMinMs` (33 ms) unless snapped; a pose within `posEpsilonXY` 0.25 / `posEpsilonZ` 0.75 units and
  `yawEpsilonDeg` 1 degree of what was last written writes nothing;
* only the changed axes are written (`SetPos X|Y|Z` is one line per axis, plus `SetAngle Z`): typically 2 to 4 lines per applied step;
* a per-frame budget `maxLinesPerFrame` (24); pending actors are served snapped first, then oldest-applied first, so a crowd cannot starve a far actor;
* a cell change between interiors, or interior and exterior, is `MoveTo <anchor> dx dy dz` (anchor = a ref inside the target cell, the player when
  it is there); a step between exterior grid squares of one worldspace is a plain `SetPos`; a cell that is not attached here is never entered.
* heading: snapshot `rot = (pitch, yaw, roll)` with yaw = REFR `rotZ`, exactly as plugin/avatar sends it; written as `SetAngle Z <degrees>`.
  **The sign of `SetAngle Z` against the stored `rotZ` is unmeasured**; the proof's `place.heading_sign` check settles it.
* cost: every line is compiled by the script interpreter; the cost per line is **not measured**. `IEngineHost::directPose()` is the hook for a direct
  write once someone finds a verified `SetPosition`-style function (TODO-RE: none is recorded in docs/engine-addresses.md).

## Sampling (owner side)

`sample()` fills: cell, pos, rot, velocity (from consecutive samples; a jump above 500 units is a teleport, speed is clamped to 1500 u/s),
`anim.weaponDrawn / aim / sneak` (BaseProcess slots 0x115 / 0x101, ActorMover flag 0x200), `anim.locomotion` (avatar's `CaptureLoco`, same codes the
puppets use), `ai.combatTarget` (ref to NetId through the port's directory or the player resolver), `ai.package` (form id without the load-order byte),
`ai.alert` (3 in combat, 2 weapon out, else 0: derived, no engine read), and the actor values Health, Fatigue and the seven limb conditions. Then
`setAnimSampler`'s callback may refine anything (jump, reload, better locomotion). `anim.jump` and `anim.reload` are not read by the port.
Actor values are written back with `ForceActorValue <name> <value>` (names: strings found in FalloutNV.exe at 0x1066110..0x1066258, e.g.
`PerceptionCondition` for the head); a value is written only when it differs by 1 or more, and **Health is never written below 1**: death is
its own message, a mirrored 0 would kill the copy before the owner's death arrives.

## Stand-ins and leveled picks

* **Stand-in:** `Call_PlaceAtMe` (plugin/hooks_world/world_addrs.h, prologue checked before the first call) next to an anchor ref in the target cell,
  restrained at once, moved to the pose, actor values applied, ledgered. Refused when the real actor is bound here, past `maxStandIns`, while a
  save is being written, or when there is no anchor or base form; a refusal backs off 500 ms (ActorSync asks every tick). Removal is
  `Disable` + `MarkForDelete`, run `deleteQuietMs` (750 ms) after the clone was taken out of the directory, never in the same frames as its last
  `SetPos` (game 2026-10-22: three client crashes, the engine ran a queued `SetPos X` on a removed stand-in's id, found a NavMesh there and called
  through text, EIP 6C676E61); the ledger entry stays until the delete ran, a save or the end of a session deletes at once. A removal the engine
  refuses stays in the ledger and is retried every 2 s.
* **Leveled pick:** there is **no verified in-place base change** (TODO-RE). When the shared pick differs from the local roll the port places a
  clone of the pick, disables the real actor (ledgered) and drives the clone; ownership of the AI follows `activeRef`. Story NPCs are never
  swapped. Losing the real actor, a session end, or a save puts everything back; after a save the swap is redone (`onNativeBase`) after `saveGraceMs`.
  Unverified in game: the clone's look (equipment from the base only), scripts attached to the real ref, and whether a disabled real actor
  keeps quest aliases.
* **Protection of named NPCs:** reference-level `SetActorRefEssential 1`; the port gives it back (`0`) unless the base form is essential itself.
  Named = base form essential or ACBS "unique" (bit 0x20, FNVEdit meaning; xNVSE only names the creature bit) (TODO-RE: not checked in the exe).
* **Death:** the feed polls tracked actors every 100 ms. Owner's engine killed it: `ActorSync::onLocalDeath` publishes it (a named NPC is refused
  and revived). A non-owned copy that died locally waits `deathGraceMs` (1.5 s) for the owner's message, then is revived; a story NPC's copy is
  never revived (it can only be a host `KillActor`). A dead or dying actor is never restrained: `SetRestrained 1` on one overwrites its life
  state with 5, which every reader counts as alive (game 2026-10-22: the owner's ganger read alive, the other machine dead). The combat glue should call `engine::onLocalDeath(ref)` from the kill path instead of
  relying on the poll.

## What other lanes wire (state of this checkout)

* **plugin/coop/live (dll-wiring)** already does: `[actors]` ini parsing into `engine::configure`, `setRefExclusion` (puppets, ghosts), `setPlayerPose`
  for every remote slot, `setPlayerNetIdResolver`, `setCreatedIdResolver` (spawn-arb's resolver), `engine::onEffect(e)` for applied effects and
  `engine::beforeSave()` for saves it announces. It needs `[live] actors=1`; with it off the port stays installed but unfed.
* **plugin/combat (combat-hooks):** uses `ownerOfNet` through its own port; the hit hook should call `engine::onLocalHit(hit)` (true = forwarded to the
  victim's owner, do not apply local damage) with `engine::setHitForwarder`, and the kill hook `engine::onLocalDeath(ref)`. Until then the feed's
  death poll (100 ms) is the only death path, and local damage on a remote-driven copy is overwritten by the owner's values.
* **plugin/actors/anim (enemy-anim)** uses `addDriveListener`, `setAnimSampler`, `addActorDiedListener`, `addActorLostListener`.
* Not wired by anyone: the host's captured `CompanionOwner` effects reach ActorSync through the module's own capture-hub sink; a guest gets them
  only through `engine::onEffect` in the coop effect path (live_glue.cpp line "if (G().sw.actors) onEffect(e)").

## ini and bridge

`nvcoop.ini [actors] engine=1 scanMs=500 maxLinesPerFrame=24 driveMinMs=33 maxStandIns=64 restrain=1 swapLeveled=1 persistLedger=1`;
`[dev] actorport=1` adds `actors.read {ref}` and `actors.dev {op}` (ops: bind sample ai place drive protect swap kill revive standin unstandin undo
release scan). `actors.port` (always) returns counters, ledger sizes, the pin check and the SEH fault count.

## Engine facts used

Pinned or taken from existing verified sources: `LookupFormByID` (xNVSE), REFR pos/rot/cell/flags, Actor `lifeState` +0x108, `swimming` +0x14D,
`baseProcess` +0x68, `actorMover` +0x190, BaseProcess slots 0x9F / 0x101 / 0x115, Actor slot 0x10B (animprobe, checked against a reference exe);
`Call_PlaceAtMe` 0x5C4B30 (hooks_world); `Actor::GetActorValue` 0x66EF50 and `TESObjectREFR::IsDisabled` 0x565090 (hooks/engine_addrs.h, prologue
compared at first use; a leading `E9` counts as "detoured by us or another mod, still callable"); `g_actorProcessManager` 0x11E0E80 lists
(`highActors`, `middleHighActors`) and the player's cell `objectList` for the actor enumeration; the `GetRestrained` condition through
`nvworld::queryCondition`. Everything is read behind `__try`.

Unknown (TODO-RE, with the fallback): a direct SetPosition call (console lines instead); an in-place base swap (clone and disable); jump / reload
state (left to the anim sampler); alert state (derived from combat target and weapon); whether `ForceActorValue` on a limb condition shows
crippling visuals; the ACBS unique bit meaning for NPCs; which commands a restrained NV actor refuses.

## Tests and twins

`nvcoop_actor_engine_tests` (92 tests): script text and masks, ledger persistence, the port against a fake engine (AI switch both ways, script-restrained
actors, epsilons, rate cap, line budget and fairness, cells, sampling, values, placement, protection, stand-ins, picks, death with retries,
ledger clean-up, saves), the feed with a real `ActorSync` (tracking filters, unload hysteresis, deaths, sessions, world changes, saves, caps), the API
(ownership answers, hit routing, listeners), and a two- and three-machine chain over a lossy net (one brain, smooth copy, handoff without
recreation, 6 rounds of ownership churn, stand-in until the real actor loads, death once, shared pick, stand-ins rebuilt after a load, link
loss and rejoin). Twins 1 to 66 (40, 58 are unused) are `FNV_MUT(n)` markers in the sources; `run_tests.cmd` runs each as `FNVCOOP_MUT=n` and requires
the suite to fail.

Found by the chain test while writing it: (1) tracking before the players' places are fed hands the first grant to the wrong machine (a needless
handoff 600 ms later), so the feed now feeds the local pose before it scans; (2) the final state of a handoff is sampled after the old owner's AI went
off and `StopCombat` had cleared the combat target, so the new owner could not take the fight up again; the port now keeps the AI state of the last
sample taken while the AI ran.

## In-game proof (for the helper)

Enable `[dev] actorport=1` on one game copy, load a save near an ordinary NPC, then
`python -I tools/actors/proof_actor_port.py --instance 0 [--ref Plugin.esm:HEX] [--pick Plugin.esm:HEX] [--game-dir <game dir>]`.
PASS looks like `proof_actor_port: all N checks passed`; exit 3 means "not wired" (nothing judged), exit 1 prints the failed checks. The checks
that most likely fail first: `place.heading_sign` (SetAngle sign), `wiring.pin.*` (exe differs), `console.no_refused_lines` (a command spelled or
parameterised wrongly: the line count says how many), `clone.protect` (essential flag), `swap.*` (only with `--pick`).

### Two scripted proofs that replace the old hand steps (INGAME-TESTS actor-port items 5 and 6)

Both are bridge calls only (no desktop input, no human), each has `--selftest` with a fake world and twins, and both run in `python -I tools/actors/run_tests.py`.

`python -I tools/actors/proof_actor_port_two_copy.py --host 0 --client 1 --ref FalloutNV.esm:104C70 [--away-ref Plugin.esm:HEX] [--allow-restrained Plugin.esm:HEX] [--outdir DIR]`
Host and client in one session near an NPC the host owns (`[live] actors=1`, `[dev] bots=1`). It checks `actors.port` session and fed, `GetRestrained` on the NPC
(console eval with the NPC as calling ref) 0 on the owner's copy and 1 on the other, then moves the host with `player.MoveTo` to `--away-ref` (default the Novac map marker
`FalloutNV.esm:09289B`, the destination of `scenarios/ownership_handoff.json`) while sampling both copies continuously: ownership moves to the client with the epoch up by one,
`GetRestrained` flips with it, NO sample has 0 on both copies (each pair is read as three reads, a b a, so the order of the reads cannot fake or hide a gap), the NPC never steps more
than 350 units between samples on either copy (it keeps its walk position) and the copies end within 200 units. The host then returns, `KillActor` runs on whichever machine owns the NPC,
`GetDead` must be 1 on both and `actors.port` `stats.deathsRefused` must not rise; `coop.leave` on the client and then the host must leave `session=false`, the whole ledger at 0 and nothing
restrained in `world.actors`. A copy that cannot read the NPC (cell not loaded there while the host is away) gives an unreadable sample that is skipped and counted, never read as 0.
Twins in the self-test: no session, not fed, owner copy restrained, non-owner copy free, no handoff, a gap where both copies are free, a position jump on the new owner, restraint that does
not follow ownership, death not synced, death refused, ledger kept after leave, restraint kept after leave. Exit 3 = not ready (the host does not own the NPC, no session).

`python -I tools/actors/proof_actor_port_save_check.py --instance 0 --ref FalloutNV.esm:104C70 --inst-dir <instance dir> [--save-name autosaveaclgcheck]`
One copy with `[dev] actorport=1` (a session, if there is one, is ended with `coop.leave`). It restrains the NPC through the port, saves with the bridge `saveGame` (only its own save name,
overwritten each run), ends the session and empties the live ledger (`actors.dev release`), loads the save with `loadSave`, waits for `worldReady` and a working console, then asserts the new
part of `Data/NVSE/Plugins/nvcoop.log` has `actors: cosave of the loaded game added N ledger entries to undo` with N >= 1, `GetRestrained` is 0 within 30 s, the ledger's restrained count is 0 and
`stats.consoleFailed` did not rise. The cosave count exists only in the log (no bridge counter; adding one is a small C++ change in `engine_module.cpp` `CosaveLoad`), so `--inst-dir` or `--log` is required.
Twins: save refused, NPC not restrained by the port, live ledger not emptied, game never ready, no cosave line, cosave line with 0 entries, undo fails (NPC stays frozen), ledger leak, refused console lines, `coop.leave` that does nothing.
