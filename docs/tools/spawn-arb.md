# spawn-arb: spawn-site arbitration (FULL-SYNC-PLAN 3.3)

Code: `synccore/spawn/` (game-free core), `plugin/spawn/` (module, engine adapter, glue, tests), `plugin/spawn/tools/spawn_proof.py` (in-game proof).
Status 2026-10-21: everything below is unit-tested game-free (fake engines, a lossy in-memory net, a random storm with oracles). NOTHING has run
in a game. The engine adapter has never touched a real actor.

## Run it
- `plugin\spawn\run_tests.cmd` (shared tree `build\all`, takes the build lock; prints `ALL OK` when the real suites pass, every mutation
  twin is caught and the proof script's self-test passes). It is in `tools/test_all.py` ("Spawn-site arbitration ...").
- Soak the storm: `set SPAWN_STORM_SEEDS=3000` then `build\all\spawn_plugin_tests\nvcoop_spawn_tests.exe --group storm` (3000 seeds passed on 2026-10-21; the suite runs 14).
- In game (the helper): `python -I plugin/spawn/tools/spawn_proof.py --instances 0 1 2 --respawn`, see "In-game proof" below. `coop.spawn` on the bridge shows the state.

## What problem it solves
Every machine's engine rolls spawns by itself: a leveled marker, a placed leveled creature, an encounter zone, an ambient random encounter.
Without arbitration one encounter would appear once PER MACHINE with a different random pick each, a cell reset on one machine would
bring back actors the party killed, and a client's own dynamic (0xFF) actor id would mean nothing to the others.

Rules (all enforced by `sp::SiteRegistry` on the host):
1. **One owner per site.** A site is a leveled placed ref, a marker, an encounter zone, a script spawn point or an ambient area. Its owner is chosen
   by `fnvcoop::OwnershipService` (host preferred, then nearest, hysteresis, epochs). A site handoff has no state to carry, so the host plays the new
   owner's Ack itself: Transfer and Commit leave in the same batch.
2. **Only the owner's roll counts**, under the current epoch. Everybody else's roll of the same site is turned off and the owner's result shown.
3. **Rolls and picks are made once.** A roll carries the leveled pick, the per-member base forms and the "nothing spawns this time" outcome.
   The host stores it; everyone shows that result.
4. **Created (0xFF) actors get server-issued net ids** from `[0xFFC00000, 0xFFFFFFFF]` when a client rolled them. A host roll keeps its engine id
   (identity, like host script spawns always did), so the two ranges never collide. The counter survives a host save / load.
5. **Cleared sites stay cleared.** The site clears when its last member dies. An interior stays cleared for ever (unless story authority calls
   `forceRespawn`); an exterior becomes `Respawnable` after its respawn time in GAME time (host clock, so Wait / Sleep count) or on the host's cell reset.
   A roll in the meantime is answered `NotDue` / `ClearedPermanent` (and a client that already knows throws it away without asking).
6. **Replays and spoofs change nothing.** A roll has a per-roller `rollId`; a replay gets the cached verdict and spawns nothing; a duplicate older than
   the cache is dropped; a report whose roller is not its sender is refused; a client cannot send verdicts or site records.

## Parts
| Piece | Where | Role |
|---|---|---|
| `SiteRegistry` | `synccore/spawn/spawn.h` | host: site table, ownership, arbitration, ids, timers, digest, save / load, join snapshot |
| `SiteView` | same | every machine: mirrors the table (rev ordered), local pre-check (`localRoll`), per-site directives |
| `RollTracker` | same | the roller: hold, resend (1.5 s), give up (12 s), turns a verdict into engine actions, waits for the owner's record |
| wire | `synccore/spawn/spawn_wire.*` | channel `0xA5` (docs/protocol.md section 2), bounded and fuzz-safe decode |
| `SpawnSync` | `plugin/spawn/spawn_sync.*` | one per machine: wires the three above to `ISpawnEngine` / `ISpawnNet` / `ISpawnHost`, digest heal, periodic sweep |
| `RollDetector` | `plugin/spawn/roll_detector.*` | groups "created actors nobody announced" (seen by the actor feed's created-id resolver) into rolls |
| `SpawnRuntime` | `plugin/spawn/spawn_runtime.*` | owns the SpawnSync of the running session, mux channel, joins / leaves (same shape as `ActorRuntime`) |
| `EngineAdapter` | `plugin/spawn/engine_adapter.*` | `ISpawnEngine` over the actor port's `IActorEngine` plus hooks (ledger, rebase, extra data) |
| `coop.spawn` | `plugin/spawn/spawn_rpc.*`, `spawn_glue.*` | bridge method: table summary, `{list:true}`, `{site:id}` |

### Flow of one encounter (three machines, host owns)
1. Each engine makes its own actors. The actor feed asks the created-id resolver about them; `SpawnSync::resolveCreated` says "unknown", **hides
   them at once** (Disable) and remembers them. After 500 ms of quiet the `RollDetector` groups them (same cell, within 1200 units) into one roll.
2. A client sends `MRoll` (members with its local handles, bases, positions; net ids 0). The host registry checks sender, report, replay, owner, epoch,
   site state. Owner's roll: **Accept** (ids issued, record published, one `SpawnRef` effect per created member committed to the story log, the host
   makes its own copy when it was not the roller). Others: **NotOwner / AlreadySpawned / NotDue / ClearedPermanent**.
3. The roller applies the verdict: Accept = bind each held actor to its issued id (so the SpawnRef that follows is a duplicate) and show it. If the
   SpawnRef beat the verdict and already made the canonical object, its own actor is the extra one and is deleted. Refusal = delete the created actors;
   placed refs are rebased to the owner's pick (or stay hidden until the owner's record arrives, then shown after at most 12 s).
4. Every other machine gets the `SpawnRef` effect through the existing world lane (ledger, queued until the cell loads) and the `MSite` record.

## Engine facts and assumptions
Addresses used: none new. Existing: `Cmd_PlaceAtMe 0x5C4A70`, `Call_PlaceAtMe 0x5C4B30`, `Cmd_PlaceLeveledActorAtMe 0x5D9810` (docs/engine-addresses.md,
`plugin/hooks/engine_addrs.h`, `plugin/hooks_world/world_addrs.h`). Everything else is console lines run on a calling reference through the actor
port's `IActorEngine`:
- hold = `Disable` / `Enable`; delete = `MarkForDelete`; kill = `Kill`.
- **TODO-RE: changing the base form of a placed ref.** No verified call. `AdapterHooks::rebase` is where the actor port's leveled-pick swap plugs in; without it
  a placed ref keeps the base this machine's engine rolled (counted in `rebaseFailed`), but is never left hidden.
- **TODO-RE: leveled placed refs.** xNVSE has `ExtraLeveledCreature` (extra 0x2E, `{baseForm, form}`, `third_party/NVSE/nvse/nvse/GameExtraData.h`); which of the
  two is the original list and which the rolled base is NOT verified against the exe. `AdapterHooks::extras` (also reads `ExtraEncounterZone`, extra 0x74) is not
  wired, so in game today only CREATED actors (encounters, markers, ambient) are arbitrated; placed leveled refs are not recognised.
- **TODO-RE: cell reset.** No hook for the engine's cell reset, so respawn runs on game time only (`Config::respawnGameMs` = 72 h is an ASSUMPTION; the
  respawn GMST is not read). `SpawnSync::onCellReset` exists for when a hook does.

## Wiring status (dll-wiring did it in `plugin/coop/live/live_glue.cpp`, 2026-10-21)
Wired: `setEngine` (adapter over the actor port), `setGameClock` (GameDaysPassed), `setIgnore` (puppets, stand-ins), created-id resolver, tracked / died
listeners, `glueFrame`, `coop.spawn`, host save sidecar `<save>.fnvspawn`, world-load purge.
NOT wired (this lane did not touch their files; each is a one-liner for dll-wiring):
1. `spawn::onHostSpawnEffect(def, spawnRef)` from the host's PlaceAtMe capture (`CaptureHub` sink for SpawnRef). Without it a host script's actor is seen by the
   feed a frame before it is announced: it is hidden for that time and then rolled as an ambient site (the duplicate SpawnRef is harmless: clients see a bound id).
2. `spawn::setPlayerPose(slot, present, cell, pos)` from the avatar layer's pose sink (same place as `actors::engine::setPlayerPose`). Without it ownership
   falls back on the proof every roll carries that its sender has the cell loaded (`Config::evidenceMs`, 3 min), which works but cannot notice a player walking away.
3. `AdapterHooks::extras` and `AdapterHooks::rebase` (TODO-RE above).
4. `nvworld::makeResolver(hostSide=true)` is identity for created ids. A host copy of a CLIENT-rolled spawn is bound in the ledger under its issued id
   (`AdapterHooks::bindCreated` / `createFromSpawnRef`), so effects that name that id on the host (RefScriptVar ...) must be translated through the ledger for ids
   `>= 0xFFC00000` (`sp::isIssuedId`). Not changed here (L12 owns it). Until then those few effects fail on the host for client-rolled actors.

## Honest limits
- The first moments: the feed finds an unannounced actor at its next scan (`[actors] scan` 500 ms unless an xNVSE ref event marks it dirty). Until then it is visible.
- A late member of an encounter (arrives after the 500 ms group window) is a second roll for an already spawned site and is deleted.
- Hold = Disable: a save written inside the hold window (one round trip) stores the actor disabled; `Show` re-enables it. A scripted actor that reads its own state in that
  window would see "disabled" (the host's script actors are not held when announced in time).
- Placed refs need the base swap (above) to look right on non-owners; until it is wired they follow the owner only for life / death (sweep every 2 s) and
  visibility, not for the leveled pick.
- Per-actor respawn flags of the engine (not site based) are not modelled.
- The em fuzz generator (`synccore/fuzz`) was NOT extended: it drives T2a's engine model, which has its own spawn-site code; the full-stack storm here is
  `storm.random_traffic_converges_with_one_copy_of_everything` (random moves, resets, kills, game-time jumps, script spawns, link cuts, duplicated / reordered
  messages; oracles: digests equal, no pending / waiting / untracked actors, one copy of every member in the right life state and base on every machine).

## Mutation twins (runtime: `FNVCOOP_MUT=<n>`)
synccore/spawn (`spawn_tests`, 1..28): 1 owner check, 2 epoch check, 3 idempotency cache, 4 AlreadySpawned, 5 permanent interiors, 6 NotDue, 7 issuer repeats an id,
8 host roll re-issued, 9 "nothing" roll not shared, 10 deaths never clear, 11 first death clears, 12 stale record applied, 13 view never suppresses, 14 bound duplicate kept,
15 refused created actors kept, 16 NotOwner retried, 17 roller not checked against sender, 18 ambient rolls never merge, 19 lax decoder, 20 cell reset revives interiors,
21 digest ignores state, 22 no auto-commit of site handoffs, 23 join table omits cleared sites, 24 load forgets the id counter, 25 member id collision accepted,
26 waiting placed refs never released, 27 duplicates older than the cache re-run, 28 roll proof wiped by pose updates.
plugin/spawn (`nvcoop_spawn_tests`, 41..59): 41 no hold, 42 no SpawnRef commit, 43 no bind, 44 no resend, 45 no enforcement of placed members, 46 host verdict dropped,
47 no heal, 48 no resync request, 49 sender checks skipped, 50 tracker not told about records, 51 detector ignores the cell, 52 detector releases early, 53 early tracking,
54 ignore predicate not asked, 55 purge keeps bindings, 56 deaths not forwarded, 57 leave not forwarded, 58 host makes no copy, 59 no periodic sweep.
Each must make its suite fail; `run_tests.cmd` checks that.

## In-game proof (for the helper)
Precondition: 2 or 3 instances in one session, standing together in an exterior cell, `[live] spawns=1 actors=1`, `[actors] engine=1`.
1. `python -I plugin/spawn/tools/spawn_proof.py --instances 0 1 2 --base 0001CF7C --type 60 --respawn` (a hex FormID, CrRadroach; the bridge console does not resolve editor ids, and a refused roll is reported as such).
   PASS = every step prints PASS and the exit code is 0.
2. By hand: `coop.spawn {}` on each instance (link true, same `digest`), `coop.spawn {"list":true}` (same state / owner / epoch / members per site),
   `coop.owner {"ref":"fnvcoop.created:FFC00001"}` for an issued id.
3. If step 1 of the script fails with "no actor appeared": the guest hook refused the console PlaceAtMe; walk into a real random encounter instead.
4. Report: stats from `coop.spawn {}` (`rollsHeld`, `rollsSuppressed`, `rollsAdopted`, `tracker.droppedBound`, `rebaseFailed`, `healsSent`) from every instance.
