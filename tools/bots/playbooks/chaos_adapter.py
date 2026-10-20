"""Optional link to T5's network chaos relay (tools/chaos). All functions are no-ops that return a note if it is absent."""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, Optional

PROFILES = ("lan", "good_wifi", "bad_wifi", "mobile_tether", "transatlantic", "hell")


def _client(repo: str):
    d = os.path.join(repo, "tools", "chaos")
    if not os.path.isfile(os.path.join(d, "client.py")):
        return None
    if d not in sys.path:
        sys.path.insert(0, d)
    import client      # noqa: WPS433
    return client


def apply_profile(repo: str, rpc: Optional[str], profile: str, seed: Optional[int]) -> Dict[str, Any]:
    """Select a chaos profile on a running relay. `rpc` is the port, host:port or info file from `chaos.py relay --info-file`."""
    if profile not in PROFILES:
        raise ValueError(f"unknown chaos profile {profile!r}; choose from {PROFILES}")
    c = _client(repo)
    if c is None or not rpc:
        return {"applied": False, "note": "tools/chaos or --chaos-rpc is missing: playbooks run without impairment"}
    return {"applied": True, "result": c.set_profile(rpc, profile, seed)}


def log_path(repo: str, rpc: Optional[str]) -> Optional[str]:
    c = _client(repo)
    if c is None or not rpc:
        return None
    try:
        return c.packet_log_path(rpc)
    except Exception:   # noqa: BLE001
        return None


# quiet window (seconds without new traffic) after which the world must be converged with 0 repairs; scaled to latency+jitter.
QUIESCE_S = {"lan": 1.0, "good_wifi": 2.0, "bad_wifi": 6.0, "mobile_tether": 8.0, "transatlantic": 6.0, "hell": 20.0}
# fake-world loss: every Nth delivery to a client is dropped and retransmitted (0 = none). Game-free stand-in for the relay.
FAKE_LOSS_EVERY = {"lan": 0, "good_wifi": 25, "bad_wifi": 5, "mobile_tether": 11, "transatlantic": 40, "hell": 3}
SETTLE_S = {"lan": 20.0, "good_wifi": 30.0, "bad_wifi": 60.0, "mobile_tether": 90.0, "transatlantic": 90.0, "hell": 180.0}


def repair_budget(repo: str, profile: str) -> float:
    """Repairs per minute allowed for a profile: `repair_budget_per_min` in tools/chaos/profiles.json, default 0 (strict).
    Loosening a number there needs a written reason next to it (key `repair_budget_reason`)."""
    import json
    try:
        with open(os.path.join(repo, "tools", "chaos", "profiles.json"), encoding="utf-8") as f:
            p = json.load(f).get(profile, {})
    except (OSError, ValueError):
        return 0.0
    b = float(p.get("repair_budget_per_min", 0.0))
    if b > 0 and not p.get("repair_budget_reason"):
        raise ValueError(f"profiles.json: {profile} loosens repair_budget_per_min to {b} without a repair_budget_reason")
    return b


def relaxed_options(profile: Optional[str], repo: str = "") -> Dict[str, Any]:
    """On an impaired link transient digest mismatches/repairs are allowed while traffic is in flight (strict_counters off for
    the per-step check), but every step ends with a quiescence check (0 repairs in a quiet window, all converged) and the
    run has a repairs-per-minute budget. applyFailed, badMessages and rebases still fail immediately."""
    if not profile or profile == "lan":
        return {}
    t = SETTLE_S.get(profile, 60.0)
    o = {"strict_counters": False, "settle_s": t, "barrier_s": t, "stage_s": t, "ui_s": t,
         "quiesce_s": QUIESCE_S.get(profile, 6.0), "repair_budget_per_min": 0.0}
    if repo:
        o["repair_budget_per_min"] = repair_budget(repo, profile)
    return o
