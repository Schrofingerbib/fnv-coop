"""Loot playbooks: loot_race, container_share."""
from __future__ import annotations

from typing import Optional, Set, Tuple

from . import probe as P
from .core import Ctx, Machine, Playbook, StepFailure
from .fakecoop import FakeCoop

ITEM_REF = "FalloutNV.esm:B00001"
ITEM_BASE = "STIMPAK"
CONTAINER = "FalloutNV.esm:B00002"


def total_held(ctx: Ctx, item: str) -> int:
    return sum(P.item_count(m.bot, item) for m in ctx.machines)


class LootRacePlaybook(Playbook):
    name = "loot_race"
    summary = "Everyone grabs the same world item at once; exactly one wins, the others get 'already taken'."
    scenario = "loot_race"
    needs_scenario_refs = ("item", "item_base")
    anim, anim_npcs = True, False
    ROUNDS = 3

    def run(self, ctx: Ctx) -> None:
        item, base = ctx.refs["item"], ctx.refs["item_base"]
        with ctx.step("everyone stands at the item"):
            res = ctx.on_all(lambda m: m.bot.goto_ref(item, 150))
            for n, r in res.items():
                ctx.must(r, f"{n} walks to the item")
        before = total_held(ctx, base)
        with ctx.step("all grab at the same instant"):
            res = ctx.on_all(lambda m: m.bot.pickup(item))
            winners = [n for n, r in res.items() if r]
            ctx.expect(len(winners) == 1, f"expected exactly one winner, got {winners or 'none'}; "
                       f"results: { {n: (bool(r), r.reason) for n, r in res.items()} }")
            ctx.settle()
            gained = total_held(ctx, base) - before
            ctx.expect(gained == 1, f"{gained} copies of {base} appeared in the party (must be exactly 1)")
            ctx.same_everywhere("item still lying there", lambda m: m.bot.ref_enabled(item))
            ctx.expect(not ctx.host.bot.ref_enabled(item), "the item is still in the world after being picked up")
            for m in ctx.machines:
                if m.name not in winners:
                    ctx.expect(P.item_count(m.bot, base) == 0, f"{m.name} lost the race but still holds the item")
        for rd in range(2, self.ROUNDS + 1):
            winner = next(m for m in ctx.machines if P.item_count(m.bot, base) > 0)
            with ctx.step(f"round {rd}: the winner drops it, everyone races again"):
                d = winner.bot.drop(base, 1)
                ctx.must(d, f"{winner.name} drops the item")
                dropped = (getattr(d, "data", None) or {}).get("ref")
                ctx.expect(bool(dropped), f"round {rd}: drop did not report the new world ref")
                if not dropped:
                    return
                ctx.settle()
                ctx.same_everywhere("dropped item visible", lambda m: m.bot.ref_enabled(dropped))
                total0 = total_held(ctx, base)
                res = ctx.on_all(lambda m: m.bot.pickup(dropped))
                winners = [n for n, r in res.items() if r]
                ctx.expect(len(winners) == 1, f"round {rd}: expected one winner, got {winners or 'none'}")
                ctx.settle()
                ctx.expect(total_held(ctx, base) == total0 + 1, f"round {rd}: item count in the party is wrong "
                           f"({total_held(ctx, base)} after, {total0} before the race)")


class ContainerSharePlaybook(Playbook):
    name = "container_share"
    summary = "Players take from and put into one container; one 'in use' lock at a time; nothing duplicated or lost."
    scenario = "door_terminal_container_matrix"
    needs_scenario_refs = ("container", "item_a", "item_b")
    anim, anim_npcs = True, False

    def run(self, ctx: Ctx) -> None:
        c, a, b = ctx.refs["container"], ctx.refs["item_a"], ctx.refs["item_b"]
        host, other = ctx.host, ctx.clients[0]

        def contents(m: Machine):
            return (P.holder_count(m.bot, a, c), P.holder_count(m.bot, b, c))

        seed = ctx.refs.get("seed")
        if seed:      # put known contents into the container on the host (replicated like any other change)
            with ctx.step("host seeds the container", compare=False):
                r = host.bot.nv.exec(f"{P.hexid(c)}.additem {P.hexid(seed['item'])} {int(seed['count'])}")
                ctx.expect(r.get("ok", True), f"additem failed: {r.get('output')}")
        with ctx.step("everyone walks to the container"):
            res = ctx.on_all(lambda m: m.bot.goto_ref(c, 120))
            for n, r in res.items():
                ctx.must(r, f"{n} walks to the container")
            init_a, init_b = ctx.same_everywhere("container contents", contents)
            ctx.expect(init_a >= 2, f"scenario container must hold at least 2 x {a}, has {init_a}")
        init = {a: init_a, b: init_b}
        party_before = {k: total_held(ctx, k) for k in (a, b)}
        take_n = max(1, init_a // 2)
        with ctx.step("host takes half of A while the client waits on the lock"):
            ctx.must(host.bot.open_container(c), "host opens the container")
            blocked = other.bot.open_container(c)
            ctx.expect(not blocked, "client opened the container while the host had it open (lock missing)")
            ctx.must(host.bot.take(a, take_n, c), "host takes")
            ctx.must(P.close_container(host.bot, c), "host closes")
            ctx.settle()
            ctx.same_everywhere("container contents after host take", contents)
            ctx.expect(contents(host)[0] == init_a - take_n,
                       f"container has {contents(host)[0]} x {a} after taking {take_n} of {init_a}")
        with ctx.step("client takes the rest and puts something back"):
            ctx.must(other.bot.open_container(c), "client opens after the host closed")
            left = contents(other)[0]
            ctx.must(other.bot.take(a, left, c), "client takes the rest of A")
            ctx.must(other.bot.put(a, 1, c), "client puts one A back")
            ctx.must(P.close_container(other.bot, c), "client closes")
            ctx.settle()
            final = ctx.same_everywhere("container contents at the end", contents)
            ctx.expect(final[0] == 1, f"expected 1 x {a} left in the container, got {final[0]}")
        with ctx.step("nothing duplicated or lost"):
            for k in (a, b):
                inside = P.holder_count(host.bot, k, c)
                held = total_held(ctx, k)
                want = party_before[k] + init[k]
                ctx.expect(inside + held == want, f"{k}: {inside} in the container + {held} held != {want} at the start")


# ---------------------------------------------------------------------------------------------
def fake_loot(n: int = 2, bugs: Optional[Set[str]] = None, **kw) -> Tuple[FakeCoop, dict]:
    kw.setdefault("walk_speed", 120.0)
    w = FakeCoop(n, bugs, **kw)
    w.add_ref(ITEM_REF, "item", pos=(500, 500, 0), cell="goodsprings", base=ITEM_BASE)
    for s in range(n):
        w.set_player(s, pos=(0, 0, 0), cell="goodsprings")
    return w, {"item": ITEM_REF, "item_base": ITEM_BASE}


def fake_container(n: int = 2, bugs: Optional[Set[str]] = None, **kw) -> Tuple[FakeCoop, dict]:
    kw.setdefault("walk_speed", 120.0)
    w = FakeCoop(n, bugs, **kw)
    w.add_ref(CONTAINER, "container", pos=(500, 500, 0), cell="goodsprings", items={"CAPS": 10, "STIMPAK": 3})
    for s in range(n):
        w.set_player(s, pos=(0, 0, 0), cell="goodsprings", inv={"WEAPON": 1})
    return w, {"container": CONTAINER, "item_a": "CAPS", "item_b": "STIMPAK"}
