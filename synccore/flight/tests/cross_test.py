"""Cross test: traces written by the C++ recorder must be read by tools/flight/nvfr.py without warnings.
Usage: python -I synccore/flight/tests/cross_test.py <dir with fresh_host.nvfr and fresh_client.nvfr>"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(ROOT, "tools", "flight"))
import nvfr  # noqa: E402


def main(d):
    bad = 0
    for name, role in (("fresh_host.nvfr", "host"), ("fresh_client.nvfr", "client")):
        t = nvfr.load(os.path.join(d, name))
        recs = t["records"]
        kinds = {}
        for r in recs:
            kinds[r["name"]] = kinds.get(r["name"], 0) + 1
        ok = (not t["warnings"] and not t["truncated"] and t["header"]["role_name"] == role
              and kinds.get("NetOut", 0) > 0 and kinds.get("NetIn", 0) > 0 and kinds.get("Ownership", 0) == 1
              and kinds.get("Digest", 0) >= 1 and kinds.get("Marker", 0) >= 1
              and (kinds.get("TxnApplied", 0) >= 5 if role == "client" else True)
              and not any(r.get("malformed") for r in recs))
        print("%s: %s %s" % (name, "ok" if ok else "BAD", kinds))
        if not ok:
            print("  warnings:", t["warnings"])
            bad += 1
    if bad:
        return 1
    print("cross test ALL OK")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
