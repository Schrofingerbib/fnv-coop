#!/usr/bin/env python3
"""Generate NVCoopLab.esp (the co-op lab test plugin) and layout.json, in pure Python.

    python -I tools/lab/esp/build_lab.py --esm <FalloutNV.esm> --out out/lab

The plugin has no cell of its own: it overrides the stock, navmeshed, quiet interior GreenRoom (FalloutNV.esm 168FBC) and
renames it NVCoopLab, so actors can path (see README.md for why). All placed content is persistent, so both copies see the
same refs with the same ids. Form ids of our own records start at 0x000800 (file index 01 because FalloutNV.esm is the one
master). docs/LAB.md is the contract for layout.json.

NVLABESP_MUTANT=<name> is test-only: one deliberate bug per name (tools/lab/esp/run_tests.py proves each is caught).
"""
from __future__ import annotations

import argparse
import json
import math
import mmap
import os
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import esm  # noqa: E402
import decor  # noqa: E402  (lights, partitions, cell lighting: new functions live there)
from esm import Group, Plugin, Record, Sub  # noqa: E402

MUTANT = os.environ.get("NVLABESP_MUTANT", "")   # test-only: one deliberate bug per name

PLUGIN = "NVCoopLab.esp"
MASTER = "FalloutNV.esm"
AUTHOR = "cranebeaker"
CELL_FORM = 0x00168FBC          # GreenRoom (EDID in FalloutNV.esm); the cell we override
CELL_EDID_SRC = "GreenRoom"
CELL_EDID = "NVCoopLab"
OWN = 0x01000000                # file index of our own records (one master)
MARGIN = 96.0                   # every placed point must have navmesh all around it, this far out
EDGE = 200.0                    # room grid keeps this far from the navmesh border

# --- own record ids (local, shown as NVCoopLab.esp:XXXXXX) -------------------------------------------------------------
L_SCRIPT, L_QUEST, L_CHEST, L_CIVIL = 0x801, 0x802, 0x803, 0x804
ROOM_ORDER = ["spawn", "walk", "fight", "loot", "door", "npc", "vats", "quest"]
ROOM_BASE = {name: 0x810 + 0x10 * i for i, name in enumerate(ROOM_ORDER)}   # ref block per room; stand marker = base + 0x0F
STAND = 0x0F

# --- stock records we use (id, record type, editor id): checked against the master, so a different game build fails loudly
BASES = {
    "marker": (0x00000034, "STAT", "XMarkerHeading"),
    "enemy": (0x0010CD73, "CREA", "NVCrGecko"),
    "stimpak": (0x00015169, "ALCH", "Stimpak"),
    "radaway": (0x00015167, "ALCH", "RadAway"),
    "buffout": (0x00015163, "ALCH", "Buffout"),
    "nuka": (0x0001519E, "ALCH", "NukaCola"),
    "beer": (0x00015197, "ALCH", "Beer"),
    "caps": (0x0000000F, "MISC", "Caps001"),
    "ammo10": (0x00004241, "AMMO", "Ammo10mm"),
    "door": (0x000159A6, "DOOR", "OffDoorSmL01"),
    "terminal": (0x000EAC00, "TERM", "DefaultUnlockTermWALL"),   # DNAM flag 0x02 = unlocked: the DESK0VeryEasy one opens the HACKING menu (flags 0), which blocks the terminal menu
    "chest_src": (0x0009874E, "CONT", "Footlocker01Empty"),
    "npc_src": (0x00162B27, "NPC_", "NVProspectorMaleA"),
    "patrol": (0x0017153E, "PACK", "DefaultPatrolCasualLinkedRef"),
}
CHEST_ITEMS = [("stimpak", 5), ("radaway", 3), ("caps", 250), ("ammo10", 40), ("buffout", 2)]
LOOSE_ITEMS = ["stimpak", "nuka", "beer", "radaway"]
DOOR_LOCK_LEVEL = 25            # XLOC lock level "Easy"; the linked terminal unlocks it with no script of ours
STAGES = [(10, "Lab stage 10"), (20, "Lab stage 20"), (30, "Lab stage 30")]
QUEST_VAR = "labvar"
IDLE_SECONDS = 5.0
INIT_DISABLED = 0x00000800
PERSISTENT = 0x00000400
TOP_ORDER = ["SCPT", "CONT", "NPC_", "CELL", "QUST"]   # the order these groups have in FalloutNV.esm


def master_form(fid: int) -> str:
    return "%s:%06X" % (MASTER, fid)


def own_form(local: int) -> str:
    return "%s:%06X" % (PLUGIN, local)


# --- reading the master ------------------------------------------------------------------------------------------------

class Master:
    def __init__(self, path):
        self.path = str(path)
        self._f = open(path, "rb")
        self.buf = mmap.mmap(self._f.fileno(), 0, access=mmap.ACCESS_READ)
        hdr, self.first = esm.read_record_at(self.buf, 0)
        if hdr.type != "TES4":
            raise ValueError("%s is not a plugin" % path)
        self.header = hdr
        self._tops = {}
        for kind, p, e in esm.iter_children(self.buf, self.first, len(self.buf)):
            if kind != "GRUP":
                raise ValueError("top level record %s outside a group" % kind)
            self._tops[bytes(self.buf[p + 8:p + 12]).decode("latin-1")] = (p, e)
        self._index: Dict[int, Tuple[str, int]] = {}

    def close(self):
        self.buf.close()
        self._f.close()

    def top(self, label: str) -> Tuple[int, int]:
        return self._tops[label]

    def index(self, label: str) -> Dict[int, Tuple[str, int]]:
        """form id -> (type, offset) of every plain record in one top group (no body decoding)."""
        if label not in self._index:
            p, e = self._tops[label]
            for kind, rp, re_ in esm.iter_children(self.buf, p + 24, e):
                if kind != "GRUP":
                    fid = struct.unpack_from("<I", self.buf, rp + 12)[0]
                    self._index[fid] = (kind, rp)
        return self._index

    def record(self, fid: int, label: str) -> Record:
        self.index(label)
        kind, pos = self._index[fid]
        return esm.read_record_at(self.buf, pos)[0]

    def base(self, key: str) -> Record:
        """A stock record from BASES, checked for the right type and editor id."""
        fid, typ, edid = BASES[key]
        try:
            rec = self.record(fid, typ)
        except KeyError:
            raise ValueError("%s %08X (%s) not in %s" % (typ, fid, edid, self.path))
        if rec.type != typ or rec.edid() != edid:
            raise ValueError("%08X is %s %r, expected %s %r" % (fid, rec.type, rec.edid(), typ, edid))
        return rec

    def cell(self, fid: int) -> "CellSource":
        """Find an interior CELL by form id: its block and sub-block labels, record and child records."""
        p, e = self._tops["CELL"]
        for kind, bp, be in esm.iter_children(self.buf, p + 24, e):
            for kind2, sp, se in esm.iter_children(self.buf, bp + 24, be):
                pos = sp + 24
                while pos < se:
                    k = bytes(self.buf[pos:pos + 4])
                    if k == b"GRUP":
                        _, pos = esm.read_group_header(self.buf, pos)
                        continue
                    rec, nxt = esm.read_record_at(self.buf, pos)
                    if rec.form_id != fid:
                        pos = nxt
                        continue
                    src = CellSource(rec, struct.unpack_from("<I", self.buf, bp + 8)[0], struct.unpack_from("<I", self.buf, sp + 8)[0])
                    if nxt < se and bytes(self.buf[nxt:nxt + 4]) == b"GRUP":
                        g, gend = esm.read_group_header(self.buf, nxt)
                        if g.gtype == esm.G_CELL_CHILDREN:
                            for sub in esm.read_tree(self.buf, nxt + 24, gend):
                                if sub.gtype == esm.G_CELL_PERSISTENT:
                                    src.persistent = sub.children
                                elif sub.gtype == esm.G_CELL_TEMPORARY:
                                    src.temporary = sub.children
                    return src
        raise KeyError("cell %08X not found" % fid)


@dataclass
class CellSource:
    record: Record
    block: int
    subblock: int
    persistent: list = field(default_factory=list)
    temporary: list = field(default_factory=list)

    def navmeshes(self) -> List["Navmesh"]:
        return [Navmesh.from_record(r) for r in self.temporary if r.type == "NAVM"]


def block_labels(fid: int) -> Tuple[int, int]:
    """Interior cells sit in block (last decimal digit of the form id), sub-block (the digit before it)."""
    return fid % 10, (fid // 10) % 10


# --- navmesh -----------------------------------------------------------------------------------------------------------

class Navmesh:
    def __init__(self, verts, tris):
        self.verts = verts          # [(x, y, z)]
        self.tris = tris            # [(a, b, c)]

    @classmethod
    def from_record(cls, rec: Record) -> "Navmesh":
        d = rec.get("DATA")
        _cell, nv, nt = struct.unpack_from("<III", d, 0)
        vv, tr = rec.get("NVVX"), rec.get("NVTR")
        if len(vv) != 12 * nv or len(tr) != 16 * nt:
            raise ValueError("NAVM %08X vertex or triangle data does not match its counts" % rec.form_id)
        verts = [struct.unpack_from("<3f", vv, 12 * i) for i in range(nv)]
        tris = [struct.unpack_from("<3H", tr, 16 * i) for i in range(nt)]
        return cls(verts, tris)

    def bounds(self):
        xs = [v[0] for v in self.verts]
        ys = [v[1] for v in self.verts]
        zs = [v[2] for v in self.verts]
        return min(xs), max(xs), min(ys), max(ys), min(zs), max(zs)

    def area(self) -> float:
        a = 0.0
        for i, j, k in self.tris:
            p, q, r = self.verts[i], self.verts[j], self.verts[k]
            a += abs((q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])) / 2
        return a

    def floor_z(self, x: float, y: float) -> Optional[float]:
        """Height of the navmesh at (x, y), or None when no triangle covers the point."""
        for i, j, k in self.tris:
            p, q, r = self.verts[i], self.verts[j], self.verts[k]
            den = (q[1] - r[1]) * (p[0] - r[0]) + (r[0] - q[0]) * (p[1] - r[1])
            if den == 0:
                continue
            a = ((q[1] - r[1]) * (x - r[0]) + (r[0] - q[0]) * (y - r[1])) / den
            b = ((r[1] - p[1]) * (x - r[0]) + (p[0] - r[0]) * (y - r[1])) / den
            c = 1 - a - b
            if a >= -1e-9 and b >= -1e-9 and c >= -1e-9:
                return a * p[2] + b * q[2] + c * r[2]
        return None

    def covers(self, x: float, y: float, margin: float = 0.0) -> bool:
        """Navmesh under the point and, when margin > 0, under a ring of 16 points around it."""
        if self.floor_z(x, y) is None:
            return False
        if margin:
            for n in range(16):
                a = 2 * math.pi * n / 16
                if self.floor_z(x + margin * math.cos(a), y + margin * math.sin(a)) is None:
                    return False
        return True


class MultiNavmesh(Navmesh):
    def __init__(self, meshes: List[Navmesh]):
        self.meshes = meshes
        super().__init__([v for m in meshes for v in m.verts], [])

    def floor_z(self, x, y):
        for m in self.meshes:
            z = m.floor_z(x, y)
            if z is not None:
                return z
        return None

    def area(self):
        return sum(m.area() for m in self.meshes)


# --- record helpers ----------------------------------------------------------------------------------------------------

def u32(v: int) -> bytes:
    return struct.pack("<I", v)


def zstr(s: str) -> bytes:
    return s.encode("latin-1") + b"\0"


def replace_sub(subs: List[Sub], typ: str, data: bytes) -> None:
    for s in subs:
        if s.type == typ:
            s.data = data
            return
    raise KeyError(typ)


def ref_data(pos, rot=(0.0, 0.0, 0.0)) -> bytes:
    return struct.pack("<6f", *pos, *rot)


def make_ref(rtype: str, local: int, base_form: int, pos, edid: str, extras: Optional[List[Sub]] = None, flags: int = PERSISTENT,
             rot=(0.0, 0.0, 0.0)) -> Record:
    """A placed reference in our plugin. Sub-record order follows the stock refs: EDID, NAME, extras, DATA."""
    subs = [Sub("EDID", zstr(edid)), Sub("NAME", u32(base_form))] + list(extras or []) + [Sub("DATA", ref_data(pos, rot))]
    return Record(rtype, flags, OWN | local, 0, 15, 0, subs)


def patrol_marker_extras(next_local: int) -> List[Sub]:
    """Stock patrol marker extras (same set as the vanilla XMarkerHeading patrol chains): idle time, XPPA, empty script, next link."""
    return [Sub("XPRD", struct.pack("<f", IDLE_SECONDS)), Sub("XPPA", b""), Sub("INAM", u32(0)),
            Sub("SCHR", bytes(16) + b"\x00\x00\x01\x00"), Sub("TNAM", u32(0)), Sub("XLKR", u32(OWN | next_local))]


# --- the lab content ---------------------------------------------------------------------------------------------------

@dataclass
class Room:
    name: str
    origin: Tuple[float, float, float]
    records: List[Record] = field(default_factory=list)
    stand_local: int = 0
    refs: Dict[str, int] = field(default_factory=dict)       # layout name -> local id
    markers: Dict[str, int] = field(default_factory=dict)
    info: dict = field(default_factory=dict)
    disabled: List[str] = field(default_factory=list)


def grid_origins(mesh: Navmesh) -> Dict[str, Tuple[float, float, float]]:
    """4 x 2 grid of room origins inside the navmesh bounds, rounded to 16 units."""
    x0, x1, y0, y1, _z0, _z1 = mesh.bounds()
    ux0, ux1, uy0, uy1 = x0 + EDGE, x1 - EDGE, y0 + EDGE, y1 - EDGE
    cw, ch = (ux1 - ux0) / 4, (uy1 - uy0) / 2
    out = {}
    for i, name in enumerate(ROOM_ORDER):
        col, row = i % 4, i // 4
        x = round((ux0 + cw * (col + 0.5)) / 16) * 16
        y = round((uy0 + ch * (row + 0.5)) / 16) * 16
        out[name] = (float(x), float(y), 0.0)
    return out


def build_rooms(mesh: Navmesh, enemies_enabled: bool = False) -> Dict[str, Room]:
    origins = grid_origins(mesh)
    rooms: Dict[str, Room] = {}
    marker_base = BASES["marker"][0]

    def zfloor(x, y):
        z = mesh.floor_z(x, y)
        if z is None:
            raise ValueError("no navmesh under (%.0f, %.0f)" % (x, y))
        return z

    def put(room: Room, idx: int, rtype: str, key: str, base: int, dx: float, dy: float, edid: str, extras=None, flags=PERSISTENT,
            lift: float = 0.0, rot=(0.0, 0.0, 0.0), is_marker: bool = False) -> int:
        ox, oy, _ = room.origin
        x, y = ox + dx, oy + dy
        local = ROOM_BASE[room.name] + idx
        room.records.append(make_ref(rtype, local, base, (x, y, zfloor(x, y) + lift), edid, extras, flags, rot))
        (room.markers if is_marker else room.refs)[key] = local
        return local

    def stand(room: Room, dy: float, label: str):
        ox, oy, _ = room.origin
        local = ROOM_BASE[room.name] + STAND
        x, y = ox, oy + dy
        room.records.append(make_ref("REFR", local, marker_base, (x, y, zfloor(x, y)), "NVCoopLab%sStand" % label))
        room.stand_local = local

    for name in ROOM_ORDER:
        ox, oy, _ = origins[name]
        rooms[name] = Room(name, (ox, oy, zfloor(ox, oy)))

    # spawn: two heading markers side by side
    r = rooms["spawn"]
    put(r, 0, "REFR", "host", marker_base, -60, 0, "NVCoopLabHost", is_marker=True)
    put(r, 1, "REFR", "client", marker_base, 60, 0, "NVCoopLabClient", is_marker=True)
    stand(r, mesh.bounds()[2] + 10 - r.origin[1] if MUTANT == "edge_margin" else -100, "Spawn")

    # walk: four waypoints on a 300 x 300 square, stand just before waypoint 1
    r = rooms["walk"]
    for n, (dx, dy) in enumerate([(-150, -150), (150, -150), (150, 150), (-150, 150)]):
        put(r, 1 + n, "REFR", "wp%d" % (n + 1), marker_base, dx, dy, "NVCoopLabWalk%d" % (n + 1))
    stand(r, -210, "Walk")

    # fight: three weak hostile creatures, stand 350 units from the middle one
    r = rooms["fight"]
    flags = PERSISTENT if enemies_enabled or MUTANT == "disabled_flag_lost" else PERSISTENT | INIT_DISABLED
    for n, (dx, dy) in enumerate([(-100, 160), (0, 170), (100, 160)]):
        put(r, 1 + n, "ACRE", "enemy%d" % (n + 1), BASES["enemy"][0], dx, dy, "NVCoopLabEnemy%d" % (n + 1), flags=flags, lift=2.0)
        if not enemies_enabled:
            r.disabled.append("enemy%d" % (n + 1))
    stand(r, -300 if MUTANT == "stand_far" else -180, "Fight")

    # loot: one chest (known items), four loose items, all on the floor
    r = rooms["loot"]
    put(r, 1, "REFR", "chest", OWN | L_CHEST, 0, 60, "NVCoopLabChest", lift=0.0)
    for n, (key, (dx, dy)) in enumerate(zip(LOOSE_ITEMS, [(-110, -20), (-40, -50), (40, -50), (110, -20)])):
        put(r, 2 + n, "REFR", "loose%d" % (n + 1), BASES[key][0], dx, dy, "NVCoopLabLoose%d" % (n + 1), lift=4.0)
    stand(r, -170, "Loot")

    # door: a lockable door (lock level 25) and the stock unlock terminal linked to it
    r = rooms["door"]
    door_local = ROOM_BASE["door"] + 1
    xloc = bytes([0 if MUTANT == "lock_level_zero" else DOOR_LOCK_LEVEL]) + bytes(19)
    put(r, 1, "REFR", "door", BASES["door"][0], 0, 40, "NVCoopLabDoor", extras=[Sub("XLOC", xloc)])
    term_extras = [Sub("XLKR", u32(OWN | door_local))]
    if MUTANT == "terminal_unlinked":
        term_extras = []
    put(r, 2, "REFR", "terminal", BASES["terminal"][0], 140, -20, "NVCoopLabTerminal", extras=term_extras)
    stand(r, -200, "Door")

    # npc: a friendly civilian patrolling between two markers (stock linked-ref patrol package)
    r = rooms["npc"]
    pa, pb, npc = ROOM_BASE["npc"] + 1, ROOM_BASE["npc"] + 2, ROOM_BASE["npc"] + 3
    put(r, 1, "REFR", "patrol_a", marker_base, -150, 0, "NVCoopLabPatrolA", extras=patrol_marker_extras(pb), is_marker=False)
    put(r, 2, "REFR", "patrol_b", marker_base, 150, 0, "NVCoopLabPatrolB", extras=patrol_marker_extras(pa if MUTANT != "patrol_open" else 0x7FF))
    put(r, 3, "ACHR", "npc", OWN | L_CIVIL, -150, 0, "NVCoopLabCivilian", extras=[Sub("XLKR", u32(OWN | pa))])
    stand(r, -220, "Npc")

    # vats: two hostile targets 350 away from the stand marker
    r = rooms["vats"]
    for n, dx in enumerate([-90, 90]):
        put(r, 1 + n, "ACRE", "target%d" % (n + 1), BASES["enemy"][0], dx, 220, "NVCoopLabVatsTarget%d" % (n + 1), flags=flags, lift=2.0)
        if not enemies_enabled:
            r.disabled.append("target%d" % (n + 1))
    stand(r, -130, "Vats")

    # quest: an anchor marker; the quest itself has no placed form
    r = rooms["quest"]
    put(r, 1, "REFR", "anchor", marker_base, 0, 0, "NVCoopLabQuestAnchor")
    stand(r, -100, "Quest")
    r.refs["quest"] = L_QUEST

    # room info for the runner (all derived from the data above)
    rooms["fight"].info = {"enemy_base": master_form(BASES["enemy"][0]), "start": "enabled" if enemies_enabled else "initially disabled"}
    rooms["vats"].info = dict(rooms["fight"].info)
    rooms["loot"].info = {
        "container_items": [{"form": master_form(BASES[k][0]), "count": n} for k, n in CHEST_ITEMS],
        "loose_items": [master_form(BASES[k][0]) for k in LOOSE_ITEMS],
    }
    rooms["door"].info = {"lock_level": DOOR_LOCK_LEVEL, "terminal_base": master_form(BASES["terminal"][0]),
                          "unlock": "terminal menu item Disengage Lock runs getLinkedRef.unlock on its linked ref (the door)"}
    rooms["npc"].info = {"npc_base": own_form(L_CIVIL), "package": master_form(BASES["patrol"][0]), "idle_seconds": IDLE_SECONDS,
                         "path": ["patrol_a", "patrol_b"]}
    rooms["quest"].info = {"quest": own_form(L_QUEST), "editor_id": "NVCoopLabQuest", "stages": [s for s, _ in STAGES], "var": QUEST_VAR,
                           "script": own_form(L_SCRIPT)}
    return rooms


def build_base_records(master: Master) -> Dict[str, List[Record]]:
    """Our own base records: quest script, quest, chest, civilian. Keyed by top group label."""
    # quest script: a stock-style script that only declares one short (compiled body is just the script-name opcode)
    sctx = b"scn NVCoopLabQuestScript\r\n\r\nshort " + QUEST_VAR.encode()
    script = Record("SCPT", 0, OWN | L_SCRIPT, 0, 15, 0, [
        Sub("EDID", zstr("NVCoopLabQuestScript")),
        Sub("SCHR", struct.pack("<IIIIHH", 0, 0, 4, 1, 1, 1)),
        Sub("SCDA", bytes([0x1D, 0, 0, 0])),
        Sub("SCTX", sctx),
        Sub("SLSD", struct.pack("<I12sB7s", 1, bytes(12), 1, b"\xcd" * 7)),
        Sub("SCVR", zstr(QUEST_VAR)),
    ])
    stages = [s for s, _ in STAGES if not (MUTANT == "quest_stage_missing" and s == 30)]
    qsubs = [Sub("EDID", zstr("NVCoopLabQuest")), Sub("SCRI", u32(OWN | L_SCRIPT)), Sub("FULL", zstr("NVCoop Lab Quest")),
             Sub("DATA", struct.pack("<BBHf", 0x15, 0x32, 0, 0.0))]
    for stage, text in STAGES:
        if stage not in stages:
            continue
        qsubs += [Sub("INDX", struct.pack("<H", stage)), Sub("QSDT", b"\x00"), Sub("CNAM", zstr(text)), Sub("SCHR", bytes(16) + b"\x00\x00\x01\x00")]
    quest = Record("QUST", 0, OWN | L_QUEST, 0, 15, 0, qsubs)

    # chest: the stock empty footlocker with our known items added before DATA
    src = master.base("chest_src")
    subs = [Sub(s.type, s.data) for s in src.subs]
    replace_sub(subs, "EDID", zstr("NVCoopLabChest"))
    replace_sub(subs, "FULL", zstr("Lab Chest"))
    at = [s.type for s in subs].index("DATA")
    items = [Sub("CNTO", struct.pack("<Ii", BASES[k][0], n)) for k, n in CHEST_ITEMS]
    if MUTANT == "chest_count":
        items[0] = Sub("CNTO", struct.pack("<Ii", BASES["stimpak"][0], 6))
    chest = Record("CONT", 0, OWN | L_CHEST, 0, src.version, 0, subs[:at] + items + subs[at:])

    # civilian: the stock prospector with its own package list (the template's AI packages bit cleared, the linked-ref patrol first)
    src = master.base("npc_src")
    subs = [Sub(s.type, s.data) for s in src.subs]
    replace_sub(subs, "EDID", zstr("NVCoopLabCivilian"))
    replace_sub(subs, "FULL", zstr("Lab Civilian"))
    acbs = bytearray(dict((s.type, s.data) for s in subs)["ACBS"])
    tpl = struct.unpack_from("<H", acbs, 22)[0]
    struct.pack_into("<H", acbs, 22, tpl if MUTANT == "npc_pkg_inherited" else tpl & ~0x20)
    replace_sub(subs, "ACBS", bytes(acbs))
    first = [s.type for s in subs].index("PKID")
    subs = [s for s in subs if s.type != "PKID"]
    subs.insert(first, Sub("PKID", u32(BASES["patrol"][0])))
    civil = Record("NPC_", 0, OWN | L_CIVIL, 0, src.version, 0, subs)
    return {"SCPT": [script], "CONT": [chest], "NPC_": [civil], "QUST": [quest]}


def build_cell_group(src: CellSource, rooms: Dict[str, Room], decor_refs: Optional[List[Record]] = None) -> Group:
    """CELL top group holding the override of GreenRoom: block -> sub-block -> CELL record -> children (persistent)."""
    rec = Record("CELL", src.record.flags & ~esm.FLAG_COMPRESSED, src.record.form_id, 0, src.record.version, 0,
                 [Sub(s.type, s.data) for s in src.record.subs])
    replace_sub(rec.subs, "EDID", zstr(CELL_EDID_SRC if MUTANT == "cell_edid_kept" else CELL_EDID))
    rec.subs.insert(1, Sub("FULL", zstr("NVCoop Lab")))
    replace_sub(rec.subs, "XCLL", decor.new_xcll(dict((s.type, s.data) for s in src.record.subs)["XCLL"]))    # bright neutral light, far fog
    # the stock persistent actor of the room is disabled, so the only actors are ours
    pers: list = []
    for r in src.persistent:
        o = Record(r.type, (r.flags & ~esm.FLAG_COMPRESSED) | (0 if MUTANT == "persistent_actor_enabled" else INIT_DISABLED), r.form_id, 0, r.version, 0, [Sub(s.type, s.data) for s in r.subs])
        pers.append(o)
    own_refs = [r for room in rooms.values() for r in room.records]
    if MUTANT == "not_persistent":
        own_refs[0].flags &= ~PERSISTENT
    if MUTANT == "dup_form_id":
        own_refs[1].form_id = own_refs[0].form_id
    pers += own_refs + list(decor_refs or [])
    label = u32(src.record.form_id)
    kids = Group(label, esm.G_CELL_CHILDREN, children=[Group(label, esm.G_CELL_PERSISTENT, children=pers)])
    blk, sub = block_labels(src.record.form_id)
    if MUTANT == "wrong_subblock":
        sub = (sub + 1) % 10
    return Group(b"CELL", esm.G_TOP, children=[
        Group(u32(blk), esm.G_INT_BLOCK, children=[Group(u32(sub), esm.G_INT_SUBBLOCK, children=[rec, kids])])])


def count_records(items: list) -> int:
    n = 0
    for x in items:
        n += 1
        if isinstance(x, Group):
            n += count_records(x.children)
    return n


def build_plugin(master: Master, enemies_enabled: bool = False) -> Tuple[bytes, dict]:
    src = master.cell(CELL_FORM)
    if src.record.edid() != CELL_EDID_SRC:
        raise ValueError("cell %08X is %r, expected %s" % (CELL_FORM, src.record.edid(), CELL_EDID_SRC))
    if (src.block, src.subblock) != block_labels(CELL_FORM):
        raise ValueError("cell %08X sits in block %d/%d, not %s" % (CELL_FORM, src.block, src.subblock, block_labels(CELL_FORM)))
    meshes = src.navmeshes()
    if not meshes:
        raise ValueError("cell %08X has no navmesh" % CELL_FORM)
    mesh = MultiNavmesh(meshes)
    verify_sources(master)
    rooms = build_rooms(mesh, enemies_enabled)
    bases = build_base_records(master)
    groups = {label: Group(label.encode(), esm.G_TOP, children=recs) for label, recs in bases.items()}
    decor_refs, decor_layout = decor.build(master, src, rooms, rooms["spawn"].origin[2])
    groups["CELL"] = build_cell_group(src, rooms, decor_refs)
    tops = [groups[l] for l in (list(reversed(TOP_ORDER)) if MUTANT == "unsorted_top" else TOP_ORDER)]
    num = count_records(tops)
    if MUTANT == "hedr_count":
        num += 1
    next_id = OWN | (max(r.form_id for r in [r for room in rooms.values() for r in room.records] + decor_refs) & 0xFFFFFF) + 1
    hedr = struct.pack("<fII", 1.34, num, next_id & 0xFFFFFFFF)
    subs = [Sub("HEDR", hedr), Sub("CNAM", zstr(AUTHOR))]
    if MUTANT != "no_master":
        subs += [Sub("MAST", zstr(MASTER)), Sub("DATA", bytes(8))]
    header = Record("TES4", 0, 0, 0, 15, 0, subs)
    plugin = Plugin(header, tops)
    layout = make_layout(src, rooms, mesh)
    layout["decor"] = decor_layout
    return esm.encode_plugin(plugin), layout


def verify_sources(master: Master) -> None:
    """The stock records we lean on must be what we think: the enemy weak and aggressive, the civilian unaggressive."""
    for key in BASES:
        master.base(key)
    gecko = master.base("enemy")
    aidt, data = gecko.get("AIDT"), gecko.get("DATA")
    if aidt[0] < 1:
        raise ValueError("enemy base is not aggressive (aggression %d)" % aidt[0])
    health = struct.unpack_from("<H", data, 4)[0]
    if health > 120:
        raise ValueError("enemy base is not weak (health %d)" % health)
    if master.base("npc_src").get("AIDT")[0] != 0:
        raise ValueError("civilian base is aggressive")


def make_layout(src: CellSource, rooms: Dict[str, Room], mesh: Navmesh) -> dict:
    layout: dict = {
        "plugin": PLUGIN,
        "cell": {"editor_id": CELL_EDID, "form": master_form(src.record.form_id), "editor_id_alt": CELL_EDID_SRC,
                 "mode": "override of a stock navmeshed interior", "floor_z": round(rooms["spawn"].origin[2], 3)},
        "rooms": {},
    }
    for name in ROOM_ORDER:
        r = rooms[name]
        entry: dict = {"origin": [round(v, 3) for v in r.origin]}
        if r.markers:
            entry["markers"] = {k: own_form(v) for k, v in r.markers.items()}
        if r.refs:
            entry["refs"] = {k: own_form(v) for k, v in r.refs.items()}
        stand = r.stand_local
        if MUTANT == "layout_wrong_form" and name == "fight":
            stand += 0x100
        entry["stand"] = own_form(stand)
        if r.disabled:
            entry["initially_disabled"] = list(r.disabled)
        if r.info:
            entry["info"] = r.info
        layout["rooms"][name] = entry
    return layout


def build_files(esm_path, out_dir, enemies_enabled: bool = False) -> Tuple[Path, Path]:
    master = Master(esm_path)
    try:
        data, layout = build_plugin(master, enemies_enabled)
    finally:
        master.close()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    esp, lay = out / PLUGIN, out / "layout.json"
    esp.write_bytes(data)
    lay.write_text(json.dumps(layout, indent=2) + "\n", encoding="utf-8")
    return esp, lay


def default_esm() -> Path:
    env = os.environ.get("FNV_ESM")
    if env:
        return Path(env)
    root = os.environ.get("FNV_INSTANCES_ROOT") or str(HERE.parents[3] / "fnv-instances")
    return Path(root) / "0" / "Data" / MASTER


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--esm", default=None, help="path to FalloutNV.esm (read only); default: instance 0 copy or FNV_ESM")
    ap.add_argument("--out", default="out/lab", help="output folder for NVCoopLab.esp and layout.json")
    ap.add_argument("--enemies-enabled", action="store_true",
                    help="place the fight and vats creatures enabled (default: initially disabled, the runner enables them at room start)")
    a = ap.parse_args(argv)
    esm_path = Path(a.esm) if a.esm else default_esm()
    if not esm_path.is_file():
        print("FalloutNV.esm not found: %s (use --esm or FNV_ESM)" % esm_path, file=sys.stderr)
        return 2
    esp, lay = build_files(esm_path, a.out, a.enemies_enabled)
    print("wrote %s (%d bytes) and %s" % (esp, esp.stat().st_size, lay))
    return 0


if __name__ == "__main__":
    sys.exit(main())
