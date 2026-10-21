"""The questline playthrough playbook: drives a plan (questlines/<line>/playthrough.json) through every stage with 2 to 4 bots.

For each quest, in the lane's order: start it, then for every stage
  1. ask the model (model.py) what must happen, and note every player's item counts,
  2. make it happen the way the plan says: a player picks the trigger dialogue INFO (natural), or the host sets the stage (console),
     a barrier quest waits for every player to pass, a ready check is vetoed once by a guest and then agreed, an exclusive
     outcome that lost is attempted and must be refused, a guest-local quest is run by every machine itself and must not replicate,
  3. wait until every machine shows the stage, settle, and check on EVERY machine: stages, objective flags, item rewards (each player
     gains exactly the reward once), enabled / killed refs, started quests, karma,
  4. do what the displayed objectives ask (kill, talk, walk, take) with the bots, and check the world agrees.
The engine (core.py) then compares all machines with snapdiff after each quest step and checks the desync counters did not rise.

What is only a model: the fake world implements the rules from docs/protocol.md; real game runs are separate (see docs/tools/questline-sweeps.md).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

from .. import probe as P
from ..core import Ctx, Machine, Playbook, StepFailure
from .model import Model, Result
from .plan import Plan, key

STAGE_S = 15.0
BARRIER_S = 30.0
VOLLEYS = 15


def _pvar_set(bot, q: str, var: str, value: int):
    if hasattr(bot, "set_player_var"):
        return bot.set_player_var(q, var, value)
    r = P._rpc(bot, "coop.playerVar.set", {"quest": P.fullref(q), "var": var, "value": value})
    if not r.ok:
        raise P.MissingBridge(r.reason)
    return r


def _pvar_get(bot, q: str, var: str) -> int:
    if hasattr(bot, "player_var"):
        return int(bot.player_var(q, var))
    r = P._rpc(bot, "coop.playerVar.get", {"quest": P.fullref(q), "var": var})
    if not r.ok:
        raise P.MissingBridge(r.reason)
    return int(r.data["result"].get("value", 0))


def _karma(bot) -> Optional[int]:
    return int(bot.karma()) if hasattr(bot, "karma") else None


class Driver:
    def __init__(self, ctx: Ctx, plan: Plan):
        self.ctx, self.plan = ctx, plan
        self.model = Model(plan)
        o = ctx.opts
        fake = ctx.clock.kind == "sim"
        self.actions = bool(o.get("ql_actions", True))
        self.natural = bool(o.get("ql_natural", fake))
        self.include_excluded = bool(o.get("ql_include_excluded", fake))
        self.strict_actions = bool(o.get("ql_strict_actions", fake))
        self.raw_setup = bool(o.get("ql_raw_setup", False))
        self.only = set(o.get("ql_quests") or [])
        self.turn = 0
        self.last_dialogue: Optional[Machine] = None
        self.dead: Set[str] = set()
        self.taken: Set[str] = set()
        self.started: Set[str] = set()
        self.stats = {"stages": 0, "natural": 0, "console": 0, "barrier": 0, "ready": 0, "exclusive_refused": 0, "guest_local": 0,
                      "actions": 0, "skipped_excluded": 0, "no_op": 0}

    # ------------------------------------------------------------------ small helpers
    @property
    def ms(self) -> List[Machine]:
        return self.ctx.present

    def initiator(self) -> Machine:
        m = self.ms[self.turn % len(self.ms)]
        self.turn += 1
        return m

    def fail(self, what: str, kind: str = "check") -> None:
        raise StepFailure(what, kind)

    def stage_everywhere(self, qk: str, n: int) -> Dict[str, bool]:
        return {m.name: P.stage_done(m.bot, qk, n) for m in self.ms}

    @staticmethod
    def count(bot, item: str) -> Optional[int]:
        """Items the player holds; 'xp' only for bots that can read it (None: not checked)."""
        if item == "xp":
            return int(bot.xp()) if hasattr(bot, "xp") else None
        return P.item_count(bot, item)

    def snap(self, before: Dict[str, Dict[str, Optional[int]]], items: Dict[str, int]) -> None:
        for m in self.ms:
            for i in items:
                if i not in before[m.name]:
                    before[m.name][i] = self.count(m.bot, i)

    def counts(self, items: List[str]) -> Dict[str, Dict[str, Optional[int]]]:
        return {m.name: {i: self.count(m.bot, i) for i in items} for m in self.ms}

    def soft(self, res: Any, what: str) -> bool:
        """An action result: strict worlds fail the step, live games note it (a bot cannot always reach a target)."""
        if getattr(res, "ok", res is True):
            return True
        reason = getattr(res, "reason", "") or "no reason given"
        if self.strict_actions:
            self.fail(f"{what}: {reason}", "action")
        self.ctx.notes.append(f"{what}: {reason} (best effort, not a failure in a real game)")
        return False

    # ------------------------------------------------------------------ the run
    def run(self) -> None:
        plan, ctx = self.plan, self.ctx
        quests = [q for q in plan.quests if not self.only or q["edid"] in self.only]
        for q in quests:
            todo = [s for s in q["stages"] if not (s.get("excluded") and not self.include_excluded)]
            self.stats["skipped_excluded"] += len(q["stages"]) - len(todo)
            if not todo:
                continue
            with ctx.step(f"{q['edid']}: {len(todo)} stage(s)" + (" [guest-local]" if q["rules"]["guest_local"] else "")):
                try:
                    self.quest(q, todo)
                except StepFailure as e:
                    raise StepFailure(f"{q['edid']}: {e.reason}", e.kind) from None
        self.final_check()

    def final_check(self) -> None:
        ctx, plan = self.ctx, self.plan
        with ctx.step("whole line: every machine agrees on every stage and nothing leaked"):
            for (qk, n) in sorted(self.model.done):
                if plan.guest_local(qk):
                    continue
                got = self.stage_everywhere(qk, n)
                if not all(got.values()):
                    self.fail(f"quest {qk} stage {n} was set but is not done on every machine: {got}")
            ctx.notes.append("questline stats: " + ", ".join(f"{k}={v}" for k, v in self.stats.items()))

    # ------------------------------------------------------------------ one quest
    def quest(self, q: dict, stages: List[dict]) -> None:
        qk = key(q["id"])
        local = q["rules"]["guest_local"]
        self.prereqs(q)
        self.start(q, local)
        first_local = True
        for s in stages:
            n = s["index"]
            self.stats["stages"] += 1
            try:
                if local:
                    self.local_stage(q, s, first_local)
                    first_local = False
                else:
                    self.stage(q, s)
            except StepFailure as e:
                raise StepFailure(f"stage {n}: {e.reason}", e.kind) from None
        if q["rules"]["vars"]:
            self.player_vars(q)
        if not local:
            self.repeat_is_noop(q, stages)

    def prereqs(self, q: dict) -> None:
        host = self.ctx.host
        for p in q.get("prereq", []):
            if p["op"] == "start":
                if key(p["q"]) not in self.started:
                    self.ctx.must(P.start_quest(host.bot, p["q"]), f"host starts prerequisite quest {p['q']}")
                    self.started.add(key(p["q"]))
            elif p["op"] == "stage":
                qk, n = key(p["q"]), p["n"]
                if (qk, n) not in self.model.done and qk in self.plan.by_key and not self.plan.guest_local(qk):
                    self.ctx.must(P.set_stage(host.bot, p["q"], n), f"host sets prerequisite stage {p['q']} {n}")
                    self.model.apply(qk, n, released=True)
                    self.ctx.wait_until(f"prerequisite {p['q']} {n} on every machine",
                                        lambda: all(P.stage_done(m.bot, qk, n) for m in self.ms), timeout_s=self.ctx.opts.get("stage_s", STAGE_S))
            elif p["op"] == "raw" and self.raw_setup:
                host.bot.nv.exec(p["line"])
            else:
                self.ctx.notes.append(f"{q['edid']}: setup line not run ({p.get('line') or p}): only startquest / setstage are replayed")

    def start(self, q: dict, local: bool) -> None:
        ctx, qk = self.ctx, key(q["id"])
        if local:
            for m in self.ms:
                ctx.must(P.start_quest(m.bot, q["id"]), f"{m.name} starts the guest-local quest on its own machine")
            return
        if qk not in self.started:
            ctx.must(P.start_quest(ctx.host.bot, q["id"]), f"host starts {q['edid']}")
            self.started.add(qk)
        ctx.wait_until(f"{q['edid']} running on every machine", lambda: all(P.quest_running(m.bot, qk) for m in self.ms),
                       timeout_s=ctx.opts.get("stage_s", STAGE_S))

    # ------------------------------------------------------------------ one stage
    def stage(self, q: dict, s: dict) -> None:
        ctx, plan, model = self.ctx, self.plan, self.model
        qk, n = key(q["id"]), s["index"]
        if (qk, n) in model.done:                       # an earlier stage already set it: asking again must change nothing
            self.stats["no_op"] += 1
            if plan.barrier_holds(qk, n):                # the engine would hold even this request for a barrier: not worth a vote
                return
            before = {m.name: P.quest_stage(m.bot, qk) for m in self.ms}
            ctx.must(P.set_stage(ctx.host.bot, qk, n), "host sets a stage that is already done")
            ctx.pause(0.2)
            ctx.settle()
            if {m.name: P.quest_stage(m.bot, qk) for m in self.ms} != before:
                self.fail("setting a stage that is already done moved the quest")
            return
        grp = plan.exclusive_group(qk, n)
        if grp is not None and model.winner.get(qk) not in (None, n):
            self.exclusive_loser(q, s)
            return
        trig = s.get("trigger")
        use_dialogue = self.natural and trig is not None and bool(trig.get("speaker_ref") or self.ctx.clock.kind == "sim")
        if plan.is_ready_stage(qk, n) and not use_dialogue:
            model.released.add((qk, n))                  # no INFO path here: the console skips the ready check by design
        res = model.apply(qk, n)
        before: Dict[str, Dict[str, Optional[int]]] = {m.name: {} for m in self.ms}
        self.snap(before, res.items)
        total = Result()
        total.merge(res)
        self.issue(q, s, use_dialogue)
        pending = list(res.held)
        guard = 0
        while pending and guard < 16:
            guard += 1
            hq, hn, kind = pending.pop(0)
            rel = model.release(hq, hn)                  # the prediction first, so the counts are taken before the effects happen
            self.snap(before, rel.items)
            if kind == "barrier":
                self.pass_barrier(hq, hn)
            else:
                self.agree_ready(hq, hn, q, s, use_dialogue, driven=(hq, hn) == (qk, n))
            total.merge(rel)
            pending += rel.held
        self.verify(total, before)
        self.after_stage(q, s)

    def issue(self, q: dict, s: dict, use_dialogue: bool) -> None:
        ctx = self.ctx
        qk, n = key(q["id"]), s["index"]
        if use_dialogue:
            t = s["trigger"]
            speaker = t.get("speaker_ref") or self.synthetic_speaker(q)
            who = self.initiator()
            ctx.must(who.bot.goto_ref(speaker, 150), f"{who.name} walks to the speaker of {t['info']}")
            ctx.must(who.bot.talk(speaker), f"{who.name} talks to {speaker}")
            ctx.must(who.bot.pick_topic(t["info"]), f"{who.name} picks the dialogue line {t['info']}")
            self.last_dialogue = who
            if not self.plan.is_ready_stage(qk, n):
                ctx.must(P.end_dialogue(who.bot), f"{who.name} ends the dialogue")
            self.stats["natural"] += 1
        else:
            ctx.must(P.set_stage(ctx.host.bot, qk, n), f"host sets {q['edid']} stage {n}")
            self.stats["console"] += 1

    def synthetic_speaker(self, q: dict) -> str:
        idx = self.plan.quests.index(q)
        return f"Speaker.esm:{0xA00000 + idx:06X}"

    def pass_barrier(self, hq: str, hn: int) -> None:
        ctx = self.ctx
        self.stats["barrier"] += 1
        ctx.pause(0.5)
        got = self.stage_everywhere(hq, hn)
        if any(got.values()):
            self.fail(f"barrier on {hq} stage {hn}: the stage fired before the players passed: {got}")
        for i, m in enumerate(self.ms):
            ctx.must(P.scene_pass(m.bot, hq), f"{m.name} passes the barrier of {hq}")
            if i < len(self.ms) - 1:
                ctx.pause(0.3)
                got = self.stage_everywhere(hq, hn)
                if any(got.values()):
                    self.fail(f"barrier on {hq} stage {hn}: released after {i + 1} of {len(self.ms)} players passed: {got}")

    def agree_ready(self, hq: str, hn: int, q: dict, s: dict, use_dialogue: bool, driven: bool) -> None:
        ctx = self.ctx
        self.stats["ready"] += 1
        ctx.pause(0.5)
        got = self.stage_everywhere(hq, hn)
        if any(got.values()):
            self.fail(f"ready check on {hq} stage {hn}: the stage fired without asking the party: {got}")
        who = self.last_dialogue or ctx.host
        guests = [m for m in self.ms if m is not who] or self.ms[1:]
        if driven and use_dialogue and len(self.ms) > 1:
            # Veto round: everybody else agrees first and ONE guest answers Not yet last, so a party that counted that answer as an
            # agreement would fire here. Asking again then starts a new vote.
            veto = guests[0]
            for m in self.ms:
                if m is not veto:
                    ctx.optional("ready check answer", lambda m=m: P.consent(m.bot, "ready", hq, "", True))
            r = ctx.optional("ready check veto", lambda: ctx.must(P.consent(veto.bot, "ready", hq, s["trigger"]["info"], False), f"{veto.name} answers Not yet"))
            if r is not None:
                ctx.pause(0.3)
                got = self.stage_everywhere(hq, hn)
                if any(got.values()):
                    self.fail(f"ready check on {hq} stage {hn}: fired although {veto.name} said Not yet: {got}")
                ctx.must(who.bot.pick_topic(s["trigger"]["info"]), f"{who.name} asks again")       # asking again starts a new vote
        for m in self.ms:
            if getattr(m.bot, "consent", None) is not None:
                ctx.optional("ready check answer", lambda m=m: P.consent(m.bot, "ready", hq, "", True))
        if driven and use_dialogue:
            P.end_dialogue(who.bot)

    def exclusive_loser(self, q: dict, s: dict) -> None:
        ctx, model = self.ctx, self.model
        qk, n = key(q["id"]), s["index"]
        model.apply(qk, n)                                       # records nothing: dropped
        ctx.must(P.set_stage(ctx.host.bot, qk, n), "host asks for an outcome that lost")
        ctx.pause(0.4)
        ctx.settle()
        got = self.stage_everywhere(qk, n)
        if any(got.values()):
            self.fail(f"exclusive outcome {n} was accepted after outcome {model.winner.get(qk)} won: {got}")
        self.stats["exclusive_refused"] += 1

    # ------------------------------------------------------------------ guest-local quests
    def local_stage(self, q: dict, s: dict, first: bool) -> None:
        ctx, model = self.ctx, self.model
        qk, n = key(q["id"]), s["index"]
        self.stats["guest_local"] += 1
        res = model.apply(qk, n, released=True)
        track = list(res.items)
        before = self.counts(track)
        host = ctx.host
        ctx.must(P.set_stage(host.bot, qk, n), "host runs the guest-local stage on its own machine")
        ctx.pause(0.3)
        got = {m.name: P.stage_done(m.bot, qk, n) for m in ctx.clients}
        if any(got.values()):
            self.fail(f"the host's run of a guest-local quest reached the clients: {got} (its txns must be dropped)")
        for m in ctx.clients:
            ctx.must(P.set_stage(m.bot, qk, n), f"{m.name} runs the guest-local stage itself")
        ctx.wait_until(f"guest-local {q['edid']} {n} on every machine", lambda: all(P.stage_done(m.bot, qk, n) for m in self.ms),
                       timeout_s=ctx.opts.get("stage_s", STAGE_S))
        ctx.settle()
        for qq, o, kind, v in self.plan.fx(qk, n).get("obj", []):
            if key(qq) != qk:
                continue
            fn = P.objective_displayed if kind == "displayed" else P.objective_completed
            for m in self.ms:
                if fn(m.bot, qk, o) != bool(v):
                    self.fail(f"{m.name}: own copy of objective {o} {kind} should be {bool(v)}")
        for item in track:
            for m in self.ms:
                b0, now = before[m.name][item], self.count(m.bot, item)
                if b0 is not None and now is not None and now - b0 != res.items[item]:
                    self.fail(f"{m.name}: guest-local reward {item} changed by {now - b0}, expected {res.items[item]}")

    # ------------------------------------------------------------------ checks
    def verify(self, total: Result, before: Dict[str, Dict[str, Optional[int]]]) -> None:
        ctx, plan = self.ctx, self.plan
        for qk, n in total.stages:
            if plan.guest_local(qk):
                continue
            ctx.wait_until(f"quest {qk} stage {n} on every machine", lambda qk=qk, n=n: all(P.stage_done(m.bot, qk, n) for m in self.ms),
                           timeout_s=ctx.opts.get("barrier_s", BARRIER_S))
        ctx.settle()
        for qk, n in total.dropped:
            got = self.stage_everywhere(qk, n)
            if any(got.values()):
                self.fail(f"quest {qk} stage {n} lost an exclusive set but is done: {got}")
        for (qk, o), flags in total.obj.items():
            for kind, v in flags.items():
                fn = P.objective_displayed if kind == "displayed" else P.objective_completed
                ctx.same_everywhere(f"objective {o} {kind} of {qk}", lambda m, f=fn, qk=qk, o=o: f(m.bot, qk, o))
                for m in self.ms:
                    if fn(m.bot, qk, o) != bool(v):
                        self.fail(f"{m.name}: objective {o} {kind} of {qk} should be {bool(v)}")
        for item, c in total.items.items():
            for m in self.ms:
                b0, now = before[m.name].get(item), self.count(m.bot, item)
                if b0 is None or now is None:
                    ctx.notes.append(f"reward {item}: this bot cannot read it, not checked")
                    continue
                if now - b0 != c:
                    self.fail(f"{m.name}: reward {item} changed by {now - b0}, every player must get exactly {c}")
        for ref, en in total.enabled.items():
            ctx.same_everywhere(f"{ref} enabled", lambda m, ref=ref: m.bot.ref_enabled(ref))
            for m in self.ms:
                if m.bot.ref_enabled(ref) != en:
                    self.fail(f"{m.name}: {ref} should be {'enabled' if en else 'disabled'}")
        for ref in total.dead:
            ctx.same_everywhere(f"{ref} alive", lambda m, ref=ref: m.bot.target_alive(ref))
            for m in self.ms:
                if m.bot.target_alive(ref):
                    self.fail(f"{m.name}: {ref} should be dead (a script killed it)")
        for qk, what in total.quests.items():
            ctx.same_everywhere(f"quest {qk} running", lambda m, qk=qk: P.quest_running(m.bot, qk))
            if what == "start":
                for m in self.ms:
                    if not P.quest_running(m.bot, qk):
                        self.fail(f"{m.name}: quest {qk} should be running (a stage started it)")
        if total.karma:
            ks = [_karma(m.bot) for m in self.ms]
            if all(k is not None for k in ks):
                if len(set(ks)) != 1 or ks[0] != self.model.karma:
                    self.fail(f"karma differs from the shared total {self.model.karma}: {ks}")
            else:
                ctx.notes.append("karma check skipped: this bot cannot read karma")

    def repeat_is_noop(self, q: dict, stages: List[dict]) -> None:
        ctx = self.ctx
        qk = key(q["id"])
        done = [s["index"] for s in stages if (qk, s["index"]) in self.model.done and not self.plan.barrier_holds(qk, s["index"])]
        if not done:
            return
        n = done[-1]
        snap = {m.name: P.quest_stage(m.bot, qk) for m in self.ms}
        ctx.must(P.set_stage(ctx.host.bot, qk, n), "host repeats the last stage")
        ctx.pause(0.3)
        ctx.settle()
        if {m.name: P.quest_stage(m.bot, qk) for m in self.ms} != snap:
            self.fail(f"repeating stage {n} moved the quest")

    def player_vars(self, q: dict) -> None:
        ctx = self.ctx
        qk = key(q["id"])
        for var in q["rules"]["vars"]:
            try:
                for m in self.ms:
                    ctx.must(_pvar_set(m.bot, qk, var, 10 + m.idx), f"{m.name} sets its own {var}")
                ctx.settle()
                for m in self.ms:
                    got = _pvar_get(m.bot, qk, var)
                    if got != 10 + m.idx:
                        self.fail(f"{m.name}: per-player variable {var} reads {got}, its own value is {10 + m.idx} (the copies leaked into each other)")
            except P.MissingBridge as e:
                ctx.notes.append(f"per-player variable {var} of {q['edid']} not checked: {e}")
                return

    # ------------------------------------------------------------------ what the objectives ask for
    def after_stage(self, q: dict, s: dict) -> None:
        if not self.actions:
            return
        for a in s.get("act", [])[:2]:
            self.act(a)

    def act(self, a: dict) -> None:
        ctx = self.ctx
        verb = a["verb"]
        refs = [r for r in a["refs"] if r not in self.dead]
        if not refs:
            return
        self.stats["actions"] += 1
        if verb == "kill":
            ctx.equip_all()
            for ref in refs:
                if not self.ms[0].bot.target_alive(ref):
                    self.dead.add(ref)
                    continue
                ok = True
                for n, r in ctx.on_all(lambda m, ref=ref: m.bot.goto_ref(ref, 500)).items():
                    ok &= self.soft(r, f"{n} walks to {ref}")
                if not ok:
                    continue
                for _ in range(VOLLEYS):
                    ctx.on_all(lambda m, ref=ref: (m.bot.aim_at(ref), m.bot.fire(1)))
                    if not any(m.bot.target_alive(ref) for m in self.ms):
                        break
                ctx.settle()
                ctx.same_everywhere(f"{ref} alive after the volleys", lambda m, ref=ref: m.bot.target_alive(ref))
                if self.ms[0].bot.target_alive(ref):
                    if self.strict_actions:
                        self.fail(f"{ref} is still alive after {VOLLEYS} volleys")
                    ctx.notes.append(f"{ref} survived {VOLLEYS} volleys (a bot may not reach it)")
                else:
                    self.dead.add(ref)
        elif verb == "walk":
            for ref in refs[:1]:
                for n, r in ctx.on_all(lambda m, ref=ref: m.bot.goto_ref(ref, 200)).items():
                    self.soft(r, f"{n} walks to {ref}")
        elif verb == "talk":
            ref = refs[0]
            who = self.initiator()
            if self.soft(who.bot.goto_ref(ref, 150), f"{who.name} walks to {ref}") and self.soft(who.bot.talk(ref), f"{who.name} talks to {ref}"):
                P.end_dialogue(who.bot)
        elif verb == "take":
            ref = refs[0]
            if ref in self.taken:
                return
            who = self.initiator()
            if self.soft(who.bot.goto_ref(ref, 150), f"{who.name} walks to {ref}") and self.soft(who.bot.pickup(ref), f"{who.name} picks up {ref}"):
                self.taken.add(ref)
                ctx.settle()
                ctx.same_everywhere(f"{ref} enabled after pickup", lambda m, ref=ref: m.bot.ref_enabled(ref))
                if self.ms[0].bot.ref_enabled(ref):
                    self.fail(f"{ref} was picked up but is still in the world")


class QuestlinePlaybook(Playbook):
    min_players = 2
    max_players = 4
    anim = False
    summary = ""

    def __init__(self, line: str, plan: Optional[Plan] = None, repo: Optional[str] = None):
        from .plan import REPO
        self.line = line
        self.name = "ql_" + line
        self.plan = plan if plan is not None else Plan.load(line, repo or REPO)
        self.summary = f"{self.plan.data.get('title', line)}: {len(self.plan.quests)} quests, every stage on 2 to 4 machines"

    def run(self, ctx: Ctx) -> None:
        d = Driver(ctx, self.plan)
        self.driver = d
        d.run()
