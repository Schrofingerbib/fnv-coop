# T2b-fuzz: co-op fuzzer and invariant oracles

Code: `synccore/fuzz/` (C++), `tools/simfuzz/` (Python driver and tests), saved repros in `scenarios/simfuzz/`.

## Run it
- Fuzz: `python -I tools/simfuzz/run.py --minutes 2 --players 4 [--profile bad_wifi|hell|...] [--target em|mini] [--bug NAME]`
  (builds on demand into `build/T2b-fuzz`; `em` is the default target, `--target mini` selects the small standalone model;
  exit 0 and `RESULT: ALL OK` when every invariant held). About 1 session per second with `em` at 4 players and 15 s of virtual time, 90 per second with `mini`.
- Tests (with mutation twins): `python -I tools/simfuzz/run_tests.py` prints `ALL OK`. Also in `tools/test_all.py`.
- Replay a scenario: `fnvcoop_simfuzz --replay file.json` (the target comes from the file unless `--target` is given; exit 1 and `VIOLATION <name> <detail>` lines when broken).
- Shrink a scenario file: `fnvcoop_simfuzz --shrink file.json [--bug NAME ...] [--save out.json]` (ddmin over ops, then link, profile, machines, duration). Extra `--bug`s are
  injected first, so a repro that needs a fault saves it in its `mutations`.
- `--fixture BUG[,BUG2]` finds a failing session for one fault (or several together) and writes `regress_<bug>.json`; add `--profile hell` for slow-link bugs. A session only counts when it passes
  without the fault (one that also fails on the real code is a different bug), and the shrinker keeps that rule at every step. `--shrink` does the same for a scenario with `mutations`.
  Prefer a plain link for a saved repro: under a chaos profile the bandwidth queue makes every packet's fate depend on the sizes of all packets before it, so any change to a message
  (a new field) moves the whole timeline and a profile repro stops reproducing without any code having regressed (that is what happened to the first `regress_new_owner_no_wait.json`).
- On a failure the CLI shrinks and writes `<out>/fail_seedN_<inv>.min.scenario.json` plus `.trace.json` and one `.nvfr` per machine.
- Build: `synccore\fuzz\build.bat` (em is on by default; `FUZZ_EM=0` builds the mini target only into `build/T2b-fuzz-mini`).

## What it checks
converge (views and digests equal on all connected machines after the link heals), double_apply, multi_owner (one owner per epoch, epochs never go back, and
no actor AI-driven on two machines at any step), stale_applied (old epoch, old sequence, non-owner, request from before a host load), echo_adopted, dup_thing (same identity twice),
repairs_after_quiescence, not_quiescent. Scenarios mix moves, hits, loot races, spawns, doors, join/leave, host save/load, link changes, stale-message injection and pickup races.

Pickup races: for every 1.5 s of scenario time the generator adds a burst where two or three machines reach for the same item within 25 ms (from a separate
random stream, so the older ops of a seed are unchanged). Without them the fault `pickup_no_arbitration` (the host grants every claim) was caught by none of 60 seeds,
because random pickups almost never collide and the item-conservation oracle is off after any leave or host load. The model counts a second grant of one world item
in `doubleApplied`, which the `double_apply` oracle reports; 5 of the first 6 seeds catch it now.

## Ops that exist for em
`generate(seed, machines, duration, "em")` (the CLI passes `--target`) adds ops from a separate random stream; `mini` ignores them and generates exactly what it always did.
They exist because a bug class of the model is invisible without them: once the real bugs were fixed, 14 of the 22 em twins were no longer caught by any of 60 seeds.
- `corrupt`: a silent change of one guest's copy (an object flag, a container count, an actor drifting 3 units and 3 hp, a ground item flag). Only the digest repair can find it:
  catches `heal_disabled` and `digest_buckets_positions`.
- `recruit` (host makes a player an actor's companion): catches `own_follower_ignored` through the end-of-session check `companion_not_with_recruiter`.
- `container` races (two machines open the same container together, then take, then close): `container_no_lock`.
- strikes (`hit` with c=1 story NPC / c=2 other actor; an attacker steps next to the victim, then three hits of 60): `named_killable`, `loot_per_machine`.
- `stale` with d=1 (replay a request that changes state), d=3 (the oldest, i.e. from before a host load, after a save/load pair), d=4 (the newest drop request, after a guest picks up and
  drops), d=5 (an old ownership broadcast, late in the run): `no_session_epoch`, `drop_no_idempotency`, `own_adopt_echo`.
- a slow-link window (`link` 900 to 1500 ms for 3.5 s) with a guest pinned in a cell and the host walking in: the host commits the handoff by timeout and starts driving before the guest
  has heard of it: `new_owner_no_wait`.
The model reports what a switched-off rule did through its breach oracles (T2a-engine.md); a breach name is the invariant name (`forged_hit_applied`, `non_owner_hit_applied`,
`pickup_loser_kept_item`, `loot_rolled_again`, `container_lock_ignored`, `story_npc_killed`, `stale_session_applied`, `owner_flapped`, `handoff_without_ack`).

## Targets
- `mini`: own small host-authoritative world and protocol, with 11 injectable bug classes. Each has a twin test that must be caught
  and a saved shrunk repro (`regress_<bug>.json`: breaks its invariant with the bug, passes without it).
- `em` (default for `tools/simfuzz/run.py`): adapter over T2a's `synccore/enginemodel` Cluster (`target_em.cpp`). 24 bug classes (`--list-bugs --target em`): T2a's 22 fault
  twins plus `new_owner_no_wait` and `digest_buckets_positions`. Every one is caught by a 60-seed sweep of 8 s sessions (`run_tests.py` checks each; the slowest are `new_owner_no_wait`
  at seed 29 and `own_follower_ignored` at seed 7). `owner_keeps_publishing` also switches on `acceptStaleEpoch`, as T2a's unit twin does (the bug only bites when a receiver applies old-epoch updates). It also reports Cluster's own oracle findings. Three more faults are accepted by `--bug` but hidden from
  `--list-bugs` because no fuzz oracle sees them alone: `created_key_is_player`, `digest_no_seq` and `join_no_tombstones` (their twins are unit scenarios in `synccore/enginemodel/tests`, see T2a-engine.md).
  Saved em repros: `regress_digest_buckets_positions.json` (two ops) and `regress_new_owner_no_wait.json` (three ops on a plain link: the slow-link window, then the host walks into the guest's cell; it reproduces on the pre-combat code too); `fuzz_tests.cpp` replays them with the fault (must break the named
  invariant) and without it (must pass).

## Link model and traces
- `--profile NAME` uses T5's `chaos::Impairment` (burst loss, reorder, jitter, duplicate, corrupt, bandwidth, all profile fields) for both targets.
  `mini` applies it in its own queue; `em` installs it through the `InMemoryNetwork::setShaper` hook (synccore/include/fnvcoop/transport.h, small shared edit).
  `SetLink` ops are ignored when a profile is set (the profile owns the link).
- The `em` target records through T1a's `EngineFlightObserver` (one recorder path); `mini` still writes `.nvfr` from `runner.cpp`.
  Chain proven: `fnvcoop_simfuzz --target em --bug no_dedupe` -> `fail_*.min.m<N>.nvfr` -> `python -I tools/flight/flight.py diverge --check` finds the divergence
  (covered by `run_tests.py`).
- `fnvcoop_simfuzz --replay` is the replay entry point; there is no separate `fnvcoop_replay` binary (see flight-format.md 6.1).

## Status (2026-10-20, npc-bugs)
Clean `em` runs hold. `python -I tools/simfuzz/run.py --minutes 2 --players 4 --target em`, plain, `--profile bad_wifi` and `--profile hell`: 76, 72 and 70 sessions with the em extras
(118, 128 and 126 before they were added), 0 violations each; 150 more 2 to 4 player sessions (seeds 9000 to 9149) were clean too. Before the fixes about 4% of 4-machine sessions failed,
and `hell` found AI driven on two machines. The four root causes are in T2a-engine.md. The only things proven are the
model and the fuzzer; nothing was run in the game.

## Open
- `digest_no_seq` and `created_key_is_player` have unit twins only (they are accepted by `--bug` but not listed). A fuzz-level repro of the original "hp95 on the host, hp100 on the guest" failure needed both faults at once and
  stopped reproducing once digests became exact with per-key stability, so no saved fuzz scenario exists for them.
- A full `--target em` sweep is about 40 s per 60 seeds of 8 s; the em twin test in `run_tests.py` stops each twin at its first catch. `python -I tools/simfuzz/run_tests.py` takes about 9 minutes
  (it also builds and runs the engine model tests, `fnvcoop_engine_tests`, groups own, world, engine and twin).
- Not proven in the game; everything here is the model and the fuzzer.
