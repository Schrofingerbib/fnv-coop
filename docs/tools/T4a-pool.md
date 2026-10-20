# T4a-pool: warm game pool and low-cost mode

Status: code complete, game-free tests green (`python -I tools/pool/run_tests.py` prints `ALL OK`). The RAM and CPU
table below was measured with the pool and a real game on 2026-10-20. The lead's first in-game steps are at the end.

## What it does

A daemon keeps N (default 1, max 2) provisioned game instances running and minimized, and hands them to tests.

- **Instance id equals the game-slot index.** The pool takes N slots from `tools/gameslot.py` as owner `pool`
  (`acquire --slots N`), so other lanes see the machine as busy, and pipe names stay `\\.\pipe\nvcoop-<id>`.
- Every launched pid is recorded with `gameslot register-pid`. The pool kills only pids it launched itself
  (and only while they are still a `FalloutNV.exe`). On `up` it also reaps pids that a crashed earlier daemon
  recorded in `runs/pool/state.json`. It never kills "all FalloutNV.exe".
- **Health check** (every 5 s and again at lease time): process alive and bridge `ping` answers. A dead or
  unresponsive instance is restarted. A dead instance is never handed to a lessee.
- **Recycle** (kill and relaunch) when: K leases completed (default 25), working set above boot size + 1200 MB or
  above 3300 MB (32-bit game), the lessee called `dirty`, the lease expired, or the health check failed.
  The thresholds are guesses; tune them after `measure.py`.
- Start-up retries stop after 3 failures in a row; then `lease` raises instead of hanging.

## Commands

```
python -I tools/pool/pool.py up [--size 1] [--base-save NAME] [--recycle-after 25] [--grids 3] [--audio-off] [--no-lowcost] [--fake]
python -I tools/pool/pool.py status
python -I tools/pool/pool.py lease --owner NAME [--n 1] [--ttl 900] [--timeout 600]   # prints JSON; lives until release or ttl
python -I tools/pool/pool.py release --id L3 [--dirty]
python -I tools/pool/pool.py down
```

Instances must be provisioned first (`python -I tools/runner/run.py provision --n 2`). `up` does not provision.
Provisioning rewrites each instance's nvcoop.ini. Give extra entries with the repeatable `--ini SECTION.KEY=VALUE`
(for example `--ini coop.enable=1 --ini dev.bots=1`), not with a hand edit. One `--ini` value applies to every
instance. So with `--n 2` or more, the per-instance keys `bridge.instance`, `coop.instanceId`, `coop.pipe` and
`coop.role` are an error (exit 2). Provisioning sets them for each instance.
`--fake` runs an in-memory fake game (no launch, no pipes).
The daemon listens on 127.0.0.1 only; port and a random token are in `runs/pool/pool.json` (`POOL_DIR` overrides
the folder). Log: `runs/pool/daemon.log`.

## Lease API (agreed with T4b-scenarios)

```python
sys.path.insert(0, "tools/pool"); from client import lease
with lease("scn-goodsprings", n=2, ttl_s=900, timeout_s=600) as L:
    L.instances      # [0, 1], host first
    nv = L.bridge(0) # NvBridge that already holds the bridge lock.* lease for L.owner
    L.boot(0)        # id of that game process: "<pid>-<random token>"
    L.dirty(0, "why")  # instance is recycled on release
    L.renew()
```

- Leaving the block releases the lease; an exception inside marks every instance dirty.
- If an instance was restarted under you, `L.bridge()` raises `PoolError` (generation changed).
- `L.boot(i)` (`boots` in the lease JSON, `boot` per instance in `status`) names the game process. The pool draws a new
  one at every successful start, so it never repeats, also not after `down` and `up`. The generation (`gen`) starts again
  at 1 on every pool start, so do not key state that must die with the game by `gen`. T4b keys its scenario state by
  `boot` (docs/tools/T4b-scenarios.md, "State keys"). A health-check restart at lease time updates `boots` as it updates
  `gens`. Tests: `check_boot_id_per_process` (twin `gen_boot`: the boot id is the generation alone) and
  `check_boot_refreshed_at_lease` (twin `stale_boot`: the lease keeps the boot id of the replaced process).
- `PoolBusy` means no free warm instance within `timeout_s`. `PoolNotRunning` means no daemon.
- The pool does not reset game state between leases. Resetting in place is the scenario runner's job (T4b); it
  calls `L.dirty()` when it could not reset cleanly. `--base-save` loads a save once into each freshly booted instance.

## Low-cost profile

`tools/pool/lowcost.py` edits only the instance-local `Fallout.ini` and `FalloutPrefs.ini` under the instances root.
It refuses (and tests prove it) the owner's `Documents\My Games`, the Steam game dir, hard-linked files and symlinks.
A test also hashes the owner's INIs before and after. The pool applies it on every instance start (idempotent).
Disable with `--no-lowcost`. With `--no-lowcost`, every instance start writes back the two INIs that provisioning
writes (`lowcost.restore`, the output of `instances.render_inis()`). A profile from an earlier `up` therefore does not
stay in force. The restore uses the same guard: it refuses the owner's `Documents\My Games`, the Steam game dir,
hard-linked files and symlinks, and it writes neither INI when one of them fails the guard. It also removes manual
edits to these two instance INIs. `python -I tools/pool/lowcost.py restore --instance 0` does the same by hand.
`python -I tools/pool/lowcost.py verify` lists which keys exist in the owner's own INIs.

Column "Status": `present in owner INI` means the key exists in the owner's INI, so the name is right.
`not in owner INI (added)` means the name is a guess from other Bethesda games; the engine ignores unknown keys, so
a wrong guess costs nothing but the saving is unknown. `opt-in` rows are off unless asked for.

| File | Section | Key | Value | Why | Status |
|---|---|---|---|---|---|
| Fallout.ini | General | `bUseMyGamesDirectory` | `0` | read INIs and saves from the instance dir, never from Documents\My Games | not in owner INI (added) |
| Fallout.ini | General | `bAlwaysActive` | `1` | keep ticking while minimized (the pool keeps windows minimized). Not enough on its own: a deactivation in gameplay opens the pause menu, so nvcoop.dll also needs its focus guard (`[bridge] focusGuard`, on by default with `minimized=1`) | present in owner INI |
| FalloutPrefs.ini | Display | `bFull Screen` | `0` | windowed so it can stay minimized without a mode switch | present in owner INI |
| FalloutPrefs.ini | Display | `iSize W` | `640` | smallest practical resolution; fewer pixels to shade and a smaller back buffer | present in owner INI |
| FalloutPrefs.ini | Display | `iSize H` | `480` | see iSize W | present in owner INI |
| FalloutPrefs.ini | Display | `iPresentInterval` | `0` | no vsync wait | present in owner INI |
| FalloutPrefs.ini | Display | `iMultiSample` | `0` | no MSAA | present in owner INI |
| FalloutPrefs.ini | Display | `iMaxAnisotropy` | `0` | no anisotropic filtering | present in owner INI |
| FalloutPrefs.ini | Display | `bTransparencyMultisampling` | `0` | no alpha-to-coverage | present in owner INI |
| FalloutPrefs.ini | Display | `bDoDepthOfField` | `0` | no depth-of-field pass | not in owner INI (added) |
| FalloutPrefs.ini | Display | `bAllow30Shaders` | `0` | shader model 2 paths, cheaper | present in owner INI |
| Fallout.ini | General | `iFPSClamp` | `30` | cap at 30 fps: halves CPU and GPU load per instance; game time is delta-based | present in owner INI |
| FalloutPrefs.ini | Display | `iShadowFilter` | `0` | no shadow filtering | present in owner INI |
| FalloutPrefs.ini | Display | `iActorShadowCountExt` | `0` | no actor shadows outdoors | present in owner INI |
| FalloutPrefs.ini | Display | `iActorShadowCountInt` | `0` | no actor shadows indoors | present in owner INI |
| FalloutPrefs.ini | Display | `bActorSelfShadowing` | `0` | no actor self shadows | present in owner INI |
| FalloutPrefs.ini | Display | `bShadowsOnGrass` | `0` | no shadows on grass | present in owner INI |
| FalloutPrefs.ini | Display | `bDoCanopyShadowPass` | `0` | no canopy shadow pass | present in owner INI |
| FalloutPrefs.ini | Display | `iShadowMapResolutionPrimary` | `256` | smallest shadow map if shadows get re-enabled | not in owner INI (added) |
| FalloutPrefs.ini | Display | `iShadowMapResolutionSecondary` | `256` | see above | not in owner INI (added) |
| Fallout.ini | Grass | `bDrawShaderGrass` | `0` | do not draw shader grass (large CPU saver outdoors) | present in owner INI |
| Fallout.ini | Display | `bDoTallGrassEffect` | `0` | no tall grass effect | present in owner INI |
| FalloutPrefs.ini | Water | `bUseWaterReflections` | `0` | no water reflection render | present in owner INI |
| FalloutPrefs.ini | Water | `bUseWaterRefractions` | `0` | no water refraction render | present in owner INI |
| FalloutPrefs.ini | Water | `bUseWaterDepth` | `0` | no water depth pass | present in owner INI |
| FalloutPrefs.ini | Water | `bUseWaterDisplacements` | `0` | no water displacement | present in owner INI |
| FalloutPrefs.ini | Water | `bUseWaterHiRes` | `0` | low-res water | present in owner INI |
| Fallout.ini | Water | `bUseWaterReflections` | `0` | same keys in Fallout.ini (stock files carry both copies) | present in owner INI |
| Fallout.ini | Water | `bUseWaterRefractions` | `0` | see above | present in owner INI |
| FalloutPrefs.ini | Display | `bUseRefractionShader` | `0` | no refraction shader (heat haze, glass) | present in owner INI |
| FalloutPrefs.ini | Display | `bFXAA` | `0` | no post-process AA | not in owner INI (added) |
| Fallout.ini | Display | `iTexMipMapMinimum` | `2` | raise the minimum texture mip (default 0) to cut texture memory; whether the engine skips the big mips for this key is unknown | present in owner INI |
| Fallout.ini | Display | `fDecalLifetime` | `1` | decals expire quickly | present in owner INI |
| Fallout.ini | Display | `bDecalsOnSkinnedGeometry` | `0` | no decals on skinned meshes | present in owner INI |
| Fallout.ini | General | `SIntroSequence` | `` | skip the logo movies | present in owner INI |
| Fallout.ini | General | `SMainMenuMovieIntro` | `` | skip the main menu intro movie | present in owner INI |
| Fallout.ini | General | `uMainMenuDelayBeforeAllowSkip` | `0` | no forced wait on the main menu | not in owner INI (added) |
| Fallout.ini | General | `uGridsToLoad` | `3` | load 9 exterior cells instead of 25: big RAM and CPU saver. RISKY: a save made with another value can crash or corrupt on load, so it is off unless --grids is given and every save the pool loads was made at that value | opt-in |
| Fallout.ini | Audio | `bEnableAudio` | `0` | no sound device, saves CPU. Opt-in because dialogue-driven waits (voice line end) may never complete without audio | opt-in |

Not done on purpose: `uExterior Cell Buffer` / `uInterior Cell Buffer` (cache size, effect on stability unknown),
texture memory beyond `iTexMipMapMinimum` (no verified key), and lowering `uGridsToLoad` by default (a save made at
one grid size can crash or corrupt when loaded at another; use `--grids 3` only when every save the pool loads was
made at 3).

## RAM and CPU estimate

Measured on 2026-10-21, one warm instance per profile, with the game world running (see the first condition):

| Profile | RAM per instance (MB) | CPU idle (% of one core) | CPU during a test | Free system RAM |
|---|---|---|---|---|
| low-cost | 678-710 (min-max working set) | 36.7 avg (max 47.0) | not measured | not measured |
| normal | 684-716 (min-max working set) | 38.0 avg (max 46.2) | not measured | not measured |

Conditions:

- The world was running during every sample: game time advanced by about 2.2 to 2.5 hours in each 5-minute sample
  (TimeScale 30). With all DLC the engine opens native message boxes (MessageMenu 1001, 10 boxes in waves) after a save
  loads, and each one keeps the game in menu mode. They were pressed away first (PR cranebeaker/fnv-coop#9), after the
  autoload had finished. Earlier numbers (about 250-700 MB, 12.5 to 13.2 % of one core) were measured with a box open,
  so with the world paused. They are not used.
- 1 instance, window minimized, 640x480 (provisioning sets 640x480 for both profiles).
- `FalloutNV.exe` 1.4.0.525 (Steam) with all DLC, xNVSE 6.4.9.
- A Goodsprings save, loaded by the nvcoop autoload.
- `nvcoop.dll` with the focus guard from PR cranebeaker/fnv-coop#3, so minimizing did not open the pause menu.
- Exactly one nvcoop DLL in the instance's Plugins folder (provisioning left older copies there before PR #5).
- 5 minutes of samples with `tools/pool/measure.py`.
- Other heavy programs ran on the PC. Free system RAM is therefore not meaningful.

In this setup the low-cost profile saves little: about 6 MB of working set and about 1.3 % of one core. Plan for about
0.7 GB of RAM and about 40 % of one core per running copy.

Both rows were measured after this fix: `up --no-lowcost` now restores the provisioned INIs, which was checked in the
game (the low-cost keys were gone and the copy started normally). Before the fix, a "normal" run after a low-cost run
measured the low-cost profile again.

To measure again, with the pool idle:

```
python -I tools/pool/pool.py up --size 1
python -I tools/pool/measure.py --label lowcost --minutes 5
python -I tools/pool/pool.py down
python -I tools/pool/pool.py up --size 1 --no-lowcost
python -I tools/pool/measure.py --label normal --minutes 5
python -I tools/pool/pool.py down
```

`up --no-lowcost` writes the provisioned INIs back on each instance start, so no re-provision is necessary between the
two runs. (Pool code from before this fix does not do this. With that code, re-provision before the "normal" run.)
Results go to `out/T4a-pool/measure-<label>.json` (working set MB, CPU percent of one core, free system RAM).

Also unknown: whether the fps cap of 30 changes any timing-based test; whether the 640x480 window keeps the HUD
usable for screenshots.

## First in-game run (for the lead)

1. `python -I tools/runner/run.py provision --n 1` (needs `out/L1/nvcoop.dll`).
2. `python -I tools/pool/pool.py up --size 1`; check `status` shows `warm` and the window is minimized.
3. `python -I tools/pool/pool.py lease --owner smoke`, then `python -I tools/runner/nvbridge.py --instance 0 ping`,
   then `release --id <id>`; `status` should show `tests: 1`.
4. Kill the game from Task Manager; within 5 s `status` should show the instance restarting (`gen` goes up).
5. `down`; confirm no `FalloutNV.exe` of the pool remains and `gameslot.py status` shows free slots.

## Files

`tools/pool/`: `pool.py` (CLI), `daemon.py`, `core.py` (policy), `backend.py` (real and fake OS layers), `client.py`
(lease API), `lowcost.py`, `measure.py`, `run_tests.py` (tests plus mutation twins; also in `tools/test_all.py`).
