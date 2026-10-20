"""Probe stream model: what `anim.probe` returns, parsed, plus the JSONL stream format used on disk.

Stream file (JSONL, one probe per line), written by collect.Collector or synth.py:
    {"machine": "host", "t_ms": 1234.5, "rx_ms": 98765.4, "frame": 811, "actors": [ <actor> ... ]}
`t_ms` is the game's own clock (QueryPerformanceCounter since the plugin started); `rx_ms` is the collector's
monotonic clock when the reply arrived (shared by all machines on one PC). Alignment across machines uses rx_ms when
every stream has it, else t_ms. Lines that are not JSON objects are ignored (comments, blank lines).
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

Vec = Tuple[float, float, float]


@dataclass
class Seq:
    slot: int
    group: int
    state: int = 1
    t: float = 0.0
    begin: float = 0.0
    end: float = 0.0
    freq: float = 1.0
    weight: float = 1.0


@dataclass
class Actor:
    ref: int
    t: float                      # alignment time in ms (see module doc)
    pos: Vec = (0.0, 0.0, 0.0)
    rotz: float = 0.0             # radians
    rotx: float = 0.0             # pitch (aim elevation), radians
    cell: int = 0
    dead: bool = False
    valid3d: bool = True
    player: bool = False
    first_person: bool = False
    have_anim: bool = True
    seqs: List[Seq] = field(default_factory=list)
    action: int = -1
    move_flags: Optional[int] = None
    weapon_out: bool = False
    weapon_form: int = 0
    aiming: bool = False
    knocked: int = 0
    swimming: bool = False
    ragdoll: bool = False
    sneaking: bool = False
    in_combat: bool = False
    combat_target: int = 0
    package: int = 0
    bones: Dict[str, Vec] = field(default_factory=dict)
    ground_z: Optional[float] = None   # reserved: ground / navmesh height if the probe ever provides it
    frame: int = 0

    def animating(self) -> List[Seq]:
        return [s for s in self.seqs if s.state == 1]


def _hex(v, default=0) -> int:
    if isinstance(v, int):
        return v
    if isinstance(v, str) and v:
        try:
            return int(v, 16)
        except ValueError:
            return default
    return default


def _vec(v, default=(0.0, 0.0, 0.0)) -> Vec:
    if isinstance(v, (list, tuple)) and len(v) == 3:
        return (float(v[0]), float(v[1]), float(v[2]))
    return default


MOVE_WALK, MOVE_RUN, MOVE_SNEAK, MOVE_SWIM = 1 << 7, 1 << 8, 1 << 9, 1 << 11


def parse_actor(a: dict, t: float, frame: int = 0) -> Actor:
    anim = a.get("anim") or {}
    seqs = [Seq(slot=int(s.get("slot", 0)), group=int(s.get("group", -1)), state=int(s.get("state", -1)),
                t=float(s.get("t", 0.0)), begin=float(s.get("begin", 0.0)), end=float(s.get("end", 0.0)),
                freq=float(s.get("freq", 1.0)), weight=float(s.get("weight", 1.0)))
            for s in anim.get("seqs", [])]
    w = a.get("weapon") or {}
    st = a.get("stance") or {}
    ai = a.get("ai") or {}
    mf = a.get("move_flags")
    act = Actor(
        ref=_hex(a.get("ref")), t=t, pos=_vec(a.get("pos")), rotz=_vec(a.get("rot"))[2], rotx=_vec(a.get("rot"))[0], cell=_hex(a.get("cell")),
        dead=bool(a.get("dead", False)), valid3d=bool(a.get("valid3d", True)), player=bool(a.get("player", False)),
        first_person=bool(a.get("first_person", False)), have_anim=("anim" in a), seqs=seqs,
        action=int(anim.get("action", -1)), move_flags=None if mf is None else int(mf),
        weapon_out=bool(w.get("out", False)), weapon_form=_hex(w.get("form")), aiming=bool(w.get("aiming", False)),
        knocked=int(st.get("knocked", 0)), swimming=bool(st.get("swimming", False)),
        ragdoll=bool(st.get("ragdoll_ctrl", False)), in_combat=bool(ai.get("in_combat", False)),
        combat_target=_hex(ai.get("combat_target")), package=_hex(ai.get("package")),
        bones={k: _vec(v) for k, v in (a.get("bones") or {}).items()},
        ground_z=a.get("ground_z"), frame=frame)
    if act.move_flags is not None:
        act.sneaking = bool(act.move_flags & MOVE_SNEAK)
        act.swimming = act.swimming or bool(act.move_flags & MOVE_SWIM)
    return act


@dataclass
class Stream:
    machine: str
    by_ref: Dict[int, List[Actor]] = field(default_factory=dict)
    t_first: float = math.inf
    t_last: float = -math.inf

    def add(self, a: Actor) -> None:
        self.by_ref.setdefault(a.ref, []).append(a)
        self.t_first = min(self.t_first, a.t)
        self.t_last = max(self.t_last, a.t)

    def finish(self) -> "Stream":
        for lst in self.by_ref.values():
            lst.sort(key=lambda x: x.t)
        return self

    @property
    def sample_count(self) -> int:
        return sum(len(v) for v in self.by_ref.values())


def stream_from_probes(machine: str, probes: Iterable[dict], use_rx: bool = False) -> Stream:
    s = Stream(machine)
    for p in probes:
        if not isinstance(p, dict):
            continue
        t = p.get("rx_ms") if (use_rx and p.get("rx_ms") is not None) else p.get("t_ms", 0.0)
        for a in p.get("actors", []):
            s.add(parse_actor(a, float(t), int(p.get("frame", 0))))
    return s.finish()


def read_probes(path: Path | str) -> List[dict]:
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line[0] != "{":
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def load_stream(path: Path | str, machine: Optional[str] = None, use_rx: Optional[bool] = None) -> Stream:
    probes = read_probes(path)
    if use_rx is None:
        use_rx = bool(probes) and all(p.get("rx_ms") is not None for p in probes)
    name = machine or (probes[0].get("machine") if probes else None) or Path(path).stem
    return stream_from_probes(name, probes, use_rx=use_rx)


def write_probes(path: Path | str, probes: Iterable[dict]) -> int:
    n = 0
    with open(path, "w", encoding="utf-8") as f:
        for p in probes:
            f.write(json.dumps(p, separators=(",", ":")) + "\n")
            n += 1
    return n


def horizontal_speed(a: Actor, b: Actor) -> float:
    """units per second between two samples of the same actor (0 if no time passed)."""
    dt = (b.t - a.t) / 1000.0
    if dt <= 0:
        return 0.0
    return math.hypot(b.pos[0] - a.pos[0], b.pos[1] - a.pos[1]) / dt
