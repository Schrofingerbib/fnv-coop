# fnv-coop - Lane ownership & interface contracts (v0)

Read `docs/MASTER-PLAN.md` first. Owner decisions there are final.

## Environment facts
- Game: Steam, `C:\Program Files (x86)\Steam\steamapps\common\Fallout New Vegas`, FalloutNV.exe **1.4.0.525**, all DLCs.
- Toolchain: VS 2022 Community 17.10 (MSVC, build **Win32/x86** for anything loaded in the game), CMake 4.1, Python 3.11.
- RE: Ghidra 12.1.4 (`~/scoop/apps/ghidra/current`), REA CLI `~/scoop/persist/nodejs-lts/bin/rea.cmd` (needs `~/scoop/apps/nodejs-lts/current` first on PATH; each CLI call re-analyses, ~4+ min).
- NV:MP 9.4 binaries + analysis: `third_party/nvmp/` (bin/, analysis/, src/ incl. NVMP-Companion, x-native, NVMP-SDK).
- Disk: ~27 GB free on C: (as of 22:10). Still keep build dirs tidy; no downloads > 500 MB.
- Owner goal: the WHOLE game co-op, as fast as possible. Optimise for an end-to-end vertical slice first (one stage synced between two real game instances), then breadth via the generated per-quest scenarios.

## OWNER IS USING THIS PC - game launches must be invisible
- NEVER launch the game fullscreen. User INIs (Documents\My Games\FalloutNV) are now windowed 800x600 + bAlwaysActive=1 (backups: *.pre-fnvcoop.bak). Instances must use their own INIs with bFull Screen=0, iSize W=640/H=480, bAlwaysActive=1.
- Immediately after launch, minimize the game window WITHOUT activating it (ShowWindowAsync(hwnd, 7 = SW_SHOWMINNOACTIVE)) and never bring it to the foreground. Prefer doing this from nvcoop.dll itself at plugin load / first frame when nvcoop.ini has `minimized=1` (default on for tests).
- Never send simulated keyboard/mouse input to the desktop; drive the game only via nvbridge.
- Never modify the owner's real config (Documents\My Games\FalloutNV\*.ini, %LOCALAPPDATA%\FalloutNV\plugins.txt) for experiments. Use an L2 instance with instance-local INIs. Never change file attributes (read-only) on owner files.
- Never write, rename or delete saves in the owner's Saves dir (Documents\My Games\FalloutNV\Saves). Save tests run only in L2 instances; never use the names "autosave"/"quicksave".

## Game slot (mandatory)
Never launch FalloutNV.exe without holding the slot: `python -I tools/gameslot.py acquire --owner <lane> --ttl 900 --wait 1800`
(exit 3 = busy). Kill your own instances and `release` when done. If the lease expired, the new holder must kill strays.
Messages asking "is the game free?" are no longer the protocol - the lock is.

## Cross-lane test gate
`python -I tools/test_all.py` runs every lane's suite (`--quick` skips C++ builds). Before reporting "done", run it and paste the summary.
Never break another lane's suite; if your change does, fix it or coordinate with that lane directly. Tests must not depend on files another lane is concurrently rewriting (copy inputs to a temp dir or use pinned fixtures), and must use unique pipe names/ports per test process.

## Hard rules for every lane
1. Only write inside your own directories (table below) + your own `docs/lanes/<lane>.md` status file.
2. **Never modify the game install directory** (no copying DLLs into Data, no INI edits there) in this phase. Stage deployables under `out/<lane>/` and describe the install step in your status file.
3. **Do not launch FalloutNV.exe** unless your lane says so (only L1 may, and only one instance at a time).
4. No git commits - the coordinator commits. No network installs > 50 MB.
5. Every deliverable ships with tests + a **mutation twin** (a deliberately broken variant that the test must catch), and a one-command way to run them.
6. Shared types live in `synccore/include/fnvcoop/` (owned by L4). Other lanes propose changes to it in their status file instead of editing it.
7. Finish with `docs/lanes/<lane>.md`: what's done, how to run tests, results (paste output), open questions, proposed contract changes.

## Lanes
| Lane | Owns | Builds |
|---|---|---|
| L1 bridge | `plugin/bridge/`, `plugin/common/`, `plugin/CMakeLists.txt`, `third_party/` (xNVSE headers) | xNVSE plugin skeleton `nvcoop.dll` + nvbridge JSON-RPC over named pipe `\\.\pipe\nvcoop-<instanceId>`; Python client lib in `tools/runner/nvbridge.py` |
| L3 questdata | `tools/questdata/` | Python ESM parser → `out/questdata/quests.json`; risk classifier; test-scenario generator |
| L4 synccore | `synccore/` | game-free C++17 lib: wire format, effect transactions, story log, merge rules, share policies, echo guard; sim harness (N fake clients, virtual clock, lossy link) |
| L5 hooks | `plugin/hooks/`, `docs/engine-addresses.md` | verify engine addresses in FalloutNV.exe via Ghidra/REA; hook + effect-capture design and code (compiled, not yet loaded in game) |
| L6 nvmp | `plugin/nvmp/` | signature scanner; signatures for 9.4 client.dll patch sites (encounter "is quested" localization, locality byte, Companion patches ported) |
| L10 integration | `plugin/coop/` | co-op runtime inside nvcoop.dll: host/client sessions wiring synccore + hooks + transport, coop.* bridge methods |
| L7 snapshot | `plugin/snapshot/`, `tests/goldens/` | canonical world-state snapshot schema + C++ dumper (compiled against xNVSE headers) + Python diff tool |

## Shared interface: nvbridge JSON-RPC (L1 owns, others consume)
Request `{"id":1,"method":"exec","params":{"cmd":"setstage VMS01 10"}}` → Response `{"id":1,"result":{...}}` / `{"id":1,"error":{"code":..,"message":..}}`.
Methods v0: `ping`, `exec{cmd, ref?}`, `state{sections?}` (delegates to L7 snapshot), `waitFor{expr,timeoutMs}`, `events{since}`, `screenshot`, `loadSave{name}`, `quit`.
All game calls are marshalled to the main thread.

`exec{cmd, ref?}`: with `ref`, the bridge runs `cmd` with that reference as the console's calling reference
(`RunScriptLine2(cmd, ref)`). The game's console does not accept `<hexFormID>.Command`. In game (FalloutNV 1.4.0.525,
2026-10-21) `00103E61.GetOpenState` got `Script command "00103E61.GetOpenState" not found`, and the EditorID of a
non-persistent ref got `Invalid reference ...`. Only `player.` works as a prefix. So a line for any other reference is
`{"cmd":"GetOpenState","ref":"FalloutNV.esm:103E61"}`, never `{"cmd":"00103E61.GetOpenState"}`.
- `ref` is `"Plugin.esm:HEX"` (HEX is the id inside the plugin, at most `FFFFFF`; the bridge adds the plugin's load-order
  index) or a runtime form id `"HEX"` / `"0xHEX"` (1 to 8 hex digits).
- Result: `{ok, output, ref}`. `ref` is the runtime id that ran the line, as 8 hex digits.
- Errors: `-32602` when `ref` is not a string or not one of the forms above. `-32013` when the plugin is not loaded, the
  form is not loaded, or the form is not a reference (only REFR, ACHR and ACRE can be a calling reference). The line
  does not run.
- `exec{cmd}` without `ref` is unchanged. `waitFor` has no `ref`: poll with `exec`/`eval` and `ref` instead.
- Python: `NvBridge.exec(cmd, ref=...)`, `NvBridge.eval(expr, ref=...)`. `nvbridge.split_ref_line(line)` turns an old
  `"<ref>.<Func> args"` line into `("<Func> args", ref)` (in `tools/runner/console_ref.py`).

## Shared types (L4 owns): `synccore/include/fnvcoop/`
- `FormRef { std::string plugin; uint32_t localId; }` - never raw load-order FormIDs on the wire.
- `Effect` variant: SetStage, SetObjective, SetGlobal, EnableRef, DisableRef, AddItem, RemoveItem, ModActorValue, ModReputation, ModKarma, StartQuest, StopQuest, MoveRef, KillActor, ResurrectActor (the first 15; the current list is generated into docs/protocol.md section 6). `RefCommand` (docs/protocol.md section 21, op ids in `synccore/include/fnvcoop/refop.h`) is the generic "script command on a reference" effect; new script commands get an op there instead of a new Effect type.
- `EffectTxn { uint64 seq; PlayerId origin; Cause cause; std::vector<Effect> effects; }` - one result-script run = one txn.
- `Recipient` routing for player-targeted effects per owner policy (rewards → all players; karma/rep → shared).

## Combat rules (combat-core owns `synccore/combat/`)
- `synccore/combat/combat.h`: `HitEvent`, `HitValidator` (run by the VICTIM's owner), `LimbState`, `DeathLedger`/`DeathDecl`, `ProjectileRegistry` (visual-only copies), `ExplosionLedger`/`planExplosionHits`, kill XP, `HostilityBook`. Game-free; engine questions go through `ICombatWorld`. Docs: `docs/tools/combat-core.md`. Engine glue (`plugin/combat/`) uses these types and does not redefine the rules.

## Area desync rules (area-desync owns `synccore/areadigest/` and `plugin/areadigest/`)
- `synccore/areadigest/areadigest.h`: `Item`, `IAreaSource` (one per category: actors, containers, objects), `IStorySource`, `AreaSync` (digest, targeted diff, heal), `RollingRate` / counters. Game-free;
  engine questions go through `IAreaSource` / `IActorFacade`. Docs: `docs/tools/area-desync.md`, wire: `docs/protocol.md` section 17 (first byte 0xDA).
- Authority of an item: the owner of the actor (as the host's ownership view says), the host for everything else. A heal never touches the owner's copy, never kills or revives an
  actor (the actor module and the story layer own those), and carries the target's item version and the owner epoch.
- Heals per category per minute are exported to the flight recorder (`FlightRecorder::healCounters`, marker kind 8) and to `areadigest.status`; tests assert 0 (`tools/areadigest/dashboard.py`).
