"""In-process fake game instances for scenario-runner tests (no game, no pipes).

FakeNV applies a scenario step's `expect.both` to its state when it sees the step's host command, and
answers waitFor-style expression polls (GetStageDone / GetQuestRunning / GetObjectiveDisplayed / ...).
make_pair(sync=True) replicates host state changes to the client after `delay_s`; sync=False models a
broken sync layer (client never learns anything).
"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional


class WaitTimeout(Exception):  # name matches nvbridge.WaitTimeout (runner checks by class name)
    pass


class FakeNV:
    def __init__(self, scenario: Dict, name: str):
        self.name = name
        self.cmds = {s["host"]: s for s in scenario["steps"]}
        self.stages = set()
        self.running = set()
        self.disp = set()
        self.comp = set()
        self.done_q = set()
        self.failed_q = set()
        self.lock = threading.Lock()
        self.peer: Optional["FakeNV"] = None
        self.sync = False
        self.delay_s = 0.0
        self.executed: List[str] = []
        self.exprs: List[str] = []
        # L3 scenarios address quests by local form id in host/wait; expectations use EDIDs
        ref = scenario.get("ref", "")
        self.alias = {k: scenario["quest"] for k in (ref.split(":")[-1], scenario.get("formid")) if k}

    # --- state mutation (also used for replication) ---
    def apply(self, step: Dict) -> None:
        b = step.get("expect", {}).get("both", {})
        with self.lock:
            if "stage" in b:
                self.stages.add((b["stage"]["quest"], b["stage"]["value"]))
            if "quest_running" in b:
                self.running.add(b["quest_running"])
            q = b.get("stage", {}).get("quest") or b.get("quest_running")
            for i in b.get("objectives", {}).get("displayed", []):
                self.disp.add((q, i))
            for i in b.get("objectives", {}).get("completed", []):
                self.comp.add((q, i))
            if b.get("quest_completed"):
                self.done_q.add(q)
            if b.get("quest_failed"):
                self.failed_q.add(q)

    def exec(self, cmd: str, timeout_ms=None) -> Dict[str, Any]:
        self.executed.append(cmd)
        step = self.cmds.get(cmd)
        if step is None:
            return {"ok": False, "output": "unknown"}
        self.apply(step)
        if self.sync and self.peer is not None:
            def rep():
                time.sleep(self.delay_s)
                self.peer.apply(step)
            threading.Thread(target=rep, daemon=True).start()
        return {"ok": True, "output": ""}

    def _eval(self, expr: str) -> float:
        self.exprs.append(expr)
        p = expr.split()
        fn, args = p[0].lower(), p[1:]
        if args and args[0] in self.alias:
            args[0] = self.alias[args[0]]
        with self.lock:
            if fn == "getstagedone":
                return float((args[0], int(args[1])) in self.stages)
            if fn == "getquestrunning":
                return float(args[0] in self.running)
            if fn == "getobjectivedisplayed":
                return float((args[0], int(args[1])) in self.disp)
            if fn == "getobjectivecompleted":
                return float((args[0], int(args[1])) in self.comp)
            if fn == "getquestcompleted":
                return float(args[0] in self.done_q)
            if fn == "getquestfailed":
                return float(args[0] in self.failed_q)
        raise ValueError("unsupported expr " + expr)

    def wait_for(self, expr=None, *, cmd=None, op="truthy", value=0, contains=None, timeout_ms=10000, poll_ms=10):
        end = time.monotonic() + timeout_ms / 1000
        v = 0.0
        while True:
            v = self._eval(expr)
            if (op == "truthy" and v) or (op == "==" and v == value) or (op == ">=" and v >= value):
                return {"satisfied": True, "value": v}
            if time.monotonic() >= end:
                raise WaitTimeout(f"timed out waiting for {expr} (last {v})")
            time.sleep(0.005)

    def state(self):
        with self.lock:
            return {"stages": sorted(self.stages), "running": sorted(self.running),
                    "disp": sorted(self.disp), "comp": sorted(self.comp)}


def make_pair(scenario: Dict, sync: bool = True, delay_s: float = 0.02):
    h, c = FakeNV(scenario, "host"), FakeNV(scenario, "client")
    h.peer, h.sync, h.delay_s = c, sync, delay_s
    return h, c


def snap_diff(a, b) -> List[str]:
    return [k for k in a if a[k] != b.get(k)]
