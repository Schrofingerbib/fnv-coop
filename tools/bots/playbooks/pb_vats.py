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


VATS_REGISTRY = {"vats_gun": VatsGun, "vats_melee": VatsMelee, "vats_explosive": VatsExplosive, "vats_energy": VatsEnergy}

# (playbook, fake bug) pairs where the playbook MUST fail. Every bug below is one wrong behaviour of VatsWorld.
VATS_TWINS: List[Tuple[str, str]] = [
    ("vats_gun", "vats_pauses_others"), ("vats_gun", "vats_killcam_pauses"), ("vats_gun", "vats_limb_ignored"), ("vats_gun", "vats_double_shot"),
    ("vats_gun", "vats_crippled_unsynced"), ("vats_gun", "vats_no_limb_choice_client"), ("vats_gun", "vats_hp_unsynced"), ("vats_gun", "vats_reports_wrong_limb"),
    ("vats_gun", "vats_settings_not_hosts"),
    ("vats_melee", "vats_no_lunge"), ("vats_melee", "vats_limb_ignored"), ("vats_melee", "vats_pauses_others"),
    ("vats_explosive", "vats_no_blast"), ("vats_explosive", "vats_double_shot"), ("vats_explosive", "vats_crippled_unsynced"),
    ("vats_energy", "vats_limb_ignored"), ("vats_energy", "vats_killcam_pauses"), ("vats_energy", "vats_crippled_unsynced"),
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


class VatsFakeNv(FakeNv):
    def call(self, method: str, params: Optional[dict] = None, timeout_s: float = 0) -> Any:
        if method.startswith("vats.") or method == "bot.input":
            self.calls.append((method, params or {}))
            return self.bot.vats_rpc(method, params or {})
        return super().call(method, params, timeout_s)

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

    def vats_rpc(self, method: str, p: dict) -> Dict[str, Any]:
        w = self.world
        self.world.tick()
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

    def vats_scale(self) -> float:
        return 0.35 if self.tick_n < self.slow_until else 1.0

    def _tick_core(self) -> None:
        super()._tick_core()
        if self.freeze_until > self.tick_n:
            for i in range(self.n):
                if i != self.freeze_by:
                    self.frames[i] -= 1                 # undo this tick's increment: that machine's world did not run

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
    def build(n: int = 2, bugs: Optional[Set[str]] = None, **kw):
        kw.setdefault("walk_speed", 120.0)
        w = VatsWorld(n, bugs, **kw)
        w.add_ref(TARGET, "raider", pos=(1000, 0, 0), cell="goodsprings", hp=100)
        w.add_ref(NEIGHBOR, "raider", pos=(1060, 40, 0), cell="goodsprings", hp=100)
        for s in range(n):
            w.set_player(s, pos=(0, 0, 0), cell="goodsprings")
            w.players[s]["inv"][WEAPON_IDS[cls]] = 1
        return w, {"target": TARGET, "weapon": WEAPON_IDS[cls], "neighbor": NEIGHBOR}
    return build
