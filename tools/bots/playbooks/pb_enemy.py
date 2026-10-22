"""Enemy / NPC sync playbooks (FULL-SYNC-PLAN section 6, "in-game two-copy tests"): the make-or-break of the mod.

    enemy_fight         both players against the same hostile NPCs: owner, AI, health and positions agree on every machine while the
                        NPCs move and attack; each shot is applied once (no double damage); each death happens once and the kill is
                        credited once; no duplicate NPCs; an NPC's hit on a player lands once; animation check clean (attacks,
                        staggers, weapon draw, death) with the FIGHT thresholds.
    enemy_handoff       the host walks away in the middle of the fight: ownership of every living NPC moves to the client once, the AI
                        runs on exactly one machine at every instant, nothing jumps, nothing is revived or duplicated, the fight goes
                        on (a kill by the new owner, then a kill by the former owner that is resolved through the new owner), and
                        ownership does not flap when the host comes back to the border.
    enemy_spawn         an encounter appears once: the same NPCs (net ids, leveled picks) on every machine, one owner each, still the
                        same after a quiet period and after the players leave and come back, and each dies once.
    corpse_loot_race    both kill a ganger who carries loot; the corpse shows the same loot everywhere; both grab it at the same instant:
                        every unit goes to exactly one player, nothing is lost or duplicated.

Each runs against the fake world (fakecoop.FakeCoop(npc_ai=True), see fakenpc.py) and, with tools/bots/run_fight.py, in a game with two
copies. NOTHING here has run in a game. Checks that need a bridge method the build does not have yet (coop.combat) are skipped with a
note, never faked; coop.owner is required (the test fails with the reason if the build has no actor-ownership adapter).
"""
from __future__ import annotations

import math
import threading
from typing import Any, Dict, List, Optional, Set, Tuple

from . import probe as P
from .core import Ctx, Machine, Playbook, StepFailure
from .fakecoop import FakeCoop
from .pb_combat import gather, goto_or_teleport, walk_to

RAIDER_BASE = "FalloutNV.esm:DBB5A0"
GECKO_BASE = "FalloutNV.esm:DBB5A1"
HOSTILES = ["FalloutNV.esm:C00001", "FalloutNV.esm:C00002", "FalloutNV.esm:C00003"]
DEFAULT_POS_TOL = 150.0         # FULL-SYNC-PLAN section 4: more than 2 m off is a desync (the rubber-band rule), less is interpolation
JUMP_BASE = 150.0               # an NPC that moved more than this between two polls, on top of what its speed explains, jumped (the
NPC_MAX_SPEED = 600.0           # rubber-band rule of tools/animcheck/config.py; 600 u/s is faster than any walking or running creature)


# ---------------------------------------------------------------------------------------------------------------------------------
class PerMachine(dict):
    """A reference whose runtime id differs on every machine (an NPC that was created at run time): {machine name: ref}. `key` names it
    (its net id). Placed NPCs have the same id everywhere and are plain strings."""

    def __init__(self, mapping: Dict[str, str], key: str) -> None:
        super().__init__(mapping)
        self.key = key


def ref_on(target: Any, m: Machine) -> str:
    if isinstance(target, PerMachine):
        if m.name not in target:
            raise StepFailure(f"{m.name} has no copy of {target.key}", "check")
        return target[m.name]
    return target


def key_of(target: Any) -> str:
    return target.key if isinstance(target, PerMachine) else target


class Ledger:
    """What the shooters did: shots that landed (fakes know it) and shots fired, per target."""

    def __init__(self) -> None:
        self.hits: Dict[str, int] = {}
        self.fired: Dict[str, int] = {}

    def add(self, target: Any, res: Any) -> None:
        target = key_of(target)
        d = getattr(res, "data", None) or {}
        self.fired[target] = self.fired.get(target, 0) + int(d.get("fired", d.get("hits", 1 if getattr(res, "ok", False) else 0)))
        if "hits" in d:
            self.hits[target] = self.hits.get(target, 0) + int(d["hits"])


ENGINE_STATS = ("combat.status", "anim.npc", "actors.port", "coop.status")      # bridge methods whose answer is saved next to the result, never asserted on


def capture_stats(ctx: Ctx, tag: str) -> None:
    """Save what the game's own combat / NPC-animation counters say on every machine (engine_stats.json in the output directory) so a
    human can compare them with the test's verdict. Not asserted: their meaning is not verified in a game yet. Missing methods are skipped."""
    import json
    import os
    out: Dict[str, Any] = {}
    for m in ctx.present:
        for method in ENGINE_STATS:
            try:
                out.setdefault(m.name, {})[method] = m.bot.nv.call(method, {}, timeout_s=5)
            except Exception:  # noqa: BLE001   a build without the method, or a fake: nothing to save
                continue
    if not out:
        return
    path = os.path.join(ctx.outdir, "engine_stats.json")
    try:
        with open(path, encoding="utf-8") as f:
            all_ = json.load(f)
    except (OSError, ValueError):
        all_ = {}
    all_[tag] = out
    with open(path, "w", encoding="utf-8") as f:
        json.dump(all_, f, indent=1, default=str)


def living(ctx: Ctx, hostiles: List[str]) -> List[str]:
    return [r for r in hostiles if ctx.host.bot.target_alive(r)]


def owner_machine(ctx: Ctx, owner_idx: int) -> Machine:
    return ctx.machines[owner_idx] if 0 <= owner_idx < len(ctx.machines) else ctx.host


def dist3(a, b) -> float:
    return math.dist(a, b)


def check_in_sync(ctx: Ctx, refs: List[Any], tol: float, what: str = "") -> Dict[str, Tuple[int, int]]:
    """For every living NPC in `refs`: every machine agrees it is alive, on its health, on (owner, epoch); the AI runs on exactly the
    owner's machine; every other machine shows it within `tol` units of where the owner has it. Returns {ref: (owner, epoch)}."""
    out: Dict[str, Tuple[int, int]] = {}
    for r in refs:
        k = key_of(r)
        alive = ctx.same_everywhere(f"{k} alive{what}", lambda m, r=r: m.bot.target_alive(ref_on(r, m)))
        if not alive:
            continue
        ctx.same_everywhere(f"{k} health{what}", lambda m, r=r: round(float(m.bot.health(ref_on(r, m)))))
        info = {m.name: P.owner_info(m.bot, ref_on(r, m)) for m in ctx.present}
        unknown = [n for n, i in info.items() if not i.get("known")]
        ctx.expect(not unknown, f"{k}: {unknown} do not know this NPC's owner (ActorSync does not track it there)")
        pending = [n for n, i in info.items() if i.get("pending")]
        ctx.expect(not pending, f"{k}: a handoff is still pending on {pending}")
        pairs = {(int(i["owner"]), int(i["epoch"])) for i in info.values()}
        ctx.expect(len(pairs) == 1, f"{k}: machines disagree on (owner, epoch): { {n: (i['owner'], i['epoch']) for n, i in info.items()} }")
        owner, epoch = next(iter(pairs))
        om = owner_machine(ctx, owner)
        ais = sorted(n for n, i in info.items() if i.get("ai"))
        ctx.expect(ais == [om.name], f"{k}: the AI runs on {ais or 'no machine'}, it must run on exactly the owner's machine ({om.name})")
        ref_pos = P.actor_pos(om.bot, ref_on(r, om))
        for m in ctx.present:
            if m.name == om.name:
                continue
            d = dist3(P.actor_pos(m.bot, ref_on(r, m)), ref_pos)
            ctx.expect(d <= tol, f"{k}: on {m.name} the NPC is {d:.0f} units from where its owner ({om.name}) has it (limit {tol:.0f})")
        out[k] = (owner, epoch)
    return out


STANDOFF_MIN = 150.0            # a shooter closer than this stands inside the target: the aim points at the floor and every shot misses
STANDOFF_DIST = 350.0


def standoff(ctx: Ctx, target: Any, shooters: List[Machine]) -> None:
    """Every shooter that stands on top of `target` (the setup's MoveTo puts the players right on the NPC) steps back to STANDOFF_DIST,
    each to its own side so they do not block each other's line of fire."""
    for i, m in enumerate(shooters):
        tp = P.actor_pos(m.bot, ref_on(target, m))
        me = tuple(float(x) for x in m.bot.pos())
        if dist3(me[:2], tp[:2]) >= STANDOFF_MIN:
            continue
        a = math.radians(90.0 * i)
        x, y = tp[0] + STANDOFF_DIST * math.cos(a), tp[1] + STANDOFF_DIST * math.sin(a)
        try:            # a hop, not a walk: the NPC's own collision traps a player standing inside it
            r = m.bot.goto_pos(x, y, me[2], mode="teleport")
        except TypeError:           # the fake world's bot has no modes
            r = m.bot.goto_pos(x, y, me[2])
        ctx.must(r, f"{m.name} steps back from {key_of(target)} to shoot it")


def volley(ctx: Ctx, target: Any, ledger: Ledger, shooters: Optional[List[Machine]] = None, max_volleys: int = 25,
           gap_s: float = 0.3) -> None:
    """The shooters aim at `target` and fire at the same time, volley after volley, until it is dead on every machine."""
    ms = list(shooters if shooters is not None else ctx.present)
    ctx.equip_all()
    standoff(ctx, target, ms)
    for _ in range(max_volleys):
        def shoot(m: Machine):
            r = m.bot.aim_at(ref_on(target, m))
            if not r:
                return r
            return m.bot.fire(1)
        res = ctx.on_all(shoot, ms)
        for name, r in res.items():
            if not r and "dead" not in (r.reason or "") and "gone" not in (r.reason or ""):
                raise StepFailure(f"{name} could not shoot {key_of(target)}: {r.reason}", "action")
            ledger.add(target, r)
        ctx.advance(gap_s)
        ctx.settle()
        if all(not m.bot.target_alive(ref_on(target, m)) for m in ctx.present):
            return
    raise StepFailure(f"{key_of(target)} is still alive on some machine after {max_volleys} volleys: "
                      f"{ {m.name: m.bot.target_alive(ref_on(target, m)) for m in ctx.present} }", "check")


def check_kill(ctx: Ctx, target: Any, hp_before: float, ledger: Ledger, killers: Optional[Set[int]] = None) -> None:
    """The target died: on every machine, exactly once, credited to exactly one player, and its health fell by what the shots did."""
    name = key_of(target)
    ctx.same_everywhere(f"{name} alive", lambda m: m.bot.target_alive(ref_on(target, m)))
    ctx.expect(not ctx.host.bot.target_alive(ref_on(target, ctx.host)), f"{name} is still alive")
    deaths = ctx.optional("death counts", lambda: {m.name: P.death_count(m.bot, ref_on(target, m)) for m in ctx.present})
    if deaths is not None:
        ctx.expect(all(v == 1 for v in deaths.values()), f"{name} died {deaths} times (must be exactly once on every machine)")
    credits = ctx.optional("kill credit", lambda: {m.name: sorted(P.kill_credits(m.bot, ref_on(target, m))) for m in ctx.present})
    if credits is not None:
        distinct = {tuple(v) for v in credits.values()}
        ctx.expect(len(distinct) == 1, f"{name}: machines disagree who killed it: {credits}")
        who = next(iter(distinct))
        ctx.expect(len(who) == 1, f"{name}: the kill is credited to {list(who)} (must be exactly one player): {credits}")
        if killers is not None:
            ctx.expect(who[0] in killers, f"{name}: the kill is credited to player {who[0]}, expected one of {sorted(killers)}")
    target = name
    exact = ctx.refs.get("hit_damage")
    hits = ledger.hits.get(target)
    if exact is not None and hits is not None:
        # `ledger` covers exactly the shots since `hp_before` was read: the last one killed it, the one before did not
        ctx.expect(hp_before <= hits * float(exact) + 1e-6,
                   f"{target}: it fell with {hits} landed shots of {exact} damage but had {hp_before:g} health: a hit was applied twice")
        ctx.expect(hits <= 1 or hp_before > (hits - 1) * float(exact) - 1e-6,
                   f"{target}: it survived {hits - 1} shots of {exact} damage that should have killed it ({hp_before:g} health): "
                   f"a hit was lost or applied too weakly")


def check_damage_exact(ctx: Ctx, target: str, hp_before: float, hp_after: float, ledger: Ledger) -> None:
    """A living NPC lost exactly what the landed shots did (fakes: exact; a game: at most fired * max_hit when the scenario says it)."""
    hits = ledger.hits.get(target, 0)
    exact = ctx.refs.get("hit_damage")
    drop = hp_before - hp_after
    if exact is not None:
        ctx.expect(abs(drop - hits * float(exact)) < 1e-6,
                   f"{target}: health fell by {drop:g} but {hits} landed shots of {exact} damage should take {hits * float(exact):g} "
                   f"(double damage or a lost hit)")
        return
    mx = ctx.refs.get("max_hit")
    if mx is not None:
        fired = ledger.fired.get(target, 0)
        ctx.expect(drop <= fired * float(mx) + 1e-6, f"{target}: health fell by {drop:g} from {fired} shots of at most {mx} (double damage?)")
    else:
        ctx.notes.append(f"{target}: per-shot damage is not known for this weapon (set max_hit): double-damage check skipped")


def current_hp(ctx: Ctx, r: Any) -> float:
    return float(ctx.same_everywhere(f"{key_of(r)} health", lambda m: round(float(m.bot.health(ref_on(r, m))))))


def dup_counts(ctx: Ctx, hostiles: List[str]) -> Optional[Dict[str, int]]:
    """Live count of each hostile's base form (same on every machine), or None when the build cannot count refs."""
    bases = sorted({b for b in (ctx.refs.get("hostile_base_of") or {}).values() if b})
    if not bases:
        return None

    def counts_agree() -> bool:
        return all(len({P.ref_count(m.bot, b) for m in ctx.present}) == 1 for b in bases)

    def counted() -> Dict[str, int]:
        # dev.countRefs counts the refs of the PLAYER'S cell. A player just teleported next to the enemies (the walk was stuck) is still in the
        # old cell for a few frames: in game 2026-10-22, enemy_handoff step 01, host 1 / client 0 right after "teleported there". Wait for the
        # machines to agree; if they never do, same_everywhere below names the ones that differ (a real missing or duplicate actor).
        try:
            ctx.wait_until("every machine counts the same live refs of each hostile base", counts_agree, timeout_s=ctx.opts.get("spawn_s", 15.0))
        except StepFailure:
            pass
        return {b: ctx.same_everywhere(f"live count of {b}", lambda m, b=b: P.ref_count(m.bot, b)) for b in bases}

    return ctx.optional("duplicate-NPC checks", counted)


def check_no_duplicates(ctx: Ctx, c0: Optional[Dict[str, int]], hostiles: List[str]) -> None:
    if c0 is None:
        return
    base_of = ctx.refs.get("hostile_base_of") or {}
    dead = [r for r in hostiles if not ctx.host.bot.target_alive(r)]
    for b, n0 in c0.items():
        want = n0 - sum(1 for r in dead if base_of.get(r) == b)
        got = ctx.same_everywhere(f"live count of {b}", lambda m, b=b: P.ref_count(m.bot, b))
        ctx.expect(got == want, f"{b}: {got} live refs but {want} expected ({n0} at the start, {len(dead)} hostiles dead): "
                   f"{'a duplicate exists' if got > want else 'one vanished'}")


class EnemyTrace:
    """What every machine said about the NPCs at every poll: (owner, epoch, ai, pos, hp). Filled by the waiting loops."""

    def __init__(self) -> None:
        self.rows: List[Dict[str, Any]] = []
        self.rounds = 0
        self._lock = threading.Lock()

    def sample(self, ctx: Ctx, refs: List[str]) -> List[Dict[str, Any]]:
        """One poll of every machine about every ref in `refs`. Returns the new rows. Safe to call from a sampler thread."""
        with self._lock:
            self.rounds += 1
            new = []
            for r in refs:
                for m in ctx.present:
                    i = P.owner_info(m.bot, r)
                    new.append({"round": self.rounds, "machine": m.name, "ref": r, "owner": int(i.get("owner", 255)),
                                "epoch": int(i.get("epoch", 0)), "ai": bool(i.get("ai")), "dead": bool(i.get("dead")),
                                "known": bool(i.get("known")), "pos": P.actor_pos(m.bot, r), "hp": float(m.bot.health(r)),
                                "t": ctx.clock.now()})
            self.rows.extend(new)
            return new

    def epochs_never_fall(self) -> Optional[str]:
        last: Dict[Tuple[str, str], int] = {}
        for x in self.rows:
            k = (x["machine"], x["ref"])
            if k in last and x["epoch"] < last[k]:
                return f"{x['machine']} saw the epoch of {x['ref']} fall from {last[k]} to {x['epoch']}"
            last[k] = max(last.get(k, 0), x["epoch"])
        return None

    def ai_conflict(self) -> Optional[str]:
        """The AI of one NPC ran on two machines in two polls in a row. One poll can catch a handoff half way (the machines are read one after
        the other), so a single overlap is not a conflict; the same overlap twice is."""
        by: Dict[Tuple[int, str], List[str]] = {}
        for x in self.rows:
            if x["ai"] and not x["dead"]:
                by.setdefault((x["round"], x["ref"]), []).append(x["machine"])
        rounds_of: Dict[str, List[int]] = {}
        for (rd, ref), ms in sorted(by.items()):
            if len(ms) > 1:
                rounds_of.setdefault(ref, []).append(rd)
        for ref, rds in rounds_of.items():
            for a, b in zip(rds, rds[1:]):
                if b == a + 1:
                    return f"the AI of {ref} ran on {by[(a, ref)]} at the same time (polls {a} and {b})"
        return None

    def hp_rise(self) -> Optional[str]:
        """An NPC's health went UP by more than regeneration explains (more than 2 and 3% of the most it had): revived, or restored from
        a stale state by the new owner."""
        last: Dict[Tuple[str, str], float] = {}
        peak: Dict[Tuple[str, str], float] = {}
        for x in self.rows:
            k = (x["machine"], x["ref"])
            peak[k] = max(peak.get(k, 0.0), x["hp"])
            if k in last and x["hp"] > last[k] + max(2.0, 0.03 * peak[k]):
                return f"{x['ref']} gained health on {x['machine']} ({last[k]:g} -> {x['hp']:g}): revived or restored from a stale state"
            last[k] = x["hp"]
        return None

    def jump_excess(self) -> Tuple[float, str]:
        """The largest distance an NPC moved between two polls beyond JUMP_BASE plus what NPC_MAX_SPEED explains for the time between
        them (a busy PC polls less often, which must not look like a jump). 0 when nothing jumped."""
        best, who = 0.0, ""
        last: Dict[Tuple[str, str], Tuple[float, Tuple[float, float, float]]] = {}
        for x in self.rows:
            k = (x["machine"], x["ref"])
            if k in last:
                t0, p0 = last[k]
                excess = dist3(p0, x["pos"]) - (JUMP_BASE + NPC_MAX_SPEED * max(0.0, x["t"] - t0))
                if excess > best:
                    best, who = excess, f"{x['ref']} on {x['machine']} ({dist3(p0, x['pos']):.0f} units in {x['t'] - t0:.2f} s)"
            last[k] = (x["t"], x["pos"])
        return best, who

    def changes(self, ref: str) -> int:
        """How often the (agreed) epoch of `ref` rose over the whole trace."""
        eps = sorted({x["epoch"] for x in self.rows if x["ref"] == ref and x["known"]})
        return max(0, len(eps) - 1)


def settled_pairs(ctx: Ctx, refs: List[str]) -> Optional[Dict[str, Tuple[int, int]]]:
    """{ref: (owner, epoch)} when every machine knows every living NPC, none is pending and they all agree; else None."""
    out: Dict[str, Tuple[int, int]] = {}
    for r in refs:
        pairs = set()
        for m in ctx.present:
            i = P.owner_info(m.bot, r)
            if not i.get("known") or i.get("pending"):
                return None
            pairs.add((int(i["owner"]), int(i["epoch"])))
        if len(pairs) != 1:
            return None
        out[r] = next(iter(pairs))
    return out


# ---- arrange: put the world into the state a step expects, so one step can run alone (--from-step / --only-step) ----------------------
# Only used by a partial run (Ctx.run_step); a full run never calls them. They use the bot's own operations (hop next to a ref, equip, read
# state) and never fake anything: what cannot be arranged (a target that is already dead, machines that disagree) is reported as an
# "arrange" failure that says to reload the scenario, instead of being papered over.
def arrange_failure(msg: str) -> StepFailure:
    return StepFailure(f"cannot arrange this step: {msg}", "arrange")


RELOAD_HINT = "reload the scenario (rerun without --from-step / --only-step, or add --reload)"


def require_alive(ctx: Ctx, refs: List[Any]) -> None:
    """Every ref in `refs` must be alive on every machine now; a dead or disagreeing one needs a scenario reload."""
    for r in refs:
        k = key_of(r)
        views = {m.name: m.bot.target_alive(ref_on(r, m)) for m in ctx.present}
        if len(set(views.values())) != 1:
            raise arrange_failure(f"{k} is alive on some machines and dead on others {views}: that is the desync, not a start state; {RELOAD_HINT}")
        if not all(views.values()):
            raise arrange_failure(f"{k} is already dead and this step needs it alive; {RELOAD_HINT}")


def place_near(ctx: Ctx, ref: Any, dist: float = 600.0, slack: float = 1.5) -> None:
    """Hop every player that is farther than `dist * slack` from `ref` to `dist` from it (teleport; a walk across the map costs a minute).
    Players already close are left where they are, so a rerun in place does not move anybody."""
    def one(m: Machine):
        target = ref_on(ref, m)
        tp = P.actor_pos(m.bot, target)
        me = tuple(float(x) for x in m.bot.pos())
        if dist3(me[:2], tp[:2]) <= dist * slack:
            return None
        try:
            return m.bot.goto_ref(target, dist, mode="teleport")
        except TypeError:           # the fake world's bot has no modes
            return m.bot.goto_ref(target, dist)
    with ctx.teleporting():
        for name, r in ctx.on_all(one).items():
            if r is not None:
                ctx.must(r, f"{name} hops next to {key_of(ref)}")


def owners_settled(ctx: Ctx, refs: List[Any]) -> None:
    """Wait until every machine knows who owns each living ref, nothing is pending and they agree (the state check_in_sync demands)."""
    alive = [r for r in refs if ctx.host.bot.target_alive(ref_on(r, ctx.host))]
    if not alive:
        return
    try:
        ctx.wait_until("every machine agrees on who owns the living enemies", lambda: settled_pairs(ctx, alive) is not None,
                       timeout_s=ctx.opts.get("arrange_s", 15.0))
    except StepFailure as e:
        raise arrange_failure(e.reason) from None


class FightState:
    """What enemy_fight's steps hand to each other. A partial run rebuilds it from the world (rebaseline_fight)."""

    def __init__(self) -> None:
        self.c0: Optional[Dict[str, int]] = None
        self.hp_start: Dict[str, float] = {}
        self.taken_start: Optional[Dict[str, int]] = None
        self.trace = EnemyTrace()
        self.killed: List[str] = []


def rebaseline_fight(ctx: Ctx, st: FightState, hostiles: List[str]) -> None:
    """Take the start-of-test numbers from the world as it is now: who is dead, how many live refs there were before the dead ones fell,
    the players' health and the hits they have taken so far."""
    capture_stats(ctx, "enemy_fight start")
    st.killed = [r for r in hostiles if not ctx.host.bot.target_alive(r)]
    counts = dup_counts(ctx, hostiles)
    if counts is not None:
        base_of = ctx.refs.get("hostile_base_of") or {}
        st.c0 = {b: n + sum(1 for r in st.killed if base_of.get(r) == b) for b, n in counts.items()}
    for m in ctx.present:
        st.hp_start[m.name] = float(m.bot.health())
    st.taken_start = ctx.optional("hits taken", lambda: {m.name: P.hits_taken(m.bot) for m in ctx.present})


# =================================================================================================================================
class EnemyFightPlaybook(Playbook):
    name = "enemy_fight"
    summary = "Both players vs the same hostile NPCs: same owner, health, positions and deaths everywhere; every hit and kill once."
    scenario = "goodsprings_enemies"
    needs_scenario_refs = ("hostiles",)
    anim = True
    anim_profile = "fight"
    resumable = True
    ENGAGE_ROUNDS = 4

    def run(self, ctx: Ctx) -> None:
        hostiles: List[str] = list(ctx.refs["hostiles"])
        tol = float(ctx.refs.get("pos_tol", DEFAULT_POS_TOL))
        st = FightState()
        trace = st.trace

        def arrange(alive: List[str], near: bool = True, owners: bool = True, rebaseline: bool = True) -> Any:
            """The arrange part of a step: the `alive` refs must be alive, the players stand near the first living enemy with the weapon
            equipped, the owners are settled, and the numbers the later checks compare against are taken from the world."""
            def go() -> None:
                require_alive(ctx, alive)
                if near:
                    place_near(ctx, (living(ctx, hostiles) or hostiles)[0])
                    ctx.equip_all()
                if owners:
                    owners_settled(ctx, hostiles)
                if rebaseline:
                    rebaseline_fight(ctx, st, hostiles)
            return go

        def walk() -> None:
            capture_stats(ctx, "enemy_fight start")
            gather(ctx, hostiles[0])
            for r in hostiles:
                ctx.same_everywhere(f"{r} alive", lambda m, r=r: m.bot.target_alive(r))
                ctx.expect(ctx.host.bot.target_alive(r), f"{r} should start alive")
            st.c0 = dup_counts(ctx, hostiles)

        def in_sync() -> None:
            check_in_sync(ctx, hostiles, tol)
            for m in ctx.present:
                st.hp_start[m.name] = float(m.bot.health())
            st.taken_start = ctx.optional("hits taken", lambda: {m.name: P.hits_taken(m.bot) for m in ctx.present})
            trace.sample(ctx, hostiles)

        def engage() -> None:
            for _ in range(self.ENGAGE_ROUNDS):
                ctx.advance(0.75)
                ctx.settle()
                check_in_sync(ctx, hostiles, tol, " while engaging")
                trace.sample(ctx, hostiles)
            check_no_duplicates(ctx, st.c0, hostiles)

        def kill(r: str):
            def body() -> None:
                hp_before = current_hp(ctx, r)
                seg = Ledger()
                volley(ctx, r, seg)
                ctx.settle()
                check_kill(ctx, r, hp_before, seg)
                st.killed.append(r)
                rest = [x for x in hostiles if x not in st.killed]
                check_in_sync(ctx, rest, tol, " after a kill")
                check_no_duplicates(ctx, st.c0, hostiles)
                if rest:
                    trace.sample(ctx, rest)
            return body

        def after() -> None:
            for r in hostiles:
                ctx.expect(not any(m.bot.target_alive(r) for m in ctx.present), f"{r} is alive somewhere")
            check_no_duplicates(ctx, st.c0, hostiles)
            taken0 = ctx.optional("hits taken", lambda: {m.name: P.hits_taken(m.bot) for m in ctx.present})
            ctx.advance(2.0)
            ctx.settle()
            taken1 = ctx.optional("hits taken", lambda: {m.name: P.hits_taken(m.bot) for m in ctx.present})
            if taken0 is not None and taken1 is not None:
                ctx.expect(taken0 == taken1, f"players were hit after every enemy was dead: {taken0} -> {taken1} (a dead NPC kept attacking)")
            npc_dmg = ctx.refs.get("npc_damage")
            if npc_dmg is not None and taken1 is not None and st.taken_start is not None:
                for m in ctx.present:
                    lost = st.hp_start[m.name] - float(m.bot.health())
                    n_hits = taken1[m.name] - st.taken_start[m.name]
                    ctx.expect(abs(lost - n_hits * float(npc_dmg)) < 1e-6,
                               f"{m.name} lost {lost:g} health from {n_hits} enemy hits of {npc_dmg} damage "
                               f"(each hit must land exactly once on the victim)")
            for r in hostiles:
                dead = ctx.optional("death counts", lambda r=r: {m.name: P.death_count(m.bot, r) for m in ctx.present})
                if dead is not None:
                    ctx.expect(all(v == 1 for v in dead.values()), f"{r} died {dead} times")
            problem = trace.hp_rise()
            ctx.expect(problem is None, problem or "")
            problem = trace.ai_conflict()
            ctx.expect(problem is None, problem or "")
            capture_stats(ctx, "enemy_fight end")

        ctx.run_step("everyone walks to the enemies", walk, arrange=arrange(hostiles, near=False, owners=False, rebaseline=False))
        ctx.run_step("the enemies are one set everywhere: owner, AI, health, positions", in_sync, arrange=arrange(hostiles))
        ctx.run_step("the enemies engage: they move and attack, positions and health stay in step", engage, arrange=arrange(hostiles),
                     compare=True)
        for i, r in enumerate(hostiles):
            ctx.run_step(f"both kill enemy {i + 1}: every hit once, one death, one credit", kill(r), arrange=arrange([r]))
        ctx.run_step("after the fight: all dead, nothing duplicated, the dead stay calm", after,
                     arrange=arrange([], near=False, owners=False))


# =================================================================================================================================
class EnemyHandoffPlaybook(Playbook):
    name = "enemy_handoff"
    summary = "The host walks away mid-fight: ownership moves once, AI on one machine, no jump/revive/duplicate, the fight goes on."
    scenario = "goodsprings_enemies"
    needs_scenario_refs = ("hostiles", "far_pos", "near_pos")
    min_players = 2
    max_players = 2
    anim = True
    anim_profile = "fight"
    MAX_CHANGES = 2             # ownership changes allowed per NPC over the whole playbook (away, and at most one way back)

    def run(self, ctx: Ctx) -> None:
        hostiles: List[str] = list(ctx.refs["hostiles"])
        tol = float(ctx.refs.get("pos_tol", DEFAULT_POS_TOL))
        far, near = ctx.refs["far_pos"], ctx.refs["near_pos"]
        host, client = ctx.host, ctx.clients[0]
        trace = EnemyTrace()
        epochs0: Dict[str, int] = {}
        epochs1: Dict[str, int] = {}
        c0: Optional[Dict[str, int]] = None
        killed: List[str] = []

        def mark_owner(name: str) -> None:
            if ctx.anim is not None:
                ctx.anim.npc_owner(name)

        with ctx.step("the fight starts: both players at the enemies, the host owns them"):
            capture_stats(ctx, "enemy_handoff start")
            gather(ctx, hostiles[0])
            ctx.equip_all()
            c0 = dup_counts(ctx, hostiles)
            ctx.advance(1.5)
            ctx.settle()
            pairs = check_in_sync(ctx, hostiles, tol)
            for r, (owner, epoch) in pairs.items():
                ctx.expect(owner == host.idx, f"{r}: the host should own an NPC both players stand next to, owner is slot {owner}")
                epochs0[r] = epoch
            r0 = hostiles[0]
            hp_before = current_hp(ctx, r0)
            res = host.bot.aim_at(r0)
            ctx.must(res, "host aims")
            seg = Ledger()
            seg.add(r0, host.bot.fire(1))
            ctx.advance(0.6)
            ctx.settle()
            check_damage_exact(ctx, r0, hp_before, current_hp(ctx, r0) if host.bot.target_alive(r0) else 0.0, seg)
            killed = [r for r in hostiles if not host.bot.target_alive(r)]
            trace.sample(ctx, living(ctx, hostiles))
            mark_owner(host.name)
        alive = living(ctx, hostiles)
        ctx.expect(len(alive) >= 2, f"the first shot left {len(alive)} living enemies; the test needs at least 2")

        marked: List[int] = []

        def watch() -> None:
            new = trace.sample(ctx, living(ctx, hostiles))
            if not marked and any(x["owner"] == client.idx for x in new):
                mark_owner(client.name)              # the animation check compares against the new owner from here on
                marked.append(1)

        with ctx.step("the host walks away: ownership of every living enemy moves to the client once"):
            with ctx.sampling(watch, ctx.opts.get("sample_s", 0.25)), ctx.walking(host):          # the handoff happens DURING the walk: watch the NPCs all the way
                ctx.must(walk_to(host, far), "host walks away")

            def moved() -> bool:
                watch()
                p = settled_pairs(ctx, living(ctx, hostiles))
                return p is not None and all(o == client.idx for o, _ in p.values())
            ctx.wait_until("ownership of every living enemy moves to the client on every machine", moved,
                           timeout_s=ctx.opts.get("handoff_s", 20.0))
            ctx.settle()
            pairs = check_in_sync(ctx, living(ctx, hostiles), tol, " after the handoff")
            for r, (owner, epoch) in pairs.items():
                ctx.expect(owner == client.idx, f"{r}: owner is slot {owner}, expected the client ({client.idx})")
                ctx.expect(epoch == epochs0[r] + 1, f"{r}: epoch went {epochs0[r]} -> {epoch}; one handoff must raise it by exactly one")
                epochs1[r] = epoch
            check_no_duplicates(ctx, c0, hostiles)
            jump, who = trace.jump_excess()
            ctx.expect(jump <= 0, f"{who}: an enemy jumped between two polls during the handoff "
                       f"(respawn or Reset3D instead of taking the actor over?)")
            problem = trace.hp_rise()
            ctx.expect(problem is None, problem or "")
            problem = trace.ai_conflict()
            ctx.expect(problem is None, problem or "")

        with ctx.step("the fight goes on without the host: the new owner kills one, everybody sees it once"):
            target = living(ctx, hostiles)[0]
            hp_before = current_hp(ctx, target)
            seg = Ledger()
            volley(ctx, target, seg, shooters=[client])
            ctx.settle()
            check_kill(ctx, target, hp_before, seg, killers={client.idx})
            killed.append(target)
            rest = living(ctx, hostiles)
            check_in_sync(ctx, rest, tol, " after the client's kill")
            if rest:
                trace.sample(ctx, rest)
            check_no_duplicates(ctx, c0, hostiles)

        with ctx.step("the host comes back to the border: ownership must not flap"):
            with ctx.sampling(watch, ctx.opts.get("sample_s", 0.25)):
                ctx.must(walk_to(host, near), "host walks back")
            for _ in range(3):
                ctx.advance(1.0)
                ctx.settle()
                trace.sample(ctx, living(ctx, hostiles))
            for r, (owner, epoch) in check_in_sync(ctx, living(ctx, hostiles), tol, " at the border").items():
                ctx.expect((owner, epoch) == (client.idx, epochs1[r]),
                           f"{r}: ownership flapped at the border: epoch {epochs0[r]} -> {epochs1[r]} -> {epoch}, owner now {owner}")

        with ctx.step("the host joins in again: its shots are resolved through the new owner, once"):
            gather(ctx, living(ctx, hostiles)[0])
            for r in list(living(ctx, hostiles)):
                hp_before = current_hp(ctx, r)
                seg = Ledger()
                volley(ctx, r, seg, shooters=[host])
                ctx.settle()
                check_kill(ctx, r, hp_before, seg, killers={host.idx})
                killed.append(r)
                rest = living(ctx, hostiles)
                check_in_sync(ctx, rest, tol, " after the host's kill")

        with ctx.step("no recreation, no duplicates, no flapping overall"):
            for r in hostiles:
                ctx.expect(not any(m.bot.target_alive(r) for m in ctx.present), f"{r} is alive somewhere")
                ctx.expect(trace.changes(r) <= self.MAX_CHANGES, f"{r}: ownership changed {trace.changes(r)} times (limit {self.MAX_CHANGES})")
            check_no_duplicates(ctx, c0, hostiles)
            problem = trace.epochs_never_fall()
            ctx.expect(problem is None, problem or "")
            problem = trace.ai_conflict()
            ctx.expect(problem is None, problem or "")
            problem = trace.hp_rise()
            ctx.expect(problem is None, problem or "")
            jump, who = trace.jump_excess()
            ctx.expect(jump <= 0, f"{who}: an enemy jumped between two polls")
            capture_stats(ctx, "enemy_handoff end")


# =================================================================================================================================
def encounter_state(ctx: Ctx, bases: List[str]) -> Dict[str, Any]:
    """{machine: sorted list of (net id, base, that machine's ref)}, or None for a machine whose build cannot list them."""
    def one(m: Machine):
        try:
            return P.encounter_members(m.bot, bases)
        except P.MissingBridge:
            return None
    return {m.name: one(m) for m in ctx.present}


def member_keys(lists: Dict[str, Any]) -> Dict[str, list]:
    """What the machines must AGREE on: (net id, base) per member."""
    return {n: [(a, b) for a, b, _ in v] for n, v in lists.items()}


def per_machine_members(lists: Dict[str, Any], skip: Any = ()) -> List[PerMachine]:
    """One PerMachine per net id (not in `skip`), built from every machine's own ref for it. Members without a net id are left out."""
    ids = sorted({a for v in lists.values() for a, _, _ in v if a is not None and (a, ) not in skip and a not in skip})
    return [PerMachine({n: ref for n, v in lists.items() for a, _, ref in v if a == i}, i) for i in ids]


class EnemySpawnPlaybook(Playbook):
    name = "enemy_spawn"
    summary = "An encounter appears once: same NPCs, net ids and leveled picks everywhere; one owner each; still once later; each dies once."
    scenario = "goodsprings_enemies"
    needs_scenario_refs = ("marker", "base")
    anim = True
    anim_profile = "fight"
    anim_npcs = True
    SETTLE_ROUNDS = 5

    def run(self, ctx: Ctx) -> None:
        marker, base = ctx.refs["marker"], ctx.refs["base"]
        bases: List[str] = list(ctx.refs.get("bases") or [base])
        n_new = int(ctx.refs.get("count", 3))
        mode = ctx.refs.get("spawn_mode", "host")
        tol = float(ctx.refs.get("pos_tol", DEFAULT_POS_TOL))
        site = ctx.refs.get("site")
        away = ctx.refs.get("away")
        members0: List[Any] = []

        def count(m: Machine) -> int:
            return sum(P.ref_count(m.bot, b) for b in bases)

        def member_lists() -> Dict[str, Any]:
            return encounter_state(ctx, bases)

        with ctx.step("nothing of that kind is there yet"):
            capture_stats(ctx, "enemy_spawn start")
            if mode == "site" and away:
                for n, r in ctx.on_all(lambda m: walk_to(m, away)).items():
                    ctx.must(r, f"{n} walks to the start position")
            n0 = ctx.same_everywhere("count before", count)
            if mode != "site":
                for n, r in ctx.on_all(lambda m: goto_or_teleport(ctx, m, marker, 300)).items():
                    ctx.must(r, f"{n} walks to the marker")
            lists = member_lists()
            if all(v is not None for v in lists.values()):
                keys = member_keys(lists)
                members0 = keys[ctx.host.name]
                ctx.expect(all(v == members0 for v in keys.values()), f"the machines already disagree on the encounter members: {keys}")
        with ctx.step(f"the encounter appears: {n_new} NPCs on every machine, no more, no less"):
            if mode == "site":
                for n, r in ctx.on_all(lambda m: walk_to(m, site)).items():       # both arrive at the same time: both could trigger it
                    ctx.must(r, f"{n} walks into the site")
            else:
                for _ in range(n_new):
                    ctx.must(P.spawn(ctx.host.bot, base, marker), "host spawns")
            ctx.wait_until(f"{n_new} new NPCs on every machine", lambda: all(count(m) >= n0 + n_new for m in ctx.present),
                           timeout_s=ctx.opts.get("spawn_s", 15.0))
            ctx.advance(1.0)
            ctx.settle()
            got = {m.name: count(m) for m in ctx.present}
            ctx.expect(all(v == n0 + n_new for v in got.values()), f"counts after the encounter appeared: {got} (must be {n0 + n_new} everywhere)")
        with ctx.step("every machine has the same NPCs: net ids, leveled picks, one owner, one AI"):
            lists = member_lists()
            if all(v is not None for v in lists.values()):
                keys = member_keys(lists)
                distinct = {tuple(v) for v in keys.values()}
                ctx.expect(len(distinct) == 1, f"the machines disagree on the encounter members (net id, base): {keys}")
                ids = [x[0] for x in keys[ctx.host.name]]
                ctx.expect(len(ids) == len(set(ids)), f"duplicate net ids in the encounter: {ids}")
                new = [x for x in keys[ctx.host.name] if x not in members0]
                ctx.expect(len(new) in (0, n_new), f"{len(new)} new members, expected {n_new}")
                members = per_machine_members(lists, skip={x[0] for x in members0})
                if any(x[0] is None for x in keys[ctx.host.name]):
                    ctx.notes.append("some encounter members have no net id on this build: only the others are checked one by one")
                check_in_sync(ctx, members, tol, " (encounter member)")
            else:
                ctx.notes.append("no encounter member list in this build (dev.refsOf): only counts are compared")
        with ctx.step("it stays the same after a quiet period"):
            for _ in range(self.SETTLE_ROUNDS):
                ctx.advance(1.0)
                ctx.settle()
            got = {m.name: count(m) for m in ctx.present}
            ctx.expect(all(v == n0 + n_new for v in got.values()), f"counts later: {got} (a second roll or a clone appeared)")
        if mode == "site" and away:
            with ctx.step("the players leave and come back: the site does not roll again"):
                for n, r in ctx.on_all(lambda m: walk_to(m, away)).items():
                    ctx.must(r, f"{n} leaves")
                ctx.advance(2.0)
                ctx.settle()
                for n, r in ctx.on_all(lambda m: walk_to(m, site)).items():
                    ctx.must(r, f"{n} comes back")
                ctx.advance(1.5)
                ctx.settle()
                got = {m.name: count(m) for m in ctx.present}
                ctx.expect(all(v == n0 + n_new for v in got.values()), f"counts after coming back: {got} (the encounter spawned again)")
                lists = member_lists()
                if all(v is not None for v in lists.values()):
                    keys = member_keys(lists)
                    ctx.expect(len({tuple(v) for v in keys.values()}) == 1, f"the machines disagree on the members now: {keys}")
        with ctx.step("each of them dies once"):
            lists = member_lists()
            targets = (per_machine_members(lists, skip={x[0] for x in members0}) if all(v is not None for v in lists.values()) else [])
            ctx.equip_all()
            for rid in targets:
                hp_before = current_hp(ctx, rid)
                seg = Ledger()
                volley(ctx, rid, seg)
                ctx.settle()
                check_kill(ctx, rid, hp_before, seg)
            if targets:
                got = {m.name: count(m) for m in ctx.present}
                ctx.expect(all(v == n0 for v in got.values()), f"live count after the kills: {got} (must be back to {n0})")
            capture_stats(ctx, "enemy_spawn end")


# =================================================================================================================================
class CorpseLootRacePlaybook(Playbook):
    name = "corpse_loot_race"
    summary = "Both players kill a ganger and grab his loot at the same instant: each unit goes to exactly one player."
    scenario = "goodsprings_enemies"
    needs_scenario_refs = ("victim", "item")
    anim = True
    anim_npcs = True
    anim_profile = "fight"
    MAX_ROUNDS = 12

    def run(self, ctx: Ctx) -> None:
        victim, item = ctx.refs["victim"], ctx.refs["item"]
        want = int(ctx.refs.get("item_count", 3))
        def on_corpse(m: Machine) -> int:
            return P.holder_count(m.bot, item, victim)

        def held(m: Machine) -> int:
            return P.item_count(m.bot, item)

        with ctx.step("everyone at the ganger, he carries the same loot everywhere"):
            capture_stats(ctx, "corpse_loot_race start")
            gather(ctx, victim)
            ctx.equip_all()
            ctx.advance(1.0)
            ctx.settle()
            n = ctx.same_everywhere("loot on the ganger", on_corpse)
            ctx.expect(n == want, f"the ganger carries {n} x {item} on every machine, the scenario put {want} on him "
                       f"(a loot change on a living NPC did not replicate)")
        before = sum(held(m) for m in ctx.machines)
        with ctx.step("both kill him: one death, one credit, a corpse"):
            hp_before = current_hp(ctx, victim)
            seg = Ledger()
            volley(ctx, victim, seg)
            ctx.settle()
            check_kill(ctx, victim, hp_before, seg)
        with ctx.step("the corpse shows the same loot on every machine"):
            n = ctx.same_everywhere("loot on the corpse", on_corpse)
            ctx.expect(n == want, f"the corpse holds {n} x {item} on every machine, expected {want} (loot lost or duplicated on death)")
        with ctx.step("all grab at the same instant: every unit goes to exactly one player"):
            def grab(m: Machine) -> int:
                got = 0
                for _ in range(self.MAX_ROUNDS):
                    if on_corpse(m) <= 0:
                        break
                    o = m.bot.open_container(victim)
                    if not o:
                        m.bot.wait(0.2)                   # the other player has the corpse open: wait for the turn
                        continue
                    try:
                        if on_corpse(m) > 0 and m.bot.take(item, 1, victim):
                            got += 1
                    finally:
                        P.close_container(m.bot, victim)
                return got
            took = ctx.on_all(grab)
            ctx.settle()
            gained = sum(held(m) for m in ctx.machines) - before
            ctx.expect(gained == want, f"{gained} x {item} arrived in the party's inventories, the corpse held {want} "
                       f"({'duplicated' if gained > want else 'lost'}); each player took: {took}")
            ctx.expect(sum(took.values()) == want, f"the players report {sum(took.values())} successful takes for {want} units: {took}")
            left = ctx.same_everywhere("loot left on the corpse", on_corpse)
            ctx.expect(left == 0, f"{left} x {item} are still on the corpse on every machine after it was all taken")
        with ctx.step("a late looter finds nothing and gets nothing"):
            late = ctx.clients[0]
            h0 = held(late)
            o = late.bot.open_container(victim)
            if o:
                t = late.bot.take(item, 1, victim)
                ctx.expect(not t, f"{late.name} took an item from an empty corpse")
                P.close_container(late.bot, victim)
            ctx.settle()
            ctx.expect(held(late) == h0, f"{late.name}'s item count changed from {h0} to {held(late)} after looting an empty corpse")
            ctx.expect(sum(held(m) for m in ctx.machines) - before == want, "the party's total changed after the race was over")
            capture_stats(ctx, "corpse_loot_race end")


# =================================================================================================================================
# fake worlds
def _npc(w: FakeCoop, ref: str, kind: str, pos, base: str, hp: int = 150, **kw) -> None:
    w.add_ref(ref, kind, pos=pos, cell="goodsprings", base=base, hp=hp, ai=True, **kw)


def _players(w: FakeCoop, n: int, start) -> None:
    for s in range(n):
        w.set_player(s, pos=(start[0], start[1] + 100 * s, 0), cell="goodsprings")
        w.players[s]["hp"] = 5000


def fake_enemy_fight(n: int = 2, bugs: Optional[Set[str]] = None, **kw) -> Tuple[FakeCoop, dict]:
    kw.setdefault("walk_speed", 120.0)
    w = FakeCoop(n, bugs, npc_ai=True, **kw)
    # health 125 and 75 are odd multiples of the 25 damage of a shot: the killing shot is the FIRST of a two-shooter volley, so the
    # second shooter fires at a corpse (the dup_kill case)
    _npc(w, HOSTILES[0], "raider", (20000, 0, 0), RAIDER_BASE, hp=125)
    _npc(w, HOSTILES[1], "raider", (20080, 0, 0), RAIDER_BASE, hp=125)
    _npc(w, HOSTILES[2], "creature", (20160, 0, 0), GECKO_BASE, hp=75)
    _players(w, n, (17500, 0, 0))
    return w, {"hostiles": list(HOSTILES), "hostile_base_of": {HOSTILES[0]: RAIDER_BASE, HOSTILES[1]: RAIDER_BASE, HOSTILES[2]: GECKO_BASE},
               "weapon": "WEAPON", "hit_damage": 25, "npc_damage": 5}


def fake_enemy_handoff(n: int = 2, bugs: Optional[Set[str]] = None, **kw) -> Tuple[FakeCoop, dict]:
    kw.setdefault("walk_speed", 240.0)
    w = FakeCoop(n, bugs, npc_ai=True, **kw)
    for i, r in enumerate(HOSTILES):
        _npc(w, r, "raider", (0, 40 * i, 0), RAIDER_BASE, hp=200)
    # the players stand 1300 units from the NPCs' post: the NPCs run about 900 units to reach them, so at the handoff they are far
    # from where a reset-to-spawn bug would put them
    w.set_player(0, pos=(1300, 0, 0), cell="goodsprings")
    for s in range(1, n):
        w.set_player(s, pos=(1400, 0, 0), cell="goodsprings")
    for s in range(n):
        w.players[s]["hp"] = 5000
    return w, {"hostiles": list(HOSTILES), "hostile_base_of": {r: RAIDER_BASE for r in HOSTILES}, "weapon": "WEAPON", "hit_damage": 25,
               "npc_damage": 5, "far_pos": {"x": 4800, "y": 0, "z": 0, "cell": "goodsprings"},
               "near_pos": {"x": 320, "y": 0, "z": 0, "cell": "goodsprings"}}


SITE_REFS = ["FalloutNV.esm:D00001", "FalloutNV.esm:D00002", "FalloutNV.esm:D00003"]
SITE_BASES = ["FalloutNV.esm:DBB5B0", "FalloutNV.esm:DBB5B1"]
SITE_MARKER = "FalloutNV.esm:D0FFFF"


def fake_enemy_spawn(n: int = 2, bugs: Optional[Set[str]] = None, **kw) -> Tuple[FakeCoop, dict]:
    kw.setdefault("walk_speed", 240.0)
    w = FakeCoop(n, bugs, npc_ai=True, **kw)
    w.add_ref(SITE_MARKER, "marker", pos=(20000, 0, 0), cell="goodsprings", base="MARKERBASE")
    # the owner's leveled pick: the base of each member comes from a pool, picked once (here: by index)
    specs = [(r, "creature", (20000 + 90 * i, 0, 0), {"base": SITE_BASES[i % 2], "hp": 60, "ai": True}) for i, r in enumerate(SITE_REFS)]
    w.add_site((20000, 0, 0), "goodsprings", specs, radius=2500.0)
    _players(w, n, (15000, 0, 0))
    return w, {"marker": SITE_MARKER, "base": SITE_BASES[0], "bases": list(SITE_BASES), "count": len(SITE_REFS), "spawn_mode": "site",
               "site": {"x": 20000, "y": 0, "z": 0, "cell": "goodsprings"}, "away": {"x": 15000, "y": 0, "z": 0, "cell": "goodsprings"},
               "weapon": "WEAPON", "hit_damage": 25, "npc_damage": 5}


CORPSE_VICTIM = "FalloutNV.esm:C10001"


def fake_corpse(n: int = 2, bugs: Optional[Set[str]] = None, **kw) -> Tuple[FakeCoop, dict]:
    kw.setdefault("walk_speed", 120.0)
    w = FakeCoop(n, bugs, npc_ai=True, **kw)
    _npc(w, CORPSE_VICTIM, "raider", (4000, 0, 0), RAIDER_BASE, hp=100, items={"STIMPAK": 3})
    _players(w, n, (2000, 0, 0))
    return w, {"victim": CORPSE_VICTIM, "item": "STIMPAK", "item_count": 3, "weapon": "WEAPON", "hit_damage": 25, "npc_damage": 5,
               "hostile_base_of": {CORPSE_VICTIM: RAIDER_BASE}}
