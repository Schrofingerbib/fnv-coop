"""Run a runtime-mutation test binary: once for real, then once per twin with FNVCOOP_MUT=<n>. Every twin MUST be caught.

    python -I tools/mut/run_twins.py --label "L12 hooks_world" --twins 1-42 [--real-regex ", 0 failed"]
                                     [--caught-regex ", [1-9][0-9]* failed"] [--jobs 4] -- EXE [ARGS with {n} for the twin number]

* Real run: FNVCOOP_MUT is removed from the environment; exit code must be 0 and, if --real-regex is given, the output must match it.
* Twin run: FNVCOOP_MUT=<n>; "caught" means a non-zero exit code. With --caught-regex the output must ALSO match (use it where the
  binary reports "<k> failed" lines, so a crash or a missing DLL is not mistaken for a caught twin). Loader errors (missing DLL, bad
  image) are never counted as caught.
* --twins takes "1-42", "1,2,5-7" or a space free mix.
* Prints "<label>: real tests pass, all N twins caught - ALL OK" on success, "SURVIVED: <n>" for each twin that was not caught.
* --self-test runs the built-in checks of this runner (including a twin that must be reported as surviving).
"""
import argparse
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

LOADER_ERRORS = {127, 0xC0000135, 0xC0000139, 0xC000007B, 0xC0000142}  # DLL not found, entry point not found, bad image, DLL init failed


def parse_twins(spec: str):
    out = []
    for part in re.split(r"[,\s]+", spec.strip()):
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return out


def run_one(cmd, n, timeout, base_env):
    env = dict(base_env)
    if n:
        env["FNVCOOP_MUT"] = str(n)
    else:
        env.pop("FNVCOOP_MUT", None)
    argv = [c.replace("{n}", str(n)) for c in cmd]
    try:
        p = subprocess.run(argv, capture_output=True, text=True, errors="replace", env=env, timeout=timeout)
    except FileNotFoundError:
        return 127, "test executable not found: " + argv[0]  # never counts as a caught twin (see LOADER_ERRORS) and fails the real run
    except subprocess.TimeoutExpired:
        return 1, "timeout"  # a hung twin is a caught twin (the real run would not hang)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def run_all(label, cmd, twins, real_regex, caught_regex, jobs, timeout, verbose):
    base_env = dict(os.environ)
    base_env.pop("FNVCOOP_MUT", None)
    rc, out = run_one(cmd, 0, timeout, base_env)
    if verbose or rc != 0:
        print(out.rstrip()[-3000:])
    if rc != 0:
        print(f"{label}: REAL RUN FAILED (exit {rc})")
        return 1
    if real_regex and not re.search(real_regex, out):
        print(f"{label}: real run exit 0 but output does not match {real_regex!r}")
        return 1
    survivors = []

    def one(n):
        c, o = run_one(cmd, n, timeout, base_env)
        uc = c & 0xFFFFFFFF
        caught = c != 0 and uc not in LOADER_ERRORS
        if caught and caught_regex and not re.search(caught_regex, o):
            caught = False
        return n, caught, c, o

    with ThreadPoolExecutor(max_workers=max(1, jobs)) as ex:
        for n, caught, c, o in ex.map(one, twins):
            if verbose:
                print(f"  twin {n}: {'caught' if caught else 'SURVIVED'} (exit {c})")
            if not caught:
                survivors.append(n)
                print(f"SURVIVED: twin {n} (exit {c}); last output:\n    " + "\n    ".join(o.rstrip().splitlines()[-4:]))
    if survivors:
        print(f"{label}: {len(survivors)} twin(s) survived: {survivors}")
        return 1
    print(f"{label}: real tests pass, all {len(twins)} twins caught - ALL OK")
    return 0


def self_test():
    py = sys.executable
    # a fake test binary: fails (exit 1, prints '3 failed') when FNVCOOP_MUT is 1 or 2, survives for 3, prints '0 failed' for real
    prog = ("import os,sys;n=int(os.environ.get('FNVCOOP_MUT','0') or 0);"
            "print('10 checks, %d failed' % (3 if n in (1,2) else 0));sys.exit(1 if n in (1,2) else 0)")
    cmd = [py, "-c", prog]
    ok = True
    r = run_all("selftest-all-caught", cmd, [1, 2], r", 0 failed", r", [1-9][0-9]* failed", 1, 30, False)
    ok &= r == 0
    print("  expect: ALL OK ->", "ok" if r == 0 else "WRONG")
    r = run_all("selftest-survivor", cmd, [1, 2, 3], r", 0 failed", r", [1-9][0-9]* failed", 1, 30, False)
    ok &= r == 1
    print("  expect: twin 3 survives ->", "ok" if r == 1 else "WRONG")
    bad = [py, "-c", "import sys;print('1 failed');sys.exit(1)"]
    r = run_all("selftest-broken-real", bad, [1], None, None, 1, 30, False)
    ok &= r == 1
    print("  expect: real run failure detected ->", "ok" if r == 1 else "WRONG")
    # a crash without the failure line must not count as caught when --caught-regex is given
    crash = [py, "-c", "import os,sys;sys.exit(3 if os.environ.get('FNVCOOP_MUT') else 0)"]
    r = run_all("selftest-crash-not-caught", crash, [1], None, r", [1-9][0-9]* failed", 1, 30, False)
    ok &= r == 1
    print("  expect: crash without failed-line not accepted ->", "ok" if r == 1 else "WRONG")
    print("run_twins self-test: ALL OK" if ok else "run_twins self-test: FAILED")
    return 0 if ok else 1


def main():
    if "--self-test" in sys.argv:
        return self_test()
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="twins")
    ap.add_argument("--twins", required=True)
    ap.add_argument("--real-regex", default=None)
    ap.add_argument("--caught-regex", default=None)
    ap.add_argument("--jobs", type=int, default=2)
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    a = ap.parse_args()
    cmd = a.cmd[1:] if a.cmd and a.cmd[0] == "--" else a.cmd
    if cmd and os.path.exists(cmd[0]):
        cmd[0] = os.path.abspath(cmd[0])  # CreateProcess does not resolve a relative exe path reliably
    if not cmd:
        ap.error("missing the test command after --")
    return run_all(a.label, cmd, parse_twins(a.twins), a.real_regex, a.caught_regex, a.jobs, a.timeout, a.verbose)


if __name__ == "__main__":
    sys.exit(main())
