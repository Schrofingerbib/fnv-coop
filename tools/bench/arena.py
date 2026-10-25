"""The benchmark arena: one place where every test happens, described as offsets from an origin, so a scenario can say "walk the square" or
"spawn at the ring" without knowing coordinates.

Pure data and geometry (no game calls): it is unit tested without the game. Finding a good origin in the real world is `find_origin` in
phases.py (it needs the game). The arena is built at runtime from console commands, not from a plugin file, so no extra load-order entry is needed.
"""
import json, math
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_FILE = HERE / "arena.json"


class Arena:
    """origin = (x, y, z) in world units; cell = (worldspace, cx, cy) for `cow`, or None to use wherever the players already are."""

    def __init__(self, origin=None, cell=None, size=450.0, ring_radius=700.0):
        self.origin = tuple(origin) if origin else None
        self.cell = tuple(cell) if cell else None
        self.size = float(size)
        self.ring_radius = float(ring_radius)

    # ---- layout
    def _o(self, around):
        o = self.origin or around
        if o is None:
            raise ValueError("the arena has no origin and no current position was given")
        return o

    def course(self, name, around=None, size=None):
        """Waypoints of a named walking course, relative to the arena origin (or `around`, a current position, when the arena has none)."""
        x, y, z = self._o(around)
        s = float(size or self.size)
        if name == "square":
            return [(x + s, y, z), (x + s, y + s, z), (x, y + s, z), (x, y, z)]
        if name == "line":
            return [(x + 2 * s, y, z), (x, y, z)]
        if name == "figure8":
            return [(x + s, y + s, z), (x + 2 * s, y, z), (x + s, y - s, z), (x, y, z)]
        if name == "circle":
            n = 8
            return [(x + s * math.cos(2 * math.pi * i / n) - s, y + s * math.sin(2 * math.pi * i / n), z) for i in range(1, n + 1)]
        raise KeyError(f"unknown course {name!r}; choose square, line, figure8, circle")

    def ring(self, n, around=None, radius=None):
        """n evenly spaced points on a ring around the origin (enemy spawn points)."""
        x, y, z = self._o(around)
        r = float(radius or self.ring_radius)
        return [(x + r * math.cos(2 * math.pi * i / n), y + r * math.sin(2 * math.pi * i / n), z) for i in range(n)]

    def loot_spot(self, around=None):
        return self._o(around)

    # ---- file
    def to_json(self):
        return {"origin": self.origin, "cell": self.cell, "size": self.size, "ring_radius": self.ring_radius}

    @classmethod
    def load(cls, path=None):
        p = Path(path) if path else DEFAULT_FILE
        if not p.is_file():
            return cls()
        d = json.loads(p.read_text(encoding="utf-8"))
        return cls(d.get("origin"), d.get("cell"), d.get("size", 450.0), d.get("ring_radius", 700.0))

    def save(self, path=None):
        Path(path or DEFAULT_FILE).write_text(json.dumps(self.to_json(), indent=1), encoding="utf-8")
