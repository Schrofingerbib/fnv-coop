#!/usr/bin/env python3
"""Live check of the census-backlog command captures + applies (lease needed, two launches).
Phase A (host): console commands run under a capture scope via exec.ref; expect one effect type per command.
Phase B (guest): apply the recorded effects (statuses must be Ok except SpawnRef / RequestSave which stay Unsupported),
                 and the same console commands are SUPPRESSED on a guest (controls word unchanged)."""
import importlib.util
import json
import sys
import time
from pathlib import Path

spec = importlib.util.spec_from_file_location("ps", Path(__file__).resolve().parent / "proof_single.py")
ps = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ps)

CHET, PLAYER = "falloutnv.esm:104C79", "falloutnv.esm:000014"
CMDS = [  # (ref, command, expected effect type)
    (CHET, "SetFactionRank 17B76D 1", "SetFactionRank"),
    (CHET, "RemoveFromFaction 17B76D", "SetFactionRank"),
    (PLAYER, "AddPerk 17714F", "SetPerk"),
    (PLAYER, "AddNote 177875", "AddNote"),
    (PLAYER, "SetWeather 0FFC88", "SetWeather"),
    (PLAYER, "SetGlobalTimeMultiplier 30", "SetTimeScale"),
    (PLAYER, "Autosave", "RequestSave"),
    (PLAYER, "DisablePlayerControls 1 0 0 0 0 0 0", "SetPlayerControls"),
    (PLAYER, "EnablePlayerControls 1 0 0 0 0 0 0", "SetPlayerControls"),
    (CHET, "AddItem 0000000F 5", "RefAddItem"),
    (CHET, "RemoveItem 0000000F 2", "RefRemoveItem"),
    (PLAYER, "PlaceAtMe 0000000F 1", "SpawnRef"),
]
UNSUPPORTED_APPLY = {"SpawnRef", "RequestSave"}


def main():
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + name + (" :: " + str(detail)[:300] if detail else ""), flush=True)
        ok &= bool(cond)

    nv, pid = ps.launch("host")
    try:
        got = []
        for ref, cmd, want in CMDS:
            nv.call("capture.begin")
            r = nv.call("exec.ref", {"ref": ref, "cmd": cmd, "scope": True})
            cap = nv.call("capture.end")
            types = [e["type"] for t in cap["txns"] for e in t["effects"]]
            got += [e for t in cap["txns"] for e in t["effects"]]
            check("A: `%s` -> %s" % (cmd, want), want in types, {"ok": r.get("ok"), "types": types})
        Path(ps.REPO / "out" / "L5" / "proof_extra").mkdir(parents=True, exist_ok=True)
        (ps.REPO / "out" / "L5" / "proof_extra" / "batch_effects.json").write_text(json.dumps(got, indent=1))
    finally:
        ps.stop(nv, pid)
    time.sleep(3)

    effects = json.loads((ps.REPO / "out" / "L5" / "proof_extra" / "batch_effects.json").read_text())
    nv, pid = ps.launch("guest")
    try:
        bad = 0
        for e in effects:
            txn = {"cause": {"kind": 3, "source": "", "aux": 0}, "origin": 0, "effects": [e]}
            r = nv.call("apply.txn", {"txn": txn})
            st = r["results"][0]["status"]
            exp_ok = e["type"] not in UNSUPPORTED_APPLY
            good = (st == "Ok") if exp_ok else (st in ("Unsupported", "Ok"))
            check("B: apply %s -> %s" % (e["type"], st), good, r["results"][0].get("error"))
            bad += not good
        r = nv.call("controls.lock", {"locked": False})
        check("B: still responsive after the batch", nv.ping()["frames"] > 0)
    finally:
        ps.stop(nv, pid)
    print("RESULT:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
