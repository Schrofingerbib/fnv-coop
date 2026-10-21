"""enemy-anim: the in-game proof script's offline half (a fake game). The mutation twin npc_proof_pass turns every FAIL into a PASS and must break this."""
from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from animcheck import npc_proof  # noqa: E402


class ProofTests(unittest.TestCase):
    def test_the_selftest_passes_and_its_bad_games_are_caught(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = npc_proof.selftest()
        self.assertEqual(rc, 0, buf.getvalue())
        self.assertIn("another numbering", buf.getvalue())

    def test_a_good_game_has_no_failure_and_all_steps(self):
        g = npc_proof.FakeGame()
        res = npc_proof.Proof(g, "0x00104C2F", weapon=True, kill=True, sleep=g.sleep).run()
        self.assertEqual([r for r in res if r[0] == "FAIL"], [])
        steps = {r[1].split()[0] for r in res}
        self.assertTrue({"1", "2", "3", "4", "5", "6", "7", "8", "9"} <= steps)

    def test_without_the_destructive_flags_those_steps_are_skipped(self):
        g = npc_proof.FakeGame()
        res = npc_proof.Proof(g, "0x00104C2F", sleep=g.sleep).run()
        self.assertTrue(any(r[0] == "SKIP" and r[1].startswith("9") for r in res))
        self.assertFalse(g.dead)

    def test_a_missing_anim_npc_is_not_ready_not_a_failure(self):
        class Dead:
            def call(self, *a, **k):
                raise RuntimeError("no such method")
            def exec(self, *a, **k):
                return {}
        with self.assertRaises(npc_proof.NotReady):
            npc_proof.Proof(Dead(), "0x1", sleep=lambda s: None).run()


if __name__ == "__main__":
    unittest.main()
