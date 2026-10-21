# T3b-state: what survives a hot reload, and how

Status: code and game-free tests done (2026-10-20). Not yet run inside a game: the lead runs `tools/runner/reload_proof.py` (below).

## What this is
When T3a's loader swaps the reloadable core DLL (`nvcoop_mod.dll`) in a running game, three things must be true:
1. State that matters (the co-op session, the hook set, slot bindings) comes back in the new core.
2. Nothing of the old core is left running: every engine hook is removed and its original bytes are back, every thread is joined, sockets are handed over or closed.
3. If that cannot be proven, the reload is refused, and the old core keeps running.

## The contract (code: `plugin/reload/`)
A **participant** is one piece of state. It has a name, a version, an order, and these optional callbacks:

| callback | when | what it does |
|---|---|---|
| `canUnload` | first, before anything is touched | returns "" or a reason to refuse (for example "a hook was chained over by another mod") |
| `save` (onBeforeUnload) | old core still fully alive | writes its state into a byte writer; returning false refuses the reload |
| `shutdown` | after every save worked, in reverse order | stops threads, uninstalls hooks, closes or hands over sockets |
| `restore` (onAfterReload) | new core, after its normal init | reads the saved state; if the saved version differs, `migrate` is called instead |
| `fresh` | no blob, damaged blob, version mismatch, restore rejected | start from scratch |

Order: ascending for save and restore, descending for shutdown. Hooks are 10, UI hooks 20, world hooks 30, co-op session 50, NV:MP patches 70.

Register one with a static registrar, same pattern as `NVCOOP_MODULE`:

    NVCOOP_RELOAD_STATE(my_module, "my_module", /*version*/ 1, /*order*/ 50,
        /*canUnload*/ nullptr, /*save*/ [](nvreload::Writer& w) { w.u64(counter); return true; },
        /*restore*/ [](uint32_t savedVersion, nvreload::Reader& r) { counter = r.u64(); return true; },
        /*migrate*/ nullptr, /*shutdown*/ [] { thread.stopAndJoin(); }, /*fresh*/ nullptr);

Rules for a module author:
- Never start a raw `std::thread` or `CreateThread`. Use `nvreload::ManagedThread` (stops and joins in its destructor, counted by the leak probe). A thread that outlives its DLL crashes the game.
- Install engine hooks only through `nvhooks::HookRegistry`. Its destructor and `restoreAllAndVerify()` restore the bytes and read them back.
- The blob is plain bytes. No pointers, no vtables. A SOCKET or HANDLE may be stored as an integer if `shutdown` leaves it open.
- A saved layout change means a version bump. Without `migrate`, an old blob is ignored (fresh start), never misread.
- Anything the loader registered for you (bridge methods, state provider) is removed by the loader; do not unregister it.

Blob format: magic `NVRL`, format 1, then per participant name, version, payload, payload CRC-32, and a CRC-32 over the whole blob. A damaged blob never reaches `restore`; a damaged payload only resets that participant.

## Glue to the loader (T3a's ABI, `plugin/loader/loader_abi.h`)
`plugin/common/reload.h` gives T3a's `core_entry.cpp` two calls:
- `nvcoop::reload::BeforeUnload(api)` = `canUnload` of all, `saveAll` into one blob stored with `api->blob_put("nvreload.state")`, `shutdownAll`, then the leak probes. Returns true only if all of it is clean.
- `nvcoop::reload::AfterLoad(api)` = `restoreAll` from `api->blob_get` when `api->is_reload`, otherwise every participant gets `fresh()`. Always true; it also registers the bridge method `reload.state`.

Two outcomes of `BeforeUnload` returning false:
- A `canUnload` refusal or a failing `save`: nothing was stopped, the old core is fully working (host test checks this).
- A leak found after shutdown (a hook not restored, a thread not joined): hooks and threads are already stopped, so the game is safe, but that core is degraded. The loader must not call FreeLibrary on it (T3a's veto path keeps it mapped).

Bridge method `reload.state` returns participants and versions, `liveHooks`, `liveThreads`, `chainedHooks`, the last blob size, and the counts and messages from the last save/restore.

## What each module does
| participant | saved | on shutdown | on restore |
|---|---|---|---|
| `l5_hooks` (plugin/hooks) | "was initialised", role | `nvhooks::Shutdown()` (restore + read back) | `InitHooks()` again |
| `l11_hooks_ui` | "was initialised" | `nvui::Shutdown()` | `InitUi()` |
| `l12_world` | "was initialised" | `nvworld::Shutdown()` | `TryInit()` |
| `l10_coop` | game-loaded flag, scene-pass flag, client token, session sidecar (host: shared world, epoch, token map) | stops the runtime (transport closed, worker thread joined), controls unlocked | after the new runtime starts, `onGameLoaded(sidecar)` resumes the session; the client keeps its token and so its slot |
| `nvmp_client_patches` | slot to net-id bindings | reverts our edits inside NV:MP's client.dll, unregisters the loader-lock DLL notification | bindings back; patches re-applied by the normal frame logic |

Co-op connection: ENet cannot adopt an existing socket, so the transport is closed and reopened. Peers see a disconnect and rejoin; the session itself (world, epoch, tokens) is kept by the sidecar. How long the rejoin takes in a real game is unknown until measured.

Hook patcher changes (`plugin/hooks/hook_patch.*`, used by L5, L11 and L12): the registry destructor could loop forever if a restore write failed (fixed); a restore is now read back and compared with the saved original bytes; `liveHookCount()` and `tamperedCount()` give process-wide accounting; `auditTampered()` is the read-only preflight; `restoreAllAndVerify()` ignores refcounts and reports what it could not restore. Each hook helper exposes `auditTamperedHooks()` for its `canUnload`.

## Tests (one command each, both print ALL OK)
- `plugin\reload\run_tests.cmd` (x86 build in `build\T3b`, outputs in `out\T3b`):
  - Registry unit tests (359 checks): roundtrip, short reads, a failing save vetoes, preflight veto, corrupt blob, damaged payload under a valid outer checksum, version mismatch with and without `migrate`, rejected or over-reading restore, removed and new participants, reverse shutdown order with a throwing participant, leak probes, managed threads, 100 save/shutdown/restore cycles. Mutation twins 1 to 8 (save failure ignored, no CRC check, version ignored, forward shutdown order, no leak probes, thread detached, throw stops shutdown, preflight ignored) must each fail.
  - **Host-process test** (1514 checks): T3a's real `CoreHost` loads a test core DLL (built from the real glue, the real hook patcher and the real `NvCore_Init` ABI) and reloads it 100 times. Between reloads it checks from outside the core: frame counter, label, background tick count and hook-call count carried over; a UDP socket handed over and adopted (same port); the patched host function has its original bytes when the new core starts and is hooked again after init; process thread count back to baseline; handle count not creeping; exactly one core module mapped and at most one cache file; then a hook "chained over by another mod" must refuse the reload while the old core keeps running, and the reload works again once the chain is gone. Mutation twins 1 to 6 (hooks not uninstalled, untracked thread not stopped, state not saved, socket closed instead of handed over, restore reads fields in the wrong order, hook refcount left at 2) must each fail.
  - Split-mode config (2026-10-20, 1723 host checks now): the test rewrites `nvcoop.ini` beside the core file before the first load and before each of the 100 reloads. The test core reads `[testcore] value=` beside its own module at init, as the real modules do, and each new core must report the value written just before its load. The same host test runs with the T3a loader run-time twins 25 `noconfig` (config not copied into the cache) and 26 `configonce` (copied only at the first load), and each must fail on these checks (FNVCOOP_MUT=25 / 26, see T3a-loader.md).
- `plugin\hooks\run_tests.bat`: five new checks (100 install/restore cycles on a fake image, restore failure never hangs and is reported, a restore that does not stick is detected, chained hook counted, read-only audit); the existing mutant twin catches them.
- `python -I -m unittest discover -s tools/runner/tests -p test_reload_proof.py`: 19 tests for the in-game proof (fake game, 17 mutation twins).
- All three are in `tools/test_all.py` (`T3b reload state`; the python one is part of "L2+L1 runner").

## In-game proof for the lead
Needs the split build (`cmake -DNVCOOP_SPLIT_LOADER=ON`), a game running under the game-slot lease, and coop enabled if the session check should mean something.

    python -I -X utf8 tools\runner\reload_proof.py --instance 0 --cycles 10 --out runs\reload_proof.json

Per cycle it calls `dev.reload` and compares before and after: generation +1 and not rolled back; hook counts unchanged, none failed, critical hooks ok, `liveHooks` unchanged; `liveThreads` and the real process thread count not higher; participants and versions unchanged, nothing failed or leaked; co-op still running with the same role and token; and a real `capture.begin`, `setstage 10A214 10`, `capture.end` shows the new core's hooks capture. At the end: handle count within +8 of the start, working set growth under 150 MB. Exit 0 pass, 1 fail, 2 could not run.

## Edits outside my directory (all minimal)
- `plugin/hooks/hook_patch.{h,cpp}`, `plugin/hooks/tests/test_hooks.cpp`, `engine_hooks.{h,cpp}` (+`auditTamperedHooks`), `plugin/hooks_ui/ui_hooks.{h,cpp}`, `ui_module.cpp`, `plugin/hooks_world/world_hooks.{h,cpp}`, `world_module.cpp`, `plugin/hooks/hooks_module.cpp`: the L5, L11 and L12 pieces above.
- `plugin/coop/coop_module.cpp`: participant plus applying the carried sidecar in `OnFrame`.
- `plugin/nvmp/client_patches/*`: `ClientPatcher::RevertAll`, participant, and `reload_state.cpp` added to its `run_tests.cmd` source list.
- `plugin/CMakeLists.txt`: `reload` added to the subdirectory list. `tools/test_all.py`: one line.
- `plugin/hooks/engine_hooks.cpp`: added a missing `#include "apply_lines.h"` (another change in the working tree used `axisIndexOf` without it, which broke the canonical build).

## Limits
- The proof of "no leak" for threads covers `ManagedThread` and what the host test counts from outside. A raw thread started by other code would show up only in the process thread count of `reload_proof.py` and in the host test; nothing prevents someone from writing one.
- In-flight detours are drained (`plugin/reload/reload_drain.{h,cpp}`, called from `BeforeUnload` in `reload_glue.cpp`). Order: every participant saves, then the hooks are taken off (`suspendAllHooks`: original bytes back, trampolines kept, so nothing can ENTER a detour any more), then `waitDrained` freezes the other threads one attempt at a time and asks of each: is its instruction pointer, or any aligned word of its live stack (a return address), inside the core image or a hook trampoline? A thread blocked inside a detour has its instruction pointer in the kernel, so the stack words are what catches it. If any thread is inside, the threads are resumed, the drain sleeps and tries again until the timeout (default 2000 ms, env `NVRELOAD_DRAIN_MS`; a positive timeout always gets at least two attempts, because one scan can itself take longer than the limit on a loaded machine). Anything that cannot be proven outside counts as inside (thread cannot be opened, suspended or read, more than 4096 threads); the drain can be slow or refuse, never succeed wrongly. `ManagedThread` ids and the calling thread are not counted. On timeout the hooks are put back (`resumeAllHooks`), the reload is refused, the old core keeps running, and the log gets one clear line, for example `reload REFUSED by the drain ...: drain: drain timed out after 412 ms (limit 400 ms, 3 attempt(s), 9 thread(s) inspected): thread 1234 is still inside the code that would be unloaded: return address 0x... on its stack (sp+48) points into the hook trampoline ...`. The same data is in `DrainResult::toJson()`. It needs no change to the detours themselves (this replaces the older per-detour counter idea).
  - Tests (game-free): `tests/test_reload_drain.cpp` calls `waitDrained` directly with a code range that is a PE section of the test exe, helper threads parked inside it (in a kernel Sleep loop, and spinning), idle threads outside it, and checks: nobody inside drains at once, a thread whose frame is inside times out with the full diagnosis, instruction-pointer detection, timeout 0 is one attempt, the ignore list, a thread that leaves a moment later is waited for, a different range does not match, `moduleRangeOf`, JSON escaping (46 checks). `tests/test_reload_host.cpp` (1626 checks) does 100 real unload/reload cycles of `testcore` with 4 worker threads hammering a hooked function whose detour sleeps (so some worker is nearly always blocked inside it), then holds a worker inside the detour, checks that the reload is refused, that the log explains why (timeout, limit, thread, where), that the hooks are back and the core still runs, and that a reload works again once the thread leaves.
  - Twins (run-time, `FNVCOOP_MUT=n`): 7 no drain at all, 8 instruction pointer only, 10 timeout counts as success, 11 ignore list not honoured, 12 only the first thread inspected, 13 no waiting, 14 JSON not escaped. 7 to 14 are caught by `nvreload_drain.exe`; 7 and 8 are also caught by the host test (the unmapped-code crash or the wrong result, and the refusal check). Host twin 9 is the unwired hook write guard.
  - Run: `plugin\reload\run_tests.cmd` (prints ALL OK).
  - Only unit-tested: nothing here has run in the game. The drain has been exercised only against a test core and fake detours; real NV threads (render, audio, loader) are not covered, and `reload_proof.py` in a real game has not been run.
- Hook writes go through a freeze guard (`freezeOthers`); host mutant 9 (guard not wired) is caught by the check that the core's `guardcalls` count is above zero each cycle. Whether a torn write is actually possible in a real game is unknown; the guard is a precaution.
- Flight recorder: participant `t1a_flight` (order 5, so its shutdown runs last) calls `service().shutdown()`, which dumps and runs `removeCrashHandler()` before the core unloads. Not unit-tested separately; the existing flight tests cover shutdown.
- Transport across reload: only the UDP socket is handed over. ENet peer state lives in the core, so peers must reconnect after a reload. Moving the ENet host into the loader means ENet in the loader plus an ABI for service/poll/send and every synccore transport call site; I estimate more than 2 days, so it is NOT done. The reconnect time has not been measured (no ENet in the host-process test); measure it in game with `reload_proof.py` while a client is connected.
