"""Co-op fuzzer driver. Usage: python -I tools/simfuzz/run.py --minutes 2 --players 4 [--profile bad_wifi] [--target em|mini]
The default target is em (the engine model, synccore/enginemodel); pass --target mini for the small standalone model.
Other options are passed to fnvcoop_simfuzz (--seed, --duration, --bug NAME, --replay FILE, --out DIR, --no-shrink, --list-bugs).
Builds the fuzzer first when the exe is missing or older than its sources. Exit 0 = every invariant held."""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EXE = os.path.join(ROOT, "build", "T2b-fuzz", "fnvcoop_simfuzz.exe")   # one exe serves both targets (FUZZ_EM is on by default)
EXE_EM = EXE                                                           # kept for older callers
DEFAULT_TARGET = "em"


def newest_source():
    t = 0
    for base in (os.path.join(ROOT, "synccore", "fuzz"), os.path.join(ROOT, "synccore", "src"), os.path.join(ROOT, "synccore", "enginemodel", "src"),
                 os.path.join(ROOT, "synccore", "enginemodel", "include"), os.path.join(ROOT, "synccore", "include"), os.path.join(ROOT, "synccore", "chaos")):
        for d, _, fs in os.walk(base):
            for f in fs:
                if f.endswith((".cpp", ".h", ".txt")):
                    t = max(t, os.path.getmtime(os.path.join(d, f)))
    return t


def build():
    env = dict(os.environ)
    env.pop("FUZZ_EM", None)   # em is on by default; this wrapper always builds the exe that serves both targets
    r = subprocess.run(["cmd", "/c", r"synccore\fuzz\build.bat"], cwd=ROOT, env=env)
    return r.returncode == 0


def with_default_target(argv):
    """--target em unless the caller chose one (a --replay of a scenario file uses the file's own target when none is given)."""
    if "--target" in argv or "--replay" in argv:
        return list(argv)
    return ["--target", DEFAULT_TARGET] + list(argv)   # in front: --list-bugs reads the target when it is reached


def main(argv):
    if "--no-build" not in argv and (not os.path.exists(EXE) or os.path.getmtime(EXE) < newest_source()):
        if not build():
            print("fuzzer build failed")
            return 2
    args = with_default_target([a for a in argv if a != "--no-build"])
    if "--out" not in args:
        args += ["--out", os.path.join(ROOT, "out", "simfuzz")]
    return subprocess.run([EXE] + args, cwd=ROOT).returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
