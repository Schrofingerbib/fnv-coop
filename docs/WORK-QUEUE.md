# Work queue (2026-10-21)

The code for every roadmap step is written and covered by game-free tests. What is left is running it in the game and fixing
what the game shows. The work splits in two:

- **Track B (contributor, in game):** a PC that can run two game copies at once. Run the in-game tests, fix the test tools, and
  report game-side desyncs as issues with the failure folder attached.
- **Track A (maintainer):** fix what Track B reports, plus the open items below. Merge Track B's PRs.

Every feature added in the last rounds is switched off by default in `nvcoop.ini` until its in-game test passes. The exact steps
for every module are in [docs/INGAME-TESTS.md](INGAME-TESTS.md).

## Already run in game (maintainer PC, 2026-10-21)

| Test | Result |
|---|---|
| Actor adapter, one copy (`tools/actors/proof_actor_port.py`) | 36 of 36 checks pass: reads, AI off/on, place, heading, drive, stand-in clone, kill/revive, protect, ledger, release |
| Enemy fight, two copies (`run_fight.py --which fight`) | Step 1 passes (both players walk to the enemies, worlds equal, no repairs). Later steps in progress |

Fixed along the way, all in the current build:
- A guest's own-player console lines (`player.moveto`, `player.setav`) were dropped by guest suppression; now they run locally.
- The co-op link dropped for good after any stall longer than ENet's default timeout (a long load); timeouts are 60 to 120 s now
  and the client reconnects and resumes by itself.
- Provisioning did not copy the plugin's data files (`nvcoop_guest_local_run.json`), so guests blocked every DLC setup script.
- The snapshot compare now treats per-machine DLC state (radio quests, the DLC player factions, load-time timers) as per-machine.
- Scenario jumps wait for the co-op session to resync after a reload, and the next playbook on the same running games jumps
  in place instead of reloading.
- Bridge state dumps and screenshots use absolute paths (the game resolves relative paths in its own folder).

## Track B (in game, in this order)

Setup for every two-copy test (always with these switches; `NVCOOP_TEST_HELPERS=1` must be set before the copies start):
```
cmd /c tools\runner\build_l1.cmd
python -I tools/runner/run.py provision --n 2 --ini coop.enable=1 --ini dev.bots=1 --ini dev.animprobe=1 --ini live.actors=1 --ini live.combat=1 --ini live.spawns=1
set NVCOOP_TEST_HELPERS=1
python -I tools/runner/run.py up --n 2 --wait-pipe 480
```
Steam must be running. Press away the DLC message boxes after the first load (`Bot.dismiss_messages`, see
`docs/tools/T6a-actions.md`). If a copy still stops answering right after a load ("main thread did not service the request"),
report it with the `nvcoop_actors_ledger.txt` of both instances: A1 is fixed, so that would be a new cause.

1. **Enemy tests:** `python -I tools/bots/run_fight.py --instances 0,1 --keep-going --outdir out/fight` (fight, handoff, spawn,
   corpse loot). Answer the UNVERIFIED lines in `docs/tools/enemy-tests.md`. Tune a threshold only with a written reason.
2. **Single-copy proofs** from `docs/INGAME-TESTS.md`: avatars (`tools/avatar/proof_avatars.py`, `proof_puppet.py`), real-time
   VATS, loot, world/time/travel, guest intents, area digests, companions and crime. Report each as PASS or the failing check.
3. **Two-copy playbooks per area:** the VATS playbook per weapon class, the loot race, doors/terminals/containers, companions,
   fast travel and sleep votes (`tools/bots/run_coop.py`).
4. **Split-screen with real controllers:** `docs/tools/splitscreen.md` section 2 (2 players, then 4).
5. **Story in game:** the Goodsprings sweep first (65 of 104 stages passed before), then the questline playthrough bots in
   `tools/bots/playbooks/questlines/` one questline at a time.
6. **Two real PCs:** installer and launcher from `docs/PLAYER-GUIDE.md`, then Host and Join over a real network, with and without
   `--chaos bad_wifi`.

For every failure: fix the test or tool if it is wrong (with a regression test), otherwise open an issue with the `FAIL-*` folder
and the flight dumps (`python -I tools/flight/flight.py diverge`). Do not edit Track A code (`synccore/`, `plugin/coop/`,
`plugin/hooks*/`, `plugin/actors/`, `plugin/combat/`); describe the fix in the issue instead.

## Track A (maintainer)

1. **A1 (FIXED 2026-10-21): host hang right after a save reload.** Cause: the actor ledger kept created refs (0xFF ids) from an
   earlier run, and `EnginePort::undoStale` deleted or un-restrained whatever object had that id after the load. Stale created-ref
   entries are now dropped unless the ref is still a restrained actor (twin 67); with the stale ledger put back, two runs did not
   hang. Found with a PDB build and a symbolized stack sampler.
2. **A1b: geckos disabled on the guest after an in-place jump.** After `run_fight` jumps in place (no reload), the two VCG02 geckos
   are enabled in the fight cell on the host but disabled with no parent cell on the guest (flags 0xC09, cell 0), restrained by
   the port. The guest made 15 stand-ins during the run. Look at how a host MoveRef of a non-owned actor into another cell meets
   the stand-in path.
3. **A1c: the strict playbook counts a heal after a reload.** On the first run after a reload the session found one digest
   mismatch and repaired 256 effects (the guest rejoining). Either the jump waits until that first digest round settles, or the
   playbook starts its counters after it.
4. **A2: everything Track B reports** as a game-side desync.
5. **A3: guest actions without a ghost ref.** With avatars off, a guest's activation runs as the host player
   (`ACTING-STANDIN ... host player 14 acts`). With `[live] avatars=1` the puppet map exists; check the intents path uses it and
   test it in game.
6. **A4: actor adapter engine pins.** On one copy the three direct calls (placeAtMe, getActorValue, isDisabled) refused their
   prologue check and the console fallback ran; on the two-copy run they passed. Find out why they differ.
7. **A5: the last script command** without sync: `UseWeapon` (13 calls) needs a second command shape.
8. **A6: switch features on by default** once their in-game tests pass, area by area.
9. **A7: merge Track B's PRs** about every hour.
