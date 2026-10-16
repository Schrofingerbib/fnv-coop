#!/usr/bin/env python3
"""Two real game instances at once (own instance root fnv-instances-l5, multiInstance=1 from provisioning). Needs the game lease.
Launches BOTH from this long-lived process (run.py `up` as a short-lived child lost its game processes right after returning),
pings both pipes, prints the multiInstance log lines, then quits both through the bridge."""
import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
ROOT = REPO.parent / "fnv-instances-l5"
os.environ["FNV_INSTANCES_ROOT"] = str(ROOT)
sys.path.insert(0, str(REPO / "tools" / "runner"))
import nvbridge  # noqa: E402
import run as runner  # noqa: E402
import winproc  # noqa: E402


def main():
    ok = True
    pids = {}
    try:
        for i in (0, 1):
            pids[i] = runner.launch_instance(i)
            print("launched instance", i, "pid", pids[i], flush=True)
            time.sleep(12 if i == 0 else 0)
        conns = {}
        for i in (0, 1):
            try:
                nv = nvbridge.NvBridge.connect(i, wait_s=240)
                p = nv.ping()
                print("instance", i, "ping:", json.dumps({k: p[k] for k in ("pong", "pid", "instance", "frames", "menuReady")}), flush=True)
                ok &= bool(p.get("pong"))
                conns[i] = nv
            except Exception as e:  # noqa: BLE001
                print("instance", i, "NO PING:", e, flush=True)
                ok = False
        time.sleep(8)
        alive = winproc.list_pids("FalloutNV.exe")
        print("FalloutNV.exe alive 8 s after both pings:", alive, flush=True)
        ok &= len(alive) >= 2
        for i, nv in conns.items():
            try:
                p = nv.ping()
                print("instance", i, "still pinging, frames", p["frames"], "menuReady", p["menuReady"], flush=True)
            except Exception as e:  # noqa: BLE001
                print("instance", i, "lost:", e)
                ok = False
        for i in (0, 1):
            log = ROOT / str(i) / "Data" / "NVSE" / "Plugins" / "nvcoop.log"
            if log.exists():
                lines = [l for l in log.read_text(errors="replace").splitlines() if "NEUTRALIZED" in l or "mutex renamed" in l]
                print("--- inst%d:" % i, *lines[-3:], sep="\n")
    finally:
        for i, nv in list(locals().get("conns", {}).items()):
            try:
                nv.quit()
            except Exception:
                pass
        time.sleep(6)
        for pid in winproc.list_pids("FalloutNV.exe"):
            if pid in pids.values():
                winproc.kill_pid(pid)
    print("RESULT:", "BOTH ALIVE AND ANSWERING" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
