"""One command for the Python half of the area desync tooling:  python -I tools/areadigest/run_tests.py   -> prints ALL OK

1. the unit suite (tests/test_dashboard.py) must pass on the real code;
2. every mutation twin (AREADIGEST_MUTANT=<name>, one deliberate bug each in dashboard.py) must make that suite FAIL.
The C++ half has its own command: plugin\\areadigest\\run_tests.cmd (it calls this script last and also ends in ALL OK).
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent

MUTANTS = ("disabled_counts", "host_only", "bug_ignored", "permin_is_total", "assert_skips_story", "nvfr_meta_ignored")


def run(mutant: str = "") -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop("AREADIGEST_MUTANT", None)
    if mutant:
        env["AREADIGEST_MUTANT"] = mutant
    return subprocess.run([sys.executable, "-I", str(HERE / "tests" / "test_dashboard.py")], cwd=ROOT, capture_output=True, text=True, env=env,
                          errors="replace")


def main() -> int:
    t0 = time.time()
    r = run()
    tail = (r.stdout + r.stderr).strip().splitlines()
    print("real suite:", tail[-1] if tail else "(no output)")
    bad = 0
    if r.returncode != 0:
        print("\n".join(tail[-30:]))
        bad += 1
    survived = []
    for m in MUTANTS:
        if run(m).returncode == 0:
            survived.append(m)
    if survived:
        print("SURVIVED:", ", ".join(survived))
        bad += 1
    if bad:
        print("FAILED")
        return 1
    print("area desync tooling: real tests pass, all %d twins caught (%.1fs)" % (len(MUTANTS), time.time() - t0))
    print("ALL OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
