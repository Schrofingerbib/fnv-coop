"""Loading and checking the questline playthrough plans (questlines/<line>/playthrough.json, written by tools/questsweep/gen_plans.py).

Plans are committed, so loading one needs no game data. `Plan` gives the playbook driver, the fake world and the model the same view
of the data (they must agree on what a stage does, otherwise the playbook would test the fake against itself).
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(HERE))))

FX_KEYS = {"obj", "stage", "quest", "items", "xp", "karma", "enable", "disable", "kill"}
RID = re.compile(r"^[0-9A-F]{8}$")
REF = re.compile(r"^[A-Za-z0-9_.]+\.es[mp]:[0-9A-F]{6}$")


def key(rid: str) -> str:
    """The quest key of the fake world and the probes: runtime id without leading zeros."""
    return rid.upper().lstrip("0") or "0"


def plan_file(line: str, repo: str = REPO) -> str:
    return os.path.join(repo, "questlines", line, "playthrough.json")


class Plan:
    def __init__(self, data: Dict[str, Any]):
        self.data = data
        self.line: str = data["line"]
        self.quests: List[Dict[str, Any]] = data["quests"]
        self.by_key: Dict[str, Dict[str, Any]] = {key(q["id"]): q for q in self.quests}
        self.stage_rows: Dict[Tuple[str, int], Dict[str, Any]] = {(key(q["id"]), s["index"]): s for q in self.quests for s in q["stages"]}
        self.ready: Dict[Tuple[str, int], str] = {(key(r["quest"]), int(r["stage"])): r["info"] for r in data.get("ready_stages", [])}

    @classmethod
    def load(cls, line: str, repo: str = REPO) -> "Plan":
        with open(plan_file(line, repo), encoding="utf-8") as f:
            return cls(json.load(f))

    # ---- what a stage does ----
    def fx(self, qk: str, stage: int) -> Dict[str, Any]:
        row = self.stage_rows.get((qk, stage))
        return row["fx"] if row else {}

    def rules(self, qk: str) -> Optional[Dict[str, Any]]:
        q = self.by_key.get(qk)
        return q["rules"] if q else None

    def barrier_holds(self, qk: str, stage: int) -> bool:
        r = self.rules(qk)
        b = r and r.get("barrier")
        return bool(b == "all" or (isinstance(b, list) and stage in b))

    def exclusive_group(self, qk: str, stage: int) -> Optional[List[int]]:
        r = self.rules(qk)
        for g in (r or {}).get("exclusive", []):
            if stage in g:
                return g
        return None

    def guest_local(self, qk: str) -> bool:
        r = self.rules(qk)
        return bool(r and r.get("guest_local"))

    def is_ready_stage(self, qk: str, stage: int) -> bool:
        return (qk, stage) in self.ready

    # ---- every id the plan mentions (the fake world creates these) ----
    def all_quest_keys(self) -> List[str]:
        ks = set(self.by_key)
        for (qk, n), row in self.stage_rows.items():
            for s in row["fx"].get("stage", []):
                ks.add(key(s[0]))
            for s in row["fx"].get("quest", []):
                ks.add(key(s[0]))
            for o in row["fx"].get("obj", []):
                ks.add(key(o[0]))
        return sorted(ks)

    def stats(self) -> Dict[str, int]:
        return dict(self.data.get("stats", {}))


def available_lines(repo: str = REPO) -> List[str]:
    from_lines = []
    base = os.path.join(repo, "questlines")
    for d in sorted(os.listdir(base)):
        if os.path.exists(os.path.join(base, d, "playthrough.json")):
            from_lines.append(d)
    return from_lines


def check_plan(data: Dict[str, Any]) -> List[str]:
    """Self-consistency problems of a plan (empty = fine). Game-free."""
    bad: List[str] = []
    if data.get("schema") != 1 or data.get("kind") != "questline-playthrough":
        bad.append("not a schema 1 questline-playthrough")
        return bad
    if not data.get("quests"):
        bad.append("plan has no quests")
    seen = set()
    for q in data.get("quests", []):
        e = q.get("edid", "?")
        if not RID.match(q.get("id", "")):
            bad.append(f"{e}: id {q.get('id')!r} is not an 8 digit runtime id")
        if q.get("id") in seen:
            bad.append(f"{e}: quest listed twice")
        seen.add(q.get("id"))
        idx = [s["index"] for s in q.get("stages", [])]
        if not idx:
            bad.append(f"{e}: no stages")
        if len(set(idx)) != len(idx):
            bad.append(f"{e}: duplicate stage index")
        for s in q.get("stages", []):
            fx = s.get("fx", {})
            extra = set(fx) - FX_KEYS
            if extra:
                bad.append(f"{e} stage {s['index']}: unknown fx keys {sorted(extra)}")
            for o in fx.get("obj", []):
                if not (RID.match(o[0]) and o[2] in ("displayed", "completed") and o[3] in (0, 1)):
                    bad.append(f"{e} stage {s['index']}: bad objective effect {o}")
            for it in fx.get("items", []):
                if not (REF.match(it[0]) and it[1] > 0):
                    bad.append(f"{e} stage {s['index']}: bad item effect {it}")
            for r in fx.get("enable", []) + fx.get("disable", []) + fx.get("kill", []):
                if not REF.match(r):
                    bad.append(f"{e} stage {s['index']}: bad ref {r}")
            for st in fx.get("stage", []):
                if not (RID.match(st[0]) and isinstance(st[1], int)):
                    bad.append(f"{e} stage {s['index']}: bad stage effect {st}")
            for a in s.get("act", []):
                if a["verb"] not in ("kill", "talk", "walk", "take") or not a["refs"]:
                    bad.append(f"{e} stage {s['index']}: bad action {a}")
        r = q.get("rules", {})
        for grp in r.get("exclusive", []):
            if len(grp) < 2:
                bad.append(f"{e}: exclusive group with fewer than two stages {grp}")
        b = r.get("barrier")
        if b not in (None, "all") and not isinstance(b, list):
            bad.append(f"{e}: barrier must be null, 'all' or a stage list")
    return bad
