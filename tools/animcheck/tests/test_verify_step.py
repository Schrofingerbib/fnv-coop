"""run_tests.verify_step: a missing verify_engine.py skips with a reason, a present one must pass and its twin must fail.
The mutant ANIMCHECK_MUTANT=verify_skip_all (never runs the check) must make these tests fail."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from animcheck import run_tests  # noqa: E402


def fake_runner(rc_real, rc_twin, stdout="verify_engine: 12 checks, 0 failed"):
    calls = []

    def runner(script, mutant=""):
        calls.append(mutant)
        rc = rc_twin if mutant else rc_real
        return subprocess.CompletedProcess([str(script)], rc, stdout=stdout if not mutant else "", stderr="")
    return runner, calls


class VerifyStep(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ve = Path(self.tmp.name) / "verify_engine.py"
        self.ve.write_text("# fake\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_script_skips_with_reason(self):
        bad, lines = run_tests.verify_step(Path(self.tmp.name) / "absent.py")
        self.assertEqual(bad, 0)
        self.assertTrue(lines[0].startswith("SKIP verify_engine: absent.py is not in this checkout"), lines)

    def test_failing_check_counts(self):
        runner, _ = fake_runner(1, 1)
        self.assertEqual(run_tests.verify_step(self.ve, runner)[0], 1)

    def test_surviving_twin_counts(self):
        runner, calls = fake_runner(0, 0)
        bad, lines = run_tests.verify_step(self.ve, runner)
        self.assertEqual((bad, calls), (1, ["", "verify"]))
        self.assertIn("SURVIVED: verify_engine twin", lines)

    def test_pass_and_caught_twin_is_clean(self):
        runner, _ = fake_runner(0, 1)
        self.assertEqual(run_tests.verify_step(self.ve, runner), (0, ["verify_engine: 12 checks, 0 failed", "verify_engine twin caught"]))

    def test_empty_output_does_not_crash(self):
        runner, _ = fake_runner(1, 1, stdout="")
        bad, lines = run_tests.verify_step(self.ve, runner)
        self.assertEqual(bad, 1)
        self.assertEqual(lines[0], "(verify_engine printed nothing)")


if __name__ == "__main__":
    unittest.main()
