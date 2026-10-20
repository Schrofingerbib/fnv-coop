"""Thresholds and tolerances. Every value is a first guess in game units (about 70 units = 1 m) and milliseconds; none
has been measured in the running game yet. Playbooks may override any of them: Thresholds(sliding_speed=80, ...)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from typing import Dict, Tuple


def mutant() -> str:
    """Name of the active mutation twin (tests only), '' in normal use."""
    return os.environ.get("ANIMCHECK_MUTANT", "")


@dataclass(frozen=True)
class Thresholds:
    # ---- sampling ----
    gap_ms: float = 600.0              # a gap longer than this between two samples of an actor breaks a streak
    min_dt_ms: float = 5.0             # samples closer than this are treated as one

    # ---- comparison across machines (FULL-SYNC-PLAN section 2: 100 ms interpolation buffer) ----
    interp_delay_ms: float = 100.0     # the receiver shows state about this much older than the owner's
    latency_allow_ms: float = 150.0    # extra window (one way network latency + sampling jitter) searched on each side
    pose_tol_units: float = 12.0       # default bound used by the report's pass/fail summary only
    facing_tol_deg: float = 35.0
    aim_tol_deg: float = 20.0
    state_grace_ms: float = 400.0      # a weapon / group mismatch shorter than this is a transition, not a bug

    # ---- sliding (moving without matching locomotion animation) ----
    slide_min_speed: float = 40.0      # units/s horizontal below which the actor counts as standing
    slide_ms: float = 300.0
    loco_bands: Dict[str, Tuple[float, float]] = field(default_factory=lambda: {
        "walk": (8.0, 280.0),          # category -> plausible horizontal speed band while that animation plays
        "run": (120.0, 900.0),
        "dodge": (0.0, 1500.0),
    })
    band_ms: float = 500.0             # speed outside the band for this long = animation / speed mismatch

    # ---- T-pose ----
    tpose_ms: float = 300.0
    tpose_arm_dz: float = 5.0          # hands within this of their upper arm height ...
    tpose_arm_span: float = 36.0       # ... and at least this far (horizontally) from it: arms straight out

    # ---- floating / sinking ----
    ground_step: float = 30.0          # z change from the tracked ground that is still "terrain"
    float_ms: float = 400.0
    fall_vz: float = 400.0             # vertical speed above which the actor is falling / jumping

    # ---- jitter ----
    jitter_window: int = 8             # samples
    jitter_amp: float = 2.5            # minimum step (units) that counts as a direction reversal
    jitter_reversals: int = 5          # reversals inside the window

    # ---- rubber banding ----
    rubber_dist: float = 150.0         # one-sample jump
    rubber_speed: float = 2500.0       # units/s implied by that jump
    snapback_ms: float = 600.0         # jump followed by a return to (near) the old spot within this

    # ---- stuck ----
    stuck_ms: float = 1500.0
    stuck_speed: float = 5.0

    # ---- wrong death pose ----
    death_grace_ms: float = 2500.0

    def with_(self, **kw) -> "Thresholds":
        return replace(self, **kw)


DEFAULT = Thresholds()
