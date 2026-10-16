# 03 - Automated testing of a multiplayer quest-sync mod for Fallout: New Vegas

Scope: how to get automated, trustworthy tests for an xNVSE co-op plugin, without a human playing.

Evidence note: web search in this session was weak. It returned nothing useful on the NV:MP repos, Skyrim Together test setups or Bethesda GDC talks. Each claim below is tagged:
- [V] verified in this session
- [K] from prior knowledge, high confidence
- [U] unverified or speculative, and must be spiked before anyone relies on it

Spikes are listed in section 9.

## 1. Running 2+ FNV instances on one Windows PC

Known facts and tricks:
- **Duplicate-instance check.** [V] Users get "There is already an instance of Fallout New Vegas running" when stale `FalloutNV.exe` or `FalloutNV 4gb` processes exist (stepmodifications.org thread). [K] This is a named-mutex or window-class check inside the exe or launcher. Gamebryo-era Bethesda titles also commonly refuse a second copy.
  - Workaround 1: hook `CreateMutexA/W` and `FindWindow` from the plugin itself, or from a tiny proxy DLL (`dinput8.dll`/`d3d9.dll` proxy, or an early-loaded NVSE plugin). Make the mutex name unique per instance, or make the call return success.
  - Workaround 2: binary-patch the check in a copy of the exe. This is not preferred, because the 4GB patcher already modifies the exe.
  - Workaround 3: run each instance under a separate Windows user, a Sandboxie-Plus box, or a VM. Mutex namespaces are per-session or per-sandbox. [U] A 2011 Nexus thread said the user-account trick did not work for GECK [V], but that was a different mechanism.
- **GOG build.** [K] GOG FNV has no DRM wrapper. It is a DRM-free exe, and GOG Galaxy is not required to launch. This is the best case for multi-instancing, since Steam's single-account-session limit does not apply. Run `FalloutNV.exe` via `nvse_loader.exe` directly.
- **Separate INI and save folders.** [K]
  - FNV reads `Documents\My Games\FalloutNV\` (Fallout.ini, FalloutPrefs.ini, Saves, plugins.txt/loadorder).
  - Each instance needs its own copy to avoid save-file and INI write races, and for different resolution and windowed settings.
  - Options:
    - (a) separate Windows users (cleanest, but heavy);
    - (b) a portable-install mod, which sets `bUseMyGamesDirectory=0` in Fallout.ini so the game uses the install dir. This requires one game directory copy per instance, or a symlinked or hardlinked Data dir with per-instance INI and Saves;
    - (c) Mod Organizer 2 with one profile per instance. MO2's USVFS redirects Documents per profile if "profile-specific saves/INIs" is enabled. [K] But MO2's own "already running" check is the one that fired in [V].
  - Recommended: a script builds `run/instanceN/` as a directory with hardlinks or junctions to the immutable game files and a private copy of `Fallout.ini`, with `bUseMyGamesDirectory=0`. [U] Verify with a spike that xNVSE honours it for Logs and Saves. NVSE logs go next to the exe or in My Games, so they are per-instance if the exe dir is per-instance.
- **Low-footprint settings.** [K]
  - `Fallout.ini`/`FalloutPrefs.ini`: `bFull Screen=0`, `iSize W=640`, `iSize H=480`.
  - Disable AA/AF/shadows, `bFXAA`, grass. Set `iPresentInterval=0` plus a frame cap, or leave vsync and let the game idle.
  - `bPreemptivelyUnloadCells=0` (see section 9 spike 4).
  - Mute audio: `bEnableAudio=0` is used by some ini guides, [U] verify.
  - NVSE `-noaudio`-like flags do not exist. [U]
  - Disable intro movies: `SIntroSequence=` empty and `uMainMenuDelayBeforeAllowSkip=0`. [K]
  - Windowed borderless plus minimised windows may pause rendering/focus-dependent code. Set `bBackground Keyboard=1` and `bBackground Mouse=1` so the game keeps running without focus. [K]
  - Unfocused FNV still ticks if `bAlwaysActive=1` in Fallout.ini. [K]
- **Headless-ish.** [U] There is no true headless mode. D3D9 device creation is required, since the engine has an Ni renderer baked in.
  - Options: a tiny window, a hooked `Present` that skips work, a "null" D3D9 wrapper (e.g. a stub d3d9.dll like the one used for CI in other old engines), or running on WARP software rasterisation inside a VM. Spike this.
  - The plugin can also reduce load by suppressing render via `SetGlobalTimeMultiplier` and similar. Do not rely on this.
- **Memory and CPU.** [K] The 4GB-patched FNV uses about 1.5-3 GB RAM with mods, so 2-3 instances on a 16 GB machine are realistic. 4 or more is probably risky.
- **Time-scale.** [K] Console `sgtm N` (SetGlobalTimeModifier) scales game time. Game logic (not wall-clock) tests can run fast. Physics and AI can break at big multipliers, so cap around 5-10.
- **VMs / Sandboxie.** [K] Sandboxie-Plus gives separate mutex and file-system namespaces, so it is a good fit for dodging the single-instance check with no code change. GPU access works, since D3D9 passes through. Hyper-V on Win11 Home is unavailable (Home edition). VirtualBox/VMware has weak D3D9 and is useless for FNV. [U]
- **Wine/Proton on Linux CI.** [K] FNV runs well under Proton (it is on Steam Deck verified). GOG version works in Wine. xNVSE works under Wine/Proton. [U]
  - It is a viable path for CI: Xvfb + `WINEPREFIX` per instance. Wine has no machine-wide mutex clash between prefixes.
  - Rendering via llvmpipe/lavapipe through DXVK is slow, but 640x480 is fine. Game assets are licensed, so a CI runner needs the game copy, which is a legal and private-runner issue and rules out public CI.
  - Phase 2, not day 1.
- **NV:MP's own approach.** [U] I could not retrieve the repos in this session. From the prior brief: x-native contains an `app_client_headless`. Read its source for: how it bypasses the exe-level checks, how the launcher injects, and whether it hooks `CreateMutex`. NV:MP supports multiple clients on a machine for dev testing (their devs test with 2+ clients). Mine that code first.

## 2. Driving the game programmatically

Mechanisms, most reliable first:

1. **In-plugin driver, with no external input needed (primary).** The plugin is ours, so give it a **test harness mode**, enabled by an INI flag or an env var (`NVCOOP_TEST=1`, `NVCOOP_SCRIPT=path`):
   - A *control channel*: a named pipe or localhost TCP/JSON-lines server inside the plugin (`\\.\pipe\nvcoop-test-<instance>`). The test runner sends commands and reads replies. All game-touching work is marshalled onto the main game thread via a queue drained on a per-frame hook (xNVSE `kMessage_MainGameLoop` / `PluginAPI` main loop message), because the engine is not thread-safe.
   - Commands: `console <line>`, `load <save>`, `newgame`, `wait_frames N`, `wait_until <predicate with timeout>`, `dump_state`, `screenshot`, `quit`.
2. **Console command execution from a plugin.** [K] xNVSE exposes `Console_Print` and can run a script line via the engine's `ConsoleManager`/`Script::Execute` path. The common pattern is `ExecuteConsoleCommand`-like helpers. JIP LN's `RunScriptLine` (a script function) and Lutana's `Console` function [V, from search] let script text run console commands. The cleanest approach in C++ is to compile and run a temp `Script` object, or to call the internal `ExecuteScriptLine` hook (what JIP's `RunScriptLine` does). [U] Confirm signatures against xNVSE headers and JIP source (`RunScriptLine`, `ExecuteScript`).
   - Everything usable from console is available: `SetStage <quest> <n>`, `GetStage`, `coc <cell>`, `player.moveto <ref>`, `player.additem`, `tgm`, `tcl`, `kill`, `startquest`, `completequest`, `resurrect`, `sexchange`, `sv`... (engine console is scripting language). [K]
3. **Batch files.** [K] `bat <file>` in console runs a file of console commands from the game root. Use for scenario setup with no code. Good for hand-authored tests, and a good smoke-test when the pipe does not work.
4. **JIP LN script runner.** [V from search] JIP LN runs `gl_*.txt` on game load and `xm_*.txt` on exit to the main menu. Handy as an automated hook, but adds a dependency. Prefer our own plugin.
5. **Auto-load save on start / skip main menu.**
   - [K/V] There is no stock command-line `-load` flag in FNV. (The `--load-savegame`/`--skip-menu` hits in search were OpenMW [V], not applicable.)
   - Options:
     - (a) Plugin on `kMessage_PostLoad`/`DeferredInit`/first main-menu frame: queue `load <save>` (console `load "name"`) or, for new game from scratch, console `coc goodsprings` once the main menu is up. [K] Known mods ("Skip Intro/Main Menu" style mods, e.g. Main Menu Skip, Continue Last Save) do this by calling the load routine from NVSE plugin hooks. [U] Find the engine function address; look at JIP LN's `LoadGame` function, and "Quick Start" mods.
     - (b) `Fallout.ini`: `SIntroSequence=`, `uMainMenuDelayBeforeAllowSkip=0`, and auto-start via `StartingCell`/`sStartingCell`? That is the old `-newgame`-style flag. [U]
     - (c) Simulated input: send `PostMessage` WM_KEYDOWN to the window to click through. Fragile, last resort.
   - Best: the plugin handles `load` from main-menu hook after assets are ready. Wait for a signal (first `MainGameLoop` message with player ref valid, not in menu mode) before accepting commands.
   - Fresh start: ship a **baseline save** (post-Doc-Mitchell, or a new-game-at-Goodsprings-start save) in the repo for all tests. One baseline per test scenario family (ideally at known quest stages). Saves depend on the load order, so pin plugin versions.
6. **Simulated input.** [K] `SendInput`/PostMessage and DirectInput hooks work, but are brittle. Needed only for dialogue where there is no API. Prefer API-level dialogue forcing, below.
7. **Forcing dialogue.** [K]
   - Console `player.startconversation`? Not a real command. [U]
   - Reliable approach: the plugin calls engine routines to start a dialogue (as JIP does with `StartConversation` and `ForceSay`-like functions, and NVSE `Say`/`SayTo`/`player.StartConversation <actor> <topic>` - the last is a documented script command: `StartConversation`). [K, moderate]
   - Choosing a topic: `SayTo`/`ForceTopic`; NVSE has `SetTopicDone`/`GetTopicInfos`. Also run result scripts directly. Many quests are advanced by INFO result scripts. The test can *invoke the INFO result script* (see section 6) instead of walking the menu. Document that this is a weaker oracle: it tests sync of the stage change, not the dialogue UI.
8. **Reading state out of the game.** Add `dump_state` to the plugin:
   - Walk the `QuestList`/`TESQuest` forms from the data handler. For each: `flags`, `currentStage`, per-stage log entries completed state, objectives (displayed/completed), `GetQuestCompleted`, variables (script locals via the quest's script event list), and quest-targets.
   - Also: global variables (`TESGlobal`), reputation (faction rep globals, `GetReputation`), player inventory (form id + count), player cell/position, killed actors / dead-flag of persistent refs (`GetDead`), `GetItemCount`, perks, karma, `GetStageDone` bits, `GetInFaction`.
   - Output canonical JSON: sorted keys, form IDs as `plugin:localid` (load-order independent), written to the pipe or to a file. Plus a stable hash per category so tests can compare cheaply and diff on failure.
   - The engine stores each quest's stage log in `TESQuest::stageList` with `TESQuestStageItem` (done flag). A `TESQuest` struct layout is in xNVSE `GameForms.h`. [K]
   - Also keep a plain-text log tail (NVSE log, `Console_Print` output, our own log) which tests grep.
9. **Screenshots.** [K] `screenshot` console command, or a D3D9 `GetBackBufferData` from the plugin. Only as a smoke test (section 8).

## 3. Existing tests/frameworks and prior art

I found no public framework to adopt. This is the honest result of weak search plus prior knowledge:
- **xNVSE unit tests.** [U] xNVSE (github.com/xNVSE/NVSE) does not ship a unit test suite as far as I know; CI is build-only. Check the repo's `.github/workflows`. Test scripts in NVSE are community-run in-game (e.g., "nvse_tests.esp", xNVSE's xNVSE test plugin?). [U] Spike: grep the repo.
- **Skyrim / Papyrus.** [K] Skyrim has community "Papyrus unit test" mods (e.g., SkyUnit, "PapyrusUtil"-based harnesses) and SKSE `SKSE::` tests run in-game. FO4 has "F4SE test" and some Papyrus Unit Test. For FNV, the analogue is NVSE-script tests (`ShowMessage`/Console_Print asserts).
- **Skyrim Together / TES3MP.** [K] Skyrim Together Reborn tests are mostly manual with 2 clients locally (it uses a standalone server executable + two game clients on one machine, often via sandbox). TES3MP has a dedicated server that runs Lua scripts and no headless client. I could not confirm automated tests for either. [U]
- **Bethesda internal automation.** [K, low] Public talks exist (e.g., GDC 2019? "Automated testing at Bethesda", Fallout 76 QA), covering bot-driven playthroughs, "Automated Ask" style smoke tests and nightly "go to every cell" crawlers. Gamebryo/Creation titles have a built-in `-QACrash`/"coc all cells" style; the console `coc` over all cells is a known manual QA technique. I could not retrieve the talk. These are inspiration, not reusable code.
- **TAS/speedrun tools.** [K] FNV speedrunners rely on glitch routing, not a TAS emulator. There is no frame-perfect input replay for the PC version. The nearest is a **record/replay of console commands**, which is what we need anyway. Not worth pursuing.
- **Generic game-bot testing.** [V] Metaplay "botclient testing" (headless bot clients for load/functional tests) and Photon forum advice to run one app per bot support the headless-client layer: protocol-level fake clients are cheap, so use them for most network tests (section 7).

## 4. State snapshot diffing as the test oracle

The core idea: **a quest-sync test passes if, after a scripted scenario, host and client converge on the same canonical state, AND that state equals an independently-defined expectation.**

Two oracle kinds:
1. **Convergence oracle (differential).** Both instances run `dump_state`. Compare category by category with an allow-list of expected differences (per-player inventories, positions, local-only UI flags, timestamps). Anything else differing is a failure, reported as a structured diff.
2. **Spec oracle (golden).** Expected stage/objective/global values derived from ESM data (section 5), or a recorded single-player golden run. Ensures "both are wrong in the same way" cannot pass (e.g., neither client applied the stage). Always include at least one positive assertion such as `GetStage(Quest) == 20`.

Rules for trustworthy tests:
- A test passes only if the *assertion actually executed*, so tests need positive control: a negative test (inject desync, e.g. disable the packet handler, or `SetStage` locally on one side only) must make the same comparison fail. This guards against vacuous diffs of empty dumps. Include a self-test of the diff tool with known-different inputs.
- Dumps must be non-empty: check that N quests >= known floor.
- Quiescence: wait for `net idle` (no pending outgoing/incoming messages, N stable frames) before dumping, never a bare sleep.
- Determinism: FNV is not deterministic (RNG, physics, AI, timers). Avoid seeds-based assumptions. Drive via scripted `SetStage`/kills/ item gives, not emergent gameplay. Set `tgm`/`tcai`/`tai` to stop combat. Accept that a golden whole-state snapshot is not stable, and compare only the selected categories.
- Include **time/frame accounting**: record game-time and wall-time, use timeouts with generous but bounded limits, and flag flakiness by running each test 3x in CI (and track a per-test flake rate).

## 5. Using game DATA as an oracle (parse FalloutNV.esm)

Goal: enumerate every quest, stage, log entry, objective, result script, and dialogue condition, so a generator makes one test per quest.

Parsers (verify each before committing):
- **xEdit / FNVEdit scripting (Pascal).** [K] The most complete and battle-tested. Runs headless via `FNVEdit.exe -script:... -IKnowWhatImDoing -autoexit`? [U] on flags; xEdit supports `-script` and batch. Export QUST/DIAL/INFO/SCPT/NOTE as JSON or CSV. Reliable for FNV (game mode `-fnv`). Windows-only, which is fine here.
- **esplugin (Rust, Ortham).** [V] Supports FNV plugins but is a low-level reader aimed at LOOT and libloadorder (header, masters, record presence), not full subrecord semantics. Use as a reference for record iteration/compression, but expect to write QUST subrecord decoding yourself (QUST: EDID, FULL, SCRI, DATA, CTDA conditions, INDX stage index, QSDT stage flags, CNAM log entry, SCHR/SCDA/SCTX result script, QOBJ/NNAM objectives, QSTA targets). [K]
- **Mutagen (C#).** [V partial] Mutagen has Fallout New Vegas support through Synthesis patchers (Nexus page, credits Noggog). [K] Mutagen's FNV support is an experimental/partial game release, so check record coverage of QUST, DIAL, INFO. If it exposes `FalloutNewVegasMod.Quests`, it gives typed access, compiled `.NET` and fast to script. [U] Verify.
- **Python:** `pytes3mp`? No. [U] Hand-written Python record reader for TES4-family formats is about 200 lines (record header 24 bytes, zlib-compressed records flag 0x00040000, GRUP nesting). Pragmatic fallback, and FNV's format is the same as Oblivion/FO3.
- Result scripts: stored both compiled (SCDA) and source text (SCTX) in the ESM. [K] SCTX is present in FalloutNV.esm, which makes the result scripts' commands greppable (`SetStage`, `StartQuest`, `AddItem`, `SetEnemy`...). Great for classifying: which stages are pure-stage and which have side effects needing extra sync.

What to generate per quest (a manifest, JSON):
- quest id, editor id, flags (start game enabled, allow repeated stages, priority), initial stage, ordered stages with log text, completion flag (`QSDT` flag 1 = complete quest), fail flag, result script lines, objectives with the stage that shows and completes each, conditions on the quest, referenced quest variables.
- Dialogue graph: DIAL -> INFO with `AddedQuest`/`Quest`, conditions (CTDA `GetStage`, `GetQuestVariable`, `GetIsID`...) and result scripts (`SetStage`...). Build a graph "which INFO sets which stage". Reachability gives a **path from stage A to B**, used by the generator to drive the quest by running INFO results in order.
- Auto-test per quest (tiers):
  1. **Stage sync (cheap):** for each stage S in order: host `SetStage Q S` (via the plugin API, so the sync hook fires the same way as the engine path), wait idle, assert client `GetStage(Q)==S`, objectives set equal, log entries equal, then diff dump. Runs for all ~hundreds of quests on one session without reloading, so it is fast.
  2. **Result-script side effects:** run INFO result scripts (as the engine would) on host, compare the full-state diff to the allow-list.
  3. **Late joiner:** client connects after host has advanced the quest (stage 40), asserting join snapshot equals current state.
  4. **Client-initiated:** client triggers a stage; host sees it (and for conflicting concurrent stage writes, defines the arbitration rule and tests it).
  5. **Reconnect:** drop client mid-quest, advance on host, rejoin, converge.
- Careful: SetStage on quest N out of order may skip prerequisites, or crash scripts that assume refs exist. The manifest should have a per-quest `known_bad` list with a reason, and a sandbox cell setup (`coc` into the right cell or `player.moveto`) for stages that spawn refs. Do not treat "can't set this stage standalone" as a sync bug.
- Test count: FNV base has roughly 100+ main/side quests (plus DLC with separate ESMs). The stage-level scenarios number in the thousands. All runnable by one pair of instances.

## 6. Network-level testing

- **Protocol layer**: define the packet schema (quest stage change, objective, global var, snapshot) in a standalone C++ library with no game dependencies (`libnvcoop-proto`). It has unit tests (serialisation round trip, fuzzing, version negotiation).
- **Server core** as a separately runnable server process/lib with an *abstract world model* (a state store of quests/stages/globals). Test the sync logic entirely in memory: N fake clients in one process, deterministic clock, and an in-process lossy/latency link simulator (drop, duplicate, reorder, delay). This is the main place for property-based/chaos tests and is fully deterministic and fast (no game).
- **Headless fake clients**: [U] NV:MP's `app_client_headless` in x-native, per the brief, is a precedent. Read it and reuse the idea or code (check its licence). Our fake client speaks the protocol, holds a world model, can script actions ("set stage", "join late", "drop") and assert on its model.
- **Chaos**: a loopback UDP/TCP proxy (own code, or `clumsy` on Windows (WinDivert), `toxiproxy` for TCP) between the real game clients adds latency/jitter/loss/disconnect. For the engine-in-loop tests, a built-in plugin-side "netem" flag is simpler and deterministic.
- **Packet replay**: log every packet in a pcap-like binary with timestamps. A replay tool feeds recorded traffic to a server or client core to reproduce a bug from a real session (record all e2e tests by default; attach on failure).
- **Invariant checks / model checking**: define invariants (authoritative stage monotonic unless allowed, host and client converge after quiescence, no lost update after reconnect). A randomised scenario generator (seeded, so reproducible) applies random op sequences across 2-3 fake clients with chaos and checks the invariants. This gives the most bugs per hour.

## 7. Vision-based checks (last resort)

- Screenshot via console `screenshot` or backbuffer grab, then a vision model asks "is the Pip-Boy quest list showing quest X" or "does the client show the same NPC position". Use only for UI things that no API reads (journal text rendering, quest-added HUD notification, dialogue screens).
- Prefer reading the same data via `GetQuestObjective` style APIs. A vision check is expensive, slow, flaky, and non-deterministic, so keep it out of the gating pipeline. A nightly advisory job only.
- A vision test needs a fixed resolution/UI scale/time of day/weather: set `set timescale to 0`, `set gamehour to 12`, `fw 0`... [K] `fw <weather>` (force weather).

## 8. Proposed architecture (layers)

```
L0  Static / unit (no game)         ms-s    run on every commit, any machine
L1  Sim integration (no game)       s-min   in-process N fake clients + netem
L2  Engine-in-loop, 1 instance      min     plugin test-harness inside real FNV
L3  Two-client E2E                  min     host+client real FNV, scripted
L4  Per-quest auto-generated        hours   same as L3, manifest-driven, nightly
L5  Vision / soak                   nightly advisory
```

- **L0 Unit.** Protocol serialisation, state-store/merge/arbitration logic, ID mapping (plugin:localid <-> runtime formid), manifest parser, snapshot differ (with a test for the differ itself). C++ with Catch2 or doctest, built with the plugin tree; plugin core logic is split from engine-touching code with an interface (`IGameWorld`) so mocks work. Pure CMake/CTest, no game files needed, so agents can run it anywhere, including in their own worktree.
- **L1 Sim.** Server plus fake clients in one process, a virtual clock, the netem link. Scenario DSL (`host.set_stage(Q,20); net.partition(client2); ...; expect_converged()`). Includes the seeded random/chaos fuzz. Gate: must pass before any engine-in-loop run (saves time and conserves the scarce game instances).
- **L2 Engine-in-loop (1 instance).** The harness plugin mode plus the pipe. Test content: the plugin loads under xNVSE without crashing, hooks resolve (address check), `dump_state` works and matches the manifest on a baseline save (e.g., Q at stage 0 for all), `SetStage` via the plugin API emits the right packet to a fake server (captured), and applying an inbound fake-server packet changes the real engine state (`GetStage`). This catches most engine-integration bugs with ONE game instance and a fake peer. High value: ~70% of the trust comes from here.
- **L3 Two-client E2E.** Python (pytest) runner: launch server (optional, or host-is-server), launch 2 FNV instances from `run/instanceN`, wait for pipe `ready`, load baseline save, connect, run a scenario, `wait_idle`, `dump_state` both, diff, plus golden assertions, capture logs/pcap/screenshots on failure, kill processes (job object so no leaks). Handle timeouts + one retry-with-flag (record flaky; do not silently pass).
- **L4 Per-quest.** Generator reads the manifest from L5-data pipeline (xEdit/Mutagen) and emits pytest params. Tiers from section 5. Parallelism is limited by instance count (2 per pair, maybe 2 pairs on a strong PC).
- **L5 Vision/soak.** Optional.

Tooling and conventions for many parallel AI agents:
- **One command per layer** (`ctest -L unit`, `py -m pytest tests/e2e -m smoke`) with machine-readable output (JUnit XML + JSON diffs) so agents get pass/fail plus a structured diff, not prose.
- **Resource arbitration.** There are only a few game instances' worth of RAM/GPU. Use a lock/queue (a file lock or tiny scheduler) on the E2E machine, with per-agent `run/<agent>/instanceN`, unique pipe names and TCP ports (derived from a base-port allocation). Agents without the game (or on a worktree) can run L0/L1 only. Make L2+ opt-in with a clear skip reason, never a silent pass.
- **Fixtures**: pinned baseline saves, game dir hash check (refuse to run on a mismatched exe/ESM/plugin list so results are meaningful), the manifest versioned with the ESM hash.
- **Anti-vacuity**: every E2E test has a mutation twin ("break sync, expect FAIL") run periodically, as in section 4. This is the only real protection against tests that agents make "pass" by weakening assertions. Also forbid editing goldens/allow-lists in the same change as plugin logic without review (CODEOWNERS-style rule).
- **Crash handling**: collect minidumps (`SetUnhandledExceptionFilter` in plugin, or `procdump`), a hang watchdog (frames-since-last-tick), and fail with the log, not a timeout.

## 9. Feasibility on ONE Windows 11 PC and spikes to run first

Feasible: L0, L1, L2, L3, L4 with 2 instances (host+client) at 640x480 windowed, plus fake clients for extra peers. A pair can run in parallel with only limited extra pairs, depending on RAM/GPU. Linux/Wine CI is a later nice-to-have (private runner, licence).

Spikes (each 0.5-1 day, ordered):
1. **Two instances launch.** Confirm what blocks the 2nd exe (mutex? window class? `.lock`?), via Process Monitor/API Monitor and by reading NV:MP's launcher. Try the Sandboxie-Plus path; otherwise hook `CreateMutex`.
2. **Per-instance INI/Saves.** Confirm `bUseMyGamesDirectory=0` or junction approach with xNVSE and hardlinked Data.
3. **Auto-load save + pipe**: plugin test harness: main-thread command queue, `console`, `load`, `dump_state` of one quest, round trip from Python.
4. **Background running**: unfocused/minimised windows keep ticking; frame cap and CPU use; whether cells unloading breaks multi-quest stage tests.
5. **ESM manifest**: xEdit script and/or Mutagen/own parser export one quest fully (compare to GECK). Decide the tool.
6. **Net loopback**: host+client sync one `SetStage`, diff, intentionally broken twin fails.
7. **Read NV:MP code** (`app_client_headless`, launcher, injection), lifting design and licence-compatible pieces. Brief says GitHub; nothing was fetched here.

## 10. Biggest risks

1. **Single-instance/DRM/INI conflicts** may be harder than hoped. Mitigation: spike 1-2; fall back to Sandboxie or two Windows users.
2. **Engine thread-safety and unreliable hooks.** Console and `SetStage` calls from the wrong thread crash the game. Mitigation: all calls via main-loop queue; L2 catches this early.
3. **Non-determinism and flakiness** of live game state (AI, physics, timers, scripts that run on their own). Mitigation: compare selected categories, quiescence, `tgm/tai`, 3x-run flake tracking, no sleeps.
4. **Vacuous or gamed tests**, by AI agents. Mitigation: mutation twins, positive assertions, non-empty-dump checks, protected goldens.
5. **Out-of-order SetStage** has side effects or breaks scripts, causing false positives. Mitigation: manifest `known_bad` list with reasons, scenario cells and prerequisite stages from the dialogue graph.
6. **Stale quest state across tests** (reusing one session) leaking between tests. Mitigation: reload baseline save per quest group, or per test for risky quests.
7. **Resource limits and concurrency**: multiple agents fighting over 2-4 instances on one PC. Mitigation: lock/queue, unique ports/pipes, L0/L1 for most iteration.
8. **ESM parsing completeness** (Mutagen FNV record coverage unknown, esplugin shallow). Mitigation: xEdit scripts as the primary export; verify against GECK.
9. **Licensing / CI**: game assets can't be in public CI, so GitHub-hosted runners can't run L2+. Mitigation: self-hosted or local runner, and keep L0/L1 meaningful.
10. **Research gaps**: this report did not confirm NV:MP's repos, xNVSE's own tests, or Bethesda's talks. Treat those as [U].

## Sources actually retrieved
- stepmodifications.org: duplicate-instance error with stale FalloutNV processes
- forums.nexusmods.com/topic/312193: running two GECKs, user-account trick reportedly failed
- geckwiki.com TSCR / Lutana Console function; eddoursul.win (JIP LN script runner gl_/xm_ files; MenuMode)
- docs.rs / lib.rs esplugin: supports FNV, aimed at LOOT
- Nexus mod 98516: Mutagen/Synthesis FNV support
- docs.metaplay.io botclient testing; forum.photonengine.com bots
