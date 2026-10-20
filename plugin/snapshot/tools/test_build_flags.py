"""run_tests.py compiles synccore sources with its own cl line. That line must carry synccore's language-conformance
flags from synccore/CMakeLists.txt, or MSVC parses the code differently: without /permissive- the std::visit over
Overloaded{...} lambdas in synccore/src/shadow.cpp fails to compile (C2651).

Twin: the same check on a copy of run_tests.py without /permissive- must report it missing.
Run: python -I plugin/snapshot/tools/test_build_flags.py
"""
import re
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
# Flags that change how the compiler reads the source. Warning and define flags are left out on purpose.
CONFORMANCE = re.compile(r"^(/permissive-?|/utf-8|/Zc:\S+|/std:\S+)$")


def synccore_flags(cmake_text: str) -> set:
    flags = set()
    m = re.search(r"if\(MSVC\)(.*?)(?:else\(\)|endif\(\))", cmake_text, re.S)
    if m:
        for opts in re.findall(r"target_compile_options\(\$\{t\}\s+PRIVATE\s+([^)]*)\)", m.group(1)):
            flags |= {f for f in opts.split() if CONFORMANCE.match(f)}
    std = re.search(r"set\(CMAKE_CXX_STANDARD\s+(\d+)\)", cmake_text)
    if std:
        flags.add("/std:c++%s" % std.group(1))
    return flags


def runner_flags(run_tests_text: str) -> set:
    m = re.search(r"binc\s*=\s*'([^']*)'", run_tests_text)
    return set(m.group(1).split()) if m else set()


def missing(cmake_text: str, run_tests_text: str) -> set:
    return synccore_flags(cmake_text) - runner_flags(run_tests_text)


class BuildFlags(unittest.TestCase):
    def setUp(self):
        self.cmake = (ROOT / "synccore" / "CMakeLists.txt").read_text(encoding="utf-8")
        self.runner = (HERE / "run_tests.py").read_text(encoding="utf-8")

    def test_synccore_flags_found(self):
        self.assertIn("/permissive-", synccore_flags(self.cmake))
        self.assertIn("/std:c++17", synccore_flags(self.cmake))

    def test_runner_carries_synccore_conformance_flags(self):
        self.assertEqual(missing(self.cmake, self.runner), set())

    def test_twin_without_permissive_is_caught(self):
        twin = self.runner.replace(" /permissive-", "")
        self.assertEqual(missing(self.cmake, twin), {"/permissive-"})


if __name__ == "__main__":
    res = unittest.main(exit=False, verbosity=2).result
    sys.exit(0 if res.wasSuccessful() else 1)
