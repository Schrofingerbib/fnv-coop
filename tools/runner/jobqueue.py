"""File-lock-based instance leasing so concurrent agents never share a game instance (L2).

One lock file per instance: <root>/locks/inst-<i>.lock. The OS lock (msvcrt.locking on byte 0) is
released automatically when the holder dies, so there are no stale locks. The holder writes a JSON
description after byte 16 (readable by others; the locked region is only byte 0).

    with lease(n=2, job="vms01") as inst:   # inst == [0, 1]; blocks until two free instances exist
        ...
"""
from __future__ import annotations

import contextlib
import json
import os
import random
import time
from pathlib import Path
from typing import Iterator, List, Optional

import msvcrt

import instances


class LeaseTimeout(Exception):
    pass


def lock_dir(root: Optional[Path] = None) -> Path:
    d = Path(root or instances.instances_root()) / "locks"
    d.mkdir(parents=True, exist_ok=True)
    return d


class _Lock:
    def __init__(self, path: Path):
        self.path = path
        self.f = None

    def try_acquire(self, info: dict) -> bool:
        f = open(self.path, "a+b")
        try:
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            f.close()
            return False
        self.f = f
        f.seek(16)
        f.truncate(16)
        f.write(json.dumps(info).encode())
        f.flush()
        return True

    def release(self) -> None:
        if self.f is None:
            return
        try:
            self.f.seek(0)
            msvcrt.locking(self.f.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            self.f.close()
            self.f = None


def holder(i: int, root: Optional[Path] = None) -> Optional[dict]:
    """Describe the current holder of instance i, or None if it is free."""
    p = lock_dir(root) / f"inst-{i}.lock"
    if not p.exists():
        return None
    lk = _Lock(p)
    if lk.try_acquire({"probe": True}):
        lk.release()
        return None
    try:
        with open(p, "rb") as f:
            f.seek(16)  # byte 0 is mandatory-locked by the holder; read past it
            data = f.read()
        return json.loads(data.decode()) if data else {"unknown": True}
    except (OSError, ValueError):
        return {"unknown": True}


def acquire(n: int = 1, *, pool: int = 2, job: str = "", timeout_s: float = 600.0,
            root: Optional[Path] = None, poll_s: float = 0.25) -> List[_Lock]:
    """All-or-nothing acquisition of n instances out of range(pool). Back off with jitter on contention."""
    if n > pool:
        raise ValueError(f"asked for {n} instances but pool is {pool}")
    deadline = time.monotonic() + timeout_s
    ld = lock_dir(root)
    while True:
        got: List[_Lock] = []
        for i in range(pool):
            lk = _Lock(ld / f"inst-{i}.lock")
            if lk.try_acquire({"job": job, "pid": os.getpid(), "instance": i, "since": time.time()}):
                got.append(lk)
                if len(got) == n:
                    return got
            # not free: keep what we have only until the end of this pass
        for lk in got:
            lk.release()
        if time.monotonic() >= deadline:
            raise LeaseTimeout(f"could not lease {n} of {pool} instances within {timeout_s}s")
        time.sleep(poll_s + random.random() * poll_s)


def instance_ids(locks: List[_Lock]) -> List[int]:
    return [int(l.path.stem.split("-")[1]) for l in locks]


@contextlib.contextmanager
def lease(n: int = 1, *, pool: int = 2, job: str = "", timeout_s: float = 600.0,
          root: Optional[Path] = None) -> Iterator[List[int]]:
    locks = acquire(n, pool=pool, job=job, timeout_s=timeout_s, root=root)
    try:
        yield instance_ids(locks)
    finally:
        for lk in locks:
            lk.release()
