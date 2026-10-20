# T3a-loader: hot-reload loader / core split

Status (2026-10-20): loader, ABI, game-free host and tests are done and green. The split build is opt-in
(`-DNVCOOP_SPLIT_LOADER=ON`); the default build is the unchanged monolithic `nvcoop.dll`. The loader DLL
(`nvcoop_loader` target) builds and exports the NVSE entry points. The full split build (loader plus core DLL) now builds clean (2026-10-20). Nothing
here has been run in the game (code-only sprint).

## What it is

- **Loader** = `nvcoop.dll` in split mode: `plugin/main.cpp` built with `NVCOOP_LOADER`, plus the bridge
  (named pipe, `Host` for console/save/screenshot), the registry (events, methods), crash filter, minimizer, and
  `plugin/loader/loader_host.cpp` (`CoreHost`). It stays loaded for the whole process.
- **Core** = `nvcoop_mod.dll`: the old `nvcoop` CMake target (hooks, snapshot, coop, ui, world, nvmp, flight,
  reload registry). Because the target keeps the name `nvcoop`, every lane's `target_sources(nvcoop ...)` works
  unchanged. Its entry is `plugin/loader/core_entry.cpp` (module dispatch, `state` and `plugins` bridge
  methods); `core_thunks.cpp` forwards `PushEvent` / `RegisterMethod` / `SetStateProvider` / `Log` /
  `RegisterModule` to the loader so module code needs no changes.
- The loader copies the core to `<plugins dir>\nvcoop_cache\nvcoop_mod.<pid>.<n>.dll` before `LoadLibrary`, so
  the build can overwrite `nvcoop_mod.dll` while the game runs. Stale cache copies of dead processes are deleted
  at start (a live game's copy is locked, so it is skipped).
- The core's modules read `nvcoop.ini`, `nvcoop_*.json`, `nvcoop_consent_messages.txt` and
  `nvcoop_volatile_keys.txt` from the folder of their own module file, and that file is the cache copy. So before
  each `LoadLibrary` (first load, reload and rollback) the loader copies these files into `nvcoop_cache\`. It
  overwrites older copies, deletes a copy whose source file is gone, and skips a missing file. If the source folder
  cannot be listed, it keeps the older copies and writes a log line. The source folder is `HostEnv::configDir`. By
  default it is `<game>\Data\NVSE\Plugins\`, the loader's folder, where the loader also reads its own `nvcoop.ini`.
  This is also true when `[loader] core=` puts the core in a different folder. Only when `HostEnv::gameDir` is empty
  (the tests) is the default the folder of the core file. A copy that fails with a sharing or lock violation (an
  editor holds the file) is tried up to 10 times, 50 ms apart. Any other copy error is logged at once, and the core
  reads the older cache copy, if there is one. An edit to `nvcoop.ini` takes effect at the
  next reload, not while the core runs. The file watcher does not watch the config, so use `dev.reload` after an
  edit. `dev.status` lists the copied files in `configFiles`.

## Build and use

- Canonical monolith build (unchanged): `cmd /c tools\runner\build_l1.cmd`.
- Split build: `plugin\loader\build_split.cmd` (own dirs `build\L1split`, `out\L1split`, so it never touches
  out\L1). Or pass `-DNVCOOP_SPLIT_LOADER=ON` to the normal configure.
- Install: put both `nvcoop.dll` (loader) and `nvcoop_mod.dll` (core) in `Data\NVSE\Plugins\`. For test instances,
  `python -I tools/runner/run.py provision --n 2 --plugin-dll out/L1split/nvcoop.dll --core-dll out/L1split/nvcoop_mod.dll`
  does this and writes `[loader] core=<core file name>` into nvcoop.ini. A path that does not exist is an error.
  `--core-dll` without an explicit `--plugin-dll` is an error: the default out\L1\nvcoop.dll can be the monolith.
- Reload: bridge method `dev.reload` (params `{"reason":"..."}`, optional) returns `{generation, msUnload, msLoad}`
  or an error; `dev.status` returns the loader state. Events `dev.reloading`, `dev.reloaded`, `dev.reloadFailed`
  go into the normal event ring (which lives in the loader, so they survive reloads).
- File watcher (opt-in): `nvcoop.ini`
  ```
  [loader]
  core=nvcoop_mod.dll      ; file next to the loader dll
  watch=1                  ; or env NVCOOP_WATCH=1
  watchIntervalMs=500
  ```
  The watcher polls size + write time, waits for one quiet interval (the linker finished), then only sets a flag;
  the reload runs on the main thread at the next frame.

## ABI (plugin/loader/loader_abi.h)

C types only (loader and core each have a static CRT). `NvLoaderApi` (loader to core): log, push_event,
register_method, set_state_provider, blob_put/get/erase, request_reload, generation, is_reload, game_dir,
nvse_interface. `NvCoreApi` (core to loader): init, on_message, on_frame, before_unload. The core exports
`int NvCore_Init(const NvLoaderApi*, NvCoreApi*)`. Both structs start with `{abi_version, size}` and only grow at
the end. Bridge methods registered by the core are removed by the loader before unload; a core method that is
mid-call blocks the unload (up to 2 s), and a stale closure that is called later answers "core is reloading"
instead of running freed code.

## Reload sequence (main thread, inside the frame message, never inside a core call)

1. Close the gate for the old core's bridge methods; wait for calls in flight (2 s limit, else refuse).
2. `core.before_unload()`: the core (T3b's `nvcoop::reload::BeforeUnload`) stops threads, closes sockets,
   uninstalls hooks and checks the original bytes, writes state with `blob_put`. Returns 0 to veto: the gate
   reopens and the old core keeps running unchanged.
3. Purge the core's bridge methods and state provider; `FreeLibrary`; check the module is really gone.
4. Copy the new `nvcoop_mod.dll` to the cache, `LoadLibrary`, `NvCore_Init`, `core.init()` (modules `onLoad`,
   then T3b's `AfterLoad` restores from blobs; `api->is_reload == 1`).
5. If step 4 fails: restore the blob store from a snapshot and load the previous cached copy again (rollback).
   Only if that fails too is the core "dead" (`dev.status` says so; `dev.reload` can retry later, also when the
   core file did not exist at game start).

If the old core refuses (`before_unload` returns 0), the reload is refused and nothing changes. A core whose
`init()` fails gets one `before_unload()` call so a half-initialised core can remove its hooks.

## Tests (game-free): `plugin\loader\run_tests.cmd` prints `ALL OK`

Builds with Ninja x86 into `build\T3a` / `out\T3a`: stub core DLLs (v1, v2, init fails, no export, wrong ABI) and
`nvloader_test` (215 checks): first load; frame/message dispatch; the build overwriting the core file while
loaded; reload keeps state and swaps methods; veto leaves the old core intact; four kinds of broken build roll
back; 100 reloads with no leaked thread, handle or cache file and the state preserved; a bridge call in flight;
stale closure refusal; the file watcher; `request_reload`; `dev.reload`/`dev.status`; orderly unload; loading a
core that appeared after game start; the config mirror. For the config mirror the stub core reads `nvcoop.ini`
(read-only) and `nvcoop_stub.json` beside its own module at init. The test checks the values at the first load,
after a reload with an edited ini and json, after each rollback, in each of the 100 reloads, after the json is
deleted, and with no ini at all. Only config names are copied (not `nvcoop.log` or the core DLL), and
`nvcoop_volatile_keys.txt` is one of them. A config folder
that cannot be listed does not delete the cached copies (checked once with a manual mutation, not a built twin).
A core in a subfolder of the plugins folder (`<game>\Data\NVSE\Plugins\devcore\`, with a different `nvcoop.ini`
in each folder) must read the ini of the plugins folder. Three folders with config names in the cache make three
copies fail with access denied; the reload must not be more than 300 ms slower than a plain reload (the old loop
added 1.5 s). A `nvcoop_stub.json` held open with no sharing for 300 ms must still reach the core.
Mutation twins, each of which must make the test fail: `nocopy` (core loaded in place), `nopurge` (old methods
left registered), `noveto` (veto ignored), `norollback`, `noconfig` (config not copied), `configonce` (config
copied only at the first load), `noprune` (a deleted config file lives on in the cache), `noattrs` (a read-only
copy blocks the next copy), `novolatile` (`nvcoop_volatile_keys.txt` not a config name), `coredirconfig` (config
from the core's folder although `gameDir` is set), `retryall` (every copy error retried, with a wait after the last
try), `noretry` (a sharing violation not retried).
`plugin\reload\run_tests.cmd` also runs its host test against the loader twins `noconfig` and `configonce`. There
the test core reads `[testcore] value=` beside its own module, and the test rewrites the ini before each of the
100 reloads.
Wired into `tools/test_all.py` as "T3a loader" (compiles, so skipped by `--quick`).

## Edits outside plugin/loader (all small)

- `plugin/main.cpp`: `#ifdef NVCOOP_LOADER` blocks (4 hook points + core creation in `NVSEPlugin_Load`). The
  monolith compiles exactly as before.
- `plugin/CMakeLists.txt`: `NVCOOP_SPLIT_LOADER` option (default OFF) and `add_subdirectory(loader)` when ON.
- `plugin/common/nvcoop_api.{h,cpp}`, `registry.h`: `RegisterMethodClosure` + `UnregisterMethodsByOwner`;
  `MethodEntry::fn` is now a `std::function` (all existing callers compile unchanged).
- `tools/test_all.py`: one line.
- Duplicated by hand: the `state`/`plugins` method bodies exist in both `main.cpp` (monolith) and
  `core_entry.cpp` (split). They are ~40 lines; keep them in sync.

## Verification state and limits (plain)

- Verified by running: the game-free tests and all twelve loader mutants (this file's "Tests" section); the loader DLL
  builds and exports `NVSEPlugin_Query` / `NVSEPlugin_Load`.
- Not verified by these tests: the full split build of the core DLL (the first line of this file records that it built clean on 2026-10-20; the loader tests do not build it),
  and anything in the real game: that NVSE accepts the loader, that real modules survive a reload, and that no
  module registers NVSE-side callbacks. I found none in the plugin sources (no `RegisterListener` /
  command registration outside `main.cpp`), but that was a text search only.
- A core that holds the DLL open after `FreeLibrary` (a leaked thread or a hook trampoline still in the code)
  is detected ("still mapped") and reported as a dead core; the old code stays mapped. T3b's leak probes are the
  defence against that.
- `dev.reload` runs on the main thread inside the bridge pump; the client gets its reply after the reload has
  finished (no polling needed).
- Process exit does not unload the core (same as the monolith). `CoreHost::Unload` exists for tests.

## Update 2026-10-20 (T3-reload)
- Canonical build, split build, `plugin\loader\run_tests.cmd` (177 checks, 4 mutants) and `plugin\reload\run_tests.cmd` (1622 checks, unit mutants 1-8, host mutants 1-9) are green.
- The in-flight detour drain and the crash filter removal are in T3b-state.md. The core's full split build is no longer a blocker.

## Update 2026-10-20 (split-mode config)
- Bug (found by code reading, not seen in a game run): in split mode the core runs from `nvcoop_cache\`, but its
  modules look for `nvcoop.ini` and `nvcoop_*.json` beside their own module. They found nothing, so `[coop]`, `[dev]`
  and `nvcoop_rules.json` had no effect and co-op stayed off (`[coop] enable` defaults to 0).
  `tools/runner/reload_proof.py` then reports the session check as skipped.
- Fix: the loader copies the config files into the cache before each load (see "What it is"). The fix is in
  `plugin/loader` only. No module code changed: the modules still read beside their own module.
- `plugin\loader\run_tests.cmd` (200 checks, 8 mutants) and `plugin\reload\run_tests.cmd` (1723 host checks, host
  mutants 1-9, loader twins `noconfig` and `configonce`) are green. Not run in the game.

## Update 2026-10-20 (split-mode config, review fixes)
- `nvcoop_volatile_keys.txt` is now a config name. `plugin/snapshot/src/baseline_nvse.cpp` is in the core and reads
  it beside its own module. Without the copy, the baseline dump fell back to the in-repo copy named at build time
  (`FNVCOOP_DEFAULT_VOLATILE_KEYS`), or, if that file does not exist, to no keys file.
- `configDir` now defaults to `<gameDir>Data\NVSE\Plugins\`, the loader's folder, not to the core's folder. They
  differ when `[loader] core=` has a folder part.
- The config copy retries only `ERROR_SHARING_VIOLATION` and `ERROR_LOCK_VIOLATION`, and does not wait after the
  last try. Before, every error but "not found" cost 10 x 50 ms per file on each load.
- `plugin\loader\run_tests.cmd` (215 checks, 12 mutants) and `plugin\reload\run_tests.cmd` (1723 host checks) are
  green. Not run in the game.
