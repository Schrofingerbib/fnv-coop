"""Bot action layer: a scripted player driven only through nvbridge (never OS keyboard/mouse input).

Every action returns ActionResult(ok, action, reason, elapsed_s, data). With strict=True a failed action raises
ActionFailed instead. Every action has a timeout and checks a postcondition by reading the game back.
"""
from __future__ import annotations

import math
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

_RUNNER = str(Path(__file__).resolve().parent.parent / "runner")
if _RUNNER not in sys.path:
    sys.path.insert(0, _RUNNER)
from nvbridge import BridgeError, ConnectionLost, RpcError, WaitTimeout  # noqa: E402

from . import primitives as P  # noqa: E402

METHOD_NOT_FOUND = -32601
PLAYER_REF = "FalloutNV.esm:14"


class Pos(Tuple[float, float, float]):
    def __new__(cls, x: float, y: float, z: float):
        return super().__new__(cls, (float(x), float(y), float(z)))

    x = property(lambda s: s[0])
    y = property(lambda s: s[1])
    z = property(lambda s: s[2])


def dist(a: Tuple[float, float, float], b: Tuple[float, float, float]) -> float:
    return math.dist(a, b)


def heading_deg(frm: Tuple[float, float, float], to: Tuple[float, float, float]) -> Tuple[float, float]:
    """(angleZ, angleX) in degrees to look from `frm` at `to`. Z = atan2(dx, dy) (clockwise from +Y), X positive looks
    down. UNVERIFIED sign conventions: check once in game."""
    dx, dy, dz = to[0] - frm[0], to[1] - frm[1], to[2] - frm[2]
    z = math.degrees(math.atan2(dx, dy)) % 360.0
    x = -math.degrees(math.atan2(dz, math.hypot(dx, dy)))
    return z, x


def angle_diff(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


@dataclass
class ActionResult:
    ok: bool
    reason: str = ""
    data: Dict[str, Any] = field(default_factory=dict)
    action: str = ""
    elapsed_s: float = 0.0

    def __bool__(self) -> bool:
        return self.ok

    def __str__(self) -> str:
        return f"{self.action}: {'ok' if self.ok else 'FAILED ' + self.reason} ({self.elapsed_s:.2f}s) {self.data or ''}"


class ActionFailed(Exception):
    def __init__(self, result: ActionResult):
        super().__init__(str(result))
        self.result = result


class _Fail(Exception):
    def __init__(self, reason: str, **data: Any):
        super().__init__(reason)
        self.reason = reason
        self.data = data


class RealClock:
    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, s: float) -> None:
        time.sleep(s)


Ref = Union[str, int]
Target = Union[str, int, Tuple[float, float, float]]


class Bot:
    def __init__(self, nv, name: str = "bot", strict: bool = False, speed: float = 220.0, step_s: float = 0.15,
                 clock=None, slot: int = 0):
        self.slot = slot
        self.nv = nv
        self.name = name
        self.strict = strict
        self.speed = speed          # units per second while walking
        self.step_s = step_s        # seconds between position steps
        self.clock = clock or getattr(nv, "clock", None) or RealClock()
        self._plugins: Optional[List[str]] = None
        self._input: Optional[bool] = None
        self.log: List[ActionResult] = []

    # ------------------------------------------------------------------ plumbing
    def _act(self, action: str, fn: Callable[[], Dict[str, Any]]) -> ActionResult:
        t0 = self.clock.monotonic()
        try:
            data = fn() or {}
            res = ActionResult(True, "", data, action)
        except _Fail as f:
            res = ActionResult(False, f.reason, f.data, action)
        except WaitTimeout as e:
            res = ActionResult(False, "timeout", {"detail": str(e)}, action)
        except RpcError as e:
            res = ActionResult(False, f"rpc_error:{e.message}", {"code": e.code, "method": e.method}, action)
        except ConnectionLost:
            raise  # the game is gone: not an action failure, let the playbook see it (GameCrashed is a subclass)
        except BridgeError as e:
            res = ActionResult(False, f"bridge_error:{e}", {}, action)
        res.elapsed_s = self.clock.monotonic() - t0
        self.log.append(res)
        if self.strict and not res.ok:
            raise ActionFailed(res)
        return res

    def _plugin_list(self) -> List[str]:
        if self._plugins is None:
            self._plugins = list(self.nv.plugins())
        return self._plugins

    def _norm(self, ref: Optional[Ref]) -> Tuple[str, str, str]:
        """-> (info form 'plugin:HEX', console prefix, runtime hex id). None means the player."""
        if ref is None or ref == "player":
            return PLAYER_REF, "player", "00000014"
        plugins = self._plugin_list()
        if isinstance(ref, int) or (isinstance(ref, str) and ":" not in ref):
            rid = ref if isinstance(ref, int) else int(ref, 16)
            idx = rid >> 24
            if idx >= len(plugins):
                raise _Fail("not_found", ref=ref, why="load index not in plugin list")
            return f"{plugins[idx]}:{rid & 0xFFFFFF:X}", f"{rid:08X}", f"{rid:08X}"
        name, local = ref.rsplit(":", 1)
        low = [p.lower() for p in plugins]
        if name.lower() not in low:
            raise _Fail("not_found", ref=ref, why="plugin not loaded")
        rid = (low.index(name.lower()) << 24) | int(local, 16)
        return f"{plugins[low.index(name.lower())]}:{int(local, 16):X}", f"{rid:08X}", f"{rid:08X}"

    def _item(self, item: Ref) -> str:
        return self._norm(item)[2]

    def _info(self, ref: Optional[Ref]) -> Dict[str, Any]:
        info_ref = self._norm(ref)[0]
        try:
            return self.nv.call(P.M_REF_INFO, {"ref": info_ref})
        except RpcError as e:
            if e.code == METHOD_NOT_FOUND:
                raise
            raise _Fail("not_found", ref=info_ref, why=e.message)

    def _eval(self, expr: str) -> float:
        return self.nv.eval(expr)

    def _optional_call(self, method: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """Call a dev-only bridge method; None if the plugin does not have it."""
        try:
            return self.nv.call(method, params or {})
        except RpcError as e:
            if e.code == METHOD_NOT_FOUND:
                return None
            raise

    def _exec(self, cmd: str, what: str = "") -> str:
        r = self.nv.exec(cmd)
        if not r.get("ok", False):
            raise _Fail("unsupported", cmd=cmd, output=r.get("output", ""), what=what)
        return r.get("output", "")

    def _poll(self, pred: Callable[[], bool], timeout_s: float, poll_s: float = 0.1) -> bool:
        end = self.clock.monotonic() + timeout_s
        while True:
            if pred():
                return True
            if self.clock.monotonic() >= end:
                return False
            self.clock.sleep(poll_s)

    # ------------------------------------------------------------------ queries
    def pos(self, ref: Optional[Ref] = None) -> Pos:
        i = self._info(ref)
        return Pos(i["x"], i["y"], i["z"])

    def cell(self, ref: Optional[Ref] = None) -> int:
        return int(self._info(ref)["cell"])

    def alive(self, ref: Optional[Ref] = None) -> bool:
        return int(self._info(ref)["lifeState"]) == 0

    def health(self, ref: Optional[Ref] = None) -> float:
        return float(self.nv.call(P.M_QUERY, {"ref": self._norm(ref)[0], "fn": "GetActorValue", "p1": P.HEALTH_AV})["value"])

    def inventory_count(self, item: Ref, holder: Optional[Ref] = None) -> int:
        h = self._norm(holder)[1]
        return int(self._eval(P.item_count(h, self._item(item))))

    def quest_stage(self, quest: Ref) -> int:
        return int(self._eval(P.get_stage(self._item(quest))))

    def dialogue_open(self) -> bool:
        return bool(self.nv.call(P.M_DIALOG_OPEN).get("open"))

    def door_state(self, ref: Ref) -> str:
        return P.OPEN_STATES.get(int(self._eval(P.open_state(self._norm(ref)[1]))), "none")

    def locked(self, ref: Ref) -> bool:
        return self._eval(P.locked(self._norm(ref)[1])) != 0

    def target_pos(self, target: Target) -> Pos:
        if isinstance(target, tuple):
            return Pos(*target)
        return self.pos(target)

    def snapshot(self, path: str, sections: Optional[List[str]] = None) -> ActionResult:
        return self._act("snapshot", lambda: dict(self.nv.state_to_file(path, sections), path=path))

    # ------------------------------------------------------------------ movement
    def _set_pos(self, p: Tuple[float, float, float]) -> None:
        for axis, v in zip("XYZ", p):
            self._exec(P.setpos(axis, v), "setpos")

    def _face(self, frm, to) -> None:
        z, _ = heading_deg(frm, to)
        self._exec(P.setangle("Z", z), "setangle")

    def has_input_driver(self) -> bool:
        """True if the game has the in-process input driver (plugin/bots, ini [dev] bots=1). Probed once."""
        if self._input is None:
            try:
                self._input = bool(self.nv.call(P.M_BOT_STATUS).get("hooked"))
            except RpcError as e:
                if e.code != METHOD_NOT_FOUND and "disabled" not in e.message:
                    raise
                self._input = False
        return self._input

    def goto(self, target: Target, radius: float = 100.0, timeout_s: float = 30.0, mode: str = "walk") -> ActionResult:
        """Move to a ref/coordinates. Post: within `radius` of the target in the same cell.
        mode 'walk'     real locomotion through the input driver when present (hold Forward+Run, steer with bot.look, stuck
                        detection), else stepped SetPos; 'input' same but fails with 'unsupported' if there is no driver;
        mode 'step'     stepped SetPos at `speed` (no animation, but works without the driver);
        mode 'teleport' MoveTo / SetPos. Walking across cells is refused (use 'teleport' or a door)."""
        def run():
            if mode not in ("walk", "input", "step", "teleport"):
                raise _Fail("bad_mode", mode=mode)
            dest = self.target_pos(target)
            is_ref = not isinstance(target, tuple)
            if is_ref and self.cell(target) != self.cell():
                if mode != "teleport":
                    raise _Fail("other_cell", mine=self.cell(), target=self.cell(target))
            if mode == "teleport":
                if is_ref:
                    self._exec(P.moveto(self._norm(target)[2]), "moveto")
                else:
                    self._set_pos(dest)
                self.clock.sleep(self.step_s)
                tol = max(radius, 250.0) if is_ref else radius
                d = dist(self.pos(), self.target_pos(target))
                if d > tol:
                    raise _Fail("postcondition", distance=d, wanted=tol)
                return {"distance": d}
            if mode == "input" and not self.has_input_driver():
                raise _Fail("unsupported", need="bot.move (ini [dev] bots=1)")
            use_input = mode == "input" or (mode == "walk" and self.has_input_driver())
            data = self._walk_input(target, radius, timeout_s) if use_input else self._walk_step(target, radius, timeout_s)
            if is_ref and self.cell(target) != self.cell():
                raise _Fail("postcondition", why="different cell on arrival")
            return data
        return self._act("goto", run)

    def _walk_step(self, target: Target, radius: float, timeout_s: float) -> Dict[str, Any]:
        end = self.clock.monotonic() + timeout_s
        stuck = 0
        while True:
            cur = self.pos()
            dest = self.target_pos(target)  # moving targets are tracked
            d = dist(cur, dest)
            if d <= radius:
                return {"distance": d, "via": "step"}
            if self.clock.monotonic() >= end:
                raise _Fail("timeout", distance=d)
            step = min(self.speed * self.step_s, d - radius * 0.5)
            f = step / d
            nxt = (cur[0] + (dest[0] - cur[0]) * f, cur[1] + (dest[1] - cur[1]) * f, cur[2] + (dest[2] - cur[2]) * f)
            self._face(cur, dest)
            self._set_pos(nxt)
            self.clock.sleep(self.step_s)
            if dist(self.pos(), cur) < 1.0:
                stuck += 1
                if stuck >= 4:
                    raise _Fail("stuck", at=tuple(cur), distance=d)
            else:
                stuck = 0

    def _walk_input(self, target: Target, radius: float, timeout_s: float, turn_dps: float = 360.0) -> Dict[str, Any]:
        """Steer with bot.look (turn-rate limited) and hold Forward+Run through the input driver. Stuck detection: if the
        player gained < 25% of the expected progress over a window, try a sidestep, then a jump, then give up."""
        end = self.clock.monotonic() + timeout_s
        window_s, tries = 1.0, 0
        w_start, w_pos = self.clock.monotonic(), self.pos()
        yaw = self._eval(P.get_angle("Z")) % 360.0
        try:
            while True:
                cur = self.pos()
                dest = self.target_pos(target)
                d = dist(cur, dest)
                if d <= radius:
                    return {"distance": d, "via": "input", "unstick": tries}
                if self.clock.monotonic() >= end:
                    raise _Fail("timeout", distance=d, unstick=tries)
                want, _ = heading_deg(cur, dest)
                delta = (want - yaw + 180.0) % 360.0 - 180.0
                maxturn = turn_dps * self.step_s
                yaw = (yaw + max(-maxturn, min(maxturn, delta))) % 360.0
                self.nv.call(P.M_BOT_LOOK, {"yaw": yaw, "pitch": 0})
                self.nv.call(P.M_BOT_MOVE, {"forward": 1, "strafe": 0, "run": True})
                self.clock.sleep(self.step_s)
                if self.clock.monotonic() - w_start >= window_s:
                    moved = dist(self.pos(), w_pos)
                    if moved < 0.25 * 200.0 * window_s and abs(delta) < 45.0:
                        tries += 1
                        if tries > 4:
                            raise _Fail("stuck", at=tuple(cur), distance=d, unstick=tries)
                        if tries % 2:  # sidestep one way, later the other
                            side = 1 if (tries // 2) % 2 == 0 else -1
                            self.nv.call(P.M_BOT_MOVE, {"forward": 1, "strafe": side, "run": True})
                            self.clock.sleep(0.6)
                        else:
                            self.nv.call(P.M_BOT_JUMP)
                            self.clock.sleep(0.5)
                    w_start, w_pos = self.clock.monotonic(), self.pos()
        finally:
            try:
                self.nv.call(P.M_BOT_STOP)
            except RpcError:
                pass

    def walk_path(self, points: List[Tuple[float, float, float]], radius: float = 120.0, timeout_s: float = 60.0) -> ActionResult:
        """Path follower: walk waypoint to waypoint (each via goto, so the input driver when present). The last point uses
        `radius`; earlier points use twice that so the avatar does not stop and turn on every corner."""
        def run():
            end = self.clock.monotonic() + timeout_s
            for i, p in enumerate(points):
                last = i == len(points) - 1
                r = self.goto(tuple(p), radius=radius if last else radius * 2, timeout_s=max(1.0, end - self.clock.monotonic()))
                if not r.ok:
                    raise _Fail(r.reason, waypoint=i, **r.data)
            return {"waypoints": len(points)}
        return self._act("walk_path", run)

    # ---- direct controls through the input driver (all need plugin/bots) ----
    def _need_driver(self) -> None:
        if not self.has_input_driver():
            raise _Fail("unsupported", need="bot.* input driver (ini [dev] bots=1)")

    def look(self, yaw: Optional[float] = None, pitch: float = 0.0, ref: Optional[Ref] = None) -> ActionResult:
        """Turn the view. With `ref`, face it (yaw and pitch); else absolute yaw/pitch in degrees. Post: the angle reads back."""
        def run():
            self._need_driver()
            p: Dict[str, Any] = {"ref": self._norm(ref)[0]} if ref is not None else {"yaw": yaw or 0.0, "pitch": pitch}
            r = dict(self.nv.call(P.M_BOT_LOOK, p))
            gz = self._eval(P.get_angle("Z"))
            if angle_diff(gz, r["yaw"]) > 3.0:
                raise _Fail("postcondition", wanted=r["yaw"], got=gz)
            return r
        return self._act("look", run)

    def stop(self) -> ActionResult:
        """Release every forced control. Post: bot.status reports nothing held."""
        def run():
            self._need_driver()
            self.nv.call(P.M_BOT_STOP)
            held = self.nv.call(P.M_BOT_STATUS).get("held", [])
            if held:
                raise _Fail("postcondition", held=held)
            return {}
        return self._act("stop", run)

    def jump(self) -> ActionResult:
        """Tap Jump. Post: the player's Z rises by more than 5 units within 1.5 s."""
        def run():
            self._need_driver()
            z0 = self.pos().z
            self.nv.call(P.M_BOT_JUMP)
            if not self._poll(lambda: self.pos().z > z0 + 5.0, 1.5, 0.05):
                raise _Fail("postcondition", why="player never left the ground", z=z0)
            return {}
        return self._act("jump", run)

    def sneak_toggle(self) -> ActionResult:
        """Tap Crouch (a toggle). No reliable crouch-state read exists yet, so the result carries verified=False."""
        def run():
            self._need_driver()
            self.nv.call(P.M_BOT_SNEAK)
            self.clock.sleep(0.3)
            return {"verified": False}
        return self._act("sneak_toggle", run)

    def activate_crosshair(self) -> ActionResult:
        """Press Activate on whatever the crosshair is on (aim with look first). Engine key path, like a real key press."""
        def run():
            self._need_driver()
            self.nv.call(P.M_BOT_ACTIVATE)
            self.clock.sleep(0.3)
            return {"verified": False}
        return self._act("activate_crosshair", run)

    def follow(self, target: Ref, keep: float = 150.0, duration_s: float = 10.0) -> ActionResult:
        """Keep within `keep` units of a moving ref for duration_s. Post: ends within keep*1.5 of it."""
        def run():
            end = self.clock.monotonic() + duration_s
            while self.clock.monotonic() < end:
                d = dist(self.pos(), self.pos(target))
                if d > keep:
                    r = self.goto(target, radius=keep * 0.8, timeout_s=max(1.0, end - self.clock.monotonic()))
                    if not r.ok and r.reason != "timeout":
                        raise _Fail(r.reason, **r.data)
                else:
                    self.clock.sleep(self.step_s)
            d = dist(self.pos(), self.pos(target))
            if d > keep * 1.5:
                raise _Fail("postcondition", distance=d, keep=keep)
            return {"distance": d}
        return self._act("follow", run)

    def wait(self, seconds: float) -> ActionResult:
        def run():
            self.clock.sleep(seconds)
            return {"waited": seconds}
        return self._act("wait", run)

    def wait_until(self, fn: Callable[[], bool], timeout_s: float = 10.0, poll_s: float = 0.1) -> ActionResult:
        def run():
            if not self._poll(fn, timeout_s, poll_s):
                raise _Fail("timeout")
            return {}
        return self._act("wait_until", run)

    # ------------------------------------------------------------------ combat
    def equip(self, item: Ref, timeout_s: float = 5.0) -> ActionResult:
        def run():
            h = self._item(item)
            if self.inventory_count(item) < 1:
                raise _Fail("not_in_inventory", item=h)
            self._exec(P.equip(h), "equip")
            if not self._poll(lambda: self._eval(P.is_equipped(h)) != 0, timeout_s):
                raise _Fail("postcondition", why="item not equipped", item=h)
            return {"item": h}
        return self._act("equip", run)

    def unequip(self, item: Ref, timeout_s: float = 5.0) -> ActionResult:
        def run():
            h = self._item(item)
            self._exec(P.unequip(h), "unequip")
            if not self._poll(lambda: self._eval(P.is_equipped(h)) == 0, timeout_s):
                raise _Fail("postcondition", why="item still equipped", item=h)
            return {"item": h}
        return self._act("unequip", run)

    def aim_at(self, ref: Ref, tolerance_deg: float = 3.0, eye_height: float = 120.0, target_height: float = 60.0) -> ActionResult:
        """Set view angles toward `ref`. Post: the player's angles read back within tolerance."""
        def run():
            me = self.pos()
            tp = self.pos(ref)
            z, x = heading_deg((me[0], me[1], me[2] + eye_height), (tp[0], tp[1], tp[2] + target_height))
            self._exec(P.setangle("Z", z), "setangle")
            self._exec(P.setangle("X", x), "setangle")
            gz = self._eval(P.get_angle("Z"))
            gx = self._eval(P.get_angle("X"))
            if angle_diff(gz, z) > tolerance_deg or angle_diff(gx, x) > tolerance_deg:
                raise _Fail("postcondition", wanted=(z, x), got=(gz, gx))
            return {"z": z, "x": x}
        return self._act("aim_at", run)

    def fire(self, ref: Optional[Ref] = None, shots: int = 1, expect_hit: bool = False) -> ActionResult:
        """Fire the equipped weapon. With `ref`: aim first and refuse a dead target. expect_hit=True requires the
        target's health to drop. Without bridge method bot.fire this falls back to a console line that cannot report ammo."""
        def run():
            before = None
            if ref is not None:
                if not self.alive(ref):
                    raise _Fail("dead", ref=str(ref))
                a = self.aim_at(ref)
                if not a.ok:
                    raise _Fail(a.reason, **a.data)
                before = self.health(ref)
            r = self._optional_call(P.M_BOT_FIRE, {"shots": shots, **({"ref": self._norm(ref)[0]} if ref is not None else {})})
            data: Dict[str, Any] = {}
            if r is None:
                for _ in range(shots):
                    self._exec(P.fire_console(), "fire")
                    self.clock.sleep(self.step_s)
                data["verified_shots"] = False
            else:
                n = int(r.get("fired", r.get("queued", 0)))
                if n < shots:
                    raise _Fail("postcondition", why="fewer shots fired", fired=n, wanted=shots)
                if "queued" in r:  # input driver: the pulses are played out over the next frames
                    if not self._poll(lambda: int(self.nv.call(P.M_BOT_STATUS).get("shotsLeft", 0)) == 0, shots * 0.5 + 3.0):
                        raise _Fail("timeout", why="shots still queued")
                    self.clock.sleep(0.3)
                data["fired"] = n
            if expect_hit:
                if before is None:
                    raise _Fail("bad_args", why="expect_hit needs ref")
                if not self._poll(lambda: self.health(ref) < before or not self.alive(ref), 3.0):
                    raise _Fail("postcondition", why="target health did not drop", health=before)
                data["health"] = self.health(ref)
            return data
        return self._act("fire", run)

    def reload(self) -> ActionResult:
        def run():
            r = self._optional_call(P.M_BOT_RELOAD)
            if r is None:
                self._exec(P.reload_console(), "reload")
                return {"verified": False}
            if "reloaded" not in r:  # input driver: a key tap, nothing to read back yet
                self.clock.sleep(1.0)
                return {"verified": False}
            if not r.get("reloaded"):
                raise _Fail("postcondition", why="bridge says not reloaded", reply=r)
            return {"ammo": r.get("ammo")}
        return self._act("reload", run)

    def vats_target(self, ref: Ref, limb: Optional[str] = None, expect_hit: bool = False) -> ActionResult:
        """Enter VATS, pick the target, accept, play it out. data['hit'] says whether the target's health dropped;
        expect_hit=True makes a miss a failure (VATS can legitimately miss)."""
        def run():
            if not self.alive(ref):
                raise _Fail("dead", ref=str(ref))
            before = self.health(ref)
            p: Dict[str, Any] = {"ref": self._norm(ref)[0]}
            if limb:
                p["limb"] = limb
            r = self._optional_call(P.M_BOT_VATS, p)
            if r is None:
                raise _Fail("unsupported", need=P.M_BOT_VATS)
            if r.get("started"):   # real driver: a tick-driven job, poll its phase
                st = {}
                def finished() -> bool:
                    st.update(self.nv.call(P.M_BOT_STATUS).get("vats", {}))
                    return st.get("phase") in ("done", "failed")
                if not self._poll(finished, 30.0, 0.25):
                    raise _Fail("timeout", why="VATS job still running", phase=st.get("phase"))
                if st.get("phase") == "failed":
                    raise _Fail("vats_failed", why=st.get("reason", ""))
                hit = self.health(ref) < before or not self.alive(ref)
                if expect_hit and not hit:
                    raise _Fail("postcondition", why="VATS finished but the target was not hurt", health=before)
                return {"phase": "done", "hit": hit}
            if not r.get("queued"):
                raise _Fail("postcondition", why="VATS attack not queued", reply=r)
            return dict(r)
        return self._act("vats_target", run)

    # ---- menu / dialogue / inventory / refs (bridge methods in plugin/bots, see docs/tools/T6a-actions.md) ----
    def _try_call(self, method: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """Like _optional_call, and also None when the plugin has the method but says "unsupported: ..." (engine glue or event handler missing)."""
        try:
            return self.nv.call(method, params or {})
        except RpcError as e:
            if e.code == METHOD_NOT_FOUND or "unsupported" in str(e).lower():
                return None
            raise

    def _need(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        r = self._optional_call(method, params)
        if r is None:
            raise _Fail("unsupported", need=method)
        return r

    def menu_info(self) -> Dict[str, Any]:
        """Current menu: {kind, id, vatsMode, dialogState?, dialogReady?}. kind in none|hud|message|pipboy|dialogue|container|terminal|vats|..."""
        return self._need(P.M_BOT_MENU)

    def dialog_choices(self) -> List[Dict[str, Any]]:
        return self._need(P.M_BOT_DIALOG)["choices"]

    def inventory(self, holder: Optional[Ref] = None, items: Optional[List[Ref]] = None) -> Dict[str, int]:
        """{form: count}. Without `items` it lists entries the engine tracks (reply 'partial'); pass items for exact totals."""
        p: Dict[str, Any] = {}
        if holder is not None:
            p["ref"] = self._norm(holder)[0]
        if items:
            p["items"] = [self._norm(i)[0] for i in items]
        return {i["item"]: i["count"] for i in self._need(P.M_BOT_INVENTORY, p)["items"]}

    def press_box(self, button: int) -> ActionResult:
        """Answer a message box with `button` (0-based, in the order the box lists its buttons). The UI layer's own box (consent prompt,
        PNR confirm) answers first; when it shows none and the engine's own message box (MessageMenu, menu id 1001) is the top menu, the
        plugin presses that box natively. data['path'] is 'ui' or 'native'; a native press also gives data['value'] (what the engine records)."""
        return self._act("press_box", lambda: dict(self._need(P.M_BOT_PRESS, {"button": button})))

    def dismiss_messages(self, max_n: int = 8, button: int = 0, settle_s: float = 0.3) -> ActionResult:
        """Clear stray message boxes, for example the DLC notices the engine shows about 5 s after a fresh save loads (MessageMenu,
        menu id 1001), which keep a test copy in menu mode. While bot.menu reports kind 'message', press `button` with bot.menu.press
        (UI layer's box first, else the engine's box natively), at most `max_n` presses, `settle_s` between them.
        Post: bot.menu no longer reports 'message'. data: {pressed, paths}. No proof calls this on its own (proofs: --dismiss-messages)."""
        def run():
            paths: List[str] = []
            while True:
                kind = self.menu_info().get("kind")
                if kind != "message":
                    return {"pressed": len(paths), "paths": paths}
                if len(paths) >= max_n:
                    raise _Fail("postcondition", why="a message box is still shown", pressed=len(paths), paths=paths)
                r = self._need(P.M_BOT_PRESS, {"button": button})
                paths.append(str(r.get("path", "?")))
                self.clock.sleep(settle_s)
        return self._act("dismiss_messages", run)

    def consent(self, answer: str = "ready") -> ActionResult:
        """Answer the party-consent prompt: 'ready' or 'notyet'."""
        return self._act("consent", lambda: dict(self._need(P.M_BOT_CONSENT, {"answer": answer})))

    def fast_travel(self, marker: Ref, timeout_s: float = 30.0, allow_fallback: bool = True) -> ActionResult:
        """Fast travel to a map marker. Genuine path (bot.fasttravel): the engine's own travel object is queued, the player moves a few frames
        later (cell load included), data['genuine'] is True. A refusal from the engine (combat, busy menu, ...) is a failure with reason
        `refused` and is NOT retried as a teleport. Only when bot.fasttravel does not exist (engine glue off) the MoveTo fallback runs
        (test.fasttravel, data['genuine'] False). Post: the player is within 400 units of the marker."""
        def run():
            m = self._norm(marker)[0]
            try:
                r = self._try_call(P.M_BOT_FASTTRAVEL, {"marker": m})
            except RpcError as e:
                raise _Fail("refused", why=str(e))
            if r is None:
                if not allow_fallback:
                    raise _Fail("unsupported", need=P.M_BOT_FASTTRAVEL)
                r = self._need(P.M_FASTTRAVEL, {"marker": m})
                r = dict(r, genuine=False)
                timeout = 2.0
            else:
                r = dict(r, genuine=True)
                timeout = timeout_s
            if not self._poll(lambda: dist(self.pos(), self.pos(marker)) <= 400.0, timeout, poll_s=0.25):
                raise _Fail("postcondition", distance=dist(self.pos(), self.pos(marker)), genuine=r["genuine"])
            return dict(r)
        return self._act("fast_travel", run)

    def use_terminal(self, ref: Ref, entry: int, timeout_s: float = 5.0) -> ActionResult:
        """Open the terminal (if its menu is not already up) and choose menu entry `entry` (0-based, as listed on screen).
        Post: the bridge confirms the click and names the entry. data['entries'] lists what the menu showed."""
        def run():
            if self.menu_info().get("kind") != "terminal":
                r = self.open(ref, timeout_s)
                if not r.ok:
                    raise _Fail(r.reason, **r.data)
            r = self._need(P.M_TERMINAL, {"ref": self._norm(ref)[0], "entry": entry})
            self.clock.sleep(0.3)
            return dict(r)
        return self._act("use_terminal", run)

    def terminal_used(self, ref: Ref) -> List[Dict[str, Any]]:
        """Entries this bot picked on that terminal ([{index,text}]). The engine keeps no used flag, so humans' clicks are not included."""
        return self._need(P.M_TERMINAL_STATE, {"ref": self._norm(ref)[0]})["used"]

    def refs_of(self, base: Ref, cell: Optional[Ref] = None) -> Optional[List[str]]:
        """dev.refsOf: the enabled refs of a base form in a cell (default: the player's), or None when the plugin cannot list them."""
        p: Dict[str, Any] = {"base": self._norm(base)[0]}
        if cell is not None:
            p["cell"] = self._norm(cell)[0]
        r = self._try_call(P.M_REFS_OF, p)
        return None if r is None else list(r["refs"])

    def count_refs(self, base: Ref, cell: Optional[Ref] = None) -> int:
        """dev.countRefs: enabled refs of a base form in a cell (default: the player's cell)."""
        p: Dict[str, Any] = {"base": self._norm(base)[0]}
        if cell is not None:
            p["cell"] = self._norm(cell)[0]
        return int(self._need(P.M_COUNT_REFS, p)["count"])

    # ------------------------------------------------------------------ interaction
    def _do_activate(self, ref: Ref) -> None:
        info, _, hexid = self._norm(ref)
        try:
            r = self.nv.call(P.M_ACTIVATE, {"ref": info})
            if not r.get("ok", False):
                raise _Fail("activate_failed", reply=r)
        except RpcError as e:
            if e.code != METHOD_NOT_FOUND:
                raise
            self._exec(P.activate_console(hexid), "activate")

    def activate(self, ref: Ref) -> ActionResult:
        """Activate a ref as the player. Post: the engine accepted it. Use open/pickup/talk for state checks."""
        def run():
            self._info(ref)  # not_found if the ref is not loaded
            self._do_activate(ref)
            return {}
        return self._act("activate", run)

    def _kind(self, ref: Ref) -> int:
        return int(self._info(ref)["type"])

    def open(self, ref: Ref, timeout_s: float = 5.0) -> ActionResult:
        """Open a door (post: GetOpenState reads open) or a container/terminal (post: bot.menu reports it)."""
        def run():
            kind = self._kind(ref)
            if kind == P.TYPE_DOOR:
                if self.door_state(ref) == "open":
                    return {"already": True}
                if self.locked(ref):
                    raise _Fail("locked")
                self._do_activate(ref)
                if not self._poll(lambda: self.door_state(ref) == "open", timeout_s):
                    raise _Fail("postcondition", state=self.door_state(ref))
                return {"state": "open"}
            self._do_activate(ref)
            m = self._optional_call(P.M_BOT_MENU)
            if m is None:
                raise _Fail("unsupported", need=P.M_BOT_MENU, why="cannot verify a container/terminal menu opened")
            if not self._poll(lambda: (self._optional_call(P.M_BOT_MENU) or {}).get("kind") in ("container", "terminal"), timeout_s):
                raise _Fail("postcondition", menu=m.get("kind"))
            return {"menu": self._optional_call(P.M_BOT_MENU).get("kind")}
        return self._act("open", run)

    def close(self, ref: Ref, timeout_s: float = 5.0) -> ActionResult:
        def run():
            if self._kind(ref) != P.TYPE_DOOR:
                raise _Fail("unsupported", why="close is only for doors")
            if self.door_state(ref) == "closed":
                return {"already": True}
            self._do_activate(ref)
            if not self._poll(lambda: self.door_state(ref) == "closed", timeout_s):
                raise _Fail("postcondition", state=self.door_state(ref))
            return {"state": "closed"}
        return self._act("close", run)

    def _transfer(self, name: str, src: Optional[Ref], dst: Optional[Ref], item: Ref, count: int, timeout_s: float) -> Dict[str, Any]:
        sh, dh, ih = self._norm(src)[1], self._norm(dst)[1], self._item(item)
        have = int(self._eval(P.item_count(sh, ih)))
        if have < count:
            raise _Fail("not_enough", have=have, wanted=count)
        dst_before = int(self._eval(P.item_count(dh, ih)))
        self._exec(P.transfer(sh, ih, dh, count), name)
        if not self._poll(lambda: int(self._eval(P.item_count(dh, ih))) == dst_before + count, timeout_s):
            raise _Fail("postcondition", why="destination count wrong", before=dst_before, wanted=count,
                        got=int(self._eval(P.item_count(dh, ih))))
        left = int(self._eval(P.item_count(sh, ih)))
        if left != have - count:
            raise _Fail("postcondition", why="source count wrong (item duplicated?)", before=have, left=left)
        return {"moved": count, "left": left}

    def take(self, container: Ref, item: Ref, count: int = 1, timeout_s: float = 5.0) -> ActionResult:
        return self._act("take", lambda: self._transfer("take", container, None, item, count, timeout_s))

    def put(self, container: Ref, item: Ref, count: int = 1, timeout_s: float = 5.0) -> ActionResult:
        return self._act("put", lambda: self._transfer("put", None, container, item, count, timeout_s))

    def pickup(self, ref: Ref, item: Optional[Ref] = None, timeout_s: float = 5.0) -> ActionResult:
        """Activate a world item. Post: the world ref is gone/disabled and, if `item` is given, its count rose by 1."""
        def run():
            before = self.inventory_count(item) if item is not None else None
            self._info(ref)
            self._do_activate(ref)

            def gone() -> bool:
                try:
                    i = self._info(ref)
                except _Fail:
                    return True
                return bool(int(i["flags"]) & P.FLAG_DISABLED)
            if not self._poll(gone, timeout_s):
                raise _Fail("postcondition", why="world ref still present")
            if item is not None and self.inventory_count(item) < before + 1:
                raise _Fail("postcondition", why="inventory did not gain the item", before=before)
            return {}
        return self._act("pickup", run)

    def drop(self, item: Ref, count: int = 1, timeout_s: float = 5.0) -> ActionResult:
        def run():
            h = self._item(item)
            have = self.inventory_count(item)
            if have < count:
                raise _Fail("not_enough", have=have, wanted=count)
            base = self._norm(item)[0]
            log = self._try_call(P.M_BOT_DROPS, {})                # the engine's OnDrop event log (native handler), position before the drop
            before = None if log is not None else self.refs_of(item)
            self._exec(P.drop(h, count), "drop")
            if not self._poll(lambda: self.inventory_count(item) == have - count, timeout_s):
                raise _Fail("postcondition", have=have, now=self.inventory_count(item))
            out: Dict[str, Any] = {"dropped": count, "ref": None, "refs": [], "source": None}
            if log is not None:
                # correlate by base form plus the event's own time window; the event may land a frame after the count changes
                def seen():
                    d = self._try_call(P.M_BOT_DROPS, {"since": log["seq"], "base": base})
                    return d["drops"] if d and d["drops"] else None
                if self._poll(lambda: seen() is not None, 1.0, poll_s=0.05):
                    refs = [d["ref"] for d in seen()]
                    out.update(ref=refs[-1], refs=refs, source="event")
            if out["ref"] is None and before is not None:
                after = self.refs_of(item) or []
                new = [r for r in after if r not in before]       # fallback: what appeared in the cell since
                if new:
                    out.update(ref=new[-1], refs=new, source="scan")
            return out
        return self._act("drop", run)

    def talk(self, npc: Ref, timeout_s: float = 5.0) -> ActionResult:
        """Activate an NPC. Post: the dialogue menu is open."""
        def run():
            if not self.alive(npc):
                raise _Fail("dead", ref=str(npc))
            if self.dialogue_open():
                raise _Fail("already_in_dialogue")
            self._do_activate(npc)
            if not self._poll(self.dialogue_open, timeout_s):
                raise _Fail("postcondition", why="dialogue menu did not open")
            return {}
        return self._act("talk", run)

    def choose_topic(self, info_id: Ref, timeout_s: float = 5.0) -> ActionResult:
        """Speak one dialogue line by INFO form id. Needs bridge method bot.say (no console primitive exists)."""
        def run():
            if not self.dialogue_open():
                raise _Fail("no_dialogue")
            r = self._optional_call(P.M_BOT_SAY, {"info": self._norm(info_id)[0]})
            if r is None:
                raise _Fail("unsupported", need=P.M_BOT_SAY)
            if not r.get("said"):
                raise _Fail("postcondition", why="line not said (condition false or wrong NPC?)", reply=r)
            return {"info": self._norm(info_id)[0], "speech_check": bool(r.get("speechCheck", False))}
        return self._act("choose_topic", run)

    def end_dialogue(self, timeout_s: float = 5.0) -> ActionResult:
        def run():
            if not self.dialogue_open():
                return {"already": True}
            self.nv.call(P.M_DIALOG_CLOSE)
            if not self._poll(lambda: not self.dialogue_open(), timeout_s):
                raise _Fail("postcondition", why="dialogue still open")
            return {}
        return self._act("end_dialogue", run)

    # ------------------------------------------------------------------ aliases for T6b playbooks
    def goto_ref(self, ref: Ref, dist: float = 150.0, timeout_s: float = 30.0, mode: str = "walk") -> ActionResult:
        return self.goto(ref, radius=dist, timeout_s=timeout_s, mode=mode)

    def goto_pos(self, x: float, y: float, z: float, cell: Optional[int] = None, timeout_s: float = 30.0,
                 mode: str = "walk") -> ActionResult:
        r = self.goto((x, y, z), radius=100.0, timeout_s=timeout_s, mode=mode)
        if r.ok and cell is not None and self.cell() != cell:
            r = ActionResult(False, "postcondition", {"cell": self.cell(), "wanted": cell}, "goto_pos", r.elapsed_s)
            self.log.append(r)
        return r

    def open_door(self, ref: Ref, timeout_s: float = 5.0) -> ActionResult:
        return self.open(ref, timeout_s)

    def close_door(self, ref: Ref, timeout_s: float = 5.0) -> ActionResult:
        return self.close(ref, timeout_s)

    def open_container(self, ref: Ref, timeout_s: float = 5.0) -> ActionResult:
        return self.open(ref, timeout_s)

    def talk_to(self, npc: Ref, timeout_s: float = 5.0) -> ActionResult:
        return self.talk(npc, timeout_s)

    def pick_topic(self, info_id: Ref, timeout_s: float = 5.0) -> ActionResult:
        return self.choose_topic(info_id, timeout_s)

    def target_alive(self, ref: Ref) -> bool:
        return self.alive(ref)

    def is_dead(self, ref: Ref) -> bool:
        return not self.alive(ref)

    def in_dialogue(self) -> bool:
        return self.dialogue_open()

    def stage_done(self, quest: Ref, stage: int) -> bool:
        return bool(self._eval(f"GetStageDone {self._item(quest)} {stage}"))

    def ref_enabled(self, ref: Ref) -> bool:
        return not (int(self._info(ref)["flags"]) & P.FLAG_DISABLED)

    def coop_status(self) -> Dict[str, Any]:
        return self.nv.call("coop.status")
