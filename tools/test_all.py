"""Run every lane's game-free test suite; exit non-zero if any fails.

Usage: python -I tools/test_all.py [--quick]
--quick skips the slow suites: the C++ builds (L1, L4, L5, L6, L8, L10-L12, T1a, T2b, T3, T6a driver, T7 probe)
and the T1b full run.
"""
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Tools a suite needs before it can run. The self-test scans each runner script (and the scripts it calls) for cmake
# and vcvars calls and fails when this list disagrees with the scan.
CM = ("cmake", "msvc")  # calls vcvars, then cmake -G Ninja (or needs the build_l1.cmd output)
MS = ("msvc",)          # calls vcvars, then cl directly
MS_OPT = ("msvc-optional",)  # calls vcvars and cl only when the vcvars file exists, else skips that part
NO = ()                 # Python only

SUITES = [
    # (name, command, skipped by --quick, tools it needs)
    ("canonical nvcoop.dll build", ["cmd", "/c", r"tools\runner\build_l1.cmd"], True, CM),
    ("test_all preflight", [sys.executable, "-I", "tools/test_all_selftest.py"], False, NO),
    ("T5 chaos box", [sys.executable, "-I", "tools/chaos/run_tests.py"], False, NO),
    ("L3 questdata", [sys.executable, "-I", "tools/questdata/run_tests.py"], False, NO),
    ("L7 snapshot", [sys.executable, "-I", "plugin/snapshot/tools/run_tests.py", "--no-engine"], False, MS_OPT),
    ("L9 coverage", [sys.executable, "-I", "tools/coverage/run_tests.py"], False, NO),
    ("T6a bot actions", [sys.executable, "-I", "tools/bots/run_tests.py"], False, NO),
    ("T6b co-op playbooks", [sys.executable, "-I", "tools/bots/playbooks/tests/run_tests.py"], False, NO),
    ("T4a warm pool", [sys.executable, "-I", "tools/pool/run_tests.py"], False, NO),
    ("T4b scenarios", [sys.executable, "-I", "-X", "utf8", "tools/scenarios/run_tests.py"], False, NO),
    ("T1b flight analyzer (fast)", [sys.executable, "-I", "tools/flight/run_tests.py"], False, NO),
    ("T1b flight analyzer (full + mutation twin)", [sys.executable, "-I", "tools/flight/run_tests.py", "--full"], True, NO),
    ("T7 animcheck", [sys.executable, "-I", "tools/animcheck/run_tests.py"], False, NO),
    ("L2+L1 runner (py)", [sys.executable, "-I", "-m", "unittest", "discover", "-s", "tools/runner/tests"], True, CM),
    ("L6 nvmp sigs (pinned 9.4)", [sys.executable, "-I", "plugin/nvmp/tools/verify_sigs.py"], False, NO),
    ("L6 nvmp compat tool", [sys.executable, "-I", "plugin/nvmp/tools/test_compat.py"], False, NO),
    ("L6 nvmp tests", ["cmd", "/c", r"plugin\nvmp\run_tests.cmd"], True, MS),
    ("L8 dialogue", [sys.executable, "-I", "plugin/dialogue/run_tests.py"], True, MS),
    ("L5 hooks", ["cmd", "/c", r"plugin\hooks\run_tests.bat"], True, CM),
    ("T2b simfuzz", [sys.executable, "-I", "tools/simfuzz/run_tests.py"], True, CM),
    ("L4 synccore", ["cmd", "/c", r"synccore\run_tests.bat"], True, CM),
    ("L6 client patcher", ["cmd", "/c", r"plugin\nvmp\client_patches\run_tests.cmd"], True, MS),
    ("L6 server hook", ["cmd", "/c", r"plugin\nvmp\server_hook\run_tests.cmd"], True, MS),
    ("T6a bot input driver", ["cmd", "/c", r"plugin\bots\run_tests.cmd"], True, CM),
    ("L10 coop runtime", ["cmd", "/c", r"plugin\coop\run_tests.cmd"], True, CM),
    ("L11 hooks_ui", ["cmd", "/c", r"plugin\hooks_ui\run_tests.cmd"], True, CM),
    ("L12 hooks_world", ["cmd", "/c", r"plugin\hooks_world\run_tests.cmd"], True, CM),
    ("T1a flight recorder", ["cmd", "/c", r"plugin\flight\run_tests.cmd"], True, CM),
    ("T3b reload state", ["cmd", "/c", r"plugin\reload\run_tests.cmd"], True, CM),
    ("T3a loader", ["cmd", "/c", r"plugin\loader\run_tests.cmd"], True, CM),
    ("T7 animprobe core", ["cmd", "/c", r"plugin\animprobe\run_tests.cmd"], True, CM),
]


import re

MUTANT = os.environ.get("TEST_ALL_MUTANT", "")

# Public export: suites that need your own game install or generated game data skip with a reason instead of failing.
# Each list holds every file the suite opens (found by reading its loaders and by tracing a run). A partial
# out/questdata (for example a run_all.py that stopped before m1.py) must skip, not die on FileNotFoundError.
QD = "out/questdata/"
QUESTDATA = "needs out/questdata (python -I tools/questdata/run_all.py --data \"<your game>\\Data\")"
NEEDS = {
    "L6 nvmp sigs (pinned 9.4)": (("third_party/nvmp/nvmp/client.dll",),
                                  "needs your own NV:MP 9.4 client.dll under third_party/nvmp/nvmp/"),
    # plugin/dialogue/tools/gen_m1_dialogue.py and gen_companion_topics.py
    "L8 dialogue": (tuple(QD + f for f in ("forms.json.gz", "dials.json.gz", "infos.jsonl.gz", "scripts.json.gz")),
                    QUESTDATA),
    # goodsprings_plan.load_plan / check_plan (scenarios/VCG0{1,2,3}.json), scenario_adapter, test_playbooks (forms)
    "T6b co-op playbooks": (tuple(QD + f for f in ("quests.json", "m1_goodsprings.json", "refnames.json.gz",
                                                   "forms.json.gz", "scenarios/VCG01.json", "scenarios/VCG02.json",
                                                   "scenarios/VCG03.json")), QUESTDATA),
    # scenlib.load_forms / load_quests / load_dials / load_infos / reset_ids (m1_goodsprings.json) / stage_world_refs
    # (scenarios/VCG02.json: without it the jump tests fail on an assertion, not on a missing file)
    "T4b scenarios": (tuple(QD + f for f in ("forms.json.gz", "quests.json", "dials.json.gz", "infos.jsonl.gz",
                                             "m1_goodsprings.json", "scenarios/VCG02.json")), QUESTDATA),
}


def missing_data(name: str) -> list:
    """The files under ROOT that suite `name` needs and that do not exist."""
    files, _ = NEEDS.get(name, ((), ""))
    if MUTANT == "no_questdata_gate" and files and files[0].startswith(QD):
        files = ()  # twin: runs data suites without their data
    if MUTANT == "questdata_one_file":
        files = files[:1]  # twin: checks only the first file, so a partial out/questdata gets through
    return [f for f in files if not (ROOT / f).exists()]


# The build scripts call vcvars by this full path, then cmake -G Ninja. vcvars puts the VS-bundled CMake and Ninja on
# PATH only when the VS component "C++ CMake tools for Windows" is installed. Without CMake every CMake script dies on
# "'cmake' is not recognized". Without vcvars every C++ script dies on "The system cannot find the path specified".
VS_ROOT = Path(r"C:\Program Files\Microsoft Visual Studio\2022\Community")
VS_CMAKE_DIR = VS_ROOT / r"Common7\IDE\CommonExtensions\Microsoft\CMake"
VCVARS_DIR = VS_ROOT / r"VC\Auxiliary\Build"
VCVARS = ("vcvars32.bat", "vcvars64.bat", "vcvarsall.bat")  # every name a build script calls


def toolchain(which=shutil.which, vs_dir=VS_CMAKE_DIR, vcvars_dir=VCVARS_DIR) -> dict:
    """{"cmake": (found, problem), "msvc": (found, problem)}. Exactly one string of each pair is empty."""
    if MUTANT == "no_preflight":  # twin: never checks
        return {"cmake": ("not checked", ""), "msvc": ("not checked", "")}
    found, missing = [], []
    for tool, sub in (("cmake", r"CMake\bin\cmake.exe"), ("ninja", r"Ninja\ninja.exe")):
        where = which(tool) or ((vs_dir / sub).exists() and str(vs_dir / sub))
        (found if where else missing).append(f"{tool} {where}" if where else tool)
    cmake = ("", " and ".join(missing) + " not found: install them (winget install Kitware.CMake Ninja-build.Ninja) "
             "or the Visual Studio 2022 component 'C++ CMake tools for Windows'") if missing else (", ".join(found), "")
    gone = [] if MUTANT == "no_vcvars_check" else [n for n in VCVARS if not (vcvars_dir / n).exists()]
    names = " and ".join(filter(None, (", ".join(gone[:-1]), gone[-1]))) if gone else ""
    msvc = ("", f"{names} not found in {vcvars_dir} (the build scripts call vcvars by this path): install "
            "Visual Studio 2022 Community with the workload 'Desktop development with C++'") if gone else \
        (f"{', '.join(VCVARS)} in {vcvars_dir}", "")
    return {"cmake": cmake, "msvc": msvc}


def required_tools(slow: bool, tools: tuple) -> tuple:
    """The tools a suite needs. This is not the --quick flag: T1b full is slow but Python only."""
    if MUTANT == "tools_from_quick":  # twin: the old proxy, every slow suite needs CMake and MSVC
        return CM if slow else NO
    return tools


def decide(name: str, slow: bool, tools: tuple, quick: bool, problems: dict):
    """('run', '') or ('skip' | 'fail', reason) for one suite, before it runs. problems: tool -> reason or ''."""
    if quick and slow:
        return "skip", ""
    missing = missing_data(name)
    if missing:
        shown = ", ".join(missing[:3]) + (f" and {len(missing) - 3} more" if len(missing) > 3 else "")
        return "skip", f"{NEEDS[name][1]}. Missing: {shown}"
    reasons = [problems[t] for t in required_tools(slow, tools) if problems.get(t)]
    if reasons:
        return "fail", ". ".join(reasons)
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


def preflight(quick: bool) -> dict:
    """Checks only the tools that the suites of this run need. Prints one line per tool. Returns tool -> problem."""
    users = {}
    for name, _, slow, tools in SUITES:
        if not (quick and slow):
            for t in required_tools(slow, tools):
                if t in ("cmake", "msvc"):  # an "-optional" tool never blocks a suite
                    users.setdefault(t, []).append(name)
    if not users:
        return {}
    problems = {}
    for tool, (found, problem) in toolchain().items():
        if tool not in users:
            continue
        problems[tool] = problem
        if problem:
            print(f"PREFLIGHT FAIL {tool}: {problem}. The {len(users[tool])} suites that need it are marked FAIL "
                  "without running.")
        else:
            print(f"PREFLIGHT OK {tool}: {found}")
    return problems


def main() -> int:
    quick = "--quick" in sys.argv
    failed = []
    problems = preflight(quick)
    for name, cmd, slow, tools in SUITES:
        action, reason = decide(name, slow, tools, quick, problems)
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
