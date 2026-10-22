# loot: containers, corpses, dropped items, pickups, physics clutter (FULL-SYNC-PLAN 3.5, roadmap step 5)

Code: `synccore/loot/` (game-free rules, wire, tests), `plugin/loot/` (the live-hub service, the real xNVSE engine, the module, tests, proof script).
Wire: channel `0xB1`, docs/protocol.md section 22.
Status 2026-10-21: everything below is unit-tested game-free (a fake world with the real wire encoder, random sessions, mutation twins). **NOTHING has run in a game.**
Engine-facing code compiles into `nvcoop.dll` and is OFF by default.

## Run it
- `plugin\loot\run_tests.cmd` (shared tree `build\all`, takes the build lock; prints `ALL OK` when both real suites pass, all 54 runtime twins are caught and the proof script's
  self-test passes). It is in `tools/test_all.py` ("Loot, containers, drops, pickups, clutter ...").
- More fuzz: `set LOOT_FUZZ_SEEDS=300` then `build\all\loot_tests\loot_tests.exe --group fuzz` (300 seeds of each of the three families passed on 2026-10-21; the suite runs 14).
  Debug aids: `LOOT_FUZZ_VERBOSE=1`, `LOOT_FUZZ_TRACE=<substring>` (prints the event log of a failing seed), `LOOT_FUZZ_SEED_ONLY=<n>` (lossy family), `LOOT_FUZZ_LOSS / DUP / NOCUT`.
- In game (the helper): see "In-game proof" below.

## The rules
1. **Shared contents, host-owned.** A container or corpse has one record on the host (`LootHost`): items, a revision, the holder. Open, take and put are requests; the machine that
   acts has already changed its own engine (optimistic) and **rolls back** when the host refuses (`Action::PlayerAdjust` takes the items back or gives them back, `Action::ContainerSet`
   puts the authoritative contents into the engine copy, kept together with the player's not-yet-confirmed moves).
2. **One lock.** A second opener gets `Busy`; its menu is closed (`Action::CloseMenu`) and the player is told. The lock is leased (default 10 minutes, refreshed by every request and by a
   30 s keep-alive), released by close, by the holder leaving and by a lost lease. A holder who lost the lock without knowing (cut link) is told by the next record it gets.
3. **Leveled loot is resolved once.** `LootClient::resolved()` (a cell loaded, a corpse was made) sends the REAL items; the first sender wins, a later different one gets
   `AlreadyResolved` plus the shared record and replaces what its engine rolled. An unregistered container opened first by a guest is adopted from the guest's engine copy. When the
   cell owner is known (`LootSync::setCellOwnerHook`) a non-owner waits 3 s before resolving so the owner's items arrive first. A read that is not exact (an unresolved leveled list is in the
   base container) is never offered; it is retried 5 times.
4. **Corpses.** `LootSync::onActorDied(netId, ref)` (the actor port's died event, owner only, wired in `loot_module.cpp`) registers the corpse 700 ms later.
5. **Drops.** `OnDrop` (xNVSE native handler, wired in the module) -> `ReqDrop` -> a host-issued id in `[0xFFB00000, 0xFFBFFFFF]`, announced to everybody. The dropper binds its own local ref to the id
   (`BindDrop`), never creating it a second time; everybody else creates a ref (`CreateItem`, retried every second when the cell is not loaded there). Only the local player's drops count.
6. **Pickups are claims.** `ReqClaim`: first claim wins; the others get `AlreadyTaken` and their optimistic pickup is taken back from the inventory with a message. Placed items are learned
   at their first claim and stay taken (tombstones are saved with the host table). Everybody else's ref of a taken item is removed.
7. **Every transfer is an idempotent transaction.** Per-sender txn ids; the host replays its answer for a repeat (container records inside replayed answers are refreshed to the present).
   The guest resends an unanswered request after 1.5 s with the same txn, gives up after 60 s.
8. **The container digest is checked on close.** The close waits until every move of that container is answered; its digest is the engine copy's. A mismatch heals the closer's copy
   and is counted (`closeMismatch`, 0 in every strict test).
9. **Detect and heal.** Containers are mirrored by revision (a gap asks for the record); every 2 s each guest sends its table digest, a mismatch makes the host push the table (at most once
   per 2 s per guest); a table is only used to prune when its record counts are complete; engine copies of closed containers are audited round-robin against the shared contents.
10. **The host's engine is the authority for what scripts do.** Every 500 ms the host compares a few of its loaded unheld containers with the table and carries a difference in
    (`externalSet`). A held container is left alone (and the host's engine copy is NOT wiped; it is carried in once the holder is gone).
11. **Physics clutter.** The machine that moves a dropped item publishes its pose while it moves (at most ~15 per second), one final "at rest" pose, then nothing. The host judges owner,
    epoch and sequence and relays; a second mover takes over only from an owner that is resting. Other machines show the pose (`SetPose`).
12. **Player inventories stay per player.** Nothing in the module sends or applies a player's inventory as such (a test pins that no message kind carries one).

## Ordering rules the engine glue must keep (found by the fuzz, each has a test)
- Read an open container BEFORE handling any message from the host (a correction replaces the engine copy and would swallow a change nobody has looked at). `LootSync` does it in `receive()` and in
  the local inbox pump. Twin 141.
- After a correction was applied, re-read the engine copy and call `rebaseline` (the engine may not have been able to apply all of it). Twin 143.
- A menu closed BY the module (busy, lock lost) is not a close by the player. Twin 144.

## Parts
| Piece | Where | Role |
|---|---|---|
| `LootHost` | `synccore/loot/loot.h` | host: table, locks, ledger, claims, clutter owners, digest, save / load |
| `LootClient` | same | every machine (the host's own too): view, pending requests with resend and rollback, engine actions |
| wire | `synccore/loot/loot_wire.*` | channel 0xB1, bounded, fuzz-safe decode |
| `LootSync` | `plugin/loot/loot_sync.*` | `livewire::ILiveService`: watcher, action execution, resolve / corpse / sweep / audit / clutter timers, sidecar, status |
| `ILootEngine` | `plugin/loot/loot_types.h` | everything the module needs from the game (fake in tests) |
| `RealLootEngine` | `plugin/loot/loot_engine_real.*` | x86, xNVSE layouts, SEH reads, console writes |
| module | `plugin/loot/loot_module.cpp` | nvcoop.ini `[loot]`, `coop.loot`, OnDrop / OnActivate handlers, died listener |

## Wiring status
- `nvcoop.ini [loot] enabled=1` creates the service and installs the handlers. **dll-wiring still has to add one line** (nothing else changes):
  `hub.add(&nvcoop::loot::service(), nvcoop::loot::enabled());` in `plugin/coop/live/live_glue.cpp` (see `plugin/loot/loot_glue.h`; `NVCOOP_WITH_LOOT` is defined). The hub
  then owns the channel, the joins and leaves, the per-frame tick and the load events. Host save sidecar: `LootSync::saveState()` / `loadState()` next to the other sidecars.
- Set `[intents] pickup=0` and `container=0` while loot is enabled: the intents module (section 19) also handles pickups and container locks and the item would be handled twice.
- `LootSync::onContainerLoaded(ref)` has no caller: no verified cell-load event exists. Until one does, a container is registered at its first open (adopt) or by a caller; corpses are covered.
- `onCellReset(cell)` has no caller either (no verified engine hook, same gap as spawn-arb).

## Engine facts and TODO-RE (none guessed; each degrades to something safe)
Used: inventory totals call `0x4C8F30`, `0x4BF220` (data), `0x4BFFB0` (base container), top menu `0x714F00`, `InterfaceManager` `0x11D8A80` + crosshair ref `+0xFC`, menu id `0x3F0`
(prologues pinned and compared at start, docs/re/dialogmenu-menus-vats-inventory.md). Writes are `AddItem` / `RemoveItem <id> <n> 1` console lines on the container or the player, inside
`ApplyScope` so a guest's script gate lets them through.
- **Closing the container menu from code (the test side):** the bridge method `bot.menu.close {kind:"container"}` (plugin/bots, dev only) calls `CloseAllMenusCore` 0x7037E0, the function the
  CloseAllMenus script command runs (it walks the menu stack at InterfaceManager+0x114 and closes every menu through its vtable). `plugin/loot/tools/loot_proof.py` uses it, so the proof has no
  hand step. It is unproven in the game for this menu (static RE only); the module's own refusal close is still not wired.
- **Closing the container menu from the module (refused second opener):** no verified call. `RealLootEngine::setCloseMenuHook` is where it plugs in (`bot.menu.close`'s call would do). Without it a refused player keeps a menu that does nothing: every move
  in it is refused by the host (NotHolder) and rolled back, so nothing is duplicated, but the menu stays on screen.
- **Which ref the menu shows:** the crosshair ref when the menu becomes the top menu (assumption).
- **OnActivate parameters:** the order is not verified; the handler finds the player by ref id 0x14 and tries the other refs as the picked item (`describeItem` refuses anything that is not a world item).
- **Leveled entries in a base container list** are not resolved by the totals call: such reads report `exact=false`, so a leveled container is registered only after the engine resolved it or when a guest
  opens it (adopt takes whatever the opener's engine shows, partial or not).
- **Creating a remote drop:** `PlaceAtMe` + the hooks `setAnchorHook` / `setNewestOfHook` (the ref a console PlaceAtMe made is not readable); the stack size and the condition (`meta`) are NOT applied.
- **Physics poses:** SetPos on the ref; rotation is not applied; whether a dynamic body follows is the same open question as the actor port's.
- **On-screen messages:** only a log line and the `loot.notice` bridge event.

## Honest limits
- Per-instance data (weapon condition, mods) is not part of a stack; container ops are by base form. A dropped item carries `meta`, which nothing applies yet.
- A player who spends or moves the optimistic items faster than one round trip, and whose move is then refused, takes back less than it should (counted: `rollbackItems` vs the engine's
  short result; the fuzz allows it only in the lossy family and counts it). Nothing in the strict family ever hits it.
- Hold = the menu; there is no lock for an NPC looting a corpse (NPC inventories are the actor owner's business).
- The host's container sweep needs `loadedContainers()`, which in the real engine only knows containers that were described before (opened, resolved). A container nobody ever described is not swept.
- Containers a script changes while a guest holds them reach the table only after the holder leaves.

## Mutation twins (runtime: `FNVCOOP_MUT=<n>`)
synccore/loot (`loot_tests`, 101..137): 101 lock not enforced, 102 take/put without the lock, 103 take beyond stock granted, 104 no idempotency ledger, 105 claimed item stays,
106 claim of an unknown issued id believed, 107 drop answer not remembered, 108 close digest not compared, 109 second resolve overwrites, 110 revision gap ignored, 111 stale delta applied,
112 leaver keeps locks, 113 lock never expires, 114 id counter restarts after load, 115 no tombstone for placed items, 116 old clutter epoch accepted, 117 non-owner clutter accepted,
118 resting clutter keeps sending, 119 mover steals from a moving owner, 120 wire count unbounded, 121 wire trailing bytes, 122 refused take not rolled back, 123 refused put not rolled back,
124 lost pickup keeps the item, 125 requests never resent, 126 item-gone not broadcast, 127 deltas not broadcast, 128 script change over a holder, 129 join table without containers,
130 already-resolved without the record, 131 grant does not replace contents, 132 own drop created twice, 133 close keeps the lock, 134 close before moves are answered, 135 digest ignores
contents, 136 replaced contents forget unconfirmed moves, 137 replayed answer carries a stale record.
plugin/loot (`nvcoop_loot_tests`, 141..157): 141 no read before a host message, 142 rollback not executed, 143 no re-baseline after a correction, 144 own menu close reported, 145 inexact read
offered, 146 claimed sender believed, 147 no host sweep, 148 no audit, 149 host answers not delivered locally, 150 NPC drop counted, 151 NPC pickup counted, 152 load keeps the view,
153 session keeps scheduled work, 154 deferred creations forgotten, 155 clutter never sampled, 156 link loss keeps pending, 157 leaver not forwarded.
Each must make its suite fail; `run_tests.cmd` checks that.

## In-game proof (for the helper)
Precondition: 2 or 3 instances in one session next to a lootable container; `nvcoop.ini` on every instance: `[loot] enabled=1`, `[intents] pickup=0 container=0`; dll-wiring's one line in place.
1. `python -I plugin/loot/tools/loot_proof.py --instances 0 1 2 --container FalloutNV.esm:HEX --item Stimpak` (the container as `Plugin.esm:HEX`). Closing the container menu is a manual step the
   script asks for. PASS = every step prints PASS and the exit code is 0.
2. By hand: `coop.loot {}` on each instance (session true, same `digest`), `coop.loot {"container": <net id>}` (holder, revision, digest); `events` for `loot.notice`.
3. Report: `coop.loot {}` from every instance (`client.rollbacks`, `client.busy`, `client.claimsLost`, `hostStats.closeMismatch`, `tick.actionsFailed`), the log lines starting `loot:`.
   Anything in `actionsFailed` or a non-zero `closeMismatch` in a clean run is a finding.
