"""A stand-in for a test runner, used by test_proof.py. Prints some lines and exits with the given code.

    stub_runner.py CODE [--sleep S] [--flaky COUNTERFILE] [--line TEXT] [--log FILE NAME]
--log: append "NAME start <time>" before and "NAME end <time>" after the sleep (shows which stubs ran at the same time). Each stub writes its OWN
       file FILE.NAME: two processes appending to one file at the same moment lose lines on Windows (a test then failed with KeyError 'end').
--flaky: exit 1 on the first call (counter file absent), 0 on later calls.
"""
import os
import sys
import time

a = sys.argv[1:]
code = int(a[0]) if a else 0


def stamp(what):
    name = a[a.index("--log") + 2]
    path = a[a.index("--log") + 1] if os.environ.get("PROOF_MUTANT") == "stub_shared_log" else a[a.index("--log") + 1] + "." + name
    with open(path, "a") as f:
        f.write(name + " " + what + " " + repr(time.time()) + "\n")


if "--log" in a:
    stamp("start")
if "--sleep" in a:
    time.sleep(float(a[a.index("--sleep") + 1]))
if "--flaky" in a:
    f = a[a.index("--flaky") + 1]
    if os.path.exists(f):
        code = 0
    else:
        open(f, "w").write("1")
        code = 1
if "--log" in a:
    stamp("end")
if "--line" in a:
    print(a[a.index("--line") + 1])
elif code:
    print("FAIL stub step 4: health host 40 client 100")
else:
    print("PASS stub")
sys.exit(code)
