# T4b scenarios: instant "jump to scenario" for full-sync tests

Status 2026-10-20: code and tests done, game-free. Nothing was run in a game. Run the tests with
`python -I -X utf8 tools/scenarios/run_tests.py` (prints `RESULT: ALL OK`; also part of `python -I tools/test_all.py --quick`).

## Files
- `tools/scenarios/scenlib.py`: format, validator, command/expression builders, jump planner.
- `tools/scenarios/runner.py`: runs a scenario over bridge objects (duck-typed), keeps a per-instance state store.
- `tools/scenarios/scen.py`: CLI (`list`, `validate`, `plan`, `run`, `jump`).
- `tools/scenarios/fakegame.py`: in-memory fake of N instances sharing one world, with bug flags (tests only).
- `tools/scenarios/run_tests.py`: 48 unit tests plus the mutation twin.
- `tools/scenarios/proof.py`: in-game proof for the lead (not run by tests): `probe`, `jump`, `suite`.
- `scenarios/*.json`: the 10 starters.

## The scenario format (schema 1)
One JSON file per scenario, file name = `name`. Keys: `schema`, `name`, `title`, `description`, `players` (1 to 4), `tags`,
`start.save` (save name without .fos; `start.reload: true` forces a reload), `reset` (`{"group":"goodsprings"}` uses the L7 sweep
config's reset ids, and/or `{"quests":[...]}`), `forms`, `setup` (steps run during the jump), `steps` (the test).

`forms` is a glossary `alias -> {ref, edid, sig}`. Steps use `"$alias"`; console text uses `{alias}`. Every ref is checked against
`out/questdata/forms.json.gz`: the ref must exist, the EditorID and the record type must match, the record type must fit the
field (a `moveto` target must be a placed ref, a `spawn` base must be an NPC/creature/item, and so on), quest stages must be
stages of that quest in `quests.json`, quest variables must be script variables of that quest, and a raw 8-digit FormID in a
console line is refused. Runtime ids are computed from `tools/questdata/loadorder.json` (DLC prefix included).

Step ops: `setstage setvar moveto spawn additem removeitem kill resurrect enable disable lock unlock activate recruit console wait
save load vats_hit fast_travel leave join check`. Every step has an `id`; `on` is `host` (default for actions), `guest`, `all`
(default for checks) or a player index; `parallel: true` runs the same step on several players at the same moment (threads).
Checks: `kind` is one of `stage stage_done item_count dead disabled locked in_cell distance av teammate spawn_count owner expr`,
with `cmp` (`== != < <= > >= same`), `value`, `agg` (`sum min max` over the players), `tol`, `timeout` (seconds, polled until it
holds). `cmp: same` means every player reports the same number (convergence without knowing the number).

## Jump to scenario in seconds
`runner.Runner.jump()` follows L7's reload-every rule. The save is reloaded only when: nothing is loaded on that instance yet
(the key is instance plus pool generation, so a restarted game counts as empty); the loaded save differs from `start.save`; the
previous scenario failed or errored; the scenario sets `start.reload`; or the previous scenario changed a world object this one
uses. Otherwise it is in place: `test.quest.reset` over the scenario's quests plus everything the previous scenario touched, then
the `setup` steps. What a scenario "dirties" is computed from its steps (kill, enable/disable, lock, item changes, spawns,
console lines; quest stages whose script changes the world, from L3's `effects_static`; an unknown stage effect or a `load` or
raw console line counts as "unnameable" and forces a reload next). `scen.py plan NAME --prev OTHER` shows the decision.
Expected cost: in place is a few bridge calls and console lines (the test measures that it stays under 20 calls on the fake);
the real time is unknown until `proof.py jump` is run. A reload is a save load, which L7 measured at the sweep's usual load time.

## The 10 starters plus 3 playbook scenarios (13 files; INFO and DIAL aliases resolve against dials/infos.jsonl.gz; the API for playbooks is `tools/scenarios/__init__.py`) (all `players: 2`; `start.save` is `autosave`)
| name | what it checks | needs (not in the bridge yet) |
|---|---|---|
| goodsprings_fight | 3 Powder Gangers, kills by host and guest, dead state agrees | none |
| loot_race | both grab one world item at once, total copies stay 1 | none |
| spawn_once | one spawn appears once everywhere and does not double | `dev.countRefs` |
| ownership_handoff | host walks to Novac, gang owner becomes the guest | `actor_owner` (`coop.owner`) |
| door_terminal_container_matrix | lock/unlock door, gate, terminal; add/remove items in a container | none |
| companion_recruit | Boone recruited, teammate on both, stays near host | none |
| quest_stage_sync | VCG02 stages 10/20/100 set from both sides | none |
| save_load_rejoin | save, leave, advance, rejoin, load | `session_leave`, `session_join` (`coop.leave`, `coop.join`) |
| vats_hit | one VATS head hit, same damage everywhere | `vats_hit` (`bot.vats` (T6a spec)) |
| dialogue_spectator | guest talks to Sunny (VCG02), end script sets stage 25 everywhere | `dialogue` (`bot.say`, T6a spec) |
| pnr_vote | Moore's confirm: host declines once, then confirms, guest answers Ready on the consent prompt, VMQ05 reaches 100 everywhere | `message_box` (`bot.menu.press`, name provisional), `party_consent` (`bot.consent`, name provisional) |
| pnr_vote_guest_declines | host confirms, guest answers Not yet, VMQ05 stays 70 everywhere | same two |
| goodsprings_opening | VCG01 at stage 0 on every machine | none |
| fast_travel_tether | host fast travels, guest is pulled | `fast_travel` (`test.fasttravel`) |

A step whose capability is missing is reported `blocked` (never a pass), and every later step is blocked too, so the scenario
ends as `partial`. The method names above are proposals; they do not exist in the bridge. Capabilities live in
`scenlib.CAPABILITIES`; pass `capabilities=` to `Runner` once a method exists.

## Final names (from T6a, all behind nvcoop.ini [dev] bots=1, implemented but never run in game): `bot.say {info}`, `bot.vats {ref}` (limb unsupported, so not sent), `bot.menu.press {button}`, `bot.consent {answer}`, `test.fasttravel {marker}` (a teleport across cells, reply `genuine:false`, not the real fast-travel call), `dev.countRefs {base}` (spawn_once; counts the base over the loaded world). These count as present. Still proposed, not in the bridge: `coop.owner`, `coop.leave`, `coop.join` (ownership_handoff and save_load_rejoin stay `partial`). Older note: vats and dialogue follow T6a's docs/tools/T6a-actions.md; the message-box press and the consent answer do not exist in T6a's spec yet, so their names are provisional and were requested from T6a-actions (all method names live in `scenlib.CAPABILITIES`, one place to change).

## Unknowns (to settle with `proof.py probe`)
- `ref.GetDead`, `ref.GetLocked`, `ref.GetItemCount`, `ref.GetAV`, `ref.GetDistance` and the other `hex.Function` forms through
  `nv.eval` (the bridge's printc wrapper) are not verified in game; only `GetStage`-style forms are.
- `GetNumRefs` as the spawn counter is unverified and may not exist with that signature. If it fails, `spawn_once` reports `error`.
- Console forms used by actions (`.lock N`, `.unlock`, `.activate player 1`, `.placeatme`, `.setplayerteammate 1`, `.setav Health`)
  are the usual console syntax but have not been run through the bridge here.
- Two-player scenarios assume the host and guest are already connected as one co-op session; the runner does not connect them.
  The format of the `coop.status` reply is unknown, so the runner does not gate on it.
- The start save name `autosave` is the sweep's default; whether that save sits in Goodsprings in the pool instances is unknown.
- How party consent is given for the fast-travel tether is not modeled.

## Lease API (agreed with T4a-pool)
Uses `tools/pool/client.py`: `with lease("scenarios", n=players) as L:` then `L.bridge(i)` for each `L.instances`; `scen.py run`
and `scen.py jump` do this. State key is `instance@generation` (generation from the lease), stored in `out/scenarios/state.json`.

## Tests and twin
48 tests: all 10 files valid; every form resolves with matching EditorID and type; load-order prefixes; 20 validator rejections
(invented id, wrong EditorID, wrong type, unnormalized ref, unknown alias, bad stage/variable, wrong record type for a field,
duplicate id, raw FormID in console, bad save name, no check, and a corrupted ref in each shipped file); translation of
steps to exact console lines; comparison table; jump rules; runner against the fake world, including injected bugs (guest does
not see changes, duplicated loot, double spawn, missing ownership handoff), steps landing on the named player, blocked steps,
console parse errors, invalid scenarios never reaching the game, and in-place versus reload jumps.
Twin: the suite is re-run under five mutants (`SCEN_MUTANT=` `noresolve`, `noreload`, `nodirty`, `passall`, `firsthost`); each must
make the suite fail, and the run prints `caught` for each.
