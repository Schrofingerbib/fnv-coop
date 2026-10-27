"""The host copy on another Windows machine, reached over ssh: start it, forward its bridge pipe, stop it.

    python -I tools/bench/coop_bench.py --scenario standard --remote dino --remote-ip 192.168.0.10

What it needs on the other machine (set up once, see docs/tools/bench.md): the repo at C:\\fnv\\fnv-coop, the game copies provisioned there, Python, and
an ssh login that works without a password (a key). Nothing here simulates input on that machine's desktop: the game window is started in the logged-in
user's session through a scheduled task (a game window needs a desktop; an ssh session has none), and the task is removed right away.

The host's bridge pipe cannot cross the network, so tools/runner/pipe_relay.py forwards it to a TCP port on that machine and an ssh tunnel brings the
port here; NVCOOP_REMOTE_0 then makes every NvBridge.connect(instance=0) use it.
"""
import os
import subprocess
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
REMOTE_ROOT = "C:/fnv/fnv-coop"
FILES = ["tools/runner/pipe_relay.py", "tools/runner/nvbridge.py", "tools/bench/remote/task.ps1", "tools/bench/remote/host_up.ps1",
         "tools/bench/remote/relay_up.ps1", "tools/bench/remote/down.ps1"]


class RemoteHost:
    def __init__(self, ssh="dino", ip="192.168.0.10", bridge_port=27971, log=print, run=None, popen=None):
        self.ssh_name, self.ip, self.port, self.log = ssh, ip, bridge_port, log
        self._run = run or (lambda cmd, timeout: subprocess.run(cmd, capture_output=True, text=True, timeout=timeout))
        self._popen = popen or subprocess.Popen
        self.tunnel = None

    # ---- ssh helpers
    def sh(self, command, timeout=120):
        return self._run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", self.ssh_name, command], timeout)

    def ps(self, script, args="", timeout=120):
        return self.sh(f'powershell -NoProfile -ExecutionPolicy Bypass -File {REMOTE_ROOT}/tools/bench/remote/{script} {args}'.strip(), timeout)

    def reachable(self):
        try:
            r = self.sh("echo ok", 20)
            return r.returncode == 0 and "ok" in (r.stdout or "")
        except Exception:   # noqa: BLE001
            return False

    def push(self):
        """Copy the few files the host side needs (the rest of the repo was reference once)."""
        self.sh(r"if not exist C:\fnv\fnv-coop\tools\bench\remote mkdir C:\fnv\fnv-coop\tools\bench\remote", 30)
        for f in FILES:
            r = self._run(["scp", "-o", "BatchMode=yes", str(ROOT / f), f"{self.ssh_name}:{REMOTE_ROOT}/{f}"], 60)
            if r.returncode != 0:
                raise RuntimeError(f"could not copy {f} to {self.ssh_name}: {(r.stderr or '')[:120]}")

    # ---- lifecycle
    def down(self):
        if self.tunnel is not None and self.tunnel.poll() is None:
            self.tunnel.terminate()
        self.tunnel = None
        self.ps("down.ps1", timeout=90)

    def up(self, boot_wait_s=240):
        """Start the host copy in the desktop session, wait for its bridge pipe, start the forwarder and the tunnel. Sets NVCOOP_REMOTE_0."""
        if not self.reachable():
            raise RuntimeError(f"cannot reach {self.ssh_name} over ssh without a password (key login)")
        self.push()
        self.down()
        self.ps("task.ps1", "-Script C:/fnv/fnv-coop/tools/bench/remote/host_up.ps1 -Wait 5", timeout=60)
        end, out = time.time() + boot_wait_s, ""
        while time.time() < end:
            r = self.sh("type C:\\fnv\\up_out.txt", 20)
            out = (r.stdout or "")
            if "pipes:" in out:
                break
            time.sleep(4)
        if "pipes: {0: True}" not in out:
            raise RuntimeError(f"the host copy on {self.ssh_name} did not bring up its bridge pipe: {out[-160:]!r}")
        self.ps("task.ps1", "-Script C:/fnv/fnv-coop/tools/bench/remote/relay_up.ps1 -Wait 3", timeout=60)
        self.tunnel = self._popen(["ssh", "-N", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=20",
                                   "-L", f"{self.port}:127.0.0.1:{self.port}", self.ssh_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.environ["NVCOOP_REMOTE_0"] = f"127.0.0.1:{self.port}"
        self.log(f"host copy up on {self.ssh_name} ({self.ip}); its bridge is forwarded to 127.0.0.1:{self.port}")

    def stop(self):
        os.environ.pop("NVCOOP_REMOTE_0", None)
        self.down()
