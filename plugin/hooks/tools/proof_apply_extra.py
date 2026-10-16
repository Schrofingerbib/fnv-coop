#!/usr/bin/env python3
"""L5 round-7 in-game checks (needs the game lease; two launches): capture + apply of KillActor / ResurrectActor / MoveRef
and the guest suppression of the same commands. Target NPC: Chet (GSChetRef 0x104C79), marker: the player ref (0x14).

    py -3 -I -X utf8 plugin/hooks/tools/proof_apply_extra.py
Phase A (role=host)  : capture.begin; exec `104C79.KillActor` / `104C79.ResurrectActor 0` / `104C79.MoveTo 14 100 0 0`
                       -> the txns must contain KillActor, ResurrectActor, MoveRef{cell=player marker, x=100}.
Phase B (role=guest) : (1) the same console commands are SUPPRESSED (state unchanged); (2) apply.txn of the recorded
                       effects (ApplyScope) really kills / resurrects / moves; (3) SetScriptVar on a ref without a script
                       is rejected cleanly (no crash).
"""
import importlib.util
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("ps", HERE / "proof_single.py")
ps = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ps)

CHET = "104C79"
OUT = ps.REPO / "out" / "L5" / "proof_extra"


REF = "falloutnv.esm:" + CHET


def info(nv):
    return nv.call("ref.info", {"ref": REF})


def isdead(nv):
    return 1 if info(nv)["lifeState"] in (1, 2) else 0


def pos(nv, axis):
    return info(nv)[axis.lower()]


def run_on_chet(nv, cmd, scope=False):
    return nv.call("exec.ref", {"ref": REF, "cmd": cmd, "scope": scope})


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        print(("PASS " if cond else "FAIL ") + name + (" :: " + str(detail)[:300] if detail else ""))
        ok &= bool(cond)

    # ------------------------------------------------------------ phase A: host capture
    nv, pid = ps.launch("host")
    try:
        check("A: hooks installed", nv.call("hooks.status")["failed"] == 0)
        check("A: Chet alive at start", isdead(nv) == 0)
        nv.call("capture.begin")
        run_on_chet(nv, "KillActor", True)
        time.sleep(0.5)
        dead = isdead(nv)
        run_on_chet(nv, "ResurrectActor 0", True)
        time.sleep(0.5)
        alive = isdead(nv)
        run_on_chet(nv, "MoveTo 14 100 0 0", True)
        time.sleep(0.5)
        px, cx = nv.call("ref.info", {"ref": "falloutnv.esm:000014"})["x"], pos(nv, "X")
        cap = nv.call("capture.end")
        (OUT / "txns.json").write_text(json.dumps(cap, indent=1))
        types = [e["type"] for t in cap["txns"] for e in t["effects"]]
        print("captured:", types, " dead/alive/dx:", dead, alive, cx - px)
        check("A: console kill really killed", dead == 1)
        check("A: console resurrect really resurrected", alive == 0)
        check("A: Chet moved next to the player marker (dx ~ +100)", abs((cx - px) - 100) < 25, cx - px)
        check("A: KillActor captured", "KillActor" in types, types)
        check("A: ResurrectActor captured", "ResurrectActor" in types, types)
        mv = [e for t in cap["txns"] for e in t["effects"] if e["type"] == "MoveRef"]
        check("A: MoveRef captured with marker + offsets", bool(mv) and mv[0]["x"] == 100 and mv[0]["cell"].endswith(":000014"), mv)
    finally:
        ps.stop(nv, pid)
    time.sleep(3)

    # ------------------------------------------------------------ phase B: guest
    cap = json.loads((OUT / "txns.json").read_text())
    effects = [e for t in cap["txns"] for e in t["effects"]]
    nv, pid = ps.launch("guest")
    try:
        check("B: guest hooks installed", nv.call("hooks.status")["failed"] == 0)
        run_on_chet(nv, "KillActor")
        time.sleep(0.5)
        check("B: guest console kill is SUPPRESSED", isdead(nv) == 0)
        x0 = pos(nv, "X")
        run_on_chet(nv, "MoveTo 14 100 0 0")
        time.sleep(0.5)
        check("B: guest console MoveTo is SUPPRESSED", abs(pos(nv, "X") - x0) < 1)
        r = nv.call("controls.lock", {"locked": True})
        check("B: player controls lock engages (no crash)", r["locked"] is True, r)
        r = nv.call("controls.lock", {"locked": False})
        check("B: player controls unlock", r["locked"] is False, r)
        kill = [e for e in effects if e["type"] == "KillActor"]
        res = [e for e in effects if e["type"] == "ResurrectActor"]
        mov = [e for e in effects if e["type"] == "MoveRef"]
        r = nv.call("apply.txn", {"txn": {"cause": {"kind": 3, "source": "", "aux": 0}, "origin": 0, "effects": kill}})
        print("apply kill:", json.dumps(r["results"]))
        time.sleep(0.5)
        check("B: applied KillActor kills", isdead(nv) == 1, r["results"])
        r = nv.call("apply.txn", {"txn": {"cause": {"kind": 3, "source": "", "aux": 0}, "origin": 0, "effects": res}})
        time.sleep(0.5)
        check("B: applied ResurrectActor resurrects", isdead(nv) == 0, r["results"])
        px = nv.call("ref.info", {"ref": "falloutnv.esm:000014"})["x"]
        r = nv.call("apply.txn", {"txn": {"cause": {"kind": 3, "source": "", "aux": 0}, "origin": 0, "effects": mov}})
        time.sleep(0.5)
        check("B: applied MoveRef moves Chet next to the marker", abs((pos(nv, "X") - px) - 100) < 25, (pos(nv, "X") - px, r["results"]))
        bad = {"cause": {"kind": 3, "source": "", "aux": 0}, "origin": 0,
               "effects": [{"type": "SetScriptVar", "owner": "falloutnv.esm:" + CHET, "var": 1, "value": 3}]}
        r = nv.call("apply.txn", {"txn": bad})
        print("apply refvar:", json.dumps(r["results"]))
        check("B: SetScriptVar on a ref is handled without crashing", r["results"][0]["status"] in ("Ok", "EngineRejected", "Unsupported"), r["results"])
        check("B: applier console lines are counted separately from leaked scripts", r["scripts"]["ranWhileApplying"] == 0, r["scripts"])
        print("status after:", nv.ping()["frames"] > 0)
    finally:
        ps.stop(nv, pid)
    print("\nRESULT:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
