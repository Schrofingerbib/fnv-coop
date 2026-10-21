# Split-screen mode (Track B)

Two to four players on ONE PC. Each player is a full game copy in a borderless window that fills part of the screen, answering to its
own controller, joined to the others through the normal co-op session on 127.0.0.1 (plan section 6a). Player one is the host and keeps
the keyboard and mouse. Nothing here changes the co-op protocol: the copies are ordinary co-op peers.

Status: all code and game-free tests are done and pass (`tools/splitscreen/run_tests.py`, ALL OK). **Nothing has run in a game yet.**
Every claim marked UNVERIFIED below is settled by the in-game steps in section 2.

## 1. Quick start (one click)

Prerequisites: the game with xNVSE installed, a build of the plugin (`python -I tools/build/build.py`, which writes `out/L1/nvcoop.dll`),
N-1 controllers plugged in for N players, no other copy of the game running, and about 2.5 GB of free memory per copy.

| Click | Does |
|---|---|
| `tools/splitscreen/splitscreen.cmd` | menu: 2 players left/right, top/bottom, 3, 4, layout check, close all |
| `play-2-leftright.cmd`, `play-2-topbottom.cmd`, `play-3.cmd`, `play-4.cmd` | straight to that layout |
| `stop.cmd` | closes the copies the launcher started (nothing else) |

Command line (all of it is optional; `--help` lists everything):

```
python -I tools/splitscreen/launch.py plan --players 2 --split lr     # print the plan, write nothing
python -I tools/splitscreen/launch.py up   --players 2 --split lr     # start it
python -I tools/splitscreen/launch.py up   --players 4 --autoload MySave --monitor 2
python -I tools/splitscreen/launch.py up   --players 2 --fit 16:9     # 16:9 windows inside each half (see section 5)
python -I tools/splitscreen/launch.py down | status | monitors
```

What `up` does, in order:
1. Checks that no other copy of the game runs and that enough memory is free.
2. Provisions one copy per player with `tools/runner/instances.py` (hardlinked game files, own INIs and saves) in
   `../fnv-instances-split/<id>` (ids 40..43, pipes `nvcoop-40` ...), so it never meets the developer lanes' instances. Documents\My Games is never touched.
3. Sets each copy's `FalloutPrefs.ini` to its cell (`bFull Screen=0`, `iSize W/H` = cell size, `iLocation X/Y`, vsync off) and `Fallout.ini`
   (`bAlwaysActive=1`, `iFPSClamp` 60). Writes each copy's `nvcoop.ini`: `[coop]` host plus clients on 127.0.0.1,
   `[bridge] minimized=0 focusGuard=1`, and `[splitscreen]` (seat, pad, audio).
4. Starts the host, waits for its bridge, then starts the clients one at a time.
5. Makes every window borderless and puts it on its cell with SetWindowPos (never activating a window, never touching the z-order) and keeps it
   there while the launcher window stays open (the game re-creates its window after the splash and the engine moves it at start-up).

Layouts: `2lr` left/right, `2tb` top/bottom, `3` and `4` the quad (top-left, top-right, bottom-left, bottom-right; 3 players use the first three).
Seat 1 is the host. `--monitor N` picks the monitor (`launch.py monitors` lists them). `--workarea` avoids the taskbar.

Controllers: player one uses keyboard and mouse, the other players take the connected controllers in order (`--pads none,0,1` to choose,
`--p1-pad` puts player one on a controller too, `--allow-missing-pads` leaves seats without a controller idle).

## 2. Exact in-game steps for a tester

1. Build: `python -I tools/build/build.py` (shared tree, takes the build lock). Close the game if it runs.
2. Dry run: `python -I tools/splitscreen/launch.py plan --players 2 --split lr`. Check the sizes and which controller each seat gets.
3. Layout check for your screen: `python -I tools/splitscreen/check_layout.py --screen 1920x1080 --players 2`. Read the WARN lines (section 5).
4. Start with the synthetic-controller hook for the smoke playbook: `python -I tools/splitscreen/launch.py up --players 2 --dev-inject --autoload <a save name>`.
   Wait for `READY. Every window is on its cell.` Both windows must sit side by side without a title bar. No window should be minimized.
5. If you gave no `--autoload`: in the left window (the host) load a save with the keyboard and mouse. The right window should join the host by itself (127.0.0.1); if it does not, see the joining note in section 8.
6. Smoke playbook (no controller needed): `python -I tools/splitscreen/smoke_playbook.py --players 2 --movement`. Expect PASS for windows, hooks,
   pads, unfocused, movement, then `ALL OK`. Run it once with your own click on the left window and once after clicking the right window.
7. By hand, with a real controller: click the left window and move with W/A/S/D: only player one moves. Push the stick of the controller
   assigned to player two (the launcher printed its number): only player two moves, although the right window is NOT focused. Press a button
   on the controller of player two while the left window is focused: nothing happens in the left window.
8. By hand, sound: only the left window plays sound. Open the Windows volume mixer: the right copy is muted.
9. By hand, rumble: take damage in the right window: only the controller of player two vibrates.
10. Live layout check: `python -I tools/splitscreen/check_layout.py --players 2 --live --instances-root ..\fnv-instances-split`.
11. Quad on a big screen: repeat with `--players 4` (3 controllers) and the 4-player smoke playbook.
12. Close: `python -I tools/splitscreen/launch.py down` (or Ctrl+C in the launcher window, then `stop.cmd`).

Report: the PASS/FAIL lines of step 6 and 10, and from each copy `Data\NVSE\Plugins\nvcoop.log` lines starting with `splitscreen:`.

## 3. Input facts (read from the 1.4.0.525 exe; `tools/splitscreen/re_pins.py` and the C++ tests check the bytes)

* The per-frame input update `0xA23010` calls `XInputGetState(0, &state)` through the import thunk `0x9F996E` (`jmp [0xFDF394]`, XINPUT1_3.dll
  ordinal 2). Only user index 0 is ever polled. Rumble uses `0xEE820A` (`jmp [0xFDF398]`, ordinal 3). The exe does not import
  `XInputGetCapabilities` (the plugin still answers it if some other module calls it through an import slot).
* The call result 0 sets the gamepad-mode byte `0x11F35C8` to 1, any other result sets it to 0.
* `OSInputGlobals::GetControlState` (`0xA24660`, the function the bot driver hooks) reads ONLY the pad bindings while that byte is 1, and only the
  keyboard and mouse bindings while it is 0.
* Consequence: a copy that must ignore the keyboard and mouse keeps a (neutral) pad "plugged in" (`ghostPad`), and player one on keyboard and mouse
  must see "not connected" from XInput. Both are done by the pad router. No engine code is patched.
* Every DirectInput cooperative level the engine sets is FOREGROUND (`SetCooperativeLevel` flags 0x16, 0x15, 6, 5 at `0xA22784`, `0xA227A3`,
  `0xA229AB`, `0xA229CD`, `0xA23C7E`, `0xA23CAF`, `0xA248C8`), always with the window from `GetActiveWindow()`. So the system gives the keyboard and
  mouse only to the window in front: keyboard and mouse meant for another window never arrive. `bBackground Keyboard/Mouse` in the INI does not
  change that (the flags are constants). UNVERIFIED in game: that the INI keys really have no effect.
* `GetActiveWindow` is NULL on a thread whose window is not active, which would make a copy started in the background call
  `SetCooperativeLevel(NULL, ...)`. The plugin therefore answers the game window instead of NULL on the game thread (`focusFake`).

## 4. Pieces

| Where | What |
|---|---|
| `plugin/splitscreen/focus_guard.*` | (existing) WM_ACTIVATE(WA_INACTIVE) never reaches the engine, so an unfocused copy does not open the pause menu and keeps simulating. Installed by `plugin/main.cpp` when `[bridge] focusGuard=1` (the launcher sets it). Twins 1..7. |
| `plugin/splitscreen/focus_fake.*` | `GetActiveWindow` answer for the game thread (see section 3). Twins 27..29. |
| `plugin/splitscreen/pad_router.*` | virtual pad 0 -> physical pad N; pads 1..3 hidden; rumble and capabilities follow; ghost pad. Twins 12..20. |
| `plugin/splitscreen/iat_patch.*` | finds the XInput and GetActiveWindow slots by walking the exe's import table (by name, or by value for the ordinal imports) and patches them with one aligned store; refuses unbound slots; restores only slots still ours. Twins 21..26. |
| `plugin/splitscreen/split_config.*` | the `[splitscreen]` section of nvcoop.ini. Twins 8..11. |
| `plugin/splitscreen/audio_policy.*` | mutes only this process's Windows audio sessions, un-mutes only what it muted. Twins 30..34. |
| `plugin/splitscreen/split_addrs.h` | the pinned engine addresses (read only; nothing is patched at an engine address). |
| `plugin/splitscreen/splitscreen_module.cpp` | the nvcoop module (game glue, x86): IAT patches, Core Audio mute thread, `split.status`, `split.inject`. Inert unless `[splitscreen] enable=1`. Registered through `NVCOOP_MODULE`; no edit to main.cpp or coop_module.cpp. Hot-reload safe (slots restored on shutdown, `canUnload` refuses when a slot was chained over). |
| `tools/splitscreen/launch.py`, `plan.py`, `layout.py`, `monitors.py`, `winplace.py` | the launcher |
| `tools/splitscreen/check_layout.py` | HUD and menu layout check (section 5) |
| `tools/splitscreen/smoke_playbook.py` | the smoke playbook |
| `tools/splitscreen/run_tests.py` | every test; prints ALL OK |

Bridge methods: `split.status` (always registered) returns the config, hook counts, pin status, pad statistics (`statePolls`, `physicalPolls[4]`,
`lastButtons`, `lastLX`, `gamepadMode`), audio session counts, focus counters and the window (rectangle, client size, borderless, foreground,
minimized). `split.inject {pad, buttons, lx, ly, rx, ry, lt, rt}` or `{pad, clear:true}` (only with `devInject=1`) replaces the hardware of a
physical pad inside that copy.

`[splitscreen]` keys: see `plugin/bridge/nvcoop.ini.sample`. The launcher writes them; hand edits are lost on the next `up`.

## 5. HUD and menus at half and quarter size

The game has no HUD scale setting; the HUD is laid out against the window size. `check_layout.py` reports, per player, with named thresholds in
the file (these are heuristics, the game cannot be asked whether a menu fits):

| Check | Level |
|---|---|
| cell below 640x360 | ERROR |
| cell below 960x540 | WARN (small text) |
| UI height factor (cell height / 720) below 0.6 | WARN |
| aspect outside 1.2 to 2.4 | WARN: the HUD is designed for 4:3 to 16:9 |
| cells overlapping or leaving the monitor | ERROR |
| INI: full screen on, `iSize` or `iLocation` not equal to the cell, `bAlwaysActive` not 1, vsync with several copies, large `[Interface] fSafeZoneX/Y` (believed keys) | ERROR / WARN |
| `--live`: client area differs from the cell, window not borderless or minimized, plugin off | ERROR / WARN |

Worth knowing: on a 16:9 monitor the quad cells are 16:9 and fine, but 2 players left/right gives 0.89:1 cells and top/bottom 3.56:1 cells,
which the aspect check flags. `--fit 16:9` makes each window 16:9 inside its half (960x540 at the vertical centre of each half on 1080p); the rest of
the half shows the desktop (a black backdrop window is not built yet).

## 6. Audio, performance, minimum specs

* Default: only player one has sound. The other copies are muted at their Windows audio session (the mixer's per-app mute), so their sound
  system keeps running and voice-line timers still fire. `--audio off` also writes `bEnableAudio=0` to their `Fallout.ini` (no sound device at all; dialogue
  waits that need a voice-line end may then never finish, see `tools/pool/lowcost.py`). `--audio play` leaves all copies audible.
  An engine master-volume key was NOT used: no INI key for it is confirmed in this repo (TODO-RE). Per-player audio devices are later work.
* Each copy is a full game: plan for about 2.5 GB of RAM each (the launcher refuses below that unless `--force`), one GPU context each, and
  `--fps-cap` (default 60) per copy. Four copies at 960x540 need a machine that runs one copy at 1080p comfortably plus about 8 GB more RAM.
  Use `--vsync` only with one copy.

## 7. Tests

`python -I tools/splitscreen/run_tests.py` (or `plugin\splitscreen\run_tests.cmd`, same thing) builds through `tools/build/build.py` and runs:
focus guard real + twins 1..7; library tests (110 checks, with a reference exe when present) + twins 8..34; the glue compile check; Python tests
(18) + 16 twins (`split_mut`); engine pins. Registered in `tools/test_all.py` twice: the C++ half under the existing "Track B splitscreen focus
guard" row, the Python half as "Track B splitscreen tools". The proof scripts (`launch.py up`, `smoke_playbook.py`, `check_layout.py --live`) were written
but NOT run: they start the game.

## 8. Open issues

* Nothing ran in a game. Section 2 settles: pad isolation, the `GetActiveWindow` fake, that unfocused copies keep simulating and polling, borderless placement, the audio mute.
* DPI scaling: if Windows scaling is above 100 percent and the game is DPI-unaware, the client size can differ from the cell. `check_layout.py --live`
  reports it (`live-client`); the fix is the game's compatibility setting "Override high DPI scaling" or a plugin-side DPI declaration (not done).
* `iLocation X/Y` and the `[Interface]` safe-zone keys are believed names, not confirmed; the launcher's SetWindowPos placement does not depend on them.
* Several keyboard and mouse sets (raw-input filtering per device) are not built (optional, plan 6a).
* A muted audio session could stay muted in the Windows mixer if a copy is killed before it un-mutes (the mute is per session, so it normally ends with the process).
* Joining: the launcher starts a plain co-op session (`[coop]` as `plugin/coop/tools/two_game_test.py` writes it). Whether a client with no save loaded joins by itself, or needs `--autoload`, is
  the co-op layer's behaviour and is not changed here.
* `--fit` leaves the rest of each half as desktop; no backdrop window yet.
* The plugin core DLL in split-loader mode: the focus guard lives in the loader and is installed by `main.cpp`; the import patches live in the reloadable core and are restored on reload (a few frames of pad 0 pass-through during a reload).
