# T6b-playbooks: multi-bot co-op playbooks

Status 2026-10-20: code and tests done, game-free. Nothing was run in a game. `python -I tools/bots/playbooks/tests/run_tests.py` prints `RESULT: ALL OK` (31 tests, about 80 s; also in `tools/test_all.py`).

## Files
- `tools/bots/run_coop.py`: the CLI. `--list`, `--fake`, `--playbook NAME|a,b|all`, `--players N`, `--instances 0,1`, `--pool`, `--chaos PROFILE --chaos-seed N --chaos-rpc X`, `--bug NAME` (fake only), `--no-scenario`, `--keep-going`, `--outdir`.
- `tools/bots/playbooks/core.py`: engine. `Ctx.step()` runs a step, then settles, runs snapdiff (host vs each client, section `shared`) and checks the desync counters; a failure saves `FAIL-<n>-<step>/` and aborts the playbook.
- `pb_combat.py` (fight, ownership_handoff), `pb_loot.py` (loot_race, container_share), `pb_world.py` (door_terminal_lock, fast_travel_tether), `pb_social.py` (companion_recruit, dialogue_spectator), `pb_story.py` (pnr_vote, quest_step_sync, goodsprings_opening).
- `goodsprings_plan.py`: the VCG01 -> VCG02 -> VCG03 path built from `out/questdata` (scenarios/VCG0x.json, quests.json, m1_goodsprings.json, refnames.json.gz). `check_plan()` proves every id resolves; it already caught one wrong guess of mine (a VCG02 stage that does not exist).
- `adapter.py` (wraps T6a's `Bot` to the method names the playbooks use), `probe.py` (fallbacks and clear "bridge method missing" results), `scenario_adapter.py` (T4b scenarios to refs, and the jump), `chaos_adapter.py` (T5), `fakecoop.py` (fake 2 to 4 machine session with 22 switchable bugs), `registry.py`.

## Running
Game-free: `python -I tools/bots/run_coop.py --fake --playbook all` (a bug run: `--bug dup_pickup --playbook loot_race` must FAIL).
In game (lead only; this tool never launches a game or takes the slot): start N instances joined as one session, then
`python -I tools/bots/run_coop.py --playbook goodsprings_opening --instances 0,1`. Host is the first instance. With `--chaos`, digest/repair counters stop failing steps (they are still recorded per step) and timeouts grow; `applyFailed`, `badMessages` and `rebases` always fail.

## Playbooks
| name | players | scenario (T4b) | what each step proves |
|---|---|---|---|
| fight | 2-4 | goodsprings_fight | all shoot the same raiders; dead on every machine; raider count constant (needs `dev.countRefs`, skipped with a note otherwise) |
| loot_race | 2-4 | loot_race | exactly one pickup wins, party holds exactly 1; 2 more rounds via drop and re-race (needs drop to report the new world ref; else skipped with a note) |
| container_share | 2-4 | door_terminal_container_matrix | one "in use" lock; take and put replicate; nothing duplicated or lost |
| door_terminal_lock | 2-4 | door_terminal_container_matrix | door open/close from host and client, locked door, unlock, terminal entry, terminal-unlocked box |
| companion_recruit | 2-4 | companion_recruit | recruit by a client: teammate everywhere, owner follows recruiter, no second recruit |
| dialogue_spectator | 2-4 | dialogue_spectator | speaker talks, others spectate and are released, no second speaker, roles swap |
| pnr_vote, pnr_vote_guest_declines | 2-4 | pnr_vote, pnr_vote_guest_declines | barrier holds until the last player passes, applies once; optional ready-check veto |
| quest_step_sync | 2-4 | quest_stage_sync | VCG02 stages and objective flags match everywhere; repeat is a no-op |
| fast_travel_tether | 2-4 | fast_travel_tether | refused without consent; all arrive together; wanderer held by tether |
| ownership_handoff | 2-4 | ownership_handoff | host walks away: owner moves once to the client, no flapping when the host returns, fight continues |
| spawn_once | 2-4 | spawn_once | host spawns one; exactly one on every machine, still one after a quiet period (needs `dev.countRefs`; spawn uses console `placeatme`, UNVERIFIED) |
| vats_hit | 2-4 | vats_hit | host VATS on the target's head; damage equal on all machines, applied once; the other machines' frame counter keeps advancing (world not paused) |
| save_load_rejoin | 2-4 | save_load_rejoin | host saves; a client leaves, host moves the quest on, client rejoins and catches up with reward item counts equal to the host's gain (reward ids: no duplicate, none missing); host loads the older save, every client rebases (counter rises) and follows; second leave/rejoin adds no rewards |
| goodsprings_opening | 2-4 | goodsprings_opening | 53 steps: every stage of VCG01 (barrier at 30), VCG02, VCG03; objective flags from questdata on every machine; actions between stages |

## Bridge methods and facts needed (not in the build today; each is a clear failure or a skipped note)
Dev-only, for T6a (`plugin/bots/`, ini `[dev] bots=1`), names as sent to T6a:
- `bot.consent {answer: ready|notyet}`: answer the L8 Ready / Not yet prompt. `bot.menu.press {button}`: press a message-box button (0 = "I'm not ready yet", 1 = "Let's do this!").
- `test.fasttravel {marker}` (T6a final name; a MoveTo teleport, not genuine fast travel), `dev.countRefs {base, cell?}` -> `{count}` (live refs), `dev.terminal` / `dev.terminalState` (built by T6a: `Bot.use_terminal(ref, entry_index)`, `Bot.terminal_used(ref)`; "used" is what that bot picked, the engine keeps no flag, so door_terminal_lock asserts the actor's own entry and then the box unlock on every machine).
- `bot.say`, `bot.menu`, `bot.inventory {ref?}` (already in T6a's spec). `Bot.drop` should return `data.ref`.
Co-op runtime (L10, to be assigned; not running now): `coop.leave {}` and `coop.join {}` (this machine drops out of / rejoins the session; T4b uses the same names);
- `coop.owner {ref}` -> `{owner, epoch}`: actor ownership service answer on this machine (same on all machines once settled).
- `coop.consent {}` -> `{pending: [{kind: "ready"|"fasttravel"|"wait", quest, info}]}`: consent prompts waiting for THIS player. Playbooks wait on it before answering; if missing they just answer.
Other facts:
- `companion_recruit` has no recruit INFO id in its scenario; the playbook then sets the teammate flag by console (tests sync, not the dialogue). `fast_travel_tether` radius 3000 is an assumption. `Bot.unlock` uses an UNVERIFIED console call.
- pnr_vote (barrier part) follows L4/L10 (VCG01 stage 30; the save must be before it). With the real scenarios, pnr_vote runs the VMQ05 endgame confirm: decline, a guest "Not yet" (vetoed), then all Ready (stage 100, starts the battle; the next jump reloads). `pnr_vote_guest_declines` stops after the veto.
- Scenarios used: all 14 in `scenarios/`. save_load_rejoin: the scenario names no reward item, so the real run skips the reward-count checks with a note (set `reward_item` to enable); reward ids are not visible over the bridge, item counts stand in. The save is named `autosave_t6b` (engine requires the autosave prefix). dialogue_spectator uses Sunny (VCG02) with the guest as the only speaker and checks stage 25 on every machine.

## Animation and pose checks (T7)
Every playbook where someone moves has `anim = True` (all except quest_step_sync and save_load_rejoin, where nobody walks) are sampled with `anim.probe` on every machine (needs `[dev] animprobe=1`; `--no-anim` skips it). After the last step an extra step "animation and pose check" runs `animcheck.analyze` with the avatar pairs (each player's real character against its puppet on every other machine; puppet refs from `coop.status playerRefs.slots`) and asserts `avatar.compared>0`, `avatar.anim_mismatches==0`, `avatar.glitches==0`, `avatar.wrong_weapon/aim/facing==0`, `avatar.pose_err_p95<=12`, plus the NPC equivalents (`compared>0`, `group_mismatch_episodes==0`, `glitches==0`, `wrong_weapon/aim/facing==0`, `pose_err_p95<=12`). The host's walk-away in ownership_handoff is passed as an `expect_moving` window on that player's puppet refs (the analyzer keys windows by ref on every machine and every machine's own player is ref 0x14, so only puppet refs can carry one). Deliberate teleports and fast travel are wrapped in `ctx.teleporting()` (fast_travel_tether): only `rubberband`, `floating` and `sinking` of the players' characters and puppets are ignored, from 100 ms before to 700 ms after the jump. Sliding, wrong weapon, T-pose and the rest are never ignored, so a puppet that glides after a suppressed jump still fails (twin `puppet_slides_after_teleport`; a test also proves the jump alone fails without the suppression). Playbooks pause 3 s after the jump so such a glitch has time to show. The fake world draws characters walking at 120 u/s on a virtual clock and has three puppet twins (slides on an idle animation, wrong weapon, T-pose), all caught. Thresholds are T7's first guesses and unmeasured.

## Impaired links (--chaos PROFILE)
Repairs are never ignored. Per step: digest mismatches/repairs may appear while traffic is in flight, but after settling the engine waits a quiet window (`quiesce_s`: lan 1, good_wifi 2, bad_wifi 6, mobile_tether 8, transatlantic 6, hell 20 s) and then requires every machine converged and 0 repairs during a second quiet window (kind `quiescence`). The run also has a repairs-per-minute budget per profile: `repair_budget_per_min` in `tools/chaos/profiles.json`, default 0 (strict); a number above 0 needs a `repair_budget_reason` next to it or the run refuses to start. `applyFailed`, `badMessages` and `rebases` fail immediately on every profile. T5 owns profiles.json: add the keys there when a budget is loosened.

## Tests and twins
Clean runs (11 playbooks, 2/3/4 players, laggy link), 28 twins that must fail, plus chaos twins (a dropped reliable message never retransmitted fails under bad_wifi; a late repair in the quiet window; repairs over budget; unexplained loosening) (`registry.TWINS`), `counter_noise` fails every playbook, every fake bug has a twin (checked), failure files for T1b (failure.json, snapshots, events, `flight.dump` result or the error), bot crash becomes a failed step, empty playbook is not a pass, plan resolution plus three breakages, scenario refs checked against `forms.json.gz`, live snapdiff comparer on the golden snapshots, adapter over T6a's fake bridge, CLI exit codes.

## Limits
- The fake proves the playbook logic and its checks, not the real console grammar or timing; those are UNVERIFIED until the first in-game run.
- Snapdiff in the real game runs on `shared` only, so per-player state (inventories, positions) is checked by playbook queries, not snapdiff.
