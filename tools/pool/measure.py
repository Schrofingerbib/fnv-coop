"""Measurement script for the lead: how much RAM and CPU does a warm instance cost? (Numbers are UNKNOWN until this is run.)

    python -I tools/pool/measure.py --label lowcost --minutes 5 [--interval 5]

Run it while the pool is up (`pool up`) and idle, then again after re-provisioning without the low-cost profile
(`pool up --no-lowcost`, label "normal"). It samples every game pid the pool owns plus total system memory, and writes
out/T4a-pool/measure-<label>.json and prints a summary: per-instance working set (MB), CPU percent of one core,
and available system RAM. Compare the two labels to fill the estimate table in docs/tools/T4a-pool.md.
It only reads process counters; it launches nothing and touches no INI.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "tools" / "runner"))
import client  # noqa: E402
import winproc  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--label", required=True)
    ap.add_argument("--minutes", type=float, default=5)
    ap.add_argument("--interval", type=float, default=5)
    a = ap.parse_args()
    pids = {i["id"]: i["pid"] for i in client.status()["instances"] if i["pid"]}
    if not pids:
        print("no live pool instances; run `pool up` first", file=sys.stderr)
        return 2
    samples = []
    last = {i: winproc.proc_stats(p) for i, p in pids.items()}
    t_last = time.time()
    end = t_last + a.minutes * 60
    while time.time() < end:
        time.sleep(a.interval)
        now = time.time()
        row = {"t": round(now - (end - a.minutes * 60), 1), "sys": winproc.system_memory(), "inst": {}}
        for i, p in pids.items():
            cur = winproc.proc_stats(p)
            if cur and last.get(i):
                row["inst"][str(i)] = {"ws_mb": cur["ws_mb"],
                                       "cpu_pct": round(100 * (cur["cpu_s"] - last[i]["cpu_s"]) / (now - t_last), 1)}
            last[i] = cur
        t_last = now
        samples.append(row)
    summary = {}
    for i in pids:
        ws = [s["inst"][str(i)]["ws_mb"] for s in samples if str(i) in s["inst"]]
        cpu = [s["inst"][str(i)]["cpu_pct"] for s in samples if str(i) in s["inst"]]
        if ws:
            summary[str(i)] = {"ws_mb_min": min(ws), "ws_mb_max": max(ws), "cpu_pct_avg": round(sum(cpu) / len(cpu), 1),
                               "cpu_pct_max": max(cpu)}
    out = {"label": a.label, "pids": pids, "summary": summary,
           "sys_avail_gb_min": min(s["sys"]["avail_gb"] for s in samples) if samples else None, "samples": samples}
    dest = REPO / "out" / "T4a-pool"
    dest.mkdir(parents=True, exist_ok=True)
    (dest / f"measure-{a.label}.json").write_text(json.dumps(out, indent=1))
    print(json.dumps({k: out[k] for k in ("label", "summary", "sys_avail_gb_min")}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
