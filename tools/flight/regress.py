"""Run every generated flight regression fixture in a directory through the replay program.

Usage: python -I tools/flight/regress.py DIR --replay-cmd "build\\L4\\fnvcoop_replay.exe {fixture}"
Prints one line per fixture and ALL OK when every fixture converges (exit 0). A fixture that diverges is a
reproduced bug. A fixture whose "expect" says converges=false is expected to diverge (used by the mutation twin).
"""
import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shrink as S  # noqa: E402


def run_dir(d, oracle):
    bad = 0
    n = 0
    for p in sorted(glob.glob(os.path.join(d, "*.json"))):
        with open(p) as f:
            fx = json.load(f)
        want = (fx.get("expect") or {}).get("converges", True)
        try:
            r = oracle(fx)
        except S.OracleError as e:
            print("ERROR %s: %s" % (os.path.basename(p), e))
            bad += 1
            n += 1
            continue
        got = not r["diverged"]
        ok = got == want
        n += 1
        bad += 0 if ok else 1
        print("%s %s (%s)" % ("PASS" if ok else "FAIL", os.path.basename(p), "converged" if got else "diverged: " + str(S.signature(r))))
    return n, bad


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("--replay-cmd", required=True)
    ap.add_argument("--tmpdir")
    args = ap.parse_args(argv)
    n, bad = run_dir(args.dir, S.CommandOracle(args.replay_cmd, tmpdir=args.tmpdir))
    if n == 0:
        print("no fixtures found in " + args.dir)
        return 2
    print("%d fixture(s), %d failed" % (n, bad))
    if bad == 0:
        print("ALL OK")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
