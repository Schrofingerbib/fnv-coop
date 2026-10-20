"""Self-test for tools/test_all.py decisions made before a suite runs.

1. Without CMake or Ninja (neither on PATH nor bundled with VS 2022), every C++ suite is FAIL with one clear reason
   instead of 15 build scripts dying on "'cmake' is not recognized".
2. Without out/questdata, the suites that read it (T6b, T4b, L8) SKIP with the command that generates it.
Twins (TEST_ALL_MUTANT): no_preflight, no_questdata_gate. Each must make the Behaviour tests fail.
Run: python -I tools/test_all_selftest.py   (prints ALL OK)
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import test_all  # noqa: E402

MUTANTS = ("no_preflight", "no_questdata_gate")
DATA_SUITES = ("T6b co-op playbooks", "T4b scenarios", "L8 dialogue")


class Behaviour(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.empty = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_no_cmake_anywhere_is_reported(self):
        msg = test_all.toolchain_problem(which=lambda t: None, vs_dir=self.empty)
        self.assertIn("cmake", msg)
        self.assertIn("ninja", msg)

    def test_vs_bundled_tools_are_enough(self):
        for sub in (r"CMake\bin\cmake.exe", r"Ninja\ninja.exe"):
            (self.empty / sub).parent.mkdir(parents=True, exist_ok=True)
            (self.empty / sub).write_bytes(b"")
        self.assertEqual(test_all.toolchain_problem(which=lambda t: None, vs_dir=self.empty), "")

    def test_tools_on_path_are_enough(self):
        self.assertEqual(test_all.toolchain_problem(which=lambda t: "C:/x/" + t + ".exe", vs_dir=self.empty), "")

    def test_compile_suites_fail_fast_without_toolchain(self):
        reason = test_all.toolchain_problem(which=lambda t: None, vs_dir=self.empty)
        for name, _, compiles in test_all.SUITES:
            if compiles and name not in test_all.NEEDS:
                self.assertEqual(test_all.decide(name, True, False, reason), ("fail", reason), name)
        self.assertEqual(test_all.decide("T5 chaos box", False, False, reason), ("run", ""))

    def test_quick_still_skips_compile_suites_silently(self):
        self.assertEqual(test_all.decide("L4 synccore", True, True, "anything"), ("skip", ""))

    def test_data_suites_skip_without_questdata(self):
        with mock.patch.object(test_all, "ROOT", self.empty):
            for name in DATA_SUITES:
                compiles = dict((n, c) for n, _, c in test_all.SUITES)[name]
                action, reason = test_all.decide(name, compiles, False, "")
                self.assertEqual(action, "skip", name)
                self.assertIn("out/questdata", reason)

    def test_data_suites_run_with_questdata(self):
        qd = self.empty / "out" / "questdata"
        qd.mkdir(parents=True)
        for f in ("quests.json", "forms.json.gz", "risk.json"):
            (qd / f).write_bytes(b"")
        with mock.patch.object(test_all, "ROOT", self.empty):
            self.assertEqual(test_all.decide("T6b co-op playbooks", False, False, ""), ("run", ""))
            self.assertEqual(test_all.decide("T4b scenarios", False, False, ""), ("run", ""))


def run_twin(mutant: str) -> int:
    env = dict(os.environ, TEST_ALL_MUTANT=mutant)
    return subprocess.run([sys.executable, "-I", __file__, "--behaviour-only"], env=env, capture_output=True,
                          text=True).returncode


def main() -> int:
    if "--behaviour-only" in sys.argv:
        res = unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.loadTestsFromTestCase(Behaviour))
        return 0 if res.wasSuccessful() else 1
    res = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Behaviour))
    survivors = [m for m in MUTANTS if run_twin(m) == 0]
    print(f"mutation twins: {len(MUTANTS)} run, {len(survivors)} survived{': ' + ', '.join(survivors) if survivors else ''}")
    ok = res.wasSuccessful() and not survivors
    print("test_all preflight: " + ("ALL OK" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
