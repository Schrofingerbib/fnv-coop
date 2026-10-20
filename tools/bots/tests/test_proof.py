"""The in-game proof script must itself work (over the fake bridge) and must FAIL when the game misbehaves."""
import argparse
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools"))
spec = importlib.util.spec_from_file_location("proof_input", ROOT / "tools" / "bots" / "proof_input.py")
proof = importlib.util.module_from_spec(spec)
spec.loader.exec_module(proof)
from bots import FakeBridge, World  # noqa: E402
from bots.bot import BridgeError  # noqa: E402


def run_with(mutant=None):
    w = World()
    w.add_actor(0x603, (0, 400, 0))
    w.add_door(0x601, (50, 50, 0))
    args = argparse.Namespace(target="FalloutNV.esm:603", door="FalloutNV.esm:601", fake=True)
    return proof.run(FakeBridge(w, mutant=mutant), args)


class Proof(unittest.TestCase):
    def test_passes_on_a_healthy_fake(self):
        rep = run_with()
        self.assertEqual([r for r in rep.rows if r["result"] != "PASS"], [])
        self.assertGreaterEqual(len(rep.rows), 11)

    def test_sneak_reads_the_console_name_the_game_has(self):
        self.assertEqual(proof.SNEAK_EXPR, "player.IsSneaking")
        row = next(r for r in run_with().rows if r["check"] == "sneak")
        self.assertEqual(row["result"], "PASS", row)
        self.assertEqual((row["before"], row["after_one_tap"], row["after_two_taps"]), (0, 1, 0))
        # twin: the name of the first in-game run. The console does not know it, and the proof must say FAIL (not PASS, not SKIP).
        old = proof.SNEAK_EXPR
        proof.SNEAK_EXPR = "player.GetSneaking"
        try:
            row = next(r for r in run_with().rows if r["check"] == "sneak")
        finally:
            proof.SNEAK_EXPR = old
        self.assertEqual(row["result"], "FAIL", row)
        self.assertIn("Unknown variable 'GetSneaking'", row["error"])

    def test_fake_console_rejects_functions_the_game_does_not_have(self):
        nv = FakeBridge(World())
        self.assertEqual(nv.eval("player.IsSneaking"), 0)
        with self.assertRaises(BridgeError) as c:
            nv.eval("player.GetSneaking")
        self.assertIn("Unknown variable 'GetSneaking'", str(c.exception))

    def test_each_defect_fails_the_matching_check(self):
        expect = {"move_ignored": "forward", "look_ignored": "turn", "stop_ignored": "stop", "jump_noop": "jump",
                  "fire_noop": "fire", "activate_noop": "activate"}
        for mutant, check in expect.items():
            with self.subTest(mutant=mutant):
                rep = run_with(mutant)
                failed = {r["check"] for r in rep.rows if r["result"] == "FAIL"}
                self.assertIn(check, failed, f"{mutant} should fail {check}; failed={failed}")



class GlueProof(unittest.TestCase):
    def test_glue_proof_script_runs_over_the_fake(self):
        import subprocess
        p = subprocess.run([sys.executable, "-I", str(ROOT / "tools" / "bots" / "proof_glue.py"), "--fake", "--out",
                            str(ROOT / "out" / "T6a" / "tmp" / "proof_glue_fake.json")], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("PROOF OK", p.stdout)


if __name__ == "__main__":
    unittest.main()
