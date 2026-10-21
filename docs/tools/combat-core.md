# combat-core: game-free combat rules (FULL-SYNC-PLAN 3.4)

Code: `synccore/combat/` (`combat.h`, `combat.cpp`, `tests/`), the fake engine integration in `synccore/enginemodel/src/machine_combat.cpp`
(+ hooks in `machine_sim.cpp`, `machine_link.cpp`, `machine_host.cpp`, `machine_intent.cpp`) and the fuzzer ops in `synccore/fuzz/`.
Status 2026-10-21: everything is unit-tested and fuzzed game-free. **Nothing here has run in a game.** The engine glue is `plugin/combat/`
(combat-hooks), which uses these types; it has its own tests.

## Run it
- `synccore\combat\run_tests.cmd` (shared tree `build/all`, takes the build lock through `tools/build/build.py`): builds `combat_tests` and
  `fnvcoop_engine_tests`, runs the rules suite once for real and once per runtime mutation twin (`FNVCOOP_MUT=301..365`, nothing recompiled),
  then the engine-model `combat` group and the 8 engine twins that guard it. Prints `ALL OK`. It is in `tools/test_all.py`.
- Engine model only: `build\all\synccore\enginemodel\fnvcoop_engine_tests.exe --group combat` (and `--group twin --name death_not_idempotent` ...).
- Fuzz (em target, 4 machines): `python -I tools/simfuzz/run.py --minutes 2 --players 4 [--profile bad_wifi|hell]`.

## What is in `combat.h`
All of it is game-free C++17 with a symmetric `io(Archive&, T&)` wire for every struct (`kCombatWireVersion`). Engine questions go through
`ICombatWorld` (who exists, where, who owns it, weapon table, projectile lookup, line of fire), so a fake can answer them.

| Piece | Rule |
|---|---|
| `HitEvent` | attacker, victim (`Entity` = player slot or actor NetId), damage, limb, impact point, weapon, ammo, projectile id, explosion id, fire time (host ms), attacker epoch, claimed range |
| `HitValidator::judge` -> `HitVerdict{apply, reason, forwardTo, resync}` | run by the **victim's owner**. In order: victim known, **this machine owns the victim** (else `NotVictimOwner` + `forwardTo`), no handoff in flight, **idempotent by hitId** (bounded memory), victim alive, damage within the weapon/global cap, hit not too old and not from the future (32-bit wrap safe), **a player attacker is the sender**, an NPC attacker is **owned by the sender at the NPC's current epoch** (older = `StaleAttackerEpoch`, newer = `FutureAttackerEpoch` + `resync`), no self hit (explosions may hurt the thrower), projectile matches its shooter, then geometry (weapon table range wins over the claimed one; same/adjacent cell; distance with slack, more slack for replica attackers; impact point near the victim plus its radius; line of fire; explosion hits are checked against the blast radius instead), and last the **rate of fire per attacker and weapon** (`minIntervalMs`, `pellets`, burst slack). Rejected hits do not spend the rate budget. |
| `LimbState`, `applyLimbDamage`, `mergeLimbMask` | crippling is **accumulated** limb damage against a per-limb threshold (torso never); the crippled mask only grows (union); `restore()` heals explicitly |
| `applyHit` -> `DamageOutcome` | health, limb crippling, death, and the story rule: named/essential victims floor at 1 health and never die from damage |
| `DeathLedger::judge` -> `DeathVerdict` | a death is declared **once**: by the owner at the current epoch, by the story host (`StoryHost`, host only) for named NPCs, or by the player itself. Idempotent by actor id *and* by declaration id (a replayed old declaration cannot kill a resurrected actor); a corpse the ledger never heard of is `AlreadyDead`; `markDead` seeds it from a world that already has corpses |
| `ProjectileRegistry::onSpawn` -> `ProjectileVerdict{role}` | only the shooter's owner may spawn; the owner's copy is `Authoritative`, everyone else's is `VisualOnly` and `mayDealDamage(role)` is false; idempotent, epoch checked, speed/origin/rate bounded, expires |
| `ExplosionLedger::judge`, `planExplosionHits` | only the source's owner declares (a scripted blast is the host's); once per id; the owner plans **one HitEvent per victim in range, falloff by distance, id derived from (explosion, victim)** so replays are dropped; each hit goes to its victim's owner |
| `attributeKillXp`, `XpLedger` | killing blow (a companion's kill credits its recruiter; NPC/environment kills pay nobody) or an equal party share (remainder to the killer); one death pays once |
| `HostilityBook` | crime/hostility is host-authoritative: clients report `CrimeReport`s, the host decides `SetEnemy`/`SetAlly` once per faction pair (attacking something already hostile, unwitnessed crimes and own-faction crimes do nothing); clients accept effects only from the host, once; `consoleLineFor` gives the fallback console line (the optional flag arguments of SetEnemy/SetAlly are TODO-RE) |
| `CombatMsg`, `encodeMsg`/`decodeMsg`, `CombatNode` | optional ready-made wire bundle (`0xCB`, version, kind) and one object that holds all ledgers of a machine |

## Mutation twins (runtime, `FNV_MUT(n)` in `combat.cpp`, numbers 301..365, every one must fail the suite)
301 no hit idempotency, 302 any machine resolves any hit, 303 dead victims take hits, 304 damage unbounded, 305 claimed range unbounded, 306/315 no age
or future check, 307 attacker need not be the sender, 308 NPC attacker need not belong to the sender, 309/318 attacker epoch (stale/future) ignored,
310 cells ignored, 311 distance ignored, 312 impact point ignored, 313 no rate of fire, 314 self hit, 316 dead attacker, 317 handoff ignored, 319 claimed
range beats the weapon table, 320 projectile shooter mismatch, 321 explosion radius ignored, 322 line of fire ignored, 323 torso crippled, 324 limb damage
not accumulated, 325 mask replaced instead of merged, 326 named victims die, 327 corpses take damage, 328 off-by-one at zero, 329 non-owner declares death,
330 named NPC dies by owner declaration, 331 duplicate death accepted, 332 stale death epoch, 333 replayed declaration, 334 story source from a non-host,
335 other player declares a player's death, 336 non-owner spawns a projectile, 337 visual copies are live, 338 projectile spawn not idempotent, 339 speed
unbounded, 340 origin unbounded, 341 projectile epoch, 342 projectile rate, 343 non-owner declares an explosion, 344 every machine plans the damage,
345 blast radius ignored, 346 no falloff, 347 victims share one hit id, 348 other cells hit, 349 explosion not idempotent, 350 explosion radius unbounded,
351 killing blow pays everyone, 352 companion kill not credited, 353 party share not split, 354 xp remainder lost, 355 xp paid twice, 356 hostility from
a non-host accepted, 357 clients decide hostility, 358 attacking a hostile is a crime, 359 escalation not idempotent, 360 unwitnessed crimes count,
361 peace does not undo war, 362 hostility effect replay applied twice, 363 a corpse the ledger never heard of can die again, 364 wrong channel byte/version
accepted, 365 trailing bytes accepted.

## Fake-engine integration (the fuzzer exercises it)
`Machine::onHit` now judges every hit with `combat::HitValidator` (the model's old inline checks are gone), deaths go through `DeathLedger`, damage through
`combat::applyHit` (limb counters travel in every hit event, so a new owner continues them), kill XP through `attributeKillXp`. New messages
`MProjectile` and `MExplosion`; new actions `Projectile` and `Explode`. The old twins `hit_no_validation` and `hit_by_non_owner` still pass (and fire their
breach oracles) through `judgeAs`/the probe copy.
New fault switches (`EngineFaults`), each with a scenario in `tests/scenarios_combat.cpp`, a twin in `tests/test_twins.cpp` and a name in the fuzzer
(`--bug`): `deathNotIdempotent` (`death_not_idempotent`, breach `death_declared_twice`), `limbNotSynced` (`limb_not_synced`, breach `limbs_not_published`),
`visualShotDamages` (`visual_shot_damages`, breach `visual_shot_damaged`), `blastRunsEverywhere` (`blast_runs_everywhere`, breach
`blast_applied_by_non_owner`). An always-on breach `damage_applied_by_non_owner` fires when anything but the owner changes an actor's health.
Scenarios: `limbsSync` (crippling by accumulated damage, counters survive a handoff, every machine agrees), `deathHappensOnce` (two lethal hits in one
instant), `storyKillRace`, `projectileVisualOnly`, `blastViaOwners`, `killXpOnce` (killing blow and party share, paid once), `forgedCombatRejected`
(stale, future, spoofed, forged-NPC and far hits rejected, a real one lands).
Fuzzer ops (em only, a separate Rng so older sessions are unchanged): `projectile`, `explosion`, `limbhit`, `simulkill` (two lethal hits and, half of the
time, a host story kill in the same instant). The fuzz view no longer compares a **dead** actor's resting position: the digest never did ("ragdolls are visual
only"), and a story `KillActor` kills each machine's copy where that copy stands, so the positions differ by the interpolation error.

## Fuzz results (2026-10-21, final re-run after the other tracks' changes, em target, 4 machines, 15 s sessions, default seeds)
plain 2 min: 175 sessions, 0 violations; bad_wifi 2 min: 173 sessions, 0 violations; hell 2 min: 191 sessions, 0 violations.

## Honest limits
- The numbers (limb thresholds, slack, rate windows) are defaults, not tuned against the real game. The damage a hit carries is whatever the attacker's
  machine computed; the victim's owner checks bounds, not the full FNV damage formula.
- Line of fire is an `ICombatWorld` hook; the fake engine always says yes. `lineOfFire` has no engine implementation yet (TODO-RE: raycast function).
- `SetEnemy`/`SetAlly` have no `Effect` wire kind (adding one means changing `effect.h`, `wire.cpp` and the protocol table); `HostilityEffect` has its own
  wire and the glue applies it with a console line.
- The XP value of a kill (50 in the model) and the party-share setting come from the glue / host settings.
