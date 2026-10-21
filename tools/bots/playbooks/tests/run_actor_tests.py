#!/usr/bin/env python3
"""One command for the actor-ownership playbook tests: python -I tools/bots/playbooks/tests/run_actor_tests.py  (prints ALL OK)."""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def main() -> int:
    p = subprocess.run([sys.executable, "-I", os.path.join(HERE, "test_actor_playbook.py")], capture_output=True, text=True,
                       errors="replace")
    out = p.stdout + p.stderr
    print("\n".join(out.strip().splitlines()[-25:]))
    ran = re.search(r"Ran (\d+) tests?", out)
    # a runner that found no tests, or skipped them, must not print ALL OK
    if p.returncode != 0 or "FAILED" in out or not ran or int(ran.group(1)) < 10 or "skipped" in out:
        print("RESULT: FAILED")
        return 1
    print("RESULT: ALL OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
