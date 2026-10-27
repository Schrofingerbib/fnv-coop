"""The room engine of the lab runner: checks that fail fast, time boxes, the first digest divergence, moved counters.

A room is a function `room(ctx)`. It talks to two machines (`ctx.host`, `ctx.client`, both a `Machine`) and records checks through the ctx:

    ctx.check(name, ok, expected, actual)           one fact; the first one that is not ok ends the room (fail fast)
    ctx.agree(name, fn, timeout_s, expected)        wait until fn(machine) gives the same answer on both machines (and `expected`)
    ctx.wait(name, fn, timeout_s, expected)         wait until fn() -> (ok, actual) holds
    ctx.skip(name, why)                              a check that cannot be made in this build (shown, never a failure)

Nothing here knows the game: the same rooms run against the fake world (tools/lab/labfake.py) and against the bridge (lab_live.py).
LAB_MUTANT=<name> switches on one deliberate bug for the twins in tools/lab/run_tests.py.
"""
from __future__ import annotations

import math
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "tools" / "proof"))
import pf_classify as C   # noqa: E402

MUTANT = os.environ.get("LAB_MUTANT", "")   # test-only; names in tools/lab/run_tests.py

HOST, CLIENT = "host", "client"


# ------------------------------------------------------------------ how a room ends
class RoomError(Exception):
    pass


class Fail(RoomError):
    """A check was not ok. `rec` is the check record (already stored in ctx.checks)."""

    def __init__(self, rec: Dict[str, Any]):
        super().__init__(rec.get("name", ""))
        self.rec = rec


class Blocked(RoomError):
    """The room cannot run here: the layout lacks something (cls TEST), the build lacks a bridge method (BUILD), the game is not ready (ENV)."""

    def __init__(self, reason: str, cls: str = "TEST"):
        super().__init__(reason)
        self.reason, self.cls = reason, cls


class TimeBox(RoomError):
    pass


class Crashed(RoomError):
    def __init__(self, which: List[str], detail: str = ""):
        super().__init__(detail or f"game copy exited: {', '.join(which)}")
        self.which = which


class RealClock:
    virtual = False

    def now(self) -> float:
        return time.monotonic()

    def sleep(self, s: float) -> None:
        time.sleep(max(0.0, s))


# ------------------------------------------------------------------ the machine API (rooms use only these)
class Machine:
    """One copy of the game. lab_live.LiveMachine drives it through the bridge, labfake.FakeMachine simulates it. Refs are forms
    `Plugin.esp:XXXXXX`; the same form is the same runtime id on both copies. Methods that act return (ok, why) unless said otherwise."""
    name = HOST
    role = HOST

    def status(self) -> Dict[str, Any]: raise NotImplementedError                 # coop.status
    def cell_of(self, ref: Optional[str] = None) -> int: raise NotImplementedError   # the player's cell, or a ref's cell
    def ref_id(self, ref: str) -> str: raise NotImplementedError                  # the runtime id this copy resolves the form to ('' when it does not)
    def exists(self, ref: str) -> bool: raise NotImplementedError
    def pos(self, ref: Optional[str] = None) -> Tuple[float, float, float]: raise NotImplementedError
    def goto_ref(self, ref: str, dist: float = 0.0) -> Tuple[bool, str]: raise NotImplementedError          # teleport next to a ref
    def goto_pos(self, x: float, y: float, z: float) -> Tuple[bool, str]: raise NotImplementedError         # teleport
    def coc(self, cells: List[str]) -> Tuple[bool, str]: raise NotImplementedError                          # `coc <cell>` (first editor id that works), then wait until the load is over
    def walk_to(self, x: float, y: float, z: float, timeout_s: float = 30.0, tick: Optional[Callable[[], None]] = None) -> Tuple[bool, str]: raise NotImplementedError
    def puppet_pos(self) -> Optional[Tuple[float, float, float]]: raise NotImplementedError                 # where THIS copy shows the other player
    def alive(self, ref: str) -> bool: raise NotImplementedError
    def health(self, ref: str) -> float: raise NotImplementedError
    def enabled(self, ref: str) -> bool: raise NotImplementedError                                          # placed and not disabled / picked up
    def arm(self) -> Tuple[bool, str]: raise NotImplementedError                                            # health up, a weapon with ammo equipped
    def enable(self, ref: str) -> Tuple[bool, str]: raise NotImplementedError                              # console enable (host)
    def disable(self, ref: str) -> Tuple[bool, str]: raise NotImplementedError
    def set_health(self, ref: str, hp: float) -> Tuple[bool, str]: raise NotImplementedError               # SetAV Health on an actor (only its owner, the host, can make that stick)
    def kill(self, ref: str) -> Tuple[bool, str]: raise NotImplementedError                                 # console kill (only the owner may)
    def shoot(self, ref: str, volleys: int = 12, stand: Optional[str] = None) -> Tuple[bool, str]: raise NotImplementedError   # fire until the ref is dead on this copy; `stand`: a safe spot to step back to when a hostile gets too close
    def vats(self, ref: str, limb: Optional[str] = None, stand: Optional[str] = None) -> Tuple[bool, str]: raise NotImplementedError   # no limb: the engine job cannot choose one
    def item_count(self, item: str, holder: Optional[str] = None) -> int: raise NotImplementedError         # holder None: this copy's player
    def can_take(self) -> Tuple[bool, str]:
        """(True, "") when this copy can take from a container the way the lab drives it; else (False, why). The lab takes with console lines (RemoveItem +
        AddItem), and a guest's console RemoveItem on a container is blocked on purpose (plugin/hooks gate: the guest's world changes go through the
        container menu and the intents). No bridge method clicks that menu yet, so the guest's take has no honest driver."""
        return True, ""

    def take(self, container: str, item: str, n: int = 1) -> Tuple[bool, str]: raise NotImplementedError
    def pickup(self, ref: str) -> Tuple[bool, str]: raise NotImplementedError
    def door_state(self, ref: str) -> str: raise NotImplementedError                                        # open | closed | ...
    def locked(self, ref: str) -> bool: raise NotImplementedError
    def open_door(self, ref: str) -> Tuple[bool, str]: raise NotImplementedError
    def close_door(self, ref: str) -> Tuple[bool, str]: raise NotImplementedError
    def use_terminal(self, ref: str, entry: int) -> Tuple[bool, str]: raise NotImplementedError
    def stage(self, quest: str) -> int: raise NotImplementedError
    def set_stage(self, quest: str, stage: int) -> Tuple[bool, str]: raise NotImplementedError
    def var(self, quest: str, name: str) -> Optional[float]: raise NotImplementedError                      # None: this build cannot read it
    def set_var(self, quest: str, name: str, value: float) -> Tuple[bool, str]: raise NotImplementedError
    def owner(self, ref: str) -> Dict[str, Any]: raise NotImplementedError                                  # coop.owner: owner, epoch, known, mine


def dist2(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def dist3(a, b) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


SHORT_EXPECTED = 200       # characters of an expected value kept in results.json
SHORT_ACTUAL = 400         # ... of an actual value (a failure reason must be readable whole by an AI: the why of a bot failure is 150..300 characters)


def short(v: Any, n: int = SHORT_ACTUAL) -> Any:
    """A value as it goes into results.json: numbers stay numbers, long text is cut (at n characters, with a ~)."""
    if isinstance(v, float):
        return round(v, 2)
    if isinstance(v, (int, bool)) or v is None:
        return v
    if isinstance(v, (list, tuple)):
        return [short(x, n) for x in v][:8]
    if isinstance(v, dict):
        return {str(k): short(x, n) for k, x in list(v.items())[:8]}
    s = str(v)
    return s if len(s) <= n else s[: n - 1] + "~"


# ------------------------------------------------------------------ digests
def new_desync_entries(desyncs: Dict[str, Any], since_id: int) -> List[Dict[str, Any]]:
    """The mismatching entries of the desync reports newer than `since_id`, OLDEST first: [{id, key, host, client, repaired}]."""
    out = []
    for r in sorted((desyncs or {}).get("desyncs") or [], key=lambda r: r.get("id", 0)):
        if r.get("id", 0) <= since_id:
            continue
        for e in r.get("entries") or []:
            out.append({"id": r.get("id"), "key": e.get("key"), "host": e.get("host"), "client": e.get("client"), "repaired": bool(r.get("repaired"))})
    return out


def digest_counts(status: Dict[str, Any]) -> Dict[str, int]:
    dg = ((status or {}).get("desyncs") or {}).get("digests") or {}
    return {k: int(dg.get(k, 0) or 0) for k in ("ok", "mismatch", "repairs")}


def connected(host_status: Dict[str, Any], client_status: Dict[str, Any]) -> Tuple[bool, str]:
    """Both copies agree they are in one session: the host sees the peer joined and everything acked, the client is joined and its transport is up."""
    h = (host_status or {}).get("host") or {}
    c = (client_status or {}).get("client") or {}
    tr = (client_status or {}).get("transport") or {}
    peers = h.get("peers") if isinstance(h.get("peers"), list) else []
    why = []
    if (host_status or {}).get("role") != "host":
        why.append(f"host role is {(host_status or {}).get('role')!r}")
    if (client_status or {}).get("role") != "client":
        why.append(f"client role is {(client_status or {}).get('role')!r}")
    if not peers or not all(p.get("joined") for p in peers):
        why.append("host sees no joined peer")
    if h.get("allSynced") is False:
        why.append("host: not all synced")
    if not c.get("joined"):
        why.append("client not joined")
    if tr.get("connected") is False:
        why.append("client transport down")
    return (not why), "; ".join(why)


# ------------------------------------------------------------------ the context of a room
class Ctx:
    def __init__(self, room: str, layout, host: Machine, client: Machine, clock=None, budget_s: float = 120.0, params: Optional[Dict[str, Any]] = None,
                 probe: Optional[Callable[[], List[str]]] = None, enter: Optional[Callable[[], None]] = None, area: str = "", log: Callable[[str], None] = lambda s: None):
        self.room, self.layout, self.host, self.client = room, layout, host, client
        self.clock = clock or RealClock()
        self.params = dict(params or {})
        self.probe, self.enter_hook, self.area, self.log = probe, enter, area, log
        self.t0 = self.clock.now()
        self.deadline = self.t0 + budget_s
        self.budget_s = budget_s
        self.cleanups: List[Callable[[], None]] = []
        self.checks: List[Dict[str, Any]] = []
        self.metrics: Dict[str, Any] = {}
        self.failure: Dict[str, Any] = {}
        self._flat0: Dict[str, float] = {}
        self._base_id = 0
        self.rebase()

    # ---- time
    @property
    def ms(self) -> List[Machine]:
        return [self.host, self.client]

    def now(self) -> float:
        return self.clock.now()

    def elapsed(self) -> float:
        return self.clock.now() - self.t0

    def sleep(self, s: float) -> None:
        self.tick()
        self.clock.sleep(s)

    def p(self, key: str) -> float:
        """A tunable number (--param room.key=value); the defaults are PARAMS in lab_rooms.py, merged into ctx.params by the runner."""
        try:
            return float(self.params[f"{self.room}.{key}"])
        except KeyError:
            raise Blocked(f"no parameter {self.room}.{key} (lab_rooms.PARAMS)", "TEST") from None

    def tick(self) -> None:
        if self.clock.now() > self.deadline and MUTANT != "no_time_box":
            raise TimeBox(f"room time box of {self.budget_s:.0f} s used up")
        if self.probe:
            dead = self.probe()
            if dead:
                raise Crashed(list(dead))

    # ---- counters and digests
    def _flat(self) -> Dict[str, float]:
        flat: Dict[str, float] = {}
        for m in self.ms:
            try:
                C.flatten(m.status(), m.name + ":", flat)
            except Exception:   # noqa: BLE001 - a copy that cannot answer is reported by the check that needs it
                pass
        return {k.replace(":.", ":"): v for k, v in flat.items()}

    def _desyncs(self) -> Dict[str, Any]:
        try:
            return (self.host.status() or {}).get("desyncs") or {}
        except Exception:   # noqa: BLE001
            return {}

    def rebase(self) -> None:
        """From here on, counters and digest reports are measured against now (the spawn room does this once both copies are in the cell)."""
        self._flat0 = self._flat()
        self._base_id = max([r.get("id", 0) for r in self._desyncs().get("desyncs") or []] or [0])

    def moved(self) -> Dict[str, float]:
        return C.suspicious_moved(C.moved(self._flat0, self._flat()))

    def divergence(self) -> Optional[Dict[str, Any]]:
        ents = new_desync_entries(self._desyncs(), self._base_id)
        return ents[0] if ents else None

    def digests_moved(self) -> Dict[str, int]:
        now = digest_counts({"desyncs": self._desyncs()})
        before = {k: int(self._flat0.get(f"host:desyncs.digests.{k}", 0)) for k in now}
        return {k: now[k] - before[k] for k in now}

    # ---- recording
    def _rec(self, name: str, ok: bool, expected: Any, actual: Any, note: str, cls: str, area: str, secs: float, status: str = "") -> Dict[str, Any]:
        rec = {"name": name, "ok": bool(ok), "expected": short(expected, SHORT_EXPECTED), "actual": short(actual, SHORT_ACTUAL), "secs": round(secs, 2)}
        if note:
            rec["note"] = str(note)[:SHORT_ACTUAL]
        if status:
            rec["status"] = status
        if not ok:
            rec["class"] = cls
            rec["area"] = area or self.area
        self.checks.append(rec)
        return rec

    def check(self, name: str, ok: bool, expected: Any = None, actual: Any = None, note: str = "", cls: str = "SYNC", area: str = "") -> bool:
        """Record one fact. Not ok: the room ends here with the evidence (fail fast), so nothing after it runs."""
        self.tick()
        if MUTANT == "checks_never_fail":
            ok = True
        rec = self._rec(name, ok, expected, actual, note, cls, area, 0.0)
        if not ok:
            self._evidence()
            if MUTANT != "no_fail_fast":
                raise Fail(rec)
            return False
        return True

    def skip(self, name: str, why: str) -> None:
        self._rec(name, True, None, None, why, "", "", 0.0, status="skipped")

    def _evidence(self) -> None:
        self.failure = {"first_divergence": self.divergence(), "counters_moved": self.moved(), "digests_moved": self.digests_moved()}

    def wait(self, name: str, fn: Callable[[], Tuple[bool, Any]], timeout_s: float, expected: Any = None, poll_s: float = 0.25, note: str = "",
             cls: str = "SYNC", area: str = "") -> Tuple[Any, float]:
        """Poll fn() -> (ok, actual) until ok or timeout_s. Returns (actual, seconds). A timeout is the failed check."""
        t0 = self.now()
        end = t0 + timeout_s
        last: Any = None
        while True:
            self.tick()
            ok, last = fn()
            if ok:
                secs = self.now() - t0
                self._rec(name, True, expected, last, note, cls, area, secs)
                return last, secs
            if self.now() >= end:
                break
            self.clock.sleep(poll_s)
        secs = self.now() - t0
        rec = self._rec(name, False, expected, last, (note + " " if note else "") + f"not true after {secs:.1f} s", cls, area, secs)
        self._evidence()
        raise Fail(rec)

    def both(self, fn: Callable[[Machine], Any]) -> Dict[str, Any]:
        return {HOST: fn(self.host), CLIENT: fn(self.client)}

    def par(self, fh: Callable[[], Any], fc: Callable[[], Any]) -> Dict[str, Any]:
        """Run one action per machine at the same moment (real threads in a game; one after the other, in no time, in the fake world)."""
        out: Dict[str, Any] = {}
        if getattr(self.clock, "virtual", False):
            out[HOST], out[CLIENT] = fh(), fc()
            return out
        errs: List[BaseException] = []

        def run(k, f):
            try:
                out[k] = f()
            except BaseException as e:   # noqa: BLE001
                errs.append(e)
        ts = [threading.Thread(target=run, args=(k, f), daemon=True) for k, f in ((HOST, fh), (CLIENT, fc))]
        [t.start() for t in ts]
        [t.join(timeout=max(1.0, self.deadline - self.now() + 5)) for t in ts]
        if errs:
            raise errs[0]
        return out

    def agree(self, name: str, fn: Callable[[Machine], Any], timeout_s: float, expected: Any = None, poll_s: float = 0.25, note: str = "",
              cls: str = "SYNC", area: str = "") -> Any:
        """Wait until fn(host) == fn(client) (and == expected when given). Returns the agreed value; a timeout fails with both values."""
        def probe():
            vals = self.both(fn)
            ok = vals[HOST] == vals[CLIENT] and (expected is None or vals[HOST] == expected)
            return ok, vals
        vals, _ = self.wait(name, probe, timeout_s, expected, poll_s, note, cls, area)
        return vals[HOST]

    # ---- helpers every room uses
    def ref(self, *prefixes: str, n: Optional[int] = None) -> Any:
        """Refs of this room by key prefix; n=None gives the list, n=0 the first one."""
        refs = self.layout.refs(self.room, *prefixes)
        if n is None:
            return refs
        if len(refs) <= n:
            raise Blocked(f"layout room {self.room} has no ref #{n} named {'/'.join(prefixes)}*", "TEST")
        return refs[n]

    def stand_all(self) -> None:
        """Both players to the room's stand marker (the host a little apart from the client)."""
        stand = self.layout.stand(self.room)
        for i, m in enumerate(self.ms):
            ok, why = m.goto_ref(stand, 60.0 * i)
            self.check(f"{m.name} stands at the {self.room} stand marker", ok, "arrived", why or "arrived", cls="ENV", area="tools/lab (teleport)")
        self.clock.sleep(1.0)

    def enable_hostiles(self, only: Optional[List[str]] = None) -> None:
        """The plugin places the room's hostiles disabled (they would charge the players of other rooms). The host enables them now (all of them, or
        `only` these: a room that shoots one hostile at a time enables just that one, so the others do not walk up to the shooter meanwhile), they must
        show on both copies, and they are disabled again when the room ends."""
        refs = list(only) if only is not None and MUTANT != "enable_all_at_once" else self.layout.initially_disabled(self.room)   # twin: all at once
        for r in refs:
            ok, why = self.host.enable(r)
            self.check(f"the host enables {r.split(':')[1]}", ok, "enabled", why or "enabled", cls="TEST", area="tools/lab (enable)")
        if refs:
            self.agree("the enabled hostiles are in the world on both copies", lambda m: [m.enabled(r) for r in refs], 15.0, [True] * len(refs), area="plugin/spawn, plugin/actors")
            self.cleanups.append(lambda: [self.host.disable(r) for r in refs])

    def refs_exist(self, refs: List[str], what: str) -> None:
        """Placed refs are persistent: each one resolves to the SAME id on both copies. Missing on both = the plugin or the layout is wrong (TEST)."""
        ids = {m.name: {r: m.ref_id(r) for r in refs} for m in self.ms}
        gone = [r for r in refs if not ids[HOST][r] and not ids[CLIENT][r]]
        if gone:
            raise Blocked(f"{what}: {gone[:3]} resolve on neither copy (is NVCoopLab.esp loaded? does the layout match the plugin?)", "TEST")
        bad = {r: (ids[HOST][r], ids[CLIENT][r]) for r in refs if not ids[HOST][r] or ids[HOST][r] != ids[CLIENT][r]}
        self.check(f"{what} resolve to the same ids on both copies", not bad, "same id", bad or "same", area="plugin/coop (persistent refs)")


# ------------------------------------------------------------------ running one room
def classify_exception(e: BaseException) -> Tuple[str, str, str]:
    """-> (verdict, class, reason) for something a room did not expect."""
    names = [c.__name__ for c in type(e).__mro__]
    text = str(e)[:400]
    if "ConnectionLost" in names or "GameCrashed" in names:
        return "FAIL", "CRASH", f"lost the game: {text}"
    if "RpcError" in names and getattr(e, "code", None) == -32601:
        return "BLOCKED", "BUILD", f"bridge method missing in this build: {text}"
    if "BridgeError" in names or "WaitTimeout" in names:
        low = text.lower()
        cls = "ENV" if any(w in low for w in ("service", "loading", "timed out", "timeout")) else "TEST"
        return "FAIL", cls, f"bridge: {text}"
    return "FAIL", "TEST", f"room code error {type(e).__name__}: {text}"


def run_room(name: str, fn: Callable[[Ctx], Optional[Dict[str, Any]]], ctx: Ctx) -> Dict[str, Any]:
    """Run one room function and return its result dict (no exception escapes). The clean rule runs last: no digest repair and no area heal."""
    res: Dict[str, Any] = {"room": name, "verdict": "PASS", "class": None, "reason": "", "area": ctx.area}
    t0 = time.time()
    try:
        extra = fn(ctx)
        if isinstance(extra, dict):
            ctx.metrics.update(extra)
        mv = {k: v for k, v in C.moved(ctx._flat0, ctx._flat()).items()}
        bad = [] if MUTANT == "no_clean_rule" else C.unclean(mv)
        ctx.check("no digest repair or area heal during the room", not bad, 0, bad or 0, area="plugin/areadigest, synccore digest")
    except Fail as e:
        res.update(verdict="FAIL", **{"class": e.rec.get("class") or "SYNC"}, reason=_why(e.rec), area=e.rec.get("area") or ctx.area)
    except Blocked as e:
        res.update(verdict="BLOCKED", **{"class": e.cls}, reason=e.reason)
    except TimeBox as e:
        res.update(verdict="TIMEOUT", **{"class": "ENV"}, reason=str(e))
        ctx._evidence()
    except Crashed as e:
        res.update(verdict="FAIL", **{"class": "CRASH"}, reason=str(e))
    except Exception as e:   # noqa: BLE001
        v, c, why = classify_exception(e)
        res.update(verdict=v, **{"class": c}, reason=why)
        if v == "FAIL" and c != "CRASH":
            ctx._rec("room step raised", False, None, why, "", c, ctx.area, 0.0)
        if v == "FAIL":
            ctx._evidence()
    if res["verdict"] != "PASS" and not ctx.failure:
        try:
            ctx._evidence()
        except Exception:   # noqa: BLE001
            pass
    for fn_ in reversed(ctx.cleanups):       # put the lab back for the next room (not judged: a failing cleanup is not a finding)
        try:
            fn_()
        except Exception:   # noqa: BLE001
            pass
    res["seconds"] = round(ctx.elapsed(), 1)
    res["wall_seconds"] = round(time.time() - t0, 1)
    res["checks"] = ctx.checks
    res["metrics"] = ctx.metrics
    if res["verdict"] != "PASS":
        res["first_divergence"] = ctx.failure.get("first_divergence")
        res["counters_moved"] = ctx.failure.get("counters_moved") or {}
        res["digests_moved"] = ctx.failure.get("digests_moved") or {}
    return res


def _why(rec: Dict[str, Any]) -> str:
    return f"{rec.get('name')}: expected {rec.get('expected')!r}, got {rec.get('actual')!r}" + (f" ({rec['note']})" if rec.get("note") else "")
