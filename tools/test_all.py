"""Run every lane's game-free test suite; exit non-zero if any fails.

Usage: python -I tools/test_all.py [--quick]
--quick skips suites that compile C++ (L1, L6, L8, L7 engine compile).
"""
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SUITES = [
    ("canonical nvcoop.dll build", ["cmd", "/c", r"tools\runner\build_l1.cmd"], True),
    # (name, command, compiles C++)
    ("test_all preflight", [sys.executable, "-I", "tools/test_all_selftest.py"], False),
    ("T5 chaos box", [sys.executable, "-I", "tools/chaos/run_tests.py"], False),
    ("L3 questdata", [sys.executable, "-I", "tools/questdata/run_tests.py"], False),
    ("L7 snapshot", [sys.executable, "-I", "plugin/snapshot/tools/run_tests.py", "--no-engine"], False),
    ("L9 coverage", [sys.executable, "-I", "tools/coverage/run_tests.py"], False),
    ("T6a bot actions", [sys.executable, "-I", "tools/bots/run_tests.py"], False),
    ("T6b co-op playbooks", [sys.executable, "-I", "tools/bots/playbooks/tests/run_tests.py"], False),
    ("T4a warm pool", [sys.executable, "-I", "tools/pool/run_tests.py"], False),
    ("T4b scenarios", [sys.executable, "-I", "-X", "utf8", "tools/scenarios/run_tests.py"], False),
    ("T1b flight analyzer (fast)", [sys.executable, "-I", "tools/flight/run_tests.py"], False),
    ("T1b flight analyzer (full + mutation twin)", [sys.executable, "-I", "tools/flight/run_tests.py", "--full"], True),
    ("T7 animcheck", [sys.executable, "-I", "tools/animcheck/run_tests.py"], False),
    ("L2+L1 runner (py)", [sys.executable, "-I", "-m", "unittest", "discover", "-s", "tools/runner/tests"], True),
    ("L6 nvmp sigs (pinned 9.4)", [sys.executable, "-I", "plugin/nvmp/tools/verify_sigs.py"], False),
    ("L6 nvmp compat tool", [sys.executable, "-I", "plugin/nvmp/tools/test_compat.py"], False),
    ("L6 nvmp tests", ["cmd", "/c", r"plugin\nvmp\run_tests.cmd"], True),
    ("L8 dialogue", [sys.executable, "-I", "plugin/dialogue/run_tests.py"], True),
    ("L5 hooks", ["cmd", "/c", r"plugin\hooks\run_tests.bat"], True),
    ("T2b simfuzz", [sys.executable, "-I", "tools/simfuzz/run_tests.py"], True),
    ("L4 synccore", ["cmd", "/c", r"synccore\run_tests.bat"], True),
    ("L6 client patcher", ["cmd", "/c", r"plugin\nvmp\client_patches\run_tests.cmd"], True),
    ("L6 server hook", ["cmd", "/c", r"plugin\nvmp\server_hook\run_tests.cmd"], True),
    ("T6a bot input driver", ["cmd", "/c", r"plugin\bots\run_tests.cmd"], True),
    ("L10 coop runtime", ["cmd", "/c", r"plugin\coop\run_tests.cmd"], True),
    ("L11 hooks_ui", ["cmd", "/c", r"plugin\hooks_ui\run_tests.cmd"], True),
    ("L12 hooks_world", ["cmd", "/c", r"plugin\hooks_world\run_tests.cmd"], True),
    ("T1a flight recorder", ["cmd", "/c", r"plugin\flight\run_tests.cmd"], True),
    ("T3b reload state", ["cmd", "/c", r"plugin\reload\run_tests.cmd"], True),
    ("T3a loader", ["cmd", "/c", r"plugin\loader\run_tests.cmd"], True),
    ("T7 animprobe core", ["cmd", "/c", r"plugin\animprobe\run_tests.cmd"], True),
]


import re

# Public export: suites that need your own game install or generated game data skip with a reason instead of failing.
def _data(*parts):
    return (ROOT.joinpath(*parts)).exists()


QUESTDATA = "needs out/questdata (python -I tools/questdata/run_all.py --data \"<your game>\\Data\")"
NEEDS = {
    "L6 nvmp sigs (pinned 9.4)": (lambda: _data("third_party", "nvmp", "nvmp", "client.dll"),
                                  "needs your own NV:MP 9.4 client.dll under third_party/nvmp/nvmp/"),
    "L8 dialogue": (lambda: _data("out", "questdata", "risk.json"),
                    "needs out/questdata (python -I tools/questdata/run_all.py) and an MSVC x86 toolchain"),
    "T6b co-op playbooks": (lambda: _data("out", "questdata", "quests.json"), QUESTDATA),
    "T4b scenarios": (lambda: _data("out", "questdata", "forms.json.gz"), QUESTDATA),
}

# The C++ suites call vcvars, which puts the VS-bundled CMake and Ninja on PATH only when the VS component
# "C++ CMake tools for Windows" is installed. Without it every build script dies on "'cmake' is not recognized".
VS_CMAKE_DIR = Path(r"C:\Program Files\Microsoft Visual Studio\2022\Community\Common7\IDE\CommonExtensions\Microsoft\CMake")


def toolchain_problem(which=shutil.which, vs_dir=VS_CMAKE_DIR) -> str:
    """Why the C++ suites cannot run, or "" when they can."""
    if os.environ.get("TEST_ALL_MUTANT") == "no_preflight":  # twin: never checks
        return ""
    missing = [tool for tool, sub in (("cmake", r"CMake\bin\cmake.exe"), ("ninja", r"Ninja\ninja.exe"))
               if not which(tool) and not (vs_dir / sub).exists()]
    if not missing:
        return ""
    return (" and ".join(missing) + " not found: install them (winget install Kitware.CMake Ninja-build.Ninja) or the "
            "Visual Studio 2022 component 'C++ CMake tools for Windows'")


def decide(name: str, compiles: bool, quick: bool, toolchain: str):
    """('run', '') or ('skip' | 'fail', reason) for one suite, before it runs."""
    if quick and compiles:
        return "skip", ""
    need = NEEDS.get(name)
    if need and os.environ.get("TEST_ALL_MUTANT") == "no_questdata_gate" and "out/questdata" in need[1]:
        need = None  # twin: runs data suites without their data
    if need and not need[0]():
        return "skip", need[1]
    if compiles and toolchain:
        return "fail", toolchain
    return "run", ""

# Positive evidence that tests actually executed (unittest, ctest, custom runners).
EVIDENCE = re.compile(
    r"Ran [1-9]\d* tests?|100% tests passed|ALL OK|RESULT: ALL OK|all signatures unique|"
    r"\b[1-9]\d* run, 0 failed|, 0 failed|\b[1-9]\d* checks?,? 0 failed|all signatures resolve uniquely|"
    r"Linking CXX shared library \S*nvcoop\.dll|ninja: no work to do",
    re.I,
)
# Explicit failure markers of the REAL run. Mutation-twin output legitimately contains [FAIL] lines,
# so only unittest summaries and non-zero ctest failure counts count here; real-run counts rely on exit code.
NEGATIVE = re.compile(r"^FAILED \((failures|errors)=|\b[1-9]\d* tests failed out of|is not recognized as an internal", re.M)


def main() -> int:
    quick = "--quick" in sys.argv
    failed = []
    toolchain = "" if quick else toolchain_problem()
    if toolchain:
        print(f"PREFLIGHT: {toolchain}. The C++ suites are marked FAIL without running.")
    for name, cmd, compiles in SUITES:
        action, reason = decide(name, compiles, quick, toolchain)
        if action == "skip":
            print(f"SKIP {name}" + (f": {reason}" if reason else ""))
            continue
        if action == "fail":
            print(f"FAIL {name}: {reason}")
            failed.append(name)
            continue
        t0 = time.time()
        p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, errors="replace")
        out = p.stdout + p.stderr
        # Exit code alone is not trusted: a test exe that never started can still exit 0.
        evidence = EVIDENCE.search(out)
        ok = p.returncode == 0 and evidence is not None and not NEGATIVE.search(out)
        if p.returncode == 0 and not ok:
            print(f"    (exit 0 but no proof tests ran, or a failure line was printed)")
        print(f"{'PASS' if ok else 'FAIL'} {name} ({time.time() - t0:.0f}s)")
        if not ok:
            failed.append(name)
            tail = (p.stdout + p.stderr).strip().splitlines()[-15:]
            print("    " + "\n    ".join(tail))
    print(f"\n{len(failed)} suite(s) failed" if failed else "\nALL SUITES PASS")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
