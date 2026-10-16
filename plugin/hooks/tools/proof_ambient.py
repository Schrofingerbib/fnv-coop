#!/usr/bin/env python3
"""Live: how much ambient script activity does the host capture now? Idle for N seconds after the autosave loads with capture on, then report
droppedNoScope (target ~0), weak txns, effect-type histogram, script executions and the frame-time cost estimate. Needs the lease."""
import importlib.util
import json
import sys
import time
from collections import Counter
from pathlib import Path

spec = importlib.util.spec_from_file_location("ps", Path(__file__).resolve().parent / "proof_single.py")
ps = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ps)
SECONDS = 30


def main():
    nv, pid = ps.launch("host")
    try:
        f0 = nv.ping()["frames"]
        nv.call("capture.begin")
        t0 = time.time()
        time.sleep(SECONDS)
        cap = nv.call("capture.end")
        dt = time.time() - t0
        frames = nv.ping()["frames"] - f0
        hist = Counter(e["type"] for t in cap["txns"] for e in t["effects"])
        causes = Counter((t["cause"]["kind"], t["cause"]["source"]) for t in cap["txns"])
        print("seconds %.1f frames %d (%.1f fps)" % (dt, frames, frames / dt))
        print("stats:", json.dumps(cap["stats"]))
        print("scripts:", json.dumps(cap["scripts"]), " per frame: %.1f" % (cap["scripts"]["ran"] / max(frames, 1)))
        print("effects:", dict(hist))
        print("top causes:", causes.most_common(8))
        ok = cap["stats"]["droppedNoScope"] == 0
        print("RESULT:", "droppedNoScope == 0" if ok else "droppedNoScope = %d" % cap["stats"]["droppedNoScope"])
        return 0 if ok else 1
    finally:
        ps.stop(nv, pid)


if __name__ == "__main__":
    sys.exit(main())
