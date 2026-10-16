# L1 game-in-loop smoke test (manual install - NOT done by the agent)

Goal: prove boot -> nvbridge ping -> exec -> autoload save -> state round trip against the real game.
The agent has NOT touched the game directory. Everything below is for the owner to approve and run.

Game dir (GAME): `C:\Program Files (x86)\Steam\steamapps\common\Fallout New Vegas` (FalloutNV.exe 1.4.0.525)
Artifact: `<repo>\out\L1\nvcoop.dll` (x86, static CRT, imports only KERNEL32). Rebuild: `tools\runner\build_l1.cmd`.

## 1. Install xNVSE 6.3.4 (once)
1. Download `nvse_6_3_4.7z` from https://github.com/xNVSE/NVSE/releases/tag/6.3.4 (small, < 5 MB).
2. Extract into GAME so these sit next to FalloutNV.exe: `nvse_loader.exe`, `nvse_1_4.dll`,
   `nvse_steam_loader.dll`, and `Data\NVSE\` (if present in the archive).
   (xNVSE 6.3.4 is the version the plugin was compiled against; any newer 6.x is fine.)
3. Create `GAME\Data\NVSE\Plugins\` if it does not exist.
4. Recommended: the 4GB Patcher / NVTF are NOT required for this test.

## 2. Install the plugin
```
copy <repo>\out\L1\nvcoop.dll "GAME\Data\NVSE\Plugins\nvcoop.dll"
copy <repo>\plugin\bridge\nvcoop.ini.sample "GAME\Data\NVSE\Plugins\nvcoop.ini"   (optional; edit autoload=)
```
Uninstall = delete those two files. Logs: `GAME\Data\NVSE\Plugins\nvcoop.log` and `GAME\nvse.log`
(look for `nvcoop 1 loading` and `nvbridge: listening on \.\pipe\nvcoop-0`).

## 3. Run
Start the game with `nvse_loader.exe` (NOT the launcher/Steam button), windowed is easiest.
Optional autoload: set `autoload=<save name without .fos>` in nvcoop.ini, or start with env
`NVCOOP_AUTOLOAD=<save>`; instance id via `NVCOOP_INSTANCE=<n>` (default 0).

## 4. Checks (from a second terminal, repo root)
```
python tools\runner\nvbridge.py --instance 0 --wait 120 ping
   -> {"pong": true, "protocol": 1, ..., "mainLoopAgeMs": small number, "menuReady": true}
python tools\runner\nvbridge.py --instance 0 events --since 0         (autoload.start / autoload.done / game.loaded)
python tools\runner\nvbridge.py --instance 0 load "<a save name>"     (if not autoloaded) -> {"loaded": true,...}
python tools\runner\nvbridge.py --instance 0 exec "player.getpos x"   -> {"ok": true, "output": "..."}
python tools\runner\nvbridge.py --instance 0 waitfor "GetStage VMS01" --op ">=" --value 0 --timeout-ms 3000
python tools\runner\nvbridge.py --instance 0 state                    -> {"todo": "L7"} until L7 registers a provider
python tools\runner\nvbridge.py --instance 0 quit                     (game exits via qqq)
```

## 5. Things this smoke test must settle (all UNVERIFIED in-game; each has a documented fallback)
| # | Assumption | If it fails |
|---|---|---|
| 1 | `exec` output capture works by pointing `ConsoleManager::COFileName` (offset 0x810, from NVSE GameAPI.h) at a temp file; the engine mirrors console prints into it. | `output` is "" but `ok` still reflects the command. waitFor{expr} then cannot read values -> switch capture to a Console_Print (0x71D0A0) hook (L5 territory) - small change confined to `GameHost::Exec` in `plugin/main.cpp`. |
| 2 | `waitFor{expr}` evaluates `printc "NVCOOP_EVAL=%g" (<expr>)`; NVSE's `(expr)` argument evaluation works for printc from the console. | Use `waitFor{cmd, contains}` (plain console line + substring) which does not depend on expression syntax. |
| 3 | `LoadGame "<name>"` console command works from the main menu. | `loadSave`/autoload return a host error / time out; alternative: invoke the engine load routine (BGSSaveLoadGame, see docs/02-engine-re.md) from `GameHost::LoadSave`. |
| 4 | `kMessage_PostLoadGame` fires with non-null `data` on success (that is how `game.loaded` is emitted). | `loadSave` times out although the game loaded; check nvse.log, flip the data test in `OnMessage`. |
| 5 | `kMessage_MainGameLoop` runs while at the main menu (so pipe requests are serviced before a save is loaded). | `ping` works anyway (pipe thread) but `exec` returns -32001 until in-game. |
| 6 | `qqq` exits cleanly; fallback is ExitProcess(0). | none needed |
| 7 | `screenshot` is a stub (returns -32000 "not implemented"); real implementation deferred. | n/a |

Report the outcome of each row in internal lane notes.
