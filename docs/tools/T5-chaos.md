# T5-chaos: network chaos box

Status: done, game-free. Tests: `python -I tools/chaos/run_tests.py` prints `ALL OK` (about 80 s, includes 10
mutation twins). Registered in `tools/test_all.py` as "T5 chaos box". No game was launched.

## Files (all in tools/chaos/)
- `profiles.json`: the shared profile table (source of truth). Profiles: lan, good_wifi, bad_wifi, mobile_tether,
  transatlantic, hell. Each has a fixed seed.
- `impair.py`: the impairment model as a library. `Impairment(params, "c2s").decide(now_ms, size)` returns a
  verdict: dropped with a reason (loss, burst, partition, disconnect, bandwidth) or a list of delivery delays
  (two entries = duplicate). Virtual time, no sockets, deterministic per seed and direction.
- `relay.py`: the UDP relay, JSON-RPC server (`RpcServer`) and a small `ChaosClient`.
- `chaos.py`: CLI. `profiles`, `relay`, `call`.
- `test_chaos.py`, `run_tests.py`: tests and twins.

## Model
Per direction (c2s = client to server, s2c = server to client): latency +/- uniform jitter; base loss plus
Gilbert-Elliott burst loss (`burst_enter`, `burst_exit`, `burst_loss`); reorder (a packet skips the latency and
overtakes earlier ones); duplicate; corrupt (one flipped bit); bandwidth cap with a tail-drop queue
(`bandwidth_bps`, `buffer_ms`); one-way partition; timed disconnect (both directions).

## Use
Start: `python -I tools/chaos/chaos.py relay --target 127.0.0.1:27971 --profile bad_wifi --log out/chaos/run.jsonl`
It prints `{"listen": P, "rpc": R}` (ports are OS-assigned unless given). Point the client at P. Each client address
gets its own upstream socket, so the host sees separate peers.

Live control, newline-delimited JSON-RPC 2.0 over TCP on R (or `chaos.py call --rpc-port R <method> '<json>'`):
`status`, `stats`, `reset_stats`, `profiles`, `set_profile {profile|name, seed?, dir?}`,
`set_params {params:{...}, dir?}`, `partition {dir: c2s|s2c|both, on}`, `disconnect {duration_ms}`, `reconnect`,
`shutdown`. Python: `from relay import ChaosClient`.

## Joining a live pair through the relay (`chaos_pair.py`)
`python -I tools/chaos/chaos_pair.py --instances 0,1 --info-file runs/chaos.json -- tools/bots/run_fight.py --instances 0,1 --which fight --chaos bad_wifi --chaos-seed 7 --chaos-rpc runs/chaos.json`
reads the host copy's `[coop] port`, starts the relay (clean `lan` profile) in front of it, writes the info file, makes the client copy `coop.leave` then
`coop.join {host:127.0.0.1, port:<relay>}` over its bridge and waits for `client.joined`, runs the command after `--`, and always puts the client back on the host's real port
and stops the relay. Exit codes: the command's own; 2 "cannot reach the game instances" when a copy's bridge is down; 1 when the client did not join through the relay.
`prove.py` uses it for `enemy_chaos_bad_wifi`. Tests: `test_chaos_pair.py` (fake client bridge, real relay and UDP echo on loopback; 2 twins in `run_tests.py`).

## Packet log (agreed format proposal sent to T1b-analyzer; no reply yet)
JSON Lines. Header: `{"format":"nvchaos-packetlog","version":1,"profile","seed","start_wall"}`. Per datagram:
`t` (ms, relay monotonic clock), `dir`, `peer`, `n` (per-direction counter), `len`, `head` (first 16 bytes, hex),
`verdict` (fwd/drop), `reason`, `copies` (delays in ms), `corrupt`. Event lines carry `event` (profile, params,
partition, disconnect, reconnect). Correlate with .nvfr traces by `head`, `len` and order, not by frame number.

## Sharing with the simulator
T2b's `LinkParams` (synccore/include/fnvcoop/transport.h) has latency range, loss, duplicate, corrupt only. Mapping:
min/max latency = latency_ms -/+ jitter_ms. Burst loss, reorder and bandwidth are not in LinkParams. Proposal sent
to T2b: add Gilbert-Elliott fields there, or I write a header-only mirror in `synccore/chaos/`. Awaiting an answer;
nothing in synccore was changed.

## Known limits (measured or unknown)
- Latency precision: the relay sets the Windows 1 ms timer resolution while running. Added error was not measured
  beyond the test tolerance (mean RTT within 14 ms + 12 percent, lan round trip under 15 ms).
- Real-game behaviour through the relay (ENet connect, timeouts) is unknown; it needs the lead's in-game run. The real ENet transport and session
  protocol without the game are covered below ("Through the relay").
- A client that changes source port looks like a new peer.
- Python relay throughput was not benchmarked; tests use under 1000 packets per second.

## C++ mirror (added on lead decision)
`synccore/chaos/impair.h` (header-only, namespace `fnvcoop::chaos`) is an exact mirror of `impair.py`: same
SplitMix64 stream (seeded from FNV-1a of "seed:label"), same call order, same arithmetic. `profiles_gen.h` is
generated from `profiles.json` (`python -I tools/chaos/gen_header.py --write`; ctest `chaos_header_fresh` fails if stale).
API: `auto p = profile("bad_wifi"); Impairment c2s(p, "c2s"); Verdict v = c2s.decide(nowMs, size);`
(`v.dropped()`, `v.reason`, `v.delaysMs`, `v.corrupt`), plus `partition(bool)`, `disconnectUntil(ms)`, `reconnect()`,
`setParams(p, reseed)`, `flipBit(bytes)`. Use labels "c2s"/"s2c" to match the relay.
Tests (ctest, synccore\run_tests.bat): `chaos_xcheck` (about 36000 decisions over 6 profiles x 2 directions with mid-run
corrupt, partition, disconnect, bandwidth, duplicate and reorder changes: identical to Python), 4 mutant builds
(`twin_chaos_xcheck_m1..m4`, must fail) and a stale-header twin. All 7 pass.
Python switched from `random.Random` to the shared SplitMix64 `Rng`; chaos tests and twins still pass.
Other L4 failures seen in the same run (unit_wire, unit_core, sim_seeds, drift_effect_table, flight_unit, several
twins) come from the Effect variant count changing elsewhere (test_wire.cpp:65), not from chaos.

## Through the relay (game-free ENet test)
`tools/chaos/enet_relay.py` runs the real ENet transport and the full session protocol through the relay, no game: it starts
`fnvcoop_loopback host` on a free loopback port, puts the relay (seeded profile) in front of it and starts the client processes against the
relay, with the loopback programs' own fault wrapper off (`--loss 0`), so every lost, late, reordered or duplicated datagram is the relay's.
It passes when the host and both clients finish, all digests are equal, and the relay forwarded traffic in both directions (and dropped some,
for a profile with loss). Options: `--profile`, `--clients`, `--txns`, `--timeout`, `--set NAME=VALUE` (override one parameter in both directions).
ctest (synccore, needs Python): `loopback_relay_bad_wifi`, `loopback_relay_mobile_tether`, `loopback_relay_transatlantic` (about 4 s each) and two
twins that must be caught: `twin_loopback_relay_bypass` (clients connect straight to the host: nothing crosses the relay) and
`twin_loopback_relay_partition` (the relay drops everything: the clients never join). A twin exits 0 when it is caught, like `twin_loopback_*`.

Measured on Linux (2 clients, 150 transactions, 400 to 2300 datagrams per direction): `lan`, `good_wifi`, `bad_wifi`, `mobile_tether` and
`transatlantic` converge in 3 to 5 s with the host's digest. **`hell` does not**: both clients apply all 150 transactions and hold the host's
digest, but the host never reaches `allSynced()` within the loopback program's 90 s (so `fnvcoop_loopback` reports TIMEOUT), at about 40 percent
dropped datagrams. Bisected by overriding one parameter at a time: it converges with `burst_enter=0` or with `latency_ms=20, jitter_ms=5`, and still
fails with `reorder=0`, `duplicate=0` or `bandwidth_bps=0`, so it is the combination of long round trips and burst loss. A longer client
linger (up to 15 s) did not help. Not investigated further (session layer); `hell` is therefore not a gated ctest. Not proven in the game.
