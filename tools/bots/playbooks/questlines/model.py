"""The expected result of a stage, computed from the plan alone (no world, no bots).

The playbook driver asks `Model.apply(quest, stage)` BEFORE it acts and then checks every machine against the answer. The fake world
implements the same rules a second time on its own (fake.py), so a disagreement between the two is a bug in one of them and a bug in the
replication (a twin) shows up as a machine that does not match the model.

Rules modelled (docs/protocol.md section 12 / 13, internal lane notes):
  * a stage fires its UNCONDITIONAL effects once; asking again changes nothing,
  * Barrier / ReadyCheck hold the request until everybody passed / agreed; `held` lists what the driver has to release,
  * ExclusiveStages: the first committed outcome of a group wins, the others are dropped,
  * rewards (items to the player, XP) go to EVERY player once; karma is one shared number.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Set, Tuple

from .plan import Plan, key

MAX_DEPTH = 24


@dataclass
class Result:
    stages: List[Tuple[str, int]] = field(default_factory=list)          # newly done, in commit order
    held: List[Tuple[str, int, str]] = field(default_factory=list)       # (quest, stage, 'barrier' | 'ready') waiting for the party
    dropped: List[Tuple[str, int]] = field(default_factory=list)         # exclusive outcomes that lost
    obj: Dict[Tuple[str, int], Dict[str, int]] = field(default_factory=dict)
    items: Dict[str, int] = field(default_factory=dict)                  # item ref -> count each player gains ('xp' = experience)
    karma: int = 0
    enabled: Dict[str, bool] = field(default_factory=dict)
    dead: List[str] = field(default_factory=list)
    quests: Dict[str, str] = field(default_factory=dict)                 # quest key -> start | stop | complete

    def merge(self, o: "Result") -> None:
        self.stages += o.stages
        self.held += o.held
        self.dropped += o.dropped
        for k, v in o.obj.items():
            self.obj.setdefault(k, {}).update(v)
        for k, v in o.items.items():
            self.items[k] = self.items.get(k, 0) + v
        self.karma += o.karma
        self.enabled.update(o.enabled)
        self.dead += [d for d in o.dead if d not in self.dead]
        self.quests.update(o.quests)


class Model:
    def __init__(self, plan: Plan):
        self.plan = plan
        self.done: Set[Tuple[str, int]] = set()
        self.winner: Dict[str, int] = {}
        self.released: Set[Tuple[str, int]] = set()
        self.karma = 0
        self.dead: Set[str] = set()

    def apply(self, qk: str, n: int, released: bool = False, _depth: int = 0) -> Result:
        out = Result()
        if (qk, n) in self.done or _depth > MAX_DEPTH:
            return out
        grp = self.plan.exclusive_group(qk, n)
        if grp is not None and self.winner.get(qk, n) != n:
            out.dropped.append((qk, n))
            return out
        if not released and (qk, n) not in self.released:
            if self.plan.barrier_holds(qk, n):
                out.held.append((qk, n, "barrier"))
                return out
            if self.plan.is_ready_stage(qk, n):
                out.held.append((qk, n, "ready"))
                return out
        self.done.add((qk, n))
        if grp is not None:
            self.winner[qk] = n
        out.stages.append((qk, n))
        fx = self.plan.fx(qk, n)
        for q, o, kind, v in fx.get("obj", []):
            out.obj.setdefault((key(q), o), {})[kind] = v
        for item, c in fx.get("items", []):
            out.items[item] = out.items.get(item, 0) + c
        if fx.get("xp"):
            out.items["xp"] = out.items.get("xp", 0) + fx["xp"]
        if fx.get("karma"):
            out.karma += fx["karma"]
            self.karma += fx["karma"]
        for r in fx.get("enable", []):
            out.enabled[r] = True
        for r in fx.get("disable", []):
            out.enabled[r] = False
        for r in fx.get("kill", []):
            if r not in out.dead:
                out.dead.append(r)
            self.dead.add(r)
        for q, what in fx.get("quest", []):
            out.quests[key(q)] = what
        for q, s in fx.get("stage", []):
            out.merge(self.apply(key(q), s, _depth=_depth + 1))
        return out

    def release(self, qk: str, n: int) -> Result:
        """The party passed the barrier / agreed to the ready check of (qk, n): now it fires."""
        self.released.add((qk, n))
        return self.apply(qk, n, released=True)
