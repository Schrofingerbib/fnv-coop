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
