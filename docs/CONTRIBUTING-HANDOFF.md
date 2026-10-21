# Contributor handoff: Track B (in-game validation, split-screen, real-time VATS)

Repo: https://github.com/cranebeaker/fnv-coop

You are a Claude Code session helping build full-campaign co-op for Fallout: New Vegas. The maintainer works on
**Track A** (the sync core: actor ownership, NPC and combat sync, story sync, build speed). You own **Track B**
below. Both tracks land in this repo through pull requests.

## Read first
1. `README.md`: what the project is.
2. `docs/FULL-SYNC-PLAN.md`: the whole plan. Section 6b lists the toolkit; your work is sections 3.4 (VATS) and 6a
   (split-screen).
3. `docs/TOOLS-BRIEF.md` and `docs/tools/*.md`: every test tool, how to run it, and what is still unverified in game.
4. `docs/protocol.md` and `docs/CONTRACTS.md`: the wire protocol and the working rules.

## Setup
- **Fork:** fork the repo, clone it, and work on a branch per task (`track-b/<task>`). Open a PR to `main` per
  task; keep PRs small and focused.
- **Requirements:**
  - Windows; Fallout: New Vegas 1.4.0.525 (Steam or GOG) with xNVSE;
  - Visual Studio 2022 (MSVC x86), CMake and Python 3.11;
  - clone xNVSE into `third_party/NVSE` (see `third_party/README.md`).
- **Game data:** this repo ships no game data. Generate the quest data from your own install with
  `python -I tools/questdata/run_all.py` where a tool needs it.
- **Tests:** `python -I tools/test_all.py --quick`, then the full run. Suites that need data you don't have print
  `SKIP` with a reason.

## Rules
- **Every change ships with tests plus a mutation twin** (a deliberately broken variant the test must catch), and
  a one-command runner that prints `ALL OK`. Run the affected suites before every PR.
- **Never loosen an existing test** without a written reason in the PR.
- **Never drive the desktop with simulated keyboard or mouse input.** Tests control the game through the bridge
  and the in-process bot input only. Launch test copies windowed and minimized.
- **Keep game files, saves and decompiled code out of the repo.** Static engine addresses with pinned prologue
  bytes are fine, following the existing headers.
- **Write C++ and multi-line strings with your editor tool, not shell heredocs.** Heredocs break `\n` escapes on
  Windows.
- **Stay inside your Track B directories** (below) unless a PR explains a minimal shared edit. Track A changes
  `synccore/`, `plugin/coop/` and `plugin/hooks*/` a lot, so avoid editing those to prevent merge pain.

## Track B tasks (in priority order)
0. **Run the enemy tests in game** (section "Track B task: run the enemy tests in game" below) as soon as a build with the actor adapter exists.
1. **In-game validation of the toolkit.** You have the game; the maintainer's PC is RAM-limited. Run the in-game
   proof scripts, one game copy at a time unless a test needs two, and fix what's wrong in the tools:
   - `tools/runner/reload_proof.py` (hot-reload);
   - `tools/bots/proof_input.py` and `tools/bots/proof_glue.py` (bot input and engine glue: menus, dialogue
     pick, VATS, consent, genuine fast travel, terminals, inventory, drops);
   - `tools/scenarios/proof.py probe`, then `jump` and `suite`;
   - the pool `tools/pool/measure.py` (RAM and CPU per instance);
   - the animation probe first-run checklist in `docs/tools/T7-anim.md`;
   - `tools/bots/run_coop.py --instances ...` playbooks on two copies.

   Report what passes. For each failure, fix the tool and add a regression test.
2. **Split-screen mode** (plan section 6a). New directories: `tools/splitscreen/` and `plugin/splitscreen/`.
   - A one-click launcher for 2 to 4 players, with borderless windows placed per layout.
   - Per-instance XInput pad routing (hook XInputGetState, virtual pad 0 to physical pad N).
   - Focus faking so unfocused copies keep simulating and taking pad input.
   - Audio only from player 1's copy.
   - HUD and menu checks at half and quarter resolution.
   - A split-screen smoke playbook.
3. **Real-time VATS** (plan section 3.4). New directory: `plugin/vats/`.
   - Fallout 76 style: no world time slowdown; targeting UI, limb cycling, hit chance, AP and criticals kept;
     shots fire immediately.
   - Rework the VATS perks for real time.
   - An optional host setting, "party slow-mo".
   - Static RE of the VATS limb-highlight state, so bots can pick limbs (currently unsupported, see
     `docs/tools/T6a-actions.md`).
   - Leave damage routing to Track A's combat sync: call the normal hit path, with a clear interface note in
     your PR.

## Track B task: run the enemy tests in game
This is the check that decides whether NPC sync works. Track A (fight-test, 2026-10-21) wrote four two-copy tests and ran them
against the fake world only; **nothing has run in a game**. Read `docs/tools/enemy-tests.md` first (what each test proves, the
list of UNVERIFIED console lines). Two game copies are needed; close everything else, keep both windowed and minimized.

1. Build and provision two instances with the bridge, bot and animation switches (provisioning rewrites `nvcoop.ini`, never hand-edit it):
   ```
   cmd /c tools\runner\build_l1.cmd
   python -I tools/runner/run.py provision --n 2 --ini coop.enable=1 --ini dev.bots=1 --ini dev.animprobe=1
   python -I tools/pool/pool.py up --size 2
   ```
   Make sure both copies are one co-op session: `coop.status` on instance 0 says `role: host`, on instance 1 `role: client` and
   `client.joined: true`.
2. Ask the build what it can do (nothing is launched or changed; exit 3 = BLOCKED, it lists the missing bridge methods):
   ```
   python -I tools/bots/run_fight.py --instances 0,1 --preflight-only
   ```
   `coop.owner` must be present AND answer `known: true` for an NPC. That needs Track A's engine adapter for `ActorSync` (the preflight
   prints `actors.port ... enabled=True session=True fed=<n>` per instance when it is there); if the
   first step of `enemy_fight` fails with "do not know this NPC's owner", the adapter is not in your build yet: report it and stop.
   `coop.combat` is optional (the checks that need it are skipped with a note).
3. Run the tests, one at a time first, then all four (each jumps to `scenarios/goodsprings_enemies.json` by itself):
   ```
   python -I tools/bots/run_fight.py --instances 0,1 --which fight --outdir out/fight-run1
   python -I tools/bots/run_fight.py --instances 0,1 --which handoff --outdir out/fight-run1
   python -I tools/bots/run_fight.py --instances 0,1 --which spawn --outdir out/fight-run1
   python -I tools/bots/run_fight.py --instances 0,1 --which loot --outdir out/fight-run1
   python -I tools/bots/run_fight.py --instances 0,1 --keep-going --outdir out/fight-run2
   ```
   PASS looks like `PASS  enemy_fight  8 steps` per test, exit code 0, and `summary.json` in the output directory. A failure
   prints the step and the reason and saves `FAIL-<n>-<step>/` (failure.json, snapshots of both machines, events, flight dumps) and
   `anim_report.json` under the test's folder; run `python -I tools/flight/flight.py diverge` on the flight dumps to narrow it.
4. Report per test: PASS or the failing step with its reason, and answer each UNVERIFIED line from `docs/tools/enemy-tests.md`
   (`player.setav Health 5000`; `additem` on a living ganger replicating and surviving his death; enabling the VCG02 geckos;
   `placeatme` for the spawn test; the host's walk to Novac leaving the loaded area; `Bot.pos` on an ACRE). For each failure decide
   whether the game or the test is wrong. Fix the test or scenario and add a regression test (the fake world is in
   `tools/bots/playbooks/fakecoop.py` and `fakenpc.py`); report a game-side desync as an issue instead of editing Track A code.
5. Optional, later: repeat step 3 with a bad link (`--chaos bad_wifi --chaos-seed 7 --chaos-rpc runs/chaos.json`, see
   `docs/tools/T5-chaos.md`) and with `--players 2` only (the tests need exactly two copies today).

Thresholds you may have to tune from a clean run (all unmeasured first guesses): `pos_tol` 150 units (`pb_enemy.DEFAULT_POS_TOL`),
`tools/animcheck/config.py` `FIGHT.state_grace_ms` 250, `FIGHT_NPC_SPEC` in `tools/bots/playbooks/animwatch.py`. Loosen one only with a
written reason in the PR and a clean run that needs it.

## Coordination
- **Describe in each PR** what was proven in game and what was only unit-tested.
- **If you need an interface from Track A** (for example the hit path or actor ownership queries), open an issue
  describing it rather than editing Track A code.
