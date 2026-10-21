# M3-actors: actor ownership in the plugin (milestone 3, game-free)

Code: `plugin/actors/` (module), `tools/bots/playbooks/pb_actors.py` + `tools/bots/run_actor_handoff.py` (two-copy playbook).
Status 2026-10-20: everything below is unit-tested game-free. NOTHING has run in a game. The engine adapter is NOT written.

## Run it
- `plugin\actors\run_tests.cmd` (x86, 2 jobs, takes `out\locks\build.lock`; prints `ALL OK` = real suite green and every mutation twin killed).
- `python -I tools/bots/playbooks/tests/run_actor_tests.py` (the playbook against T6b's fake world, 12 tests incl. 8 wrong-world twins).
- Both are in `tools/test_all.py`.

## What it is
- `ActorSync` (one per machine) wires `fnvcoop::OwnershipService` (host) and `OwnershipView` (everyone) to the live layer
  (`synccore/live`: SnapshotSender/Receiver, InterpBuffer, InterestManager). It reads and does not edit either.
- Owner: native AI on, publishes snapshots (unreliable, delta) stamped with the owner epoch. Others: AI off, pose from the InterpBuffer.
  A client's snapshots go to the host, which judges them (`OwnershipView::check`) and relays at the interest rate.
- Handoff: `Transfer` -> old owner AI off + reliable final state -> new owner `place()`s it (no respawn / Reset3D: the port has no such call)
  and Acks -> `Commit` -> new owner AI on. Every step is idempotent (replays change nothing). If the final state is lost the host forces the
  commit after 1 s and the new owner places its best-known state.
- Named NPCs: protected on every machine (`setProtected`); `onLocalDeath` refuses; forged death messages are ignored; only a host `KillActor`
  effect (`onEffect`) kills them. `ResurrectActor` continues the same epoch history (the ownership record is kept on death).
- Follower exception: the host feeds `CompanionOwner` effects to `onEffect`, the service does the rest.
- Leveled pick: the first report from the current owner becomes the shared pick (host broadcasts `MPick`); everyone swaps the base to match.
- Stand-ins: an actor with no local ref in a locally loaded cell gets a clone; removed when the real ref loads; `purgeStandIns()` on load.
- `coop.owner {ref}` -> `{owner, epoch, known, mine, pending, self, netId, ai, standIn, dead}` (`owner` 255 = nobody). Wired in
  `coop_module.cpp` (`NVCOOP_WITH_ACTORS`) through `actor_glue.h`.
- Wire: first byte `0xAC` on the coop MuxTransport. Sub 0 = live SnapshotBatch, 1 = SnapshotAck, 2 = envelope (own, track, full state,
  death, pick, pick report, resync).

- Join state: a client's module only listens once it has joined, so the host's join burst (records, picks, ownership table) can reach the mux before
  the channel has a handler. `hostConnected()` therefore sends `MResync{id=0}` ("the whole table") and the host answers with the same burst
  (`sendJoinState`); a client that went through a baseline (`hostLost`/`hostConnected`) gets its wiped view back the same way. All parts are idempotent.
- Forced commit: when the host commits without the old owner's final state, the old owner may still be driving the actor (a slow link). The new owner
  keeps its AI off (`awaitPrev`) until the old owner's final state arrives (it is stale by epoch, so it is not applied; the host passes it on to a guest
  new owner), the old owner leaves, or `holdAiMaxMs` (4 s) passes. A previous owner that is the host needs no wait. Mirrors the engine model's `awaitOld_`.

## Engine side (2026-10-21, actor-port round): written, unit-tested, never run in a game
- `plugin/actors/engine/` holds the real `IActorPort` (`EnginePort`), the feed (`ActorFeed`), the xNVSE reads (`RealEngineHost`), the module that
  calls `setGamePort()`, and the public API for the other lanes. Design, AI-switch choice, traps, wiring still needed from other lanes and
  the in-game proof: **docs/tools/actor-port.md**. Run: `plugin\actors\engine\run_tests.cmd`.
- Still needed from other lanes before it is end to end: `engine::onEffect` from the coop effect path, remote players' poses and puppet refs
  from the avatar layer, created-ref identities from spawn-arb (all listed in actor-port.md).
- The playbook `ownership_transfer` has never run against a game; it needs the scenario `ownership_handoff` and a game that has the port.

## Mutation twins (NVA_MUT in `actor_sync.cpp`, each must fail the whole suite)
1 AI never turned off, 2 non-owner publishes, 3 AI before Commit, 4 no place at takeover, 5 old owner keeps AI while pending, 6 takeover
recreates the actor, 7 no epoch/owner judge, 8 replays re-processed, 9 named death via message, 10 named death / non-owner death allowed,
11 follower exception ignored, 12 pick never applied, 13 pick overridable, 14 stand-ins survive a load, 15 clone though the real actor is
there, 16 wrong epoch in coop.owner, 17 no Ack, 20 KillActor only on the host, 21 ownership record dropped on death, 22 epoch/seq floors survive a host restart,
23 client never asks for the whole table, 24 host ignores that request, 25 forced commit starts the new owner's AI at once, 26 host does not pass a stale final on,
27 a final of a much older handoff releases the AI hold.
Run-time twins (OwnershipService faults): acceptStaleEpoch, adoptEcho, acceptNonOwner, followerIgnored, transferNoAck, noHysteresis, noEpochBump.

## enemy-anim additions (2026-10-21)
- `MAnimEvent {netId, epoch, eventId, payload}` (Body index 7, append-only): a reliable owner-to-everyone event with an opaque payload. `ActorSync::sendAnimEvent`
  refuses unless this machine is the committed owner (a dead actor keeps its record); the host judges a client's event with `OwnershipView::check`, relays it,
  de-duplicates by eventId and delivers it to `setAnimEventHandler`. Stats: `animEventsSent / Applied / Rejected / Duplicate`. Compile-time twins 30 (anybody may
  send), 31 (a non-owner or stale event is relayed), 32 (a duplicate is applied twice). Test `anim.owner_events_reach_everyone_once_and_forgeries_do_not`.
- `kActorWireVersion` is 2: the reliable full state (`MSnapFull`) carries the schema v2 animation extension (`docs/tools/live-core.md`).
- Only user so far: the settled corpse pose (`plugin/actors/anim`, `docs/tools/enemy-anim.md`).
