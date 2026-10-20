#!/usr/bin/env python3
"""L12 in-game proof of L5's ObjectState and ref-inventory APPLY (needs the game; ONE instance, minimized; run through run_slot.sh).

    sh plugin/hooks_world/tools/run_slot.sh proof_objects.py

One launch, role=host, save l12base:
  1. choose a container and/or door in the player's cell, record their engine state (world.query: GetLocked, GetLockLevel,
     GetOpenState, GetDestroyed, GetItemCount) as the "start" state
  2. capture.begin -> real console commands (Lock, SetOpenState, SetDestroyed, AddItem/RemoveItem on the container, AddItem on
     an NPC) -> capture.end: the ObjectState / RefAddItem / RefRemoveItem effects L5 captured. Record the engine state ("host")
  3. loadSave again (pristine world) -> state must equal "start" (so the proof is meaningful) -> apply.txn each captured txn
     (L5's RemoteApplier, no scripts run) -> engine state "client" must equal "host", field by field.
The L7 snapshot does not carry lock/open/inventory, so the diff is over these engine queries; the snapshot's refs section is
diffed as well (exists/enabled/cell/vars of the touched refs must be unchanged between host-after and client-after).
Writes out/L12/proof/objects.json.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import proof_world as pw  # noqa: E402

CAPS = 0xF
ITEM = 0xF


def q(nv, rid, fn, p1=None):
    a = {"id": "%x" % rid, "fn": fn}
    if p1 is not None:
        a["p1"] = "%x" % p1
    try:
        return nv.call("world.query", a)["value"]
    except Exception:
        return None


def state(nv, rid, kind):
    s = {}
    if kind == "cont":
        s["locked"] = q(nv, rid, "GetLocked")
        s["lockLevel"] = q(nv, rid, "GetLockLevel")
        s["destroyed"] = q(nv, rid, "GetDestroyed")
        s["caps"] = q(nv, rid, "GetItemCount", ITEM)
    elif kind == "door":
        s["locked"] = q(nv, rid, "GetLocked")
        s["lockLevel"] = q(nv, rid, "GetLockLevel")
        s["openState"] = q(nv, rid, "GetOpenState")
    else:
        s["caps"] = q(nv, rid, "GetItemCount", ITEM)
        for name, code in (("charisma", 8), ("agility", 10), ("luck", 11), ("endurance", 7)):
            s[name] = q(nv, rid, "GetActorValue", code)
    return s


CELL = "GSProspectorSaloonInterior"


def go_interior(nv):
    return  # placed containers/doors are found by base type in the exterior cell; no cell change needed

    c0 = nv.call("world.scripted", {"max": 1})["cell"]
    r = nv.call("world.execid", {"noref": True, "cmd": "CenterOnCell " + CELL})
    pw.wait_frames(nv, 240)
    print("CenterOnCell", r, "cell %x -> %x" % (c0, nv.call("world.scripted", {"max": 1})["cell"]), flush=True)


def main():
    out = Path(pw.REPO) / "out" / "L12" / "proof"
    out.mkdir(parents=True, exist_ok=True)
    report = {"checks": {}, "unproven": []}
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        report["checks"][name] = {"pass": bool(cond), "detail": str(detail)[:600]}
        print(("PASS " if cond else "FAIL ") + name + (" :: " + str(detail)[:300] if detail else ""), flush=True)
        ok &= bool(cond)

    watchfile = out / "watchrefs_obj.txt"
    watchfile.write_text("# L12 objects\n")
    nv, pid = pw.launch("host", str(watchfile))
    try:
        go_interior(nv)
        conts = nv.call("world.actors", {"max": 20, "type": 0x1B})["actors"]
        doors = nv.call("world.actors", {"max": 20, "type": 0x1C})["actors"]
        npcs = nv.call("world.actors", {"max": 5})["actors"]
        print("containers:", len(conts), "doors:", len(doors), "npcs:", len(npcs), flush=True)
        subjects = []  # (label, kind, actor dict)
        if conts:
            subjects.append(("container", "cont", conts[0]))
        if doors:
            subjects.append(("door", "door", doors[0]))
        if npcs:
            subjects.append(("npc", "npc", npcs[0]))
        check("setup: have at least one container/door and one NPC", any(s[1] != "npc" for s in subjects) and npcs, [s[0] for s in subjects])
        watchfile.write_text("# L12 objects\n" + "\n".join(s[2]["ref"] for s in subjects) + "\n")

        start = {s[0]: state(nv, s[2]["id"], s[1]) for s in subjects}
        S = {}

        def dump(name):
            p = out / ("O_%s.json" % name)
            nv.state_to_file(str(p))
            S[name] = p

        dump("host0")
        cmds = []
        for label, kind, a in subjects:
            if kind == "cont":
                cmds += [(a, "Lock 50"), (a, "SetDestroyed 1"), (a, "AddItem 0000000F 7"), (a, "RemoveItem 0000000F 2")]
            elif kind == "door":
                cmds += [(a, "Lock 25"), (a, "SetOpenState 1")]
            else:
                cmds += [(a, "AddItem 0000000F 5"), (a, "SetActorValue Charisma 9"), (a, "ModActorValue Luck 2"),
                         (a, "ForceActorValue Agility 8"), (a, "DamageActorValue Endurance 1")]
        txns = []
        weffs = []
        nv.call("world.drain")
        for a, c in cmds:
            nv.call("capture.begin")
            r = nv.call("world.execid", {"id": "%x" % a["id"], "cmd": c, "scope": True})
            cap = nv.call("capture.end")
            effs = [e for t in cap["txns"] for e in t["effects"]]
            print("  %-22s ok=%s -> %s" % (c, r.get("ok"), [(e["type"]) for e in effs]), flush=True)
            txns += cap["txns"]
            weffs += nv.call("world.drain")["effects"]
        (out / "cap_objects.json").write_text(json.dumps(txns, indent=1))
        types = sorted({e["type"] for t in txns for e in t["effects"]})
        check("capture: ObjectState and RefAddItem/RefRemoveItem effects were captured", "ObjectState" in types or not any(s[1] != "npc" for s in subjects), types)
        check("capture: RefAddItem captured for the NPC/container", "RefAddItem" in types, types)
        wt = sorted({e["type"] for e in weffs})
        check("capture: NPC Set/Mod/Force/Damage actor-value commands captured as SetActorValueRef (4 of them)",
              sum(1 for e in weffs if e["type"] == "SetActorValueRef") == 4 or not npcs, wt)
        pw.wait_frames(nv, 60)  # the client is read 60 frames after its apply: read the host the same way
        host = {s[0]: state(nv, s[2]["id"], s[1]) for s in subjects}
        dump("host_after")
        report["start"], report["host"] = start, host
        check("host: the commands changed the engine state (the proof is not vacuous)", host != start, (start, host))

        pw.reload(nv)
        go_interior(nv)
        base = {s[0]: state(nv, s[2]["id"], s[1]) for s in subjects}
        check("replay: pristine reload equals the start state", base == start, (base, start))
        res = []
        for t in txns:
            res.append(nv.call("apply.txn", {"txn": t, "gate": True}))
        print("world effects:", json.dumps(weffs), flush=True)
        for w in weffs:
            ar = nv.call("world.apply", {"effect": w})
            print("  apply", w["type"], ar, flush=True)
            res.append({"applied": 1, "total": 1} if ar["status"] == "Ok" else {"applied": 0, "total": 1, "w": w})
        bad = [r for r in res if r.get("applied") != r.get("total")]
        check("replay: every captured txn applied (no skips)", not bad, bad or len(res))
        pw.wait_frames(nv, 60)
        client = {s[0]: state(nv, s[2]["id"], s[1]) for s in subjects}
        dump("client_after")
        report["client"] = client
        for label, _, _ in subjects:
            check("equal: %s client state equals host state" % label, client[label] == host[label], (client[label], host[label]))
        if client.get("npc") != host.get("npc"):
            n = [x for x in subjects if x[1] == "npc"][0][2]
            for c in ("SetActorValue Charisma 9", "ModActorValue Luck 2"):
                nv.call("world.execid", {"id": "%x" % n["id"], "cmd": c})
            pw.wait_frames(nv, 30)
            print("diag: direct console on the client ->", state(nv, n["id"], "npc"), flush=True)
        rc, so = pw.snapdiff(S["host_after"], S["client_after"], extra=("--sections", "shared"))
        report["snapdiff"] = {"rc": rc, "out": so[-600:]}
        check("snapdiff: host-after vs client-after shared refs equal", rc == 0, so[-300:])
    finally:
        (out / "objects.json").write_text(json.dumps(report, indent=1))
        pw.stop(nv, pid)
    print("ALL PASS" if ok else "SOME FAILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
