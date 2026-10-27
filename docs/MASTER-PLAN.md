# FNV Full Co-op - Master Plan v1 (2026-10-15)

Sources: 00-test-harness-design, 01-prior-art, 02-engine-re, 03-testing, 04-quest-data, 05-nvmp-intel, ../analysis/FINDINGS.md

## North star
2–4 players play the entire New Vegas story together - one shared world, one shared story state,
every quest, every ending - and **every claim of "works" is proven by automated tests**.

## Architecture

```
 FalloutNV.exe (host)                               FalloutNV.exe (client)
 └─ nvcoop.dll (ours, xNVSE plugin)                 └─ nvcoop.dll
     ├─ hooks: SetStage, objectives, globals,           ├─ applies deltas (no script re-run)
     │  rep/karma, topic result scripts,                ├─ suppresses local quest-script authority
     │  script-exec tracer, effect capture              ├─ guest intents (dialogue, activate, triggers)
     ├─ sync core (game-free C++)  <── own transport (ENet) ──>
     ├─ actors, avatars, combat, VATS, loot, world/time (all ours)
     ├─ plugin/nvmp: optional, only patches NV:MP when a player also runs it
     └─ nvbridge (test API, JSON over pipe)
```

Update (2026-10-27): the mod no longer rides on NV:MP for anything. Players (avatars), NPC motion, combat, loot
and the world sync through our own layers and our own transport, and every in-game test runs without NV:MP
installed. NV:MP was a starting point only: we studied how it hooks the game. Its networking is closed source and
none of its code is in this repo. `plugin/nvmp` stays as an optional compatibility module for players who run
NV:MP at the same time.

- Script variables only via whitelist + effect capture.

## Architecture decision: hybrid (owner, 2026-10-15)
Story layer (synccore, hooks, dialogue, coop runtime, our own transport) is 100% ours and must stay NV:MP-independent
(only plugin/nvmp/ may reference NV:MP). Avatars / NPC motion / combat ride on NV:MP for now. Optimise for the best
end product; revisit an own actor-sync layer once story co-op is playable.
Superseded (2026-10-27): the own actor-sync layer was built (avatars, actors, combat, VATS, loot, world). Nothing
depends on NV:MP now; only plugin/nvmp/ may reference it, as an optional compatibility module.

## Owner decisions (locked 2026-10-15)
| Question | Decision |
|---|---|
| Karma | **shared** (party = one Courier) |
| Faction reputation | **shared** |
| Quest items | duplicated to every party member |
| Caps/XP quest rewards | every player gets the full reward |
| Dialogue | initiator chooses, everyone sees the line + result |
| Party spread | free roam; story-critical moments require party in the same area |
| Faction membership/rank (AddToFaction/SetFactionRank on player) | **shared** - coordinator decision, follows "party = one Courier" |
| Companions | owned by recruiter; commands (wait/follow/dismiss/trade) are owner-only, an exception to initiator-picks |
| Follower cap | per recruiting player (1 humanoid + 1 critter each), configurable party total, default 4 |
| Waiting-companion auto-fire | only if not in the same cell as ANY player of the party |
| Companion recruit perks | owner only |
| Quest-reward perks (e.g. Power Armor Training) | every player (follows "full reward to everyone") |
| Casino winnings / gambling | per-player (not a quest reward) |
| Journal/progress | AddTopic, AddNote, map markers: shared. ForceActiveQuest (UI focus): per-player. Achievements/challenges: per machine |
| Endings, time skips, cinematics | host decides; all players locked and warped together; ending slideshow for all; game time host-authoritative; AutoDoc/implant fades per-player |
| Per-machine quest scripts (achievements, rumble, DLC setup, fades) | run locally on every machine via new rule kind GuestLocalRun (allow-list) |
| Patch04Quest00 (DLC-uninstall cleanup) | excluded with the debug quests |
| One-way transitions (DLC entry, Fort→Dam, satellite, Sierra Madre) | party barrier (all players present and agree), then all players warped together; nobody is left behind |
| Story effects on the Courier's body (OWB brain/heart/spine, AutoDoc restores) | applied to every player |
| Teleporters (OWB) | move the firer plus party members in range |
| Endings (Think Tank, Dam, etc.) | wait until the whole party is in the ending area |
| Stage merge for mutually exclusive outcomes | rule kind ExclusiveStages: first committed wins |
| Wait/sleep | needs party consent; otherwise refused with a message |
| Shared player storage containers | per-player |
| Leveled-list rewards | resolve on host, send the actual items (never re-roll on clients) |
| Max players | (open) default: 4 |

## Workstreams (parallel agent lanes)
Each lane owns a directory, has a written interface contract, and its own test gate. Lanes must not edit each other's dirs.

| # | Lane | Deliverable | Gate | Depends on |
|---|---|---|---|---|
| L1 | **nvbridge** | xNVSE plugin: JSON-RPC pipe - exec, state(), waitFor, events, talk, screenshot, autoload save | T1 smoke: boot→load→exec→state round trip | game installed |
| L2 | **multi-instance + runner** | launcher for N isolated instances (per-instance dir, INI, saves, mutex bypass/Sandboxie), job queue/locks, pinned-hash check | 2 instances alive 10 min, both answering nvbridge | L1 |
| L3 | **quest data** | Python ESM parser → quests.json (stages, objectives, SCTX scripts, conditions, dialogue links) + risk classifier + test generator | parser matches xEdit dump for 50 quests; exact counts | game installed |
| L4 | **sync core** | game-free C++ lib: wire format, effect transactions, story log, merge rules, share policies, echo guard; sim harness with N fake clients, virtual clock, lossy link | T0 + seeded chaos fuzz, mutation twins fail | - (start now) |
| L5 | **engine hooks** | verify 02-engine-re addresses in Ghidra on FalloutNV.exe; hooks for SetStage, objectives, globals, rep, karma, topic result scripts, script-exec tracer; **effect capture** | T1: each hook fires + captured effects match a known script | L1 |
| L6 | **NV:MP integration** | signature scanner; port Companion patches to 9.4; patch encounter manager so quest/persistent actors sync host-owned; side-channel ENet | T2: Doc Mitchell exists once, same state on both | L1, L2 |
| L7 | **state snapshot** | canonical world-state dump (shared vs per-player sections), diff tool | snapshot stable across 2 dumps of same save; diff catches injected change | L1 |
| L8 | **dialogue** | initiator-driven dialogue sync, spectator subtitles, speech-check policy | T2 scenario: Goodsprings dialogue tree | L4, L5 |
| L9 | **scenarios & CI** | per-quest generated tests, SP-vs-coop differential runner, coverage dashboard | nightly run produces dashboard | L3, L7, L2 |

## Test gates (definition of done)
1. T0 unit + sim tests green, including a **mutation twin** that must fail.
2. T2 two-client scenario green, incl. chaos profile (latency/loss/rejoin).
3. Invariant monitors silent: no non-host quest-script exec, no duplicate reward, no ghost duplicate NPC.
4. Snapshot convergence: host.shared == client.shared.
5. SP differential: co-op shared state == singleplayer trajectory at checkpoints.
6. Coverage dashboard does not regress. Goldens are protected (agents can't loosen assertions).

## Milestones
| M | Proof | Rough time |
|---|---|---|
| M0 | Tooling: nvbridge + 2 instances + quests.json + sync-core sim | 1–2 wk |
| M1 | **Goodsprings**: Ain't That a Kick in the Head + Back in the Saddle + By a Campfire - shared stages, objectives, items, Doc Mitchell/Sunny shared | 3–5 wk |
| M2 | Primm + Novac + all of Act 1 (Benny), reputation/karma per policy, late-join + rejoin | 2–3 mo |
| M3 | Main quest to every ending, faction questlines, stage-walk coverage ≥ 80% | 4–6 mo |
| M4 | Side quests, DLCs (Dead Money, HH, OWB, LR), 100% stage-walk, soak tests | 6–9+ mo |

## Top risks
- NV:MP updates break patches → signature scanning + keep our logic in our own plugin.
- Host simulates only loaded cells → party tether / cell-owner handoff.
- Engine thread safety → all engine calls marshalled to main thread.
- Flaky game-in-loop tests → wait-for-quiescence, deterministic saves, retries flagged not hidden.
- Agents gaming tests → protected goldens, mutation twins, owner reviews gate changes.

## Immediate next actions
1. **Owner:** free disk space so Steam can finish installing FNV; run `rea setup --client claude_code --yes`; decide the policy table.
2. Create `fnv-coop/` git repo with lane directories + interface contracts (CONTRACTS.md) + test runner skeleton.
3. Start lanes that don't need the game: **L4 sync core**. Once installed: L1 → L3/L5/L7 → L2 → L6.
