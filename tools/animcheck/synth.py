"""Synthetic probe streams for game-free tests: a scripted actor plus injected glitches and a delayed 'client' copy.

Everything is built as the dicts `anim.probe` would return, so the same parser (model.py) is exercised.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from .animgroups_table import NAMES

G = {n: i for i, n in enumerate(NAMES)}
HOST_REF = 0x00000014
NPC_REF = 0x0010A2B3
STEP_MS = 50.0


@dataclass
class State:
    pos: Tuple[float, float, float]
    rotz: float = 0.0
    rotx: float = 0.0
    aiming: bool = False
    groups: List[Tuple[int, int]] = field(default_factory=list)   # (slot, group) animating
    phase: float = 0.0                                              # walk cycle phase (radians)
    dead: bool = False
    weapon_out: bool = False
    weapon_form: int = 0
    arms_out: bool = False
    cell: int = 0x00000DEAD
    swimming: bool = False
    knocked: int = 0
    seq_state: int = 1
    pose_noise: float = 0.0
    arm_offset: float = 0.0     # injected pose defect: left hand displaced by this many units


def bones_for(st: State) -> Dict[str, List[float]]:
    """A plausible standing / walking skeleton in the actor's local frame, then rotated and placed in the world."""
    sw = math.sin(st.phase)
    loc = {
        "Bip01 Pelvis": (0, 0, 40), "Bip01 Spine1": (0, 0, 55), "Bip01 Spine2": (0, 0, 65),
        "Bip01 Neck1": (0, 0, 78), "Bip01 Head": (0, 0, 86),
        "Bip01 L UpperArm": (0, -10, 72), "Bip01 R UpperArm": (0, 10, 72),
        "Bip01 L Calf": (8 * sw, -6, 22), "Bip01 R Calf": (-8 * sw, 6, 22),
        "Bip01 L Foot": (14 * sw, -6, 4), "Bip01 R Foot": (-14 * sw, 6, 4),
    }
    if st.arms_out:
        loc["Bip01 L Forearm"], loc["Bip01 L Hand"] = (0, -30, 72), (0, -52, 72)
        loc["Bip01 R Forearm"], loc["Bip01 R Hand"] = (0, 30, 72), (0, 52, 72)
    else:
        loc["Bip01 L Forearm"], loc["Bip01 L Hand"] = (-5 * sw, -12, 58), (-9 * sw, -12, 46)
        loc["Bip01 R Forearm"], loc["Bip01 R Hand"] = (5 * sw, 12, 58), (9 * sw, 12, 46)
    loc["Weapon"] = (loc["Bip01 R Hand"][0] + 6, loc["Bip01 R Hand"][1], loc["Bip01 R Hand"][2])
    lx, ly, lz = loc["Bip01 L Hand"]
    loc["Bip01 L Hand"] = (lx + st.arm_offset, ly, lz)
    c, s = math.cos(st.rotz), math.sin(st.rotz)
    out = {}
    for k, (x, y, z) in loc.items():
        out[k] = [round(st.pos[0] + x * c - y * s + st.pose_noise, 3), round(st.pos[1] + x * s + y * c, 3), round(st.pos[2] + z, 3)]
    return out


def actor_dict(ref: int, st: State, bones: bool = True, t: float = 0.0) -> dict:
    seqs = [{"slot": sl, "group": g, "state": st.seq_state, "t": round(t / 1000.0, 3), "begin": 0.0, "end": 1.0,
             "freq": 1.0, "weight": 1.0} for sl, g in st.groups]
    d = {"ref": f"0x{ref:08X}", "base": "0x00000007", "valid3d": True, "first_person": False, "dead": st.dead,
         "life": 2 if st.dead else 0, "pos": [round(st.pos[0], 3), round(st.pos[1], 3), round(st.pos[2], 3)],
         "rot": [round(st.rotx, 4), 0.0, round(st.rotz, 4)], "cell": f"0x{st.cell:08X}",
         "anim": {"action": 0, "sequence_raw": "0x00000000", "seqs": seqs},
         "weapon": {"out": st.weapon_out, "aiming": st.aiming, "form": f"0x{st.weapon_form:08X}"},
         "stance": {"knocked": st.knocked, "sit_sleep": 0, "swimming": st.swimming, "ragdoll_ctrl": False},
         "ai": {"in_combat": False, "combat_target": "0x00000000", "package": "0x00000000"}}
    if bones:
        d["bones"] = bones_for(st)
    return d


Script = Callable[[float], State]   # t_ms -> State


def walker(speed: float = 120.0, start=(1000.0, 2000.0, 100.0), heading: float = 0.0) -> Script:
    """Walks forward at `speed` u/s with the matching animation (Forward < 200 u/s <= FastForward)."""
    fast = speed >= 200.0

    def f(t: float) -> State:
        d = speed * t / 1000.0
        pos = (start[0] + d * math.cos(heading), start[1] + d * math.sin(heading), start[2])
        grp = G["FastForward"] if fast else G["Forward"]
        return State(pos=pos, rotz=heading, groups=[(1, grp), (0, G["Idle"])], phase=2 * math.pi * t / 700.0)
    return f


def idler(start=(1000.0, 2000.0, 100.0), heading: float = 0.0) -> Script:
    return lambda t: State(pos=start, rotz=heading, groups=[(0, G["Idle"])], phase=0.0)


def make_probes(machine: str, scripts: Dict[int, Script], duration_ms: float, step_ms: float = STEP_MS,
                t0: float = 0.0, clock_offset: float = 0.0, bones: bool = True, rx: bool = True,
                transform: Optional[Callable[[int, float, State], Optional[State]]] = None) -> List[dict]:
    """One probe per step. transform(ref, t, state) may alter or drop (None) the state of an actor at a given time."""
    probes = []
    n = int(duration_ms / step_ms) + 1
    for i in range(n):
        t = i * step_ms
        actors = []
        for ref, sc in scripts.items():
            st = sc(t)
            if transform is not None:
                st = transform(ref, t, st)
                if st is None:
                    continue
            actors.append(actor_dict(ref, st, bones, t))
        p = {"machine": machine, "v": 1, "frame": i * 3, "t_ms": round(t0 + t + clock_offset, 2), "cost_us": 40.0, "actors": actors}
        if rx:
            p["rx_ms"] = round(t0 + t, 2)
        probes.append(p)
    return probes


def delayed(script: Script, delay_ms: float) -> Script:
    """What a client shows for an actor the host runs: the same script, `delay_ms` later."""
    return lambda t: script(max(0.0, t - delay_ms))


def between(t: float, a: float, b: float) -> bool:
    return a <= t < b


# ---- glitch injectors: each wraps a clean script and corrupts it inside [a, b) ----
def inject_sliding(sc: Script, a: float, b: float, speed: float = 150.0) -> Script:
    """Slides at `speed` u/s on an idle animation between a and b, and stays where it ended up."""
    def f(t: float) -> State:
        s = sc(t)
        if t >= a:
            dx = speed * (min(t, b) - a) / 1000.0
            s = State(pos=(s.pos[0] + dx, s.pos[1], s.pos[2]), rotz=s.rotz, groups=s.groups if t >= b else [(0, G["Idle"])])
        return s
    return f


def inject_tpose(sc: Script, a: float, b: float, mode: str = "noseq") -> Script:
    def f(t: float) -> State:
        s = sc(t)
        if between(t, a, b):
            if mode == "noseq":
                s.groups = []
            else:
                s.arms_out = True
        return s
    return f


def inject_float(sc: Script, a: float, b: float, dz: float = 90.0) -> Script:
    def f(t: float) -> State:
        s = sc(t)
        if between(t, a, b):
            s.pos = (s.pos[0], s.pos[1], s.pos[2] + dz)
        return s
    return f


def inject_jitter(sc: Script, a: float, b: float, amp: float = 4.0) -> Script:
    def f(t: float) -> State:
        s = sc(t)
        if between(t, a, b):
            k = int((t - a) / STEP_MS)
            s.pos = (s.pos[0] + (amp if k % 2 else -amp), s.pos[1], s.pos[2])
        return s
    return f


def inject_rubber(sc: Script, a: float, dist: float = 400.0, back_ms: float = 200.0) -> Script:
    def f(t: float) -> State:
        s = sc(t)
        if between(t, a, a + back_ms):
            s.pos = (s.pos[0] + dist, s.pos[1], s.pos[2])
        return s
    return f


def inject_death_alive(sc: Script, a: float) -> Script:
    def f(t: float) -> State:
        s = sc(t)
        if t >= a:
            s = State(pos=s.pos, rotz=s.rotz, groups=[(0, G["Idle"])], dead=True)
        return s
    return f


def proper_death(sc: Script, a: float) -> Script:
    def f(t: float) -> State:
        s = sc(t)
        if t >= a:
            s = State(pos=s.pos, rotz=s.rotz, groups=[(0, G["Death"])], dead=True, seq_state=1 if t < a + 2000 else 0)
        return s
    return f


def noisy(sc: Script, rng: random.Random, amp: float = 0.4) -> Script:
    """Sub-unit position noise: real sampling is never exact."""
    def f(t: float) -> State:
        s = sc(t)
        s.pos = (s.pos[0] + rng.uniform(-amp, amp), s.pos[1] + rng.uniform(-amp, amp), s.pos[2])
        return s
    return f
