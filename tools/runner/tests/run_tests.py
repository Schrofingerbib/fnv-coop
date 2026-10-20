"""One command for the L2+L1 runner suite:  python -I tools/runner/tests/run_tests.py

It runs every tools/runner/tests/test_*.py (the same set as `python -I -m unittest discover -s tools/runner/tests`)
and prints ALL OK only if at least one test ran and no test failed or had an error. The C++ tests need the
prebuilt out/L1 exes (tools/runner/build_l1.cmd). NVCOOP_NO_CPP=1 skips them explicitly.
"""
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> int:
    suite = unittest.defaultTestLoader.discover(str(HERE), pattern="test*.py", top_level_dir=str(HERE))
    res = unittest.TextTestRunner(verbosity=1).run(suite)
    print(f"{res.testsRun} tests, {len(res.failures)} failures, {len(res.errors)} errors, {len(res.skipped)} skipped")
    if res.testsRun == 0 or not res.wasSuccessful():
        print("FAILED")
        return 1
    print("ALL OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
