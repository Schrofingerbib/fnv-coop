"""Two-player VATS playbooks, one per weapon class: gun, melee, explosive, energy (FULL-SYNC-PLAN 3.4, docs/tools/vats.md).

Each playbook drives REAL-TIME VATS on both machines through the bridge methods of plugin/vats (vats.state, vats.select_target, vats.select_limb,
vats.fire, vats.cancel, vats.set, vats.status) plus the input driver of plugin/bots (bot.input: tap Vats / tap Activate), and checks:
  * every machine can read the VATS state and CHOOSE EVERY LIMB of the target (the bot-side limb choice the plan calls a blocking task);
  * a VATS shot at a chosen limb damages the target once, the same everywhere, and the other machines' world keeps running (frames advance);
  * the crippled-limb state of the target is identical on every machine;
  * the same with the kill-cam on and off, ending in a death that happens once;
  * (optional, `slowmo`) the host's party slow-mo slows every machine together and puts the time scale back.
Nothing here has run in a game. The fake world below (VatsWorld) is what the unit tests and the mutation twins use: each twin is one wrong behaviour
the playbooks must catch. The playbooks are NOT in playbooks/registry.py (its tests tie every entry to a scenario mapper); this module has its own
registry and tools/bots/run_vats.py runs it.
"""
from __future__ import annotations

import math
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from . import fakecoop
from . import probe as P
from .core import Ctx, Playbook
from .fakecoop import FakeBot, FakeCoop, FakeNv, norm
from .probe import MissingBridge, Result

LIMBS = ("head", "torso", "left_arm", "right_arm", "left_leg", "right_leg")
# limb condition ActorValue names (the engine strings found in the exe; plugin/vats vats_types.h)
LIMB_AV = {"head": "PerceptionCondition", "torso": "EnduranceCondition", "left_arm": "LeftAttackCondition", "right_arm": "RightAttackCondition",
           "left_leg": "LeftMobilityCondition", "right_leg": "RightMobilityCondition"}
CLASSES = ("gun", "melee", "explosive", "energy")
TARGET = "FalloutNV.esm:F00003"
NEIGHBOR = "FalloutNV.esm:F00005"
WEAPON_IDS = {c: f"WEAPON_{c.upper()}" for c in CLASSES}     # fake world item names; a live run passes real ones (run_vats.py --weapon gun=...)


# ---- the bridge side: helpers every playbook uses -----------------------------------------------------------------------------------


def _call(bot, method: str, params: Optional[dict] = None) -> Dict[str, Any]:
    try:
        return bot.nv.call(method, params or {}, timeout_s=15.0)
    except Exception as e:  # noqa: BLE001
        name = type(e).__name__
        if name in ("ConnectionLost", "GameCrashed"):
            raise
        if getattr(e, "code", None) in (-32601, -32600) or "unknown method" in str(e).lower():
            raise MissingBridge(f"bridge method {method!r} is not in this build (nvcoop.ini [vats] enabled=1 and [dev] bots=1 are needed)") from e
        raise


def ref_id(ref: str) -> int:
    return int(P.hexid(ref), 16)


def state(bot) -> Dict[str, Any]:
    return _call(bot, "vats.state")


def status(bot) -> Dict[str, Any]:
    return _call(bot, "vats.status")


def selecting(st: Dict[str, Any]) -> bool:
    return int(st.get("mode", 0)) in (1, 2, 3)


def _wait_a_little(bot, seconds: float) -> None:
    if getattr(bot, "is_fake", False):
        bot.nv.pump()
    else:
        time.sleep(seconds)


def enter(bot, ref: str, timeout_s: float = 6.0) -> Result:
    """Press the VATS control and wait until the engine is in target / limb selection."""
    if selecting(state(bot)):
        return Result(True)
    _call(bot, "bot.input", {"tap": ["Vats"]})
    limit = 0.2 if getattr(bot, "is_fake", False) else timeout_s
    t0 = time.monotonic()
    while True:
        st = state(bot)
        if selecting(st):
            return Result(True, "", {"state": st})
        if time.monotonic() - t0 > limit:
            return Result(False, "VATS did not open (no weapon, no target in view, or no AP)")
        _wait_a_little(bot, 0.1)


def leave(bot) -> Result:
    st = state(bot)
    if not selecting(st):
        return Result(True)
    _call(bot, "vats.cancel")
    _call(bot, "bot.input", {"tap": ["Vats"]})       # the same control closes the mode again
    return Result(not selecting(state(bot)), "VATS did not close")


def select(bot, ref: str, limb: str) -> Result:
    r = _call(bot, "vats.select_target", {"ref": f"0x{ref_id(ref):X}"})
    if not r.get("ok"):
        return Result(False, f"select target: {r.get('reason')}")
    r = _call(bot, "vats.select_limb", {"limb": limb})
    if not r.get("ok"):
        return Result(False, f"select limb {limb}: {r.get('reason')}")
    return Result(True, "", {"reply": r})


def limbs_of(st: Dict[str, Any]) -> List[str]:
    return [str(x.get("limb")) for x in st.get("limbs", [])]


def attack(bot, ref: str, limb: str, timeout_s: float = 20.0, during: Optional[Callable[[], None]] = None) -> Result:
    """Enter VATS, choose target and limb, queue the action; the real-time auto fire plays it (vats.fire is the fallback). Returns when VATS is closed.
    `during` runs once while VATS is open and nothing is chosen yet: the caller measures there that the OTHER machines' world keeps running."""
    r = enter(bot, ref)
    if not r:
        return r
    if during is not None:
        during()
    r = select(bot, ref, limb)
    if not r:
        return r
    _call(bot, "bot.input", {"tap": ["Activate"]})                 # queue the action on the highlighted limb
    t0 = time.monotonic()
    manual = False
    while True:
        st = state(bot)
        mode = int(st.get("mode", 0))
        if mode == 0:
            return Result(True)
        if mode in (1, 2, 3) and int(st.get("queued", 0)) > 0 and not manual and (getattr(bot, "is_fake", False) or time.monotonic() - t0 > 2.0):
            _call(bot, "vats.fire")                                # auto fire is off or late: start the playback by hand
            manual = True
        if time.monotonic() - t0 > timeout_s:
            return Result(False, f"the VATS shot did not finish (mode {mode}, queued {st.get('queued')})")
        _wait_a_little(bot, 0.05)


def limb_conditions(bot, ref: str) -> Tuple[float, ...]:
    """The six limb condition values of `ref` as THIS machine sees them (a crippled limb is 0)."""
    if callable(getattr(bot, "limb_state", None)):
        return tuple(bot.limb_state(ref))
    return tuple(round(float(P._eval_ref(bot, ref, f"GetActorValue {LIMB_AV[l]}")), 1) for l in LIMBS)   # noqa: SLF001


def hits_applied(bot) -> int:
    """Hits this machine applied as a victim's owner (combat.status stats.hitsApplied); summed over the machines it is the number of hits that landed."""
    if callable(getattr(bot, "hits_applied", None)):
        return int(bot.hits_applied())
    return int(((_call(bot, "combat.status").get("stats")) or {}).get("hitsApplied", 0))


def set_health(bot, ref: str, hp: int) -> Result:
    if callable(getattr(bot, "set_health", None)):
        return bot.set_health(ref, hp)
    return P._exec_ref(bot, ref, f"setav Health {hp}")        # noqa: SLF001


def revive(bot, ref: str, hp: int = 100) -> Result:
    if callable(getattr(bot, "revive", None)):
        return bot.revive(ref, hp)
    r = P._exec_ref(bot, ref, "resurrect 1")                   # noqa: SLF001
    return r if not r else P._exec_ref(bot, ref, f"setav Health {hp}")   # noqa: SLF001


def give_and_equip(bot, weapon: str) -> Result:
    if callable(getattr(bot, "give_item", None)):
        bot.give_item(weapon, 1)
    else:
        P.seed_items(bot, "player", weapon, 1)
    return bot.equip(weapon)


def set_config(bot, **kw) -> Result:
    """Host only (the host's values win on every machine): killcam / partyslowmo / autofire as 0 or 1."""
    r = _call(bot, "vats.set", {k: (1 if v else 0) for k, v in kw.items()})
    return Result(bool(r.get("ok")), str(r.get("reason", "")), r)


def machine_settings(bot) -> Dict[str, Any]:
    return (status(bot).get("sync") or {}).get("settings", {})


# ---- the playbooks ------------------------------------------------------------------------------------------------------------------


class VatsClassPlaybook(Playbook):
    name = "vats_gun"
    weapon_class = "gun"
    summary = "Both players use real-time VATS with a gun on one target: every limb can be chosen, damage and crippled limbs match everywhere, the world keeps running."
    scenario = "vats_hit"
    needs_scenario_refs = ("target", "weapon")
    anim = False
    MIN_FRAMES = 2
    SHOTS_TO_CRIPPLE = 12
    START_DISTANCE = 500
    HITS_PER_SHOT_MAX = 1            # hits one queued action may produce: a single projectile; the explosive class adds the blast victims

    # what is class specific
    def extra_checks(self, ctx: Ctx, shooter, target: str) -> None:
        return None

    def run(self, ctx: Ctx) -> None:
        t, weapon = ctx.refs["target"], ctx.refs["weapon"]
        host, client = ctx.host, ctx.clients[0]
        others_of = lambda m: [x for x in ctx.machines if x is not m]       # noqa: E731

        with ctx.step(f"everyone holds the {self.weapon_class} weapon and stands at the target"):
            for m in ctx.machines:
                ctx.must(give_and_equip(m.bot, weapon), f"{m.name} equips the {self.weapon_class} weapon")
            for n, r in ctx.on_all(lambda m: m.bot.goto_ref(t, self.START_DISTANCE)).items():
                ctx.must(r, f"{n} walks to the target")
            hp0 = ctx.same_everywhere("target health", lambda m: m.bot.health(t))
            ctx.expect(hp0 > 0, "the target is dead before the test")
            ctx.same_everywhere("limb conditions before", lambda m: limb_conditions(m.bot, t))

        with ctx.step("every machine reads the VATS state and can choose every limb"):
            for m in ctx.machines:
                ctx.must(enter(m.bot, t), f"{m.name} enters VATS")
                st = state(m.bot)
                ctx.expect(selecting(st), f"{m.name}: VATS mode is {st.get('mode')}")
                ctx.must(select(m.bot, t, "torso"), f"{m.name} selects the target")
                st = state(m.bot)
                ctx.expect(int(st.get("target", 0)) == ref_id(t), f"{m.name}: selected target {st.get('target')} is not {ref_id(t)}")
                have = limbs_of(st)
                ctx.expect(len(have) >= 4, f"{m.name}: only {have} are selectable")
                for limb in have:
                    ctx.must(select(m.bot, t, limb), f"{m.name} highlights {limb}")
                    got = state(m.bot).get("limb")
                    ctx.expect(got == limb, f"{m.name}: asked for {limb}, the engine highlights {got}")
                ctx.must(leave(m.bot), f"{m.name} leaves VATS")
            ctx.same_everywhere("health untouched by browsing", lambda m: m.bot.health(t))

        def shot(shooter, limb: str) -> int:
            """One VATS shot at `limb` by `shooter`; checks the shared outcome; returns the damage."""
            before = ctx.same_everywhere(f"health before the {limb} shot", lambda m: m.bot.health(t))
            cond0 = limb_conditions(shooter.bot, t)
            h0 = ctx.optional("hits applied", lambda: sum(hits_applied(m.bot) for m in ctx.machines), default=None)
            watchers = others_of(shooter)

            def while_in_vats() -> None:
                f0 = {m.name: P.frames(m.bot) for m in watchers}
                ctx.pause(0.5)
                for m in watchers:
                    d = P.frames(m.bot) - f0[m.name]
                    ctx.expect(d >= self.MIN_FRAMES, f"{m.name} ran only {d} frames while {shooter.name} sat in the VATS menu (the world paused)")

            ctx.must(attack(shooter.bot, t, limb, during=while_in_vats), f"{shooter.name} fires VATS at the {limb}")
            ctx.pause(0.5)
            ctx.settle()
            after = ctx.same_everywhere(f"health after the {limb} shot", lambda m: m.bot.health(t))
            ctx.expect(after < before, f"the VATS {limb} shot did no damage anywhere ({before} -> {after})")
            cond1 = ctx.same_everywhere(f"limb conditions after the {limb} shot", lambda m: limb_conditions(m.bot, t))
            i = LIMBS.index(limb)
            if limb != "torso" and host.bot.target_alive(t) and cond0[i] > 0:
                ctx.expect(cond1[i] < cond0[i], f"the shot at the {limb} did not hurt the {limb}: its condition stayed {cond1[i]} (the chosen limb was dropped)")
            if h0 is not None:
                landed = sum(hits_applied(m.bot) for m in ctx.machines) - h0
                ctx.expect(1 <= landed <= self.HITS_PER_SHOT_MAX, f"one VATS action produced {landed} applied hits (expected 1 to {self.HITS_PER_SHOT_MAX}): "
                                                                 f"a hit applied twice, or not at all")
            return before - after

        dmg_host = dmg_client = 0
        with ctx.step("the host fires a VATS shot at the head; the other machines keep running; damage lands once"):
            dmg_host = shot(host, "head")
            ctx.expect(dmg_host <= hp0, f"damage {dmg_host} is more than the target had")
            self.extra_checks(ctx, host, t)
        with ctx.step("the client fires a VATS shot at the right leg through the same victim owner"):
            dmg_client = shot(client, "right_leg")
            self.extra_checks(ctx, client, t)
        with ctx.step("repeated shots at one limb cripple it, and every machine agrees which limbs are crippled"):
            for _ in range(self.SHOTS_TO_CRIPPLE):
                if not host.bot.target_alive(t):
                    break
                before = limb_conditions(host.bot, t)
                if before[LIMBS.index("left_arm")] <= 0:
                    break
                shot(host, "left_arm")
            ctx.settle()
            cond = ctx.same_everywhere("limb conditions after the volley", lambda m: limb_conditions(m.bot, t))
            ctx.expect(cond[LIMBS.index("left_arm")] <= 0 or not host.bot.target_alive(t), f"the left arm is not crippled after {self.SHOTS_TO_CRIPPLE} shots: {cond}")

        for killcam in (True, False):
            with ctx.step(f"kill-cam {'on' if killcam else 'off'}: a killing VATS shot ends the target on every machine, once, and nobody's world pauses"):
                ctx.must(set_config(host.bot, killcam=killcam), "host sets the kill-cam")
                ctx.wait_until("every machine runs the host's kill-cam setting", lambda: all(machine_settings(m.bot).get("killCam") == killcam for m in ctx.machines),
                               timeout_s=ctx.opts.get("config_s", 15.0))
                if not host.bot.target_alive(t):
                    ctx.must(revive(host.bot, t), "the host revives the target")
                    ctx.settle()
                ctx.must(set_health(host.bot, t, 1), "the host sets the target to 1 health")
                ctx.settle()
                shooter = client if killcam else host
                d0 = ctx.optional("death counts", lambda: ctx.same_everywhere("deaths", lambda m: P.death_count(m.bot, t)), default=0)
                ctx.must(attack(shooter.bot, t, "head"), f"{shooter.name} fires the killing shot")
                f1 = {m.name: P.frames(m.bot) for m in others_of(shooter)}      # right after the kill: the kill-cam is playing on the shooter's machine
                ctx.pause(0.4)
                for m in others_of(shooter):
                    d = P.frames(m.bot) - f1[m.name]
                    ctx.expect(d >= self.MIN_FRAMES, f"{m.name} ran only {d} frames during the kill-cam (the world paused)")
                ctx.settle()
                alive = ctx.same_everywhere("target alive", lambda m: m.bot.target_alive(t))
                ctx.expect(not alive, "the target survived the killing shot")
                deaths = ctx.optional("death counts", lambda: ctx.same_everywhere("deaths", lambda m: P.death_count(m.bot, t)), default=d0 + 1) - d0
                ctx.expect(deaths == 1, f"the target died {deaths} times in that one shot")
        if ctx.opts.get("slowmo"):
            with ctx.step("party slow-mo: one player's VATS slows every machine together, then the time scale returns"):
                ctx.must(set_config(host.bot, partyslowmo=True), "host turns party slow-mo on")
                ctx.wait_until("every machine runs the setting", lambda: all(machine_settings(m.bot).get("partySlowMo") for m in ctx.machines), timeout_s=15.0)
                if not host.bot.target_alive(t):
                    ctx.must(revive(host.bot, t), "revive")
                ctx.must(enter(client.bot, t), "the client enters VATS")
                ctx.wait_until("every machine is slowed", lambda: all(float(((status(m.bot).get("sync") or {}).get("partySlow") or {}).get("appliedScale", 1.0)) < 0.99
                                                                       for m in ctx.machines), timeout_s=10.0)
                ctx.must(leave(client.bot), "the client leaves VATS")
                ctx.wait_until("every machine is back to normal speed", lambda: all(float(((status(m.bot).get("sync") or {}).get("partySlow") or {}).get("appliedScale", 1.0)) == 1.0
                                                                                      for m in ctx.machines), timeout_s=15.0)
                set_config(host.bot, partyslowmo=False)
        with ctx.step("nothing was refused, spoofed or malformed on the VATS channel"):
            for m in ctx.machines:
                sync = status(m.bot).get("sync") or {}
                stt = sync.get("stats", {})
                ctx.expect(int(stt.get("spoofed", 0)) == 0 and int(stt.get("undecodable", 0)) == 0, f"{m.name}: channel errors {stt}")
                th = status(m.bot).get("thunks", {})
                ctx.expect(int(th.get("errors", 0)) == 0 and int(th.get("offThread", 0)) == 0, f"{m.name}: hook errors {th}")


class VatsGun(VatsClassPlaybook):
    name, weapon_class = "vats_gun", "gun"


class VatsMelee(VatsClassPlaybook):
    name, weapon_class = "vats_melee", "melee"
    START_DISTANCE = 600
    summary = "Both players use real-time VATS with a melee weapon: the engine's lunge brings the attacker to the target, and everything else matches the gun playbook."

    def extra_checks(self, ctx: Ctx, shooter, target: str) -> None:
        d = P.distance(shooter.bot, target)
        ctx.expect(d <= 250, f"{shooter.name}: after a melee VATS attack the target is {d:.0f} units away (no lunge)")


class VatsExplosive(VatsClassPlaybook):
    name, weapon_class = "vats_explosive", "explosive"
    HITS_PER_SHOT_MAX = 2            # the target and one neighbour in the blast
    summary = "Both players use real-time VATS with a thrown explosive: damage goes out through each victim's owner, a neighbour in the blast is hurt once."

    def extra_checks(self, ctx: Ctx, shooter, target: str) -> None:
        nb = ctx.refs.get("neighbor")
        if not nb:
            ctx.notes.append("no neighbor ref in the scenario: the blast-radius check is skipped")
            return
        hp = ctx.same_everywhere("neighbour health", lambda m: m.bot.health(nb))
        ctx.expect(hp < ctx.refs.get("neighbor_hp", 100), f"the neighbour at {nb} took no blast damage (health {hp})")


class VatsEnergy(VatsClassPlaybook):
    name, weapon_class = "vats_energy", "energy"
    summary = "Both players use real-time VATS with an energy weapon: same checks as the gun, with the energy weapon's higher limb damage."


# ---- vats_world_moves: the hand steps of INGAME-TESTS step 4 ("by hand, once"), measured -------------------------------------------------


def game_clock(bot) -> Dict[str, Any]:
    """The game clock of THIS machine: {"hour": hour of day (float), "timeScale": game seconds per real second or None}.
    worldtime.clock first (plugin/world_time reads the clock globals); when that module is not loaded, the globals GameHour and TimeScale through the
    console expression evaluator. GameHour has a precision of about 2e-6 h, far finer than the 0.02 h one window moves it."""
    try:
        r = bot.nv.call("worldtime.clock", {}, timeout_s=15.0)
        if isinstance(r, dict) and "hour" in r:
            ts = float(r.get("timeScale") or 0.0)
            return {"hour": float(r["hour"]), "timeScale": ts if ts > 0 else None}
    except Exception as e:  # noqa: BLE001
        if type(e).__name__ in ("ConnectionLost", "GameCrashed"):
            raise
    hour = float(bot.nv.eval("GameHour"))
    try:
        ts = float(bot.nv.eval("TimeScale"))
    except Exception as e:  # noqa: BLE001
        if type(e).__name__ in ("ConnectionLost", "GameCrashed"):
            raise
        ts = 0.0
    return {"hour": hour, "timeScale": ts if ts > 0 else None}


def clock_delta_h(h0: float, h1: float) -> float:
    """Game hours from hour-of-day h0 to h1 (midnight wraps; a jump back of more than 12 h reads as negative)."""
    d = (h1 - h0) % 24.0
    return d - 24.0 if d > 12.0 else d


def path_length(points: List[Tuple[float, float, float]]) -> float:
    return sum(math.dist(a, b) for a, b in zip(points, points[1:]))


def has_weapon_equipped(bot) -> bool:
    """NVSE GetEquippedObject, slot 5 = weapon; 0 = nothing. The expression is P.equipped_weapon() (a number: printc refuses the bare form).
    A game that rejects it anyway cannot tell, and this check only guards a clearer message: the playbook's own steps measure the rest."""
    try:
        return float(bot.nv.eval(P.EQUIPPED_WEAPON_EXPR)) != 0.0
    except Exception as e:  # noqa: BLE001
        if type(e).__name__ in ("ConnectionLost", "GameCrashed"):
            raise
        return True


def hold_move(bot, forward: float, strafe: float = 0.0, run: bool = True) -> None:
    """Hold the movement keys through the in-process input driver (plugin/bots bot.move). release_move lets go."""
    _call(bot, "bot.move", {"forward": forward, "strafe": strafe, "run": run})


def release_move(bot) -> None:
    _call(bot, "bot.stop")


def face(bot, yaw_deg: float) -> None:
    _call(bot, "bot.look", {"yaw": yaw_deg % 360.0, "pitch": 0})


class VatsWorldMoves(Playbook):
    name = "vats_world_moves"
    weapon_class = "any"            # no weapon argument needed: a given one (--weapon any=ITEM) is equipped, else the weapon the player already holds is used
    summary = ("While one player sits in the VATS menu the OTHER copy's game clock and a walking NPC keep moving (measured against a baseline with nobody in VATS), "
               "the VATS player cannot walk (and can after leaving), and the selected limb is the highlighted and the hit limb.")
    scenario = "vats_hit"
    needs_scenario_refs = ("target",)
    START_DISTANCE = 500
    WINDOW_S = 3.0                  # seconds of game time one measurement lasts
    SAMPLES = 6                     # NPC position samples per window
    CLOCK_FRACTION = 0.5            # the clock must move at least this much of what it does with nobody in VATS (and of what TimeScale says)
    NPC_FRACTION = 0.4              # the same for the distance the NPC walks
    MIN_NPC_MOVE = 60.0             # units: the NPC must walk at least this far in the baseline, else there is nothing to compare
    RUN_S = 1.0                     # the VATS player tries to walk for this long
    STILL_MAX = 12.0                # units a player in VATS may drift
    MOVED_MIN = 40.0                # units the same move must cover once VATS is closed (a 1 s backwards run is about 100)
    LIMB_ORDER = ("right_leg", "left_leg", "right_arm", "left_arm")      # the limb to shoot: the first that is not crippled yet (never the head: it kills)
    ATTEMPTS = 3                    # VATS can miss: shots allowed until the limb condition drops

    # --- helpers ---
    def _arm(self, ctx: Ctx, m) -> None:
        w = ctx.refs.get("weapon")
        if w:
            ctx.must(give_and_equip(m.bot, w), f"{m.name} equips {w}")
        else:
            ctx.expect(has_weapon_equipped(m.bot), f"{m.name} holds no weapon and none was given: equip one in the save, or pass --weapon any=<editor id or Plugin.esm:HEX>")

    def _gather(self, ctx: Ctx, t: str) -> None:
        for n, r in ctx.on_all(lambda m: m.bot.goto_ref(t, self.START_DISTANCE)).items():
            ctx.must(r, f"{n} walks to the target")

    def _run_away(self, ctx: Ctx, runner, t: str) -> None:
        """The runner turns away from the NPC and holds run. The hostile NPC chases it: that is the NPC motion the other copy must show."""
        me = tuple(float(x) for x in runner.bot.pos())
        npc = P.actor_pos(runner.bot, t)
        face(runner.bot, math.degrees(math.atan2(me[0] - npc[0], me[1] - npc[1])))
        hold_move(runner.bot, 1.0)

    def _window(self, ctx: Ctx, watchers, runner, t: str, shooter=None) -> Dict[str, Dict[str, Any]]:
        """One measurement of WINDOW_S: the clock and the NPC position as the `watchers` see them while `runner` runs away from the NPC.
        With `shooter`: that player must still be in the VATS menu at the end. Without: the baseline, nobody in VATS."""
        c0 = {m.name: game_clock(m.bot) for m in watchers}
        pts = {m.name: [P.actor_pos(m.bot, t)] for m in watchers}
        t0 = ctx.clock.now()
        self._run_away(ctx, runner, t)
        try:
            for _ in range(self.SAMPLES):
                ctx.advance(self.WINDOW_S / self.SAMPLES)
                for m in watchers:
                    pts[m.name].append(P.actor_pos(m.bot, t))
        finally:
            release_move(runner.bot)
        elapsed = ctx.clock.now() - t0
        c1 = {m.name: game_clock(m.bot) for m in watchers}
        if shooter is not None:
            ctx.expect(selecting(state(shooter.bot)), f"{shooter.name} was thrown out of the VATS menu during the measurement")
        out = {}
        for m in watchers:
            ts = c0[m.name]["timeScale"]
            out[m.name] = {"clock_h": clock_delta_h(c0[m.name]["hour"], c1[m.name]["hour"]), "elapsed": max(elapsed, 1e-6), "ts": ts,
                           "expected_h": (ts * elapsed / 3600.0) if ts else None, "npc": path_length(pts[m.name])}
        return out

    def _limb_drops(self, ctx: Ctx, shooter, t: str, limb: str) -> Tuple[float, float]:
        """Select `limb`, read it back from vats.state, fire, and return (how far that limb's condition fell, the largest fall of any other limb but the torso)."""
        cond0 = limb_conditions(shooter.bot, t)
        i = LIMBS.index(limb)
        ctx.expect(selecting(state(shooter.bot)), f"{shooter.name} is not in VATS")
        ctx.must(select(shooter.bot, t, limb), f"{shooter.name} selects the {limb}")
        got = state(shooter.bot)
        ctx.expect(got.get("limb") == limb, f"asked for the {limb}, vats.state highlights {got.get('limb')}")
        lit = [str(x.get("limb")) for x in got.get("limbs", []) if x.get("highlighted")]
        ctx.expect(lit in ([limb], []), f"the highlighted limbs are {lit}, not just the {limb}")
        ctx.must(attack(shooter.bot, t, limb), f"{shooter.name} fires VATS at the {limb}")
        ctx.pause(0.5)
        ctx.settle()
        cond1 = ctx.same_everywhere(f"limb conditions after the {limb} shot", lambda m: limb_conditions(m.bot, t))
        drops = [a - b for a, b in zip(cond0, cond1)]
        others = max([d for j, d in enumerate(drops) if j != i and LIMBS[j] != "torso"] + [0.0])
        return drops[i], others

    def run(self, ctx: Ctx) -> None:
        t = ctx.refs["target"]
        host = ctx.host

        with ctx.step("everyone holds a weapon and stands at the target"):
            for m in ctx.machines:
                self._arm(ctx, m)
            self._gather(ctx, t)
            ctx.expect(host.bot.health(t) > 0, "the target is dead before the test")

        with ctx.step(f"{host.name} in VATS cannot walk; after leaving VATS the same move walks"):
            self._gather(ctx, t)
            ctx.must(enter(host.bot, t), f"{host.name} enters VATS")
            try:
                p0 = tuple(float(x) for x in host.bot.pos())
                hold_move(host.bot, -1.0)
                ctx.advance(self.RUN_S)
                release_move(host.bot)
                still = math.dist(p0, tuple(float(x) for x in host.bot.pos()))
            finally:
                leave(host.bot)
            ctx.expect(still <= self.STILL_MAX, f"{host.name} walked {still:.0f} units while the VATS menu was open (limit {self.STILL_MAX:.0f})")
            ctx.expect(not selecting(state(host.bot)), f"{host.name} is still in VATS")
            p2 = tuple(float(x) for x in host.bot.pos())
            hold_move(host.bot, -1.0)
            ctx.advance(self.RUN_S)
            release_move(host.bot)
            moved = math.dist(p2, tuple(float(x) for x in host.bot.pos()))
            ctx.expect(moved >= self.MOVED_MIN, f"{host.name} moved only {moved:.0f} units in {self.RUN_S:g} s after leaving VATS (the controls stayed locked, or the move does not work)")

        for shooter in ctx.machines:
            watchers = [m for m in ctx.machines if m is not shooter]
            runner = watchers[0]
            names = ", ".join(m.name for m in watchers)
            with ctx.step(f"{shooter.name} sits in VATS: the clock and the walking NPC keep moving on {names}"):
                self._gather(ctx, t)
                base = self._window(ctx, watchers, runner, t)
                self._gather(ctx, t)
                ctx.must(enter(shooter.bot, t), f"{shooter.name} enters VATS")
                try:
                    during = self._window(ctx, watchers, runner, t, shooter)
                finally:
                    leave(shooter.bot)
                for m in watchers:
                    b, d = base[m.name], during[m.name]
                    ratio = d["elapsed"] / b["elapsed"]
                    ctx.expect(b["clock_h"] > 0, f"{m.name}: the game clock did not move by itself in {b['elapsed']:.1f} s with nobody in VATS (TimeScale 0?): nothing to compare")
                    ctx.expect(d["clock_h"] >= self.CLOCK_FRACTION * b["clock_h"] * ratio,
                               f"{m.name}: its game clock moved {d['clock_h'] * 3600:.1f} game s in {d['elapsed']:.1f} s while {shooter.name} was in VATS, against "
                               f"{b['clock_h'] * 3600:.1f} game s in {b['elapsed']:.1f} s with nobody in VATS (the clock paused)")
                    if d["expected_h"]:
                        ctx.expect(d["clock_h"] >= self.CLOCK_FRACTION * d["expected_h"],
                                   f"{m.name}: its game clock moved {d['clock_h'] * 3600:.1f} game s while {shooter.name} was in VATS, TimeScale {d['ts']:g} says "
                                   f"{d['expected_h'] * 3600:.1f} (the clock paused)")
                    ctx.expect(b["npc"] >= self.MIN_NPC_MOVE,
                               f"{m.name}: the NPC walked only {b['npc']:.0f} units in {b['elapsed']:.1f} s with nobody in VATS: the chase did not start, nothing to compare")
                    ctx.expect(d["npc"] >= self.NPC_FRACTION * b["npc"] * ratio,
                               f"{m.name}: the NPC walked {d['npc']:.0f} units on that copy while {shooter.name} was in VATS, against {b['npc']:.0f} with nobody in VATS (the NPCs froze)")
                ctx.notes.append(f"{shooter.name} in VATS: " + "; ".join(
                    f"{m.name} clock {during[m.name]['clock_h'] * 3600:.1f} game s (base {base[m.name]['clock_h'] * 3600:.1f}), "
                    f"NPC {during[m.name]['npc']:.0f} units (base {base[m.name]['npc']:.0f})" for m in watchers))

        with ctx.step("the limb that is selected is the limb that is highlighted and hit"):
            self._gather(ctx, t)
            if not host.bot.target_alive(t):
                ctx.must(revive(host.bot, t), "the host revives the target")
                ctx.settle()
            cond = limb_conditions(host.bot, t)
            limb = next((l for l in self.LIMB_ORDER if cond[LIMBS.index(l)] > 0), None)
            ctx.expect(limb is not None, f"no limb of the target is left to shoot at: {cond}")
            ctx.must(enter(host.bot, t), f"{host.name} enters VATS")
            drop = others = 0.0
            try:
                for l in LIMBS:                                    # every limb reads back as highlighted once chosen (no shot yet)
                    ctx.must(select(host.bot, t, l), f"{host.name} highlights {l}")
                    got = state(host.bot).get("limb")
                    ctx.expect(got == l, f"asked for {l}, vats.state highlights {got}")
                for _ in range(self.ATTEMPTS):
                    if not host.bot.target_alive(t):
                        break
                    drop, others = self._limb_drops(ctx, host, t, limb)
                    if drop > 0:
                        break
                    ctx.must(enter(host.bot, t), f"{host.name} enters VATS again")       # a shot closes VATS: open it for the next try
            finally:
                leave(host.bot)
            ctx.expect(drop > 0, f"the {limb} was selected and highlighted, but its condition never dropped in {self.ATTEMPTS} shots (the shot went to another limb)")
            ctx.expect(drop >= others, f"the {limb} lost {drop:.1f} condition but another limb lost {others:.1f}: the hit did not follow the selection")

        with ctx.step("nothing was refused, spoofed or malformed on the VATS channel"):
            for m in ctx.machines:
                stt = (status(m.bot).get("sync") or {}).get("stats", {})
                ctx.expect(int(stt.get("spoofed", 0)) == 0 and int(stt.get("undecodable", 0)) == 0, f"{m.name}: channel errors {stt}")


VATS_REGISTRY = {"vats_gun": VatsGun, "vats_melee": VatsMelee, "vats_explosive": VatsExplosive, "vats_energy": VatsEnergy,
                 "vats_world_moves": VatsWorldMoves}
# what `run_vats.py` runs when no --playbook is given: the four weapon classes (they need a weapon each). vats_world_moves is asked for by name.
VATS_CLASS_PLAYBOOKS = ("vats_gun", "vats_melee", "vats_explosive", "vats_energy")

# (playbook, fake bug) pairs where the playbook MUST fail. Every bug below is one wrong behaviour of VatsWorld.
VATS_TWINS: List[Tuple[str, str]] = [
    ("vats_gun", "vats_pauses_others"), ("vats_gun", "vats_killcam_pauses"), ("vats_gun", "vats_limb_ignored"), ("vats_gun", "vats_double_shot"),
    ("vats_gun", "vats_crippled_unsynced"), ("vats_gun", "vats_no_limb_choice_client"), ("vats_gun", "vats_hp_unsynced"), ("vats_gun", "vats_reports_wrong_limb"),
    ("vats_gun", "vats_settings_not_hosts"),
    ("vats_melee", "vats_no_lunge"), ("vats_melee", "vats_limb_ignored"), ("vats_melee", "vats_pauses_others"),
    ("vats_explosive", "vats_no_blast"), ("vats_explosive", "vats_double_shot"), ("vats_explosive", "vats_crippled_unsynced"),
    ("vats_energy", "vats_limb_ignored"), ("vats_energy", "vats_killcam_pauses"), ("vats_energy", "vats_crippled_unsynced"),
    # vats_world_moves: the measured version of the hand steps
    ("vats_world_moves", "vats_pauses_others"), ("vats_world_moves", "vats_world_paused"), ("vats_world_moves", "vats_clock_frozen"),
    ("vats_world_moves", "vats_npcs_frozen"), ("vats_world_moves", "vats_player_walks"), ("vats_world_moves", "vats_controls_stay_locked"),
    ("vats_world_moves", "vats_limb_ignored"), ("vats_world_moves", "vats_reports_wrong_limb"), ("vats_world_moves", "vats_limb_hits_neighbour"),
]
VATS_BUGS = {
    "vats_pauses_others": "the world of the other machines freezes while one player is in VATS",
    "vats_killcam_pauses": "the other machines freeze while the kill-cam of a killing VATS shot plays",
    "vats_limb_ignored": "the chosen limb is dropped: the shot lands on the torso whatever was highlighted",
    "vats_double_shot": "a VATS shot is applied twice (the shooter's prediction plus the owner's result)",
    "vats_crippled_unsynced": "the crippled-limb state is only known on the shooter's machine",
    "vats_no_limb_choice_client": "a client cannot highlight any limb but the default one",
    "vats_hp_unsynced": "the other machines never see the damage of a VATS shot",
    "vats_reports_wrong_limb": "vats.state reports another limb than the one that was highlighted",
    "vats_settings_not_hosts": "a client keeps its own kill-cam setting instead of the host's",
    "vats_no_lunge": "a melee VATS attack does not bring the attacker to the target",
    "vats_no_blast": "an explosive VATS shot hurts only the target, not its neighbour",
    "vats_slowmo_stuck": "party slow-mo never ends: the world scale stays slowed after the VATS user left",
    "vats_world_paused": "while one player is in VATS the game clock and the NPCs of the other copies stand still (their frames still run)",
    "vats_clock_frozen": "while one player is in VATS the game clock of the other copies stands still (NPCs and frames run)",
    "vats_npcs_frozen": "while one player is in VATS the NPCs of the other copies stand still (clock and frames run)",
    "vats_player_walks": "a player in the VATS menu can still walk",
    "vats_controls_stay_locked": "a player who left VATS cannot walk any more (the controls stay locked)",
    "vats_limb_hits_neighbour": "the shot hits the limb next to the highlighted one (arm for leg), the highlight itself reads back right",
}
# twins that only the optional party slow-mo step can see (run with opts slowmo=True)
VATS_SLOWMO_TWINS: List[Tuple[str, str]] = [("vats_gun", "vats_slowmo_stuck")]
fakecoop.BUGS.update(VATS_BUGS)      # FakeCoop refuses unknown bug names


def make_vats(name: str) -> Playbook:
    return VATS_REGISTRY[name]()


# ---- the fake world -----------------------------------------------------------------------------------------------------------------

CLASS_DAMAGE = {"gun": 18, "melee": 22, "explosive": 30, "energy": 26}
LIMB_MULT = {"head": 1.5, "torso": 1.0, "left_arm": 0.8, "right_arm": 0.8, "left_leg": 0.8, "right_leg": 0.8}
LIMB_HP = {"head": 30, "torso": 9999, "left_arm": 25, "right_arm": 25, "left_leg": 25, "right_leg": 25}   # damage that cripples a limb (torso never)


def _bridge_error(msg: str) -> Exception:
    """nvbridge.BridgeError (what NvBridge.eval raises for a line the game refuses); a plain RuntimeError where tools/runner is not on the path."""
    try:
        from nvbridge import BridgeError  # type: ignore
        return BridgeError(msg)
    except ImportError:
        return RuntimeError(msg)


class VatsFakeNv(FakeNv):
    def call(self, method: str, params: Optional[dict] = None, timeout_s: float = 0) -> Any:
        if method.startswith("vats.") or method in ("bot.input", "bot.move", "bot.stop", "bot.look", "worldtime.clock"):
            self.calls.append((method, params or {}))
            return self.bot.vats_rpc(method, params or {})
        return super().call(method, params, timeout_s)

    def eval(self, expr: str, ref: Optional[str] = None) -> float:
        """The few console expressions the vats_world_moves playbook reads (the game clock globals, the equipped weapon)."""
        w, b = self.bot.world, self.bot
        e = expr.strip().lower()
        if e == "gamehour":
            return float(w.clock_h[b.idx])
        if e == "timescale":
            return float(w.time_scale)
        if e == "player.getequippedobject 5":     # what the game does with the bare form (in game 2026-10-22)
            raise _bridge_error(f"cannot evaluate {expr!r}: 'Error on line 1\r\n\r\nInvalid expression for parameter 2. Expected variable (got Form).'")
        if e == P.EQUIPPED_WEAPON_EXPR.lower():
            return 1.0 if b.me["equipped"] else 0.0
        raise RuntimeError(f"fake VATS world cannot evaluate {expr!r}")

    def pump(self) -> None:
        self.bot.world.tick()


class VatsFakeBot(FakeBot):
    is_fake = True

    def __init__(self, world: "VatsWorld", idx: int):
        super().__init__(world, idx)
        self.nv = VatsFakeNv(self)
        self.mode = 0
        self.target: Optional[str] = None
        self.limb = "torso"
        self.queue: List[Tuple[str, str]] = []
        self.counters = {"actionsQueued": 0, "autoFires": 0, "spoofed": 0, "undecodable": 0}
        self.my_killcam = True

    # ---- test helpers (fake-only fast paths) ----
    def limb_state(self, ref):
        """The six limb conditions as this machine sees them: 100 untouched, falling with limb damage, 0 crippled."""
        r = self.view["refs"][norm(ref)]
        ld = r.get("limb_damage", {})
        crippled = set(r.get("crippled", []))
        return tuple(0.0 if l in crippled else round(100.0 - 100.0 * ld.get(l, 0) / LIMB_HP[l], 1) for l in LIMBS)

    def hits_applied(self):
        """combat.status stats.hitsApplied: hits THIS machine applied as the victim's owner (the host owns the fake NPCs)."""
        return self.world.hit_n if self.idx == 0 else 0

    def set_health(self, ref, hp):
        self.world.set_ref(ref, hp=hp)
        return Result(True)

    def revive(self, ref, hp=100):
        self.world.set_ref(ref, hp=hp, dead=False, crippled=[], limb_damage={})
        return Result(True)

    def give_item(self, item, n=1):
        self.me["inv"][item] = self.me["inv"].get(item, 0) + n

    # ---- the bridge methods of plugin/vats and plugin/bots ----
    def _targets(self) -> List[str]:
        out = []
        for k, r in self.view["refs"].items():
            if r["kind"] == "raider" and r["enabled"] and not r["dead"] and r["cell"] == self.me["cell"] and self.world.dist(self.me["pos"], r["pos"]) < 4000:
                out.append(k)
        return out

    def _settings(self) -> Dict[str, Any]:
        w = self.world
        if self.idx != 0 and "vats_settings_not_hosts" in w.bugs:
            return {"realtime": True, "killCam": self.my_killcam, "partySlowMo": False, "autoFire": True}
        return dict(w.vats_settings)

    def npc_pos(self, ref):
        """Where THIS machine draws the NPC: a chased NPC moves only while this machine's world runs (VatsWorld._world_step)."""
        k = norm(ref)
        if k in self.world.chasers:
            return tuple(self.world.npc_shown[self.idx].get(k) or self.view["refs"][k]["pos"])
        return super().npc_pos(ref)

    def vats_rpc(self, method: str, p: dict) -> Dict[str, Any]:
        w = self.world
        self.world.tick()
        if method == "bot.move":
            w.mover[self.idx] = {"f": float(p.get("forward", 0)), "s": float(p.get("strafe", 0)), "run": bool(p.get("run", False))}
            return {"held": ["Forward"]}
        if method == "bot.stop":
            w.mover[self.idx] = None
            return {"held": []}
        if method == "bot.look":
            w.yaw[self.idx] = math.radians(float(p.get("yaw", 0.0)))
            return {"ok": True}
        if method == "worldtime.clock":
            if w.no_worldtime:
                err = RuntimeError("unknown method worldtime.clock")
                err.code = -32601      # type: ignore[attr-defined]
                raise err
            return {"year": 2281, "month": 10, "day": 1, "hour": float(w.clock_h[self.idx]), "daysPassed": 3.0 + w.clock_h[self.idx] / 24.0, "timeScale": float(w.time_scale)}
        if method == "bot.input":
            for c in p.get("tap", []):
                if c == "Vats":
                    if self.mode == 0:
                        tg = self._targets()
                        if self.me["equipped"] and tg:
                            self.mode, self.target, self.limb = 1, tg[0], "torso"
                            self.queue = []
                            if "vats_pauses_others" in w.bugs:
                                w.vats_until, w.vats_by = w.tick_n + 10_000, self.idx
                            if w.vats_settings.get("partySlowMo"):
                                w.slow_until = w.tick_n + 10_000
                    else:
                        self._close()
                elif c == "Activate" and self.mode in (1, 2, 3) and self.target:
                    self.queue.append((self.target, self.limb))
                    self.counters["actionsQueued"] += 1
                    if self._settings().get("autoFire", True):
                        self._play()
            return {"held": []}
        if method == "vats.state":
            return self._state()
        if method == "vats.status":
            return self._status()
        if method == "vats.select_target":
            ref = norm("FalloutNV.esm:" + format(int(str(p.get("ref", "0")), 0), "X"))
            if self.mode == 0:
                return {"ok": False, "reason": "VATS is not in target / limb selection"}
            if ref not in self._targets():
                return {"ok": False, "reason": "not in the engine's VATS target list"}
            self.target, self.limb = ref, "torso"
            return {"ok": True, "target": _ref_number(ref)}
        if method == "vats.select_limb":
            limb = str(p.get("limb", ""))
            if self.mode == 0 or not self.target:
                return {"ok": False, "reason": "VATS is not in target / limb selection"}
            if limb not in LIMBS:
                return {"ok": False, "reason": "unknown limb"}
            if self.idx != 0 and "vats_no_limb_choice_client" in w.bugs and limb != "torso":
                return {"ok": False, "reason": "the engine refused the highlight change"}
            self.limb = limb
            return {"ok": True, "limb": limb}
        if method == "vats.fire":
            if self.mode == 0 or not self.queue:
                return {"ok": False, "reason": "no VATS action is queued"}
            self._play()
            return {"ok": True}
        if method == "vats.cancel":
            self.queue = []
            return {"ok": True}
        if method == "vats.set":
            if self.idx != 0:
                return {"ok": False, "reason": "only the host sets the VATS settings"}
            for k, v in p.items():
                key = {"killcam": "killCam", "partyslowmo": "partySlowMo", "autofire": "autoFire"}.get(k)
                if key:
                    w.vats_settings[key] = bool(int(v))
            return {"ok": True}
        return {"ok": False, "reason": f"unknown vats method {method}"}

    def _state(self) -> Dict[str, Any]:
        shown = self.limb
        if "vats_reports_wrong_limb" in self.world.bugs and self.limb == "head":
            shown = "torso"
        limbs = [{"limb": l, "index": i, "highlighted": l == shown} for i, l in enumerate(LIMBS)] if self.mode else []
        return {"mode": self.mode, "target": _ref_number(self.target) if (self.target and self.mode) else 0, "limb": shown if self.mode else "none",
                "limbs": limbs, "queued": len(self.queue), "menuOpen": bool(self.mode)}

    def _status(self) -> Dict[str, Any]:
        s = self._settings()
        stats = dict(self.counters)
        return {"enabled": True, "inited": True, "running": True, "pins": {}, "thunks": {"errors": 0, "offThread": 0}, "hooks": [],
                "sync": {"role": "host" if self.idx == 0 else "client",
                         "settings": {"realtime": s["realtime"], "killCam": s["killCam"], "partySlowMo": s["partySlowMo"], "autoFire": s["autoFire"]},
                         "partySlow": {"active": False, "appliedScale": self.world.vats_scale(), "started": 0}, "stats": stats}}

    def _close(self) -> None:
        if self.mode and "vats_controls_stay_locked" in self.world.bugs:
            self.world.locked[self.idx] = True
        self.mode, self.target, self.queue = 0, None, []
        if self.world.vats_by == self.idx:
            self.world.vats_until, self.world.vats_by = -1, -1
        if "vats_slowmo_stuck" not in self.world.bugs:
            self.world.slow_until = -1

    def _play(self) -> None:
        w = self.world
        self.mode = 4
        self.counters["autoFires"] += 1
        for tgt, limb in self.queue:
            self._shoot(tgt, limb)
        self.queue = []
        self._close()

    def _shoot(self, k: str, limb: str) -> None:
        w = self.world
        cls = next((c for c in CLASSES if WEAPON_IDS[c] == self.me["equipped"]), "gun")
        if cls == "melee" and "vats_no_lunge" not in w.bugs:
            r = self.view["refs"][k]
            self.me["pos"] = (r["pos"][0] - 60.0, r["pos"][1], r["pos"][2])      # the engine's melee lunge
        eff_limb = "torso" if "vats_limb_ignored" in w.bugs else limb
        if "vats_limb_hits_neighbour" in w.bugs:
            eff_limb = {"left_leg": "left_arm", "right_leg": "right_arm", "left_arm": "left_leg", "right_arm": "right_leg"}.get(limb, limb)
        dmg = int(CLASS_DAMAGE[cls] * LIMB_MULT[eff_limb])
        reps = 2 if "vats_double_shot" in w.bugs else 1
        for _ in range(reps):
            tr = w.t(k)
            if tr is None or tr["dead"]:
                break
            res = w.resolve_vats_hit(k, self.idx, dmg, eff_limb)
            if cls == "explosive" and "vats_no_blast" not in w.bugs:
                nb = norm(NEIGHBOR)
                if nb in w.truth["refs"] and not w.t(nb)["dead"]:
                    w.resolve_vats_hit(nb, self.idx, dmg // 2, "torso")
            if res["killed"] and "vats_killcam_pauses" in w.bugs:
                w.freeze_until, w.freeze_by = w.tick_n + 12, self.idx


def _ref_number(key: str) -> int:
    return fakecoop._ref_number(key)   # noqa: SLF001


class VatsWorld(FakeCoop):
    """FakeCoop plus the VATS behaviour above."""

    def __init__(self, n_players: int = 2, bugs: Optional[Set[str]] = None, **kw):
        super().__init__(n_players, bugs, **kw)
        self.bots = [VatsFakeBot(self, i) for i in range(n_players)]
        self.vats_settings = {"realtime": True, "killCam": True, "partySlowMo": False, "autoFire": True}
        self.freeze_until, self.freeze_by = -1, -1
        self.slow_until = -1
        # vats_world_moves: a game clock per copy, one chased NPC per copy, and movement keys per player
        self.time_scale = 30.0
        self.no_worldtime = False                     # True: worldtime.clock is not in this build (the playbook reads GameHour / TimeScale instead)
        self.clock_h = [10.0 + 0.5 * i for i in range(n_players)]
        self.mover: List[Optional[dict]] = [None] * n_players
        self.yaw = [0.0] * n_players
        self.locked = [False] * n_players
        self.chasers: Set[str] = set()                # NPCs that chase the moving player (positions drawn per copy in npc_shown)
        self.npc_shown: List[Dict[str, List[float]]] = [dict() for _ in range(n_players)]

    def vats_scale(self) -> float:
        return 0.35 if self.tick_n < self.slow_until else 1.0

    def tick(self) -> None:
        with self._lock:
            self._move_players()
        super().tick()

    def _vats_users(self) -> Set[int]:
        return {b.idx for b in self.bots if b.mode in (1, 2, 3)}

    def _move_players(self) -> None:
        """Players hold their movement keys: 300 units/s running, 150 walking; a player in the VATS menu does not move."""
        in_vats = self._vats_users()
        for i, mv in enumerate(self.mover):
            if not mv or self.locked[i] or (i in in_vats and "vats_player_walks" not in self.bugs):
                continue
            speed = (300.0 if mv["run"] else 150.0) * 0.05
            y = self.yaw[i]
            dx = math.sin(y) * mv["f"] + math.cos(y) * mv["s"]
            dy = math.cos(y) * mv["f"] - math.sin(y) * mv["s"]
            n = math.hypot(dx, dy)
            if n > 1e-9:
                x0, y0, z0 = self.players[i]["pos"]
                self.players[i]["pos"] = (x0 + dx / n * speed, y0 + dy / n * speed, z0)

    def _tick_core(self) -> None:
        before = list(self.frames)
        super()._tick_core()
        if self.freeze_until > self.tick_n:
            for i in range(self.n):
                if i != self.freeze_by:
                    self.frames[i] -= 1                 # undo this tick's increment: that machine's world did not run
        self._world_step([self.frames[i] > before[i] for i in range(self.n)])

    def _world_step(self, ran: List[bool]) -> None:
        """What each copy's own world does in one tick: the game clock runs, chased NPCs walk toward the player who holds a movement key.
        A copy whose frame did not run does neither; the bugs stop the clock and/or the NPCs on the copies of everybody who is not in VATS."""
        dt = 0.05
        users = self._vats_users()
        runner = next((i for i, mv in enumerate(self.mover) if mv and not self.locked[i]), None)
        for i in range(self.n):
            if not ran[i]:
                continue
            other_in_vats = bool(users - {i})
            if not (other_in_vats and ({"vats_clock_frozen", "vats_world_paused"} & self.bugs)):
                self.clock_h[i] = (self.clock_h[i] + dt * self.time_scale / 3600.0) % 24.0
            if other_in_vats and ({"vats_npcs_frozen", "vats_world_paused"} & self.bugs):
                continue
            for k in self.chasers:
                pos = self.npc_shown[i].setdefault(k, list(self.truth["refs"][k]["pos"]))
                if runner is None:
                    continue
                tx, ty, _tz = self.players[runner]["pos"]
                d = math.hypot(tx - pos[0], ty - pos[1])
                if d > 120.0:
                    step = min(250.0 * dt, d - 120.0)
                    pos[0] += (tx - pos[0]) / d * step
                    pos[1] += (ty - pos[1]) / d * step

    def resolve_vats_hit(self, k: str, attacker: int, dmg: int, limb: str) -> dict:
        with self._lock:
            before_hp = self.truth["refs"][k]["hp"]
            if "vats_hp_unsynced" in self.bugs:
                r = self.truth["refs"][k]
                r["hp"] = max(0, r["hp"] - dmg)       # only the truth moves: no replication to the other views
                res = {"dealt": before_hp - r["hp"], "killed": r["hp"] <= 0}
                if res["killed"]:
                    r["dead"] = True
                return res
            res = self.resolve_hit(k, attacker, dmg, limb)
            r = self.truth["refs"][k]
            ld = dict(r.get("limb_damage", {}))
            ld[limb] = ld.get(limb, 0) + dmg
            crippled = sorted(l for l, v in ld.items() if v >= LIMB_HP[l])
            if "vats_crippled_unsynced" in self.bugs:
                r["limb_damage"], r["crippled"] = ld, crippled
                self.views[attacker]["refs"][k]["crippled"] = list(crippled)
                self.views[attacker]["refs"][k]["limb_damage"] = dict(ld)
            else:
                self.set_ref(k, limb_damage=ld, crippled=crippled)
            return res


def fake_vats_class(cls: str):
    """Builder with the registry-style signature: (n, bugs, **kw) -> (world, refs)."""
    cls = "gun" if cls == "any" else cls

    def build(n: int = 2, bugs: Optional[Set[str]] = None, **kw):
        kw.setdefault("walk_speed", 120.0)
        w = VatsWorld(n, bugs, **kw)
        w.add_ref(TARGET, "raider", pos=(1000, 0, 0), cell="goodsprings", hp=100)
        w.chasers.add(norm(TARGET))
        w.add_ref(NEIGHBOR, "raider", pos=(1060, 40, 0), cell="goodsprings", hp=100)
        for s in range(n):
            w.set_player(s, pos=(0, 0, 0), cell="goodsprings")
            w.players[s]["inv"][WEAPON_IDS[cls]] = 1
        return w, {"target": TARGET, "weapon": WEAPON_IDS[cls], "neighbor": NEIGHBOR}
    return build
