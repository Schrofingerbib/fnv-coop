#!/usr/bin/env python3
"""In-game proof of the story-capture RefCommand hooks (docs/tools/story-capture.md). WRITTEN, NEVER RUN against a real game.

    python -I plugin/hooks/tools/proof_story_capture.py --host 0 --guest 1     # two instances already running, nvcoop.ini [hooks] story_capture=1
    python -I plugin/hooks/tools/proof_story_capture.py --selftest             # no game: the checker against a scripted fake, plus injected defects

Provision the instances first (the lead starts them; this tool never launches a game and never takes the game slot):
    python -I tools/runner/run.py provision --n 2 --ini hooks.story_capture=1 --ini coop.role=host ...    (instance 1: coop.role=client)

Checks (each is one line of output; exit 0 = all pass, 1 = a check failed, 2 = cannot reach the game):
  * hooks.status: no hook failed to install, and the story hooks are among the installed ones (more than the 60 of the old table);
  * for each sample command run on an actor of the host's cell inside a capture scope (bridge exec.ref with scope): the capture returns a RefCommand
    effect with the expected op name (events and state ops alike);
  * a STATE sample (SetScale) captured on the host is applied on the guest with apply.txn and the guest then reads the new value back (GetScale);
  * an AI order sample (EvaluatePackage) is applied on the guest as "NotForMe" when the guest does not own the actor, or performed when it does.
The checker takes a `bridge` object with call(method, params) so the self-test can drive it with a fake.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Callable, Dict, List, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))

# (command run on the actor, expected op name, is it a state op)
SAMPLES: List[Tuple[str, str, bool]] = [
    ("EvaluatePackage", "EvaluatePackage", False),
    ("SetGhost 1", "SetGhost", True),
    ("SetGhost 0", "SetGhost", True),
    ("SetScale 1.25", "SetScale", True),
    ("SetScale 1", "SetScale", True),
]

Check = Tuple[str, bool, str]


def effects_of(cap: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [e for t in cap.get("txns", []) for e in t.get("effects", [])]


def run_checks(host, guest, ref: str) -> List[Check]:
    out: List[Check] = []
    st = host.call("hooks.status", {})
    out.append(("hooks installed without failures", st.get("failed", 1) == 0 and st.get("installed", 0) > 60,
                "installed=%s failed=%s" % (st.get("installed"), st.get("failed"))))
    for cmd, op, state in SAMPLES:
        host.call("capture.begin", {})
        host.call("exec.ref", {"ref": ref, "cmd": cmd, "scope": True})
        cap = host.call("capture.end", {})
        got = [e for e in effects_of(cap) if e.get("type") == "RefCommand" and e.get("op") == op]
        out.append(("capture %-16s -> RefCommand %s" % (cmd, op), len(got) == 1, "got %s" % [e.get("op") for e in effects_of(cap)]))
        if cmd == "SetScale 1.25" and got:
            # the guest applies the host's txn and reads the value back
            guest_before = guest.call("eval", {"expr": "GetScale", "ref": ref}).get("value")
            res = guest.call("apply.txn", {"txn": cap["txns"][0]})
            after = guest.call("eval", {"expr": "GetScale", "ref": ref}).get("value")
            out.append(("guest applied SetScale", res.get("applied", 0) >= 1 and abs(float(after) - 1.25) < 0.01,
                        "before=%s after=%s result=%s" % (guest_before, after, res)))
        if cmd == "EvaluatePackage" and got:
            res = guest.call("apply.txn", {"txn": cap["txns"][0]})
            statuses = [r.get("status") for r in res.get("results", [])]
            out.append(("guest answers an AI order with Ok or NotForMe", all(s in ("Ok", "NotForMe") for s in statuses) and bool(statuses), str(statuses)))
    return out


# ------------------------------------------------------------------------------------------------------------ self-test
class FakeGame:
    """Scripted stand-in: records the way the real hooks should answer. `defect` breaks one behaviour."""

    def __init__(self, defect: str = "", is_guest: bool = False):
        self.defect, self.is_guest = defect, is_guest
        self.scale = 1.0
        self.cap: List[Dict[str, Any]] = []

    def call(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        if method == "hooks.status":
            return {"installed": 40 if self.defect == "few_hooks" else 135, "failed": 1 if self.defect == "hook_failed" else 0}
        if method == "capture.begin":
            self.cap = []
            return {}
        if method == "exec.ref":
            cmd = params["cmd"]
            name = cmd.split()[0]
            if name == "SetScale":
                self.scale = float(cmd.split()[1])
            if self.defect == "no_capture":
                return {"ok": True}
            self.cap.append({"type": "RefCommand", "op": name, "ref": params["ref"], "cmd": cmd})
            return {"ok": True}
        if method == "capture.end":
            return {"txns": [{"effects": list(self.cap)}] if self.cap else [], "stats": {}}
        if method == "apply.txn":
            applied = 0
            results = []
            for e in params["txn"]["effects"]:
                if e["op"] == "SetScale" and self.defect != "apply_ignored":
                    self.scale = float(e["cmd"].split()[1])
                    applied += 1
                    results.append({"status": "Ok"})
                elif e["op"] == "EvaluatePackage":
                    results.append({"status": "NotForMe" if self.defect != "bad_status" else "Boom"})
            return {"applied": applied, "results": results}
        if method == "eval":
            return {"value": self.scale}
        raise KeyError(method)


def selftest() -> int:
    ref = "falloutnv.esm:104C20"
    bad = 0

    def run(defect: str) -> List[Check]:
        return run_checks(FakeGame(defect), FakeGame(defect, True), ref)

    base = run("")
    for name, ok, why in base:
        print(("PASS " if ok else "FAIL ") + name, "" if ok else why)
    if not all(ok for _, ok, _ in base):
        print("selftest: the clean fake does not pass")
        return 1
    for defect in ("few_hooks", "hook_failed", "no_capture", "apply_ignored", "bad_status"):
        res = run(defect)
        caught = any(not ok for _, ok, _ in res)
        print("%s injected defect %-14s" % ("caught " if caught else "MISSED ", defect))
        bad += 0 if caught else 1
    print("proof_story_capture selftest:", "ALL OK" if not bad else "%d defect(s) missed" % bad)
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--host", type=int, default=0)
    ap.add_argument("--guest", type=int, default=1)
    ap.add_argument("--ref", default="", help="an actor of the host's cell as Plugin.esm:HEX (default: the first from world.actors)")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    sys.path.insert(0, os.path.join(REPO, "tools", "runner"))
    try:
        import nvbridge  # type: ignore
        hostb = nvbridge.NvBridge.connect(a.host, wait_s=5)
        guestb = nvbridge.NvBridge.connect(a.guest, wait_s=5)
    except Exception as e:  # noqa: BLE001
        print("cannot reach the game:", e)
        return 2
    ref = a.ref or hostb.call("world.actors", {"max": 1})["actors"][0]["ref"]
    res = run_checks(hostb, guestb, ref)
    for name, ok, why in res:
        print(("PASS " if ok else "FAIL ") + name, "" if ok else why)
    return 0 if all(ok for _, ok, _ in res) else 1


if __name__ == "__main__":
    sys.exit(main())
