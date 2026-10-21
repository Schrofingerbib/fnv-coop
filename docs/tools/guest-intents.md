# Guest intents (roadmap step 8, FULL-SYNC-PLAN 3.7)

Owner: guest-intents agent. **Everything here is unit-tested game-free. Nothing has run in a game.** The x86 glue compiles into `nvcoop.dll` and stays
inert until `nvcoop.ini` `[intents] enable=1`.

What it does: every interaction a guest has with the shared world (activate, trigger enter / leave, pickup, hit, dialogue start, terminal use,
container open / close) is forwarded to the host as an **intent**. The host runs the real interaction inside a capture scope with the acting-player
context set to the guest's **puppet**, and commits whatever comes out with the guest as the txn origin. Rewards (`Recipient::origin()`) therefore
reach the guest and only the guest, and `player` inside every script block that runs is the guest's puppet, not the host.

## Run

```
plugin\intents\run_tests.cmd        # build (shared tree build\all) + 47 tests + 25 runtime twins + proof self-test + pin regeneration check; prints ALL OK
python -I plugin/intents/tools/intents_proof.py --instances 0 1 2 --door .. --item .. --item-base .. --container ..    # in-game, NOT RUN
```
Registered in `tools/test_all.py` ("Guest intents ...").

## Files (`plugin/intents/`)

| File | What |
|---|---|
| `intent_types.h` | `Kind` (9), `Result` (15), `Intent`, `Verdict`, `Config` (the `[intents]` section) |
| `intent_wire.{h,cpp}` | channel 0x8D codec (docs/protocol.md section 19); every truncation, bit flip, unknown enum and over-long path rejected |
| `intent_engine.h` | `IIntentEngine` / `IActingScope`: everything the module needs from the game, so everything above it is unit-testable |
| `intent_policy.{h,cpp}` | guest side: what is forwarded and whether the local engine call goes on (`decideActivate`, `decideEvent`); host side: reach, stand-in matrix, kind switches; the ini reader |
| `intent_host.{h,cpp}` | the host pipeline: join check, exactly-once ids, token-bucket rate limit, target resolution, acting-player choice, reach, claims, container locks, trigger cooldown, verdicts |
| `intent_client.{h,cpp}` | the guest: observers, double-click coalescing, pending list with timeouts, container-menu watcher, verdict handling (close menu, "already taken", open the redirected container) |
| `container_redirect.{h,cpp}` | per-slot storage containers from `nvcoop_intents.json` |
| `intent_service.{h,cpp}` | glue to the real coop `Runtime` (mux channel, `Runtime::runAsGuest`, story-channel executor takeover, leaver detection) and the `SaveGate` |
| `engine/game_engine.{h,cpp}` | x86: `IIntentEngine` over xNVSE (reads under SEH, console lines, `MarkBaseExtraListScriptEvent`, terminal entry walk) |
| `engine/intents_module.cpp` | x86: the NVCOOP module: ini, observers, the `TerminalEntryRun` and `CanSaveNow` hooks, bridge RPCs, hot-reload state |
| `engine/intents_addrs.h` | GENERATED pins (`tools/gen_intents_addrs.py` reads `<reference exe>`) |
| `testing/fake_engine.h`, `tests/*` | fake game + 47 tests (`test_intents_core.cpp`, `test_intents_service.cpp`, `test_intents_pins.cpp`) |
| `tools/intents_proof.py` | the in-game proof (not run) and its self-test with 6 injected defects |

Additive edits outside the folder: `plugin/coop/coop_runtime.{h,cpp}` (`Runtime::runAsGuest`, `Runtime::active()`, `Runtime::setDialogueRunner`), `plugin/CMakeLists.txt` (lane list),
`tools/test_all.py`, `docs/protocol.md`.

## How a guest interaction travels

```
guest   L5 activate / script-event observer  ->  IntentClient.decide  ->  Intent on 0x8D  (local call goes on, or is suppressed for pickups / triggers)
host    Mux handler -> IntentHost.handle: joined? new id? rate? kind on? target loaded? acting actor? in reach?
        -> Runtime::runAsGuest(slot, ...)  [origin forced, acting hook on]
              -> IIntentEngine::beginActing(puppet, slot, Cause{Intent, target, kind})   [ActingPlayerScope + ScopedCapture]
                    activate / raiseEvent / runTerminalEntry / disableRef + record(AddItem origin)
           (scope closes: the txn is emitted with the guest as origin)
        -> Verdict on 0x8D back to the guest
```

| Kind | Guest decision | Host action |
|---|---|---|
| Activate (door, switch, lever, furniture, terminal) | forward, local call goes on (animation, menu) | engine `Activate` with the puppet as activator inside the scope (OnActivate blocks run with `player` = puppet) |
| PickUp (world item) | forward, **suppress** the local pickup | first claim wins (`Taken` for everyone else); the item ref is disabled and an `AddItem{base, count, origin}` is recorded; `OnAdd` blocks run as the puppet. Nothing touches any actor's inventory |
| ContainerOpen (chest, dead body) | forward, local menu goes on | one user at a time (`Busy` + holder), 120 s lease, released by ContainerClose, the leaver, or the lease; the OnActivate half runs as the puppet when there is one. Shared storage ids in `nvcoop_intents.json` are answered with a per-slot container the guest opens instead |
| TriggerEnter / TriggerLeave | forward, **suppress** the local event | `MarkBaseExtraListScriptEvent(puppet, trigger, mask)`; 800 ms per-player cooldown against re-fires |
| Hit (`[intents] hit=1`, off by default) | forward, local goes on; actors are never forwarded | OnHit on non-actors only; the combat channel raises OnHit for actors, a second path would run the blocks twice |
| TerminalUse | the `TerminalEntryRun` hook computes the entry's index path and forwards; local call goes on | the entry's result script runs with the puppet acting (`TerminalEntryRun`, pinned) |
| DialogueStart (`[intents] dialogue=1`, off by default) | routed to the 0xD1 dialogue adapter (`DialogueNode::requestStart`), never run as an activation | pre-flight verdict if sent on 0x8D: Denied for a dead NPC |

**Who acts for the guest (host side):** the guest's puppet (plugin/avatar, through `livewire::glueAvatarRef`), else NV:MP's ghost, else the host's own
player as a **stand-in** (policy limited): `Activate` as the host's player would pop a menu on the host's screen for containers, terminals, NPCs
and unknown targets, so the stand-in is refused (`NoPuppet`) for those unless `standin_unsafe=1`. The v1 behaviour of coop_module (stand-in for
everything) is gone as soon as `[intents] enable=1`: the service takes the story-channel executor over, so `forward_activate=1` guests get the same
checks. Reach is checked against the puppet only (the stand-in's position is meaningless) and only when both positions are known.

**Guards:** per-player token bucket (`rate_burst`, `rate_per_sec`), exactly-once ids per session (a duplicate gets the SAME verdict again and runs
nothing), bounded claim table (`claim_memory`), leased locks, 64 intents in flight per guest, a datagram from a slot that is not a joined player is dropped.

## `nvcoop.ini`

```
[intents]
enable=0              ; master switch, default OFF (every live-layer part is)
activate=1 trigger=1 pickup=1 terminal=1 container=1
hit=0                 ; see the table: opt-in
dialogue=0            ; DialogueStart forwarding; needs L8's engine glue (not wired in this build)
dialogue_results=1    ; host: run a guest conversation's INFO result scripts (replaces coop_module's stub runner)
standin=1             ; the host's player may stand in for a guest without a puppet ...
standin_unsafe=0      ; ... also for targets that open a menu for the player (container, terminal, NPC)
require_puppet=0      ; never stand in
check_reach=1  reach_activate=450 reach_pickup=450 reach_trigger=4000 reach_container=450 reach_terminal=450 reach_hit=6000
save_gate=1           ; enforce the shared SaveSuppress flag (CanSaveNow hook)
rate_burst=40 rate_per_sec=20 lock_lease_ms=120000 trigger_cooldown_ms=800 pending_timeout_ms=6000 coalesce_ms=250 claim_memory=4096
redirect_file=nvcoop_intents.json
```
Environment `NVCOOP_INTENTS_<KEY>` beats the ini. Numbers are clamped; a bad boolean keeps the module off and says why (`intents.status` `note`).

Bridge RPCs: `intents.status`, `intents.submit {kind, ref, path?}` (guest), `intents.host.run {slot, kind, ref}` (host, runs an intent as if that player had
sent it: single-instance proof), `intents.cansave` (asks the engine's CanSaveNow through the gate, saves nothing).

## SaveSuppress enforcement (internal lane notes "SaveSuppress enforcement (L5)")

`SaveSuppress` was accepted and stored but nothing stopped a save. `intents_module.cpp` hooks the engine's `CanSaveNow` (0x850FE0, thiscall ret 4, pinned
from the exe, the same function the bridge's direct save asks first); the `SaveGate` returns false while `saveSuppressedBy(Runtime)` is true (guest: the
state it applied; host: its own world), at most one HUD line per 5 s. Manual save, quicksave and autosave all ask `CanSaveNow`: **assumed, not proven**
(the in-game proof step 7 checks the function itself, not each caller).

## Honest status

| Part | State |
|---|---|
| wire, policy, host pipeline, client, redirect, SaveGate | unit-tested, 25 runtime twins caught, real Runtime with a host and two guests over ENet loopback |
| `runAsGuest`, `Runtime::active()`, `setDialogueRunner` | unit-tested (nesting, throw safety, stop; a guest conversation's Begin and End scripts run once each as that guest, paid to the initiator only) |
| engine glue (`game_engine.cpp`, module, two hooks) | compiles x86, pins match a reference exe (checked by the tests when the exe is present), **never executed** |
| acting-player resolution inside real script blocks | relies on L5's `ActingPlayerScope` (`nvhooks::actingPlayer()` consumers); whether the engine's `player` operand really resolves to the puppet is L5's open item |
| terminal entry run via `0x501830(entry, terminalRef)` | from docs/re (static); the argument meaning is not proven in a game; the host answers `Unsupported` if the walk finds no entry |
| puppet map | `glueAvatarRef(slot)` needs `[live] avatars=1`; without it every guest is a stand-in |

### Known limits (not hidden)
* **Stand-in slot:** while the host's player stands in, `nvhooks::actingSlot()` reads 0 (`registerPlayerSlot(0x14, slot)` would re-map the host's own player for
  good). A companion a guest recruits that way gets owner 0. With a puppet this does not happen.
* **nvhooks slot table holds 8 entries** and never forgets a ghost ref: every puppet respawn (each save load) uses one. After 8 distinct puppet refs
  `actingSlot()` falls back to 0 for new ones. Needs `ActingContext::clearSlots()` exposed by L5 (`nvhooks::clearPlayerSlots()`), then call it from the
  module on `PostLoadGame`. Not done here (L5's files).
* **Disabled flag:** `kGoneFlags` uses 0x800 (GECK "initially disabled"), which is not in xNVSE's header. A wrong bit only costs the `Taken` verdict
  for an item the HOST picked up itself; guests are still protected by the claim table (there is a test where the flag is invisible).
* **Combat flag:** `TargetInfo::inCombat` is always false (no verified flag), so `DialogueStart` denies dead NPCs only.
* **Container contents** (take / put) are the loot lane's; this module owns the open / close lock and the OnActivate half.
* **DialogueStart** only reaches the 0xD1 adapter; L8's `EngineGlue` (talk veto, topic list, INFO pick) is not instantiated in the plugin, so a guest conversation
  is not end to end. See the wiring ledger.
* **Terminal hook** is installed on the first guest session, not at load; a terminal pick before that is not forwarded.
* The guest-side `OnTrigger` (every frame inside a volume) and `OnAdd` / `OnOpen` / `OnClose` events are deliberately not forwarded.

## Wiring ledger (internal lane notes, L11.md, L12.md audited 2026-10-21)

Checked against the code, not the docs:

| Item (doc) | Result |
|---|---|
| L11 wiring 1-3, 5: router bridge, 0xE5 slide channel, SlideshowEnd, INFO reader | **already wired** in `coop_runtime.cpp` (`enableUi`, `slideHost()`, `uiSlideEnded`, `installInfoReader`); `L11.md` "Wiring L10 still has to do" is stale |
| L11 wiring 4: Activate observer | not needed (the doc says so) |
| L12 `setScriptPackageObserver` | **wired** (`world_hooks.cpp`) |
| L12 IgnoreCrime / SetPCEnemyOfFaction / ClearFactionPlayerEnemyFlag | **hooked** (`world_hooks.cpp`, per-machine `emitLocal`) |
| GuestLocalRun per-machine effects | **already done**: `h_ScriptExec` mutes the capture hub (`pushMute`) around allow-listed scripts, so their effects never reach the wire; the 35-script list loads from `nvcoop_guest_local_run.json` (hooks_module). `rules.json` has no `local-machine` quest, so `RulesEngine::guestLocalQuests()` is empty |
| L10 SaveSuppress enforcement | **done here** (SaveGate + CanSaveNow hook) |
| L10 guest `Activate` / `PickUp` / `TriggerEnter` forwarding with the ghost / puppet | **done here** (the executor takeover; TriggerEnter now runs through `MarkBaseExtraListScriptEvent`) |
| L10 per-slot container redirection (protocol.md section 12 open item) | **done here** (`container_redirect`, needs `nvcoop_intents.json` data) |
| L10 `SetPlayerControls` mask granularity | **open**: coop_module locks all seven classes; a granular controller needs coop_module's frame code (dll-wiring's file) |
| L10 ScenePass auto-detection (the menu) | **open**: only `scenepass=<EDID>:<stage>` exists |
| L8 `ResultRunner` stub (`coop_module.cpp` `h.run` returns 0): the host never ran a guest conversation's INFO result scripts | **done here**: `IntentService::runDialogueResult` replaces it through `Runtime::setDialogueRunner`; it calls `TESTopicInfo::RunResultScript` (0x61F170, pinned and hooked by L5, which opens the `DialogueResult` capture scope) inside `Runtime::runAsGuest(initiator)` with the initiator's puppet as the acting player; returns the committed story seq. Answers `unsupported` (nothing runs) when L5's hook is not installed. The guest half (L8 `EngineGlue`: talk veto, topic list, INFO pick) is still not instantiated in the plugin |
