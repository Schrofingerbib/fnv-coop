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


def run_with(mutant=None, always_run=True):
    w = World()
    w.add_actor(0x603, (0, 400, 0))
    w.add_door(0x601, (50, 50, 0))
    args = argparse.Namespace(target="FalloutNV.esm:603", door="FalloutNV.esm:601", fake=True)
    nv = FakeBridge(w, mutant=mutant)
    nv.always_run = always_run
    return proof.run(nv, args)


def run_with_boxes(dismiss, mutant=None):
    """The proof over a fake that shows two engine message boxes; -> (report, bridge)."""
    w = World()
    w.add_actor(0x603, (0, 400, 0))
    w.add_door(0x601, (50, 50, 0))
    nv = FakeBridge(w, mutant=mutant)
    nv.native_boxes = [1, 2]
    args = argparse.Namespace(target="FalloutNV.esm:603", door="FalloutNV.esm:601", fake=True, dismiss_messages=dismiss)
    return proof.run(nv, args), nv


def run_with_waves(**dismiss_kw):
    """The proof with --dismiss-messages over a fake whose engine boxes come in waves (6, then 2 at 3 s, then 2 at 19 s)."""
    w = World()
    w.add_actor(0x603, (0, 400, 0))
    w.add_door(0x601, (50, 50, 0))
    nv = FakeBridge(w)
    nv.native_boxes, nv.box_waves = [1] * 6, [(3.0, [1, 1]), (19.0, [1, 2])]
    args = argparse.Namespace(target="FalloutNV.esm:603", door="FalloutNV.esm:601", fake=True, dismiss_messages=True, **dismiss_kw)
    return proof.run(nv, args), nv


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

    def test_sneak_waits_for_the_landing_after_the_jump(self):
        row = next(r for r in run_with().rows if r["check"] == "sneak")
        self.assertEqual(row["result"], "PASS", row)
        # twin: the first in-game order (sneak right after the jump) must fail like it did in the game
        old = proof.SETTLE_AFTER_JUMP_S
        proof.SETTLE_AFTER_JUMP_S = 0.0
        try:
            row = next(r for r in run_with().rows if r["check"] == "sneak")
        finally:
            proof.SETTLE_AFTER_JUMP_S = old
        self.assertEqual(row["result"], "FAIL", row)
        self.assertEqual((row["after_one_tap"], row["after_two_taps"]), (1, 1))

    def test_forward_runs_whatever_the_always_run_toggle(self):
        # first in-game run (2026-10-21): always-run on, and Run held for run:true made the player walk (152-165 units in 2 s)
        for on in (True, False):
            with self.subTest(always_run=on):
                row = next(r for r in run_with(always_run=on).rows if r["check"] == "forward")
                self.assertEqual(row["result"], "PASS", row)
                self.assertEqual((row["move"]["alwaysRun"], row["move"]["run"], row["move"]["runControl"]), (on, True, not on))
        # twin: the old key meaning (Run held for run:true). With always-run on the player walks and forward must FAIL.
        row = next(r for r in run_with("run_is_key").rows if r["check"] == "forward")
        self.assertEqual(row["result"], "FAIL", row)
        self.assertLess(row["along"], 300)
        self.assertTrue(row["move"]["runControl"])
        row = next(r for r in run_with("run_is_key", always_run=False).rows if r["check"] == "forward")
        self.assertEqual(row["result"], "PASS", row)                   # with always-run off the key meaning was right

    def test_fake_console_rejects_functions_the_game_does_not_have(self):
        nv = FakeBridge(World())
        self.assertEqual(nv.eval("player.IsSneaking"), 0)
        with self.assertRaises(BridgeError) as c:
            nv.eval("player.GetSneaking")
        self.assertIn("Unknown variable 'GetSneaking'", str(c.exception))

    def test_each_defect_fails_the_matching_check(self):
        expect = {"move_ignored": "forward", "look_ignored": "turn", "stop_ignored": "stop", "jump_noop": "jump",
                  "fire_noop": "fire", "activate_noop": "activate", "run_is_key": "forward"}
        for mutant, check in expect.items():
            with self.subTest(mutant=mutant):
                rep = run_with(mutant)
                failed = {r["check"] for r in rep.rows if r["result"] == "FAIL"}
                self.assertIn(check, failed, f"{mutant} should fail {check}; failed={failed}")

    def test_dismiss_messages_is_opt_in(self):
        rep, nv = run_with_boxes(dismiss=False)
        self.assertEqual(nv.native_presses, [])                     # never called on its own
        self.assertNotIn("dismiss_messages", {r["check"] for r in rep.rows})
        rep, nv = run_with_boxes(dismiss=True)
        self.assertEqual((rep.rows[0]["check"], rep.rows[0]["result"]), ("dismiss_messages", "PASS"))
        self.assertEqual((nv.native_presses, nv.native_boxes), ([0, 0], []))
        rep, nv = run_with_boxes(dismiss=True, mutant="msgbox_stuck")   # a box that never closes fails the row
        self.assertEqual((rep.rows[0]["check"], rep.rows[0]["result"]), ("dismiss_messages", "FAIL"))

    def test_dismiss_messages_row_passes_while_boxes_close(self):
        # PR #9 review: a box that is closing (top menu 1001, no current message) refused the next press and the row failed
        w = World()
        w.add_actor(0x603, (0, 400, 0))
        w.add_door(0x601, (50, 50, 0))
        nv = FakeBridge(w)
        nv.native_boxes, nv.box_closing_s = [1, 2, 1], 0.8
        args = argparse.Namespace(target="FalloutNV.esm:603", door="FalloutNV.esm:601", fake=True, dismiss_messages=True)
        rep = proof.run(nv, args)
        self.assertEqual((rep.rows[0]["check"], rep.rows[0]["result"], rep.rows[0]["pressed"]), ("dismiss_messages", "PASS", 3))
        self.assertEqual([r for r in rep.rows if r["result"] != "PASS"], [])

    def test_dismiss_messages_waits_out_the_waves_before_the_checks(self):
        rep, nv = run_with_waves()
        self.assertEqual([r for r in rep.rows if r["result"] != "PASS"], [])
        self.assertEqual((rep.rows[0]["check"], rep.rows[0]["pressed"]), ("dismiss_messages", 10))
        self.assertGreaterEqual(rep.rows[0]["waited_s"], 39.0)
        self.assertEqual((nv.native_presses, nv.native_boxes, nv.box_waves), ([0] * 10, [], []))
        # twin: press only the boxes shown now (quiet 0 = the old behaviour). The wave at 3 s lands mid-proof and the player cannot move.
        rep, nv = run_with_waves(dismiss_quiet_s=0.0)
        self.assertEqual((rep.rows[0]["check"], rep.rows[0]["result"], rep.rows[0]["pressed"]), ("dismiss_messages", "PASS", 6))
        self.assertIn("forward", {r["check"] for r in rep.rows if r["result"] == "FAIL"})



class GlueProof(unittest.TestCase):
    def test_glue_proof_script_runs_over_the_fake(self):
        import subprocess
        p = subprocess.run([sys.executable, "-I", str(ROOT / "tools" / "bots" / "proof_glue.py"), "--fake", "--out",
                            str(ROOT / "out" / "T6a" / "tmp" / "proof_glue_fake.json")], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("PROOF OK", p.stdout)


if __name__ == "__main__":
    unittest.main()
