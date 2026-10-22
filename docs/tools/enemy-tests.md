# Enemy / NPC sync tests (fight-test, 2026-10-21)

Owner: fight-test (Track A, enemy-sync round). Status: **code and game-free tests done; nothing has run in a game.** Everything below
is proven only against T6b's fake co-op world. The in-game runner exists and is meant for the Track B helper
(docs/CONTRIBUTING-HANDOFF.md, "Track B task: run the enemy tests in game").

FULL-SYNC-PLAN section 6 asks for two-copy tests of the live layer. This is that: four playbooks, one scenario, one runner.

## Run it

Game-free (about 2 minutes on a busy PC, no game, no bridge):

```
python -I tools/bots/playbooks/tests/run_enemy_tests.py          # unit tests + every mutation twin, prints RESULT: ALL OK
python -I tools/bots/run_fight.py --fake                          # the four playbooks on the fake world
python -I tools/bots/run_fight.py --fake --which fight --bug double_damage     # a twin: must FAIL (exit 1)
python -I tools/bots/run_fight.py --fake --list-bugs              # every deliberate bug and what it breaks
```

In a game (two copies joined as one co-op session, host = pipe 0; see the Track B task for the full recipe):

```
python -I tools/bots/run_fight.py --instances 0,1 --preflight-only     # which bridge methods does this build have? exit 3 = BLOCKED
python -I tools/bots/run_fight.py --instances 0,1                      # all four, in order: fight, handoff, spawn, loot
python -I tools/bots/run_fight.py --instances 0,1 --which handoff --keep-going --outdir out/fight-run1
```

Exit codes: 0 all pass, 1 a test failed, 2 usage or setup error, 3 BLOCKED (a required bridge method is missing: nothing was run).

## The four tests

All four start from the scenario `scenarios/goodsprings_enemies.json` (two powder gangers GSPG01/GSPG02 and the two VCG02 geckos,
both players next to them with health raised, three duct tape on the first ganger). After EVERY step the engine settles, runs snapdiff
host vs client, and fails if a desync counter rose. At the end each test runs the T7 animation / pose check with the FIGHT thresholds.

| Test | Steps | What must hold |
|---|---|---|
| `enemy_fight` | walk to the enemies; one set everywhere; engage; both kill each enemy; after the fight | every machine agrees on alive/health/(owner, epoch); the AI flag is on the owner's machine only; every non-owner copy is within 150 units (2 m, the plan's rubber-band rule) of the owner's; each shot is applied once (health falls by exactly hits x damage in the fake, at most shots x `max_hit` in a game); each death happens once (`death_count == 1` on every machine); each kill is credited to exactly one player and every machine agrees who; no duplicate of an enemy's base form; an enemy's hit on a player lands once; nothing attacks after it died |
| `enemy_handoff` | fight starts; host walks away; the new owner kills one; host back at the border; host joins in again; overall | ownership of every living enemy moves to the client exactly once (epoch + 1, all machines agree, nobody pending); the AI runs on exactly one machine at every poll, including during the walk (a sampler runs while the host walks); nothing jumps (distance per poll beyond 150 units + 600 u/s x elapsed time between the two polls), health never rises, no duplicate; the fight goes on (the new owner's kill and the former owner's kill, resolved through the new owner, are each applied and credited once); no flapping when the host returns to the border, at most 2 owner changes per NPC over the whole test |
| `enemy_spawn` | nothing there yet; the encounter appears; same NPCs everywhere; quiet period; leave and come back; each dies once | exactly N new NPCs on every machine; the same (net id, base) list on every machine (leveled pick shared, ids shared, no duplicates); one owner and one AI each; still N after 5 quiet rounds; no second roll when the players leave and return (site mode, fake only); each dies once; live count back to the start |
| `corpse_loot_race` | at the ganger; both kill him; same loot on the corpse; all grab at once; late looter | the loot on the living ganger and on his corpse is identical on every machine; when both grab it at the same instant every unit goes to exactly one player (party gain == corpse contents, takes == contents, corpse empty everywhere); an empty corpse gives nothing |

Spawn has two modes. `host` (the game, scenario refs): the host places N copies of the gang base at the marker with the console
(`placeatme`, UNVERIFIED), which is the closest a bot can get to a script spawn. `site` (fake only): both players walk into an encounter
site's radius at the same moment, so both could trigger it; the fake spawns it once, the twins break that.

## Bridge methods the tests use

| Method | Status | Used for |
|---|---|---|
| `coop.status`, `ref.info`, `dev.countRefs`, `anim.probe`, `bot.*`, `world.query` / `eval` | exist (T6a, T7, L10) | counters, positions, duplicate counts, animation, shooting, health |
| `coop.owner {ref}` | exists in `plugin/actors` (`actor_glue.h`); needs the engine adapter that feeds `ActorSync` (actor-port agent) or it answers `known:false` and every test stops at its first check | owner, epoch, `ai`, `pending`, `netId`, `dead` |
| `coop.combat {ref}` -> `{deaths, credited:[slot], hitsApplied}` and `coop.combat {self:true}` -> `{hitsTaken}` | **REQUESTED from the combat hooks** (combat-hooks agent). Not in this build: the checks that need it are skipped with a note, never faked | death counted once, kill credited once, NPC hits on the player counted once |
| `dev.refsOf {base}` | exists (T6a) | encounter member lists; without net ids from `coop.owner` only counts are compared |
| `actors.port` | added by the actor-port agent (2026-10-21) | preflight prints `enabled`, `session`, `fed` per instance: `enabled=False` or `fed=0` means the adapter is not feeding `ActorSync` and the owner checks cannot work |
| `combat.status`, `anim.npc` | being added by the combat-hooks and enemy-anim agents (2026-10-21) | their answers are saved to `engine_stats.json` next to the result at the start and end of each test, so a human can compare the game's own counters with the verdict. Never asserted: what they count is not verified in a game |

`run_fight.py --preflight-only` prints which of these the build answers.

## The fake NPC layer (`playbooks/fakenpc.py`, `FakeCoop(npc_ai=True)`)

The owner machine runs each NPC's AI (walk at 110 u/s toward the nearest player who is within a leash of its post, draw the weapon,
attack every 1.8 s from 450 units, stagger for 0.6 s when hit, die at 0 health). Every other machine shows the owner's state from
100 ms ago. Health, death, owner and epoch replicate through the normal reliable path (so snapdiff sees them); positions and
animations do not (like the real live layer). 20 deliberate bugs (`fakecoop.BUGS`, the ones after `endgame_ignores_notyet`), each
registered with the playbook that must catch it in `registry.TWINS`, and each with the regex its failure reason must match in
`tests/test_enemy.py` (`REASONS`).

## The playbook clock (the Track A inbox flake, fixed)

The old playbooks waited on `time.monotonic()` with 0.2 to 0.3 real second budgets on the fake world. On a busy PC the process did not
get that much CPU, so six clean playbooks failed and a rerun passed. Now (`playbooks/clocks.py`, used by `core.Ctx`):

* fake runs measure every wait in the world's virtual time (`world.vt`), with a floor of 5 virtual seconds, so CPU load cannot fail
  them and a real desync (never converges, ownership never moves, a reliable message lost for good) still fails after the full virtual
  budget; a clock that stops moving fails with a clear message instead of hanging;
* live runs keep wall time but multiply only the waiting budgets by `LoadMeter.factor()` (a calibration workload timed now against its
  time on a quiet PC, like synccore's `budgetScale()`, capped at 8). No check is loosened;
* `opts["clock"] = "wall"` keeps the old behaviour; the twin `test_twin_the_old_wall_clock_fails_when_the_process_is_starved` shows it
  fails on a starved process, and `test_the_six_flaky_playbooks_pass_under_cpu_load` runs the six with busy threads.

Existing tests keep their numbers (`handoff_s: 0.2` ...): in a fake run they are now virtual seconds, lifted to the 5 s floor. Reason:
the numbers were budgets for CPU time, which is exactly what must not matter.

## Animation wiring

* `tools/animcheck/config.py`: `FIGHT = DEFAULT.with_(state_grace_ms=250)`. The comparison matches inside a +-150 ms window, so a missing
  600 ms stagger shows as a ~300 ms episode: above the FIGHT grace, below the default 400 ms. Unmeasured first guess like all thresholds.
* `playbooks/animwatch.py`: `Playbook.anim_profile = "fight"` selects the profile and `FIGHT_NPC_SPEC` (adds `dead_mismatch == 0`,
  `stance_mismatch == 0`, `pos_err_p95 <= 120`, `glitch.deathpose == 0`). When the NPC owner changes (`ctx.anim.npc_owner(machine)`), the
  pose comparison runs once per ownership window against that window's owner, leaving 600 ms around the change out; glitch detectors and
  the avatar comparison still run over the whole stream. Without the windows a clean handoff run fails (twin in the tests).
* The collector now parses each probe reply at once and drops the raw dict: a minute of virtual fight was hundreds of MB before.

## Files

`tools/bots/playbooks/`: `pb_enemy.py` (4 playbooks + fake builders), `fakenpc.py`, `clocks.py`, `core.py` (clock, `advance`, `sampling`,
`anim_profile`), `fakecoop.py` (NPC hooks, 20 bugs, atomic hits and container locks), `probe.py` (owner_info, actor_pos, death_count, kill_credits,
hits_taken, encounter_members), `animwatch.py`, `registry.py` (entries, `ENEMY_TESTS`, twins, `ANIM_BUGS`), `scenario_adapter.py` (mappers),
`tests/test_enemy.py` + `tests/run_enemy_tests.py`. `tools/bots/run_fight.py`. `scenarios/goodsprings_enemies.json`. `tools/animcheck/config.py`.

## Limits (be honest)

* The fake proves the playbooks' logic and checks, not the engine, the real timing, or the real console grammar. Thresholds (150 units,
  600 ms, 250 ms) are first guesses.
* UNVERIFIED in a game: the console line `player.setav Health 5000`; `additem` on a living ganger replicating to the guest and landing on
  the corpse; enabling the VCG02 geckos by console outside their quest; `placeatme` for the spawn test; that `Bot.pos(ref)` works for an ACRE;
  `world.query GetActorValue` (the Track A detour fix is another agent's); that the host's walk to Novac is short enough to leave the loaded area.
* `coop.combat` does not exist yet: death-once, credit-once and hit-twice-on-player are unchecked in a game until it does (the playbooks
  note this and carry on).
* Hits are checked per shot only when the weapon damage is known (`max_hit` in the scenario refs); otherwise only an upper bound or nothing.
* `enemy_spawn` in a game does not exercise the engine's own encounter zones (a bot cannot trigger one on demand): it places the NPCs with
  the host console. The site mode with two simultaneous arrivals is fake only.
* A 600 ms stagger or a 700 ms swing is a fake number. If the game's animations are shorter than about 550 ms the animation check cannot see them
  missing (the +-150 ms matching window), whatever the grace.
