#!/usr/bin/env python3
"""Live proof, L5 round 11 (needs a game slot: run through run_slot.sh). Two launches.

Phase G (role=guest):
  * GuestLocalRun: the allow-list loaded (guest.stats.allowListed), allow-listed quest scripts really RUN on the guest while other quest
    scripts are still skipped, and the DLC setup quest variables (zero on the client in L10's snapdiff) get set.
  * guest event / activate observers: observe.set suppress + activate.ref / event.mark call the ENGINE functions the way a key press does.
  * party-aware condition evaluation: a real Condition::Evaluate call (cond.eval) is reduced over a two-member test party.
  * per-frame cost of the Script::Execute detour (perf.hooks) over an idle window, against the 33 ms frame budget.
Phase H (role=host):
  * acting-player scope on a real command (SetPlayerTeammate under a ghost -> CompanionOwner.owner == slot), EnableFastTravel inside a
    quest-sourced scope -> SetFastTravelLock, weak script scopes on real quest scripts (droppedNoScope / weakTxns while idle).
"""
import importlib.util
import json
import math
import sys
import time
from pathlib import Path

spec = importlib.util.spec_from_file_location("ps", Path(__file__).resolve().parent / "proof_single.py")
ps = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ps)

CHET, PLAYER, GHOST = "falloutnv.esm:104C79", "falloutnv.esm:000014", "falloutnv.esm:15D982"
IDLE_S = 20
RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + (" :: " + str(detail)[:400] if detail else ""), flush=True)


def dist(a, b):
    return math.sqrt(sum((a[k] - b[k]) ** 2 for k in "xyz") if False else (a["x"] - b["x"]) ** 2 + (a["y"] - b["y"]) ** 2 + (a["z"] - b["z"]) ** 2)


def phase_guest():
    nv, pid = ps.launch("guest")
    try:
        gs = nv.call("guest.stats")
        check("allow-list loaded", gs["allowListed"] >= 30, gs)
        nv.call("perf.hooks", {"on": True})
        f0 = nv.ping()["frames"]
        time.sleep(IDLE_S)
        frames = nv.ping()["frames"] - f0
        perf = nv.call("perf.hooks", {})
        gs = nv.call("guest.stats")
        check("allow-listed quest scripts ran on the guest", gs["localRan"] > 0, {k: gs[k] for k in ("localRan", "perScript")})
        check("other quest scripts still skipped on the guest", gs["questScriptsSkipped"] > 0, gs["questScriptsSkipped"])
        per_frame_us = perf["overheadUs"] / max(frames, 1)
        print("PERF Script::Execute detour: %d calls, %.3f us/call, %.2f us/frame over %d frames (frame budget 33000 us)"
              % (perf["calls"], perf["usPerCall"], per_frame_us, frames), flush=True)
        check("detour overhead far below the frame budget", per_frame_us < 330, "%.2f us/frame" % per_frame_us)
        # DLC setup quest variables: nonzero means the setup scripts ran here (L10 snapdiff had them at 0 on the client)
        qs = ["honesthearts.esm:008891", "lonesomeroad.esm:003603", "oldworldblues.esm:002FCA", "deadmoney.esm:005229", "gunrunnersarsenal.esm:00093C"]
        vd = nv.call("vars.dump", {"quests": qs})
        nz = {q["quest"]: {k: v for k, v in q["vars"].items() if v} for q in vd["quests"]}
        check("DLC setup quest variables set on the guest", all(nz[q] for q in qs), nz)

        # observers
        nv.call("observe.set", {"events": "suppress", "activate": "suppress", "reset": True})
        r = nv.call("activate.ref", {"ref": CHET})
        st = nv.call("observe.stats")
        check("guest activate observer fires and suppresses", st["activates"] == 1 and r["returned"] == 0 and CHET.split(":")[1].lower() in st["lastActivateTarget"].lower(), [r, st])
        r = nv.call("event.mark", {"ref": CHET, "mask": 1})
        st = nv.call("observe.stats")
        check("guest event observer fires and suppresses", st["events"] == 1 and r["returned"] is False, [r, st])
        nv.call("observe.set", {"events": "off", "activate": "off"})

        # party-aware conditions on a real Condition::Evaluate
        pc = nv.call("ref.info", {"ref": PLAYER})
        ch = nv.call("ref.info", {"ref": CHET})
        gh = nv.call("ref.info", {"ref": GHOST})
        d0, d1 = dist(ch, pc), dist(ch, gh)
        thr = (d0 + d1) / 2.0
        print("distances chet-player %.1f chet-ghost %.1f threshold %.1f" % (d0, d1, thr))
        if abs(d0 - d1) > 20:
            op, red = ("<", "min") if d1 < d0 else (">", "max")
            nv.call("party.set", {"members": []})
            vanilla = nv.call("cond.eval", {"func": "GetDistance", "subject": CHET, "param1": PLAYER, "op": op, "value": thr})
            nv.call("party.set", {"members": [{"slot": 0}, {"slot": 1, "ref": GHOST}], "reduce": red})
            party = nv.call("cond.eval", {"func": "GetDistance", "subject": CHET, "param1": PLAYER, "op": op, "value": thr})
            nv.call("party.set", {"members": []})
            check("vanilla GetDistance condition is false", vanilla["result"] is False and not vanilla["crashed"], vanilla)
            check("party-reduced GetDistance condition is true", party["result"] is True and party["reduced"] == 1 and not party["crashed"], party)
            nv.call("party.set", {"members": [{"slot": 0}, {"slot": 1, "ref": GHOST}], "reduce": "initiator"})
            init = nv.call("cond.eval", {"func": "GetDistance", "subject": CHET, "param1": PLAYER, "op": op, "value": thr})
            nv.call("party.set", {"members": []})
            check("initiator reduction equals vanilla", init["result"] == vanilla["result"], init)
        else:
            check("distances differ enough to test", False, (d0, d1))
    finally:
        ps.stop(nv, pid)


def phase_host():
    nv, pid = ps.launch("host")
    try:
        # weak scopes on real quest scripts while idle
        nv.call("capture.begin")
        f0 = nv.ping()["frames"]
        time.sleep(IDLE_S)
        cap = nv.call("capture.end")
        frames = nv.ping()["frames"] - f0
        st = cap["stats"]
        print("idle host %d frames: stats %s scripts %s" % (frames, json.dumps(st), json.dumps(cap["scripts"])))
        check("no ambient mutation dropped (weak scopes cover quest scripts)", st["droppedNoScope"] == 0, st)

        # acting-player scope -> CompanionOwner owner slot
        nv.call("capture.begin")
        nv.call("exec.ref", {"ref": CHET, "cmd": "SetPlayerTeammate 1", "scope": True, "acting": {"ghost": GHOST, "slot": 2}})
        nv.call("exec.ref", {"ref": CHET, "cmd": "SetPlayerTeammate 0", "scope": True, "acting": {"ghost": GHOST, "slot": 2}})
        cap = nv.call("capture.end")
        effs = [e for t in cap["txns"] for e in t["effects"] if e["type"] == "CompanionOwner"]
        check("CompanionOwner captured with the acting player's slot", len(effs) == 2 and all(e["owner"] == 2 for e in effs) and effs[0].get("active", True) and not effs[1].get("active", True), effs)
        nv.call("capture.begin")
        nv.call("exec.ref", {"ref": CHET, "cmd": "SetPlayerTeammate 1", "scope": True})
        nv.call("exec.ref", {"ref": CHET, "cmd": "SetPlayerTeammate 0", "scope": True})
        cap = nv.call("capture.end")
        effs = [e for t in cap["txns"] for e in t["effects"] if e["type"] == "CompanionOwner"]
        check("host player owns by default (slot 0)", len(effs) == 2 and all(e["owner"] == 0 for e in effs), effs)

        # EnableFastTravel inside a quest-sourced scope
        nv.call("capture.begin")
        nv.call("exec.ref", {"ref": PLAYER, "cmd": "EnableFastTravel 0", "scope": True, "source": "falloutnv.esm:104C79"})
        nv.call("exec.ref", {"ref": PLAYER, "cmd": "EnableFastTravel 1", "scope": True, "source": "falloutnv.esm:104C79"})
        cap = nv.call("capture.end")
        effs = [e for t in cap["txns"] for e in t["effects"] if e["type"] == "SetFastTravelLock"]
        check("EnableFastTravel captured as SetFastTravelLock on/off", [e["on"] for e in effs] == [True, False], effs)
        # apply on the same instance: lock bookkeeping + engine accepts the command
        applied = 0
        for t in cap["txns"]:
            if any(e["type"] == "SetFastTravelLock" for e in t["effects"]):
                applied += nv.call("apply.txn", {"txn": t})["applied"]
        check("SetFastTravelLock applies", applied == len(effs), applied)
    finally:
        ps.stop(nv, pid)


def main():
    phase_guest()
    phase_host()
    ok = all(RESULTS)
    print("ALL PASS" if ok else "SOME FAILED (%d/%d)" % (sum(1 for r in RESULTS if not r), len(RESULTS)))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
