"""Story playbooks: pnr_vote, quest_step_sync, goodsprings_opening."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

from . import goodsprings_plan as GP
from . import probe as P
from .core import Ctx, Machine, Playbook, StepFailure
from .fakecoop import FakeCoop

PNR_Q, PNR_STAGE = "FalloutNV.esm:E00001", 30
SYNC_Q = "FalloutNV.esm:E00002"
SYNC_STAGES = [(5, [(3, "displayed", 1)]), (10, []), (20, [(20, "completed", 1)]), (25, [])]


def all_stage_done(ctx: Ctx, q: str, n: int) -> Dict[str, bool]:
    return {m.name: P.stage_done(m.bot, q, n) for m in ctx.machines}


def expect_stage_everywhere(ctx: Ctx, q: str, n: int, want: bool = True) -> None:
    got = all_stage_done(ctx, q, n)
    if any(v != want for v in got.values()):
        raise StepFailure(f"quest {q} stage {n} done={want} expected on every machine, got {got}", "check")


class PnrVotePlaybook(Playbook):
    name = "pnr_vote"
    summary = "A one-way step waits for every player: chargen barrier, and the endgame Ready / Not yet consent."
    scenario = "pnr_vote"
    needs_scenario_refs = ()
    anim, anim_npcs = True, False
    final_ready = True          # False: stop after the 'Not yet' veto (the world stays reusable)

    def run(self, ctx: Ctx) -> None:
        ctx.expect("quest" in ctx.refs or "moore" in ctx.refs, "scenario gives neither a barrier quest nor an endgame ref")
        if "quest" in ctx.refs:
            self.barrier(ctx)
        if "moore" in ctx.refs:
            self.ready_check(ctx)

    def barrier(self, ctx: Ctx) -> None:
        q, n = ctx.refs["quest"], int(ctx.refs["stage"])
        with ctx.step("barrier: host asks for the one-way stage, nothing happens yet"):
            ctx.must(P.set_stage(ctx.host.bot, q, n), "host requests the stage")
            ctx.pause(1.0)
            expect_stage_everywhere(ctx, q, n, False)
        passers = ctx.machines[:]
        for i, m in enumerate(passers[:-1]):
            with ctx.step(f"barrier: {m.name} passes ({i + 1} of {len(passers)}), still waiting"):
                ctx.must(P.scene_pass(m.bot, q), f"{m.name} passes")
                ctx.pause(0.5)
                expect_stage_everywhere(ctx, q, n, False)
        with ctx.step("barrier: last player passes, the stage applies everywhere once"):
            ctx.must(P.scene_pass(passers[-1].bot, q), f"{passers[-1].name} passes")
            ctx.wait_until("stage applied on every machine", lambda: all(all_stage_done(ctx, q, n).values()),
                           timeout_s=ctx.opts.get("barrier_s", 20.0))
            ctx.settle()
            after = ctx.same_everywhere("current stage", lambda m: P.quest_stage(m.bot, q))
            ctx.expect(after >= n, f"current stage is {after}, expected at least {n}")

    def ready_check(self, ctx: Ctx) -> None:
        """Endgame confirm: the host answers a message box, every other player answers Ready / Not yet (L8 consent)."""
        moore, q = ctx.refs["moore"], ctx.refs["ready_quest"]
        s_from, s_to = int(ctx.refs["ready_from"]), int(ctx.refs["ready_to"])
        host, others = ctx.host, ctx.clients
        timeout = ctx.opts.get("ui_s", 10.0)

        def ask() -> None:
            ctx.must(host.bot.activate(moore), "host activates the endgame NPC")
            ctx.must(P.menu_press(host.bot, 1), "host presses 'Let's do this!'")

        def prompts_arrive() -> None:
            def seen() -> bool:
                got = ctx.optional("consent prompt query", lambda: all(P.consent_pending(m.bot) for m in others), True)
                return bool(got)
            ctx.wait_until("every other player has a Ready / Not yet prompt", seen, timeout_s=timeout)

        with ctx.step("ready check: everybody stands at the NPC, quest is at the start stage"):
            for n_, r in ctx.on_all(lambda m: m.bot.goto_ref(moore, 150)).items():
                ctx.must(r, f"{n_} walks to the NPC")
            ctx.same_everywhere("quest stage", lambda m: P.quest_stage(m.bot, q))
            ctx.expect(P.quest_stage(host.bot, q) == s_from, f"quest should be at stage {s_from}")
        with ctx.step("ready check: the host says 'not ready yet': nothing changes"):
            ctx.must(host.bot.activate(moore), "host activates the endgame NPC")
            ctx.must(P.menu_press(host.bot, 0), "host presses 'I'm not ready yet'")
            ctx.pause(1.0)
            expect_stage_everywhere(ctx, q, s_to, False)
            ctx.expect(all(P.quest_stage(m.bot, q) == s_from for m in ctx.machines), "stage moved after a decline")
        with ctx.step("ready check: a guest answers 'Not yet': the step is vetoed"):
            ask()
            prompts_arrive()
            ctx.must(P.consent(others[0].bot, "ready", quest=q, yes=False), f"{others[0].name} answers Not yet")
            for m in others[1:]:
                ctx.must(P.consent(m.bot, "ready", quest=q, yes=True), f"{m.name} answers Ready")
            ctx.pause(1.0)
            expect_stage_everywhere(ctx, q, s_to, False)
            ctx.expect(all(P.quest_stage(m.bot, q) == s_from for m in ctx.machines), "a 'Not yet' did not stop the step")
        if not self.final_ready:
            return
        with ctx.step("ready check: everybody answers Ready: it applies once everywhere"):
            ask()
            prompts_arrive()
            for m in others:
                ctx.must(P.consent(m.bot, "ready", quest=q, yes=True), f"{m.name} answers Ready")
            ctx.wait_until(f"stage {s_to} on every machine", lambda: all(all_stage_done(ctx, q, s_to).values()),
                           timeout_s=ctx.opts.get("barrier_s", 30.0))


class PnrVoteGuestDeclinesPlaybook(PnrVotePlaybook):
    name = "pnr_vote_guest_declines"
    summary = "Endgame confirm where a guest answers 'Not yet': stage 70 holds on every machine (does not start the battle)."
    scenario = "pnr_vote_guest_declines"
    final_ready = False


class QuestStepSyncPlaybook(Playbook):
    name = "quest_step_sync"
    summary = "The host walks a quest through its stages; stages and objective flags match on every machine."
    scenario = "quest_stage_sync"
    needs_scenario_refs = ("quest", "stages")

    def run(self, ctx: Ctx) -> None:
        q = ctx.refs["quest"]
        stages = ctx.refs["stages"]            # [(stage, [(objective, "displayed"|"completed", value)]), ...]
        if ctx.refs.get("start", True):
            with ctx.step("host starts the quest"):
                ctx.must(P.start_quest(ctx.host.bot, q), "host starts the quest")
                ctx.wait_until("running everywhere", lambda: all(P.quest_running(m.bot, q) for m in ctx.machines),
                               timeout_s=ctx.opts.get("stage_s", 15.0))
        last = 0
        for n, effs in stages:
            with ctx.step(f"stage {n}"):
                ctx.must(P.set_stage(ctx.host.bot, q, n), f"host sets stage {n}")
                ctx.wait_until(f"stage {n} on every machine", lambda: all(all_stage_done(ctx, q, n).values()),
                               timeout_s=ctx.opts.get("stage_s", 15.0))
                ctx.settle()
                cur = ctx.same_everywhere("current stage", lambda m: P.quest_stage(m.bot, q))
                ctx.expect(cur >= max(n, last), f"current stage {cur} is behind stage {n}")
                last = cur
                for obj, kind, val in effs:
                    fn = P.objective_displayed if kind == "displayed" else P.objective_completed
                    ctx.same_everywhere(f"objective {obj} {kind}", lambda m, o=obj, f=fn: f(m.bot, q, o))
                    for m in ctx.machines:
                        ctx.expect(fn(m.bot, q, obj) == bool(val), f"{m.name}: objective {obj} {kind} should be {bool(val)}")
        with ctx.step("repeating a stage changes nothing"):
            n = stages[-1][0]
            snap = {m.name: P.quest_stage(m.bot, q) for m in ctx.machines}
            ctx.must(P.set_stage(ctx.host.bot, q, n), "host repeats the stage")
            ctx.pause(0.5)
            ctx.settle()
            ctx.expect({m.name: P.quest_stage(m.bot, q) for m in ctx.machines} == snap, "repeating a stage moved the quest")


# ---------------------------------------------------------------------------------------------
def run_action(ctx: Ctx, plan: Dict[str, Any], spec: str) -> None:
    """Interpret one ACTIONS entry (see goodsprings_plan.ACTIONS)."""
    kind, _, rest = spec.partition(":")
    refs = plan["refs"]
    if kind == "all":
        verb, _, name = rest.partition(":")
        ref = refs[name]
        if verb == "walk":
            for n, r in ctx.on_all(lambda m: m.bot.goto_ref(ref, 200)).items():
                ctx.must(r, f"{n} walks to {name}")
        elif verb == "activate":
            for n, r in ctx.on_all(lambda m: m.bot.activate(ref)).items():
                ctx.must(r, f"{n} activates {name}")
        elif verb == "kill":
            ctx.equip_all()
            for n, r in ctx.on_all(lambda m: m.bot.goto_ref(ref, 500)).items():
                ctx.must(r, f"{n} walks to {name}")
            for _ in range(15):
                ctx.on_all(lambda m: (m.bot.aim_at(ref), m.bot.fire(1)))
                if not any(m.bot.target_alive(ref) for m in ctx.machines):
                    break
            ctx.settle()
            ctx.same_everywhere(f"{name} alive", lambda m: m.bot.target_alive(ref))
            ctx.expect(not ctx.host.bot.target_alive(ref), f"{name} is still alive after 15 volleys")
        else:
            raise ValueError(spec)
    elif kind == "talk_end":
        ref = refs[rest]
        talker = ctx.machines[-1]
        ctx.must(talker.bot.goto_ref(ref, 150), f"{talker.name} walks to {rest}")
        ctx.must(talker.bot.talk(ref), f"{talker.name} talks to {rest}")
        ctx.must(P.end_dialogue(talker.bot), f"{talker.name} ends the dialogue")
    elif kind == "split_pick":
        names = rest.split(",")
        ms = ctx.machines
        for i, name in enumerate(names):
            m = ms[i % len(ms)]
            ctx.must(m.bot.goto_ref(refs[name], 150), f"{m.name} walks to {name}")
            ctx.must(m.bot.pickup(refs[name]), f"{m.name} picks up {name}")
        ctx.settle()
        for name in names:
            ctx.same_everywhere(f"{name} still in the world", lambda m, n=name: m.bot.ref_enabled(refs[n]))
            ctx.expect(not ctx.host.bot.ref_enabled(refs[name]), f"{name} was picked up but is still there")
    else:
        raise ValueError(spec)


class GoodspringsOpeningPlaybook(Playbook):
    name = "goodsprings_opening"
    summary = "VCG01 -> VCG02 -> VCG03: the first full-questline run, every stage and objective flag checked on every machine."
    scenario = "goodsprings_opening"
    anim = True
    min_players = 2
    max_players = 4

    def __init__(self, plan: Optional[dict] = None, repo_root: str = ""):
        self.plan = plan
        self.repo_root = repo_root

    def run(self, ctx: Ctx) -> None:
        plan = self.plan or GP.load_plan(self.repo_root or ctx.opts["repo_root"])
        bad = GP.check_plan(plan, self.repo_root or ctx.opts.get("repo_root", ""))
        if bad:
            raise StepFailure("the Goodsprings plan does not resolve against questdata: " + "; ".join(bad[:5]), "check")
        host = ctx.host
        for q in plan["quests"]:
            qid, edid = q["formid"], q["edid"]
            barriers = plan["barriers"].get(qid, [])
            for kind, n in q["steps"]:
                if kind == "start":
                    with ctx.step(f"{edid}: start"):
                        ctx.must(P.start_quest(host.bot, qid), f"host starts {edid}")
                        ctx.wait_until(f"{edid} running everywhere", lambda: all(P.quest_running(m.bot, qid) for m in ctx.machines),
                                       timeout_s=ctx.opts.get("stage_s", 15.0))
                    continue
                with ctx.step(f"{edid}: stage {n}"):
                    ctx.must(P.set_stage(host.bot, qid, n), f"host sets {edid} stage {n}")
                    if n in barriers:
                        ctx.pause(0.5)
                        expect_stage_everywhere(ctx, qid, n, False)       # held by the barrier
                        for i, m in enumerate(ctx.machines):
                            ctx.must(P.scene_pass(m.bot, qid), f"{m.name} finishes its own {edid} intro step")
                            if i < len(ctx.machines) - 1:
                                ctx.pause(0.3)
                                expect_stage_everywhere(ctx, qid, n, False)       # others have not finished yet
                    ctx.wait_until(f"{edid} stage {n} on every machine", lambda: all(all_stage_done(ctx, qid, n).values()),
                                   timeout_s=ctx.opts.get("barrier_s", 30.0))
                    ctx.settle()
                    for obj, okind, val in q["objective_effects"].get(n, []):
                        fn = P.objective_displayed if okind == "displayed" else P.objective_completed
                        for m in ctx.machines:
                            ctx.expect(fn(m.bot, qid, obj) == bool(val),
                                       f"{m.name}: {edid} stage {n} should set objective {obj} {okind}={bool(val)}")
                    for spec in plan["actions"].get((edid, n), []):
                        run_action(ctx, plan, spec)
        with ctx.step("whole opening: stages agree on every machine"):
            for q in plan["quests"]:
                for kind, n in q["steps"]:
                    if kind == "stage":
                        expect_stage_everywhere(ctx, q["formid"], n, True)


# ---------------------------------------------------------------------------------------------
ENDGAME_NPC, ENDGAME_Q = "FalloutNV.esm:E00010", "FalloutNV.esm:E00011"


def fake_pnr(n: int = 2, bugs: Optional[Set[str]] = None, **kw) -> Tuple[FakeCoop, dict]:
    kw.setdefault("walk_speed", 120.0)
    w = FakeCoop(n, bugs, barriers={(PNR_Q, PNR_STAGE)}, **kw)
    w.add_quest(PNR_Q)
    w.add_quest(ENDGAME_Q, [70])
    w.add_ref(ENDGAME_NPC, "actor", pos=(900, 0, 0), cell="hoover")
    w.endgames[ENDGAME_NPC.lower().split(":")[0] + ":" + P.hexid(ENDGAME_NPC)] = {"quest": ENDGAME_Q, "from": 70, "to": 100}
    for s_ in range(n):
        w.set_player(s_, pos=(0, 0, 0), cell="hoover")
    return w, {"quest": PNR_Q, "stage": PNR_STAGE, "moore": ENDGAME_NPC, "ready_quest": ENDGAME_Q,
               "ready_from": 70, "ready_to": 100}


def fake_sync(n: int = 2, bugs: Optional[Set[str]] = None, **kw) -> Tuple[FakeCoop, dict]:
    rules = {}
    for st, effs in SYNC_STAGES:
        rules[(SYNC_Q, st)] = [{"op": "obj", "q": SYNC_Q.split(":")[-1], "o": o, "f": {kind: bool(v)}} for o, kind, v in effs]
    w = FakeCoop(n, bugs, quest_rules=rules, **kw)
    w.add_quest(SYNC_Q)
    return w, {"quest": SYNC_Q, "stages": SYNC_STAGES}


def fake_goodsprings(plan: dict, n: int = 2, bugs: Optional[Set[str]] = None, **kw) -> Tuple[FakeCoop, dict]:
    rules: Dict[Tuple[str, int], list] = {}
    barriers = set()
    for q in plan["quests"]:
        for st, effs in q["objective_effects"].items():
            rules[(q["formid"], st)] = [{"op": "obj", "q": q["formid"], "o": o, "f": {k: bool(v)}} for o, k, v in effs]
        for b in plan["barriers"].get(q["formid"], []):
            barriers.add((q["formid"], b))
    kw.setdefault("walk_speed", 240.0)
    w = FakeCoop(n, bugs, quest_rules=rules, barriers=barriers, **kw)
    for q in plan["quests"]:
        w.add_quest(q["formid"])
    r = plan["refs"]
    pos = {k: (200 * i, 0, 0) for i, k in enumerate(r)}
    for k, ref in r.items():
        if k.startswith("gecko"):
            w.add_ref(ref, "raider", pos=pos[k], cell="goodsprings", base="GECKO", hp=40)
        elif k in ("broc", "xander"):
            w.add_ref(ref, "item", pos=pos[k], cell="goodsprings", base=k.upper())
        elif k in ("doc", "sunny", "trudy"):
            w.add_ref(ref, "actor", pos=pos[k], cell="goodsprings", base=k)
        else:
            w.add_ref(ref, "activator", pos=pos[k], cell="goodsprings", base=k)
    for s in range(n):
        w.set_player(s, pos=(0, 0, 0), cell="goodsprings")
    return w, {"weapon": "WEAPON"}
