#!/usr/bin/env python3
"""Semantic diff of two world-state snapshots.

  snapdiff.py A.json B.json [--sections shared[,perPlayer]] [--include-meta]
              [--tol 'glob=abs'] [--ignore 'glob'] [--min-quests N] [--min-globals N] [--json]

Exit: 0 equivalent | 1 semantic difference | 2 invalid or vacuous input (empty/truncated/errored dump).
Key order, whitespace, array order of stage sets, float noise within tolerance, meta.timestamp and
volatile clock globals are ignored. meta and perPlayer are ignored unless asked for.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import snaplib  # noqa: E402


def compare(a, b, sections=("shared",), include_meta=False, tolerances=None, ignore=(),
            min_quests=snaplib.DEFAULT_MIN_QUESTS, min_globals=snaplib.DEFAULT_MIN_GLOBALS, local_split=True):
    """Returns (exitcode, problems, diffs)."""
    problems = []
    for name, s in (("A", a), ("B", b)):
        problems += ["%s: %s" % (name, x) for x in snaplib.guard(s, sections, min_quests, min_globals)]
    if problems:
        return 2, problems, []
    d = snaplib.diff(a, b, sections, include_meta, tolerances, ignore, local_split=local_split)
    return (1 if d else 0), [], d


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--sections", default="shared")
    ap.add_argument("--include-meta", action="store_true")
    ap.add_argument("--tol", action="append", default=[], help="path-glob=abs-tolerance (repeatable)")
    ap.add_argument("--ignore", action="append", default=[], help="path glob to skip (repeatable)")
    ap.add_argument("--min-quests", type=int, default=snaplib.DEFAULT_MIN_QUESTS)
    ap.add_argument("--min-globals", type=int, default=snaplib.DEFAULT_MIN_GLOBALS)
    ap.add_argument("--keep-local-shared", action="store_true",
                    help="do not move GuestLocalRun quests to perPlayer (old behaviour)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--max", type=int, default=50)
    snaplib.setup_console()
    args = ap.parse_args(argv)
    try:
        a, b = snaplib.load(args.a), snaplib.load(args.b)
    except snaplib.SnapshotError as e:
        print("INVALID: %s" % e)
        return 2
    sections = tuple(s for s in args.sections.split(",") if s)
    code, problems, d = compare(a, b, sections, args.include_meta, snaplib.parse_tols(args.tol),
                                args.ignore, args.min_quests, args.min_globals,
                                not args.keep_local_shared)
    if args.json:
        print(json.dumps({"exit": code, "problems": problems, "diffs": d}, default=list, indent=1))
    elif code == 2:
        print("INVALID SNAPSHOT (refusing to compare):")
        for p in problems:
            print("  " + p)
    elif code == 1:
        print("DIFFERENT: %d difference(s) in %s" % (len(d), ",".join(sections)))
        print(snaplib.render(d, args.max))
    else:
        print("EQUAL (%s)" % ",".join(sections))
    return code


if __name__ == "__main__":
    sys.exit(main())
