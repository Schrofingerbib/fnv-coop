"""Lighting, interior partitions and camera hints for the lab cell (called from build_lab.py; stdlib only).

What GreenRoom already is (read from FalloutNV.esm, see README "Decor"): an enclosed office-kit room. 70 OffRmMid01 tiles are floor
AND ceiling (256 units apart, the ceiling torn open in places), 30 OffRmWall01 plus 4 OffRmCorIn01 pieces form the outer wall ring.
Stock lighting is ambient 50, no directional, fog 100..2500 in grey, and no light refs at all. Seen from above the ceiling is a dark
plane with holes, seen from inside it is a dark box: that is the "grey void".

This module adds, all as persistent refs with our own form ids (>= DECOR_FIRST, after the room refs):
- an XCLL replacement (bright neutral ambient, a directional, fog pushed out to 20000..40000, far clip 40000);
- light refs (LIGH bases from the master) over each room, below the ceiling fixtures;
- interior partitions: a back (north) wall and side partitions per room from the slab OffRmWallONLY01a (collision, 256 tall).
  The front of every room stays open. The outer boundary of the whole grid is the stock wall ring, nothing is added there.
- suggested camera positions inside each room (the camera must be under the 256 ceiling to see anything).

Nothing here changes the navmesh. NVLABESP_MUTANT is test-only (one deliberate bug per name, see run_tests.py).
"""
from __future__ import annotations

import math
import os
import struct
from typing import Dict, List, Tuple

import esm
from esm import Record, Sub

MUTANT = os.environ.get("NVLABESP_MUTANT", "")

OWN = 0x01000000
PERSISTENT = 0x00000400
DECOR_FIRST = 0x900             # local ids: after the room refs (0x810..0x88F), before nothing else
CEILING = 256.0                 # stock office pieces: floor at 0, ceiling at 256 (verified in the NIFs)

# stock records used (id, record type, editor id): checked against the master at build time
DECOR_BASES = {
    "wall": (0x000288B6, "STAT", "OffRmWallONLY01a"),
    "light_key": (0x0002F50D, "LIGH", "gVaultAmbWhite512"),
    "light_fill": (0x0011E398, "LIGH", "MALightFillWarm"),
}
if MUTANT == "wall_no_collision":
    DECOR_BASES["wall"] = (0x00032E8A, "STAT", "OffRmFloorHolePlatform03")      # a stock piece without a collision body
if MUTANT == "light_flicker":
    DECOR_BASES["light_key"] = (0x0003D5B0, "LIGH", "Barrel02Fire1024")         # flickers

# LIGH DATA flag bits that make a light unsuitable for a steady fill: flicker, flicker slow, pulse, pulse slow, spot, spot shadow, off by default
BAD_LIGHT_FLAGS = 0x8 | 0x40 | 0x80 | 0x100 | 0x200 | 0x400 | 0x20
MIN_LIGHT_RADIUS = 400

LIGHT_Z = 200.0                 # above the room floor, below the ceiling fixtures (they hang from 237 up)
KEY_OFFSETS = [(0.0, -110.0)]   # (dx, dy) from the room origin, white 512 radius
FILL_OFFSETS = [(0.0, 130.0)]   # warm-neutral 1024 radius, wide fill

# partition geometry, in units, relative to a room origin. Rooms are 480 apart in x, 832 in y.
ROOM_HALF_W = 240.0             # side partitions on the room boundary x = ox +- 240 (shared with the neighbour)
BACK_DY = 330.0                 # back wall centre line: oy + 330 (targets are at most oy + 220)
FRONT_DY = 428.0                # side partitions end 428 in front of the origin (open front, no front wall)
CLEARANCE = 60.0                # nothing placed in a room may be closer than this to a wall (tests enforce it)

LIGHTING = {
    "ambient": (150, 150, 150, 0),
    "directional": (120, 120, 120, 0),
    "fog_color": (110, 110, 110, 0),
    "fog_near": 20000.0,
    "fog_far": 40000.0,
    "rot_xy": 45,
    "rot_z": 45,
    "dir_fade": 1.0,
    "fog_clip": 40000.0,
    "fog_power": 1.0,
}


def pack_xcll(l: dict = LIGHTING) -> bytes:
    """FNV XCLL (40 bytes): ambient, directional and fog colours (RGBA), fog near/far, directional rotation xy and z, fade, clip, power."""
    return struct.pack("<4B4B4B2f2i3f", *l["ambient"], *l["directional"], *l["fog_color"], l["fog_near"], l["fog_far"], l["rot_xy"], l["rot_z"],
                       l["dir_fade"], l["fog_clip"], l["fog_power"])


def unpack_xcll(d: bytes) -> dict:
    v = struct.unpack("<4B4B4B2f2i3f", d)
    return {"ambient": v[0:4], "directional": v[4:8], "fog_color": v[8:12], "fog_near": v[12], "fog_far": v[13], "rot_xy": v[14], "rot_z": v[15],
            "dir_fade": v[16], "fog_clip": v[17], "fog_power": v[18]}


def new_xcll(stock: bytes) -> bytes:
    """The cell's lighting: the stock subrecord replaced (MUTANT dim_cell keeps it)."""
    if len(stock) != 40:
        raise ValueError("XCLL is %d bytes, expected 40" % len(stock))
    return stock if MUTANT == "dim_cell" else pack_xcll()


def _ref(local: int, base: int, pos, edid: str, rot=(0.0, 0.0, 0.0)) -> Record:
    subs = [Sub("EDID", edid.encode("latin-1") + b"\0"), Sub("NAME", struct.pack("<I", base)), Sub("DATA", struct.pack("<6f", *pos, *rot))]
    return Record("REFR", PERSISTENT, OWN | local, 0, 15, 0, subs)


def check_bases(master) -> Dict[str, dict]:
    """Type and editor id of every stock record we place, plus the facts the placement depends on (wall footprint, light data)."""
    facts: Dict[str, dict] = {}
    for key, (fid, typ, edid) in DECOR_BASES.items():
        rec = master.record(fid, typ)
        if rec.type != typ or rec.edid() != edid:
            raise ValueError("%08X is %s %r, expected %s %r" % (fid, rec.type, rec.edid(), typ, edid))
        if key == "wall":
            x0, y0, z0, x1, y1, z1 = struct.unpack("<6h", rec.get("OBND"))
            facts[key] = {"half_t": float(max(-x0, x1)), "half_len": float(max(-y0, y1)), "height": float(z1 - z0), "model": rec.get("MODL").split(b"\0")[0].decode("latin-1")}
            if MUTANT != "wall_no_collision" and (facts[key]["half_len"] < 100 or facts[key]["half_t"] > 40 or facts[key]["height"] < 200):
                raise ValueError("wall base %s is not a slab (%r)" % (edid, facts[key]))
        else:
            t, rad, cr, cg, cb, _ca, flags = struct.unpack_from("<iI4BI", rec.get("DATA"))
            facts[key] = {"radius": rad, "color": [cr, cg, cb], "flags": flags, "time": t}
            if flags & BAD_LIGHT_FLAGS or rad < MIN_LIGHT_RADIUS or t != -1:
                raise ValueError("light base %s is not a steady wide light (%r)" % (edid, facts[key]))
    return facts


def _centres(lo: float, hi: float, length: float) -> List[float]:
    """Centres of equal-length pieces covering lo..hi with no gap (the last overlaps its neighbour a little)."""
    span = hi - lo
    n = max(1, int(math.ceil(span / length - 1e-9)))
    if n == 1:
        return [(lo + hi) / 2]
    step = (span - length) / (n - 1)
    return [lo + length / 2 + step * j for j in range(n)]


def wall_plan(origins: Dict[str, Tuple[float, float, float]], half_t: float, half_len: float) -> List[dict]:
    """The partitions as dicts {role, x, y, rot_z, axis, rooms}: side partitions on each room column boundary, a back wall per room row."""
    xs = sorted({o[0] for o in origins.values()})
    ys = sorted({o[1] for o in origins.values()})
    length = 2 * half_len
    plan: List[dict] = []
    lines = [xs[0] - ROOM_HALF_W + 480 * k for k in range(len(xs) + 1)]
    for y_origin in ys:
        row = [n for n, o in origins.items() if o[1] == y_origin]
        top = y_origin + BACK_DY + half_t
        bottom = y_origin - FRONT_DY
        for k, x in enumerate(lines):
            if MUTANT == "wall_in_room" and abs(y_origin - ys[0]) < 1 and k == 2:
                x -= 120.0          # twin: the walk room's east partition moved in across its waypoints
            for y in _centres(bottom, top, length):
                plan.append({"role": "side", "x": x, "y": y, "rot_z": 0.0, "axis": "y", "row": y_origin})
        left, right = lines[0] - half_t, lines[-1] + half_t
        for x in _centres(left, right, length):
            plan.append({"role": "back", "x": x, "y": y_origin + BACK_DY, "rot_z": math.pi / 2, "axis": "x", "row": y_origin})
    return plan


def build(master, src, rooms, floor_z: float, first_local: int = DECOR_FIRST) -> Tuple[List[Record], dict]:
    """Returns (placed refs, the layout.json "decor" object). `rooms` maps name -> Room (needs .origin and .name)."""
    facts = check_bases(master)
    origins = {n: r.origin for n, r in rooms.items()}
    nxt = first_local
    if MUTANT == "decor_id_clash":
        nxt = 0x810
    recs: List[Record] = []
    lights: List[dict] = []
    walls: List[dict] = []

    def alloc() -> int:
        nonlocal nxt
        v = nxt
        nxt += 1
        return v

    for name, room in rooms.items():
        if MUTANT == "no_light_room" and name == "loot":
            continue
        ox, oy, _ = room.origin
        for kind, offs in (("light_key", KEY_OFFSETS), ("light_fill", FILL_OFFSETS)):
            for dx, dy in offs:
                local = alloc()
                pos = (ox + dx, oy + dy, floor_z + LIGHT_Z)
                recs.append(_ref(local, DECOR_BASES[kind][0], pos, "NVCoopLabLight%s%d" % (name.capitalize(), len(lights))))
                lights.append({"form": "NVCoopLab.esp:%06X" % local, "base": "FalloutNV.esm:%06X" % DECOR_BASES[kind][0], "editor_id": DECOR_BASES[kind][2],
                               "room": name, "pos": [round(v, 3) for v in pos], "radius": facts[kind]["radius"]})
    for w in wall_plan(origins, facts["wall"]["half_t"], facts["wall"]["half_len"]):
        local = alloc()
        pos = (w["x"], w["y"], floor_z)
        recs.append(_ref(local, DECOR_BASES["wall"][0], pos, "NVCoopLabWall%d" % len(walls), rot=(0.0, 0.0, w["rot_z"])))
        row_rooms = sorted(n for n, o in origins.items() if o[1] == w["row"])
        walls.append({"form": "NVCoopLab.esp:%06X" % local, "role": w["role"], "pos": [round(v, 3) for v in pos], "rot_z": round(w["rot_z"], 6),
                      "axis": w["axis"], "row_rooms": row_rooms})
    stock_walls = [r for r in src.temporary if r.type == "REFR" and struct.unpack("<I", r.get("NAME"))[0] in (0x159E0, 0x159DD)]
    decor = {
        "note": "decor only: the runner ignores it. Partitions are back and side walls, rooms are open to the front (south).",
        "lighting": {k: (list(v) if isinstance(v, tuple) else v) for k, v in LIGHTING.items()},
        "light_base": {k: {"form": "FalloutNV.esm:%06X" % DECOR_BASES[k][0], "editor_id": DECOR_BASES[k][2], **facts[k]} for k in ("light_key", "light_fill")},
        "lights": lights,
        "wall_base": {"form": "FalloutNV.esm:%06X" % DECOR_BASES["wall"][0], "editor_id": DECOR_BASES["wall"][2], "collision": True, **facts["wall"]},
        "walls": walls,
        "clearance": CLEARANCE,
        "outer_boundary": {"kind": "stock office wall ring (OffRmWall01 and OffRmCorIn01), already in the cell", "pieces": len(stock_walls),
                           "ceiling_z": floor_z + CEILING},
        "disabled_stock_refs": [],
        "first_form": "NVCoopLab.esp:%06X" % first_local,
        "cameras": cameras(rooms, floor_z),
    }
    return recs, decor


def cameras(rooms, floor_z: float) -> dict:
    """Suggested camera per room: inside the side partitions, in front of the stand marker, looking at the room origin.
    Feet height floor + 20: the eye is then about 130 up, under the 256 ceiling (a camera above it only sees the torn ceiling)."""
    out = {}
    for name, room in rooms.items():
        ox, oy, _ = room.origin
        cx, cy = ox - 150.0, oy - 380.0
        yaw = math.degrees(math.atan2(ox - cx, oy - cy)) % 360.0
        out[name] = {"pos": [cx, cy, floor_z + 20.0], "yaw": round(yaw, 1), "pitch": 6.0, "look_at": [ox, oy, floor_z + 60.0]}
    return out


# --- geometry shared with the tests ------------------------------------------------------------------------------------

def wall_rect(w: dict, half_t: float, half_len: float) -> Tuple[float, float, float, float]:
    """(x0, y0, x1, y1) footprint of one wall entry of the decor object."""
    x, y, _ = w["pos"]
    if w["axis"] == "y":
        return x - half_t, y - half_len, x + half_t, y + half_len
    return x - half_len, y - half_t, x + half_len, y + half_t


def dist_point_rect(px: float, py: float, r: Tuple[float, float, float, float]) -> float:
    dx = max(r[0] - px, 0.0, px - r[2])
    dy = max(r[1] - py, 0.0, py - r[3])
    return math.hypot(dx, dy)


def dist_segment_rect(a, b, r, steps: int = 64) -> float:
    return min(dist_point_rect(a[0] + (b[0] - a[0]) * i / steps, a[1] + (b[1] - a[1]) * i / steps, r) for i in range(steps + 1))
