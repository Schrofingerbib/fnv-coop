# area-desync: per-area digests, targeted diff and heal (roadmap step 7, FULL-SYNC-PLAN section 4)

Status 2026-10-21: everything below is **unit-tested game-free only**. NOTHING has run in a game. The engine side (`plugin/areadigest/engine_facade.cpp`) is
written against the documented actor API and has never run. The module is OFF by default (`[areadigest] enabled=0`) and is not wired into the per-frame loop
yet: one line in `coop_module.cpp` (below) is still needed from the dll-wiring lane.

## Run it
- `plugin\areadigest\run_tests.cmd` (shared tree, x86, takes the build lock; prints `ALL OK`): the core suite with 40 runtime twins, the plugin suite with 18 twins,
  the Python dashboard suite with 6 twins, and the in-game proof script's own self-test with 6 injected defects. It is in `tools/test_all.py` twice (the C++ runner as a
  slow suite, the Python half as a quick one).
- Pieces: `synccore/areadigest/` (core, wire, 57 tests), `plugin/areadigest/` (config, actor source, shadow sources, runtime, service, dll glue, 34 tests),
  `tools/areadigest/` (dashboard), `plugin/areadigest/tools/area_proof.py` (in-game proof, not run), flight additions in `synccore/flight` (heal counters and markers).

## What it does
Every 2 s each machine sends the host a digest of the areas (cells) it has loaded, one order-independent hash per (cell, category): actors (alive, health bucket,
position bucket, cell), containers (contents), objects (open / lock / destroyed state), plus one global story digest. The host compares with its own world.

1. **Equal**: nothing happens. In a healthy session the host sends nothing at all and each client one small datagram per round. Measured game-free (test
   `perf.a_round_with_hundreds_of_actors_is_cheap`, fake sources, so no engine reads): one client digest for 400 actors in 4 cells plus 60 containers and 60 objects is
   124 bytes; a whole 2 s round on 3 machines costs 0.47 ms of CPU in steady state and 1.2 ms while 40 keys are being healed.
2. **Different**: the host asks that client for the full items of the mismatching groups (`MItemsReq` / `MItems`) and compares item by item.
3. **An item differs materially** (health off by more than 5, another alive flag, a position more than 2 m off, another cell, another state) **and both sides were
   quiet for `settleMs`** (1 s): the authority decides.
   - Actors: the **owner** of the actor. If the owner is a third machine the host asks it for the item first (by key). Unowned actors and actors in a handoff are
     never healed.
   - Containers, objects, everything else: the **host**.
4. **Heal**: the authoritative item goes to the machine that is wrong (`MHeal`), or is applied on the host itself when the host is wrong. The target re-checks before it
   touches anything: its item must still have the version it reported, it must not own the actor, no handoff may be in flight, its owner epoch must not be newer
   than the heal's, and the difference must still exist. A duplicated or late heal therefore changes nothing.
5. **Rubber-band rules** for an actor's position: more than 2 m off is moved smoothly (`Smoother`, 300 to 1500 ms, a few engine writes per frame), more than 10 m
   off or in another cell is snapped (`moveToCell` for a cell change).
6. **Repeated mismatch**: a key healed again within a minute is logged as a bug with both values (`BugReport`, at most one line per key per 10 s). The plugin writes it into
   the flight recorder (marker kind 8 plus a rate-limited desync dump).
7. **Story**: the global story digest (the same incremental bucket hashes the flight recorder uses, `bucketHashesCached`) is compared when both sides are at the same
   applied seq and world epoch. A mismatch asks the existing story repair to run now (`HostSession::requestDigest`, then the shard diff and `SyncRepair` that already
   exist); the repairs it sends (`HostStats::desyncRepairs`) are counted as story heals. Containers and objects work the same way (`delegated`): the story layer owns
   those effects, so a mismatch asks it to repair and no values are pushed from here.

### Guards (each has a mutation twin)
| Guard | Why it exists |
|---|---|
| Items that changed less than `settleMs` ago hash only (cell, alive) | a value that is still travelling must not make every digest differ |
| Heal only if both items were quiet `settleMs` | the same, at diff time (`inFlight` counter) |
| Corpses hash and compare neither health nor position | ragdolls rest in different places on different machines |
| Health tolerance 5 is smaller than the 10 hp bucket, the position bucket is the 2 m smooth distance | a bucket-boundary straddle is not a desync (`falsePositive` counter), every smooth-worthy error is visible |
| Backoff after a diff that found nothing to heal (hashes unchanged: 10 s, doubling, 40 s cap); not applied while something was in flight | an unhealable difference (an item one side has not loaded) is not diffed every 2 s |
| One open exchange per client, 3 s timeout, 8 groups per exchange, 1000 items per reply | bounded work and traffic; a silent client cannot block the host |
| Heal budget: 240 keys per minute per host (token bucket), 64 per exchange | a bad day produces a trickle of heals and a bug line, not a storm |
| Version, owner epoch, pending-handoff and owner checks at the target | nothing stale wins; the owner is never overwritten (the STR #906 lessons of FULL-SYNC-PLAN section 0) |
| Death is never applied by a heal | the actor module owns the death path (owner declares, a named NPC only through the story host); the actor source asks it to resync the actor |
| Resurrect refused | a resurrect is a story effect |
| Datagram checks | decoder bounds, finite floats, no trailing bytes, a client obeys only the host, the host trusts the transport peer, not the `src` claim |

## Counters, dashboard, flight recorder
- `areadigest.status` (bridge) returns per machine: `heals.total`, `heals.perMin` and per category (story, actors, containers, objects) `total`, `perMin`, `mismatches`,
  `inFlight`, `falsePositive`, `decided`, `raced`, `refused`, `missing`, `delegated`, `ownerSilent`, `repeats`; plus message counters and the bug lines. "Heals" are keys
  that were corrected **on that machine**; the sum over machines is the number of corrections. The target in every scripted test is **0**.
- `tools/areadigest/dashboard.py` sums the machines and prints one line, e.g.
  `area desync | heals/min story 0 actors 0 containers 0 objects 0 | heals total 0 | group mismatches 0 | bug lines 0 | machines 2/2 with the module`.
  `failures(summary)` lists what broke the target; `python -I tools/areadigest/dashboard.py status|nvfr <files>` does it from status JSON or flight dumps (exit 1 on a heal).
- The playbook engine (`tools/bots/playbooks/core.py`) merges `areadigest.status` into each machine's status and counts `heals.total` as the desync counter `areaHeals`:
  it must not rise during a step (tolerated like repairs under chaos profiles). A game without the module is asked once and then left alone.
- Flight recorder (`synccore/flight`, additive): `FlightRecorder::healCounters` writes `heals.<category>.total|perMin|mismatches` and `heals.total|perMin` into the header
  Meta record (every 5 s and on `areadigest.flight`), `healed` writes one marker (kind 8) per corrected key. Format: `docs/tools/flight-format.md`.

## Settings (`nvcoop.ini` `[areadigest]`, environment `NVCOOP_AREADIGEST_<KEY>` wins)
`enabled=0` (OFF), `intervalMs=2000`, `settleMs=1000`, `smoothMetres=2`, `snapMetres=10`, `hpTolerance=5`, `healBudgetPerMin=240` (0 = detect and report only),
`maxActors=400`, `actors=1`, `containers=1`, `objects=1`, `story=1`. Values are clamped (a warning says so); a text that is not a number keeps the default.

## Wiring still to do (dll-wiring)
In `plugin/coop/coop_module.cpp`, next to the other lanes' per-frame calls (after `nvcoop::actors::glueFrame(g_rt.get())`):
```
#ifdef NVCOOP_WITH_AREADIGEST
#include "areadigest/area_glue.h"
#endif
...
#ifdef NVCOOP_WITH_AREADIGEST
    nvcoop::areadigest::glueFrame(g_rt.get());
#endif
```
The module itself (`areadigest_module.cpp`, `NVCOOP_MODULE`) is already part of `nvcoop.dll` (CMake: `plugin/areadigest/CMakeLists.txt`): it reads the ini, registers
`areadigest.status` and `areadigest.flight`, drops the session part on a load or exit and joins the hot-reload registry. Until the `glueFrame` line exists the module
answers `{"enabled":true,"running":false}` and does nothing else. Optional: `setOwnerResyncHook` (engine_facade.h) maps "ask the owner for the whole actor state" to
`ActorSync::requestResync` (private today; without it a death heal is counted as refused), `setCellOfRef` puts containers and objects into their cells.

## In-game proof (written, not run)
`python -I plugin/areadigest/tools/area_proof.py --instances 0 1 --ref <Plugin.esm:HEX of an NPC the host owns>` with two instances in one session, `[live] actors=1`,
`[areadigest] enabled=1`: 30 s quiet (no counter moves), then the console sets the NPC's health wrong on the client only, which must have the owner's health back
within 25 s with exactly one heal on that client and none elsewhere, then 20 s stable. Its self-test (`--selftest`, part of the runner) injects six defects.

## Not covered, unknown, honest
- **Nothing has run in a game.** Every engine call (`forceAv`, `setPos`, `moveToCell`, the `netIdOf` / `ownerOfNet` lookups, the loaded-actor enumeration) is the
  documented API of `plugin/actors/engine`, used as described, never exercised.
- **Containers and objects are compared at shadow level**, not read from the engine: the digest covers what the session believes (`ObjState`, locks, `RefInv`), per
  reference, optionally per cell. A reader for the real chest contents and door states does not exist (TODO-RE). Engine-level drift of those is still caught by the 10 s
  story digest of the real world dump, which this module only asks to run early.
- **A heal can be overwritten by the actor port.** For a non-owned actor the port drives the position every frame from its interpolation buffer. If that buffer is
  stale, a direct `setPos` from here can be overwritten on the next drive. The sanctioned fix is a resync from the owner (the hook above); the direct write is the fallback.
- **Death heals depend on that hook.** Without it a "dead at the authority, alive here" difference is logged as refused on every round (a bug line after the second).
- Loaded cells are the cells of the synced actors plus the player's cell (shadow sources: "cell 0"); an empty loaded cell the player is not in is not reported.
- `named` is approximated by the base form's unique / essential flags; wrong only in the safe direction (such an actor is never killed from here).
- The CPU cost above excludes the engine reads (up to 400 actor reads per round, each behind SEH); that part is not measured. The collect cache (`collectCacheMs`, 100 ms) bounds how often it runs.
- The 10 s story digest and its repair are unchanged (`HostSession` desync options); this module adds a 2 s early warning for the story, not a second repair path.
- Build note: the shared preflight (`tools/test_all_selftest.py`) currently fails on another lane's suite declaration (splitscreen tools); unrelated to this module.

## Mutation twins (runtime, `FNVCOOP_MUT=<n>`)
Core (`synccore/areadigest`): 1 in-flux values hashed, 2 corpse health and position hashed, 3 health not hashed, 4 position not hashed, 5 state not hashed, 6 cell not hashed,
7 alive not hashed, 8 story digest ignores values, 9 exactly 2 m smooths, 10 everything past 2 m snaps, 11 another cell does not snap, 12 smoother jumps, 13 per-minute count
never expires, 14 tracker version never moves, 15 any wobble is a change, 16 no health tolerance, 17 corpses compared, 18 no digest rate limit, 19 in-flight values healed,
20 handoff in flight ignored, 21 host always the authority, 22 third-party owner never asked, 23 version check missing, 24 owner epoch ignored, 25 owner can be overwritten,
26 no-op heals applied and counted, 27 client obeys any sender, 28 story repairs not counted, 29 no backoff, 30 repeats never reported, 31 digest group count unbounded,
32 trailing bytes accepted, 34 only item counts compared, 35 stalled exchange never times out, 36 no heal budget, 37 items from a machine that was not asked accepted,
38 story compared at different seqs, 39 story compared across epochs, 41 / 42 flight export faults (counters not written, heal leaves no marker).
Plugin: 61 ini ignored, 62 no clamp, 63 on by default, 64 death never handed over, 65 named actor sent through, 66 corpse revived, 67 cell change done with a same-cell move,
68 medium error snapped, 70 dead actor reported alive, 72 lock changes invisible, 73 container contents invisible, 74 destroyed ignored, 76 unknown peer taken as the host,
77 new session keeps the old sync, 78 host link state ignored, 79 joins and leaves not forwarded, 80 heal leaves no flight marker, 81 counters never exported.
Python: disabled_counts, host_only, bug_ignored, permin_is_total, assert_skips_story, nvfr_meta_ignored.
