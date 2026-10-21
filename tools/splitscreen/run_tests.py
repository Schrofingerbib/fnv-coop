#!/usr/bin/env python3
"""One runner for every split-screen test. Prints ALL OK when everything passes.

    python -I tools/splitscreen/run_tests.py [--no-build] [--skip-cpp]

Steps (shared build tree build/all through tools/build/build.py, which takes the build lock; CMAKE_BUILD_PARALLEL_LEVEL=2 unless set):
  1. build nvsplit_focus_tests, nvsplit_tests and nvsplit_glue_check (the game glue, compile check only)
  2. focus guard tests: real run, then runtime twins 1..7 (FNVCOOP_MUT)
  3. split-screen library tests: real run (with SPLIT_EXE = a reference exe when <reference exe> exists), then twins 8..34
  4. Python tool tests: real run, then 16 twins (split_mut), which includes the check_layout and smoke playbook self-tests
  5. engine pins against a reference exe (re_pins.py; SKIP when the exe is not on this PC)
Never starts the game.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PY = [sys.executable, "-I"]
EXE_DIR = REPO / "build" / "all" / "splitscreen_tests"
REFERENCE = REPO / "out" / "L5" / "re" / "fnv_reference.exe"


def run(label: str, cmd, env=None) -> bool:
    t0 = time.time()
    print(f"== {label}", flush=True)
    p = subprocess.run(cmd, cwd=REPO, env=env, capture_output=True, text=True, errors="replace")
    out = (p.stdout + p.stderr).strip()
    tail = "\n".join(out.splitlines()[-6:])
    print(tail)
    print(f"   -> {'ok' if p.returncode == 0 else 'FAILED'} ({time.time() - t0:.0f} s)", flush=True)
    return p.returncode == 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-build", action="store_true")
    ap.add_argument("--skip-cpp", action="store_true", help="Python tools and engine pins only (the C++ half is covered by plugin/splitscreen/run_tests.cmd)")
    a = ap.parse_args(argv)
    env = dict(os.environ)
    env.setdefault("CMAKE_BUILD_PARALLEL_LEVEL", "2")
    ok = True
    if not a.skip_cpp:
        if not a.no_build:
            ok &= run("build", PY + ["tools/build/build.py", "--target", "nvsplit_focus_tests", "nvsplit_tests", "nvsplit_glue_check",
                                      "--label", "Track B splitscreen"], env)
        if ok:
            ok &= run("focus guard: real + twins 1..7",
                      PY + ["tools/mut/run_twins.py", "--label", "Track B focus guard", "--twins", "1-7", "--real-regex", " 0 failed",
                            "--caught-regex", ", [1-9][0-9]* failed", "--", str(EXE_DIR / "nvsplit_focus_tests.exe")], env)
            lib_env = dict(env)
            if REFERENCE.is_file():
                lib_env["SPLIT_EXE"] = str(REFERENCE)
            else:
                lib_env.pop("SPLIT_EXE", None)
            ok &= run("split-screen libraries: real + twins 8..34",
                      PY + ["tools/mut/run_twins.py", "--label", "Track B splitscreen libs", "--twins", "8-34", "--real-regex", " 0 failed",
                            "--caught-regex", ", [1-9][0-9]* failed", "--", str(EXE_DIR / "nvsplit_tests.exe")], lib_env)
    ok &= run("python tools: tests", PY + ["tools/splitscreen/tests/test_tools.py"], env)
    ok &= run("python tools: twins", PY + ["tools/splitscreen/tests/test_tools.py", "--twins"], env)
    ok &= run("engine pins", PY + ["tools/splitscreen/re_pins.py"], env)
    print("ALL OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
