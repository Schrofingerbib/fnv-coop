"""The eight rooms of the lab (docs/LAB.md). Each is `room(ctx)`: it records checks through the ctx, and the first failed check ends it.

Every check compares the SAME placed ref (same id) on both copies, never counts. Numbers that are guesses until a real run are in PARAMS
(--param room.key=value changes one for a run) and are listed as uncertain in docs/tools/lab.md.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

import lab_core as K
from lab_core import CLIENT, HOST, Blocked, Ctx, connected, dist2, dist3, digest_counts

# the numbers a room compares against; the first in-game run may move them
PARAMS: Dict[str, float] = {
    "spawn.idle_s": 20.0,            # idle digest window
    "spawn.connect_s": 30.0,
    "spawn.digest_wait_s": 12.0,     # after the idle window: how long to wait for the first digest verdict (ok, or a mismatch / repair, which ends the wait)
    "spawn.puppet_err": 250.0,       # units between the other player's real position and this copy's puppet of him, standing still
    "walk.err_p95": 150.0,           # puppet error while the other player walks (units, 2D)
    "walk.err_max": 300.0,
    "walk.jump": 250.0,              # a puppet that moves more than this between two samples (0.25 s) teleported
    "walk.lag_s": 3.0,               # after the walker stops, the puppet is within walk.near of him by then
    "walk.near": 80.0,
    "walk.min_samples": 8.0,
    "walk.leg_timeout_s": 25.0,
    "fight.latency_s": 3.0,          # a death seen on the shooter's copy shows on the other copy within this
    "fight.corpse_s": 8.0,           # how long the dead are watched
    "fight.weak_hp": 5.0,            # the host (owner) sets the target's health this low first, so one pistol hit kills it (as tools/bench/real_stage.py does)
    "fight.weak_s": 8.0,             # the client's copy shows the weakened health within this
    "fight.volleys": 24.0,           # first in-game run: 12 single pistol shots from 350 units left a hostile at 21 health
    "loot.settle_s": 10.0,
    "door.settle_s": 10.0,
    "npc.handoff_s": 25.0,
    "npc.min_far": 600.0,            # the stand marker must be this far from the NPC for a handoff to be possible
    "npc.watch_s": 8.0,
    "npc.hold_s": 5.0,               # the host keeps the NPC this long with the client next to it (minHoldMs 1500 + the switch margin, with room to spare)
    "npc.err_max": 150.0,            # the two copies' views of the NPC's position
    "npc.jump": 300.0,               # one NPC position step larger than this is a snap
    "npc.min_moved": 20.0,           # the patrolling NPC moved at least this far on its owner's copy
    "vats.tries": 6.0,
    "vats.weak_hp": 30.0,            # the host sets the VATS target's health this low first (two or three pistol hits)
    "vats.weak_s": 8.0,
    "vats.settle_s": 8.0,
    "quest.settle_s": 10.0,
    "quest.hold_s": 4.0,
}

AWAY_CELL = "GSDocMitchellHouse"      # a small stock interior (Doc Mitchell's house, CELL in FalloutNV.esm) the host `coc`s into so it stops being eligible for the lab NPC
DEFAULT_FLOOR_ITEM = "FalloutNV.esm:015169"   # a Stimpak, the same item the bench arena uses


def _ok_why(r: Tuple[bool, str]) -> Tuple[bool, str]:
    return bool(r[0]), r[1] or ""


# ------------------------------------------------------------------ spawn
def room_spawn(ctx: Ctx) -> Dict[str, Any]:
    """Both copies in the lab cell, connected, an idle digest that stays clean."""
    if ctx.enter_hook:
        ctx.enter_hook()
    L = ctx.layout
    mk = {HOST: L.marker("spawn", "host"), CLIENT: L.marker("spawn", "client")}
    ctx.refs_exist(list(mk.values()), "the spawn markers")
    for m in ctx.ms:
        ok, why = m.goto_ref(mk[m.name], 0.0)
        ctx.check(f"{m.name} is moved to its spawn marker", ok, "arrived", why or "arrived", cls="ENV", area="tools/lab (teleport)")
    ctx.agree("both copies are in the lab cell", lambda m: m.cell_of() == m.cell_of(mk[HOST]), 20.0, True, cls="ENV", area="plugin/coop (area change)")
    ctx.wait("both copies are connected", lambda: (lambda r: (r[0], r[1] or "joined"))(connected(ctx.host.status(), ctx.client.status())),
             ctx.p("connect_s"), "joined", area="plugin/coop, plugin/loader")

    def puppet_err(m, other):
        pp, op = m.puppet_pos(), other.pos()
        return None if pp is None else dist2(pp, op)
    lim = ctx.p("puppet_err")
    ctx.wait("each copy shows the other player's puppet near him", lambda: (lambda a, b: (a is not None and b is not None and a <= lim and b <= lim,
             {HOST: a, CLIENT: b}))(puppet_err(ctx.host, ctx.client), puppet_err(ctx.client, ctx.host)), 30.0, f"<= {lim:.0f} units",
             area="plugin/avatar")
    ctx.rebase()
    d0 = digest_counts(ctx.host.status())
    ctx.sleep(ctx.p("idle_s"))
    # The wait for a first "ok" is short and ends the moment a digest is judged bad: a copy pair that never agrees (a mismatch the repair cannot
    # fix) must fail HERE with the divergence, not use up the room's time box waiting for an ok that cannot come.
    slow = K.MUTANT == "digest_wait_unbounded"          # twin: the old 30 s wait for an ok that did not end on a bad digest
    end = ctx.now() + (30.0 if slow else ctx.p("digest_wait_s"))
    while True:
        d = digest_counts(ctx.host.status())
        dm = ctx.digests_moved()
        if d["ok"] > d0["ok"] or (not slow and (dm["mismatch"] or dm["repairs"])) or ctx.now() >= end:
            break
        ctx.sleep(1.0)
    ctx.check("idle digest stayed clean for the window", dm["mismatch"] == 0 and dm["repairs"] == 0, "mismatch 0, repairs 0", dm, area="plugin/areadigest, synccore digest")
    ok_after = d["ok"] - d0["ok"]
    ctx.check("the host judged at least one digest ok while idle", ok_after > 0, "> 0", ok_after, f"waited {ctx.p('idle_s') + ctx.p('digest_wait_s'):.0f} s at most",
              area="plugin/areadigest, synccore digest")
    return {"digest_ok_idle": ok_after}


# ------------------------------------------------------------------ walk
def walk_stats(samples: List[Tuple[float, Tuple[float, float, float], Optional[Tuple[float, float, float]]]], jump: float) -> Dict[str, float]:
    """samples: (time, the walker's real position, where the OTHER copy shows him). -> error p95 / max, teleports, count (pure, tested alone)."""
    errs, tele, prev = [], 0, None
    for _t, mp, pp in samples:
        if pp is None:
            continue
        errs.append(dist2(mp, pp))
        if prev is not None and dist2(prev, pp) > jump:
            tele += 1
        prev = pp
    errs.sort()
    p95 = errs[min(len(errs) - 1, int(0.95 * len(errs)))] if errs else 0.0
    return {"samples": len(errs), "err_p95": round(p95, 1), "err_max": round(errs[-1], 1) if errs else 0.0, "teleports": tele}


def room_walk(ctx: Ctx) -> Dict[str, Any]:
    """Host walks the waypoints, then the client does: the other copy's puppet follows without error spikes, teleports or lag."""
    wps = ctx.ref("wp", "waypoint")
    ctx.stand_all()
    ctx.refs_exist(wps + [ctx.layout.stand("walk")], "the walk waypoints")
    pts = [ctx.host.pos(w) for w in wps]
    out: Dict[str, Any] = {}
    for mover, obs in ((ctx.host, ctx.client), (ctx.client, ctx.host)):
        samples: List[Tuple[float, Any, Any]] = []

        def tick():
            samples.append((ctx.now(), mover.pos(), obs.puppet_pos()))
        for i, (x, y, z) in enumerate(pts, 1):
            ok, why = mover.walk_to(x, y, z, ctx.p("leg_timeout_s"), tick)
            ctx.check(f"{mover.name} walks to waypoint {i}", ok, "arrived", why or "arrived", cls="TEST", area="tools/lab (walking), plugin/bots")
        st = walk_stats(samples, ctx.p("jump"))
        ctx.check(f"the {obs.name} copy saw the {mover.name} walk", st["samples"] >= ctx.p("min_samples"), f">= {ctx.p('min_samples'):.0f} samples",
                  st["samples"], "no puppet to measure" if not st["samples"] else "", cls="SYNC", area="plugin/avatar")
        ctx.check(f"{mover.name}'s puppet on the {obs.name} copy: error p95", st["err_p95"] <= ctx.p("err_p95"), f"<= {ctx.p('err_p95'):.0f}", st["err_p95"],
                  area="plugin/avatar, plugin/avatar/engine")
        ctx.check(f"{mover.name}'s puppet on the {obs.name} copy: error max", st["err_max"] <= ctx.p("err_max"), f"<= {ctx.p('err_max'):.0f}", st["err_max"],
                  area="plugin/avatar, plugin/avatar/engine")
        ctx.check(f"{mover.name}'s puppet on the {obs.name} copy: teleports", st["teleports"] == 0, 0, st["teleports"], area="plugin/avatar, plugin/avatar/engine")
        near = ctx.p("near")

        def settled():
            pp = obs.puppet_pos()
            d = None if pp is None else dist2(pp, mover.pos())
            return (d is not None and d <= near), (None if d is None else round(d, 1))
        _, lag = ctx.wait(f"{mover.name}'s puppet catches up after he stops", settled, ctx.p("lag_s"), f"within {near:.0f} units", poll_s=0.2, area="plugin/avatar")
        out[f"{mover.name}_err_p95"], out[f"{mover.name}_err_max"], out[f"{mover.name}_lag_s"] = st["err_p95"], st["err_max"], round(lag, 2)
    return out


# ------------------------------------------------------------------ fight
def room_fight(ctx: Ctx) -> Dict[str, Any]:
    """Three hostiles: the host kills one, the client kills one; both copies see both deaths, nothing gets up, the third lives on both."""
    en = ctx.ref("enemy")[:3]
    ctx.stand_all()
    ctx.refs_exist(en + [ctx.layout.stand("fight")], "the enemies")
    for m in ctx.ms:
        ok, why = m.arm()
        ctx.check(f"{m.name} is armed (weapon, ammo, health)", ok, "armed", why or "armed", cls="TEST", area="tools/lab (arming)")
    lat: Dict[str, float] = {}
    volleys = int(ctx.p("volleys"))
    stand = ctx.layout.stand("fight")
    for shooter, other, e, idx in ((ctx.host, ctx.client, en[0], 1), (ctx.client, ctx.host, en[1], 2)):
        # one hostile at a time: enabled together they all charge the players, and by the client's turn a gecko stood inside aim_at's 100 units
        # (first in-game run: "the player is 60 units from the target")
        ctx.enable_hostiles([e])
        ctx.agree(f"enemy {idx} is alive on both copies", lambda m, e=e: m.alive(e), 15.0, True, area="plugin/actors, plugin/spawn")
        # the host, as owner of the actor, makes it weak; the shooter's copy must show that before it shoots (a hit on a 65 health gecko took 8 of 24
        # volleys in the first in-game run: the pistol hits a moving gecko now and then)
        if K.MUTANT != "no_weaken":     # twin: the targets are shot at full health again
            ok, why = ctx.host.set_health(e, ctx.p("weak_hp"))
            ctx.check(f"the host weakens enemy {idx}", ok, "health set", why or "health set", cls="TEST", area="tools/lab (SetAV Health)")
            ctx.agree(f"enemy {idx} shows the weakened health on both copies", lambda m, e=e: m.health(e) <= ctx.p("weak_hp") + 1.0, ctx.p("weak_s"), True,
                      area="plugin/actors (owner's health to the other copy)")
        ok, why = shooter.shoot(e, volleys, stand)
        ctx.check(f"{shooter.name} kills enemy {idx}", ok, "dead on the shooter's copy", why or "dead", cls="TEST", area="tools/lab (shooting), plugin/combat")
        _, secs = ctx.wait(f"enemy {idx} killed by the {shooter.name} is dead on the {other.name} copy", lambda: (not other.alive(e), other.health(e)),
                           ctx.p("latency_s"), 0, poll_s=0.1, area="plugin/combat, plugin/actors")
        lat[f"{shooter.name}_kill_latency_s"] = round(secs, 2)
    ctx.enable_hostiles([en[2]])
    ctx.agree("deaths agree: enemies 1 and 2 dead, enemy 3 alive, on both copies", lambda m: [m.alive(e) for e in en], 10.0, [False, False, True], area="plugin/combat, plugin/actors")
    ctx.agree("the survivor has the same health on both copies", lambda m: round(m.health(en[2])), 10.0, area="plugin/combat, plugin/actors")
    end = ctx.now() + ctx.p("corpse_s")
    seen: Dict[str, Any] = {}
    while ctx.now() < end:
        ctx.sleep(1.0)
        for m in ctx.ms:
            a = [m.alive(e) for e in en]
            if a != [False, False, True] and m.name not in seen:
                seen[m.name] = a
    ctx.check("no corpse gets up and the survivor stays alive for the watch window", not seen, [False, False, True], seen or [False, False, True],
              area="plugin/combat, plugin/actors (revive / stale echo)")
    return lat


# ------------------------------------------------------------------ loot
def room_loot(ctx: Ctx) -> Dict[str, Any]:
    """A container with known items and four loose items: takes and pickups on both sides, a race for one: no dupes, nothing lost, totals match."""
    cont = ctx.ref("container", "chest", n=0)
    floors = ctx.ref("item", "loose", "floor")[:4]
    ci = ctx.layout.container_items() or {DEFAULT_FLOOR_ITEM: 5}
    item_c = next(iter(ci))
    bases = ctx.layout.loose_bases()
    if len(bases) < len(floors):
        bases = [ctx.layout.extra("loot", "floor_item") or DEFAULT_FLOOR_ITEM] * len(floors)
    ctx.stand_all()
    ctx.refs_exist([cont] + floors, "the container and the loose items")
    c0 = ctx.agree("the container holds the same count of the item on both copies", lambda m: m.item_count(item_c, cont), 10.0, area="plugin/loot")
    ctx.check("the container holds what the plugin put in it", c0 >= 3 and c0 == ci[item_c], ci[item_c], c0, cls="TEST", area="tools/lab/esp")
    ctx.agree("the four loose items are in the world on both copies", lambda m: [m.enabled(r) for r in floors], 10.0, [True] * 4, area="plugin/loot")
    base = {m.name: {it: m.item_count(it) for it in set([item_c] + bases)} for m in ctx.ms}
    settle = ctx.p("settle_s")

    def gains(m, item):
        return m.item_count(item) - base[m.name][item]
    taken = 0
    want = {"host": 0, "client": 0}
    for taker, n in ((ctx.host, 2), (ctx.client, 1)):
        can, why_not = taker.can_take()
        if not can:
            ctx.skip(f"{taker.name} takes {n} from the container", why_not)
            continue
        ok, why = taker.take(cont, item_c, n)
        ctx.check(f"{taker.name} takes {n} from the container", ok, "taken", why or "taken", cls="SYNC", area="plugin/loot, plugin/intents")
        taken += n
        want[taker.name] = n
        ctx.agree(f"container count after the {taker.name}'s take is the same on both copies", lambda m: m.item_count(item_c, cont), settle, c0 - taken, area="plugin/loot")
    got = {"host": gains(ctx.host, item_c), "client": gains(ctx.client, item_c)}
    ctx.check("takers got exactly what they took, nobody else got anything", got == want, want, got, area="plugin/loot (dupe)")
    for picker, idx in ((ctx.host, 0), (ctx.client, 1)):
        g0 = picker.item_count(bases[idx])
        ok, why = picker.pickup(floors[idx])
        ctx.check(f"{picker.name} picks up loose item {idx + 1}", ok, "picked up", why or "picked up", area="plugin/loot, plugin/intents")
        ctx.agree(f"loose item {idx + 1} is gone on both copies", lambda m: m.enabled(floors[idx]), settle, False, area="plugin/loot")
        d = picker.item_count(bases[idx]) - g0
        ctx.check(f"only the {picker.name} gained loose item {idx + 1}", d == 1, 1, d, area="plugin/loot (dupe)")
    before = {m.name: m.item_count(bases[2]) for m in ctx.ms}
    ctx.par(lambda: ctx.host.pickup(floors[2]), lambda: ctx.client.pickup(floors[2]))
    ctx.agree("the raced loose item 3 is gone on both copies", lambda m: m.enabled(floors[2]), settle, False, area="plugin/loot (race)")
    ctx.sleep(2.0)
    won = {m.name: m.item_count(bases[2]) - before[m.name] for m in ctx.ms}
    ctx.check("the race gave loose item 3 to exactly one player", sum(won.values()) == 1 and all(v in (0, 1) for v in won.values()), "one winner, 1 item", won,
              area="plugin/loot (dupe / lost item)")
    ctx.agree("the untouched loose item 4 is still there on both copies", lambda m: m.enabled(floors[3]), 5.0, True, area="plugin/loot")
    return {"race_winner": max(won, key=won.get)}


# ------------------------------------------------------------------ door
def room_door(ctx: Ctx) -> Dict[str, Any]:
    """A locked door and its terminal: a locked door stays shut, the terminal unlocks it, then it opens and closes from either side, the same on both copies."""
    door, term = ctx.ref("door", n=0), ctx.ref("terminal", n=0)
    entry = int(ctx.layout.extra("door", "terminal_entry", 0))      # the stock unlock terminal has one entry, "Disengage Lock"
    settle = ctx.p("settle_s")
    ctx.stand_all()
    ctx.refs_exist([door, term], "the door and the terminal")
    ctx.agree("the door is closed on both copies", lambda m: m.door_state(door), settle, "closed", area="plugin/hooks_world, plugin/intents")
    locked = ctx.agree("the door has the same lock state on both copies", lambda m: m.locked(door), settle, area="plugin/hooks_world, plugin/intents")
    if not locked:
        raise Blocked("door room: the door must start locked (the terminal unlocks it); the plugin placed it unlocked", "TEST")
    ok, _ = ctx.host.open_door(door)
    ctx.check("the host cannot open the locked door", not ok, False, ok, area="plugin/intents")
    ctx.agree("the locked door is still closed on both copies", lambda m: m.door_state(door), 3.0, "closed", area="plugin/hooks_world")
    ok, why = ctx.client.use_terminal(term, entry)
    ctx.check("the client uses the terminal to unlock the door", ok, "entry used", why or "entry used", area="plugin/intents (terminal)")
    # Control: the click must have run the entry's unlock SOMEWHERE. A guest's click does not run the result script on its own copy: the TerminalEntryRun hook
    # forwards it as an intent and the host runs it (measured 2026-10-27: after the client's click the host showed the door unlocked within a second while the
    # client's copy stayed locked for 15 s). So the control looks at both copies; when neither unlocks, the click did nothing (a TEST problem, not a sync one),
    # and the next check, unlocked on both, is the SYNC check of the lock state.
    ctx.wait("the terminal click unlocked the door on at least one copy (if neither, the click unlocked nothing: not a sync problem)",
             lambda: (any(not m.locked(door) for m in ctx.ms), [m.locked(door) for m in ctx.ms]), 3.0, True, cls="TEST",
             area="plugin/bots (dev.terminal click), tools/lab/esp (terminal)")
    ctx.agree("the door is unlocked on both copies", lambda m: m.locked(door), settle, False, area="plugin/hooks_world, plugin/intents (lock)")
    for opener, closer in ((ctx.host, ctx.client), (ctx.client, ctx.host)):
        ok, why = opener.open_door(door)
        ctx.check(f"{opener.name} opens the door", ok, "opened", why or "opened", area="plugin/intents, plugin/hooks_world")
        ctx.agree(f"the door the {opener.name} opened is open on both copies", lambda m: m.door_state(door), settle, "open", area="plugin/hooks_world")
        ok, why = closer.close_door(door)
        ctx.check(f"{closer.name} closes the door", ok, "closed", why or "closed", area="plugin/intents, plugin/hooks_world")
        ctx.agree(f"the door the {closer.name} closed is closed on both copies", lambda m: m.door_state(door), settle, "closed", area="plugin/hooks_world")
    return {}


# ------------------------------------------------------------------ npc
def _owner_view(m, npc):
    o = m.owner(npc)
    return {"owner": o.get("owner"), "epoch": o.get("epoch"), "mine": bool(o.get("mine")), "known": bool(o.get("known", True))}


def room_npc(ctx: Ctx) -> Dict[str, Any]:
    """A friendly NPC on a patrol. Ownership is the HOST while it is eligible (in or next to the NPC's cell), else the nearest player
    (docs/FULL-SYNC-PLAN.md 1.2 and 3.2, synccore pickOwner). The whole lab is one interior cell, so no distance inside it moves the NPC away from the host:
    the room (1) keeps the client right next to the NPC and the host far away and expects the host to keep it, (2) sends the host into another stock interior
    and expects the client to take it (new epoch), (3) brings the host back to the lab and expects it to take it back (new epoch).
    While the host is away its copy has the NPC unloaded, so that copy is not sampled."""
    npc = ctx.ref("npc", n=0)
    stand = ctx.layout.stand("npc")
    away_cell = str(ctx.layout.extra("npc", "away_cell", AWAY_CELL))
    lab_cells = [ctx.layout.cell_edid] + ([ctx.layout.cell_alt] if getattr(ctx.layout, "cell_alt", "") else [])
    ctx.stand_all()
    ctx.refs_exist([npc, stand], "the NPC and its stand marker")
    ctx.agree("the NPC is alive on both copies", lambda m: m.alive(npc), 10.0, True, area="plugin/actors")
    # the host waits at the origin of the room farthest from the NPC; the client stands next to the NPC
    npc_at = ctx.host.pos(npc)
    spots = [(dist3(o, npc_at), o) for r in ctx.layout.rooms if r != "npc" and (o := ctx.layout.origin(r))]
    far, far_at = max(spots, default=(0.0, None))
    if far < ctx.p("min_far"):
        raise Blocked(f"npc room: the farthest room is {far:.0f} units from the NPC, the host must stand at least {ctx.p('min_far'):.0f} away (plugin layout)", "TEST")
    traces: Dict[str, List[Optional[Tuple[float, ...]]]] = {HOST: [], CLIENT: []}
    present = {HOST: True, CLIENT: True}      # a copy that is not in the lab cell has the NPC unloaded: no sample

    def sample():
        for m in ctx.ms:
            traces[m.name].append(tuple(m.pos(npc)) if present[m.name] else None)

    def watch(secs):
        end = ctx.now() + secs
        while ctx.now() < end:
            sample()
            ctx.sleep(0.5)
    epochs: List[int] = []

    def views():
        return {m.name: _owner_view(m, npc) for m in ctx.ms}

    def agreed(v, owner):
        mine = owner == HOST
        return (all(x["known"] for x in v.values()) and v[HOST]["mine"] == mine and v[CLIENT]["mine"] == (not mine) and v[HOST]["epoch"] == v[CLIENT]["epoch"])

    def moved_to(who):
        def settled():
            sample()                                   # the snap of a handoff happens inside this wait, so the NPC is watched here too
            v = views()
            return agreed(v, who) and (not epochs or (isinstance(v[HOST]["epoch"], int) and v[HOST]["epoch"] > epochs[-1])), v
        v, _ = ctx.wait(f"ownership of the NPC moves to the {who} with a new epoch and both copies agree", settled, ctx.p("handoff_s"), {"owner": who},
                        poll_s=0.2, area="plugin/actors, synccore ownership (pickOwner)")
        epochs.append(v[HOST]["epoch"] if isinstance(v[HOST]["epoch"], int) else 0)

    # 1. the client next to the NPC, the host far away in the same cell: the eligible host keeps it
    ok, why = ctx.client.goto_ref(npc, 150.0)
    ctx.check("the client walks up to the NPC", ok, "arrived", why or "arrived", cls="ENV", area="tools/lab (teleport)")
    ok, why = ctx.host.goto_pos(*far_at)
    ctx.check("the host waits far from the NPC", ok, "arrived", why or "arrived", cls="ENV", area="tools/lab (teleport)")
    v, _ = ctx.wait("the host owns the NPC while it is in the lab, however far it is and however near the client", lambda: (lambda v: (agreed(v, HOST), v))(views()),
                    ctx.p("handoff_s"), {"owner": HOST}, poll_s=0.2, area="plugin/actors, synccore ownership (pickOwner: an eligible host owns)")
    epochs.append(v[HOST]["epoch"] if isinstance(v[HOST]["epoch"], int) else 0)
    sample()
    end = ctx.now() + ctx.p("hold_s")
    stolen = None
    while ctx.now() < end and stolen is None:
        sample()
        ctx.sleep(0.5)
        v = views()
        if not agreed(v, HOST) or v[HOST]["epoch"] != epochs[-1]:
            stolen = v
    ctx.check("the host keeps the NPC for the whole hold (the client is nearer, the host is eligible)", stolen is None, {"owner": HOST, "epoch": epochs[-1]}, stolen or "kept",
              area="plugin/actors, synccore ownership (pickOwner)")
    # 2. the host leaves for a stock interior: the client takes the NPC
    ok, why = ctx.host.coc([away_cell])
    ctx.check(f"the host goes to {away_cell}", ok, "arrived", why or "arrived", cls="ENV", area="tools/lab (coc)")
    present[HOST] = False
    moved_to(CLIENT)
    watch(ctx.p("watch_s"))
    # 3. the host comes back to the lab and the NPC returns to it
    ok, why = ctx.host.coc(lab_cells)
    ctx.check("the host returns to the lab cell", ok, "arrived", why or "arrived", cls="ENV", area="tools/lab (coc)")
    ok, why = ctx.host.goto_ref(stand)
    ctx.check("the host stands at the NPC room's stand marker", ok, "arrived", why or "arrived", cls="ENV", area="tools/lab (teleport)")
    present[HOST] = True
    moved_to(HOST)
    watch(ctx.p("watch_s"))
    steps = []
    for tr in traces.values():
        steps += [dist3(a, b) for a, b in zip(tr, tr[1:]) if a is not None and b is not None]
    ctx.check("the NPC never snaps (largest step between samples)", max(steps, default=0.0) <= ctx.p("jump"), f"<= {ctx.p('jump'):.0f}", round(max(steps, default=0.0), 1),
              area="plugin/actors (handoff snap / Reset3D)")
    both = [(a, b) for a, b in zip(traces[HOST], traces[CLIENT]) if a is not None and b is not None]
    err = max((dist3(a, b) for a, b in both), default=0.0)
    ctx.check("the two copies show the NPC at the same place while both have it loaded (max position error)", err <= ctx.p("err_max"), f"<= {ctx.p('err_max'):.0f}", round(err, 1),
              area="plugin/actors (position sync)")
    moved = max((sum(dist3(a, b) for a, b in zip(tr, tr[1:]) if a is not None and b is not None) for tr in traces.values()), default=0.0)
    ctx.check("the NPC patrols (it moved while watched)", moved >= ctx.p("min_moved"), f">= {ctx.p('min_moved'):.0f}", round(moved, 1), cls="TEST", area="tools/lab/esp (patrol package)")
    return {"npc_err_max": round(err, 1), "npc_max_step": round(max(steps, default=0.0), 1)}


# ------------------------------------------------------------------ vats
def room_vats(ctx: Ctx) -> Dict[str, Any]:
    """Real-time VATS on both sides, each player on its own target: the hits and the deaths agree on both copies."""
    tg = ctx.ref("target")[:2]
    ctx.stand_all()
    ctx.refs_exist(tg + [ctx.layout.stand("vats")], "the VATS targets")
    for m in ctx.ms:
        ok, why = m.arm()
        ctx.check(f"{m.name} is armed (weapon, ammo, health)", ok, "armed", why or "armed", cls="TEST", area="tools/lab (arming)")
    settle = ctx.p("settle_s")
    stand = ctx.layout.stand("vats")
    hits: Dict[str, int] = {}
    for m, t in ((ctx.host, tg[0]), (ctx.client, tg[1])):
        hits[m.name] = 0
        # One hostile at a time, as in the fight room: both enabled together charge the players and are on top of the shooter (not in view, aim_at refuses
        # under 100 units) when the VATS shot starts (lab run 2026-10-27: vats_not_entered). The host, owner of the actor, makes it a little weaker so a few
        # VATS hits kill it.
        ctx.enable_hostiles([t])
        ctx.agree(f"{m.name}'s target is alive on both copies", lambda x, t=t: x.alive(t), 15.0, True, area="plugin/actors, plugin/spawn")
        ok, why = ctx.host.set_health(t, ctx.p("weak_hp"))
        ctx.check(f"the host weakens the {m.name}'s target", ok, "health set", why or "health set", cls="TEST", area="tools/lab (SetAV Health)")
        ctx.agree(f"the {m.name}'s target shows the weakened health on both copies", lambda x, t=t: x.health(t) <= ctx.p("weak_hp") + 1.0, ctx.p("weak_s"), True,
                  area="plugin/actors (owner's health to the other copy)")
        for attempt in range(1, int(ctx.p("tries")) + 1):
            h0 = m.health(t)
            ok, why = m.vats(t, None, stand)
            ctx.check(f"{m.name} VATS shot {attempt}", ok, "played out", why or "played out", cls="TEST", area="plugin/vats, plugin/bots")
            ctx.agree(f"{m.name} VATS shot {attempt}: the target's health agrees on both copies", lambda x: round(x.health(t)), settle, area="plugin/vats, plugin/combat")
            if m.health(t) < h0 or not m.alive(t):
                hits[m.name] += 1
            if not m.alive(t):
                break
        ctx.agree(f"{m.name}'s target is dead on both copies", lambda x: x.alive(t), settle, False, area="plugin/vats, plugin/combat")
        ctx.check(f"{m.name} hit with VATS at least once", hits[m.name] >= 1, ">= 1", hits[m.name], cls="TEST", area="plugin/vats")
    ctx.agree("deaths agree: both targets dead on both copies", lambda m: [m.alive(t) for t in tg], settle, [False, False], area="plugin/vats, plugin/combat")
    return {f"{k}_vats_hits": v for k, v in hits.items()}


# ------------------------------------------------------------------ quest
def room_quest(ctx: Ctx) -> Dict[str, Any]:
    """The lab quest: the host sets a stage and a variable and the client sees them; the client triggers a stage and the host agrees."""
    q = ctx.ref("quest", n=0)
    var = str(ctx.layout.extra("quest", "var", "labvar"))
    settle = ctx.p("settle_s")
    ctx.refs_exist([q], "the lab quest")
    ctx.agree("the quest is at the same stage on both copies at the start", lambda m: m.stage(q), settle, area="plugin/hooks (quest stage), plugin/snapshot")
    ok, why = ctx.host.set_stage(q, 10)
    ctx.check("the host sets stage 10", ok, "set", why or "set", cls="TEST", area="tools/lab (setstage)")
    ctx.agree("the client sees stage 10", lambda m: m.stage(q), settle, 10, area="plugin/hooks (quest stage), synccore")
    if ctx.client.var(q, var) is None:
        ctx.skip("the client sees the quest variable", f"this build cannot read quest variable {var!r} (no bridge read; see docs/tools/lab.md)")
    else:
        ok, why = ctx.host.set_var(q, var, 7)
        ctx.check("the host sets the quest variable to 7", ok, "set", why or "set", cls="TEST", area="tools/lab (setvar)")
        ctx.agree("the client sees the quest variable", lambda m: m.var(q, var), settle, 7, area="plugin/hooks (quest var), synccore")
    ok, why = ctx.client.set_stage(q, 20)
    ctx.check("the client triggers stage 20", ok, "set", why or "set", cls="TEST", area="tools/lab (setstage)")
    ctx.agree("the host agrees on stage 20", lambda m: m.stage(q), settle, 20, area="plugin/intents (guest stage), plugin/hooks")
    ok, why = ctx.host.set_stage(q, 30)
    ctx.check("the host sets stage 30", ok, "set", why or "set", cls="TEST", area="tools/lab (setstage)")
    ctx.agree("both copies are at stage 30", lambda m: m.stage(q), settle, 30, area="plugin/hooks (quest stage), synccore")
    ctx.sleep(ctx.p("hold_s"))
    st = ctx.both(lambda m: m.stage(q))
    ctx.check("the stage does not roll back", st[HOST] == 30 and st[CLIENT] == 30, 30, st, area="plugin/hooks (guest rollback), synccore")
    return {}


# spawn also loads the save and enters the lab cell (minutes on a loaded PC: the first in-game run used 70+ s of its 120 s before the first check);
# every other room starts inside the lab
SPAWN_BOX_S = 240.0


class RoomSpec:
    def __init__(self, name: str, fn: Callable[[Ctx], Any], area: str, cost_s: float, budget_s: float = 0.0):
        self.name, self.fn, self.area, self.cost_s, self.budget_s = name, fn, area, cost_s, budget_s


# cost_s: a guess of the run time in a game, only used to split rooms over pairs
ROOMS: Dict[str, RoomSpec] = {s.name: s for s in (
    RoomSpec("spawn", room_spawn, "plugin/coop, plugin/avatar, plugin/areadigest", 60, budget_s=SPAWN_BOX_S),
    RoomSpec("walk", room_walk, "plugin/avatar, plugin/avatar/engine", 90),
    RoomSpec("fight", room_fight, "plugin/combat, plugin/actors", 120),
    RoomSpec("loot", room_loot, "plugin/loot, plugin/intents", 70),
    RoomSpec("door", room_door, "plugin/hooks_world, plugin/intents", 50),
    RoomSpec("npc", room_npc, "plugin/actors, synccore/include/fnvcoop/ownership.h", 100),
    RoomSpec("vats", room_vats, "plugin/vats, plugin/combat", 120),
    RoomSpec("quest", room_quest, "plugin/hooks, plugin/snapshot, synccore", 50),
)}
ROOM_ORDER = tuple(ROOMS)


def split_rooms(rooms: List[str], pairs: int) -> List[List[str]]:
    """Spawn first on every pair; the other rooms go, most expensive first, onto the pair with the least work so far (stable)."""
    pairs = max(1, pairs)
    plan: List[List[str]] = [["spawn"] for _ in range(pairs)]
    load = [ROOMS["spawn"].cost_s] * pairs
    rest = sorted((r for r in rooms if r != "spawn"), key=lambda r: (-ROOMS[r].cost_s, ROOM_ORDER.index(r)))
    for r in rest:
        k = min(range(pairs), key=lambda i: (load[i], i))
        plan[k].append(r)
        load[k] += ROOMS[r].cost_s
    for p in plan:
        p[1:] = sorted(p[1:], key=ROOM_ORDER.index)
    return plan
