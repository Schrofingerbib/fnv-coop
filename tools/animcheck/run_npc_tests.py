"""One command for the NPC fight assertions:  python -I tools/animcheck/run_npc_tests.py   -> prints ALL OK

1. the NPC suite (tests/test_npc*.py: the assertions and the in-game proof script's offline half) must pass on the real code;
2. every mutation twin (ANIMCHECK_MUTANT=<name>, one deliberate bug each) must make that suite FAIL. The npc_* twins break npc.py itself; the others
   are the shared detector / counter twins, which the fight assertions must also notice (a disabled detector would let a glitch through a fight).
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent

MUTANTS = ("npc_attack_match", "npc_extra", "npc_weapon_never", "npc_corpse_err", "npc_corpse_unsettled", "npc_proof_pass",
           "sliding", "tpose", "floating", "jitter", "rubberband", "stuck", "deathpose", "counters")


def run(mutant: str = "") -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop("ANIMCHECK_MUTANT", None)
    if mutant:
        env["ANIMCHECK_MUTANT"] = mutant
    return subprocess.run([sys.executable, "-I", str(HERE / "tests" / "run_npc_suite.py")], cwd=ROOT, capture_output=True, text=True, env=env, errors="replace")


def main() -> int:
    t0 = time.time()
    r = run()
    out = r.stdout + r.stderr
    if r.returncode != 0:
        print(out[-3000:])
        print("FAIL: the NPC suite on the real code")
        return 1
    print("npc suite passes (" + [l for l in out.splitlines() if l.startswith("Ran ")][0] + ")")
    survivors = [m for m in MUTANTS if run(m).returncode == 0]
    print(f"mutation twins: {len(MUTANTS)} run, {len(survivors)} survived{(': ' + ', '.join(survivors)) if survivors else ''}")
    ok = not survivors
    print(f"animcheck npc: {'ALL OK' if ok else 'FAILED'} ({time.time() - t0:.0f}s)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
