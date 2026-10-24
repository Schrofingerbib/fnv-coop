"""Shared bits of the scripted pieces that tools/proof/manifest.json runs for roadmap step 10 (release scripts, launcher loopback, public tree
build, split-screen smoke). Every driver prints one `PASS name` or `FAIL name: why` line per check, so prove.py can read the first FAIL line
as the reason, and exits 0 (all pass), 1 (a check failed), 2 (the PC is not ready: the line starts "cannot reach the game instances"), or 3 (a
needed input is missing: prove.py classes it as BLOCKED/BUILD).
"""
from __future__ import annotations

import os
import sys
from typing import Callable, List, Tuple

# test-only: one deliberate bug per name; set by tools/proof/drivers/tests/run_tests.py
MUTANT = os.environ.get("PROOF_DRIVER_MUTANT", "")

EXIT_OK, EXIT_FAIL, EXIT_ENV, EXIT_BLOCKED = 0, 1, 2, 3


class Checks:
    def __init__(self, out: Callable[[str], None] = lambda s: print(s, flush=True)):
        self.rows: List[Tuple[str, bool, str]] = []
        self.out = out

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.rows.append((name, bool(ok), detail))
        self.out(("PASS " if ok else "FAIL ") + name + (f": {detail}" if detail and not ok else ""))
        return bool(ok)

    @property
    def ok(self) -> bool:
        return all(r[1] for r in self.rows)

    @property
    def failed(self) -> List[str]:
        return [r[0] for r in self.rows if not r[1]]

    def exit_code(self) -> int:
        return EXIT_OK if self.ok and self.rows else EXIT_FAIL

    def summary(self) -> str:
        n = len(self.rows)
        return f"{n - len(self.failed)}/{n} checks passed" + ("" if self.ok else "; FAILED: " + ", ".join(self.failed))


def env_exit(why: str, out: Callable[[str], None] = lambda s: print(s, flush=True)) -> int:
    """The PC is not ready (owner playing, no RAM, slot busy): prove.py classes this as ENV and asks again later."""
    out(f"cannot reach the game instances: {why}")
    return EXIT_ENV


def blocked_exit(why: str, out: Callable[[str], None] = lambda s: print(s, flush=True)) -> int:
    out(f"BLOCKED {why}")
    return EXIT_BLOCKED
