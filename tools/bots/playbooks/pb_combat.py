"""Combat playbooks: fight, ownership_handoff."""
from __future__ import annotations

from typing import Dict, List, Optional, Set, Tuple

from . import probe as P
from .core import Ctx, Machine, Playbook, StepFailure
from .fakecoop import FakeCoop

RAIDER_BASE = "FalloutNV.esm:0DBB5A"       # fake only
RAIDERS = ["FalloutNV.esm:A00001", "FalloutNV.esm:A00002", "FalloutNV.esm:A00003"]


def volley(ctx: Ctx, target: str, max_volleys: int = 12) -> None:
    """Every machine aims at the same actor and fires, at the same time, until it is dead on every machine."""
    from .pb_enemy import standoff     # at call time: pb_enemy imports this module
    ctx.equip_all()
    # The scenario's setup MoveTo puts the players on the first raider, and gather() leaves a player within its distance alone
    # (in game under Proton 2026-10-25: "host could not shoot ...104C70: too_close: the player is 4 units from the target").
    standoff(ctx, target, list(ctx.machines))
    for _ in range(max_volleys):
        def shoot(m: Machine):
            r = m.bot.aim_at(target)
            if not r:
                return r                      # out of range etc.: real failure
            return m.bot.fire(1)
        res = ctx.on_all(shoot)
        for name, r in res.items():
            # "already dead" from the second shooter is expected when two players kill in the same instant
            if not r and "dead" not in (r.reason or "") and "gone" not in (r.reason or ""):
                raise StepFailure(f"{name} could not shoot {target}: {r.reason}", "action")
        if all(not m.bot.target_alive(target) for m in ctx.machines):
            return
        ctx.settle()
    raise StepFailure(f"{target} is still alive on some machine after {max_volleys} volleys: "
                      f"{ {m.name: m.bot.target_alive(target) for m in ctx.machines} }", "check")


def walk_to(m: Machine, spec):
    """spec is {"x","y","z","cell"} or {"ref": ref, "dist": units}."""
    if "ref" in spec:
        return m.bot.goto_ref(spec["ref"], spec.get("dist", 300))
    return m.bot.goto_pos(spec["x"], spec["y"], spec["z"], spec.get("cell"))


def goto_or_teleport(ctx: Ctx, m: Machine, ref: str, dist: float):
    """Walk to `ref`. When the walk is stuck on terrain, teleport there and add a note. Use it only to place a player before the
    checks: the sync checks come after. In game 2026-10-21 the host stuck on the slope between GSPG01 and
    GoodspringsPowderGangMarker (1042 units, 290 up) and enemy_spawn failed at step 01 before it checked anything."""
    r = m.bot.goto_ref(ref, dist)
    reason = str(getattr(r, "reason", ""))
    # a walk that times out far away is a placement problem too (in game 2026-10-22: the guest still 4774 units from the marker)
    if getattr(r, "ok", False) or not any(w in reason for w in ("stuck", "timeout")):
        return r
    note = f"{m.name}: the walk to {ref} did not arrive ({reason}), teleported there"
    ctx.notes.append(note)
    ctx.log(note)
    r = m.bot.goto_ref(ref, dist, mode="teleport")
    ctx.settle()          # a teleport lands at once: let the engine and the session catch up (cell load, replication)
    return r


def gather(ctx: Ctx, near: str, dist: int = 600) -> None:
    # placement only (the checks come after): a walk stuck on terrain teleports (in game 2026-10-21: stuck 612 units out, wanted 600)
    res = ctx.on_all(lambda m: goto_or_teleport(ctx, m, near, dist))
    for name, r in res.items():
        ctx.must(r, f"{name} walk to {near}")


class FightPlaybook(Playbook):
    name = "fight"
    summary = "Both players shoot the same raiders; deaths, health and counts match everywhere."
    scenario = "goodsprings_fight"
    needs_scenario_refs = ("raiders",)
    anim = True

    def run(self, ctx: Ctx) -> None:
        raiders: List[str] = list(ctx.refs["raiders"])
        base = ctx.refs.get("raider_base")
        if base and ctx.optional("duplicate-raider checks", lambda: P.ref_count(ctx.host.bot, base)) is None:
            base = None                 # this build cannot count refs: skip those checks
        site = ctx.refs.get("site")
        if site:      # the raiders are an encounter: they only exist once somebody gets near
            with ctx.step("everyone walks to the encounter site"):
                for n, r in ctx.on_all(lambda m: walk_to(m, site)).items():
                    ctx.must(r, f"{n} walks to the site")
                ctx.wait_until("every raider is loaded on every machine",
                               lambda: all(P.ref_count(m.bot, base) >= len(raiders) for m in ctx.machines) if base else True,
                               timeout_s=ctx.opts.get("spawn_s", 15.0))
        with ctx.step("everyone walks to the raiders"):
            gather(ctx, raiders[0])
            for r in raiders:
                ctx.same_everywhere(f"{r} alive", lambda m, r=r: m.bot.target_alive(r))
                ctx.expect(ctx.host.bot.target_alive(r), f"{r} should start alive")
        if base:
            with ctx.step("raider count before the fight", compare=False):
                n0 = ctx.same_everywhere("raider count", lambda m: P.ref_count(m.bot, base))
                ctx.expect(n0 == len(raiders), f"expected {len(raiders)} raiders, every machine sees {n0}")
        for i, r in enumerate(raiders):
            with ctx.step(f"both kill raider {i + 1}"):
                volley(ctx, r)
                ctx.settle()
                ctx.same_everywhere(f"{r} alive", lambda m, r=r: m.bot.target_alive(r))
                for m in ctx.machines:
                    dc = getattr(m.bot, "death_count", None)
                    if dc is not None:
                        ctx.expect(dc(r) == 1, f"{r} died {dc(r)} times on {m.name} (must be exactly once)")
        with ctx.step("final state"):
            for r in raiders:
                ctx.expect(not any(m.bot.target_alive(r) for m in ctx.machines), f"{r} is alive somewhere")
            if base:
                n1 = ctx.same_everywhere("live raider count", lambda m: P.ref_count(m.bot, base))
                ctx.expect(n1 == 0, f"{n1} raiders still exist after the fight (duplicates or revived)")


class OwnershipHandoffPlaybook(Playbook):
    name = "ownership_handoff"
    summary = "The host walks away mid-fight; ownership moves once, the fight continues, no flapping or duplicates."
    scenario = "ownership_handoff"
    needs_scenario_refs = ("raiders", "far_pos", "near_pos")
    anim = True
    MAX_EPOCH_STEPS = 2           # allowed ownership changes per actor during the whole playbook

    def run(self, ctx: Ctx) -> None:
        raiders: List[str] = list(ctx.refs["raiders"])
        far = ctx.refs["far_pos"]          # {"x":..,"y":..,"z":..,"cell":..} the host walks to
        near = ctx.refs["near_pos"]        # a spot closer to the raiders than the client (flap bait)
        base = ctx.refs.get("raider_base")
        if base and ctx.optional("duplicate-raider checks", lambda: P.ref_count(ctx.host.bot, base)) is None:
            base = None
        client = ctx.clients[0]
        epochs0: Dict[str, int] = {}
        epochs1: Dict[str, int] = {}
        with ctx.step("fight starts, everyone near the raiders"):
            gather(ctx, raiders[0])
            ctx.equip_all()
            ctx.on_all(lambda m: (m.bot.aim_at(raiders[0]), m.bot.fire(1)))
            ctx.settle()
            for r in raiders:
                o = ctx.same_everywhere(f"owner of {r}", lambda m, r=r: P.owner_of(m.bot, r))
                epochs0[r] = o[1]
        with ctx.step("host walks away"):
            with ctx.walking(ctx.host):        # the host must visibly walk (stuck detector)
                ctx.must(walk_to(ctx.host, far), "host walks away")
            ctx.wait_until("ownership moves to the client for every live raider on every machine",
                           lambda: all(P.owner_of(m.bot, r)[0] == client.idx for m in ctx.machines for r in raiders
                                       if m.bot.target_alive(r)), timeout_s=ctx.opts.get("handoff_s", 15.0))
            ctx.settle()
            for r in raiders:
                if ctx.host.bot.target_alive(r):
                    o = ctx.same_everywhere(f"owner of {r}", lambda m, r=r: P.owner_of(m.bot, r))
                    ctx.expect(o[0] == client.idx, f"{r} owner is {o[0]}, expected the client ({client.idx})")
                    ctx.expect(o[1] > epochs0[r], f"{r} epoch did not increase on handoff ({epochs0[r]} -> {o[1]})")
                    epochs1[r] = o[1]
        with ctx.step("host comes back to the border: ownership must not flap"):
            ctx.must(walk_to(ctx.host, near), "host walks back")
            ctx.pause(2.0)
            for _ in range(5):
                ctx.settle()
            for r in raiders:
                if ctx.host.bot.target_alive(r):
                    o = ctx.same_everywhere(f"owner of {r}", lambda m, r=r: P.owner_of(m.bot, r))
                    ctx.expect(o[1] == epochs1[r] and o[0] == client.idx,
                               f"{r} ownership flapped: epoch {epochs0[r]} -> {epochs1[r]} -> {o[1]}, owner now {o[0]}")
        with ctx.step("client finishes the fight"):
            for r in raiders:
                if client.bot.target_alive(r):
                    ctx.must(client.bot.aim_at(r), f"client aims at {r}")
                    for _ in range(12):
                        res = client.bot.fire(1)
                        if not client.bot.target_alive(r):
                            break
            ctx.settle()
            for r in raiders:
                ctx.same_everywhere(f"{r} alive", lambda m, r=r: m.bot.target_alive(r))
                ctx.expect(not ctx.host.bot.target_alive(r), f"{r} is still alive: the fight did not continue")
        with ctx.step("no duplicates, no flapping overall"):
            if base:
                n = ctx.same_everywhere("raider count", lambda m: P.ref_count(m.bot, base))
                ctx.expect(n == 0, f"{n} raiders exist after everyone died")


# ---------------------------------------------------------------------------------------------
def fake_fight(n: int = 2, bugs: Optional[Set[str]] = None, **kw) -> Tuple[FakeCoop, dict]:
    kw.setdefault("walk_speed", 120.0)
    w = FakeCoop(n, bugs, **kw)
    w.add_site((20000, 0, 0), "goodsprings",
               [(r, "raider", (20000 + 80 * i, 0, 0), {"base": RAIDER_BASE}) for i, r in enumerate(RAIDERS)])
    for s in range(n):
        w.set_player(s, pos=(16500, 100 * s, 0), cell="goodsprings")
    return w, {"raiders": list(RAIDERS), "raider_base": RAIDER_BASE, "weapon": "WEAPON",
               "site": {"x": 18000, "y": 0, "z": 0, "cell": "goodsprings"}}


def fake_handoff(n: int = 2, bugs: Optional[Set[str]] = None, **kw) -> Tuple[FakeCoop, dict]:
    kw.setdefault("walk_speed", 120.0)
    w = FakeCoop(n, bugs, **kw)
    for i, r in enumerate(RAIDERS):
        w.add_ref(r, "raider", pos=(0, 0, 0), cell="goodsprings", base=RAIDER_BASE, hp=200)
    w.set_player(0, pos=(400, 0, 0), cell="goodsprings")
    for s in range(1, n):
        w.set_player(s, pos=(500, 0, 0), cell="goodsprings")
    return w, {"raiders": list(RAIDERS), "raider_base": RAIDER_BASE, "weapon": "WEAPON",
               "far_pos": {"x": 6000, "y": 0, "z": 0, "cell": "goodsprings"},
               "near_pos": {"x": 250, "y": 0, "z": 0, "cell": "goodsprings"}}
