# T6a-actions: bot action layer (tools/bots/)

Status: Python API, fake bridge, in-process input driver (C++), engine-glue bridge methods, tests and mutation twins done and green (`python -I tools/bots/run_tests.py` prints ALL OK; wired into tools/test_all.py).
Nothing in the real game has been run: every console line and engine call is UNVERIFIED until the lead's first in-game run. C++ tests: `plugin\bots\run_tests.cmd` (41 mutants).

No OS keyboard or mouse input anywhere. Everything goes through nvbridge (`exec`, `ref.info`, `activate.ref`, `world.query`, `ui.dialogopen`, `ui.closedialog`, `state`, `plugins`).

## Using it
```python
sys.path.insert(0, "<repo>/tools")
from bots import Bot, FakeBridge, World, ActionFailed
bot = Bot(nv, "host")            # nv = NvBridge (real) or FakeBridge (game-free)
r = bot.goto("FalloutNV.esm:104C79", radius=100)   # r.ok, r.reason, r.elapsed_s, r.data
```
Each action returns `ActionResult`. `Bot(strict=True)` raises `ActionFailed` instead. A dead game (ConnectionLost / GameCrashed) is never turned into a failed action; it propagates.
Refs are `"Plugin.esm:HEX"`, bare hex (runtime FormID), or int. `None` means the player. `bot.log` keeps every result.
Failure reasons: `timeout`, `not_found`, `dead`, `postcondition`, `unsupported`, `stuck`, `other_cell`, `locked`, `not_enough`, `not_in_inventory`, `no_dialogue`, `already_in_dialogue`, `rpc_error:<msg>`.

## Actions and their postconditions
| Action | How | Postcondition |
|---|---|---|
| goto(target, radius, timeout_s, mode) | walk: stepped `player.SetPos` at 220 u/s (exercises position sync); teleport: `player.MoveTo` / SetPos | within radius, same cell; walk detects "stuck" and refuses other cells |
| follow(ref, keep, duration_s) | repeated goto | ends within 1.5x keep |
| wait, wait_until | clock | predicate true |
| equip / unequip(item) | `player.EquipItem` / `UnequipItem` | `GetEquipped` reads 1 / 0 |
| aim_at(ref) | `SetAngle Z/X` computed from positions | `GetAngle` within 3 deg |
| fire(ref, shots, expect_hit) | `bot.fire`, console fallback | fired >= shots; with expect_hit the target's health drops |
| reload, vats_target | `bot.reload`, `bot.vats` | bridge confirms |
| activate(ref) | `activate.ref`, console fallback | engine accepted |
| open / close(door) | activate | `GetOpenState` open / closed; locked doors fail with `locked` |
| open(container/terminal) | activate | `bot.menu` kind (unsupported without it) |
| take / put(container, item, count) | `RemoveItemTarget` | destination +count AND source -count (catches duplication) |
| pickup(ref, item?) | activate | world ref gone or disabled; item count +1 if given |
| drop(item, count) | `player.Drop` | count -n; `data.ref` is the dropped world ref (`source` event via OnDrop, or `scan` via before/after dev.refsOf, or None) |
| talk(npc) | activate | `ui.dialogopen` true |
| choose_topic(info) | `bot.say` | bridge reply `said` |
| end_dialogue | `ui.closedialog` | `ui.dialogopen` false |

Queries: pos, cell, alive, health, inventory_count(item, holder), quest_stage, dialogue_open, door_state, locked.

## Unverified grammar (fix in tools/bots/primitives.py only)
All of primitives.py except `GetStage`. In particular: `player.SetPos/SetAngle/GetAngle`, `EquipItem/UnequipItem/GetEquipped`, `RemoveItemTarget item target count silent` argument order, `Drop`, `GetOpenState` values (1 open, 3 closed), `GetLocked`, the FLAG_DISABLED bit 0x800, Health = actor value 16 for `world.query p1`, angle sign conventions in `heading_deg`, and whether hex refs work as `HEX.Cmd` prefixes and inside `printc` expressions. Update fake.py to the same grammar if you change a string.
Walking is a stepped SetPos: it does not path around walls, so use it inside open areas and teleport otherwise.

## In-process input driver and bridge methods (plugin/bots, ini `[dev] bots=1`) - BUILT, NOT RUN IN GAME
Nothing below has run in the real game; every engine address was read statically and each live call is gated.
To turn it on in test instances, provision with `python -I tools/runner/run.py provision --n 2 --ini dev.bots=1`. Provisioning rewrites nvcoop.ini, so a hand edit is lost.
**Input driver.** `plugin/bots/bots_module.cpp` detours `OSInputGlobals::GetControlState(ctrl, state)` (thiscall 0xA24660, `ret 8`, prologue pinned in
`plugin/bots/bots_addrs.h`, stolen 6 bytes, restored by the hot-reload shutdown). It is the single function the game asks "is control X down" (state 0 held,
1 pressed this frame, 2 released this frame; seen at ~170 call sites, a count from the `--table` output of the pin verifier, which is not in the public repo: see "Pin verifier" below). `ForcedInput` (input_core.h) holds or taps
controls and serves edge events exactly once, because toggles (Crouch, Jump) fire on the edge. No SendInput, no desktop events, works minimized.
Look/aim sets the player angles with `SetAngle`; movement is real held Forward/Run so the avatar plays locomotion animation.
Controls 0..9, 12, 14, 16 are proven from call sites; 10, 11 and 13..27 follow the NVSE table.
| Method | Notes |
|---|---|
| bot.status | {enabled, hooked, frame, held[], shotsLeft, vats{phase,reason}} |
| bot.move {forward,strafe,run?,ms?} / bot.stop | axes in [-1,1], digital with a 0.3 dead zone |
| bot.look {yaw,pitch} or {ref} | degrees, same convention as SetAngle |
| bot.fire {shots,gapMs} | queued Attack pulses; Reload is a tap of ReadyItem (UNVERIFIED which control) |
| bot.reload / bot.sneak / bot.jump / bot.activate / bot.input | taps and generic hold/release/tap by name or id |
| bot.menu | {kind,id,vatsMode,dialogState?,dialogReady?} from the menu stack top (0x714F00) and the VATS camera mode |
| bot.dialog / bot.say {info} | list the DialogMenu ListBox choices; pick one by replicating the plain-topic path of HandleClick (0x7624F0) with gates: state 2, click gate, special INFOs refused |
| bot.menu.press {button} / bot.consent {answer} | answer the message box the UI layer shows (new `ui.answer`); consent: Ready=0, Not yet=1. When the UI layer shows no box and the engine's own MessageMenu (1001) is the top menu, `bot.menu.press` presses that box natively (see "Native message box press" below). The reply has `path` `ui` or `native`. `bot.consent` never uses the native path |
| bot.inventory {ref?,items?} | totals via 0x4C8F30 (base + delta, equipped included); without items only ExtraContainerChanges entries, partial:true |
| bot.vats {ref} / test.vats.hit | tick-driven job: tap Vats, cycle targets with Right until VATSMenu's selected ref matches, tap Activate, wait for playback. Limb choice is refused (unsupported) |
| bot.fasttravel {marker} | GENUINE fast travel, queued the way the map menu does it (see below); gated on busy menus/VATS, a pending travel, and the engine's own may-travel check 0x93D660. Reply `{genuine:true,queued:true}`; the player moves a few frames later |
| test.fasttravel {marker} | fallback only: a `MoveTo` teleport that crosses cells; reply `genuine:false`. `Bot.fast_travel` uses it only when `bot.fasttravel` is missing, never after an engine refusal |
| bot.drops {since?,base?,windowMs?} | what the xNVSE `OnDrop` event reported (native handler, params `[dropper, droppedRef]`): `{seq,drops:[{seq,ref,base,dropper}]}`. Correlated by base form plus a 5 s window. `unsupported` if the handler could not register |
| dev.refsOf {base,cell?} | enabled refs of a base form in the cell; `Bot.drop` uses a before/after difference as the fallback when `bot.drops` is unavailable |
| dev.terminal {ref,entry} / dev.terminalState {ref} | click entry N of the open terminal menu (0x757F70 on the ListBox tile); state = what this bot picked, the engine has no used flag; needs the terminal menu open (Bot.use_terminal activates it first) |
| dev.countRefs {base,cell?} | walks TESObjectCELL::objectList (cell+0xAC), skips disabled/deleted |

Python: `Bot.goto` walks with the driver (turn-rate limited steering, sidestep then jump when stuck, `walk_path`), falls back to stepped SetPos; `look, stop, jump,
sneak_toggle, activate_crosshair, menu_info, dialog_choices, inventory, press_box, dismiss_messages, consent, fast_travel, count_refs, vats_target(expect_hit)`.
In-game proofs (lead runs, one game at a time): `tools/bots/proof_input.py` (movement, strafe axes, turn, jump edge, sneak toggle, fire, activate) and
`tools/bots/proof_glue.py` (menu, dialogue pick, inventory vs console, VATS, consent, fast travel, countRefs). Both also self-test over the fake bridge.
Both exit 0 when every check passed, 1 when a check failed, and 2 when the proof could not run. "Could not run" is one of these: a bad command line (refused before the bridge is opened), no game on the instance, the game lost during the run (a crash or a pipe loss: `ConnectionLost` or `GameCrashed`), or a `proof_glue.py` form that the load order cannot resolve.
`main()` returns the exit code and never raises `SystemExit` (`--help` returns 0). Any other Python exception is a bug in the proof: it ends in a traceback and exit 1.
`proof_glue.py` needs `--npc` and `--info` without `--fake`. Every form argument is `Plugin.esm:HEX` or a bare hex runtime id, with an optional `0x` prefix and leading zeros. An empty form value is a bad command line.
`--other-info` names an INFO that the NPC does not offer now: the dialog check then also fails if `bot.dialog` lists it.
**How `--info` and `--other-info` are compared** (`proof_args.form_key`): as numbers, as (plugin, local id), never as text. So `FalloutNV.esm:801`, `FalloutNV.esm:0x801`, `FalloutNV.esm:0801`, `0x801` and `00000801` name the same INFO, and `FalloutNV.esm:1800` is not `FalloutNV.esm:800`.
A bare id is a runtime id: its top byte is the load index, and the game's plugin list (bridge method `plugins`) gives its plugin. Its low 24 bits are the local id.
`Plugin.esm:HEX` with more than 6 significant digits is a runtime id with its plugin given: it is accepted only when its top byte is the load index of that plugin.
So the low 24 bits are compared only when the plugin is known to match. A top byte outside the load order, a plugin that is not loaded, or a top byte that is not the load index of the given plugin gives exit 2 before the proof drives anything.
`--other-info` and `--info` that name the same INFO also give exit 2: before the bridge is opened when the spellings show it (the same plugin, or both bare), else after the load order is read and before the first check.
**Pin verifier: not in the public repo.** The pin verifier `tools/bots/re/verify_pins.py` is not in this repository, so its commands cannot run from a clone.
From this doc, it checked the pinned prologue bytes of every engine function called (the pins in `plugin/bots/bots_addrs.h`, the fast-travel pins included) against the exe.
Its `--table` output is the source of the ~170 call sites of `GetControlState` given above. The C++ test with `BOTS_EXE` also checks the pins (C++ tests: `plugin\bots\run_tests.cmd`).

### Round 3 additions (static RE, nothing run in game)
- **Inventory totals** (confirmed): `bot.inventory` answers with 0x4C8F30 (base container + delta, equipped included) for explicit `items`, and for every ExtraContainerChanges entry otherwise.
- **Speech checks:** `bot.say` allows INFOs for which 0x7627E0/0x762820 is true (reply `speechCheck:true`; `Bot.choose_topic` returns `data.speech_check`). HandleClick only plays a UI sound for them (0x4D5C60 with 0x1F or 0xC), then runs the same select/click/after/speak tail we replicate, so the engine rolls the check in its own speak path 0x762FF0 and the host result syncs through the normal dialogue layer. Flag-0x80 INFOs (0x7627C0) stay refused: they take another branch (item select 0x83E850, speak flag 1).
- **Drop ref:** the module registers a native `OnDrop` handler through `NVSEEventManagerInterface::SetNativeEventHandler` (interface taken from the NVSE interface at load, handler set at DeferredInit, removed on shutdown and hot reload). Params are `void*[2] = {dropper, droppedRef}`; the base form is read at ref+0x20. If registration fails `bot.drops` is "unsupported" and `Bot.drop` falls back to the cell scan.
- **Genuine fast travel** (found statically in the map menu): clicking a marker stores the ref at MapMenu(*0x11DA368)+0x118; the Yes callback queues deferred worker 0x798A00, which does `obj = heap(0x34) [0x401000]; ctor 0x456540(obj); obj+0x30 = marker; player(*0x11DEA3C)->0x93BE30(obj)`. The main loop (0x86F94F calling 0x93BEA0) consumes player+0x1EC and calls 0x93CDF0 to move. The may-travel check is 0x93D660(player) -> bool and shows the engine's own refusal message (combat, hostiles, cell, script block, overencumbered, health damage, undiscovered, alarm, in air). `fastTravel()` in bot_engine.h replicates the worker with those gates. Pins: PlayerCanTravel, PendingTravelSet, TravelObjCtor, HeapAlloc in bots_addrs.h (checked against the exe by the pin verifier `verify_pins.py`, which is not in the public repo: see "Pin verifier" above). UNVERIFIED in game: that the marker handed over (any TESObjectREFR, e.g. the map marker ref) is what +0x118 holds, and that the worker has no hidden dependency on the MapMenu being open (we skip the menu). If the first run shows no movement, read player+0x1EC after the call.
- **VATS limb choice: unsupported, documented.** Limb-select is mode 2, but the RE has no field for the currently highlighted limb and no mapping from limb to Left/Right presses, so a bot cannot know when it landed on the wanted limb. `bot.vats {limb}` keeps refusing with `unsupported`.

### Native message box press (static RE on the 1.4.0.525 exe, nothing run in game)
Problem seen in game: with all DLC installed, the engine shows its own message boxes (MessageMenu, menu id 1001) about 5 s after a fresh save loads.
`bot.menu` reports `{"kind":"message","id":1001}`. The old `bot.menu.press` only forwarded to `ui.answer`, which knows only the boxes the UI layer showed, so it answered "no message box is being shown".
A bot Activate tap does not close the box either, because menus do not read input through GetControlState. A minimized test copy stays in menu mode and every movement proof fails.

How the native press works (`pressNativeMessageButton` in `plugin/bots/bot_engine.h`, live call in `bots_module.cpp`):
1. The UI layer answers first (`ui.answer`). The native path runs only when that fails.
2. Gates, in this order. The call must run on the game main thread. The interface manager must be ready and the top menu (0x714F00) must be 1001. If the top menu is not 1001, the reply is the UI layer's error, unchanged.
3. The menu object comes from xNVSE `GetMenuByType(1001)`: `g_TileMenuArray` data pointer at 0x11F350C, entry `[1001 - 1001]`, then `TileMenu::menu` at +0x3C. The engine also keeps this object in its MessageMenu singleton 0x11DA4F0.
4. The object's vtable must be 0x107566C (MessageMenu, from RTTI). Any other vtable is refused.
5. A current message must exist: `*0x11DA4F4 < count` of the queue array at 0x11DA53C (data +4, count +8). This is what 0x7A92A0 reads. No current message means the box is closing, so the press is refused.
6. The buttons are a `ListBox<int>` at menu+0x40 (vtable 0x107286C). Its list head node is at menu+0x44: `{item*, next*}`, item = `{Tile* @0, int value @4}`. Button N is the N-th item in list order (0-based, the order the box shows). A button outside the list or an item with a null tile is refused.
7. The press calls vtable slot 3 (+0x0C, HandleClick 0x7AA070, thiscall, `ret 8`) with `(7, tile)`. The engine does the same for a mouse click and for its own Enter and hotkey path (0x7AA110: list index, then tile, then `HandleClick(7, tile)`).
8. HandleClick with id 7 reads the item value of the tile (0x7E1110 on the ListBox), writes it to InterfaceManager+0xE4 (`msgBoxButton`), runs the message callback, and calls 0x7AA530. 0x7AA530 closes the box or shows the next queued message.

Reply: `{menu:1001, button, value, buttons, path:"native"}`. `value` is what the engine records (what `GetButtonPressed` gets). The builder 0x7A92E0 sets value = `(signed char)entry[0x1C]` + the index of the button text. ShowMessageBox passes 0 for entry[0x1C], so value equals the button index unless a button text is empty (an empty text keeps its index but gets no list item).

Pins (`plugin/bots/bots_addrs.h`, checked at init and by the C++ test with `BOTS_EXE`):
- `MsgMenuHandleClick` 0x7AA070, 16 prologue bytes `55 8b ec 51 53 89 4d fc 83 7d 0c 00 75 19 83 7d`, `ret 8`.
- `MessageMenuVtbl` 0x107566C, the vtable gate.
- Data pin `MessageMenu.vtbl[3]`: the dword at 0x1075678 must be 0x7AA070. A mismatch turns off the engine glue, as the call pins do.
- The live call reads the vtable again and calls slot 3 only when the slot holds the pinned 0x7AA070.

Differences from the first brief (verified in the decompile and with dumpbin):
- The six tiles at menu+0x28..0x3C are not the buttons. MessageMenu::SetField (0x7A8AB0) stores the XML `<id>` 0..5 parts there. HandleClick reads that array only for ids 0..5, and then it does nothing, because it acts only on id 7. A press with one of those tiles would record button value 0 (0x7E1110 returns 0 for a tile that is not in the list). So the press takes the tile from the button ListBox.
- The button count is not limited to 6. The list holds one item per non-empty button text. `bot.menu.press` accepts 0..9, and the native path refuses a button the list does not have.

Limits: only MessageMenu 1001, and only when it is the top menu. Button order is the box's list order. The UI layer's box always wins. Nothing here has run in the game yet.
Python: `Bot.dismiss_messages(quiet_s=20, max_s=120, max_n=8, button=0, settle_s=0.3, poll_s=0.5, max_closing=10)` presses `button` while `bot.menu` reports kind `message`, and polls every `poll_s` otherwise. It is done when no box has been shown for `quiet_s` seconds. The boxes come in waves: in the first in-game run (all DLC, a fresh save) 6 boxes came at once, then 2, then 2 more about 16 s later. A press-only-now loop returned after the first wave and the later waves stopped movement in the middle of the proof. It fails with `timeout` when `max_s` passes first and with `postcondition` when a box is still shown after `max_n` presses in a row (a box that does not close). A closing box (menu 1001 is still the top menu, but its current message is gone) refuses the native press with "the message box has no current message (it is closing)". dismiss_messages matches only that text (`MSGBOX_CLOSING` in bot.py), waits `settle_s` and tries again. A retry is not a press. It fails with `postcondition` after more than `max_closing` retries in a row, and the count starts again after each press. Every other refusal still ends it (`rpc_error:...`). data: `{pressed, paths, waited_s, quiet_s, closing}`. `quiet_s=0` gives the old press-only-now behaviour. No proof calls it on its own. Pass `--dismiss-messages` to `proof_input.py` or `proof_glue.py` to clear the boxes before the checks (`--dismiss-quiet-s`, `--dismiss-max-s`). It adds a `dismiss_messages` row with `pressed` and `waited_s`.

Known limits: terminal state only knows the bot's own picks; VATS limb choice; Reload control id; full inventory enumeration of base-container-only items.

## Tests
`tools/bots/tests/test_bots.py`: 24 tests over the fake bridge (virtual clock, milliseconds). Mutation twins in fake.py (`setpos_ignored, angle_ignored, activate_noop, transfer_noop, transfer_dup, equip_noop, drop_noop, fire_miss, health_stuck, dialog_never, ref_info_error, crash`): each must make the matching action fail. The fake uses the same grammar as primitives.py, so it proves the Bot logic, not the real console.
`tools/bots/tests/test_proof.py` and `test_proof_cli.py`: the two proof scripts over the fake, and their command lines. A bad command line (an empty form value included) must make `main()` return 2 and name the argument before the bridge is opened, without `SystemExit`.
A missing game, and a game lost in the middle of the run (`ConnectionLost` and `GameCrashed` from the fake), must give 2 without a traceback. `--other-info` in every accepted spelling must make the dialog check catch the fake mutant `dialog_lists_all`.
`--info FalloutNV.esm:1800 --other-info FalloutNV.esm:800` must pass on a fake whose NPC offers 1800. A `form_key` table and the load-order refusals (exit 2 before the first check) check the runtime id rule.
Twins: in-process copies of the scripts with each check removed or with the old code put back (text and suffix matching, the number-only same-INFO check, empty values let through, `ap.error` exits, an unguarded run, low 24 bits compared without the plugin).

`tools/bots/tests/test_bots.py`: 24 tests over the fake bridge (virtual clock, milliseconds). Mutation twins in fake.py (`setpos_ignored, angle_ignored, activate_noop, transfer_noop, transfer_dup, equip_noop, drop_noop, fire_miss, health_stuck, dialog_never, ref_info_error, crash, msgbox_stuck, msgbox_ui_only`): each must make the matching action fail. `msgbox_stuck` (the box never closes) and `msgbox_ui_only` (the plugin before the native path) must make `dismiss_messages` fail. The fake's `box_waves` adds engine boxes later in virtual time, and while an engine box is shown the fake player does not move (menu mode). The waves tests press all 10 boxes of the in-game pattern. Their twin (`_press_only_now`, the old loop) leaves 4 boxes, and in proof_input it makes `forward` fail. The fake's `box_closing_s` keeps a pressed box in the closing state for that many seconds. Its twin (`_any_refusal_fatal`, the old loop) fails on the first closing refusal.
C++ twins for the native press (`plugin/bots/tests/test_bots.cpp`, NVBOTS_MUTANT): 33 native path never used, 34 vtable check skipped, 35 button index off by one, 36 native path tried before the UI layer, 37 null tile not refused, 38 top-menu gate skipped, 39 closing-box gate skipped, 40 native reply says path "ui", 41 main-thread gate skipped. The fake uses the same grammar as primitives.py, so it proves the Bot logic, not the real console.

## For T6b
FakeBridge.pair() gives two bridges on one World (host and client views are not synchronised by the fake; a playbook that needs sync behaviour must simulate it on top). Agreed API was sent by message; any requested change goes to T6a-actions.
