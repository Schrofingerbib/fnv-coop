"""Write an animcheck report into a T1 flight trace (.nvfr) as Marker records, so the flight analyzer's timeline and
HTML report show animation findings next to the network events.

Records written (type = nvfr.T_MARKER, `kind` field per docs/tools/flight-format.md): one kind-4 (test step) with the JSON counters, one kind-1 (desync detected) per glitch event
('animcheck.<kind> actor=0x.. t0_ms=.. t1_ms=.. <detail>'), and one kind-1 per failed default check.
Uses tools/flight/nvfr.py as it is; if that module's writer changes shape this export raises, nothing else breaks.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional


def _nvfr():
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "flight"))
    import nvfr  # type: ignore
    return nvfr


def report_to_records(report: dict, machine: Optional[str] = None) -> list:
    nvfr = _nvfr()
    marker = nvfr.T_MARKER
    recs = [{"type": marker, "mono_us": 0, "frame": 0, "kind": 4,
             "text": "animcheck " + json.dumps(report["counters"], separators=(",", ":"), sort_keys=True)}]
    for name, m in report["machines"].items():
        if machine and name != machine:
            continue
        for e in m["events"]:
            recs.append({"type": marker, "mono_us": int(e["t0_ms"] * 1000), "frame": 0, "kind": 1,
                         "text": f"animcheck.{e['kind']} actor={e['ref']} machine={name} t0_ms={e['t0_ms']} t1_ms={e['t1_ms']} {e['detail']}"})
    for f in report.get("failures", []):
        recs.append({"type": marker, "mono_us": 0, "frame": 0, "kind": 1, "text": f"animcheck failed: {f}"})
    recs.sort(key=lambda r: r["mono_us"])
    return recs


def write_trace(path, report: dict, instance: str = "animcheck", machine: Optional[str] = None) -> int:
    nvfr = _nvfr()
    recs = report_to_records(report, machine)
    nvfr.dump(str(path), {"instance": instance, "reason": 4}, recs)
    return len(recs)
