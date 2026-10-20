"""Poll anim.probe on one or more game instances at a fixed rate and keep the replies as JSONL streams.

    from animcheck.collect import Collector
    with Collector.from_instances({"host": 0, "client": 1}, out_dir="out/run1") as c:
        c.run(seconds=10, hz=20, params={"near": {"radius": 3000}, "max": 8})
    streams = c.streams()

Any object with `.call(method, params)` works as a bridge (tests use a fake). The collector never sends input to the
game; it only calls anim.probe / anim.stats, which need nvcoop.ini [dev] animprobe=1 in each instance.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .model import Stream, stream_from_probes


class Collector:
    def __init__(self, bridges: Dict[str, Any], out_dir: Optional[str | Path] = None, clock=time.monotonic):
        self.bridges = bridges
        self.out_dir = Path(out_dir) if out_dir else None
        self.clock = clock
        self.probes: Dict[str, List[dict]] = {m: [] for m in bridges}
        self.errors: Dict[str, int] = {m: 0 for m in bridges}
        self.last_error: Dict[str, str] = {}
        self._files: Dict[str, Any] = {}
        if self.out_dir:
            self.out_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_instances(cls, instances: Dict[str, int], out_dir=None, wait_s: float = 10.0) -> "Collector":
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "runner"))
        from nvbridge import NvBridge  # type: ignore
        return cls({m: NvBridge.connect(instance=i, wait_s=wait_s) for m, i in instances.items()}, out_dir)

    def __enter__(self) -> "Collector":
        return self

    def __exit__(self, *a) -> None:
        self.close()

    def close(self) -> None:
        for f in self._files.values():
            f.close()
        self._files.clear()
        for b in self.bridges.values():
            close = getattr(b, "close", None)
            if close:
                try:
                    close()
                except Exception:
                    pass

    def poll_once(self, params: Optional[dict] = None) -> int:
        n = 0
        for m, b in self.bridges.items():
            t0 = self.clock()
            try:
                r = b.call("anim.probe", params or {"near": {"radius": 3000}})
            except Exception as e:  # keep sampling the other machines
                self.errors[m] += 1
                self.last_error[m] = repr(e)
                continue
            t1 = self.clock()
            if not isinstance(r, dict):
                self.errors[m] += 1
                continue
            r = dict(r)
            r["machine"] = m
            r["rx_ms"] = round((t0 + t1) / 2.0 * 1000.0, 3)   # midpoint of the request: the reply describes that instant
            self.probes[m].append(r)
            self._write(m, r)
            n += 1
        return n

    def _write(self, m: str, r: dict) -> None:
        if not self.out_dir:
            return
        f = self._files.get(m)
        if f is None:
            f = open(self.out_dir / f"{m}.probe.jsonl", "a", encoding="utf-8")
            self._files[m] = f
        f.write(json.dumps(r, separators=(",", ":")) + "\n")
        f.flush()

    def run(self, seconds: float, hz: float = 20.0, params: Optional[dict] = None, sleep=time.sleep) -> None:
        period = 1.0 / hz
        end = self.clock() + seconds
        nxt = self.clock()
        while self.clock() < end:
            self.poll_once(params)
            nxt += period
            d = nxt - self.clock()
            if d > 0:
                sleep(d)
            else:
                nxt = self.clock()

    def streams(self) -> Dict[str, Stream]:
        return {m: stream_from_probes(m, p, use_rx=True) for m, p in self.probes.items()}

    def costs(self) -> Dict[str, dict]:
        """anim.stats of every machine (probe cost); {} for machines that fail."""
        out = {}
        for m, b in self.bridges.items():
            try:
                out[m] = b.call("anim.stats", {})
            except Exception:
                pass
        return out
