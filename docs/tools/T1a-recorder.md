# T1a flight recorder: status

Owner: T1a-recorder. Partner: T1b-analyzer (Python side, `tools/flight/`). Format spec: `docs/tools/flight-format.md` (NVFR v1).

## What exists

| Piece | Where | Notes |
|---|---|---|
| Recorder, wrappers, reader | `synccore/flight/flight.h`, `flight.cpp` | `FlightRecorder` (ring buffer + dump), `RecordingTransport`, `RecordingSink`, `readTrace`/`parseTrace` |
| Tests + mutation twins | `synccore/flight/tests/test_flight.cpp` | run with `synccore\flight\run_tests.bat` (prints `ALL OK`) |
| Golden traces for the Python reader | `synccore/flight/golden/sample_host.nvfr`, `sample_client.nvfr` | deterministic; a test fails if the format drifts from them |
| Plugin service | `plugin/flight/flight_service.{h,cpp}` | ini `[flight]`, dump to the instance folder, crash filter, bridge helpers |
| Plugin module | `plugin/flight/flight_module.cpp` | bridge methods `flight.dump` and `flight.status` |
| Runtime hook | `plugin/coop/coop_runtime.{h,cpp}` | `Runtime::setFlight(recorder)`; wraps the transport and the client sink; records host commits; story digest every 2 s when the world moved; desync detection triggers a dump |
| Plugin-side tests + twins | `plugin/flight/tests/test_flight_runtime.cpp` | run with `plugin\flight\run_tests.cmd` (also runs the synccore tests). Real host + client Runtimes over loopback ENet |
| Registered in | `tools/test_all.py` ("T1a flight recorder") | |

## What is recorded

Every datagram in and out (all channels, with peer, channel, message type, session, epoch and seq range decoded from the
wire), every effect handed to the local sink (with seq, epoch, origin, flags, effect tag, effect hash), every host commit,
story digests, desync markers, and ownership records (the record type is fixed; the ownership service is T2a's and will call
`FlightRecorder::ownership`). Per-category digests for actors, containers and objects use `FlightRecorder::digest`; the
engine side for those does not exist yet, so only the story category is produced today. A `SyncDigest` message (the client's
128-shard world dump) is also turned into a Digest record at dump time.

## How to use it

- In the game: `nvcoop.ini` section `[flight]`: `enable=1` (default), `dir=` (default `<plugin folder>\nvcoop_flight`),
  `ringMb=16`, `crash=1`, `label=nvcoop`.
- Dump on demand over the bridge: `flight.dump` (optional `{"path": "..."}`), status with `flight.status`.
- Automatic dumps: on a detected desync (host sees a digest mismatch, or a client is repaired), on an unhandled exception
  (the crash filter chains to the previous filter), and on `Runtime::stop()`. Desync dumps are limited to 8 per run and one per 30 s.
- In a simulator or fuzzer (T2a, T2b): create one `FlightRecorder` per machine, `setClockSource` to the virtual clock,
  `setFrame` per tick, wrap each endpoint in `RecordingTransport` and each sink in `RecordingSink`, call `ownership(...)`,
  `digest(...)`, `marker(...)`, then `dump(path)`.

## Measured overhead (game-free benchmark, x86 Release, this PC)

Test `perf.idle_frame_overhead_is_far_below_0_1_ms`: idle frame (set frame number, an ack pair every 30 frames, a digest every
120 frames) about 0.01 microseconds per frame; busy frame (20 datagrams out, 20 in, 10 applied effects) about 27 microseconds
per frame. The budget is 100 microseconds. The story digest in the game is now incremental: `ShadowState::bucketCache` (synccore/include/fnvcoop/shadow.h) keeps per-segment bucket
sums (28 segments, one per entry map) and `applyEffect` marks the segments an effect can touch; the big maps (stages, globals, enabled,
placed) update only the touched key (`bucketHashesCached` in baseline.cpp). Benchmark `perf.story_digest_from_a_realistic_shared_state`
(640 quests plus 20000 entries, 23201 entries): building the entry list with `baselineEntries` costs about 13 ms, a full walk from the
maps about 3.4 ms, and the incremental digest after one applied effect about 0.01 ms (limit 2 ms). The first digest of a state is
still one full walk. Safety net: every 32nd cached digest is compared with a full walk and heals the cache on a disagreement
(`bucketCache.verifyFailures`); a random-effect equivalence test and a twin (`incremental_misses_dirty`) cover the dirty marking.
Effects not listed in `segmentsOf` (shadow.cpp) dirty every segment, so a new effect kind is safe by default.

## Mutation twins

synccore: drop-newest ring, no NetIn, zeroed seq fields, no truncation flag, no dropped count, no applied flags, no derived
digest. Incremental digest forgets to mark a segment dirty. Plugin: host recorder not attached, client recorder not attached, recorder attached after `start()`. Each must make its
test fail; the run scripts check this.

## Shared files touched (minimal edits)

- `synccore/CMakeLists.txt`: one line, `add_subdirectory(flight)`.
- `plugin/CMakeLists.txt`: `flight` added to the module sub-directory list (after `coop`).
- `plugin/coop/CMakeLists.txt`: `synccore/flight/flight.cpp` added to the synccore library, plus the `synccore` include dir.
- `plugin/coop/coop_runtime.{h,cpp}`: the `setFlight` hook described above (does nothing when no recorder is set).
- `plugin/coop/coop_module.cpp`: hands the service's recorder to the Runtime under `NVCOOP_WITH_FLIGHT`.
- `tools/test_all.py`: one suite line.

## Open items

- `pluginlightun_tests.cmd` now also runs `tests/check_runtime_traces.py` on the desync traces the runtime test keeps
  (`FNVCOOP_FLIGHT_KEEP=1`): the analyzer must name bucket 72, key aa650f118b7cb748, host 0 vs client 77. The runtime test waits up
  to 15 s for the client's desync dump (a fixed 300 ms pause failed about half the runs on a loaded PC).
- Full `build_l1.cmd` could not be confirmed at the end: it stopped on compile errors in other agents' files
  (`plugin/hooks/engine_hooks.cpp` and an unfinished `NVCOOP_RELOAD_STATE` block in `coop_module.cpp`). The flight module and
  service compiled in that run (`flight_module.cpp.obj` built).
- `plugin\coop\run_tests.cmd` printed 8 failing checks in the L11 wiring part (`host.ui.eng.writes`); the flight hook is inert
  there (no recorder set), and `plugin/hooks_ui` has uncommitted changes from another agent. Not investigated further.
- Actor, container and object digests: waiting for T2a's model and the later engine-side actor layer.
- Nothing was run in a real game (code-only rule).
