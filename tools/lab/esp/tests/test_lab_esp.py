"""Tests for the NVCoopLab.esp generator (tools/lab/esp).

Run through tools/lab/esp/run_tests.py, which also runs every mutation twin (NVLABESP_MUTANT=<name>, one deliberate bug each).
Classes ending in Synthetic need no game files. The others read FalloutNV.esm (a copy, read only) and skip without it.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
ESP = HERE.parent
sys.path.insert(0, str(ESP))

import build_lab as bl   # noqa: E402
import decor             # noqa: E402
import esm               # noqa: E402
import install_lab as il  # noqa: E402
from esm import Group, Record, Sub  # noqa: E402

ESM_PATH = bl.default_esm()
HAVE_ESM = ESM_PATH.is_file()
NEEDS_ESM = unittest.skipUnless(HAVE_ESM, "needs FalloutNV.esm (FNV_ESM, or <games>/fnv-instances/0/Data)")

OWNER = b"mth" + b"wj"   # spelled in two parts so this file itself carries no trace of it
_cache = {}


def master():
    if "m" not in _cache:
        _cache["m"] = bl.Master(ESM_PATH)
    return _cache["m"]


def built(enabled=False):
    key = ("b", enabled)
    if key not in _cache:
        data, layout = bl.build_plugin(master(), enabled)
        _cache[key] = (data, layout, esm.read_plugin(data))
    return _cache[key]


def walk(items):
    for x in items:
        yield x
        if isinstance(x, Group):
            yield from walk(x.children)


def records(plugin):
    return [x for x in walk(plugin.top) if isinstance(x, Record)]


def split_form(s):
    name, local = s.rsplit(":", 1)
    return name, int(local, 16)


def refs_by_local(plugin):
    """local id -> record, for our own placed refs (file index 01)."""
    return {r.form_id & 0xFFFFFF: r for r in records(plugin) if r.type in ("REFR", "ACHR", "ACRE") and r.form_id >> 24 == 1}


def own_by_local(plugin):
    return {r.form_id & 0xFFFFFF: r for r in records(plugin) if r.form_id >> 24 == 1}


def pos(rec):
    return struct.unpack("<3f", rec.get("DATA")[:12])


# ---------------------------------------------------------------------------------------------------------------------
class FormatSynthetic(unittest.TestCase):
    def test_subrecord_roundtrip_and_xxxx(self):
        subs = [Sub("EDID", b"abc\0"), Sub("BIGG", bytes(range(256)) * 300), Sub("LAST", b"")]
        data = esm.encode_subs(subs)
        self.assertIn(b"XXXX", data)
        back = esm.parse_subs(data)
        self.assertEqual([(s.type, s.data) for s in back], [(s.type, s.data) for s in subs])

    def test_compressed_record_roundtrip(self):
        rec = Record("NPC_", esm.FLAG_COMPRESSED, 0x0100ABCD, 0, 15, 0, [Sub("EDID", b"x\0"), Sub("FGGS", b"\x07" * 200)])
        raw = esm.encode_record(rec)
        self.assertEqual(struct.unpack_from("<I", raw, 24)[0], len(esm.encode_subs(rec.subs)))
        back, end = esm.read_record_at(raw, 0)
        self.assertEqual(end, len(raw))
        self.assertEqual((back.flags, back.form_id), (esm.FLAG_COMPRESSED, 0x0100ABCD))
        self.assertEqual([(s.type, s.data) for s in back.subs], [(s.type, s.data) for s in rec.subs])
        self.assertEqual(zlib.decompress(raw[28:]), esm.encode_subs(rec.subs))

    def test_group_size_counts_its_header_and_nests(self):
        inner = Group(b"\x01\0\0\0", esm.G_CELL_PERSISTENT, children=[Record("REFR", 0x400, 1, 0, 15, 0, [Sub("DATA", bytes(24))])])
        top = Group(b"CELL", esm.G_TOP, children=[Group(b"\x08\0\0\0", esm.G_INT_BLOCK, children=[inner])])
        raw = esm.encode_group(top)
        self.assertEqual(struct.unpack_from("<I", raw, 4)[0], len(raw))
        self.assertEqual(struct.unpack_from("<I", raw, 24 + 4)[0], len(raw) - 24)
        tree = esm.read_tree(raw, 0, len(raw))
        self.assertEqual(esm.encode_group(tree[0]), raw)
        self.assertEqual(tree[0].children[0].children[0].children[0].type, "REFR")

    def test_truncated_group_is_refused(self):
        top = Group(b"CELL", esm.G_TOP, children=[Record("CELL", 0, 5, 0, 15, 0, [Sub("EDID", b"a\0")])])
        raw = bytearray(esm.encode_group(top))
        struct.pack_into("<I", raw, 4, len(raw) + 40)     # size larger than the data
        with self.assertRaises(Exception):
            esm.read_tree(bytes(raw), 0, len(raw))

    def test_block_labels(self):
        self.assertEqual(bl.block_labels(0x168FBC), (8, 8))
        self.assertEqual(bl.block_labels(0x0001EC51), (3, 3))   # 126033

    def test_navmesh_lookup(self):
        mesh = bl.Navmesh([(0, 0, 10), (100, 0, 10), (100, 100, 30), (0, 100, 30)], [(0, 1, 2), (0, 2, 3)])
        self.assertAlmostEqual(mesh.floor_z(50, 50), 20)
        self.assertIsNone(mesh.floor_z(150, 50))
        self.assertTrue(mesh.covers(50, 50, 40))
        self.assertFalse(mesh.covers(50, 50, 60))       # the ring leaves the mesh
        self.assertEqual(mesh.area(), 10000)


class InstallSynthetic(unittest.TestCase):
    INI = "[General]\r\nSStartingCell=\r\nSTestFile10=\r\nSTestFile9=\r\nSTestFile2=\r\nSTestFile1=FalloutNV.esm\r\n\r\n[Display]\r\niSize W=1280\r\n"

    def instance(self, tmp, ini=None):
        d = Path(tmp) / "inst"
        (d / "Data").mkdir(parents=True)
        (d / "Fallout.ini").write_bytes((ini or self.INI).encode("latin-1"))
        (d / "Data" / "FalloutNV.esm").write_bytes(b"x")
        esp = Path(tmp) / "NVCoopLab.esp"
        esp.write_bytes(b"TES4fake")
        return d, esp

    def test_install_and_uninstall(self):
        with tempfile.TemporaryDirectory() as tmp:
            d, esp = self.instance(tmp)
            r = il.install(d, esp)
            self.assertEqual((d / "Data" / "NVCoopLab.esp").read_bytes(), b"TES4fake")
            self.assertEqual(r["slot"], 2)
            text = (d / "Fallout.ini").read_bytes().decode("latin-1")
            self.assertIn("STestFile2=NVCoopLab.esp\r\n", text)
            self.assertIn("STestFile1=FalloutNV.esm\r\n", text)      # slot 1 untouched
            self.assertEqual(text.count("NVCoopLab.esp"), 1)
            self.assertNotIn("\n" + "STestFile2=NVCoopLab.esp\n", text)   # still CRLF
            il.install(d, esp)                                          # idempotent
            self.assertEqual((d / "Fallout.ini").read_bytes().decode("latin-1"), text)
            u = il.uninstall(d)
            self.assertEqual(u, {"esp_removed": True, "ini_cleared": True, "plugins_txt_cleared": False})
            self.assertFalse((d / "Data" / "NVCoopLab.esp").exists())
            self.assertEqual((d / "Fallout.ini").read_bytes().decode("latin-1"), self.INI)
            self.assertEqual(il.uninstall(d)["ini_cleared"], False)    # nothing left to remove

    def test_slot_is_added_when_none_exist_and_ini_without_trailing_newline(self):
        with tempfile.TemporaryDirectory() as tmp:
            d, esp = self.instance(tmp, "[General]\nSStartingCell=")
            il.install(d, esp)
            self.assertEqual((d / "Fallout.ini").read_text("latin-1"), "[General]\nSStartingCell=\nSTestFile2=NVCoopLab.esp\n")

    def test_refuses_game_folder_and_non_instances(self):
        with tempfile.TemporaryDirectory() as tmp:
            d, esp = self.instance(tmp)
            old = os.environ.get("FNV_GAME_DIR")
            os.environ["FNV_GAME_DIR"] = str(d)
            try:
                with self.assertRaises(il.InstallError):
                    il.install(d, esp)
                self.assertFalse((d / "Data" / "NVCoopLab.esp").exists())
            finally:
                if old is None:
                    del os.environ["FNV_GAME_DIR"]
                else:
                    os.environ["FNV_GAME_DIR"] = old
            with self.assertRaises(il.InstallError):
                il.install(Path(tmp) / "nope", esp)
            (Path(tmp) / "empty").mkdir()
            with self.assertRaises(il.InstallError):
                il.install(Path(tmp) / "empty", esp)

    def test_hardlinked_target_is_not_written_through(self):
        with tempfile.TemporaryDirectory() as tmp:
            d, esp = self.instance(tmp)
            orig = Path(tmp) / "orig.bin"
            orig.write_bytes(b"ORIGINAL")
            try:
                os.link(orig, d / "Data" / "NVCoopLab.esp")
            except OSError:
                self.skipTest("no hardlinks here")
            il.install(d, esp)
            self.assertEqual(orig.read_bytes(), b"ORIGINAL")

    def test_plugins_txt_is_opt_in_and_the_shared_one_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            d, esp = self.instance(tmp)
            pt = Path(tmp) / "plugins.txt"
            pt.write_bytes(b"FalloutNV.esm\r\n")
            il.install(d, esp)
            self.assertEqual(pt.read_bytes(), b"FalloutNV.esm\r\n")          # not asked: untouched
            il.install(d, esp, pt)
            self.assertEqual(pt.read_bytes(), b"FalloutNV.esm\r\nNVCoopLab.esp\r\n")
            il.install(d, esp, pt)
            self.assertEqual(pt.read_bytes().count(b"NVCoopLab.esp"), 1)
            self.assertTrue(il.uninstall(d, pt)["plugins_txt_cleared"])
            self.assertEqual(pt.read_bytes(), b"FalloutNV.esm\r\n")
            shared = tmp_shared = Path(tmp) / "Local" / "FalloutNV" / "plugins.txt"
            shared.parent.mkdir(parents=True)
            shared.write_bytes(b"FalloutNV.esm\r\n")
            old = os.environ.get("LOCALAPPDATA")
            os.environ["LOCALAPPDATA"] = str(Path(tmp) / "Local")
            try:
                with self.assertRaises(il.InstallError):
                    il.install(d, esp, tmp_shared)
            finally:
                if old is None:
                    del os.environ["LOCALAPPDATA"]
                else:
                    os.environ["LOCALAPPDATA"] = old
            self.assertEqual(shared.read_bytes(), b"FalloutNV.esm\r\n")


# ---------------------------------------------------------------------------------------------------------------------
@NEEDS_ESM
class MasterLayout(unittest.TestCase):
    """The reader against the real file: proof that the layout we write is the layout the game's own plugin has."""

    def test_header_and_green_room(self):
        m = master()
        self.assertEqual(m.header.type, "TES4")
        self.assertAlmostEqual(struct.unpack_from("<f", m.header.get("HEDR"))[0], 1.34, places=2)
        src = m.cell(bl.CELL_FORM)
        self.assertEqual(src.record.edid(), "GreenRoom")
        self.assertEqual(src.record.get("DATA"), b"\x01")                         # interior
        self.assertEqual([r.form_id for r in src.persistent], [0x168FBD])
        self.assertEqual(src.persistent[0].type, "ACHR")
        self.assertTrue(src.persistent[0].flags & bl.PERSISTENT)
        meshes = src.navmeshes()
        self.assertEqual(len(meshes), 1)
        mesh = meshes[0]
        self.assertEqual((len(mesh.verts), len(mesh.tris)), (62, 100))
        x0, x1, y0, y1, z0, z1 = mesh.bounds()
        self.assertEqual((z0, z1), (7040.0, 7040.0))
        self.assertAlmostEqual(mesh.area(), (x1 - x0) * (y1 - y0))               # the mesh covers its whole rectangle

    def test_every_interior_cell_sits_in_the_block_the_form_id_says(self):
        m = master()
        p, e = m.top("CELL")
        n = 0
        for _k, bp, be in esm.iter_children(m.buf, p + 24, e):
            blk = struct.unpack_from("<I", m.buf, bp + 8)[0]
            self.assertEqual(struct.unpack_from("<I", m.buf, bp + 12)[0], esm.G_INT_BLOCK)
            for _k2, sp, se in esm.iter_children(m.buf, bp + 24, be):
                sub = struct.unpack_from("<I", m.buf, sp + 8)[0]
                self.assertEqual(struct.unpack_from("<I", m.buf, sp + 12)[0], esm.G_INT_SUBBLOCK)
                pos_ = sp + 24
                while pos_ < se:
                    if bytes(m.buf[pos_:pos_ + 4]) == b"GRUP":
                        g, pos_ = esm.read_group_header(m.buf, pos_)
                        self.assertEqual(g.gtype, esm.G_CELL_CHILDREN)
                        continue
                    rec, pos_ = esm.read_record_at(m.buf, pos_, decode=False)
                    self.assertEqual(rec.type, "CELL")
                    self.assertEqual(bl.block_labels(rec.form_id), (blk, sub), "%08X" % rec.form_id)
                    n += 1
        self.assertGreater(n, 300)

    def test_children_groups_carry_the_cell_form_id_and_sizes_add_up(self):
        m = master()
        src = m.cell(bl.CELL_FORM)
        # re-read the stock group and check the label of every nested group against the cell
        start = [p for p in range(src.record.offset, src.record.offset + 4000) if m.buf[p:p + 4] == b"GRUP"][0]
        g, end = esm.read_group_header(m.buf, start)
        self.assertEqual((g.gtype, g.label), (esm.G_CELL_CHILDREN, struct.pack("<I", bl.CELL_FORM)))
        kinds = []
        for k, p, e in esm.iter_children(m.buf, start + 24, end):
            self.assertEqual(k, "GRUP")
            sub, _ = esm.read_group_header(m.buf, p)
            self.assertEqual(sub.label, struct.pack("<I", bl.CELL_FORM))
            kinds.append(sub.gtype)
        self.assertEqual(kinds, [esm.G_CELL_PERSISTENT, esm.G_CELL_TEMPORARY])

    def test_real_records_roundtrip(self):
        """decode -> encode of stock records: byte-identical when uncompressed, equal sub-records when compressed."""
        m = master()
        n_plain = n_comp = 0
        for label in ("CONT", "NPC_", "TERM", "PACK", "QUST", "SCPT", "CREA", "DOOR"):
            for fid, (kind, pos_) in list(m.index(label).items()):
                if label not in ("QUST", "SCPT") and fid & 0xF != 3:
                    continue
                rec, end = esm.read_record_at(m.buf, pos_)
                raw = bytes(m.buf[pos_:end])
                if rec.flags & esm.FLAG_COMPRESSED:
                    again, _ = esm.read_record_at(esm.encode_record(rec), 0)
                    self.assertEqual([(s.type, s.data) for s in again.subs], [(s.type, s.data) for s in rec.subs])
                    n_comp += 1
                else:
                    self.assertEqual(esm.encode_record(rec), raw, "%s %08X" % (label, fid))
                    n_plain += 1
        self.assertGreater(n_plain, 50)
        self.assertGreater(n_comp, 5)

    def test_green_room_group_roundtrips_byte_for_byte(self):
        m = master()
        src = m.cell(bl.CELL_FORM)
        start = [p for p in range(src.record.offset, src.record.offset + 4000) if m.buf[p:p + 4] == b"GRUP"][0]
        g, end = esm.read_group_header(m.buf, start)
        tree = esm.read_tree(m.buf, start, end)
        raw = b"".join(esm.encode_group(t) for t in tree)
        orig = bytes(m.buf[start:end])
        if any(r.flags & esm.FLAG_COMPRESSED for r in walk(tree) if isinstance(r, Record)):
            self.assertEqual(len(tree), 1)       # compressed records re-deflate differently: compare decoded content
            again = esm.read_tree(raw, 0, len(raw))
            self.assertEqual(len(list(walk(again))), len(list(walk(tree))))
        else:
            self.assertEqual(raw, orig)

    def test_stock_records_we_lean_on(self):
        m = master()
        for key in bl.BASES:
            m.base(key)
        gecko = m.base("enemy")
        self.assertGreaterEqual(gecko.get("AIDT")[0], 1)                         # aggressive
        self.assertLessEqual(struct.unpack_from("<H", gecko.get("DATA"), 4)[0], 120)   # weak
        term = m.base("terminal")
        self.assertIn(b"getLinkedRef", term.get("SCTX"))
        self.assertTrue(term.get("SCTX").endswith(b"unlock"))
        self.assertIsNone(term.get("PNAM"))                                      # no password: nothing to hack
        pack = m.base("patrol")
        self.assertEqual(pack.get("PKDT")[4], 13)                                # patrol
        self.assertEqual(struct.unpack_from("<I", pack.get("PLDT"))[0], 6)       # location: linked reference
        self.assertEqual(m.base("marker").type, "STAT")
        chest = m.base("chest_src")
        self.assertEqual(chest.get_all("CNTO"), [])


# ---------------------------------------------------------------------------------------------------------------------
@NEEDS_ESM
class GeneratedPlugin(unittest.TestCase):
    def setUp(self):
        self.data, self.layout, self.plugin = built()
        self.recs = records(self.plugin)
        self.own = own_by_local(self.plugin)
        self.refs = refs_by_local(self.plugin)

    # --- container structure
    def test_header(self):
        h = self.plugin.header
        self.assertEqual(self.plugin.masters, ["FalloutNV.esm"])
        ver, num, nxt = struct.unpack("<fII", h.get("HEDR"))
        self.assertAlmostEqual(ver, 1.34, places=2)
        self.assertEqual(num, sum(1 for _ in walk(self.plugin.top)))             # records and groups, not counting TES4
        self.assertGreater(nxt & 0xFFFFFF, max(self.own) )
        self.assertEqual(h.flags & 1, 0)                                          # an esp, not an esm
        self.assertEqual(h.get("CNAM"), b"cranebeaker\0")
        self.assertNotIn(OWNER, self.data.lower())

    def test_top_groups_in_game_order_and_cell_nesting(self):
        self.assertEqual([g.label.decode() for g in self.plugin.top], bl.TOP_ORDER)
        cell = self.plugin.top_group("CELL")
        blk, sub = bl.block_labels(bl.CELL_FORM)
        self.assertEqual([(g.gtype, g.label) for g in cell.children], [(esm.G_INT_BLOCK, struct.pack("<I", blk))])
        s = cell.children[0].children[0]
        self.assertEqual((s.gtype, s.label), (esm.G_INT_SUBBLOCK, struct.pack("<I", sub)))
        self.assertEqual([c.type if isinstance(c, Record) else c.gtype for c in s.children], ["CELL", esm.G_CELL_CHILDREN])
        kids = s.children[1]
        self.assertEqual(kids.label, struct.pack("<I", bl.CELL_FORM))
        self.assertEqual([(g.gtype, g.label) for g in kids.children], [(esm.G_CELL_PERSISTENT, struct.pack("<I", bl.CELL_FORM))])

    def test_cell_override_matches_stock_except_name(self):
        src = master().cell(bl.CELL_FORM)
        cell = [r for r in self.recs if r.type == "CELL"][0]
        self.assertEqual(cell.form_id, bl.CELL_FORM)                              # override of the stock form, master index 00
        self.assertEqual(cell.edid(), "NVCoopLab")
        stock = {s.type: s.data for s in src.record.subs}
        ours = {s.type: s.data for s in cell.subs}
        self.assertEqual(ours.pop("FULL"), b"NVCoop Lab\0")
        ours.pop("EDID"), stock.pop("EDID")
        ours.pop("XCLL"), stock.pop("XCLL")                                       # the lighting is ours (checked in test_lab_decor.py)
        self.assertEqual(ours, stock)                                             # flags, water, lighting template: all stock

    def test_stock_persistent_actor_is_disabled(self):
        old = [r for r in self.recs if r.form_id == 0x168FBD]
        self.assertEqual(len(old), 1)
        self.assertTrue(old[0].flags & bl.INIT_DISABLED)
        self.assertTrue(old[0].flags & bl.PERSISTENT)
        stock = master().cell(bl.CELL_FORM).persistent[0]
        self.assertEqual([(s.type, s.data) for s in old[0].subs], [(s.type, s.data) for s in stock.subs])

    def test_deterministic(self):
        again, layout = bl.build_plugin(master())
        self.assertEqual(again, self.data)
        self.assertEqual(layout, self.layout)

    # --- ids
    def test_own_ids_unique_in_range_and_all_refs_persistent(self):
        ids = [r.form_id for r in self.recs if r.form_id >> 24 == 1]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(i & 0xFFFFFF >= 0x800 for i in ids))
        self.assertGreater(len(self.refs), 25)
        for local, r in self.refs.items():
            self.assertTrue(r.flags & bl.PERSISTENT, "%06X" % local)
        # every NAME and link resolves: to our own record or a stock record of the type we think it is
        m = master()
        for r in self.refs.values():
            base = struct.unpack("<I", r.get("NAME"))[0]
            if base >> 24 == 1:
                self.assertIn(base & 0xFFFFFF, self.own)
            else:
                self.assertIn(base, {v[0] for v in bl.BASES.values()} | {v[0] for v in decor.DECOR_BASES.values()})
            for link in r.get_all("XLKR"):
                self.assertIn(struct.unpack("<I", link)[0] & 0xFFFFFF, self.own, "%08X links nowhere" % r.form_id)

    # --- layout.json
    def test_layout_schema(self):
        L = self.layout
        self.assertEqual(L["plugin"], "NVCoopLab.esp")
        self.assertEqual(L["cell"]["editor_id"], "NVCoopLab")
        self.assertEqual(split_form(L["cell"]["form"]), ("FalloutNV.esm", bl.CELL_FORM))
        self.assertEqual(list(L["rooms"]), ["spawn", "walk", "fight", "loot", "door", "npc", "vats", "quest"])
        self.assertEqual(json.loads(json.dumps(L)), L)
        for name, room in L["rooms"].items():
            self.assertEqual(len(room["origin"]), 3, name)
            self.assertIn("stand", room, name)
            self.assertTrue(room.get("refs") or room.get("markers"), name)
            for form in list(room.get("refs", {}).values()) + list(room.get("markers", {}).values()) + [room["stand"]]:
                plug, local = split_form(form)
                self.assertEqual(plug, "NVCoopLab.esp")
                self.assertIn(local, self.own, "%s %s is not in the plugin" % (name, form))
                self.assertRegex(form, r"^NVCoopLab\.esp:[0-9A-F]{6}$")
        self.assertEqual(set(L["rooms"]["spawn"]["markers"]), {"host", "client"})

    def test_stand_markers_are_heading_markers_in_their_room(self):
        marker = bl.BASES["marker"][0]
        for name, room in self.layout["rooms"].items():
            rec = self.refs[split_form(room["stand"])[1]]
            self.assertEqual(struct.unpack("<I", rec.get("NAME"))[0], marker, name)
            sx, sy, _ = pos(rec)
            ox, oy, oz = room["origin"]
            self.assertLess(math.hypot(sx - ox, sy - oy), 260, name)

    def test_everything_stands_on_navmesh_and_rooms_do_not_mix(self):
        mesh = bl.MultiNavmesh(master().cell(bl.CELL_FORM).navmeshes())
        origins = {n: r["origin"] for n, r in self.layout["rooms"].items()}
        for n, o in origins.items():
            for m, p in origins.items():
                if n < m:
                    self.assertGreater(math.hypot(o[0] - p[0], o[1] - p[1]), 440, (n, m))
        for name, room in self.layout["rooms"].items():
            forms = list(room.get("refs", {}).values()) + list(room.get("markers", {}).values()) + [room["stand"]]
            for form in forms:
                local = split_form(form)[1]
                if local not in self.refs:
                    continue                                                      # the quest form is not a placed ref
                x, y, z = pos(self.refs[local])
                self.assertTrue(mesh.covers(x, y, bl.MARGIN), "%s %s (%.0f,%.0f) is not %d units inside the navmesh" % (name, form, x, y, bl.MARGIN))
                self.assertLess(abs(z - mesh.floor_z(x, y)), 6, form)
                nearest = min(origins, key=lambda k: math.hypot(x - origins[k][0], y - origins[k][1]))
                self.assertEqual(nearest, name, "%s belongs to another room's area" % form)

    def test_spawn_and_walk(self):
        sp = self.layout["rooms"]["spawn"]
        hx, hy, _ = pos(self.refs[split_form(sp["markers"]["host"])[1]])
        cx, cy, _ = pos(self.refs[split_form(sp["markers"]["client"])[1]])
        self.assertGreater(math.hypot(hx - cx, hy - cy), 50)
        w = self.layout["rooms"]["walk"]
        pts = [pos(self.refs[split_form(w["refs"]["wp%d" % i])[1]]) for i in (1, 2, 3, 4)]
        self.assertEqual(len({(round(p[0]), round(p[1])) for p in pts}), 4)
        for a, b in zip(pts, pts[1:] + pts[:1]):
            self.assertGreater(math.hypot(a[0] - b[0], a[1] - b[1]), 250)         # real walking distance between waypoints

    def test_fight_and_vats_targets(self):
        gecko = bl.BASES["enemy"][0]
        for room, names, lo, hi in (("fight", ["enemy1", "enemy2", "enemy3"], 330, 380), ("vats", ["target1", "target2"], 300, 380)):
            r = self.layout["rooms"][room]
            sx, sy, _ = pos(self.refs[split_form(r["stand"])[1]])
            for n in names:
                rec = self.refs[split_form(r["refs"][n])[1]]
                self.assertEqual(rec.type, "ACRE")
                self.assertEqual(struct.unpack("<I", rec.get("NAME"))[0], gecko)
                x, y, _ = pos(rec)
                self.assertTrue(lo <= math.hypot(x - sx, y - sy) <= hi, (room, n, math.hypot(x - sx, y - sy)))
                self.assertEqual(bool(rec.flags & bl.INIT_DISABLED), n in r["initially_disabled"], (room, n))
            self.assertEqual(r["initially_disabled"], names)
        # nothing else in the plugin starts disabled except the stock actor
        disabled = {l for l, r in self.refs.items() if r.flags & bl.INIT_DISABLED}
        listed = {split_form(self.layout["rooms"][room]["refs"][n])[1] for room in ("fight", "vats")
                  for n in self.layout["rooms"][room]["initially_disabled"]}
        self.assertEqual(disabled, listed)

    def test_enemies_enabled_option(self):
        data, layout, plugin = built(True)
        refs = refs_by_local(plugin)
        self.assertFalse(any(r.flags & bl.INIT_DISABLED for r in refs.values()))
        self.assertNotIn("initially_disabled", layout["rooms"]["fight"])

    def test_loot(self):
        room = self.layout["rooms"]["loot"]
        chest = self.refs[split_form(room["refs"]["chest"])[1]]
        base = self.own[struct.unpack("<I", chest.get("NAME"))[0] & 0xFFFFFF]
        self.assertEqual(base.type, "CONT")
        have = [(struct.unpack_from("<I", c)[0], struct.unpack_from("<i", c, 4)[0]) for c in base.get_all("CNTO")]
        want = [(split_form(i["form"])[1], i["count"]) for i in room["info"]["container_items"]]
        self.assertEqual(have, want)
        self.assertGreaterEqual(len(have), 3)
        loose = [self.refs[split_form(room["refs"]["loose%d" % i])[1]] for i in (1, 2, 3, 4)]
        bases = [struct.unpack("<I", r.get("NAME"))[0] for r in loose]
        self.assertEqual(bases, [split_form(f)[1] for f in room["info"]["loose_items"]])
        for b in bases:
            self.assertEqual(master().index("ALCH")[b][0], "ALCH")
        self.assertEqual(len(set(bases)), 4)

    def test_door_and_terminal(self):
        room = self.layout["rooms"]["door"]
        door = self.refs[split_form(room["refs"]["door"])[1]]
        term = self.refs[split_form(room["refs"]["terminal"])[1]]
        xloc = door.get("XLOC")
        self.assertEqual(len(xloc), 20)
        self.assertEqual(xloc[0], room["info"]["lock_level"])
        self.assertGreater(xloc[0], 0)
        self.assertEqual(xloc[4:8], bytes(4))                                    # no key: the terminal is the way in
        self.assertIsNone(door.get("XTEL"))                                      # an in-cell door, not a teleport
        self.assertEqual(master().index("DOOR")[struct.unpack("<I", door.get("NAME"))[0]][0], "DOOR")
        link = term.get("XLKR")
        self.assertIsNotNone(link, "terminal is not linked to the door")
        self.assertEqual(struct.unpack("<I", link)[0], door.form_id)
        tbase = struct.unpack("<I", term.get("NAME"))[0]
        self.assertEqual(split_form(room["info"]["terminal_base"])[1], tbase)
        self.assertIn(b"getLinkedRef", master().record(tbase, "TERM").get("SCTX"))

    def test_npc_patrol(self):
        room = self.layout["rooms"]["npc"]
        a, b, npc = (self.refs[split_form(room["refs"][k])[1]] for k in ("patrol_a", "patrol_b", "npc"))
        self.assertEqual(struct.unpack("<I", a.get("XLKR"))[0], b.form_id)
        self.assertEqual(struct.unpack("<I", b.get("XLKR"))[0], a.form_id)       # a closed loop
        self.assertGreater(math.dist(pos(a)[:2], pos(b)[:2]), 250)
        self.assertEqual(struct.unpack("<f", a.get("XPRD"))[0], room["info"]["idle_seconds"])
        self.assertEqual(struct.unpack("<I", npc.get("XLKR"))[0], a.form_id)
        self.assertEqual(npc.type, "ACHR")
        base = self.own[struct.unpack("<I", npc.get("NAME"))[0] & 0xFFFFFF]
        self.assertEqual(base.type, "NPC_")
        self.assertEqual([struct.unpack("<I", p)[0] for p in base.get_all("PKID")], [split_form(room["info"]["package"])[1]])
        stock = master().base("npc_src")
        tpl_stock = struct.unpack_from("<H", stock.get("ACBS"), 22)[0]
        tpl_ours = struct.unpack_from("<H", base.get("ACBS"), 22)[0]
        self.assertEqual(tpl_ours, tpl_stock & ~0x20)                           # the template's AI packages no longer override ours
        self.assertEqual(base.get("AIDT")[0], 0)                                # friendly
        self.assertFalse(base.flags & esm.FLAG_COMPRESSED)

    def test_quest(self):
        room = self.layout["rooms"]["quest"]
        info = room["info"]
        q = self.own[split_form(info["quest"])[1]]
        self.assertEqual((q.type, q.edid()), ("QUST", "NVCoopLabQuest"))
        stages = [struct.unpack("<H", s.data)[0] for s in q.subs if s.type == "INDX"]
        self.assertEqual(stages, [10, 20, 30])
        self.assertEqual(stages, info["stages"])
        self.assertEqual(q.get("QSDT") is not None, True)
        script = self.own[struct.unpack("<I", q.get("SCRI"))[0] & 0xFFFFFF]
        self.assertEqual(script.type, "SCPT")
        self.assertEqual(split_form(info["script"])[1], script.form_id & 0xFFFFFF)
        self.assertEqual(script.get("SCVR"), info["var"].encode() + b"\0")
        _unused, refs, size, nvars, typ, _fl = struct.unpack("<IIIIHH", script.get("SCHR"))
        self.assertEqual((refs, nvars, typ), (0, 1, 1))                          # quest script, one variable
        self.assertEqual(size, len(script.get("SCDA")))
        self.assertEqual(room["refs"]["quest"], info["quest"])

    def test_cli_writes_both_files_and_leaves_the_master_alone(self):
        before = (ESM_PATH.stat().st_size, ESM_PATH.stat().st_mtime_ns)
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(bl.main(["--esm", str(ESM_PATH), "--out", tmp]), 0)
            self.assertEqual((Path(tmp) / "NVCoopLab.esp").read_bytes(), self.data)
            self.assertEqual(json.loads((Path(tmp) / "layout.json").read_text("utf-8")), self.layout)
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()), ["NVCoopLab.esp", "layout.json"])
        self.assertEqual((ESM_PATH.stat().st_size, ESM_PATH.stat().st_mtime_ns), before)

    def test_no_owner_identity_in_outputs(self):
        text = json.dumps(self.layout)
        for blob in (self.data.lower(), text.lower().encode()):
            self.assertNotIn(OWNER, blob)
            self.assertNotIn(b"c:" + b"\\" + b"users", blob)

    def test_missing_esm_exits_2(self):
        self.assertEqual(bl.main(["--esm", str(Path(tempfile.gettempdir()) / "no_such_dir" / "FalloutNV.esm"), "--out", tempfile.gettempdir()]), 2)


if __name__ == "__main__":
    unittest.main(verbosity=1)
