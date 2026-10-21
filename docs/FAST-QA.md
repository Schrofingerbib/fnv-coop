# Fast QA loop

Goal: one in-game fix round in a minute or two instead of 15 (a target, not a measurement). Nothing in this file has run in a real game yet. Every
tool was tested on fake worlds and stub cores only. The section "Not verified in a game" lists what to watch on the first live run.

Where the time goes in the old round, and what replaces it:

| Old cost | Old time | Now |
| --- | --- | --- |
| Rebuild the DLL | 1.5 min | same, but only the core (`build_split.cmd`) |
| Restart both game copies | 4 min | `run.py swap` reloads the core inside the running games |
| Load the save and press 14 DLC boxes | 1 min | prepared save, a few seconds |
| Two fixed 60 s sync-settle waits | 2 min | event-driven waits, about 6 s when healthy, fail early when stuck |
| Walk and setup | 1 min | `--from-step N` arranges the world for the failing step only |
| The test steps | 2 min | `--timescale 3` |
| Manual diagnosis | 3+ min | failure reasons name the cause, `inspect_live.py`, `from_failure.py` |

All commands run from the repo root `fnv-coop` and use `python -I`.

## 0. One-time setup

The reload needs the split build: a small loader (`nvcoop.dll`) that stays in the game, and the core (`nvcoop_mod.dll`) that gets
swapped. Copies started from the old monolith DLL cannot hot reload. They need one restart onto the split build:

    plugin\loader\build_split.cmd
    python -I tools/runner/run.py provision --plugin-dll out/L1split/nvcoop.dll --core-dll out/L1split/nvcoop_mod.dll --ini coop.enable=1 --ini dev.bots=1 --ini dev.animprobe=1
    python -I tools/runner/run.py up

(Match the `--ini` flags your copies use today.) The loader is never unloaded, so a change to the loader itself always needs a
restart. A change to the core never does.

Check that both copies can hot reload, without changing anything:

    python -I tools/runner/run.py swap --instances 0,1 --dry-run

Exit 2 and a reason means that copy cannot hot reload (monolith, old loader, no pipe).

## 1. Prepare the save once

A load of the test save raises 14 message boxes over about 50 s. Press them away once and save the result as
`nvcoop_<start save>_ready` on each copy. Do this with the copies at the main menu or in a world, and not during a QA round
(it loads saves on both copies).

    python -I tools/scenarios/prepare_save.py --direct 0,1 goodsprings_enemies

`--direct 0,1` opens the pipes of copies started by `run.py up`, the same way `run_fight.py --instances 0,1` does. Without it the
tool leases instances from the pool (`pool up` first). Any scenario name works, or `--save autosave`.
It prints one JSON line per save with `ok` true or false, and exits 1 if the boxes never go quiet or the reload check still shows one.

Check what is prepared without a game: `python -I tools/scenarios/prepare_save.py --status --direct 0,1 --save autosave`.

After that every normal jump uses it on its own. Look for `"prepared": true` and `loadMessages.pressed 0` in the jump output.
Opt out with `NVCOOP_NO_PREPARED=1` in the environment, or `"start": {"prepared": false}` in a scenario file.
Run `prepare_save.py` again if you replace the base save. A prepared save of a named save is skipped on its own when the source
save changed. A prepared save of `autosave` is not checked, because the game rewrites that file.

The sync waits after a jump now return the moment the condition holds. Read `jump.coopSync.reason`, `settleReason`, `desyncEntries`
and `digestDelta` in a run log to see why a wait ended.

## 2. After a rebuild, swap the core

    plugin\loader\build_split.cmd
    python -I tools/runner/run.py swap --instances 0,1

It checks every copy first and refuses (exit 2) before touching any of them if one cannot reload. Per copy it copies the new core
beside the old one, renames it over, calls `dev.reload`, then checks that the running build id equals the new DLL, the generation went
up, nothing rolled back, and the co-op session runs again (`--settle-s`, default 20). It stops at the first failing copy (exit 1) and
reports the rest as skipped. The JSON report is on stdout, progress on stderr. Exit codes: 0 swapped, 1 failed, 2 refused.

Useful flags: `--dll <path>` for another core build, `--force` to reload an unchanged build, `--no-data` to skip the data files,
`--out FILE` for the report. If a bridge client holds the instance lease, `dev.reload` fails with a lease message: stop that runner first.
A full proof over many cycles is still `python -I -X utf8 tools/runner/reload_proof.py --instance 0 --cycles 10`.

## 3. Run the test, fast

    python -I tools/bots/run_fight.py --instances 0,1 --which fight --timescale 3

`--timescale 3` sends `sgtm 3` to every copy after the setup and `sgtm 1` at the end, on failure, and on Ctrl-C. If a copy refuses,
the run stops before any step. The animation check is tuned for 1x: if only that fails, rerun at 1 or add `--no-anim`.

When a step fails, the summary prints the exact rerun line. Fix round:

    python -I tools/bots/run_fight.py --instances 0,1 --which fight --from-step 4 --timescale 3     # step 4 and later, no scenario reload
    python -I tools/bots/run_fight.py --instances 0,1 --which fight --only-step 4                    # just step 4
    python -I tools/bots/run_fight.py --instances 0,1 --which fight --from-step 4 --reload           # jump to the scenario first

The first step that runs in a partial run arranges the world it expects: it moves players that are far from the first enemy next to
it, equips the weapon, waits until all copies agree on who owns each enemy, and rebuilds the start numbers. A target that is already
dead cannot be arranged: the step fails as kind "arrange" and tells you to reload the scenario. Run the whole test once without
`--from-step` before you trust a pass. Only `enemy_fight` supports `--from-step` and `--only-step` so far; `handoff`, `spawn` and
`loot` exit 2 and name the tests that can.

`Ctx.settle()` gives up early when no copy's `lastSeq` and no host `acked` value has moved for the stall window
(`settle_stall_s`, default `max(6, budget/4)` seconds, 0 turns it off). The reason names every machine's `lastSeq` and the mismatch entries.

## 4. Several tests at once

Each pair of copies is its own co-op session: pair k is instances 2k (host) and 2k+1 (client) on co-op port 27960+k. Pair 0 is the
pair you already run. Add pairs (about 0.7 GB of RAM per copy; `up` refuses when the free RAM is too small, `--no-ram-check` overrides):

    python -I tools/runner/run.py provision --pairs 3 --first-pair 1 --ini coop.enable=1 --ini dev.bots=1 --ini dev.animprobe=1
    python -I tools/runner/run.py up --pairs 3
    python -I tools/bots/run_parallel.py --pairs 3 --dry-run

Then run:

    python -I tools/bots/run_parallel.py --pairs 3                                  # run_fight.py on each pair, all four tests
    python -I tools/bots/run_parallel.py --pairs 3 -- --which fight,handoff         # arguments after -- go to every runner
    python -I tools/bots/run_parallel.py --only 1,2                                 # rerun chosen pairs
    python -I tools/bots/run_parallel.py --pairs 3 --runner tools/bots/run_coop.py -- --playbook goodsprings_opening

You get one table at the end, one row per pair, with the reason of every failure. Results are in `out/parallel/<time>/pair<k>/` and
`parallel_summary.json`. Exit codes: 0 all pass, 1 any FAIL, TIMEOUT or CRASH, 3 BLOCKED, 2 setup problem. Chaos options are refused
in parallel mode. Game-free smoke test: `python -I tools/bots/run_parallel.py --pairs 3 --fake -- --which fight`.

Prepare the save on the extra pairs too: `python -I tools/scenarios/prepare_save.py --direct 2,3 goodsprings_enemies`
(the runner picks the prepared save by the pipe number of each copy, so pair 1 never loads the saves of instances 0 and 1).

Parallel runs add throughput. They do not shorten one round.

## 5. When something fails: look

Read-only, one table with a column per copy. Stop any runner attached to the copies first (a pipe takes one client at a time).

    python -I tools/bots/inspect_live.py --instances 0,1
    python -I tools/bots/inspect_live.py --instances 0,1 --ref FalloutNV.esm:104C70
    python -I tools/bots/inspect_live.py --instances 0,1 --json

It shows role, menu and queued message boxes, weapon drawn and equipped, health, position and cell, the `--ref` block (distance,
health, `coop.owner`), the co-op state (`lastSeq`, acks and lag, digest ok / mismatch / repairs, top desync entries) and recent
boxes, then a plain-words list of problems it spotted. It never presses a box away.

Failed bot actions now explain themselves: the reason reads `code: what the bot saw` (units left of a walk radius and the menu
state, a target too close or too steep to aim at, weapon and ammo state for a fire, menu state and distance for talk and activate).
Ammo before and after needs `bot.ammo_item` set to the ammo form.

## 6. When something fails: get an offline repro

Both copies stay up. This only reads files and takes 1 to 4 s:

    python -I tools/flight/from_failure.py                       # newest failure under out/
    python -I tools/flight/from_failure.py out/fight-XYZ         # every failure.json under that run dir
    python -I tools/flight/from_failure.py host.nvfr client.nvfr # two recordings given directly

It finds the host and client recordings named by `failure.json` (resolved against `../fnv-instances/<instance>`, or
`$FNV_INSTANCES_ROOT`, or `--instances DIR`), replays them through the flight analyzer, prints REPRODUCED or NOT REPRODUCED, shrinks
the recordings, and writes a regression case to `tools/flight/regress/`. `--no-write` gives only the verdict, `--report FILE.html`
the full flight report, `--json` the raw result.

Exit 0 means every failure reproduced, 1 at least one did not, 2 recordings were missing. After a fix, run the fight again and run
`from_failure.py` on the new failure dir: NOT REPRODUCED (exit 1) is the sign the fix worked. A written case pins the recorded
evidence, so it stays green while the old recording still shows the failure; replay all cases with
`python -I tools/flight/regress.py tools/flight/regress`.

## The round, in the order you use it

    plugin\loader\build_split.cmd                                                      # after editing code
    python -I tools/runner/run.py swap --instances 0,1                                 # no restart; time not measured yet
    python -I tools/bots/run_fight.py --instances 0,1 --which fight --from-step 4 --timescale 3
    python -I tools/bots/inspect_live.py --instances 0,1                               # if it failed
    python -I tools/flight/from_failure.py                                             # if it was a sync failure

Run the whole test (no `--from-step`) before you call a fix done.

## Not verified in a game

Everything below is a guess from reading code or from the fake world. Watch these on the first live run.

Hot swap
- The whole in-game reload path has never run. It is proven only with stub and test cores and fake bridges.
- The rejoin time after a reload (the transport closes and reopens) has never been measured. `--settle-s` defaults to 20 and the copy
  fails if the session has not come back. Tune it after the first run.
- The currently running copies have the old loader, which has no `buildId`. Their first swap is verified by reading the CRC of the
  cache copy. They only reload at all if they run the split loader.
- With `[loader] watch=1` the file watcher may reload as well, so the generation can rise by 2. The tool accepts that.
- Reloads go one copy after the other in the order given. Which order rejoins faster (host first or guest first) is still to be measured.

Prepared saves
- The save name `nvcoop_<save>_ready` is accepted by the bridge code, but `nvbridge.save_game` documents names that start with
  `autosave`. If the game refuses, `prepare_save` falls back to `autosave_nvcoop_<save>_ready`; the reload check proves the result.
- Only the `syncing` field of `coop.status` is read as the baseline-active flag. If it stays true for long in a healthy join, the sync
  wait ends with "baseline still running" after 30 s and the steps run anyway.
- The fail-fast limits are guesses: 30 s with no client progress, 20 s with no digest, 3 stale digests, 3 identical mismatches.
  They are parameters of `_wait_coop_synced` and `_wait_digest_ok` in `tools/scenarios/runner.py`.
- Loads are still one copy after the other. There is no bridge call that asks for an immediate digest (`HostSession::requestDigest`
  exists in the core without a `coop.*` method), which would make the digest wait nearly instant.

Steps and speed
- Whether the FNV console accepts `sgtm X` in this form is unverified. So is how the sync core, bot timeouts and animation thresholds
  behave above 1x. Poll loops inside `advance` and `pause` stay at real time, so at high X each poll covers X times 0.1 s of game time.
- The 6 s settle stall window is a guess. A long connection hiccup in a real game could trip it; raise or disable it with
  `settle_stall_s` (a Ctx option, not on the command line).
- `--from-step` exists for `enemy_fight` only.

Parallel pairs
- Nobody has run three real pairs together. CPU, GPU, disk and game-side global state are unknown.
- RAM was tight at the last check: 1.3 GB free with two copies running. Four more copies need about 2.8 GB plus a 2 GB reserve.
- `up --pairs` still starts copies one after another. Nothing checks that a pair's UDP port is free on this PC.
- Two `run_parallel` runs on the same pairs at once are not prevented.

Diagnosis
- The expression `player.GetEquippedObject 5` (the "nothing equipped" diagnosis) is unverified. If the game rejects it, that text is
  left out of the failure.
- The inspector's `ui.status` and `coop.status` reads follow the plugin source, not a live run.
- Ammo count is the reserve; in FNV the loaded clip is not in it, so it can stay unchanged until a reload.
- Playbooks run with `auto_dismiss` on, so `activate`, `talk` and `activate_crosshair` now read the menu first and may press a box
  away before acting.

Offline repro
- The planned real-core replay binary does not exist. Recordings are replayed through the Python analyzer, not fed back through the
  sync core, so a written case cannot turn green after a plugin fix. NOT REPRODUCED on a fresh failure dir is the fix check.
- Failures that are not sync problems (a bad assertion, a bridge exception) can show "no divergence". The live-status cross-check is
  printed next to the verdict so this is visible.
- The analyzer reports stuck-sync on early `txn:1` or `txn:2` in many older runs. Possible false positives; not changed.
