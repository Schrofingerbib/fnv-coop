"""Pool client (stdlib + tools/runner/nvbridge.py). This is the lease API scenario code uses.

    sys.path.insert(0, "tools/pool"); from client import lease
    with lease("scn-goodsprings", n=2, ttl_s=900, timeout_s=600) as L:
        L.instances            # [0, 1]  lowest id first (host first)
        nv = L.bridge(0)       # NvBridge, already holding the bridge lock.* lease for L.owner
        L.dirty(0, "loaded a foreign save")   # this instance is recycled on release
        L.renew()              # extend the ttl during a long test
    # leaving the block releases the lease (dirty if the block raised)

An instance may be restarted under you (crash, health check). L.bridge() raises PoolError if that happened,
so a test never talks to a fresh game that has lost its state.
"""
from __future__ import annotations

import contextlib
import json
import os
import socket
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools" / "runner"))


class PoolError(Exception):
    pass


class PoolBusy(PoolError):
    pass


class PoolNotRunning(PoolError):
    pass


PoolUnavailable = PoolNotRunning   # name T4b-scenarios codes to


def pool_dir() -> Path:
    return Path(os.environ.get("POOL_DIR") or REPO / "runs" / "pool")


def _info() -> Dict[str, Any]:
    try:
        return json.loads((pool_dir() / "pool.json").read_text())
    except (OSError, ValueError) as e:
        raise PoolNotRunning("pool is not running (no runs/pool/pool.json); start it with `pool up`") from e


def request(op: str, sock_timeout_s: float = 30.0, **kw: Any) -> Dict[str, Any]:
    info = _info()
    try:
        s = socket.create_connection(("127.0.0.1", info["port"]), timeout=5)
    except OSError as e:
        raise PoolNotRunning(f"pool daemon not answering on port {info['port']}: {e}") from e
    with s:
        s.settimeout(sock_timeout_s)
        s.sendall((json.dumps({"op": op, "token": info["token"], **kw}) + "\n").encode())
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(65536)
            if not chunk:
                raise PoolError("pool closed the connection")
            buf += chunk
    rep = json.loads(buf.split(b"\n", 1)[0])
    if not rep.get("ok"):
        raise (PoolBusy if rep.get("kind") == "busy" else PoolError)(rep.get("error", "unknown error"))
    return rep


def status() -> Dict[str, Any]:
    return request("status")


class Lease:
    def __init__(self, data: Dict[str, Any]):
        self.id: str = data["id"]
        self.owner: str = data["owner"]
        self.instances: List[int] = list(data["instances"])
        self.ttl_s: float = data["ttl_s"]
        self._gens = {int(k): v for k, v in data["gens"].items()}
        self._bridges: Dict[int, Any] = {}
        self.released = False
        self._loaded: Dict[int, str] = {}

    def bridge(self, i: Optional[int] = None):
        i = self.instances[0] if i is None else i
        if i not in self.instances:
            raise PoolError(f"instance {i} is not part of lease {self.id} ({self.instances})")
        cur = {x["id"]: x for x in status()["instances"]}[i]
        if cur["gen"] != self._gens[i] or cur["state"] != "leased":
            raise PoolError(f"instance {i} was restarted or lost under lease {self.id} (gen {self._gens[i]} -> {cur['gen']}, {cur['state']})")
        if i not in self._bridges:
            from nvbridge import NvBridge
            nv = NvBridge.connect(i, wait_s=10)
            nv.acquire_lease(self.owner, ttl_ms=int(self.ttl_s * 1000))
            self._bridges[i] = nv
        return self._bridges[i]

    # ---- single-instance conveniences (duck-typed contract with tools/scenarios)
    @property
    def instance(self) -> int:
        return self.instances[0]

    @property
    def nv(self):
        return self.bridge(self.instances[0])

    @property
    def generation(self) -> int:
        return self._gens[self.instances[0]]

    def mark_loaded(self, save: str, i: Optional[int] = None) -> None:
        self._loaded[self.instances[0] if i is None else i] = save

    def needs_reload(self, save: str, i: Optional[int] = None) -> bool:
        """True unless this lease already loaded `save` on that instance (the pool itself tracks nothing between leases)."""
        return self._loaded.get(self.instances[0] if i is None else i) != save

    def dirty(self, i: int, reason: str) -> None:
        request("dirty", id=self.id, instance=i, reason=reason)

    def renew(self, ttl_s: Optional[float] = None) -> bool:
        ok = request("renew", id=self.id, ttl_s=ttl_s)["renewed"]
        for nv in self._bridges.values():
            try:
                nv.renew_lease(int((ttl_s or self.ttl_s) * 1000))
            except Exception:  # noqa: BLE001 - the pool-level lease is the authority
                pass
        return ok

    def release(self, dirty: bool = False, reason: str = "") -> None:
        if self.released:
            return
        self.released = True
        for nv in self._bridges.values():
            try:
                nv.release_lease()
            except Exception:  # noqa: BLE001
                pass
            nv.close()
        request("release", id=self.id, dirty=dirty, reason=reason)


def acquire(owner: str, n: int = 1, ttl_s: float = 900, timeout_s: float = 600) -> Lease:
    return Lease(request("lease", sock_timeout_s=timeout_s + 60, owner=owner, n=n, ttl_s=ttl_s, timeout_s=timeout_s)["lease"])


@contextlib.contextmanager
def lease(owner: str, n: int = 1, ttl_s: float = 900, timeout_s: float = 600, wait_s: Optional[float] = None):
    L = acquire(owner, n, ttl_s, timeout_s if wait_s is None else wait_s)
    try:
        yield L
    except BaseException:
        L.release(dirty=True, reason="exception in lessee")
        raise
    else:
        L.release()
