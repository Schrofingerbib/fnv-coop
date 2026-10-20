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

## Coordination
- **Describe in each PR** what was proven in game and what was only unit-tested.
- **If you need an interface from Track A** (for example the hit path or actor ownership queries), open an issue
  describing it rather than editing Track A code.
