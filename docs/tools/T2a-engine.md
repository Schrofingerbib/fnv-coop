# T2a-engine: fake game engine and ownership service

Code: `synccore/enginemodel/` (model, `Machine`, `Cluster`, tests). The ownership service itself is `synccore/src/ownership.cpp`.

## Run it
- Engine tests only: `python -I tools/build/build.py --target fnvcoop_engine_tests` then
  `build\all\synccore\enginemodel\fnvcoop_engine_tests.exe --group engine|world|own|twin` (all four print `== N tests, N passed, 0 failed`).
  Whole suite: `synccore\run_tests.bat` (set `CMAKE_BUILD_PARALLEL_LEVEL=2`). ctest names: `engine_own`, `engine_world`, `engine_sim`, `engine_twin_*`.
- Fuzz the model: `python -I tools/simfuzz/run.py --minutes 2 --players 4` (em is the default target now, see T2b-fuzz.md).

## NPC sync fixes (npc-bugs, 2026-10-20)
The fuzzer found four root causes behind the old "hp mismatch never repaired", "repairs continue after quiescence" and "actor position does
not converge after the link heals" failures (about 4% of 4-machine sessions). All four are fixed in the model; each has a deterministic
scenario in `tests/scenarios_npc.cpp` (named in `tests/scenarios.h`) and a mutation twin in `tests/test_twins.cpp` (an `EngineFaults` switch that
brings the bug back; the scenario must then fail).

1. **Digest keys of created actors were read as player slots** (twin `created_key_is_player`, scenario `createdActorRepairKeepsPlayerState`).
   Players are keyed `0xF0000000 | slot` in the Actors digest, but the test was `key >= 0xF0000000`, which also matched every created actor
   (`0xFF......`). Repairing a created actor therefore exported the host's copy of player `(id & 0xFF) % 8` and imported it over that player on the
   receiver: a guest that had just been hit went back to its old hp and vitals sequence (the "hp95 on the host, hp100 on the guest" failure), a
   guest-only created actor could never be removed, and a created actor the guest lacked was never delivered, so the host kept repairing it
   every report (the 119 repairs after quiescence). Now `isPlayerKey` is exactly `kMaxSlots` wide (`types.h`), `importKey` checks the slot against
   the key, and the "keys the reporter owns are never overwritten" rule applies to created actors too.
2. **A digest report that the link duplicated counted as a second observation** (twin `digest_no_seq`, scenario
   `duplicatedDigestIsNotAStableMismatch`). The host repairs only a mismatch seen in two separate reports, but a duplicated datagram of one report
   looked like the second one, so differences that were about to close by themselves were "repaired" mid-play (this is what made
   `fight_over_lossy_link` need repairs). `MDigest` carries a report number (`seqBase() + counter`, like poses and vitals); the host ignores
   numbers it has already judged. `fight_over_lossy_link` is green again.
3. **Digests hid small differences, and one key starved the others** (twin `digest_buckets_positions`, scenario `smallDriftIsHealed`, saved fuzz
   repro `scenarios/simfuzz/regress_digest_buckets_positions.json`: two ops, a guest walks into a cell and leaves). Positions were bucketed to 256
   units and health to tens, so an owner that left while its actor was still walking left the host and the other guests with different last
   positions below the bucket; nothing republishes a released actor, and no digest saw it. Positions and health are exact in the digest now.
   Exactness only works with a per-key stability rule: the host used to want the whole category's totals to repeat in two reports, so one
   actor that kept walking starved every other repair. `Mismatch` now remembers, per reporter and category, `{reporter's hash, host's hash}` per
   differing key, and a key is repaired when the same pair shows up in two separate reports.
4. **A forced commit let two machines drive one NPC** (twin `new_owner_no_wait`, scenario `forcedCommitNeverDrivesTwice`, saved fuzz repro
   `scenarios/simfuzz/regress_new_owner_no_wait.json`, three ops on a plain link; the first copy of it was recorded under profile `hell` and stopped reproducing when the combat fields
   changed the packet sizes, see T2b-fuzz.md). When the old owner does not answer a Transfer within the host's timeout the
   host commits anyway, and the new owner started its AI at once while the old owner, which had not heard of the Transfer yet, was still driving
   the actor (seen under the `hell` profile: "AI-driven on 2 machines: m0(sees owner p0 e3) m3(sees owner p3 e2)"). The new owner of a commit
   that did not see the old owner's final state now keeps its AI off (`awaitOld_`, `drivesAi`) until that final state arrives (it is rejected as
   a stale epoch, but proves the old owner stopped), the old owner leaves, or `holdAiMaxMs` (8 s) passes as a safety net.
5. **A joiner revived an owner for a corpse** (twin `join_no_tombstones`, scenario `joinerKeepsReleasedEpochs`, found by the fuzzer: profile `hell`, seed 300, two players).
   An actor that dies is untracked by the ownership service, so the full state a joiner gets had no entry for it, although the host's table keeps the epoch of its last
   Release. A late copy of an older assignment (a reordered Grant from before the death) then looked like news to the joiner, which believed the corpse had an owner and
   never matched the host's table again (nothing repairs the table). The full state now carries the host's released epochs too (`buildFullState`).

## Breach oracles (the em twins are visible to the fuzzer)
Fixing the four bugs above made clean fuzz runs green, and showed that 14 of the 22 em twin tests had only ever "passed" because the model was
broken: an injected fault changed the timing, the real bugs fired, and the sweep counted that as a catch. A fault that is harmless on its own
(a forged hit that happens to land, a lock that nobody contends for) left no trace. The model now says so itself: `Machine::breach(name, detail)`
records a rule that a test fault switched off AND whose absence changed the outcome, and `Cluster::checkAlways` reports each one as
`breach <name>: ...` (the fuzzer uses the name as the invariant). Clean runs never breach.
`forged_hit_applied` (validation off, the real checks still run to see if the hit would have failed), `non_owner_hit_applied`, `pickup_loser_kept_item`,
`loot_rolled_again`, `container_lock_ignored`, `story_npc_killed`, `stale_session_applied`, `owner_flapped` (host: an actor was taken from an
eligible owner inside the hold time; recruits are exempt), and `handoff_without_ack` (Cluster: commits minus forced commits must not exceed
the acks the host received). `onPickup` counts a second grant of one world item in `doubleApplied`. The fuzzer adds one more at the end of a
session: `companion_not_with_recruiter` (a companion recruited by a connected player must be owned by that player). Without the oracles `heal_disabled`,
`digest_buckets_positions`, `own_adopt_echo` and `drop_no_idempotency` need the fuzzer to create the situation, see T2b-fuzz.md "Ops that exist for em". `containerMatrix` read a container with `operator[]`, which inserted a zero entry into the host's
container and changed its digest; it uses `find` now. `finish()` in `scenarios_engine.cpp` counts repairs made while settling as well (a clean
link must need none), and `spawnOnce` now compares every created actor's leveled pick right after the spawn, because exact digests would
otherwise repair `pick_per_machine` silently while the cluster settles.

What is unit-tested only: the four fixes are verified in the model and the fuzzer, nothing here has run in the game.

## Fuzzer adapter fixes (synccore/fuzz/target_em.cpp, T2b's file, minimal edits)
- Dropped-item identity was an xor fold of (src, reqId) and collided across different drops: now injective.
- Item conservation is switched off after a leave or a host load (the model's own scenarios already do this).
- A guest adopting a new host session after a load now emits a Reset event, so the stale-epoch oracle forgets the old world.
- Quiescence sampling hashes every digest of every machine, which dominated the run time: it samples every 50 ms instead of every 10 ms
  (a replay of a 20-virtual-second session went from 5.4 s to 0.6 s).

## Combat (combat-core, 2026-10-21)
The model's hit checks, damage, limb crippling, deaths and kill XP are `synccore/combat`'s code now (see `docs/tools/combat-core.md`). New messages `MProjectile`, `MExplosion`,
new actions `Projectile` and `Explode`, new fault switches `deathNotIdempotent`, `limbNotSynced`, `visualShotDamages`, `blastRunsEverywhere` (twins in `tests/test_twins.cpp`,
scenarios in `tests/scenarios_combat.cpp`, group `combat`). `ActorState::limbDmg` (per-limb damage counters) travels in every hit event and in the save, not in the digest.

## Open
- None known in the model. x86 `run_tests.bat` can show SEGFAULT across many tests when another agent builds into the same
  `build\L4` at the same time; rerun when no other build is running.
