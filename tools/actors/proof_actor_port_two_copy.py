"""In-game proof of the actor port INSIDE a two-copy session, fully scripted (docs/INGAME-TESTS.md "Step 1-2 Enemy sync: actor-port" item 5).
No human step: every action is a bridge call (coop.owner, actors.port, exec with a calling ref, coop.leave); nothing simulates desktop input.

    python -I tools/actors/proof_actor_port_two_copy.py --host 0 --client 1 --ref Plugin.esm:HEX [--away-ref Plugin.esm:HEX] [--outdir DIR]
    python -I tools/actors/proof_actor_port_two_copy.py --selftest

Setup: host and client in ONE co-op session, both copies near an ordinary living NPC that the HOST owns (the Goodsprings powder ganger of the
manifest is fine), nvcoop.ini `[live] actors=1` on both and `[dev] bots=1` (see the manifest entry actor_port_two_copy). The script never launches a
game. It moves the host (player.MoveTo), kills the NPC, and ends the session on both copies, so run it last in a boot.

What it asserts, in order (each line of the report is one check):
  wiring   actors.port on both: enabled, session=true, fed>0 on the non-owner copy; coop.owner on both: known, same (owner, epoch), the host is the
           owner; GetRestrained (console, with the NPC as calling ref) is 0 on the owner's copy and 1 on the non-owner's copy.
  flip     the host is moved to --away-ref (another cell, default the Novac map marker) with player.MoveTo. Both copies are sampled over and over while
           ownership moves: the owner becomes the client, the epoch rises by exactly one, GetRestrained flips with it, and at NO sample are both copies
           at 0 (three reads bracket the pair, so one machine is seen at 0 before and after the other one was read: no false alarm from the order of
           the reads). The NPC's position never steps further than JUMP_UNITS between two samples on either copy (it keeps its walk position) and the
           two copies end within CROSS_UNITS of each other. Then the host moves back next to the NPC and everything is judged again once it settles.
  kill     KillActor on the machine that owns the NPC at that moment: GetDead becomes 1 on both copies and actors.port stats.deathsRefused did not rise
           on either.
  leave    coop.leave on the client, then on the host: each copy's actors.port says session=false and its ledger (restrained, standIn, disabled,
           essential, stale) is all 0, GetRestrained on the NPC is 0, and no actor of world.actors is restrained on either copy.

A copy that cannot read the NPC (its cell is not loaded there) gives an unreadable sample: it is skipped, never counted as 0 (the report says how many
samples were unreadable). Exit code: 0 pass, 1 a check failed, 2 usage / cannot reach the game, 3 not ready (nothing judged: no actors.port, no session,
the host does not own the NPC, coop.owner does not know it). Every threshold is a first guess.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import math
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tools" / "runner"))
from proof_actor_port import NotReady, Result  # noqa: E402

DEFAULT_AWAY_REF = "FalloutNV.esm:09289B"   # NovacMapMarker, the destination of scenarios/ownership_handoff.json (far from Goodsprings)
JUMP_UNITS = 350.0          # largest NPC step between two samples that still counts as walking (same number as the ownership playbook)
CROSS_UNITS = 200.0         # the two copies of the NPC must end this close to each other
POLL_S = 0.25
SETTLE_SAMPLES = 3          # samples that must agree after a handoff before it is judged
WIRING_TIMEOUT_S = 30.0
FLIP_TIMEOUT_S = 60.0
RETURN_WATCH_S = 8.0        # after the host is back next to the NPC the copies keep being sampled (a handoff back may happen) before the end state is judged
KILL_TIMEOUT_S = 20.0
LEAVE_TIMEOUT_S = 25.0
LEDGER_KEYS = ("restrained", "standIn", "disabled", "essential", "stale")


def dist(a: Tuple[float, float, float], b: Tuple[float, float, float]) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


class Proof:
    """`host` and `client` are NvBridge-like: call(method, params), eval(expr, ref=...), exec(cmd, ref=...)."""

    def __init__(self, host: Any, client: Any, res: Result, npc: str, away_ref: str = DEFAULT_AWAY_REF, allow_restrained: Tuple[str, ...] = (),
                 sleep: Callable[[float], None] = time.sleep, now: Callable[[], float] = time.monotonic) -> None:
        self.m = {"host": host, "client": client}
        self.r = res
        self.npc = npc
        self.away_ref = away_ref
        self.allow = {a.split(":")[-1].upper().zfill(6) for a in allow_restrained}
        self.sleep, self.now = sleep, now
        self.rounds = 0
        self.unreadable = 0
        self.both_free: List[float] = []
        self.restr: Dict[str, Optional[int]] = {"host": None, "client": None}
        self.pos_prev: Dict[str, Optional[Tuple[float, float, float]]] = {"host": None, "client": None}
        self.max_step: Dict[str, float] = {"host": 0.0, "client": 0.0}
        self.pos_last: Dict[str, Optional[Tuple[float, float, float]]] = {"host": None, "client": None}
        self.epochs: Dict[str, List[int]] = {"host": [], "client": []}

    # ---- reads ---------------------------------------------------------------------------------------------------------------------
    def restrained(self, who: str) -> Optional[int]:
        try:
            return int(self.m[who].eval("GetRestrained", ref=self.npc))
        except Exception:  # noqa: BLE001 - the ref is not loaded on that copy
            self.unreadable += 1
            return None

    def dead(self, who: str) -> Optional[int]:
        try:
            return int(self.m[who].eval("GetDead", ref=self.npc))
        except Exception:  # noqa: BLE001
            return None

    def pos(self, who: str) -> Optional[Tuple[float, float, float]]:
        try:
            return tuple(float(self.m[who].eval(f"GetPos {a}", ref=self.npc)) for a in "XYZ")  # type: ignore[return-value]
        except Exception:  # noqa: BLE001
            return None

    def owner(self, who: str) -> Dict[str, Any]:
        try:
            o = self.m[who].call("coop.owner", {"ref": self.npc})
        except Exception as e:  # noqa: BLE001
            raise NotReady(f"coop.owner is not answering on the {who} ({e})") from e
        if o.get("unsupported"):
            raise NotReady(f"coop.owner is unsupported on the {who}: {o.get('reason')}")
        return o

    def port(self, who: str) -> Dict[str, Any]:
        try:
            return self.m[who].call("actors.port", {})
        except Exception as e:  # noqa: BLE001
            raise NotReady(f"actors.port is not registered on the {who} ({e}): nvcoop.dll without plugin/actors/engine") from e

    def runtime_id(self, spec: str) -> str:
        """'Plugin.esm:HEX' -> 8 hex digits with the load-order index of the HOST's game (MoveTo needs a runtime id)."""
        if ":" not in spec:
            return spec.upper().zfill(8)
        name, local = spec.rsplit(":", 1)
        for p in self.m["host"].call("plugins", {}).get("plugins", []):
            if p["name"].lower() == name.lower():
                return "%08X" % ((int(p["index"]) << 24) | int(local, 16))
        raise NotReady(f"plugin {name} is not loaded on the host")

    # ---- one sample of both copies -------------------------------------------------------------------------------------------------
    def sample(self) -> Dict[str, Any]:
        first, second = ("host", "client") if self.rounds % 2 == 0 else ("client", "host")
        self.rounds += 1
        a = self.restrained(first)
        b = self.restrained(second)
        a2 = self.restrained(first)
        if a == 0 and b == 0 and a2 == 0:
            self.both_free.append(self.now())
        self.restr[first], self.restr[second] = a2, b
        snap: Dict[str, Any] = {"restrained": dict(self.restr), "owner": {}}
        for who in ("host", "client"):
            p = self.pos(who)
            if p is not None:
                prev = self.pos_prev[who]
                if prev is not None:
                    self.max_step[who] = max(self.max_step[who], dist(prev, p))
                self.pos_prev[who] = p
                self.pos_last[who] = p
            o = self.owner(who)
            snap["owner"][who] = o
            if o.get("known"):
                self.epochs[who].append(int(o.get("epoch", 0)))
        return snap

    @staticmethod
    def agreed(snap: Dict[str, Any]) -> Optional[Tuple[int, int]]:
        o = snap["owner"]
        if not all(o[w].get("known") and not o[w].get("pending") for w in o):
            return None
        pairs = {(int(o[w]["owner"]), int(o[w]["epoch"])) for w in o}
        return next(iter(pairs)) if len(pairs) == 1 else None

    def sample_until(self, done: Callable[[Dict[str, Any]], bool], timeout_s: float) -> Optional[Dict[str, Any]]:
        """Sample until `done(snap)` holds SETTLE_SAMPLES times in a row; returns the last snapshot, or None on timeout."""
        end = self.now() + timeout_s
        ok = 0
        while True:
            snap = self.sample()
            ok = ok + 1 if done(snap) else 0
            if ok >= SETTLE_SAMPLES:
                return snap
            if self.now() >= end:
                return None
            self.sleep(POLL_S)

    # ---- steps ---------------------------------------------------------------------------------------------------------------------
    def wiring(self) -> Dict[str, Any]:
        c = self.r.check
        ports = {w: self.port(w) for w in ("host", "client")}
        for w, st in ports.items():
            if not st.get("enabled"):
                raise NotReady(f"the actor port is disabled on the {w} ([actors] engine=0)")
        c("wiring.session_both", all(st.get("session") for st in ports.values()), f"session host {ports['host'].get('session')} client {ports['client'].get('session')}")
        snap = self.sample_until(lambda s: self.agreed(s) is not None, WIRING_TIMEOUT_S)
        if snap is None:
            raise NotReady("coop.owner does not know the NPC on both copies (or they disagree): is [live] actors=1 on both, and are both near the NPC?")
        owner, epoch = self.agreed(snap)  # type: ignore[misc]
        c("wiring.epoch_positive", epoch >= 1, f"epoch {epoch}")
        if owner != 0 or not snap["owner"]["host"].get("mine", owner == 0):
            raise NotReady(f"the host does not own the NPC (owner slot {owner}): stand the host next to it and the client further away")
        non_owner = "client"
        ports = {w: self.port(w) for w in ("host", "client")}
        c("wiring.fed_non_owner", int(ports[non_owner].get("fed", 0)) > 0, f"actors.port fed {ports[non_owner].get('fed')} on the {non_owner}")
        c("wiring.owner_copy_free", snap["restrained"]["host"] == 0, f"GetRestrained on the owner's copy = {snap['restrained']['host']}")
        c("wiring.non_owner_copy_restrained", snap["restrained"]["client"] == 1, f"GetRestrained on the non-owner's copy = {snap['restrained']['client']}")
        self.r.info["start"] = {"owner": owner, "epoch": epoch}
        return snap

    def travel(self, spec: str, what: str) -> None:
        r = self.m["host"].exec(f"player.MoveTo {self.runtime_id(spec)}")
        self.r.check(f"flip.travel_{what}", bool(r.get("ok", True)), str(r.get("output", "")))

    def settled(self, snap: Dict[str, Any]) -> bool:
        """Agreed owner, and the copies' GetRestrained are the opposite of each other where readable (owner 0, other 1)."""
        pair = self.agreed(snap)
        if pair is None:
            return False
        owner_name = "host" if pair[0] == 0 else "client"
        other = "client" if owner_name == "host" else "host"
        r = snap["restrained"]
        return r[owner_name] in (0, None) and r[other] in (1, None) and r[owner_name] is not None

    def flip(self, start: Dict[str, Any]) -> Dict[str, Any]:
        c = self.r.check
        e0 = self.r.info["start"]["epoch"]
        self.travel(self.away_ref, "away")
        snap = self.sample_until(lambda s: self.settled(s) and self.agreed(s)[0] == 1, FLIP_TIMEOUT_S)  # type: ignore[index]
        pair = self.agreed(snap) if snap else None
        c("flip.owner_moved_to_client", snap is not None, "the owner never became the client on both copies, or GetRestrained did not follow")
        if snap is not None and pair is not None:
            c("flip.epoch_plus_one", pair[1] == e0 + 1, f"epoch {e0} -> {pair[1]}")
            c("flip.client_copy_free", snap["restrained"]["client"] == 0, f"GetRestrained on the new owner's copy = {snap['restrained']['client']}")
        # the host comes back next to the NPC; the copies must again agree, whoever owns it now
        self.travel(self.r.info["npc_back"], "back")
        end = self.now() + RETURN_WATCH_S
        while self.now() < end:
            self.sample()
            self.sleep(POLL_S)
        snap2 = self.sample_until(self.settled, FLIP_TIMEOUT_S)
        pair2 = self.agreed(snap2) if snap2 else None
        c("flip.settled_after_return", snap2 is not None, "no agreed owner with opposite GetRestrained after the host came back")
        if snap2 is not None and pair2 is not None:
            who = "host" if pair2[0] == 0 else "client"
            other = "client" if who == "host" else "host"
            c("flip.restrained_follows_owner", snap2["restrained"][who] == 0 and snap2["restrained"][other] in (1, None),
              f"owner {who}: GetRestrained {snap2['restrained']}")
            self.r.info["owner_now"] = who
        c("flip.never_both_free", not self.both_free, f"both copies read 0 at {len(self.both_free)} sample(s) ({self.unreadable} unreadable reads skipped)")
        for w in ("host", "client"):
            c(f"flip.no_jump_{w}", self.max_step[w] < JUMP_UNITS, f"largest NPC step on the {w} copy {self.max_step[w]:.0f} units (limit {JUMP_UNITS:.0f})")
        a, b = self.pos_last["host"], self.pos_last["client"]
        if a is not None and b is not None:
            c("flip.copies_agree", dist(a, b) < CROSS_UNITS, f"the two copies are {dist(a, b):.0f} units apart (limit {CROSS_UNITS:.0f})")
        for w in ("host", "client"):
            ep = self.epochs[w]
            c(f"flip.epoch_never_falls_{w}", all(y >= x for x, y in zip(ep, ep[1:])), f"epochs seen {ep[:12]}")
        return snap2 or snap or start

    def kill(self) -> None:
        c = self.r.check
        who = self.r.info.get("owner_now", "host")
        before = {w: int(self.port(w).get("stats", {}).get("deathsRefused", 0)) for w in ("host", "client")}
        r = self.m[who].exec("KillActor", ref=self.npc)
        c("kill.line_accepted", bool(r.get("ok", True)), f"on the owner ({who}): {r.get('output', '')}")
        end = self.now() + KILL_TIMEOUT_S
        dead: Dict[str, Optional[int]] = {}
        while True:
            dead = {w: self.dead(w) for w in ("host", "client")}
            if all(v == 1 for v in dead.values()) or self.now() >= end:
                break
            self.sleep(POLL_S)
        c("kill.dead_on_owner", dead.get(who) == 1, f"GetDead on the owner ({who}) = {dead.get(who)}")
        c("kill.dead_on_both", all(v == 1 for v in dead.values()), f"GetDead {dead}")
        after = {w: int(self.port(w).get("stats", {}).get("deathsRefused", 0)) for w in ("host", "client")}
        c("kill.deaths_refused_zero", after == before, f"actors.port stats.deathsRefused before {before} after {after}")

    def scan_restrained(self, who: str) -> List[str]:
        out: List[str] = []
        try:
            actors = self.m[who].call("world.actors", {"max": 60}).get("actors", [])
        except Exception:  # noqa: BLE001
            return out
        for a in actors:
            ref = a.get("ref") or ""
            if not ref or ref.split(":")[-1].upper().zfill(6) in self.allow:
                continue
            try:
                if int(self.m[who].eval("GetRestrained", ref=ref)) == 1:
                    out.append(ref)
            except Exception:  # noqa: BLE001
                continue
        return out

    def leave(self) -> None:
        c = self.r.check
        for who in ("client", "host"):
            try:
                self.m[who].call("coop.leave", {})
                accepted = True
            except Exception as e:  # noqa: BLE001
                accepted = False
                c(f"leave.{who}_accepted", False, str(e))
            if not accepted:
                continue
            end = self.now() + LEAVE_TIMEOUT_S
            while True:
                st = self.port(who)
                led = st.get("ledger", {})
                if not st.get("session") and all(int(led.get(k, 0)) == 0 for k in LEDGER_KEYS):
                    break
                if self.now() >= end:
                    break
                self.sleep(POLL_S)
            led = st.get("ledger", {})
            c(f"leave.{who}_session_ended", not st.get("session"), f"actors.port session {st.get('session')}")
            c(f"leave.{who}_ledger_zero", all(int(led.get(k, 0)) == 0 for k in LEDGER_KEYS), f"ledger {led}")
            c(f"leave.{who}_npc_not_restrained", self.restrained(who) == 0, f"GetRestrained on the NPC = {self.restrained(who)}")
        for who in ("host", "client"):
            left = self.scan_restrained(who)
            c(f"leave.{who}_no_actor_restrained", not left, f"still restrained on the {who}: {left[:6]}")

    def run(self) -> Result:
        self.r.info.update({"npc": self.npc, "away_ref": self.away_ref, "npc_back": self.npc})
        start = self.wiring()
        self.flip(start)
        self.kill()
        self.leave()
        self.r.info.update({"unreadable_reads": self.unreadable, "max_step": self.max_step})
        return self.r


# ---- offline self-test: a fake two-copy session ---------------------------------------------------------------------------------------
class FakeSession:
    """Host and client in one session around one NPC. The owner is the host while the host stands near the NPC, else the client. A handoff takes
    FLIP_DELAY after the host moved, a short pending window, then the new non-owner is restrained BEFORE the new owner is released. Faults (a set of
    names) each break one promise; the proof must fail on exactly the check named in TWINS."""

    FLIP_DELAY = 3.0
    PENDING = 0.4
    DEATH_LAG = 0.5
    CALL_S = 0.02

    def __init__(self, faults: Optional[set] = None) -> None:
        self.f = faults or set()
        self.t = 0.0
        self.owner = 0 if "host_not_owner" not in self.f else 1
        self.epoch = 1
        self.host_cell = "near"
        self.cell_changed_at = -100.0
        self.pending = False
        self.flip_to: Optional[int] = None
        self.ev: List[Tuple[float, str, int]] = []
        self.restr = {0: False, 1: True}
        if "owner_restrained" in self.f:
            self.restr[0] = True
        if "non_owner_free" in self.f:
            self.restr[1] = False
        if "host_not_owner" in self.f:
            self.restr = {0: True, 1: False}
        self.session = {0: True, 1: "no_session" not in self.f}
        self.dead = {0: False, 1: False}
        self.refused = {0: 0, 1: 0}
        self.offset = {0: 0.0, 1: 0.0}
        self.left = {0: False, 1: False}
        self.machines = {"host": FakeMachine(self, 0), "client": FakeMachine(self, 1)}

    # clock and state machine
    def sleep(self, s: float) -> None:
        self.t += s
        self.update()

    def now(self) -> float:
        return self.t

    def update(self) -> None:
        want = 0 if self.host_cell == "near" else 1
        if "no_flip" in self.f or "host_not_owner" in self.f:
            want = self.owner
        if self.flip_to is None and want != self.owner and self.t >= self.cell_changed_at + self.FLIP_DELAY:
            self.flip_to = want
            self.pending = True
            t1 = self.t + self.PENDING
            if "gap" in self.f:     # fault: the new owner is freed first, the old one is held 0.8 s later: both copies are free in between
                self.ev += [(t1, "free_new", want), (t1 + 0.8, "hold_old", want)]
            else:                   # right order: hold the old owner's copy first, then free the new owner's
                self.ev += [(t1, "hold_old", want), (t1 + 0.01, "free_new", want)]
            self.ev.append((t1, "move_owner", want))
        for ev in sorted(self.ev, key=lambda e: (e[0], e[1])):
            when, what, new = ev
            if when > self.t:
                continue
            self.ev.remove(ev)
            old = 1 - new
            if what == "hold_old":
                self.restr[old] = True
            elif what == "free_new":
                if "restrained_stuck" not in self.f:
                    self.restr[new] = False
                self.pending = False
                self.flip_to = None
                if "jump" in self.f:
                    self.offset[new] += 600.0
            elif what == "move_owner":
                self.owner = new
                self.epoch += 1
            elif what == "die":
                for m in (0, 1):
                    if m == self.owner:
                        self.dead[m] = True
                    elif "death_not_synced" in self.f:
                        pass
                    elif "death_refused" in self.f:
                        self.refused[m] += 1
                    else:
                        self.dead[m] = True
        # a machine that left holds nothing
        for m in (0, 1):
            if self.left[m] and "leave_keeps_restrained" not in self.f:
                self.restr[m] = False

    def ledger(self, m: int) -> Dict[str, int]:
        n = 1 if (self.restr[m] and self.session[m]) else 0
        if self.left[m] and "leave_keeps_ledger" in self.f:
            n = 1
        return {"restrained": n, "standIn": 0, "disabled": 0, "essential": 0, "stale": 0}

    def npc_pos(self, m: int) -> Tuple[float, float, float]:
        return (1000.0 + 20.0 * self.t + self.offset[m], 2000.0, 30.0)


class FakeMachine:
    def __init__(self, s: FakeSession, idx: int) -> None:
        self.s, self.i = s, idx

    def _tick(self) -> None:
        self.s.t += self.s.CALL_S
        self.s.update()

    def call(self, method: str, params: Optional[dict] = None) -> Any:
        s, i = self.s, self.i
        self._tick()
        if method == "actors.port":
            fed = 0 if (i == 1 and "not_fed" in s.f) else 3
            return {"enabled": True, "session": s.session[i], "fed": fed, "ledger": s.ledger(i), "stats": {"deathsRefused": s.refused[i]}}
        if method == "coop.owner":
            return {"known": True, "owner": s.owner, "epoch": s.epoch, "mine": s.owner == i, "pending": s.pending}
        if method == "plugins":
            return {"plugins": [{"name": "FalloutNV.esm", "index": 0}]}
        if method == "world.actors":
            return {"actors": [{"id": 1, "ref": "FalloutNV.esm:104C70"}]}
        if method == "coop.leave":
            if s.left[i]:
                raise RuntimeError("already left the session")
            s.left[i] = True
            s.session[i] = False
            if "leave_keeps_ledger" not in s.f and "leave_keeps_restrained" not in s.f:
                s.restr[i] = False
            if i == 1 and s.owner == 1:
                s.owner = 0
            return {"ok": True}
        raise KeyError(method)

    def eval(self, expr: str, ref: Optional[str] = None) -> float:
        s, i = self.s, self.i
        self._tick()
        # the host's copy is not loaded while it stands in the other cell and the fault 'unloaded_away' is on (an unreadable sample)
        if i == 0 and s.host_cell == "far" and "unloaded_away" in s.f:
            raise RuntimeError("ref not loaded")
        if expr == "GetRestrained":
            return 1.0 if s.restr[i] else 0.0
        if expr == "GetDead":
            return 1.0 if s.dead[i] else 0.0
        if expr.startswith("GetPos"):
            return s.npc_pos(i)["XYZ".index(expr.split()[1])]
        raise KeyError(expr)

    def exec(self, cmd: str, ref: Optional[str] = None) -> dict:
        s, i = self.s, self.i
        self._tick()
        if cmd.startswith("player.MoveTo") and i == 0:
            target = cmd.split()[-1]
            s.host_cell = "far" if target == "0009289B" else "near"
            s.cell_changed_at = s.t
        elif cmd == "KillActor":
            if s.owner == i:
                s.ev.append((s.t + s.DEATH_LAG, "die", 0))
                s.dead[i] = True
        return {"ok": True, "output": ""}


TWINS = {   # fault -> the check that must fail
    "no_session": "wiring.session_both", "not_fed": "wiring.fed_non_owner", "owner_restrained": "wiring.owner_copy_free",
    "non_owner_free": "wiring.non_owner_copy_restrained", "no_flip": "flip.owner_moved_to_client", "gap": "flip.never_both_free",
    "jump": "flip.no_jump_client", "restrained_stuck": "flip.owner_moved_to_client", "death_not_synced": "kill.dead_on_both",
    "death_refused": "kill.deaths_refused_zero", "leave_keeps_ledger": "leave.client_ledger_zero", "leave_keeps_restrained": "leave.client_npc_not_restrained",
}


def run_fake(faults: set) -> Result:
    s = FakeSession(faults)
    res = Result()
    with contextlib.redirect_stdout(io.StringIO()):
        Proof(s.machines["host"], s.machines["client"], res, "FalloutNV.esm:104C70", sleep=s.sleep, now=s.now).run()
    return res


def selftest() -> int:
    ok = True
    good = run_fake(set())
    if good.failures:
        print("selftest: a correct two-copy session failed the proof:", good.failures)
        ok = False
    else:
        print(f"selftest: a correct two-copy session passes all {len(good.checks)} checks")
    for fault, expect in TWINS.items():
        res = run_fake({fault})
        names = [f.split(":")[0] for f in res.failures]
        if expect not in names:
            print(f"selftest: TWIN SURVIVED {fault!r}: expected {expect} to fail, failures were {names}")
            ok = False
        else:
            print(f"selftest: twin {fault!r} caught by {expect}")
    # an unreadable copy (the cell is not loaded on the host while it is away) is skipped, not counted as 0 and not a failure
    res = run_fake({"unloaded_away"})
    if res.failures:
        print("selftest: unreadable samples were counted as failures:", res.failures)
        ok = False
    else:
        print("selftest: unreadable samples are skipped (reported, not failed)")
    # not-ready paths: the host does not own the NPC; no actors.port at all
    s = FakeSession({"host_not_owner"})
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            Proof(s.machines["host"], s.machines["client"], Result(), "FalloutNV.esm:104C70", sleep=s.sleep, now=s.now).run()
        print("selftest: a host that does not own the NPC was not reported as not ready")
        ok = False
    except NotReady:
        print("selftest: a host that does not own the NPC is reported as not ready")

    class Dead:
        def call(self, method: str, params: Optional[dict] = None) -> Any:
            raise RuntimeError("no such method")

    try:
        Proof(Dead(), Dead(), Result(), "FalloutNV.esm:104C70").run()
        print("selftest: a missing actors.port was not reported as not ready")
        ok = False
    except NotReady:
        print("selftest: a missing actors.port is reported as not ready")
    if dist((0, 0, 0), (3, 4, 0)) != 5.0:
        print("selftest: dist helper wrong")
        ok = False
    if ok:
        print("proof_actor_port_two_copy selftest: ALL OK")
    return 0 if ok else 1


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--host", type=int, default=0, help="bridge instance id of the host")
    ap.add_argument("--client", type=int, default=1, help="bridge instance id of the client")
    ap.add_argument("--ref", default=None, help="Plugin.esm:HEX of the NPC the host owns")
    ap.add_argument("--away-ref", default=DEFAULT_AWAY_REF, help="Plugin.esm:HEX the host is moved to (another cell); default %(default)s, the Novac map marker")
    ap.add_argument("--allow-restrained", action="append", default=[], help="Plugin.esm:HEX of an actor that is legitimately restrained in the world (repeatable)")
    ap.add_argument("--outdir", default="out/actor-port/two-copy")
    ap.add_argument("--wait", type=float, default=30.0)
    ap.add_argument("--selftest", action="store_true")
    try:
        a = ap.parse_args(argv)
    except SystemExit:
        return 2
    if a.selftest:
        return selftest()
    if not a.ref:
        print("--ref Plugin.esm:HEX (the NPC the host owns) is required", file=sys.stderr)
        return 2
    from nvbridge import NvBridge  # noqa: E402

    out = Path(a.outdir)
    out.mkdir(parents=True, exist_ok=True)
    res = Result()
    try:
        hb = NvBridge.connect(instance=a.host, wait_s=a.wait)
        cb = NvBridge.connect(instance=a.client, wait_s=a.wait)
    except Exception as e:  # noqa: BLE001
        print(f"cannot reach the game instances: {e}", file=sys.stderr)
        return 2
    try:
        Proof(hb, cb, res, a.ref, a.away_ref, tuple(a.allow_restrained)).run()
    except NotReady as e:
        print(f"proof_actor_port_two_copy: NOT READY: {e}")
        return 3
    finally:
        hb.close()
        cb.close()
    (out / "report.json").write_text(json.dumps({"checks": res.checks, "info": res.info}, indent=2, default=str), encoding="utf-8")
    if res.failures:
        print(f"proof_actor_port_two_copy: {len(res.failures)} check(s) FAILED:")
        for f in res.failures:
            print("  -", f)
        return 1
    print(f"proof_actor_port_two_copy: all {len(res.checks)} checks passed (report in {out})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
