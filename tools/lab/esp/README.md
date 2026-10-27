# NVCoopLab.esp generator

Pure Python (stdlib only, run with `python -I`). Writes the co-op lab test plugin and the `layout.json` the lab runner reads
(contract: `docs/LAB.md`). The game is never started and no GUI tool is used: the plugin is written as bytes in the FNV plugin format
and checked by reading it back and by reading the real `FalloutNV.esm` with the same reader.

```
python -I tools/lab/esp/build_lab.py --esm <FalloutNV.esm> --out out/lab        # out/lab/NVCoopLab.esp + out/lab/layout.json
python -I tools/lab/esp/install_lab.py --instance-dir <instance folder>          # copy into a test copy, make it load
python -I tools/lab/esp/install_lab.py --instance-dir <instance folder> --uninstall
python -I tools/lab/esp/run_tests.py                                            # tests + 28 mutation twins, ends in ALL OK
```

`--esm` defaults to `FNV_ESM`, else `<games>/fnv-instances/0/Data/FalloutNV.esm` (a copy; the file is opened read only, the stock
game folder is never touched). `--enemies-enabled` places the fight and vats creatures enabled (see "Hostile actors" below).

## Cell choice: override GreenRoom, do not make a new cell

A new cell has no navmesh, and without a navmesh actors cannot path: the npc room (patrol, ownership handoff) and the fight room
(creatures that close in) both need movement. The GECK's navmesh tools are not available here, so the plugin overrides a stock interior that
already has a navmesh instead of writing one by hand.

Evidence from `FalloutNV.esm` (read with this reader, 388 interior cells):

- There is no QASmoke or similar cell. The only `TestQA*` records are containers; the `Test*` cells are `TestJoshWeapons` (its navmesh
  is four single-triangle fragments), `TestTraps` (59 statics, 29 activators and 9 lights, the traps), `TestMentats` (94 refs),
  `HDCombatTest` and `HooverDamIntTest` (NPCs, doors).
- **GreenRoom (`FalloutNV.esm:168FBC`)**: one navmesh (62 vertices, 100 triangles) that covers its whole
  2304 x 2048 rectangle (x 2560..4864, y -6656..-4608) at a flat z of 7040. Its only content is 105 floor-tile statics and one
  persistent NPC (`168FBD`). No containers, doors, traps, lights or scripts. A green-screen style test room.

What the plugin does with it:

- Overrides the CELL record (all stock subrecords kept, so lighting, water and flags are stock), renames it `NVCoopLab` (full name
  "NVCoop Lab") so `coc NVCoopLab` works as in the contract. `layout.json` carries `cell.editor_id` = `NVCoopLab`,
  `cell.editor_id_alt` = `GreenRoom` (the stock name, in case a build keeps the first editor id) and `cell.form` =
  `FalloutNV.esm:168FBC`. The navmesh stays with the stock cell (an override of the CELL record does not replace its child records).
- Disables the stock persistent NPC (an override of `168FBD` with the initially-disabled flag), so the only actors are ours.
- Adds all lab content as persistent refs (flag 0x400) in the cell's persistent child group, so both copies hold the same refs
  with the same ids. Our own forms start at `0x000800` (file index 01, because `FalloutNV.esm` is the only master): `NVCoopLab.esp:000810`
  and up for refs, `000801`..`000804` for the base records.
- Every placed point is on that navmesh with at least 96 units of mesh all around it (the tests check this against the NAVM data).

Rooms sit on a 4 x 2 grid, at least 480 units apart, so no room's content is in another room's way. All ten stock records we depend on are
checked at build time for type and editor id, so a different game build fails loudly instead of producing a wrong plugin.

## Rooms (origins are cell coordinates, z = floor 7040)

| Room | Origin (x, y) | Content |
|---|---|---|
| spawn | 2992, -6048 | `host` and `client` heading markers 120 apart, stand marker |
| walk | 3472, -6048 | 4 waypoint markers on a 300 x 300 square, stand just before waypoint 1 |
| fight | 3952, -6048 | 3 geckos (`NVCrGecko`, aggression 2, health 65), stand 350 from the middle one |
| loot | 4432, -6048 | chest `NVCoopLabChest` (5 Stimpak, 3 RadAway, 250 caps, 40 10mm, 2 Buffout) and 4 loose items (Stimpak, Nuka-Cola, Beer, RadAway) |
| door | 2992, -5216 | in-cell door `OffDoorSmL01` with lock level 25, stock unlock terminal linked to it |
| npc | 3472, -5216 | `NVCoopLabCivilian` (friendly) patrolling between two markers 300 apart, 5 s idle |
| vats | 3952, -5216 | 2 geckos 350 from the stand marker |
| quest | 4432, -5216 | `NVCoopLabQuest` (stages 10/20/30), `labvar`, an anchor marker |

`layout.json` follows `docs/LAB.md`. Additions the runner can use or ignore: `cell.editor_id_alt`, `cell.mode`, `cell.floor_z`,
`rooms.<r>.initially_disabled` and `rooms.<r>.info` (items in the chest, lock level, patrol path, quest stages and variable).

## Decor: lighting, partitions, cameras (`decor.py`)

Why the first in-game pictures were a grey void: GreenRoom is an enclosed office-kit room. Read from the master and from the meshes in
`Fallout - Meshes.bsa`: the 70 `OffRmMid01` tiles are floor (z 0) and a torn ceiling (z 256) in one piece, and 30 `OffRmWall01` plus 4
`OffRmCorIn01` form a wall ring around the whole grid. The stock lighting is ambient 50, no directional, fog 100..2500 in grey, and there are
no light refs. A camera above z 256 only sees the dark ceiling top with its holes (the "holes" in the pictures); a camera below it sees a dark
box. There are no floating rock or other non-floor refs to disable: the stock refs are the tiles, the ring, and one `COCMarkerHeading` (where
`coc NVCoopLab` puts the player), so `decor.disabled_stock_refs` is empty and nothing stock is touched.

What is added (all persistent refs, form ids from `NVCoopLab.esp:000900`, after the room refs; the navmesh is not changed):

- **Cell lighting**: the CELL `XCLL` is replaced: ambient 150, directional 120, fog colour 110 (all neutral grey), fog 20000..40000, far clip 40000.
  The rest of the cell record is stock. The directional rotation (45, 45) was not checked in game.
- **Lights**, 2 per room, both checked against their `FalloutNV.esm` records: `gVaultAmbWhite512` (`0002F50D`, white, radius 512) at
  (origin x, y - 110) and `MALightFillWarm` (`0011E398`, warm grey, radius 1024) at (origin x, y + 130), both at floor + 200 (the ceiling
  fixtures hang from 237). Both have flags 0 (no flicker, pulse, spot or off-by-default), no burn-out time and no model. The generator and
  the tests refuse a base that flickers, pulses, is a spot, starts off or has a radius under 400.
- **Partitions**: `OffRmWallONLY01a` (`000288B6`, 43 thick, 260 long, 256 tall, has a Havok collision body: the test reads the NIF out of
  the BSA to prove it, and skips without the BSA). Per room: a back wall on the north side (`origin y + 330`) and side partitions on the
  room boundaries (`origin x +- 240`, shared with the neighbour) that run from `origin y - 428` to the back wall. The front (south) of every
  room is open, so cameras and the runner can come in from there. 46 pieces: 30 side partitions (5 lines x 2 rows x 3) and 16 back wall pieces
  (8 per row, overlapping a little). The outer boundary of the grid is the stock wall ring, nothing is added there.
- **Clearance**: every ref and marker of every room, and every walking or firing line (stand to each target, the waypoint square, the patrol
  line), keeps at least 60 units from every wall footprint; the actual minimum is 69 (the walk waypoints and patrol markers, 150 from the
  room origin, against partitions 218 from it). Walls stand outside the rooms' content, never across a path.
- **`layout.json` `decor`** (ignored by the runner): `lighting`, `lights` (form, base, room, pos, radius), `walls` (form, role, pos, rot_z, axis),
  `wall_base`, `light_base`, `outer_boundary`, `disabled_stock_refs`, `first_form`, and `cameras`: a suggested camera per room (feet at floor + 20 so
  the eye is under the 256 ceiling, inside the partitions, in front of the stand marker, with yaw and pitch toward the room).
  `tools/lab/shoot_arena.py` currently puts the camera at feet z = floor + 160 (eye above the ceiling) and at x - 260 (behind the partition);
  use `decor.cameras` instead for readable pictures.

Not done: removing the ceiling. The floor and the ceiling are one mesh with one collision body, so disabling the tiles would drop the floor.

## Hostile actors start disabled

The room is open floor with no walls, so a hostile gecko placed enabled would see the players in the spawn room and run at them. The fight
and vats creatures are therefore placed initially disabled (flag 0x800) and listed in `rooms.fight.initially_disabled` and
`rooms.vats.initially_disabled`. The runner enables them (console `enable`) when it starts that room. They are still persistent refs with the
same ids on both copies. `--enemies-enabled` builds the other variant.

## Door and terminal: no script of ours

The terminal base is the stock `DefaultUnlockTermWALL` (`0EAC00`; DNAM flag 0x02 "unlocked": nothing to hack). The `DESK0VeryEasy` one (`064B85`), used before, has flag 0, so the first in-game run's use opened the HACKING menu and the terminal menu never came. Its menu item "Disengage Lock" runs the
stock result script `ref myLink / set myLink to getLinkedRef / myLink.unlock`. The terminal ref only has an `XLKR` link to the door ref, which is
exactly how the stock game wires these terminals. The door is `OffDoorSmL01` (a stock in-cell door, no teleport data) with an `XLOC` lock of
level 25 and no key. Checks: open, close, unlock through the terminal.

## NPC patrol: no script of ours

`NVCoopLabCivilian` is a copy of the stock `NVProspectorMaleA` (unaggressive, level 6) with its own package list: the stock patrol package
`DefaultPatrolCasualLinkedRef` (type patrol, location = linked reference) and the template's "use AI packages" flag cleared so the list
is not overridden. The NPC ref links (`XLKR`) to marker A; marker A links to B and B to A, each with the stock patrol extras (idle time 5 s),
the same extras the stock patrol markers have.

## Quest

`NVCoopLabQuest` has stages 10, 20, 30 (with log text) and the quest script `NVCoopLabQuestScript`, which only declares `short labvar` (its compiled
body is the stock "script name only" body, copied from stock quest scripts of the same shape). Flags: start game enabled, as most stock quests.
There is no script logic, so nothing sets the variable by itself: `set NVCoopLabQuest.labvar to N` and `SetStage NVCoopLabQuest N` from the console
are the actions. Writing real quest script bytecode was not needed for the checks in `docs/LAB.md`.

## Installing into a test copy

`install_lab.py` copies the esp to `<instance>/Data/` and sets the first free `STestFile` slot (2..10) in the instance's `Fallout.ini`. The
instances read their INIs from their own folder, but have no `plugins.txt` of their own: the game reads
`%LOCALAPPDATA%\FalloutNV\plugins.txt`, shared with the real game, which the repo rules forbid changing (`docs/CONTRACTS.md`). `sTestFile1..10` is the
engine's own per-copy way to load a plugin (the keys are in `FalloutNV.exe` and in `Fallout_default.ini`; slot 1 holds `FalloutNV.esm` and is never
touched). With `--plugins-txt FILE` the plugin is also listed in that private file; the shared one is refused. `provision` rewrites `Fallout.ini`, so
install again after it. It refuses the stock game folder and anything that is not an instance copy.

Limits, because the game is never started here: the engine taking the esp from `STestFile2` (the keys exist, the behaviour was not run) and
`coc NVCoopLab` finding the renamed cell. If the first fails, list the plugin in a private plugins.txt with `--plugins-txt`; if the second
fails, use `coc GreenRoom` (`cell.editor_id_alt`).

## Files

- `esm.py`: reader and writer for the TES4 format (records, GRUPs, sub-records, compressed records, XXXX).
- `build_lab.py`: the generator. `NVLABESP_MUTANT` is test-only.
- `decor.py`: cell lighting, lights, partitions, camera hints (called from `build_lab.build_plugin`).
- `install_lab.py`: install and uninstall.
- `tests/test_lab_esp.py`, `tests/test_lab_decor.py`, `run_tests.py`: tests and mutation twins (registered in `tools/test_all.py`).
