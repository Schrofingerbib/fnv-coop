"""Playbooks for the remaining scenarios: spawn_once, vats_hit, save_load_rejoin."""
from __future__ import annotations

from typing import Optional, Set, Tuple

from . import probe as P
from .core import Ctx, Machine, Playbook, StepFailure
from .fakecoop import FakeCoop

MARKER, GANG_BASE = "FalloutNV.esm:F00001", "FalloutNV.esm:F00002"
TARGET = "FalloutNV.esm:F00003"
QUEST = "FalloutNV.esm:F00004"
REWARD = "CAPS"


class SpawnOncePlaybook(Playbook):
    name = "spawn_once"
    summary = "The host spawns an encounter; every machine sees exactly one, and it stays one (no double spawn)."
    scenario = "spawn_once"
    needs_scenario_refs = ("marker", "base")
    anim, anim_npcs = True, False
    SETTLE_ROUNDS = 5

    def run(self, ctx: Ctx) -> None:
        marker, base = ctx.refs["marker"], ctx.refs["base"]

        def count(m: Machine) -> int:
            return P.ref_count(m.bot, base)      # needs dev.countRefs; no skip here: the count IS this test

        with ctx.step("everyone stands at the marker, nothing of that kind exists yet"):
            for n, r in ctx.on_all(lambda m: m.bot.goto_ref(marker, 300)).items():
                ctx.must(r, f"{n} walks to the marker")
            ctx.expect(ctx.same_everywhere("count before", count) == 0, "the encounter already exists before the spawn")
        with ctx.step("host spawns one: exactly one appears on every machine"):
            ctx.must(P.spawn(ctx.host.bot, base, marker), "host spawns")
            ctx.wait_until("one on every machine", lambda: all(count(m) >= 1 for m in ctx.machines),
                           timeout_s=ctx.opts.get("spawn_s", 15.0))
            ctx.settle()
            ctx.expect(ctx.same_everywhere("count after spawn", count) == 1,
                       f"counts after the spawn: {ctx.on_all(count)} (must be 1 everywhere)")
        with ctx.step("it stays one after a quiet period"):
            for _ in range(self.SETTLE_ROUNDS):
                ctx.pause(1.0)
                ctx.settle()
            ctx.expect(ctx.same_everywhere("count later", count) == 1, f"counts later: {ctx.on_all(count)} (double spawn)")


class VatsHitPlaybook(Playbook):
    name = "vats_hit"
    summary = "One player uses VATS; the hit and damage match on every machine and the others' world keeps running."
    scenario = "vats_hit"
    needs_scenario_refs = ("target",)
    anim = True
    MIN_FRAMES = 2

    def run(self, ctx: Ctx) -> None:
        t, limb = ctx.refs["target"], ctx.refs.get("limb", "Head")
        host, others = ctx.host, ctx.clients
        with ctx.step("everyone at the target, same starting health"):
            ctx.equip_all()
            for n, r in ctx.on_all(lambda m: m.bot.goto_ref(t, 500)).items():
                ctx.must(r, f"{n} walks to the target")
            hp0 = ctx.same_everywhere("target health", lambda m: m.bot.health(t))
            ctx.expect(hp0 > 0, "target is dead before the test")
        with ctx.step("host fires one VATS attack; the others keep running"):
            f0 = {m.name: P.frames(m.bot) for m in others}
            ctx.must(host.bot.vats_target(t, limb), "host VATS attack")
            ctx.pause(1.0)
            for m in others:
                d = P.frames(m.bot) - f0[m.name]
                ctx.expect(d >= self.MIN_FRAMES, f"{m.name} ran only {d} frames while the host was in VATS (world paused)")
            ctx.settle()
            hp1 = ctx.same_everywhere("target health after the hit", lambda m: m.bot.health(t))
            ctx.expect(hp1 < hp0, f"the VATS hit did no damage anywhere ({hp0} -> {hp1})")
        with ctx.step("damage is applied once"):
            dmg = hp0 - hp1
            ctx.expect(dmg <= ctx.refs.get("max_damage", hp0), f"damage {dmg} is more than the target had")
            ctx.same_everywhere("alive", lambda m: m.bot.target_alive(t))


class SaveLoadRejoinPlaybook(Playbook):
    name = "save_load_rejoin"
    summary = "Host saves; a client drops and rejoins with no duplicate rewards; host loads the older save and all rebase."
    scenario = "save_load_rejoin"
    needs_scenario_refs = ("quest", "stage_a", "stage_b")
    min_players = 2

    def run(self, ctx: Ctx) -> None:
        q, a, b = ctx.refs["quest"], int(ctx.refs["stage_a"]), int(ctx.refs["stage_b"])
        item = ctx.refs.get("reward_item")
        save = ctx.refs.get("save_name", "autosave_t6b")
        host, guest = ctx.host, ctx.clients[0]

        def held(m: Machine) -> int:
            return P.item_count(m.bot, item) if item else 0
        if not item:
            ctx.notes.append("no reward_item in the scenario: reward-duplication checks are skipped")
        with ctx.step(f"host sets stage {a} and saves"):
            ctx.must(P.set_stage(host.bot, q, a), f"host sets stage {a}")
            ctx.wait_until(f"stage {a} everywhere", lambda: all(P.stage_done(m.bot, q, a) for m in ctx.machines),
                           timeout_s=ctx.opts.get("stage_s", 15.0))
            ctx.settle()
            ctx.must(P.save_game(host.bot, save), "host saves")
        before = {}
        with ctx.step("a client drops out; the host moves the quest on", settle=False, compare=False):
            ctx.must(P.session_leave(guest.bot), f"{guest.name} leaves")
            ctx.absent.add(guest.name)
            before = {m.name: held(m) for m in ctx.machines}
            ctx.must(P.set_stage(host.bot, q, b), f"host sets stage {b}")
            ctx.settle()
            ctx.expect(not P.stage_done(guest.bot, q, b), "the absent client saw the new stage")
        with ctx.step("the client rejoins and catches up; rewards arrive once"):
            ctx.must(P.session_join(guest.bot), f"{guest.name} rejoins")
            ctx.absent.discard(guest.name)
            ctx.wait_until("client caught up", lambda: P.stage_done(guest.bot, q, b), timeout_s=ctx.opts.get("stage_s", 15.0))
            ctx.settle()
            if item:
                host_gain = held(host) - before[host.name]
                got = held(guest) - before[guest.name]
                ctx.expect(got == host_gain, f"{guest.name} gained {got} x {item} on rejoin, the host gained {host_gain} for the same "
                           f"stage (duplicate or missing reward)")
        after_rejoin = held(guest)
        with ctx.step("host loads the older save: everyone rebases", allow_rebase=True):
            rb0 = {m.name: P.rebases(m.bot) for m in ctx.clients}
            ctx.must(P.load_game(host.bot, save), "host loads the save")
            ctx.wait_until("clients follow the host back", lambda: all(not P.stage_done(m.bot, q, b) for m in ctx.machines),
                           timeout_s=ctx.opts.get("stage_s", 30.0))
            ctx.settle()
            for m in ctx.clients:
                ctx.expect(P.rebases(m.bot) > rb0[m.name], f"{m.name} did not rebase (rebases still {rb0[m.name]})")
            ctx.same_everywhere("stage", lambda m: P.quest_stage(m.bot, q))
            if item:
                ctx.expect(held(guest) == after_rejoin, f"{guest.name} item count changed on rebase "
                           f"({after_rejoin} -> {held(guest)}): rewards re-delivered")
        with ctx.step("the client drops and rejoins again: still no duplicate rewards"):
            c0 = held(guest)
            ctx.must(P.session_leave(guest.bot), f"{guest.name} leaves again")
            ctx.absent.add(guest.name)
            ctx.pause(0.5)
            ctx.must(P.session_join(guest.bot), f"{guest.name} rejoins again")
            ctx.absent.discard(guest.name)
            ctx.settle()
            if item:
                ctx.expect(held(guest) == c0, f"rewards duplicated on the second rejoin ({c0} -> {held(guest)})")


# ---------------------------------------------------------------------------------------------
def fake_spawn(n: int = 2, bugs: Optional[Set[str]] = None, **kw) -> Tuple[FakeCoop, dict]:
    kw.setdefault("walk_speed", 120.0)
    w = FakeCoop(n, bugs, **kw)
    w.add_ref(MARKER, "marker", pos=(100, 0, 0), cell="goodsprings", base="MARKERBASE")
    for s in range(n):
        w.set_player(s, pos=(0, 0, 0), cell="goodsprings")
    return w, {"marker": MARKER, "base": GANG_BASE}


def fake_vats(n: int = 2, bugs: Optional[Set[str]] = None, **kw) -> Tuple[FakeCoop, dict]:
    kw.setdefault("walk_speed", 120.0)
    w = FakeCoop(n, bugs, **kw)
    w.add_ref(TARGET, "raider", pos=(1000, 0, 0), cell="goodsprings", hp=100)
    for s in range(n):
        w.set_player(s, pos=(0, 0, 0), cell="goodsprings")
    return w, {"target": TARGET, "weapon": "WEAPON", "limb": "Head"}


def fake_saveload(n: int = 2, bugs: Optional[Set[str]] = None, **kw) -> Tuple[FakeCoop, dict]:
    rules = {(QUEST, 20): [{"op": "reward", "id": "r20", "item": REWARD, "count": 5}],
             (QUEST, 10): [{"op": "reward", "id": "r10", "item": REWARD, "count": 2}]}
    w = FakeCoop(n, bugs, quest_rules=rules, **kw)
    w.add_quest(QUEST)
    return w, {"quest": QUEST, "stage_a": 10, "stage_b": 20, "reward_item": REWARD}
