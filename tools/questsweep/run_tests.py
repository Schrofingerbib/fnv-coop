#!/usr/bin/env python3
"""One command for the questline sweep tests: python -I tools/questsweep/run_tests.py  (prints RESULT: ALL OK)."""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def main() -> int:
    p = subprocess.run([sys.executable, "-I", os.path.join(HERE, "tests", "test_questsweep.py")], capture_output=True, text=True, errors="replace")
    out = p.stdout + p.stderr
    print("\n".join(out.strip().splitlines()[-40:]))
    if p.returncode != 0 or "FAILED" in out:
        print("RESULT: FAILED")
        return 1
    print("RESULT: ALL OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
