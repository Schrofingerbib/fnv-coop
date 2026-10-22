# prove.py: a script runs the proof tests, models only read verdicts

Private doc (not in the export list). Plan: docs/PROOF-PLAN.md. Code: `tools/proof/`. Nothing here has run against a real game yet; the
whole harness is tested on fake worlds, stub runners and fake process tables (`python -I tools/proof/tests/run_tests.py` prints ALL OK).

## The three commands a cheap model may use

```
python -I tools/proof/prove.py status              # per roadmap step: proven / failing / blocked and the percentage
python -I tools/proof/prove.py queue               # run the whole matrix in order; resumable; proven tests are skipped
python -I tools/proof/prove.py run --test NAME     # N consecutive runs of one test (add --runs 1 for a single run)
```

`queue --runs N` overrides every test's N for that queue (`--runs 1` = a bug-finding pass over everything). A run asked for with `--runs` below the
test's manifest N is written to the ledger with `short: true` and never counts as proven (`status` shows "k short pass(es) ... do not count"); a restarted queue
resumes from the clean runs already on the ledger whatever `--runs` and `--pairs` it is given, and still skips tests that are proven.
Retest mode: `queue --tests a,b,c` (or `--test`) and `queue --from-bugs out/proof/bugs3.json` (the tests of its open rows; they add up) run only those tests. `--round NAME` tags every verdict line with NAME and makes a restarted queue with the same NAME skip the tests that already have a verdict in it (FAIL, CRASH, FLAKY, or a PASS of the last run of the series; ENV and setup BLOCKED lines do not count), so it goes on instead of starting from the top.
A game copy left running with the wrong `nvcoop.ini` or pair role is stopped, re-provisioned and booted again (only that copy). When `out/proof/owned.json` is lost (a killed queue), prove.py takes back the `FalloutNV.exe` that runs from an instance folder and is registered on the slot lease of owner `proof`; another lane's copy and the owner's own game are never adopted or stopped.
`run --step N` runs every test of one roadmap step. `--pairs 2` (on `run` and `queue`) runs two independent two-copy tests at the same time,
one on pair 0 (instances 0,1) and one on pair 1 (instances 2,3, co-op port 27961); see "Two pairs at once" below. `check` validates the manifest. Add `--fake` to any of them for the game-free
self-test on the fake worlds (its own ledger, `out/proof/ledger.fake.jsonl`, which never counts as proof). A model does not edit code,
open raw logs, or launch anything else. It runs one of the commands above, reads the verdict lines, and routes by `class`.

## Reading a verdict

One JSON line per test run on stdout (progress goes to stderr), also appended to `out/proof/ledger.jsonl`:

```json
{"test":"enemy_fight","roadmap":2,"run":3,"of":3,"pair":0,"verdict":"FAIL","class":"SYNC","reason":"step 4: ganger 105D4D health host 40 client 100 after 3 s",
 "check":"04-both kill enemy 1","repro":"REPRODUCED","counters":{"coop.status.desyncs.digests.repairs":2},
 "rerun":"python -I tools/bots/run_fight.py --instances 0,1 --which fight --from-step 4 --timescale 3",
 "packet":"out/proof/20261022-0130/enemy_fight/run3/packet.md","secs":184,"build":"a1b2c3d4e5"}
```

| verdict | meaning | what to do |
|---|---|---|
| PASS | the test passed and was clean (digest repairs 0, area heals 0, no crash) | nothing |
| FAIL | a check failed, or it passed but a must-stay-zero counter moved | route by class below |
| FLAKY | failed, then passed on the automatic rerun | log it; two FLAKY on one test in a week: treat as SYNC |
| CRASH | a game copy exited during the run | Opus, give it the packet |
| TIMEOUT | the runner hit its timeout (class ENV) | requeue once |
| BLOCKED | not run: BUILD = missing bridge method, switch off, pin mismatch; TEST with reason "blocked: ..." = a hand step is not automated yet | BUILD: Sonnet; blocked: harness backlog, not a failure |
| ENV | not run or not the build's fault: owner is playing, Steam off, slot busy, RAM guard, pipe never came up | requeue later, no model |

`class` routes the fix: ENV requeue (no model); BUILD, TEST, and SYNC with `repro` REPRODUCED go to Sonnet with the packet only; SYNC
with NOT REPRODUCED gets two more samples, then Opus; CRASH goes to Opus. `counters` lists only counters that moved and look wrong
(summed over the copies). `rerun` is the exact command that repeats the failing part. `packet` is the first and usually only file a fixer
opens: under 3 KB, with the failure summary, the `inspect_live` snapshot taken before teardown, moved counters, the `from_failure` verdict
and shrunk case, crash info (exit code, faulting module and offset, last bridge calls) and the end of both copies' `nvcoop.log` warnings
(all 40 lines per copy are saved beside it as `warn_inst<N>.txt`). Open raw logs only when the packet says "insufficient".

`pair` is the pair the run used (0 or 1; a test with three or more copies reports 0; null for a game-free test). Verdict lines appear in the order
the tests finish, one whole line each.

## Two pairs at once (`--pairs 2`)

- A two-copy test floats to the first free pair. A one-copy test uses instance 0. Tests with three or four copies, and tests marked `"alone"` in the
  manifest, run alone: nothing starts beside them and the queue does not skip past them. Never more tests at once than pairs. Order is the manifest
  order (`pf_sched.py`).
- One ledger (appends are locked), one queue lock, one game slot lease (re-acquired with as many slots as copies are in use). Boots are one at a time.
- A copy that dies fails only the test running on its own pair (the crash watcher is scoped to the test's instances); stopping copies after a provision
  touches only that test's copies; provisioning pair 1 uses `run.py provision --pairs 2 --first-pair 1`, so a running pair 0 stays untouched.
- RAM guard: every copy that is not running yet needs 0.7 GB, and `--reserve-gb` (default 1.5) must stay free. Four copies are allowed when free RAM is at
  least 4 x 0.7 + reserve; the copies of the tests already running count. When two pairs do not fit, `--pairs 2` quietly runs one at a time.
- `--pairs` is at most 2 (the owner's PC).

## What "proven" means

A test is proven when its last N runs (N in `tools/proof/manifest.json`, 1 or 3) are all PASS and clean, on the current build (a hash of
the built DLLs), and at least one of them ran on copies prove.py booted itself and nobody hot swapped. `status` shows the percentage per
step. ENV verdicts and fake runs do not count either way. `queue` stops a test's sequence at the first non-PASS, and a later queue starts
that test again from run 1.

## Safety on the owner's PC

- Refuses (ENV) when any `FalloutNV.exe` runs from outside the `fnv-instances*` folders next to the repo (the owner is playing), when Steam
  is not running or not signed in, or when the RAM guard fails. The queue stops on the owner playing, Steam signed out and a busy game slot. A RAM guard refusal of one test (it needs more copies than fit) is recorded as ENV, the queue moves on, and that test gets one more try at the end.
- Signed in = the registry `ActiveProcess` names the running steam.exe with a non zero `ActiveUser` (stale, or 0 right after a Steam restart),
  OR `config/loginusers.vdf` holds a login newer than the steam.exe process start. A booted copy whose top window is titled "Steam Error"
  ("Application load error 5:0000065434") fails at once as ENV ("Steam is not signed in ...") instead of waiting 300 s for the pipe.
- Every copy prove.py boots gets `NVCOOP_TEST_HELPERS=1` (the manifest's per test `game_env` adds to it). owned.json records the env of each
  copy; a running copy of ours that lacks what the next test needs is stopped and booted again. Copies the owner started are never touched.
- The game slot's RAM check (`gameslot.py acquire --need-gb`) gets prove's own number: copies still to boot x 0.7 GB + `--reserve-gb`
  (replaces the 3 GB default). The measure is Available MBytes (standby counts).
- One queue runner at a time (`out/proof/queue.lock`; a dead runner's lock is taken over).
- The game slot is taken with `tools/gameslot.py` (owner `proof`) before any copy is booted.
- It only ever stops processes it started itself (`out/proof/owned.json`); asking it to stop any other pid raises an error.
  At the end of `run` and `queue` it stops its own copies and releases the slot, unless `--keep-up` is given.

## Files

| file | job |
|---|---|
| `manifest.json` | every test: roadmap step, commands, copies, ini switches, N, timeout, counters to snapshot, `blocked_reason` |
| `params.local.json` (optional, yours) | values for params that are `null` in the manifest (weapon editor ids for VATS, faction ids) |
| `prove.py` | run / queue / status / check |
| `pf_classify.py` | evidence to verdict and class (pure) |
| `pf_packet.py` | the failure packet and its size limit |
| `pf_ledger.py` | ledger file and the proven rule |
| `pf_safety.py` | owner-PC guard, RAM rule, queue lock, owned pids |
| `pf_sched.py` | which tests may run at the same time (`--pairs`), how a pair is provisioned |
| `drivers/` | the scripted pieces of step 10 (below) and their tests; `load_scenario.py` loads the Goodsprings gang scenario on a booted copy before a single-copy test that reads the NPC (`needs_npc` in the manifest) |
| `pf_crash.py`, `pf_trace.py` | crash catcher and the last-bridge-call ring |
| `tests/run_tests.py` | unit tests plus mutation twins, prints ALL OK |

## Scripted pieces of step 10 (`tools/proof/drivers/`, tests: `python -I tools/proof/drivers/tests/run_tests.py`)

| test | driver | what it does |
|---|---|---|
| `release_cmd_scripts` | `release_cmd_scripts.py` | packages a synthetic release, unzips it, and runs its `Check.cmd`, `Install.cmd` and `Uninstall.cmd` with cmd.exe on a fake game folder; after Uninstall the folder is byte for byte as before. Also an upgrade that keeps the player's `nvcoop.ini`, a wrong game version, no xNVSE, a damaged payload, and no Python on PATH. An isolation shim keeps the scripts off this PC's real install and processes |
| `launcher_loopback` | `launcher_loopback.py` | the launcher's Host, port check, test listener, Test connection and Join on 127.0.0.1 with two fake game folders, then again through the seeded chaos relay (bad_wifi, seed 7) with a one-way partition |
| `public_tree_build` | `public_tree_build.py` | `export_public.py` into a temp folder outside the checkout (SCAN CLEAN, then a second scan plus no `.git`, `out/` or binaries), `test_all.py --quick` inside the export, then `plugin/all` built there (xNVSE sources from `third_party/NVSE` or `--nvse`, exit 3 BLOCKED if absent). Never pushes. Slow and CPU heavy |
| `splitscreen_smoke` | `splitscreen_smoke.py` | game slot, `launch.py up --dev-inject --autoload autosave --pads none,0`, `smoke_playbook.py --movement`, then `launch.py down` in a finally. Manifest `alone` + `launches: 2`: prove.py stops its own copies first, runs nothing beside it, and counts the two copies the driver starts in the owner-PC guard and the RAM rule |
| `enemy_chaos_bad_wifi` | `tools/chaos/chaos_pair.py` | starts the seeded relay (docs/tools/T5-chaos.md) in front of the host's real port, makes the client `coop.leave` and `coop.join` through it, runs the fight with `--chaos bad_wifi --chaos-seed 7`, and always puts the client back on the host's port |

Drivers print `PASS name` / `FAIL name: why` lines and exit 0, 1, 2 (PC not ready: the line starts "cannot reach the game instances", class ENV) or 3 (an input is missing:
BLOCKED). `avatars_lifecycle` now has defaults in the manifest params: interior `GSDocMitchellHouse`, exterior `Goodsprings` (the `coc` targets the repo already uses) and
save `autosave` (the save every scenario in `scenarios/` starts from); `params.local.json` still overrides them. The ledger state for a test that never ran is `not_run`.

Switches (`--provision`, on by default, re-provisions `nvcoop.ini` for a test when it lacks its switches, `--no-provision` turns that off; `prove.py check` flags a game test whose `ini` lacks `coop.enable=1` (2+ copies) or `coop.enable=0` (1 copy), `--no-boot` never launches copies,
`--no-rerun`) are listed by `prove.py run --help`.

## One build, saves and readiness on every copy (2026-10-22 sweep fixes)

- **Build**: before a test boots or reuses copies, `prove.py` compares each copy's `nvcoop.dll` (and `nvcoop_mod.dll` with the `[loader] core` line when
  the split build is installed) with the build provisioning installs (`out/L1split`, else `out/L1`). A mismatch re-provisions those copies; if it still
  differs the test is BLOCKED/BUILD with "build mismatch: instance N: ...", never run as a test (`pf_sched.build_problem`).
- **Saves**: copies made by `run.py provision` have an empty Saves folder, so a scenario jump timed out loading `autosave`. `prove.py` now fills every
  missing file (start save, prepared `nvcoop_<save>_ready.*`, `nvcoop_prepared.json`) from a donor copy of the same role, last resort the owner's
  `autosave.fos` read only (`pf_saves.py`). It never overwrites.
- **Readiness**: a pipe that is up is not a game that serves requests. `tools/runner/nvready.py` waits for `ping` menuReady and a ticking main loop;
  prove.py waits after booting (ENV when the game never gets ready), and avatars, social, loot, world time, run_fight and load_scenario call the same helper.
- Scripts: `loot_proof` waits for the guests to join before its session check; `worldtime_proof` waits for the confirm box (`bot.menu` kind `message`)
  before `bot.menu.press`.
