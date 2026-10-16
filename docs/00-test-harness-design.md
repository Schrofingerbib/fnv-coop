# FNV Co-op - Test Harness Design (draft v0)

Goal: lots of agents work in parallel, and **nothing counts as done unless a machine proves it**.
No "I played it and it seemed fine". Every claim = a passing test with logs/state diffs as evidence.

## Core idea: the game is the test runner

### 1. `nvbridge` - "Playwright for New Vegas"
An xNVSE plugin (ours) that exposes the running game over a local JSON-RPC socket / named pipe:

| Call | Does |
|---|---|
| `exec(cmd)` | run any console command (`coc`, `setstage`, `player.moveto`, `additem`...) |
| `state()` | canonical world-state snapshot (see §2) as JSON |
| `waitFor(expr, timeout)` | block until e.g. `GetStage VMS01 >= 20` |
| `talk(npc, choices[])` | start dialogue and pick topics by FormID |
| `events(since)` | stream of hooked engine events: SetStage, objective change, AddItem, death, script exec |
| `screenshot()` | last-resort visual evidence |
| `loadSave(name)` / `quit()` | lifecycle |

Agents write tests like web E2E tests: `host.exec("setstage VMS01 10"); client.waitFor(...)`.
Auto-boots to a test save (skips main menu), windowed, low-res, frame-capped - so 2–3 instances fit on one PC.

### 2. Canonical world-state snapshot = universal oracle
One deterministic, sorted JSON per instance:
- every quest: stage, completed/failed flags, displayed+completed objectives, quest vars
- all globals, faction reputation (REPU), karma, faction ranks
- quest-relevant refs: alive/dead, enabled/disabled, cell, owner of quest items
- player-specific section (inventory, AVs, perks) kept separate

**Invariant: after any action + settle time, host.shared == every client.shared.**
This one check catches most sync bugs with zero per-quest knowledge. Diff output = exact field that diverged.

### 3. Single-player differential testing (the big one)
Singleplayer is the ground truth.
1. Run scenario S in **SP** → record state trajectory T_sp.
2. Run the same S in **co-op**, with actions split between host and client(s) → record T_coop.
3. Shared state must match T_sp at every checkpoint (and finally).
If co-op diverges from what vanilla would do, it's a bug - we never have to hand-write "expected" values.

### 4. Auto-generated per-quest tests from the ESM
Parse FalloutNV.esm + DLCs → every quest, stage, objective, result script, condition (see 04-quest-data.md).
- **Stage-walk test**: for each quest, drive the host through its stage graph via console; assert clients mirror every stage, objective, item, NPC state.
- **Risk classifier**: static analysis of result/dialogue scripts flags player-referencing / control-locking / item-moving
  calls → those quests get priority + hand-written scenario tests.
- Coverage dashboard: % of quests whose stage-walk passes in co-op.

### 5. Invariant monitors (always on, in every run)
- **No double-fire**: any quest/stage result script executing on a non-host = failure.
- **No duplicate rewards**: AddItem/caps/XP from quest scripts happen exactly once per intended recipient.
- **No orphan NPCs**: quest NPC exists exactly once (no ghost duplicates) unless explicitly per-player.
- Crash/hang watchdog: collect crash dumps + NVSE/NVAC/plugin logs; auto-attach to the failing test.

### 6. Record / replay - fast tests without the game
The plugin can log every hooked engine event + every network message. Recorded traces become
offline fixtures: the sync logic (state machine, conflict resolution, serialization) runs against them
in milliseconds as plain unit tests. Agents iterate here first; only promote to the game-in-loop
tier when green. This is what makes "a shit ton of agents" scale - the game-in-loop tier is the
scarce resource (one GPU), the replay tier is unlimited.

### 7. Network chaos
Transport shim with injected latency / loss / reorder / disconnect-rejoin, deterministic seeds.
Every e2e scenario also runs under "bad wifi" and "client rejoins mid-quest" profiles.

## Test tiers
| Tier | Runs | Speed | Who |
|---|---|---|---|
| T0 unit + replay | offline, no game | ms | every agent, every change |
| T1 single instance | 1 game + nvbridge | ~1 min | engine hooks, state snapshot correctness |
| T2 two-client e2e | host + client(s) on one PC | minutes | sync scenarios, chaos profiles |
| T3 differential + stage-walk | SP vs co-op, all quests | hours, nightly | coverage dashboard |

A scheduler queues T1–T3 jobs (only N game instances at once); agents submit and get back pass/fail + evidence.

## Definition of done for any feature
1. T0 tests added and green. 2. T2 scenario added and green incl. chaos profile.
3. No invariant monitor fired. 4. Stage-walk coverage didn't regress.

## Open questions (research agents)
- multi-instance on one PC: single-instance mutex, shared `My Games\FalloutNV`, Steam DRM → 03-testing.md
- skip-main-menu autoload + headless-ish rendering → 03-testing.md
- engine addresses for SetStage/objectives/dialogue/script exec hooks → 02-engine-re.md
- build on NV:MP vs own netcode → 05-nvmp-intel.md
