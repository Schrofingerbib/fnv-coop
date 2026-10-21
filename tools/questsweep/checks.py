"""Consistency checks between the plans, the lane sweep configs and rules.json (game-free; the tests and `--check` use them)."""
from __future__ import annotations

import os
import sys
from typing import Any, Dict, List

from . import common, rulesview
from .lines import BY_SLUG

sys.path.insert(0, os.path.join(common.REPO, "plugin", "snapshot", "tools"))


def lane_stages(slug: str) -> Dict[str, set]:
    """quest edid -> stage indexes (swept and excluded) of the lane's sweep config, read with sweep_config.load_config."""
    import sweep_config as sc
    line = BY_SLUG[slug]
    path = os.path.join(common.REPO, "questlines", line.sweep or slug, "sweep.json")
    plan = sc.load_config(path)
    return {q["edid"]: {s["index"] for s in q["stages"]} | {e["index"] for e in q["excluded"]} for q in plan["quests"]
            if q["stages"] or q["excluded"]}


def coverage_gaps(plan: Dict[str, Any], lane: Dict[str, set]) -> List[str]:
    """Every stage the lane sweeps (or excludes) must be a stage of the plan."""
    have = {q["edid"]: {s["index"] for s in q["stages"]} for q in plan["quests"]}
    gaps = []
    for edid, idx in sorted(lane.items()):
        if edid not in have:
            gaps.append(f"{edid}: quest missing from the plan")
        elif idx - have[edid]:
            gaps.append(f"{edid}: stages {sorted(idx - have[edid])} missing")
    return gaps


def rules_drift(plan: Dict[str, Any], rules: Dict[str, Any]) -> List[str]:
    """The rules block of every plan quest must equal what rules.json says now."""
    bad = []
    for q in plan["quests"]:
        now = rulesview.view(rules, q["edid"])
        if now != q["rules"]:
            diff = sorted(k for k in now if now[k] != q["rules"].get(k))
            bad.append(f"{q['edid']}: plan rules differ from rules.json in {diff}")
    return bad


def guest_local_gaps(rules: Dict[str, Any], guest: Dict[str, Any]) -> List[str]:
    """Every script of plugin/hooks/data/guest_local_run.json must be listed by a GuestLocalRun rule of rules.json."""
    listed = set()
    for q in rules.values():
        for r in q["rules"]:
            if r["kind"] == "GuestLocalRun":
                listed |= set(r.get("params", {}).get("scripts", []))
    return [f"{it['quest']}: script {it['script']} is not in any GuestLocalRun rule" for it in guest["scripts"] if it["script"] not in listed]
