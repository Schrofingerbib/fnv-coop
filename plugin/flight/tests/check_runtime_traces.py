"""Runs T1b's analyzer on the desync traces the plugin runtime test leaves behind (FNVCOOP_FLIGHT_KEEP=1).

Usage: python -I check_runtime_traces.py <tmp dir given to flight_runtime_tests.exe>
The runtime test drifts quest 0x203's stage on the client (host 0, client 77); the analyzer must narrow the first digest
divergence to bucket 72, key 0xAA650F118B7CB748, and name each machine's value text.
"""
import glob
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "tools", "flight"))
import analyze as A   # noqa: E402
import nvfr as N      # noqa: E402

KEY = "0xAA650F118B7CB748"


def main():
    root = sys.argv[1]
    files = []
    for side in ("dhost", "dclient"):
        found = sorted(glob.glob(os.path.join(root, side, "*desync*.nvfr")))
        if not found:
            print("no desync trace in %s" % os.path.join(root, side))
            return 1
        files.append(found[0])
    a = A.align([N.load(f) for f in files])
    d = A.find_divergence(a)
    if d is None or d["kind"] != "digest":
        print("expected a digest divergence, got %r" % (d and d["kind"]))
        return 1
    nw = d["narrowing"]
    keys = [k for k in nw["keys"] if k["key"] == KEY]
    ok = nw["buckets"] == [0x48] and [k["key"] for k in nw["keys"]] == [KEY] and len(keys) == 1
    if ok:
        k = keys[0]
        ok = "= 0" in (k["texts"].get(0) or "") and "= 77" in (k["texts"].get(1) or "")
    print(A.format_divergence(a, d).split("causal chain")[0])
    print("runtime traces: %s" % ("narrowing OK (bucket 72, key %s, host 0 vs client 77)" % KEY if ok else "NARROWING WRONG"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
