"""Collision-free pipe instance ids for tests: random per-process base, skips ids whose pipe already exists
(other test processes, L1's runs, the real game's nvcoop-0)."""
import os
import random
import threading

_lock = threading.Lock()
_next = [(os.getpid() * 7919 + random.randrange(1 << 20)) % 1_500_000_000 + 100_000]


def unique_instance() -> int:
    with _lock:
        while True:
            _next[0] += 1
            if not os.path.exists(rf"\.\pipe\nvcoop-{_next[0]}"):
                return _next[0]
