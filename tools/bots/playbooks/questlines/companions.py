"""The companions line: recruit by a client, follower caps per player, one owner per companion, dismiss, then the personal quests.

A companion follows the player who recruited it on every machine (CompanionOwner owner=recruiter, docs/protocol.md section 12). The vanilla
cap is one humanoid and one critter (VNPCFollowers.bHumanoidInParty / bCritterInParty: questlines/systems/companions.json), and with several
players that has to be PER PLAYER (HostSession::Options::followers), otherwise the first recruiter uses the whole party's slots.
Recruiting and dismissing here goes through the companion's INFO (recruit_info / dismiss_info of companions.json), picked by a player.
Nothing here has run in a game.
"""
from __future__ import annotations

from typing import List

from .. import probe as P
from ..core import Ctx, StepFailure
from .driver import QuestlinePlaybook


class CompanionsPlaybook(QuestlinePlaybook):
    def run(self, ctx: Ctx) -> None:
        comps = [c for c in (self.plan.data.get("companions") or {}).get("companions", []) if c.get("ref") and c.get("recruit_info")]
        hum = [c for c in comps if c.get("kind") == "humanoid"]
        crit = [c for c in comps if c.get("kind") != "humanoid"]
        ctx.expect(len(hum) >= 2, f"the plan needs two humanoid companions with recruit lines, has {len(hum)}")
        self.recruit_steps(ctx, hum, crit)
        super().run(ctx)

    # ---- helpers ----
    def say(self, ctx: Ctx, who, c: dict, info_key: str) -> None:
        ctx.must(who.bot.goto_ref(c["ref"], 150), f"{who.name} walks to {c['name']}")
        ctx.must(who.bot.talk(c["ref"]), f"{who.name} talks to {c['name']}")
        ctx.must(who.bot.pick_topic(c[info_key]), f"{who.name} picks the {info_key} line of {c['name']}")
        ctx.must(P.end_dialogue(who.bot), f"{who.name} ends the dialogue")
        ctx.settle()

    def follows(self, ctx: Ctx, c: dict, want: bool, label: str) -> None:
        ctx.same_everywhere(f"{c['name']} teammate", lambda m: P.is_teammate(m.bot, c["ref"]))
        for m in ctx.present:
            if P.is_teammate(m.bot, c["ref"]) != want:
                raise StepFailure(f"{m.name}: {c['name']} should {'follow' if want else 'not follow'} ({label})", "check")

    def owner_is(self, ctx: Ctx, c: dict, idx: int) -> None:
        r = ctx.optional(f"owner of {c['name']}", lambda: ctx.same_everywhere(f"{c['name']} owner", lambda m: P.owner_of(m.bot, c["ref"])[0]))
        if r is not None and r != idx:
            raise StepFailure(f"{c['name']} is owned by slot {r}, the recruiter is slot {idx}", "check")

    def recruit_steps(self, ctx: Ctx, hum: List[dict], crit: List[dict]) -> None:
        a, b = hum[0], hum[1]
        ms = ctx.present
        p0, p1 = ms[0], ms[-1]
        with ctx.step(f"{p1.name} recruits {a['name']}: a companion follows its recruiter on every machine"):
            self.say(ctx, p1, a, "recruit_info")
            self.follows(ctx, a, True, "recruited")
            self.owner_is(ctx, a, p1.idx)
        with ctx.step(f"{p1.name} cannot recruit a second humanoid ({b['name']}): the cap is per player"):
            self.say(ctx, p1, b, "recruit_info")
            self.follows(ctx, b, False, "second humanoid of the same player")
        with ctx.step(f"{p0.name} cannot take {a['name']} from {p1.name}"):
            self.say(ctx, p0, a, "recruit_info")
            self.follows(ctx, a, True, "still followed")
            self.owner_is(ctx, a, p1.idx)
        with ctx.step(f"{p0.name} recruits {b['name']} with its own slot"):
            self.say(ctx, p0, b, "recruit_info")
            self.follows(ctx, b, True, "the other player has a free slot")
            self.owner_is(ctx, b, p0.idx)
        if crit:
            c = crit[0]
            with ctx.step(f"{p1.name} recruits the critter {c['name']} next to {a['name']}: one humanoid plus one critter"):
                self.say(ctx, p1, c, "recruit_info")
                self.follows(ctx, c, True, "critter slot is separate")
                self.owner_is(ctx, c, p1.idx)
        if a.get("dismiss_info"):
            with ctx.step(f"{p1.name} dismisses {a['name']}: nobody follows it any more"):
                self.say(ctx, p1, a, "dismiss_info")
                self.follows(ctx, a, False, "dismissed")
            with ctx.step(f"{a['name']} can be recruited again after the dismiss"):
                self.say(ctx, p1, a, "recruit_info")
                self.follows(ctx, a, True, "recruited again")
