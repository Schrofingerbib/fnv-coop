"""Clocks for the playbook engine: how long a wait may take.

Why this exists (Track A inbox, 2026-10-21): the playbooks waited for convergence with `time.monotonic()` deadlines of 0.2 to 0.3 s on the
fake world. On a busy PC (builds, other game copies) the process simply did not get enough CPU inside that window, so six clean playbooks
(fight, loot_race, companion_recruit, fast_travel_tether, ownership_handoff, vats_hit) failed and a rerun passed. Nothing was desynced; the
test measured the machine, not the sync.

Two fixes, one per kind of run:
  * SimClock (fake worlds): time is the world's own virtual clock `world.vt`, which only advances when the world is ticked. A deadline in
    virtual seconds is the same on a quiet PC and on a PC with 100% load, so a wait fails if and only if the world never converges inside
    that many virtual seconds. A floor (SIM_MIN_WAIT_S) keeps a budget written for real time ("0.3 s") from meaning "3 ticks".
  * WallClock (live games): real time, but every budget is multiplied by LoadMeter.factor(), the same idea as synccore's budgetScale():
    a fixed calibration workload is timed right now and compared with its time on a quiet machine. Only waiting budgets are scaled; no
    check is ever loosened, and a desync that never heals still fails (it just takes the scaled time to be declared).

ManualClock exists for the tests: its sleep() moves its own time, so a "slow machine" can be simulated without waiting.
"""
from __future__ import annotations

import time
from typing import Callable, Optional

SIM_MIN_WAIT_S = 5.0        # virtual seconds: smallest wait budget in a fake world
LOAD_CAP = 8.0              # never scale a live budget by more than this
LOAD_REF_US = 3000.0        # calibration workload time on a quiet PC: a guess below the fastest run seen while 8 agents worked on this PC
                            # (5.1 ms); too low only makes live budgets a bit longer. Re-measure: `python -I tools/bots/playbooks/clocks.py`


class Clock:
    kind = "wall"

    def now(self) -> float:
        raise NotImplementedError

    def sleep(self, seconds: float) -> None:
        raise NotImplementedError


class WallClock(Clock):
    kind = "wall"

    def now(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)


class SimClock(Clock):
    """The fake world's virtual time (`world.vt`, 0.05 s per tick). sleep() does nothing: polling a bot ticks the world."""
    kind = "sim"

    def __init__(self, world) -> None:
        self.world = world

    def now(self) -> float:
        return float(self.world.vt)

    def sleep(self, seconds: float) -> None:
        return None


class ManualClock(Clock):
    """Test clock: time moves only through sleep() (and advance())."""
    kind = "wall"

    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += max(0.0, seconds)

    def advance(self, seconds: float) -> None:
        self.t += seconds


def calibrate_us() -> float:
    """Median time in microseconds of a fixed, library-independent workload (dict of strings plus hashing), best of 9."""
    runs = []
    for _ in range(9):
        t0 = time.perf_counter()
        m = {}
        for i in range(3000):
            k = "plugin.esm:" + str(i * 7919)
            m[k] = m.get(k, 0) + i
        h = 1469598103934665603
        for k, v in m.items():
            for c in k:
                h = ((h ^ ord(c)) * 1099511628211) & 0xFFFFFFFFFFFFFFFF
            h ^= v
        runs.append((time.perf_counter() - t0) * 1e6)
    runs.sort()
    return runs[len(runs) // 2]


class LoadMeter:
    """How much slower than a quiet PC is this process right now? 1.0 = not slower. Cached for `ttl_s` seconds."""

    def __init__(self, calibrate: Callable[[], float] = calibrate_us, ref_us: float = LOAD_REF_US, cap: float = LOAD_CAP,
                 ttl_s: float = 2.0, clock: Optional[Clock] = None) -> None:
        self.calibrate, self.ref_us, self.cap, self.ttl_s = calibrate, ref_us, cap, ttl_s
        self._clock = clock or WallClock()
        self._at = -1e18
        self._value = 1.0

    def factor(self) -> float:
        t = self._clock.now()
        if t - self._at >= self.ttl_s:
            self._value = min(self.cap, max(1.0, self.calibrate() / self.ref_us))
            self._at = t
        return self._value


def make_clock(machines, opts: dict) -> Clock:
    """opts['clock']: a Clock object, 'wall', 'sim', or 'auto' (default): the fake world's clock when the bots belong to one and the
    run is a fast fake run, else wall time."""
    c = opts.get("clock", "auto")
    if isinstance(c, Clock):
        return c
    if c == "wall":
        return WallClock()
    world = getattr(machines[0].bot, "world", None) if machines else None
    has_vt = world is not None and hasattr(world, "vt")
    if c == "sim":
        if not has_vt:
            raise ValueError("clock 'sim' needs bots that belong to a fake world with a virtual clock (world.vt)")
        return SimClock(world)
    if c != "auto":
        raise ValueError(f"unknown clock {c!r}")
    return SimClock(world) if (has_vt and opts.get("fast")) else WallClock()


if __name__ == "__main__":
    xs = sorted(calibrate_us() for _ in range(50))
    print(f"calibration workload: min {xs[0]:.0f} us, median {xs[len(xs) // 2]:.0f} us, max {xs[-1]:.0f} us (LOAD_REF_US = {LOAD_REF_US:.0f})")
