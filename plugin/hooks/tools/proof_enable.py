#!/usr/bin/env python3
"""Live check of the Enable/Disable capture path (needs the lease; one host launch).
VCG03 (0x15D912) stage 10 enables six refs (oracle: 15D982 15D981 168C25 168C26 168C27 168C28), most of them initially disabled in the plugin, i.e. they
only exist as a FORM-FLAG state, never touching the ExtraFlags path (0x5650D0). Expect EnableRef for each in one txn."""
import importlib.util
import json
import sys
import time
from pathlib import Path

spec = importlib.util.spec_from_file_location("ps", Path(__file__).resolve().parent / "proof_single.py")
ps = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ps)

QUEST, STAGE = "15D912", 10
WANT = {"falloutnv.esm:" + r for r in ("15d982", "15d981", "168c25", "168c26", "168c27", "168c28")}


def main():
    ok = True
    nv, pid = ps.launch("host")
    try:
        def flags():
            return {r: nv.call("ref.info", {"ref": "falloutnv.esm:" + r})["flags"] for r in ("15D982", "168C25")}
        print("form flags before:", {k: hex(v) for k, v in flags().items()})
        print("stage before:", nv.eval("GetStage %s" % QUEST), "done10:", nv.eval("GetStageDone %s %d" % (QUEST, STAGE)))
        # direct probe: does a plain Enable on a ref reach the hooks at all?
        nv.call("capture.begin")
        r = nv.call("exec.ref", {"ref": "falloutnv.esm:15D982", "cmd": "Enable", "scope": True})
        probe = nv.call("capture.end")
        print("probe Enable ->", [e["type"] + ":" + e.get("ref", "") for t in probe["txns"] for e in t["effects"]], probe["stats"])
        nv.call("capture.begin")
        nv.exec("setstage %s %d" % (QUEST, STAGE))
        time.sleep(2)
        cap = nv.call("capture.end")
        out = ps.REPO / "out" / "L5" / "proof_extra"
        out.mkdir(parents=True, exist_ok=True)
        (out / "enable_txns.json").write_text(json.dumps(cap, indent=1))
        print("form flags after:", {k: hex(v) for k, v in flags().items()})
        effs = [e for t in cap["txns"] for e in t["effects"]]
        got = {e["ref"].lower() for e in effs if e["type"] == "EnableRef"}
        print("effects:", [e["type"] for e in effs], "stats:", cap["stats"])
        for name, cond in (("one or more txns emitted", len(cap["txns"]) >= 1),
                           ("every oracle EnableRef captured", WANT <= got),
                           ("no stuck scope left over", cap["stats"].get("staleScopes", 0) == 0)):
            print(("PASS " if cond else "FAIL ") + name, "" if cond else "missing=%s got=%s" % (sorted(WANT - got), sorted(got)))
            ok &= cond
    finally:
        ps.stop(nv, pid)
    print("RESULT:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
