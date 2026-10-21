"""What the playbooks need to know about a quest's co-op rules (docs/quest-policies/rules.json), in one small dict.

The same function builds the `rules` block of every committed plan and is used by the drift test (a plan whose rules block no longer
matches rules.json fails), so a rules change cannot silently leave the playbooks asserting the old behaviour.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from . import common


def load_rules(path: Optional[str] = None) -> Dict[str, Any]:
    return common.load_json(path or common.RULES_JSON)["quests"]


def _ints(v) -> list:
    return [int(x) for x in (v or [])]


def view(rules: Dict[str, Any], edid: str) -> Dict[str, Any]:
    """Keys: guest_local (bool), barrier (None | 'all' | [stages]), ready (infos), exclusive ([[stages]]), vars ([names]),
    reward_all, item_dup, initiator_dialogue, companion_owner, tether (None | {...}), slideshow (bool)."""
    q = rules.get(edid) or {}
    out: Dict[str, Any] = {"guest_local": False, "barrier": None, "ready": [], "exclusive": [], "vars": [],
                           "reward_all": False, "item_dup": False, "initiator_dialogue": False, "companion_owner": False,
                           "tether": None, "slideshow": False}
    for r in q.get("rules", []):
        k, p = r["kind"], r.get("params") or {}
        if k == "GuestLocalRun":
            out["guest_local"] = True
        elif k == "PerPlayerScene":
            if p.get("run") == "every-machine" and p.get("scope") == "local-machine":
                out["guest_local"] = True
        elif k == "Barrier":
            st = _ints(p.get("stages"))
            out["barrier"] = st if st else "all"
            if p.get("before") == "slideshow":
                out["slideshow"] = True
        elif k == "ReadyCheck":
            out["ready"] += [str(i) for i in p.get("infos", [])]
        elif k == "ExclusiveStages":
            out["exclusive"].append(_ints(p.get("stages")))
        elif k == "PerPlayerVars":
            out["vars"] += [str(v) for v in p.get("vars", [])]
        elif k == "RewardAll":
            out["reward_all"] = True
        elif k == "QuestItemDup":
            out["item_dup"] = True
        elif k == "InitiatorDialogue":
            out["initiator_dialogue"] = True
        elif k == "CompanionOwner":
            out["companion_owner"] = True
        elif k == "PartyTether":
            out["tether"] = {"cells": [str(c) for c in p.get("cells", [])], "anchor": p.get("anchor"), "radius": p.get("radius"),
                             "stages": _ints(p.get("stages"))}
    return out


def barrier_holds(v: Dict[str, Any], stage: int) -> bool:
    b = v.get("barrier")
    return b == "all" or (isinstance(b, list) and stage in b)
