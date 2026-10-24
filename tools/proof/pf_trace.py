"""Runs a python script with a ring buffer of its last bridge calls, so a crash report can say what the game was asked last.

    python -I tools/proof/pf_trace.py TRACE.json script.py [args ...]

It patches NvBridge.call (tools/runner/nvbridge.py) to keep the last 20 calls in TRACE.json, then runs the script as __main__. The exit code
of the script is passed through. A script that is not python, or a fake run, is started without this wrapper.
"""
from __future__ import annotations

import collections
import json
import os
import runpy
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
RUNNER = os.path.join(os.path.dirname(os.path.dirname(HERE)), "tools", "runner")


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    trace, script, rest = sys.argv[1], sys.argv[2], sys.argv[3:]
    ring = collections.deque(maxlen=20)
    sys.path.insert(0, RUNNER)
    try:
        import nvbridge
        orig = nvbridge.NvBridge.call

        def flush() -> None:
            try:
                with open(trace, "w", encoding="utf-8") as f:
                    json.dump(list(ring), f)
            except OSError:
                pass

        def call(self, method, params=None, *a, **k):
            rec = {"t": round(time.time(), 2), "inst": str(getattr(self, "name", ""))[-3:], "method": method}
            ring.append(rec)
            flush()
            try:
                return orig(self, method, params, *a, **k)
            except Exception as e:                      # noqa: BLE001
                rec["error"] = f"{type(e).__name__}: {str(e)[:80]}"
                flush()
                raise

        nvbridge.NvBridge.call = call
    except Exception:                                   # noqa: BLE001  (no bridge module: run the script untraced)
        pass
    sys.argv = [script] + rest
    try:
        runpy.run_path(script, run_name="__main__")
    except SystemExit as e:
        c = e.code
        return c if isinstance(c, int) else (0 if c is None else 1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
