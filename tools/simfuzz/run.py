"""Co-op fuzzer driver. Usage: python -I tools/simfuzz/run.py --minutes 2 --players 4 [--profile bad_wifi] [--target mini|em]
Other options are passed to fnvcoop_simfuzz (--seed, --duration, --bug NAME, --replay FILE, --out DIR, --no-shrink, --list-bugs).
Builds the fuzzer first when the exe is missing or older than its sources. Exit 0 = every invariant held."""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EXE = os.path.join(ROOT, "build", "T2b-fuzz", "fnvcoop_simfuzz.exe")
EXE_EM = os.path.join(ROOT, "build", "T2b-fuzz-em", "fnvcoop_simfuzz.exe")


def newest_source():
    t = 0
    for base in (os.path.join(ROOT, "synccore", "fuzz"), os.path.join(ROOT, "synccore", "src")):
        for d, _, fs in os.walk(base):
            for f in fs:
                if f.endswith((".cpp", ".h", ".txt")):
                    t = max(t, os.path.getmtime(os.path.join(d, f)))
    return t


def build(em):
    env = dict(os.environ)
    env["FUZZ_EM"] = "1" if em else "0"
    r = subprocess.run(["cmd", "/c", r"synccore\fuzz\build.bat"], cwd=ROOT, env=env)
    return r.returncode == 0


def main(argv):
    em = "em" in [argv[i + 1] for i in range(len(argv) - 1) if argv[i] == "--target"]
    exe = EXE_EM if em else EXE
    if "--no-build" not in argv and (not os.path.exists(exe) or os.path.getmtime(exe) < newest_source()):
        if not build(em):
            print("fuzzer build failed")
            return 2
    args = [a for a in argv if a != "--no-build"]
    if "--out" not in args:
        args += ["--out", os.path.join(ROOT, "out", "simfuzz")]
    return subprocess.run([exe] + args, cwd=ROOT).returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
