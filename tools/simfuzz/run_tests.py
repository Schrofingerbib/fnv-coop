"""Tests for the co-op fuzzer: C++ suite (oracles, clean sweep, one mutation twin per bug class, shrink, fixtures) plus CLI checks.
Prints ALL OK. Usage: python -I tools/simfuzz/run_tests.py"""
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run as R  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (" " + extra if extra and not cond else ""))
    if not cond:
        fails.append(name)


def em_fail_seeds(args, out, max_fail=999):
    p = subprocess.run([R.EXE, "--target", "em", "--count", "60", "--duration", "8000", "--no-shrink", "--max-fail", str(max_fail), "--quiet", "--out", out] + args,
                       capture_output=True, text=True)
    return set(re.findall(r"^FAIL seed (\d+)", p.stdout, re.M))


def em_twins(tmp):
    """Mutation twin per em bug class: some seed that passes clean must fail once the bug is injected, and the clean sweep must be green.
    The clean sweep runs 60 seeds (4 players are covered by the long runs in docs/tools/T2b-fuzz.md); a twin run stops at its first catch."""
    d = os.path.join(tmp, "emtwin")
    clean = em_fail_seeds([], d)
    check("em: clean sweep (60 seeds) has no failures", not clean, "failing seeds: %s" % sorted(clean, key=int))
    bugs = subprocess.run([R.EXE, "--target", "em", "--list-bugs"], capture_output=True, text=True).stdout.split()
    check("em: %d bug classes listed (22 from T2a plus new_owner_no_wait and digest_buckets_positions)" % len(bugs), len(bugs) >= 24, str(bugs))
    for b in bugs:
        caught = em_fail_seeds(["--bug", b], d, max_fail=1) - clean
        check("em twin %s is caught" % b, bool(caught))


def engine_model_tests():
    """The engine model's own tests (own, world, engine scenarios and every mutation twin), built through the shared build driver."""
    b = subprocess.run([sys.executable, "-I", os.path.join(ROOT, "tools", "build", "build.py"), "--target", "fnvcoop_engine_tests", "--label", "engine tests", "--quiet"],
                       cwd=ROOT, capture_output=True, text=True, errors="replace")
    exe = os.path.join(ROOT, "build", "all", "synccore", "enginemodel", "fnvcoop_engine_tests.exe")
    check("engine model tests build", b.returncode == 0 and os.path.exists(exe), (b.stdout + b.stderr)[-1500:])
    if b.returncode != 0 or not os.path.exists(exe):
        return
    for group in ("own", "world", "engine", "combat", "twin"):
        p = subprocess.run([exe, "--group", group], cwd=ROOT, capture_output=True, text=True, errors="replace")
        m = re.search(r"== (\d+) tests, (\d+) passed, (\d+) failed", p.stdout)
        check("engine model tests, group %s (%s)" % (group, m.group(0) if m else "no summary"), p.returncode == 0 and m is not None and m.group(3) == "0" and int(m.group(1)) > 0,
              " | ".join(l for l in p.stdout.splitlines() if "FAIL" in l)[-1500:])


def main():
    engine_model_tests()
    p = subprocess.run(["cmd", "/c", r"synccore\fuzz\run_tests.bat", "--group", "fuzz"], cwd=ROOT, capture_output=True, text=True, errors="replace")
    out = p.stdout + p.stderr
    m = re.search(r"== (\d+) tests, (\d+) passed, (\d+) failed", out)
    check("C++ suite (%s)" % (m.group(0) if m else "no summary"), p.returncode == 0 and m is not None and m.group(3) == "0", out[-1500:])
    exe = R.EXE
    tmp = tempfile.mkdtemp(prefix="simfuzz_")
    # clean CLI run passes
    p = subprocess.run([exe, "--count", "30", "--seed", "5", "--quiet", "--out", tmp], capture_output=True, text=True)
    check("clean CLI run exits 0 and says ALL OK", p.returncode == 0 and "ALL OK" in p.stdout, p.stdout)
    # twin of the tool: an injected bug must make the CLI fail, shrink and write a replayable scenario
    p = subprocess.run([exe, "--count", "20", "--bug", "two_owners", "--max-fail", "1", "--out", tmp], capture_output=True, text=True)
    check("CLI fails with an injected bug", p.returncode == 1 and "FAIL seed" in p.stdout and "shrunk" in p.stdout, p.stdout)
    mins = [f for f in os.listdir(tmp) if f.endswith(".min.scenario.json")]
    check("minimized scenario written", len(mins) == 1)
    if mins:
        sc = os.path.join(tmp, mins[0])
        p = subprocess.run([exe, "--replay", sc], capture_output=True, text=True)
        check("replay of the minimized scenario reproduces (exit 1, VIOLATION line)", p.returncode == 1 and "VIOLATION multi_owner" in p.stdout, p.stdout)
        p = subprocess.run([exe, "--replay", os.path.join(tmp, "nope.json")], capture_output=True, text=True)
        check("replay of a missing file exits 2", p.returncode == 2)
    traces = [f for f in os.listdir(tmp) if f.endswith(".trace.json")]
    check("trace written", len(traces) >= 1)
    p = subprocess.run([exe, "--count", "3", "--profile", "hell", "--quiet", "--out", tmp], capture_output=True, text=True)
    check("chaos profile accepted", p.returncode == 0, p.stdout + p.stderr)
    p = subprocess.run([sys.executable, "-I", os.path.join(ROOT, "tools", "simfuzz", "run.py"), "--no-build", "--count", "5", "--players", "4", "--quiet", "--out", tmp], capture_output=True, text=True)
    check("run.py wrapper", p.returncode == 0 and "ALL OK" in p.stdout, p.stdout + p.stderr)
    # the wrapper defaults to the em target; --list-bugs without --target shows the engine-model bug classes
    p = subprocess.run([sys.executable, "-I", os.path.join(ROOT, "tools", "simfuzz", "run.py"), "--no-build", "--list-bugs"], capture_output=True, text=True)
    check("run.py defaults to the em target", "pickup_no_arbitration" in p.stdout.split() and "two_owners" not in p.stdout.split(), p.stdout + p.stderr)
    p = subprocess.run([sys.executable, "-I", os.path.join(ROOT, "tools", "simfuzz", "run.py"), "--no-build", "--target", "mini", "--list-bugs"], capture_output=True, text=True)
    check("run.py --target mini still lists the mini bug classes", "two_owners" in p.stdout.split(), p.stdout + p.stderr)
    # replay takes the target from the scenario file (the saved em repros carry "target": "em")
    reg = os.path.join(ROOT, "scenarios", "simfuzz")
    for name, inv in (("regress_digest_buckets_positions.json", "converge"), ("regress_new_owner_no_wait.json", "multi_owner")):
        f = os.path.join(reg, name)
        if not os.path.exists(f):
            check("saved em repro %s exists" % name, False)
            continue
        p = subprocess.run([R.EXE, "--replay", f], capture_output=True, text=True)
        check("replay of %s (target from the file) reproduces its bug" % name, p.returncode == 1 and "VIOLATION" in p.stdout and (not inv or ("VIOLATION " + inv) in p.stdout), p.stdout[-600:])
    # --shrink: a failing scenario file shrinks and the result still fails; a passing one is refused (the twin of the check)
    src = os.path.join(reg, "regress_digest_buckets_positions.json")
    if os.path.exists(src):
        small = os.path.join(tmp, "shrunk_regress.json")
        p = subprocess.run([R.EXE, "--shrink", src, "--save", small], capture_output=True, text=True)
        check("--shrink writes a smaller-or-equal scenario that still reproduces", p.returncode == 0 and os.path.exists(small) and "breaks converge" in p.stdout, p.stdout + p.stderr)
        if os.path.exists(small):
            p = subprocess.run([R.EXE, "--replay", small], capture_output=True, text=True)
            check("the shrunk scenario still fails on replay", p.returncode == 1 and "VIOLATION converge" in p.stdout, p.stdout[-400:])
        clean = os.path.join(tmp, "no_bug.json")
        with open(src) as f, open(clean, "w") as g:
            g.write(f.read().replace('"digest_buckets_positions"', ""))
        p = subprocess.run([R.EXE, "--shrink", clean, "--save", os.path.join(tmp, "unused.json")], capture_output=True, text=True)
        check("--shrink refuses a scenario that passes (exit 1)", p.returncode == 1 and "nothing to shrink" in p.stderr, p.stdout + p.stderr)
    # em target: the failure is recorded through T1a's EngineFlightObserver and T1b's diverge reads it back
    if True:
        d = os.path.join(tmp, "em")
        p = subprocess.run([R.EXE, "--target", "em", "--count", "20", "--bug", "no_dedupe", "--max-fail", "1", "--out", d], capture_output=True, text=True)
        check("em: injected bug fails the CLI", p.returncode == 1 and "FAIL seed" in p.stdout, p.stdout[-800:])
        nv = sorted(os.path.join(d, f) for f in (os.listdir(d) if os.path.isdir(d) else []) if f.endswith(".min.m0.nvfr") or f.endswith(".min.m1.nvfr"))
        check("em: .nvfr per machine written by the engine observer", len(nv) == 2)
        if len(nv) == 2:
            p = subprocess.run([sys.executable, "-I", os.path.join(ROOT, "tools", "flight", "flight.py"), "diverge", "--check"] + nv, capture_output=True, text=True)
            check("em: flight.py diverge finds the divergence in the recorded traces", p.returncode == 1 and "FIRST DIVERGENCE" in p.stdout, p.stdout[-800:] + p.stderr)
        em_twins(tmp)
    if fails:
        print("FAILED: " + ", ".join(fails))
        return 1
    print("ALL OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
