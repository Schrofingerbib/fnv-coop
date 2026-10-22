"""One command for the scripted pieces in tools/proof/drivers: the unit tests, then the mutation twins. Prints ALL OK when everything holds.

    python -I tools/proof/drivers/tests/run_tests.py

A twin runs the tests with one deliberate bug in a driver (PROOF_DRIVER_MUTANT=<name>); the tests must FAIL, otherwise they would not notice it.
(The tests also hold twins of the other kind: they damage the release scripts or the launcher and require the driver to fail.)
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
PY = [sys.executable, "-I", "-X", "utf8"]

# name -> (what the bug does, the tests that must notice it)
TWINS = {
    "no_dev_inject": ("the split-screen copies start without the synthetic controller hook", ["SplitSmoke.test_happy_path_runs_up_smoke_down_in_that_order_and_releases_the_slot"]),
    "no_down": ("the split-screen copies are left running after the smoke", ["SplitSmoke"]),
    "ignore_up_failure": ("the smoke playbook runs although the copies never came up", ["SplitSmoke"]),
    "no_ready_wait": ("load_scenario jumps before the game's main thread serves requests", ["LoadScenario"]),
    "no_forbidden_scan": ("the export rescan ignores .git, out/ and binaries", ["PublicTree.test_the_rescan_finds_what_must_never_be_published"]),
}


def run(args, env_extra=None, timeout=900):
    env = dict(os.environ)
    env.update(env_extra or {})
    return subprocess.run(PY + args, cwd=ROOT, capture_output=True, text=True, env=env, timeout=timeout)


def main() -> int:
    bad = 0
    cp = run(["tools/proof/drivers/tests/test_drivers.py"])
    tail = (cp.stdout + cp.stderr).strip().splitlines()[-3:]
    print("driver tests:", " | ".join(tail))
    if cp.returncode:
        print((cp.stdout + cp.stderr)[-3000:])
        bad += 1
    for name, (what, tests) in TWINS.items():
        t = run(["tools/proof/drivers/tests/test_drivers.py"] + tests, {"PROOF_DRIVER_MUTANT": name})
        caught = t.returncode != 0
        print(f"twin {name:20s} {'caught' if caught else 'NOT CAUGHT'}   ({what})")
        if not caught:
            bad += 1
    print("RESULT: ALL OK" if not bad else f"RESULT: {bad} problem(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
