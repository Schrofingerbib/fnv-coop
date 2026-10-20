#!/usr/bin/env python3
"""One command for the T6b playbook tests: python -I tools/bots/playbooks/tests/run_tests.py  (prints ALL OK)."""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def main() -> int:
    p = subprocess.run([sys.executable, "-I", os.path.join(HERE, "test_playbooks.py")], capture_output=True, text=True,
                       errors="replace")
    out = p.stdout + p.stderr
    tail = out.strip().splitlines()[-25:]
    print("\n".join(tail))
    if p.returncode != 0 or "FAILED" in out:
        print("RESULT: FAILED")
        return 1
    print("RESULT: ALL OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
