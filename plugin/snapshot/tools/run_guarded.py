#!/usr/bin/env python3
"""Run a command only while the disk has room; WAIT (poll) instead of exiting when it does not.

    python -I plugin/snapshot/tools/run_guarded.py [--min-gb 1.5] [--poll 30] [--max-wait 3600] -- <cmd...>

Honours the PAUSE stop-file (out/L7tmp/PAUSE): while present, nothing is started. Exit code is the command's.
"""
import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
PAUSE = REPO / "out" / "L7tmp" / "PAUSE"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-gb", type=float, default=1.4)
    ap.add_argument("--poll", type=int, default=30)
    ap.add_argument("--max-wait", type=int, default=3600)
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    a = ap.parse_args()
    cmd = a.cmd[1:] if a.cmd and a.cmd[0] == "--" else a.cmd
    t0 = time.time()
    while True:
        free = shutil.disk_usage(str(REPO)).free / 2**30
        if PAUSE.exists():
            why = "PAUSE file present"
        elif free < a.min_gb:
            why = "only %.2f GB free (< %.2f)" % (free, a.min_gb)
        else:
            break
        if time.time() - t0 > a.max_wait:
            print("gave up waiting: " + why)
            return 3
        print("waiting: " + why, flush=True)
        time.sleep(a.poll)
    return subprocess.call(cmd)


if __name__ == "__main__":
    sys.exit(main())
