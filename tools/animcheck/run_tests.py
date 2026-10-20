"""One command for the whole animcheck tool:  python -I tools/animcheck/run_tests.py   -> prints ALL OK

1. the unit suite (tests/test_animcheck.py) must pass on the real code;
2. every mutation twin (ANIMCHECK_MUTANT=<name>, one deliberate bug each) must make that suite FAIL;
3. verify_engine.py (static offsets against a reference exe) must pass, and its own twin must fail.
The C++ half (plugin/animprobe) has its own command: plugin\\animprobe\\run_tests.cmd (also ends in ALL OK).
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))

MUTANTS = ("sliding", "tpose", "floating", "jitter", "rubberband", "stuck", "deathpose",
           "group_compare", "weapon_compare", "facing_compare", "pose_compare", "aim_compare", "stance_compare", "counters",
           "verify_skip_all")


def run(script: Path, mutant: str = "") -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop("ANIMCHECK_MUTANT", None)
    if mutant:
        env["ANIMCHECK_MUTANT"] = mutant
    return subprocess.run([sys.executable, "-I", str(script)], cwd=ROOT, capture_output=True, text=True, env=env, errors="replace")


def main() -> int:
    t0 = time.time()
    suite = HERE / "tests" / "run_suite.py"
    bad = 0
    r = run(suite)
    out = r.stdout + r.stderr
    print(out.strip().splitlines()[-1] if out.strip() else "(no output)")
    if r.returncode != 0:
        print(out[-3000:])
        print("FAIL: unit suite on the real code")
        return 1
    print(f"unit suite passes ({[l for l in out.splitlines() if l.startswith('Ran ')][0]})")

    survivors = []
    for m in MUTANTS:
        rm = run(suite, m)
        if rm.returncode == 0:
            survivors.append(m)
    print(f"mutation twins: {len(MUTANTS)} run, {len(survivors)} survived{(': ' + ', '.join(survivors)) if survivors else ''}")
    bad += len(survivors)

    vbad, lines = verify_step(HERE / "verify_engine.py")
    print("\n".join(lines))
    bad += vbad

    print(f"animcheck: {'FAILED' if bad else 'ALL OK'} ({time.time() - t0:.0f}s)")
    return 1 if bad else 0


def verify_step(ve: Path, runner=run):
    """Static engine-offset check (step 3). Returns (failures, report lines).

    verify_engine.py reads a reference FalloutNV.exe and is not in the public repo, so a checkout without it skips the
    step with a reason. A present script must pass and its own twin must fail.
    """
    if os.environ.get("ANIMCHECK_MUTANT") == "verify_skip_all":  # twin: never runs the check
        return 0, ["SKIP verify_engine (mutant)"]
    if not ve.exists():
        return 0, [f"SKIP verify_engine: {ve.name} is not in this checkout (it needs a reference exe, see docs/tools/T7-anim.md)"]
    rv = runner(ve)
    out = rv.stdout.strip()
    lines = [out.splitlines()[-1] if out else "(verify_engine printed nothing)"]
    if rv.returncode != 0:
        return 1, lines + [rv.stdout[-3000:] + rv.stderr[-1000:]]
    if "SKIP" in rv.stdout:
        return 0, lines
    if runner(ve, "verify").returncode == 0:
        return 1, lines + ["SURVIVED: verify_engine twin"]
    return 0, lines + ["verify_engine twin caught"]


if __name__ == "__main__":
    raise SystemExit(main())
