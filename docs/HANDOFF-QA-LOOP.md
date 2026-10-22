# Handoff: the in-game QA loop (2026-10-22)

Read this first, then `docs/tools/proof.md` (the test runner) and `docs/INGAME-TESTS.md` (what each test proves).

## The loop

1. A script runs every in-game test: `python -I tools/proof/prove.py queue --pairs 2 --runs 1 --reserve-gb 0.3`.
   It boots its own game copies (2 pairs = 4 copies, about 0.7 GB each), prints one JSON verdict line per test and appends it to
   `out/proof/ledger.jsonl`. Every failure leaves a `packet.md` (under 3 KB) next to its logs: read that first, not the raw logs.
2. A cheap model (Haiku) only runs the queue and writes the bug sheet. It never edits code.
3. Fixers (Sonnet) take the failures grouped by area, a few agents per wave, each in its own git worktree
   (`git worktree add -b wt/<name> ../fnv-coop-wt-<name> HEAD`, junction the untracked dirs they need: `third_party/NVSE`,
   `out/questdata`, `out/L5/re`, `out/L10/data`). Every fix ships with a regression test and a mutation twin.
4. Merge the branches, rebuild (`plugin\loader\build_split.cmd` then `tools\runner\build_l1.cmd`), retest only what failed:
   `prove.py queue --tests <comma list> --round <name> --pairs 2 --runs 1 --reserve-gb 0.3`. `--round` lets a restarted queue
   skip tests it already ran this round. Repeat until nothing fails, then one full run with the manifest's N (3 clean runs for
   two-copy tests) before anything counts as proven.

Before any game launch: Steam must be running AND signed in (a signed-out Steam makes every copy stop at "Application load error
5:0000065434"; prove.py now fails fast on that). Start Steam yourself, not from a sandboxed shell. Do not lock or switch the
Windows session during a run. Never stop a process the runner did not start.

## Where it stands

Latest verdict per test (59 tests, mostly from the build before the last three fixes):
6 PASS, 38 FAIL, 6 BLOCKED, 9 ENV (stopped mid-round, not real failures).

Passing: `actor_port_save_check`, `live_wiring_default_off`, `enemy_playbooks_offline`, `release_tools_offline`,
`release_cmd_scripts`, `launcher_loopback`.

The last build (merge `9289f4a`, all fixes below) was only partly retested (round r3 stopped after about 10 tests). First job for the
next person: rebuild, then rerun round r3 with the list the ledger gives (every test whose latest verdict is not PASS).

## Fixed on 2026-10-21/22 (in the source, most need the next retest to confirm in game)

- Host shares quest var changes even without baseline mode; a guest puts back quest vars its own object scripts write (534 vars,
  e.g. VMS16 nGangerDeathCount). This likely unblocks many story tests.
- Ownership: a Grant back to the new owner keeps its wait for the old owner's final state (issue #45); no restrain on dying actors;
  stand-in clones are deleted 750 ms after their last queued SetPos (the "angl" crash, issue #33); NPC inventory changes replicate
  from the owner.
- Sync check: RefScriptVar, weather and time scale are out of the digest; spawner counters and per-frame timers are per-machine
  (`plugin/snapshot/tools/volatile_scan.py`); SayOnceFlag applied on clients; quest running state is settled by the host (#25).
- Hot swap chains over xNVSE's MarkScriptEvent jump; cosave written (SetOpcodeBase); actor values read through the console's
  evaluator; ForceActorValue sends whole numbers; the client's avatar runtime learns its slot.
- Harness: test helpers on every copy, every pair gets the same build and the saves, readiness waits, scenario loads before tests
  that need a world, retest mode, RAM refusals no longer halt the queue.

## Open, in order

1. **Avatars**: they start now. Left: `puppet_two_copy` "avatar.glitch.stuck 1", `avatars_phases` puppet misses the 9mm pistol
   (0xE3778), `avatars_lifecycle` "slot 1 has a puppet: []", `puppet_loopback` slot 1 puppet stays Absent for 30 s.
2. **Bot walking**: in some runs the client's avatar never moves while the walk input runs (not a blocked path). After a loot
   failure all copies showed the pause menu open: check whether minimized copies pause and swallow bot input.
3. **actor_port / actor_port_two_copy**: the NPC 104C70 is not loaded after the scenario load (cell 000E1AA6); coop.owner does
   not know the NPC on both copies.
4. **Story steps 8 and 9**: every questline bot timed out on "quest X running on every machine". Retest after the quest var fix.
5. Remaining two-copy tests: rerun after the fixes above; any digest repair left is a real key to chase from the flight dumps.
6. `door_terminal_lock` needs a terminal in the same cell as the Goodsprings players (ScoreTerminalREF is at the Nellis camp).
7. Hot swap: every swap after the first is refused by the drain timeout (return address at core offset 0x37FE48).
8. Two real PCs (`lan_two_pc`) needs a second Windows PC with the game.

## Rules

Public text: no personal names or local paths, no em dashes, publish only as cranebeaker through
`tools/publish/export_public.py` (its scan must say CLEAN). Never drive the desktop with simulated input; game copies run
windowed and minimized; never capture the desktop (in-game frame capture only).
