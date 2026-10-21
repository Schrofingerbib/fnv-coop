# T1b-analyzer status

Date: 2026-10-20. Owner of `tools/flight/` (Python only) and of the wording of `docs/tools/flight-format.md`.

## Done
* Reader, writer and pretty-printer for the real `.nvfr` v1 layout that `synccore/flight` writes (`nvfr.py`); reads T1a's golden
  samples in the tests; tolerates cut-short crash dumps and malformed records.
* Align 2 to 4 machines: clock offsets from paired messages, lost and duplicated messages (`analyze.py`).
* First-divergence finder with causal chain: effect content per (seq, index), double apply, missing txn, ownership conflict and
  stale epoch, digest mismatch down to the key or shard.
* Auto-shrink (ddmin) with a same-failure check (`shrink.py`), for trace fixtures (`steps`) and T2b's simfuzz-1 scenarios (`ops`,
  `fnvcoop_simfuzz --replay`, `VIOLATION <name> <detail>` lines).
* Auto-test generation (JSON fixture + cmake `add_test`) and a directory runner (`gentest.py`, `regress.py`).
* HTML report, self-contained, escaped (`report.py`). CLI: `flight.py`.
* `python -I tools/flight/run_tests.py`: 34 tests plus a 29-mutation twin, prints ALL OK (about 4 minutes on a loaded PC).
  Added to `tools/test_all.py` (non-compiling suite, so it also runs in `--quick`).

## Not done / depends on others
* `fnvcoop_replay` and `fnvcoop_simfuzz --replay` do not exist yet (T2b). Shrinking is tested only against `toy_replay.py`, which
  mimics both contracts.
* Datagram pairing assumes the NetIn/NetOut `peer` id equals the other machine's slot (true in golden samples). Unknown for ENet
  in the plugin until T1a confirms.
* Never run on a real in-game trace; only synthetic traces and the two golden files.
* A trace fixture cannot reproduce host-local commits (the recorder keeps only effect hashes, not encoded txns).
* When identical bytes are retransmitted, which copy was lost is a guess (the count is right).

## Update (suite split and end-to-end loop)
* `run_tests.py` default = fast tests only (about 1 s). `--full` adds shrink, generation and CLI tests plus the mutation twin
  (about 4 min). `tools/test_all.py` runs the fast one always and `--full` only outside `--quick`.
* End-to-end loop proven against the real `build/T2b-fuzz/fnvcoop_simfuzz.exe`: `--bug stale_epoch_accepted --no-shrink` produced
  a 378-op failing scenario; `flight.py shrink` cut it to 3 ops in 65 replays (3 s), signature `stale_applied`; `gentest` wrote the
  regression fixture; `regress` FAILS on it with the bug present and PASSES once the scenario's injected bug is removed ("fix").
  Use an absolute path to the exe in `--replay-cmd` (relative paths fail under subprocess on Windows).
* Not covered: the fuzzer writes its own `.trace.json`, not `.nvfr`, so the trace-to-analyzer step of the loop is not proven;
  that needs T1a's recorder wired into the simulator. Whether T1a's writer matches the doc is still unconfirmed.

## Update (real .nvfr chain)
* Ownership op 5 (rejectedNonOwner) is named and, like ops 3 and 4, never reported (test added).
* Real chain run on a fresh fuzz failure (`--bug stale_epoch_accepted`, .nvfr traces from the current `fnvcoop_simfuzz.exe`):
  `diverge` on the 3 .nvfr files found no state mismatch (the bug is a stale update accepted, not a hash difference), so I added
  the `desync-marker` finder: it reports the machine's own desync Marker and the ownership history of the named actor
  (transfers at epochs 16, 20, 22, 24 and the epoch-11 echo). Then `shrink` 378 -> 3 ops, `gentest` fixture, `regress` fails with
  the bug present and passes with `mutations` emptied. Printed times are now relative to the first record.
* Full suite: 31 mutations, all caught.

## Update (bucket -> key -> value narrowing)
* `analyze.narrow` gives every digest divergence a `narrowing` block: differing buckets (256 bucket hashes, bucket = key & 0xFF), then the
  keys, then each machine's value hash (None = no such entry) and value text (the recorder's `entry` markers). Keys come from the same
  digest's detail records; for a story digest that has only buckets, they come from the newest other digest with key detail (the desync
  dump) whose differing keys fall in the differing buckets (a key outside them is flagged). With no detail anywhere it says so.
* Shown in `flight.py diverge` text and in the HTML report ("Narrowing: digest, bucket, key, value" table).
* Fixed a false `txn-missing`: a host commit (TxnCaptured with a seq) now counts as applied on the host.
* Real runtime traces: `plugin/flight/tests/check_runtime_traces.py` (run by `plugin\flight\run_tests.cmd`) narrows the plugin runtime
  test's desync to bucket 72, key 0xAA650F118B7CB748, host `SetStage 203 = 0` vs client `= 77`.
* Tests: 41 fast (about 0.1 s), 5 new mutations (cross-digest bucket filter, host commit, value text, report table, report text).
