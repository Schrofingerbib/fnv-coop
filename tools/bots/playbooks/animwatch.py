"""T7 animation and pose assertions for playbooks.

While a playbook that sets `anim = True` runs, every machine is sampled with `anim.probe` (tools/animcheck Collector). At the
end an extra step "animation and pose check" runs animcheck.analyze with the players' avatars (the real character on its own
machine against its puppet on the others) and asserts the T7 counters (docs/tools/T7-anim.md):
    avatar.compared > 0, avatar.anim_mismatches == 0, avatar.glitches == 0, avatar.wrong_weapon/aim/facing == 0, avatar.pose_err_p95 <= 12
    and, when NPCs are in the scene, the NPC equivalents (compared > 0, group_mismatch_episodes == 0, glitches == 0, ...).
Walks the bots do are given to the analyzer as `expect_moving` windows (turns the "stuck" detector on); deliberate teleports are
given as `suppress` entries. Live mode samples from a background thread at 20 Hz; fake worlds sample from their virtual clock.
"""
from __future__ import annotations

import contextlib
import os
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

PLAYER_REF = 0x14
PUPPET_BASE = 0xFF0000      # fake worlds: puppet of slot s is 0xFF0000 + s

TELEPORT_KINDS = ("rubberband", "floating", "sinking")      # NOT sliding, tpose, jitter, stuck, deathpose
AVATAR_SPEC = {"avatar.compared": ">0", "avatar.anim_mismatches": "==0", "avatar.glitches": "==0",
               "avatar.wrong_weapon": "==0", "avatar.wrong_aim": "==0", "avatar.wrong_facing": "==0",
               "avatar.pose_err_p95": "<=12"}
NPC_SPEC = {"compared": ">0", "group_mismatch_episodes": "==0", "glitches": "==0", "wrong_weapon": "==0",
            "wrong_aim": "==0", "wrong_facing": "==0", "pose_err_p95": "<=12"}


def _animcheck():
    tools = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if tools not in sys.path:
        sys.path.insert(0, tools)
    from animcheck import analyze as A      # noqa: WPS433
    from animcheck import collect as C
    from animcheck import counters as K
    return A, C, K


class AnimWatch:
    def __init__(self, ctx, hz: float = 20.0, outdir: Optional[str] = None, npcs: bool = True):
        A, C, K = _animcheck()
        self._A, self._K = A, K
        self.ctx = ctx
        self.npcs = npcs
        self.hz = hz
        world = getattr(ctx.host.bot, "world", None)           # fake worlds expose a virtual clock
        self.world = world
        bridges = {m.name: m.bot.nv for m in ctx.machines}
        clock = (lambda: world.vt) if world is not None else time.monotonic
        self.clock = clock
        self.collector = C.Collector(bridges, outdir, clock=clock)
        self.expect_moving: Dict[int, List[Tuple[float, float]]] = {}
        self.suppress_list: List[dict] = []
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last = -1e9
        self.params = {"near": {"radius": 4000}, "bones": True}

    # ---- sampling ----
    def start(self) -> None:
        if self.world is not None:
            self.world.tick_hooks.append(self._sync_sample)
            return
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        period = 1.0 / self.hz
        while not self._stop.is_set():
            self.collector.poll_once(self.params)
            self._stop.wait(period)

    def _sync_sample(self) -> None:
        if self.world.vt - self._last >= 1.0 / self.hz - 1e-9:
            self._last = self.world.vt
            self.collector.poll_once(self.params)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        for f in list(self.collector._files.values()):      # not collector.close(): that would close the bots' pipes
            f.close()
        self.collector._files.clear()
        if self.world is not None and self._sync_sample in self.world.tick_hooks:
            self.world.tick_hooks.remove(self._sync_sample)

    def now_ms(self) -> float:
        return self.clock() * 1000.0

    # ---- what the playbook tells the analyzer ----
    @contextlib.contextmanager
    def walking(self, owner: str, margin_ms: float = 400.0):
        """Wrap a bot's walk: that player's PUPPET on the other machines must be moving during it (inner window, edges
        trimmed). The analyzer keys windows by ref on every machine, and every machine's own player is ref 0x14, so only the
        puppet refs (unique per slot) can carry a window; the owner's real character is covered by the avatar comparison."""
        t0 = self.now_ms()
        yield
        t1 = self.now_ms()
        if t1 - t0 > 2 * margin_ms + 200:
            for av in self.avatars():
                if av["owner"] == owner:
                    self.expect_moving.setdefault(av["observer_ref"], []).append((t0 + margin_ms, t1 - margin_ms))

    @contextlib.contextmanager
    def teleporting(self, grace_ms: float = 700.0):
        t0 = self.now_ms()
        yield
        t1 = self.now_ms()
        refs = {PLAYER_REF} | {av["observer_ref"] for av in self.avatars()}
        for ref in refs:
            for kind in TELEPORT_KINDS:
                self.suppress(kind, ref, t0 - 100.0, t1 + grace_ms)

    def suppress(self, kind: str, ref: int, t0_ms: float, t1_ms: float) -> None:
        self.suppress_list.append({"kind": kind, "ref": ref, "t0": t0_ms, "t1": t1_ms})

    # ---- evaluation ----
    def avatars(self) -> List[dict]:
        out = []
        ms = self.ctx.machines
        for si, owner in enumerate(ms):
            for oi, obs in enumerate(ms):
                if si == oi:
                    continue
                ref = self._puppet_ref(obs, si)
                if ref:
                    out.append({"name": f"{owner.name}->{obs.name}", "owner": owner.name, "owner_ref": PLAYER_REF,
                                "observer": obs.name, "observer_ref": ref})
        return out

    def _puppet_ref(self, observer, slot: int) -> int:
        if self.world is not None:
            return PUPPET_BASE + slot
        try:        # coop.status playerRefs.slots: {"<slot>": "plugin:<decimal local id>"}
            s = observer.bot.coop_status().get("playerRefs", {}).get("slots", {}).get(str(slot), "")
            return int(s.split(":")[-1]) if s else 0
        except Exception:   # noqa: BLE001
            return 0

    def report(self) -> dict:
        streams = self.collector.streams()
        return self._A.analyze(streams, owner=self.ctx.host.name, expect_moving=self.expect_moving or None,
                               suppress=self.suppress_list, avatars=self.avatars(), label=self.ctx.playbook)

    def failures(self) -> List[str]:
        rep = self.report()
        self.last_report = rep
        spec = dict(AVATAR_SPEC)
        if self.npcs:
            spec.update(NPC_SPEC)
        fails = self._K.check(rep, spec)
        if not self.avatars():
            fails.append("no avatar pairs: the puppet refs of the other players are unknown (coop.status playerRefs)")
        return fails
