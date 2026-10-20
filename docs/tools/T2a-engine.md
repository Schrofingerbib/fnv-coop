# T2a-engine: fake game engine and ownership service

Code: `synccore/enginemodel/` (model, `Machine`, `Cluster`, tests). The ownership service itself is `synccore/src/ownership.cpp`.

## Run it
- `synccore\run_tests.bat` (set `CMAKE_BUILD_PARALLEL_LEVEL=2`). Engine groups: `engine_own`, `engine_world`, `engine_sim`, `engine_twin_*`.
- Fuzz the model: `set FUZZ_EM=1` then `python -I tools/simfuzz/run.py --target em ...` (see T2b-fuzz.md).

## Fixed on 2026-10-20: AI driven on two machines
Cause: `Machine::aiOn` was only recomputed on the machine's own tick. When an ownership message moved an actor
(Commit, Grant, Release, or the cancel of a handoff), the old owner kept `aiOn = true` until its next tick while the new
owner could already be ticking, so a check between the two saw the actor AI-driven on 2 machines.
Fix: `Machine::refreshAi(id)` runs at once from `onOwnApplied`, so AI follows the ownership view the moment it changes.
The Cluster check was not touched.

## Fuzzer adapter fixes (synccore/fuzz/target_em.cpp, T2b's file, minimal edits)
- Dropped-item identity was an xor fold of (src, reqId) and collided across different drops: now injective.
- Item conservation is switched off after a leave or a host load (the model's own scenarios already do this).
- A guest adopting a new host session after a load now emits a Reset event, so the stale-epoch oracle forgets the old world.

## Open
- None known in the model. x86 `run_tests.bat` can show SEGFAULT across many tests when another agent builds into the same
  `build\L4` at the same time; rerun when no other build is running.
