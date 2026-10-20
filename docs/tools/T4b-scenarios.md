# T4b scenarios: instant "jump to scenario" for full-sync tests

Status 2026-10-20: code and tests done, game-free. Nothing was run in a game. Run the tests with
`python -I -X utf8 tools/scenarios/run_tests.py` (prints `RESULT: ALL OK`; also part of `python -I tools/test_all.py --quick`).

## Files
- `tools/scenarios/scenlib.py`: format, validator, command/expression builders, jump planner.
- `tools/scenarios/runner.py`: runs a scenario over bridge objects (duck-typed), keeps a state store keyed by game process.
- `tools/scenarios/scen.py`: CLI (`list`, `validate`, `plan`, `run`, `jump`).
- `tools/scenarios/fakegame.py`: in-memory fake of N instances sharing one world, with bug flags (tests only).
- `tools/scenarios/run_tests.py`: 63 unit tests plus the mutation twin.
- `tools/scenarios/proof.py`: in-game proof for the lead (not run by tests in a game; the tests drive its `jump` and `suite`
  modes on the fake pool and fake game only to check the state keys): `probe`, `jump`, `suite`.
- `scenarios/*.json`: 14 scenario files, the 10 starters plus 4 added for the playbooks (table below). The files in
  `scenarios/simfuzz/` are T2b's saved simfuzz repros (docs/tools/T2b-fuzz.md); the scenario tool reads only
  `scenarios/*.json`.

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

## Lines for a reference: exec {cmd, ref} (VERIFIED in game, 2026-10-21)
The game's console does not accept `<hexFormID>.Command`. On FalloutNV.exe 1.4.0.525, `00103E61.GetOpenState` got
`Script command "00103E61.GetOpenState" not found`, and the EditorID of a non-persistent ref got `Invalid reference ...`.
Only `player.` works as a prefix. So the scenario tool never sends `<hex>.Func`:
- `scenlib.step_actions` gives `("exec", line)` for a line with no reference or with `player.`, and
  `("exec", line, ref)` for a line on another reference, for example `lock` gives `("exec", "lock 50", "00103E61")`. The
  runner sends it as `nv.exec(line, ref=ref)`: the bridge runs the line with that reference as the console's calling
  reference (docs/CONTRACTS.md, `exec{cmd, ref?}`).
- `scenlib.check_expr` returns a seventh item, the calling reference: `dead` gives `("GetDead", ..., "00104C70")` and the
  runner evaluates `nv.eval("GetDead", ref="00104C70")`. `player` stays the `player.` prefix.
- A `console` step or an `expr` check may name one reference, and only as the first word: `{gang1}.setav Health 100` runs as
  `exec("setav Health 100", ref=<gang1>)` (`console_ref.split_ref_line`). The validator refuses a `{alias}.Func` that is
  not the first word, or a second one, because the console cannot run it.
- fakegame.py behaves like the game: a `<hex>.Func` line fails (`Script command ... not found`), and `exec`/`eval` with
  `ref` act on that reference.
- Twins: `SCEN_MUTANT=hexprefix` (the tool builds `<hex>.Func` lines again), `SCEN_MUTANT=refline` (the validator accepts a
  reference call that is not first), and the fake world flag `exec_ref_ignored` (the host drops the ref). Each must fail.

## Jump to scenario in seconds
`runner.Runner.jump()` follows L7's reload-every rule. The save is reloaded only when: nothing is loaded on that instance yet
(the state key names the game process, see "State keys" below, so a new game counts as empty); the loaded save differs from `start.save`; the
previous scenario failed or errored; the scenario sets `start.reload`; or the previous scenario changed a world object this one
uses. Otherwise it is in place: `test.quest.reset` over the scenario's quests plus everything the previous scenario touched, then
the `setup` steps. What a scenario "dirties" is computed from its steps (kill, enable/disable, lock, item changes, spawns,
console lines; quest stages whose script changes the world, from L3's `effects_static`; an unknown stage effect or a `load` or
raw console line counts as "unnameable" and forces a reload next). `scen.py plan NAME --prev OTHER` shows the decision.
Expected cost: in place is a few bridge calls and console lines (the test measures that it stays under 20 calls on the fake);
the real time is unknown until `proof.py jump` is run. A reload is a save load, which L7 measured at the sweep's usual load time.

## State keys
The runner remembers, per game process, which save is loaded and what the last scenario dirtied. `scen.py run` and
`scen.py jump` keep this in `out/scenarios/state.json`; `proof.py` keeps it in `out/scenarios/proof_state.json`.
- The key is `instance@boot`. `boot` is the id that the pool gives each game process at each successful start: the game
  pid plus a random token (`L.boot(i)` in `tools/pool/client.py`, `boot` in `pool.py status`). A new game process always
  gets a new id, also after `pool down` and `pool up`.
- Earlier keys were wrong. `scen.py` and `proof.py suite` used `instance@generation`, and the generation starts again at
  1 on every pool start. `proof.py jump` used the fixed key `0@proof`. So a run after a pool restart found the state of
  the earlier game, skipped the save load, and ran the checks against the main menu or a different world.
- The same live game keeps its key from lease to lease, so a second `scen.py run` in the same pool still jumps in place.
  For `proof.py jump` this means that the first jump of a second run in the same pool is in place too. To time a
  reload again, restart the pool or delete `out/scenarios/proof_state.json`.
- A lease without a boot id (a pool older than this change) gets keys that are new for that run, so its first jump
  reloads. A `Runner` made without `keys` keeps its state private to that `Runner` object.
- `runner.for_lease(L, path)` builds the runner for a pool lease with these keys; `StateStore.lease_keys(L)` builds the
  keys alone.
- Limit: the store assumes that nothing else changed the world of that game process between two of your leases (another
  lessee of the same warm instance, or a person at the console). If that can have happened, use `scen.py run --force-reload`
  (or `jump --force-reload`); for `proof.py`, delete `out/scenarios/proof_state.json`.

Locking: two `scen.py` (or two `proof.py`) processes can use the same state file at the same time, with leases on
different instances. Every read and every write of the file holds an exclusive lock on the sidecar file `<file>.lock`
(`msvcrt.locking` on Windows, `fcntl.flock` elsewhere). A write reads the file, changes only its own keys, writes a
temporary file in the same folder and replaces the state file with `os.replace`, so the state file is never partial.
Each plan reads the file again, so it sees what the other process wrote. A lock that stays held for 30 s stops the run
with `runner.StateLockTimeout`, which names the lock file. The `.lock` file stays in the folder; it is empty.

## The 10 starters plus 4 playbook scenarios (14 files; INFO and DIAL aliases resolve against dials/infos.jsonl.gz; the API for playbooks is `tools/scenarios/__init__.py`) (all `players: 2`; `start.save` is `autosave`)
The 4 added for the playbooks are dialogue_spectator, pnr_vote, pnr_vote_guest_declines and goodsprings_opening.

| name | what it checks | needs (capability, bridge method; status below) |
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
ends as `partial`. Capabilities and their method names live in `scenlib.CAPABILITIES` (one place to change); the runner
uses the capabilities marked `present` there unless you pass `capabilities=` to `Runner`.

## Capability status (checked 2026-10-20 against `scenlib.CAPABILITIES` and `plugin/coop/coop_module.cpp`)
- Present, from T6a, all behind nvcoop.ini [dev] bots=1, implemented but never run in game: `bot.say {info}`,
  `bot.vats {ref}` (limb unsupported, so not sent), `bot.menu.press {button}`, `bot.consent {answer}`,
  `test.fasttravel {marker}` (a teleport across cells, reply `genuine:false`, not the real fast-travel call),
  `dev.countRefs {base}` (spawn_once; counts the base over the loaded world).
- `coop.leave` and `coop.join` are built. `plugin/coop/coop_module.cpp` registers both in `OnLoad`. `coop.leave {}`
  drops this player out of the session and replies `{"ok":true}`. `coop.join {host?, port?}` builds a new session with the
  configured or the given address and replies `{"ok":true}`. Both fail with `coop not running` when the co-op runtime is
  not running. Neither was run through the scenario runner in a game. `scenlib.CAPABILITIES` still marks `session_leave`
  and `session_join` as `proposed`, so `save_load_rejoin` stays `partial` until an in-game run proves them and they are
  changed to `present`.
- `coop.owner` is registered but not implemented. It always replies
  `{"unsupported":true,"reason":"actor-ownership service is not in the plugin yet"}`, with no `owner` field.
  `actor_owner` stays `proposed`, so `ownership_handoff` stays `partial`. By code reading, if `actor_owner` were marked
  present against this plugin, the owner check would report `error` (`KeyError: 'owner'`), not a pass.

## Unknowns (to settle with `proof.py probe`)
- `GetDead`, `GetLocked`, `GetItemCount`, `GetAV`, `GetDistance` and the other reference functions through `nv.eval(expr, ref=...)`
  (the bridge's printc wrapper, run with the reference as the calling reference) are not verified in game; only
  `GetStage`-style forms are. The `<hex>.Function` form is verified NOT to work (see "Lines for a reference").
- `GetNumRefs` as the spawn counter is unverified and may not exist with that signature. If it fails, `spawn_once` reports `error`.
- Console forms used by actions (`lock N`, `unlock`, `activate player 1`, `placeatme`, `setplayerteammate 1`, `setav Health`, each
  with the reference in exec's `ref`) are the usual console syntax but have not been run through the bridge here.
- Two-player scenarios assume the host and guest are already connected as one co-op session; the runner does not connect them.
  The format of the `coop.status` reply is unknown, so the runner does not gate on it.
- The start save name `autosave` is the sweep's default; whether that save sits in Goodsprings in the pool instances is unknown.
- How party consent is given for the fast-travel tether is not modeled.

## Lease API (agreed with T4a-pool)
Uses `tools/pool/client.py`: `with lease("scenarios", n=players) as L:` then `L.bridge(i)` for each `L.instances`; `scen.py run`
and `scen.py jump` do this through `runner.for_lease`. The state key is `instance@boot` (`L.boot(i)`, the id of the game
process), stored in `out/scenarios/state.json`. See "State keys".

## Tests and twin
68 tests: all 14 files valid; every form resolves with matching EditorID and type; load-order prefixes; validator rejections
(21 tests in `ValidatorRejects`: invented id, wrong EditorID, wrong type, unnormalized ref, unknown alias, bad stage/variable,
wrong record type for a field, duplicate id, raw FormID in console, bad save name, no check, and a corrupted ref in each shipped
file); translation of steps to exact console lines and calling references (no shipped step or check sends `<hex>.Func`);
the game-like fake console; the proof probes; comparison table; jump rules; runner against the fake world, including
injected bugs (guest does not see changes, duplicated loot, double spawn, missing ownership handoff), steps landing on the named
player, blocked steps, console parse errors, invalid scenarios never reaching the game, and in-place versus reload jumps.
State keys (6 tests in `GameProcessKeys`, on the real pool policy `tools/pool/core.py` with its fake backend, the real
`client.Lease` and the fake game): after a pool restart, where the old `instance@generation` key repeats, a new game reloads
the save; the same live game still jumps in place on the next lease; `proof.py jump`, `proof.py suite` and `scen.py jump` use
these keys; a lease without a boot id and a `Runner` without keys never reuse old state.
Shared state file (5 tests in `SharedStateFile`): two stores loaded before each other's put keep both keys, and the older
copy of a key in one store does not replace the newer key on disk; a plan sees a dirty state that another store wrote
after this store was made, and reloads; a failure between the write and the replace leaves the old valid file and no
temporary file; a held lock gives `StateLockTimeout`; two Python processes that each put 200 different keys end with all
400 keys.
Twin: the suite is re-run under nine mutants (`SCEN_MUTANT=` `noresolve`, `noreload`, `nodirty`, `passall`, `firsthost`,
`oldkey`, `wholedict`, `noreread`, `inplace`); each must make the suite fail, and the run prints `caught` for each.
`oldkey` brings back the `instance@generation` key; all 6 `GameProcessKeys` tests fail under it. `wholedict` brings back
the write of the whole in-memory dict (lost update), `noreread` plans from the state read at start, and `inplace` writes
into the state file itself; each fails its `SharedStateFile` test. The pool suite has its own twins for the boot id
(`gen_boot`, `stale_boot`, see docs/tools/T4a-pool.md).
