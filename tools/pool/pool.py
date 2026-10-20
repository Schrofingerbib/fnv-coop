#!/usr/bin/env python3
"""Warm game pool CLI.

    python -I tools/pool/pool.py up [--size 1] [--fake] [daemon options...]   start the daemon (detached), wait until warm
    python -I tools/pool/pool.py status                                       instances, leases, counters (JSON)
    python -I tools/pool/pool.py lease --owner NAME [--n 1] [--ttl 900] [--timeout 600]
                                                                              prints the lease JSON; the lease lasts until
                                                                              `release` or ttl expiry
    python -I tools/pool/pool.py release --id L3 [--dirty]
    python -I tools/pool/pool.py down                                         stop the daemon, kill only its own games, free the slots

Options after `up` are passed to daemon.py (see its --help). Exit codes: 0 ok, 2 not running / bad use, 3 busy or failed.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import client  # noqa: E402


def cmd_up(rest) -> int:
    try:
        client._info()
        client.status()
        print("pool already running", file=sys.stderr)
        return 2
    except client.PoolError:
        pass
    (client.pool_dir()).mkdir(parents=True, exist_ok=True)
    (client.pool_dir() / "pool.json").unlink(missing_ok=True)
    log = open(client.pool_dir() / "daemon.log", "w")
    flags = 0x00000008 | 0x00000200 | 0x01000000   # DETACHED | NEW_PROCESS_GROUP | BREAKAWAY_FROM_JOB (as run.py does)
    cmd = [sys.executable, "-I", str(HERE / "daemon.py"), *rest]
    try:
        p = subprocess.Popen(cmd, stdout=log, stderr=log, stdin=subprocess.DEVNULL, creationflags=flags, close_fds=True)
    except OSError:
        p = subprocess.Popen(cmd, stdout=log, stderr=log, stdin=subprocess.DEVNULL, creationflags=flags & ~0x01000000, close_fds=True)
    deadline = time.time() + 900
    while time.time() < deadline:
        if (client.pool_dir() / "pool.json").exists():
            print(json.dumps(client.status()["instances"], indent=1))
            return 0
        if p.poll() is not None:
            print("daemon exited early:\n" + (client.pool_dir() / "daemon.log").read_text(), file=sys.stderr)
            return 3
        time.sleep(1)
    print("timed out waiting for the pool to warm up; see runs/pool/daemon.log", file=sys.stderr)
    return 3


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == "up":
        return cmd_up(rest)
    import argparse
    ap = argparse.ArgumentParser(prog="pool " + cmd)
    ap.add_argument("--owner")
    ap.add_argument("--n", type=int, default=1)
    ap.add_argument("--ttl", type=float, default=900)
    ap.add_argument("--timeout", type=float, default=600)
    ap.add_argument("--id")
    ap.add_argument("--dirty", action="store_true")
    a = ap.parse_args(rest)
    try:
        if cmd == "status":
            print(json.dumps(client.status(), indent=1))
        elif cmd == "lease":
            if not a.owner:
                ap.error("--owner required")
            print(json.dumps(client.request("lease", sock_timeout_s=a.timeout + 60, owner=a.owner, n=a.n, ttl_s=a.ttl,
                                            timeout_s=a.timeout)["lease"], indent=1))
        elif cmd == "release":
            if not a.id:
                ap.error("--id required")
            print(json.dumps(client.request("release", id=a.id, dirty=a.dirty)))
        elif cmd == "down":
            try:
                pid = json.loads((client.pool_dir() / "pool.json").read_text()).get("pid")
            except (OSError, ValueError):
                pid = None
            client.request("down")
            for _ in range(120):    # the daemon removes pool.json after it killed its games and freed the slots
                if not (client.pool_dir() / "pool.json").exists():
                    break
                time.sleep(1)
            print("pool down")
        else:
            ap.error("unknown command " + cmd)
    except client.PoolBusy as e:
        print("busy:", e, file=sys.stderr)
        return 3
    except client.PoolNotRunning as e:
        print(e, file=sys.stderr)
        return 2
    except client.PoolError as e:
        print("error:", e, file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
