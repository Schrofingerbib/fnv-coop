"""OS-facing parts of the pool: real (game, bridge pipe, gameslot CLI) and fake (in-memory, for tests).

The real classes never launch anything on import. Only RealBackend.start() starts FalloutNV.exe, and the
pool daemon is the only caller.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

REPO = Path(__file__).resolve().parents[2]
RUNNER = REPO / "tools" / "runner"
GAMESLOT = REPO / "tools" / "gameslot.py"
OWNER = "pool"


# ---------------------------------------------------------------------------------------------- slots
class RealSlots:
    """Holds `n` game slots through tools/gameslot.py so other lanes see the machine as busy."""

    def __init__(self, owner: str = OWNER, ttl_s: int = 3600, wait_s: int = 600):
        self.owner, self.ttl_s, self.wait_s = owner, ttl_s, wait_s

    def _cli(self, *a: str):
        cp = subprocess.run([sys.executable, "-I", str(GAMESLOT), *a], capture_output=True, text=True)
        return cp.returncode, (cp.stdout + cp.stderr).strip()

    def acquire(self, n: int) -> List[int]:
        rc, out = self._cli("acquire", "--owner", self.owner, "--ttl", str(self.ttl_s), "--wait", str(self.wait_s), "--slots", str(n))
        if rc != 0:
            raise RuntimeError(f"game slots unavailable: {out}")
        m = re.search(r"SLOTS ([0-9,]+)", out)
        return [int(x) for x in m.group(1).split(",")] if m else []

    def renew(self, n: int) -> None:
        self._cli("acquire", "--owner", self.owner, "--ttl", str(self.ttl_s), "--wait", "0", "--slots", str(n))

    def register_pid(self, pid: int) -> None:
        self._cli("register-pid", "--owner", self.owner, "--pid", str(pid))

    def release(self) -> None:
        self._cli("release", "--owner", self.owner)


class FakeSlots:
    def __init__(self, available=(0, 1)):
        self.available, self.held, self.pids, self.renewed = list(available), [], [], 0

    def acquire(self, n: int) -> List[int]:
        self.held = self.available[:n]
        return list(self.held)

    def renew(self, n: int) -> None:
        self.renewed += 1

    def register_pid(self, pid: int) -> None:
        self.pids.append(pid)

    def release(self) -> None:
        self.held = []


# ---------------------------------------------------------------------------------------------- backends
class RealBackend:
    def __init__(self, *, boot_timeout_s: float = 300, base_save: str = "", grids: Optional[int] = None,
                 audio_off: bool = False, lowcost: bool = True, mutant: Optional[str] = None):
        os.environ["FNV_LANE"] = OWNER           # run.register_pid() records pids under our gameslot owner
        for p in (str(RUNNER), str(Path(__file__).resolve().parent)):
            if p not in sys.path:
                sys.path.insert(0, p)
        import instances, lowcost as lc, nvbridge, run, winproc  # noqa: E401
        self.instances, self.lc, self.nv, self.run, self.wp = instances, lc, nvbridge, run, winproc
        self.boot_timeout_s, self.base_save, self.grids, self.audio_off, self.lowcost = boot_timeout_s, base_save, grids, audio_off, lowcost
        self.mutant = mutant                      # test twins only (tools/pool/run_tests.py)
        self._mine: set = set()

    def prepare(self, i: int) -> None:
        """Set the instance INIs for this start. Low-cost on: apply the profile. Low-cost off: write the INIs that
        provisioning writes, so a profile applied by an earlier `pool up` does not stay in force."""
        d = self.instances.instance_dir(i)
        if not (d / "instance.json").exists():
            raise RuntimeError(f"instance {i} not provisioned: run `python -I tools/runner/run.py provision --n N`")
        if self.lowcost:
            self.lc.apply(d, self.grids, self.audio_off)
        elif self.mutant != "no_restore":
            self.lc.restore(d)

    def start(self, i: int) -> int:
        pid = self.run.launch_instance(i)
        st = self.run.load_state()
        st["pids"][str(i)] = pid
        self.run.save_state(st)
        self._mine.add(pid)
        return pid

    def alive(self, pid: int) -> bool:
        return bool(pid) and self.wp.pid_alive(pid) and pid in self.wp.list_pids("FalloutNV.exe")

    def ping(self, i: int) -> bool:
        try:
            with self.nv.NvBridge.connect(i, wait_s=0) as b:
                return bool(b.ping().get("pong"))
        except self.nv.BridgeError:
            return False

    def wait_ready(self, i: int, pid: int) -> bool:
        t0 = time.monotonic()
        while time.monotonic() - t0 < self.boot_timeout_s:
            if not self.alive(pid):
                return False
            try:
                with self.nv.NvBridge.connect(i, wait_s=0) as b:
                    if b.ping().get("pong"):
                        if self.base_save:
                            b.load_save(self.base_save)
                        return True
            except self.nv.BridgeError:
                pass
            time.sleep(2)
        return False

    def stats(self, pid: int) -> Dict:
        return self.wp.proc_stats(pid)

    def kill(self, pid: int) -> None:
        """Only a pid this pool recorded, and only while it is still a FalloutNV.exe (guards against pid reuse)."""
        if pid in self._mine or self._recorded(pid):
            if self.alive(pid):
                self.wp.kill_pid(pid)
        self._mine.discard(pid)

    def _recorded(self, pid: int) -> bool:
        return pid in self.run.load_state().get("pids", {}).values()

    def unlock(self, i: int) -> None:
        try:
            with self.nv.NvBridge.connect(i, wait_s=0) as b:
                b.call("lock.release", {"token": "", "force": True}, timeout_s=5)
        except self.nv.BridgeError:
            pass

    def all_game_pids(self) -> List[int]:
        return self.wp.list_pids("FalloutNV.exe")

    def kill_any(self, pid: int) -> None:   # only reachable through the deliberate test mutant
        self.wp.kill_pid(pid)


class FakeBackend:
    """In-memory stand-in. Instances become 'processes' in a set; tests inject deaths and RAM growth."""

    def __init__(self):
        self.procs: Dict[int, int] = {}      # pid -> ws_mb
        self.foreign = {900001, 900002}      # game processes belonging to someone else; must never be killed
        self.mine: set = set()
        self.pid_of: Dict[int, int] = {}
        self.next_pid = 1000
        self.starts: List[int] = []
        self.killed: List[int] = []
        self.unlocked: List[int] = []
        self.prepared: List[int] = []
        self.fail_start = set()              # instance ids whose start fails
        self.unresponsive = set()            # instance ids whose bridge does not answer ping
        self.boot_ws = 1200

    def prepare(self, i: int) -> None:
        self.prepared.append(i)

    def start(self, i: int) -> int:
        self.starts.append(i)
        self.next_pid += 1
        self.procs[self.next_pid] = self.boot_ws
        self.mine.add(self.next_pid)
        self.pid_of[i] = self.next_pid
        return self.next_pid

    def alive(self, pid: int) -> bool:
        return pid in self.procs or pid in self.foreign

    def ping(self, i: int) -> bool:
        return i not in self.unresponsive and self.pid_of.get(i, 0) in self.procs

    def wait_ready(self, i: int, pid: int) -> bool:
        return i not in self.fail_start and pid in self.procs

    def stats(self, pid: int) -> Dict:
        return {"ws_mb": self.procs.get(pid, 0)}

    def kill(self, pid: int) -> None:
        if pid in self.mine:
            self.procs.pop(pid, None)
            self.killed.append(pid)

    def unlock(self, i: int) -> None:
        self.unlocked.append(i)

    def all_game_pids(self) -> List[int]:
        return list(self.procs) + sorted(self.foreign)

    def kill_any(self, pid: int) -> None:
        self.procs.pop(pid, None)
        self.foreign.discard(pid)
        self.killed.append(pid)

    # test levers
    def crash(self, i: int) -> None:
        self.procs.pop(self.pid_of[i], None)

    def grow(self, i: int, ws_mb: int) -> None:
        self.procs[self.pid_of[i]] = ws_mb
