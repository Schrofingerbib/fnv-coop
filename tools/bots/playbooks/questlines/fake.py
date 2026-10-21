"""A fake co-op world that runs a questline plan: what the T6b fake (fakecoop.py) does for the Goodsprings opening, for any plan.

QuestlineFake(FakeCoop) commits the unconditional effects of each stage as the host would (objectives, rewards to every player, karma,
enable / disable / kill, other quests started or advanced) and applies the co-op rules of the plan the way docs/protocol.md section 13
describes them: Barrier and ReadyCheck hold a request, ExclusiveStages lets the first outcome win, a guest-local quest never replicates and
runs on every machine by itself, per-player quest variables keep one copy per slot, companions respect the follower caps.

It implements the rules a second time, on its own, next to questlines/model.py (which the driver checks every machine against), and
`qbugs` switches on deliberately wrong behaviour (a mutation twin: the matching check must fail). Everything here is a model of the
rules, NOT of the engine; nothing about the real game is proven by it.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from .. import fakecoop
from ..fakecoop import FakeBot, FakeCoop, norm
from ..probe import Result, hexid
from .plan import Plan, key

QL_BUGS = {
    "exclusive_ignored": "a later outcome of an ExclusiveStages set is accepted (the highest number wins instead of the first)",
    "ready_skipped": "a ready-check stage fires without asking the party",
    "barrier_ignored": "a barrier quest's stage fires without waiting for the players",
    "guestlocal_replicated": "the host's run of a guest-local quest is replicated to the clients",
    "guestlocal_clients_blocked": "clients cannot run a guest-local quest themselves (its script is suppressed on guests)",
    "reward_to_host_only": "quest rewards are delivered to the host's player only",
    "reward_twice": "quest rewards are delivered twice",
    "no_world_sync": "EnableRef / DisableRef never reach the clients",
    "karma_local": "karma changes stay on the host",
    "no_quest_run_sync": "StartQuest / StopQuest never reach the clients",
    "cross_stage_dropped": "a stage that sets a stage of another quest does not set it",
    "pvars_shared": "per-player quest variables are one shared copy",
    "no_follower_cap": "the follower caps are not enforced (a player can recruit any number of companions)",
    "recruit_not_exclusive": "a companion that follows one player can be recruited by another at the same time",
}
ALL_BUGS = {**fakecoop.BUGS, **QL_BUGS}
KARMA_KEY = norm("global:karma")
CAPS = {"humanoid": 1, "critter": 1}
PARTY_TOTAL = 4


class QBot(FakeBot):
    """One machine of the questline fake: adds guest-local runs, per-player vars, karma and the companion caps."""

    def set_stage(self, quest, stage):
        w: "QuestlineFake" = self.world
        if w.plan.guest_local(hexid(quest)) and not (self.idx == 0 and "guestlocal_replicated" in w.qbugs):
            return w.local_stage(self.idx, hexid(quest), int(stage))
        return super().set_stage(quest, stage)

    def start_quest(self, quest):
        w: "QuestlineFake" = self.world
        if w.plan.guest_local(hexid(quest)):
            return w.local_stage(self.idx, hexid(quest), None)
        return super().start_quest(quest)

    def set_player_var(self, quest, var, value):
        w: "QuestlineFake" = self.world
        slots = range(w.n) if "pvars_shared" in w.qbugs else [self.idx]
        for s in slots:
            w.pvars[(s, hexid(quest), var)] = value
        return Result(True)

    def player_var(self, quest, var):
        return self.world.pvars.get((self.idx, hexid(quest), var), 0)

    def xp(self) -> int:
        return int(self.me["inv"].get("xp", 0))

    def karma(self) -> int:
        return int(self.view["refs"].get(KARMA_KEY, {}).get("value", 0))

    def pick_topic(self, info_id, timeout_s=10):
        w: "QuestlineFake" = self.world
        if not self.me["dialogue"]:
            return Result(False, "not in dialogue")
        k = self.me["dialogue"]
        eff = w.truth["refs"][k].get("info_effects", {}).get(hexid(info_id))
        if eff not in ("recruit", "dismiss"):
            return super().pick_topic(info_id, timeout_s)
        w.set_ref(k, last_info=hexid(info_id))
        r = w.truth["refs"][k]
        if eff == "dismiss":
            if r["follower_of"] == self.idx:
                w.set_ref(k, follower_of=None, owner=0)
            return Result(True)
        if r["follower_of"] is not None and "recruit_not_exclusive" not in w.qbugs:
            return Result(True, "already follows someone")
        mine = [x for x in w.truth["refs"].values() if x.get("follower_of") == self.idx]
        same = [x for x in mine if x.get("ckind") == r.get("ckind")]
        if "no_follower_cap" not in w.qbugs and (len(same) >= CAPS.get(r.get("ckind", "humanoid"), 1) or len(mine) >= PARTY_TOTAL):
            return Result(True, "follower cap reached")
        w.set_ref(k, follower_of=self.idx, owner=self.idx)
        return Result(True)


class QuestlineFake(FakeCoop):
    def __init__(self, plan: Plan, n: int = 2, bugs: Optional[Iterable[str]] = None, **kw):
        bugs = set(bugs or ())
        unknown = bugs - set(ALL_BUGS)
        if unknown:
            raise ValueError(f"unknown fake bugs {sorted(unknown)}")
        super().__init__(n, {b for b in bugs if b in fakecoop.BUGS}, **kw)
        self.qbugs = {b for b in bugs if b in QL_BUGS}
        self.plan = plan
        self.bots = [QBot(self, i) for i in range(n)]
        self.winner: Dict[str, int] = {}
        self.exclusive_rejected = 0
        self.reward_seq = 0
        self.karma = 0
        self.pvars: Dict[Tuple[int, str, str], Any] = {}
        self.local_log: List[Tuple[int, str, Optional[int]]] = []       # (slot, quest, stage) runs that were NOT replicated
        self.pending_released: Set[Tuple[str, int]] = set()
        self.early_passes: Dict[str, Set[int]] = {}

    # ---- stages ----
    def request_stage(self, q: str, n: int) -> Result:
        qk = hexid(q)
        if n in self.truth["quests"].get(qk, {}).get("stages", []):
            return Result(True, "already done")
        if (qk, n) not in self.deferred:
            if self.plan.barrier_holds(qk, n) and "barrier_ignored" not in self.qbugs:
                self.barriers.add((qk, n))
            if self.plan.is_ready_stage(qk, n) and "ready_skipped" not in self.qbugs:
                self.readychecks.add((qk, n))
        res = super().request_stage(qk, n)
        d = self.deferred.get((qk, n))
        if d is not None and d["kind"] == "barrier" and self.early_passes.get(qk):
            d["passed"] |= self.early_passes.pop(qk)       # players that finished before the host asked (consumed, like the engine's earlyPasses_)
            if len(d["passed"]) >= self.n:
                self._release((qk, n))
        return res

    def pass_scene(self, slot: int, q: str) -> Result:
        q = hexid(q)
        for k, d in list(self.deferred.items()):
            if k[0] == q and d["kind"] == "barrier":
                d["passed"].add(slot)
                if "pnr_early" in self.bugs or len(d["passed"]) >= self.n:
                    self._release(k)
                return Result(True)                          # the engine resolves one pending barrier per pass
        self.early_passes.setdefault(q, set()).add(slot)
        return Result(True)

    def vote(self, slot: int, q: str, yes: bool) -> Result:
        res = super().vote(slot, q, yes)
        for store in [self.truth] + self.views:                 # the veto note is a bare ref in the T6b fake: give it the fields the movement code reads
            r = store["refs"].get("message:veto")
            if r is not None and "kind" not in r:
                r.update(kind="message", dead=False, pos=[0.0, 0.0, 0.0], cell="world", enabled=True)
        return res

    def _do_stage(self, q: str, n: int) -> None:
        if n in self.truth["quests"].get(q, {}).get("stages", []):
            return
        if self.plan.guest_local(q) and "guestlocal_replicated" not in self.qbugs:
            self.local_stage(0, q, n)            # the host's run of a guest-local quest stays on the host
            return
        grp = self.plan.exclusive_group(q, n)
        if grp is not None and "exclusive_ignored" not in self.qbugs:
            win = self.winner.get(q)
            if win is not None and win != n:
                self.exclusive_rejected += 1
                return
            self.winner[q] = n
        self.commit({"op": "stage", "q": q, "n": n})
        fx = self.plan.fx(q, n)
        for oq, o, kind, v in fx.get("obj", []):
            self.commit({"op": "obj", "q": hexid(oq), "o": o, "f": {kind: bool(v)}})
        for item, c in fx.get("items", []):
            self._reward(item, c)
        if fx.get("xp"):
            self._reward("xp", fx["xp"])
        if fx.get("karma"):
            self.karma += fx["karma"]
            self.commit({"op": "ref", "ref": KARMA_KEY, "f": {"value": self.karma}})
        for r in fx.get("enable", []):
            self.commit({"op": "ref", "ref": norm(r), "f": {"enabled": True}})
        for r in fx.get("disable", []):
            self.commit({"op": "ref", "ref": norm(r), "f": {"enabled": False}})
        for r in fx.get("kill", []):
            self.commit({"op": "ref", "ref": norm(r), "f": {"dead": True, "hp": 0, "deaths": 1}})
        for q2, what in fx.get("quest", []):
            self.commit({"op": "quest", "q": hexid(q2), "running": what == "start"})
        for q2, s2 in fx.get("stage", []):
            if "cross_stage_dropped" not in self.qbugs:
                self.request_stage(hexid(q2), s2)

    def _release(self, key_) -> None:
        self.deferred.pop(key_, None)
        self.pending_released.add(key_)
        self._do_stage(*key_)

    def _reward(self, item: str, count: int) -> None:
        self.reward_seq += 1
        self.commit({"op": "reward", "id": self.reward_seq, "item": item, "count": count})

    def _apply(self, view: dict, eff: dict, client: bool, who: int) -> None:
        op, qb = eff["op"], self.qbugs
        if client:
            if op == "reward" and "reward_to_host_only" in qb:
                return
            if op == "quest" and "no_quest_run_sync" in qb:
                return
            if op == "ref" and eff["ref"] == KARMA_KEY and "karma_local" in qb:
                return
            if op == "ref" and "no_world_sync" in qb and "enabled" in eff["f"] and not eff.get("spawn"):
                eff = dict(eff, f={k: v for k, v in eff["f"].items() if k != "enabled"})
        super()._apply(view, eff, client, who)
        if op == "reward" and "reward_twice" in qb:
            super()._apply(view, dict(eff, id=("dup", eff["id"])), client, who)

    # ---- guest-local quests: every machine runs its own copy, nothing is replicated ----
    def local_stage(self, slot: int, q: str, n: Optional[int]) -> Result:
        if slot != 0 and "guestlocal_clients_blocked" in self.qbugs:
            return Result(False, "guest scripts do not run: this quest's script is suppressed on guests")
        with self._lock:
            for view in ([self.views[slot], self.truth] if slot == 0 else [self.views[slot]]):
                qd = view["quests"].setdefault(q, {"stages": [], "running": False})
                qd["running"] = True
                if n is not None and n not in qd["stages"]:
                    qd["stages"] = sorted(qd["stages"] + [n])
            if n is not None:
                inv = self.players[slot]["inv"]
                for item, c in self.plan.fx(q, n).get("items", []):
                    inv[item] = inv.get(item, 0) + c                    # a guest-local reward is the machine's own: it goes to its own player
                if self.plan.fx(q, n).get("xp"):
                    inv["xp"] = inv.get("xp", 0) + self.plan.fx(q, n)["xp"]
                for oq, o, kind, v in self.plan.fx(q, n).get("obj", []):
                    for view in ([self.views[slot], self.truth] if slot == 0 else [self.views[slot]]):
                        view["objectives"].setdefault(hexid(oq), {}).setdefault(str(o), {}).update({kind: bool(v)})
            self.local_log.append((slot, q, n))
        return Result(True)

    # ---- helpers for tests ----
    def stage_everywhere(self, q: str, n: int) -> Dict[str, bool]:
        return {b.name: b.stage_done(q, n) for b in self.bots}


def build_fake(plan: Plan, n: int = 2, bugs: Optional[Iterable[str]] = None, **kw) -> Tuple[QuestlineFake, dict]:
    kw.setdefault("walk_speed", None)
    w = QuestlineFake(plan, n, bugs, **kw)
    for qk in plan.all_quest_keys():
        w.add_quest(qk)
    refs: Dict[str, str] = {"weapon": "WEAPON"}
    kinds: Dict[str, str] = {}
    initial: Dict[str, bool] = {}
    order: List[str] = []

    def want(ref: str, kind: str, enabled: bool = True) -> None:
        if ref not in kinds:
            kinds[ref] = kind
            initial[ref] = enabled
            order.append(ref)

    KIND = {"kill": "raider", "talk": "actor", "take": "item", "walk": "activator"}
    for q in plan.quests:
        for s in q["stages"]:
            fx = s["fx"]
            for r in fx.get("enable", []):
                want(r, "activator", False)
            for r in fx.get("disable", []):
                want(r, "activator", True)
            for r in fx.get("kill", []):
                want(r, "actor", True)
            for a in s.get("act", []):
                for r in a["refs"]:
                    want(r, KIND[a["verb"]], True)
    speakers: Dict[str, str] = {}
    for qi, q in enumerate(plan.quests):
        for s in q["stages"]:
            t = s.get("trigger")
            if t:
                sp = t.get("speaker_ref") or speakers.setdefault(q["id"], f"Speaker.esm:{0xA00000 + qi:06X}")
                want(sp, "actor", True)
    w.add_ref("global:karma", "global", pos=(0.0, 0.0, 0.0), cell="world", value=0)
    for i, r in enumerate(order):
        pos = (150.0 * (i % 6), 150.0 * ((i // 6) % 6), 0.0)
        extra = {"base": f"obj:{r}"}
        if kinds[r] == "raider":
            extra["hp"] = 40
        w.add_ref(r, kinds[r], pos=pos, cell="world", enabled=initial[r], **extra)
    for qi, q in enumerate(plan.quests):
        for s in q["stages"]:
            t = s.get("trigger")
            if t and (key(q["id"]), s["index"]) in plan.stage_rows:
                sp = t.get("speaker_ref") or speakers[q["id"]]
                w.truth["refs"][norm(sp)].setdefault("info_effects", {})[hexid(t["info"])] = (key(q["id"]), s["index"])
                for v in w.views:
                    v["refs"][norm(sp)].setdefault("info_effects", {})[hexid(t["info"])] = (key(q["id"]), s["index"])
                w.truth["refs"][norm(sp)]  # exists
    for c in (plan.data.get("companions") or {}).get("companions", []):
        if not c.get("ref"):
            continue
        k = norm(c["ref"])
        if k not in w.truth["refs"]:
            w.add_ref(c["ref"], "actor", pos=(0.0, 300.0, 0.0), cell="world")
        ie = {}
        if c.get("recruit_info"):
            ie[hexid(c["recruit_info"])] = "recruit"
        if c.get("dismiss_info"):
            ie[hexid(c["dismiss_info"])] = "dismiss"
        for store in [w.truth] + w.views:
            r = store["refs"][k]
            r.update(base=c["name"], ckind=c.get("kind", "humanoid"))
            r.setdefault("info_effects", {}).update(ie)
    for s in range(n):
        w.set_player(s, pos=(0, 0, 0), cell="world")
    return w, refs
