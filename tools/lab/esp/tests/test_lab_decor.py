"""Tests for the lab cell's lighting, partitions and their clearance (tools/lab/esp/decor.py).

Run through tools/lab/esp/run_tests.py, which also runs the mutation twins (NVLABESP_MUTANT=<name>). Needs FalloutNV.esm (a read only
copy); the collision check also needs `Fallout - Meshes.bsa` next to it and skips without it.
"""
from __future__ import annotations

import math
import struct
import sys
import unittest
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
ESP = HERE.parent
sys.path.insert(0, str(ESP))
sys.path.insert(0, str(HERE))

import test_lab_esp as T   # noqa: E402  (helpers only: its test classes are not imported into this module)
import build_lab as bl     # noqa: E402
import decor               # noqa: E402
import esm                 # noqa: E402

NEEDS_ESM = T.NEEDS_ESM
MESHES_BSA = T.ESM_PATH.with_name("Fallout - Meshes.bsa")
CLEAR = 60.0                    # the least distance from a wall the tests accept (the generator's own clearance must be at least this)
PLACED = ("REFR", "ACHR", "ACRE")


# --- a minimal BSA (v104) and NIF block-type reader, only to prove the wall mesh has a collision body ------------------------
def nif_block_types(bsa_path: Path, model: str):
    path = ("meshes\\" + model).lower().replace("/", "\\")
    with open(bsa_path, "rb") as f:
        magic, ver, off, flags, nfold, nfile, tfn, tfl, _ff = struct.unpack("<4sIIIIIIII", f.read(36))
        assert magic == b"BSA\0" and ver == 104
        folders = [struct.unpack("<QII", f.read(16)) for _ in range(nfold)]
        recs = []
        for _h, cnt, _o in folders:
            name = f.read(f.read(1)[0])[:-1].decode("latin-1")
            recs.append((name, [struct.unpack("<QII", f.read(16)) for _ in range(cnt)]))
        names = f.read(tfl).split(b"\0")
        i, hit = 0, None
        for name, files in recs:
            for _h, size, o in files:
                if (name + "\\" + names[i].decode("latin-1")).lower() == path:
                    hit = (size, o)
                i += 1
        if hit is None:
            raise KeyError(path)
        size, o = hit
        comp = bool(flags & 4)
        if size & (1 << 30):
            comp, size = not comp, size & ~(1 << 30)
        f.seek(o)
        d = f.read(size)
    if flags & 0x100:
        d = d[1 + d[0]:]
    if comp:
        d = zlib.decompress(d[4:])
    i = d.index(b"\n") + 1
    _ver, _end, _uv, nblocks, _uv2 = struct.unpack_from("<IBIII", d, i)
    i += 17
    for _ in range(3):
        i += 1 + d[i]
    (ntypes,) = struct.unpack_from("<H", d, i)
    i += 2
    types = []
    for _ in range(ntypes):
        (n,) = struct.unpack_from("<I", d, i)
        types.append(d[i + 4:i + 4 + n].decode())
        i += 4 + n
    idx = struct.unpack_from("<%dH" % nblocks, d, i)
    return [types[k & 0x7FFF] for k in idx]


def wall_geometry(layout):
    w = layout["decor"]["wall_base"]
    return [(e, decor.wall_rect(e, w["half_t"], w["half_len"])) for e in layout["decor"]["walls"]]


@NEEDS_ESM
class Lighting(unittest.TestCase):
    def setUp(self):
        self.data, self.layout, self.plugin = T.built()
        self.recs = T.records(self.plugin)
        self.cell = [r for r in self.recs if r.type == "CELL"][0]

    def test_cell_lighting_is_bright_neutral_with_far_fog(self):
        x = decor.unpack_xcll(self.cell.get("XCLL"))
        for key in ("ambient", "directional", "fog_color"):
            r, g, b = x[key][:3]
            self.assertLess(max(r, g, b) - min(r, g, b), 12, key + " is not neutral")
        self.assertGreaterEqual(min(x["ambient"][:3]), 120)
        self.assertGreaterEqual(min(x["directional"][:3]), 80)
        self.assertGreaterEqual(x["fog_near"], 10000)                              # stock is 100: everything beyond 100 units fades to grey
        self.assertGreater(x["fog_far"], x["fog_near"])
        self.assertGreaterEqual(x["fog_clip"], 20000)                              # stock is 2500; the grid is 2300 across
        self.assertGreater(x["fog_far"], 3 * 2500)

    def test_only_the_lighting_subrecord_differs_from_stock(self):
        stock = T.master().cell(bl.CELL_FORM).record
        old = {s.type: s.data for s in stock.subs}
        new = {s.type: s.data for s in self.cell.subs}
        self.assertEqual(len(old["XCLL"]), 40)
        self.assertEqual(len(new["XCLL"]), 40)
        self.assertNotEqual(new["XCLL"], old["XCLL"])
        for k in ("EDID", "FULL", "XCLL"):
            new.pop(k, None), old.pop(k, None)
        self.assertEqual(new, old)                                                 # LTMP, LNAM, water, flags: stock

    def test_layout_records_the_lighting(self):
        self.assertEqual(self.layout["decor"]["lighting"]["fog_near"], decor.unpack_xcll(self.cell.get("XCLL"))["fog_near"])

    def test_every_room_has_lights_below_the_ceiling(self):
        m = T.master()
        d = self.layout["decor"]
        refs = T.refs_by_local(self.plugin)
        fz = self.layout["cell"]["floor_z"]
        per_room = {n: [] for n in self.layout["rooms"]}
        for light in d["lights"]:
            rec = refs[T.split_form(light["form"])[1]]
            self.assertEqual(rec.type, "REFR")
            self.assertTrue(rec.flags & bl.PERSISTENT)
            for got, want in zip(T.pos(rec), light["pos"]):
                self.assertAlmostEqual(got, want, places=2)
            base = struct.unpack("<I", rec.get("NAME"))[0]
            self.assertEqual(T.split_form(light["base"]), ("FalloutNV.esm", base))
            lrec = m.record(base, "LIGH")
            self.assertEqual(lrec.type, "LIGH")
            t, radius, _r, _g, _b, _a, flags = struct.unpack_from("<iI4BI", lrec.get("DATA"))
            self.assertGreaterEqual(radius, decor.MIN_LIGHT_RADIUS, lrec.edid())
            self.assertEqual(flags & decor.BAD_LIGHT_FLAGS, 0, "%s flickers, pulses, is a spot or starts off" % lrec.edid())
            self.assertEqual(t, -1, lrec.edid())                                    # no burn-out time
            self.assertIsNone(lrec.get("MODL"), lrec.edid())                        # invisible: the cell has its own fixtures
            ox, oy, _ = self.layout["rooms"][light["room"]]["origin"]
            x, y, z = T.pos(rec)
            self.assertLess(math.hypot(x - ox, y - oy), 300, light["form"])
            self.assertGreater(z, fz + 100)
            self.assertLess(z, fz + decor.CEILING - 30, "light is in the ceiling fixtures")
            per_room[light["room"]].append(radius)
        for name, radii in per_room.items():
            self.assertGreaterEqual(len(radii), 1, "room %s has no light" % name)
            self.assertGreaterEqual(max(radii), 512, "room %s has no wide light" % name)


@NEEDS_ESM
class Partitions(unittest.TestCase):
    def setUp(self):
        self.data, self.layout, self.plugin = T.built()
        self.refs = T.refs_by_local(self.plugin)
        self.d = self.layout["decor"]
        self.walls = wall_geometry(self.layout)
        self.rects = [r for _, r in self.walls]

    def room_points(self, name):
        room = self.layout["rooms"][name]
        pts = {}
        for key, form in list(room.get("refs", {}).items()) + list(room.get("markers", {}).items()) + [("stand", room["stand"])]:
            local = T.split_form(form)[1]
            if local in self.refs:                                                  # the quest form is not a placed ref
                pts[key] = T.pos(self.refs[local])[:2]
        return pts

    def room_paths(self, name):
        """Segments the lab runner walks or shoots along: stand to every target, the waypoint square, the patrol line."""
        pts = self.room_points(name)
        segs = [(pts["stand"], p) for k, p in pts.items() if k.startswith(("enemy", "target"))]
        wps = [pts["wp%d" % i] for i in (1, 2, 3, 4)] if "wp1" in pts else []
        segs += [(wps[i], wps[(i + 1) % 4]) for i in range(len(wps))] + ([(pts["stand"], wps[0])] if wps else [])
        if "patrol_a" in pts:
            segs.append((pts["patrol_a"], pts["patrol_b"]))
        return segs

    def test_wall_base_is_a_slab_with_collision(self):
        w = self.d["wall_base"]
        base = T.split_form(w["form"])[1]
        rec = T.master().record(base, "STAT")
        self.assertEqual(rec.type, "STAT")
        self.assertEqual(rec.edid(), w["editor_id"])
        x0, y0, z0, x1, y1, z1 = struct.unpack("<6h", rec.get("OBND"))
        self.assertLessEqual(max(-x0, x1), 40)                                       # thin
        self.assertGreaterEqual(max(-y0, y1), 100)                                   # long enough to be a partition
        self.assertGreaterEqual(z1 - z0, 250)                                        # reaches the 256 ceiling
        self.assertEqual((w["half_t"], w["half_len"]), (float(max(-x0, x1)), float(max(-y0, y1))))
        self.assertEqual(w["collision"], True)

    @unittest.skipUnless(MESHES_BSA.is_file(), "needs 'Fallout - Meshes.bsa' next to FalloutNV.esm")
    def test_wall_mesh_has_a_collision_body(self):
        rec = T.master().record(T.split_form(self.d["wall_base"]["form"])[1], "STAT")
        model = rec.get("MODL").split(b"\0")[0].decode("latin-1")
        types = nif_block_types(MESHES_BSA, model)
        self.assertIn("bhkRigidBody", types, model + " has no collision")
        self.assertIn("bhkCollisionObject", types, model)

    def test_walls_are_placed_refs_of_that_base_at_floor_level(self):
        fz = self.layout["cell"]["floor_z"]
        base = T.split_form(self.d["wall_base"]["form"])[1]
        self.assertGreaterEqual(len(self.walls), 8)
        mesh = bl.MultiNavmesh(T.master().cell(bl.CELL_FORM).navmeshes())
        x0, x1, y0, y1, _a, _b = mesh.bounds()
        for e, rect in self.walls:
            rec = self.refs[T.split_form(e["form"])[1]]
            self.assertEqual(struct.unpack("<I", rec.get("NAME"))[0], base)
            self.assertTrue(rec.flags & bl.PERSISTENT)
            for got, want in zip(T.pos(rec), e["pos"]):
                self.assertAlmostEqual(got, want, places=2)                           # the layout rounds to 3 places, the ref is float32
            self.assertAlmostEqual(T.pos(rec)[2], fz, places=2)
            rot = struct.unpack("<3f", rec.get("DATA")[12:24])
            self.assertAlmostEqual(rot[2], e["rot_z"], places=4)
            self.assertAlmostEqual(math.sin(rot[2]) ** 2, 1.0 if e["axis"] == "x" else 0.0, places=4)   # axis matches the rotation
            self.assertTrue(x0 < rect[0] and rect[2] < x1 and y0 < rect[1] and rect[3] < y1, e["form"])  # inside the navmesh rectangle

    def test_every_room_has_a_back_wall_and_two_side_partitions(self):
        hl = self.d["wall_base"]["half_len"]
        for name, room in self.layout["rooms"].items():
            ox, oy, _ = room["origin"]
            back = [r for e, r in self.walls if e["axis"] == "x" and oy + 250 < (r[1] + r[3]) / 2 < oy + 450]
            # the back pieces together cover the room's width
            covered = all(any(r[0] <= x <= r[2] for r in back) for x in range(int(ox) - 200, int(ox) + 201, 20))
            self.assertTrue(covered, "%s has no back wall" % name)
            for side in (-1, 1):
                sx = ox + side * decor.ROOM_HALF_W
                col = [r for e, r in self.walls if e["axis"] == "y" and abs((r[0] + r[2]) / 2 - sx) < 1]
                covered = all(any(r[1] <= y <= r[3] for r in col) for y in range(int(oy) - 300, int(oy) + 301, 20))
                self.assertTrue(covered, "%s has no partition on side %+d" % (name, side))
        self.assertGreater(hl, 100)

    def test_the_outer_boundary_is_the_stock_wall_ring(self):
        src = T.master().cell(bl.CELL_FORM)
        ring = [r for r in src.temporary if r.type == "REFR" and struct.unpack("<I", r.get("NAME"))[0] in (0x159E0, 0x159DD)]
        self.assertEqual(self.d["outer_boundary"]["pieces"], len(ring))
        self.assertGreaterEqual(len(ring), 30)
        xs = [T.pos(r)[0] for r in ring]
        ys = [T.pos(r)[1] for r in ring]
        for _e, rect in self.walls:                                                  # our partitions stay inside the ring
            self.assertTrue(min(xs) < rect[0] and rect[2] < max(xs) and min(ys) < rect[1] and rect[3] < max(ys))

    def test_stand_markers_and_refs_keep_clearance_from_walls(self):
        self.assertGreaterEqual(self.d["clearance"], CLEAR)
        for name in self.layout["rooms"]:
            pts = self.room_points(name)
            self.assertIn("stand", pts)
            for key, (x, y) in pts.items():
                for e, rect in self.walls:
                    dist = decor.dist_point_rect(x, y, rect)
                    self.assertGreaterEqual(dist, CLEAR, "%s.%s is %.0f from wall %s" % (name, key, dist, e["form"]))

    def test_walking_and_firing_lines_are_clear_of_walls(self):
        fight = self.room_paths("fight")
        self.assertEqual(len(fight), 3)                                              # stand to each of the 3 enemies
        for name in self.layout["rooms"]:
            for a, b in self.room_paths(name):
                for e, rect in self.walls:
                    dist = decor.dist_segment_rect(a, b, rect)
                    self.assertGreaterEqual(dist, CLEAR, "%s: %s to %s passes %.0f from wall %s" % (name, a, b, dist, e["form"]))
        s = self.room_points("fight")
        for key in ("enemy1", "enemy2", "enemy3"):                                   # about 350 of clear line of fire
            self.assertGreater(math.hypot(s[key][0] - s["stand"][0], s[key][1] - s["stand"][1]), 300)

    def test_the_console_arrival_marker_is_not_walled_in(self):
        src = T.master().cell(bl.CELL_FORM)
        coc = [r for r in src.temporary if r.type == "REFR" and struct.unpack("<I", r.get("NAME"))[0] == 0x32]
        self.assertEqual(len(coc), 1)                                                # COCMarkerHeading: `coc NVCoopLab` puts the player here
        x, y, _ = T.pos(coc[0])
        for e, rect in self.walls:
            self.assertGreaterEqual(decor.dist_point_rect(x, y, rect), 40, e["form"])

    def test_wall_pieces_do_not_cross_a_room_even_when_nothing_is_in_the_way(self):
        # no partition through the middle of a room: every wall footprint's centre line is outside the |dx| < 200 core of every room
        for name, room in self.layout["rooms"].items():
            ox, oy, _ = room["origin"]
            for e, rect in self.walls:
                x0, y0, x1, y1 = rect
                inside = x0 < ox + 200 and x1 > ox - 200 and y0 < oy + 250 and y1 > oy - 250
                self.assertFalse(inside, "%s cuts through room %s" % (e["form"], name))


@NEEDS_ESM
class DecorBookkeeping(unittest.TestCase):
    def setUp(self):
        self.data, self.layout, self.plugin = T.built()
        self.own = T.own_by_local(self.plugin)

    def test_ids_come_after_the_room_refs_and_the_header_covers_them(self):
        d = self.layout["decor"]
        decor_locals = [T.split_form(x["form"])[1] for x in d["lights"] + d["walls"]]
        room_locals = [T.split_form(f)[1] for r in self.layout["rooms"].values() for f in list(r.get("refs", {}).values()) + list(r.get("markers", {}).values()) + [r["stand"]]]
        self.assertEqual(len(decor_locals), len(set(decor_locals)))
        self.assertGreater(min(decor_locals), max(l for l in room_locals if l < 0x900))
        self.assertEqual(T.split_form(d["first_form"])[1], min(decor_locals))
        _v, _n, nxt = struct.unpack("<fII", self.plugin.header.get("HEDR"))
        self.assertGreater(nxt & 0xFFFFFF, max(decor_locals))
        for local in decor_locals:
            self.assertIn(local, self.own)

    def test_existing_layout_keys_are_unchanged(self):
        self.assertEqual(set(self.layout), {"plugin", "cell", "rooms", "decor"})
        self.assertEqual(list(self.layout["rooms"]), bl.ROOM_ORDER)
        for i, name in enumerate(bl.ROOM_ORDER):
            room = self.layout["rooms"][name]
            self.assertEqual(room["stand"], bl.own_form(bl.ROOM_BASE[name] + bl.STAND))
            self.assertEqual(set(room) - {"initially_disabled", "info", "markers", "refs"}, {"origin", "stand"})

    def test_no_stock_ref_is_disabled_or_overridden_but_the_npc(self):
        src = T.master().cell(bl.CELL_FORM)
        stock_ids = {r.form_id for r in src.temporary if r.type != "NAVM"}
        ours = {r.form_id for r in T.records(self.plugin) if r.form_id >> 24 == 0}
        self.assertEqual(ours & stock_ids, set())                                    # the floor, wall ring and marker are left alone
        bases = {struct.unpack("<I", r.get("NAME"))[0] for r in src.temporary if r.type == "REFR"}
        self.assertEqual(bases, {0x159DF, 0x159E0, 0x159DD, 0x32})                   # floor/ceiling tiles, wall pieces, corners, the coc marker
        self.assertEqual(self.layout["decor"]["disabled_stock_refs"], [])


if __name__ == "__main__":
    unittest.main(verbosity=1)
