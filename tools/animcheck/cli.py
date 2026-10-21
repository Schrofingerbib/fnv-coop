#!/usr/bin/env python3
"""animcheck command line.

  python -I tools/animcheck/cli.py analyze host.jsonl client.jsonl [--html out.html] [--json out.json] [--nvfr out.nvfr]
        [--assert "pose_err_p95<=12" --assert glitches==0] [--pose-tol 12] [--delay-ms 100] [--latency-ms 150]
  python -I tools/animcheck/cli.py collect --instance host=0 --instance client=1 --seconds 10 --hz 20 --out out/run1
  python -I tools/animcheck/cli.py selftest

Exit code 0 = no failed assertions (default assertions: glitches==0, wrong_weapon==0, wrong_facing==0 and
pose_err_p95 <= --pose-tol when there is a pair to compare).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from animcheck.analyze import analyze  # noqa: E402
from animcheck.config import Thresholds  # noqa: E402
from animcheck.counters import check  # noqa: E402
from animcheck.model import load_stream  # noqa: E402


def cmd_analyze(a) -> int:
    streams = {}
    for p in a.streams:
        name, path = (p.split("=", 1) if "=" in p else (None, p))
        st = load_stream(path, machine=name)
        streams[name or st.machine] = st
    th = Thresholds(pose_tol_units=a.pose_tol, interp_delay_ms=a.delay_ms, latency_allow_ms=a.latency_ms)
    avatars = []
    for spec in a.avatar:   # NAME=OWNER_MACHINE:OWNER_REF>OBSERVER_MACHINE:OBSERVER_REF  (refs hex)
        m = re.match(r"^(\w+)=(\w+):(\w+)>(\w+):(\w+)$", spec)
        if not m:
            print(f"bad --avatar {spec!r}", file=sys.stderr)
            return 2
        avatars.append({"name": m.group(1), "owner": m.group(2), "owner_ref": int(m.group(3), 16),
                        "observer": m.group(4), "observer_ref": int(m.group(5), 16)})
    rep = analyze(streams, th, owner=a.owner, auto_offset=a.auto_offset, label=a.label, avatars=avatars)
    spec = {}
    for s in a.assert_:
        m = re.match(r"^([\w.@]+)\s*(<=|>=|==|!=|<|>)\s*(.+)$", s)
        if not m:
            print(f"bad --assert {s!r}", file=sys.stderr)
            return 2
        spec[m.group(1)] = m.group(2) + m.group(3)
    if spec:
        rep["failures"] = check(rep, spec)
        rep["ok"] = not rep["failures"]
    if a.json:
        Path(a.json).write_text(json.dumps(rep, indent=1), encoding="utf-8")
    if a.html:
        from animcheck.report_html import render
        Path(a.html).write_text(render(rep), encoding="utf-8")
    if a.nvfr:
        from animcheck.nvfr_export import write_trace
        write_trace(a.nvfr, rep)
    c = rep["counters"]
    print("animcheck:", " ".join(f"{k}={c[k]}" for k in ("samples", "actors", "pose_err_p50", "pose_err_p95", "group_mismatch_pct",
                                                      "wrong_weapon", "wrong_facing", "glitches")))
    for k in sorted(c):
        if k.startswith("glitch.") and c[k]:
            print(f"  {k} = {c[k]}")
    for f in rep["failures"]:
        print("  FAIL", f)
    print("PASS" if rep["ok"] else "FAIL")
    return 0 if rep["ok"] else 1


def cmd_collect(a) -> int:
    from animcheck.collect import Collector
    inst = {}
    for s in a.instance:
        n, _, i = s.partition("=")
        inst[n] = int(i)
    with Collector.from_instances(inst, out_dir=a.out) as c:
        from animcheck.collect import probe_params
        c.run(a.seconds, a.hz, probe_params(a.radius, a.max))
        print("probes:", {m: len(p) for m, p in c.probes.items()}, "errors:", c.errors)
        print("cost:", json.dumps(c.costs()))
    return 0


def cmd_selftest(a) -> int:
    from animcheck import run_tests
    return run_tests.main()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="animcheck")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("analyze")
    p.add_argument("streams", nargs="+", help="probe JSONL files, optionally name=path; the first is the owner")
    p.add_argument("--owner")
    p.add_argument("--html")
    p.add_argument("--json")
    p.add_argument("--nvfr")
    p.add_argument("--label", default="")
    p.add_argument("--assert", dest="assert_", action="append", default=[])
    p.add_argument("--pose-tol", type=float, default=12.0)
    p.add_argument("--delay-ms", type=float, default=100.0)
    p.add_argument("--latency-ms", type=float, default=150.0)
    p.add_argument("--avatar", action="append", default=[], help="NAME=host:14>client:FF000A, player ref on its machine > puppet ref on the other")
    p.add_argument("--auto-offset", action="store_true", help="estimate the clock offset between streams that use different clocks")
    p.set_defaults(fn=cmd_analyze)
    p = sub.add_parser("collect")
    p.add_argument("--instance", action="append", required=True, help="name=instance, e.g. host=0")
    p.add_argument("--seconds", type=float, default=10)
    p.add_argument("--hz", type=float, default=20)
    p.add_argument("--radius", type=float, default=3000)
    p.add_argument("--max", type=int, default=16)
    p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_collect)
    p = sub.add_parser("selftest")
    p.set_defaults(fn=cmd_selftest)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
