# live-core: actor snapshots, delta codec, interpolation, interest management

Status: unit-tested only (host build, no game). Nothing here has run in-game.

Code: `synccore/live/` (`live.h`, `live.cpp`, `tests/`). Library target `fnvcoop_live` (links `fnvcoop`),
test binary `live_tests`. One command: `synccore\live\run_tests.cmd` (builds into `build\live-core`, takes the
repo compile lock `out\locks\build.lock` only while compiling, prints `ALL OK`).

## What is in it

| Piece | Purpose |
|---|---|
| `ActorSnapshot` | netId, ownerEpoch, seq, tick, cell, pos, rot, vel, anim (locomotion, weaponDrawn, sneak, jump, reload, aim), ai (combatTarget, package, alert), avDeltas |
| `SnapshotSender` | per (peer, actor) delta encoder. Deltas are always against the newest ACKED snapshot, never the newest sent one. Full snapshot when nothing is acked, after a resync request, or after 48 unacked. Idle actors are skipped, with a 1 s heartbeat |
| `SnapshotReceiver` | decodes, rebuilds state from a per-actor history of the last 64 accepted snapshots, rejects stale epoch, stale seq and wrong owner, builds acks and "send me a full one" requests |
| `InterpBuffer` | 100 ms buffer, max 250 ms extrapolation, snap > 10 m or other cell, ease > 2 m, short-arc rotation, wrap-safe ticks, deeper buffer for slow (5 Hz) streams |
| `InterestManager` | per observer and actor: Near (same cell, within 2800 units, hysteresis 1.25x) at 20 Hz, Far (same cell beyond, or adjacent cell) at 5 Hz, otherwise nothing; never sent back to its owner |
| `channelFor` / `enetChannel` | snapshots and acks: unreliable sequenced (ENet channel 1); ownership and events: reliable ordered (channel 0). Matches `ITransport::Channel` |

## Wire format
`[kind u8][count u8][entries...][crc32 LE]`. Every datagram carries a CRC, so a flipped bit is a dropped datagram,
never a wrong position. Entry: `netId epoch seq baseSeq tick` (varints), a field mask, then only the changed fields.
Positions are 1/8 unit, velocities 1/4 unit/s, angles 2pi/65536. Both sides store the QUANTIZED state, so sender and
receiver reconstruct bit-identical states and deltas never drift. AV values are absolute and accumulated sender side
so a value changed in a lost snapshot is still different from the acked baseline and is resent.

## How siblings use it
Owner machine: `SeqAllocator::next(netId, epoch)` -> fill `ActorSnapshot` (put the owner's current AV values in
`avDeltas`) -> `SnapshotSender::queue(hostPeer, snap, now)` -> `flush` -> send on `Channel::Unreliable`.
Host: `SnapshotReceiver` with `checkSenderIsOwner = true`; call `setAuthority(netId, epoch, owner)` on every Grant and
Commit from the ownership service and `clearAuthority` on Release. To relay, queue the accepted snapshot (use
`ReceivedSnapshot::avAll` as `avDeltas`) into a sender per client; `InterestManager::collect` says which actors are
due for a client. Client: receiver with `checkSenderIsOwner = false` (it only hears the host), push accepted snapshots
into one `InterpBuffer` per actor and call `sample(now, &actualPose)` each frame. Send `takeAcks(peer)` back about
every 50 ms and feed incoming ack datagrams to `SnapshotSender::onAckPacket`.

Units are game units, 70 per metre (`InterpConfig::unitsPerMetre`). Locomotion codes, alert levels and package ids are
opaque to this layer.

## Tests (all under `synccore/live/tests`)
- `codec`, `receiver`, `interp`, `interest`, `channel`: unit tests, including a single-bit-flip and truncation sweep of
  a datagram, 3000 random CRC-valid garbage datagrams, boundary values at exactly 2 m and 10 m.
- `lossy`: a 3-machine pipeline (owner -> host -> client, acks back) over `synccore/chaos/impair.h` for all six
  profiles with several seeds, an owner handoff at 10 s, optional ghost (former owner keeps publishing), rogue
  (bystander forges), a 2 s partition and 3% bit corruption. Checks: nothing applied out of order, every
  reconstructed state equals what the real owner published at that seq, the AV set matches, the puppet converges
  after the link heals, no snap on realistic links, no pop above the thresholds.
- `twin`: 20 mutation twins (`LiveFaults` switches, runtime-selectable). Each one breaks a rule and its guarding
  scenario must fail: delta from latest, stale seq, stale epoch, non-owner, no extrapolation cap, extrapolate only,
  no snap, no smooth, snap ignores cell, near rate as far, no far tier, ack ignored, drop AV changes, no resync, plus
  six of them again on the lossy pipeline.

Set `LIVE_VERBOSE=1` to print one summary line per lossy run. Measured on the circle-walking test actor: 26.6 bytes
per snapshot on a clean link (45.8 for a full snapshot, so 58%), 28 to 30 bytes under bad_wifi / hell. The suite is also
part of the gate through `synccore\run_tests.bat` (L4), because `synccore/CMakeLists.txt` adds `live/`.

Relay rule (easy to get wrong): `ReceivedSnapshot::snap.avDeltas` holds only the AV values that changed in THAT
datagram. When relaying to another peer, copy `avAll` into `avDeltas` before `queue()`, otherwise a peer that has no
baseline yet receives an incomplete AV set. `SnapshotSender` accumulates AVs per (peer, actor) from then on.

## Not covered (be honest)
- Nothing runs in the game. Real ENet jitter, the game's frame times, restrained-actor behaviour and animation
  re-assertion are the avatar and actor agents' problem.
- The interest manager works on cell id equality and a configured adjacency list; the plugin has to supply the
  neighbour cells. Exterior grids with radius loading are not modelled.
- Rotation is not smoothed or snapped, only interpolated.
- Hits, deaths, spawns and drops are reliable events, outside this module (only their channel is mapped).

## Schema v2: the animation extension (enemy-anim, 2026-10-21)
`AnimState` carries, after the five v1 flags: `action` (base animation group id of the owner's current one-shot, 0 none), `actionSeq` (u8 counter,
bumps when a one-shot STARTS), and the bits `dead`, `knocked`, `swim`, `talk`, `block`, `ragdoll`. Wire: bit 7 of the anim flags byte says
"three bytes follow" (`action`, `actionSeq`, `extBits`); an all-zero extension is never sent, so such a snapshot is byte-identical to v1; a v1
receiver rejects the extension. `kLiveSchemaVersion = 2` (live.h) and `kActorWireVersion = 2` (plugin/actors) change together. The decoder accepts
only canonical forms (reserved flag bits 5-6, reserved extension bits 6-7 and an all-zero extension are Malformed). `actionSeqNewer(a, b)` is the
wrap-safe compare. The InterpBuffer needs no change: anim is the zero-order hold of the snapshot the render time has passed, so a one-shot shows
when the interpolated position reaches the snapshot that announced it. Tests: group `anim` (8 tests) and twins `drop_anim_ext`,
`drop_anim_ext_one_shot`, `anim_ext_no_delta`, `accept_bad_anim_ext`. Meaning of the fields and the users: `docs/tools/enemy-anim.md`.
