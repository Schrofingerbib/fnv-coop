"""Tests of the build-speed tooling: runtime mutation twins, the twin runner, the build lock, the --affected selector.

    python -I tools/mut/run_tests.py [--no-build]

Prints ALL OK when everything passes. Every check has a twin (a deliberately broken variant that the check must catch):
  * fnvcoop_mut.h      : C++ program with 3 runtime twins, each must fail; production build must ignore FNVCOOP_MUT
  * run_twins.py       : its own --self-test (all caught / a survivor / broken real run / crash is not a caught twin)
  * build.py lock      : mkdir lock is exclusive, released, and a stale-free retry works; twin = a lock that is not released
  * test_all --affected: path -> suite selection; twin = a mapper that forgets shared sources
"""
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
PY = sys.executable
fails = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name + (("  " + detail) if (detail and not cond) else ""))
    if not cond:
        fails.append(name)


def run(cmd, env=None, cwd=None):
    e = dict(os.environ)
    e.pop("FNVCOOP_MUT", None)
    if env:
        e.update(env)
    p = subprocess.run(cmd, capture_output=True, text=True, errors="replace", env=e, cwd=cwd)
    return p.returncode, p.stdout + p.stderr


def cpp_tests(no_build: bool):
    print("== fnvcoop_mut.h (C++)")
    if not no_build:
        rc, out = run([PY, "-I", str(ROOT / "tools" / "build" / "build.py"), "--target", "fnvcoop_mut_tests", "fnvcoop_mut_prod_tests",
                       "--label", "mut-selftest"])
        check("build fnvcoop_mut_tests + fnvcoop_mut_prod_tests", rc == 0, out[-600:])
        if rc != 0:
            return
    exe = ROOT / "build" / "all" / "mut_tests" / "fnvcoop_mut_tests.exe"
    prod = ROOT / "build" / "all" / "mut_tests" / "fnvcoop_mut_prod_tests.exe"
    rc, out = run([PY, "-I", str(ROOT / "tools" / "mut" / "run_twins.py"), "--label", "mut selftest", "--twins", "1-3", "--real-regex",
                   ", 0 failed", "--caught-regex", ", [1-9][0-9]* failed", "--", str(exe)])
    check("3 runtime twins all caught", rc == 0 and "ALL OK" in out, out[-500:])
    # --mut N on the command line works too
    rc, out = run([str(exe), "--mut", "2"])
    check("--mut 2 switches twin 2 (exit non-zero)", rc != 0 and "twin = 2" in out, out[-300:])
    # the twin runner must NOT report ALL OK when a twin is missing from the code: twin 9 does not exist, so it survives
    rc, out = run([PY, "-I", str(ROOT / "tools" / "mut" / "run_twins.py"), "--label", "twin-of-the-runner", "--twins", "1-3,9",
                   "--real-regex", ", 0 failed", "--caught-regex", ", [1-9][0-9]* failed", "--", str(exe)])
    check("twin: a non-existent twin 9 is reported as SURVIVED", rc != 0 and "SURVIVED: twin 9" in out, out[-400:])
    # production build ignores FNVCOOP_MUT completely
    rc, out = run([str(prod)], env={"FNVCOOP_MUT": "1"})
    check("production build ignores FNVCOOP_MUT=1", rc == 0 and ", 0 failed" in out, out[-300:])


def runner_tests():
    print("== run_twins.py self-test")
    rc, out = run([PY, "-I", str(ROOT / "tools" / "mut" / "run_twins.py"), "--self-test"])
    check("run_twins --self-test", rc == 0 and "run_twins self-test: ALL OK" in out, out[-600:])


def lock_tests():
    print("== build lock")
    sys.path.insert(0, str(ROOT / "tools" / "build"))
    import importlib

    b = importlib.import_module("build")
    old = b.LOCK
    tmp = Path(tempfile.mkdtemp(prefix="locktest_"))
    b.LOCK = tmp / "build.lock"
    try:
        b.acquire_lock(True)
        check("lock dir created by acquire", b.LOCK.is_dir())
        got = []

        def second():
            b.acquire_lock(True)
            got.append(time.time())
            b.release_lock()

        orig_sleep = time.sleep
        b.time.sleep = lambda s: orig_sleep(0.05)  # do not wait 15 s in the test
        t = threading.Thread(target=second)
        t0 = time.time()
        t.start()
        orig_sleep(0.3)
        check("second acquirer waits while the lock is held", not got)
        b.release_lock()
        t.join(5)
        check("second acquirer proceeds after release", bool(got) and not b.LOCK.exists())
        b.time.sleep = orig_sleep
        # twin: a lock that is never released blocks the next acquirer (this is what the test above would catch)
        b.acquire_lock(True)
        blocked = []
        b.time.sleep = lambda s: (blocked.append(1), orig_sleep(0.02))[1] if len(blocked) < 5 else (_ for _ in ()).throw(SystemExit)
        try:
            b.acquire_lock(True)
            twin_caught = False
        except SystemExit:
            twin_caught = True
        b.time.sleep = orig_sleep
        check("twin: an unreleased lock keeps blocking (detected)", twin_caught)
        b.release_lock()
    finally:
        b.LOCK = old
        b.time.sleep = time.sleep
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)


def affected_tests():
    print("== test_all --affected selection")
    sys.path.insert(0, str(ROOT / "tools"))
    import importlib

    ta = importlib.import_module("test_all")
    sel = ta.select_affected
    names = [s[0] for s in ta.SUITES]
    check("nothing changed -> nothing selected", sel(files=[]) == [])
    s1 = sel(files=["plugin/hooks_world/world_core.cpp"])
    check("hooks_world change selects L12 hooks_world", any("L12" in n for n in s1), str(s1))
    check("hooks_world change does not select unrelated python suites", not any("T5 chaos" in n for n in s1), str(s1))
    s2 = sel(files=["synccore/src/wire.cpp"])
    check("synccore/src change selects every C++ suite", all(n in s2 for n, _c, comp, _t in ta.SUITES if comp and "build" not in n.lower() or n.startswith("L4")), str(len(s2)))
    s3 = sel(files=["tools/mut/fnvcoop_mut.h"])
    check("the mutation header selects the twin suites (L12, T6a, L11)", all(any(k in n for n in s3) for k in ("L12", "T6a bot input", "L11")), str(s3))
    s4 = sel(files=["tools/chaos/impair.py"])
    check("tools/chaos change selects the chaos suite", any("T5 chaos" in n for n in s4), str(s4))
    s5 = sel(files=["docs/TOOLS-BRIEF.md"])
    check("docs-only change selects nothing", s5 == [], str(s5))
    s5b = sel(files=["docs/quest-policies/rules.json"])
    check("rules.json (compiled into the synccore tests) is test input, not documentation", any("L4 synccore" in n for n in s5b), str(s5b))
    s5c = sel(files=["scenarios/simfuzz/regress_new_owner_no_wait.json"])
    check("a saved fuzz scenario selects the simfuzz suite", any("T2b simfuzz" in n for n in s5c), str(s5c))
    check("a saved fuzz scenario does not select unrelated suites", not any("T5 chaos" in n or "L12" in n for n in s5c), str(s5c))
    s6 = sel(files=["something/new/unknown.cpp"])
    check("unknown path -> everything (safe default)", len(s6) == len(names), str(len(s6)))
    # twin: a selector that forgets the shared-source rule must be detected by the synccore check above
    saved = dict(ta.SHARED_PREFIXES) if isinstance(ta.SHARED_PREFIXES, dict) else None
    ta.SHARED_PREFIXES_BACKUP = ta.SHARED_PREFIXES
    try:
        ta.SHARED_PREFIXES = ()
        s7 = sel(files=["synccore/src/wire.cpp"])
        check("twin: without the shared-source rule a synccore change selects fewer suites (detected)", len(s7) < len(s2), "%d vs %d" % (len(s7), len(s2)))
    finally:
        ta.SHARED_PREFIXES = ta.SHARED_PREFIXES_BACKUP
    saved_ni = ta.NOT_IGNORED_PREFIXES
    try:
        ta.NOT_IGNORED_PREFIXES = ()
        check("twin: ignoring all of docs/ and scenarios/simfuzz/ makes rules.json select nothing (detected)", sel(files=["docs/quest-policies/rules.json"]) == [])
    finally:
        ta.NOT_IGNORED_PREFIXES = saved_ni


def main():
    no_build = "--no-build" in sys.argv
    runner_tests()
    lock_tests()
    affected_tests()
    cpp_tests(no_build)
    if fails:
        print("\nFAILED: " + "; ".join(fails))
        return 1
    print("\nbuild-speed tooling: ALL OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
