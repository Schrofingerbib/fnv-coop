#!/usr/bin/env python3
"""Live: (1) VCG02 stage 40 AddItem of a LEVELED list must be captured as the RESOLVED item(s), never the list (L7 found list 096C06 vs
inventory 07EA24); (2) repro of the VCG00 stage 95 game death (pipe closed). Needs the lease; one host launch each."""
import importlib.util
import json
import sys
import time
from pathlib import Path

spec = importlib.util.spec_from_file_location("ps", Path(__file__).resolve().parent / "proof_single.py")
ps = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ps)
LIST = "falloutnv.esm:096c06"


def main():
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + name + (" :: " + str(detail)[:300] if detail else ""), flush=True)
        ok &= bool(cond)

    nv, pid = ps.launch("host")
    try:
        nv.call("capture.begin")
        nv.exec("setstage 10A214 40")
        time.sleep(2)
        cap = nv.call("capture.end")
        items = [e for t in cap["txns"] for e in t["effects"] if e["type"] in ("AddItem", "RefAddItem")]
        print("captured items:", json.dumps(items), flush=True)
        check("leveled AddItem captured as resolved item(s), never the list", items and all(e["item"].lower() != LIST for e in items), items)
    finally:
        ps.stop(nv, pid)
    time.sleep(3)
    # VCG00 95 repro (fresh launch: quest at its autosave state)
    nv, pid = ps.launch("host")
    try:
        nv.call("capture.begin")
        try:
            nv.exec("setstage 102037 95")
            time.sleep(3)
            cap = nv.call("capture.end")
            print("VCG00 95 survived; stats", cap["stats"], "types", [e["type"] for t in cap["txns"] for e in t["effects"]], flush=True)
            check("VCG00 stage 95 does not kill the game", True)
        except Exception as e:  # noqa: BLE001
            print("VCG00 95 -> pipe/game lost:", e, flush=True)
            check("VCG00 stage 95 does not kill the game", False, e)
    finally:
        ps.stop(nv, pid)
    print("RESULT:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
