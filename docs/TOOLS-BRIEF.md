# Tooling sprint brief (2026-10-20): read fully before starting

> **RESUME (round 2):** agents were cut off by a usage limit, some MID-EDIT. Several left C++/Python with a
> broken `\n` escape from bash heredocs. FIRST: read your docs/tools/<agent>.md status, run your own suite, and
> fix anything half-written, using the Edit tool and never heredocs. **Done already:** T1b (except the
> bucket→key narrowing), T4a, T4b, T6b, T7. Nothing is committed since 4d91568; the lead commits once
> `python -I tools/test_all.py` is fully green. Still CODE ONLY: no game launches.

We are building the test and dev tooling that makes the full-sync build (docs/FULL-SYNC-PLAN.md) fast.
Read first: docs/FULL-SYNC-PLAN.md (sections 1, 4, 6), docs/CONTRACTS.md, docs/protocol.md, ../automatedqa.md,
docs/RESUME.md.

## Hard rules
- **CODE ONLY.** Do not launch FalloutNV.exe, NV:MP or any game, and do not acquire the gameslot. Write in-game proof
  scripts if useful, but do not run them; the lead runs them later, one game at a time.
- **RAM is tight on this PC:** builds use `set CMAKE_BUILD_PARALLEL_LEVEL=2`. Never run more than one C++ build of
  your own at a time.
- **Write only in your own directory** (below), plus your status file docs/tools/<agent>.md. If you must touch
  shared files (a CMakeLists.txt subdir line, plugin/main.cpp, a synccore header), keep the edit minimal, keep
  it building, and tell the owning agent or the lead by SendMessage.
- **Write C++ and multi-line strings with the Write/Edit tools,** never bash heredocs.
- **Every tool ships with tests plus a mutation twin,** and one command to run them that prints `ALL OK`. When
  done, add it to tools/test_all.py.
- **Keep `cmd /c tools\runner\build_l1.cmd` and `python -I tools/test_all.py --quick` green.**
- **No git commits;** the lead commits.
- **Scratch files** go in your own subfolder (out/<agent>/tmp).
- **Use plain language in docs.** Say "unknown" rather than guessing.

## The four tools and the eight agents

### T1 Flight recorder + time machine
- **T1a-recorder** owns `synccore/flight/` and `plugin/flight/`. It defines a compact binary trace format,
  versioned, and records on every machine:
  - every network message in and out (with channel, seq and epoch);
  - every captured and applied effect txn;
  - every ownership grant, transfer and release (for the future actor layer: define the record type now);
  - periodic state digests per category (story, actors, containers, objects), with a monotonic clock and a
    frame number.
  Ring buffer in memory; flush to `<instance>/nvcoop_flight/*.nvfr` on desync, crash, or a bridge command
  `flight.dump`. Recording overhead must stay well under 0.1 ms per frame at idle; measure it in a benchmark.
  Hook the recorder into synccore's session/transport and plugin/coop's runtime through small observer
  interfaces (coordinate with T2a for the sim side).
- **T1b-analyzer** owns `tools/flight/` (Python). It provides:
  - (1) a reader and pretty-printer for the trace format (agree on the format with T1a early; write the spec in
    docs/tools/flight-format.md);
  - (2) **align**: line up the traces of 2 to 4 machines by txn ids and seq numbers;
  - (3) **first-divergence finder**: the earliest digest or key where the machines differ, plus the causal
    chain of messages that led to it;
  - (4) **auto-shrink** (delta debugging): reduce a failing message sequence to the smallest one that still
    diverges when replayed through synccore's simulator (coordinate a replay entry point with T2b);
  - (5) **auto-test generation**: emit a ready-to-run regression test (a C++ ctest case or JSON fixture plus
    runner) from the shrunk trace;
  - (6) an HTML report.

### T2 Fake game engine (game-free co-op simulation)
- **T2a-engine** owns `synccore/enginemodel/`. It is a deterministic in-memory model of the New Vegas world parts
  we sync:
  - actors (position, cell, health and AVs, dead, AI on/off, leveled pick), players and puppets;
  - cells and loading by proximity;
  - containers and world items (drops and pickups);
  - doors, locks and object states;
  - spawn sites and encounters;
  - simple combat (hits with attacker, victim, damage, limb, impact point);
  - quest stages through the existing effect path.
  Plus the **actor ownership service** from FULL-SYNC-PLAN 3.2: epochs, grant/transfer/release, hysteresis, the
  follower exception, and stale and echo rejection. That service is real product code (in synccore), so design
  it to be reused by the plugin later. Expose seeds, a virtual clock and digests.
- **T2b-fuzz** owns `synccore/fuzz/` and `tools/simfuzz/`. It builds:
  - a scenario generator and property-based fuzzer that runs thousands of 2 to 4 player sessions over the
    existing lossy-link simulator and T2a's model: random moves, fights, loot races, spawns, joins/leaves,
    ownership churn, host save/load, stale-echo injection (the Skyrim Together PR #906 bug);
  - invariant oracles: all machines converge; nothing is applied twice; each actor has one owner per epoch;
    no duplicate actors or items; nothing stale applied; repairs per minute equal 0 after quiescence;
  - on failure, a T1a-format trace (coordinate with T1a/T1b) and a minimized seed;
  - mutation twins that inject each known bug class and must be caught;
  - a CLI: `python -I tools/simfuzz/run.py --minutes 2 --players 4`.

### T3 Hot-reload of our plugin in a running game
- **T3a-loader** owns `plugin/loader/` plus a minimal, coordinated edit to `plugin/CMakeLists.txt` and
  `plugin/main.cpp`. It splits nvcoop into:
  - a tiny permanent **loader** (the xNVSE plugin, stays loaded: NVSE registration, the bridge pipe, a stable
    C ABI);
  - a reloadable **core DLL** (everything else). The loader copies the core to a temp name before LoadLibrary,
    so the build can overwrite it.
  Bridge method `dev.reload`, plus a file watcher (opt-in via ini). Unload is only allowed between frames.
- **T3b-state** owns the reload contract inside the modules:
  - a registry `onBeforeUnload` / `onAfterReload` with versioned state blobs;
  - every engine hook must uninstall cleanly and restore the original bytes; check it in L5/L11/L12's hook
    helpers, with minimal coordinated edits;
  - threads stopped, sockets handed over or reopened, the session survives (synccore state serialized and
    restored).
  Tests: a game-free reload test in a host process that loads, unloads and reloads the core 100 times with
  state preserved and no leaked hooks or threads.
  Write `tools/runner/reload_proof.py` for the lead to run in-game later.

### T4 Warm game pool + instant scenarios + low-cost mode
- **T4a-pool** owns `tools/pool/`. It is a pool daemon (Python) that:
  - keeps N (default 1, max 2) game instances warm and minimized;
  - leases them to tests through the existing gameslot (register-pid, kill only its own);
  - health-checks over the bridge and restarts dead ones;
  - recycles an instance after K tests or on memory growth.
  **Low-cost profile:** instance-local INIs at the smallest practical resolution, lowest settings, no
  shadows/grass/water reflections, an fps cap, small texture memory and a reduced uGridsToLoad if safe.
  Document every key, plus a RAM and CPU estimate (unknown until measured; leave a measurement script for the
  lead). A CLI: `pool up|status|lease|release|down`.
- **T4b-scenarios** owns `tools/scenarios/` and `scenarios/*.json`. It is a scenario format plus runner: the
  starting save, the quest states to reset (using L7's test.quest.reset / setvar helpers), player positions,
  spawns, items and expected checks.
  - **"Jump to scenario" in seconds on a warm instance:** reset in place, no save reload unless required (same
    rule as L7's reload-every mode).
  - Ship 10 starter scenarios for the full-sync tests: the Goodsprings fight (2 players, 3 raiders), a loot race
    (both grab the same item), a spawn test (an encounter appears once), an ownership handoff (host walks away
    mid-fight), a door/terminal/container matrix, a companion recruit, a quest stage sync, a save/load rejoin, a
    VATS hit, and a fast-travel tether.
  - Validate scenario files game-free (IDs resolve against out/questdata), with tests and a twin.

### T5 Network chaos box
- **T5-chaos** owns `tools/chaos/` (and, if C++ is needed, `synccore/chaos/`). It is a UDP relay that sits
  between game instances or sim peers on localhost (ENet runs over UDP; our port is 27971). Configurable live:
  latency plus jitter, loss, burst loss, reorder, duplicate, bandwidth cap, a one-way partition, and a timed
  disconnect/reconnect.
  - Profiles: `lan`, `good_wifi`, `bad_wifi`, `mobile_tether`, `transatlantic`, `hell`. Each profile has a
    seed for repeatability.
  - Driven by CLI and JSON-RPC, so tests can change conditions mid-run.
  - Logs a per-direction packet log that T1b's analyzer can read.
  - Also exposes the same impairment model as a library for synccore's simulator, so game-free and in-game tests
    share profiles.
  - Tests: real UDP round trips on loopback with statistical checks per profile (loss and latency within
    tolerance), plus a twin.
  - Ports: OS-assigned for tests.

### T6 Co-op bots (scripted players)
- **T6a-actions** owns `tools/bots/` (the action layer). It is a Python bot API over nvbridge, with:
  - **movement:** go to ref, marker or coordinates; follow; wait;
  - **combat:** equip, aim at a ref, fire, reload, VATS target;
  - **interaction:** activate, open or close a door/container/terminal, take or put items, pick up, drop, talk
    to an NPC, pick a dialogue topic by INFO id;
  - **queries:** health, position, cell, inventory, quest stage, target alive.
  The bot API must not send OS keyboard or mouse input (CONTRACTS). Where xNVSE or the console lacks a primitive,
  specify the bridge method needed in docs/tools/T6a-actions.md (and implement it as a dev-only bridge method in
  `plugin/bots/` if feasible, behind ini `[dev] bots=1`). Every action has a timeout, a postcondition check and
  a clear failure reason. Game-free tests use a fake bridge (the same idea as the existing fakehost).
- **T6b-playbooks** owns `tools/bots/playbooks/` and `tools/bots/run_coop.py`. These are multi-bot co-op
  playbooks that drive 2 to 4 bots at once, each on its own instance pipe:
  - fight (both shoot the same raiders);
  - loot race;
  - container share;
  - door, terminal and lock;
  - companion recruit;
  - dialogue with spectator;
  - point-of-no-return vote;
  - quest step sync;
  - fast-travel tether;
  - an ownership handoff walk-away;
  - a Goodsprings opening playthrough (VCG01 to VCG02 to VCG03 critical path) as the first full-questline bot run.
  After each step it compares host and client snapshots (snapdiff) and the desync counters. On failure it saves
  traces for T1b. Uses T4b's scenarios to start from known states, and T5's chaos profiles optionally.
  Playbooks are validated game-free against a fake bridge. Pair with T6a on the API first.

### T7 Animation and pose checker
- **T7-anim** owns `plugin/animprobe/` (dev-only bridge methods, behind ini `[dev] animprobe=1`) and
  `tools/animcheck/`. It measures animation sync objectively, never by eye.
  - **(1) Probe.** A bridge method returns, per actor (refs or all actors near a point): the current animation
    group and sequence plus its time; locomotion (speed, direction); weapon state (drawn, equipped weapon,
    aiming, reloading); stance (sneak, swimming, knocked down, ragdoll); AI procedure and combat target; facing;
    and **skeleton bone world positions** for about 15 key bones (head, spine, hands, feet, weapon node), from
    the actor's 3D NiNode tree. Read only, on the main thread, with a measured cost.
  - **(2) Compare.** Given two or more machines' probe streams (time-aligned with the latency allowance from
    FULL-SYNC-PLAN section 2, a 100 ms interpolation buffer), score:
    - the pose distance (mean bone error in game units after aligning the root);
    - mismatched anim groups;
    - wrong weapon, wrong facing.
  - **(3) Glitch detectors** on a single machine's stream, plus thresholds:
    - **sliding:** moving without a locomotion anim, or a speed vs anim-speed mismatch;
    - **T-pose:** a bind pose or no sequence running;
    - **floating or sinking:** z against the ground or navmesh height if obtainable, otherwise against the
      last grounded z;
    - **jitter:** high-frequency position oscillation;
    - **rubber-banding:** jumps over N units;
    - **stuck:** the same idle while it should move;
    - **wrong death pose:** alive anim on a dead actor.
  - **(4) Output.** A report plus counters the dashboard and T6b's playbooks can assert on (e.g. pose error p95
    under X, glitches == 0). Traces go into T1's format if possible.
  - Game-free tests use synthetic probe streams with injected glitches (each detector has a twin). Verify bone
    and anim-state engine offsets statically only (<reference exe>, read-only).

## Pairing
T1a with T1b, T2a with T2b, T3a with T3b, T4a with T4b: agree on interfaces with your partner first, by
SendMessage, before writing code. Cross-pair: T1 with T2 (trace format, replay entry point), T4b with L7's sweep
helpers (read only).

## Build speed (2026-10-20): one shared build tree, runtime mutation twins, affected-only tests

Why: about 13 build dirs (`build/*`, `out/*/build*`, `%TEMP%\nv*_build`) each recompiled the same code, and every mutation twin
was its own compile (`-DNVWORLD_MUT=28` ...). `plugin/hooks_world` alone compiled its sources 43 times (42 twins plus the real
one, each a core library plus a test exe). A cold build of that one suite took 869 s on this machine (measured, 2-job Ninja,
shared with other agents), and a full `test_all` took 10+ minutes.

### What exists now
| Piece | Where | What it does |
|---|---|---|
| Shared tree | root `CMakeLists.txt`, tree `build/all` | `add_subdirectory(plugin)` (nvcoop.dll and all plugin lanes, artifacts still in `out/L1`, `out/L10`, `out/T3b` ...) plus `add_subdirectory(synccore)` (core, sim, enginemodel, flight, chaos, live) plus `synccore/fuzz` (EXCLUDE_FROM_ALL, FUZZ_EM=ON). Win32/x86 like the game. |
| Build driver | `tools/build/build.py` | Sets up MSVC (vcvars), takes the lock `out/locks/build.lock` (mkdir, retries every 15 s, released on success, failure and Ctrl-C, writes `owner.txt` inside), configures when needed, builds `--target ...` (or everything), prints `BUILD-TIME`. Uses `sccache` as compiler launcher when it is on PATH. `-k` reports all compile errors, `--configure-only` needs no lock. |
| Runtime twins | `tools/mut/fnvcoop_mut.h` | `FNV_MUT(n)` replaces `#if X_MUT == n`. Test code is compiled ONCE with `FNVCOOP_RUNTIME_MUT`; the twin is chosen with env `FNVCOOP_MUT=<n>` (or `--mut n` if the test main calls `fnvcoop_mut::initFromArgs`). In production builds `FNV_MUT(n)` is the constant `false`: nothing in a shipped DLL reads the environment. |
| Twin runner | `tools/mut/run_twins.py` | Runs the exe once for real (must exit 0, optional output regex), then once per twin with `FNVCOOP_MUT=n` (must be caught: non-zero exit, optionally also a `N failed` line; missing-DLL loader errors never count as caught). Prints `ALL OK`. |
| Converter | `tools/mut/convert_mut.py` | One-shot helper that rewrites `#if M == n ... #else ... #endif` blocks into `if (FNV_MUT(n)) { ... } else { ... }`. Declarations, `case` labels and class members need a hand edit (the compiler points at them). |
| Precompiled headers | `tools/build/pch.cmake` (`fnvcoop_pch(target)`) | Heavy std headers for targets with 3+ sources (synccore, coop synccore, hooks core, bots tests). Off automatically with sccache or `-DFNVCOOP_PCH=OFF`. |
| Affected-only tests | `python -I tools/test_all.py --affected [--list] [--base REF]` | `git diff --name-only <base>` plus untracked files, mapped to suites. A suite owns the directories named in its command line (so a new `SUITES` entry needs no registration); suites that compile C++ also depend on the shared sources (`synccore/src`, `synccore/include`, `plugin/common`, `plugin/hooks`, `tools/mut`, `tools/build`, the CMake files, `third_party`). `docs/` never triggers a run; an unknown path under an unknown root runs everything. `--only SUBSTR` runs by name. |
| Self-test | `python -I tools/mut/run_tests.py` (suite "build-speed tooling" in test_all) | C++ mechanism test with 3 twins, production build ignores `FNVCOOP_MUT`, runner self-test (survivor, broken real run, crash is not a caught twin), lock test (+ twin), selector test (+ twin). |

### Converted suites (compile count per suite, before -> after)
| Suite | Twins | Old compiles for twins | Now |
|---|---|---|---|
| L12 hooks_world | 42 | 42 core libs x 2 sources + 42 test exes (93 compile units + links) | 2 core libs (prod + `_rt`) + 1 test |
| T6a bots | 32 | 33 test exes (each: test + hook_patch) | 1 test exe |
| L11 hooks_ui | 9 + 7 | 10 core libs + 10 tests + 8 net libs + 8 bridge tests | `nvui_core`, `nvui_core_rt`, `nvui_net`, 2 tests |
| T7 animprobe | 6 | 7 exes | 1 exe |
| T3b reload | 8 + 7 + 9 | 9 + 8 unit/drain exes + 10 test-core DLLs | 2 exes + 1 DLL |
| L10 coop | 5 + 2 + 4 peers | 6 core libs + 7 exes | `nvcoop_coop_core`, `_rt`, `coop_tests`, `coop_peer` |
| T1a plugin flight | 3 | 4 exes | 1 exe |

The old per-suite build dirs are NOT deleted by this change (list in the handoff). Scripts that used them now build in `build/all`.

### Measured (this machine, 2 build jobs, other agents compiling at the same time, so absolute numbers are noisy)
| What | Before | After |
|---|---|---|
| L12 hooks_world, cold build from nothing (same sources, same flags) | 868.8 s, 224 ninja steps, 1.3 GB build dir, x64 tree in `%TEMP%\nvw_build` | 90.3 s, 16 steps, 105 MB, x86 shared tree (about 9.6x faster) |
| L12 hooks_world, the 42 twin runs | 42 separate exes | 8.6 s for real + 42 twins in one `run_twins.py` call |
| T6a bots twin runs (32) | 33 exes | 5.2 s for real + 32 twins |
| Per-twin results of hooks_world (failed-check counts of all 42 twins, old exes vs new runtime twins) | recorded from the old exes | identical, twin by twin (`1 751 checks, 3 failed`, `2 ... 1 failed`, ...) |
| nvcoop.dll + all plugin targets (`build_l1.cmd`), cold, shared tree | n/a (old tree: 5.6 GB) | 180.6 s, 106 steps |
| synccore (`run_tests.bat` build part), cold, shared tree | n/a | 158.8 s |
| A suite whose targets are already built | took the lock and ran ninja (queued behind other agents' compiles) | `build.py` dry-runs ninja first and skips the lock when there is nothing to compile |

Not measured: the full `test_all` (it was not run: other agents are editing at the same time, and the lead runs the gate). Everything above
was run on the suites that were converted: hooks_world, bots, hooks_ui, animprobe, coop (+ 2-process integration), flight (synccore + plugin),
the mut self-test, `build_l1.cmd`. All printed ALL OK except one item that is not caused by this change: `unit_perf` in synccore failed its p99 budget
once while the machine was saturated (23 s run).

### How to add or convert a suite
1. Put `#include "fnvcoop_mut.h"` in the code under test (add `tools/mut` to its include path) and write `if (FNV_MUT(7)) { broken }`.
2. Compile the code under test twice only if production also links it: a production lib (no define) and an `_rt` lib / test exe with `FNVCOOP_RUNTIME_MUT`. Never link a production and an `_rt` copy of inline code into one binary. Components linked into the same test binary must use different twin numbers (the flight test links the production coop core for this reason).
3. In CMake add ONE test exe; register `add_test(NAME x_mutN COMMAND exe)` with `set_tests_properties(... WILL_FAIL TRUE ENVIRONMENT "FNVCOOP_MUT=N")`.
4. `run_tests.cmd`: `python -I tools\build\build.py --target <exe>` then `python -I tools\mut\run_twins.py --twins 1-N --real-regex ", 0 failed" -- <exe>`.
5. Plugin lanes are picked up by the `foreach(sub ...)` in `plugin/CMakeLists.txt`; synccore parts by `add_subdirectory` in `synccore/CMakeLists.txt`. Use `EXCLUDE_FROM_ALL` for test-only targets (the `plugin/all` build of `build_l1.cmd` then stays small).

### Rules for everyone building
- Always go through `tools/build/build.py` (or a `run_tests` that does): it holds the lock only while cmake runs.
- Do not start a second ninja in `build/all` by hand while someone else builds; the lock exists for that.
- sccache: not installed here. `scoop install sccache` (not done automatically) is picked up by `build.py` on the next run.
- `synccore/run_tests.bat` runs ctest with `-j 2` (the timing-sensitive tests `unit_perf`, `soak_*`, `loopback_*` are `RUN_SERIAL`). The other agents'
  suites that still configure their own tree (`synccore/live/run_tests.cmd` -> `build/live-core`, `plugin/actors` -> `build/actors`,
  `plugin/avatar` -> `%TEMP%\nvav_build`, `synccore/fuzz` -> `build/T2b-fuzz*`, `plugin/loader` -> `build/T3a`) can move to the shared tree by
  replacing their cmake/lock lines with `python -I tools\build\build.py --target <targets>`; their targets are already part of `build/all`
  (actors and avatar are in the `foreach` of `plugin/CMakeLists.txt`, live is added by `synccore/CMakeLists.txt`, fuzz by the root file).
- Twins that are still compile-time: L5 hooks (`NVHOOKS_MUTANT`, 8 files), the bridge (`NVBRIDGE_MUTANT*`, `NVPNG_MUTANT`), the chaos mirror (`CHAOS_MUTANT`, the header is generated by `tools/chaos/gen_header.py`), nvmp (`SIGSCAN_MUTANT_*`). They are one to four compiles each. Run-time since the merge of the helper's PRs: bots (twins 1..47), the loader (21..32, also used by the reload host test), splitscreen (1..7, suite in the shared tree).
