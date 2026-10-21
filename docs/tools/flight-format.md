# Flight trace format (.nvfr v1) and replay fixtures

Writer: `synccore/flight/` (T1a, `flight.h`, `flight.cpp`; the C++ side is the source of truth for byte layout).
Reader, analyzer, shrinker: `tools/flight/` (T1b, Python). The Python reader also has a writer (`nvfr.dump`) used by tests and by
tools that add records (animcheck adds Marker records). Golden samples: `synccore/flight/golden/sample_host.nvfr` and
`sample_client.nvfr`; `tools/flight/run_tests.py` reads them on every run.

## 1. File layout

One file per machine per dump. All integers little endian. `str16` = `u16 length` + UTF-8 bytes.

```
HEADER (64 bytes)
  "NVFR" (4)  u16 version (=1)  u16 headerSize (=64)
  u8 slot  u8 role (0 host, 1 client, 2 sim)  u8 reason (0 manual, 1 desync, 2 crash, 3 shutdown, 4 test)  u8 reserved
  u32 wireVersion   u64 wallStartMs   u64 dumpTUs   u64 recordsDropped   u64 recordCount
  u32 sessionId     u32 epoch         u64 reserved
RECORD (16-byte header + payload), repeated to end of file
  u8 type  u8 flags  u16 payloadLen  u32 frame  u64 tUs      then payloadLen bytes
```

* `tUs` is microseconds since the recorder started, monotonic, and only comparable inside one file. In the simulator all
  machines share one virtual clock. The analyzer estimates real offsets between machines from paired messages.
* A half-written last record (crash dump) is fine: readers keep everything before it. Unknown record types are kept as raw payload.
* Record flags: bit0 = datagram truncated (Net), bit1 = derived (Digest built from a SyncDigest message at dump time), bit2 = detail (Digest carrying only per-key entries).
* Bucketed digests (T1a, `bucketedDigest`): category 0 (story) digests recorded by the plugin and category 4 (world, at a desync) carry
  `nShards = 256` bucket hashes: bucket = `key & 0xFF`, bucket hash = sum (mod 2^32) of a 64-bit mix of (key, value hash) over its entries,
  so it is independent of entry order; `hash` covers the 256 buckets and the entry count. Two machines' digests of the same
  `(category, epoch, seq)` differ exactly in the buckets whose entries differ.
* Per-key detail: on a desync both machines write the entries of the state they compared as Digest records with flag bit2 (category 4,
  `seq` 0, up to 4000 `(key, value hash)` per record, several records if needed; `itemCount` = total) plus one Marker per entry (up to 20000)
  with text `entry <key as 16 hex digits> <value hash decimal> <describeEntry text>`, e.g. `entry aa65... 2426343424 SetStage 203@falloutnv.esm = 77`.
  The client side is the engine world its last SyncDigest was built from (before the host's repair); the host side is its shared state.
  Narrowing: differing buckets (category 4 digest with shards) -> keys whose value hash differs or exists on one side only (detail records)
  -> value text from the Marker lines on each machine.

## 2. Record types and payloads

| Type | Name | Payload |
|---|---|---|
| 1 | Meta | repeated `str16 key`, `str16 value` (first pair is `label`; the analyzer shows it as the instance name) |
| 2 / 3 | NetOut / NetIn | 36-byte prefix: `u32 peer`, `u8 channel` (0xFF on NetIn), `u8 mux` (first datagram byte: 0xFC story, 0xD1 dialogue, 0xE5 slideshow), `u8 msgType` (protocol.md section 5 id, 0 if not a story datagram), `u8 decode` (0 decoded, 1 decode failed, 2 not a story datagram), `u32 session`, `u32 epoch`, `u64 seqLo`, `u64 seqHi`, `u32 rawLen`; then the datagram bytes (cut at `maxRawBytes`, flag bit0 set when cut) |
| 4 | TxnCaptured | `u8 kind` (0 host commit input, 1 client intent), `u32 intentId`, `u64 seq` (0 for a client intent), `u32 effectCount`, `u64 hash` (FNV fold of the effect hashes), `u8 cause`, `u32 epoch` |
| 5 | TxnApplied | one record per applied effect: `u64 seq`, `u32 epoch`, `u8 origin`, `u32 index`, `u8 appliedFlags` (1 fromSnapshot, 2 repair, 4 baseline, 8 catchUp), `u8 cause`, `u8 effectTag`, `u64 hash` |
| 6 | Ownership | `u8 op` (0 grant, 1 transfer, 2 release, 3 rejectedStale, 4 rejectedEcho, 5 rejectedNonOwner), `u64 actor`, `u32 epoch`, `u8 fromOwner` (255 none), `u8 toOwner` (255 none), `u8 reason`, `u64 txnId` |
| 7 | Digest | `u8 category` (0 story, 1 actors, 2 containers, 3 objects, 4 other), `u32 epoch`, `u64 seq`, `u64 hash`, `u32 itemCount`, `u16 nShards` + `u32` each, `u32 nKeys` + (`u64 key`, `u32 hash`) each |
| 8 | Marker | `u16 kind` (0 note, 1 desync, 2 join, 3 leave, 4 test step, 5 save, 6 load, 7 rebase, 8 heal), `str16 text` |

Marker kinds 1 and 4 are stable and are what other tools use (animcheck writes kind 4 summaries and kind 1 breaches).

Marker kind 8 (heal, added for the area desync digests, additive): `heal <category> key=<id> cell=<id> <before> -> <after>` for one corrected key,
`heals/min story=N actors=N containers=N objects=N total=N` when the heal total moved, and `BUG repeated <category> key=<id> xN authority{..} other{..}` for a
key that came back after a heal. The Meta record then also carries `heals.<category>.total|perMin|mismatches` (categories story, actors, containers, objects)
and the sums `heals.total` / `heals.perMin`; `tools/areadigest/dashboard.py nvfr <dump>` prints them as one line. Details: docs/tools/area-desync.md.

## 3. What the analyzer relies on

* TxnApplied `(seq, index)` identifies one effect of one committed txn. Its `hash` must be equal on every machine that applies it.
  Applies flagged snapshot, repair or baseline restate state rather than replay the txn, so they are ignored for content,
  double-apply and missing checks.
* Datagrams are paired between machines by their bytes (a hash of the raw datagram), the sender being the NetOut peer and the
  receiver the NetIn peer. This assumes the `peer` id equals the other machine's slot, as in the simulator. For ENet in the plugin
  this is **unknown** until T1a confirms how peer ids are assigned.
* Digests with the same `(category, epoch, seq, derived)` are compared across machines. A derived digest (built from a SyncDigest
  message) shows up on both the sender and the receiver and is identical there by construction. Derived and real digests are grouped
  apart so they are never compared with each other.
* Ownership ops 3, 4 and 5 are the system rejecting something stale or echoed, so they are never reported. Two different owners at
  the same `(actor, epoch)`, or a grant with an epoch lower than one already seen on that machine, are.

## 4. What the analyzer reports

`flight.py diverge a.nvfr b.nvfr [c.nvfr]` finds the earliest of: `txn-hash` (same seq and index, different effect hash),
`txn-double` (applied twice), `txn-missing` (applied elsewhere, never here: only when this machine applied a later seq, or kept
recording more than 2 s past it), `ownership-conflict`, `ownership-stale`, `digest` (hash, a shard or a key differs), and `desync-marker` (a machine wrote its own desync Marker, kind 1: lowest priority; the chain shows that actor's ownership history when the text contains `ref=#<hex>`). It prints
the key, the suspect machine(s) and a causal chain: the records about that key (marked `*`) plus surrounding txns, ownership
events and messages between the involved machines, back to the last digest of that category everyone agreed on, plus any message
lost or delivered twice in that window. "Lost" means sent and never seen arriving; when identical bytes are retransmitted, which
copy was lost is a guess (the count is right).

## 5. Commands

```
python -I tools/flight/flight.py print FILE [--type NetIn,Digest] [--limit N]
python -I tools/flight/flight.py align FILE FILE [FILE]
python -I tools/flight/flight.py diverge FILE FILE [FILE] [--json] [--check]
python -I tools/flight/flight.py report FILE FILE [FILE] -o report.html [--shrink shrunk.json]
python -I tools/flight/flight.py fixture FILE FILE [FILE] -o fixture.json
python -I tools/flight/flight.py shrink fixture.json --replay-cmd "exe ... {fixture}" -o shrunk.json
python -I tools/flight/flight.py gentest shrunk.json --name stale_echo [--out DIR]
python -I tools/flight/flight.py regress DIR --replay-cmd "exe ... {fixture}"
python -I tools/flight/run_tests.py          # tests + mutation twin, prints ALL OK
```

## 6. Replay programs and fixtures

The shrinker reduces a list of independent steps and keeps a smaller list only if the replay still fails with the **same failure
signature**. It understands two fixture shapes, both JSON:

* **simfuzz-1 scenarios** (T2b): `{"format":"simfuzz-1","seed":N,"machines":M,"link":{...},"mutations":[...],"ops":[...]}`, replayed by
  `fnvcoop_simfuzz --replay scenario.json`; exit 0 = every invariant held, 1 = violation, with stdout lines
  `VIOLATION <invariant-name> <detail>`. The signature is the first invariant name. The list key is `ops`.
  Ops must be independently removable (own absolute time, explicit args). The shrinker never deletes an op with `"pin": true`
  (T2b: please make the loader ignore that key).
* **Trace fixtures** (built by `flight.py fixture` from real traces): `{"version":1,"config":{...},"steps":[...]}` where a step is
  `{"op":"deliver","to":SLOT,"from":SLOT,"hex":"<datagram>","t":ms}` (one per NetIn with a full datagram) or a scenario control
  (`drop`, `disconnect`, `rejoin`, `inject`, `quiesce`). The final `{"op":"quiesce","pin":true}` is added automatically.
  Replay program contract: `<exe> <fixture.json>` prints one JSON line `{"diverged":bool,"violations":[...],"first":{...}}`
  and exits 0 converged, 1 diverged, 2 bad fixture. Host-side commits are not in a trace as bytes (the recorder keeps only effect
  hashes), so a trace fixture replays what each machine received, not what the host decided locally. `tools/flight/toy_replay.py`
  implements both contracts for the tests.

Generated regression tests (`gentest`): `<name>.json` is the shrunk fixture plus `"expect": {"converges": true}` (it passes once the
bug is fixed) and `<name>.cmake` holds an `add_test` line for `fnvcoop_replay <file>` or `fnvcoop_simfuzz --replay <file>`.
`regress.py` runs every fixture in a directory and prints ALL OK when each matches its expectation.

### 6.1 Note from T2b: `fnvcoop_simfuzz --replay` is the replay entry point for fuzz scenarios

Fuzz failures are replayed by `fnvcoop_simfuzz --replay scenario.json` (format `simfuzz-1`: seed, machines, link or chaos `profile`, injected `mutations`,
and independent timed `ops`). It exits 0 when every invariant holds and 1 otherwise, printing one `VIOLATION <invariant> <detail>` line per break;
2 means an unreadable file. Unknown keys (`pin`, `expect`, `shrunk`) are ignored. `flight.py shrink scenario.json --replay-cmd "fnvcoop_simfuzz --replay {fixture}"`
works as is. A separate `fnvcoop_replay` binary (real HostSession/ClientSession fed recorded datagrams) is NOT built; the `deliver`/`txn` fixture
steps above are therefore only handled by T1b's Python toy oracle for now.
