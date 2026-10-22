"""tools/runner/nvready.py: the readiness wait shared by prove.py and the proof scripts (no game).

    python -I tools/runner/tests/test_nvready.py

The mutation twin (NVREADY_MUTANT=pipe_only: a pipe that answers `ping` counts as ready) must make test_a_pipe_that_answers_is_not_ready fail;
Twin.test_the_twin_is_caught runs that.
"""
from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNNER = HERE.parent
sys.path.insert(0, str(RUNNER))
import nvready  # noqa: E402


class FakeNv:
    """A game that is booting for `boot_pings` pings (pipe answers, menuReady false, no main loop), then is ready."""

    def __init__(self, boot_pings=0, lost_pings=0):
        self.boot, self.lost, self.n = boot_pings, lost_pings, 0

    def call(self, method, params=None, **_):
        assert method == "ping"
        self.n += 1
        if self.n <= self.lost:
            raise RuntimeError("no reply within 10s")
        if self.n <= self.lost + self.boot:
            return {"menuReady": False, "mainLoopAgeMs": -1}
        return {"menuReady": True, "mainLoopAgeMs": 12}


class Clock:
    def __init__(self):
        self.t = 0.0

    def sleep(self, s):
        self.t += s

    def now(self):
        return self.t


class Ready(unittest.TestCase):
    def wait(self, bridges, wait_s=60.0):
        c = Clock()
        return nvready.wait_ready(bridges, wait_s, sleep=c.sleep, now=c.now)

    def test_a_pipe_that_answers_is_not_ready(self):
        self.assertFalse(nvready.is_ready({"menuReady": False, "mainLoopAgeMs": -1}))
        self.assertFalse(nvready.is_ready({"menuReady": True, "mainLoopAgeMs": -1}))
        self.assertFalse(nvready.is_ready({}))
        self.assertFalse(nvready.is_ready(None))
        self.assertTrue(nvready.is_ready({"menuReady": True, "mainLoopAgeMs": 0}))

    def test_waits_until_every_copy_is_ready(self):
        a, b = FakeNv(boot_pings=4), FakeNv(boot_pings=7, lost_pings=2)
        self.assertIsNone(self.wait([a, b]))
        self.assertGreater(a.n, a.boot)
        self.assertGreater(b.n, b.boot + b.lost)

    def test_a_game_that_never_gets_ready_says_which_copy(self):
        why = self.wait([FakeNv(), FakeNv(boot_pings=10 ** 6)], wait_s=10.0)
        self.assertIn("copy 1 never became ready in 10 s", why)

    def test_ids_helper_closes_what_it_opened(self):
        opened = []

        class B(FakeNv):
            closed = False

            def close(self):
                self.closed = True

        def connect(i):
            opened.append(B(boot_pings=1))
            return opened[-1]
        c = Clock()
        self.assertIsNone(nvready.wait_ready_ids([2, 3], 30, connect=connect, sleep=c.sleep, now=c.now))
        self.assertEqual(len(opened), 2)
        self.assertTrue(all(o.closed for o in opened))

    def test_ids_helper_reports_a_missing_pipe(self):
        def connect(i):
            raise OSError("pipe gone")
        self.assertIn("copy 2: no bridge pipe", nvready.wait_ready_ids([2], 5, connect=connect))


REPO = HERE.parents[2]
# every script that talks to a copy right after prove.py booted it must wait for the copy to be ready first (item 3 of the 2026-10-22 sweep)
WAITERS = ["tools/avatar/proof_avatars.py", "plugin/companions/proof/social_proof.py", "plugin/loot/tools/loot_proof.py",
           "plugin/world_time/tools/worldtime_proof.py", "tools/bots/run_fight.py", "tools/proof/drivers/load_scenario.py"]


def waits(text: str) -> bool:
    return "nvready.wait_ready(" in text or "nvready.wait_ready_ids(" in text or "wait_ready = nvready.wait_ready" in text


class Wiring(unittest.TestCase):
    def test_every_script_waits_for_the_game_before_its_first_call(self):
        for rel in WAITERS:
            text = (REPO / rel).read_text(encoding="utf-8")
            self.assertTrue(waits(text), f"{rel} does not call the shared readiness wait (tools/runner/nvready.py)")

    def test_the_check_notices_a_script_without_the_wait(self):   # twin of the test above
        for rel in WAITERS:
            text = (REPO / rel).read_text(encoding="utf-8")
            stripped = text.replace("nvready.wait_ready", "nothing")
            self.assertFalse(waits(stripped), rel)


class Twin(unittest.TestCase):
    def test_the_twin_is_caught(self):
        env = dict(os.environ, NVREADY_MUTANT="pipe_only")
        cp = subprocess.run([sys.executable, "-I", str(Path(__file__).resolve()), "Ready.test_a_pipe_that_answers_is_not_ready"],
                            capture_output=True, text=True, env=env)
        self.assertNotEqual(cp.returncode, 0, "the pipe_only mutant was not noticed")


if __name__ == "__main__":
    unittest.main(verbosity=1)
