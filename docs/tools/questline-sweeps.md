# Questline sweeps: stage sweep configs, two-copy playthrough playbooks, rules overlay

Status 2026-10-21 (roadmap steps 8 and 9): code and tests done, game-free. **Nothing here has run in a game.** The playbooks
are proven against the fake co-op world (a model of the rules in docs/protocol.md), the rules against the real C++ rules loader
(`fnvcoop_tests --group rules`), and nothing else. `python -I tools/questsweep/run_tests.py` prints `RESULT: ALL OK` (36 tests, about 35 s; also in `tools/test_all.py`).

## What exists

| Piece | Where |
|---|---|
| Generator | `tools/questsweep/gen_plans.py` (`--check` fails on a stale file; needs `out/questdata`, skips with a reason without it) |
| Plans / sweep configs, one per questline | `questlines/<line>/playthrough.json` (17 lines, committed; no game data needed to run them), `questlines/goodsprings/sweep.json` |
| Playbook package | `tools/bots/playbooks/questlines/` (`driver.py`, `model.py`, `fake.py`, `companions.py`, `plan.py`) |
| Runner | `tools/questsweep/run_questlines.py` (fake or live) |
| Rules overlay | `tools/questsweep/rules_overlay.py` + `rules_overlay.json`, applied to `docs/quest-policies/rules.json` |
| Tests | `tools/questsweep/tests/test_questsweep.py`, runner `tools/questsweep/run_tests.py` |

The playbooks are NOT in `playbooks/registry.py`: that registry is looped over by the T6b suite with scenario based fake worlds, and 17 plan driven
playbooks would only slow it down. `playbooks.questlines.names()` lists them (`ql_goodsprings`, `ql_prologue`, `ql_act2`, `ql_house`, `ql_ncr`, `ql_legion`,
`ql_yesman`, `ql_towns`, `ql_strip`, `ql_factions`, `ql_leftovers`, `ql_systems`, `ql_companions`, `ql_deadmoney`, `ql_honesthearts`, `ql_owb`, `ql_lonesomeroad`).

## The 17 lines (numbers from the committed plans)

| Line | Quests | Stages | With unconditional effects | With a dialogue trigger | With bot actions | Lane-excluded |
|---|---:|---:|---:|---:|---:|---:|
| goodsprings (new config) | 6 | 104 | 37 | 34 | 16 | 0 |
| prologue | 5 | 38 | 16 | 13 | 7 | 14 |
| act2 (Ring-a-Ding-Ding and Act 2) | 14 | 95 | 68 | 45 | 33 | 0 |
| house | 13 | 58 | 11 | 18 | 6 | 0 |
| ncr | 10 | 84 | 33 | 20 | 12 | 0 |
| legion | 9 | 37 | 27 | 9 | 11 | 0 |
| yesman | 6 | 19 | 11 | 11 | 7 | 0 |
| towns | 16 | 116 | 65 | 39 | 30 | 2 |
| strip | 25 | 216 | 144 | 106 | 79 | 0 |
| factions | 33 | 192 | 104 | 62 | 58 | 0 |
| leftovers | 19 | 147 | 74 | 44 | 43 | 0 |
| systems | 34 | 157 | 57 | 35 | 31 | 0 |
| companions | 8 | 51 | 29 | 19 | 19 | 0 |
| deadmoney | 18 | 114 | 103 | 21 | 45 | 10 |
| honesthearts | 25 | 102 | 90 | 31 | 46 | 0 |
| owb | 21 | 102 | 81 | 14 | 49 | 0 |
| lonesomeroad | 10 | 59 | 40 | 2 | 24 | 0 |
| **total** | **272** | **1691** | **990** | **523** | **516** | **26** |

Every stage the lane's own sweep config lists (swept or excluded) is in its plan (a test compares them through `plugin/snapshot/tools/sweep_config.py`, which reads all 15 lane shapes).
`goodsprings` had no sweep config of its own (only `out/questdata/m1_goodsprings.json`); `questlines/goodsprings/sweep.json` is new and loads with the same loader.

## What a plan says (questlines/<line>/playthrough.json)

A superset of the `m1_goodsprings.json` format (`quests[].edid/ref/stages[].index/objective_effects`). Quest ids are runtime FormIDs with the load order byte
(`01005229` for Dead Money), so DLC quests cannot collide with base quests. Per stage:

* `fx`: the **unconditional, non dynamic** effects of the stage result script: `obj` (objective flags), `items` (AddItem to the player), `xp`, `karma`,
  `enable` / `disable` / `kill` (refs), `quest` (start / stop / complete), `stage` (a stage of another quest). These are what is asserted on every machine.
* `cond`: counts of the conditional effects (the host decides the branch; only "all machines agree" can be asserted) and of the unmodelled script calls.
* `trigger`: the dialogue INFO whose result sets this stage, when exactly one INFO does (an INFO that sets several stages unconditionally is ambiguous and is no trigger).
  `speaker_ref` is a GUESS (the single target of the previously displayed "talk" objective); it is used by fake runs and by live runs with `--natural`.
* `act`: what a player does for the objectives this stage displays: `kill`, `talk`, `walk`, `take`, with the objective's placed target refs (verb from the objective text).
* per quest `rules`: barrier, ready check, exclusive outcome groups, guest-local, per-player variables, taken from rules.json (a test fails when a plan drifts from it).

## What a playbook does

Per quest one step (the engine then settles, snapdiffs all machines and checks the desync counters). For each stage, in the lane's order:

1. the **model** (`model.py`) computes what must happen (stages, objectives, rewards, enabled / killed refs, started quests, karma, what is held or dropped) and the item counts of every player are noted;
2. the stage is reached the way its rules say: a player (rotating, clients too) walks to the speaker and picks the trigger INFO, or the host sets it by console; a **barrier** quest holds the
   request until every player passed (checked: not fired after n-1 passes); a **ready check** stage is first answered "Not yet" by a guest *last* (so a party that counted it as agreement fires)
   and then agreed by all; an **exclusive** outcome that lost is asked for and must be refused everywhere; a **guest-local** quest is run on the host only (must not reach the clients) and then by
   each client itself; **per-player variables** get a different value per player and must stay apart;
3. every machine must show the stage, the objective flags, **exactly one reward per player**, the enabled / killed refs, started quests and the shared karma total;
4. the bots then do what the displayed objectives ask (kill with the fight helpers, talk, walk, pick up) and the world must agree.

`ql_companions` first plays recruit (by a client), the per-player follower cap (1 humanoid + 1 critter, `questlines/systems/companions.json`), "nobody can take another player's companion",
dismiss and recruit again, then sweeps the companions' personal quests.

## Running

```
python -I tools/questsweep/run_tests.py                                             # everything game-free (prints RESULT: ALL OK)
python -I tools/questsweep/run_questlines.py --list
python -I tools/questsweep/run_questlines.py --fake --line all [--players 3] [--quests VCG02]
python -I tools/questsweep/run_questlines.py --fake --line yesman --bug ready_skipped    # a twin: must FAIL
python -I tools/questsweep/gen_plans.py [--check]                                        # regenerate / verify the plans (needs out/questdata)
python -I tools/questsweep/rules_overlay.py --check | --apply | --derive
```

In a game (the lead runs it; this tool never starts one): start N co-op instances as one session on a **disposable** save, then
`python -I tools/questsweep/run_questlines.py --line goodsprings --instances 0,1 [--natural] [--actions] [--include-excluded]`.
The plan's quests are reset first with `test.quest.reset` when the bridge has it. Live defaults are conservative: console stage setting only,
no bot actions (they can kill named NPCs), the lane's excluded stages skipped. `--natural` plays the dialogue lines (needs the speaker refs), `--actions` lets the bots act.
Bridge methods a live run may find missing are noted, not fatal: `coop.playerVar.set/get` (per-player vars), `coop.consent` (ready check answers), karma / XP readers.

## Fake bugs (mutation twins; Python tests, so no `FNV_MUT` numbers)

`questlines/fake.py` `QL_BUGS` plus the T6b fake bugs. Each pair below must FAIL (all are in `Playthroughs.TWINS` / `test_tiny_plan_twins`):
`exclusive_ignored`, `ready_skipped`, `barrier_ignored`, `guestlocal_replicated`, `guestlocal_clients_blocked`, `reward_to_host_only`, `reward_twice`, `no_world_sync`,
`karma_local`, `no_quest_run_sync`, `cross_stage_dropped`, `pvars_shared`, `no_follower_cap`, `recruit_not_exclusive`, plus `no_stage_sync`, `no_objective_sync`, `no_fight_sync`, `recruit_local`,
`ready_veto_ignored`, `pnr_early`, `counter_noise`. Three more twins break the *model* (double rewards, no exclusive, no barrier): the run must disagree with the correct fake.

## The rules overlay (policy gaps closed in docs/quest-policies/rules.json)

rules.json is generated from 16 rule kinds; what the lanes proposed beyond that sat in `rules.pending.json`. The RulesEngine implements them since round 8, so the parked rules are
switched on (`origin: "questsweep"` on added rules; 98 ops, each with a reason and source in `rules_overlay.json`). Re-running `tools/coverage/build_rules.py` drops them; `rules_overlay.py --check` then fails and `--apply` restores them.

| Gap | What was done |
|---|---|
| GuestLocalRun for the DLC setup quests | `NVDLC01..04MQ00` stage results start the DLC story, so they must keep replicating: a quest-level `GuestLocalRun` would drop them. Their setup **scripts** are listed on `VDLCPackQuest`'s rule instead (`guestLocalScripts()`); every other pure per-machine quest of `plugin/hooks/data/guest_local_run.json` (29) got the rule with its own script; `NVDLC02RadioMessage` / `NVDLC02RainQuest` got entries. A test keeps rules.json and the hooks list in step. |
| Per-machine scenes | `PerPlayerScene` `run=every-machine` + `scope=local-machine` for the 41 lane-decided quests (the engine drops their txns). |
| Per-machine variables | `PerPlayerVars` on the 7 Old World Blues quests that named script variables (identifiers only; the wildcard and prose entries of the lane list were left out). |
| ExclusiveStages | `VNipton` 30/35/70, `nVPrimmDeputyConv` 110/120/130, `VMQYesMan01a` 100/110 (documented in the lane reviews). I did NOT derive more from the complete/fail stage flags: 123 quests have both and some fail stages legitimately follow a completion. |
| ReadyCheck at one-way points | INFO based: Mr. House "I'm ready" (`VDialogueMrHouse`, INFO 15EB69), Yes Man "Are you ready?" topics 067 / 073 (`VMQTops`, INFOs 160723 / 160735). The Hoover Dam start for NCR and Legion (and the Yes Man message box) is a **message box** read by the script, not an INFO: it is handled by the UI layer (`plugin/hooks_ui`, the T6b `pnr_vote` playbook), not by a rule. |
| Without a `stages` list the engine holds EVERY stage | `Barrier.stages` for `VMQ05` [100] and `vHDBattleController` [30], `PartyTether.stages` for `VMQ05` [60, 100], `vHDBattleController` [10], `VMQHouse1` [25]. |
| Slideshow barriers | `Barrier.before = slideshow` on the 9 ending quests (controls stay locked from the release to the committed SlideshowEnd). |
| Satellite tether | `NVDLC03MQ00` `PartyTether` anchor `NVDLC03CrashedSatelliteREF`, radius 1500, stage 100. |

Checked: `validate()` mirrors `validateRule()` of `synccore/src/rules.cpp` (with twins), and the test also runs the real loader (`build/all/synccore/fnvcoop_tests.exe --group rules`, 21 tests, including "loads the real rules.json and is strict"); it skips with the build command when the exe is missing.
The lane suites under `questlines/*/run_tests.py` fail in the same way before and after the overlay (checked against a clean `git archive HEAD` copy): they compare against their own patches and are not part of this change.

## Limits and open items

* **Not run in a game.** The fake implements the rules a second time (next to `model.py`); a shared misunderstanding of the engine would pass both. Console grammar, timing, dialogue picking and what the bots can reach are UNVERIFIED.
* The plugin side of three overlay rules is not wired: nothing calls `RulesEngine::onReadyRequested` from the INFO end script (the ReadyCheck rules are data until the dialogue layer asks), the acting player's `PerPlayerVars` copy is not loaded around script runs (internal lane notes "Plugin follow-ups"), and `GuestLocalRun.scripts` is not yet read into `SuppressPolicy` from rules.json (hooks still read `guest_local_run.json`).
* Only unconditional effects are asserted. Conditional ones (741 effects over the 17 plans) are only compared across machines, and the fake never takes those branches.
* House's ready INFO sets its stage from a script, not from the INFO result, so no stage maps to it: the ready flow is exercised for Yes Man only.
* `VCG00` / `VCG01` carry a `Barrier` without `stages`, so the engine defers every one of their stage requests: the Goodsprings playbook passes the barrier 76 times. Whether that is wanted is a lane decision; the playbook follows rules.json.
* Speaker refs for natural dialogue are guesses (see `trigger.speaker_ref`); the objective actions can hit essential NPCs in a real save.
* Lane-excluded stages (26: the prologue's, Dead Money's, two in towns) run in the fake and are skipped live by default.
* The Hoover Dam / Yes Man message-box confirmations are covered by T6b `pnr_vote`, not by these playbooks.
