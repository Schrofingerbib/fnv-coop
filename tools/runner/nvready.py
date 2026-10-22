"""Readiness wait shared by every script that talks to a freshly booted or still loading game copy.

A copy whose pipe is up is not necessarily ready: while the game boots or loads a save, the bridge pipe answers `ping` (or does not answer at all)
but the main thread does not service requests, so calls fail with "main thread did not service the request (is the game loading?)" or
"no reply within 10s". The right moment to start is when `ping` says menuReady and a main loop that ticks (mainLoopAgeMs >= 0).

    import nvready
    why = nvready.wait_ready(bridges, 240)       # None when every copy is ready, else why not
    if why: print("cannot reach the game instances: " + why); return 2

load_scenario.py, prove.py and the proof scripts (avatars, social, loot, world time, run_fight) use it. Tests: tools/runner/tests/test_nvready.py.
"""
from __future__ import annotations

import os
import time
from typing import Any, Callable, List, Optional

# test-only: one deliberate bug (tools/runner/tests/test_nvready.py)
MUTANT = os.environ.get("NVREADY_MUTANT", "")

READY_WAIT_S = 240.0
POLL_S = 1.0


def is_ready(ping: Any) -> bool:
    """menuReady and a main loop that has ticked (mainLoopAgeMs >= 0): the main thread can serve a request."""
    if MUTANT == "pipe_only":
        return isinstance(ping, dict)
    try:
        return bool(ping.get("menuReady")) and int(ping.get("mainLoopAgeMs", -1)) >= 0
    except (AttributeError, TypeError, ValueError):
        return False


def wait_ready(bridges: List[Any], wait_s: float = READY_WAIT_S, sleep: Callable[[float], None] = time.sleep,
               now: Callable[[], float] = time.monotonic, poll_s: float = POLL_S) -> Optional[str]:
    """None when every bridge's game is ready, else why not (after wait_s, counted from the call for all copies together)."""
    end = now() + wait_s
    last: Any = {}
    for k, nv in enumerate(bridges):
        while True:
            try:
                last = nv.call("ping")
                if is_ready(last):
                    break
            except Exception as e:  # noqa: BLE001 - the pipe answers late (or not at all) while the game boots or loads
                last = {"error": str(e)}
            if now() >= end:
                return f"copy {k} never became ready in {wait_s:.0f} s (last ping {str(last)[:120]})"
            sleep(poll_s)
    return None


def wait_ready_ids(ids: List[int], wait_s: float = READY_WAIT_S, connect: Optional[Callable[[int], Any]] = None, **kw: Any) -> Optional[str]:
    """wait_ready for instance ids: opens a pipe client per copy, waits, closes them again (one pipe client per copy at a time)."""
    if connect is None:
        import nvbridge

        def connect(i: int) -> Any:   # noqa: E306
            return nvbridge.NvBridge.connect(instance=i, wait_s=min(wait_s, 30))
    opened: List[Any] = []
    try:
        for i in ids:
            try:
                opened.append(connect(i))
            except Exception as e:  # noqa: BLE001
                return f"copy {i}: no bridge pipe ({str(e)[:120]})"
        return wait_ready(opened, wait_s, **kw)
    finally:
        for b in opened:
            try:
                b.close()
            except Exception:  # noqa: BLE001
                pass
