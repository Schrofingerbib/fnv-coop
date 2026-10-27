# lab.py: every co-op mechanic checked in about 20 minutes

Private doc. Contract: `docs/LAB.md`. Code: `tools/lab/` (the plugin generator is `tools/lab/esp/`, another tool). It ran in a game on the second PC from 2026-10-27 (what that found is under "Uncertain until it runs in a game" and "Measured in game"); the whole
runner is tested on a fake two-copy world (`python -I tools/lab/run_tests.py` prints ALL OK).

## Run it

```
python -I tools/lab/lab.py run --pairs 3                      # all rooms over 3 pairs of copies, spawn first on every pair
python -I tools/lab/lab.py run --pairs 1 --rooms fight,loot   # some rooms (spawn always runs first, it puts the pair into the lab)
python -I tools/lab/lab.py smoke                              # spawn room only, about 3 minutes
python -I tools/lab/lab.py summary out/lab/<stamp>            # print a finished run's summary again
python -I tools/lab/lab.py down                               # stop the copies a run left running (only ones this tool started)
```

Options: `--budget-min 20` (the rooms of one pair together; counted from its first room, so boot and the save load are not in it; `wall_seconds` in results.json has the total), `--room-budget 120` (seconds per room, a hard time box; spawn has its own larger box of 240 s, because it also loads the save and enters the lab: `lab_rooms.SPAWN_BOX_S`), `--layout out/lab/layout.json`, `--confirm K` (rooms that passed
run K more times, each on a fresh load; a failed room is never rerun), `--param room.key=value` (change a threshold for one run), `--keep` (leave the copies
running), `--fake` (the game-free run) with `--bug NAME` (one fake bug on, the matching room must fail; names in `tools/lab/labfake.py`).

Needs: `out/lab/layout.json` and `out/lab/NVCoopLab.esp` from `tools/lab/esp`, a built `out/L1*/nvcoop.dll`, Steam running, the owner not playing (the same
guards as `prove.py`, which this tool reuses: slot lease owner `lab`, RAM guard, `run.py provision`, boot, saves, crash watch). The lab plugin goes only
into the test copies: before each boot the runner calls `python -I tools/lab/esp/install_lab.py --instance-dir <copy>`; without that script the pair is BLOCKED with
that reason.

On the second PC: `tools/bench/remote/task.ps1 -Script C:/fnv/fnv-coop/tools/bench/remote/lab_run.ps1`, which runs 3 pairs and writes `C:\fnv\lab_done.txt` when it ends
(`lab_out.txt` holds the summary, `lab_err.txt` the progress).

## What a run does

1. Per pair (instances 2k and 2k+1): provision, install the lab plugin, boot once, wait until both copies serve requests.
2. The spawn room first: load the save (`--save`, default `nvcoop_autosave_ready`) once on both copies, wait until the session is synced, `coc NVCoopLab` on both, then its checks.
3. The other rooms in order, each from its stand marker, each in a function with a hard time box. The first failed check ends the room and records what differed.
4. `out/lab/<stamp>/results.json` and `summary.md`, then the comparison with the newest earlier run in `out/lab`.

Rooms are split over the pairs with the most expensive first (spawn is on every pair, so a bad pair shows up as its own spawn row).

## The rooms

Every check compares the same placed ref (same id) on both copies, never counts.

| room | what it checks | likely source |
|---|---|---|
| spawn | both copies in the lab cell, connected (host sees the peer joined, client joined and transport up), each shows the other's puppet near him, 20 s idle with at least one digest ok and no mismatch or repair | plugin/coop, plugin/avatar, plugin/areadigest |
| walk | host then client walks four waypoints; the other copy's puppet: error p95 and max, teleports, catch-up time after he stops | plugin/avatar |
| fight | 3 hostiles; host shoots one dead, client shoots one dead; each death shows on the other copy within the latency limit, deaths and the survivor's health agree, no corpse gets up for 8 s | plugin/combat, plugin/actors |
| loot | host and client take from the container, pick up loose items, race for one: container and world agree, nobody gets a duplicate, totals match | plugin/loot, plugin/intents |
| door | a locked door stays shut for a try, the client unlocks it through the terminal, host and client each open and the other closes: lock and open state the same on both | plugin/hooks_world, plugin/intents |
| npc | the client stands next to the NPC and the host far away in the same cell: the host keeps it (an eligible host always owns NPCs, synccore pickOwner). The host `coc`s into GSDocMitchellHouse: ownership moves to the client with a new epoch and both copies agree. The host `coc`s back to the lab, stands at the npc stand marker: ownership returns to it, new epoch. No position snap, the copies agree while both have the NPC loaded (the host's copy is not sampled while it is away), it patrols | plugin/actors, synccore ownership |
| vats | real-time VATS by host and client on their own target: health after every shot agrees, targets die on both | plugin/vats, plugin/combat |
| quest | host sets stage 10 and a variable, client sees them; client sets stage 20, host agrees; host sets 30 and it does not roll back | plugin/hooks, plugin/snapshot |

Every room ends with the clean rule: no digest repair and no area heal during the room (as in `prove.py`).

## Reading the results

`summary.md` (under 5 KB): a header line with the verdict and build, one table (room, pair, verdict, class, seconds, checks passed, first failed check), then 3 to 6 lines per
failed room (the check, expected and actual per machine, the first digest key that differed with both values, counters that moved, the plugin folders to look in), then
"vs previous": rooms that were fixed or regressed, and numbers (walk error, kill latency) that moved by a quarter or more.

`results.json`: `rooms[]` with `verdict` PASS, FAIL, BLOCKED or TIMEOUT; `class` SYNC (the copies disagree), CRASH (a copy exited), ENV (the PC or load, requeue), TEST
(the lab or the layout is wrong), BUILD (a bridge method is missing); every check with `expected`, `actual`, `secs`; `first_divergence`, `counters_moved`, `digests_moved`, `metrics`.
Exit code: 0 all PASS, 1 FAIL or TIMEOUT or CRASH, 3 BLOCKED (layout, plugin, build), 4 ENV, 2 bad arguments.

## Measured in game (second PC, 2026-10-27)

- Failure texts are kept at 400 characters (`actual`) and 200 (`expected`) in results.json; a tight summary.md cuts only the tail of the got clause.
- `bot.look` does nothing on the CLIENT copy (reply ok, the angle reads back unchanged); the console `SetAngle` works on both. `LiveMachine` sets `bot.look_via_console`, so a walk steers with the console line.
- This build has no `RemoveItemTarget` (the console answers ok:true and prints "not found" in the output). `bot.take` then does `RemoveItem` on the source and `AddItem` on the destination.
- A guest's console `RemoveItem` on a container changes nothing (blocked on purpose), so the lab does not take from the container as the guest: `Machine.can_take()` says why and the check is skipped. It needs a plugin/bots method that clicks the container menu.
- A picked-up world item shows the deleted flag (0x20) in `ref.info`, not the disabled one: both count as gone.
- The fight room enables one hostile at a time (all three charge the players, and by the second shooter's turn one stood 60 units away, inside aim_at's 100 units); a shooter that is too close steps back to the stand marker.
- The lab terminal is the stock `DefaultUnlockTermWALL` (unlocked flag set). The DESK0VeryEasy one opened the hacking menu. It has ONE entry, "Disengage Lock", with the stock result script `ref myLink / set myLink to getLinkedRef / myLink.unlock`; the lab provides what that needs (the terminal links to the door with an XLKR), the generator is unchanged.
- The door check failed because `dev.terminal` walked the terminal menu's list from the wrong node (the ListBox embeds its tList head at +4; the code took the value there for a node address): no entry text was readable and the click got a vtable as its tile. Fixed in `plugin/bots` (`terminalEntries`); measured afterwards on the host copy: entries `['Disengage Lock']`, the click unlocks the door within a second.
- A first-use hint box (TutorialMenu 0x423) opens over a terminal, VATS and lockpicking and eats the input. `bot.menu.close {kind: "tutorial"|"any"}` now closes it with the engine's own close (0x7E8D60); `Bot.open`, `Bot.use_terminal` and the input pre-flight close it first.
- Door room: a guest's terminal click does not run the result script on its own copy; the TerminalEntryRun hook forwards it as an intent and the host runs it (client clicked: host showed the door unlocked within 1 s, the client's copy stayed locked 15 s). The control check therefore accepts the unlock on either copy; "unlocked on both copies" is the SYNC check and fails today (host unlocked, client still locked): a lock-state sync gap, plugin/hooks_world or plugin/intents.
- VATS room: the two geckos are enabled one at a time and weakened to 30 health first (the host is the owner). Enabled together they charged the host and were on top of it when its shot started (`vats_not_entered`); `LiveMachine.vats` also steps back when the target is closer than 150 units and retries when the target is not in view. Last run: shots 1 to 3 enter, play and exit with the health agreeing; shot 4 shows host -14 and client 1 (the run stopped at that check, so it is not known whether the client's gecko was dead: a dead copy shows the port's floor 1 for the owner's -14; plugin/combat now kills the victim of a natural lethal hit whose life state stayed alive, see docs/tools/combat-hooks.md; the check should compare health only while the target lives and then ask "dead on both copies").
- NPC room (ownership, rewritten): the eligible host always owns NPCs, so the room sends the host to GSDocMitchellHouse and back; it passes in game (29.9 s).
- The fight room's hostiles are weakened first: the host (owner) sets `SetAV Health 5` and the room waits until both copies show it, then the shooter fires (a 65 health gecko took 24 volleys and was not dead in the first run). Before every volley `line_of_fire` checks the distance (150..500 units) and `GetLOS`; a bad spot steps to the 350 units standoff.
- VATS opens only with the weapon drawn (the lab draws it) and the target in view; no limb can be chosen (`unsupported: limb selection in VATS is not implemented`). With the weapon drawn, `bot.vats` made the game's main thread stop answering the bridge (a hang, reproduced on two copies). Cause (a minidump of the hung copy, 2026-10-27): with real-time VATS on (`[vats] realtime=1`, the lab's setting) the main loop still runs the menu update for the open VATS menu, and that update waits with an INFINITE timeout for a worker-thread event (`0x713C70` -> `0x8C7A70`) that belongs to a paused world; nobody signals it in the VATS zoom (camera mode 3), CPU 0, window still answering. `plugin/vats` now redirects the one call that asks "are the workers started" (`0x713C8D`) and answers no while the VATS menu is open and the world runs. The bot job also had three smaller faults, all measured: an accept in the frame after the queue started no playback (it now waits 400 ms and repeats, at most 3 times), a hint box that opens over the accept swallows it (the job closes the box and accepts again), and the accept gate had no limit (now 16 answers and 3 s). Result on the host copy: enter, queue, accept, playback, done in 1.6 s with the target hit (65 to 55.8). `Bot.vats_target` also ends its wait with `main_thread_stuck` after three bridge answers "main thread did not service the request" in a row.
- The spawn room's idle digest window: it waits at most `spawn.digest_wait_s` (12 s) for the first digest verdict and ends the moment one is bad.

## Uncertain until it runs in a game

- Everything in `tools/lab/lab_live.py`: the bridge side was written from `tools/bots` and has only run against the bots' own fake bridge.
- The thresholds in `tools/lab/lab_rooms.py` `PARAMS` (puppet error 150 p95 and 300 max, kill latency 3 s, NPC error 150, handoff 25 s) are guesses; change one with `--param`.
- Layout keys: the runner reads the names the generator writes (`tools/lab/tests/layout_generated.json` is a copy, a test loads it): refs `wp*`, `enemy*`, `chest`, `loose*`,
  `door`, `terminal`, `npc`, `target*`, `quest`; `initially_disabled`; `info.container_items`, `info.loose_items` (same order as the loose refs), `info.var`. The fight and vats hostiles
  start disabled: the host enables them (`enable` through exec with the ref) when the room starts, both copies must show them, and the host disables them again when the room ends.
  Verified on the second PC by the generator's author: the plugin loads through `install_lab.py`, and `coc NVCoopLab` after `loadSave nvcoop_autosave_ready` lands on the lab floor.
  Not verified: the unlock terminal's entry index (assumed 0, `info.terminal_entry` overrides), and the loose items being in `loose_items` order.
- The npc room parks the away player at the origin of the room farthest from the NPC (the stand marker may be too close for an ownership handoff).
- Quest variables: no bridge method reads one. The runner tries NVSE expressions; when none evaluates the check is shown as skipped. Setting one needs `test.quest.setvar` (test helpers on).
- Shooting and VATS need the 9mm pistol and ammo (`FalloutNV.esm:0E3778`, `:08ED03`) and health 5000, set by console at the start of the room.
- A copy that boots without the plugin (started before it was installed) is BLOCKED with "run `lab.py down` and again". `provision` rewrites `Fallout.ini`, so the runner installs the plugin right before each boot.
- The bridge `screenshot` method is not used (it is not available on the second PC).
- `--confirm` and the hung-room watchdog (a room still running 20 s after its box) are only tested on the fake world.
