#!/usr/bin/env python3
"""Convergence check for N snapshots (host vs clients): every other snapshot's `shared` section must
equal the reference's (tolerance-aware).

  converge.py host=host.json c1=client1.json [c2=client2.json ...] [--ref host]
              [--require-items REF,REF] [--min-quests N] [--tol glob=abs]

Extra owner-decision checks (docs/MASTER-PLAN.md): with perPlayer data present,
  * every player's karma AV must equal shared.karma (karma is shared),
  * --require-items: every player's inventory must contain each listed FormRef (quest items to everyone).
Exit: 0 all converged | 1 divergence | 2 invalid/vacuous input.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import snaplib  # noqa: E402


def check(named, ref=None, tolerances=None, require_items=(), min_quests=snaplib.DEFAULT_MIN_QUESTS,
          min_globals=snaplib.DEFAULT_MIN_GLOBALS, sections=("shared",)):
    """named: ordered list of (name, snapshot). Returns (exit, report_lines)."""
    if len(named) < 2:
        return 2, ["need at least 2 snapshots"]
    lines, problems = [], []
    for n, s in named:
        problems += ["%s: %s" % (n, x) for x in snaplib.guard(s, sections, min_quests, min_globals)]
    if problems:
        return 2, ["INVALID SNAPSHOT (refusing to compare):"] + ["  " + p for p in problems]
    names = [n for n, _ in named]
    ref = ref or names[0]
    if ref not in names:
        return 2, ["unknown reference %r" % ref]
    snaps = dict(named)
    bad = 0
    tol = list(tolerances or [])
    for n in names:
        if n == ref:
            continue
        d = snaplib.diff(snaps[ref], snaps[n], sections, False, tol)
        if d:
            bad += 1
            lines.append("DIVERGED %s vs %s: %d difference(s)" % (n, ref, len(d)))
            lines.append(snaplib.render(d))
        else:
            lines.append("converged %s == %s" % (n, ref))
    karma = snaps[ref].get("shared", {}).get("karma")
    for n in names:
        for pid, pdata in (snaps[n].get("perPlayer") or {}).items():
            k = (pdata.get("actorValues") or {}).get("Karma", {}).get("current")
            if karma is not None and k is not None and abs(k - karma) > 1e-3:
                bad += 1
                lines.append("KARMA NOT SHARED: %s/%s karma %s != shared %s" % (n, pid, k, karma))
            for item in require_items:
                if item not in (pdata.get("inventory") or {}):
                    bad += 1
                    lines.append("MISSING ITEM: %s/%s lacks %s" % (n, pid, item))
    if not bad:
        lines.append("ALL CONVERGED (%d snapshots, ref=%s)" % (len(names), ref))
    return (1 if bad else 0), lines


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("snapshots", nargs="+", help="name=path (path alone uses the file stem as name)")
    ap.add_argument("--ref")
    ap.add_argument("--tol", action="append", default=[])
    ap.add_argument("--require-items", default="")
    ap.add_argument("--min-quests", type=int, default=snaplib.DEFAULT_MIN_QUESTS)
    ap.add_argument("--min-globals", type=int, default=snaplib.DEFAULT_MIN_GLOBALS)
    snaplib.setup_console()
    args = ap.parse_args(argv)
    named = []
    try:
        for spec in args.snapshots:
            name, sep, path = spec.partition("=")
            if not sep:
                name, path = os.path.splitext(os.path.basename(spec))[0], spec
            named.append((name, snaplib.load(path)))
    except snaplib.SnapshotError as e:
        print("INVALID: %s" % e)
        return 2
    code, lines = check(named, args.ref, snaplib.parse_tols(args.tol),
                        [x for x in args.require_items.split(",") if x], args.min_quests, args.min_globals)
    print("\n".join(lines))
    return code


if __name__ == "__main__":
    sys.exit(main())
