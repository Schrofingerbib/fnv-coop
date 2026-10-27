"""World-object playbooks: door_terminal_lock, fast_travel_tether."""
from __future__ import annotations

from typing import Optional, Set, Tuple

from . import probe as P
from .core import Ctx, Machine, Playbook, StepFailure
from .fakecoop import FakeCoop
from .pb_combat import walk_to

DOOR = "FalloutNV.esm:C00001"
LOCKED_DOOR = "FalloutNV.esm:C00002"
TERMINAL = "FalloutNV.esm:C00003"
LOCKED_BOX = "FalloutNV.esm:C00004"
MARKER = "FalloutNV.esm:C00005"
ANCHOR = "FalloutNV.esm:C00006"
RAIDER = "FalloutNV.esm:C00007"


class DoorTerminalLockPlaybook(Playbook):
    name = "door_terminal_lock"
    summary = "Door, terminal and lock matrix: each acts from host and from client; state is the same everywhere."
    scenario = "door_terminal_container_matrix"
    needs_scenario_refs = ("door", "locked_door", "terminal", "terminal_entry", "locked_box", "key")
    anim, anim_npcs = True, False

    def run(self, ctx: Ctx) -> None:
        door, ldoor, term = ctx.refs["door"], ctx.refs["locked_door"], ctx.refs["terminal"]
        entry, box, key = ctx.refs["terminal_entry"], ctx.refs["locked_box"], ctx.refs["key"]
        host, client = ctx.host, ctx.clients[0]
        with ctx.step("everyone walks to the objects"):
            for ref in (door, term):
                res = ctx.on_all(lambda m, ref=ref: m.bot.goto_ref(ref, 120))
                for n, r in res.items():
                    ctx.must(r, f"{n} walks to {ref}")
        # (actor, observer-check) matrix: every action is done by the host once and by the client once
        for who in (host, client):
            with ctx.step(f"{who.name} opens and closes the plain door"):
                ctx.must(who.bot.open_door(door), f"{who.name} opens the door")
                ctx.settle()
                ctx.expect(ctx.same_everywhere("door open", lambda m: P.door_open(m.bot, door)) is True, "door should be open everywhere")
                ctx.must(who.bot.close_door(door), f"{who.name} closes the door")
                ctx.settle()
                ctx.expect(ctx.same_everywhere("door open", lambda m: P.door_open(m.bot, door)) is False, "door should be closed everywhere")
        with ctx.step("locked door refuses without a key"):
            ctx.expect(ctx.same_everywhere("locked", lambda m: P.is_locked(m.bot, ldoor)) is True, "door should start locked everywhere")
            r = client.bot.open_door(ldoor)
            ctx.expect(not r, "client opened a locked door without unlocking it")
            ctx.settle()
            ctx.expect(not P.door_open(host.bot, ldoor), "host sees the locked door open")
        with ctx.step("client unlocks with the key, host opens it"):
            ctx.must(client.bot.unlock(ldoor, key), "client unlocks the door")
            ctx.settle()
            ctx.expect(ctx.same_everywhere("locked", lambda m: P.is_locked(m.bot, ldoor)) is False, "door should be unlocked everywhere")
            ctx.must(host.bot.open_door(ldoor), "host opens the unlocked door")
            ctx.settle()
            ctx.expect(ctx.same_everywhere("door open", lambda m: P.door_open(m.bot, ldoor)) is True, "unlocked door should be open everywhere")
        for who in (client, host):
            with ctx.step(f"{who.name} uses the terminal"):
                ctx.must(who.bot.use_terminal(term, entry), f"{who.name} uses terminal entry {entry}")
                ctx.settle()
                ctx.expect(P.terminal_entry_used(who.bot, term, entry) is True,
                           f"{who.name}'s terminal entry {entry} is not recorded")      # the engine has no per-terminal flag for others
        with ctx.step("terminal unlocks the box; client opens it"):
            ctx.expect(ctx.same_everywhere("box locked", lambda m: P.is_locked(m.bot, box)) is False,
                       "the terminal entry should have unlocked the box on every machine")
            ctx.must(client.bot.open_container(box), "client opens the box")
            ctx.must(P.close_container(client.bot, box), "client closes the box")


def travel_mode(m: Machine) -> object:
    """True when the machine's world-time module runs fast travel as a party decision (worldtime.status partyTravel; a client reports the
    host's last beacon), False when it is free, the error text when the module does not answer."""
    try:
        r = m.bot.nv.call("worldtime.status", {}, timeout_s=15.0)
    except Exception as e:      # module off, or an old build
        return f"worldtime.status: {e}"
    if not isinstance(r, dict) or not r.get("enabled", True):
        return "world-time module off"
    return bool(r.get("partyTravel"))


class FastTravelTetherPlaybook(Playbook):
    name = "fast_travel_tether"
    summary = "Fast travel needs party consent and no combat; a tethered player cannot wander off."
    scenario = "fast_travel_tether"
    needs_scenario_refs = ("marker", "anchor", "tether_radius", "far_pos")
    anim = True
    MARKER_SLACK = 800

    def run(self, ctx: Ctx) -> None:
        marker, anchor = ctx.refs["marker"], ctx.refs["anchor"]
        radius = float(ctx.refs["tether_radius"])
        far = ctx.refs["far_pos"]
        hostile = ctx.refs.get("hostile")
        host, others = ctx.host, ctx.clients
        if hostile:
            with ctx.step("fast travel is refused in combat", compare=False):
                for m in others:        # consent is given, so only the combat rule can refuse
                    ctx.must(P.consent(m.bot, "fasttravel", yes=True), f"{m.name} consents")
                r = host.bot.fast_travel(marker)
                ctx.expect(not r, "fast travel started while hostile actors were awake")
                for m in others:
                    P.consent(m.bot, "fasttravel", yes=False)
            with ctx.step("everybody kills the hostile first"):
                ctx.equip_all()
                ctx.on_all(lambda m: m.bot.goto_ref(hostile, 600))
                for _ in range(12):
                    ctx.on_all(lambda m: (m.bot.aim_at(hostile), m.bot.fire(1)))
                    if not host.bot.target_alive(hostile):
                        break
                ctx.settle()
                ctx.same_everywhere("hostile alive", lambda m: m.bot.target_alive(hostile))
                ctx.expect(not host.bot.target_alive(hostile), "hostile survived 12 volleys")
        with ctx.step("fast travel is a party decision here", compare=False):
            # The game ships with [worldtime] travel=free (a trip needs no consent), so "without consent is refused" below only holds when the
            # test session runs travel=party (manifest ini worldtime.travel=party, plus coop.dialogue=1 for the question itself). Say that
            # first instead of reporting a sync bug: in the 2026-10-22 in-game sweep the host simply travelled because the mode was free.
            deadline = ctx.clock.now() + ctx.budget(10.0)         # a client learns the mode from the host's next beacon
            modes: dict = {}
            while True:
                modes = {m.name: travel_mode(m) for m in ctx.machines}
                if all(v is True for v in modes.values()) or ctx.clock.now() >= deadline:
                    break
                ctx.pause(0.5)
            ctx.expect(all(v is True for v in modes.values()),
                       f"fast travel is not set to party consent on every machine ({modes}); start the game with [worldtime] travel=party "
                       "(and [coop] dialogue=1 for the question)")
        with ctx.step("fast travel without consent is refused", compare=False):
            r = host.bot.fast_travel(marker)
            ctx.expect(not r, "fast travel went ahead without the other players' consent")
        with ctx.step("everyone consents: all arrive at the marker"):
            for m in others:
                ctx.must(P.consent(m.bot, "fasttravel", yes=True), f"{m.name} consents")
            with ctx.teleporting():
                ctx.must(host.bot.fast_travel(marker), "host fast travels")
            ctx.settle()
            ctx.pause(3.0)          # long enough for a glitch right after the jump to show (and be judged)
            cells = {m.name: m.bot.cell() for m in ctx.machines}
            ctx.expect(len(set(cells.values())) == 1, f"players ended in different cells: {cells}")
            for m in ctx.machines:
                d = P.distance(m.bot, marker)
                ctx.expect(d <= self.MARKER_SLACK, f"{m.name} is {d:.0f} units from the marker after arriving")
        with ctx.step("the tether holds a wandering player", compare=False):
            wanderer = others[0]
            walk_to(wanderer, far)
            ctx.settle()
            d = P.distance(wanderer.bot, anchor)
            ctx.expect(d <= radius * 1.1, f"{wanderer.name} is {d:.0f} units from the anchor, tether radius is {radius:g}")


# ---------------------------------------------------------------------------------------------
def fake_doors(n: int = 2, bugs: Optional[Set[str]] = None, **kw) -> Tuple[FakeCoop, dict]:
    kw.setdefault("walk_speed", 120.0)
    w = FakeCoop(n, bugs, **kw)
    w.add_ref(DOOR, "door", pos=(100, 0, 0), cell="vault")
    w.add_ref(LOCKED_DOOR, "door", pos=(200, 0, 0), cell="vault", locked=True, key="KEY")
    w.add_ref(TERMINAL, "terminal", pos=(120, 0, 0), cell="vault", unlocks=LOCKED_BOX)
    w.add_ref(LOCKED_BOX, "container", pos=(150, 0, 0), cell="vault", locked=True, items={"CAPS": 5})
    for s in range(n):
        w.set_player(s, pos=(0, 0, 0), cell="vault", inv={"WEAPON": 1, "KEY": 1})
    return w, {"door": DOOR, "locked_door": LOCKED_DOOR, "terminal": TERMINAL, "terminal_entry": "unlock",
               "locked_box": LOCKED_BOX, "key": "KEY"}


def fake_travel(n: int = 2, bugs: Optional[Set[str]] = None, **kw) -> Tuple[FakeCoop, dict]:
    kw.setdefault("walk_speed", 120.0)
    w = FakeCoop(n, bugs, tether={"anchor": ANCHOR, "radius": 3000}, **kw)
    w.add_ref(MARKER, "marker", pos=(50000, 0, 0), cell="primm")
    w.add_ref(ANCHOR, "marker", pos=(50000, 0, 0), cell="primm")
    w.add_ref(RAIDER, "raider", pos=(1500, 0, 0), cell="goodsprings", base="RAIDERBASE")
    for s in range(n):
        w.set_player(s, pos=(0, 100 * s, 0), cell="goodsprings")
    return w, {"marker": MARKER, "anchor": ANCHOR, "tether_radius": 3000, "hostile": RAIDER, "weapon": "WEAPON",
               "far_pos": {"x": 90000, "y": 0, "z": 0, "cell": "primm"}}
