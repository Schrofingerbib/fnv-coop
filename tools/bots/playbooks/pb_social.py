"""Social playbooks: companion_recruit, dialogue_spectator."""
from __future__ import annotations

from typing import Optional, Set, Tuple

from . import probe as P
from .core import Ctx, Machine, Playbook, StepFailure
from .fakecoop import FakeCoop

NPC = "FalloutNV.esm:D00001"
COMPANION = "FalloutNV.esm:D00002"
INFO_A, INFO_B, INFO_RECRUIT = "FalloutNV.esm:D10001", "FalloutNV.esm:D10002", "FalloutNV.esm:D10003"


def optional_owner(ctx: Ctx, ref: str):
    """Ownership service answer if the build has it; None (and a note) if the bridge method is not there yet."""
    return ctx.optional(f"owner check of {ref}", lambda: ctx.same_everywhere(f"owner of {ref}", lambda m: P.owner_of(m.bot, ref)))


class CompanionRecruitPlaybook(Playbook):
    name = "companion_recruit"
    summary = "One player recruits a companion; everyone sees it as that player's follower; no second recruit."
    scenario = "companion_recruit"
    needs_scenario_refs = ("companion",)
    anim = True

    def run(self, ctx: Ctx) -> None:
        comp, info = ctx.refs["companion"], ctx.refs.get("recruit_info")
        recruiter = ctx.clients[0]
        rival = ctx.host
        with ctx.step("recruiter walks up and talks"):
            ctx.must(recruiter.bot.goto_ref(comp, 150), "recruiter walks to the companion")
            if info:
                ctx.must(recruiter.bot.talk(comp), "recruiter talks")
                ctx.wait_until("others show the spectator view", lambda: P.spectating(rival.bot), timeout_s=ctx.opts.get("ui_s", 10.0))
        with ctx.step("recruiter picks the recruit line"):
            if info:
                ctx.must(recruiter.bot.pick_topic(info), "recruiter picks the recruit topic")
                ctx.must(P.end_dialogue(recruiter.bot), "recruiter ends the dialogue")
            else:   # the scenario has no recruit INFO id: set the teammate flag on the recruiter's machine instead
                ctx.notes.append("no recruit_info: recruited with SetPlayerTeammate (tests sync, not the dialogue)")
                r = recruiter.bot.nv.exec(f"{P.hexid(comp)}.SetPlayerTeammate 1")
                ctx.expect(r.get("ok", True), f"SetPlayerTeammate failed: {r.get('output')}")
            ctx.settle()
            ctx.expect(ctx.same_everywhere("is a companion", lambda m: P.is_teammate(m.bot, comp)) is True,
                       "the NPC should be a teammate on every machine")
            if info:
                ctx.wait_until("spectator view released", lambda: not P.spectating(rival.bot), timeout_s=ctx.opts.get("ui_s", 10.0))
        with ctx.step("ownership follows the recruiter"):
            o = optional_owner(ctx, comp)
            if o is not None:
                ctx.expect(o[0] == recruiter.idx, f"companion owner is slot {o[0]}, expected the recruiter ({recruiter.idx})")
        with ctx.step("a second player cannot recruit the same companion", compare=True):
            ctx.must(rival.bot.goto_ref(comp, 150), "rival walks to the companion")
            if info:
                r = rival.bot.talk(comp)
                if r:
                    rival.bot.pick_topic(info)
                    P.end_dialogue(rival.bot)
            ctx.settle()
            if optional_owner(ctx, comp) is not None:
                o = optional_owner(ctx, comp)
                ctx.expect(o[0] == recruiter.idx, f"companion changed owner to slot {o[0]} after a second recruit attempt")


class DialogueSpectatorPlaybook(Playbook):
    name = "dialogue_spectator"
    summary = "One player talks, everybody else spectates and is released afterwards; roles swap."
    scenario = "dialogue_spectator"
    needs_scenario_refs = ("npc", "infos")
    anim = True

    def run(self, ctx: Ctx) -> None:
        npc, infos = ctx.refs["npc"], list(ctx.refs["infos"])
        order = [ctx.host] + ctx.clients
        # "guest": only the first guest talks (a real conversation cannot be repeated); default: host, then the first guest
        speakers = order[1:2] if ctx.refs.get("speakers") == "guest" else order[:2]
        for speaker in speakers:
            others = [m for m in ctx.machines if m is not speaker]
            with ctx.step(f"{speaker.name} talks, the others spectate"):
                ctx.must(speaker.bot.goto_ref(npc, 150), f"{speaker.name} walks to the NPC")
                ctx.must(speaker.bot.talk(npc), f"{speaker.name} starts the dialogue")
                ctx.wait_until("every other machine shows the spectator view",
                               lambda: all(P.spectating(m.bot) for m in others), timeout_s=ctx.opts.get("ui_s", 10.0))
                ctx.expect(sum(1 for m in ctx.machines if m.bot.in_dialogue()) == 1,
                           "more than one player is in this dialogue as speaker")
                blocked = others[0].bot.talk(npc)
                ctx.expect(not blocked, f"{others[0].name} started a second dialogue with the same NPC")
                for i in infos:
                    ctx.must(speaker.bot.pick_topic(i), f"{speaker.name} picks topic {i}")
                    ctx.settle()
                    ctx.expect(all(P.spectating(m.bot) for m in others), "a spectator was dropped mid-dialogue")
            with ctx.step(f"{speaker.name} ends the dialogue, the others are released"):
                ctx.must(P.end_dialogue(speaker.bot), f"{speaker.name} ends the dialogue")
                ctx.wait_until("spectators released", lambda: not any(P.spectating(m.bot) for m in ctx.machines),
                               timeout_s=ctx.opts.get("ui_s", 10.0))
                ctx.expect(not any(m.bot.in_dialogue() for m in ctx.machines), "somebody is still in dialogue")
                ctx.settle()
                dq = ctx.refs.get("done_quest")
                if dq:       # the conversation's end script must have run once, on every machine
                    ctx.wait_until(f"{dq} stage {ctx.refs['done_stage']} on every machine",
                                   lambda: all(P.stage_done(m.bot, dq, int(ctx.refs["done_stage"])) for m in ctx.machines),
                                   timeout_s=ctx.opts.get("stage_s", 15.0))


# ---------------------------------------------------------------------------------------------
def fake_recruit(n: int = 2, bugs: Optional[Set[str]] = None, **kw) -> Tuple[FakeCoop, dict]:
    kw.setdefault("walk_speed", 120.0)
    w = FakeCoop(n, bugs, **kw)
    w.add_ref(COMPANION, "companion", pos=(300, 0, 0), cell="goodsprings", info_effects={"D10003": "recruit"})
    for s in range(n):
        w.set_player(s, pos=(0, 0, 0), cell="goodsprings")
    return w, {"companion": COMPANION, "recruit_info": INFO_RECRUIT}


def fake_dialogue(n: int = 2, bugs: Optional[Set[str]] = None, **kw) -> Tuple[FakeCoop, dict]:
    kw.setdefault("walk_speed", 120.0)
    w = FakeCoop(n, bugs, **kw)
    w.add_ref(NPC, "actor", pos=(300, 0, 0), cell="goodsprings")
    for s in range(n):
        w.set_player(s, pos=(0, 0, 0), cell="goodsprings")
    return w, {"npc": NPC, "infos": [INFO_A, INFO_B]}
