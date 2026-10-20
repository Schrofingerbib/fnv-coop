# T2b-fuzz: co-op fuzzer and invariant oracles

Code: `synccore/fuzz/` (C++), `tools/simfuzz/` (Python driver and tests), saved repros in `scenarios/simfuzz/`.

## Run it
- Fuzz: `python -I tools/simfuzz/run.py --minutes 2 --players 4 [--profile bad_wifi] [--target mini|em] [--bug NAME]`
  (builds on demand into `build/T2b-fuzz`; about 90 sessions per second; exit 0 and `RESULT: ALL OK` when every invariant held).
- Tests (with mutation twins): `python -I tools/simfuzz/run_tests.py` prints `ALL OK`. Also in `tools/test_all.py`.
- Replay a scenario: `fnvcoop_simfuzz --replay file.json` (exit 1 and `VIOLATION <name> <detail>` lines when broken). `--fixture BUG` writes a shrunk repro.
- On a failure the CLI shrinks (ddmin over ops, then link, machines, duration) and writes `<out>/fail_seedN_<inv>.min.scenario.json` plus `.trace.json`.

## What it checks
converge (views and digests equal on all connected machines after the link heals), double_apply, multi_owner (one owner per epoch, epochs never go back),
stale_applied (old epoch, old sequence, non-owner, request from before a host load), echo_adopted, dup_thing (same identity twice), repairs_after_quiescence,
not_quiescent. Scenarios mix moves, hits, loot races, spawns, doors, join/leave, host save/load, link changes and stale-message injection.

## Targets
- `mini` (default): own small host-authoritative world and protocol, with 11 injectable bug classes. Each has a twin test that must be caught
  and a saved shrunk repro (`regress_<bug>.json`: breaks its invariant with the bug, passes without it).
- `em`: adapter over T2a's `synccore/enginemodel` Cluster (`target_em.cpp`). Opt-in build: `set FUZZ_EM=1` before `synccore\fuzz\build.bat`
  (exe in `build/T2b-fuzz-em`). Bug names are T2a's fault-twin names (`--list-bugs --target em`). It also reports Cluster's own oracle findings.
  Current state: the fuzzer finds "AI-driven on 2 machines" in clean runs (see T2a); no em twins are tested here yet.

## Link model and traces
- `--profile NAME` uses T5's `chaos::Impairment` (burst loss, reorder, jitter, duplicate, corrupt, bandwidth, all profile fields) for both targets.
  `mini` applies it in its own queue; `em` installs it through the new `InMemoryNetwork::setShaper` hook (synccore/include/fnvcoop/transport.h, small shared edit).
  `SetLink` ops are ignored when a profile is set (the profile owns the link).
- The `em` target records through T1a's `EngineFlightObserver` (one recorder path); `mini` still writes `.nvfr` from `runner.cpp`.
  Chain proven: `fnvcoop_simfuzz --target em --bug no_dedupe` -> `fail_*.min.m<N>.nvfr` -> `python -I tools/flight/flight.py diverge --check` finds the divergence
  (covered by `run_tests.py` when the em build exists).
- `fnvcoop_simfuzz --replay` is the replay entry point; there is no separate `fnvcoop_replay` binary (see flight-format.md 6.1).

## Not done / open
- `em` is still opt-in and not the default target: clean em runs fail today (stale_applied after host load, dup_thing with provisional 0xFF.. spawn ids, plus
  "AI-driven on 2 machines"); reported to T2a. Once em is clean: flip the default target, make FUZZ_EM default ON, add one twin test per `--list-bugs --target em` class.
