"""NPC fight assertions for the animation checker (enemy-anim). Used by the fight playbook (tools/bots/playbooks) on the probe streams of two
machines that fought the same enemies:

    from animcheck.npc import analyze_npc, check_fight, FIGHT_ASSERTS
    rep = analyze_npc({"host": host_stream, "client": client_stream}, owner="host", expect_moving={ref: [(t0, t1)]})
    failures = check_fight(rep)            # [] when everything holds; strings otherwise

What it adds on top of animcheck.analyze (which already runs the eight glitch detectors on every machine and compares pose / weapon / facing):
  * the glitch detectors with NPC wiring: sliding, tpose, floating, sinking, jitter, rubberband, stuck (windows the playbook gives) and
    deathpose, all asserted ZERO in FIGHT_ASSERTS;
  * attack / reaction / reload EVENTS: every one-shot the owner plays must show on the other machine within the interpolation delay plus link
    latency (attack_missing, reaction_missing, reload_missing), and the other machine must not play one the owner did not (attack_extra ...);
  * weapon_never_drawn: the owner had the weapon out for a long time and the copy never did;
  * corpses: where the body of a dead NPC rests on the other machine against where it rests on the owner (corpse_err_max / corpse_wrong_place),
    a copy that never died (corpse_missing), a body still moving long after the death (corpse_unsettled).
Thresholds are first guesses (none measured in the game): see NpcThresholds and config.py.

    python -I tools/animcheck/npc.py OWNER.jsonl OBSERVER.jsonl [--map OWNER_REF=OBSERVER_REF ...] [--json out.json]   -> prints the failures, exit 1 on any
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple, Union

if __package__ in (None, ""):   # run as a script: python -I tools/animcheck/npc.py
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "animcheck"

from .analyze import analyze  # noqa: E402
from .animgroups import category  # noqa: E402
from .compare import percentile  # noqa: E402
from .config import DEFAULT, Thresholds, mutant  # noqa: E402
from .counters import check  # noqa: E402
from .model import Actor, Stream, load_stream  # noqa: E402

SCHEMA = "animcheck.npc.v1"

# Detector thresholds tuned for NPC fights: the shared defaults, written out so a playbook can see (and override) what applies.
NPC_THRESHOLDS: Thresholds = DEFAULT.with_(
    slide_min_speed=40.0,    # an NPC that moves faster than this horizontally needs a locomotion animation
    slide_ms=300.0,          # ... for at least this long before it counts as sliding
    rubber_dist=150.0,       # a one-sample jump over about 2 m (a melee lunge is far below this per 50 ms sample)
    jitter_amp=2.5, jitter_reversals=5,
    stuck_ms=1500.0,
    death_grace_ms=2500.0,   # a dead actor may still show a live animation this long
    state_grace_ms=250.0,    # a weapon / group mismatch shorter than this is a transition (an attack, stagger or draw lasts 350 to 700 ms: 400 would miss a short one)
    pose_tol_units=14.0,     # pose error after root alignment (NPC weapons and armour make bones noisier than the player's)
)


@dataclass(frozen=True)
class NpcThresholds:
    lead_ms: float = 100.0          # the copy shows state about this much later than the owner (interpolation buffer)
    latency_ms: float = 150.0       # plus the one-way link latency and the probe sampling jitter
    event_slack_ms: float = 300.0   # a one-shot may start this much later still before it is called missing
    never_drawn_ms: float = 2000.0  # the owner must have the weapon out this long for "never drawn" to be judged
    corpse_tol_units: float = 40.0  # horizontal distance between the two resting places that is tolerated (about 0.6 m)
    corpse_z_tol_units: float = 30.0
    corpse_settle_ms: float = 4000.0   # a body has come to rest this long after the death
    corpse_move_speed: float = 20.0    # units/s: a body still faster than this after settling is "unsettled"
    corpse_unsettled_ms: float = 600.0 # ... for at least this long


NPC_TH = NpcThresholds()

# Counter assertions of a fight test. Every key must exist in analyze_npc()'s counters (a typo is a failure, not a silent pass).
FIGHT_ASSERTS: Dict[str, Union[str, int, float]] = {
    "glitch.sliding": "==0", "glitch.tpose": "==0", "glitch.floating": "==0", "glitch.sinking": "==0",
    "glitch.jitter": "==0", "glitch.rubberband": "==0", "glitch.stuck": "==0", "glitch.deathpose": "==0",
    "wrong_weapon": "==0", "wrong_facing": "==0", "dead_mismatch": "==0",
    "pose_err_p95": f"<={NPC_THRESHOLDS.pose_tol_units}",
    "attack_missing": "==0", "attack_extra": "==0", "reaction_missing": "==0", "reaction_extra": "==0", "reload_missing": "==0", "reload_extra": "==0",
    "weapon_never_drawn": "==0",
    "corpse_missing": "==0", "corpse_wrong_place": "==0", "corpse_unsettled": "==0", "corpse_err_max": "<=40",
}

Episode = Tuple[float, float, int]   # (t0, t1, group)


# ---- one-shot episodes ------------------------------------------------------------------------------------------------------------------
def _episodes(samples: Sequence[Actor], cats: Sequence[str], gap_ms: float = 600.0) -> List[Episode]:
    """Maximal runs of samples in which some animating sequence of one group (category in cats) plays. A change of group, or a gap, starts a new run."""
    out: List[Episode] = []
    cur: Optional[List[float]] = None   # [t0, t1, group]
    for a in samples:
        groups = sorted({s.group for s in a.animating() if category(s.group) in cats})
        g = groups[0] if groups else -1
        if cur is not None and (g != cur[2] or a.t - cur[1] > gap_ms):
            out.append((cur[0], cur[1], int(cur[2])))
            cur = None
        if g >= 0:
            if cur is None:
                cur = [a.t, a.t, g]
            else:
                cur[1] = a.t
    if cur is not None:
        out.append((cur[0], cur[1], int(cur[2])))
    return out


def _plays(samples: Sequence[Actor], group: int, lo: float, hi: float) -> bool:
    return any(lo <= a.t <= hi and any(s.group == group and s.state == 1 for s in a.seqs) for a in samples)


def match_events(src: Sequence[Actor], obs: Sequence[Actor], cats: Sequence[str], nt: NpcThresholds, th: Thresholds) -> Tuple[int, int, int]:
    """(owner events, owner events with no match on the copy, copy events with no owner event)."""
    se = _episodes(src, cats, th.gap_ms)
    oe = _episodes(obs, cats, th.gap_ms)
    missing = 0
    for t0, t1, g in se:
        lo = t0 + nt.lead_ms - nt.latency_ms
        hi = t1 + nt.lead_ms + nt.latency_ms + nt.event_slack_ms
        if not _plays(obs, g, lo, hi):
            missing += 1
    extra = 0
    for t0, t1, g in oe:
        lo = t0 - nt.lead_ms - nt.latency_ms - nt.event_slack_ms
        hi = t1 - nt.lead_ms + nt.latency_ms
        if not _plays(src, g, lo, hi):
            extra += 1
    if mutant() == "npc_attack_match":
        missing = 0
    if mutant() == "npc_extra":
        extra = 0
    return len(se), missing, extra


# ---- weapon -------------------------------------------------------------------------------------------------------------------------------
def weapon_never_drawn(src: Sequence[Actor], obs: Sequence[Actor], nt: NpcThresholds, th: Thresholds) -> int:
    """Owner intervals with the weapon out for at least never_drawn_ms during which the copy's weapon was never out."""
    n = 0
    i = 0
    while i < len(src):
        if not src[i].weapon_out:
            i += 1
            continue
        j = i
        while j + 1 < len(src) and src[j + 1].weapon_out and src[j + 1].t - src[j].t <= th.gap_ms:
            j += 1
        t0, t1 = src[i].t, src[j].t
        if t1 - t0 >= nt.never_drawn_ms:
            lo, hi = t0 + nt.lead_ms - nt.latency_ms, t1 + nt.lead_ms + nt.latency_ms
            if mutant() != "npc_weapon_never" and not any(lo <= o.t <= hi and o.weapon_out for o in obs):
                n += 1
        i = j + 1
    return n


# ---- corpses ------------------------------------------------------------------------------------------------------------------------------
def _horiz(a: Tuple[float, float, float], b: Tuple[float, float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def corpse_report(src: Sequence[Actor], obs: Sequence[Actor], nt: NpcThresholds, th: Thresholds) -> Dict[str, float]:
    """For an actor that died on the owner: the copy must be dead, rest where the owner's body rests and stop moving."""
    dead = [a for a in src if a.dead]
    if not dead:
        return {"corpses": 0}
    t_death = dead[0].t
    rest_src = src[-1] if src[-1].dead else dead[-1]
    obs_after = [o for o in obs if o.t >= t_death]
    if not obs_after:
        return {"corpses": 1, "missing": 1, "err": 0.0, "wrong": 0, "unsettled": 0}
    last = obs[-1]
    out = {"corpses": 1, "missing": 0, "wrong": 0, "unsettled": 0, "err": 0.0}
    if not last.dead and last.t - t_death > nt.corpse_settle_ms:
        out["missing"] = 1
        return out
    err = _horiz(rest_src.pos, last.pos)
    dz = abs(rest_src.pos[2] - last.pos[2])
    if mutant() == "npc_corpse_err":
        err, dz = 0.0, 0.0
    out["err"] = err
    if err > nt.corpse_tol_units or dz > nt.corpse_z_tol_units:
        out["wrong"] = 1
    # still moving long after it should have come to rest
    settled = [o for o in obs if o.t >= t_death + nt.corpse_settle_ms]
    moving_ms = best = 0.0
    for a, b in zip(settled, settled[1:]):
        dt = (b.t - a.t) / 1000.0
        if dt <= 0 or dt * 1000.0 > th.gap_ms:
            moving_ms = 0.0
            continue
        sp = math.dist(a.pos, b.pos) / dt
        if sp > nt.corpse_move_speed:
            moving_ms += dt * 1000.0
            best = max(best, moving_ms)
        else:
            moving_ms = 0.0
    if best >= nt.corpse_unsettled_ms and mutant() != "npc_corpse_unsettled":
        out["unsettled"] = 1
    return out


# ---- the analysis -------------------------------------------------------------------------------------------------------------------------
def analyze_npc(streams: Union[Dict[str, Stream], Sequence[Stream]], owner: Optional[str] = None,
                ref_maps: Optional[Dict[str, Dict[int, int]]] = None, expect_moving: Optional[Dict[int, Sequence[tuple]]] = None,
                suppress: Sequence[dict] = (), th: Thresholds = NPC_THRESHOLDS, nt: NpcThresholds = NPC_TH, label: str = "") -> dict:
    """animcheck.analyze plus the NPC counters. The streams are the machines' probes of the SAME enemies; `owner` is the machine that ran them."""
    if not isinstance(streams, dict):
        streams = {s.machine: s for s in streams}
    names = list(streams)
    owner = owner or (names[0] if names else None)
    rep = analyze(streams, th, owner=owner, ref_maps=ref_maps, expect_moving=expect_moving, suppress=suppress, label=label)
    c = rep["counters"]
    keys = ("attack_events", "attack_missing", "attack_extra", "reaction_events", "reaction_missing", "reaction_extra",
            "reload_events", "reload_missing", "reload_extra", "weapon_never_drawn", "corpses", "corpse_missing", "corpse_wrong_place", "corpse_unsettled")
    for k in keys:
        c[k] = 0
    errs: List[float] = []
    if owner is not None and owner in streams:
        src = streams[owner]
        for name, obs_stream in streams.items():
            if name == owner:
                continue
            rm = (ref_maps or {}).get(name, {})
            for sref, ssamples in src.by_ref.items():
                osamples = obs_stream.by_ref.get(rm.get(sref, sref))
                if not osamples or len(ssamples) < 2:
                    continue
                for tag, cats in (("attack", ("attack",)), ("reaction", ("hit",)), ("reload", ("reload",))):
                    ev, miss, extra = match_events(ssamples, osamples, cats, nt, th)
                    c[f"{tag}_events"] += ev
                    c[f"{tag}_missing"] += miss
                    c[f"{tag}_extra"] += extra
                c["weapon_never_drawn"] += weapon_never_drawn(ssamples, osamples, nt, th)
                cr = corpse_report(ssamples, osamples, nt, th)
                c["corpses"] += cr.get("corpses", 0)
                c["corpse_missing"] += cr.get("missing", 0)
                c["corpse_wrong_place"] += cr.get("wrong", 0)
                c["corpse_unsettled"] += cr.get("unsettled", 0)
                if cr.get("corpses"):
                    errs.append(cr.get("err", 0.0))
    c["corpse_err_max"] = round(max(errs), 3) if errs else 0.0
    c["corpse_err_p95"] = round(percentile(errs, 95), 3) if errs else 0.0
    rep["schema"] = SCHEMA
    rep["npc_thresholds"] = asdict(nt)
    rep["failures"] = check(rep, FIGHT_ASSERTS)
    rep["ok"] = not rep["failures"]
    return rep


def check_fight(report: dict, extra: Optional[Dict[str, Union[str, int, float]]] = None) -> List[str]:
    """Failure strings of FIGHT_ASSERTS (plus `extra`, which overrides keys) against an analyze_npc report. [] = pass."""
    spec = dict(FIGHT_ASSERTS)
    if extra:
        spec.update(extra)
    return check(report, spec)


def main(argv: Sequence[str]) -> int:
    ap = argparse.ArgumentParser(description="NPC fight assertions over two machines' probe streams")
    ap.add_argument("owner_stream")
    ap.add_argument("observer_stream")
    ap.add_argument("--map", action="append", default=[], help="OWNER_REF=OBSERVER_REF (hex), when the copy has another ref id")
    ap.add_argument("--json", default="")
    a = ap.parse_args(list(argv))
    o = load_stream(a.owner_stream, "owner")
    b = load_stream(a.observer_stream, "observer")
    rm: Dict[int, int] = {}
    for m in a.map:
        x, y = m.split("=", 1)
        rm[int(x, 16)] = int(y, 16)
    rep = analyze_npc({"owner": o, "observer": b}, owner="owner", ref_maps={"observer": rm} if rm else None)
    if a.json:
        Path(a.json).write_text(json.dumps(rep, indent=1), encoding="utf-8")
    for f in rep["failures"]:
        print("FAIL:", f)
    print("npc fight check:", "OK" if rep["ok"] else f"{len(rep['failures'])} failure(s)")
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
