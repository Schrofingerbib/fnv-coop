"""Game-free fake of a 2 to 4 machine co-op session, for validating playbooks without a game.

FakeCoop owns the truth plus one lagging view per machine. A FakeBot is one machine's bot: it implements the bot
method names the playbooks use (same names as T6a's tools/bots/bot.py plus the probe.py extras). Rules follow
docs/FULL-SYNC-PLAN.md: the host is authoritative; client actions are intents; first pickup wins; barriers and ready
checks defer a stage until everybody has passed/agreed; ownership has epochs and hysteresis.

`bugs` is a set of names. Each name switches on one deliberately wrong behaviour (a mutation twin); the matching playbook
must fail when it is set. See BUGS below.
"""
from __future__ import annotations

import copy
import json
import math
import os
import re
import threading
from typing import Any, Dict, List, Optional, Set, Tuple

from . import probe
from .core import Comparer, Machine
from .probe import Result, fullref, hexid

BUGS = {
    "no_stage_sync": "clients never apply SetStage",
    "no_objective_sync": "clients never apply objective flags",
    "dup_pickup": "the player who lost a pickup race keeps the optimistic copy",
    "no_container_sync": "clients never see container content changes",
    "door_unsynced": "clients never see door open/close",
    "lock_unsynced": "clients never see lock changes",
    "terminal_unsynced": "clients never see terminal entries used",
    "dup_kill": "a second shooter's hit on a dead actor counts a second death",
    "stale_echo": "a stale health echo revives a dead actor on clients",
    "double_spawn": "clients create a local duplicate of every spawned actor",
    "recruit_local": "companion recruit is only applied on the recruiter",
    "spectator_stuck": "the spectator view is never released after dialogue",
    "dialogue_not_bridged": "spectators never get the dialogue bridge",
    "pnr_early": "barrier releases on the first pass instead of the last",
    "ready_veto_ignored": "a No vote does not stop the one-way step",
    "tether_off": "tether radius is not enforced",
    "consent_ignored": "fast travel proceeds without party consent",
    "travel_in_combat": "fast travel is allowed in combat",
    "owner_split": "ownership transfer is not delivered to clients",
    "flap_owner": "no hysteresis: ownership flips back and forth near the border",
    "counter_noise": "every commit raises the digest-mismatch and repair counters",
    "no_fight_sync": "clients never see actor health or death",
    "no_retransmit": "a dropped reliable message is never retransmitted",
    "repairs_in_flight": "every dropped message is healed by a repair instead of a retransmit",
    "repair_after_quiet": "a late repair fires after the world has been quiet",
    "vats_pauses_others": "the world of the other machines freezes while one player is in VATS",
    "no_rebase": "clients keep their old world when the host loads an older save",
    "reward_dup_on_rejoin": "a client that rejoins gets every reward in the ledger again",
    "puppet_slides": "a puppet glides across the ground on an idle animation",
    "puppet_wrong_weapon": "a puppet holds the wrong weapon state",
    "puppet_slides_after_teleport": "right after a teleport a puppet starts gliding on an idle animation",
    "puppet_tpose": "a puppet has no animation running (T-pose)",
    "endgame_ignores_notyet": "a 'Not yet' answer to the endgame confirm does not stop the battle",
}


def norm(ref: str) -> str:
    return fullref(ref).split(":")[0].lower() + ":" + hexid(ref)


class FakeNv:
    """Just enough of NvBridge for the core engine (coop.status, events, flight.dump, state)."""

    def __init__(self, bot: "FakeBot"):
        self.bot = bot
        self.calls: List[Tuple[str, dict]] = []

    def call(self, method: str, params: Optional[dict] = None, timeout_s: float = 0) -> Any:
        self.calls.append((method, params or {}))
        if method == "coop.status":
            return self.bot.coop_status()
        if method == "anim.probe":
            return self.bot.anim_probe()
        if method == "events":
            return {"events": [], "next": 0}
        if method == "flight.dump":
            d = self.bot.world.flight_dir
            if not d:
                raise RuntimeError("unknown method flight.dump")
            os.makedirs(d, exist_ok=True)
            p = os.path.join(d, f"{self.bot.name}.nvfr")
            with open(p, "wb") as f:
                f.write(b"FAKE")
            return {"path": p}
        raise RuntimeError(f"unknown method {method}")

    def exec(self, cmd: str, timeout_ms: Optional[int] = None, *, ref: Optional[str] = None) -> dict:
        """Like the game's console: "<hex>.Func" fails (Script command ... not found); a line for a reference names it
        in `ref` (the calling reference of exec {cmd, ref}). 'SetPlayerTeammate 1' on an NPC recruits it for this
        machine's player (as the recruit INFO does); other ref-targeted lines are accepted without a world effect."""
        self.calls.append(("exec", {"cmd": cmd, **({} if ref is None else {"ref": ref})}))
        head = re.match(r"\s*([^\s.]+)\.\w", cmd)
        if head and head.group(1).lower() != "player":
            return {"ok": False, "output": f'Script command "{cmd.split()[0]}" not found'}
        if ref is not None:
            w = self.bot.world
            r = w.t(ref)
            if r is None:
                raise RuntimeError(f"exec: ref '{ref}' is not a loaded form")     # the bridge's -32013
            if cmd.strip().lower() == "setplayerteammate 1" and r["follower_of"] is None:
                w.set_ref(ref, follower_of=self.bot.idx, owner=self.bot.idx)
            return {"ok": True, "output": ""}
        m = re.match(r"setstage (\w+) (\d+)", cmd)
        if m:
            r = self.bot.set_stage(m.group(1), int(m.group(2)))
            return {"ok": r.ok, "output": r.reason}
        return {"ok": True, "output": ""}


class FakeCoop:
    TICK_LAG = 2

    def __init__(self, n_players: int = 2, bugs: Optional[Set[str]] = None, lag: int = 1,
                 barriers: Optional[Set[Tuple[str, int]]] = None, readychecks: Optional[Set[Tuple[str, int]]] = None,
                 quest_rules: Optional[dict] = None, tether: Optional[dict] = None, flight_dir: str = "", loss_every: int = 0, retransmit_ticks: int = 3,
                 walk_speed: Optional[float] = None):
        unknown = set(bugs or ()) - set(BUGS)
        if unknown:
            raise ValueError(f"unknown fake bugs {unknown}")
        self.n = n_players
        self.bugs = set(bugs or ())
        self.lag = lag
        self.barriers = {(hexid(q), n) for q, n in (barriers or ())}
        self.readychecks = {(hexid(q), n) for q, n in (readychecks or ())}
        self.quest_rules = {(hexid(q), n): list(v) for (q, n), v in (quest_rules or {}).items()}
        self.tether = tether or {}          # {"anchor": ref, "radius": units}
        self.tether_active = False          # switched on by a successful fast travel
        self.flight_dir = flight_dir
        self.loss_every, self.retransmit_ticks = loss_every, retransmit_ticks
        self.walk_speed = walk_speed            # units/s; None: movement is instant and the animation checks have nothing to see
        self.vt = 0.0                           # virtual seconds, 0.05 per tick
        self.tick_hooks: List[Any] = []
        self._lock = threading.RLock()
        self.shown: List[List[float]] = []      # where each player's character is drawn (walks toward its position)
        self.hist: List[List[List[float]]] = []
        self.phase: List[float] = []
        self.heading: List[float] = []
        self.teleported_at: Dict[int, float] = {}
        self.n_deliv = 0
        self.vetoed: Set[str] = set()
        self.frames = [0] * n_players
        self.vats_until, self.vats_by = -1, -1
        self.connected = [True] * n_players
        self.saves: Dict[str, Tuple[dict, int]] = {}
        self.delivered: List[Set[Any]] = [set() for _ in range(n_players)]
        self.reward_log: List[dict] = []
        self.rebases = [0] * n_players
        self.last_commit_tick = 0
        self.endgames: Dict[str, dict] = {}     # ref -> {quest, from, to}
        self.boxes: Dict[int, str] = {}          # slot -> endgame ref whose confirm box is open
        self.seq = 0
        self.tick_n = 0
        self.truth = {"quests": {}, "objectives": {}, "refs": {}}
        self.views = [copy.deepcopy(self.truth) for _ in range(n_players)]
        self.pending: List[List[Tuple[int, int, dict]]] = [[] for _ in range(n_players)]
        self.applied = [0] * n_players
        self.players = [{"pos": (0.0, 0.0, 0.0), "cell": "start", "inv": {"WEAPON": 1, "AMMO": 200}, "aim": None,
                         "equipped": None, "dialogue": None, "spectating": False, "hp": 100, "consent": {}}
                        for _ in range(n_players)]
        self.shown = [list(p["pos"]) for p in self.players]
        self.hist = [[list(p["pos"])] for p in self.players]
        self.phase = [0.0] * n_players
        self.heading = [0.0] * n_players
        self.deferred: Dict[Tuple[str, int], dict] = {}     # barrier / ready check waiting for players
        self.stats = {"mismatch": [0] * n_players, "repairs": [0] * n_players}
        self.owner_epochs: Dict[str, int] = {}
        self.bots: List[FakeBot] = [FakeBot(self, i) for i in range(n_players)]
        self.dup_n = 0
        self.sites: List[dict] = []

    # ---- building the world ----
    @staticmethod
    def _mkref(ref: str, kind: str, pos, cell: str, **kw) -> dict:
        r = {"kind": kind, "pos": list(pos), "cell": cell, "enabled": True, "dead": False, "hp": 100, "deaths": 0,
             "open": False, "locked": False, "items": {}, "in_use": None, "taken": False, "base": kw.pop("base", ref),
             "follower_of": None, "owner": 0, "epoch": 1, "hostile": kind == "raider", "entries_used": [], "speaking": None}
        r.update(kw)
        return r

    def add_ref(self, ref: str, kind: str, pos=(0.0, 0.0, 0.0), cell: str = "start", **kw):
        r = self._mkref(ref, kind, pos, cell, **kw)
        k = norm(ref)
        self.truth["refs"][k] = r
        for v in self.views:
            v["refs"][k] = copy.deepcopy(r)
        return k

    def add_site(self, pos, cell: str, specs, radius: float = 3000.0) -> None:
        """An encounter site: its refs are created (spawned, replicated) when a player first comes within `radius`."""
        self.sites.append({"pos": tuple(pos), "cell": cell, "specs": list(specs), "radius": radius, "done": False})

    def check_spawns(self) -> None:
        for site in self.sites:
            if site["done"]:
                continue
            for p in self.players:
                if p["cell"] == site["cell"] and self.dist(p["pos"], site["pos"]) <= site["radius"]:
                    site["done"] = True
                    for ref, kind, pos, kw in site["specs"]:
                        k = norm(ref)
                        self.commit({"op": "ref", "ref": k, "f": self._mkref(ref, kind, pos, site["cell"], **dict(kw)),
                                     "spawn": True})
                    break

    def set_player(self, slot: int, pos=None, cell=None, inv=None):
        p = self.players[slot]
        if pos is not None:
            p["pos"] = tuple(pos)
        if cell is not None:
            p["cell"] = cell
        if inv is not None:
            p["inv"] = dict(inv)
        self.shown[slot] = list(p["pos"])
        self.hist[slot] = [list(p["pos"])]

    def add_quest(self, q: str, stages=()):
        self.truth["quests"][hexid(q)] = {"stages": sorted(set(stages)), "running": bool(stages)}
        for v in self.views:
            v["quests"][hexid(q)] = copy.deepcopy(self.truth["quests"][hexid(q)])

    # ---- replication ----
    def commit(self, eff: dict) -> None:
        self.seq += 1
        self.last_commit_tick = self.tick_n
        eff = dict(eff, seq=self.seq)
        self._apply(self.truth, eff, client=False, who=0)
        self._apply(self.views[0], eff, client=False, who=0)
        self.applied[0] = self.seq
        for i in range(1, self.n):
            self.pending[i].append((self.tick_n + self.lag, self.seq, eff))
        if "counter_noise" in self.bugs:
            for i in range(self.n):
                self.stats["mismatch"][i] += 1
                self.stats["repairs"][i] += 1

    def tick(self) -> None:
        with self._lock:
            self.vt += 0.05
            self._step_shown()
            self._tick_core()
            for h in list(self.tick_hooks):
                h()

    def _step_shown(self) -> None:
        for i, p in enumerate(self.players):
            tgt = p["pos"]
            cur = self.shown[i]
            if self.walk_speed is None or p["cell"] != getattr(self, "_cells", {}).get(i, p["cell"]):
                nxt = list(tgt)
                if self.walk_speed is not None and tgt != cur:
                    self.teleported_at[i] = self.vt
            else:
                dx, dy = tgt[0] - cur[0], tgt[1] - cur[1]
                d = math.hypot(dx, dy)
                step = self.walk_speed * 0.05
                if d <= step:
                    nxt = list(tgt)
                else:
                    nxt = [cur[0] + dx / d * step, cur[1] + dy / d * step, tgt[2]]
            moved = math.hypot(nxt[0] - cur[0], nxt[1] - cur[1])
            if moved > 0.5:
                self.heading[i] = math.atan2(nxt[1] - cur[1], nxt[0] - cur[0])
                self.phase[i] += moved / 25.0
            self.shown[i] = nxt
            self.hist[i].append(list(nxt))
            if len(self.hist[i]) > 8:
                self.hist[i].pop(0)
            self._cells = getattr(self, "_cells", {})
            self._cells[i] = p["cell"]

    def _tick_core(self) -> None:
        self.tick_n += 1
        for i in range(self.n):
            if not ("vats_pauses_others" in self.bugs and self.vats_until > self.tick_n and i != self.vats_by):
                self.frames[i] += 1
        for i in range(1, self.n):
            if not self.connected[i]:
                continue
            keep = []
            blocked = False
            for due, seq, eff in self.pending[i]:
                if blocked or due > self.tick_n:
                    keep.append((due, seq, eff))
                    blocked = blocked or due > self.tick_n
                    continue
                if self.loss_every and not eff.get("_retx"):
                    self.n_deliv += 1
                    if self.n_deliv % self.loss_every == 0:         # the datagram was lost
                        self.stats["mismatch"][i] += 1               # a digest taken now would mismatch (transient)
                        if "repairs_in_flight" in self.bugs:
                            self.stats["repairs"][i] += 1
                        if "no_retransmit" not in self.bugs:
                            keep.append((self.tick_n + self.retransmit_ticks, seq, dict(eff, _retx=True)))
                        blocked = True                              # reliable channel: later messages wait behind it
                        continue
                self._apply(self.views[i], eff, client=True, who=i)
                self.applied[i] = max(self.applied[i], seq)
            self.pending[i] = keep
        if "repair_after_quiet" in self.bugs and self.tick_n > self.last_commit_tick                 and (self.tick_n - self.last_commit_tick) % 6 == 0:
            self.stats["repairs"][0] += 1
            self.stats["mismatch"][0] += 1

    def _apply(self, view: dict, eff: dict, client: bool, who: int) -> None:
        op = eff["op"]
        b = self.bugs
        if op == "stage":
            if client and "no_stage_sync" in b:
                return
            q = view["quests"].setdefault(eff["q"], {"stages": [], "running": True})
            if eff["n"] not in q["stages"]:
                q["stages"] = sorted(q["stages"] + [eff["n"]])
            q["running"] = True
        elif op == "reward":
            if eff["id"] not in self.delivered[who]:
                self.delivered[who].add(eff["id"])
                inv = self.players[who]["inv"]
                inv[eff["item"]] = inv.get(eff["item"], 0) + eff["count"]
            if not client and all(e["id"] != eff["id"] for e in self.reward_log):
                self.reward_log.append(eff)
        elif op == "quest":
            q = view["quests"].setdefault(eff["q"], {"stages": [], "running": False})
            q["running"] = eff["running"]
        elif op == "obj":
            if client and "no_objective_sync" in b:
                return
            view["objectives"].setdefault(eff["q"], {}).setdefault(str(eff["o"]), {}).update(eff["f"])
        elif op == "ref":
            f = dict(eff["f"])
            k = eff["ref"]
            if client:
                if "no_container_sync" in b:
                    f.pop("items", None)
                if "door_unsynced" in b:
                    f.pop("open", None)
                if "lock_unsynced" in b:
                    f.pop("locked", None)
                if "terminal_unsynced" in b:
                    f.pop("entries_used", None)
                if "recruit_local" in b:
                    f.pop("follower_of", None)
                if "owner_split" in b:
                    f.pop("owner", None)
                    f.pop("epoch", None)
                if "no_fight_sync" in b and ("hp" in f or "dead" in f):
                    f.pop("hp", None)
                    f.pop("dead", None)
                if "stale_echo" in b and f.get("dead") is True and k in view["refs"]:
                    f["dead"] = False
                    f["hp"] = 30
            view["refs"].setdefault(k, {}).update(copy.deepcopy(f))
            if client and "double_spawn" in b and eff.get("spawn"):
                self.dup_n += 1
                view["refs"][f"{k}#dup{self.dup_n}"] = copy.deepcopy(view["refs"][k])
        elif op == "dialogue":
            slot = eff["slot"]
            if who == slot:
                pass
            elif client and "dialogue_not_bridged" in b:
                pass
            else:
                self.players[who]["spectating"] = eff["on"] and not (client and "spectator_stuck" in b and not eff["on"])
                if client and "spectator_stuck" in b and not eff["on"]:
                    self.players[who]["spectating"] = True
        elif op == "move":
            pass

    # ---- ref helpers (authority = host) ----
    def t(self, ref: str) -> Optional[dict]:
        return self.truth["refs"].get(norm(ref))

    def set_ref(self, ref: str, **f) -> None:
        k = norm(ref)
        self.truth["refs"][k].update(f)    # _apply on truth also updates; double-apply is harmless
        self.commit({"op": "ref", "ref": k, "f": f})

    def dist(self, a, b) -> float:
        return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))

    # ---- stage / barrier / ready check ----
    def request_stage(self, q: str, n: int) -> Result:
        q = hexid(q)
        key = (q, n)
        if key in self.barriers:
            d = self.deferred.setdefault(key, {"kind": "barrier", "passed": set()})
            if "pnr_early" in self.bugs and d["passed"]:
                self._release(key)
            return Result(True, "deferred by barrier")
        if key in self.readychecks:
            self.deferred.setdefault(key, {"kind": "ready", "yes": set(), "no": set()})
            return Result(True, "deferred by ready check")
        self._do_stage(q, n)
        return Result(True)

    def _do_stage(self, q: str, n: int) -> None:
        if n in self.truth["quests"].get(q, {}).get("stages", []):
            return
        self.commit({"op": "stage", "q": q, "n": n})
        for eff in self.quest_rules.get((q, n), []):
            self.commit(dict(eff))

    def _release(self, key) -> None:
        self.deferred.pop(key, None)
        self._do_stage(*key)

    def pass_scene(self, slot: int, q: str) -> Result:
        q = hexid(q)
        for key, d in list(self.deferred.items()):
            if key[0] == q and d["kind"] == "barrier":
                d["passed"].add(slot)
                if "pnr_early" in self.bugs or len(d["passed"]) >= self.n:
                    self._release(key)
        # an early pass (before the host asked) is remembered
        self.early = getattr(self, "early", {})
        self.early.setdefault(q, set()).add(slot)
        for key, d in list(self.deferred.items()):
            if key[0] == q and d["kind"] == "barrier" and len(self.early[q] | d["passed"]) >= self.n:
                self._release(key)
        return Result(True)

    def vote(self, slot: int, q: str, yes: bool) -> Result:
        q = hexid(q)
        hit = False
        for key, d in list(self.deferred.items()):
            if key[0] == q and d["kind"] == "ready":
                self.vetoed.discard(q)
                hit = True
                (d["yes"] if yes else d["no"]).add(slot)
                if d["no"] and "ready_veto_ignored" not in self.bugs and "endgame_ignores_notyet" not in self.bugs:
                    self.deferred.pop(key)
                    self.vetoed.add(key[0])
                    self.commit({"op": "ref", "ref": "message:veto", "f": {"text": f"{q}:{key[1]} vetoed by {sorted(d['no'])}"}})
                elif len(d["yes"]) >= self.n or (("ready_veto_ignored" in self.bugs or "endgame_ignores_notyet" in self.bugs)
                                                  and len(d["yes"] | d["no"]) >= self.n):
                    self._release(key)
        if not hit and q in self.vetoed:
            return Result(True, "late answer to a vetoed check is ignored")
        return Result(hit, "" if hit else "no ready check is pending for that quest")

    # ---- ownership ----
    def rebalance_owners(self) -> None:
        for k, r in self.truth["refs"].items():
            if r["kind"] not in ("raider", "actor", "companion") or r["dead"]:
                continue
            cur = r["owner"]
            best, bd = cur, self.dist(self.players[cur]["pos"], r["pos"]) if self.players[cur]["cell"] == r["cell"] else 1e9
            for s in range(self.n):
                if self.players[s]["cell"] != r["cell"]:
                    continue
                d = self.dist(self.players[s]["pos"], r["pos"])
                margin = 0 if "flap_owner" in self.bugs else 1500
                if d + margin < bd:
                    best, bd = s, d
            if bd >= 1e9:
                continue
            if best != cur:
                r["epoch"] += 1
                self.set_ref(k, owner=best, epoch=r["epoch"])
                self.owner_epochs[k] = r["epoch"]


class FakeBot:
    """One machine. Method names match T6a's bot plus the probe extras."""

    def __init__(self, world: FakeCoop, idx: int):
        self.world = world
        self.idx = idx
        self.name = "host" if idx == 0 else f"client{idx}"
        self.nv = FakeNv(self)
        self.actions: List[Tuple[str, tuple]] = []

    # --- plumbing ---
    @property
    def view(self) -> dict:
        return self.world.views[self.idx]

    @property
    def me(self) -> dict:
        return self.world.players[self.idx]

    def _log(self, name, *a):
        self.actions.append((name, a))

    def _tick(self):
        self.world.tick()

    def coop_status(self) -> dict:
        w = self.world
        self._tick()
        st: Dict[str, Any] = {"role": "host" if self.idx == 0 else "client", "enabled": True, "running": True,
                              "counters": {"applyFailed": 0, "frames": w.frames[self.idx]},
                              "lastSeq": w.applied[self.idx],
                              "ui": {"bridged": self.me["spectating"], "bridges": 0}}
        st["playerRefs"] = {"binds": w.n - 1, "slots": {str(j): str(0x14 if j == self.idx else 0xFF0000 + j) for j in range(w.n)}}
        if self.idx == 0:
            st["host"] = {"lastSeq": w.seq, "allSynced": all(not p for i, p in enumerate(w.pending) if w.connected[i]), "badMessages": 0,
                          "connected": w.n - 1}
            st["desyncs"] = {"digests": {"mismatch": sum(w.stats["mismatch"]), "repairs": sum(w.stats["repairs"]),
                                         "effectsRepaired": 0}, "desyncs": []}
            st["lastSeq"] = w.seq
        else:
            st["client"] = {"joined": True, "rebases": w.rebases[self.idx], "staleEpochDropped": 0, "duplicatesDropped": 0}
        return st

    def anim_probe(self) -> dict:
        """What `anim.probe` would say on this machine: its own character, the puppets of the others, and the NPCs."""
        from animcheck import synth
        w, me = self.world, self.idx
        G = synth.G
        actors = []
        with w._lock:
            for j in range(w.n):
                own = j == me
                h = w.hist[j]
                cur = h[-1] if own else h[max(0, len(h) - 3)]            # puppets lag 100 ms: the interpolation buffer
                prev = h[-2] if len(h) > 1 else h[-1]
                moving = math.hypot(h[-1][0] - prev[0], h[-1][1] - prev[1]) > 0.5
                groups = [(1, G["FastForward" if (w.walk_speed or 0) >= 200 else "Forward"]), (0, G["Idle"])] if moving else [(0, G["Idle"])]
                weapon_out = bool(w.players[j]["equipped"])
                if not own:
                    if "puppet_slides" in w.bugs and moving:
                        groups = [(0, G["Idle"])]
                    if "puppet_slides_after_teleport" in w.bugs and j in w.teleported_at and w.vt > w.teleported_at[j] + 0.1:
                        drift = min(60.0 * (w.vt - w.teleported_at[j] - 0.1), 3000.0)
                        cur = [cur[0] + drift, cur[1], cur[2]]
                        groups = [(0, G["Idle"])]
                    if "puppet_tpose" in w.bugs:
                        groups = []
                    if "puppet_wrong_weapon" in w.bugs:
                        weapon_out = not weapon_out
                st = synth.State(pos=tuple(cur), rotz=w.heading[j], groups=groups, phase=w.phase[j], weapon_out=weapon_out,
                                 weapon_form=0x100 if weapon_out else 0, aiming=False)
                actors.append(synth.actor_dict(0x14 if own else 0xFF0000 + j, st, True, w.vt * 1000.0))
            for k, r in self.view["refs"].items():
                if r["kind"] in ("raider", "actor", "companion") and r["enabled"]:
                    ref = int(k.split(":")[-1], 16)
                    groups = [(0, G["Death"])] if r["dead"] else [(0, G["Idle"])]
                    st = synth.State(pos=tuple(r["pos"]), groups=groups, dead=r["dead"], seq_state=1)
                    actors.append(synth.actor_dict(ref, st, True, w.vt * 1000.0))
        return {"v": 1, "frame": w.tick_n * 3, "t_ms": round(w.vt * 1000.0, 2), "cost_us": 40.0, "actors": actors}

    def snapshot(self, path: str):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        s = self._snap()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(s, f, indent=1, sort_keys=True)
        return s

    def _snap(self) -> dict:
        v = copy.deepcopy(self.view)
        for r in v["refs"].values():       # per-player and cosmetic fields are not shared state
            r.pop("in_use", None)
        return {"shared": v, "perPlayer": {"inv": dict(self.me["inv"])}}

    # --- movement ---
    def goto_ref(self, ref, dist=150, timeout_s=30):
        self._log("goto_ref", ref)
        r = self.view["refs"].get(norm(ref))
        if r is None:
            return Result(False, f"{ref} is not loaded on {self.name}")
        off = dist * (0.5 + 0.1 * self.idx)     # players stop short of the ref, each a bit differently
        return self._move((r["pos"][0] + off, r["pos"][1], r["pos"][2]), r["cell"])

    def goto_pos(self, x, y, z, cell=None, timeout_s=30):
        return self._move((x, y, z), cell or self.me["cell"])

    def _move(self, pos, cell) -> Result:
        w = self.world
        tcfg = w.tether
        if tcfg and w.tether_active and "tether_off" not in w.bugs:
            a = w.t(tcfg["anchor"])
            if a and w.dist(pos, a["pos"]) > tcfg["radius"]:
                return Result(False, f"tethered: {tcfg['radius']} units around the anchor")
        far = 0.0
        if w.walk_speed and cell == self.me["cell"]:
            far = math.hypot(pos[0] - self.me["pos"][0], pos[1] - self.me["pos"][1])
        self.me["pos"], self.me["cell"] = tuple(pos), cell
        if far:        # a walk takes virtual time: the bot blocks until its character has arrived
            t_end = w.vt + far / w.walk_speed + 0.05
            while w.vt < t_end:
                w.tick()
        w.check_spawns()
        w.rebalance_owners()
        return Result(True)

    def follow(self, ref, timeout_s=10):
        return self.goto_ref(ref)

    def wait(self, seconds=1.0):
        for _ in range(3):
            self._tick()
        return Result(True)

    # --- combat ---
    def equip(self, item, timeout_s=5):
        if self.me["inv"].get(hexid(item) if item[:1].isdigit() else item, 0) <= 0:
            return Result(False, f"{item} is not in the inventory")
        self.me["equipped"] = item
        return Result(True)

    def aim_at(self, ref, timeout_s=5):
        r = self.view["refs"].get(norm(ref))
        if r is None:
            return Result(False, f"{ref} is not loaded on {self.name}")
        if r["cell"] != self.me["cell"] or self.world.dist(r["pos"], self.me["pos"]) > 4000:
            return Result(False, "target is out of range")
        self.me["aim"] = norm(ref)
        return Result(True)

    def reload(self):
        return Result(True)

    def fire(self, n=1, timeout_s=5):
        return self._hit(25, n)

    def vats_target(self, ref, limb=None, timeout_s=10):
        r = self.aim_at(ref)
        if not r:
            return r
        self.world.vats_until, self.world.vats_by = self.world.tick_n + 8, self.idx
        return self._hit(40, 1, limb)

    def _hit(self, dmg, n, limb=None) -> Result:
        w, k = self.world, self.me["aim"]
        if not k:
            return Result(False, "nothing aimed at")
        if not self.me["equipped"]:
            return Result(False, "no weapon equipped")
        hits = 0
        for _ in range(n):
            r = w.t(k)
            if r is None or not r["enabled"]:
                return Result(False, "target is gone", {"hits": hits})
            if r["dead"]:
                if "dup_kill" in w.bugs:
                    w.set_ref(k, deaths=r["deaths"] + 1)
                return Result(False, "target is already dead", {"hits": hits})
            hp = r["hp"] - dmg
            f: Dict[str, Any] = {"hp": max(hp, 0)}
            if limb:
                f["last_limb"] = limb
            if hp <= 0:
                f.update(dead=True, deaths=r["deaths"] + 1, killer=self.idx)
            w.set_ref(k, **f)
            hits += 1
        return Result(True, "", {"hits": hits})

    # --- interaction ---
    def _tgt(self, ref):
        r = self.view["refs"].get(norm(ref))
        return r

    def activate(self, ref, timeout_s=10):
        r = self._tgt(ref)
        if r is None:
            return Result(False, f"{ref} is not loaded on {self.name}")
        if r["kind"] == "door":
            return self.open_door(ref) if not r["open"] else self.close_door(ref)
        if r["kind"] == "container":
            return self.open_container(ref)
        if norm(ref) in self.world.endgames:
            self.world.boxes[self.idx] = norm(ref)           # the confirm box opens on this player
            return Result(True)
        return Result(True)

    def open_door(self, ref, timeout_s=10):
        w = self.world
        r = w.t(ref)
        if r is None or r["kind"] != "door":
            return Result(False, "not a door")
        if r["locked"]:
            return Result(False, "door is locked")
        if not r["open"]:
            w.set_ref(ref, open=True)
        return Result(True)

    def close_door(self, ref, timeout_s=10):
        r = self.world.t(ref)
        if r is None or r["kind"] != "door":
            return Result(False, "not a door")
        if r["open"]:
            self.world.set_ref(ref, open=False)
        return Result(True)

    def open_container(self, ref, timeout_s=10):
        w = self.world
        r = w.t(ref)
        if r is None or r["kind"] not in ("container", "corpse"):
            return Result(False, "not a container")
        if r["locked"]:
            return Result(False, "container is locked")
        if r["in_use"] not in (None, self.idx):
            return Result(False, f"container is in use by player {r['in_use']}")
        r["in_use"] = self.idx
        return Result(True)

    def close_container(self, ref):
        r = self.world.t(ref)
        if r and r["in_use"] == self.idx:
            r["in_use"] = None
        return Result(True)

    def take(self, item, count=1, from_ref=None, timeout_s=10):
        w = self.world
        r = w.t(from_ref)
        if r is None:
            return Result(False, "no such container")
        if r["in_use"] != self.idx:
            return Result(False, "container is not open")
        have = r["items"].get(item, 0)
        if have < count:
            return Result(False, f"only {have} x {item} left")
        items = dict(r["items"])
        items[item] = have - count
        if items[item] == 0:
            del items[item]
        w.set_ref(from_ref, items=items)
        self.me["inv"][item] = self.me["inv"].get(item, 0) + count
        return Result(True)

    def put(self, item, count, to_ref, timeout_s=10):
        w = self.world
        r = w.t(to_ref)
        if r is None or r["in_use"] != self.idx:
            return Result(False, "container is not open")
        if self.me["inv"].get(item, 0) < count:
            return Result(False, f"only {self.me['inv'].get(item, 0)} x {item} in the inventory")
        self.me["inv"][item] -= count
        items = dict(r["items"])
        items[item] = items.get(item, 0) + count
        w.set_ref(to_ref, items=items)
        return Result(True)

    def pickup(self, ref, timeout_s=10):
        w = self.world
        k = norm(ref)
        v = self.view["refs"].get(k)
        if v is None or v["taken"]:
            return Result(False, "already taken")
        item = v["base"]
        optimistic = item
        self.me["inv"][optimistic] = self.me["inv"].get(optimistic, 0) + 1     # optimistic local apply
        r = w.t(k)
        if r["taken"]:
            if "dup_pickup" not in w.bugs:
                self.me["inv"][optimistic] -= 1                                 # rollback
            return Result(False, "already taken (lost the race)")
        w.set_ref(k, taken=True, enabled=False, taken_by=self.idx)
        return Result(True)

    def drop(self, item, count=1):
        if self.me["inv"].get(item, 0) < count:
            return Result(False, f"only {self.me['inv'].get(item, 0)} x {item}")
        self.me["inv"][item] -= count
        w = self.world
        w.dup_n += 1
        k = f"falloutnv.esm:{0xFF0000 + w.dup_n:X}"
        w.add_ref(k, "item", self.me["pos"], self.me["cell"], base=item)
        w.commit({"op": "ref", "ref": k, "f": copy.deepcopy(w.truth["refs"][k]), "spawn": False})
        return Result(True, "", {"ref": k})

    def talk(self, npc, timeout_s=10):
        w = self.world
        r = w.t(npc)
        if r is None:
            return Result(False, "no such NPC")
        if self.me["dialogue"]:
            return Result(False, "already in dialogue")
        if r["speaking"] not in (None, self.idx):
            return Result(False, "NPC is talking to another player")
        self.me["dialogue"] = norm(npc)
        w.set_ref(npc, speaking=self.idx)
        w.commit({"op": "dialogue", "slot": self.idx, "on": True})
        return Result(True)

    def pick_topic(self, info_id, timeout_s=10):
        w = self.world
        if not self.me["dialogue"]:
            return Result(False, "not in dialogue")
        k = self.me["dialogue"]
        w.set_ref(k, last_info=hexid(info_id))
        eff = w.truth["refs"][k].get("info_effects", {}).get(hexid(info_id))
        if eff == "recruit":
            if w.truth["refs"][k]["follower_of"] is None:        # an NPC that already follows someone cannot be re-recruited
                w.set_ref(k, follower_of=self.idx, owner=self.idx)
        elif isinstance(eff, tuple):
            w.request_stage(*eff)
        return Result(True)

    def end_dialogue(self):
        w = self.world
        k = self.me["dialogue"]
        if k:
            self.me["dialogue"] = None
            w.set_ref(k, speaking=None)
            w.commit({"op": "dialogue", "slot": self.idx, "on": False})
        return Result(True)

    def use_terminal(self, ref, entry=None, timeout_s=10):
        w = self.world
        r = w.t(ref)
        if r is None or r["kind"] != "terminal":
            return Result(False, "not a terminal")
        if r["locked"]:
            return Result(False, "terminal is locked")
        used = list(r["entries_used"]) + [entry or "main"]
        w.set_ref(ref, entries_used=used)
        unlocks = r.get("unlocks")
        if unlocks and entry == "unlock":
            w.set_ref(unlocks, locked=False)
        return Result(True)

    def unlock(self, ref, key=None, timeout_s=10):
        w = self.world
        r = w.t(ref)
        if r is None:
            return Result(False, "no such ref")
        need = r.get("key")
        if need and key != need and self.me["inv"].get(need, 0) <= 0:
            return Result(False, f"need key {need}")
        if r["locked"]:
            w.set_ref(ref, locked=False)
        return Result(True)

    # --- extras (probe) ---
    def set_stage(self, quest, stage):
        if self.idx != 0:
            return Result(False, "guest scripts do not run: only the host advances stages")
        return self.world.request_stage(quest, stage)

    def start_quest(self, quest):
        if self.idx != 0:
            return Result(False, "only the host starts quests")
        self.world.commit({"op": "quest", "q": hexid(quest), "running": True})
        return Result(True)

    def scene_pass(self, quest):
        return self.world.pass_scene(self.idx, quest)

    def menu_press(self, button):
        w = self.world
        k = w.boxes.pop(self.idx, None)
        if k is None:
            return Result(False, "no message box is open")
        if button == 0:
            return Result(True, "", {"declined": True})
        g = w.endgames[k]
        key = (hexid(g["quest"]), g["to"])
        w.deferred[key] = {"kind": "ready", "yes": {self.idx}, "no": set()}      # everybody else gets Ready / Not yet
        return Result(True)

    def consent_pending(self):
        return any(d["kind"] == "ready" and self.idx not in d["yes"] | d["no"] for d in self.world.deferred.values())

    def consent(self, kind, quest="", info="", yes=True):
        w = self.world
        if kind == "ready":
            if not quest:
                for (q, n), d in list(w.deferred.items()):
                    if d["kind"] == "ready":
                        quest = q
                        break
            return w.vote(self.idx, quest, yes)
        self.me["consent"][kind] = bool(yes)
        return Result(True)

    def fast_travel(self, marker):
        w = self.world
        m = w.t(marker)
        if m is None:
            return Result(False, "no such marker")
        if any(r["hostile"] and not r["dead"] and r["enabled"] and r["cell"] == self.me["cell"] for r in w.truth["refs"].values()) \
                and "travel_in_combat" not in w.bugs:
            return Result(False, "fast travel refused: in combat")
        others = [i for i in range(w.n) if i != self.idx]
        if "consent_ignored" not in w.bugs and not all(w.players[i]["consent"].get("fasttravel") for i in others):
            return Result(False, "fast travel refused: party consent missing")
        tcfg = w.tether
        for i in range(w.n):
            w.players[i]["pos"], w.players[i]["cell"] = tuple(m["pos"]), m["cell"]
            w.players[i]["consent"].pop("fasttravel", None)
        w.tether_active = True
        w.rebalance_owners()
        return Result(True)

    def owner_of(self, ref):
        r = self.view["refs"].get(norm(ref))
        if r is None:
            raise RuntimeError(f"{ref} is not loaded on {self.name}")
        return (r["owner"], r["epoch"])

    def ref_count(self, base, cell=""):
        return sum(1 for k, r in self.view["refs"].items()
                   if r.get("base") and norm(r["base"]) == norm(base) and r["enabled"] and not r["dead"] and (not cell or r["cell"] == cell))

    def spawn(self, base, at):
        w = self.world
        if self.idx != 0:
            return Result(False, "only the host spawns")
        a = w.t(at)
        if a is None:
            return Result(False, "no such marker")
        w.dup_n += 1
        k = norm(f"falloutnv.esm:{0xFE0000 + w.dup_n:X}")
        w.commit({"op": "ref", "ref": k, "f": w._mkref(k, "raider", a["pos"], a["cell"], base=base), "spawn": True})
        return Result(True, "", {"ref": k})

    def save_game(self, name):
        w = self.world
        if self.idx != 0:
            return Result(False, "only the host saves the world")
        w.saves[name] = (copy.deepcopy(w.truth), w.seq)
        return Result(True)

    def load_game(self, name):
        w = self.world
        if self.idx != 0:
            return Result(False, "only the host loads the world")
        if name not in w.saves:
            return Result(False, "no such save")
        truth, _ = w.saves[name]
        w.truth = copy.deepcopy(truth)
        w.views[0] = copy.deepcopy(truth)
        w.seq += 1
        w.applied[0] = w.seq
        if "no_rebase" not in w.bugs:
            for i in range(1, w.n):
                w.views[i] = copy.deepcopy(truth)
                w.pending[i] = []
                w.applied[i] = w.seq
                w.rebases[i] += 1
        return Result(True)

    def session_leave(self):
        if self.idx == 0:
            return Result(False, "the host cannot leave")
        self.world.connected[self.idx] = False
        return Result(True)

    def session_join(self):
        w = self.world
        w.connected[self.idx] = True
        if "reward_dup_on_rejoin" in w.bugs:
            for eff in w.reward_log:
                inv = self.me["inv"]
                inv[eff["item"]] = inv.get(eff["item"], 0) + eff["count"]
        return Result(True)

    # --- queries ---
    def health(self, ref=None):
        if ref is None:
            return self.me["hp"]
        return self.view["refs"][norm(ref)]["hp"]

    def pos(self):
        return self.me["pos"]

    def cell(self):
        return self.me["cell"]

    def inventory(self):
        return dict(self.me["inv"])

    def inventory_count(self, item, holder=None):
        if holder is None:
            return int(self.me["inv"].get(item, 0))
        return int(self.view["refs"][norm(holder)]["items"].get(item, 0))

    def death_count(self, ref):
        return int(self.view["refs"][norm(ref)]["deaths"])

    def quest_stage(self, q):
        st = self.view["quests"].get(hexid(q), {}).get("stages", [])
        return max(st) if st else 0

    def stage_done(self, q, n):
        return n in self.view["quests"].get(hexid(q), {}).get("stages", [])

    def quest_running(self, q):
        return bool(self.view["quests"].get(hexid(q), {}).get("running"))

    def objective_displayed(self, q, o):
        return bool(self.view["objectives"].get(hexid(q), {}).get(str(o), {}).get("displayed"))

    def objective_completed(self, q, o):
        return bool(self.view["objectives"].get(hexid(q), {}).get(str(o), {}).get("completed"))

    def target_alive(self, ref):
        r = self.view["refs"].get(norm(ref))
        return bool(r and r["enabled"] and not r["dead"])

    def is_dead(self, ref):
        r = self.view["refs"].get(norm(ref))
        return bool(r and r["dead"])

    def ref_enabled(self, ref):
        r = self.view["refs"].get(norm(ref))
        return bool(r and r["enabled"])

    def ref_container_items(self, ref):
        return dict(self.view["refs"][norm(ref)]["items"])

    def in_dialogue(self):
        return bool(self.me["dialogue"])

    def spectating(self):
        return bool(self.me["spectating"])

    def is_teammate(self, ref):
        r = self.view["refs"].get(norm(ref))
        return bool(r and r["follower_of"] is not None)

    def door_open(self, ref):
        return bool(self.view["refs"][norm(ref)]["open"])

    def is_locked(self, ref):
        return bool(self.view["refs"][norm(ref)]["locked"])

    def terminal_entry_used(self, ref, entry):
        return entry in self.view["refs"][norm(ref)]["entries_used"]

    def in_combat(self):
        return any(r["hostile"] and not r["dead"] and r["enabled"] for r in self.view["refs"].values()
                   if r["cell"] == self.me["cell"])

    def distance_to(self, ref):
        r = self.view["refs"].get(norm(ref))
        if r is None:
            raise RuntimeError(f"{ref} is not loaded")
        return self.world.dist(self.me["pos"], r["pos"])


class FakeComparer(Comparer):
    def snapshot(self, m: Machine, path: str):
        return m.bot.snapshot(path)

    def diff(self, a, b, ignore):
        lines: List[str] = []

        def walk(x, y, path):
            if isinstance(x, dict) and isinstance(y, dict):
                for k in sorted(set(x) | set(y)):
                    if k not in x:
                        lines.append(f"{path}/{k}: only on B")
                    elif k not in y:
                        lines.append(f"{path}/{k}: only on A")
                    else:
                        walk(x[k], y[k], f"{path}/{k}")
            elif x != y:
                lines.append(f"{path}: {x!r} != {y!r}")

        walk(a.get("shared", {}), b.get("shared", {}), "shared")
        import fnmatch
        lines = [l for l in lines if not any(fnmatch.fnmatch(l, g) for g in ignore)]
        return (1 if lines else 0), lines


def make_machines(world: FakeCoop) -> List[Machine]:
    return [Machine(i, b.name, b, instance=i) for i, b in enumerate(world.bots)]
